"""Tests for EsmaTapeNormaliser primitive.

Two categories:
1. Unit tests for ``_detect_annex`` — no network, fast.
2. Integration test against the real Green Lion April 2026 tape on HuggingFace.
   Marked ``@pytest.mark.slow`` so CI can skip it with ``-m "not slow"`` when
   network access is unavailable.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from loanwhiz.config import GREEN_LION
from loanwhiz.data.deeploans_client import DeepLoansClient
from loanwhiz.primitives.esma_tape_normaliser import (
    EsmaTapeInput,
    EsmaTapeNormaliser,
    _detect_annex,
    _load_tape,
    non_performing_mask,
    performing_mask,
)
from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY


# ---------------------------------------------------------------------------
# Unit tests — performing / non-performing mask (shared with collections)
# ---------------------------------------------------------------------------


class TestPerformingMask:
    """The shared performing/non-performing definition used by S3."""

    @staticmethod
    def _df(rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    def test_defaulted_is_non_performing(self) -> None:
        df = self._df([{"default_crr_flag": "Y", "arrears_bucket": "Performing"}])
        assert non_performing_mask(df).tolist() == [True]
        assert performing_mask(df).tolist() == [False]

    def test_180d_arrears_is_non_performing(self) -> None:
        df = self._df([{"default_crr_flag": "N", "arrears_bucket": "180+d"}])
        assert non_performing_mask(df).tolist() == [True]

    def test_performing_and_short_arrears_are_performing(self) -> None:
        df = self._df(
            [
                {"default_crr_flag": "N", "arrears_bucket": "Performing"},
                {"default_crr_flag": "n", "arrears_bucket": "<29d"},
            ]
        )
        assert performing_mask(df).tolist() == [True, True]

    def test_missing_columns_degrade_to_all_performing(self) -> None:
        df = self._df([{"current_balance": 100.0}, {"current_balance": 200.0}])
        assert performing_mask(df).tolist() == [True, True]
        assert non_performing_mask(df).tolist() == [False, False]

    def test_mask_complement(self) -> None:
        df = self._df(
            [
                {"default_crr_flag": "Y", "arrears_bucket": "Performing"},
                {"default_crr_flag": "N", "arrears_bucket": "180+d"},
                {"default_crr_flag": "N", "arrears_bucket": "Performing"},
            ]
        )
        assert (performing_mask(df) == ~non_performing_mask(df)).all()

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
# Unit tests — format-agnostic loading (CSV or parquet) and per-period split
#
# All fixtures are tiny synthetic frames written to ``tmp_path`` — no network.
# ---------------------------------------------------------------------------


def _rmbs_frame(reporting_date: str, *, balances: list[float]) -> pd.DataFrame:
    """Return a minimal synthetic RMBS (Annex 2) tape with the given balances."""
    n = len(balances)
    return pd.DataFrame(
        {
            "current_balance": balances,
            "current_interest_rate_pct": [3.5] * n,
            "cltomv_current": [70.0] * n,
            "seasoning_months": [24] * n,
            "remaining_term_months": [300] * n,
            "epc_label": ["A", "B", "C", "D"][:n],
            "property_type": ["House", "Apartment", "House", "Apartment"][:n],
            "arrears_bucket": ["Performing"] * n,
            "default_crr_flag": ["N"] * n,
            "province": ["NL-NH", "NL-ZH", "NL-NH", "NL-ZH"][:n],
            "transaction_name": ["Synthetic Deal"] * n,
            "reporting_date": [reporting_date] * n,
        }
    )


class TestFormatAgnosticLoading:
    """``_load_tape`` and ``execute`` accept both CSV and parquet URLs."""

    def test_single_period_parquet_normalises(self, tmp_path: Path) -> None:
        df = _rmbs_frame("2026-04-30", balances=[100_000.0, 200_000.0])
        parquet_path = tmp_path / "single_period.parquet"
        df.to_parquet(parquet_path)

        result = EsmaTapeNormaliser().execute(
            EsmaTapeInput(file_url=f"file://{parquet_path}")
        )

        assert result.output.loan_count == 2
        assert result.output.reporting_date == "2026-04-30"
        assert result.output.pool_balance_eur == pytest.approx(300_000.0)
        assert result.output.annex_detected == "Annex 2 (RMBS)"
        assert result.output.transaction_name == "Synthetic Deal"

    def test_csv_path_still_works(self, tmp_path: Path) -> None:
        # Regression guard: the historical CSV path is unchanged.
        df = _rmbs_frame("2026-04-30", balances=[100_000.0, 200_000.0])
        csv_path = tmp_path / "tape.csv"
        df.to_csv(csv_path, index=False)

        result = EsmaTapeNormaliser().execute(
            EsmaTapeInput(file_url=f"file://{csv_path}")
        )

        assert result.output.loan_count == 2
        assert result.output.reporting_date == "2026-04-30"
        assert result.output.pool_balance_eur == pytest.approx(300_000.0)
        assert result.output.annex_detected == "Annex 2 (RMBS)"

    def test_csv_and_parquet_produce_identical_output(self, tmp_path: Path) -> None:
        # The format is purely an ingestion detail; analytics must match.
        df = _rmbs_frame("2026-04-30", balances=[150_000.0, 250_000.0, 350_000.0])
        csv_path = tmp_path / "tape.csv"
        parquet_path = tmp_path / "tape.parquet"
        df.to_csv(csv_path, index=False)
        df.to_parquet(parquet_path)

        csv_out = EsmaTapeNormaliser().execute(
            EsmaTapeInput(file_url=f"file://{csv_path}")
        ).output
        parquet_out = EsmaTapeNormaliser().execute(
            EsmaTapeInput(file_url=f"file://{parquet_path}")
        ).output

        assert csv_out.loan_count == parquet_out.loan_count
        assert csv_out.pool_balance_eur == pytest.approx(parquet_out.pool_balance_eur)
        assert csv_out.pool_stats == parquet_out.pool_stats
        assert csv_out.arrears_breakdown == parquet_out.arrears_breakdown

    def test_query_string_routes_to_parquet_reader(self, tmp_path: Path) -> None:
        # A signed-URL-style query suffix must not defeat extension detection.
        df = _rmbs_frame("2026-04-30", balances=[100_000.0])
        parquet_path = tmp_path / "signed.parquet"
        df.to_parquet(parquet_path)

        loaded, data_source = _load_tape(f"file://{parquet_path}?token=abc123", period=None)
        assert len(loaded) == 1
        assert data_source == "direct"


class TestCombinedParquetSplit:
    """A combined multi-month parquet is split by ``reporting_date``."""

    def _combined_path(self, tmp_path: Path) -> Path:
        jan = _rmbs_frame("2024-01-31", balances=[100_000.0, 200_000.0])
        feb = _rmbs_frame("2024-02-29", balances=[300_000.0, 400_000.0])
        mar = _rmbs_frame("2024-03-31", balances=[500_000.0])
        combined = pd.concat([jan, feb, mar], ignore_index=True)
        path = tmp_path / "Overall_2024_all_months.parquet"
        combined.to_parquet(path)
        return path

    def test_period_selects_single_month_slice(self, tmp_path: Path) -> None:
        path = self._combined_path(tmp_path)

        result = EsmaTapeNormaliser().execute(
            EsmaTapeInput(file_url=f"file://{path}", period="2024-02-29")
        )

        # Only the February slice (2 loans, 300k + 400k) participates.
        assert result.output.loan_count == 2
        assert result.output.reporting_date == "2024-02-29"
        assert result.output.pool_balance_eur == pytest.approx(700_000.0)

    def test_each_period_is_independently_addressable(self, tmp_path: Path) -> None:
        path = self._combined_path(tmp_path)
        url = f"file://{path}"

        jan = EsmaTapeNormaliser().execute(
            EsmaTapeInput(file_url=url, period="2024-01-31")
        ).output
        mar = EsmaTapeNormaliser().execute(
            EsmaTapeInput(file_url=url, period="2024-03-31")
        ).output

        assert jan.loan_count == 2
        assert jan.pool_balance_eur == pytest.approx(300_000.0)
        assert mar.loan_count == 1
        assert mar.pool_balance_eur == pytest.approx(500_000.0)

    def test_load_tape_slices_frame_directly(self, tmp_path: Path) -> None:
        path = self._combined_path(tmp_path)

        sliced, data_source = _load_tape(f"file://{path}", period="2024-01-31")

        assert len(sliced) == 2
        assert set(sliced["reporting_date"].astype(str)) == {"2024-01-31"}
        assert data_source == "direct"

    def test_absent_period_raises_value_error(self, tmp_path: Path) -> None:
        path = self._combined_path(tmp_path)

        with pytest.raises(ValueError, match="matched no rows"):
            EsmaTapeNormaliser().execute(
                EsmaTapeInput(file_url=f"file://{path}", period="2099-12-31")
            )

    def test_no_period_reads_whole_combined_file(self, tmp_path: Path) -> None:
        # Backward-compatible path: unset period reads every row.
        path = self._combined_path(tmp_path)

        result = EsmaTapeNormaliser().execute(EsmaTapeInput(file_url=f"file://{path}"))

        assert result.output.loan_count == 5  # 2 + 2 + 1 across all months


class TestDeeploansRouting:
    """``_load_tape`` routes ``deeploans://`` refs through the deeploans backend.

    All tests mock the ``DeepLoansClient.fetch_tape`` boundary, so they run with
    **no live deeploans backend** — the demo/CI environment requirement.
    """

    def test_direct_url_is_tagged_direct(self, tmp_path: Path) -> None:
        df = _rmbs_frame("2026-04-30", balances=[100_000.0, 200_000.0])
        csv_path = tmp_path / "tape.csv"
        df.to_csv(csv_path, index=False)

        result = EsmaTapeNormaliser().execute(
            EsmaTapeInput(file_url=f"file://{csv_path}")
        )
        assert result.output.data_source == "direct"

    def test_deeploans_ref_routes_through_client(self) -> None:
        tape = _rmbs_frame("2026-04-30", balances=[100_000.0, 200_000.0])

        with patch.object(
            DeepLoansClient, "fetch_tape", return_value=tape
        ) as mock_fetch:
            result = EsmaTapeNormaliser().execute(
                EsmaTapeInput(file_url="deeploans://sme/green_lion_loans")
            )

        mock_fetch.assert_called_once_with("sme", "green_lion_loans")
        assert result.output.data_source == "deeploans"
        assert result.output.loan_count == 2
        assert result.output.pool_balance_eur == pytest.approx(300_000.0)

    def test_deeploans_ref_propagates_into_load_tape(self) -> None:
        tape = _rmbs_frame("2026-04-30", balances=[150_000.0])
        with patch.object(DeepLoansClient, "fetch_tape", return_value=tape):
            df, data_source = _load_tape("deeploans://sme/loans", period=None)
        assert data_source == "deeploans"
        assert len(df) == 1

    def test_unreachable_deeploans_backend_raises(self) -> None:
        # fetch_tape returns None when the backend is unreachable; the loader
        # must raise a clear error rather than silently mis-reading the literal
        # "deeploans://" string as a file path.
        with patch.object(DeepLoansClient, "fetch_tape", return_value=None):
            with pytest.raises(RuntimeError, match="no deeploans backend is reachable"):
                _load_tape("deeploans://sme/loans", period=None)

    def test_deeploans_ref_supports_period_slicing(self) -> None:
        jan = _rmbs_frame("2024-01-31", balances=[100_000.0, 200_000.0])
        feb = _rmbs_frame("2024-02-29", balances=[300_000.0])
        combined = pd.concat([jan, feb], ignore_index=True)

        with patch.object(DeepLoansClient, "fetch_tape", return_value=combined):
            result = EsmaTapeNormaliser().execute(
                EsmaTapeInput(
                    file_url="deeploans://sme/loans", period="2024-02-29"
                )
            )

        assert result.output.data_source == "deeploans"
        assert result.output.loan_count == 1
        assert result.output.pool_balance_eur == pytest.approx(300_000.0)


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


# ---------------------------------------------------------------------------
# ESMA Annex 2 canonical-column resolution + citation anchoring (#280)
# ---------------------------------------------------------------------------


class TestAnnex2ColumnResolution:
    """Issuer/vintage column-name synonyms resolve onto canonical names via the
    ESMA Annex 2 table, and the output citation is anchored to RREL codes."""

    def test_canonical_green_lion_tape_is_byte_stable(self, tmp_path: Path) -> None:
        # A tape already in canonical column names must produce the exact same
        # analytics it did before Annex-2 resolution was wired in.
        df = _rmbs_frame("2026-04-30", balances=[100_000.0, 300_000.0])
        csv = tmp_path / "canon.csv"
        df.to_csv(csv, index=False)
        out = EsmaTapeNormaliser().execute(
            EsmaTapeInput(file_url=f"file://{csv}")
        ).output
        assert out.pool_balance_eur == 400_000.0
        assert out.pool_stats["wtd_ltv"] == 70.0
        assert out.arrears_breakdown["current_pct"] == 100.0
        assert out.annex_detected == "Annex 2 (RMBS)"

    def test_synonym_columns_resolve(self, tmp_path: Path) -> None:
        # A tape using issuer-variant column names still produces correct pool
        # analytics because the Annex 2 table resolves them to canonical names.
        df = pd.DataFrame(
            {
                "outstanding_balance": [100_000.0, 300_000.0],  # → current_balance
                "current_ltv": [60.0, 80.0],                    # → cltomv_current
                "arrears_status": ["Performing", "180+d"],      # → arrears_bucket
                "default_flag": ["N", "N"],                     # → default_crr_flag
                "epc_rating": ["A", "B"],                       # → epc_label
                "property_type": ["House", "Apartment"],
                "deal_name": ["Variant Deal", "Variant Deal"],  # → transaction_name
                "data_cut_off_date": ["2026-04-30", "2026-04-30"],  # → reporting_date
            }
        )
        csv = tmp_path / "variant.csv"
        df.to_csv(csv, index=False)
        out = EsmaTapeNormaliser().execute(
            EsmaTapeInput(file_url=f"file://{csv}", reporting_date="2026-04-30")
        ).output
        # Balance resolved from the synonym column.
        assert out.pool_balance_eur == 400_000.0
        # Balance-weighted LTV: (100k*60 + 300k*80)/400k = 75.0
        assert out.pool_stats["wtd_ltv"] == 75.0
        # 180+d arrears bucket resolved from the synonym column.
        assert out.arrears_breakdown["arrears_180d_plus_pct"] == 50.0

    def test_citation_is_annex2_anchored(self, tmp_path: Path) -> None:
        df = _rmbs_frame("2026-04-30", balances=[100_000.0])
        csv = tmp_path / "anchor.csv"
        df.to_csv(csv, index=False)
        result = EsmaTapeNormaliser().execute(EsmaTapeInput(file_url=f"file://{csv}"))
        citation = result.citations[0]
        # RREL codes for the mapped columns appear in the locator + excerpt.
        assert "RREL" in citation.page_or_row
        assert "RREL18" in citation.excerpt  # current_balance
        assert "RREL40" in citation.excerpt  # cltomv_current (LTV)
