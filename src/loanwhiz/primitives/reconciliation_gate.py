"""Reconciliation-as-gate — wire the ``Reconciler`` as an automated governance gate (#272).

The report path can do what the prospectus path cannot: **recompute the
distributions and check them** (design spec
``docs/superpowers/specs/2026-06-20-report-extractor-design.md`` →
"Reconciliation-as-gate"). This module composes the three pieces already on the
base branch into one governance flow::

    extract (report_extractor #271)
        -> ParsedReport (typed, per-field ProvenanceMap)
    adapt (ReportAdapter #267, via ParsedReport.to_notes_cash_report())
        -> (seed, PeriodInputs[])
    fold (run_period, via api.main.fold_report_series)
        -> DealStateSeries
    reconcile (Reconciler #270, reconcile_series)
        -> ReconciliationReport  (engine-computed vs report-stated, to the cent)

…and then **annotates the ``ParsedReport``'s provenance** from the
reconciliation result:

- A report field whose engine-computed value matched the report-stated value
  **to the cent** gets ``FieldProvenance.reconciled = True`` — the strong
  correctness signal, stronger than any extraction-confidence heuristic.
- The review gate (:func:`fields_for_human_review`) routes to a human **only**
  the fields that are *both* unreconciled *and* low-confidence. Reconciled
  fields are auto-trusted regardless of their extraction confidence.

This inverts the review burden: instead of a human checking everything the
extractor produced, they check only the handful the engine could not confirm.

Mapping discipline
------------------
The Reconciler proves *priority-of-payments step amounts* (keyed by priority
label within a period + waterfall: ``(d)`` → ``class_a_interest`` etc.). So the
gate marks ``reconciled`` at the **PoP-step amount** dotted paths
(``periods.{i}.revenue_pop.{k}.amount`` / ``…redemption_pop.{k}.amount``) — the
exact lines the Reconciler proves, a 1:1 mapping with each
:class:`~loanwhiz.primitives.reconciler.StepReconciliation`. A provenance entry
is created at that path when the deterministic/LLM extractor did not already key
it (the deterministic path keys only structural fields), preserving any existing
source / method / confidence / citation.

Pure & offline: composition over the existing primitives. The fold itself is the
shared ``api.main.fold_report_series`` (the same fold the live cold-start path
and the offline reconciler proof use), so this gate cannot drift from them.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from loanwhiz.domain.provenance import FieldProvenance, ProvenanceMap
from loanwhiz.primitives.reconciler import (
    DEFAULT_TOLERANCE_EUR,
    ReconciliationReport,
    StepReconciliation,
    WaterfallReconciliation,
    reconcile_series,
)
from loanwhiz.primitives.report_adapter import ReportAdapter
from loanwhiz.primitives.report_extractor import ParsedReport

#: Default extraction-confidence floor below which an *unreconciled* field is
#: routed to a human. Reconciled fields are exempt regardless of confidence.
DEFAULT_REVIEW_CONFIDENCE_THRESHOLD = 0.7

#: Maps a reconciliation waterfall type onto the ``ParsedReportPeriod`` PoP list
#: whose steps it proves.
_WATERFALL_POP_ATTR = {
    "revenue": "revenue_pop",
    "redemption": "redemption_pop",
}


# ===========================================================================
# Field-path helpers
# ===========================================================================


def _step_field_path(period_index: int, waterfall_type: str, step_index: int) -> str:
    """Dotted provenance path for one PoP step's amount.

    e.g. ``periods.0.revenue_pop.3.amount`` — the same dotted-path convention the
    extractor's :func:`~loanwhiz.primitives.report_extractor._deterministic_provenance`
    uses for structural fields.
    """
    pop_attr = _WATERFALL_POP_ATTR[waterfall_type]
    return f"periods.{period_index}.{pop_attr}.{step_index}.amount"


def _index_steps_by_label(steps: list[Any]) -> dict[str, int]:
    """Map a PoP list's priority labels to their index (first wins on duplicates)."""
    out: dict[str, int] = {}
    for i, step in enumerate(steps):
        if step.priority_label not in out:
            out[step.priority_label] = i
    return out


