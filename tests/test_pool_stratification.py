"""Tests for the PoolStratification primitive (issue #325).

Categories:
1. Pure-function unit tests for bucketing, stratification, concentration
   checks, and migration — no network, fast.
2. Public-surface / registry presence tests.
3. An integration test against the real Green Lion April 2026 tape on
   HuggingFace, marked ``@pytest.mark.slow`` so CI can deselect it with
   ``-m "not slow"`` when network access is unavailable.

The unit tests stub the tape loader (``_load_tape``) so the real stratification
code path runs against synthetic in-memory frames — the loader is the only
genuine I/O boundary and is covered live by the slow test below and by the
normaliser's own suite.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from loanwhiz.primitives import (
    ConcentrationCheck,
    ConcentrationLimit,
    PoolStratification,
    PoolStratificationInput,
    PoolStratificationOutput,
    StratumCell,
)
from loanwhiz.primitives.pool_stratification import (
    UNAVAILABLE_BUCKET,
    _bin_numeric,
    _bucket_labels,
    _classify,
    _marginal_shares,
    _stratify,
)
from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY

_LOAD_TAPE = "loanwhiz.primitives.pool_stratification._load_tape"


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 1a. Bucketing — numeric binning
# ---------------------------------------------------------------------------


class TestBucketing:
    def test_labels_are_ordered_and_half_open(self) -> None:
        labels = _bucket_labels((60.0, 70.0, 80.0))
        assert labels == ["<60", "60-70", "70-80", "80+"]

    def test_labels_drop_trailing_zero_for_whole_numbers(self) -> None:
        # 90.0 must render as "90", not "90.0".
        assert _bucket_labels((90.0,)) == ["<90", "90+"]

    def test_bin_numeric_assigns_half_open_bins(self) -> None:
        s = pd.Series([55.0, 60.0, 65.0, 80.0, 95.0])
        out = _bin_numeric(s, (60.0, 70.0, 80.0))
        # 60 falls in [60,70) not (<60); 80 falls in [80,+inf).
        assert out.tolist() == ["<60", "60-70", "60-70", "80+", "80+"]

    def test_bin_numeric_missing_maps_to_unavailable(self) -> None:
        s = pd.Series([55.0, None, float("nan")])
        out = _bin_numeric(s, (60.0,))
        assert out.tolist() == ["<60", UNAVAILABLE_BUCKET, UNAVAILABLE_BUCKET]


# ---------------------------------------------------------------------------
# 1b. Stratification core — multi-dimensional grouping
# ---------------------------------------------------------------------------


class TestStratify:
    def _df(self) -> pd.DataFrame:
        return _frame(
            [
                # LTV bucket, rate type, balance
                {"cltomv_current": 65.0, "rate_type": "Fixed", "current_balance": 100.0},
                {"cltomv_current": 66.0, "rate_type": "Fixed", "current_balance": 100.0},
                {"cltomv_current": 95.0, "rate_type": "Floating", "current_balance": 200.0},
            ]
        )

    def test_count_and_balance_per_cell(self) -> None:
        df = self._df().rename(columns=str.lower)
        inp = PoolStratificationInput(file_url="x", dimensions=["ltv", "rate_type"])
        cells, unavailable = _stratify(df, ["ltv", "rate_type"], inp)
        assert unavailable == []
        # Two occupied cells: (60-70, Fixed) x2 and (90-100, Floating) x1.
        by_key = {(c.key["ltv"], c.key["rate_type"]): c for c in cells}
        assert by_key[("60-70", "Fixed")].loan_count == 2
        assert by_key[("60-70", "Fixed")].balance_eur == 200.0
        assert by_key[("90-100", "Floating")].loan_count == 1

    def test_balance_pct_sums_to_100(self) -> None:
        df = self._df().rename(columns=str.lower)
        inp = PoolStratificationInput(file_url="x", dimensions=["ltv"])
        cells, _ = _stratify(df, ["ltv"], inp)
        total = sum(c.balance_pct for c in cells)
        assert round(total, 2) == 100.0
        # 200 of 400 EUR is in the Floating/95 LTV cell → 50%.
        assert any(round(c.balance_pct, 1) == 50.0 for c in cells)

    def test_count_pct_sums_to_100(self) -> None:
        df = self._df().rename(columns=str.lower)
        inp = PoolStratificationInput(file_url="x", dimensions=["ltv", "rate_type"])
        cells, _ = _stratify(df, ["ltv", "rate_type"], inp)
        assert round(sum(c.count_pct for c in cells), 2) == 100.0

    def test_missing_dimension_degrades_to_unavailable(self) -> None:
        # No "province" column → region collapses to the unavailable bucket.
        df = self._df().rename(columns=str.lower)
        inp = PoolStratificationInput(file_url="x", dimensions=["region"])
        cells, unavailable = _stratify(df, ["region"], inp)
        assert unavailable == ["region"]
        assert len(cells) == 1
        assert cells[0].key["region"] == UNAVAILABLE_BUCKET
        assert cells[0].loan_count == 3

    def test_empty_frame_yields_no_cells(self) -> None:
        df = _frame([]).rename(columns=str.lower)
        inp = PoolStratificationInput(file_url="x", dimensions=["ltv"])
        cells, _ = _stratify(df, ["ltv"], inp)
        assert cells == []


# ---------------------------------------------------------------------------
# 1c. Concentration checks
# ---------------------------------------------------------------------------


class TestConcentrationChecks:
    def test_classify_within_near_breach(self) -> None:
        assert _classify(observed=10.0, limit=50.0) == "within"
        assert _classify(observed=46.0, limit=50.0) == "near"  # >= 45 (0.9*50)
        assert _classify(observed=60.0, limit=50.0) == "breach"

    def test_breach_flagged_on_balance_basis(self) -> None:
        df = _frame(
            [
                {"rate_type": "Floating", "current_balance": 700.0},
                {"rate_type": "Fixed", "current_balance": 300.0},
            ]
        ).rename(columns=str.lower)
        inp = PoolStratificationInput(
            file_url="x",
            dimensions=["rate_type"],
            concentration_limits=[
                ConcentrationLimit(
                    dimension="rate_type", bucket="Floating", max_pct=50.0, basis="balance"
                )
            ],
        )
        with patch(_LOAD_TAPE, return_value=(df, "direct")):
            result = PoolStratification().execute(inp)
        check = result.output.concentration_checks[0]
        assert check.observed_pct == 70.0  # 700 / 1000
        assert check.status == "breach"

    def test_count_basis_within(self) -> None:
        df = _frame(
            [
                {"province": "ES30", "current_balance": 100.0},
                {"province": "ES30", "current_balance": 100.0},
                {"province": "ES51", "current_balance": 100.0},
                {"province": "ES51", "current_balance": 100.0},
            ]
        ).rename(columns=str.lower)
        inp = PoolStratificationInput(
            file_url="x",
            dimensions=["region"],
            concentration_limits=[
                ConcentrationLimit(
                    dimension="region", bucket="ES30", max_pct=80.0, basis="count"
                )
            ],
        )
        with patch(_LOAD_TAPE, return_value=(df, "direct")):
            result = PoolStratification().execute(inp)
        check = result.output.concentration_checks[0]
        assert check.observed_pct == 50.0  # 2 of 4 loans
        assert check.status == "within"

    def test_absent_bucket_observed_zero(self) -> None:
        df = _frame([{"rate_type": "Fixed", "current_balance": 100.0}]).rename(
            columns=str.lower
        )
        inp = PoolStratificationInput(file_url="x", dimensions=["rate_type"])
        check = _check_only(
            df,
            ConcentrationLimit(
                dimension="rate_type", bucket="Floating", max_pct=50.0
            ),
        )
        assert check.observed_pct == 0.0
        assert check.status == "within"


def _check_only(df: pd.DataFrame, limit: ConcentrationLimit) -> ConcentrationCheck:
    inp = PoolStratificationInput(
        file_url="x", dimensions=[limit.dimension], concentration_limits=[limit]
    )
    with patch(_LOAD_TAPE, return_value=(df, "direct")):
        return PoolStratification().execute(inp).output.concentration_checks[0]


# ---------------------------------------------------------------------------
# 1d. Migration across periods
# ---------------------------------------------------------------------------


class TestMigration:
    def test_migration_emits_per_bucket_deltas(self) -> None:
        period_a = _frame(
            [
                {"rate_type": "Fixed", "current_balance": 100.0},
                {"rate_type": "Fixed", "current_balance": 100.0},
            ]
        ).rename(columns=str.lower)
        period_b = _frame(
            [
                {"rate_type": "Fixed", "current_balance": 100.0},
                {"rate_type": "Floating", "current_balance": 100.0},
            ]
        ).rename(columns=str.lower)
        inp = PoolStratificationInput(
            file_url="x",
            period="2026-01",
            period_compare="2026-04",
            dimensions=["rate_type"],
        )

        def fake_load(_url: str, period: str | None):
            return (period_a if period == "2026-01" else period_b), "direct"

        with patch(_LOAD_TAPE, side_effect=fake_load):
            result = PoolStratification().execute(inp)

        assert result.output.migration is not None
        by_bucket = {m.bucket: m for m in result.output.migration}
        # Fixed went 100% → 50% balance share.
        assert by_bucket["Fixed"].balance_pct_a == 100.0
        assert by_bucket["Fixed"].balance_pct_b == 50.0
        assert by_bucket["Fixed"].balance_pct_delta == -50.0
        # Floating appeared (absent in A → 50% in B).
        assert by_bucket["Floating"].balance_pct_a == 0.0
        assert by_bucket["Floating"].balance_pct_b == 50.0
        assert by_bucket["Floating"].count_a == 0
        assert by_bucket["Floating"].count_b == 1

    def test_no_migration_without_period_compare(self) -> None:
        df = _frame([{"rate_type": "Fixed", "current_balance": 100.0}]).rename(
            columns=str.lower
        )
        inp = PoolStratificationInput(file_url="x", dimensions=["rate_type"])
        with patch(_LOAD_TAPE, return_value=(df, "direct")):
            result = PoolStratification().execute(inp)
        assert result.output.migration is None


# ---------------------------------------------------------------------------
# 1e. End-to-end primitive shape (governed result)
# ---------------------------------------------------------------------------


class TestPrimitiveResult:
    def test_result_is_governed(self) -> None:
        df = _frame(
            [
                {
                    "cltomv_current": 65.0,
                    "seasoning_months": 30.0,
                    "province": "ES30",
                    "rate_type": "Fixed",
                    "current_balance": 100.0,
                }
            ]
        ).rename(columns=str.lower)
        inp = PoolStratificationInput(file_url="tape.csv")  # all four dimensions
        with patch(_LOAD_TAPE, return_value=(df, "direct")):
            result = PoolStratification().execute(inp)
        out = result.output
        assert isinstance(out, PoolStratificationOutput)
        assert out.dimensions == ["ltv", "seasoning", "region", "rate_type"]
        assert out.total_loans == 1
        assert out.total_balance_eur == 100.0
        assert isinstance(out.strata[0], StratumCell)
        # Governance triplet present.
        assert 0.0 <= result.confidence <= 1.0
        assert result.confidence == 1.0  # all dimensions available, balance present
        assert len(result.citations) == 1
        assert result.audit_entry.primitive_name == "pool_stratification"

    def test_confidence_dents_on_unavailable_dimension(self) -> None:
        # No "province" column → region unavailable → -0.1 confidence.
        df = _frame(
            [{"cltomv_current": 65.0, "current_balance": 100.0}]
        ).rename(columns=str.lower)
        inp = PoolStratificationInput(file_url="x", dimensions=["ltv", "region"])
        with patch(_LOAD_TAPE, return_value=(df, "direct")):
            result = PoolStratification().execute(inp)
        assert result.output.unavailable_dimensions == ["region"]
        assert result.confidence == 0.9

    def test_missing_balance_column_dents_confidence(self) -> None:
        df = _frame([{"rate_type": "Fixed"}]).rename(columns=str.lower)
        inp = PoolStratificationInput(file_url="x", dimensions=["rate_type"])
        with patch(_LOAD_TAPE, return_value=(df, "direct")):
            result = PoolStratification().execute(inp)
        # current_balance absent → -0.1; rate_type present so no dim deduction.
        assert result.confidence == 0.9
        assert result.output.total_balance_eur == 0.0


# ---------------------------------------------------------------------------
# 2. Public surface / registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_registered_under_pool_stratification(self) -> None:
        reg = PRIMITIVE_REGISTRY.get("pool_stratification")
        assert reg is not None
        assert reg.primitive_class is PoolStratification

    def test_exported_from_package(self) -> None:
        from loanwhiz import primitives

        assert "PoolStratification" in primitives.__all__
        assert primitives.PoolStratification is PoolStratification


# ---------------------------------------------------------------------------
# 1f. Custom edges
# ---------------------------------------------------------------------------


def test_custom_ltv_edges_override_defaults() -> None:
    df = _frame(
        [
            {"cltomv_current": 25.0, "current_balance": 100.0},
            {"cltomv_current": 75.0, "current_balance": 100.0},
        ]
    ).rename(columns=str.lower)
    inp = PoolStratificationInput(
        file_url="x", dimensions=["ltv"], ltv_edges=[50.0]
    )
    with patch(_LOAD_TAPE, return_value=(df, "direct")):
        result = PoolStratification().execute(inp)
    buckets = {c.key["ltv"] for c in result.output.strata}
    assert buckets == {"<50", "50+"}


def test_marginal_shares_helper() -> None:
    df = _frame(
        [
            {"rate_type": "Fixed", "current_balance": 300.0},
            {"rate_type": "Floating", "current_balance": 100.0},
        ]
    ).rename(columns=str.lower)
    inp = PoolStratificationInput(file_url="x", dimensions=["rate_type"])
    marg = _marginal_shares(df, "rate_type", inp)
    assert marg["Fixed"] == (1, 75.0)
    assert marg["Floating"] == (1, 25.0)


# ---------------------------------------------------------------------------
# 3. Integration — real tape (slow, network)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_real_green_lion_tape_stratifies() -> None:
    """Stratify the real Green Lion April 2026 tape across all four dimensions."""
    from loanwhiz.config import GREEN_LION

    tape_url = GREEN_LION["tape_urls"][-1]["url"]  # Apr 2026 cut-off
    inp = PoolStratificationInput(
        file_url=tape_url,
        concentration_limits=[
            ConcentrationLimit(
                dimension="ltv", bucket="90-100", max_pct=25.0, basis="balance"
            )
        ],
    )
    result = PoolStratification().execute(inp)
    out = result.output
    assert out.total_loans > 0
    assert out.total_balance_eur > 0
    assert len(out.strata) > 0
    # Each cell carries a key for every active dimension.
    for cell in out.strata:
        assert set(cell.key) == set(out.dimensions)
    # The single supplied concentration rule produced a check.
    assert len(out.concentration_checks) == 1
