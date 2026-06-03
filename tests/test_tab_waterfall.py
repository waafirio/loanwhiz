"""Offline tests for the Waterfall demo tab (``clients/demo/tabs/waterfall.py``).

These never launch the UI or hit the network. They verify the tab-plugin
contract surface (``render(state)`` builds inside a Blocks context) and the
live collections → waterfall handler, with the two primitives monkeypatched so
no CSV is fetched.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import gradio as gr
import pandas as pd


# ---------------------------------------------------------------------------
# Import the tab module by path (mirrors tests/test_demo_shell.py).
# ---------------------------------------------------------------------------


def _import_waterfall():
    """Import ``clients/demo/tabs/waterfall.py`` as a module."""
    repo_root = Path(__file__).resolve().parent.parent
    for p in (str(repo_root), str(repo_root / "src")):
        if p not in sys.path:
            sys.path.insert(0, p)

    mod_path = repo_root / "clients" / "demo" / "tabs" / "waterfall.py"
    spec = importlib.util.spec_from_file_location("demo_tab_waterfall", mod_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


waterfall = _import_waterfall()


# ---------------------------------------------------------------------------
# Mock DealState + mock primitive outputs
# ---------------------------------------------------------------------------


class _MockDealState:
    """Minimal stand-in for the shell's ``DealState`` (loaded, two tapes)."""

    def __init__(self, loaded=True, tapes=None):
        self.loaded = loaded
        self.tapes = tapes if tapes is not None else []


def _green_lion_tapes():
    """Two tapes whose ``period`` labels resolve to real Green Lion CSV URLs."""
    from loanwhiz.config import GREEN_LION

    dates = [e["date"] for e in GREEN_LION["tape_urls"]]
    # Use the last two known periods so _tape_csv_url resolves and there is a
    # prior period for prev_pool_balance.
    return [
        {"period": dates[-2], "pool_balance_eur": 1_050_000_000.0, "loan_count": 100},
        {"period": dates[-1], "pool_balance_eur": 1_042_490_000.0, "loan_count": 99},
    ]


class _Result:
    def __init__(self, output):
        self.output = output


def _patch_primitives(monkeypatch):
    """Patch both primitives so the handler runs offline (no CSV read)."""
    from loanwhiz.primitives.waterfall_runner import (
        TrancheDistribution,
        WaterfallStep,
    )

    class _Collections:
        available_revenue_funds = 9_050_000.0
        available_principal_funds = 7_510_000.0
        senior_fees = 50_000.0

    class _FakeAggregator:
        def execute(self, input):  # noqa: A002 - mirror primitive signature
            return _Result(_Collections())

    # 11 revenue steps (a)–(k), one tranche row per class.
    priorities = [f"({c})" for c in "abcdefghijk"]
    rev_steps = [
        WaterfallStep(
            priority=p,
            recipient=r,
            amount_available=1_000_000.0,
            amount_distributed=100_000.0,
            shortfall=0.0,
        )
        for p, r in zip(
            priorities,
            [
                "senior_fees",
                "operating_fees",
                "swap_payment",
                "class_a_interest",
                "class_a_pdl_replenishment",
                "reserve_account_replenishment",
                "expense_account_replenishment",
                "class_b_pdl_replenishment",
                "subordinated_swap_payment",
                "class_c_principal_from_revenue",
                "deferred_purchase_price_seller",
            ],
        )
    ]
    tranches = [
        TrancheDistribution(
            tranche=t,
            interest_received=1.0,
            principal_received=2.0,
            total_received=3.0,
            opening_balance=10.0,
            closing_balance=8.0,
        )
        for t in ("class_a", "class_b", "class_c")
    ]

    class _Waterfall:
        revenue_waterfall = rev_steps
        tranche_distributions = tranches
        total_distributed = 1_100_000.0
        shortfall = 0.0

    class _FakeRunner:
        def execute(self, input):  # noqa: A002
            return _Result(_Waterfall())

    monkeypatch.setattr(waterfall, "CollectionsAggregator", _FakeAggregator)
    monkeypatch.setattr(waterfall, "WaterfallRunner", _FakeRunner)


# ---------------------------------------------------------------------------
# 1. render builds inside a Blocks/Tab context
# ---------------------------------------------------------------------------


