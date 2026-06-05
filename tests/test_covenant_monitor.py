"""Tests for CovenantMonitor primitive.

Covers:
1. Model definitions — TriggerDefinition, TriggerStatus, CovenantInput, CovenantOutput
2. DEFAULT_TRIGGERS structure and citation integrity.
3. Clean-data scenario: three Green Lion snapshots (Feb/Mar/Apr synthetic) — all
   non-triggered with a healthy pool (default_pct ≈ 0.0).
4. Breach scenario: synthetic period where default_pct > 1.5% — cumulative loss
   trigger fires.
5. proximity_pct computation — formulaic accuracy for "above" and "below" triggers.
6. Near-miss detection — metric within 20% of threshold flagged as near-miss.
7. Direction tracking — improving/deteriorating/stable across two periods.
8. Registry integration — covenant_monitor is registered in PRIMITIVE_REGISTRY.
"""

from __future__ import annotations

import pytest

from loanwhiz.primitives.base import Citation
from loanwhiz.primitives.covenant_monitor import (
    CovenantInput,
    CovenantMonitor,
    CovenantOutput,
    TriggerDefinition,
    TriggerEvaluation,
    TriggerStatus,
    _canonical_metric,
    _compute_direction,
    _compute_proximity,
    _extract_metric,
    _is_triggered,
    evaluate_triggers,
)
from loanwhiz.primitives.deal_state import DealState
from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY


# ---------------------------------------------------------------------------
# Shared DealState helper for the predicate-over-DealState tests
# ---------------------------------------------------------------------------


def _deal_state(
    reporting_date: str = "2026-04-30",
    *,
    class_a_pdl: float = 0.0,
    class_b_pdl: float = 0.0,
    reserve_balance: float = 10_000_000.0,
    reserve_target: float = 10_000_000.0,
    cumulative_losses: float = 0.0,
    pool_balance: float = 1_000_000_000.0,
    original_pool_balance: float = 1_000_000_000.0,
    period_index: int = 0,
) -> DealState:
    """Build a DealState directly (bypassing the seed) for trigger tests."""
    return DealState(
        reporting_date=reporting_date,
        period_index=period_index,
        class_a_balance=1_000_000_000.0,
        class_b_balance=53_100_000.0,
        class_c_balance=10_500_000.0,
        class_a_pdl=class_a_pdl,
        class_b_pdl=class_b_pdl,
        reserve_balance=reserve_balance,
        reserve_target=reserve_target,
        cumulative_losses=cumulative_losses,
        pool_balance=pool_balance,
        pool_factor=pool_balance / original_pool_balance,
        original_pool_balance=original_pool_balance,
    )

# ---------------------------------------------------------------------------
# Shared fixtures and synthetic period helpers
# ---------------------------------------------------------------------------


def _clean_period(reporting_date: str, default_pct: float = 0.0) -> dict:
    """Return a minimal EsmaTapeOutput-compatible dict for a healthy period."""
    return {
        "reporting_date": reporting_date,
        "pool_balance_eur": 950_000_000.0,
        "arrears_breakdown": {
            "current_pct": 100.0 - default_pct,
            "arrears_1_2m_pct": 0.0,
            "arrears_180d_plus_pct": 0.0,
            "default_pct": default_pct,
        },
        "pool_stats": {
            "wtd_coupon_pct": 3.5,
            "wtd_ltv": 72.0,
            "wtd_seasoning": 24.0,
            "wtd_remaining_term": 240.0,
        },
    }


def _breached_period(reporting_date: str, default_pct: float = 2.0) -> dict:
    """Return a period dict with default_pct above the 1.5% trigger threshold."""
    return _clean_period(reporting_date, default_pct=default_pct)


# Three canonical Green Lion synthetic snapshots (clean, non-triggered).
FEB_PERIOD = _clean_period("2026-02-28")
MAR_PERIOD = _clean_period("2026-03-31")
APR_PERIOD = _clean_period("2026-04-30")


# ---------------------------------------------------------------------------
# 1 — Model definitions
# ---------------------------------------------------------------------------


class TestModelDefinitions:
    """Verify the Pydantic models are importable and instantiable."""

    def test_trigger_definition_instantiates(self) -> None:
        td = TriggerDefinition(
            name="test_trigger",
            description="A test trigger.",
            metric="default_pct",
            threshold=1.5,
            direction="above",
            consequence="Something happens.",
            citation=Citation(
                document="Test Doc",
                page_or_row="p.1",
                excerpt="test excerpt",
            ),
        )
        assert td.name == "test_trigger"
        assert td.threshold == 1.5
        assert td.direction == "above"

    def test_trigger_definition_none_threshold(self) -> None:
        td = TriggerDefinition(
            name="pdl",
            description="PDL trigger.",
            metric="pdl_class_a",
            threshold=None,
            direction="above",
            consequence="PDL cure.",
            citation=Citation(document="Prospectus", page_or_row="5.3", excerpt="PDL"),
        )
        assert td.threshold is None

    def test_trigger_status_instantiates(self) -> None:
        ts = TriggerStatus(
            trigger_name="cumulative_loss_trigger",
            period="2026-04-30",
            metric_value=0.5,
            threshold=1.5,
            is_triggered=False,
            proximity_pct=33.33,
            direction="stable",
        )
        assert ts.trigger_name == "cumulative_loss_trigger"
        assert not ts.is_triggered

    def test_covenant_input_defaults(self) -> None:
        inp = CovenantInput(periods=[FEB_PERIOD])
        assert inp.class_a_pdl_balance == 0.0
        assert inp.class_b_pdl_balance == 0.0
        assert inp.reserve_account_balance == 0.0
        assert inp.reserve_account_target == 0.0
        assert inp.original_pool_balance == 0.0
        assert inp.triggers == []

    def test_covenant_output_instantiates(self) -> None:
        out = CovenantOutput(
            trigger_statuses=[],
            active_triggers=[],
            near_miss_triggers=[],
            summary="All clear.",
        )
        assert out.summary == "All clear."


