"""Waterfall runner primitive — Green Lion 2026-1 Revenue and Redemption waterfalls.

Executes the Green Lion 2026-1 B.V. Priority of Payments against a set of
monthly collection inputs (interest receipts, principal collections,
prepayments, recoveries) and returns per-tranche distributions with a full
deterministic audit trace.

Two waterfalls are modelled:

Revenue Priority of Payments (prospectus section 5.2, 11 steps):
  (a) Senior fees (Security Trustee)
  (b) Operating fees (Servicer, Administrator, Paying Agent, etc.) — pari passu
  (c) Swap payments (non-subordinated)
  (d) Class A interest
  (e) Class A PDL replenishment
  (f) Reserve Account replenishment
  (g) Expense Account replenishment
  (h) Class B PDL replenishment
  (i) Subordinated swap payments
  (j) Class C principal (from First Optional Redemption Date)
  (k) Deferred Purchase Price to Seller

Redemption Priority (principal waterfall, 4 steps):
  (a) New Mortgage Receivables (during Revolving Period)
  (b) Class A principal — pari passu
  (c) Class B principal — pari passu
  (d) Deferred Purchase Price to Seller

Confidence is always 1.0 — this is a pure deterministic computation; no LLM.
"""

from __future__ import annotations

import time

from pydantic import BaseModel

from loanwhiz.primitives.base import (
    AuditEntry,
    BaseInput,
    Citation,
    Primitive,
    PrimitiveResult,
)
from loanwhiz.primitives.registry import register_primitive
from loanwhiz.primitives.waterfall_interpreter import (
    ConditionEvaluator,
    StepSpec,
    WaterfallExecution,
    WaterfallFunds,
    allocate_principal,
    interpret,
)

# ---------------------------------------------------------------------------
# Output sub-models
# ---------------------------------------------------------------------------


class WaterfallStep(BaseModel):
    """One priority step in the waterfall.

    Attributes:
        priority:            The step label from the prospectus, e.g. ``"(a)"``.
        recipient:           Snake-case recipient identifier, e.g.
                             ``"senior_fees"``.
        amount_available:    Funds available at the start of this step.
        amount_distributed:  Funds actually distributed (min of need and
                             available).
        shortfall:           Unmet need = ``max(0, need - amount_available)``.
        condition:           Any trigger condition from the prospectus that
                             applied to this step, or ``None``.
    """

    priority: str
    recipient: str
    amount_available: float
    amount_distributed: float
    shortfall: float
    condition: str | None = None


class TrancheDistribution(BaseModel):
    """Aggregate distribution summary for one tranche.

    Attributes:
        tranche:             ``"class_a"``, ``"class_b"``, or ``"class_c"``.
        interest_received:   Total interest distributed to this tranche.
        principal_received:  Total principal distributed to this tranche.
        total_received:      ``interest_received + principal_received``.
        opening_balance:     Outstanding balance at the start of the period.
        closing_balance:     ``opening_balance - principal_received`` (floored
                             at zero).
    """

    tranche: str
    interest_received: float
    principal_received: float
    total_received: float
    opening_balance: float
    closing_balance: float


# ---------------------------------------------------------------------------
# Input / Output models
# ---------------------------------------------------------------------------


