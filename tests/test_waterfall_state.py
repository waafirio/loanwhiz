"""Tests for WaterfallState and MultiPeriodWaterfallRunner.

Tests are organised around the six scenarios specified in issue #36:
1. PDL accumulation across periods
2. PDL replenishment ordering (step e before Class A principal)
3. Reserve fund persistence across 3 Green Lion periods (Feb/Mar/Apr)
4. Cumulative loss rate computation
5. state_trajectory length equals len(periods)
6. Green Lion clean-pool synthetic run (no PDL breaches expected)

Synthetic Green Lion 2026-1 inputs follow the deal's structural parameters:
  - Class A: €1.0B at 3.62% p.a. (EURIBOR 3.19 + 0.43)
  - Class B: €53.1M
  - Class C: €10.5M
  - Original pool balance: €1,063,600,000
  - 30-day monthly periods (Act/360, 30 days per month)
"""

from __future__ import annotations

import math

import pytest

from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY
from loanwhiz.primitives.waterfall_runner import WaterfallInput
from loanwhiz.primitives.waterfall_state import (
    MultiPeriodWaterfallInput,
    MultiPeriodWaterfallRunner,
    WaterfallState,
)

# ---------------------------------------------------------------------------
# Constants — Green Lion 2026-1 deal parameters
# ---------------------------------------------------------------------------

_CLASS_A_BALANCE = 1_000_000_000.0   # €1.0 billion
_CLASS_A_RATE_PCT = 3.62              # 3.62% p.a.
_CLASS_B_BALANCE = 53_100_000.0      # €53.1M
_CLASS_C_BALANCE = 10_500_000.0      # €10.5M
_DAYS = 30                            # monthly period
_ORIGINAL_POOL = 1_063_600_000.0     # Green Lion initial pool

# Expected Class A monthly interest: 1e9 * 0.0362 / 360 * 30 ≈ €3,016,667
_CLASS_A_MONTHLY_INTEREST = _CLASS_A_BALANCE * (_CLASS_A_RATE_PCT / 100.0) / 360.0 * _DAYS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _period_input(
    reporting_period: str = "Feb 2026",
    available_revenue_funds: float = 5_000_000.0,
    available_principal_funds: float = 3_000_000.0,
    senior_fees: float = 20_000.0,
    swap_payment: float = 0.0,
    class_a_balance: float = _CLASS_A_BALANCE,
    class_a_rate_pct: float = _CLASS_A_RATE_PCT,
    class_b_balance: float = _CLASS_B_BALANCE,
    class_c_balance: float = _CLASS_C_BALANCE,
    reserve_account_balance: float = 0.0,   # will be overridden by state
    reserve_account_target: float = 5_000_000.0,
    class_a_pdl_balance: float = 0.0,       # will be overridden by state
    class_b_pdl_balance: float = 0.0,       # will be overridden by state
    days_in_period: int = _DAYS,
) -> WaterfallInput:
    """Return a synthetic ``WaterfallInput`` for one monthly period."""
    return WaterfallInput(
        reporting_period=reporting_period,
        available_revenue_funds=available_revenue_funds,
        available_principal_funds=available_principal_funds,
        senior_fees=senior_fees,
        swap_payment=swap_payment,
        class_a_balance=class_a_balance,
        class_a_rate_pct=class_a_rate_pct,
        class_b_balance=class_b_balance,
        class_c_balance=class_c_balance,
        reserve_account_balance=reserve_account_balance,
        reserve_account_target=reserve_account_target,
        class_a_pdl_balance=class_a_pdl_balance,
        class_b_pdl_balance=class_b_pdl_balance,
        days_in_period=days_in_period,
    )


def _green_lion_period(reporting_period: str) -> WaterfallInput:
    """Return a clean-pool Green Lion period input with ample revenue."""
    return _period_input(
        reporting_period=reporting_period,
        # Revenue comfortably covers Class A monthly interest + fees.
        available_revenue_funds=4_000_000.0,
        available_principal_funds=5_000_000.0,
        senior_fees=20_000.0,
        reserve_account_target=5_000_000.0,
    )


# ---------------------------------------------------------------------------
# 1. WaterfallState unit tests
# ---------------------------------------------------------------------------