# ---------------------------------------------------------------------------
# 2 — DEFAULT_TRIGGERS structure
# ---------------------------------------------------------------------------


class TestDefaultTriggers:
    """Verify the five default Green Lion triggers are correctly defined."""

    def test_five_default_triggers(self) -> None:
        assert len(CovenantMonitor.DEFAULT_TRIGGERS) == 5

    def test_cumulative_loss_trigger_definition(self) -> None:
        trigger = next(
            t for t in CovenantMonitor.DEFAULT_TRIGGERS
            if t.name == "cumulative_loss_trigger"
        )
        assert trigger.metric == "default_pct"
        assert trigger.threshold == 1.5
        assert trigger.direction == "above"
        assert "sequential" in trigger.consequence.lower()

    def test_pdl_triggers_have_none_threshold(self) -> None:
        pdl_a = next(t for t in CovenantMonitor.DEFAULT_TRIGGERS if t.name == "pdl_class_a")
        pdl_b = next(t for t in CovenantMonitor.DEFAULT_TRIGGERS if t.name == "pdl_class_b")
        assert pdl_a.threshold is None
        assert pdl_b.threshold is None

    def test_reserve_fund_trigger_definition(self) -> None:
        trigger = next(
            t for t in CovenantMonitor.DEFAULT_TRIGGERS if t.name == "reserve_fund_trigger"
        )
        assert trigger.metric == "reserve_fund_ratio"
        assert trigger.threshold == 100.0
        assert trigger.direction == "below"

    def test_clean_up_call_definition(self) -> None:
        trigger = next(
            t for t in CovenantMonitor.DEFAULT_TRIGGERS if t.name == "clean_up_call"
        )
        assert trigger.metric == "pool_balance_pct"
        assert trigger.threshold == 10.0
        assert trigger.direction == "below"

    def test_all_triggers_have_citations(self) -> None:
        for trigger in CovenantMonitor.DEFAULT_TRIGGERS:
            assert trigger.citation.document, f"{trigger.name} missing citation document"
            assert trigger.citation.excerpt, f"{trigger.name} missing citation excerpt"


# ---------------------------------------------------------------------------
# 3 — Clean data scenario (three periods, all non-triggered)
# ---------------------------------------------------------------------------


class TestCleanDataScenario:
    """Three Green Lion synthetic snapshots (Feb/Mar/Apr) — all non-triggered."""

    @pytest.fixture(scope="class")
    def result(self):
        monitor = CovenantMonitor()
        inp = CovenantInput(
            periods=[FEB_PERIOD, MAR_PERIOD, APR_PERIOD],
            original_pool_balance=1_000_000_000.0,
            reserve_account_balance=10_000_000.0,
            reserve_account_target=10_000_000.0,
        )
        return monitor.execute(inp)

    def test_no_active_triggers(self, result) -> None:
        assert result.output.active_triggers == [], (
            f"Expected no active triggers, got: {result.output.active_triggers}"
        )

    def test_no_near_miss_triggers(self, result) -> None:
        assert result.output.near_miss_triggers == [], (
            f"Expected no near-miss triggers, got: {result.output.near_miss_triggers}"
        )

    def test_trigger_statuses_count(self, result) -> None:
        # 3 periods × 5 triggers = 15 statuses
        assert len(result.output.trigger_statuses) == 15

    def test_cumulative_loss_not_triggered_any_period(self, result) -> None:
        loss_statuses = [
            s for s in result.output.trigger_statuses
            if s.trigger_name == "cumulative_loss_trigger"
        ]
        assert len(loss_statuses) == 3
        for s in loss_statuses:
            assert not s.is_triggered, (
                f"Expected non-triggered for period {s.period}, "
                f"got metric_value={s.metric_value}"
            )

    def test_confidence_is_one(self, result) -> None:
        assert result.confidence == 1.0

    def test_summary_indicates_compliance(self, result) -> None:
        assert "compliance" in result.output.summary.lower()
        # "BREACH" (uppercase) is used only in the summary when a trigger is active;
        # the all-clear message says "No breaches or near-misses detected." with lowercase.
        assert "BREACH" not in result.output.summary

    def test_audit_entry_populated(self, result) -> None:
        audit = result.audit_entry
        assert audit.primitive_name == "covenant_monitor"
        assert audit.version == "0.1.0"
        assert len(audit.input_hash) == 64
        assert audit.duration_ms >= 0.0

    def test_citations_non_empty(self, result) -> None:
        assert len(result.citations) == 5  # one per default trigger

    def test_first_period_direction_is_na(self, result) -> None:
        feb_statuses = [
            s for s in result.output.trigger_statuses if s.period == "2026-02-28"
        ]
        for s in feb_statuses:
            assert s.direction == "n/a", (
                f"First period direction must be 'n/a', got '{s.direction}' "
                f"for trigger '{s.trigger_name}'"
            )


