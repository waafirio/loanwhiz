"""``Reconciler`` — the one engine-vs-report reader, to the cent (#270, epic #257).

The single **reader** over the fold's :class:`DealStateSeries` that proves the
engine reproduces a real deal's published Priority of Payments. It compares each
period's *engine-computed* waterfall steps (read straight out of the folded
series' :class:`PeriodResult` executions) against the deal's own published
Notes & Cash report, **to EUR 0.01**, labelling every line honestly as
engine-computed / report-supplied / residual so the proof never manufactures a
false 100%.

This subsumes two earlier modules
(``docs/superpowers/specs/2026-06-20-cold-start-edw-deal-engine-design.md`` →
"Consolidation: what gets deleted"):

- the offline ``engine_validation_harness`` (which re-ran the interpreter
  standalone) — its typed reconciliation report models + its
  ``engine | report-supplied | residual`` per-line discipline live here, but the
  engine side is now read from the **live fold**, not a second interpretation, so
  the proof is "the live engine lands the published numbers", not "a parallel
  harness does";
- the dead Gemini-based ``report_verifier`` (a 5-figure %-tolerance comparison
  that nothing consumed) — its job (did the published figures match the
  computed?) is the Reconciler's, done to the cent against the full PoP.

The collapsing insight (design spec "Architecture"): *history, projection, and
reconciliation are the same fold with different input streams* — reconciliation
is a reader comparing the fold's engine-computed steps to the report's actuals,
not a second engine.

Pure & offline. The convenience builder :func:`validate_green_lion_2024_1` reads
the committed seed model + the committed Notes & Cash fixtures and folds them
through ``run_period`` (no network, no LLM), so the headline proof runs in the
fast test suite across all 3 quarterly periods.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from loanwhiz.extraction.assembler import DealModel
from loanwhiz.primitives.notes_cash_parser import (
    NotesCashPeriod,
    NotesCashReport,
    parse_report_text,
)
from loanwhiz.primitives.period_state_machine import DealStateSeries, PeriodResult
from loanwhiz.primitives.report_adapter import (
    DEFAULT_REDEMPTION_RESIDUAL_LABEL,
    DEFAULT_REVENUE_RESIDUAL_LABEL,
    ReportAdapter,
)
from loanwhiz.primitives.step_source_classifier import ENGINE_COMPUTED_RECIPIENTS
from loanwhiz.primitives.waterfall_interpreter import WaterfallExecution

_REPO_ROOT = Path(__file__).resolve().parents[3]

#: The deal this reader grounds the headline proof on.
GREEN_LION_2024_1_NAME = "Green Lion 2024-1 B.V."
_SEED_PATH = (
    _REPO_ROOT / "src" / "loanwhiz" / "data" / "deals" / "seed"
    / "green-lion-2024-1-bv.json"
)
_FIXTURE_DIR = _REPO_ROOT / "tests" / "fixtures" / "notes_cash"

#: Green Lion 2024-1's three committed quarterly Notes & Cash fixtures, oldest
#: first (the report holds them sorted by reporting date). Each is a deterministic
#: ``pypdf`` text extract of the published PDF (no Gemini), so the proof is
#: reproducible in CI with no network.
_GREEN_LION_2024_1_FIXTURES: tuple[tuple[str, str], ...] = (
    ("green-lion-2024-1-september-2025.txt", "September 2025"),
    ("green-lion-2024-1-december-2025.txt", "December 2025"),
    ("green-lion-2024-1-march-2026.txt", "March 2026"),
)

#: Reconciliation gate. The proof is "to the cent", so this is an ABSOLUTE EUR
#: tolerance (one cent) — never a percentage (a percentage gate would let a
#: multi-thousand-EUR slip through on a billion-EUR pool).
DEFAULT_TOLERANCE_EUR = 0.01

#: Standing honesty disclosure: which lines the engine derived vs. supplied.
SOURCE_NOTE = (
    "Per-step `source`: 'engine' lines were COMPUTED by the fold's engine from "
    "the extracted deal model (balances/rates), with NO report input — these are "
    "the independent proof. 'report-supplied' lines (swap payments, fee buckets, "
    "expense-account top-up, the revolving-period purchase of new receivables) "
    "have no prospectus formula; their amount is taken from the report and the "
    "engine is proven only to ROUTE them in priority order. 'residual' lines "
    "sweep the remaining pot."
)


# ===========================================================================
# Report-label folding (mirrors the report's printed shape)
# ===========================================================================


def _fold_report_revenue_steps(period: NotesCashPeriod) -> dict[str, float]:
    """Collapse the report's revenue PoP into ``{priority_label: amount}``.

    The Notes & Cash report prints step ``(b)`` as wrapped sub-line items
    ``(1)…(n)`` (a ``pypdf`` layout artefact the V3 parser surfaces individually);
    the extracted model carries a single ``(b)`` step, so the sub-items are folded
    back into one ``(b)`` total. Top-level ``(a)`` and ``(c)…(k)`` labels pass
    through unchanged.
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


