"""``ReportAdapter`` — a Notes & Cash report → ``(seed, PeriodInputs[])`` (#267).

The **input adapter** for the report-driven (no-tape) deal path of the cold-start
engine slice (epic #257; design spec
``docs/superpowers/specs/2026-06-20-cold-start-edw-deal-engine-design.md`` →
"Adapters" + "Worked data flows" + Migration item 2). It turns a parsed
:class:`~loanwhiz.primitives.notes_cash_parser.NotesCashReport` into the two
things the generalised fold needs::

    seed, inputs = ReportAdapter.from_deal_model(model).to_inputs(report)
    # then: fold(run_period, seed, inputs) → DealStateSeries

- **seed** — the period-0 :class:`~loanwhiz.domain.state.DealState`, **seeded from
  the *first* report's opening balances (B5)**. Per the spec's locked decision,
  the report-driven path seeds liabilities from the report (closest to actual),
  *not* from prospectus constants — that is the tape path's seed. The Notes & Cash
  report prints each class's *closing* balance and the principal repaid this
  period, so a tranche's period-opening balance is ``closing + principal_repaid``.
  The seed carries :class:`~loanwhiz.domain.provenance.FieldProvenance` (it was
  *extracted*; spec §3 — only the seed carries provenance, rolled states do not).

- **inputs** — one :class:`~loanwhiz.domain.inputs.PeriodInputs` per report period,
  with report-supplied ``step_overrides`` (keyed by **priority label**, because the
  generalised ``run_period._apply_step_overrides`` re-keys ``step_overrides[label]``
  to recipient itself) and ``step_sources`` in the canonical
  ``"engine" | "reported" | "residual"`` spelling.

Reuse, not duplication
----------------------
The step classification reuses the **shared** classifier
(:func:`~loanwhiz.primitives.step_source_classifier.build_step_specs` +
:data:`~loanwhiz.primitives.step_source_classifier.ENGINE_COMPUTED_RECIPIENTS`),
so the live adapter and the offline validation harness classify steps with **one**
classifier and cannot drift. The classifier speaks the harness vocabulary
(``"report-supplied"``); translating that to the canonical ``step_sources``
spelling (``"reported"``) is — by the classifier's own contract — *the adapter's*
concern, done here. The report's revenue ``(b)`` line is printed as wrapped
sub-items ``(1)…(14)`` (a ``pypdf`` layout artefact the V3 parser surfaces
individually); :func:`_fold_revenue_pop` folds them back into one ``(b)`` total,
mirroring the harness's ``_fold_report_revenue_steps``.

Pure & offline: depends only on the parsed report model + the deal's extracted
waterfall steps. No network, no LLM, no engine call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from loanwhiz.domain.inputs import PeriodInputs
from loanwhiz.domain.provenance import FieldProvenance, ProvenanceMap
from loanwhiz.domain.state import DealState, TrancheState
from loanwhiz.primitives.base import Citation
from loanwhiz.primitives.notes_cash_parser import NotesCashPeriod, NotesCashReport
from loanwhiz.primitives.step_source_classifier import build_step_specs

# ---------------------------------------------------------------------------
# Defaults — the Green Lion 2024-1 report shape (matches the offline harness).
# ---------------------------------------------------------------------------

#: Revenue PoP labels whose amount the prospectus does NOT formulate — taken from
#: the report (security-trustee/various fees, swap payments, expense top-ups).
#: Mirrors ``engine_validation_harness._REVENUE_REPORT_SUPPLIED_LABELS``.
DEFAULT_REVENUE_REPORT_SUPPLIED_LABELS: frozenset[str] = frozenset(
    {"(a)", "(b)", "(c)", "(g)", "(i)", "(j)"}
)

#: The terminal revenue step is a residual sweep ("any Deferred Purchase Price
#: Instalment to the Seller" — whatever remains in the pot).
DEFAULT_REVENUE_RESIDUAL_LABEL = "(k)"

#: Every redemption step below ``(a)`` is report-supplied this period; the
#: redemption waterfall has no residual sweep (the report leaves a documented
#: unapplied-rounding remainder rather than sweeping the pot).
DEFAULT_REDEMPTION_REPORT_SUPPLIED_LABELS: frozenset[str] = frozenset(
    {"(b)", "(c)", "(d)"}
)
DEFAULT_REDEMPTION_RESIDUAL_LABEL = ""

#: Canonical note-class keys, senior → junior, as the parser emits them.
DEFAULT_TRANCHE_CLASSES: tuple[str, ...] = ("class_a", "class_b", "class_c")

#: Source-vocabulary translation: the shared classifier speaks the harness's
#: ``"report-supplied"``; the canonical ``PeriodInputs.step_sources`` spelling is
#: ``"reported"`` (``"engine"`` / ``"residual"`` pass through unchanged).
_CANONICAL_SOURCE: dict[str, Literal["engine", "reported", "residual"]] = {
    "engine": "engine",
    "report-supplied": "reported",
    "residual": "residual",
}


def _fold_revenue_pop(period: NotesCashPeriod) -> dict[str, float]:
    """Collapse the report's revenue PoP into ``{priority_label: amount}``.

    The report prints step ``(b)`` as fourteen wrapped sub-line items
    ``(1)…(14)`` (a ``pypdf`` layout artefact). The extracted model carries a
    single ``(b)`` step, so the sub-items' amounts are folded back into one
    ``(b)`` total; top-level ``(a)`` and ``(c)…(k)`` labels pass through. Mirrors
    ``engine_validation_harness._fold_report_revenue_steps`` so the adapter and
    harness fold identically.
    """
    folded: dict[str, float] = {}
    b_total = 0.0
    saw_sub_item = False
    for step in period.revenue_pop:
        inner = step.priority.strip("()").strip()
        if inner.isdigit():
            b_total += step.amount
            saw_sub_item = True
        else:
            folded[step.priority] = folded.get(step.priority, 0.0) + step.amount
    if saw_sub_item:
        folded["(b)"] = b_total
    return folded


def _fold_redemption_pop(period: NotesCashPeriod) -> dict[str, float]:
    """``{priority_label: amount}`` for the report's redemption PoP (no folding)."""
    out: dict[str, float] = {}
    for step in period.redemption_pop:
        out[step.priority] = out.get(step.priority, 0.0) + step.amount
    return out


