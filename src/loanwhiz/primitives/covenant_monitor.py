"""Covenant monitor primitive for RMBS trigger compliance.

Checks tape metrics against extracted trigger thresholds each period, tracks
proximity over time, flags breaches and near-misses, and outputs compliance
status per trigger per period with citations back to prospectus definitions.

Green Lion 2026-1 known triggers (from prospectus):
1. Sequential Pay Trigger — fires if cumulative net loss rate > threshold.
   Switches from pro-rata to sequential principal distribution.
2. PDL (Principal Deficiency Ledger) — fires if a tranche's PDL has a
   debit balance (positive value = debit).
3. Reserve Fund — flag if reserve account < target level.
4. Clean-Up Call — optional redemption when pool balance < 10% of original.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from loanwhiz.domain.esma_annex2 import locator_for
from loanwhiz.primitives.base import (
    AuditEntry,
    BaseInput,
    Citation,
    Primitive,
    PrimitiveResult,
)
from loanwhiz.primitives.deal_state import DealState
from loanwhiz.primitives.registry import register_primitive

# ---------------------------------------------------------------------------
# Thresholds and constants
# ---------------------------------------------------------------------------

# Near-miss threshold: if proximity_pct >= this value and not yet triggered,
# the trigger is flagged as a near-miss.
_NEAR_MISS_FLOOR = 80.0  # i.e. within 20% of the threshold

# Clean-up call: optional redemption when pool balance < 10% of original.
_CLEANUP_CALL_PCT = 10.0  # percent of original pool balance

# ---------------------------------------------------------------------------
# Metric-vocabulary alias map (the extractor↔monitor mismatch fix)
# ---------------------------------------------------------------------------
#
# The covenant *extractor* (``extraction.covenant_extractor``) emits metric
# names from Gemini's own vocabulary — ``cumulative_loss_rate_pct``,
# ``pdl_debit_balance``, ``reserve_fund_balance``, ``pool_balance_fraction``.
# The monitor's ``_extract_metric`` understands a *different* set of canonical
# sentinels — ``default_pct``, ``pdl_class_a`` / ``pdl_class_b``,
# ``reserve_fund_ratio``, ``pool_balance_pct``. Before this map, an extracted
# trigger whose ``metric`` matched no sentinel fell through to a silent
# ``0.0`` — so a deal's *own* extracted triggers evaluated against a constant
# and never fired (the Covenant audit's metric-vocabulary mismatch).
#
# This map normalises the extractor vocabulary (and a few common synonyms) onto
# the canonical sentinels at the single resolution point in ``_extract_metric``.
# It is the only place the two vocabularies are reconciled; add a row here
# rather than scattering name handling. Keys are matched case-insensitively.
_METRIC_ALIASES: dict[str, str] = {
    # Cumulative / realised loss-rate. ``default_pct`` is the tape-arrears proxy
    # the default sequential-pay trigger keys on; it canonicalises to the
    # structural loss-rate so that — when a ``DealState`` is present — the
    # trigger reads ``DealState.cumulative_loss_rate_pct`` (the real realised
    # loss rate), and otherwise falls back to the period dict's own
    # ``default_pct`` key (handled by the dual-name lookup in ``_extract_metric``).
    "default_pct": "cumulative_loss_rate_pct",
    "cumulative_loss_rate_pct": "cumulative_loss_rate_pct",
    "cumulative_net_loss_rate": "cumulative_loss_rate_pct",
    "cumulative_loss_rate": "cumulative_loss_rate_pct",
    "net_loss_rate_pct": "cumulative_loss_rate_pct",
    "loss_rate_pct": "cumulative_loss_rate_pct",
    # PDL debit balance. The generic extractor name maps to the Class A ledger
    # (the senior PDL the sequential/PDL trigger keys on); explicit per-class
    # names map to their own sentinel.
    "pdl_debit_balance": "pdl_class_a",
    "pdl_balance": "pdl_class_a",
    "principal_deficiency_ledger": "pdl_class_a",
    "class_a_pdl": "pdl_class_a",
    "class_a_pdl_balance": "pdl_class_a",
    "class_a_pdl_debit_balance": "pdl_class_a",
    "class_b_pdl": "pdl_class_b",
    "class_b_pdl_balance": "pdl_class_b",
    "class_b_pdl_debit_balance": "pdl_class_b",
    # Reserve fund → the funded-ratio sentinel.
    "reserve_fund_balance": "reserve_fund_ratio",
    "reserve_account_balance": "reserve_fund_ratio",
    "reserve_fund_amount": "reserve_fund_ratio",
    "reserve_ratio": "reserve_fund_ratio",
    # Pool balance as a fraction/factor of original → the pct sentinel.
    "pool_balance_fraction": "pool_balance_pct",
    "pool_factor": "pool_balance_pct",
    "pool_balance_ratio": "pool_balance_pct",
    # ---- Tape-native (B7) signals — arrears / LTV ----
    # These resolve onto the metric keys the ESMA tape normaliser already
    # emits (``pool_stats.wtd_ltv``, ``arrears_breakdown.arrears_180d_plus_pct``
    # / ``default_pct``), so the tape-native triggers below key on the tape's
    # own pool analytics with no plumbing change to ``_extract_metric`` (which
    # already searches the nested ``pool_stats`` / ``arrears_breakdown`` dicts).
    # Weighted-average LTV synonyms → the normaliser's ``wtd_ltv`` pool stat.
    "wa_ltv": "wtd_ltv",
    "weighted_average_ltv": "wtd_ltv",
    "weighted_avg_ltv": "wtd_ltv",
    "pool_wa_ltv": "wtd_ltv",
    "current_ltv_pct": "wtd_ltv",
    # Severe-arrears synonyms → the normaliser's 180+d arrears bucket pct.
    "arrears_severe_pct": "arrears_180d_plus_pct",
    "arrears_180d_pct": "arrears_180d_plus_pct",
    "severe_arrears_pct": "arrears_180d_plus_pct",
    # Default-rate synonyms → the tape's defaulted-balance arrears bucket pct.
    # (Distinct from ``cumulative_loss_rate_pct``: this is the tape's current
    # default *flag* proportion, not realised structural loss.)
    "tape_default_pct": "default_pct",
    "default_rate_pct": "default_pct",
}


def _canonical_metric(metric: str) -> str:
    """Resolve an extracted/synonym metric name to its canonical sentinel.

    Looks the name up in :data:`_METRIC_ALIASES` (case-insensitively). A name
    that is already canonical, or that has no alias, is returned unchanged — so
    period-dict tape metrics (``default_pct`` and friends) and the canonical
    sentinels pass straight through.
    """
    return _METRIC_ALIASES.get(metric.strip().lower(), metric)


# ---------------------------------------------------------------------------
# I/O models
# ---------------------------------------------------------------------------


class TriggerDefinition(BaseModel):
    """Definition of a covenant trigger extracted from the prospectus.

    Attributes:
        name:        Snake_case identifier for the trigger (used as dict key).
        description: Human-readable one-line description.
        metric:      The field name to read from the tape period dict (or the
                     special sentinel ``"pdl_class_a"``, ``"pdl_class_b"``,
                     ``"reserve_fund_ratio"``, ``"pool_balance_pct"``).
        threshold:   Threshold value; ``None`` if not applicable (e.g. PDL
                     debit balance check where any positive value fires).
        direction:   ``"above"`` — trigger fires when metric > threshold.
                     ``"below"`` — trigger fires when metric < threshold.
        consequence: Plain English description of what happens if triggered.
        citation:    Source reference pointing to the prospectus section.
    """

    name: str
    description: str
    metric: str
    threshold: float | None
    direction: str  # "above" | "below"
    consequence: str
    citation: Citation


class TriggerStatus(BaseModel):
    """Compliance status for a single trigger in a single period.

    Attributes:
        trigger_name:  Name of the trigger (matches ``TriggerDefinition.name``).
        period:        Reporting period identifier (e.g. ``"2026-02-28"``).
        metric_value:  Observed metric value for this period. ``None`` when the
                       metric could not be resolved (``evaluable`` is False).
        threshold:     Threshold at which the trigger fires (mirrors definition).
        is_triggered:  Whether the trigger is currently breached. Always False
                       when ``evaluable`` is False.
        proximity_pct: How close to the threshold (0–100). 100 = at threshold,
                       > 100 = beyond threshold (triggered). For "above"
                       triggers: ``metric_value / threshold * 100`` when
                       threshold > 0. For "below" triggers:
                       ``threshold / metric_value * 100`` when metric_value > 0.
                       ``None`` when not evaluable — so an honest "couldn't
                       measure this" reads differently from a genuine 0.
        direction:     Trend vs prior period: ``"improving"`` | ``"deteriorating"``
                       | ``"stable"`` | ``"n/a"`` (first period or no threshold).
        evaluable:     ``False`` when the metric could not be resolved (unknown
                       metric name, missing structural input). A not-evaluable
                       status is excluded from active/near-miss tallies so the
                       proximity output stays honest — not-evaluable is NOT 0.
        not_evaluable_reason: One-line reason when ``evaluable`` is False
                       (e.g. ``"metric 'foo' not resolvable from period or
                       structural state"``); ``None`` otherwise.
    """

    trigger_name: str
    period: str
    metric_value: float | None
    threshold: float | None
    is_triggered: bool
    proximity_pct: float | None  # 0–100+: 100 = at threshold; None = not evaluable
    direction: str  # "improving" | "deteriorating" | "stable" | "n/a"
    evaluable: bool = True
    not_evaluable_reason: str | None = None


class CovenantInput(BaseInput):
    """Input to the covenant monitor.

    Attributes:
        periods:                   List of EsmaTapeOutput-compatible dicts,
                                   one per reporting period. Must be in
                                   chronological order (oldest first) so that
                                   direction (improving/deteriorating) is
                                   computed correctly.
        triggers:                  Trigger definitions to evaluate. When empty,
                                   the monitor falls back to ``DEFAULT_TRIGGERS``
                                   defined on the class.
        class_a_pdl_balance:       Class A PDL ledger balance (EUR). A positive
                                   value means a debit balance (trigger fires).
        class_b_pdl_balance:       Class B PDL ledger balance (EUR). Same sign
                                   convention.
        reserve_account_balance:   Current reserve account balance (EUR).
        reserve_account_target:    Target / required reserve level (EUR).
        original_pool_balance:     Original pool balance at closing (EUR). Used
                                   to compute clean-up call proximity.
    """

    periods: list[dict[str, Any]]
    triggers: list[TriggerDefinition] = Field(default_factory=list)
    class_a_pdl_balance: float = 0.0
    class_b_pdl_balance: float = 0.0
    reserve_account_balance: float = 0.0
    reserve_account_target: float = 0.0
    original_pool_balance: float = 0.0
    # Optional per-period canonical structural state (S1's ``DealState``). When
    # provided — one entry per ``periods`` entry, same order — the monitor reads
    # the structural metrics (PDL, reserve, cumulative loss, pool factor) from
    # the matching ``DealState`` for that period instead of the single scalar
    # fields above. This is what makes PDL/reserve a real, non-flat series
    # rather than permanently 0 / 100% (the audit's structural-plumbing gap).
    # When absent the scalar fields are used (backward compatible).
    period_states: list[DealState] | None = None

    @classmethod
    def from_deal_states(
        cls,
        deal_states: list[DealState],
        *,
        periods: list[dict[str, Any]] | None = None,
        triggers: list[TriggerDefinition] | None = None,
    ) -> "CovenantInput":
        """Build a ``CovenantInput`` from a chain of canonical ``DealState``s.

        Convenience constructor for the S6 multi-period path (and tests): the
        structural metrics for every period come from ``deal_states`` (PDL,
        reserve, cumulative loss, pool factor), so the resulting proximity
        series is driven by real state rather than a single scalar snapshot.

        ``periods`` (the ESMA-tape dicts) is optional — when omitted, a minimal
        period dict carrying the ``reporting_date`` and ``pool_balance_eur`` is
        synthesised from each ``DealState`` so tape-sourced metrics
        (``default_pct``) and the clean-up-call pool metric still resolve. The
        ``original_pool_balance`` denominator is taken from the first state.
        """
        if not deal_states:
            return cls(periods=periods or [], triggers=triggers or [])
        synthesised: list[dict[str, Any]] = []
        for st in deal_states:
            synthesised.append(
                {
                    "reporting_date": st.reporting_date,
                    "pool_balance_eur": st.pool_balance,
                    "cumulative_loss_rate_pct": st.cumulative_loss_rate_pct,
                }
            )
        resolved_periods = periods if periods is not None else synthesised
        return cls(
            periods=resolved_periods,
            triggers=triggers or [],
            period_states=list(deal_states),
            original_pool_balance=deal_states[0].original_pool_balance,
        )


class CovenantOutput(BaseModel):
    """Output of the covenant monitor primitive.

    Attributes:
        trigger_statuses:  One ``TriggerStatus`` per trigger per period.
        active_triggers:   Names of triggers currently breached in the
                           latest period.
        near_miss_triggers: Names of triggers approaching but not yet at their
                            threshold (``80 <= proximity_pct < 100``) and not
                            yet triggered, in the latest period.
        unevaluable_triggers: Names of triggers whose metric could not be
                            resolved in the latest period (``evaluable`` False).
                            Surfaced separately so the dashboard can show
                            "couldn't measure" distinctly from "compliant".
        summary:           Plain English compliance summary.
    """

    trigger_statuses: list[TriggerStatus]
    active_triggers: list[str]
    near_miss_triggers: list[str]
    unevaluable_triggers: list[str] = Field(default_factory=list)
    summary: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute_proximity(
    metric_value: float,
    threshold: float | None,
    direction: str,
) -> float:
    """Compute proximity_pct in [0, ∞) for a trigger.

    Returns how far the metric has moved toward (or past) the threshold,
    expressed as a percentage of the threshold value.

    - "above" triggers: ``metric_value / threshold * 100``.
      At threshold → 100. Breached → > 100.
    - "below" triggers: ``threshold / metric_value * 100`` (metric shrinks
      toward threshold). At threshold → 100. Breached (below) → > 100.

    Returns 0.0 when the threshold is ``None`` or when the denominator is 0.
    """
    if threshold is None or threshold == 0.0:
        return 0.0
    if direction == "above":
        return round(metric_value / threshold * 100.0, 4)
    else:  # "below"
        if metric_value == 0.0:
            # metric_value is 0 and threshold > 0 means the trigger is
            # already breached (pool balance = 0 < target).
            return 100.0
        return round(threshold / metric_value * 100.0, 4)


def _compute_direction(
    current_proximity: float,
    prior_proximity: float | None,
) -> str:
    """Determine whether the metric is improving or deteriorating.

    "Improving" means moving *away* from a breach; "deteriorating" means
    moving *toward* a breach. "Stable" means no material change (< 1 ppt).
    "n/a" when there is no prior period to compare to.

    Because proximity is always expressed as a percentage of the threshold
    (higher = closer to breach, for both "above" and "below" triggers), the
    direction logic is symmetric: a rising proximity is always deteriorating.
    """
    if prior_proximity is None:
        return "n/a"

    delta = current_proximity - prior_proximity
    if abs(delta) < 1.0:
        return "stable"

    # Higher proximity always means closer to breach for both directions.
    if delta > 0:
        return "deteriorating"
    return "improving"


def _extract_metric(
    period: dict[str, Any],
    metric: str,
    input: CovenantInput,
    state: DealState | None = None,
) -> float | None:
    """Resolve a trigger's metric to a value, or ``None`` if not evaluable.

    The metric name is first normalised through :func:`_canonical_metric` (the
    extractor↔monitor alias map) so an extracted name like ``pdl_debit_balance``
    or ``reserve_fund_balance`` resolves onto the canonical sentinel rather than
    silently missing.

    Structural sentinels prefer the per-period ``DealState`` (``state``) when
    one is supplied — so PDL/reserve/pool are a real per-period series — and
    fall back to the ``input`` scalar fields otherwise:

    - ``"pdl_class_a"``        → ``state.class_a_pdl`` else ``input.class_a_pdl_balance``
    - ``"pdl_class_b"``        → ``state.class_b_pdl`` else ``input.class_b_pdl_balance``
    - ``"reserve_fund_ratio"`` → reserve balance / target * 100 (percent funded)
    - ``"pool_balance_pct"``   → current pool / original pool * 100
    - ``"cumulative_loss_rate_pct"`` → ``state.cumulative_loss_rate_pct`` else
      the value from the period dict (the ``default_pct`` loss proxy).

    Returns ``None`` (NOT ``0.0``) when the metric genuinely cannot be resolved
    — unknown name, or a structural sentinel with neither a ``DealState`` nor a
    usable scalar input. The caller turns ``None`` into an honest *not-evaluable*
    status so "couldn't measure" never masquerades as a healthy 0.
    """
    canonical = _canonical_metric(metric)

    if canonical == "pdl_class_a":
        if state is not None:
            return float(state.class_a_pdl)
        return float(input.class_a_pdl_balance)
    if canonical == "pdl_class_b":
        if state is not None:
            return float(state.class_b_pdl)
        return float(input.class_b_pdl_balance)
    if canonical == "reserve_fund_ratio":
        if state is not None:
            target = float(state.reserve_target)
            if target == 0.0:
                return None  # no reserve target known → not evaluable
            return round(float(state.reserve_balance) / target * 100.0, 4)
        target = float(input.reserve_account_target)
        if target == 0.0:
            return None  # no reserve target supplied → not evaluable (not a fake 100%)
        return round(float(input.reserve_account_balance) / target * 100.0, 4)
    if canonical == "pool_balance_pct":
        if state is not None:
            return round(state.pool_factor * 100.0, 4)
        original = float(input.original_pool_balance)
        if original == 0.0:
            return None  # original pool unknown → not evaluable
        pool_bal = period.get("pool_balance_eur")
        if pool_bal is None:
            return None
        return round(float(pool_bal) / original * 100.0, 4)
    if canonical == "cumulative_loss_rate_pct":
        if state is not None:
            return round(state.cumulative_loss_rate_pct, 4)
        # fall through to the period-dict lookup below (the default_pct proxy)

    # Generic tape metric — expected to live in the period dict directly
    # or nested under "arrears_breakdown" / "pool_stats". We look up BOTH the
    # canonical name and the original (pre-alias) name so a tape that uses the
    # raw extractor vocabulary still resolves.
    for name in (canonical, metric):
        if name in period:
            return float(period[name])
        arrears = period.get("arrears_breakdown", {})
        if name in arrears:
            return float(arrears[name])
        pool_stats = period.get("pool_stats", {})
        if name in pool_stats:
            return float(pool_stats[name])
    return None


def _is_triggered(
    metric_value: float | None, threshold: float | None, direction: str
) -> bool:
    """Return True when the trigger condition is met.

    A ``None`` metric (not evaluable) never fires — there is nothing to compare.
    """
    if metric_value is None:
        return False
    if threshold is None:
        # PDL check: any positive (debit) balance fires the trigger.
        return metric_value > 0.0
    if direction == "above":
        return metric_value > threshold
    else:  # "below"
        return metric_value < threshold


def _evaluate_one(
    trigger: TriggerDefinition,
    period: dict[str, Any],
    input: CovenantInput,
    state: DealState | None,
    prior_proximity: float | None,
    period_label: str | None = None,
) -> TriggerStatus:
    """Evaluate one trigger against one period of state — the evaluation core.

    Resolves the trigger's metric (alias-normalised, preferring ``state`` for
    structural sentinels), then computes ``is_triggered`` / ``proximity_pct`` /
    trend ``direction``. When the metric cannot be resolved the result is an
    honest *not-evaluable* status (``evaluable=False``, ``metric_value`` and
    ``proximity_pct`` both ``None``, ``is_triggered=False``) rather than a fake
    0. This is the single function both :meth:`CovenantMonitor.execute` and the
    public :func:`evaluate_triggers` (S4's predicate seam) delegate to.
    """
    label = period_label if period_label is not None else str(
        period.get("reporting_date", "unknown")
    )
    metric_value = _extract_metric(period, trigger.metric, input, state)

    if metric_value is None:
        return TriggerStatus(
            trigger_name=trigger.name,
            period=label,
            metric_value=None,
            threshold=trigger.threshold,
            is_triggered=False,
            proximity_pct=None,
            direction="n/a",
            evaluable=False,
            not_evaluable_reason=(
                f"metric '{trigger.metric}' not resolvable from period data "
                "or structural state"
            ),
        )

    triggered = _is_triggered(metric_value, trigger.threshold, trigger.direction)
    prox = _compute_proximity(metric_value, trigger.threshold, trigger.direction)
    dir_label = _compute_direction(prox, prior_proximity)
    return TriggerStatus(
        trigger_name=trigger.name,
        period=label,
        metric_value=metric_value,
        threshold=trigger.threshold,
        is_triggered=triggered,
        proximity_pct=prox,
        direction=dir_label,
        evaluable=True,
        not_evaluable_reason=None,
    )


# ---------------------------------------------------------------------------
# Primitive
# ---------------------------------------------------------------------------


@register_primitive(
    name="covenant_monitor",
    version="0.1.0",
    description="Monitor RMBS covenant compliance against trigger thresholds",
    tags=["covenant", "trigger", "compliance", "computation"],
)
class CovenantMonitor(Primitive[CovenantInput, CovenantOutput]):
    """Check tape metrics against trigger thresholds; track proximity over time.

    Evaluates each trigger across every reporting period in the input and
    produces a ``TriggerStatus`` per trigger per period. Identifies active
    breaches and near-misses (within 20% of threshold) in the latest period.
    Confidence is always 1.0 (deterministic rule-based logic; no LLM calls).
    """

    name = "covenant_monitor"
    version = "0.1.0"
    description = "Monitor RMBS covenant compliance against trigger thresholds"

    # Default Green Lion 2026-1 triggers (used when CovenantInput.triggers is empty).
    DEFAULT_TRIGGERS: list[TriggerDefinition] = [
        TriggerDefinition(
            name="cumulative_loss_trigger",
            description=(
                "Sequential pay trigger: cumulative net loss rate exceeds threshold. "
                "Uses default_pct from the ESMA tape as a proxy for realised loss rate."
            ),
            metric="default_pct",
            threshold=1.5,
            direction="above",
            consequence=(
                "Switches principal distribution from pro-rata to sequential "
                "(senior tranches paid before mezzanine/junior)."
            ),
            citation=Citation(
                document="Green Lion 2026-1 Prospectus",
                page_or_row="Section 5.2",
                excerpt=(
                    "Sequential Pay Trigger: if the Cumulative Net Loss Rate "
                    "exceeds the applicable trigger percentage, principal will "
                    "be distributed on a sequential basis."
                ),
            ),
        ),
        TriggerDefinition(
            name="pdl_class_a",
            description="Class A PDL debit balance trigger.",
            metric="pdl_class_a",
            threshold=None,  # any positive (debit) balance fires the trigger
            direction="above",
            consequence=(
                "PDL debit balance for Class A notes indicates unreimbursed "
                "principal deficiency; distributions diverted to cure the PDL."
            ),
            citation=Citation(
                document="Green Lion 2026-1 Prospectus",
                page_or_row="Section 5.3",
                excerpt=(
                    "Principal Deficiency Ledger: if the Class A PDL shows a "
                    "debit balance, a PDL trigger event is deemed to have occurred."
                ),
            ),
        ),
        TriggerDefinition(
            name="pdl_class_b",
            description="Class B PDL debit balance trigger.",
            metric="pdl_class_b",
            threshold=None,
            direction="above",
            consequence=(
                "PDL debit balance for Class B notes; distributions diverted "
                "to cure the Class B PDL before any junior payments."
            ),
            citation=Citation(
                document="Green Lion 2026-1 Prospectus",
                page_or_row="Section 5.3",
                excerpt=(
                    "Principal Deficiency Ledger: if the Class B PDL shows a "
                    "debit balance, a PDL trigger event is deemed to have occurred."
                ),
            ),
        ),
        TriggerDefinition(
            name="reserve_fund_trigger",
            description=(
                "Reserve fund below target level. Metric is reserve_account_balance "
                "as a percentage of reserve_account_target."
            ),
            metric="reserve_fund_ratio",
            threshold=100.0,  # 100% = fully funded; trigger fires below 100%
            direction="below",
            consequence=(
                "Reserve fund draw required; any shortfall is funded from available "
                "revenue before junior distribution."
            ),
            citation=Citation(
                document="Green Lion 2026-1 Prospectus",
                page_or_row="Section 5.4",
                excerpt=(
                    "Reserve Fund: if the Reserve Account balance is below the "
                    "Reserve Fund Required Amount, the shortfall constitutes a "
                    "trigger event."
                ),
            ),
        ),
        TriggerDefinition(
            name="clean_up_call",
            description=(
                "Optional clean-up call: pool balance below 10% of original. "
                "Metric is current pool balance as a percentage of original balance."
            ),
            metric="pool_balance_pct",
            threshold=_CLEANUP_CALL_PCT,
            direction="below",
            consequence=(
                "Issuer (or servicer) may optionally redeem all outstanding notes "
                "at par once the pool has amortised to below 10% of its original balance."
            ),
            citation=Citation(
                document="Green Lion 2026-1 Prospectus",
                page_or_row="Section 7.1",
                excerpt=(
                    "Clean-Up Call Option: the Issuer may, at its option, redeem "
                    "all Notes once the Outstanding Principal Balance of the "
                    "Mortgage Receivables is less than 10% of the Original "
                    "Principal Balance."
                ),
            ),
        ),
    ]

    # ---- Tape-native (B7) covenant triggers ----------------------------------
    #
    # Pool-risk early-warning triggers sourced directly from the ESMA loan tape
    # (arrears severity, default rate, weighted-average LTV) rather than from the
    # deal's contractual structural state. They are a SEPARATE composable list,
    # NOT folded into ``DEFAULT_TRIGGERS`` — adding them to the GL-2026-1 defaults
    # would change that deal's covenant output and break its regression locks.
    # A caller monitors tape-native risk by passing
    # ``CovenantMonitor.DEFAULT_TRIGGERS + CovenantMonitor.TAPE_NATIVE_TRIGGERS``
    # (or just the tape-native list) as ``CovenantInput.triggers``.
    #
    # Each keys on a metric the ESMA tape normaliser already emits — the
    # ``_METRIC_ALIASES`` rows above resolve common synonyms onto these tape
    # keys, and ``_extract_metric`` already searches the nested ``pool_stats`` /
    # ``arrears_breakdown`` dicts — so no metric-resolution plumbing changes.
    # Citations are anchored to the ESMA RTS Annex 2 RREL field the signal comes
    # from (``esma_annex2.locator_for``), not to a prospectus section.
    TAPE_NATIVE_TRIGGERS: list[TriggerDefinition] = [
        TriggerDefinition(
            name="severe_arrears_trigger",
            description=(
                "Tape-native arrears trigger: share of the pool 180+ days in "
                "arrears exceeds a threshold (early-warning of credit "
                "deterioration). Keys on the tape's "
                "``arrears_breakdown.arrears_180d_plus_pct``."
            ),
            metric="arrears_severe_pct",
            threshold=5.0,
            direction="above",
            consequence=(
                "Rising severe arrears signal deteriorating pool credit quality "
                "ahead of realised losses; a leading indicator for the structural "
                "loss / sequential-pay triggers."
            ),
            citation=Citation(
                document="ESMA RTS Annex 2 (RMBS)",
                page_or_row=locator_for("arrears_bucket") or "RREL64 · arrears bucket",
                excerpt=(
                    "Share of the underlying-exposure pool in the 180+ days "
                    "arrears bucket, derived from the ESMA Annex 2 arrears fields."
                ),
            ),
        ),
        TriggerDefinition(
            name="tape_default_rate_trigger",
            description=(
                "Tape-native default-rate trigger: share of the pool flagged in "
                "default on the tape exceeds a threshold. Distinct from the "
                "structural cumulative-loss trigger — this is the tape's current "
                "default *flag* proportion, not realised loss. Keys on "
                "``arrears_breakdown.default_pct``."
            ),
            metric="tape_default_pct",
            threshold=3.0,
            direction="above",
            consequence=(
                "Elevated current-default share warns of pool stress before those "
                "defaults crystallise into realised losses in the cashflow."
            ),
            citation=Citation(
                document="ESMA RTS Annex 2 (RMBS)",
                page_or_row=locator_for("default_status") or "RREL66 · default status",
                excerpt=(
                    "Share of underlying exposures flagged as defaulted / "
                    "credit-impaired per the ESMA Annex 2 default-status field."
                ),
            ),
        ),
        TriggerDefinition(
            name="weighted_average_ltv_trigger",
            description=(
                "Tape-native LTV trigger: balance-weighted average current "
                "loan-to-value of the pool exceeds a threshold (collateral-cover "
                "deterioration). Keys on the tape's ``pool_stats.wtd_ltv``."
            ),
            metric="wa_ltv",
            threshold=80.0,
            direction="above",
            consequence=(
                "A high weighted-average LTV means thinner collateral cover, "
                "raising loss-given-default if the pool deteriorates."
            ),
            citation=Citation(
                document="ESMA RTS Annex 2 (RMBS)",
                page_or_row=(
                    locator_for("current_loan_to_value")
                    or "RREL40 · current loan-to-value"
                ),
                excerpt=(
                    "Balance-weighted average current loan-to-value of the pool, "
                    "derived from the ESMA Annex 2 current-LTV field."
                ),
            ),
        ),
    ]

    def execute(self, input: CovenantInput) -> PrimitiveResult[CovenantOutput]:  # type: ignore[override]
        """Evaluate all triggers across all periods.

        For each period × trigger pair, extracts the relevant metric,
        computes ``is_triggered`` and ``proximity_pct``, and determines the
        trend direction relative to the prior period. Returns a
        ``PrimitiveResult`` with ``confidence=1.0``.

        Args:
            input: Validated ``CovenantInput`` with periods and optional
                   trigger overrides.

        Returns:
            ``PrimitiveResult[CovenantOutput]`` with one ``TriggerStatus``
            per trigger per period, lists of active and near-miss trigger
            names (latest period), and a plain-English summary.
        """
        t0 = time.perf_counter()
        input_hash = input.input_hash()

        triggers: list[TriggerDefinition] = (
            input.triggers if input.triggers else self.DEFAULT_TRIGGERS
        )

        all_statuses: list[TriggerStatus] = []
        # Maps trigger_name → last evaluable proximity_pct (in chronological
        # order) for direction computation. A not-evaluable period contributes
        # no point, so the trend skips over gaps rather than treating them as 0.
        proximity_history: dict[str, float | None] = {t.name: None for t in triggers}

        for idx, period in enumerate(input.periods):
            period_label = str(period.get("reporting_date", "unknown"))
            state = (
                input.period_states[idx]
                if input.period_states is not None and idx < len(input.period_states)
                else None
            )

            for trigger in triggers:
                prior_prox = proximity_history[trigger.name]
                status = _evaluate_one(
                    trigger, period, input, state, prior_prox, period_label
                )
                if status.evaluable:
                    proximity_history[trigger.name] = status.proximity_pct
                all_statuses.append(status)

        # Latest-period summary: determine active, near-miss, and not-evaluable
        # triggers. Not-evaluable triggers are kept OUT of active/near-miss so
        # the proximity output stays honest.
        active_triggers: list[str] = []
        near_miss_triggers: list[str] = []
        unevaluable_triggers: list[str] = []

        if input.periods:
            latest_label = str(input.periods[-1].get("reporting_date", "unknown"))
            for status in all_statuses:
                if status.period != latest_label:
                    continue
                if not status.evaluable:
                    unevaluable_triggers.append(status.trigger_name)
                elif status.is_triggered:
                    active_triggers.append(status.trigger_name)
                elif (
                    status.threshold is not None
                    and status.proximity_pct is not None
                    and _NEAR_MISS_FLOOR <= status.proximity_pct < 100.0
                ):
                    near_miss_triggers.append(status.trigger_name)

        summary = _build_summary(
            active_triggers,
            near_miss_triggers,
            unevaluable_triggers,
            len(input.periods),
            triggers,
        )

        output = CovenantOutput(
            trigger_statuses=all_statuses,
            active_triggers=active_triggers,
            near_miss_triggers=near_miss_triggers,
            unevaluable_triggers=unevaluable_triggers,
            summary=summary,
        )

        duration_ms = (time.perf_counter() - t0) * 1000.0
        audit = AuditEntry.now(
            primitive_name=self.name,
            version=self.version,
            input_hash=input_hash,
            duration_ms=duration_ms,
        )

        citations = [t.citation for t in triggers]

        return PrimitiveResult[CovenantOutput](
            output=output,
            confidence=1.0,
            citations=citations,
            audit_entry=audit,
        )


# ---------------------------------------------------------------------------
# Summary builder
# ---------------------------------------------------------------------------


def _build_summary(
    active: list[str],
    near_miss: list[str],
    unevaluable: list[str],
    n_periods: int,
    triggers: list[TriggerDefinition],
) -> str:
    """Build a plain-English compliance summary.

    Not-evaluable triggers are reported separately (and never folded into the
    "all clear" count) so the summary distinguishes "compliant" from "couldn't
    measure".
    """
    n_triggers = len(triggers)
    period_word = "period" if n_periods == 1 else "periods"

    unevaluable_clause = ""
    if unevaluable:
        joined = ", ".join(f"'{t}'" for t in unevaluable)
        unevaluable_clause = f" NOT EVALUABLE (metric unresolved): {joined}."

    if not active and not near_miss:
        n_clear = n_triggers - len(unevaluable)
        return (
            f"{n_clear} of {n_triggers} covenant triggers are within compliance "
            f"across {n_periods} reporting {period_word}. No breaches or "
            f"near-misses detected.{unevaluable_clause}"
        )

    parts: list[str] = []
    if active:
        joined = ", ".join(f"'{t}'" for t in active)
        parts.append(f"BREACHED: {joined}")
    if near_miss:
        joined = ", ".join(f"'{t}'" for t in near_miss)
        parts.append(f"NEAR-MISS (within 20% of threshold): {joined}")

    return (
        f"Covenant compliance across {n_periods} reporting {period_word} — "
        + "; ".join(parts)
        + "."
        + unevaluable_clause
    )


# ---------------------------------------------------------------------------
# Predicate interface — the seam S4 (#184, the waterfall interpreter) consumes
# ---------------------------------------------------------------------------


class TriggerEvaluation(BaseModel):
    """Trigger state for one ``DealState``, keyed by trigger name.

    This is the **predicate interface** the waterfall interpreter (S4 / #184)
    calls to gate conditional waterfall steps: a step whose free-text
    ``condition`` references a named trigger (e.g. "if Sequential Pay Trigger is
    in effect") resolves to a boolean via :meth:`is_triggered`. S5 produces this
    state; S4 consumes it.

    Attributes:
        period:    The reporting date / period label evaluated.
        statuses:  ``{trigger_name: TriggerStatus}`` for every trigger.
    """

    period: str
    statuses: dict[str, TriggerStatus]

    def is_triggered(self, trigger_name: str) -> bool:
        """Whether ``trigger_name`` is currently breached.

        Returns ``False`` for an unknown trigger name and for a not-evaluable
        trigger (a step gated on a trigger we couldn't measure does not fire —
        the caller can additionally consult :meth:`evaluable` to branch on the
        not-evaluable case explicitly).
        """
        status = self.statuses.get(trigger_name)
        return bool(status and status.evaluable and status.is_triggered)

    def evaluable(self, trigger_name: str) -> bool:
        """Whether ``trigger_name`` could be evaluated (metric resolved)."""
        status = self.statuses.get(trigger_name)
        return bool(status and status.evaluable)

    @property
    def active(self) -> list[str]:
        """Names of all currently-breached, evaluable triggers."""
        return [
            name
            for name, st in self.statuses.items()
            if st.evaluable and st.is_triggered
        ]


def evaluate_triggers(
    deal_state: DealState,
    triggers: list[TriggerDefinition] | None = None,
    *,
    period: dict[str, Any] | None = None,
) -> TriggerEvaluation:
    """Evaluate triggers as predicates over a single canonical ``DealState``.

    The clean entry point for callers that hold one ``DealState`` and want
    per-trigger truth — principally S4 (#184), which gates conditional waterfall
    steps on named triggers. Structural metrics (PDL, reserve, cumulative loss,
    pool factor) are read directly from ``deal_state``; the tape-sourced loss
    proxy (``default_pct``) is read from ``period`` when one is supplied.

    Parameters
    ----------
    deal_state:
        The period's canonical structural state.
    triggers:
        Trigger definitions to evaluate. Defaults to
        :data:`CovenantMonitor.DEFAULT_TRIGGERS` when ``None``.
    period:
        Optional ESMA-tape period dict for tape-only metrics (e.g.
        ``default_pct``). A minimal dict is synthesised from ``deal_state`` when
        omitted (carrying ``reporting_date``, ``pool_balance_eur`` and the
        state's ``cumulative_loss_rate_pct``).

    Returns
    -------
    TriggerEvaluation
        Per-trigger :class:`TriggerStatus` keyed by name, plus the
        ``is_triggered`` / ``evaluable`` predicates S4 consumes.
    """
    resolved_triggers = (
        triggers if triggers is not None else CovenantMonitor.DEFAULT_TRIGGERS
    )
    resolved_period = period if period is not None else {
        "reporting_date": deal_state.reporting_date,
        "pool_balance_eur": deal_state.pool_balance,
        "cumulative_loss_rate_pct": deal_state.cumulative_loss_rate_pct,
    }
    # A single-state evaluation has no prior period, so direction is "n/a" and
    # the structural metrics come from the state. The scalar-field fallbacks on
    # the synthesised input are never reached because ``state`` is non-None.
    eval_input = CovenantInput(
        periods=[resolved_period],
        triggers=resolved_triggers,
        period_states=[deal_state],
        original_pool_balance=deal_state.original_pool_balance,
    )
    statuses = {
        trigger.name: _evaluate_one(
            trigger,
            resolved_period,
            eval_input,
            deal_state,
            prior_proximity=None,
        )
        for trigger in resolved_triggers
    }
    return TriggerEvaluation(period=deal_state.reporting_date, statuses=statuses)
