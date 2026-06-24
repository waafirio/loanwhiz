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

The single canonical waterfall engine
-------------------------------------
This module is the one ``DealState``-based per-period loop. The old duplicate
execution paths — ``waterfall_runner.WaterfallRunner`` (single-period snapshot),
``waterfall_state.MultiPeriodWaterfallRunner`` over the thin ``WaterfallState``
scalars, and ``cashflow_projector.CashflowProjector`` — were deleted in #276.
Nothing else executes a waterfall: the registered ``waterfall_runner`` MCP tool
is now a thin single-period wrapper over ``run_period`` (it imports the
``DEFAULT_*_STEPS`` lists below), and ``/project`` folds a ``ScenarioGenerator``
stream through ``run_period`` (#275).

Deal-agnostic by construction
-----------------------------
Nothing here branches on a specific deal. The prospectus figures (capital
structure, reserve target, original pool) and the ordered waterfall steps enter
as **arguments** — never as hardcoded constants in this module. A builtin
Green-Lion default step list (``DEFAULT_REVENUE_STEPS`` / ``DEFAULT_REDEMPTION_STEPS``)
is provided as a *convenience* (mirroring how S4 ships a
``DefaultConditionEvaluator``); the steps are expressed as data, so an extracted
``DealModel.waterfalls[*].steps`` runs through the same kernel.

Pure & deterministic — no LLM, no network. Mirrors the immutable typed-pydantic
conventions of the surrounding primitives.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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
    TrancheFunds,
    WaterfallExecution,
    WaterfallFunds,
    allocate_principal,
    interpret,
    to_waterfall_result,
)

if TYPE_CHECKING:
    # Imported under TYPE_CHECKING only: ``CanonicalPeriodInputs`` is used solely
    # in (string) type annotations — never at runtime (the sole isinstance check
    # is against the local ``PeriodInput``). A runtime import here would close a
    # pre-existing import cycle (``domain`` → ``domain.inputs`` → ``provenance``
    # → ``primitives.base`` → ``primitives`` → ``period_state_machine`` →
    # ``domain.inputs`` mid-init), making ``import loanwhiz.domain`` fail when it
    # is the first package imported. Now that ``deal_state`` imports
    # ``domain.state`` (#363), that latent cycle would otherwise bite, so the
    # back-edge is deferred.
    from loanwhiz.domain.inputs import PeriodInputs as CanonicalPeriodInputs

# The canonical Green-Lion priority-of-payments step lists, expressed as *data*
# (an ordered ``StepSpec`` list the generic interpreter executes — the same shape
# the extraction layer produces in ``DealModel.waterfalls[*].steps``). These live
# here, in the kernel module, because ``run_period`` / ``reconstruct_period_series``
# are now their sole runtime consumers (the old ``WaterfallRunner`` /
# ``CashflowProjector`` / ``MultiPeriodWaterfallRunner`` execution paths that once
# shared them were deleted in #276). The thin ``waterfall_runner`` MCP wrapper
# imports these back from here. Recipients not in the interpreter's
# need-calculator registry (operating fees, expense account, subordinated swap,
# new receivables, deferred purchase price) contribute need 0 and are recorded
# ``not_evaluable`` — the audit trace stays structurally complete without
# inventing figures the inputs do not carry.

DEFAULT_REVENUE_STEPS: list[StepSpec] = [
    StepSpec(priority="(a)", recipient="senior_fees"),
    StepSpec(
        priority="(b)",
        recipient="operating_fees",
        condition="pari passu: servicer, administrator, paying agent",
    ),
    StepSpec(priority="(c)", recipient="swap_payment"),
    StepSpec(priority="(d)", recipient="class_a_interest"),
    StepSpec(priority="(e)", recipient="class_a_pdl_replenishment"),
    StepSpec(priority="(f)", recipient="reserve_account_replenishment"),
    StepSpec(priority="(g)", recipient="expense_account_replenishment"),
    StepSpec(priority="(h)", recipient="class_b_pdl_replenishment"),
    StepSpec(
        priority="(i)",
        recipient="subordinated_swap_payment",
        condition="subordinated swap",
    ),
    StepSpec(
        priority="(j)",
        recipient="class_c_principal_from_revenue",
        condition="from First Optional Redemption Date",
    ),
    StepSpec(
        priority="(k)", recipient="deferred_purchase_price_seller", residual=True
    ),
]