# ===========================================================================
# Annotation — mark reconciled fields in the ParsedReport's provenance
# ===========================================================================


def _mark_reconciled(provenance: ProvenanceMap, path: str) -> None:
    """Set ``reconciled=True`` at ``path``, creating the entry if absent.

    Preserves an existing entry's source / method / confidence / citation (only
    flips the ``reconciled`` flag); synthesizes a ``source="reconciled",
    method="computed", confidence=1.0`` entry when the extractor never keyed this
    field (the deterministic path does not key PoP-step amounts).
    """
    existing = provenance.get(path)
    if existing is not None:
        provenance[path] = existing.model_copy(update={"reconciled": True})
    else:
        provenance[path] = FieldProvenance(
            source="reconciled",
            method="computed",
            confidence=1.0,
            citation=None,
            reconciled=True,
        )


def _apply_waterfall(
    *,
    provenance: ProvenanceMap,
    period_index: int,
    recon_wf: WaterfallReconciliation,
    pop_steps: list[Any],
) -> int:
    """Mark each reconciled step of one waterfall; return the count marked."""
    label_to_index = _index_steps_by_label(pop_steps)
    marked = 0
    step: StepReconciliation
    for step in recon_wf.steps:
        if not step.passed:
            continue
        idx = label_to_index.get(step.priority)
        if idx is None:
            # The Reconciler folds some labels (e.g. revenue ``(b)`` sub-items
            # into one ``(b)`` total) that the ParsedReport may carry only as
            # sub-items; a folded label with no top-level PoP step has nothing to
            # mark. That is correct — only fields the report actually carries get
            # a reconciled flag.
            continue
        _mark_reconciled(
            provenance,
            _step_field_path(period_index, recon_wf.waterfall_type, idx),
        )
        marked += 1
    return marked


def apply_reconciliation(
    report: ParsedReport,
    recon: ReconciliationReport,
) -> int:
    """Mark the ``ParsedReport``'s provenance from a :class:`ReconciliationReport`.

    For each reconciliation period's revenue + redemption steps that reconciled
    (``passed``), set ``reconciled=True`` on the provenance entry for the matching
    PoP-step amount in ``report``. Mutates ``report.provenance`` in place and
    returns the number of fields newly confirmed.

    The reconciliation periods and the report periods are joined positionally —
    both were built from the same ordered period list (the same positional join
    :func:`~loanwhiz.primitives.reconciler.reconcile_series` itself asserts). A
    reconciliation period with no matching report period is skipped defensively.
    """
    marked = 0
    for index, pv in enumerate(recon.periods):
        if index >= len(report.periods):
            continue
        rp = report.periods[index]
        marked += _apply_waterfall(
            provenance=report.provenance,
            period_index=index,
            recon_wf=pv.revenue,
            pop_steps=rp.revenue_pop,
        )
        marked += _apply_waterfall(
            provenance=report.provenance,
            period_index=index,
            recon_wf=pv.redemption,
            pop_steps=rp.redemption_pop,
        )
    return marked


# ===========================================================================
# Review routing — only unreconciled + low-confidence fields reach a human
# ===========================================================================


class ReviewItem(BaseModel):
    """One report field routed to human review (unreconciled AND low-confidence)."""

    field_path: str = Field(..., description="Dotted provenance path of the field.")
    confidence: float = Field(..., description="The field's extraction confidence.")
    reason: str = Field(..., description="Why it was routed (human-readable).")


