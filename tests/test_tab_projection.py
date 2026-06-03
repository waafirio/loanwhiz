"""Offline tests for the Cashflow Projection demo tab.

Target: ``clients/demo/tabs/projection.py``. These never launch the UI or hit
the network. They verify the tab-plugin contract surface (``render(state)``
builds inside a Blocks context) and the live projection handler, with
:class:`CashflowProjector` monkeypatched so no waterfall is actually run.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import gradio as gr
import pandas as pd


# ---------------------------------------------------------------------------
# Import the tab module by path (mirrors tests/test_tab_waterfall.py).
# ---------------------------------------------------------------------------


def _import_projection():
    """Import ``clients/demo/tabs/projection.py`` as a module."""
    repo_root = Path(__file__).resolve().parent.parent
    for p in (str(repo_root), str(repo_root / "src")):
        if p not in sys.path:
            sys.path.insert(0, p)

    mod_path = repo_root / "clients" / "demo" / "tabs" / "projection.py"
    spec = importlib.util.spec_from_file_location("demo_tab_projection", mod_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


projection = _import_projection()


# ---------------------------------------------------------------------------
# Mock DealState + mock projector output
# ---------------------------------------------------------------------------


class _MockDealState:
    """Minimal stand-in for the shell's ``DealState``."""

    def __init__(self, loaded=True, tapes=None):
        self.loaded = loaded
        self.tapes = tapes if tapes is not None else []


def _green_lion_tapes():
    """A single tape carrying a current pool balance."""
    return [
        {"period": "2026-02-28", "pool_balance_eur": 1_042_490_000.0, "loan_count": 99},
    ]


class _Result:
    def __init__(self, output):
        self.output = output


def _make_scenario(name, *, breach=False, wal_months=36.0):
    """Build a fake ``ScenarioProjection``-shaped object with 12 periods."""
    from loanwhiz.primitives.cashflow_projector import (
        PeriodProjection,
        ScenarioAssumptions,
        ScenarioProjection,
    )

    periods = []
    for t in range(1, 13):
        # When breach=True, cumulative losses cross the €10.6M reserve at
        # month 3 (5M per month); otherwise losses stay well below it.
        cum_losses = t * 5_000_000.0 if breach else t * 100_000.0
        periods.append(
            PeriodProjection(
                period=t,
                pool_balance_eur=1_000_000_000.0 - t * 10_000_000.0,
                class_a_distribution=20_000_000.0,
                class_b_distribution=1_000_000.0,
                class_c_distribution=200_000.0,
                cumulative_losses=cum_losses,
                reserve_fund_balance=projection._RESERVE_FUND_BALANCE,
            )
        )
    return ScenarioProjection(
        scenario=ScenarioAssumptions(name=name, description=f"{name} case"),
        periods=periods,
        total_class_a=240_000_000.0,
        total_class_b=12_000_000.0,
        wal_class_a_months=wal_months,
    )


def _patch_projector(monkeypatch, *, stress_breach=True):
    """Patch ``CashflowProjector`` so the handler runs offline (no waterfall)."""
    from loanwhiz.primitives.cashflow_projector import CashflowProjectorOutput

    base = _make_scenario("base", breach=False, wal_months=36.0)
    stress = _make_scenario("stress", breach=stress_breach, wal_months=48.0)
    output = CashflowProjectorOutput(
        scenario_projections=[base, stress],
        summary="Base: Class A WAL 3.0yr; Stress: Class A WAL 4.0yr",
    )

    captured = {}

    class _FakeProjector:
        def execute(self, input):  # noqa: A002 - mirror primitive signature
            captured["input"] = input
            return _Result(output)

    monkeypatch.setattr(projection, "CashflowProjector", _FakeProjector)
    return captured


# ---------------------------------------------------------------------------
# 1. render builds inside a Blocks/Tab context
# ---------------------------------------------------------------------------


def test_render_builds_in_blocks_context():
    """render(state) populates the tab without error inside gr.Blocks."""
    with gr.Blocks():
        state = gr.State(_MockDealState(loaded=False))
        with gr.Tab("Cashflow Projection"):
            assert projection.render(state) is None


def test_render_is_callable():
    """The module exposes the contract's render(state) callable."""
    assert callable(projection.render)