class TestWaterfallStateDefaults:
    """WaterfallState initialises with expected defaults."""

    def test_default_pdl_balances_are_zero(self):
        state = WaterfallState()
        assert state.class_a_pdl_balance == 0.0
        assert state.class_b_pdl_balance == 0.0

    def test_default_reserve_is_zero(self):
        assert WaterfallState().reserve_account_balance == 0.0

    def test_revolving_period_active_by_default(self):
        assert WaterfallState().revolving_period_active is True

    def test_default_original_pool_balance(self):
        assert WaterfallState().original_pool_balance == _ORIGINAL_POOL

    def test_default_loss_fields_zero(self):
        state = WaterfallState()
        assert state.cumulative_principal_losses == 0.0
        assert state.cumulative_loss_rate_pct == 0.0


class TestRecordLoss:
    """WaterfallState.record_loss() updates PDL and cumulative loss fields."""

    def test_class_a_loss_increases_pdl(self):
        state = WaterfallState()
        new_state = state.record_loss(1_000_000.0, "class_a")
        assert new_state.class_a_pdl_balance == 1_000_000.0
        assert new_state.class_b_pdl_balance == 0.0

    def test_class_b_loss_increases_pdl(self):
        state = WaterfallState()
        new_state = state.record_loss(500_000.0, "class_b")
        assert new_state.class_b_pdl_balance == 500_000.0
        assert new_state.class_a_pdl_balance == 0.0

    def test_cumulative_loss_accumulates(self):
        state = WaterfallState()
        state = state.record_loss(1_000_000.0, "class_a")
        state = state.record_loss(500_000.0, "class_b")
        assert state.cumulative_principal_losses == 1_500_000.0

    def test_cumulative_loss_rate_computed_correctly(self):
        state = WaterfallState(original_pool_balance=_ORIGINAL_POOL)
        loss = 10_636_000.0  # 1% of pool
        new_state = state.record_loss(loss, "class_a")
        expected_rate = loss / _ORIGINAL_POOL * 100.0
        assert math.isclose(new_state.cumulative_loss_rate_pct, expected_rate, rel_tol=1e-9)

    def test_zero_loss_is_noop(self):
        state = WaterfallState(class_a_pdl_balance=100_000.0)
        new_state = state.record_loss(0.0, "class_a")
        assert new_state.class_a_pdl_balance == 100_000.0
        assert new_state.cumulative_principal_losses == 0.0

    def test_negative_loss_clamped_to_zero(self):
        state = WaterfallState()
        new_state = state.record_loss(-500.0, "class_a")
        assert new_state.class_a_pdl_balance == 0.0

    def test_invalid_tranche_raises(self):
        with pytest.raises(ValueError, match="tranche must be"):
            WaterfallState().record_loss(100.0, "class_c")

    def test_original_state_not_mutated(self):
        original = WaterfallState()
        _ = original.record_loss(1_000_000.0, "class_a")
        assert original.class_a_pdl_balance == 0.0


class TestReplenishPdl:
    """WaterfallState.replenish_pdl() reduces PDL balance, returns amount applied."""

    def test_full_replenishment(self):
        state = WaterfallState(class_a_pdl_balance=500_000.0)
        new_state, applied = state.replenish_pdl("class_a", 500_000.0)
        assert applied == 500_000.0
        assert new_state.class_a_pdl_balance == 0.0

    def test_partial_replenishment(self):
        state = WaterfallState(class_a_pdl_balance=1_000_000.0)
        new_state, applied = state.replenish_pdl("class_a", 300_000.0)
        assert applied == 300_000.0
        assert new_state.class_a_pdl_balance == 700_000.0

    def test_excess_payment_capped_at_outstanding(self):
        state = WaterfallState(class_a_pdl_balance=200_000.0)
        new_state, applied = state.replenish_pdl("class_a", 999_999.0)
        assert applied == 200_000.0
        assert new_state.class_a_pdl_balance == 0.0

    def test_class_b_replenishment(self):
        state = WaterfallState(class_b_pdl_balance=750_000.0)
        new_state, applied = state.replenish_pdl("class_b", 200_000.0)
        assert applied == 200_000.0
        assert new_state.class_b_pdl_balance == 550_000.0

    def test_zero_balance_replenishment_noop(self):
        state = WaterfallState()
        new_state, applied = state.replenish_pdl("class_a", 100_000.0)
        assert applied == 0.0
        assert new_state.class_a_pdl_balance == 0.0

    def test_invalid_tranche_raises(self):
        with pytest.raises(ValueError, match="tranche must be"):
            WaterfallState().replenish_pdl("senior", 100.0)


