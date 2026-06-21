"""Tests for the cross-deal portfolio / multi-deal monitoring primitive (#326).

The portfolio monitor is the *aggregator* sibling of the single-deal early-
warning monitor (#322): it rolls every deal's covenant proximity-trend
projection up into one cross-deal watchlist, ranked by which deal is breaching
or about to breach a covenant trigger first. Like the relative-value screener
(#324) it runs offline over an injected per-deal loader, and its load-bearing
honesty contract is that a deal that cannot be evaluated offline is reported
``watch_status='unavailable'`` with a real reason — never a fabricated status.

These tests pin:
- the pure roll-up core on synthetic per-deal projections (deal-level status
  derivation, worst-proximity, soonest periods-to-breach, cross-deal ranking,
  most-urgent deal, tally),
- the honest edge cases (a deal the loader returns ``None`` for, a loader that
  raises, an empty registry) that must not crash or fabricate,
- the registered-primitive envelope (registry presence, confidence as the
  evaluable fraction, return type), and
- the monitor over the *real* shipped registry with the canonical offline
  loader.
"""

from __future__ import annotations

from typing import Any

from loanwhiz.config import DEAL_REGISTRY
from loanwhiz.primitives.base import PrimitiveResult
from loanwhiz.primitives.portfolio_monitor import (
    WATCH_BREACHED,
    WATCH_CLEAR,
    WATCH_PROJECTED,
    WATCH_UNAVAILABLE,
    DealWatchRow,
    PortfolioMonitor,
    PortfolioMonitorInput,
    PortfolioMonitorOutput,
    PortfolioWatchlist,
    build_portfolio_watchlist,
)
from loanwhiz.primitives.proximity_trend_monitor import (
    ProximityTrendOutput,
    TriggerProjection,
)
from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY


# ---------------------------------------------------------------------------
# Helpers — synthetic per-deal proximity-trend projections
# ---------------------------------------------------------------------------


def _proj(
    name: str,
    *,
    status: str,
    latest: float | None,
    ptb: int | None,
) -> TriggerProjection:
    """A minimal TriggerProjection in a given status (the fields the roll-up reads)."""
    label: str | None
    if status == "breached":
        label = "now"
    elif status == "projected" and ptb is not None:
        label = f"+{ptb} period" + ("s" if ptb != 1 else "")
    else:
        label = None
    return TriggerProjection(
        trigger_name=name,
        evaluable_points=3,
        latest_proximity_pct=latest,
        slope_per_period=1.0 if status == "projected" else None,
        trend="deteriorating" if status in ("projected", "breached") else "stable",
        periods_to_breach=ptb,
        projected_breach_period=label,
        status=status,
    )


def _trend(projections: list[TriggerProjection]) -> ProximityTrendOutput:
    """Wrap projections in a ProximityTrendOutput (mirrors the monitor's order)."""
    most_urgent = next(
        (p.trigger_name for p in projections if p.status in ("breached", "projected")),
        None,
    )
    return ProximityTrendOutput(
        projections=projections,
        most_urgent=most_urgent,
        summary="synthetic",
    )


def _registry(deal_ids: list[str]) -> dict[str, dict[str, Any]]:
    """A synthetic deal registry with the given ids (deal_name + jurisdiction)."""
    return {
        d: {"deal_name": d.replace("-", " ").title(), "jurisdiction": "Testland"}
        for d in deal_ids
    }


def _loader_from(
    mapping: dict[str, ProximityTrendOutput | None]
) -> Any:
    """Build a proximity_loader keyed on deal_name from a {deal_id: projection} map."""
    by_name = {
        d.replace("-", " ").title(): v for d, v in mapping.items()
    }

    def loader(deal_ctx: dict[str, Any]) -> ProximityTrendOutput | None:
        return by_name.get(deal_ctx["deal_name"])

    return loader


# ---------------------------------------------------------------------------
# Pure core — status derivation, ranking, tally
# ---------------------------------------------------------------------------


