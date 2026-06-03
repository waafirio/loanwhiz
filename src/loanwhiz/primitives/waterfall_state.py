"""Stateful multi-period waterfall runner — PDL and reserve tracking.

Extends the single-period ``WaterfallRunner`` with a ``WaterfallState``
object that carries forward:

- Principal Deficiency Ledger (PDL) debit balances per tranche (Class A,
  Class B) — increases when principal losses are recorded; reduced when the
  revenue waterfall's PDL replenishment steps (e) and (h) distribute funds.
- Reserve account balance — topped up by revenue step (f) and drawn when
  revenue is insufficient to meet senior obligations.
- Revolving period status — ``True`` until the deal's revolving period end
  date; controls whether new mortgage receivables can be purchased in the
  redemption waterfall step (a).
- Cumulative loss tracking — total principal losses and loss rate as a
  percentage of the original pool balance.

``MultiPeriodWaterfallRunner`` orchestrates a sequence of single-period
``WaterfallRunner`` executions, threading state forward after each period.

Green Lion 2026-1 context:
- Original pool balance: €1,063,600,000 (from the deal's initial pool).
- Three monthly periods: February, March, April 2026.
- Under normal conditions (clean pool) no PDL debit is expected.
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
from loanwhiz.primitives.waterfall_runner import (
    WaterfallInput,
    WaterfallOutput,
    WaterfallRunner,
)

# ---------------------------------------------------------------------------
# WaterfallState — persistent state carried across periods
# ---------------------------------------------------------------------------


class WaterfallState(BaseModel):
    """Persistent state carried across monthly waterfall executions.

    All monetary fields are in the deal currency (EUR for Green Lion 2026-1).

    Attributes:
        class_a_pdl_balance:        Debit balance on the Class A Principal
                                    Deficiency Ledger.  Increases when a
                                    principal loss is allocated to Class A;
                                    decreases when revenue waterfall step (e)
                                    replenishes it.  0.0 = no outstanding
                                    deficiency.
        class_b_pdl_balance:        Debit balance on the Class B PDL.
                                    Replenished by revenue step (h).
        reserve_account_balance:    Current cash balance in the reserve
                                    account.  Topped up by revenue step (f);
                                    drawn when senior obligations cannot be
                                    met from collections alone.
        revolving_period_active:    ``True`` while the deal's revolving period
                                    is active (new mortgage receivables may be
                                    purchased from principal collections —
                                    redemption waterfall step (a)).  ``False``
                                    after the revolving period end date.
        cumulative_principal_losses: Running total of principal losses recorded
                                    across all periods.
        cumulative_loss_rate_pct:   ``cumulative_principal_losses /
                                    original_pool_balance * 100``.  Updated
                                    whenever ``record_loss`` is called.
        original_pool_balance:      Initial pool balance at deal closing —
                                    the denominator for the loss rate.
                                    Defaults to Green Lion 2026-1's closing
                                    balance of €1,063,600,000.
    """

    class_a_pdl_balance: float = 0.0
    class_b_pdl_balance: float = 0.0
    reserve_account_balance: float = 0.0
    revolving_period_active: bool = True
    cumulative_principal_losses: float = 0.0
    cumulative_loss_rate_pct: float = 0.0
    original_pool_balance: float = 1_063_600_000.0

    # ------------------------------------------------------------------
    # Mutation helpers — return a new WaterfallState (immutable pattern)
    # ------------------------------------------------------------------

    def record_loss(
        self, loss_amount: float, tranche: str = "class_a"
    ) -> "WaterfallState":
        """Record a principal loss against the specified tranche's PDL.

        Increases the PDL debit balance for *tranche* and updates the
        cumulative loss fields.  Does not mutate ``self`` — returns a new
        ``WaterfallState``.

        Parameters
        ----------
        loss_amount:
            The EUR amount of principal lost.  Must be >= 0.  A zero-amount
            call is a no-op (returns a copy identical to ``self``).
        tranche:
            ``"class_a"`` (default) or ``"class_b"``.  Losses flow
            senior→junior under the deal's loss allocation rules; Class A
            losses are rare in investment-grade pools.

        Returns
        -------
        WaterfallState
            New state with updated PDL and cumulative loss fields.

        Raises
        ------
        ValueError
            If *tranche* is not ``"class_a"`` or ``"class_b"``.
        """
        if loss_amount < 0:
            loss_amount = 0.0
        if tranche not in ("class_a", "class_b"):
            raise ValueError(
                f"tranche must be 'class_a' or 'class_b'; got {tranche!r}"
            )

        new_a_pdl = self.class_a_pdl_balance
        new_b_pdl = self.class_b_pdl_balance
        if tranche == "class_a":
            new_a_pdl += loss_amount
        else:
            new_b_pdl += loss_amount

        new_cumulative = self.cumulative_principal_losses + loss_amount
        new_loss_rate = (
            new_cumulative / self.original_pool_balance * 100.0
            if self.original_pool_balance > 0
            else 0.0
        )

        return self.model_copy(
            update={
                "class_a_pdl_balance": new_a_pdl,
                "class_b_pdl_balance": new_b_pdl,
                "cumulative_principal_losses": new_cumulative,
                "cumulative_loss_rate_pct": new_loss_rate,
            }
        )

    def replenish_pdl(
        self, tranche: str, amount: float
    ) -> tuple["WaterfallState", float]:
        """Apply a PDL replenishment payment to the specified tranche.

        The payment is capped at the outstanding PDL debit balance — you
        cannot replenish more than is owed.

        Parameters
        ----------
        tranche:
            ``"class_a"`` or ``"class_b"``.
        amount:
            EUR amount offered for replenishment.  Must be >= 0.

        Returns
        -------
        tuple[WaterfallState, float]
            ``(new_state, amount_actually_applied)`` where
            ``amount_actually_applied <= amount`` and
            ``amount_actually_applied <= outstanding_pdl_balance``.

        Raises
        ------
        ValueError
            If *tranche* is not ``"class_a"`` or ``"class_b"``.
        """
        if amount < 0:
            amount = 0.0
        if tranche not in ("class_a", "class_b"):
            raise ValueError(
                f"tranche must be 'class_a' or 'class_b'; got {tranche!r}"
            )

        outstanding = (
            self.class_a_pdl_balance
            if tranche == "class_a"
            else self.class_b_pdl_balance
        )
        applied = min(amount, outstanding)

        if tranche == "class_a":
            new_state = self.model_copy(
                update={"class_a_pdl_balance": self.class_a_pdl_balance - applied}
            )
        else:
            new_state = self.model_copy(
                update={"class_b_pdl_balance": self.class_b_pdl_balance - applied}
            )

        return new_state, applied

    def update_reserve(
        self, payment: float, withdrawal: float = 0.0
    ) -> "WaterfallState":
        """Update the reserve account after a period's waterfall.

        The reserve balance is increased by *payment* (the amount distributed
        to the reserve by revenue step (f)) and reduced by *withdrawal* (the
        amount the reserve contributed to cover a revenue shortfall).  The
        balance is floored at 0.0.

        Parameters
        ----------
        payment:
            EUR amount added to the reserve (revenue step (f) distribution).
        withdrawal:
            EUR amount drawn from the reserve to cover a shortfall.

        Returns
        -------
        WaterfallState
            New state with updated ``reserve_account_balance``.
        """
        new_balance = max(
            0.0, self.reserve_account_balance + max(0.0, payment) - max(0.0, withdrawal)
        )
        return self.model_copy(update={"reserve_account_balance": new_balance})


# ---------------------------------------------------------------------------
# Multi-period input / output models
# ---------------------------------------------------------------------------


class MultiPeriodWaterfallInput(BaseInput):
    """Input schema for the multi-period waterfall runner.

    Attributes:
        periods:        Ordered list of single-period waterfall inputs, oldest
                        first (e.g. [February, March, April]).  The
                        ``class_a_pdl_balance``, ``class_b_pdl_balance``, and
                        ``reserve_account_balance`` fields on each period input
                        are **overridden** at execution time with values from
                        the carry-forward state — callers may leave them at
                        their defaults or supply them as documentation; the
                        runner ignores them in favour of the live state.
        initial_state:  The starting ``WaterfallState`` before any period runs.
                        Defaults to a clean state (zero PDL, zero reserve, …).
    """

    periods: list[WaterfallInput]
    initial_state: WaterfallState = WaterfallState()


class MultiPeriodWaterfallOutput(BaseModel):
    """Output of the multi-period waterfall runner.

    Attributes:
        period_results:          Ordered ``WaterfallOutput`` for each input
                                 period, in the same order as
                                 ``MultiPeriodWaterfallInput.periods``.
        final_state:             The ``WaterfallState`` after the last period
                                 has run (carry-forward state for the next
                                 batch of periods, if any).
        cumulative_distributions: Total EUR distributed to each recipient
                                 across all periods, keyed by recipient name
                                 (matching ``WaterfallStep.recipient``).
        state_trajectory:        The ``WaterfallState`` snapshot after each
                                 period.  Length equals ``len(periods)``.
    """

    period_results: list[WaterfallOutput]
    final_state: WaterfallState
    cumulative_distributions: dict[str, float]
    state_trajectory: list[WaterfallState]


# ---------------------------------------------------------------------------
# MultiPeriodWaterfallRunner primitive
# ---------------------------------------------------------------------------


@register_primitive(
    name="multi_period_waterfall_runner",
    version="0.1.0",
    description=(
        "Execute waterfall across multiple periods with persistent PDL/reserve state"
    ),
    tags=["waterfall", "cashflow", "stateful", "computation"],
)
class MultiPeriodWaterfallRunner(
    Primitive[MultiPeriodWaterfallInput, MultiPeriodWaterfallOutput]
):
    """Execute the Green Lion waterfall across multiple monthly periods.

    Delegates each period to the existing single-period ``WaterfallRunner``,
    then threads the resulting PDL replenishment amounts and reserve account
    change forward into the next period's ``WaterfallState``.

    State threading logic per period
    ---------------------------------
    1. Override the period's ``class_a_pdl_balance``, ``class_b_pdl_balance``,
       and ``reserve_account_balance`` with the carry-forward state values.
    2. Run ``WaterfallRunner.execute()``.
    3. Extract ``class_a_pdl_replenishment`` (step e) and
       ``class_b_pdl_replenishment`` (step h) distributed amounts from the
       revenue waterfall.  Apply them via ``WaterfallState.replenish_pdl()``.
    4. Extract ``reserve_account_replenishment`` (step f) distributed amount.
       Update the reserve via ``WaterfallState.update_reserve(payment=...)``.
    5. Append the updated state to ``state_trajectory``.

    Confidence is always 1.0 — this is a pure deterministic computation.
    """

    name = "multi_period_waterfall_runner"
    version = "0.1.0"
    description = (
        "Execute waterfall across multiple periods with persistent PDL/reserve state"
    )

    def execute(  # type: ignore[override]
        self, input: MultiPeriodWaterfallInput
    ) -> PrimitiveResult[MultiPeriodWaterfallOutput]:
        """Run all periods sequentially, threading PDL and reserve state forward.

        Parameters
        ----------
        input:
            Validated ``MultiPeriodWaterfallInput`` with an ordered period list
            and an optional initial state.

        Returns
        -------
        PrimitiveResult[MultiPeriodWaterfallOutput]
            Typed output with ``confidence=1.0``, prospectus citations, and an
            ``AuditEntry`` recording input hash and wall-clock duration.
        """
        t0 = time.perf_counter()
        input_hash = input.input_hash()

        state = input.initial_state
        period_results: list[WaterfallOutput] = []
        state_trajectory: list[WaterfallState] = []
        cumulative: dict[str, float] = {}

        runner = WaterfallRunner()

        for period_input in input.periods:
            # Override PDL and reserve balances with live carry-forward state.
            # model_copy respects the frozen=True config on BaseInput by
            # passing update= which creates a new instance.
            period_input_with_state = period_input.model_copy(
                update={
                    "class_a_pdl_balance": state.class_a_pdl_balance,
                    "class_b_pdl_balance": state.class_b_pdl_balance,
                    "reserve_account_balance": state.reserve_account_balance,
                }
            )

            result = runner.execute(period_input_with_state)
            period_output = result.output
            period_results.append(period_output)

            # Accumulate cumulative distributions across both waterfalls.
            for step in period_output.revenue_waterfall + period_output.redemption_waterfall:
                cumulative[step.recipient] = (
                    cumulative.get(step.recipient, 0.0) + step.amount_distributed
                )

            # Thread PDL replenishments into state.
            a_replenish = _step_distributed(period_output, "class_a_pdl_replenishment")
            state, _ = state.replenish_pdl("class_a", a_replenish)

            b_replenish = _step_distributed(period_output, "class_b_pdl_replenishment")
            state, _ = state.replenish_pdl("class_b", b_replenish)

            # Thread reserve account replenishment into state.
            reserve_payment = _step_distributed(
                period_output, "reserve_account_replenishment"
            )
            state = state.update_reserve(payment=reserve_payment)

            state_trajectory.append(state)

        duration_ms = (time.perf_counter() - t0) * 1000.0
        audit = AuditEntry.now(
            primitive_name=self.name,
            version=self.version,
            input_hash=input_hash,
            duration_ms=duration_ms,
        )

        citations = [
            Citation(
                document="Green Lion 2026-1 B.V. Prospectus",
                page_or_row="section 5.2",
                excerpt=(
                    "Revenue Priority of Payments (steps a–k) and "
                    "Redemption Priority of Payments (steps a–d); "
                    "PDL replenishment steps (e) Class A and (h) Class B."
                ),
            )
        ]

        output = MultiPeriodWaterfallOutput(
            period_results=period_results,
            final_state=state,
            cumulative_distributions=cumulative,
            state_trajectory=state_trajectory,
        )

        return PrimitiveResult[MultiPeriodWaterfallOutput](
            output=output,
            confidence=1.0,
            citations=citations,
            audit_entry=audit,
        )


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _step_distributed(output: WaterfallOutput, recipient: str) -> float:
    """Return the ``amount_distributed`` for *recipient* across both waterfalls."""
    for step in output.revenue_waterfall + output.redemption_waterfall:
        if step.recipient == recipient:
            return step.amount_distributed
    return 0.0
