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

attachable per deal under ``data/deals/answer_keys/<slug>.json`` (resolved by
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
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from loanwhiz.config import ANSWER_KEY_DATA_DIR
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
    reconcile_series,
)

#: The current answer-key schema version. Bump on a breaking shape change so a
#: stale committed key fails loudly at load rather than mis-grading silently.
ANSWER_KEY_FORMAT_VERSION = 1


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

    # --- bridge to the reconciler's report side -------------------------------

    def to_notes_cash_report(self) -> NotesCashReport:
        """Project the PoP ground truth onto a :class:`NotesCashReport`.

        The bridge into :func:`~loanwhiz.primitives.reconciler.reconcile_series`:
        each :class:`AnswerKeyPeriod` becomes a
        :class:`~loanwhiz.primitives.notes_cash_parser.NotesCashPeriod` carrying
        the published revenue + redemption PoP and the available-funds totals —
        the exact surface the reconciler reads. Covenant / pool-stat ground truth
        is not part of the PoP report and is graded separately (#428).
        """
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
            for p in self.periods
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
    """
    report = answer_key.to_notes_cash_report()
    return reconcile_series(
        series,
        report,
        deal_name=answer_key.deal_name,
        tolerance=tolerance if tolerance is not None else answer_key.tolerance_eur,
    )