def fields_for_human_review(
    report: ParsedReport,
    *,
    confidence_threshold: float = DEFAULT_REVIEW_CONFIDENCE_THRESHOLD,
) -> list[ReviewItem]:
    """The fields a human must check: **unreconciled AND below the threshold**.

    A field with ``reconciled=True`` is auto-trusted and never routed, regardless
    of its extraction confidence (the inversion of the review burden — the engine
    confirmed it to the cent). An unreconciled field is routed only when its
    confidence is *strictly below* ``confidence_threshold``; a high-confidence
    unreconciled field is left alone. Returned in provenance-map order.
    """
    items: list[ReviewItem] = []
    for path, fp in report.provenance.items():
        if fp.reconciled:
            continue
        if fp.confidence < confidence_threshold:
            items.append(
                ReviewItem(
                    field_path=path,
                    confidence=fp.confidence,
                    reason=(
                        f"unreconciled and low-confidence "
                        f"({fp.confidence:.2f} < {confidence_threshold:.2f})"
                    ),
                )
            )
    return items


# ===========================================================================
# Top-level gate — extract → adapt → fold → reconcile → annotate → route
# ===========================================================================


class ReconciliationGateResult(BaseModel):
    """The outcome of running the reconciliation-as-gate over an extracted report.

    Attributes
    ----------
    report:
        The same :class:`ParsedReport` with its provenance annotated — reconciled
        fields carry ``reconciled=True``.
    reconciliation:
        The full engine-vs-report :class:`ReconciliationReport` (the proof).
    reconciled_field_count:
        How many report fields were confirmed to the cent.
    review_items:
        The fields routed to human review (unreconciled AND low-confidence).
    """

    report: ParsedReport
    reconciliation: ReconciliationReport
    reconciled_field_count: int
    review_items: list[ReviewItem]


def reconcile_as_gate(
    report: ParsedReport,
    model: Any,
    *,
    tolerance: float = DEFAULT_TOLERANCE_EUR,
    confidence_threshold: float = DEFAULT_REVIEW_CONFIDENCE_THRESHOLD,
    adapter: ReportAdapter | None = None,
) -> ReconciliationGateResult:
    """Run the full reconciliation-as-gate flow over an extracted ``ParsedReport``.

    Adapts the report into the engine's ``(seed, PeriodInputs[])`` via the #267
    :class:`ReportAdapter` (built from ``model``'s extracted waterfalls unless an
    ``adapter`` is supplied), folds it through ``run_period`` (the shared
    ``api.main.fold_report_series``), reconciles the folded engine against the
    report's published PoP to the cent (#270), then **marks the reconciled fields**
    in the report's provenance and **routes only the unreconciled + low-confidence
    fields** to human review.

    ``model`` is the deal's extracted model (duck-typed: any object exposing
    ``waterfalls["revenue"|"redemption"]["steps"]`` works — the same shape #267's
    :meth:`ReportAdapter.from_deal_model` consumes).

    Returns a :class:`ReconciliationGateResult`. Pure & offline — no network, no
    LLM (the import of the API-layer fold is deferred, mirroring the reconciler's
    offline builder, so this reader does not pull the API surface at module load).
    """
    # Deferred import (mirrors reconciler.fold_green_lion_2024_1): keep this pure
    # reader from pulling the API layer in at module load. The shared fold lives
    # there so the gate folds IDENTICALLY to the live cold-start + offline paths.
    from loanwhiz.api.main import fold_report_series

    adapter = adapter or ReportAdapter.from_deal_model(model)
    notes_cash = report.to_notes_cash_report()
    series = fold_report_series(model, notes_cash, adapter)
    recon = reconcile_series(
        series,
        notes_cash,
        deal_name=report.deal_name,
        revenue_residual_label=adapter.revenue_residual_label,
        redemption_residual_label=adapter.redemption_residual_label,
        tolerance=tolerance,
    )
    reconciled_count = apply_reconciliation(report, recon)
    review_items = fields_for_human_review(
        report, confidence_threshold=confidence_threshold
    )
    return ReconciliationGateResult(
        report=report,
        reconciliation=recon,
        reconciled_field_count=reconciled_count,
        review_items=review_items,
    )
