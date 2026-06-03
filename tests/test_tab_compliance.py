"""Offline tests for the Compliance & Covenants demo tab.

Target: ``clients/demo/tabs/compliance.py``. These never launch the UI or hit
the network. They verify the tab-plugin contract surface (``render(state)``
builds inside a Blocks/Tab context), the three live handlers' guards and
happy paths with every primitive monkeypatched, and — most importantly — the
pure ``_loss_trigger_chart_data`` helper that produces the differentiator
chart's actual + base + stress + trigger-limit series.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import gradio as gr
import pandas as pd


# ---------------------------------------------------------------------------
# Import the tab module by path (mirrors tests/test_tab_projection.py).
# ---------------------------------------------------------------------------


def _import_compliance():
    """Import ``clients/demo/tabs/compliance.py`` as a module."""
    repo_root = Path(__file__).resolve().parent.parent
    for p in (str(repo_root), str(repo_root / "src")):
        if p not in sys.path:
            sys.path.insert(0, p)

    mod_path = repo_root / "clients" / "demo" / "tabs" / "compliance.py"
    spec = importlib.util.spec_from_file_location("demo_tab_compliance", mod_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


compliance = _import_compliance()


# ---------------------------------------------------------------------------
# Mock DealState + fixtures
# ---------------------------------------------------------------------------


class _MockDealState:
    """Minimal stand-in for the shell's ``DealState``."""

    def __init__(self, loaded=True, tapes=None):
        self.loaded = loaded
        self.tapes = tapes if tapes is not None else []


def _green_lion_tapes():
    """Three monthly tapes whose ``period`` labels resolve to real CSV URLs.

    Each carries an ``arrears_breakdown.default_pct`` so the covenant monitor
    and the loss-rate chart have a realised metric to read.
    """
    from loanwhiz.config import GREEN_LION

    dates = [e["date"] for e in GREEN_LION["tape_urls"]]
    losses = [0.20, 0.35, 0.55]
    balances = [1_063_000_000.0, 1_055_000_000.0, 1_042_490_000.0]
    return [
        {
            "period": d,
            "pool_balance_eur": b,
            "loan_count": 100,
            "arrears_breakdown": {"default_pct": loss},
        }
        for d, b, loss in zip(dates, balances, losses)
    ]


class _Result:
    def __init__(self, output):
        self.output = output


def _make_projector_output(*, stress_cum_losses=None):
    """Build a fake ``CashflowProjectorOutput`` with base + stress scenarios."""
    from loanwhiz.primitives.cashflow_projector import (
        CashflowProjectorOutput,
        PeriodProjection,
        ScenarioAssumptions,
        ScenarioProjection,
    )

    def _scenario(name, per_period_loss):
        periods = []
        for t in range(1, 13):
            periods.append(
                PeriodProjection(
                    period=t,
                    pool_balance_eur=1_000_000_000.0 - t * 5_000_000.0,
                    class_a_distribution=10_000_000.0,
                    class_b_distribution=500_000.0,
                    class_c_distribution=100_000.0,
                    cumulative_losses=t * per_period_loss,
                    reserve_fund_balance=compliance._RESERVE_FUND_BALANCE,
                )
            )
        return ScenarioProjection(
            scenario=ScenarioAssumptions(name=name, description=f"{name} case"),
            periods=periods,
            total_class_a=120_000_000.0,
            total_class_b=6_000_000.0,
            wal_class_a_months=36.0,
        )

    base = _scenario("base", 200_000.0)
    stress = _scenario("stress", stress_cum_losses or 2_000_000.0)
    return CashflowProjectorOutput(
        scenario_projections=[base, stress],
        summary="Base: low losses; Stress: losses climb toward the trigger.",
    )


def _patch_projector(monkeypatch):
    """Patch ``CashflowProjector`` so the chart handler runs offline."""
    output = _make_projector_output()
    captured = {}

    class _FakeProjector:
        def execute(self, input):  # noqa: A002 — mirror primitive signature
            captured["input"] = input
            return _Result(output)

    monkeypatch.setattr(compliance, "CashflowProjector", _FakeProjector)
    return captured


# ---------------------------------------------------------------------------
# 1. render builds inside a Blocks/Tab context
# ---------------------------------------------------------------------------


