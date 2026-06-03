"""Tests for the LoanWhiz Gradio dashboard.

Covers:
1. build_pool_trend_table() — correct column count and types with mock tapes.
2. build_epc_table() — returns rows sorted lexicographically by EPC label.
3. build_covenant_table() — correct row structure with mocked CovenantMonitor.
4. create_dashboard() — returns a gradio.Blocks instance.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _make_tape(
    period: str = "2026-02-28",
    pool_balance_eur: float = 950_000_000.0,
    loan_count: int = 5000,
    current_pct: float = 99.5,
    default_pct: float = 0.1,
    wtd_ltv: float = 72.5,
    epc: dict | None = None,
) -> dict:
    """Return a minimal tape dict matching EsmaTapeOutput.model_dump() + period key."""
    return {
        "period": period,
        "reporting_date": period,
        "asset_class": "Annex 2 (RMBS)",
        "transaction_name": "Green Lion 2026-1",
        "loan_count": loan_count,
        "pool_balance_eur": pool_balance_eur,
        "pool_stats": {
            "wtd_coupon_pct": 3.5,
            "wtd_ltv": wtd_ltv,
            "wtd_seasoning": 24.0,
            "wtd_remaining_term": 240.0,
        },
        "arrears_breakdown": {
            "current_pct": current_pct,
            "arrears_1_2m_pct": 0.3,
            "arrears_180d_plus_pct": 0.1,
            "default_pct": default_pct,
        },
        "epc_breakdown": epc if epc is not None else {"A": 10.0, "B": 45.0, "C": 30.0, "D": 15.0},
        "rate_type_breakdown": {"Fixed": 60.0, "Floating": 40.0},
        "property_type_breakdown": None,
        "geographic_breakdown": None,
        "annex_detected": "Annex 2 (RMBS)",
    }


# ---------------------------------------------------------------------------
# Import the dashboard module (adjusting sys.path as app.py does)
# ---------------------------------------------------------------------------


def _import_dashboard():
    """Import clients/dashboard/app.py as a module."""
    import importlib.util
    import sys
    from pathlib import Path

    app_path = Path(__file__).parent.parent / "clients" / "dashboard" / "app.py"
    src_path = str(Path(__file__).parent.parent / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    spec = importlib.util.spec_from_file_location("dashboard_app", app_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Test 1: build_pool_trend_table returns correct columns
# ---------------------------------------------------------------------------


def test_build_pool_trend_table_columns():
    """build_pool_trend_table returns one row per tape with 6 columns."""
    mod = _import_dashboard()

    tapes = [
        _make_tape("2026-02-28", pool_balance_eur=950_000_000.0, loan_count=5000),
        _make_tape("2026-03-31", pool_balance_eur=940_000_000.0, loan_count=4950),
        _make_tape("2026-04-30", pool_balance_eur=930_000_000.0, loan_count=4900),
    ]

    rows = mod.build_pool_trend_table(tapes)

    assert len(rows) == 3, "Expected one row per tape"
    for i, row in enumerate(rows):
        assert len(row) == 6, f"Row {i} should have 6 columns, got {len(row)}"

    # Check period column
    assert rows[0][0] == "2026-02-28"
    assert rows[1][0] == "2026-03-31"
    assert rows[2][0] == "2026-04-30"

    # Pool balance formatted as EUR millions
    assert rows[0][1] == "€950.0M"

    # Loan count formatted with thousands separator
    assert rows[0][2] == "5,000"

    # WTD LTV present and formatted
    assert rows[0][5] == "72.5%"


def test_build_pool_trend_table_missing_ltv():
    """build_pool_trend_table handles missing wtd_ltv gracefully."""
    mod = _import_dashboard()

    tape = _make_tape("2026-02-28")
    tape["pool_stats"] = {}  # remove wtd_ltv

    rows = mod.build_pool_trend_table([tape])
    assert rows[0][5] == "N/A"


# ---------------------------------------------------------------------------
# Test 2: build_epc_table returns sorted rows
# ---------------------------------------------------------------------------


def test_build_epc_table_sorted():
    """build_epc_table returns rows sorted lexicographically by EPC label."""
    mod = _import_dashboard()

    tape = _make_tape(epc={"C": 30.0, "A+": 5.0, "A": 10.0, "B": 45.0, "D": 15.0})
    rows = mod.build_epc_table(tape)

    labels = [r[0] for r in rows]
    assert labels == sorted(labels), f"Rows are not sorted: {labels}"
    assert labels == ["A", "A+", "B", "C", "D"]

    # Percentage column formatted to 1 dp
    for label, pct_str in rows:
        assert pct_str.endswith("%"), f"Row for {label!r} has malformed pct: {pct_str!r}"


def test_build_epc_table_empty_epc():
    """build_epc_table returns empty list when epc_breakdown is None."""
    mod = _import_dashboard()

    tape = _make_tape()
    tape["epc_breakdown"] = None

    rows = mod.build_epc_table(tape)
    assert rows == []


# ---------------------------------------------------------------------------
# Test 3: build_covenant_table with mock CovenantMonitor output
# ---------------------------------------------------------------------------


def test_build_covenant_table_row_structure():
    """build_covenant_table returns 4-column rows with correct structure."""
    mod = _import_dashboard()

    # Build mock TriggerStatus objects
    def _trigger_status(name, period, proximity, triggered):
        s = SimpleNamespace(
            trigger_name=name,
            period=period,
            proximity_pct=proximity,
            is_triggered=triggered,
        )
        return s

    mock_statuses = [
        _trigger_status("cumulative_loss_trigger", "2026-02-28", 5.0, False),
        _trigger_status("pdl_class_a", "2026-02-28", 0.0, False),
        _trigger_status("cumulative_loss_trigger", "2026-04-30", 110.0, True),
    ]

    mock_output = SimpleNamespace(trigger_statuses=mock_statuses)
    mock_result = SimpleNamespace(output=mock_output)

    tapes = [_make_tape("2026-02-28"), _make_tape("2026-04-30")]

    with patch(
        "clients.dashboard.app.CovenantMonitor.execute",
        return_value=mock_result,
    ):
        # Import fresh so patch path resolves; call via the module directly
        rows = mod.build_covenant_table.__wrapped__(tapes) if hasattr(mod.build_covenant_table, "__wrapped__") else None

    # Patch at the module attribute level instead
    original_cls = mod.CovenantMonitor

    class _MockMonitor:
        DEFAULT_TRIGGERS = original_cls.DEFAULT_TRIGGERS

        def execute(self, input):
            return mock_result

    mod.CovenantMonitor = _MockMonitor
    try:
        rows = mod.build_covenant_table(tapes)
    finally:
        mod.CovenantMonitor = original_cls

    assert len(rows) == 3, f"Expected 3 rows, got {len(rows)}"
    for i, row in enumerate(rows):
        assert len(row) == 4, f"Row {i} should have 4 columns, got {len(row)}: {row}"

    # Triggered row should show red indicator
    triggered_row = rows[2]
    assert "TRIGGERED" in triggered_row[3], f"Triggered row status should say TRIGGERED: {triggered_row[3]}"

    # Clean row should show green or amber
    clean_row = rows[0]
    assert "OK" in clean_row[3], f"Non-triggered row status should say OK: {clean_row[3]}"

    # Period column
    assert rows[0][1] == "2026-02-28"

    # Proximity column formatted with %
    assert rows[0][2].endswith("%")


# ---------------------------------------------------------------------------
# Test 4: create_dashboard returns a gr.Blocks instance
# ---------------------------------------------------------------------------


def test_create_dashboard_returns_blocks():
    """create_dashboard() returns a gradio.Blocks object."""
    import gradio as gr

    mod = _import_dashboard()
    demo = mod.create_dashboard()

    assert isinstance(demo, gr.Blocks), (
        f"create_dashboard() should return gr.Blocks, got {type(demo)}"
    )
