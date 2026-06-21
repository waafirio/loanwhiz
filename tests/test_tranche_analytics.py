"""Tests for the TrancheAnalytics primitive (#321).

All tests run against hand-built synthetic ``DealStateSeries`` instances with
known per-tranche amortization — no engine run, no network. This keeps the unit
of test the analytics derivation itself (balance-delta amortization, WAL,
principal window, pro-rata/sequential switch read-through) rather than the
upstream reconstruction engine.
"""

from __future__ import annotations

import math

import pytest

from loanwhiz.primitives.base import PrimitiveResult
from loanwhiz.primitives.covenant_monitor import TriggerEvaluation, TriggerStatus
from loanwhiz.primitives.deal_state import DealState
from loanwhiz.primitives.period_state_machine import DealStateSeries, PeriodResult
from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY
from loanwhiz.primitives.tranche_analytics import (
    TrancheAnalytics,
    TrancheAnalyticsInput,
    TrancheAnalyticsOutput,
)
from loanwhiz.primitives.waterfall_interpreter import StepResult, WaterfallExecution

SEQ_TRIGGER = "cumulative_loss_trigger"
ORIG_POOL = 1_000_000.0


def _state(
    *,
    period_index: int,
    reporting_date: str,
    a: float,
    b: float,
    c: float,
    pool: float,
) -> DealState:
    return DealState(
        reporting_date=reporting_date,
        period_index=period_index,
        class_a_balance=a,
        class_b_balance=b,
        class_c_balance=c,
        pool_balance=pool,
        original_pool_balance=ORIG_POOL,
    )


def _redemption(*, a: float = 0.0, b: float = 0.0, c: float = 0.0) -> WaterfallExecution:
    """A redemption WaterfallExecution distributing the given principal amounts."""
    steps = [
        StepResult(
            priority="(a)",
            recipient="class_a_principal",
            amount_available=a + b + c,
            need=a,
            amount_distributed=a,
            shortfall=0.0,
        ),
        StepResult(
            priority="(b)",
            recipient="class_b_principal",
            amount_available=b + c,
            need=b,
            amount_distributed=b,
            shortfall=0.0,
        ),
        StepResult(
            priority="(c)",
            recipient="class_c_principal",
            amount_available=c,
            need=c,
            amount_distributed=c,
            shortfall=0.0,
        ),
    ]
    return WaterfallExecution(
        steps=steps,
        remaining=0.0,
        total_distributed=a + b + c,
        total_shortfall=0.0,
    )


def _trigger_eval(period: str, *, sequential: bool, evaluable: bool = True) -> TriggerEvaluation:
    status = TriggerStatus(
        trigger_name=SEQ_TRIGGER,
        period=period,
        metric_value=10.0 if evaluable else None,
        threshold=5.0,
        is_triggered=sequential if evaluable else False,
        proximity_pct=200.0 if evaluable else None,
        direction="n/a",
        evaluable=evaluable,
    )
    return TriggerEvaluation(period=period, statuses={SEQ_TRIGGER: status})


def _period_result(
    closing: DealState,
    *,
    a: float = 0.0,
    b: float = 0.0,
    c: float = 0.0,
    sequential: bool = True,
    evaluable: bool = True,
) -> PeriodResult:
    revenue = WaterfallExecution(steps=[], remaining=0.0, total_distributed=0.0, total_shortfall=0.0)
    return PeriodResult(
        closing_state=closing,
        revenue_execution=revenue,
        redemption_execution=_redemption(a=a, b=b, c=c),
        trigger_evaluation=_trigger_eval(closing.reporting_date, sequential=sequential, evaluable=evaluable),
    )


def _sequential_series() -> DealStateSeries:
    """A 3-period sequential-pay series: Class A amortizes first.

    Period 1: A pays 200k (1M→800k pool). Period 2: A pays the remaining 800k
    and B starts. Period 3: B & C finish. Dates are one year apart so WAL math
    is clean.
    """
    s0 = _state(period_index=0, reporting_date="2024-01-01", a=1_000_000, b=200_000, c=100_000, pool=1_300_000)
    s1 = _state(period_index=1, reporting_date="2025-01-01", a=800_000, b=200_000, c=100_000, pool=1_100_000)
    s2 = _state(period_index=2, reporting_date="2026-01-01", a=0, b=200_000, c=100_000, pool=300_000)
    s3 = _state(period_index=3, reporting_date="2027-01-01", a=0, b=0, c=0, pool=0)
    pr = [
        _period_result(s1, a=200_000, sequential=True),
        _period_result(s2, a=800_000, sequential=True),
        _period_result(s3, b=200_000, c=100_000, sequential=True),
    ]
    return DealStateSeries(states=[s0, s1, s2, s3], period_results=pr)


