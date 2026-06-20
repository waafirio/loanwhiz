"""Waterfall runner MCP tool — a thin single-period wrapper over ``run_period``.

This module exists only to **preserve the registered ``waterfall_runner`` MCP
tool surface**. The standalone single-period ``WaterfallRunner`` execution path
(and its sibling duplicate engines ``CashflowProjector`` /
``MultiPeriodWaterfallRunner``) were deleted in #276 — there is now exactly one
engine, ``period_state_machine.run_period``. To keep the MCP tool that consumers
call (`waterfall_runner`, reachability ``live``) working with the **same
request/response contract** it had before, the primitive is retained here as a
thin wrapper: it accepts the flat ``WaterfallInput``, builds a single-period
``DealState`` seed + ``PeriodInput``, folds one period through ``run_period``,
and maps the resulting per-period ``WaterfallExecution`` traces back into the
unchanged ``WaterfallOutput`` shape. (Design doc: *"the registered
``waterfall_runner`` MCP primitive becomes a thin single-period wrapper over
``run_period`` (preserving the MCP tool surface)"*.)

The two modelled waterfalls (Green Lion 2026-1 Priority of Payments) are exactly
the kernel's canonical ``DEFAULT_REVENUE_STEPS`` / ``DEFAULT_REDEMPTION_STEPS``
(now defined in ``period_state_machine``), so the figures this tool returns are
the *one engine's* figures — there is no second waterfall implementation.

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
from loanwhiz.primitives.deal_state import DealState, PeriodCollections
from loanwhiz.primitives.period_state_machine import (
    DEFAULT_REDEMPTION_STEPS,
    DEFAULT_REVENUE_STEPS,
    PeriodInput,
    run_period,
)
from loanwhiz.primitives.registry import register_primitive
from loanwhiz.primitives.waterfall_interpreter import (
    ConditionEvaluator,
    WaterfallExecution,
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
# Input / Output models  (the MCP tool's public contract — unchanged)
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
# Mapping helpers: kernel WaterfallExecution → public WaterfallOutput shape
# ---------------------------------------------------------------------------


def _to_output_steps(execution: WaterfallExecution) -> list[WaterfallStep]:
    """Map a kernel ``WaterfallExecution`` to the public ``WaterfallStep``s.

    ``amount_available`` mirrors the prior tool's semantics: funds available at
    the *start* of the step (before its own deduction).
    """
    return [
        WaterfallStep(
            priority=s.priority,
            recipient=s.recipient,
            amount_available=s.amount_available,
            amount_distributed=s.amount_distributed,
            shortfall=s.shortfall,
            condition=s.condition,
        )
        for s in execution.steps
    ]


# ---------------------------------------------------------------------------
# Primitive — the preserved MCP tool, now a thin run_period wrapper
# ---------------------------------------------------------------------------


@register_primitive(
    name="waterfall_runner",
    version="0.1.0",
    description="Execute RMBS payment waterfall against monthly collections",
    tags=["waterfall", "cashflow", "computation"],
)
class WaterfallRunner(Primitive[WaterfallInput, WaterfallOutput]):
    """Execute one period of the Green Lion 2026-1 Revenue + Redemption waterfalls.

    Pure deterministic computation — no LLM calls. Confidence is always 1.0.

    This primitive is the **MCP tool surface** for the deal engine. It does not
    contain its own waterfall logic: it seeds a single-period ``DealState`` from
    the flat ``WaterfallInput``, folds one period through the canonical
    ``period_state_machine.run_period`` kernel (with the kernel's
    ``DEFAULT_*_STEPS``), and maps the kernel's execution traces back into the
    unchanged ``WaterfallOutput``. Condition gating + the sequential-pay branch
    are owned by ``run_period`` and its trigger engine.
    """

    name = "waterfall_runner"
    version = "0.1.0"
    description = "Execute RMBS payment waterfall against monthly collections"

    def __init__(self, condition_evaluator: ConditionEvaluator | None = None) -> None:
        """Construct the runner.

        Parameters
        ----------
        condition_evaluator:
            Retained for backward compatibility with the prior tool's
            constructor signature. ``run_period`` derives its condition
            evaluator from the live trigger engine over the opening state, so a
            caller-supplied evaluator is not threaded through; the parameter is
            accepted (and ignored) so existing call sites do not break.
        """
        self.condition_evaluator = condition_evaluator

    def execute(  # type: ignore[override]
        self, input: WaterfallInput
    ) -> PrimitiveResult[WaterfallOutput]:
        """Fold one period through ``run_period`` and return tranche distributions.

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
        # 1. Seed a single-period opening DealState from the flat input.
        # ------------------------------------------------------------------
        #
        # The flat WaterfallInput carries only the figures the waterfall needs;
        # the kernel works over a DealState. ``original_pool_balance`` /
        # ``pool_balance`` are not inputs to this single-period snapshot tool, so
        # seed the pool from the total note balance (a structurally consistent
        # placeholder — the pool-factor / loss-rate readers are not consumed by
        # this tool's output, which only reports the waterfall lines).
        total_notes = (
            input.class_a_balance + input.class_b_balance + input.class_c_balance
        )
        opening = DealState(
            reporting_date=_seed_date(input.reporting_period),
            class_a_balance=input.class_a_balance,
            class_b_balance=input.class_b_balance,
            class_c_balance=input.class_c_balance,
            class_a_pdl=input.class_a_pdl_balance,
            class_b_pdl=input.class_b_pdl_balance,
            class_c_pdl=0.0,
            reserve_balance=input.reserve_account_balance,
            reserve_target=input.reserve_account_target,
            pool_balance=total_notes,
            original_pool_balance=total_notes if total_notes > 0 else 1.0,
        )

        # ------------------------------------------------------------------
        # 2. Build the period inputs. The tool supplies aggregate available
        #    funds directly (the per-leg breakdown is not in the schema): route
        #    revenue through ``interest`` and principal through
        #    ``scheduled_principal`` so the kernel's funds view derives the same
        #    two pots the flat input carries.
        # ------------------------------------------------------------------
        period = PeriodInput(
            reporting_date=_seed_date(input.reporting_period),
            collections=PeriodCollections(
                interest=max(0.0, input.available_revenue_funds),
                scheduled_principal=max(0.0, input.available_principal_funds),
            ),
            days_in_period=input.days_in_period,
        )

        # ------------------------------------------------------------------
        # 3. Fold one period through the single canonical engine.
        # ------------------------------------------------------------------
        result = run_period(
            opening,
            period,
            rates={"class_a_rate_pct": input.class_a_rate_pct},
            revenue_steps=DEFAULT_REVENUE_STEPS,
            redemption_steps=DEFAULT_REDEMPTION_STEPS,
            principal_classes=("class_a", "class_b"),
            senior_fees=input.senior_fees,
            swap_payment=input.swap_payment,
        )

        rev_execution = result.revenue_execution
        red_execution = result.redemption_execution
        rev_steps = _to_output_steps(rev_execution)
        red_steps = _to_output_steps(red_execution)

        # ------------------------------------------------------------------
        # 4. Per-tranche distributions (read from the kernel's executions).
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
                closing_balance=max(
                    0.0, input.class_a_balance - class_a_principal_dist
                ),
            ),
            TrancheDistribution(
                tranche="class_b",
                interest_received=class_b_interest_dist,
                principal_received=class_b_principal_dist,
                total_received=class_b_total,
                opening_balance=input.class_b_balance,
                closing_balance=max(
                    0.0, input.class_b_balance - class_b_principal_dist
                ),
            ),
            TrancheDistribution(
                tranche="class_c",
                interest_received=class_c_interest_dist,
                principal_received=class_c_principal_dist,
                total_received=class_c_total,
                opening_balance=input.class_c_balance,
                closing_balance=max(
                    0.0, input.class_c_balance - class_c_principal_dist
                ),
            ),
        ]

        # ------------------------------------------------------------------
        # 5. Aggregate totals.
        # ------------------------------------------------------------------
        all_steps = rev_steps + red_steps
        total_distributed = sum(s.amount_distributed for s in all_steps)
        total_shortfall = sum(s.shortfall for s in all_steps)

        # ------------------------------------------------------------------
        # 6. Citations — prospectus section 5.2.
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
        # 7. Audit entry.
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


def _seed_date(reporting_period: str) -> str:
    """Return a non-empty ISO-ish date string for the seed ``DealState``.

    ``DealState.reporting_date`` only requires a non-empty string; the
    single-period tool's output echoes the human-readable ``reporting_period``
    back to the caller, so the exact value threaded into the state is immaterial
    to the tool's contract. Fall back to a sentinel when the caller passes an
    empty period label.
    """
    return reporting_period.strip() or "period"
