"""Tests for CollectionsAggregator primitive.

Two categories:
1. Unit tests — no network access; use an in-memory synthetic tape via
   ``io.StringIO``.  Exercises every code path: interest calculation,
   principal delta (with and without prev_pool_balance), class-A coupon,
   confidence levels, registry registration.
2. Integration test — marked ``@pytest.mark.slow``; hits the real Green
   Lion April 2026 tape on HuggingFace.  Validates the end-to-end numbers
   against the Mar→Apr balance delta.
"""

from __future__ import annotations

import io
import math

import pandas as pd
import pytest

from loanwhiz.config import GREEN_LION
from loanwhiz.primitives.collections_aggregator import (
    CollectionsAggregator,
    CollectionsInput,
    CollectionsOutput,
)
from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APRIL_TAPE_URL = next(
    entry["url"]
    for entry in GREEN_LION["tape_urls"]
    if entry["date"] == "2026-04-30"
)

# March ending balance (the prior period balance for April)
MAR_POOL_BALANCE = 1_042_490_000.0

# Class A analytic sanity
CLASS_A_BALANCE = 1_000_000_000.0
CLASS_A_RATE_PCT = 3.62
DAYS_IN_PERIOD = 90

# Expected class A interest due (analytic formula)
EXPECTED_CLASS_A_INTEREST = CLASS_A_BALANCE * CLASS_A_RATE_PCT / 100.0 * DAYS_IN_PERIOD / 360.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_synthetic_tape(
    *,
    loan_count: int = 10,
    balance_per_loan: float = 100_000.0,
    rate_pct: float = 3.0,
    payment_per_loan: float = 500.0,
) -> str:
    """Return a CSV string with the minimal columns the aggregator uses."""
    rows = []
    for i in range(loan_count):
        rows.append(
            {
                "loan_id": f"L{i:04d}",
                "current_balance": balance_per_loan,
                "current_interest_rate_pct": rate_pct,
                "scheduled_monthly_payment": payment_per_loan,
                "default_crr_flag": "N",
            }
        )
    df = pd.DataFrame(rows)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


def _tape_url_from_string(csv_str: str) -> str:
    """Write the CSV to a temp file and return a local path.

    The file is created with ``delete=False`` so pandas can open it by path
    after the function returns.  Temp files are small (synthetic tapes) and
    will be cleaned up by the OS on process exit.
    """
    import tempfile

    tf = tempfile.NamedTemporaryFile(
        mode="w", suffix=".csv", delete=False, encoding="utf-8"
    )
    tf.write(csv_str)
    tf.flush()
    tf.close()
    return tf.name  # pandas read_csv accepts plain local paths


# ---------------------------------------------------------------------------
# Unit tests — CollectionsAggregator (no network)
# ---------------------------------------------------------------------------