# ---------------------------------------------------------------------------
# 2. Handler guards
# ---------------------------------------------------------------------------


def test_handler_unloaded_state_returns_empty():
    """No loaded deal → guidance message + empty frames, no exception."""
    status, balances, summary = projection._run_projection(
        _MockDealState(loaded=False)
    )
    assert "Load a deal" in status
    assert isinstance(balances, pd.DataFrame) and balances.empty
    assert isinstance(summary, pd.DataFrame) and summary.empty


def test_handler_none_state_returns_empty():
    """A None state is handled gracefully."""
    status, balances, summary = projection._run_projection(None)
    assert "Load a deal" in status
    assert balances.empty and summary.empty


# ---------------------------------------------------------------------------
# 3. Live projection (CashflowProjector monkeypatched — offline)
# ---------------------------------------------------------------------------


def test_handler_runs_live_projection(monkeypatch):
    """A loaded deal runs the projection and builds base+stress series."""
    _patch_projector(monkeypatch)
    state = _MockDealState(loaded=True, tapes=_green_lion_tapes())

    status, balances, summary = projection._run_projection(state)

    assert status.startswith("✅")

    # Balance frame: long-format with the contract columns.
    assert {"month", "balance_eur", "series"} <= set(balances.columns)
    series = set(balances["series"])
    # Both scenarios, all three tranches → 6 distinct series.
    expected = {
        f"{t} ({s})"
        for t in ("Class A", "Class B", "Class C")
        for s in ("base", "stress")
    }
    assert expected == series
    # 13 points per series (month 0 opening + 12 projected months).
    for s in expected:
        assert (balances["series"] == s).sum() == 13


def test_balance_series_amortises_down(monkeypatch):
    """Reconstructed balances start at opening and decline as principal repays."""
    scenario = _make_scenario("base", breach=False)
    rows = projection._balance_series(scenario, "base")
    df = pd.DataFrame(rows)

    class_a = df[df["series"] == "Class A (base)"].sort_values("month")
    # Month 0 is the opening Class A balance (€1.0B → 1000.0 €m).
    assert class_a.iloc[0]["balance_eur"] == 1_000_000_000.0 / 1_000_000.0
    # Strictly decreasing as Class A distributions are peeled off.
    vals = list(class_a["balance_eur"])
    assert all(b <= a for a, b in zip(vals, vals[1:]))
    assert vals[-1] < vals[0]


def test_summary_has_wal_and_breach(monkeypatch):
    """Summary table carries per-scenario per-tranche WAL + breach period."""
    _patch_projector(monkeypatch, stress_breach=True)
    state = _MockDealState(loaded=True, tapes=_green_lion_tapes())

    _status, _balances, summary = projection._run_projection(state)

    assert list(summary.columns) == projection._SUMMARY_COLUMNS
    # 2 scenarios × 3 tranches.
    assert len(summary) == 6
    assert set(summary["Scenario"]) == {"Base", "Stress"}

    # Base Class A WAL = 36 months → 3.00 yr; stress = 48 → 4.00 yr.
    base_a = summary[(summary["Scenario"] == "Base") & (summary["Tranche"] == "Class A")]
    stress_a = summary[
        (summary["Scenario"] == "Stress") & (summary["Tranche"] == "Class A")
    ]
    assert base_a.iloc[0]["WAL (yr)"] == "3.00"
    assert stress_a.iloc[0]["WAL (yr)"] == "4.00"

    # Stress breaches the reserve at month 3; base does not.
    assert stress_a.iloc[0]["Breach period"] == "Month 3"
    assert base_a.iloc[0]["Breach period"] == "—"


def test_handler_uses_latest_tape_pool_balance(monkeypatch):
    """The current pool balance is read from the latest tape, not re-fetched."""
    captured = _patch_projector(monkeypatch)
    tapes = _green_lion_tapes()
    state = _MockDealState(loaded=True, tapes=tapes)

    projection._run_projection(state)

    assert captured["input"].current_pool_balance == tapes[-1]["pool_balance_eur"]
    # Green Lion capital structure is passed through.
    assert captured["input"].current_class_a_balance == projection._CLASS_A_BALANCE
    assert captured["input"].class_a_rate_pct == projection._CLASS_A_RATE_PCT
    assert captured["input"].reserve_fund_balance == projection._RESERVE_FUND_BALANCE