def test_render_builds_in_blocks_context():
    """render(state) populates the tab without error inside gr.Blocks."""
    with gr.Blocks():
        state = gr.State(_MockDealState(loaded=False))
        with gr.Tab("Compliance & Covenants"):
            assert compliance.render(state) is None


def test_render_is_callable():
    """The module exposes the contract's render(state) callable."""
    assert callable(compliance.render)


# ---------------------------------------------------------------------------
# 2. Handler guards (unloaded / None state)
# ---------------------------------------------------------------------------


def test_report_handler_unloaded_returns_empty():
    status, df = compliance._run_report_verification(_MockDealState(loaded=False))
    assert "Load a deal" in status
    assert isinstance(df, pd.DataFrame) and df.empty


def test_covenant_handler_none_returns_empty():
    status, df = compliance._run_covenant_monitor(None)
    assert "Load a deal" in status
    assert isinstance(df, pd.DataFrame) and df.empty


def test_chart_handler_unloaded_returns_empty():
    status, df = compliance._run_loss_chart(_MockDealState(loaded=False))
    assert "Load a deal" in status
    assert isinstance(df, pd.DataFrame) and df.empty


# ---------------------------------------------------------------------------
# 3. Report verification — live chain monkeypatched + graceful degradation
# ---------------------------------------------------------------------------


def test_report_verification_runs_and_marks_matches(monkeypatch):
    """A loaded deal runs collections→waterfall→verifier and marks 🟢/🔴."""
    from loanwhiz.primitives.report_verifier import (
        ReportedFigure,
        ReportVerifierOutput,
    )

    # Stub the three primitives so no CSV/PDF is fetched.
    class _FakeCollections:
        def execute(self, input):  # noqa: A002
            class _Out:
                available_revenue_funds = 14_000_000.0
                available_principal_funds = 8_000_000.0
                senior_fees = 50_000.0
                pool_balance_eur = 1_042_490_000.0

            return _Result(_Out())

    class _FakeWaterfall:
        def execute(self, input):  # noqa: A002
            class _Out:
                def model_dump(self):
                    return {"tranche_distributions": [], "total_distributed": 0.0}

            return _Result(_Out())

    verifier_out = ReportVerifierOutput(
        reporting_period="April 2026",
        figures_checked=2,
        figures_matched=1,
        figures_mismatched=1,
        line_items=[
            ReportedFigure(
                line_item="class_a_interest_paid",
                reported_value=9_050_000.0,
                computed_value=9_050_000.0,
                delta=0.0,
                delta_pct=0.0,
                match=True,
            ),
            ReportedFigure(
                line_item="pool_balance",
                reported_value=1_000_000_000.0,
                computed_value=1_042_490_000.0,
                delta=-42_490_000.0,
                delta_pct=-4.08,
                match=False,
            ),
        ],
        overall_match=False,
        summary="1/2 figures match within 1% tolerance; 1 mismatch: pool_balance",
    )

    class _FakeVerifier:
        def execute(self, input):  # noqa: A002
            return _Result(verifier_out)

    monkeypatch.setattr(compliance, "CollectionsAggregator", _FakeCollections)
    monkeypatch.setattr(compliance, "WaterfallRunner", _FakeWaterfall)
    monkeypatch.setattr(compliance, "ReportVerifier", _FakeVerifier)

    state = _MockDealState(loaded=True, tapes=_green_lion_tapes())
    status, df = compliance._run_report_verification(state)

    assert "April 2026" in status
    assert list(df.columns) == compliance._VERIFY_COLUMNS
    assert len(df) == 2
    markers = list(df[""])
    assert "🟢" in markers and "🔴" in markers


def test_report_verification_degrades_on_exception(monkeypatch):
    """A verifier exception (e.g. Vertex slow) degrades to a notice, no crash."""

    class _FakeCollections:
        def execute(self, input):  # noqa: A002
            raise RuntimeError("vertex unreachable")

    monkeypatch.setattr(compliance, "CollectionsAggregator", _FakeCollections)
    state = _MockDealState(loaded=True, tapes=_green_lion_tapes())

    status, df = compliance._run_report_verification(state)
    assert "unavailable" in status.lower()
    assert df.empty


# ---------------------------------------------------------------------------
# 4. Covenant monitor — live grid (real primitive, offline)
# ---------------------------------------------------------------------------