# ---------------------------------------------------------------------------
# 4 — Breach scenario
# ---------------------------------------------------------------------------


class TestBreachScenario:
    """Synthetic period where default_pct > 1.5% — cumulative loss trigger fires."""

    @pytest.fixture(scope="class")
    def result(self):
        monitor = CovenantMonitor()
        inp = CovenantInput(
            periods=[_breached_period("2026-04-30", default_pct=2.0)],
            original_pool_balance=1_000_000_000.0,
            reserve_account_balance=10_000_000.0,
            reserve_account_target=10_000_000.0,
        )
        return monitor.execute(inp)

    def test_cumulative_loss_trigger_fires(self, result) -> None:
        assert "cumulative_loss_trigger" in result.output.active_triggers, (
            f"Expected cumulative_loss_trigger in active_triggers, "
            f"got: {result.output.active_triggers}"
        )

    def test_cumulative_loss_status_is_triggered(self, result) -> None:
        loss_status = next(
            s for s in result.output.trigger_statuses
            if s.trigger_name == "cumulative_loss_trigger"
        )
        assert loss_status.is_triggered
        assert loss_status.metric_value == 2.0

    def test_summary_mentions_breach(self, result) -> None:
        assert "BREACH" in result.output.summary.upper()

    def test_confidence_still_one(self, result) -> None:
        assert result.confidence == 1.0


class TestBreachAtExactThreshold:
    """Metric exactly at threshold should NOT trigger (trigger is strictly >)."""

    def test_at_threshold_not_triggered(self) -> None:
        monitor = CovenantMonitor()
        inp = CovenantInput(
            periods=[_clean_period("2026-04-30", default_pct=1.5)],
            original_pool_balance=1_000_000_000.0,
        )
        result = monitor.execute(inp)
        loss_status = next(
            s for s in result.output.trigger_statuses
            if s.trigger_name == "cumulative_loss_trigger"
        )
        # default_pct == 1.5 == threshold; strictly > required to trigger
        assert not loss_status.is_triggered
        assert loss_status.proximity_pct == pytest.approx(100.0, abs=0.01)


# ---------------------------------------------------------------------------
# 5 — proximity_pct computation
# ---------------------------------------------------------------------------


class TestProximityPct:
    """Unit tests for _compute_proximity helper."""

    def test_above_at_half_threshold(self) -> None:
        prox = _compute_proximity(0.75, 1.5, "above")
        assert prox == pytest.approx(50.0, abs=0.01)

    def test_above_at_threshold(self) -> None:
        prox = _compute_proximity(1.5, 1.5, "above")
        assert prox == pytest.approx(100.0, abs=0.01)

    def test_above_beyond_threshold(self) -> None:
        prox = _compute_proximity(2.0, 1.5, "above")
        assert prox == pytest.approx(133.33, abs=0.1)

    def test_above_zero_metric(self) -> None:
        prox = _compute_proximity(0.0, 1.5, "above")
        assert prox == pytest.approx(0.0, abs=0.01)

    def test_below_at_half_way(self) -> None:
        # metric=200, threshold=100 → ratio = 100/200 * 100 = 50%
        prox = _compute_proximity(200.0, 100.0, "below")
        assert prox == pytest.approx(50.0, abs=0.01)

    def test_below_at_threshold(self) -> None:
        prox = _compute_proximity(100.0, 100.0, "below")
        assert prox == pytest.approx(100.0, abs=0.01)

    def test_below_beyond_threshold(self) -> None:
        # metric=80, threshold=100 → 100/80 * 100 = 125%
        prox = _compute_proximity(80.0, 100.0, "below")
        assert prox == pytest.approx(125.0, abs=0.1)

    def test_none_threshold_returns_zero(self) -> None:
        prox = _compute_proximity(5.0, None, "above")
        assert prox == 0.0

    def test_zero_threshold_returns_zero(self) -> None:
        prox = _compute_proximity(5.0, 0.0, "above")
        assert prox == 0.0

    def test_75_pct_proximity(self) -> None:
        """Issue body: proximity_pct for metric at 75% of threshold is 75.0."""
        prox = _compute_proximity(1.125, 1.5, "above")  # 1.125 / 1.5 = 75%
        assert prox == pytest.approx(75.0, abs=0.01)


# ---------------------------------------------------------------------------
# 6 — Near-miss detection
# ---------------------------------------------------------------------------