# Redemption (principal) steps. Steps (b)/(c) carry the principal allocation,
# which ``run_period`` computes via the sequential-pay branch
# (``allocate_principal``) and feeds back through ``need_overrides`` — so the
# pro-rata ↔ sequential choice (a since-closed modelling gap; see
# SYSTEM-STATUS.md) actually gates Class B's
# principal instead of Class A always taking 100%.
DEFAULT_REDEMPTION_STEPS: list[StepSpec] = [
    StepSpec(
        priority="(a)",
        recipient="new_mortgage_receivables",
        condition="during Revolving Period",
    ),
    StepSpec(priority="(b)", recipient="class_a_principal"),
    StepSpec(priority="(c)", recipient="class_b_principal"),
    StepSpec(
        priority="(d)",
        recipient="deferred_purchase_price_seller_principal",
        residual=True,
    ),
]

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
    swap_payment: float = 0.0,
    available_revenue: float | None = None,
    available_principal: float | None = None,
) -> WaterfallFunds:
    """Build the S4 ``WaterfallFunds`` view from the opening state + collections.

    The funds pots default to the period's collections (S3): the revenue pot is
    the interest collected; the principal pot is scheduled + prepayment +
    recovery (the available principal funds). Tranche balances / PDLs / reserve
    come from the **opening** ``DealState``; coupon rates come from the
    caller-supplied capital structure (rates are not tracked on ``DealState``).

    The report path (#265) supplies the aggregate available funds directly via
    ``available_revenue`` / ``available_principal`` (the per-leg breakdown is not
    published); when given, they override the collections-derived defaults. The
    tape path passes the collections-derived values (or ``None``), so the funds
    view is unchanged.
    """
    if available_revenue is None:
        available_revenue = collections.interest
    if available_principal is None:
        available_principal = (
            collections.scheduled_principal
            + collections.prepayment
            + collections.recovery
        )
    # Build the per-tranche funds context by name from the opening state's
    # tranche list (no hardcoded A/B/C). Coupon rates come from the caller's
    # capital structure (``{<name>_rate_pct: float}``) — rates are not tracked on
    # ``DealState`` — defaulting to 0 (no interest need) for a tranche with no
    # rate supplied.
    tranches = [
        TrancheFunds(
            name=t.name,
            balance=t.balance,
            rate_pct=rates.get(f"{t.name}_rate_pct", 0.0),
            pdl_balance=t.pdl_balance,
        )
        for t in state.tranches
    ]
    return WaterfallFunds(
        available_revenue_funds=available_revenue,
        available_principal_funds=available_principal,
        senior_fees=senior_fees,
        swap_payment=swap_payment,
        tranches=tranches,
        reserve_balance=state.reserve_balance,
        reserve_target=state.reserve_target,
        days_in_period=days_in_period,
    )


# ---------------------------------------------------------------------------
# PeriodInputs (canonical) → kernel-internal normalized period
# ---------------------------------------------------------------------------


class _NormalizedPeriod(BaseModel):
    """The kernel's per-period inputs, normalized from either source shape.

    ``run_period`` accepts *either* the legacy tape-only :class:`PeriodInput`
    *or* the canonical adapter-agnostic ``domain.PeriodInputs`` (the #265
    generalisation — spec migration step 1). Both are reduced to this internal
    shape so the kernel body has a single code path.

    The ``report_sourced`` / ``step_overrides`` / ``step_sources`` fields carry
    the report-path semantics: report-supplied step amounts (the engine has no
    formula for them) and the instruction to **clear extracted conditions** on
    report-sourced steps — the report is the post-resolution actual, so
    re-gating a step the report already paid (or zeroed) would double-count.

    Attributes
    ----------
    collections:
        The :class:`PeriodCollections` ``DealState.transition`` records. On the
        tape path this is the real per-leg breakdown; on the report path it is
        reconstructed from the aggregate available funds (legs unknown).
    reporting_date / days_in_period / revolving:
        Period metadata threaded to the funds view and the transition.
    available_revenue / available_principal:
        The two waterfall pots. On the tape path these are derived from the
        collection legs exactly as today; on the report path they come straight
        from the aggregate ``PeriodInputs.available_revenue/principal``.
    step_overrides:
        ``priority_label -> reported amount`` for report-supplied steps. Empty
        on the tape path → engine behaviour identical to today.
    step_sources:
        ``priority_label -> "engine" | "reported" | "residual"``.
    report_sourced:
        ``True`` when these inputs are report-actuals (``source == "report"``).
        Drives the condition-clearing: report-sourced steps are not re-gated.
    """

    collections: PeriodCollections
    reporting_date: str
    days_in_period: int
    revolving: bool | None
    available_revenue: float
    available_principal: float
    # Per-waterfall report-supplied maps (#270). Each waterfall is fed its OWN
    # override/source map so the two waterfalls' reused labels (a)…(d) never bleed
    # across (the #269 cross-waterfall flat-label collision). ``_normalize_period``
    # resolves these from the canonical ``PeriodInputs``'s per-waterfall fields,
    # falling back to its flat ``step_overrides`` / ``step_sources`` when a
    # per-waterfall map is empty (so single-waterfall callers and the tape path are
    # unchanged).
    revenue_step_overrides: dict[str, float] = Field(default_factory=dict)
    revenue_step_sources: dict[str, str] = Field(default_factory=dict)
    redemption_step_overrides: dict[str, float] = Field(default_factory=dict)
    redemption_step_sources: dict[str, str] = Field(default_factory=dict)
    report_sourced: bool = False


