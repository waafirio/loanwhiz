"""Cross-deal comparison assembly (#283, Epic 7 — analyst-facing tools).

The pure assembly/alignment layer behind ``GET /compare`` — it takes the
per-deal artefacts the platform already produces (canonical :class:`DealRules`
from the cached ``DealModel`` and the reconstructed :class:`DealStateSeries`)
and *aligns* them into one N-way comparison payload that the dashboard view and
the drill-down chat both consume. **No new modelling** happens here — this is
assembly over already-validated outputs.

The design (``docs/superpowers/specs/2026-06-20-deal-comparison-tool-design.md``)
locks v1 to two panels plus a benchmark lens:

* **Panel 1 — structural diff** (:func:`build_structural_diff`): a column-per-
  deal table whose rows align by the canonical :class:`RecipientType` (waterfall
  steps) and :class:`MetricType` (triggers), so a step or covenant lines up
  across deals even when each issuer labels it differently. Cells where deals
  differ are diff-flagged; an ``unmapped`` recipient/metric is surfaced
  honestly as *not comparable* rather than coerced onto a wrong sentinel.
* **Panel 2 — performance / risk** (:func:`build_performance_panel`): one
  overlaid series per deal (pool factor, reserve balance/target, PDL,
  cumulative losses) on the intersection of reporting dates, plus a latest-
  period covenant proximity-to-breach risk-summary value per deal.
* **Benchmark lens** (:func:`apply_benchmark`): with a ``target`` designated,
  each comparable structural threshold and latest-period risk metric is
  annotated with the comp-set **median** (the non-target deals) and the
  target's signed deviation from it.

Everything in this module is a pure function over already-loaded models, so the
alignment / median maths is unit-testable without the FastAPI app or any
network. The endpoint in :mod:`loanwhiz.api.main` is a thin handler that loads
the per-deal models (reusing ``_load_cached_deal_model`` + ``_reconstruct_series``)
and calls into here.
"""

from __future__ import annotations

import re
import statistics
from typing import Any, Literal

from pydantic import BaseModel, Field

from loanwhiz.domain.rules import (
    DealRules,
    MetricType,
    RecipientType,
    StepRule,
    TriggerRule,
    WaterfallKind,
)
from loanwhiz.primitives.deal_state import DealState
from loanwhiz.primitives.relative_value_screener import (
    DIM_SUBORDINATION_CE,
    DIM_TRIGGER_HEADROOM,
    RelativeValueScorecard,
)

# The waterfalls compared in Panel 1, in the priority order an analyst reads
# them. ``post_enforcement`` is intentionally omitted from the default diff: it
# only applies after an event of default and would add a mostly-empty section
# for performing deals (it is still carried on each DealRules and can be added
# as an additive panel later).
_COMPARED_WATERFALLS: tuple[WaterfallKind, ...] = ("revenue", "redemption")


# ---------------------------------------------------------------------------
# Response shapes — what the endpoint returns and the view/chat consume.
# ---------------------------------------------------------------------------


class DealRef(BaseModel):
    """One deal in the comparison set, with provenance flags for honest UI."""

    deal_id: str
    deal_name: str
    jurisdiction: str
    vintage: int | None = Field(
        default=None, description="Origination year parsed from the deal name, if any."
    )
    is_target: bool = False
    # Honesty flags so a thinner deal isn't read as equivalent (spec's
    # "flag provenance/coverage differences" note).
    has_structural: bool = Field(
        default=False, description="A canonical DealRules was assembled for this deal."
    )
    has_performance: bool = Field(
        default=False, description="A DealStateSeries reconstructed for this deal."
    )
    performance_provenance: Literal["reported", "projected"] | None = Field(
        default=None,
        description=(
            "Provenance of this deal's Panel-2 series: 'reported' when "
            "reconstructed from the deal's own tape/report history, 'projected' "
            "when derived from the canonical model's forward projection "
            "(projected-not-reported), or None when no series is available."
        ),
    )
    note: str | None = Field(
        default=None, description="One-line honesty note when a panel is unavailable."
    )


