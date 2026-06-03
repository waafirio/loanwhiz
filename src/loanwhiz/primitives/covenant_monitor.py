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

from loanwhiz.primitives.base import (
    AuditEntry,
    BaseInput,
    Citation,
    Primitive,
    PrimitiveResult,
)
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
        metric_value:  Observed metric value for this period.
        threshold:     Threshold at which the trigger fires (mirrors definition).
        is_triggered:  Whether the trigger is currently breached.
        proximity_pct: How close to the threshold (0–100). 100 = at threshold,
                       > 100 = beyond threshold (triggered). For "above"
                       triggers: ``metric_value / threshold * 100`` when
                       threshold > 0. For "below" triggers:
                       ``threshold / metric_value * 100`` when metric_value > 0.
        direction:     Trend vs prior period: ``"improving"`` | ``"deteriorating"``
                       | ``"stable"`` | ``"n/a"`` (first period or no threshold).
    """

    trigger_name: str
    period: str
    metric_value: float
    threshold: float | None
    is_triggered: bool
    proximity_pct: float  # 0–100+: 100 = at threshold
    direction: str  # "improving" | "deteriorating" | "stable" | "n/a"


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


class CovenantOutput(BaseModel):
    """Output of the covenant monitor primitive.

    Attributes:
        trigger_statuses:  One ``TriggerStatus`` per trigger per period.
        active_triggers:   Names of triggers currently breached in the
                           latest period.
        near_miss_triggers: Names of triggers approaching but not yet at their
                            threshold (``80 <= proximity_pct < 100``) and not
                            yet triggered, in the latest period.
        summary:           Plain English compliance summary.
    """

    trigger_statuses: list[TriggerStatus]
    active_triggers: list[str]
    near_miss_triggers: list[str]
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
    trigger_direction: str,
) -> str:
    """Determine whether the metric is improving or deteriorating.

    "Improving" means moving *away* from a breach; "deteriorating" means
    moving *toward* a breach. "Stable" means no material change (< 1 ppt).
    "n/a" when there is no prior period to compare to or no threshold.
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
) -> float:
    """Extract the relevant metric value from a period dict or input scalars.

    Special metric sentinels handled:
    - ``"pdl_class_a"``       → ``input.class_a_pdl_balance``
    - ``"pdl_class_b"``       → ``input.class_b_pdl_balance``
    - ``"reserve_fund_ratio"`` → ``input.reserve_account_balance /
                                   input.reserve_account_target * 100``
                                   (percent of target; trigger fires below 100)
    - ``"pool_balance_pct"``  → ``period["pool_balance_eur"] /
                                   input.original_pool_balance * 100``
                                   (percent of original; trigger fires below 10)

    For all other metrics the value is read from the period dict as a float.
    Missing keys default to 0.0.
    """
    if metric == "pdl_class_a":
        return float(input.class_a_pdl_balance)
    if metric == "pdl_class_b":
        return float(input.class_b_pdl_balance)
    if metric == "reserve_fund_ratio":
        target = float(input.reserve_account_target)
        if target == 0.0:
            return 100.0  # no target set → assume fully funded
        return round(float(input.reserve_account_balance) / target * 100.0, 4)
    if metric == "pool_balance_pct":
        original = float(input.original_pool_balance)
        if original == 0.0:
            return 100.0  # original unknown → assume well above trigger
        pool_bal = float(period.get("pool_balance_eur", 0.0))
        return round(pool_bal / original * 100.0, 4)

    # Generic tape metric — expected to live in the period dict directly
    # or nested under "arrears_breakdown".
    if metric in period:
        return float(period[metric])
    arrears = period.get("arrears_breakdown", {})
    if metric in arrears:
        return float(arrears[metric])
    # Try pool_stats dict
    pool_stats = period.get("pool_stats", {})
    if metric in pool_stats:
        return float(pool_stats[metric])
    return 0.0


def _is_triggered(metric_value: float, threshold: float | None, direction: str) -> bool:
    """Return True when the trigger condition is met."""
    if threshold is None:
        # PDL check: any positive (debit) balance fires the trigger.
        return metric_value > 0.0
    if direction == "above":
        return metric_value > threshold
    else:  # "below"
        return metric_value < threshold


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
        # Maps trigger_name → list of proximity_pct values across periods
        # (in chronological order) for direction computation.
        proximity_history: dict[str, list[float]] = {t.name: [] for t in triggers}

        for period in input.periods:
            period_label = str(period.get("reporting_date", "unknown"))

            for trigger in triggers:
                metric_value = _extract_metric(period, trigger.metric, input)
                triggered = _is_triggered(metric_value, trigger.threshold, trigger.direction)

                prox = _compute_proximity(metric_value, trigger.threshold, trigger.direction)

                history = proximity_history[trigger.name]
                prior_prox = history[-1] if history else None
                dir_label = _compute_direction(prox, prior_prox, trigger.direction)

                history.append(prox)

                all_statuses.append(
                    TriggerStatus(
                        trigger_name=trigger.name,
                        period=period_label,
                        metric_value=metric_value,
                        threshold=trigger.threshold,
                        is_triggered=triggered,
                        proximity_pct=prox,
                        direction=dir_label,
                    )
                )

        # Latest-period summary: determine active and near-miss triggers.
        active_triggers: list[str] = []
        near_miss_triggers: list[str] = []

        if input.periods:
            latest_label = str(input.periods[-1].get("reporting_date", "unknown"))
            for status in all_statuses:
                if status.period != latest_label:
                    continue
                if status.is_triggered:
                    active_triggers.append(status.trigger_name)
                elif (
                    status.threshold is not None
                    and _NEAR_MISS_FLOOR <= status.proximity_pct < 100.0
                ):
                    near_miss_triggers.append(status.trigger_name)

        summary = _build_summary(
            active_triggers, near_miss_triggers, len(input.periods), triggers
        )

        output = CovenantOutput(
            trigger_statuses=all_statuses,
            active_triggers=active_triggers,
            near_miss_triggers=near_miss_triggers,
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
    n_periods: int,
    triggers: list[TriggerDefinition],
) -> str:
    """Build a plain-English compliance summary."""
    n_triggers = len(triggers)
    period_word = "period" if n_periods == 1 else "periods"

    if not active and not near_miss:
        return (
            f"All {n_triggers} covenant triggers are within compliance across "
            f"{n_periods} reporting {period_word}. No breaches or near-misses detected."
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
    )