def _normalize_period(period: "PeriodInput | CanonicalPeriodInputs") -> _NormalizedPeriod:
    """Reduce the incoming period (legacy or canonical) to ``_NormalizedPeriod``.

    The legacy :class:`PeriodInput` path is the *exact* current behaviour: funds
    come from the collection legs, no overrides, conditions preserved. The
    canonical ``domain.PeriodInputs`` path additionally supplies aggregate
    available funds, ``step_overrides``/``step_sources``, and the report-sourced
    flag — but a tape-source ``PeriodInputs`` with ``legs`` present and empty
    overrides reduces to the *same* values the legacy path would, so the engine
    is byte-for-byte unchanged on the tape path.
    """
    if isinstance(period, PeriodInput):
        collections = period.collections
        return _NormalizedPeriod(
            collections=collections,
            reporting_date=period.reporting_date,
            days_in_period=period.days_in_period,
            revolving=period.revolving,
            available_revenue=collections.interest,
            available_principal=(
                collections.scheduled_principal
                + collections.prepayment
                + collections.recovery
            ),
        )

    # Canonical domain.PeriodInputs.
    if period.legs is not None:
        # Tape path: the finer per-leg breakdown is known — record the real
        # collections, identical to the legacy tape path.
        legs = period.legs
        collections = PeriodCollections(
            interest=legs.interest,
            scheduled_principal=legs.scheduled_principal,
            prepayment=legs.prepayment,
            recovery=legs.recovery,
            realized_loss=legs.realized_loss,
        )
        available_revenue = collections.interest
        available_principal = (
            collections.scheduled_principal
            + collections.prepayment
            + collections.recovery
        )
    else:
        # Report path: only the aggregates are known. Fold the aggregate
        # available principal into ``scheduled_principal`` (the legs are not
        # individually published) so ``DealState.transition`` records the pool
        # movement; route revenue through ``interest``.
        collections = PeriodCollections(
            interest=max(0.0, period.available_revenue),
            scheduled_principal=max(0.0, period.available_principal),
            realized_loss=max(0.0, period.realized_loss),
        )
        available_revenue = period.available_revenue
        available_principal = period.available_principal

    # Resolve each waterfall's override/source map: prefer the per-waterfall map
    # (#270), fall back to the flat map when the per-waterfall one is empty. This
    # keeps single-waterfall callers (and the tape path, all maps empty) unchanged
    # while letting the report path feed each waterfall its own report actuals so
    # the reused labels (a)…(d) never collide across waterfalls.
    flat_overrides = dict(period.step_overrides)
    flat_sources = dict(period.step_sources)
    rev_overrides = dict(period.revenue_step_overrides) or flat_overrides
    rev_sources = dict(period.revenue_step_sources) or flat_sources
    red_overrides = dict(period.redemption_step_overrides) or flat_overrides
    red_sources = dict(period.redemption_step_sources) or flat_sources

    return _NormalizedPeriod(
        collections=collections,
        reporting_date=period.reporting_date,
        days_in_period=period.days_in_period,
        revolving=None,
        available_revenue=available_revenue,
        available_principal=available_principal,
        revenue_step_overrides=rev_overrides,
        revenue_step_sources=rev_sources,
        redemption_step_overrides=red_overrides,
        redemption_step_sources=red_sources,
        report_sourced=period.source == "report",
    )