def _run(series: DealStateSeries, **kwargs) -> tuple[PrimitiveResult, TrancheAnalyticsOutput]:
    prim = TrancheAnalytics()
    result = prim.execute(TrancheAnalyticsInput(series=series, **kwargs))
    assert isinstance(result.output, TrancheAnalyticsOutput)
    return result, result.output


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_registry_contains_primitive(self) -> None:
        assert "tranche_analytics" in PRIMITIVE_REGISTRY

    def test_registry_metadata(self) -> None:
        reg = PRIMITIVE_REGISTRY.get("tranche_analytics")
        assert reg is not None
        assert reg.version == "0.1.0"
        assert "wal" in reg.tags
        assert "tranche" in reg.tags


# ---------------------------------------------------------------------------
# Amortization schedule
# ---------------------------------------------------------------------------


class TestAmortization:
    def test_rows_reconcile(self) -> None:
        _, out = _run(_sequential_series())
        sched_a = next(s for s in out.schedules if s.tranche == "class_a")
        for row in sched_a.rows:
            # opening − principal == closing for every row.
            assert math.isclose(row.opening_balance - row.principal_paid, row.closing_balance)

    def test_final_closing_matches_last_state(self) -> None:
        _, out = _run(_sequential_series())
        sched_a = next(s for s in out.schedules if s.tranche == "class_a")
        # Final closing balance of the schedule == last state's class A balance (0).
        assert sched_a.final_balance == 0.0
        assert sched_a.rows[-1].closing_balance == 0.0

    def test_principal_paid_balance_delta(self) -> None:
        _, out = _run(_sequential_series())
        sched_a = next(s for s in out.schedules if s.tranche == "class_a")
        # Class A: 200k then 800k then 0, 0.
        assert [r.principal_paid for r in sched_a.rows] == [200_000, 800_000, 0, 0][: len(sched_a.rows)]

    def test_total_principal_repaid(self) -> None:
        _, out = _run(_sequential_series())
        sched_a = next(s for s in out.schedules if s.tranche == "class_a")
        assert sched_a.total_principal_repaid == 1_000_000

    def test_principal_distributed_from_trace(self) -> None:
        _, out = _run(_sequential_series())
        sched_a = next(s for s in out.schedules if s.tranche == "class_a")
        # The redemption trace corroborates the balance-delta principal.
        assert sched_a.rows[0].principal_distributed == 200_000
        assert sched_a.rows[1].principal_distributed == 800_000


# ---------------------------------------------------------------------------
# WAL
# ---------------------------------------------------------------------------


class TestWAL:
    def test_wal_matches_hand_computation(self) -> None:
        _, out = _run(_sequential_series())
        sched_a = next(s for s in out.schedules if s.tranche == "class_a")
        # Class A repays 200k at t≈1y and 800k at t≈2y.
        # WAL = (1*200k + 2*800k) / 1M = 1.8 years (actual/365.25 ≈ exact).
        assert sched_a.wal_years is not None
        assert math.isclose(sched_a.wal_years, 1.8, rel_tol=2e-3)

    def test_wal_none_when_no_principal(self) -> None:
        # A tranche that never amortizes: build a series where class C stays flat.
        s0 = _state(period_index=0, reporting_date="2024-01-01", a=100, b=0, c=500, pool=600)
        s1 = _state(period_index=1, reporting_date="2025-01-01", a=0, b=0, c=500, pool=500)
        pr = [_period_result(s1, a=100, sequential=True)]
        series = DealStateSeries(states=[s0, s1], period_results=pr)
        _, out = _run(series)
        sched_c = next(s for s in out.schedules if s.tranche == "class_c")
        assert sched_c.wal_years is None
        assert sched_c.total_principal_repaid == 0.0

    def test_wal_falls_back_to_period_index_when_dates_bad(self) -> None:
        # Reporting dates that don't parse → year-spacing proxy from period_index.
        s0 = _state(period_index=0, reporting_date="period-open", a=1000, b=0, c=0, pool=1000)
        s1 = _state(period_index=1, reporting_date="period-one", a=500, b=0, c=0, pool=500)
        s2 = _state(period_index=2, reporting_date="period-two", a=0, b=0, c=0, pool=0)
        pr = [_period_result(s1, a=500, sequential=True), _period_result(s2, a=500, sequential=True)]
        series = DealStateSeries(states=[s0, s1, s2], period_results=pr)
        _, out = _run(series)
        sched_a = next(s for s in out.schedules if s.tranche == "class_a")
        # 500 at t=1, 500 at t=2 → WAL = 1.5 (period-index proxy).
        assert sched_a.wal_years is not None
        assert math.isclose(sched_a.wal_years, 1.5)


# ---------------------------------------------------------------------------
# Principal window
# ---------------------------------------------------------------------------