def _fold_report_redemption_steps(period: NotesCashPeriod) -> dict[str, float]:
    """``{label: amount}`` for the report's redemption PoP (no folding needed)."""
    out: dict[str, float] = {}
    for step in period.redemption_pop:
        out[step.priority] = out.get(step.priority, 0.0) + step.amount
    return out


def _source_of(recipient: str) -> str:
    """Classify an engine-execution step's source from the shared classifier.

    A recipient in :data:`ENGINE_COMPUTED_RECIPIENTS` is ``"engine"`` (the
    interpreter computed it from balances/rates with no report input); anything
    else is ``"report-supplied"`` (its amount was fed in from the report). The
    residual sweep is detected separately by the caller (it is whichever step is
    flagged ``residual`` on the spec, surfaced via the execution's remaining pot).
    """
    return "engine" if recipient in ENGINE_COMPUTED_RECIPIENTS else "report-supplied"


# ===========================================================================
# Typed reconciliation report
# ===========================================================================


class StepReconciliation(BaseModel):
    """One reconciled priority step: engine output vs. report's published amount.

    Attributes
    ----------
    priority:
        The model/report priority label, e.g. ``"(d)"``.
    recipient:
        The model recipient kind, e.g. ``"class_a_interest"``.
    engine_amount:
        What the fold's engine distributed at this step.
    report_amount:
        The amount the published report distributed at this step.
    delta:
        ``engine_amount - report_amount`` (signed).
    source:
        ``"engine"`` — the fold COMPUTED this from the deal model with no report
        input (the independent proof). ``"report-supplied"`` — the need was taken
        from the report (no prospectus formula) and the engine is proven only to
        route it. ``"residual"`` — a terminal sweep of the remaining pot.
    passed:
        ``abs(delta) <= tolerance``.
    """

    priority: str
    recipient: str
    engine_amount: float
    report_amount: float
    delta: float
    source: str
    passed: bool


class WaterfallReconciliation(BaseModel):
    """Per-step reconciliation of one waterfall (revenue or redemption).

    ``unapplied_rounding`` is the report's own documented "Unapplied … due to
    rounding" remainder — the pot the report deliberately leaves UNDISTRIBUTED.
    The tie-out gate checks ``engine_total + unapplied_rounding == available_funds``,
    so the engine reproduces the published distribution exactly *and* the leftover
    pot matches the report's own rounding line — neither side is fudged.
    """

    waterfall_type: str = Field(..., description="'revenue' or 'redemption'.")
    steps: list[StepReconciliation]
    engine_total: float = Field(..., description="Sum of engine distributions.")
    report_total: float = Field(..., description="Sum of report distributions.")
    available_funds: float = Field(..., description="The pot distributed this period.")
    unapplied_rounding: float = Field(
        default=0.0,
        description="Report's documented undistributed remainder (rounding line).",
    )
    tolerance_eur: float = DEFAULT_TOLERANCE_EUR

    @property
    def passed(self) -> bool:
        """All steps reconciled AND the engine + rounding total ties to the pot."""
        return all(s.passed for s in self.steps) and (
            abs(self.engine_total + self.unapplied_rounding - self.available_funds)
            <= self.tolerance_eur
        )

    @property
    def steps_passed(self) -> int:
        return sum(1 for s in self.steps if s.passed)

    @property
    def engine_computed_passed(self) -> int:
        """Count of ENGINE-computed steps that reconciled — the headline metric."""
        return sum(1 for s in self.steps if s.source == "engine" and s.passed)


class PeriodValidation(BaseModel):
    """One reporting period's revenue + redemption reconciliation."""

    reporting_date: str
    period_label: str
    revenue: WaterfallReconciliation
    redemption: WaterfallReconciliation

    @property
    def passed(self) -> bool:
        return self.revenue.passed and self.redemption.passed