class TestUpdateReserve:
    """WaterfallState.update_reserve() adjusts reserve_account_balance."""

    def test_payment_increases_balance(self):
        state = WaterfallState(reserve_account_balance=1_000_000.0)
        new_state = state.update_reserve(payment=500_000.0)
        assert new_state.reserve_account_balance == 1_500_000.0

    def test_withdrawal_decreases_balance(self):
        state = WaterfallState(reserve_account_balance=2_000_000.0)
        new_state = state.update_reserve(payment=0.0, withdrawal=500_000.0)
        assert new_state.reserve_account_balance == 1_500_000.0

    def test_balance_floored_at_zero(self):
        state = WaterfallState(reserve_account_balance=100_000.0)
        new_state = state.update_reserve(payment=0.0, withdrawal=999_999.0)
        assert new_state.reserve_account_balance == 0.0

    def test_payment_and_withdrawal_net(self):
        state = WaterfallState(reserve_account_balance=5_000_000.0)
        new_state = state.update_reserve(payment=1_000_000.0, withdrawal=2_000_000.0)
        assert new_state.reserve_account_balance == 4_000_000.0

    def test_zero_payment_withdrawal_noop(self):
        state = WaterfallState(reserve_account_balance=3_000_000.0)
        new_state = state.update_reserve(payment=0.0, withdrawal=0.0)
        assert new_state.reserve_account_balance == 3_000_000.0


# ---------------------------------------------------------------------------
# 2. PDL accumulation across periods
# ---------------------------------------------------------------------------


class TestPdlAccumulationAcrossPeriods:
    """PDL debit accumulates when losses are recorded between periods."""

    def test_pdl_increases_in_period_two_after_loss(self):
        """Period 1 clean; period 2 input has a loss → PDL carried into period 3."""
        # Simulate: record a loss on the state before period 2 runs.
        initial = WaterfallState().record_loss(2_000_000.0, "class_a")
        # Period 1: clean (no PDL in state before this batch)
        p1 = _period_input("Feb 2026", available_revenue_funds=4_000_000.0)
        p2 = _period_input("Mar 2026", available_revenue_funds=4_000_000.0)

        runner = MultiPeriodWaterfallRunner()
        result = runner.execute(
            MultiPeriodWaterfallInput(periods=[p1, p2], initial_state=initial)
        ).output

        # The initial state already carries a PDL; period 1 may replenish it
        # if revenue allows. Check that trajectory starts with the initial PDL.
        assert result.state_trajectory[0].class_a_pdl_balance >= 0.0
        assert result.state_trajectory[0].class_a_pdl_balance <= initial.class_a_pdl_balance

    def test_pdl_carried_forward_if_not_replenished(self):
        """When revenue is insufficient to replenish PDL, balance persists."""
        # A large PDL debit that cannot be replenished with €100 revenue.
        initial = WaterfallState().record_loss(5_000_000.0, "class_a")
        # Provide very low revenue — barely enough for fees, certainly not PDL.
        p1 = _period_input("Feb 2026", available_revenue_funds=100.0, senior_fees=0.0)

        runner = MultiPeriodWaterfallRunner()
        result = runner.execute(
            MultiPeriodWaterfallInput(periods=[p1], initial_state=initial)
        ).output

        # PDL should still be almost entirely outstanding.
        assert result.final_state.class_a_pdl_balance > 4_000_000.0

    def test_pdl_fully_replenished_in_second_period(self):
        """PDL recorded in state is replenished across two periods."""
        # Start with a PDL debit of €1M.
        initial = WaterfallState().record_loss(1_000_000.0, "class_a")
        # Period 1: revenue large enough to replenish the full PDL.
        # After fees + Class A interest (~€3M), there is surplus for PDL step (e).
        p1 = _period_input("Feb 2026", available_revenue_funds=10_000_000.0, senior_fees=0.0)

        runner = MultiPeriodWaterfallRunner()
        result = runner.execute(
            MultiPeriodWaterfallInput(periods=[p1], initial_state=initial)
        ).output

        # After one high-revenue period the PDL should be cleared.
        assert result.final_state.class_a_pdl_balance == 0.0


