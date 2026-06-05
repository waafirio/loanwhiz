"""Period-by-period deal-state machine — the S6 integrator (#186).

This module is the integrator that finally connects the spine's building blocks
(#179) into a working, period-by-period reconstruction loop:

- **S1** ``deal_state`` — the canonical immutable :class:`DealState`, its
  ``seed_from_prospectus`` constructor, and the ``transition`` contract where
  ``closing[N] == opening[N+1]``.
- **S3** ``collections_aggregator`` — produces a period's
  :class:`PeriodCollections` from the tape (this module consumes that shape; it
  does not re-derive collections).
- **S4** ``waterfall_interpreter`` — ``interpret`` over an ordered ``StepSpec``
  list → ``WaterfallExecution`` → ``WaterfallResult`` via ``to_waterfall_result``.
- **S5** ``covenant_monitor`` — ``evaluate_triggers(deal_state, triggers)`` →
  ``TriggerEvaluation``, the predicate state S4's conditional gating reads.

The loop, per period
---------------------
Seed the **period-0 opening** ``DealState`` from the prospectus capital
structure, then for each subsequent period:

    opening DealState
      → evaluate triggers on the opening state (S5)
      → build the period's WaterfallFunds from opening state + collections
      → run the revenue + redemption waterfalls (S4), conditional steps GATED
        by the trigger engine (S5) via :class:`TriggerConditionEvaluator`, and
        the principal sequential↔pro-rata branch driven by the same triggers
      → map to a WaterfallResult and apply it through DealState.transition (S1),
        which records collections, allocates losses to the PDLs, redeems
        tranches, replenishes PDLs and tops up / draws the reserve
      → closing DealState  (== opening DealState of the next period)

The output is the full ordered per-period ``DealState`` **series** — the thing
S7 (reconciliation), S8 (invariants) and S9 (endpoints) consume — plus a
per-period diagnostic record (the waterfall execution traces and the trigger
evaluation) so downstream callers get both the state and its provenance.

Supersedes the dead machine
---------------------------
``waterfall_state.MultiPeriodWaterfallRunner`` was the old, never-wired
multi-period runner over the thin ``WaterfallState`` scalars. This module is the
single canonical ``DealState``-based loop; ``waterfall_state`` is left in place
only because the API still imports it (its rewire onto this engine is S9's job),
and is marked there as superseded.

Deal-agnostic by construction
-----------------------------
Nothing here branches on a specific deal. The prospectus figures (capital
structure, reserve target, original pool) and the ordered waterfall steps enter
as **arguments** — never as hardcoded constants in this module. A builtin
Green-Lion default step list is provided as a *convenience* (mirroring how S4
ships a ``DefaultConditionEvaluator``), re-using the canonical step lists already
defined in ``waterfall_runner`` rather than duplicating them.

Pure & deterministic — no LLM, no network. Mirrors the immutable typed-pydantic
conventions of the surrounding primitives.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from loanwhiz.primitives.covenant_monitor import (
    TriggerDefinition,
    TriggerEvaluation,
    evaluate_triggers,
)
from loanwhiz.primitives.deal_state import (
    DealState,
    PeriodCollections,
    WaterfallResult,
)
from loanwhiz.primitives.waterfall_interpreter import (
    ConditionEvaluator,
    StepSpec,
    WaterfallExecution,
    WaterfallFunds,
    allocate_principal,
    interpret,
    to_waterfall_result,
)

# The canonical Green-Lion priority-of-payments step lists live in
# ``waterfall_runner`` (expressed as data). Re-use them as the builtin default so
# the engine is runnable/testable today without duplicating the step vocabulary.
from loanwhiz.primitives.waterfall_runner import (
    _GREEN_LION_REDEMPTION_STEPS as DEFAULT_REDEMPTION_STEPS,
)
from loanwhiz.primitives.waterfall_runner import (
    _GREEN_LION_REVENUE_STEPS as DEFAULT_REVENUE_STEPS,
)

# Trigger name (in ``CovenantMonitor.DEFAULT_TRIGGERS``) whose breach flips the
# deal from pro-rata to sequential principal distribution.
_SEQUENTIAL_PAY_TRIGGER = "cumulative_loss_trigger"

# Per-tranche annual coupon rates (percent) the revenue interest needs read.
# These are *rate* inputs to the waterfall, supplied by the caller via the
# capital structure — kept off ``DealState`` (which tracks balances, not rates).
_DEFAULT_RATE_KEYS = ("class_a_rate_pct", "class_b_rate_pct", "class_c_rate_pct")


# ---------------------------------------------------------------------------
# The S4 ↔ S5 join: a ConditionEvaluator backed by the real trigger engine
# ---------------------------------------------------------------------------


class TriggerConditionEvaluator:
    """A :class:`ConditionEvaluator` backed by an S5 :class:`TriggerEvaluation`.

    This is the seam the epic designed S4's ``ConditionEvaluator`` Protocol for:
    the waterfall interpreter never parses condition prose itself — it asks an
    evaluator. Here that evaluator resolves a step's free-text ``condition``
    against the **real** trigger state computed by S5 over the opening
    ``DealState`` for the period, instead of S4's standalone
    ``DefaultConditionEvaluator``.

    Semantics
    ---------
    - ``sequential_pay_active`` reads the breach of the deal's sequential-pay
      trigger (``cumulative_loss_trigger`` by default — the cumulative-loss
      trigger that, when breached, switches principal from pro-rata to
      sequential). When that trigger is not evaluable, it falls back to the
      senior-protective default (active / sequential), matching S4's default.
    - ``evaluate`` resolves a condition by:
        * a **sequential-pay** mention → ``sequential_pay_active`` (negation
          honoured), so "if the Sequential Pay Trigger is *not* in effect" pays
          only under pro-rata;
        * a mention of a **named trigger** present in the evaluation → that
          trigger's breach (negation honoured), so e.g. a step gated "during the
          Revolving Period" or "while the Reserve Fund is below target" reads the
          live trigger;
        * otherwise → ``True`` (do not suppress; an unknown gate that silently
          zeroed a senior step is the more dangerous failure — same stance as
          S4's ``DefaultConditionEvaluator``).
    """

    # Phrases indicating the condition references the sequential pay trigger.
    _SEQ_MARKERS = ("sequential pay", "sequential payment", "sequential_pay")
    # Phrases that flip the polarity of a condition.
    _NEG_MARKERS = ("not ", "no longer", "absence", "unless", "is not")

    def __init__(
        self,
        evaluation: TriggerEvaluation,
        *,
        sequential_pay_trigger: str = _SEQUENTIAL_PAY_TRIGGER,
    ) -> None:
        self._eval = evaluation
        self._seq_trigger = sequential_pay_trigger

    def sequential_pay_active(self, funds: WaterfallFunds) -> bool:
        """``True`` when the sequential-pay trigger is breached this period.

        Reads the real S5 trigger state. When the trigger could not be evaluated
        (metric unresolved) we adopt S4's senior-protective default — sequential
        pay protects senior noteholders, so an unknown gate stays sequential.
        """
        if not self._eval.evaluable(self._seq_trigger):
            return True
        return self._eval.is_triggered(self._seq_trigger)

    def evaluate(self, condition: str, funds: WaterfallFunds) -> bool:
        text = (condition or "").strip().lower()
        if not text:
            return True

        negated = any(neg in text for neg in self._NEG_MARKERS)

        if any(marker in text for marker in self._SEQ_MARKERS):
            active = self.sequential_pay_active(funds)
            return (not active) if negated else active

        # Named-trigger conditions: the condition text names a known trigger.
        for name in self._eval.statuses:
            if name.lower().replace("_", " ") in text or name.lower() in text:
                if not self._eval.evaluable(name):
                    # Couldn't measure the gate → don't suppress the step.
                    return True
                fired = self._eval.is_triggered(name)
                return (not fired) if negated else fired

        # Unknown condition prose: pay the step (conservative for distribution).
        return True


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------


class PeriodInput(BaseModel):
    """One period's exogenous inputs to the reconstruction loop.

    Deal-agnostic: every figure comes from the deal's tape / collections layer,
    not from a hardcoded deal constant. The loop transitions the *opening* state
    of this period through these inputs to produce its closing state.

    Attributes
    ----------
    reporting_date:
        ISO period-end date for this period's closing state (e.g.
        ``"2026-03-31"``).
    collections:
        The period's cashflow breakdown (S3's :class:`PeriodCollections`):
        interest, scheduled principal, prepayment, recovery, realized loss.
    days_in_period:
        Day count for interest accrual (Act/360) in the waterfall.
    revolving:
        Whether the deal is in its revolving period this period (carried onto
        the closing state's ``revolving`` flag). ``None`` carries the prior
        value forward unchanged.
    """

    reporting_date: str = Field(..., description="ISO period-end date for the closing state.")
    collections: PeriodCollections = Field(
        ..., description="The period's cashflow breakdown (S3)."
    )
    days_in_period: int = Field(
        default=30, gt=0, description="Day count for interest accrual (Act/360)."
    )
    revolving: bool | None = Field(
        default=None, description="Revolving-period flag for the closing state (carried if None)."
    )


class PeriodResult(BaseModel):
    """One period's transition result: the closing state + its provenance.

    Attributes
    ----------
    closing_state:
        The :class:`DealState` at the end of this period (== the opening state of
        the next period by construction).
    revenue_execution / redemption_execution:
        The S4 :class:`WaterfallExecution` audit traces for this period's revenue
        and redemption waterfalls (so downstream callers can inspect every step's
        need / distribution / shortfall, including which steps were *gated* by a
        trigger).
    trigger_evaluation:
        The S5 :class:`TriggerEvaluation` over the **opening** state that gated
        this period's conditional steps (and drove the sequential-pay branch).
    """

    closing_state: DealState
    revenue_execution: WaterfallExecution
    redemption_execution: WaterfallExecution
    trigger_evaluation: TriggerEvaluation


class DealStateSeries(BaseModel):
    """The full per-period reconstruction over a deal's tapes.

    Attributes
    ----------
    states:
        The ordered ``DealState`` series — ``states[0]`` is the prospectus-seeded
        period-0 opening state, and each subsequent entry is a period's closing
        state. By construction ``states[N] == opening[N+1]``, so the series is the
        canonical opening→closing chain S7/S8/S9 consume.
    period_results:
        One :class:`PeriodResult` per *transition* (i.e. ``len(states) - 1``
        entries), carrying each period's waterfall traces and trigger evaluation.
    """

    states: list[DealState]
    period_results: list[PeriodResult]

    @property
    def final_state(self) -> DealState:
        """The last (most recent) state in the series."""
        return self.states[-1]

    @property
    def cumulative_losses(self) -> float:
        """Cumulative realized losses at the final state."""
        return self.states[-1].cumulative_losses


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


def _funds_from_state(
    state: DealState,
    collections: PeriodCollections,
    *,
    rates: dict[str, float],
    days_in_period: int,
    senior_fees: float,
) -> WaterfallFunds:
    """Build the S4 ``WaterfallFunds`` view from the opening state + collections.

    The funds pots come from the period's collections (S3): the revenue pot is
    the interest collected; the principal pot is scheduled + prepayment +
    recovery (the available principal funds). Tranche balances / PDLs / reserve
    come from the **opening** ``DealState``; coupon rates come from the
    caller-supplied capital structure (rates are not tracked on ``DealState``).
    """
    available_revenue = collections.interest
    available_principal = (
        collections.scheduled_principal
        + collections.prepayment
        + collections.recovery
    )
    return WaterfallFunds(
        available_revenue_funds=available_revenue,
        available_principal_funds=available_principal,
        senior_fees=senior_fees,
        class_a_balance=state.class_a_balance,
        class_b_balance=state.class_b_balance,
        class_c_balance=state.class_c_balance,
        class_a_rate_pct=rates.get("class_a_rate_pct", 0.0),
        class_b_rate_pct=rates.get("class_b_rate_pct", 0.0),
        class_c_rate_pct=rates.get("class_c_rate_pct", 0.0),
        class_a_pdl_balance=state.class_a_pdl,
        class_b_pdl_balance=state.class_b_pdl,
        class_c_pdl_balance=state.class_c_pdl,
        reserve_balance=state.reserve_balance,
        reserve_target=state.reserve_target,
        days_in_period=days_in_period,
    )


def run_period(
    opening: DealState,
    period: PeriodInput,
    *,
    rates: dict[str, float],
    triggers: list[TriggerDefinition] | None = None,
    revenue_steps: list[StepSpec] = DEFAULT_REVENUE_STEPS,
    redemption_steps: list[StepSpec] = DEFAULT_REDEMPTION_STEPS,
    principal_classes: tuple[str, ...] = ("class_a", "class_b"),
    senior_fees: float = 0.0,
) -> PeriodResult:
    """Advance one period: opening ``DealState`` → closing, composing S3/S4/S5/S1.

    The single-period kernel of the loop. It does **not** re-derive collections
    or re-implement the waterfall / trigger logic — it composes the existing
    primitives:

    1. Evaluate the deal's triggers on the **opening** state (S5,
       ``evaluate_triggers``) and wrap them in a :class:`TriggerConditionEvaluator`
       so S4's conditional gating + sequential-pay branch read the live trigger
       state.
    2. Build the period's :class:`WaterfallFunds` from the opening state +
       collections.
    3. Run the revenue waterfall and the redemption waterfall through S4's
       ``interpret``, with the principal sequential↔pro-rata allocation
       (``allocate_principal``) fed back as ``need_overrides`` — exactly the S4
       composition the single-period ``WaterfallRunner`` uses.
    4. Map the two executions to a :class:`WaterfallResult` and advance the state
       via S1's ``DealState.transition`` (records collections, allocates the
       period's realized loss to the PDLs, redeems tranches, replenishes PDLs,
       tops up / draws the reserve).

    Parameters
    ----------
    opening:
        The opening ``DealState`` for this period.
    period:
        The period's exogenous inputs (collections, reporting date, day count).
    rates:
        ``{class_x_rate_pct: float}`` coupon rates for the interest needs.
    triggers:
        Trigger definitions to evaluate (defaults to ``CovenantMonitor``'s).
    revenue_steps / redemption_steps:
        Ordered ``StepSpec`` lists (default: the Green-Lion canonical lists).
    principal_classes:
        Tranches that receive principal from the redemption waterfall (the
        sequential-pay allocation is restricted to these — Green Lion redeems
        Class C from revenue, not principal).
    senior_fees:
        Senior-fee need for the revenue waterfall's senior-fees step.

    Returns
    -------
    PeriodResult
        The closing state plus the period's waterfall traces and trigger eval.
    """
    # 1. Triggers over the opening state → the condition evaluator.
    trigger_eval = evaluate_triggers(opening, triggers)
    evaluator: ConditionEvaluator = TriggerConditionEvaluator(trigger_eval)

    # 2. Funds view from the opening state + this period's collections.
    funds = _funds_from_state(
        opening,
        period.collections,
        rates=rates,
        days_in_period=period.days_in_period,
        senior_fees=senior_fees,
    )

    # 3a. Revenue waterfall (a)→(k), gated by the trigger engine.
    revenue_execution = interpret(
        revenue_steps,
        funds,
        available=funds.available_revenue_funds,
        evaluator=evaluator,
    )

    # 3b. Redemption waterfall — sequential↔pro-rata principal allocation driven
    # by the same trigger engine, fed back through need_overrides.
    principal_alloc = allocate_principal(
        funds,
        available=funds.available_principal_funds,
        classes=principal_classes,
        evaluator=evaluator,
    )
    redemption_execution = interpret(
        redemption_steps,
        funds,
        available=funds.available_principal_funds,
        evaluator=evaluator,
        need_overrides={
            f"{cls}_principal": principal_alloc.get(cls, 0.0)
            for cls in principal_classes
        },
    )

    # 4. Map to a WaterfallResult and advance the canonical state (S1).
    waterfall_result: WaterfallResult = to_waterfall_result(
        revenue=revenue_execution,
        redemption=redemption_execution,
        principal_allocation=principal_alloc,
    )
    closing = opening.transition(
        collections=period.collections,
        waterfall_result=waterfall_result,
        realized_loss=period.collections.realized_loss,
        next_reporting_date=period.reporting_date,
        next_revolving=period.revolving,
    )

    return PeriodResult(
        closing_state=closing,
        revenue_execution=revenue_execution,
        redemption_execution=redemption_execution,
        trigger_evaluation=trigger_eval,
    )


def reconstruct_period_series(
    *,
    capital_structure: dict[str, float],
    reserve_target: float,
    original_pool_balance: float,
    opening_pool_balance: float | None = None,
    seed_reporting_date: str,
    periods: list[PeriodInput],
    triggers: list[TriggerDefinition] | None = None,
    revenue_steps: list[StepSpec] = DEFAULT_REVENUE_STEPS,
    redemption_steps: list[StepSpec] = DEFAULT_REDEMPTION_STEPS,
    principal_classes: tuple[str, ...] = ("class_a", "class_b"),
    senior_fees: float = 0.0,
    seed_revolving: bool = False,
) -> DealStateSeries:
    """Reconstruct a deal's full per-period ``DealState`` series from the spine.

    Seeds the **period-0 opening** state from the prospectus capital structure
    (S1's ``seed_from_prospectus`` — liabilities from the prospectus per spike
    S0), then threads ``run_period`` across ``periods`` so each closing state is
    the next period's opening state (``closing[N] == opening[N+1]``).

    The result's ``states`` list is the canonical opening→closing chain S7/S8/S9
    consume; ``period_results`` carries each transition's provenance.

    Parameters
    ----------
    capital_structure:
        Prospectus capital structure — at least ``class_{a,b,c}_balance`` (the
        seed tranche balances) and, for the interest needs, ``class_{a,b,c}_rate_pct``.
        Coupon-rate keys are passed through to the per-period funds view; missing
        rate keys default to 0 (no interest need for that tranche).
    reserve_target:
        The reserve account target (the reserve opens funded at this amount).
    original_pool_balance:
        Pool balance at deal closing — the factor / loss-rate denominator.
    opening_pool_balance:
        Pool balance at the start of period 0 (defaults to
        ``original_pool_balance`` — pool factor 1.0 at par).
    seed_reporting_date:
        ISO reporting date for the seeded period-0 opening state.
    periods:
        Ordered :class:`PeriodInput` list — one per *transition* after period 0.
        Each carries the collections and the reporting date for that period's
        closing state.
    triggers / revenue_steps / redemption_steps / principal_classes / senior_fees:
        Forwarded to :func:`run_period` (see its docstring).
    seed_revolving:
        Whether period-0 opens in the revolving period.

    Returns
    -------
    DealStateSeries
        ``states`` — the prospectus-seeded period-0 opening state followed by
        every period's closing state; ``period_results`` — one per transition.
    """
    rates = {k: float(capital_structure[k]) for k in _DEFAULT_RATE_KEYS if k in capital_structure}

    opening = DealState.seed_from_prospectus(
        capital_structure,
        reserve_target=reserve_target,
        original_pool_balance=original_pool_balance,
        opening_pool_balance=opening_pool_balance,
        reporting_date=seed_reporting_date,
        revolving=seed_revolving,
        period_index=0,
    )

    states: list[DealState] = [opening]
    period_results: list[PeriodResult] = []

    current = opening
    for period in periods:
        result = run_period(
            current,
            period,
            rates=rates,
            triggers=triggers,
            revenue_steps=revenue_steps,
            redemption_steps=redemption_steps,
            principal_classes=principal_classes,
            senior_fees=senior_fees,
        )
        period_results.append(result)
        states.append(result.closing_state)
        # closing[N] becomes opening[N+1] — the spine invariant the loop drives.
        current = result.closing_state

    return DealStateSeries(states=states, period_results=period_results)
