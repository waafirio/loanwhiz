"""Tests for the ProximityTrendMonitor primitive (#322).

Covers:
1. OLS slope of proximity over the evaluable series.
2. periods-to-breach — deteriorating (finite), improving/flat (None),
   already-breached (0).
3. not-evaluable periods excluded from the slope fit.
4. < 2 evaluable points → insufficient-data.
5. Ranking by time-to-breach (breached first, then soonest projected, then
   no-projected-breach / insufficient-data last).
6. most_urgent selection.
7. Registry integration + public-surface export.
8. End-to-end from a CovenantMonitor run over a deteriorating synthetic series.
"""

from __future__ import annotations

import math

import pytest

from loanwhiz.primitives.base import Citation, PrimitiveResult
from loanwhiz.primitives.covenant_monitor import (
    CovenantInput,
    CovenantMonitor,
    TriggerDefinition,
    TriggerStatus,
)
from loanwhiz.primitives.proximity_trend_monitor import (
    ProximityTrendInput,
    ProximityTrendMonitor,
    ProximityTrendOutput,
    TriggerProjection,
    _linear_slope,
    _periods_to_breach,
    _rank_key,
    project_from_covenant_output,
    project_proximity_trends,
)
from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _status(
    name: str,
    period: str,
    proximity: float | None,
    *,
    evaluable: bool = True,
    is_triggered: bool = False,
) -> TriggerStatus:
    """Build a TriggerStatus for one trigger in one period."""
    return TriggerStatus(
        trigger_name=name,
        period=period,
        metric_value=proximity,  # value is incidental to the trend math
        threshold=100.0,
        is_triggered=is_triggered,
        proximity_pct=proximity,
        direction="n/a",
        evaluable=evaluable,
        not_evaluable_reason=None if evaluable else "unresolved",
    )


def _series(name: str, proximities: list[float | None]) -> list[TriggerStatus]:
    """A chronological series of statuses for one trigger.

    A ``None`` proximity entry is a not-evaluable period.
    """
    out: list[TriggerStatus] = []
    for i, p in enumerate(proximities):
        out.append(
            _status(
                name,
                f"2026-{i + 1:02d}-28",
                p,
                evaluable=p is not None,
                is_triggered=(p is not None and p > 100.0),
            )
        )
    return out


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestLinearSlope:
    def test_perfectly_linear_slope(self) -> None:
        # y = 50 + 10x → slope 10 per period.
        pts = [(0, 50.0), (1, 60.0), (2, 70.0)]
        assert _linear_slope(pts) == pytest.approx(10.0)

    def test_flat_series_slope_zero(self) -> None:
        assert _linear_slope([(0, 80.0), (1, 80.0), (2, 80.0)]) == pytest.approx(0.0)

    def test_improving_negative_slope(self) -> None:
        assert _linear_slope([(0, 90.0), (1, 80.0), (2, 70.0)]) == pytest.approx(-10.0)

    def test_single_point_is_none(self) -> None:
        assert _linear_slope([(0, 50.0)]) is None

    def test_empty_is_none(self) -> None:
        assert _linear_slope([]) is None


class TestPeriodsToBreach:
    def test_deteriorating_finite(self) -> None:
        # latest 70, slope 10/period → (100-70)/10 = 3 periods.
        assert _periods_to_breach(70.0, 10.0) == 3

    def test_deteriorating_rounds_up(self) -> None:
        # (100-70)/7 = 4.28 → ceil 5.
        assert _periods_to_breach(70.0, 7.0) == 5

    def test_already_breached_zero(self) -> None:
        assert _periods_to_breach(105.0, 10.0) == 0
        assert _periods_to_breach(100.0, 10.0) == 0

    def test_improving_slope_none(self) -> None:
        assert _periods_to_breach(70.0, -5.0) is None

    def test_flat_slope_none(self) -> None:
        assert _periods_to_breach(70.0, 0.0) is None

    def test_unfit_slope_none(self) -> None:
        assert _periods_to_breach(70.0, None) is None

    def test_at_least_one_period_when_close(self) -> None:
        # 99 proximity, big slope → ceil(1/1000) would be 0, floored to 1.
        assert _periods_to_breach(99.0, 1000.0) == 1


# ---------------------------------------------------------------------------
# project_proximity_trends — core behaviour
# ---------------------------------------------------------------------------