def test_render_builds_in_blocks_context():
    """render(state) populates the tab without error inside gr.Blocks."""
    with gr.Blocks():
        state = gr.State(_MockDealState(loaded=False))
        with gr.Tab("Waterfall"):
            assert waterfall.render(state) is None


def test_render_is_callable():
    """The module exposes the contract's render(state) callable."""
    assert callable(waterfall.render)


# ---------------------------------------------------------------------------
# 2. Handler guards
# ---------------------------------------------------------------------------


def test_handler_unloaded_state_returns_empty():
    """No loaded deal → guidance message + empty tables, no exception."""
    status, steps, tranches, cascade = waterfall._run_waterfall(
        _MockDealState(loaded=False)
    )
    assert "Load a deal" in status
    assert isinstance(steps, pd.DataFrame) and steps.empty
    assert isinstance(tranches, pd.DataFrame) and tranches.empty


def test_handler_none_state_returns_empty():
    """A None state is handled gracefully."""
    status, steps, tranches, cascade = waterfall._run_waterfall(None)
    assert "Load a deal" in status


def test_handler_unknown_period_reports_error():
    """A tape whose period has no matching CSV URL surfaces a clear error."""
    state = _MockDealState(
        loaded=True,
        tapes=[{"period": "not-a-real-period", "pool_balance_eur": 1.0}],
    )
    status, steps, tranches, cascade = waterfall._run_waterfall(state)
    assert "Could not resolve" in status
    assert steps.empty


# ---------------------------------------------------------------------------
# 3. Live chain (primitives monkeypatched — offline)
# ---------------------------------------------------------------------------


def test_handler_runs_live_chain(monkeypatch):
    """A loaded deal runs the collections → waterfall chain and builds tables."""
    _patch_primitives(monkeypatch)
    state = _MockDealState(loaded=True, tapes=_green_lion_tapes())

    status, steps, tranches, cascade = waterfall._run_waterfall(state)

    assert status.startswith("✅")
    # 11 revenue-priority steps.
    assert len(steps) == 11
    assert list(steps["Step"]) == [f"({c})" for c in "abcdefghijk"]
    # Per-tranche table: Class A / B / C.
    assert len(tranches) == 3
    assert list(tranches["Tranche"]) == ["Class A", "Class B", "Class C"]
    # Bonus cascade chart frame has one row per revenue step.
    assert isinstance(cascade, pd.DataFrame)
    assert len(cascade) == 11
    assert {"step", "amount_eur"} <= set(cascade.columns)


def test_handler_passes_prev_pool_balance(monkeypatch):
    """The prior period's pool balance is passed as prev_pool_balance."""
    captured = {}

    from loanwhiz.primitives.waterfall_runner import (
        TrancheDistribution,
        WaterfallStep,
    )

    class _FakeAggregator:
        def execute(self, input):  # noqa: A002
            captured["prev_pool_balance"] = input.prev_pool_balance
            captured["tape_file_url"] = input.tape_file_url

            class _O:
                available_revenue_funds = 1.0
                available_principal_funds = 1.0
                senior_fees = 1.0

            return _Result(_O())

    class _FakeRunner:
        def execute(self, input):  # noqa: A002
            class _O:
                revenue_waterfall = [
                    WaterfallStep(
                        priority="(a)",
                        recipient="senior_fees",
                        amount_available=1.0,
                        amount_distributed=1.0,
                        shortfall=0.0,
                    )
                ]
                tranche_distributions = [
                    TrancheDistribution(
                        tranche="class_a",
                        interest_received=0.0,
                        principal_received=0.0,
                        total_received=0.0,
                        opening_balance=0.0,
                        closing_balance=0.0,
                    )
                ]
                total_distributed = 1.0
                shortfall = 0.0

            return _Result(_O())

    monkeypatch.setattr(waterfall, "CollectionsAggregator", _FakeAggregator)
    monkeypatch.setattr(waterfall, "WaterfallRunner", _FakeRunner)

    tapes = _green_lion_tapes()
    waterfall._run_waterfall(_MockDealState(loaded=True, tapes=tapes))

    # prev_pool_balance is the prior (second-to-last) tape's pool balance.
    assert captured["prev_pool_balance"] == tapes[-2]["pool_balance_eur"]
    # The CSV URL was resolved from the latest tape's period.
    assert captured["tape_file_url"].endswith(".csv")