class TestCollectionsAggregatorUnit:
    """Fast unit tests against synthetic in-memory tapes."""

    def _run(self, **kwargs) -> tuple:
        """Return (result, output) for convenience."""
        csv_str = _make_synthetic_tape(**kwargs)
        tape_path = _tape_url_from_string(csv_str)
        agg = CollectionsAggregator()
        inp = CollectionsInput(
            tape_file_url=tape_path,
            reporting_period="Test Period",
            class_a_balance=CLASS_A_BALANCE,
            class_a_rate_pct=CLASS_A_RATE_PCT,
            days_in_period=DAYS_IN_PERIOD,
        )
        result = agg.execute(inp)
        return result, result.output

    # ------------------------------------------------------------------
    # Registry
    # ------------------------------------------------------------------

    def test_registry_contains_primitive(self) -> None:
        # Importing the module registers it; just assert it's there.
        assert "collections_aggregator" in PRIMITIVE_REGISTRY

    def test_registry_metadata(self) -> None:
        reg = PRIMITIVE_REGISTRY.get("collections_aggregator")
        assert reg is not None
        assert reg.version == "0.1.0"
        assert "waterfall" in reg.tags

    # ------------------------------------------------------------------
    # Pool balance and loan count
    # ------------------------------------------------------------------

    def test_pool_balance_sum(self) -> None:
        result, output = self._run(loan_count=10, balance_per_loan=100_000.0)
        assert math.isclose(output.pool_balance_eur, 1_000_000.0, rel_tol=1e-6)

    def test_loan_count(self) -> None:
        result, output = self._run(loan_count=7)
        assert output.loan_count == 7

    # ------------------------------------------------------------------
    # Interest calculation
    # ------------------------------------------------------------------

    def test_interest_collected_formula(self) -> None:
        """interest = pool_balance * rate/100 * days/360"""
        loan_count = 10
        balance = 100_000.0
        rate = 3.0
        result, output = self._run(
            loan_count=loan_count, balance_per_loan=balance, rate_pct=rate
        )
        pool_balance = loan_count * balance
        expected_interest = pool_balance * rate / 100.0 * DAYS_IN_PERIOD / 360.0
        assert math.isclose(output.interest_collected, expected_interest, rel_tol=1e-6)

    def test_available_revenue_funds_equals_interest_plus_swap(self) -> None:
        result, output = self._run()
        assert math.isclose(
            output.available_revenue_funds,
            output.interest_collected + output.swap_receipts,
            rel_tol=1e-9,
        )

    def test_swap_receipts_is_zero(self) -> None:
        result, output = self._run()
        assert output.swap_receipts == 0.0

    # ------------------------------------------------------------------
    # Principal — without prev_pool_balance (estimation)
    # ------------------------------------------------------------------

    def test_scheduled_principal_estimated_from_payments(self) -> None:
        """Without prev_balance, scheduled = sum(monthly_payment) * days/30."""
        loan_count = 10
        payment = 500.0
        csv_str = _make_synthetic_tape(loan_count=loan_count, payment_per_loan=payment)
        tape_path = _tape_url_from_string(csv_str)
        agg = CollectionsAggregator()
        inp = CollectionsInput(
            tape_file_url=tape_path,
            reporting_period="Test",
            prev_pool_balance=None,
            days_in_period=DAYS_IN_PERIOD,
        )
        result = agg.execute(inp)
        expected = loan_count * payment * (DAYS_IN_PERIOD / 30.0)
        assert math.isclose(result.output.scheduled_principal, expected, rel_tol=1e-6)

    # ------------------------------------------------------------------
    # Principal — with prev_pool_balance (balance delta)
    # ------------------------------------------------------------------

    def test_scheduled_principal_from_balance_delta(self) -> None:
        csv_str = _make_synthetic_tape(loan_count=10, balance_per_loan=100_000.0)
        tape_path = _tape_url_from_string(csv_str)
        current_pool = 10 * 100_000.0  # 1_000_000
        prev_pool = 1_009_000.0  # prior period was higher
        agg = CollectionsAggregator()
        inp = CollectionsInput(
            tape_file_url=tape_path,
            reporting_period="Test",
            prev_pool_balance=prev_pool,
            days_in_period=DAYS_IN_PERIOD,
        )
        result = agg.execute(inp)
        expected = prev_pool - current_pool  # 9_000.0
        assert math.isclose(result.output.scheduled_principal, expected, rel_tol=1e-6)

    def test_scheduled_principal_never_negative(self) -> None:
        """If balance grew (shouldn't happen in practice), principal = 0, not negative."""
        csv_str = _make_synthetic_tape(loan_count=10, balance_per_loan=100_000.0)
        tape_path = _tape_url_from_string(csv_str)
        prev_pool = 500_000.0  # lower than current — balance grew somehow
        agg = CollectionsAggregator()
        inp = CollectionsInput(
            tape_file_url=tape_path,
            reporting_period="Test",
            prev_pool_balance=prev_pool,
            days_in_period=DAYS_IN_PERIOD,
        )
        result = agg.execute(inp)
        assert result.output.scheduled_principal >= 0.0

    # ------------------------------------------------------------------
    # Available Principal Funds
    # ------------------------------------------------------------------

    def test_available_principal_funds_formula(self) -> None:
        result, output = self._run()
        assert math.isclose(
            output.available_principal_funds,
            output.scheduled_principal + output.unscheduled_principal + output.recoveries,
            rel_tol=1e-9,
        )

    def test_unscheduled_principal_is_zero(self) -> None:
        result, output = self._run()
        assert output.unscheduled_principal == 0.0

    def test_recoveries_is_zero(self) -> None:
        result, output = self._run()
        assert output.recoveries == 0.0

    # ------------------------------------------------------------------
    # Class A interest due
    # ------------------------------------------------------------------

    def test_class_a_interest_due_formula(self) -> None:
        """class_a_interest_due = balance * rate/100 * days/360"""
        result, output = self._run()
        assert math.isclose(
            output.class_a_interest_due,
            EXPECTED_CLASS_A_INTEREST,
            rel_tol=1e-6,
        )

    def test_class_a_interest_due_with_custom_params(self) -> None:
        csv_str = _make_synthetic_tape()
        tape_path = _tape_url_from_string(csv_str)
        agg = CollectionsAggregator()
        balance = 500_000_000.0
        rate = 4.0
        days = 30
        inp = CollectionsInput(
            tape_file_url=tape_path,
            reporting_period="Test",
            class_a_balance=balance,
            class_a_rate_pct=rate,
            days_in_period=days,
        )
        result = agg.execute(inp)
        expected = balance * rate / 100.0 * days / 360.0
        assert math.isclose(result.output.class_a_interest_due, expected, rel_tol=1e-6)

    # ------------------------------------------------------------------
    # Confidence
    # ------------------------------------------------------------------

    def test_confidence_low_when_no_prev_balance(self) -> None:
        result, output = self._run()
        assert math.isclose(result.confidence, 0.6, rel_tol=1e-6)

    def test_confidence_high_when_prev_balance_known(self) -> None:
        csv_str = _make_synthetic_tape()
        tape_path = _tape_url_from_string(csv_str)
        agg = CollectionsAggregator()
        inp = CollectionsInput(
            tape_file_url=tape_path,
            reporting_period="Test",
            prev_pool_balance=1_100_000.0,
        )
        result = agg.execute(inp)
        assert math.isclose(result.confidence, 0.8, rel_tol=1e-6)

    def test_confidence_always_below_one(self) -> None:
        result, output = self._run()
        assert result.confidence < 1.0

    # ------------------------------------------------------------------
    # Audit & citations
    # ------------------------------------------------------------------

    def test_audit_entry_present(self) -> None:
        result, _ = self._run()
        assert result.audit_entry.primitive_name == "collections_aggregator"
        assert result.audit_entry.version == "0.1.0"
        assert len(result.audit_entry.input_hash) == 64

    def test_citation_present(self) -> None:
        result, _ = self._run()
        assert len(result.citations) == 1
        assert result.citations[0].document is not None

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def test_summary_contains_period(self) -> None:
        result, output = self._run()
        assert "Test Period" in output.summary

    def test_senior_fees_passed_through(self) -> None:
        csv_str = _make_synthetic_tape()
        tape_path = _tape_url_from_string(csv_str)
        agg = CollectionsAggregator()
        inp = CollectionsInput(
            tape_file_url=tape_path,
            reporting_period="Test",
            senior_fees_estimate=75_000.0,
        )
        result = agg.execute(inp)
        assert result.output.senior_fees == 75_000.0

    # ------------------------------------------------------------------
    # Output typing
    # ------------------------------------------------------------------

    def test_output_is_collections_output(self) -> None:
        result, output = self._run()
        assert isinstance(output, CollectionsOutput)


