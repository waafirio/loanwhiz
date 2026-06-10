"""Regression test for the reserve-fund-shortfall false breach (MODELING-GAPS B4).

A reserve account at or above its target is *fully funded* and must NOT trip the
reserve shortfall trigger. Before the fix, a reserve trigger with no extracted
threshold fell through the PDL heuristic in `_is_triggered` (`threshold is None`
→ "any positive value fires"), so `reserve_fund_ratio = 100` (fully funded)
spuriously reported `is_triggered = True` ("BREACHED").
"""

from __future__ import annotations

from loanwhiz.primitives.base import Citation
from loanwhiz.primitives.covenant_monitor import (
    CovenantInput,
    CovenantMonitor,
    TriggerDefinition,
)


def _reserve_trigger() -> TriggerDefinition:
    # Mirrors the deal's extracted "reserve_fund_shortfall_trigger": a reserve
    # ratio metric with no explicit threshold extracted from the prospectus.
    return TriggerDefinition(
        name="reserve_fund_shortfall_trigger",
        metric="reserve_fund_ratio",
        threshold=None,
        direction="below",
        description="Reserve fund below target level.",
        consequence="Trap available funds to top up the reserve.",
        citation=Citation(document="Prospectus", page_or_row="§6", excerpt="Reserve Fund Shortfall Trigger"),
    )


def test_fully_funded_reserve_does_not_breach():
    """reserve balance == target → ratio 100% funded → NOT triggered."""
    result = CovenantMonitor().execute(
        CovenantInput(
            periods=[{"reporting_date": "2026-04-30"}],
            triggers=[_reserve_trigger()],
            reserve_account_balance=10_000_000.0,
            reserve_account_target=10_000_000.0,  # fully funded → ratio = 100
        )
    )
    status = next(
        s
        for s in result.output.trigger_statuses
        if s.trigger_name == "reserve_fund_shortfall_trigger"
    )
    assert status.evaluable is True
    assert status.metric_value == 100.0
    assert status.is_triggered is False  # fully funded must not breach


def test_underfunded_reserve_does_breach():
    """reserve below target → ratio < 100 → IS triggered (the real shortfall)."""
    result = CovenantMonitor().execute(
        CovenantInput(
            periods=[{"reporting_date": "2026-04-30"}],
            triggers=[_reserve_trigger()],
            reserve_account_balance=7_000_000.0,
            reserve_account_target=10_000_000.0,  # 70% funded → shortfall
        )
    )
    status = next(
        s
        for s in result.output.trigger_statuses
        if s.trigger_name == "reserve_fund_shortfall_trigger"
    )
    assert status.is_triggered is True