def _apply_step_overrides(
    steps: list[StepSpec],
    *,
    step_overrides: dict[str, float],
    step_sources: dict[str, str],
    report_sourced: bool,
) -> tuple[list[StepSpec], dict[str, float]]:
    """Thread report-supplied overrides + condition-clearing onto a step list.

    Returns ``(specs, need_overrides)``:

    - ``specs`` — the input steps, with the **condition cleared** on any step
      that is report-sourced (``report_sourced`` and the step is not explicitly
      marked ``"engine"`` in ``step_sources``, or the step's own source is
      ``"reported"``/``"residual"``). Clearing matches the offline harness's
      ``_build_specs``: the report's published distribution already reflects the
      conditions' resolution, so re-gating here would double-count. Steps that
      stay engine-computed keep their conditions (and the live trigger gating).
    - ``need_overrides`` — ``recipient -> reported amount``, translated from the
      ``priority_label -> amount`` ``step_overrides`` map via each step's
      ``priority``/``recipient`` pair (the interpreter keys overrides by
      recipient, while the report keys by priority label).

    When ``step_overrides`` is empty and ``report_sourced`` is ``False`` (the
    tape path), this returns the steps unchanged and an empty override map — the
    engine behaves exactly as it did before #265.
    """
    if not step_overrides and not report_sourced:
        return steps, {}

    out_specs: list[StepSpec] = []
    need_overrides: dict[str, float] = {}
    for spec in steps:
        label = spec.priority
        source = step_sources.get(label)
        # A step is report-sourced when the whole period is report-actuals and
        # the step is not explicitly pinned to "engine", or when the step's own
        # source says so.
        step_is_reported = (
            source in ("reported", "residual")
            or (report_sourced and source != "engine")
        )
        if step_is_reported and spec.condition is not None:
            spec = spec.model_copy(update={"condition": None})
        out_specs.append(spec)
        if label in step_overrides:
            need_overrides[spec.recipient] = step_overrides[label]
    return out_specs, need_overrides