class StructuralCell(BaseModel):
    """One deal's value for one aligned structural row."""

    deal_id: str
    present: bool = Field(
        default=False, description="False when this deal has no entry for the row."
    )
    label: str | None = Field(
        default=None, description="The deal's own (issuer) label for the step/trigger."
    )
    detail: str | None = Field(
        default=None, description="Human-readable cell value (recipient basis / threshold)."
    )
    value: float | None = Field(
        default=None, description="Numeric value for benchmarking (a normalised threshold)."
    )
    comparable: bool = Field(
        default=True, description="False for an ``unmapped`` recipient/metric — 'not comparable'."
    )
    # Benchmark annotations (populated by apply_benchmark when a target is set).
    comp_median: float | None = None
    deviation: float | None = Field(
        default=None, description="value - comp_median (signed), for a target cell."
    )


class StructuralRow(BaseModel):
    """One aligned row of the structural-diff table (a recipient or a metric)."""

    key: str = Field(..., description="Canonical row key: a RecipientType or MetricType value.")
    section: str = Field(..., description="'tranche' | 'waterfall:revenue' | 'waterfall:redemption' | 'trigger' | 'reserve'.")
    label: str = Field(..., description="Display label for the row.")
    differs: bool = Field(
        default=False, description="True when the deals' cells are not all equal — diff-highlight."
    )
    cells: list[StructuralCell] = Field(default_factory=list)


class PerformancePoint(BaseModel):
    """One (period, metric-bundle) sample for a deal's overlaid series."""

    reporting_date: str
    pool_factor: float
    reserve_balance: float
    reserve_target: float
    total_pdl: float
    cumulative_losses: float
    cumulative_loss_rate_pct: float


class RiskSummary(BaseModel):
    """Latest-period risk snapshot per deal (the triage row above Panel 2)."""

    deal_id: str
    latest_period: str | None = None
    # Worst (closest-to-breach) covenant in the latest period.
    tightest_trigger: str | None = None
    tightest_proximity_pct: float | None = None
    active_triggers: list[str] = Field(default_factory=list)
    near_miss_triggers: list[str] = Field(default_factory=list)
    latest_pool_factor: float | None = None
    latest_cumulative_loss_rate_pct: float | None = None
    # Benchmark annotations (proximity vs comp-set median), set by apply_benchmark.
    comp_median_proximity_pct: float | None = None
    proximity_deviation: float | None = None


class PerformanceSeries(BaseModel):
    """One deal's overlaid performance series for Panel 2."""

    deal_id: str
    points: list[PerformancePoint] = Field(default_factory=list)


class ComparativeVerdict(BaseModel):
    """The reasoned "why one deal is better" answer over the comparison set (#400).

    Grounds the verdict in the relative-value scorecard's *available* (real,
    structural) factors and the latest-period covenant proximity — never a
    fabricated winner. When fewer than two deals produced a scored tranche the
    set cannot be ranked honestly, so ``confidence`` is ``"insufficient-data"``,
    ``winner_deal_id`` is ``None``, and ``summary`` says what was missing.
    """

    confidence: Literal["scored", "insufficient-data"] = Field(
        ...,
        description=(
            "'scored' when >=2 deals produced a ranked tranche; "
            "'insufficient-data' when the structural inputs were too thin to rank."
        ),
    )
    winner_deal_id: str | None = Field(
        default=None,
        description="Deal with the best top-ranked tranche, or None when insufficient-data.",
    )
    winner_deal_name: str | None = Field(
        default=None, description="Human name of the winning deal, or None."
    )
    ranking: list[str] = Field(
        default_factory=list,
        description="Deal ids best->worst by their best tranche's composite (scored deals only).",
    )
    summary: str = Field(
        ..., description="One-line plain verdict, honest about the basis or the gap."
    )
    reasons: list[str] = Field(
        default_factory=list,
        description="Grounded one-liners citing real structural values (CE, trigger coverage, proximity).",
    )
    caveats: list[str] = Field(
        default_factory=list,
        description="Honest 'unavailable / live-data-required' flags, sourced from real RvFactor reasons.",
    )