class TestNearMissDetection:
    """Metric within 20% of threshold (proximity_pct >= 80) — flagged as near-miss."""

    def test_near_miss_at_85_pct(self) -> None:
        """Issue body: metric at 85% of threshold is classified as near-miss."""
        monitor = CovenantMonitor()
        # default_pct = 1.275 → 1.275 / 1.5 * 100 = 85%
        inp = CovenantInput(
            periods=[_clean_period("2026-04-30", default_pct=1.275)],
            original_pool_balance=1_000_000_000.0,
            reserve_account_balance=10_000_000.0,
            reserve_account_target=10_000_000.0,
        )
        result = monitor.execute(inp)
        assert "cumulative_loss_trigger" in result.output.near_miss_triggers, (
            f"Expected near-miss, got: {result.output.near_miss_triggers}"
        )
        assert "cumulative_loss_trigger" not in result.output.active_triggers

    def test_not_near_miss_below_80_pct(self) -> None:
        monitor = CovenantMonitor()
        # default_pct = 1.0 → 1.0 / 1.5 * 100 = 66.7% — not a near-miss
        inp = CovenantInput(
            periods=[_clean_period("2026-04-30", default_pct=1.0)],
            original_pool_balance=1_000_000_000.0,
        )
        result = monitor.execute(inp)
        assert "cumulative_loss_trigger" not in result.output.near_miss_triggers

    def test_triggered_not_in_near_miss(self) -> None:
        """A breached trigger must NOT appear in near_miss_triggers."""
        monitor = CovenantMonitor()
        inp = CovenantInput(
            periods=[_breached_period("2026-04-30", default_pct=2.0)],
            original_pool_balance=1_000_000_000.0,
        )
        result = monitor.execute(inp)
        assert "cumulative_loss_trigger" not in result.output.near_miss_triggers
        assert "cumulative_loss_trigger" in result.output.active_triggers

    def test_summary_mentions_near_miss(self) -> None:
        monitor = CovenantMonitor()
        inp = CovenantInput(
            periods=[_clean_period("2026-04-30", default_pct=1.275)],
            original_pool_balance=1_000_000_000.0,
            reserve_account_balance=10_000_000.0,
            reserve_account_target=10_000_000.0,
        )
        result = monitor.execute(inp)
        assert "near-miss" in result.output.summary.lower()


# ---------------------------------------------------------------------------
# 7 — Direction tracking
# ---------------------------------------------------------------------------


class TestDirectionTracking:
    """Verify improving/deteriorating/stable classification across two periods."""

    def _run_two_periods(self, first_pct: float, second_pct: float):
        monitor = CovenantMonitor()
        inp = CovenantInput(
            periods=[
                _clean_period("2026-03-31", default_pct=first_pct),
                _clean_period("2026-04-30", default_pct=second_pct),
            ],
            original_pool_balance=1_000_000_000.0,
        )
        return monitor.execute(inp)

    def test_deteriorating_when_metric_increases(self) -> None:
        # 0.5% → 1.0%: proximity increases → deteriorating
        result = self._run_two_periods(0.5, 1.0)
        apr_loss = next(
            s for s in result.output.trigger_statuses
            if s.trigger_name == "cumulative_loss_trigger" and s.period == "2026-04-30"
        )
        assert apr_loss.direction == "deteriorating"

    def test_improving_when_metric_decreases(self) -> None:
        # 1.0% → 0.5%: proximity decreases → improving
        result = self._run_two_periods(1.0, 0.5)
        apr_loss = next(
            s for s in result.output.trigger_statuses
            if s.trigger_name == "cumulative_loss_trigger" and s.period == "2026-04-30"
        )
        assert apr_loss.direction == "improving"

    def test_stable_when_change_less_than_1ppt(self) -> None:
        # 1.0% → 1.005%: delta in proximity = 0.33 ppt → stable
        result = self._run_two_periods(1.0, 1.005)
        apr_loss = next(
            s for s in result.output.trigger_statuses
            if s.trigger_name == "cumulative_loss_trigger" and s.period == "2026-04-30"
        )
        assert apr_loss.direction == "stable"

    def test_first_period_is_na(self) -> None:
        result = self._run_two_periods(0.5, 1.0)
        mar_loss = next(
            s for s in result.output.trigger_statuses
            if s.trigger_name == "cumulative_loss_trigger" and s.period == "2026-03-31"
        )
        assert mar_loss.direction == "n/a"


class TestDirectionHelpers:
    """Unit tests for _compute_direction helper directly."""

    def test_no_prior_returns_na(self) -> None:
        assert _compute_direction(50.0, None) == "n/a"

    def test_higher_proximity_is_deteriorating(self) -> None:
        assert _compute_direction(60.0, 50.0) == "deteriorating"

    def test_lower_proximity_is_improving(self) -> None:
        assert _compute_direction(40.0, 50.0) == "improving"

    def test_small_delta_is_stable(self) -> None:
        assert _compute_direction(50.5, 50.0) == "stable"


# ---------------------------------------------------------------------------
# 8 — PDL trigger
# ---------------------------------------------------------------------------


class TestPdlTrigger:
    """PDL triggers fire on any positive (debit) balance."""

    def test_pdl_class_a_triggers_when_positive(self) -> None:
        monitor = CovenantMonitor()
        inp = CovenantInput(
            periods=[FEB_PERIOD],
            class_a_pdl_balance=100_000.0,  # positive = debit balance
        )
        result = monitor.execute(inp)
        assert "pdl_class_a" in result.output.active_triggers

    def test_pdl_class_a_not_triggered_when_zero(self) -> None:
        monitor = CovenantMonitor()
        inp = CovenantInput(periods=[FEB_PERIOD], class_a_pdl_balance=0.0)
        result = monitor.execute(inp)
        assert "pdl_class_a" not in result.output.active_triggers

    def test_pdl_class_b_triggers_when_positive(self) -> None:
        monitor = CovenantMonitor()
        inp = CovenantInput(
            periods=[FEB_PERIOD],
            class_b_pdl_balance=50_000.0,
        )
        result = monitor.execute(inp)
        assert "pdl_class_b" in result.output.active_triggers