class TestProjectionBehaviour:
    def test_deteriorating_trigger_projects_finite_breach(self) -> None:
        out = project_proximity_trends(_series("t", [40.0, 60.0, 80.0]))
        proj = out.projections[0]
        assert proj.trigger_name == "t"
        assert proj.slope_per_period == pytest.approx(20.0)
        assert proj.trend == "deteriorating"
        # latest 80, slope 20 → (100-80)/20 = 1 period.
        assert proj.periods_to_breach == 1
        assert proj.projected_breach_period == "+1 period"
        assert proj.status == "projected"
        assert out.most_urgent == "t"

    def test_already_breached_trigger_zero_periods(self) -> None:
        out = project_proximity_trends(_series("t", [90.0, 100.0, 110.0]))
        proj = out.projections[0]
        assert proj.status == "breached"
        assert proj.periods_to_breach == 0
        assert proj.projected_breach_period == "now"
        assert out.most_urgent == "t"

    def test_improving_trigger_no_projected_breach(self) -> None:
        out = project_proximity_trends(_series("t", [90.0, 80.0, 70.0]))
        proj = out.projections[0]
        assert proj.trend == "improving"
        assert proj.periods_to_breach is None
        assert proj.projected_breach_period is None
        assert proj.status == "no-projected-breach"
        assert out.most_urgent is None

    def test_flat_trigger_no_projected_breach(self) -> None:
        out = project_proximity_trends(_series("t", [50.0, 50.0, 50.0]))
        proj = out.projections[0]
        assert proj.trend == "stable"
        assert proj.periods_to_breach is None
        assert proj.status == "no-projected-breach"

    def test_not_evaluable_periods_excluded_from_fit(self) -> None:
        # Real points are (0,40), (2,80) → slope 20/period over 2 ordinals.
        # The None at index 1 must NOT drag the fit toward 0.
        out = project_proximity_trends(_series("t", [40.0, None, 80.0]))
        proj = out.projections[0]
        assert proj.evaluable_points == 2
        assert proj.slope_per_period == pytest.approx(20.0)
        # latest 80, slope 20 → 1 period.
        assert proj.periods_to_breach == 1

    def test_insufficient_data_single_point(self) -> None:
        out = project_proximity_trends(_series("t", [None, 50.0]))
        proj = out.projections[0]
        assert proj.evaluable_points == 1
        assert proj.status == "insufficient-data"
        assert proj.slope_per_period is None
        assert proj.periods_to_breach is None

    def test_insufficient_data_no_points(self) -> None:
        out = project_proximity_trends(_series("t", [None, None]))
        proj = out.projections[0]
        assert proj.evaluable_points == 0
        assert proj.status == "insufficient-data"
        assert proj.latest_proximity_pct is None

    def test_single_evaluable_point_already_breached_is_breached(self) -> None:
        out = project_proximity_trends(_series("t", [None, 120.0]))
        proj = out.projections[0]
        assert proj.evaluable_points == 1
        assert proj.status == "breached"
        assert proj.periods_to_breach == 0


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


class TestRanking:
    def test_ranked_breached_then_soonest_then_no_breach(self) -> None:
        statuses = (
            _series("breached", [90.0, 100.0, 110.0])
            + _series("slow", [10.0, 12.0, 14.0])  # slope 2 → far breach
            + _series("fast", [40.0, 60.0, 80.0])  # slope 20 → 1 period
            + _series("improving", [90.0, 80.0, 70.0])
        )
        out = project_proximity_trends(statuses)
        order = [p.trigger_name for p in out.projections]
        assert order[0] == "breached"
        assert order[1] == "fast"  # sooner projected before slower
        assert order[2] == "slow"
        assert order[3] == "improving"  # no-projected-breach last
        assert out.most_urgent == "breached"

    def test_rank_key_buckets(self) -> None:
        breached = TriggerProjection(
            trigger_name="b", evaluable_points=3, latest_proximity_pct=110.0,
            slope_per_period=5.0, trend="deteriorating", periods_to_breach=0,
            projected_breach_period="now", status="breached",
        )
        projected = TriggerProjection(
            trigger_name="p", evaluable_points=3, latest_proximity_pct=80.0,
            slope_per_period=5.0, trend="deteriorating", periods_to_breach=4,
            projected_breach_period="+4 periods", status="projected",
        )
        assert _rank_key(breached) < _rank_key(projected)

    def test_most_urgent_none_when_nothing_breaching(self) -> None:
        out = project_proximity_trends(_series("t", [10.0, 10.0, 10.0]))
        assert out.most_urgent is None