class CompareResponse(BaseModel):
    """The single comparison payload the view + chat both consume."""

    deals: list[DealRef]
    target_deal_id: str | None = None
    structural_rows: list[StructuralRow] = Field(default_factory=list)
    performance_series: list[PerformanceSeries] = Field(default_factory=list)
    risk_summary: list[RiskSummary] = Field(default_factory=list)
    common_periods: list[str] = Field(
        default_factory=list, description="Reporting dates shared by all performance-bearing deals."
    )
    comp_suggestions: list[str] = Field(
        default_factory=list,
        description="Registry deal_ids (not in the set) sharing the target's jurisdiction/vintage.",
    )
    relative_value: RelativeValueScorecard | None = Field(
        default=None,
        description="Cross-deal relative-value scorecard scoped to this comparison set (#400).",
    )
    comparative_verdict: ComparativeVerdict | None = Field(
        default=None,
        description="Reasoned 'why one deal is better' verdict grounded in the scorecard (#400).",
    )
    notes: list[str] = Field(
        default_factory=list, description="Honesty notes about coverage / provenance differences."
    )


# ---------------------------------------------------------------------------
# Small pure helpers.
# ---------------------------------------------------------------------------


def parse_vintage(deal_name: str) -> int | None:
    """Recover an origination year (vintage) from a deal name, e.g. 2023.

    Most EDW deal names embed the vintage (``Green Lion 2024-1``,
    ``Leone Arancio RMBS 2023-1``). Returns the first 19xx/20xx year found, or
    ``None`` when the name carries no year.
    """
    m = re.search(r"\b(19|20)\d{2}\b", deal_name)
    return int(m.group(0)) if m else None


def _recipient_label(recipient: RecipientType) -> str:
    """Human-readable label for a canonical recipient row."""
    return recipient.value.replace("_", " ").title()


def _metric_label(metric: MetricType) -> str:
    """Human-readable label for a canonical metric row."""
    return metric.value.replace("_", " ").title()


def _normalise_threshold(trigger: TriggerRule) -> float | None:
    """A unit-normalised numeric threshold for cross-deal comparison.

    ``TriggerRule.threshold_unit`` is normalised once at extraction time, but a
    cross-deal comparison still needs a single comparable scale. We express
    every threshold as a **fraction** (``percent`` /100, ``bps`` /10_000,
    ``fraction`` as-is). ``eur`` thresholds are absolute amounts and are left
    on their own scale (returned as-is) — comparing two deals' absolute EUR
    floors is still meaningful and the UI labels the unit. ``None`` thresholds
    (qualitative triggers) stay ``None``.
    """
    if trigger.threshold is None:
        return None
    unit = trigger.threshold_unit
    if unit == "percent":
        return trigger.threshold / 100.0
    if unit == "bps":
        return trigger.threshold / 10_000.0
    # "fraction" and "eur" are already on their own comparable scale.
    return trigger.threshold


# ---------------------------------------------------------------------------
# Panel 1 — structural diff (aligned by RecipientType / MetricType).
# ---------------------------------------------------------------------------


def _tranche_rows(rules_by_deal: dict[str, DealRules], order: list[str]) -> list[StructuralRow]:
    """Rows for the tranche stack, aligned by seniority rank across deals.

    A deal's tranches are ordered by ``seniority`` (0 = senior); we align by
    rank so "the senior note" lines up even when names differ. Each cell shows
    the original balance + rating.
    """
    max_tranches = max(
        (len(r.tranches) for r in rules_by_deal.values()), default=0
    )
    rows: list[StructuralRow] = []
    for rank in range(max_tranches):
        cells: list[StructuralCell] = []
        details: list[str | None] = []
        for deal_id in order:
            rules = rules_by_deal.get(deal_id)
            ranked = sorted(rules.tranches, key=lambda t: t.seniority) if rules else []
            if rules is None or rank >= len(ranked):
                cells.append(StructuralCell(deal_id=deal_id, present=False))
                details.append(None)
                continue
            tr = ranked[rank]
            detail = f"{tr.original_balance:,.0f} {rules.currency}"
            if tr.rating:
                detail += f" · {tr.rating}"
            cells.append(
                StructuralCell(
                    deal_id=deal_id,
                    present=True,
                    label=tr.name,
                    detail=detail,
                    value=tr.original_balance,
                )
            )
            details.append(detail)
        rows.append(
            StructuralRow(
                key=f"tranche_rank_{rank}",
                section="tranche",
                label=f"Tranche (seniority {rank})",
                differs=_cells_differ(details),
                cells=cells,
            )
        )
    return rows