class ReconciliationReport(BaseModel):
    """The full engine-vs-report reconciliation for one deal, across its periods.

    The headline artifact: PASS/FAIL of the *folded* engine against the deal's own
    published Notes & Cash priority of payments, period by period, to the cent.
    ``source_note`` carries the standing honesty disclosure of which lines were
    engine-computed vs. report-supplied.
    """

    deal_name: str
    periods: list[PeriodValidation]
    tolerance_eur: float = DEFAULT_TOLERANCE_EUR
    source_note: str = SOURCE_NOTE

    @property
    def passed(self) -> bool:
        """Every period's revenue + redemption reconciled to the cent."""
        return bool(self.periods) and all(p.passed for p in self.periods)

    @property
    def periods_checked(self) -> int:
        return len(self.periods)

    @property
    def periods_passed(self) -> int:
        return sum(1 for p in self.periods if p.passed)

    def summary(self) -> str:
        """A one-block human summary of the proof."""
        verdict = "PASS" if self.passed else "FAIL"
        lines = [
            f"{verdict}: {self.deal_name} live engine vs. its own Notes & Cash "
            f"report ({self.periods_passed}/{self.periods_checked} periods "
            f"reconciled to EUR {self.tolerance_eur:.2f})",
        ]
        for p in self.periods:
            ec = sum(wf.engine_computed_passed for wf in (p.revenue, p.redemption))
            lines.append(
                f"  {p.reporting_date} ({p.period_label}): "
                f"revenue {p.revenue.steps_passed}/{len(p.revenue.steps)} steps, "
                f"redemption {p.redemption.steps_passed}/{len(p.redemption.steps)} "
                f"steps — {ec} engine-computed line(s) matched"
            )
        lines.append(SOURCE_NOTE)
        return "\n".join(lines)


# ===========================================================================
# Reconciliation core — reads the folded series
# ===========================================================================


def _reconcile_one(
    *,
    waterfall_type: str,
    execution: WaterfallExecution,
    report_amounts: dict[str, float],
    available: float,
    residual_label: str,
    tolerance: float,
) -> WaterfallReconciliation:
    """Reconcile one folded waterfall execution against the report's PoP.

    The engine side is read straight off the fold's :class:`WaterfallExecution`
    (no re-interpretation); the report side is the published per-label amounts.
    Each step is labelled ``engine`` / ``report-supplied`` / ``residual`` so the
    proof distinguishes the independently-computed lines from the routed ones.
    """
    recs: list[StepReconciliation] = []
    for result in execution.steps:
        label = result.priority
        recipient = result.recipient
        report_amt = report_amounts.get(label, 0.0)
        engine_amt = result.amount_distributed
        delta = engine_amt - report_amt
        source = "residual" if label == residual_label else _source_of(recipient)
        recs.append(
            StepReconciliation(
                priority=label,
                recipient=recipient,
                engine_amount=engine_amt,
                report_amount=report_amt,
                delta=delta,
                source=source,
                passed=abs(delta) <= tolerance,
            )
        )
    report_total = sum(report_amounts.values())
    # The report's own documented "Unapplied … due to rounding" remainder: the pot
    # it deliberately left undistributed (available − published distribution).
    # Clamp tiny negatives to 0 (published steps may sum a hair over the pot from
    # independent rounding); a real undistributed remainder is non-negative.
    unapplied = max(0.0, available - report_total)
    return WaterfallReconciliation(
        waterfall_type=waterfall_type,
        steps=recs,
        engine_total=execution.total_distributed,
        report_total=report_total,
        available_funds=available,
        unapplied_rounding=unapplied,
        tolerance_eur=tolerance,
    )


def reconcile_period(
    period_result: PeriodResult,
    period: NotesCashPeriod,
    *,
    revenue_residual_label: str = DEFAULT_REVENUE_RESIDUAL_LABEL,
    redemption_residual_label: str = DEFAULT_REDEMPTION_RESIDUAL_LABEL,
    tolerance: float = DEFAULT_TOLERANCE_EUR,
) -> PeriodValidation:
    """Reconcile one folded period's executions against the report period's PoP.

    Reads the engine-computed revenue + redemption steps from the fold's
    :class:`PeriodResult` and compares each to the report's published amount, to
    the cent.
    """
    revenue = _reconcile_one(
        waterfall_type="revenue",
        execution=period_result.revenue_execution,
        report_amounts=_fold_report_revenue_steps(period),
        available=period.available_revenue_funds or 0.0,
        residual_label=revenue_residual_label,
        tolerance=tolerance,
    )
    redemption = _reconcile_one(
        waterfall_type="redemption",
        execution=period_result.redemption_execution,
        report_amounts=_fold_report_redemption_steps(period),
        available=period.available_principal_funds or 0.0,
        residual_label=redemption_residual_label,
        tolerance=tolerance,
    )
    return PeriodValidation(
        reporting_date=period.reporting_date,
        period_label=period.period_label,
        revenue=revenue,
        redemption=redemption,
    )