# ---------------------------------------------------------------------------
# Primitive + registry + public surface
# ---------------------------------------------------------------------------


class TestPrimitive:
    def test_execute_returns_primitive_result(self) -> None:
        inp = ProximityTrendInput(trigger_statuses=_series("t", [40.0, 60.0, 80.0]))
        result = ProximityTrendMonitor().execute(inp)
        assert isinstance(result, PrimitiveResult)
        assert result.confidence == 1.0
        assert isinstance(result.output, ProximityTrendOutput)
        assert result.audit_entry.primitive_name == "proximity_trend_monitor"
        assert result.citations and isinstance(result.citations[0], Citation)
        assert result.output.most_urgent == "t"

    def test_from_covenant_output_constructor(self) -> None:
        statuses = _series("t", [40.0, 60.0, 80.0])

        class _FakeOut:
            trigger_statuses = statuses

        inp = ProximityTrendInput.from_covenant_output(_FakeOut())  # type: ignore[arg-type]
        assert inp.trigger_statuses == statuses

    def test_registry_integration(self) -> None:
        reg = PRIMITIVE_REGISTRY.get("proximity_trend_monitor")
        assert reg is not None
        assert reg.version == "0.1.0"
        assert "early-warning" in reg.tags

    def test_public_surface_export(self) -> None:
        from loanwhiz import primitives as pkg

        for name in (
            "ProximityTrendMonitor",
            "ProximityTrendInput",
            "ProximityTrendOutput",
            "TriggerProjection",
            "project_proximity_trends",
            "project_from_covenant_output",
        ):
            assert hasattr(pkg, name), name
            assert name in pkg.__all__


# ---------------------------------------------------------------------------
# End-to-end: covenant monitor → proximity trend monitor
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_e2e_from_covenant_monitor_deteriorating_loss(self) -> None:
        # A deteriorating cumulative-loss series: default_pct climbs toward the
        # 1.5% sequential-pay threshold across four periods.
        loss_trigger = TriggerDefinition(
            name="cumulative_loss_trigger",
            description="loss trend",
            metric="default_pct",
            threshold=1.5,
            direction="above",
            consequence="sequential pay",
            citation=Citation(document="P", page_or_row="5.2", excerpt="x"),
        )
        periods = [
            {"reporting_date": "2026-01-31", "default_pct": 0.6},
            {"reporting_date": "2026-02-28", "default_pct": 0.9},
            {"reporting_date": "2026-03-31", "default_pct": 1.2},
            {"reporting_date": "2026-04-30", "default_pct": 1.35},
        ]
        cov = CovenantMonitor().execute(
            CovenantInput(periods=periods, triggers=[loss_trigger])
        )
        # Proximity at latest = 1.35/1.5*100 = 90; rising → finite breach.
        trend = project_from_covenant_output(cov.output)
        proj = next(
            p for p in trend.projections if p.trigger_name == "cumulative_loss_trigger"
        )
        assert proj.status == "projected"
        assert proj.trend == "deteriorating"
        assert proj.periods_to_breach is not None
        assert proj.periods_to_breach >= 1
        assert trend.most_urgent == "cumulative_loss_trigger"

    def test_e2e_via_primitive_execute(self) -> None:
        loss_trigger = TriggerDefinition(
            name="loss",
            description="d",
            metric="default_pct",
            threshold=2.0,
            direction="above",
            consequence="c",
            citation=Citation(document="P", page_or_row="x", excerpt="x"),
        )
        periods = [
            {"reporting_date": "2026-01-31", "default_pct": 0.5},
            {"reporting_date": "2026-02-28", "default_pct": 1.0},
            {"reporting_date": "2026-03-31", "default_pct": 1.5},
        ]
        cov = CovenantMonitor().execute(
            CovenantInput(periods=periods, triggers=[loss_trigger])
        )
        out = ProximityTrendMonitor().execute(
            ProximityTrendInput.from_covenant_output(cov.output)
        ).output
        proj = out.projections[0]
        # proximity series 25 → 50 → 75 (default_pct/threshold*100); slope
        # 25/period; latest 75 → (100-75)/25 = 1 period to breach.
        assert proj.latest_proximity_pct == pytest.approx(75.0)
        assert proj.slope_per_period == pytest.approx(25.0)
        assert proj.periods_to_breach == 1