def _first_step_for(rules: DealRules, kind: WaterfallKind, recipient: RecipientType) -> StepRule | None:
    """The first step paying ``recipient`` in ``rules``' ``kind`` waterfall."""
    for step in rules.waterfalls.get(kind, []):
        if step.recipient == recipient:
            return step
    return None


def _waterfall_rows(
    rules_by_deal: dict[str, DealRules], order: list[str], kind: WaterfallKind
) -> list[StructuralRow]:
    """Rows for one waterfall, aligned by canonical ``RecipientType``.

    The row order follows the canonical ``RecipientType`` enum (senior →
    junior), so steps line up across deals. ``unmapped`` steps are surfaced as
    a single "not comparable" row carrying each deal's own labels, never
    coerced into a comparable recipient.
    """
    # Which recipients appear in any deal's waterfall of this kind.
    present_recipients: list[RecipientType] = [
        rec
        for rec in RecipientType
        if rec is not RecipientType.unmapped
        and any(
            _first_step_for(r, kind, rec) is not None for r in rules_by_deal.values()
        )
    ]
    rows: list[StructuralRow] = []
    for rec in present_recipients:
        cells: list[StructuralCell] = []
        details: list[str | None] = []
        for deal_id in order:
            rules = rules_by_deal.get(deal_id)
            step = _first_step_for(rules, kind, rec) if rules else None
            if step is None:
                cells.append(StructuralCell(deal_id=deal_id, present=False))
                details.append(None)
                continue
            detail = step.amount.basis
            if step.condition is not None:
                detail += f" · gated on {step.condition.trigger_name} ({step.condition.when})"
            cells.append(
                StructuralCell(
                    deal_id=deal_id,
                    present=True,
                    label=step.priority_label,
                    detail=detail,
                )
            )
            details.append(detail)
        rows.append(
            StructuralRow(
                key=rec.value,
                section=f"waterfall:{kind}",
                label=_recipient_label(rec),
                differs=_cells_differ(details),
                cells=cells,
            )
        )

    # One honest "not comparable" row aggregating each deal's unmapped steps.
    unmapped_cells: list[StructuralCell] = []
    any_unmapped = False
    for deal_id in order:
        rules = rules_by_deal.get(deal_id)
        unmapped = (
            [s for s in rules.waterfalls.get(kind, []) if s.recipient == RecipientType.unmapped]
            if rules
            else []
        )
        if unmapped:
            any_unmapped = True
            labels = ", ".join(s.priority_label for s in unmapped)
            unmapped_cells.append(
                StructuralCell(
                    deal_id=deal_id,
                    present=True,
                    label=labels,
                    detail=f"{len(unmapped)} step(s) not comparable",
                    comparable=False,
                )
            )
        else:
            unmapped_cells.append(StructuralCell(deal_id=deal_id, present=False, comparable=False))
    if any_unmapped:
        rows.append(
            StructuralRow(
                key=f"{kind}_unmapped",
                section=f"waterfall:{kind}",
                label="Unmapped steps (not comparable)",
                differs=False,
                cells=unmapped_cells,
            )
        )
    return rows