def test_covenant_monitor_builds_grid():
    """CovenantMonitor runs over all 3 tapes and yields a per-period grid."""
    state = _MockDealState(loaded=True, tapes=_green_lion_tapes())
    status, df = compliance._run_covenant_monitor(state)

    # One column per period plus the Trigger column.
    tapes = _green_lion_tapes()
    expected_cols = ["Trigger"] + [t["period"] for t in tapes]
    assert list(df.columns) == expected_cols
    # One row per default trigger.
    from loanwhiz.primitives.covenant_monitor import CovenantMonitor

    assert len(df) == len(CovenantMonitor.DEFAULT_TRIGGERS)
    # Every period cell is a proximity marker (default_pct 0.2–0.55 is well
    # below the 1.5% loss trigger → 🟢 on that row).
    markers = set()
    for col in expected_cols[1:]:
        markers.update(df[col])
    assert markers <= {"🟢", "🟡", "🔴", "—"}
    assert "🟢" in markers
    assert status  # non-empty summary


# ---------------------------------------------------------------------------
# 5. The differentiator chart helper — actual + base + stress + limit
# ---------------------------------------------------------------------------


def test_chart_data_actuals_only_without_projector():
    """Without a projector output, only the actual loss points are emitted."""
    tapes = _green_lion_tapes()
    df = compliance._loss_trigger_chart_data(tapes, None)
    assert list(df.columns) == compliance._CHART_COLUMNS
    assert set(df["series"]) == {compliance._SERIES_ACTUAL}
    assert len(df) == len(tapes)
    # Actuals are the tapes' default_pct values at months 0..n-1.
    actual = df.sort_values("month")
    assert list(actual["value"]) == [0.20, 0.35, 0.55]


def test_chart_data_has_all_four_series():
    """The chart helper produces actual + base + stress + trigger-limit series."""
    tapes = _green_lion_tapes()
    projector_out = _make_projector_output()

    df = compliance._loss_trigger_chart_data(
        tapes, projector_out, pool_balance=1_042_490_000.0
    )

    assert list(df.columns) == compliance._CHART_COLUMNS
    series = set(df["series"])
    assert series == {
        compliance._SERIES_ACTUAL,
        compliance._SERIES_BASE,
        compliance._SERIES_STRESS,
        compliance._SERIES_LIMIT,
    }

    # The trigger-limit line is flat at the primitive's threshold (1.5%).
    threshold = compliance._loss_trigger_threshold()
    limit_vals = set(df[df["series"] == compliance._SERIES_LIMIT]["value"])
    assert limit_vals == {threshold}

    # Projected paths anchor at the last actual month (n-1 = 2) and extend
    # forward by the 12 projected months → months 2..14.
    base = df[df["series"] == compliance._SERIES_BASE].sort_values("month")
    assert base.iloc[0]["month"] == len(tapes) - 1
    assert base["month"].max() == (len(tapes) - 1) + 12

    # Stress losses exceed base losses at the final projected month (the
    # headline "stress climbs toward the trigger" signal).
    stress = df[df["series"] == compliance._SERIES_STRESS].sort_values("month")
    assert stress["value"].max() > base["value"].max()


def test_run_loss_chart_live(monkeypatch):
    """The live chart handler runs the projector and emits all four series."""
    captured = _patch_projector(monkeypatch)
    state = _MockDealState(loaded=True, tapes=_green_lion_tapes())

    status, df = compliance._run_loss_chart(state)

    assert status.startswith("✅")
    assert set(df["series"]) == {
        compliance._SERIES_ACTUAL,
        compliance._SERIES_BASE,
        compliance._SERIES_STRESS,
        compliance._SERIES_LIMIT,
    }
    # Pool balance comes from the latest tape, not a re-fetch.
    assert captured["input"].current_pool_balance == _green_lion_tapes()[-1][
        "pool_balance_eur"
    ]
    assert captured["input"].class_a_rate_pct == compliance._CLASS_A_RATE_PCT


def test_run_loss_chart_degrades_on_projector_exception(monkeypatch):
    """A projector exception degrades to actuals-only, no crash."""

    class _BoomProjector:
        def execute(self, input):  # noqa: A002
            raise RuntimeError("projector boom")

    monkeypatch.setattr(compliance, "CashflowProjector", _BoomProjector)
    state = _MockDealState(loaded=True, tapes=_green_lion_tapes())

    status, df = compliance._run_loss_chart(state)
    assert "unavailable" in status.lower()
    assert set(df["series"]) == {compliance._SERIES_ACTUAL}