def reconcile_series(
    series: DealStateSeries,
    report: NotesCashReport,
    *,
    deal_name: str | None = None,
    revenue_residual_label: str = DEFAULT_REVENUE_RESIDUAL_LABEL,
    redemption_residual_label: str = DEFAULT_REDEMPTION_RESIDUAL_LABEL,
    tolerance: float = DEFAULT_TOLERANCE_EUR,
) -> ReconciliationReport:
    """Reconcile a folded ``DealStateSeries`` against its published report.

    The headline reader: for every report period, take the fold's matching
    :class:`PeriodResult` (the report path's series carries one ``period_result``
    per report period, in the same order) and reconcile its engine-computed steps
    to the report's published PoP, to the cent.

    The report path's series is ``states = [seed, *closing_states]`` with
    ``period_results`` one-per-period in report order, so ``period_results[i]``
    is the transition that produced the state for ``report.periods[i]``. We join
    positionally (the report and the fold were built from the same ordered period
    list), and assert the join is well-formed (one result per report period).
    """
    if len(series.period_results) != len(report.periods):
        raise ValueError(
            "Reconciler join mismatch: the folded series has "
            f"{len(series.period_results)} period result(s) but the report has "
            f"{len(report.periods)} period(s). The series must be folded from the "
            "same ordered report periods the reconciler reads."
        )
    periods = [
        reconcile_period(
            pr,
            p,
            revenue_residual_label=revenue_residual_label,
            redemption_residual_label=redemption_residual_label,
            tolerance=tolerance,
        )
        for pr, p in zip(series.period_results, report.periods)
    ]
    return ReconciliationReport(
        deal_name=deal_name or report.deal_name,
        periods=periods,
        tolerance_eur=tolerance,
    )


# ===========================================================================
# Offline convenience builder (the committed-fixture path — no network)
# ===========================================================================


def load_green_lion_2024_1_model() -> DealModel:
    """Load the committed Green Lion 2024-1 extracted :class:`DealModel` seed."""
    return DealModel.model_validate_json(_SEED_PATH.read_text(encoding="utf-8"))


def load_green_lion_2024_1_report() -> NotesCashReport:
    """Parse the committed Green Lion 2024-1 Notes & Cash fixtures (all 3, offline).

    Each fixture is a deterministic ``pypdf`` text extract of the published
    quarterly PDF, so the report is byte-reproducible in CI with no network and no
    Gemini. Periods are returned oldest-first (the fold and the reconciler both
    read them in this order).
    """
    model = load_green_lion_2024_1_model()
    periods = [
        parse_report_text(
            (_FIXTURE_DIR / fixture).read_text(encoding="utf-8"),
            period_label=label,
        )
        for fixture, label in _GREEN_LION_2024_1_FIXTURES
    ]
    return NotesCashReport(deal_name=model.metadata.deal_name, periods=periods)


def fold_green_lion_2024_1() -> tuple[DealStateSeries, NotesCashReport]:
    """Fold Green Lion 2024-1 from its committed reports → ``(series, report)``.

    The offline equivalent of the live cold-start path
    (``api/main._reconstruct_series_from_reports``): resolve the extracted model,
    parse the committed reports, run :class:`ReportAdapter` to seed period-0 from
    the first report's opening balances and build per-period inputs, then fold
    ``run_period`` over them with the deal's *extracted* waterfall steps. No
    Green-Lion-2026-1 constant is consulted.
    """
    # Import here (not at module load) so this pure reader does not pull in the API
    # layer; the shared report-path fold lives there alongside the live cold-start
    # path, so the offline proof and the live endpoints fold IDENTICALLY (no drift).
    from loanwhiz.api.main import fold_report_series

    model = load_green_lion_2024_1_model()
    report = load_green_lion_2024_1_report()
    adapter = ReportAdapter.from_deal_model(model)
    series = fold_report_series(model, report, adapter)
    return series, report


def validate_green_lion_2024_1(
    *, tolerance: float = DEFAULT_TOLERANCE_EUR
) -> ReconciliationReport:
    """Run the headline proof for Green Lion 2024-1 offline, across all 3 periods.

    Folds the committed seed model + the committed Notes & Cash fixtures through
    ``run_period`` (no network, no LLM) and reconciles the folded engine against
    the deal's own published priority of payments, to the cent.
    """
    series, report = fold_green_lion_2024_1()
    return reconcile_series(series, report, tolerance=tolerance)