def _trigger_rows(rules_by_deal: dict[str, DealRules], order: list[str]) -> list[StructuralRow]:
    """Rows for triggers, aligned by canonical ``MetricType`` with thresholds.

    A deal can have several triggers on one metric (rare); we surface the first
    one per metric in the comparable row and fold the rest into the unmapped /
    extra handling implicitly (the first is the load-bearing covenant). The
    normalised threshold is the benchmarkable ``value``.
    """
    present_metrics: list[MetricType] = [
        m
        for m in MetricType
        if m is not MetricType.unmapped
        and any(
            any(t.metric == m for t in r.triggers) for r in rules_by_deal.values()
        )
    ]
    rows: list[StructuralRow] = []
    for metric in present_metrics:
        cells: list[StructuralCell] = []
        details: list[str | None] = []
        for deal_id in order:
            rules = rules_by_deal.get(deal_id)
            trig = next((t for t in rules.triggers if t.metric == metric), None) if rules else None
            if trig is None:
                cells.append(StructuralCell(deal_id=deal_id, present=False))
                details.append(None)
                continue
            norm = _normalise_threshold(trig)
            if trig.threshold is None:
                detail = "qualitative"
            else:
                detail = f"{trig.operator} {trig.threshold:g} {trig.threshold_unit}"
            cells.append(
                StructuralCell(
                    deal_id=deal_id,
                    present=True,
                    label=trig.name,
                    detail=detail,
                    value=norm,
                )
            )
            details.append(detail)
        rows.append(
            StructuralRow(
                key=metric.value,
                section="trigger",
                label=_metric_label(metric),
                differs=_cells_differ(details),
                cells=cells,
            )
        )

    # Honest "not comparable" row for unmapped-metric triggers.
    unmapped_cells: list[StructuralCell] = []
    any_unmapped = False
    for deal_id in order:
        rules = rules_by_deal.get(deal_id)
        unmapped = (
            [t for t in rules.triggers if t.metric == MetricType.unmapped] if rules else []
        )
        if unmapped:
            any_unmapped = True
            unmapped_cells.append(
                StructuralCell(
                    deal_id=deal_id,
                    present=True,
                    label=", ".join(t.name for t in unmapped),
                    detail=f"{len(unmapped)} trigger(s) not comparable",
                    comparable=False,
                )
            )
        else:
            unmapped_cells.append(StructuralCell(deal_id=deal_id, present=False, comparable=False))
    if any_unmapped:
        rows.append(
            StructuralRow(
                key="trigger_unmapped",
                section="trigger",
                label="Unmapped triggers (not comparable)",
                differs=False,
                cells=unmapped_cells,
            )
        )
    return rows


def _reserve_rows(rules_by_deal: dict[str, DealRules], order: list[str]) -> list[StructuralRow]:
    """Two rows for the reserve mechanics: floor and pct-of-note-balance."""
    rows: list[StructuralRow] = []
    for attr, label, key in (
        ("floor", "Reserve floor", "reserve_floor"),
        ("pct_of_note_balance", "Reserve % of note balance", "reserve_pct"),
    ):
        cells: list[StructuralCell] = []
        details: list[str | None] = []
        for deal_id in order:
            rules = rules_by_deal.get(deal_id)
            if rules is None:
                cells.append(StructuralCell(deal_id=deal_id, present=False))
                details.append(None)
                continue
            val = getattr(rules.reserve, attr)
            if val is None:
                detail = "—"
                cells.append(StructuralCell(deal_id=deal_id, present=False, detail=detail))
                details.append(None)
                continue
            detail = f"{val:,.0f} {rules.currency}" if attr == "floor" else f"{val:.2%}"
            cells.append(
                StructuralCell(deal_id=deal_id, present=True, detail=detail, value=float(val))
            )
            details.append(detail)
        rows.append(
            StructuralRow(
                key=key,
                section="reserve",
                label=label,
                differs=_cells_differ(details),
                cells=cells,
            )
        )
    return rows


def _cells_differ(values: list[Any]) -> bool:
    """True when the present (non-None) cell values are not all equal."""
    present = [v for v in values if v is not None]
    return len(set(present)) > 1