@dataclass(frozen=True)
class ReportAdapter:
    """Turn a Notes & Cash report into ``(seed, PeriodInputs[])`` for the fold.

    Parameterised by the deal's *extracted* waterfall steps (the
    ``{priority, recipient}`` dicts the shared classifier consumes — the same
    shape the offline harness feeds it) plus the report-supplied label sets and
    residual labels that describe *this* deal's published report. Defaults match
    the Green Lion 2024-1 report; :meth:`from_deal_model` pulls the step lists out
    of an extracted ``DealModel``.

    Attributes:
        revenue_steps:    Extracted revenue waterfall steps (``{priority, recipient}``).
        redemption_steps: Extracted redemption waterfall steps.
        revenue_report_supplied_labels:    Revenue labels forced report-supplied.
        revenue_residual_label:            Terminal revenue residual-sweep label.
        redemption_report_supplied_labels: Redemption labels forced report-supplied.
        redemption_residual_label:         Terminal redemption residual label ("" disables).
        tranche_classes:  Canonical note-class keys, senior → junior.
        original_pool_balance: Pool balance at closing (factor denominator); when
                          ``None`` the seed uses the first period's outstanding
                          tranche total as the closing-par proxy.
    """

    revenue_steps: list[dict[str, Any]]
    redemption_steps: list[dict[str, Any]]
    revenue_report_supplied_labels: frozenset[str] = (
        DEFAULT_REVENUE_REPORT_SUPPLIED_LABELS
    )
    revenue_residual_label: str = DEFAULT_REVENUE_RESIDUAL_LABEL
    redemption_report_supplied_labels: frozenset[str] = (
        DEFAULT_REDEMPTION_REPORT_SUPPLIED_LABELS
    )
    redemption_residual_label: str = DEFAULT_REDEMPTION_RESIDUAL_LABEL
    tranche_classes: tuple[str, ...] = DEFAULT_TRANCHE_CLASSES
    original_pool_balance: float | None = None

    # -- constructors -------------------------------------------------------

    @classmethod
    def from_deal_model(
        cls,
        model: Any,
        *,
        revenue_report_supplied_labels: frozenset[str] = (
            DEFAULT_REVENUE_REPORT_SUPPLIED_LABELS
        ),
        revenue_residual_label: str = DEFAULT_REVENUE_RESIDUAL_LABEL,
        redemption_report_supplied_labels: frozenset[str] = (
            DEFAULT_REDEMPTION_REPORT_SUPPLIED_LABELS
        ),
        redemption_residual_label: str = DEFAULT_REDEMPTION_RESIDUAL_LABEL,
        tranche_classes: tuple[str, ...] = DEFAULT_TRANCHE_CLASSES,
        original_pool_balance: float | None = None,
    ) -> "ReportAdapter":
        """Build an adapter from an extracted ``DealModel``.

        Pulls the revenue / redemption step dicts out of ``model.waterfalls`` (the
        ``{priority, recipient, condition}`` shape the offline harness already
        feeds the shared classifier). ``model`` is typed ``Any`` to avoid importing
        the API-layer ``DealModel`` into this primitive (it is duck-typed: any
        object exposing ``waterfalls["revenue"|"redemption"]["steps"]`` works).
        """
        waterfalls = model.waterfalls
        return cls(
            revenue_steps=list(waterfalls["revenue"]["steps"]),
            redemption_steps=list(waterfalls["redemption"]["steps"]),
            revenue_report_supplied_labels=revenue_report_supplied_labels,
            revenue_residual_label=revenue_residual_label,
            redemption_report_supplied_labels=redemption_report_supplied_labels,
            redemption_residual_label=redemption_residual_label,
            tranche_classes=tranche_classes,
            original_pool_balance=original_pool_balance,
        )

    # -- public surface -----------------------------------------------------

    def to_inputs(
        self, report: NotesCashReport
    ) -> tuple[DealState, list[PeriodInputs]]:
        """Map a report to ``(period-0 seed, one PeriodInputs per period)``.

        Seeds period-0 from ``report.periods[0]``'s opening balances (B5) and
        builds one :class:`PeriodInputs` for every period (the report holds them
        sorted by reporting date). Raises ``ValueError`` on an empty report — a
        deal with no report periods is not modelable on the report path.
        """
        if not report.periods:
            raise ValueError(
                f"Notes & Cash report for {report.deal_name!r} has no periods — "
                "cannot seed a report-driven deal (no opening balances to seed from)."
            )
        seed = self.seed(report.periods[0])
        inputs = [self.period_inputs(p) for p in report.periods]
        return seed, inputs

    def seed(self, first_period: NotesCashPeriod) -> DealState:
        """Period-0 :class:`DealState` from the first report's opening balances (B5).

        A tranche's period-opening balance is its printed *closing* balance plus
        the principal repaid that period (the report prints both). The reserve
        opens at ``balance_end + drawings`` (drawings are debits taken during the
        period). The seed carries provenance — it was *extracted* from the report.
        """
        tranches: list[TrancheState] = []
        opening_total = 0.0
        cumulative_losses = 0.0
        for cls in self.tranche_classes:
            nb = first_period.note_balance(cls)
            closing = (nb.principal_balance_after_payment if nb else None) or 0.0
            principal_paid = (nb.total_principal_payments if nb else None) or 0.0
            pdl = (nb.pdl_balance_after_payment if nb else None) or 0.0
            opening_balance = closing + principal_paid
            opening_total += opening_balance
            cumulative_losses += pdl
            tranches.append(
                TrancheState(
                    name=cls,
                    balance=opening_balance,
                    pdl_balance=pdl,
                )
            )

        reserve_acct = first_period.account("reserve_account")
        reserve_balance = (reserve_acct.balance_end if reserve_acct else None) or 0.0
        reserve_drawings = (reserve_acct.drawings if reserve_acct else None) or 0.0
        # Reserve opens at the end balance plus any drawings taken during the period.
        reserve_opening = reserve_balance + reserve_drawings
        reserve_target = first_period.reserve_target or reserve_opening

        original_pool = (
            self.original_pool_balance
            if self.original_pool_balance is not None
            else opening_total
        )

        citation = Citation(
            document=f"Notes & Cash report — {first_period.period_label}",
            page_or_row=first_period.reporting_date,
            excerpt="Period-0 seed reconstructed from the first report's opening "
            "balances (B5).",
        )
        provenance: ProvenanceMap = {
            "tranches": FieldProvenance(
                source="report",
                method="deterministic",
                confidence=1.0,
                citation=citation,
            ),
            "reserve_balance": FieldProvenance(
                source="report",
                method="deterministic",
                confidence=1.0,
                citation=citation,
            ),
        }

        return DealState(
            reporting_date=first_period.reporting_date,
            tranches=tranches,
            reserve_balance=reserve_opening,
            reserve_target=reserve_target,
            pool_balance=opening_total,
            original_pool_balance=original_pool,
            cumulative_losses=cumulative_losses,
            sequential_pay_active=first_period.any_trigger_breached,
            provenance=provenance,
        )

    def period_inputs(self, period: NotesCashPeriod) -> PeriodInputs:
        """One canonical :class:`PeriodInputs` from a report period.

        Available funds come straight from the report totals; ``legs`` is ``None``
        (tape-only); ``source="report"``. ``step_overrides`` (keyed by priority
        label) and ``step_sources`` (canonical spelling) are derived via the
        shared classifier across both waterfalls. ``realized_loss`` defaults to
        ``0.0`` — the Notes & Cash report prints no single crystallised-loss line
        (loss surfaces as PDL movement, reconstructed downstream); seeding it
        ``0.0`` is the honest, conservative cut for this report shape.
        """
        revenue_amounts = _fold_revenue_pop(period)
        redemption_amounts = _fold_redemption_pop(period)

        _, rev_overrides, rev_source = build_step_specs(
            self.revenue_steps,
            residual_label=self.revenue_residual_label,
            report_supplied_labels=self.revenue_report_supplied_labels,
            report_amounts=revenue_amounts,
        )
        _, red_overrides, red_source = build_step_specs(
            self.redemption_steps,
            residual_label=self.redemption_residual_label,
            report_supplied_labels=self.redemption_report_supplied_labels,
            report_amounts=redemption_amounts,
        )

        # The classifier keys overrides/sources by RECIPIENT; PeriodInputs keys by
        # PRIORITY LABEL (run_period re-keys label → recipient itself, applying the
        # SAME flat map across BOTH waterfalls). Re-key via each waterfall's step
        # list, translating the source vocabulary as we go.
        #
        # Label-collision rule (load-bearing). The revenue and redemption
        # waterfalls REUSE the labels (a)…(d) for DIFFERENT recipients (revenue
        # (d) is the engine-computed Class A interest; redemption (d) is a
        # report-supplied principal line), and the canonical maps are a single
        # FLAT label→… dict that run_period applies to BOTH waterfalls (mapping
        # ``step_overrides[label]`` to each waterfall's own recipient). Two rules
        # keep that flat map honest:
        #
        # 1. **"engine" wins source collisions.** run_period clears a step's
        #    extracted condition unless its label is pinned ``"engine"``; a
        #    report-supplied entry from the other waterfall on the same label would
        #    un-gate the engine-computed step and double-count. So an engine label
        #    is never demoted to reported/residual and never carries an override.
        # 2. **Revenue wins report-supplied override collisions (first-wins).**
        #    The revenue waterfall is processed first, so a label it owns as
        #    report-supplied keeps its revenue amount; redemption only contributes
        #    overrides for labels revenue did not already claim. (The canonical
        #    flat shape cannot carry two distinct amounts for one shared label —
        #    this is a #263/#265 schema property, not an adapter choice; revenue
        #    precedence is the documented, deterministic resolution.)
        step_overrides: dict[str, float] = {}
        step_sources: dict[str, Literal["engine", "reported", "residual"]] = {}
        for steps, overrides, source in (
            (self.revenue_steps, rev_overrides, rev_source),
            (self.redemption_steps, red_overrides, red_source),
        ):
            for step in steps:
                label = str(step.get("priority", ""))
                recipient = str(step.get("recipient", ""))
                canonical = _CANONICAL_SOURCE[source[recipient]]
                if step_sources.get(label) == "engine":
                    # Rule 1: already pinned engine by an earlier waterfall — keep.
                    continue
                if canonical == "engine":
                    # Rule 1: engine-computed wins; drop any override the other
                    # waterfall set on this label.
                    step_sources[label] = "engine"
                    step_overrides.pop(label, None)
                    continue
                # Reported/residual. Rule 2: don't overwrite a label an
                # earlier (revenue) waterfall already set as reported/residual.
                if label in step_sources:
                    continue
                step_sources[label] = canonical
                if recipient in overrides:
                    step_overrides[label] = overrides[recipient]

        return PeriodInputs(
            reporting_date=period.reporting_date,
            days_in_period=90,
            available_revenue=period.available_revenue_funds or 0.0,
            available_principal=period.available_principal_funds or 0.0,
            realized_loss=0.0,
            legs=None,
            step_overrides=step_overrides,
            step_sources=step_sources,
            risk_signals=None,
            source="report",
        )