def test_ranks_breached_then_projected_then_clear_then_unavailable():
    """The watchlist orders deals breached < projected < clear < unavailable, and
    within projected the soonest breach ranks ahead."""
    deals = _registry(["d-clear", "d-proj-late", "d-breached", "d-proj-soon", "d-na"])
    loader = _loader_from(
        {
            "d-clear": _trend([_proj("t", status="no-projected-breach", latest=30.0, ptb=None)]),
            "d-proj-late": _trend([_proj("t", status="projected", latest=70.0, ptb=9)]),
            "d-breached": _trend([_proj("t", status="breached", latest=120.0, ptb=0)]),
            "d-proj-soon": _trend([_proj("t", status="projected", latest=88.0, ptb=2)]),
            "d-na": None,
        }
    )

    wl = build_portfolio_watchlist(deals, proximity_loader=loader)

    order = [r.deal_id for r in wl.rows]
    assert order == ["d-breached", "d-proj-soon", "d-proj-late", "d-clear", "d-na"]
    assert [r.rank for r in wl.rows] == [1, 2, 3, 4, 5]
    assert wl.most_urgent_deal == "d-breached"

    statuses = {r.deal_id: r.watch_status for r in wl.rows}
    assert statuses["d-breached"] == WATCH_BREACHED
    assert statuses["d-proj-soon"] == WATCH_PROJECTED
    assert statuses["d-clear"] == WATCH_CLEAR
    assert statuses["d-na"] == WATCH_UNAVAILABLE


def test_deal_row_rolls_up_trigger_fields():
    """A deal's row carries the worst proximity, soonest ptb, most-urgent trigger,
    and breached/projected counts rolled from its trigger projections."""
    deals = _registry(["d1"])
    loader = _loader_from(
        {
            "d1": _trend(
                [
                    _proj("loss", status="projected", latest=85.0, ptb=4),
                    _proj("reserve", status="projected", latest=60.0, ptb=7),
                    _proj("clean_up", status="no-projected-breach", latest=20.0, ptb=None),
                ]
            )
        }
    )

    wl = build_portfolio_watchlist(deals, proximity_loader=loader)
    row = wl.rows[0]

    assert row.watch_status == WATCH_PROJECTED
    assert row.worst_trigger_proximity_pct == 85.0  # highest proximity
    assert row.periods_to_breach == 4  # soonest projected
    assert row.projected_breach_period == "+4 periods"
    assert row.n_triggers_projected == 2
    assert row.n_triggers_breached == 0


def test_breached_deal_reports_now_and_counts():
    deals = _registry(["d1"])
    loader = _loader_from(
        {
            "d1": _trend(
                [
                    _proj("loss", status="breached", latest=130.0, ptb=0),
                    _proj("reserve", status="projected", latest=70.0, ptb=5),
                ]
            )
        }
    )

    wl = build_portfolio_watchlist(deals, proximity_loader=loader)
    row = wl.rows[0]

    assert row.watch_status == WATCH_BREACHED
    assert row.periods_to_breach == 0
    assert row.projected_breach_period == "now"
    assert row.n_triggers_breached == 1


def test_tally_and_summary_reflect_states():
    deals = _registry(["a", "b", "c"])
    loader = _loader_from(
        {
            "a": _trend([_proj("t", status="breached", latest=110.0, ptb=0)]),
            "b": _trend([_proj("t", status="projected", latest=80.0, ptb=3)]),
            "c": None,
        }
    )

    wl = build_portfolio_watchlist(deals, proximity_loader=loader)

    assert wl.tally["deals_total"] == 3
    assert wl.tally["deals_evaluable"] == 2
    assert wl.tally[WATCH_BREACHED] == 1
    assert wl.tally[WATCH_PROJECTED] == 1
    assert wl.tally[WATCH_UNAVAILABLE] == 1
    assert "BREACHED" in wl.summary and "PROJECTED" in wl.summary


# ---------------------------------------------------------------------------
# Honesty edge cases — never crash, never fabricate
# ---------------------------------------------------------------------------


def test_loader_returns_none_yields_honest_unavailable_row():
    """A deal the loader can't evaluate offline is an honest unavailable row with
    a non-empty reason — not a crash, not a fabricated status."""
    deals = _registry(["d1"])
    wl = build_portfolio_watchlist(deals, proximity_loader=lambda _ctx: None)

    row = wl.rows[0]
    assert row.evaluable is False
    assert row.watch_status == WATCH_UNAVAILABLE
    assert row.reason  # non-empty
    assert row.worst_trigger_proximity_pct is None
    assert row.most_urgent_trigger is None
    assert wl.most_urgent_deal is None


