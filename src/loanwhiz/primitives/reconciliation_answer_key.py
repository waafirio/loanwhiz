"""Per-deal ground-truth answer-key format (#427, epic #425).

The data-driven generalization of the hand-built ``_VALIDATION_BUILDERS`` map
(``api/main.py``). Today the only "answer key" a reconciler can grade against is
``reconciler.validate_green_lion_2024_1`` — a bespoke Python builder that
hard-codes a seed-model path, a fixed tuple of Notes & Cash fixtures, and the
parse→fold→:func:`~loanwhiz.primitives.reconciler.reconcile_series` flow. Adding
a graded answer key for any other deal means writing more bespoke code, so
quality grading does not scale (epic #425 → "quality grading does not scale").

This module defines that ground truth as **data**: a typed, JSON-backed,
per-deal answer key carrying the deal's published

- **Notes & Cash Priority-of-Payments line items** (per period, revenue +
  redemption) — the to-the-cent reconciliation ground truth the existing
  reconciler consumes today;
- **covenant test results** (per period) — the deal's published trigger /
  covenant pass/fail outcomes;
- **pool statistics** (per period) — e.g. end-of-period pool balance,
  principal collected,

attachable per deal under ``data/deals/answer_keys/<slug>.json``. A deal may
publish those across more than one document — Cairn CLO XVII states its coverage
tests in monthly trustee reports and its two Priorities of Payments in a
quarterly Note Valuation Report — so each document has its own constructor and
:func:`merge_answer_keys` unions them into the one committed file (#495). Keys
are resolved by
the same deal-name slug the committed *seed model* uses,
``data/deals/seed/<slug>.json``), plus the loader and the thin reconciler-
consume adapter (:func:`reconcile_against_answer_key`) the quality_harness
(#428) calls.

Scope discipline (#427): this is the **format + loader + consume adapter**. It
does NOT author any production deal's real answer key (that is the backfill,
#429), and it does NOT build the grading harness/scorecard (that is #428). The
PoP section converts to the exact :class:`~loanwhiz.primitives.notes_cash_parser.NotesCashReport`
shape :func:`~loanwhiz.primitives.reconciler.reconcile_series` already takes, so
the existing to-the-cent core proves config-loaded ground truth unchanged.

Pure & offline: model definitions + JSON I/O only. No network, no LLM.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from loanwhiz.config import ANSWER_KEY_DATA_DIR
from loanwhiz.primitives.collateral_schedule_parser import (
    CoverageTestOutcome,
    ReportLiabilitySummary,
)
from loanwhiz.primitives.covenant_monitor import TriggerDefinition
from loanwhiz.primitives.notes_cash_parser import (
    NotesCashPeriod,
    NotesCashReport,
    PoPStep,
    _slug,
)
from loanwhiz.primitives.period_state_machine import DealStateSeries
from loanwhiz.primitives.reconciler import (
    DEFAULT_TOLERANCE_EUR,
    ReconciliationReport,
    SkippedPeriod,
    reconcile_series,
)

#: The current answer-key schema version. Bump on a breaking shape change so a
#: stale committed key fails loudly at load rather than mis-grading silently.
ANSWER_KEY_FORMAT_VERSION = 1


#: Prefix for the per-class applied-rate figures a Note Valuation Report
#: publishes, recorded in :attr:`AnswerKeyPeriod.pool_stats` as
#: ``applied_rate_class_a`` and so on. It is the **all-in rate the report says
#: was applied** this period, in per cent — a CLO report publishes no index
#: fixing, so none is recorded. No grader resolves these against the engine
#: today; ``quality_harness._grade_pool_stats`` reports them under
#: ``ungraded_stat_keys``, which is the honest surface for a published figure
#: nothing yet checks.
APPLIED_RATE_STAT_PREFIX = "applied_rate_"


#: Why a period of a partly-PoP-bearing key was not graded (#513). Stated as a
#: fact about the **source document**, not about the engine or the key: the
#: trustee reports Cairn's other three periods were authored from publish
#: coverage-test outcomes and no cascade, so there is genuinely nothing for a
#: reconciliation to compare against — which is a different claim from "the
#: engine was not run" or "the key is incomplete", and #457/#471 are the record
#: of what it costs to state a refusal more strongly than the evidence supports.
NO_POP_PERIOD_REASON = (
    "The document this period was authored from publishes no Priority of "
    "Payments, so there is nothing in it for the engine to be reconciled against."
)


#: The date format a U.S. Bank trustee report prints in its section headers.
#: Answer-key periods are keyed by ISO date (:attr:`AnswerKeyPeriod.reporting_date`),
#: so the one conversion happens here rather than at each call site.
_REPORT_DATE_FORMAT = "%d/%m/%Y"


def _iso_reporting_date(stated: str) -> str:
    """The ISO form of a report-stated reporting date.

    Refuses an unrecognised format rather than passing the raw string through:
    the reporting date is the key the grader name-matches published covenants
    on, so a period keyed in the wrong format matches nothing and would surface
    as "no published covenant matched" — a grading verdict, indistinguishable
    from a real one, produced by a date-parsing bug.
    """
    try:
        return datetime.strptime(stated.strip(), _REPORT_DATE_FORMAT).date().isoformat()
    except ValueError as exc:
        raise ValueError(
            f"unrecognised reporting date {stated!r} — expected "
            f"{_REPORT_DATE_FORMAT!r} as trustee reports state it"
        ) from exc


# ===========================================================================
# Typed answer-key models
# ===========================================================================


class AnswerKeyPopStep(BaseModel):
    """One published Priority-of-Payments line item: a priority label + its amount.

    The ground-truth analogue of one
    :class:`~loanwhiz.primitives.notes_cash_parser.PoPStep`. ``recipient`` is
    optional human context (the printed step description); the reconciler keys on
    ``priority`` and grades on ``amount``, so a key authored from numbers alone
    can omit it.
    """

    priority: str = Field(..., description="Prospectus priority label, e.g. '(d)'.")
    amount: float = Field(..., description="EUR distributed at this step (ground truth).")
    recipient: str | None = Field(
        default=None, description="Step description / recipient as published (optional)."
    )


class CovenantResult(BaseModel):
    """One published covenant / trigger test result for a period.

    The deal's own answer for "did this covenant pass this period?" — graded by
    the quality_harness (#428) against the engine's
    :class:`~loanwhiz.primitives.covenant_monitor` output. ``threshold`` /
    ``actual`` carry the published figures where numeric; ``passed`` is the
    published outcome (a report's ``OK`` ⇒ ``True``).
    """

    name: str = Field(..., description="Covenant / trigger name or label, e.g. 'sequential_pay'.")
    threshold: float | None = Field(default=None, description="Published threshold, if numeric.")
    actual: float | None = Field(default=None, description="Published observed value, if numeric.")
    passed: bool = Field(..., description="Published pass/fail (report 'OK' ⇒ True).")
    note: str | None = Field(default=None, description="Optional human-readable context.")


class AnswerKeyPeriod(BaseModel):
    """The published ground truth for one reporting period.

    Carries all three answer-key categories the issue names. The PoP lists feed
    the to-the-cent reconciler today (:meth:`to_notes_cash_report`); covenants
    and ``pool_stats`` are typed and loadable now and graded by #428.
    """

    reporting_date: str = Field(..., description="ISO reporting date — the period key (e.g. 2026-03-31).")
    period_label: str = Field(..., description='Human-readable period label, e.g. "March 2026".')

    available_revenue_funds: float | None = Field(
        default=None, description="Total Available Revenue Funds for the period (EUR)."
    )
    available_principal_funds: float | None = Field(
        default=None, description="Total Available Principal Funds for the period (EUR)."
    )
    revenue_pop: list[AnswerKeyPopStep] = Field(
        default_factory=list, description="Published revenue Priority-of-Payments line items."
    )
    redemption_pop: list[AnswerKeyPopStep] = Field(
        default_factory=list, description="Published redemption Priority-of-Payments line items."
    )

    covenants: list[CovenantResult] = Field(
        default_factory=list, description="Published covenant / trigger test results."
    )
    pool_stats: dict[str, float] = Field(
        default_factory=dict,
        description="Published pool statistics, e.g. {'pool_balance_end': ..., 'principal_collected': ...}.",
    )

    @property
    def has_priority_of_payments(self) -> bool:
        """Does the document behind *this period* publish a Priority of Payments?

        The per-period form of the question the key-level ``_has_pop_section``
        asks (#513). A key drawn from more than one document family answers it
        differently period by period — Cairn CLO XVII's three trustee-report
        periods publish covenant outcomes and no PoP, while its Note Valuation
        Report period publishes both — so the key-level answer ("some period
        somewhere has one") cannot decide whether *this* period is gradeable.

        #495 recorded the same rule for the authorship side: assert what each
        record may claim per period, not per key. This is the grading side of it,
        and it is the single definition
        :func:`loanwhiz.primitives.capability_matrix._has_pop_section` and
        ``quality_harness._reconcile_deal`` both read, so the two surfaces cannot
        drift apart into disagreeing about what carries ground truth.
        """
        return bool(self.revenue_pop or self.redemption_pop)


class DealAnswerKey(BaseModel):
    """A deal's complete published ground truth — its answer key.

    The data-driven generalization of a hand-built ``_VALIDATION_BUILDERS``
    entry: a config artifact a reconciler consumes, rather than bespoke Python.
    Round-trips to/from JSON (the committed ``answer_keys/<slug>.json`` form) and
    to/from a :class:`NotesCashReport` (the reconciler's report side).
    """

    format_version: int = Field(
        default=ANSWER_KEY_FORMAT_VERSION,
        description="Answer-key schema version (see ANSWER_KEY_FORMAT_VERSION).",
    )
    deal_id: str = Field(..., description="Canonical deal id used in /deal/{deal_id}/... routes.")
    deal_name: str = Field(..., description="Deal name as published (matches the seed model's).")
    tolerance_eur: float = Field(
        default=DEFAULT_TOLERANCE_EUR,
        description="Absolute EUR reconciliation tolerance (the 'to the cent' gate).",
    )
    periods: list[AnswerKeyPeriod] = Field(
        default_factory=list, description="Published ground truth, one entry per reporting period."
    )

    # --- which periods carry gradeable PoP ground truth ------------------------

    @property
    def pop_periods(self) -> list[AnswerKeyPeriod]:
        """The periods carrying a Priority of Payments, in key order.

        The gradeable subset. A key unioned from several document families (#495)
        is only partly PoP-bearing, and this is what the reconciler grades; the
        complement is reported not-applicable rather than graded, because a
        covenant-only period has nothing for the engine to be compared against.
        """
        return [p for p in self.periods if p.has_priority_of_payments]

    @property
    def non_pop_periods(self) -> list[AnswerKeyPeriod]:
        """The periods carrying no Priority of Payments, in key order.

        Named as its own accessor rather than left as an inline complement: these
        periods must be *reported*, never silently dropped, and a caller that has
        to re-derive them tends to drop them instead.
        """
        return [p for p in self.periods if not p.has_priority_of_payments]

    @property
    def has_pop_section(self) -> bool:
        """Does any period carry a Priority of Payments?

        The key-level question, defined once here in terms of the per-period
        predicate. ``capability_matrix._has_pop_section`` and
        ``quality_harness._reconcile_deal`` both read this, so "this key carries
        no PoP" has one definition rather than three copies that can diverge.
        """
        return any(p.has_priority_of_payments for p in self.periods)

    # --- bridge to the reconciler's report side -------------------------------

    def to_notes_cash_report(self, *, pop_periods_only: bool = False) -> NotesCashReport:
        """Project the PoP ground truth onto a :class:`NotesCashReport`.

        The bridge into :func:`~loanwhiz.primitives.reconciler.reconcile_series`:
        each :class:`AnswerKeyPeriod` becomes a
        :class:`~loanwhiz.primitives.notes_cash_parser.NotesCashPeriod` carrying
        the published revenue + redemption PoP and the available-funds totals —
        the exact surface the reconciler reads. Covenant / pool-stat ground truth
        is not part of the PoP report and is graded separately (#428).

        ``pop_periods_only`` narrows the projection to :attr:`pop_periods` — what
        :func:`reconcile_against_answer_key` grades. It defaults to ``False`` so
        the round-trip this method is half of (:meth:`from_notes_cash_report`)
        stays lossless: a projection that silently dropped periods would make the
        committed key un-regenerable from its own report fixtures.
        """
        source_periods = self.pop_periods if pop_periods_only else self.periods
        nc_periods = [
            NotesCashPeriod(
                reporting_date=p.reporting_date,
                period_label=p.period_label,
                deal_name=self.deal_name,
                available_revenue_funds=p.available_revenue_funds,
                available_principal_funds=p.available_principal_funds,
                revenue_pop=[
                    PoPStep(priority=s.priority, recipient=s.recipient or "", amount=s.amount)
                    for s in p.revenue_pop
                ],
                redemption_pop=[
                    PoPStep(priority=s.priority, recipient=s.recipient or "", amount=s.amount)
                    for s in p.redemption_pop
                ],
            )
            for p in source_periods
        ]
        return NotesCashReport(deal_name=self.deal_name, periods=nc_periods)

    @classmethod
    def from_notes_cash_report(
        cls,
        report: NotesCashReport,
        *,
        deal_id: str,
        tolerance_eur: float = DEFAULT_TOLERANCE_EUR,
    ) -> DealAnswerKey:
        """Author a :class:`DealAnswerKey` from a parsed :class:`NotesCashReport`.

        The symmetric inverse of :meth:`to_notes_cash_report`, capturing the PoP
        ground truth. Used to seed answer keys from an already-parsed published
        report (the path the backfill #429 leans on) and to drive the round-trip
        regression test. Covenant / pool-stat sections start empty — they are
        authored from the report's trigger / collateral surfaces separately.
        """
        periods = [
            AnswerKeyPeriod(
                reporting_date=p.reporting_date,
                period_label=p.period_label,
                available_revenue_funds=p.available_revenue_funds,
                available_principal_funds=p.available_principal_funds,
                revenue_pop=[
                    AnswerKeyPopStep(priority=s.priority, amount=s.amount, recipient=s.recipient)
                    for s in p.revenue_pop
                ],
                redemption_pop=[
                    AnswerKeyPopStep(priority=s.priority, amount=s.amount, recipient=s.recipient)
                    for s in p.redemption_pop
                ],
            )
            for p in report.periods
        ]
        return cls(
            deal_id=deal_id,
            deal_name=report.deal_name,
            tolerance_eur=tolerance_eur,
            periods=periods,
        )

    @classmethod
    def from_trustee_liability_summaries(
        cls,
        summaries: Sequence[ReportLiabilitySummary],
        *,
        deal_id: str,
        deal_name: str,
        tolerance_eur: float = DEFAULT_TOLERANCE_EUR,
    ) -> DealAnswerKey:
        """Author a :class:`DealAnswerKey` from parsed trustee-report summaries.

        The **sibling** of :meth:`from_notes_cash_report`, not a new format: a
        deal whose investor reporting is a monthly trustee report publishes no
        Priority of Payments, but it does state each coverage test's computed
        ratio, its required level and its outcome. Those are exactly the
        ``covenants`` section this schema already carries, so a CLO earns a key
        through this constructor rather than through a second shape.

        Every figure comes from :mod:`loanwhiz.primitives.collateral_schedule_parser`
        reading the report's own text (#480). **No engine module is on this
        path** — an answer key inferred from the engine's own output would grade
        the engine against itself and make every graded cell vacuously green.
        That is the one property this constructor exists to guarantee, and the
        regeneration regression in ``tests/test_quality_harness.py`` is what
        pins it: the committed key must reproduce byte-for-byte from the
        committed report fixtures.

        Three refusals, none of them recoverable by guessing:

        - a summary that was not parsed strictly (``reconciled`` is ``False``)
          never reached the parser's own acceptance oracle, so its figures are
          unverified against the document's stated totals;
        - a summary whose ``deal_name`` names a different deal — the guard that
          stops one deal's reports being authored under another's slug;
        - a summary with no reporting date, which leaves the period unkeyable.

        A test the report states as ``N/A`` is **excluded, not coerced**.
        :attr:`CovenantResult.passed` is a ``bool`` and cannot express "this
        test did not apply this period"; writing ``True`` there would publish a
        pass the trustee never stated. The exclusion is deliberate and is
        recorded in ``data/deals/answer_keys/README.md``.
        """
        periods: list[AnswerKeyPeriod] = []
        for summary in summaries:
            if not summary.reconciled:
                raise ValueError(
                    f"trustee-report summary for {summary.period_label!r} was not "
                    "reconciled against the report's own stated totals; parse it "
                    "with strict=True before authoring an answer key from it"
                )
            if summary.deal_name is not None and summary.deal_name != deal_name:
                raise ValueError(
                    f"summary for {summary.period_label!r} states deal "
                    f"{summary.deal_name!r}, not {deal_name!r} — refusing to author "
                    "one deal's answer key from another deal's report"
                )
            if summary.reporting_date is None:
                raise ValueError(
                    f"summary for {summary.period_label!r} states no reporting date, "
                    "so its period cannot be keyed"
                )
            covenants = [
                CovenantResult(
                    name=test.trigger_key,
                    threshold=float(test.required_pct),
                    actual=float(test.current_pct),
                    passed=test.result is CoverageTestOutcome.PASSED,
                    note=f"{test.name}; stated in {test.stated_in}",
                )
                for test in summary.coverage_tests
                if test.result is not CoverageTestOutcome.NOT_APPLICABLE
            ]
            periods.append(
                AnswerKeyPeriod(
                    reporting_date=_iso_reporting_date(summary.reporting_date),
                    period_label=summary.period_label,
                    covenants=covenants,
                )
            )
        return cls(
            deal_id=deal_id,
            deal_name=deal_name,
            tolerance_eur=tolerance_eur,
            periods=periods,
        )

    @classmethod
    def from_note_valuation_report(
        cls,
        report: NotesCashReport,
        *,
        deal_id: str,
        deal_name: str,
        tolerance_eur: float = DEFAULT_TOLERANCE_EUR,
    ) -> DealAnswerKey:
        """Author a :class:`DealAnswerKey` from a parsed Note Valuation Report.

        The **third** constructor, and a sibling of the two above rather than a
        third format (#495). A CLO's quarterly Note Valuation Report publishes,
        on facing sections, an Interest and a Principal Priority of Payments —
        the same facts an RMBS Notes & Cash report carries, which is why
        :mod:`loanwhiz.primitives.note_valuation_parser` emits the *existing*
        :class:`NotesCashReport` shape. So the PoP half of this constructor is
        deliberately identical in effect to :meth:`from_notes_cash_report`.

        What it adds is the half that report carries and an RMBS one does not:
        the **Distribution Summary's per-class all-in applied rate**, recorded
        in ``pool_stats`` as ``applied_rate_<class key>``. That is the rate the
        report says was actually applied this period — *not* an index fixing.
        The document publishes no EURIBOR fixing anywhere, so none is written;
        inventing one would put a figure in ground truth that no document
        states. ``pool_stats`` is the only free-form published-figure map this
        schema carries, and no reader grades an ``applied_rate_*`` key today, so
        :func:`~loanwhiz.primitives.quality_harness._grade_pool_stats` reports
        it under ``ungraded_stat_keys`` — surfaced honestly rather than faked.

        **No engine module is on this path.** Every figure comes from
        :mod:`loanwhiz.primitives.note_valuation_parser` reading the report's own
        text. An answer key inferred from the engine's own output would grade the
        engine against itself and make every cell vacuously green; the
        regeneration regression in ``tests/test_quality_harness.py`` is what
        makes that checkable rather than merely intended.

        Three refusals, mirroring the trustee constructor's and preserving the
        parser's own acceptance oracle rather than routing around it:

        - a period stating **no available funds** for either waterfall never
          passed :func:`~loanwhiz.primitives.note_valuation_parser.reconcile_note_valuation`'s
          tie-out of the step sum to the report's own stated total, so its steps
          are unverified against the document;
        - a period with **no steps on either side** carries no Priority of
          Payments at all — writing it would claim a section this key does not
          have, which is exactly the overclaim ``_has_pop_section`` reads;
        - a period whose **own** ``deal_name`` — the deal name the parser read
          off the report's first page, not the one the caller passed — names a
          different deal, the guard that stops one deal's report being authored
          under another's slug.

        A class the report publishes **no** rate for (Cairn's Subordinated
        Notes) is **excluded, not coerced**: ``interest_rate_applied is None``
        means the report prints none, never "zero per cent", and #481's rule
        applies unchanged.
        """
        periods: list[AnswerKeyPeriod] = []
        for period in report.periods:
            if period.deal_name is not None and period.deal_name != deal_name:
                raise ValueError(
                    f"Note Valuation Report for {period.period_label!r} states deal "
                    f"{period.deal_name!r}, not {deal_name!r} — refusing to author "
                    "one deal's answer key from another deal's report"
                )
            if not period.revenue_pop and not period.redemption_pop:
                raise ValueError(
                    f"Note Valuation Report period {period.period_label!r} parsed no "
                    "Priority-of-Payments steps on either side; a key authored from "
                    "it would claim a PoP section it does not carry"
                )
            for side, funds in (
                ("Interest", period.available_revenue_funds),
                ("Principal", period.available_principal_funds),
            ):
                if funds is None:
                    raise ValueError(
                        f"Note Valuation Report period {period.period_label!r} states no "
                        f"available funds for its {side} Priority of Payments, so its "
                        "steps were never tied to the report's own stated total; parse "
                        "it with strict=True before authoring an answer key from it"
                    )
            periods.append(
                AnswerKeyPeriod(
                    reporting_date=period.reporting_date,
                    period_label=period.period_label,
                    available_revenue_funds=period.available_revenue_funds,
                    available_principal_funds=period.available_principal_funds,
                    revenue_pop=[
                        AnswerKeyPopStep(
                            priority=s.priority, amount=s.amount, recipient=s.recipient
                        )
                        for s in period.revenue_pop
                    ],
                    redemption_pop=[
                        AnswerKeyPopStep(
                            priority=s.priority, amount=s.amount, recipient=s.recipient
                        )
                        for s in period.redemption_pop
                    ],
                    pool_stats={
                        f"{APPLIED_RATE_STAT_PREFIX}{balance.note_class}": float(
                            balance.interest_rate_applied
                        )
                        for balance in period.note_balances
                        if balance.interest_rate_applied is not None
                    },
                )
            )
        return cls(
            deal_id=deal_id,
            deal_name=deal_name,
            tolerance_eur=tolerance_eur,
            periods=periods,
        )


# ===========================================================================
# Union — one deal, two published documents, one committed key
# ===========================================================================


def merge_answer_keys(*keys: DealAnswerKey) -> DealAnswerKey:
    """Union several keys for **one** deal into the single key that gets committed.

    A deal can publish its ground truth across more than one document: Cairn CLO
    XVII states its coverage-test results in monthly trustee reports and its two
    Priorities of Payments in a quarterly Note Valuation Report, and each is read
    by its own constructor. This is the seam that puts them in one file, so a
    key stays "everything this deal publishes" rather than "whichever document
    was read last".

    Periods are unioned and returned in reporting-date order — a stable order, so
    the committed JSON is a function of the documents rather than of the order
    they were passed in, which is what makes the byte-for-byte regeneration
    regression meaningful.

    Two refusals:

    - keys that disagree about ``deal_id``, ``deal_name``, ``tolerance_eur`` or
      ``format_version`` are not two views of one deal; merging them would file
      one deal's ground truth under another's identity, or silently adopt one of
      two disagreeing tolerances as the gate every step is graded to;
    - two keys carrying the **same reporting date** would need a field-by-field
      reconciliation of two documents' figures for one period. Nothing here can
      decide which is right, and picking one would publish a figure as ground
      truth on no authority, so it refuses instead.
    """
    if not keys:
        raise ValueError("merge_answer_keys needs at least one key")
    first, *rest = keys
    for other in rest:
        for field in ("deal_id", "deal_name", "tolerance_eur", "format_version"):
            if getattr(other, field) != getattr(first, field):
                raise ValueError(
                    f"refusing to merge answer keys disagreeing on {field}: "
                    f"{getattr(first, field)!r} vs {getattr(other, field)!r}"
                )
    periods: dict[str, AnswerKeyPeriod] = {}
    for key in keys:
        for period in key.periods:
            if period.reporting_date in periods:
                raise ValueError(
                    f"two answer keys both carry reporting date "
                    f"{period.reporting_date!r}; reconciling one period's figures "
                    "across two documents is not something this can decide"
                )
            periods[period.reporting_date] = period
    return first.model_copy(
        update={"periods": [periods[date] for date in sorted(periods)]}
    )


# ===========================================================================
# Loader — resolve a deal's committed answer key from the data dir
# ===========================================================================


def answer_key_path(deal_name: str, *, base_dir: Path | None = None) -> Path:
    """The committed answer-key path for a deal name.

    Resolved by the same deal-name slug the committed seed model uses
    (``data/deals/seed/<slug>.json`` → ``data/deals/answer_keys/<slug>.json``),
    so a deal's seed and answer key sit under one naming convention. ``base_dir``
    overrides the package dir (a patchable seam for tests).
    """
    root = base_dir if base_dir is not None else ANSWER_KEY_DATA_DIR
    return root / f"{_slug(deal_name)}.json"


def load_answer_key(
    deal: Mapping[str, Any] | str,
    *,
    base_dir: Path | None = None,
) -> DealAnswerKey | None:
    """Load a deal's committed :class:`DealAnswerKey`, or ``None`` if none exists.

    Mirrors ``api.main._load_cached_deal_model`` — accepts a deal-context mapping
    (reads ``deal["deal_name"]``, the shape the registry yields) or a bare deal
    name string. Resolves :func:`answer_key_path`; a miss returns ``None`` so the
    caller degrades honestly (no fabricated ground truth). A *present* but
    malformed / schema-invalid file raises pydantic ``ValidationError``
    (``model_validate_json`` wraps a JSON-decode error too) rather than being
    silently swallowed — a corrupt answer key must fail loudly, not grade nothing.
    """
    deal_name = deal["deal_name"] if isinstance(deal, Mapping) else deal
    path = answer_key_path(deal_name, base_dir=base_dir)
    if not path.exists():
        return None
    return DealAnswerKey.model_validate_json(path.read_text(encoding="utf-8"))


def write_answer_key(
    key: DealAnswerKey,
    *,
    base_dir: Path | None = None,
) -> Path:
    """Write ``key`` to its committed answer-key path; return the path.

    The authoring counterpart to :func:`load_answer_key` (used by tests and by
    the backfill #429). Writes pretty JSON keyed by the deal-name slug.
    """
    path = answer_key_path(key.deal_name, base_dir=base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(key.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


# ===========================================================================
# Published thresholds — quantifying a trigger the prospectus left open
# ===========================================================================


def published_thresholds(key: DealAnswerKey) -> dict[str, float]:
    """Covenant name → the threshold this key publishes for it, where unambiguous.

    A name whose periods publish **different** thresholds, or none at all, is
    absent from the result. Silence is the honest answer: the caller leaves such
    a trigger unquantified, and the monitor reports it *not evaluable* with a
    reason, which is what a reader needs to see. Picking one of two disagreeing
    published levels would grade every period against a threshold that was not
    in force for some of them.
    """
    seen: dict[str, set[float]] = {}
    for period in key.periods:
        for covenant in period.covenants:
            if covenant.threshold is not None:
                seen.setdefault(covenant.name, set()).add(covenant.threshold)
    return {name: next(iter(values)) for name, values in seen.items() if len(values) == 1}


def published_metric_values(
    period: AnswerKeyPeriod,
    metric_by_name: Mapping[str, str],
) -> dict[str, float]:
    """Metric name → the value this period publishes for it, where unambiguous.

    The value half of the same transfer :func:`quantify_triggers` makes for
    thresholds: a report that states a test's computed ratio beside its result
    has already published the figure the monitor would otherwise have to resolve
    from state it cannot reach. ``CovenantResult.actual`` is where that figure
    lives, and ``metric_by_name`` maps each published covenant onto the metric
    name its trigger reads.

    Two triggers may legitimately share one metric. If they publish *different*
    values for it this period, the metric is omitted — the same silence
    :func:`published_thresholds` keeps, and for the same reason: picking one of
    two disagreeing published figures would put a wrong number three layers
    downstream, where it reads as a grading verdict rather than as ambiguity.
    """
    seen: dict[str, set[float]] = {}
    for covenant in period.covenants:
        metric = metric_by_name.get(covenant.name)
        if metric is not None and covenant.actual is not None:
            seen.setdefault(metric, set()).add(covenant.actual)
    return {metric: next(iter(values)) for metric, values in seen.items() if len(values) == 1}


def quantify_triggers(
    triggers: Iterable[TriggerDefinition],
    key: DealAnswerKey,
) -> list[TriggerDefinition]:
    """Fill each trigger's *absent* threshold from the answer key's published one.

    A prospectus extraction states which coverage tests exist and what they
    consequence, but a CLO's *required levels* live in its periodic reporting,
    not its offering document — so an extracted coverage trigger arrives with
    ``threshold=None`` and the monitor, correctly, refuses to evaluate it (a
    ratio has nothing to be tested against). The deal's own trustee report
    states the level beside the result. This is the one seam that hands it over.

    **The direction of travel is the whole contract.** A *published* figure may
    quantify an engine trigger; an engine figure must never reach an answer
    key. So this function only ever *fills an absence*:

    - a trigger that already carries a threshold is returned **unchanged**,
      whatever the key publishes — an extracted or configured level outranks a
      reported one, and letting a key override it would let ground truth
      silently redefine the test it is grading;
    - a name the key does not publish unambiguously (see
      :func:`published_thresholds`) is likewise returned unchanged, so it stays
      honestly *not evaluable* rather than being quantified by a guess.

    Order and identity are preserved: the result is one entry per input, in
    input order, each either the original object or a copy differing only in
    ``threshold``.
    """
    available = published_thresholds(key)
    quantified: list[TriggerDefinition] = []
    for trigger in triggers:
        # Fill-only-absences. This condition IS the contract above; a test in
        # tests/test_quality_harness.py reds if it is ever relaxed to override.
        if trigger.threshold is None and trigger.name in available:
            quantified.append(trigger.model_copy(update={"threshold": available[trigger.name]}))
        else:
            quantified.append(trigger)
    return quantified


# ===========================================================================
# Reconciler-consume adapter — config-loaded ground truth → reconciliation
# ===========================================================================


def reconcile_against_answer_key(
    series: DealStateSeries,
    answer_key: DealAnswerKey,
    *,
    tolerance: float | None = None,
) -> ReconciliationReport:
    """Reconcile a folded engine series against a config-loaded answer key.

    The deliverable of #427: the seam that lets the existing to-the-cent
    reconciler (:func:`~loanwhiz.primitives.reconciler.reconcile_series`) consume
    *data* (a :class:`DealAnswerKey`) instead of a hand-built builder. Projects
    the answer key's PoP onto a :class:`NotesCashReport` and reconciles the folded
    series against it. ``tolerance`` defaults to the answer key's own
    ``tolerance_eur``. The quality_harness (#428) calls this per deal.

    **Grades the PoP-bearing periods, reports the rest not-applicable (#513).** A
    key unioned from several document families is only partly PoP-bearing: Cairn
    CLO XVII's key carries four periods, of which the three authored from monthly
    trustee reports state covenant outcomes and no Priority of Payments, and only
    the one authored from the quarterly Note Valuation Report states both. A fold
    can only produce a period result from a document that publishes a cascade, so
    projecting all four periods handed :func:`reconcile_series` four report
    periods against one period result and it refused the whole key on the join —
    #496 had to bypass the key entirely to reach a verdict. Grading the gradeable
    subset and naming the remainder is the honest answer; the key's shape is
    correct and deliberate (#495) and is not what needed fixing.

    A skipped period is **not** a pass. It never enters ``report.periods``, so it
    is invisible to ``passed``, ``periods_checked``, ``periods_passed`` and every
    downstream tally — see :class:`~loanwhiz.primitives.reconciler.SkippedPeriod`
    for why an empty-waterfall period would have read as one.

    Raises ``ValueError`` when the key carries no Priority of Payments at all
    (there is nothing to grade, and an empty report is not the same claim as a
    graded one), or when the PoP-bearing period count still does not match the
    series — the positional join is only meaningful if the series was folded from
    exactly those documents.
    """
    pop_periods = answer_key.pop_periods
    if not pop_periods:
        raise ValueError(
            f"Answer key for {answer_key.deal_name} carries no Priority-of-Payments "
            f"section in any of its {len(answer_key.periods)} period(s), so there is "
            "nothing for the engine to be reconciled against. Grade its covenant or "
            "pool-statistic ground truth instead."
        )
    if len(series.period_results) != len(pop_periods):
        # Distinct from reconcile_series' own join message on purpose: there the
        # counts are the report's, here one side has already been narrowed, and an
        # operator told "the report has 1 period" of a four-period key would be
        # reading about a report that does not exist.
        raise ValueError(
            "Reconciler join mismatch: the folded series has "
            f"{len(series.period_results)} period result(s) but the answer key for "
            f"{answer_key.deal_name} carries {len(pop_periods)} Priority-of-Payments "
            f"period(s) (of {len(answer_key.periods)} total). The series must be "
            "folded from exactly the documents those periods were authored from."
        )

    report = answer_key.to_notes_cash_report(pop_periods_only=True)
    reconciliation = reconcile_series(
        series,
        report,
        deal_name=answer_key.deal_name,
        tolerance=tolerance if tolerance is not None else answer_key.tolerance_eur,
    )
    return reconciliation.model_copy(
        update={
            "skipped_periods": [
                SkippedPeriod(
                    reporting_date=p.reporting_date,
                    period_label=p.period_label,
                    reason=NO_POP_PERIOD_REASON,
                )
                for p in answer_key.non_pop_periods
            ]
        }
    )