def build_structural_diff(
    rules_by_deal: dict[str, DealRules], order: list[str]
) -> list[StructuralRow]:
    """Assemble Panel 1: tranche stack, waterfalls, triggers, reserve.

    ``order`` is the deal_id column order (the comparison set as requested).
    ``rules_by_deal`` carries a ``DealRules`` for every deal that has one; a
    deal absent from the dict renders empty (``present=False``) cells so the
    column still appears.
    """
    rows: list[StructuralRow] = []
    rows.extend(_tranche_rows(rules_by_deal, order))
    for kind in _COMPARED_WATERFALLS:
        rows.extend(_waterfall_rows(rules_by_deal, order, kind))
    rows.extend(_trigger_rows(rules_by_deal, order))
    rows.extend(_reserve_rows(rules_by_deal, order))
    return rows


# ---------------------------------------------------------------------------
# Panel 2 — performance / risk (overlaid series + risk summary).
# ---------------------------------------------------------------------------


def _series_points(states: list[DealState]) -> list[PerformancePoint]:
    """Map a deal's reconstructed states onto Panel-2 series points."""
    return [
        PerformancePoint(
            reporting_date=s.reporting_date,
            pool_factor=s.pool_factor,
            reserve_balance=s.reserve_balance,
            reserve_target=s.reserve_target,
            total_pdl=s.total_pdl,
            cumulative_losses=s.cumulative_losses,
            cumulative_loss_rate_pct=s.cumulative_loss_rate_pct,
        )
        for s in states
    ]


def intersect_periods(states_by_deal: dict[str, list[DealState]]) -> list[str]:
    """The reporting dates shared by every performance-bearing deal.

    Used as the shared period axis for the overlaid series. Returns the sorted
    intersection; empty when the deals share no period (or only one deal has a
    series, in which case the intersection is that deal's own dates).
    """
    date_sets = [
        {s.reporting_date for s in states} for states in states_by_deal.values() if states
    ]
    if not date_sets:
        return []
    common = set.intersection(*date_sets) if len(date_sets) > 1 else date_sets[0]
    return sorted(common)


def build_performance_panel(
    states_by_deal: dict[str, list[DealState]], order: list[str]
) -> tuple[list[PerformanceSeries], list[str]]:
    """Assemble Panel-2 overlaid series + the common-period axis.

    Each deal's full series is emitted (the UI overlays them); ``common_periods``
    is the intersection so the UI can default the shared axis to where every
    deal has data without hiding the longer histories.
    """
    series = [
        PerformanceSeries(deal_id=deal_id, points=_series_points(states_by_deal[deal_id]))
        for deal_id in order
        if states_by_deal.get(deal_id)
    ]
    return series, intersect_periods(states_by_deal)


# ---------------------------------------------------------------------------
# Benchmark lens — comp-set median + per-target deviation.
# ---------------------------------------------------------------------------


def _comp_median(values: list[float]) -> float | None:
    """Median of the comp-set values, or ``None`` when the comp set is empty."""
    return statistics.median(values) if values else None


def apply_benchmark(
    response: CompareResponse, target_deal_id: str
) -> CompareResponse:
    """Annotate structural cells + risk summaries with comp-set deviations.

    For every comparable structural row carrying numeric ``value``s, compute the
    **median** over the non-target deals' cells and the target cell's signed
    deviation from it; likewise for each deal's latest-period tightest-covenant
    proximity in the risk summary. Mutates and returns ``response`` (the caller
    owns it). A no-op when there are no comps (a single-deal set with a target).
    """
    comp_ids = [d.deal_id for d in response.deals if d.deal_id != target_deal_id]

    for row in response.structural_rows:
        comp_values = [
            c.value
            for c in row.cells
            if c.comparable and c.value is not None and c.deal_id in comp_ids
        ]
        median = _comp_median(comp_values)
        if median is None:
            continue
        for cell in row.cells:
            if not cell.comparable or cell.value is None:
                continue
            cell.comp_median = median
            if cell.deal_id == target_deal_id:
                cell.deviation = cell.value - median

    # Risk-summary benchmark: tightest-proximity vs comp median.
    comp_prox = [
        rs.tightest_proximity_pct
        for rs in response.risk_summary
        if rs.deal_id in comp_ids and rs.tightest_proximity_pct is not None
    ]
    median_prox = _comp_median(comp_prox)
    if median_prox is not None:
        for rs in response.risk_summary:
            if rs.tightest_proximity_pct is None:
                continue
            rs.comp_median_proximity_pct = median_prox
            if rs.deal_id == target_deal_id:
                rs.proximity_deviation = rs.tightest_proximity_pct - median_prox

    return response


