"""Tests for the Pool & Performance demo tab (``clients/demo/tabs/pool.py``).

Offline contract tests for issue #79's tab. They verify the pure formatting
helpers and that ``render(state)`` builds its UI inside a Gradio context
against a mock :class:`DealState` carrying three tapes — without launching the
UI or hitting the network. The tape dicts mirror ``EsmaTapeOutput.model_dump()``
plus the shell's ``"period"`` label key (see ``clients/demo/CONTRACT.md``).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import gradio as gr

# Repo root on path so ``clients.demo...`` imports resolve (mirrors app.py's
# shim and the absolute imports the shell uses).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from clients.demo.tabs import pool  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures — a mock DealState with three tapes (EsmaTapeOutput shape + period).
# ---------------------------------------------------------------------------


def _tape(
    period: str, balance: float, loans: int, default_pct: float, ltv: float
) -> dict:
    """Build one tape dict in the shape the shell shares with tabs."""
    return {
        "period": period,
        "reporting_date": period,
        "asset_class": "RMBS",
        "transaction_name": "Green Lion 2026-1 B.V.",
        "loan_count": loans,
        "pool_balance_eur": balance,
        "pool_stats": {
            "wtd_coupon_pct": 3.6,
            "wtd_ltv": ltv,
            "wtd_seasoning": 24.0,
            "wtd_remaining_term": 300.0,
        },
        "arrears_breakdown": {
            "current_pct": 100.0 - default_pct - 1.0,
            "arrears_1_2m_pct": 0.7,
            "arrears_180d_plus_pct": 0.3,
            "default_pct": default_pct,
        },
        "epc_breakdown": {"A": 40.0, "B": 35.0, "C": 25.0},
        "rate_type_breakdown": {"Fixed": 80.0, "Floating": 20.0},
        "property_type_breakdown": {"Detached": 55.0, "Apartment": 45.0},
        "geographic_breakdown": {"Noord-Holland": 60.0, "Zuid-Holland": 40.0},
        "annex_detected": "Annex 2 (RMBS)",
    }


def _mock_state():
    """A loaded DealState-like object with three tapes (chronological)."""
    return SimpleNamespace(
        deal_name="Green Lion 2026-1 B.V.",
        tapes=[
            _tape("2026-02-28", 1.063e9, 5000, 0.010, 72.5),
            _tape("2026-03-31", 1.050e9, 4960, 0.020, 72.1),
            _tape("2026-04-30", 1.038e9, 4910, 0.035, 71.8),
        ],
        deal_model=None,
        loaded=True,
        load_error=None,
    )


# ---------------------------------------------------------------------------
# 1. Pure helpers
# ---------------------------------------------------------------------------


def test_pool_trend_rows_shape_and_values():
    """build_pool_trend_rows yields one 6-column row per tape."""
    rows = pool.build_pool_trend_rows(_mock_state().tapes)
    assert len(rows) == 3
    for row in rows:
        assert len(row) == len(pool.POOL_TREND_HEADERS)
    # First row: period, formatted balance, loan count, arrears %, default %, LTV.
    first = rows[0]
    assert first[0] == "2026-02-28"
    assert first[1] == "€1063.0M"
    assert first[2] == "5,000"
    # Arrears % = 100 - current_pct = 100 - (100 - 0.01 - 1.0) = 1.01.
    assert first[3] == "1.01%"
    assert first[4] == "0.010%"
    assert first[5] == "72.5%"


def test_pool_trend_rows_handles_missing_fields():
    """Missing pool_stats / arrears degrade to N/A and zeros, never crash."""
    rows = pool.build_pool_trend_rows(
        [{"period": "2026-05-31", "pool_balance_eur": 5.0e8, "loan_count": 100}]
    )
    assert rows[0][0] == "2026-05-31"
    assert rows[0][5] == "N/A"  # no wtd_ltv
    assert rows[0][3] == "0.00%"  # no arrears breakdown
    assert rows[0][4] == "0.000%"


def test_distribution_rows_sorted_desc():
    """build_distribution_rows sorts descending by percentage."""
    rows = pool.build_distribution_rows({"A": 10.0, "B": 50.0, "C": 40.0})
    assert [r[0] for r in rows] == ["B", "C", "A"]
    assert rows[0][1] == "50.0%"


def test_distribution_rows_none_is_empty():
    """A None / empty breakdown yields no rows."""
    assert pool.build_distribution_rows(None) == []
    assert pool.build_distribution_rows({}) == []


def test_plot_data_records():
    """build_plot_data emits category/percent records sorted desc."""
    data = pool.build_plot_data({"A": 10.0, "B": 50.0})
    assert data == [
        {"category": "B", "percent": 50.0},
        {"category": "A", "percent": 10.0},
    ]
    assert pool.build_plot_data(None) == []


# ---------------------------------------------------------------------------
# 2. render(state) builds without error inside a Gradio context
# ---------------------------------------------------------------------------


def test_render_builds_inside_blocks():
    """render(state) populates a tab inside an open gr.Blocks/Tab context."""
    with gr.Blocks():
        state = gr.State(_mock_state())
        # Must not raise — builds the trend table, distribution tables/plots,
        # and wires the refresh handler.
        result = pool.render(state)
    assert result is None  # render builds UI as a side effect


def test_render_matches_contract_signature():
    """The module exposes exactly the contract's render(state) entrypoint."""
    assert callable(pool.render)
    assert pool.render.__code__.co_argcount == 1