def run_period(
    opening: DealState,
    period: "PeriodInput | CanonicalPeriodInputs",
    *,
    rates: dict[str, float],
    triggers: list[TriggerDefinition] | None = None,
    revenue_steps: list[StepSpec] = DEFAULT_REVENUE_STEPS,
    redemption_steps: list[StepSpec] = DEFAULT_REDEMPTION_STEPS,
    principal_classes: tuple[str, ...] = ("class_a", "class_b"),
    senior_fees: float = 0.0,
    swap_payment: float = 0.0,
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
       composition the thin single-period ``waterfall_runner`` MCP wrapper folds
       a single period through.
    4. Map the two executions to a :class:`WaterfallResult` and advance the state
       via S1's ``DealState.transition`` (records collections, allocates the
       period's realized loss to the PDLs, redeems tranches, replenishes PDLs,
       tops up / draws the reserve).

    Inputs: legacy or canonical (#265)
    ----------------------------------
    ``period`` is **either** the legacy tape-only :class:`PeriodInput` **or** the
    canonical adapter-agnostic ``domain.PeriodInputs``. The canonical shape
    additionally carries ``step_overrides`` (report-supplied step amounts the
    engine has no formula for) and ``step_sources``; report-sourced steps have
    their extracted conditions cleared before interpretation (the report is the
    post-resolution actual). A tape-source ``PeriodInputs`` with ``legs`` present
    and empty overrides is reduced to the *same* values the legacy path produces,
    so the tape path's output is byte-for-byte unchanged.

    Parameters
    ----------
    opening:
        The opening ``DealState`` for this period.
    period:
        The period's exogenous inputs — a legacy :class:`PeriodInput` or a
        canonical ``domain.PeriodInputs``.
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
    swap_payment:
        Net non-subordinated swap need for the revenue waterfall's swap step
        (c). Defaults to ``0.0``; the tape/report fold paths carry no swap leg
        (it is not on ``DealState`` or ``PeriodInputs``), so they are unchanged.
        The single-period ``waterfall_runner`` MCP wrapper threads its input's
        ``swap_payment`` through here so step (c) is modelled as before.

    Returns
    -------
    PeriodResult
        The closing state plus the period's waterfall traces and trigger eval.
    """
    norm = _normalize_period(period)

    # 1. Triggers over the opening state → the condition evaluator.
    trigger_eval = evaluate_triggers(opening, triggers)
    evaluator: ConditionEvaluator = TriggerConditionEvaluator(trigger_eval)

    # 2. Funds view from the opening state + this period's available funds.
    funds = _funds_from_state(
        opening,
        norm.collections,
        rates=rates,
        days_in_period=norm.days_in_period,
        senior_fees=senior_fees,
        swap_payment=swap_payment,
        available_revenue=norm.available_revenue,
        available_principal=norm.available_principal,
    )

    # 2b. Thread report-supplied overrides + clear conditions on report-sourced
    # steps (no-op on the tape path → empty-overrides behaviour is unchanged).
    rev_steps, rev_overrides = _apply_step_overrides(
        revenue_steps,
        step_overrides=norm.revenue_step_overrides,
        step_sources=norm.revenue_step_sources,
        report_sourced=norm.report_sourced,
    )
    red_steps, red_overrides = _apply_step_overrides(
        redemption_steps,
        step_overrides=norm.redemption_step_overrides,
        step_sources=norm.redemption_step_sources,
        report_sourced=norm.report_sourced,
    )

    # 3a. Revenue waterfall (a)→(k), gated by the trigger engine.
    revenue_execution = interpret(
        rev_steps,
        funds,
        available=funds.available_revenue_funds,
        evaluator=evaluator,
        need_overrides=rev_overrides or None,
    )

    # 3b. Redemption waterfall.
    #
    # Tape path: the engine computes its own sequential↔pro-rata principal
    # allocation (driven by the trigger engine), fed back through need_overrides,
    # and that allocation also drives the tranche redemption in the state
    # transition.
    #
    # Report path (#270): the report's redemption PoP IS the post-resolution
    # actual — the servicer already decided what principal each tranche received
    # (e.g. during the revolving period, principal funds the purchase of new
    # receivables, NOT note redemption, so the report's class_*_notes_principal
    # lines are 0). Running the engine's own `allocate_principal` here would
    # synthesise a sequential redemption the report never made and over-redeem the
    # senior tranche. So on the report path we do NOT compute an allocation: the
    # report-supplied redemption overrides fully determine the distribution, and
    # the tranche redemption is read from the redemption execution's own lines.
    if norm.report_sourced:
        principal_alloc = None
        combined_red_overrides = dict(red_overrides)
    else:
        principal_alloc = allocate_principal(
            funds,
            available=funds.available_principal_funds,
            classes=principal_classes,
            evaluator=evaluator,
        )
        combined_red_overrides = {
            f"{cls}_principal": principal_alloc.get(cls, 0.0)
            for cls in principal_classes
        }
        combined_red_overrides.update(red_overrides)
    redemption_execution = interpret(
        red_steps,
        funds,
        available=funds.available_principal_funds,
        evaluator=evaluator,
        need_overrides=combined_red_overrides,
    )

    # 4. Map to a WaterfallResult and advance the canonical state (S1). On the
    # report path `principal_allocation` is None, so `to_waterfall_result` reads
    # the tranche principal straight from the redemption execution's lines.
    waterfall_result: WaterfallResult = to_waterfall_result(
        revenue=revenue_execution,
        redemption=redemption_execution,
        principal_allocation=principal_alloc,
    )
    closing = opening.transition(
        collections=norm.collections,
        waterfall_result=waterfall_result,
        realized_loss=norm.collections.realized_loss,
        next_reporting_date=norm.reporting_date,
        next_revolving=norm.revolving,
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
    periods: "list[PeriodInput] | list[CanonicalPeriodInputs]",
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
        Ordered period-inputs list — one per *transition* after period 0. May be
        either legacy :class:`PeriodInput` (tape-only collections) or canonical
        ``domain.PeriodInputs`` (the tape adapter's ``source="tape"`` inputs,
        carrying legs + risk_signals); ``run_period`` accepts both, and a tape
        ``PeriodInputs`` with ``legs`` present reduces to the same normalized
        period the legacy ``PeriodInput`` would, so the fold is unchanged.
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