class WaterfallInput(BaseInput):
    """Input schema for the waterfall runner.

    All monetary amounts are in the deal currency (EUR for Green Lion 2026-1).
    Interest rates are in percent per annum (e.g. 3.62 for 3.62%).

    Attributes:
        reporting_period:         Human-readable period, e.g. ``"April 2026"``.
        available_revenue_funds:  Total interest + swap receipts collected.
        available_principal_funds: Total principal collections (scheduled +
                                  prepayments + recoveries).
        senior_fees:              Trustee fee for step (a).
        swap_payment:             Net non-subordinated swap amount for step (c);
                                  pass 0.0 when there is no swap.
        class_a_balance:          Outstanding Class A note balance.
        class_a_rate_pct:         Class A annual coupon rate in percent
                                  (e.g. 3.62 for EURIBOR 3.19 + 0.43).
        class_b_balance:          Outstanding Class B note balance.
        class_c_balance:          Outstanding Class C note balance.
        reserve_account_balance:  Current reserve account balance.
        reserve_account_target:   Required reserve account balance (target).
        class_a_pdl_balance:      Principal Deficiency Ledger debit balance for
                                  Class A; 0.0 when no losses have occurred.
        class_b_pdl_balance:      Principal Deficiency Ledger debit balance for
                                  Class B; 0.0 when no losses have occurred.
        days_in_period:           Actual days in the payment period. Defaults to
                                  90 (quarterly, Act/360 approximation).
    """

    reporting_period: str
    available_revenue_funds: float
    available_principal_funds: float
    senior_fees: float
    swap_payment: float
    class_a_balance: float
    class_a_rate_pct: float
    class_b_balance: float
    class_c_balance: float
    reserve_account_balance: float
    reserve_account_target: float
    class_a_pdl_balance: float
    class_b_pdl_balance: float
    days_in_period: int = 90
    # Sequential Pay Trigger state for this period. ``None`` (default) leaves the
    # senior-protective sequential stance (Class A redeems first) — unchanged
    # legacy behaviour. ``False`` selects pro-rata / pari-passu principal across
    # the redemption classes (the deal's healthy base case); ``True`` forces
    # sequential. This lets a caller drive the registered primitive to the same
    # allocation the platform's trigger engine computes for the period, so a
    # standalone ``waterfall_runner`` call agrees with the reconstructed ledger
    # instead of always paying Class A 100% of principal. (MODELING-GAPS B6.)
    sequential_pay: bool | None = None


class WaterfallOutput(BaseModel):
    """Output of the waterfall runner for one payment period.

    Attributes:
        reporting_period:       The period this output covers.
        revenue_waterfall:      Ordered list of revenue-priority steps (a)–(k).
        redemption_waterfall:   Ordered list of redemption-priority steps (a)–(d).
        tranche_distributions:  Per-tranche summary (Class A, B, C).
        total_distributed:      Sum of all ``amount_distributed`` across both
                                waterfalls.
        shortfall:              Sum of all ``shortfall`` values across both
                                waterfalls; 0.0 if all obligations were met.
    """

    reporting_period: str
    revenue_waterfall: list[WaterfallStep]
    redemption_waterfall: list[WaterfallStep]
    tranche_distributions: list[TrancheDistribution]
    total_distributed: float
    shortfall: float


# ---------------------------------------------------------------------------
# Green Lion 2026-1 builtin waterfall step lists (data, not control flow)
# ---------------------------------------------------------------------------
#
# The Green-Lion priority-of-payments is expressed here as DATA — an ordered
# list of ``StepSpec`` the generic interpreter executes. This is the same shape
# the extraction layer produces in ``DealModel.waterfalls[*].steps``, so the
# same interpreter runs an extracted deal model. Recipients not in the
# interpreter's need-calculator registry (operating fees, expense account,
# subordinated swap, new receivables, deferred purchase price) contribute need 0
# and are recorded ``not_evaluable`` — the audit trace stays structurally
# complete without inventing figures the input schema does not carry (matching
# the prior runner's "modelled as zero" steps).

