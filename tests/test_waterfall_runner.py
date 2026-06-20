"""Tests for WaterfallRunner primitive.

Synthetic Green Lion 2026-1 inputs based on the deal's structural parameters:
- Class A: €1.0B at 3.62% p.a. (EURIBOR 3.19 + 0.43)
- Class B: €53.1M
- Class C: €10.5M
- Quarterly payment period, 90 days, Act/360 convention
"""

from __future__ import annotations

import math

import pytest

from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY
from loanwhiz.primitives.waterfall_runner import (
    WaterfallInput,
    WaterfallOutput,
    WaterfallRunner,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CLASS_A_BALANCE = 1_000_000_000.0   # €1.0 billion
_CLASS_A_RATE_PCT = 3.62              # 3.62% p.a. (EURIBOR 3.19 + 0.43)
_CLASS_B_BALANCE = 53_100_000.0      # €53.1M
_CLASS_C_BALANCE = 10_500_000.0      # €10.5M
_DAYS = 90

# Expected Class A quarterly interest: 1e9 * 0.0362 / 360 * 90
_EXPECTED_CLASS_A_INTEREST = _CLASS_A_BALANCE * (_CLASS_A_RATE_PCT / 100.0) / 360.0 * _DAYS
# ≈ 9,050,000


def _base_input(**overrides) -> WaterfallInput:
    """Return a synthetic WaterfallInput with happy-path defaults."""
    defaults = dict(
        reporting_period="April 2026",
        available_revenue_funds=10_000_000.0,    # €10M — covers Class A interest
        available_principal_funds=5_000_000.0,   # €5M principal collections
        senior_fees=50_000.0,                    # €50k trustee fee
        swap_payment=0.0,
        class_a_balance=_CLASS_A_BALANCE,
        class_a_rate_pct=_CLASS_A_RATE_PCT,
        class_b_balance=_CLASS_B_BALANCE,
        class_c_balance=_CLASS_C_BALANCE,
        reserve_account_balance=5_000_000.0,
        reserve_account_target=5_000_000.0,      # reserve is full → no top-up needed
        class_a_pdl_balance=0.0,
        class_b_pdl_balance=0.0,
        days_in_period=_DAYS,
    )
    defaults.update(overrides)
    return WaterfallInput(**defaults)


@pytest.fixture
def runner() -> WaterfallRunner:
    return WaterfallRunner()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _step(output: WaterfallOutput, waterfall: str, recipient: str):
    """Return the WaterfallStep for *recipient* in *waterfall* ('revenue'/'redemption')."""
    steps = (
        output.revenue_waterfall
        if waterfall == "revenue"
        else output.redemption_waterfall
    )
    for step in steps:
        if step.recipient == recipient:
            return step
    raise KeyError(f"No step with recipient={recipient!r} in {waterfall} waterfall")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_waterfall_runner_registered():
    """The primitive must be discoverable from PRIMITIVE_REGISTRY."""
    reg = PRIMITIVE_REGISTRY.get("waterfall_runner")
    assert reg is not None
    assert reg.name == "waterfall_runner"
    assert reg.version == "0.1.0"
    assert "waterfall" in reg.tags


# ---------------------------------------------------------------------------
# Happy-path test
# ---------------------------------------------------------------------------

class TestHappyPath:
    """Available revenue covers all obligations; no shortfall expected."""

    def test_class_a_interest_amount(self, runner: WaterfallRunner):
        """Class A should receive the Act/360 quarterly interest."""
        result = runner.execute(_base_input())
        step = _step(result.output, "revenue", "class_a_interest")
        assert math.isclose(
            step.amount_distributed, _EXPECTED_CLASS_A_INTEREST, rel_tol=1e-9
        ), (
            f"Expected Class A interest ≈ {_EXPECTED_CLASS_A_INTEREST:,.2f}, "
            f"got {step.amount_distributed:,.2f}"
        )

    def test_no_shortfall(self, runner: WaterfallRunner):
        """Overall shortfall must be zero when revenue covers all steps.

        The tool now folds the period through ``run_period`` (#276), whose
        PDL/reserve arithmetic can leave a sub-cent floating-point residue, so
        assert ``≈ 0`` (well within EUR 0.01) rather than exact equality.
        """
        result = runner.execute(_base_input())
        assert result.output.shortfall == pytest.approx(0.0, abs=1e-6), (
            f"Expected shortfall≈0.0, got {result.output.shortfall}"
        )

    def test_confidence_is_1(self, runner: WaterfallRunner):
        """Deterministic computation — confidence must always be 1.0."""
        result = runner.execute(_base_input())
        assert result.confidence == 1.0

    def test_class_a_tranche_interest(self, runner: WaterfallRunner):
        """TrancheDistribution for class_a must record the interest received."""
        result = runner.execute(_base_input())
        class_a = next(
            d for d in result.output.tranche_distributions if d.tranche == "class_a"
        )
        assert math.isclose(
            class_a.interest_received, _EXPECTED_CLASS_A_INTEREST, rel_tol=1e-9
        )

    def test_audit_entry_populated(self, runner: WaterfallRunner):
        """AuditEntry must carry the correct primitive name and a valid hash."""
        result = runner.execute(_base_input())
        ae = result.audit_entry
        assert ae.primitive_name == "waterfall_runner"
        assert ae.version == "0.1.0"
        assert len(ae.input_hash) == 64  # SHA-256 hex
        assert ae.duration_ms >= 0.0

    def test_citation_references_prospectus(self, runner: WaterfallRunner):
        """At least one citation must reference the prospectus section 5.2."""
        result = runner.execute(_base_input())
        assert any(
            "5.2" in (c.page_or_row or "") or "5.2" in c.excerpt
            for c in result.citations
        )

    def test_revenue_waterfall_step_count(self, runner: WaterfallRunner):
        """Revenue waterfall must have exactly 11 steps (a)–(k)."""
        result = runner.execute(_base_input())
        assert len(result.output.revenue_waterfall) == 11

    def test_redemption_waterfall_step_count(self, runner: WaterfallRunner):
        """Redemption waterfall must have exactly 4 steps (a)–(d)."""
        result = runner.execute(_base_input())
        assert len(result.output.redemption_waterfall) == 4

    def test_senior_fees_distributed(self, runner: WaterfallRunner):
        """Step (a) must distribute the full senior_fees amount."""
        result = runner.execute(_base_input())
        step = _step(result.output, "revenue", "senior_fees")
        assert step.amount_distributed == 50_000.0
        assert step.shortfall == 0.0

    def test_reserve_full_no_replenishment(self, runner: WaterfallRunner):
        """When reserve is full, step (f) distributes zero."""
        result = runner.execute(_base_input())
        step = _step(result.output, "revenue", "reserve_account_replenishment")
        assert step.amount_distributed == 0.0
        assert step.shortfall == 0.0

    def test_tranche_closing_balance(self, runner: WaterfallRunner):
        """Class A closing balance = opening balance minus principal received."""
        result = runner.execute(_base_input())
        class_a = next(
            d for d in result.output.tranche_distributions if d.tranche == "class_a"
        )
        expected_closing = max(
            0.0, class_a.opening_balance - class_a.principal_received
        )
        assert math.isclose(class_a.closing_balance, expected_closing, rel_tol=1e-9)

    def test_total_received_consistency(self, runner: WaterfallRunner):
        """total_received must equal interest + principal for every tranche."""
        result = runner.execute(_base_input())
        for dist in result.output.tranche_distributions:
            assert math.isclose(
                dist.total_received,
                dist.interest_received + dist.principal_received,
                rel_tol=1e-9,
            )


# ---------------------------------------------------------------------------
# Shortfall test
# ---------------------------------------------------------------------------

class TestShortfall:
    """Available revenue is less than Class A interest — shortfall expected."""

    def test_shortfall_positive_when_revenue_insufficient(self, runner: WaterfallRunner):
        """WaterfallOutput.shortfall > 0 when revenue < Class A interest."""
        # €5M revenue is ~55% of the ~€9.05M Class A interest need.
        result = runner.execute(_base_input(available_revenue_funds=5_000_000.0))
        assert result.output.shortfall > 0.0

    def test_class_a_interest_step_has_shortfall(self, runner: WaterfallRunner):
        """Class A interest step must show a shortfall when revenue is low."""
        result = runner.execute(
            _base_input(
                available_revenue_funds=1_000_000.0,  # far below Class A interest need
                senior_fees=0.0,
            )
        )
        step = _step(result.output, "revenue", "class_a_interest")
        assert step.shortfall > 0.0

    def test_class_a_interest_capped_at_available(self, runner: WaterfallRunner):
        """Class A interest distributed must not exceed available revenue (after fees)."""
        available = 2_000_000.0
        result = runner.execute(
            _base_input(available_revenue_funds=available, senior_fees=0.0)
        )
        step = _step(result.output, "revenue", "class_a_interest")
        assert step.amount_distributed <= available

    def test_confidence_is_1_on_shortfall(self, runner: WaterfallRunner):
        """Confidence must still be 1.0 even when there is a shortfall."""
        result = runner.execute(_base_input(available_revenue_funds=100.0))
        assert result.confidence == 1.0


# ---------------------------------------------------------------------------
# PDL test
# ---------------------------------------------------------------------------

class TestPdlReplenishment:
    """PDL debit balance reduces funds available for downstream steps."""

    def test_class_a_pdl_step_distributes_before_reserve(self, runner: WaterfallRunner):
        """Class A PDL step (e) runs before reserve replenishment step (f)."""
        pdl_amount = 500_000.0
        result = runner.execute(
            _base_input(
                available_revenue_funds=15_000_000.0,  # plenty of revenue
                class_a_pdl_balance=pdl_amount,
                reserve_account_balance=4_000_000.0,   # reserve needs top-up
                reserve_account_target=5_000_000.0,
            )
        )
        # Step (e) must fully distribute the PDL balance.
        pdl_step = _step(result.output, "revenue", "class_a_pdl_replenishment")
        assert math.isclose(pdl_step.amount_distributed, pdl_amount, rel_tol=1e-9)
        assert pdl_step.shortfall == 0.0

    def test_class_a_pdl_step_priority_order(self, runner: WaterfallRunner):
        """PDL step (e) priority index must be less than reserve step (f) index."""
        result = runner.execute(_base_input(class_a_pdl_balance=100_000.0))
        recipients = [s.recipient for s in result.output.revenue_waterfall]
        assert recipients.index("class_a_pdl_replenishment") < recipients.index(
            "reserve_account_replenishment"
        )

    def test_class_b_pdl_step_distributes(self, runner: WaterfallRunner):
        """Class B PDL step (h) distributes correctly."""
        pdl_b = 200_000.0
        result = runner.execute(
            _base_input(
                available_revenue_funds=15_000_000.0,
                class_b_pdl_balance=pdl_b,
            )
        )
        step = _step(result.output, "revenue", "class_b_pdl_replenishment")
        assert math.isclose(step.amount_distributed, pdl_b, rel_tol=1e-9)
        assert step.shortfall == 0.0

    def test_pdl_drains_available_funds(self, runner: WaterfallRunner):
        """Funds distributed to PDL step must reduce availability for later steps."""
        # Very tight revenue: just enough for fees + Class A interest + PDL
        # → downstream steps (reserve) should get nothing or less.
        result = runner.execute(
            _base_input(
                available_revenue_funds=9_200_000.0,  # tight budget
                senior_fees=50_000.0,
                class_a_pdl_balance=1_000_000.0,      # large PDL consumes post-interest surplus
                reserve_account_balance=4_000_000.0,
                reserve_account_target=5_000_000.0,   # needs €1M top-up
            )
        )
        # Class A interest ≈ €9.05M; fees = €50k; PDL = €1M.
        # 9.2M - 50k = 9.15M; 9.15M - 9.05M = 100k left after interest;
        # PDL needs 1M but only 100k available → PDL has a shortfall.
        pdl_step = _step(result.output, "revenue", "class_a_pdl_replenishment")
        assert pdl_step.shortfall > 0.0
        # Reserve step follows PDL → nothing left for reserve.
        reserve_step = _step(result.output, "revenue", "reserve_account_replenishment")
        assert reserve_step.amount_distributed == 0.0


# ---------------------------------------------------------------------------
# Waterfall step ordering
# ---------------------------------------------------------------------------

class TestStepOrdering:
    """Verify priority ordering of steps in both waterfalls."""

    _EXPECTED_REV_PRIORITIES = ["(a)", "(b)", "(c)", "(d)", "(e)", "(f)", "(g)", "(h)", "(i)", "(j)", "(k)"]
    _EXPECTED_RED_PRIORITIES = ["(a)", "(b)", "(c)", "(d)"]

    def test_revenue_priority_order(self, runner: WaterfallRunner):
        result = runner.execute(_base_input())
        priorities = [s.priority for s in result.output.revenue_waterfall]
        assert priorities == self._EXPECTED_REV_PRIORITIES

    def test_redemption_priority_order(self, runner: WaterfallRunner):
        result = runner.execute(_base_input())
        priorities = [s.priority for s in result.output.redemption_waterfall]
        assert priorities == self._EXPECTED_RED_PRIORITIES


# ---------------------------------------------------------------------------
# Interest formula
# ---------------------------------------------------------------------------

class TestInterestFormula:
    """Verify the Act/360 quarterly interest formula."""

    @pytest.mark.parametrize("days,rate_pct,balance,expected", [
        # Standard quarterly: 90 days, 3.62%, €1B
        (90, 3.62, 1_000_000_000.0, 1_000_000_000.0 * 0.0362 / 360 * 90),
        # Monthly stub: 30 days, 3.62%, €1B
        (30, 3.62, 1_000_000_000.0, 1_000_000_000.0 * 0.0362 / 360 * 30),
        # Different rate: 91 days, 5.00%, €500M
        (91, 5.00, 500_000_000.0, 500_000_000.0 * 0.05 / 360 * 91),
    ])
    def test_act_360_interest(
        self,
        runner: WaterfallRunner,
        days: int,
        rate_pct: float,
        balance: float,
        expected: float,
    ):
        """Act/360 interest = balance * rate/100 / 360 * days."""
        result = runner.execute(
            _base_input(
                available_revenue_funds=expected * 2,  # enough to not shortfall
                class_a_balance=balance,
                class_a_rate_pct=rate_pct,
                days_in_period=days,
                senior_fees=0.0,
            )
        )
        step = _step(result.output, "revenue", "class_a_interest")
        assert math.isclose(step.amount_distributed, expected, rel_tol=1e-9)