# ---------------------------------------------------------------------------
# 9 — Reserve fund trigger
# ---------------------------------------------------------------------------


class TestReserveFundTrigger:
    """Reserve fund trigger fires when balance < target."""

    def test_reserve_fund_triggers_when_below_target(self) -> None:
        monitor = CovenantMonitor()
        inp = CovenantInput(
            periods=[FEB_PERIOD],
            reserve_account_balance=8_000_000.0,
            reserve_account_target=10_000_000.0,
        )
        result = monitor.execute(inp)
        assert "reserve_fund_trigger" in result.output.active_triggers

    def test_reserve_fund_not_triggered_when_at_target(self) -> None:
        monitor = CovenantMonitor()
        inp = CovenantInput(
            periods=[FEB_PERIOD],
            reserve_account_balance=10_000_000.0,
            reserve_account_target=10_000_000.0,
        )
        result = monitor.execute(inp)
        assert "reserve_fund_trigger" not in result.output.active_triggers

    def test_reserve_fund_proximity_when_80_pct_funded(self) -> None:
        monitor = CovenantMonitor()
        inp = CovenantInput(
            periods=[FEB_PERIOD],
            reserve_account_balance=8_000_000.0,
            reserve_account_target=10_000_000.0,
        )
        result = monitor.execute(inp)
        reserve_status = next(
            s for s in result.output.trigger_statuses
            if s.trigger_name == "reserve_fund_trigger"
        )
        # metric = 80% of target; threshold = 100%; proximity = 100/80 * 100 = 125%
        assert reserve_status.proximity_pct == pytest.approx(125.0, abs=0.1)


# ---------------------------------------------------------------------------
# 10 — Clean-up call trigger
# ---------------------------------------------------------------------------


class TestCleanUpCallTrigger:
    """Clean-up call fires when pool balance < 10% of original."""

    def test_cleanup_fires_below_10_pct(self) -> None:
        monitor = CovenantMonitor()
        period = dict(FEB_PERIOD)
        period["pool_balance_eur"] = 80_000_000.0  # 8% of 1B
        inp = CovenantInput(
            periods=[period],
            original_pool_balance=1_000_000_000.0,
        )
        result = monitor.execute(inp)
        assert "clean_up_call" in result.output.active_triggers

    def test_cleanup_not_triggered_above_10_pct(self) -> None:
        monitor = CovenantMonitor()
        period = dict(FEB_PERIOD)
        period["pool_balance_eur"] = 950_000_000.0  # 95% of 1B
        inp = CovenantInput(
            periods=[period],
            original_pool_balance=1_000_000_000.0,
        )
        result = monitor.execute(inp)
        assert "clean_up_call" not in result.output.active_triggers


# ---------------------------------------------------------------------------
# 11 — Custom triggers
# ---------------------------------------------------------------------------


class TestCustomTriggers:
    """When CovenantInput.triggers is populated, DEFAULT_TRIGGERS are not used."""

    def test_custom_trigger_replaces_defaults(self) -> None:
        custom_trigger = TriggerDefinition(
            name="custom_test",
            description="Test custom trigger.",
            metric="default_pct",
            threshold=0.5,
            direction="above",
            consequence="Custom consequence.",
            citation=Citation(
                document="Custom Doc",
                page_or_row="p.1",
                excerpt="custom",
            ),
        )
        monitor = CovenantMonitor()
        inp = CovenantInput(
            periods=[_clean_period("2026-04-30", default_pct=0.6)],
            triggers=[custom_trigger],
        )
        result = monitor.execute(inp)
        # Only one trigger evaluated
        assert len(result.output.trigger_statuses) == 1
        assert result.output.trigger_statuses[0].trigger_name == "custom_test"
        assert result.output.trigger_statuses[0].is_triggered

    def test_empty_periods_produces_empty_output(self) -> None:
        monitor = CovenantMonitor()
        inp = CovenantInput(periods=[])
        result = monitor.execute(inp)
        assert result.output.trigger_statuses == []
        assert result.output.active_triggers == []
        assert result.output.near_miss_triggers == []


# ---------------------------------------------------------------------------
# 12 — Registry integration
# ---------------------------------------------------------------------------


class TestRegistration:
    """covenant_monitor must be registered in PRIMITIVE_REGISTRY."""

    def test_registered_in_primitive_registry(self) -> None:
        assert "covenant_monitor" in PRIMITIVE_REGISTRY

    def test_describe_returns_non_empty_schemas(self) -> None:
        meta = CovenantMonitor.describe()
        assert meta.name == "covenant_monitor"
        assert meta.version == "0.1.0"
        assert meta.input_schema, "input_schema must not be empty"
        assert meta.output_schema, "output_schema must not be empty"

    def test_registry_entry_has_correct_tags(self) -> None:
        reg = PRIMITIVE_REGISTRY.get("covenant_monitor")
        assert reg is not None
        assert "covenant" in reg.tags
        assert "compliance" in reg.tags


# ---------------------------------------------------------------------------
# 13 — _extract_metric helper
# ---------------------------------------------------------------------------