def test_loader_raising_is_contained_as_unavailable():
    """A per-deal loader exception must not sink the whole watchlist."""
    deals = _registry(["ok", "boom"])

    def loader(deal_ctx: dict[str, Any]) -> ProximityTrendOutput | None:
        if deal_ctx["deal_name"].lower().startswith("boom"):
            raise RuntimeError("tape unreachable")
        return _trend([_proj("t", status="no-projected-breach", latest=20.0, ptb=None)])

    wl = build_portfolio_watchlist(deals, proximity_loader=loader)

    by_id = {r.deal_id: r for r in wl.rows}
    assert by_id["boom"].watch_status == WATCH_UNAVAILABLE
    assert by_id["boom"].evaluable is False
    assert by_id["ok"].watch_status == WATCH_CLEAR


def test_evaluable_deal_with_no_triggers_is_clear_with_reason():
    deals = _registry(["d1"])
    loader = _loader_from({"d1": _trend([])})

    wl = build_portfolio_watchlist(deals, proximity_loader=loader)
    row = wl.rows[0]
    assert row.evaluable is True
    assert row.watch_status == WATCH_CLEAR
    assert row.reason


def test_empty_registry_is_safe():
    wl = build_portfolio_watchlist({}, proximity_loader=lambda _ctx: None)
    assert wl.rows == []
    assert wl.most_urgent_deal is None
    assert wl.tally["deals_total"] == 0


# ---------------------------------------------------------------------------
# Registered-primitive envelope
# ---------------------------------------------------------------------------


def test_portfolio_monitor_registered():
    assert PRIMITIVE_REGISTRY.get("portfolio_monitor") is not None


def test_primitive_confidence_is_evaluable_fraction():
    """Confidence is evaluable-deals / total-deals (1.0 only when all evaluable)."""
    deals = _registry(["a", "b", "c", "d"])
    loader = _loader_from(
        {
            "a": _trend([_proj("t", status="breached", latest=110.0, ptb=0)]),
            "b": _trend([_proj("t", status="no-projected-breach", latest=20.0, ptb=None)]),
            "c": None,
            "d": None,
        }
    )
    result = PortfolioMonitor(proximity_loader=loader).execute(
        PortfolioMonitorInput(deals=deals)
    )
    assert isinstance(result, PrimitiveResult)
    assert isinstance(result.output, PortfolioMonitorOutput)
    assert isinstance(result.output.watchlist, PortfolioWatchlist)
    assert result.confidence == 0.5  # 2 of 4 deals evaluable
    assert result.citations  # grounded


def test_bare_primitive_reports_all_unavailable():
    """A bare PortfolioMonitor() (default no-op loader) reports every deal as an
    honest unavailable row — never a fabricated status."""
    deals = _registry(["a", "b"])
    result = PortfolioMonitor().execute(PortfolioMonitorInput(deals=deals))
    wl = result.output.watchlist
    assert all(r.watch_status == WATCH_UNAVAILABLE for r in wl.rows)
    assert result.confidence == 0.0


# ---------------------------------------------------------------------------
# Real shipped registry — must not crash, honest where uncached
# ---------------------------------------------------------------------------


def test_over_real_registry_does_not_crash():
    """Over the real DEAL_REGISTRY with the default offline loader, the monitor
    produces one row per deal and never raises, reporting honest unavailability
    where no model is cached in this environment."""
    result = PortfolioMonitor().execute(
        PortfolioMonitorInput(deals=DEAL_REGISTRY)
    )
    wl = result.output.watchlist
    assert len(wl.rows) == len(DEAL_REGISTRY)
    assert {r.deal_id for r in wl.rows} == set(DEAL_REGISTRY)
    # Ranks are a 1..N permutation.
    assert sorted(r.rank for r in wl.rows) == list(range(1, len(wl.rows) + 1))
    for row in wl.rows:
        assert isinstance(row, DealWatchRow)
        assert row.reason  # honesty: always a reason
        if not row.evaluable:
            assert row.watch_status == WATCH_UNAVAILABLE