# ---------------------------------------------------------------------------
# Integration test — Green Lion April 2026 tape (requires network)
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestCollectionsAggregatorIntegration:
    """Integration tests against the real Green Lion 2026 April tape."""

    @pytest.fixture(scope="class")
    def april_result(self):
        agg = CollectionsAggregator()
        inp = CollectionsInput(
            tape_file_url=APRIL_TAPE_URL,
            reporting_period="April 2026",
            prev_pool_balance=MAR_POOL_BALANCE,
            class_a_balance=CLASS_A_BALANCE,
            class_a_rate_pct=CLASS_A_RATE_PCT,
            days_in_period=DAYS_IN_PERIOD,
        )
        return agg.execute(inp)

    def test_available_revenue_funds_positive(self, april_result) -> None:
        assert april_result.output.available_revenue_funds > 0

    def test_available_principal_funds_positive(self, april_result) -> None:
        """With prev_balance known, principal reduction should be ~€9m."""
        assert april_result.output.available_principal_funds > 0

    def test_principal_reduction_order_of_magnitude(self, april_result) -> None:
        """Mar→Apr pool reduction should be in the single-digit millions."""
        # The March balance is ~1,042,490,000; April should be lower.
        # We assert principal is between 1m and 50m (rough sanity bound).
        principal = april_result.output.scheduled_principal
        assert 1_000_000 <= principal <= 50_000_000, (
            f"Unexpected principal {principal:,.0f} — "
            f"expected single-digit millions"
        )

    def test_confidence_below_one(self, april_result) -> None:
        assert april_result.confidence < 1.0

    def test_confidence_is_0_8_with_prev_balance(self, april_result) -> None:
        assert math.isclose(april_result.confidence, 0.8, rel_tol=1e-6)

    def test_class_a_interest_due_matches_formula(self, april_result) -> None:
        """class_a_interest_due must equal the analytic formula to within 1%."""
        assert math.isclose(
            april_result.output.class_a_interest_due,
            EXPECTED_CLASS_A_INTEREST,
            rel_tol=0.01,
        )

    def test_pool_balance_reasonable(self, april_result) -> None:
        """Pool balance should be in the order of ~€1bn."""
        pool = april_result.output.pool_balance_eur
        assert 900_000_000 <= pool <= 1_100_000_000, (
            f"Pool balance {pool:,.0f} outside expected range"
        )

    def test_loan_count_positive(self, april_result) -> None:
        assert april_result.output.loan_count > 0

    def test_summary_mentions_period(self, april_result) -> None:
        assert "April 2026" in april_result.output.summary

    def test_citation_references_tape(self, april_result) -> None:
        assert any(
            "green_lion" in c.document.lower() or "huggingface" in c.document.lower()
            for c in april_result.citations
        )