class TestExtractMetric:
    """Unit tests for the metric extraction helper."""

    def test_extract_from_arrears_breakdown(self) -> None:
        period = {"arrears_breakdown": {"default_pct": 1.2}}
        inp = CovenantInput(periods=[period])
        val = _extract_metric(period, "default_pct", inp)
        assert val == pytest.approx(1.2)

    def test_extract_pdl_class_a(self) -> None:
        inp = CovenantInput(periods=[{}], class_a_pdl_balance=50_000.0)
        val = _extract_metric({}, "pdl_class_a", inp)
        assert val == pytest.approx(50_000.0)

    def test_extract_reserve_fund_ratio_fully_funded(self) -> None:
        inp = CovenantInput(
            periods=[{}],
            reserve_account_balance=10_000_000.0,
            reserve_account_target=10_000_000.0,
        )
        val = _extract_metric({}, "reserve_fund_ratio", inp)
        assert val == pytest.approx(100.0)

    def test_extract_reserve_fund_ratio_partially_funded(self) -> None:
        inp = CovenantInput(
            periods=[{}],
            reserve_account_balance=8_000_000.0,
            reserve_account_target=10_000_000.0,
        )
        val = _extract_metric({}, "reserve_fund_ratio", inp)
        assert val == pytest.approx(80.0)

    def test_extract_pool_balance_pct(self) -> None:
        period = {"pool_balance_eur": 100_000_000.0}
        inp = CovenantInput(periods=[period], original_pool_balance=1_000_000_000.0)
        val = _extract_metric(period, "pool_balance_pct", inp)
        assert val == pytest.approx(10.0)

    def test_missing_key_returns_none_not_zero(self) -> None:
        # Honest not-evaluable: an unresolvable metric is None, never a fake 0.
        period: dict = {}
        inp = CovenantInput(periods=[period])
        val = _extract_metric(period, "nonexistent_metric", inp)
        assert val is None


# ---------------------------------------------------------------------------
# 14 — _is_triggered helper
# ---------------------------------------------------------------------------


class TestIsTriggered:
    """Unit tests for the trigger evaluation helper."""

    def test_above_triggered(self) -> None:
        assert _is_triggered(2.0, 1.5, "above") is True

    def test_above_not_triggered(self) -> None:
        assert _is_triggered(1.0, 1.5, "above") is False

    def test_above_at_threshold_not_triggered(self) -> None:
        assert _is_triggered(1.5, 1.5, "above") is False

    def test_below_triggered(self) -> None:
        assert _is_triggered(80.0, 100.0, "below") is True

    def test_below_not_triggered(self) -> None:
        assert _is_triggered(100.0, 100.0, "below") is False

    def test_none_threshold_fires_on_positive(self) -> None:
        assert _is_triggered(0.01, None, "above") is True

    def test_none_threshold_not_fires_on_zero(self) -> None:
        assert _is_triggered(0.0, None, "above") is False


# ---------------------------------------------------------------------------
# 15 — Metric-vocabulary alias resolution (the extractor↔monitor mismatch fix)
# ---------------------------------------------------------------------------


class TestMetricAliasResolution:
    """Extractor vocabulary must resolve onto the canonical monitor sentinels."""

    def test_canonical_metric_passthrough(self) -> None:
        # Already-canonical sentinels are unchanged.
        assert _canonical_metric("pdl_class_a") == "pdl_class_a"
        assert _canonical_metric("reserve_fund_ratio") == "reserve_fund_ratio"
        # An unknown/unaliased name passes through verbatim.
        assert _canonical_metric("some_tape_field") == "some_tape_field"

    def test_default_pct_canonicalises_to_loss_rate(self) -> None:
        # The sequential-pay proxy resolves to the structural loss rate, so a
        # DealState drives it; the period-dict default_pct still resolves via
        # the dual-name lookup in _extract_metric.
        assert _canonical_metric("default_pct") == "cumulative_loss_rate_pct"

    @pytest.mark.parametrize(
        "extracted, canonical",
        [
            ("pdl_debit_balance", "pdl_class_a"),
            ("class_b_pdl_balance", "pdl_class_b"),
            ("reserve_fund_balance", "reserve_fund_ratio"),
            ("pool_balance_fraction", "pool_balance_pct"),
            ("pool_factor", "pool_balance_pct"),
            ("cumulative_loss_rate_pct", "cumulative_loss_rate_pct"),
            ("cumulative_net_loss_rate", "cumulative_loss_rate_pct"),
        ],
    )
    def test_alias_maps_extractor_to_sentinel(self, extracted, canonical) -> None:
        assert _canonical_metric(extracted) == canonical

    def test_alias_is_case_insensitive(self) -> None:
        assert _canonical_metric("PDL_Debit_Balance") == "pdl_class_a"

    def test_extracted_pdl_metric_resolves_not_silent_zero(self) -> None:
        """An extracted ``pdl_debit_balance`` metric reads the structural state."""
        state = _deal_state(class_a_pdl=250_000.0)
        val = _extract_metric({}, "pdl_debit_balance", CovenantInput(periods=[{}]), state)
        assert val == pytest.approx(250_000.0)

    def test_extracted_reserve_metric_resolves(self) -> None:
        state = _deal_state(reserve_balance=8_000_000.0, reserve_target=10_000_000.0)
        val = _extract_metric(
            {}, "reserve_fund_balance", CovenantInput(periods=[{}]), state
        )
        assert val == pytest.approx(80.0)

    def test_extracted_trigger_with_aliased_metric_fires(self) -> None:
        """End-to-end: an extracted trigger whose metric is the extractor name
        evaluates against real state rather than a silent 0.0."""
        trigger = TriggerDefinition(
            name="class_a_pdl_trigger",
            description="Class A PDL debit.",
            metric="pdl_debit_balance",  # extractor vocabulary, not a sentinel
            threshold=None,
            direction="above",
            consequence="PDL cure.",
            citation=Citation(document="P", page_or_row="5.3", excerpt="PDL"),
        )
        state = _deal_state(class_a_pdl=100_000.0)
        monitor = CovenantMonitor()
        result = monitor.execute(
            CovenantInput(
                periods=[{"reporting_date": "2026-04-30"}],
                triggers=[trigger],
                period_states=[state],
            )
        )
        assert "class_a_pdl_trigger" in result.output.active_triggers