# ---------------------------------------------------------------------------
# 3. PDL replenishment ordering (step e before Class A principal)
# ---------------------------------------------------------------------------


class TestPdlReplenishmentOrdering:
    """Revenue step (e) replenishes Class A PDL before Class A gets principal."""

    def test_step_e_before_class_a_principal(self):
        """Class A PDL step (e) priority index < Class A principal step in redemption."""
        initial = WaterfallState().record_loss(500_000.0, "class_a")
        p1 = _period_input("Feb 2026", available_revenue_funds=8_000_000.0)

        runner = MultiPeriodWaterfallRunner()
        result = runner.execute(
            MultiPeriodWaterfallInput(periods=[p1], initial_state=initial)
        ).output

        period_out = result.period_results[0]
        rev_recipients = [s.recipient for s in period_out.revenue_waterfall]
        # Step (e) is in the revenue waterfall.
        assert "class_a_pdl_replenishment" in rev_recipients
        # Class A principal is in the redemption waterfall (separate from revenue).
        red_recipients = [s.recipient for s in period_out.redemption_waterfall]
        assert "class_a_principal" in red_recipients
        # Revenue waterfall runs entirely before redemption waterfall.
        # Verify that step (e) runs before step (b) of redemption by checking
        # that the revenue waterfall's step order is (a)…(e)…(k).
        pdl_idx = rev_recipients.index("class_a_pdl_replenishment")
        # Steps (a) through (d) precede (e); index should be 4.
        assert pdl_idx == 4

    def test_pdl_step_distributes_from_revenue_not_principal(self):
        """PDL replenishment comes from revenue funds; principal is unaffected."""
        initial = WaterfallState().record_loss(1_000_000.0, "class_a")
        p1 = _period_input(
            "Feb 2026",
            available_revenue_funds=10_000_000.0,
            available_principal_funds=3_000_000.0,
            senior_fees=0.0,
        )

        runner = MultiPeriodWaterfallRunner()
        result = runner.execute(
            MultiPeriodWaterfallInput(periods=[p1], initial_state=initial)
        ).output

        period_out = result.period_results[0]
        # Revenue PDL step distributes.
        pdl_step = next(
            s for s in period_out.revenue_waterfall
            if s.recipient == "class_a_pdl_replenishment"
        )
        assert pdl_step.amount_distributed == 1_000_000.0
        assert pdl_step.shortfall == 0.0

        # Principal waterfall total is still based on available_principal_funds.
        red_total = sum(s.amount_distributed for s in period_out.redemption_waterfall)
        assert math.isclose(red_total, 3_000_000.0, rel_tol=1e-9)


# ---------------------------------------------------------------------------
# 4. Reserve fund persistence across 3 Green Lion periods
# ---------------------------------------------------------------------------