# ---------------------------------------------------------------------------
# Comparative verdict — reasoned "why one deal is better" over the comp set.
# ---------------------------------------------------------------------------


def _best_tranche_per_deal(scorecard: RelativeValueScorecard) -> dict[str, Any]:
    """The highest-composite scored tranche for each deal in the scorecard.

    The scorecard ranks tranches cross-deal; for a deal-level verdict we take
    each deal's *best* (lowest-rank / highest-composite) scored tranche as that
    deal's representative — typically its senior note. Deals whose tranches all
    came back unscored (``composite_score is None``) are omitted; they cannot be
    ranked honestly.
    """
    best: dict[str, Any] = {}
    for tr in scorecard.tranches:
        if tr.composite_score is None:
            continue
        cur = best.get(tr.deal_id)
        if cur is None or tr.composite_score > cur.composite_score:
            best[tr.deal_id] = tr
    return best


def build_comparative_verdict(
    scorecard: RelativeValueScorecard,
    deals: list[DealRef],
    risk_summary: list[RiskSummary],
    target_deal_id: str | None = None,
) -> ComparativeVerdict:
    """Turn the relative-value scorecard into a reasoned, honest deal verdict (#400).

    Pure and deterministic — no LLM, no network. Ranks the deals by their best
    scored tranche's composite, composes grounded ``reasons`` from the
    *available* structural factors (credit enhancement, trigger coverage) and
    each deal's latest-period covenant proximity, and collects ``caveats`` from
    every factor the screener honestly flagged ``available=False``.

    When fewer than two deals produced a scored tranche the set cannot be ranked
    honestly: ``confidence`` is ``"insufficient-data"``, ``winner_deal_id`` is
    ``None``, and ``summary`` names what was missing — never a fabricated winner.
    """
    name_by_id = {d.deal_id: d.deal_name for d in deals}
    proximity_by_id = {
        rs.deal_id: rs.tightest_proximity_pct
        for rs in risk_summary
        if rs.tightest_proximity_pct is not None
    }
    tightest_name_by_id = {
        rs.deal_id: rs.tightest_trigger for rs in risk_summary if rs.tightest_trigger
    }

    best = _best_tranche_per_deal(scorecard)

    # Caveats: the honest unavailable-dimension flags, deduped, sourced from the
    # screener's real RvFactor.reason strings (never fabricated).
    caveats: list[str] = []
    seen_caveats: set[str] = set()
    for tr in scorecard.tranches:
        deal_name = name_by_id.get(tr.deal_id, tr.deal_name)
        for factor in tr.factors.values():
            if factor.available:
                continue
            caveat = f"{deal_name} · {factor.dimension}: {factor.reason}"
            if caveat not in seen_caveats:
                seen_caveats.add(caveat)
                caveats.append(caveat)

    if len(best) < 2:
        missing = [
            name_by_id.get(d.deal_id, d.deal_id)
            for d in deals
            if d.deal_id not in best
        ]
        if best:
            scored_name = name_by_id.get(next(iter(best)), next(iter(best)))
            summary = (
                f"Insufficient data to rank: only {scored_name} produced a scored "
                "tranche from the available structural data. "
                f"No comparable score for: {', '.join(missing) or 'the other deals'}."
            )
        else:
            summary = (
                "Insufficient data to rank: no deal in the set produced a scorable "
                "capital structure from the committed extracted models."
            )
        return ComparativeVerdict(
            confidence="insufficient-data",
            winner_deal_id=None,
            winner_deal_name=None,
            ranking=[],
            summary=summary,
            reasons=[],
            caveats=caveats,
        )

    # Rank deals by their best tranche's composite, desc; deterministic tie-break
    # on deal_id (mirrors the scorecard's own stable ordering).
    ranked_ids = sorted(
        best.keys(),
        key=lambda did: (-best[did].composite_score, did),
    )
    winner_id = ranked_ids[0]
    winner_tr = best[winner_id]
    winner_name = name_by_id.get(winner_id, winner_tr.deal_name)

    reasons: list[str] = []
    # Composite headline.
    runner_up_id = ranked_ids[1]
    reasons.append(
        f"{winner_name} ({winner_tr.tranche_name}) leads on composite relative-value "
        f"score {winner_tr.composite_score:.0f}/100 vs "
        f"{name_by_id.get(runner_up_id, runner_up_id)} "
        f"({best[runner_up_id].composite_score:.0f}/100)."
    )
    # Credit enhancement — the load-bearing structural protection.
    ce = winner_tr.factors.get(DIM_SUBORDINATION_CE)
    if ce is not None and ce.available and ce.value is not None:
        reasons.append(
            f"{winner_name} {winner_tr.tranche_name} carries "
            f"{ce.value:.1%} structural credit enhancement (capital junior to it)."
        )
    # Trigger coverage proxy.
    th = winner_tr.factors.get(DIM_TRIGGER_HEADROOM)
    if th is not None and th.available and th.value is not None:
        reasons.append(
            f"{winner_name} protective-trigger coverage proxy: {th.value:.0%} of "
            "extracted triggers are quantified."
        )
    # Live covenant proximity, when a risk summary exists for the winner.
    win_prox = proximity_by_id.get(winner_id)
    if win_prox is not None:
        trig = tightest_name_by_id.get(winner_id)
        trig_phrase = f" ({trig})" if trig else ""
        reasons.append(
            f"{winner_name} tightest covenant{trig_phrase} sits "
            f"{win_prox:.1f}% from breach in the latest reported period."
        )

    summary = (
        f"{winner_name} ranks best on structural relative value "
        f"(composite {winner_tr.composite_score:.0f}/100). "
        "Ranking blends only the dimensions with real structural data; "
        "live-data-only dimensions are flagged as caveats."
    )

    return ComparativeVerdict(
        confidence="scored",
        winner_deal_id=winner_id,
        winner_deal_name=winner_name,
        ranking=ranked_ids,
        summary=summary,
        reasons=reasons,
        caveats=caveats,
    )


