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
    """

    name = "waterfall_runner"
    version = "0.1.0"
    description = "Execute RMBS payment waterfall against monthly collections"

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
        # 1. Derived amounts
        # ------------------------------------------------------------------

        # Class A quarterly interest: balance * (rate / 100) / 360 * days
        # Act/360 day-count convention (prospectus section 5.2 implicit).
        class_a_interest = (
            input.class_a_balance
            * (input.class_a_rate_pct / 100.0)
            / 360.0
            * input.days_in_period
        )

        # Reserve shortfall (how much is needed to top up the reserve account).
        reserve_shortfall = max(
            0.0, input.reserve_account_target - input.reserve_account_balance
        )

        # ------------------------------------------------------------------
        # 2. Revenue waterfall (a) → (k)
        # ------------------------------------------------------------------

        remaining_rev = input.available_revenue_funds
        rev_steps: list[WaterfallStep] = []

        def _rev_step(
            priority: str,
            recipient: str,
            need: float,
            condition: str | None = None,
        ) -> WaterfallStep:
            nonlocal remaining_rev
            distributed = min(need, remaining_rev)
            shortfall = max(0.0, need - distributed)
            remaining_rev -= distributed
            return WaterfallStep(
                priority=priority,
                recipient=recipient,
                amount_available=remaining_rev + distributed,  # available before deduction
                amount_distributed=distributed,
                shortfall=shortfall,
                condition=condition,
            )

        # (a) Senior fees
        rev_steps.append(_rev_step("(a)", "senior_fees", input.senior_fees))

        # (b) Operating fees — modelled as a pari-passu bundle; the input
        #     carries no separate operating-fee field, so this step distributes
        #     zero unless a future input field is added.  The step is kept in
        #     the sequence so the audit trace is structurally complete.
        rev_steps.append(
            _rev_step(
                "(b)",
                "operating_fees",
                0.0,
                condition="pari passu: servicer, administrator, paying agent",
            )
        )

        # (c) Swap payments (non-subordinated)
        rev_steps.append(_rev_step("(c)", "swap_payment", input.swap_payment))

        # (d) Class A interest
        rev_steps.append(_rev_step("(d)", "class_a_interest", class_a_interest))

        # (e) Class A PDL replenishment
        rev_steps.append(
            _rev_step("(e)", "class_a_pdl_replenishment", input.class_a_pdl_balance)
        )

        # (f) Reserve Account replenishment
        rev_steps.append(
            _rev_step("(f)", "reserve_account_replenishment", reserve_shortfall)
        )

        # (g) Expense Account replenishment — modelled as zero (no separate
        #     expense-account shortfall in the input schema).
        rev_steps.append(
            _rev_step("(g)", "expense_account_replenishment", 0.0)
        )

        # (h) Class B PDL replenishment
        rev_steps.append(
            _rev_step("(h)", "class_b_pdl_replenishment", input.class_b_pdl_balance)
        )

        # (i) Subordinated swap payments — modelled as zero (no subordinated
        #     swap field in the input schema).
        rev_steps.append(
            _rev_step(
                "(i)",
                "subordinated_swap_payment",
                0.0,
                condition="subordinated swap",
            )
        )

        # (j) Class C principal — only from First Optional Redemption Date.
        #     The input does not carry the redemption-date flag; the condition
        #     is captured in the audit trace.  In this model the step distributes
        #     from remaining revenue (not the principal waterfall) as specified.
        rev_steps.append(
            _rev_step(
                "(j)",
                "class_c_principal_from_revenue",
                0.0,
                condition="from First Optional Redemption Date",
            )
        )

        # (k) Deferred Purchase Price to Seller — residual revenue
        rev_steps.append(
            _rev_step("(k)", "deferred_purchase_price_seller", remaining_rev)
        )

        # ------------------------------------------------------------------
        # 3. Redemption waterfall (a) → (d)
        # ------------------------------------------------------------------

        remaining_prin = input.available_principal_funds
        red_steps: list[WaterfallStep] = []

        def _red_step(
            priority: str,
            recipient: str,
            need: float,
            condition: str | None = None,
        ) -> WaterfallStep:
            nonlocal remaining_prin
            distributed = min(need, remaining_prin)
            shortfall = max(0.0, need - distributed)
            remaining_prin -= distributed
            return WaterfallStep(
                priority=priority,
                recipient=recipient,
                amount_available=remaining_prin + distributed,
                amount_distributed=distributed,
                shortfall=shortfall,
                condition=condition,
            )

        # (a) New Mortgage Receivables during Revolving Period — modelled as
        #     zero (the input does not carry a revolving-period flag or a new-
        #     receivables purchase amount).
        red_steps.append(
            _red_step(
                "(a)",
                "new_mortgage_receivables",
                0.0,
                condition="during Revolving Period",
            )
        )

        # (b) Class A principal — pari passu.
        # The redemption waterfall distributes available principal collections
        # in priority order; it does not attempt to repay the full outstanding
        # balance in a single period.  Class A absorbs all remaining principal
        # after step (a); there is no "need" shortfall — whatever arrives is
        # distributed to Class A first (they are senior).
        class_a_principal_need = remaining_prin  # senior class takes all remaining
        red_steps.append(
            _red_step(
                "(b)",
                "class_a_principal",
                class_a_principal_need,
                condition="pari passu",
            )
        )

        # (c) Class B principal — pari passu.
        # After Class A absorbs available principal, Class B takes whatever
        # remains (typically zero in a sequential-pay structure during normal
        # amortisation).  The step records zero distributed with zero shortfall.
        class_b_principal_need = remaining_prin  # what's left after Class A
        red_steps.append(
            _red_step(
                "(c)",
                "class_b_principal",
                class_b_principal_need,
                condition="pari passu",
            )
        )

        # (d) Deferred Purchase Price to Seller — residual principal
        red_steps.append(
            _red_step("(d)", "deferred_purchase_price_seller_principal", remaining_prin)
        )

        # ------------------------------------------------------------------
        # 4. Per-tranche distributions
        # ------------------------------------------------------------------

        # Revenue step look-ups
        def _rev_amount(recipient: str) -> float:
            for step in rev_steps:
                if step.recipient == recipient:
                    return step.amount_distributed
            return 0.0

        def _red_amount(recipient: str) -> float:
            for step in red_steps:
                if step.recipient == recipient:
                    return step.amount_distributed
            return 0.0

        class_a_interest_dist = _rev_amount("class_a_interest")
        class_a_principal_dist = _red_amount("class_a_principal")
        class_a_total = class_a_interest_dist + class_a_principal_dist

        class_b_interest_dist = 0.0  # Class B receives no revenue interest in this model
        class_b_principal_dist = _red_amount("class_b_principal")
        class_b_total = class_b_interest_dist + class_b_principal_dist

        # Class C — interest from revenue step (j) + no redemption-waterfall entry
        class_c_interest_dist = _rev_amount("class_c_principal_from_revenue")
        class_c_principal_dist = 0.0
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