class TestPrincipalWindow:
    def test_window_bounds(self) -> None:
        _, out = _run(_sequential_series())
        sched_a = next(s for s in out.schedules if s.tranche == "class_a")
        # Class A pays in periods 1 and 2.
        assert sched_a.principal_window_start_period == 1
        assert sched_a.principal_window_start_date == "2025-01-01"
        assert sched_a.principal_window_end_period == 2
        assert sched_a.principal_window_end_date == "2026-01-01"

    def test_window_empty_when_no_principal(self) -> None:
        s0 = _state(period_index=0, reporting_date="2024-01-01", a=100, b=0, c=500, pool=600)
        s1 = _state(period_index=1, reporting_date="2025-01-01", a=0, b=0, c=500, pool=500)
        pr = [_period_result(s1, a=100, sequential=True)]
        series = DealStateSeries(states=[s0, s1], period_results=pr)
        _, out = _run(series)
        sched_c = next(s for s in out.schedules if s.tranche == "class_c")
        assert sched_c.principal_window_start_period is None
        assert sched_c.principal_window_end_period is None
        assert sched_c.principal_window_start_date is None


# ---------------------------------------------------------------------------
# Switch state (pro-rata vs sequential)
# ---------------------------------------------------------------------------


class TestSwitchState:
    def test_sequential_read_through(self) -> None:
        _, out = _run(_sequential_series())
        sched_a = next(s for s in out.schedules if s.tranche == "class_a")
        # Every period was sequential & evaluable.
        for row in sched_a.rows:
            assert row.sequential_pay_active is True
            assert row.pro_rata_active is False
            assert row.switch_state_evaluable is True

    def test_pro_rata_read_through(self) -> None:
        s0 = _state(period_index=0, reporting_date="2024-01-01", a=1000, b=1000, c=0, pool=2000)
        s1 = _state(period_index=1, reporting_date="2025-01-01", a=500, b=500, c=0, pool=1000)
        # Pro-rata period: trigger NOT breached.
        pr = [_period_result(s1, a=500, b=500, sequential=False)]
        series = DealStateSeries(states=[s0, s1], period_results=pr)
        _, out = _run(series)
        sched_a = next(s for s in out.schedules if s.tranche == "class_a")
        assert sched_a.rows[0].sequential_pay_active is False
        assert sched_a.rows[0].pro_rata_active is True
        assert sched_a.rows[0].switch_state_evaluable is True

    def test_not_evaluable_falls_back_to_sequential(self) -> None:
        s0 = _state(period_index=0, reporting_date="2024-01-01", a=1000, b=0, c=0, pool=1000)
        s1 = _state(period_index=1, reporting_date="2025-01-01", a=500, b=0, c=0, pool=500)
        pr = [_period_result(s1, a=500, sequential=False, evaluable=False)]
        series = DealStateSeries(states=[s0, s1], period_results=pr)
        result, out = _run(series)
        sched_a = next(s for s in out.schedules if s.tranche == "class_a")
        # Senior-protective default: sequential active, flagged not evaluable.
        assert sched_a.rows[0].sequential_pay_active is True
        assert sched_a.rows[0].switch_state_evaluable is False
        # Confidence lowered to flag the fallback.
        assert result.confidence == 0.8

    def test_full_confidence_when_all_evaluable(self) -> None:
        result, _ = _run(_sequential_series())
        assert result.confidence == 1.0


# ---------------------------------------------------------------------------
# Envelope / inputs
# ---------------------------------------------------------------------------


class TestEnvelope:
    def test_result_envelope_well_formed(self) -> None:
        result, out = _run(_sequential_series())
        assert 0.0 <= result.confidence <= 1.0
        assert len(result.audit_entry.input_hash) == 64
        assert result.citations
        assert result.audit_entry.primitive_name == "tranche_analytics"

    def test_output_metadata(self) -> None:
        _, out = _run(_sequential_series())
        assert out.periods_analysed == 3
        assert out.series_start_date == "2024-01-01"
        assert out.series_end_date == "2027-01-01"
        assert len(out.schedules) == 3
        assert "fully repaid" in out.summary

    def test_unknown_tranche_raises(self) -> None:
        with pytest.raises(ValueError):
            _run(_sequential_series(), tranches=["class_z"])

    def test_no_period_results_uses_balance_deltas(self) -> None:
        # A series with states but no period_results (no traces): amortization
        # still derives from balance deltas; switch state falls back.
        s0 = _state(period_index=0, reporting_date="2024-01-01", a=1000, b=0, c=0, pool=1000)
        s1 = _state(period_index=1, reporting_date="2025-01-01", a=400, b=0, c=0, pool=400)
        series = DealStateSeries(states=[s0, s1], period_results=[])
        result, out = _run(series)
        sched_a = next(s for s in out.schedules if s.tranche == "class_a")
        assert sched_a.rows[0].principal_paid == 600
        assert sched_a.rows[0].principal_distributed is None
        assert sched_a.rows[0].switch_state_evaluable is False
        assert result.confidence == 0.8
