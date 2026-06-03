"""Tests for EsmaTapeNormaliser primitive.

Two categories:
1. Unit tests for ``_detect_annex`` — no network, fast.
2. Integration test against the real Green Lion April 2026 tape on HuggingFace.
   Marked ``@pytest.mark.slow`` so CI can skip it with ``-m "not slow"`` when
   network access is unavailable.
"""

from __future__ import annotations

import pytest

from loanwhiz.config import GREEN_LION
from loanwhiz.primitives.esma_tape_normaliser import (
    EsmaTapeInput,
    EsmaTapeNormaliser,
    _detect_annex,
)
from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

APRIL_TAPE_URL = next(
    entry["url"]
    for entry in GREEN_LION["tape_urls"]
    if entry["date"] == "2026-04-30"
)


# ---------------------------------------------------------------------------
# Unit tests — _detect_annex
# ---------------------------------------------------------------------------


class TestDetectAnnex:
    """Unit tests for the column-based Annex detection logic."""

    def test_rmbs_detected_on_epc_and_property_type(self) -> None:
        cols = {"epc_label", "property_type", "current_balance", "arrears_bucket"}
        label, certain = _detect_annex(cols)
        assert label == "Annex 2 (RMBS)"
        assert certain is True

    def test_auto_detected_on_vehicle_type(self) -> None:
        cols = {"vehicle_type", "current_balance", "arrears_bucket"}
        label, certain = _detect_annex(cols)
        assert label == "Annex 5 (Auto)"
        assert certain is True

    def test_sme_detected_on_company_size(self) -> None:
        cols = {"company_size", "current_balance", "arrears_bucket"}
        label, certain = _detect_annex(cols)
        assert label == "Annex 8 (SME)"
        assert certain is True

    def test_unknown_abs_when_no_signature_matches(self) -> None:
        cols = {"current_balance", "borrower_id", "maturity_date"}
        label, certain = _detect_annex(cols)
        assert label == "Unknown ABS"
        assert certain is False

    def test_rmbs_requires_both_epc_and_property_type(self) -> None:
        # epc_label alone without property_type should NOT trigger RMBS
        cols_epc_only = {"epc_label", "current_balance"}
        label, certain = _detect_annex(cols_epc_only)
        assert label == "Unknown ABS"
        assert certain is False

        # property_type alone without epc_label should NOT trigger RMBS
        cols_prop_only = {"property_type", "current_balance"}
        label, certain = _detect_annex(cols_prop_only)
        assert label == "Unknown ABS"
        assert certain is False


# ---------------------------------------------------------------------------
# Unit tests — primitive registration
# ---------------------------------------------------------------------------


class TestPrimitiveRegistration:
    """Verify the decorator wired the primitive into the global registry."""

    def test_registered_in_primitive_registry(self) -> None:
        assert "esma_tape_normaliser" in PRIMITIVE_REGISTRY

    def test_describe_returns_non_empty_schemas(self) -> None:
        meta = EsmaTapeNormaliser.describe()
        assert meta.name == "esma_tape_normaliser"
        assert meta.version == "0.1.0"
        assert meta.input_schema, "input_schema must not be empty"
        assert meta.output_schema, "output_schema must not be empty"


# ---------------------------------------------------------------------------
# Integration test — real Green Lion April 2026 tape
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestEsmaTapeNormaliserIntegration:
    """End-to-end test against the real HuggingFace CSV.

    Asserts pool analytics values validated from the Algoritmica/green-lion-2026
    dataset (April 2026 tape). Run with ``pytest -m slow`` or include implicitly
    when no marker filter is set.
    """

    @pytest.fixture(scope="class")
    def result(self):
        """Execute the normaliser once; share result across tests in this class."""
        normaliser = EsmaTapeNormaliser()
        tape_input = EsmaTapeInput(file_url=APRIL_TAPE_URL)
        return normaliser.execute(tape_input)

    def test_loan_count_approx_3237(self, result) -> None:
        count = result.output.loan_count
        # Allow ±5% tolerance for synthetic data
        assert 3000 <= count <= 3500, f"Expected ~3237 loans, got {count}"

    def test_pool_balance_approx_1_03bn(self, result) -> None:
        bal = result.output.pool_balance_eur
        # ~€1.03 billion; allow ±10%
        assert 9e8 <= bal <= 1.2e9, f"Expected ~€1.03B, got {bal:.2e}"

    def test_current_pct_approx_99_9(self, result) -> None:
        current = result.output.arrears_breakdown.get("current_pct", 0.0)
        assert current >= 99.0, f"Expected current_pct ≥ 99.0, got {current}"

    def test_annex_detected_is_rmbs(self, result) -> None:
        assert "RMBS" in result.output.annex_detected, (
            f"Expected annex containing 'RMBS', got {result.output.annex_detected!r}"
        )

    def test_confidence_above_0_8(self, result) -> None:
        assert result.confidence > 0.8, (
            f"Expected confidence > 0.8, got {result.confidence}"
        )

    def test_citations_non_empty(self, result) -> None:
        assert len(result.citations) >= 1
        citation = result.citations[0]
        assert citation.document == APRIL_TAPE_URL
        assert "RMBS" in citation.excerpt

    def test_audit_entry_populated(self, result) -> None:
        audit = result.audit_entry
        assert audit.primitive_name == "esma_tape_normaliser"
        assert audit.version == "0.1.0"
        assert len(audit.input_hash) == 64  # SHA-256 hex
        assert audit.duration_ms > 0

    def test_pool_stats_contains_wtd_coupon(self, result) -> None:
        stats = result.output.pool_stats
        assert "wtd_coupon_pct" in stats, f"pool_stats keys: {list(stats.keys())}"
        assert 0.0 < stats["wtd_coupon_pct"] < 20.0, (
            f"wtd_coupon_pct out of plausible range: {stats['wtd_coupon_pct']}"
        )

    def test_epc_breakdown_present_for_rmbs(self, result) -> None:
        # Green Lion is RMBS → epc_label column present → breakdown must be populated
        assert result.output.epc_breakdown is not None
        assert len(result.output.epc_breakdown) > 0

    def test_arrears_breakdown_sums_to_100(self, result) -> None:
        total = sum(result.output.arrears_breakdown.values())
        # Allow small floating-point drift
        assert abs(total - 100.0) < 0.1, f"Arrears breakdown sums to {total}"

    def test_geographic_breakdown_present(self, result) -> None:
        # Green Lion has 'province' column
        assert result.output.geographic_breakdown is not None
        assert len(result.output.geographic_breakdown) > 0