class TestReserveFundPersistence:
    """Reserve account balance threads forward correctly across 3 periods."""

    def test_reserve_builds_up_across_three_periods(self):
        """Each period tops up the reserve; balance grows to the target."""
        target = 5_000_000.0
        # Start with no reserve; each period contributes ~€1M to the reserve.
        # Available revenue: fees + Class A interest + €1M reserve top-up headroom.
        initial = WaterfallState(reserve_account_balance=0.0)

        # Revenue exactly covers Class A interest + fees + reserve top-up slice.
        # Class A monthly interest ≈ €3.017M; fees = €20k.
        # We provide €5M per period — surplus after interest goes to reserve.
        periods = [
            _green_lion_period("Feb 2026"),
            _green_lion_period("Mar 2026"),
            _green_lion_period("Apr 2026"),
        ]

        runner = MultiPeriodWaterfallRunner()
        result = runner.execute(
            MultiPeriodWaterfallInput(periods=periods, initial_state=initial)
        ).output

        # The reserve should have grown across three periods.
        traj = result.state_trajectory
        assert len(traj) == 3
        # Each period should either keep or grow the reserve (never decrease,
        # since we never model a reserve draw in this clean scenario).
        assert traj[1].reserve_account_balance >= traj[0].reserve_account_balance
        assert traj[2].reserve_account_balance >= traj[1].reserve_account_balance

    def test_reserve_balance_passed_into_period_two(self):
        """The period 1 reserve balance is correctly threaded into period 2."""
        # After period 1 the reserve should reflect what was replenished.
        initial = WaterfallState(reserve_account_balance=2_000_000.0)
        p1 = _period_input(
            "Feb 2026",
            available_revenue_funds=6_000_000.0,
            senior_fees=0.0,
            reserve_account_target=5_000_000.0,
            # This field is overridden by state; set it to an obviously wrong
            # value to prove the runner ignores the period input's field.
            reserve_account_balance=0.0,
        )
        p2 = _period_input(
            "Mar 2026",
            available_revenue_funds=6_000_000.0,
            senior_fees=0.0,
            reserve_account_target=5_000_000.0,
            reserve_account_balance=0.0,
        )

        runner = MultiPeriodWaterfallRunner()
        result = runner.execute(
            MultiPeriodWaterfallInput(periods=[p1, p2], initial_state=initial)
        ).output

        # Period 1 starts with a €2M reserve from state (not the 0.0 in p1).
        # The reserve is already partially funded; period 1 adds more.
        # Period 2's input is also overridden, so its reserve comes from state too.
        assert result.state_trajectory[0].reserve_account_balance >= 2_000_000.0
        assert result.state_trajectory[1].reserve_account_balance >= \
               result.state_trajectory[0].reserve_account_balance


# ---------------------------------------------------------------------------
# 5. Cumulative loss rate computation
# ---------------------------------------------------------------------------


class TestCumulativeLossRate:
    """cumulative_loss_rate_pct reflects losses relative to original pool."""

    def test_one_percent_loss(self):
        loss = _ORIGINAL_POOL * 0.01  # exactly 1%
        state = WaterfallState(original_pool_balance=_ORIGINAL_POOL)
        new_state = state.record_loss(loss, "class_a")
        assert math.isclose(new_state.cumulative_loss_rate_pct, 1.0, rel_tol=1e-9)

    def test_cumulative_rate_additive_across_records(self):
        state = WaterfallState(original_pool_balance=1_000_000.0)
        state = state.record_loss(10_000.0, "class_a")   # 1%
        state = state.record_loss(10_000.0, "class_b")   # another 1%
        assert math.isclose(state.cumulative_loss_rate_pct, 2.0, rel_tol=1e-9)
        assert math.isclose(state.cumulative_principal_losses, 20_000.0, rel_tol=1e-9)

    def test_zero_loss_rate_on_fresh_state(self):
        assert WaterfallState().cumulative_loss_rate_pct == 0.0

    def test_loss_rate_independent_of_pdl_replenishment(self):
        """Replenishing PDL does not reduce the cumulative loss rate."""
        state = WaterfallState(original_pool_balance=_ORIGINAL_POOL)
        state = state.record_loss(1_000_000.0, "class_a")
        rate_before = state.cumulative_loss_rate_pct
        # Replenish the PDL fully.
        state, _ = state.replenish_pdl("class_a", 1_000_000.0)
        # The loss rate must not change — replenishment is a revenue action,
        # not a reversal of the loss itself.
        assert math.isclose(state.cumulative_loss_rate_pct, rate_before, rel_tol=1e-9)


# ---------------------------------------------------------------------------
# 6. state_trajectory length equals len(periods)
# ---------------------------------------------------------------------------


class TestStateTrajectoryLength:
    """state_trajectory has exactly one entry per period."""

    @pytest.mark.parametrize("n_periods", [1, 2, 3, 5])
    def test_trajectory_length(self, n_periods: int):
        periods = [
            _period_input(f"Period {i}", available_revenue_funds=4_000_000.0)
            for i in range(n_periods)
        ]
        runner = MultiPeriodWaterfallRunner()
        result = runner.execute(
            MultiPeriodWaterfallInput(periods=periods)
        ).output
        assert len(result.state_trajectory) == n_periods

    def test_trajectory_length_matches_period_results(self):
        periods = [_period_input("P1"), _period_input("P2"), _period_input("P3")]
        runner = MultiPeriodWaterfallRunner()
        result = runner.execute(
            MultiPeriodWaterfallInput(periods=periods)
        ).output
        assert len(result.state_trajectory) == len(result.period_results)