_GREEN_LION_REVENUE_STEPS: list[StepSpec] = [
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
# which the runner computes via the sequential-pay branch
# (``allocate_principal``) and feeds back through ``need_overrides`` — so the
# pro-rata ↔ sequential choice (MODELING-GAPS.md A3) actually gates Class B's
# principal instead of Class A always taking 100%.
_GREEN_LION_REDEMPTION_STEPS: list[StepSpec] = [
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


def _funds_from_input(input: WaterfallInput) -> WaterfallFunds:
    """Build the interpreter's ``WaterfallFunds`` from a ``WaterfallInput``."""
    return WaterfallFunds(
        available_revenue_funds=input.available_revenue_funds,
        available_principal_funds=input.available_principal_funds,
        senior_fees=input.senior_fees,
        swap_payment=input.swap_payment,
        class_a_balance=input.class_a_balance,
        class_a_rate_pct=input.class_a_rate_pct,
        class_b_balance=input.class_b_balance,
        class_c_balance=input.class_c_balance,
        class_a_pdl_balance=input.class_a_pdl_balance,
        class_b_pdl_balance=input.class_b_pdl_balance,
        reserve_balance=input.reserve_account_balance,
        reserve_target=input.reserve_account_target,
        days_in_period=input.days_in_period,
        sequential_pay=input.sequential_pay,
    )


def _to_output_steps(execution: WaterfallExecution) -> list["WaterfallStep"]:
    """Map an interpreter ``WaterfallExecution`` to the public ``WaterfallStep``s.

    ``amount_available`` mirrors the prior runner's semantics: funds available at
    the *start* of the step (before its own deduction).
    """
    out: list[WaterfallStep] = []
    for s in execution.steps:
        out.append(
            WaterfallStep(
                priority=s.priority,
                recipient=s.recipient,
                amount_available=s.amount_available,
                amount_distributed=s.amount_distributed,
                shortfall=s.shortfall,
                condition=s.condition,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Primitive
# ---------------------------------------------------------------------------


@register_primitive(
    name="waterfall_runner",
    version="0.1.0",
    description="Execute RMBS payment waterfall against monthly collections",
    tags=["waterfall", "cashflow", "computation"],
)
class WaterfallRunner(Primitive[WaterfallInput, WaterfallOutput]):
    """Execute the Green Lion 2026-1 Revenue and Redemption waterfalls.

    Pure deterministic computation — no LLM calls. Confidence is always 1.0.

    The Green-Lion priority-of-payments is now expressed as *data* (the
    ``_GREEN_LION_*_STEPS`` ``StepSpec`` lists) executed by the generic
    ``waterfall_interpreter``; the same interpreter runs an extracted
    ``DealModel.waterfalls[*].steps``. Condition gating (and the sequential-pay
    branch) flow through an injectable ``ConditionEvaluator`` — pass one to the
    constructor to compose with S5's (#185) trigger engine; the default handles
    the Green-Lion conditions.
    """

    name = "waterfall_runner"
    version = "0.1.0"
    description = "Execute RMBS payment waterfall against monthly collections"

    def __init__(self, condition_evaluator: ConditionEvaluator | None = None) -> None:
        """Construct the runner, optionally with a custom condition evaluator.

        Parameters
        ----------
        condition_evaluator:
            The predicate used to gate conditional steps and to drive the
            sequential-pay branch. Defaults to ``None``, in which case the
            interpreter's ``DefaultConditionEvaluator`` is used. Inject S5's
            (#185) trigger engine here to compose the two without either side
            editing the other's internals.
        """
        self.condition_evaluator = condition_evaluator

    def execute(  # type: ignore[override]
        self, input: WaterfallInput
    ) -> PrimitiveResult[WaterfallOutput]:
        """Run both waterfalls and return per-tranche distributions.

        Parameters
        ----------
        input:
            Validated ``WaterfallInput`` for the reporting period.

        Returns
        -------
        PrimitiveResult[WaterfallOutput]
            Typed output with ``confidence=1.0``, prospectus citations, and an
            ``AuditEntry`` recording input hash and wall-clock duration.
        """
        t0 = time.perf_counter()
        input_hash = input.input_hash()

        # ------------------------------------------------------------------
        # 1. Build the interpreter's funds view + condition evaluator
        # ------------------------------------------------------------------

        funds = _funds_from_input(input)
        evaluator = self.condition_evaluator

        # ------------------------------------------------------------------
        # 2. Revenue waterfall (a) → (k) — driven by the generic interpreter
        # ------------------------------------------------------------------
        #
        # The residual step (k) Deferred Purchase Price absorbs whatever revenue
        # remains. It has no registry need-calculator (it is a residual, not a
        # fixed obligation), so we feed it an effectively-unbounded need override
        # — the interpreter caps every distribution at the remaining pot, so the
        # override makes the step sweep the residual without a phantom shortfall.

        rev_execution = interpret(
            _GREEN_LION_REVENUE_STEPS,
            funds,
            available=input.available_revenue_funds,
            evaluator=evaluator,
        )
        rev_steps = _to_output_steps(rev_execution)

        # ------------------------------------------------------------------
        # 3. Redemption waterfall (a) → (d) — sequential-pay branch (A3)
        # ------------------------------------------------------------------
        #
        # New receivables (a) is gated by the revolving-period condition (the
        # input carries no revolving flag, so the default evaluator suppresses
        # it). Principal steps (b)/(c) are allocated by the sequential-pay
        # branch: pro-rata ↔ sequential by the Sequential Pay Trigger — so Class
        # B can receive principal under pro-rata instead of Class A always
        # taking 100%. Step (d) sweeps the residual.

        # Green Lion's redemption waterfall repays only Class A and Class B from
        # principal (Class C is redeemed from revenue, step (j)); restrict the
        # sequential-pay allocation to the classes that actually have a
        # redemption step so the residual (step (d)) is correct.
        principal_alloc = allocate_principal(
            funds,
            available=input.available_principal_funds,
            classes=("class_a", "class_b"),
            evaluator=evaluator,
        )
        red_execution = interpret(
            _GREEN_LION_REDEMPTION_STEPS,
            funds,
            available=input.available_principal_funds,
            evaluator=evaluator,
            need_overrides={
                "class_a_principal": principal_alloc["class_a"],
                "class_b_principal": principal_alloc["class_b"],
            },
        )
        red_steps = _to_output_steps(red_execution)

        # ------------------------------------------------------------------
        # 4. Per-tranche distributions
        # ------------------------------------------------------------------

        class_a_interest_dist = rev_execution.distributed_to("class_a_interest")
        class_a_principal_dist = red_execution.distributed_to("class_a_principal")
        class_a_total = class_a_interest_dist + class_a_principal_dist

        class_b_interest_dist = rev_execution.distributed_to("class_b_interest")
        class_b_principal_dist = red_execution.distributed_to("class_b_principal")
        class_b_total = class_b_interest_dist + class_b_principal_dist

        # Class C — interest from revenue step (j) + any redemption principal.
        class_c_interest_dist = rev_execution.distributed_to(
            "class_c_principal_from_revenue"
        )
        class_c_principal_dist = red_execution.distributed_to("class_c_principal")
        class_c_total = class_c_interest_dist + class_c_principal_dist

        tranche_distributions = [
            TrancheDistribution(
                tranche="class_a",
                interest_received=class_a_interest_dist,
                principal_received=class_a_principal_dist,
                total_received=class_a_total,
                opening_balance=input.class_a_balance,
                closing_balance=max(0.0, input.class_a_balance - class_a_principal_dist),
            ),
            TrancheDistribution(
                tranche="class_b",
                interest_received=class_b_interest_dist,
                principal_received=class_b_principal_dist,
                total_received=class_b_total,
                opening_balance=input.class_b_balance,
                closing_balance=max(0.0, input.class_b_balance - class_b_principal_dist),
            ),
            TrancheDistribution(
                tranche="class_c",
                interest_received=class_c_interest_dist,
                principal_received=class_c_principal_dist,
                total_received=class_c_total,
                opening_balance=input.class_c_balance,
                closing_balance=max(0.0, input.class_c_balance - class_c_principal_dist),
            ),
        ]

        # ------------------------------------------------------------------
        # 5. Aggregate totals
        # ------------------------------------------------------------------

        all_steps = rev_steps + red_steps
        total_distributed = sum(s.amount_distributed for s in all_steps)
        total_shortfall = sum(s.shortfall for s in all_steps)

        # ------------------------------------------------------------------
        # 6. Citations — prospectus section 5.2
        # ------------------------------------------------------------------

        citations = [
            Citation(
                document="Green Lion 2026-1 B.V. Prospectus",
                page_or_row="section 5.2",
                excerpt=(
                    "Revenue Priority of Payments (steps a–k) and "
                    "Redemption Priority of Payments (steps a–d)"
                ),
            )
        ]

        # ------------------------------------------------------------------
        # 7. Audit entry
        # ------------------------------------------------------------------

        duration_ms = (time.perf_counter() - t0) * 1000.0
        audit = AuditEntry.now(
            primitive_name=self.name,
            version=self.version,
            input_hash=input_hash,
            duration_ms=duration_ms,
        )

        output = WaterfallOutput(
            reporting_period=input.reporting_period,
            revenue_waterfall=rev_steps,
            redemption_waterfall=red_steps,
            tranche_distributions=tranche_distributions,
            total_distributed=total_distributed,
            shortfall=total_shortfall,
        )

        return PrimitiveResult[WaterfallOutput](
            output=output,
            confidence=1.0,  # deterministic computation — no estimation
            citations=citations,
            audit_entry=audit,
        )