# ---------------------------------------------------------------------------
# 16 — Not-evaluable distinguished from a genuine 0
# ---------------------------------------------------------------------------


class TestNotEvaluable:
    """An unresolvable metric is honestly not-evaluable, never a fake 0."""

    def test_unknown_metric_marked_not_evaluable(self) -> None:
        trigger = TriggerDefinition(
            name="mystery",
            description="Unknown metric.",
            metric="totally_unknown_metric",
            threshold=5.0,
            direction="above",
            consequence="?",
            citation=Citation(document="P", page_or_row="x", excerpt="x"),
        )
        monitor = CovenantMonitor()
        result = monitor.execute(
            CovenantInput(periods=[{"reporting_date": "2026-04-30"}], triggers=[trigger])
        )
        status = result.output.trigger_statuses[0]
        assert status.evaluable is False
        assert status.metric_value is None
        assert status.proximity_pct is None
        assert status.is_triggered is False
        assert status.not_evaluable_reason is not None

    def test_not_evaluable_excluded_from_active_and_near_miss(self) -> None:
        trigger = TriggerDefinition(
            name="mystery",
            description="Unknown metric.",
            metric="totally_unknown_metric",
            threshold=5.0,
            direction="above",
            consequence="?",
            citation=Citation(document="P", page_or_row="x", excerpt="x"),
        )
        monitor = CovenantMonitor()
        result = monitor.execute(
            CovenantInput(periods=[{"reporting_date": "2026-04-30"}], triggers=[trigger])
        )
        assert "mystery" not in result.output.active_triggers
        assert "mystery" not in result.output.near_miss_triggers
        assert "mystery" in result.output.unevaluable_triggers

    def test_reserve_with_no_target_is_not_evaluable_not_fake_100(self) -> None:
        """The old code returned a fake 100% when no target was set; now None."""
        val = _extract_metric(
            {}, "reserve_fund_ratio", CovenantInput(periods=[{}])
        )
        assert val is None

    def test_summary_distinguishes_not_evaluable_from_compliant(self) -> None:
        trigger = TriggerDefinition(
            name="mystery",
            description="Unknown metric.",
            metric="totally_unknown_metric",
            threshold=5.0,
            direction="above",
            consequence="?",
            citation=Citation(document="P", page_or_row="x", excerpt="x"),
        )
        monitor = CovenantMonitor()
        result = monitor.execute(
            CovenantInput(periods=[{"reporting_date": "2026-04-30"}], triggers=[trigger])
        )
        assert "not evaluable" in result.output.summary.lower()


# ---------------------------------------------------------------------------
# 17 — Each trigger as a predicate over DealState
# ---------------------------------------------------------------------------


class TestTriggersOverDealState:
    """Sequential-pay/loss, PDL A/B, reserve, clean-up evaluated over DealState."""

    def test_class_a_pdl_fires_from_state(self) -> None:
        state = _deal_state(class_a_pdl=50_000.0)
        ev = evaluate_triggers(state)
        assert ev.is_triggered("pdl_class_a")

    def test_class_a_pdl_clean_when_zero(self) -> None:
        ev = evaluate_triggers(_deal_state(class_a_pdl=0.0))
        assert not ev.is_triggered("pdl_class_a")

    def test_class_b_pdl_fires_from_state(self) -> None:
        ev = evaluate_triggers(_deal_state(class_b_pdl=25_000.0))
        assert ev.is_triggered("pdl_class_b")

    def test_reserve_fund_fires_below_target(self) -> None:
        state = _deal_state(reserve_balance=8_000_000.0, reserve_target=10_000_000.0)
        ev = evaluate_triggers(state)
        assert ev.is_triggered("reserve_fund_trigger")

    def test_reserve_fund_clean_at_target(self) -> None:
        state = _deal_state(reserve_balance=10_000_000.0, reserve_target=10_000_000.0)
        ev = evaluate_triggers(state)
        assert not ev.is_triggered("reserve_fund_trigger")

    def test_clean_up_call_fires_below_10pct(self) -> None:
        state = _deal_state(pool_balance=80_000_000.0, original_pool_balance=1_000_000_000.0)
        ev = evaluate_triggers(state)
        assert ev.is_triggered("clean_up_call")

    def test_clean_up_call_clean_above_10pct(self) -> None:
        state = _deal_state(pool_balance=900_000_000.0, original_pool_balance=1_000_000_000.0)
        ev = evaluate_triggers(state)
        assert not ev.is_triggered("clean_up_call")

    def test_cumulative_loss_sequential_pay_fires_from_state(self) -> None:
        # 2% cumulative loss rate > 1.5% threshold → sequential-pay trigger.
        state = _deal_state(
            cumulative_losses=20_000_000.0, original_pool_balance=1_000_000_000.0
        )
        ev = evaluate_triggers(state)
        assert ev.is_triggered("cumulative_loss_trigger")

    def test_cumulative_loss_clean_below_threshold(self) -> None:
        state = _deal_state(
            cumulative_losses=5_000_000.0, original_pool_balance=1_000_000_000.0
        )
        ev = evaluate_triggers(state)
        assert not ev.is_triggered("cumulative_loss_trigger")