# ---------------------------------------------------------------------------
# Comp-set suggestion (jurisdiction / vintage).
# ---------------------------------------------------------------------------


def suggest_comps(
    target_deal_id: str,
    target_jurisdiction: str,
    target_vintage: int | None,
    registry: dict[str, dict],
    already_selected: set[str],
    *,
    vintage_window: int = 1,
) -> list[str]:
    """Registry deal_ids (not already selected) that comp the target.

    A registry deal comps the target when it shares the target's jurisdiction
    (when known) and its vintage is within ``vintage_window`` years. When the
    target jurisdiction is ``Unknown`` we fall back to vintage proximity alone.
    Excludes the target and any already-selected deal.
    """
    suggestions: list[str] = []
    juris_known = bool(target_jurisdiction) and target_jurisdiction.lower() != "unknown"
    for deal_id, ctx in registry.items():
        if deal_id == target_deal_id or deal_id in already_selected:
            continue
        ctx_juris = ctx.get("jurisdiction")
        if juris_known and ctx_juris and ctx_juris.lower() != target_jurisdiction.lower():
            continue
        ctx_vintage = parse_vintage(ctx.get("deal_name", ""))
        if (
            target_vintage is not None
            and ctx_vintage is not None
            and abs(ctx_vintage - target_vintage) > vintage_window
        ):
            continue
        suggestions.append(deal_id)
    return suggestions