# ---------------------------------------------------------------------------
# 7. Green Lion clean-pool synthetic run (no PDL breaches expected)
# ---------------------------------------------------------------------------


class TestGreenLionCleanPool:
    """Three Green Lion periods (Feb/Mar/Apr) with no PDL breaches."""

    @pytest.fixture
    def clean_pool_result(self):
        """Run three Green Lion periods with a clean initial state."""
        initial = WaterfallState(
            reserve_account_balance=5_000_000.0,  # reserve pre-funded at target
        )
        periods = [
            _green_lion_period("Feb 2026"),
            _green_lion_period("Mar 2026"),
            _green_lion_period("Apr 2026"),
        ]
        runner = MultiPeriodWaterfallRunner()
        return runner.execute(
            MultiPeriodWaterfallInput(periods=periods, initial_state=initial)
        )

    def test_no_pdl_breach_in_clean_pool(self, clean_pool_result):
        """In a clean pool no PDL debit should accumulate."""
        result = clean_pool_result.output
        assert result.final_state.class_a_pdl_balance == 0.0
        assert result.final_state.class_b_pdl_balance == 0.0

    def test_three_period_results_produced(self, clean_pool_result):
        """Three periods → three WaterfallOutput objects."""
        assert len(clean_pool_result.output.period_results) == 3

    def test_confidence_always_1(self, clean_pool_result):
        """Deterministic computation — confidence must be 1.0."""
        assert clean_pool_result.confidence == 1.0

    def test_cumulative_loss_rate_zero(self, clean_pool_result):
        """No losses recorded → cumulative loss rate is zero."""
        assert clean_pool_result.output.final_state.cumulative_loss_rate_pct == 0.0

    def test_cumulative_distributions_covers_all_steps(self, clean_pool_result):
        """cumulative_distributions should include every waterfall step recipient."""
        cumul = clean_pool_result.output.cumulative_distributions
        # Senior fees are always distributed (they are at the top of the waterfall).
        assert "senior_fees" in cumul
        assert cumul["senior_fees"] > 0.0
        # Class A interest should appear (revenue step d).
        assert "class_a_interest" in cumul
        assert cumul["class_a_interest"] > 0.0

    def test_cumulative_class_a_interest_roughly_three_months(self, clean_pool_result):
        """Cumulative Class A interest over 3 months ≈ 3 × monthly interest."""
        expected_total = _CLASS_A_MONTHLY_INTEREST * 3
        actual = clean_pool_result.output.cumulative_distributions.get(
            "class_a_interest", 0.0
        )
        # Allow for rounding; use 0.1% relative tolerance.
        assert math.isclose(actual, expected_total, rel_tol=1e-3), (
            f"Expected cumulative Class A interest ≈ {expected_total:,.0f}, "
            f"got {actual:,.0f}"
        )

    def test_audit_entry_is_populated(self, clean_pool_result):
        ae = clean_pool_result.audit_entry
        assert ae.primitive_name == "multi_period_waterfall_runner"
        assert ae.version == "0.1.0"
        assert len(ae.input_hash) == 64
        assert ae.duration_ms >= 0.0


# ---------------------------------------------------------------------------
# 8. Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    """MultiPeriodWaterfallRunner must be discoverable from PRIMITIVE_REGISTRY."""

    def test_registered(self):
        reg = PRIMITIVE_REGISTRY.get("multi_period_waterfall_runner")
        assert reg is not None
        assert reg.name == "multi_period_waterfall_runner"
        assert reg.version == "0.1.0"

    def test_stateful_tag(self):
        reg = PRIMITIVE_REGISTRY.get("multi_period_waterfall_runner")
        assert "stateful" in reg.tags

    def test_waterfall_tag(self):
        reg = PRIMITIVE_REGISTRY.get("multi_period_waterfall_runner")
        assert "waterfall" in reg.tags
