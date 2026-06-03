"""Tests for the LoanWhiz Gradio compliance view.

Covers:
1. run_covenant_compliance() — returns (rows, summary) with 6-column rows when
   the EsmaTapeNormaliser and CovenantMonitor primitives are mocked (no network).
2. build_covenant_rows() — status icon logic (breach / near-miss / OK).
3. run_report_verification() — graceful failure path: when the report verifier
   raises (no Vertex AI), returns one explanatory row + an "unavailable" summary.
4. build_report_rows() — match / mismatch row structure.
5. create_compliance_view() — returns a gradio.Blocks instance.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


# ---------------------------------------------------------------------------
# Import the compliance app module (adjusting sys.path as app.py does)
# ---------------------------------------------------------------------------


def _import_compliance():
    """Import clients/compliance/app.py as a module."""
    app_path = Path(__file__).parent.parent / "clients" / "compliance" / "app.py"
    src_path = str(Path(__file__).parent.parent / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    spec = importlib.util.spec_from_file_location("compliance_app", app_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _trigger_status(name, period, metric, threshold, proximity, triggered):
    """Build a TriggerStatus-like object for the covenant table."""
    return SimpleNamespace(
        trigger_name=name,
        period=period,
        metric_value=metric,
        threshold=threshold,
        proximity_pct=proximity,
        is_triggered=triggered,
    )


def _reported_figure(line_item, computed, reported, delta, delta_pct, match):
    """Build a ReportedFigure-like object for the report table."""
    return SimpleNamespace(
        line_item=line_item,
        computed_value=computed,
        reported_value=reported,
        delta=delta,
        delta_pct=delta_pct,
        match=match,
    )


# ---------------------------------------------------------------------------
# Test 1: run_covenant_compliance with mocked primitives
# ---------------------------------------------------------------------------


def test_run_covenant_compliance_with_mocked_primitives():
    """run_covenant_compliance returns (rows, summary) with 6-column rows."""
    mod = _import_compliance()

    mock_statuses = [
        _trigger_status("cumulative_loss_trigger", "2026-02-28", 0.05, 1.5, 3.3, False),
        _trigger_status("reserve_fund_trigger", "2026-03-31", 95.0, 100.0, 105.0, False),
        _trigger_status("cumulative_loss_trigger", "2026-04-30", 2.0, 1.5, 133.0, True),
    ]
    mock_output = SimpleNamespace(
        trigger_statuses=mock_statuses,
        summary="Covenant compliance across 3 reporting periods — BREACHED: 'cumulative_loss_trigger'.",
    )
    mock_result = SimpleNamespace(output=mock_output)

    original_monitor = mod.CovenantMonitor
    original_normaliser = mod.EsmaTapeNormaliser

    class _MockMonitor:
        DEFAULT_TRIGGERS = original_monitor.DEFAULT_TRIGGERS

        def execute(self, input):
            return mock_result

    class _MockNormaliser:
        def execute(self, input):
            # Return a minimal output whose model_dump() is a dict.
            out = SimpleNamespace(model_dump=lambda: {"reporting_date": "2026-02-28"})
            return SimpleNamespace(output=out)

    mod.CovenantMonitor = _MockMonitor
    mod.EsmaTapeNormaliser = _MockNormaliser
    try:
        rows, summary = mod.run_covenant_compliance()
    finally:
        mod.CovenantMonitor = original_monitor
        mod.EsmaTapeNormaliser = original_normaliser

    assert len(rows) == 3, f"Expected 3 rows, got {len(rows)}"
    for i, row in enumerate(rows):
        assert len(row) == 6, f"Row {i} should have 6 columns, got {len(row)}: {row}"

    # Breached row shows red BREACH.
    assert "🔴" in rows[2][5] and "BREACH" in rows[2][5]
    # Non-triggered rows show OK.
    assert "OK" in rows[0][5]

    # Summary is the monitor's summary string, passed through.
    assert "Covenant compliance" in summary


# ---------------------------------------------------------------------------
# Test 2: build_covenant_rows status icon logic
# ---------------------------------------------------------------------------


def test_build_covenant_rows_status_icons():
    """build_covenant_rows picks 🔴 breach, 🟡 near-miss, 🟢 ok correctly."""
    mod = _import_compliance()

    statuses = [
        _trigger_status("breach", "2026-04-30", 2.0, 1.5, 133.0, True),
        _trigger_status("near_miss", "2026-04-30", 1.4, 1.5, 90.0, False),
        _trigger_status("ok", "2026-04-30", 0.1, 1.5, 6.6, False),
        _trigger_status("no_threshold", "2026-04-30", 0.0, None, 0.0, False),
    ]
    rows = mod.build_covenant_rows(statuses)

    assert "🔴" in rows[0][5]
    assert "🟡" in rows[1][5]
    assert "🟢" in rows[2][5]
    # None threshold renders as N/A and is not a breach.
    assert rows[3][3] == "N/A"
    assert "🟢" in rows[3][5]
    # Proximity column formatted with %.
    assert rows[0][4].endswith("%")


# ---------------------------------------------------------------------------
# Test 3: run_report_verification graceful failure path
# ---------------------------------------------------------------------------


def test_run_report_verification_graceful_failure():
    """run_report_verification returns an explanatory row when verifier raises."""
    mod = _import_compliance()

    original_runner = mod.WaterfallRunner

    class _RaisingRunner:
        def execute(self, input):
            # Simulate the report-verification path being unavailable
            # (e.g. no Vertex AI credentials downstream). Raising here
            # exercises the try/except graceful-degradation contract.
            raise RuntimeError("Vertex AI credentials not configured")

    mod.WaterfallRunner = _RaisingRunner
    try:
        rows, summary = mod.run_report_verification()
    finally:
        mod.WaterfallRunner = original_runner

    assert len(rows) == 1, f"Expected exactly one explanatory row, got {len(rows)}"
    assert len(rows[0]) == 6, f"Explanatory row should have 6 columns: {rows[0]}"
    assert "Vertex AI" in rows[0][0]
    assert "unavailable" in summary.lower()


# ---------------------------------------------------------------------------
# Test 4: build_report_rows match / mismatch structure
# ---------------------------------------------------------------------------


def test_build_report_rows_match_mismatch():
    """build_report_rows renders 🟢 MATCH and 🔴 MISMATCH rows."""
    mod = _import_compliance()

    figures = [
        _reported_figure("class_a_interest_paid", 9_050_000.0, 9_050_000.0, 0.0, 0.0, True),
        _reported_figure("reserve_fund_balance", 10_636_000.0, 5_000_000.0, -5_636_000.0, -53.0, False),
    ]
    rows = mod.build_report_rows(figures)

    assert len(rows) == 2
    for row in rows:
        assert len(row) == 6
    assert "🟢" in rows[0][5] and "MATCH" in rows[0][5]
    assert "🔴" in rows[1][5] and "MISMATCH" in rows[1][5]
    # Delta % column formatted with %.
    assert rows[0][4].endswith("%")


# ---------------------------------------------------------------------------
# Test 5: create_compliance_view returns a gr.Blocks instance
# ---------------------------------------------------------------------------


def test_create_compliance_view_returns_blocks():
    """create_compliance_view() returns a gradio.Blocks object."""
    import gradio as gr

    mod = _import_compliance()
    demo = mod.create_compliance_view()

    assert isinstance(demo, gr.Blocks), (
        f"create_compliance_view() should return gr.Blocks, got {type(demo)}"
    )