# ---------------------------------------------------------------------------
# 18 — Predicate interface (the S4 seam)
# ---------------------------------------------------------------------------


class TestTriggerEvaluationInterface:
    """evaluate_triggers / TriggerEvaluation — the interface S4 (#184) consumes."""

    def test_returns_status_per_trigger_keyed_by_name(self) -> None:
        ev = evaluate_triggers(_deal_state())
        assert isinstance(ev, TriggerEvaluation)
        names = {t.name for t in CovenantMonitor.DEFAULT_TRIGGERS}
        assert set(ev.statuses.keys()) == names

    def test_is_triggered_unknown_name_is_false(self) -> None:
        ev = evaluate_triggers(_deal_state())
        assert ev.is_triggered("no_such_trigger") is False

    def test_evaluable_predicate(self) -> None:
        ev = evaluate_triggers(_deal_state())
        assert ev.evaluable("pdl_class_a") is True

    def test_active_lists_only_breached_evaluable(self) -> None:
        state = _deal_state(class_a_pdl=10_000.0)
        ev = evaluate_triggers(state)
        assert "pdl_class_a" in ev.active
        assert "reserve_fund_trigger" not in ev.active

    def test_custom_triggers_respected(self) -> None:
        custom = TriggerDefinition(
            name="only_one",
            description="x",
            metric="pdl_class_a",
            threshold=None,
            direction="above",
            consequence="x",
            citation=Citation(document="P", page_or_row="x", excerpt="x"),
        )
        ev = evaluate_triggers(_deal_state(class_a_pdl=1.0), triggers=[custom])
        assert set(ev.statuses.keys()) == {"only_one"}
        assert ev.is_triggered("only_one")


# ---------------------------------------------------------------------------
# 19 — Non-flat proximity series from per-period DealState
# ---------------------------------------------------------------------------


class TestNonFlatProximitySeries:
    """Per-period DealStates produce a real, moving proximity series."""

    def test_reserve_proximity_is_not_flat(self) -> None:
        # Reserve draws down over three periods: 100% → 90% → 80% funded.
        states = [
            _deal_state("2026-02-28", reserve_balance=10_000_000.0, reserve_target=10_000_000.0, period_index=0),
            _deal_state("2026-03-31", reserve_balance=9_000_000.0, reserve_target=10_000_000.0, period_index=1),
            _deal_state("2026-04-30", reserve_balance=8_000_000.0, reserve_target=10_000_000.0, period_index=2),
        ]
        periods = [{"reporting_date": s.reporting_date} for s in states]
        monitor = CovenantMonitor()
        result = monitor.execute(
            CovenantInput(periods=periods, period_states=states)
        )
        reserve_series = [
            s.proximity_pct
            for s in result.output.trigger_statuses
            if s.trigger_name == "reserve_fund_trigger"
        ]
        # Three distinct values — not a flat line.
        assert len(reserve_series) == 3
        assert len(set(reserve_series)) == 3
        # Deteriorating: proximity rises as the reserve drains toward breach.
        assert reserve_series[0] < reserve_series[1] < reserve_series[2]

    def test_pdl_proximity_moves_with_state(self) -> None:
        states = [
            _deal_state("2026-02-28", class_a_pdl=0.0, period_index=0),
            _deal_state("2026-03-31", class_a_pdl=100_000.0, period_index=1),
        ]
        periods = [{"reporting_date": s.reporting_date} for s in states]
        monitor = CovenantMonitor()
        result = monitor.execute(CovenantInput(periods=periods, period_states=states))
        pdl_statuses = [
            s for s in result.output.trigger_statuses if s.trigger_name == "pdl_class_a"
        ]
        # First period clean, second period breached — real movement.
        assert pdl_statuses[0].is_triggered is False
        assert pdl_statuses[1].is_triggered is True

    def test_from_deal_states_constructor(self) -> None:
        states = [
            _deal_state("2026-02-28", cumulative_losses=0.0, period_index=0),
            _deal_state("2026-03-31", cumulative_losses=20_000_000.0, period_index=1),
        ]
        inp = CovenantInput.from_deal_states(states)
        assert inp.period_states is not None
        assert len(inp.periods) == 2
        assert inp.original_pool_balance == 1_000_000_000.0
        monitor = CovenantMonitor()
        result = monitor.execute(inp)
        loss_statuses = [
            s for s in result.output.trigger_statuses
            if s.trigger_name == "cumulative_loss_trigger"
        ]
        assert loss_statuses[0].is_triggered is False
        assert loss_statuses[1].is_triggered is True
