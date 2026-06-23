"""Tests for the S8 comprehensiveness invariants (#188).

Covers :mod:`loanwhiz.primitives.series_invariants` — the invariant suite
asserted over S6's reconstructed ``DealStateSeries``:

1. **Healthy chain** — a synthetic multi-period chain (Green-Lion figures
   supplied as data) passes every invariant clean: ``report.ok`` and zero
   findings.
2. **Each invariant fires** — a copy of the series is deliberately corrupted to
   break exactly one invariant, and the matching ``error`` finding is asserted.
3. **Honest shortfall** — a revenue-starved period (the S6 ``reserve_draw=0``
   path: insufficient interest to pay senior interest, with no auto reserve
   draw) surfaces a ``warning`` shortfall finding while ``error`` findings stay
   empty — the shortfall is reported, not hidden.

Green Lion 2026-1 figures are used as a *concrete* deal supplied as data to the
engine, never hardcoded into the module under test.
"""

from __future__ import annotations

import pytest

from loanwhiz.primitives.deal_state import PeriodCollections
from loanwhiz.primitives.period_state_machine import (
    DEFAULT_REDEMPTION_STEPS,
    DEFAULT_REVENUE_STEPS,
    PeriodInput,
    reconstruct_period_series,
)
from loanwhiz.primitives.series_invariants import (
    InvariantViolation,
    assert_series_invariants,
    check_series,
)

# ---------------------------------------------------------------------------
# Concrete deal figures (Green Lion 2026-1) — supplied as data to the engine.
# ---------------------------------------------------------------------------

_CAP_STRUCTURE = {
    "class_a_balance": 1_000_000_000.0,
    "class_a_rate_pct": 3.62,
    "class_b_balance": 53_100_000.0,
    "class_b_rate_pct": 4.50,
    "class_c_balance": 10_500_000.0,
    "class_c_rate_pct": 6.00,
}
_RESERVE_TARGET = 10_636_000.0
_ORIGINAL_POOL = 1_063_600_000.0


def _healthy_series(n_periods: int = 3):
    """A well-funded multi-period chain that satisfies every invariant."""
    collections = [
        PeriodCollections(
            interest=8_000_000.0,
            scheduled_principal=20_000_000.0,
            prepayment=5_000_000.0,
            recovery=100_000.0,
            realized_loss=400_000.0,
        ),
        PeriodCollections(
            interest=7_500_000.0,
            scheduled_principal=18_000_000.0,
            prepayment=4_000_000.0,
            recovery=80_000.0,
            realized_loss=250_000.0,
        ),
        PeriodCollections(
            interest=7_200_000.0,
            scheduled_principal=17_000_000.0,
            prepayment=3_500_000.0,
            recovery=60_000.0,
            realized_loss=150_000.0,
        ),
    ]
    dates = ["2026-03-31", "2026-04-30", "2026-05-31"]
    periods = [
        PeriodInput(reporting_date=dates[i], collections=collections[i])
        for i in range(n_periods)
    ]
    return reconstruct_period_series(
        capital_structure=_CAP_STRUCTURE,
        reserve_target=_RESERVE_TARGET,
        original_pool_balance=_ORIGINAL_POOL,
        seed_reporting_date="2026-02-28",
        periods=periods,
    )


def _check(series):
    return check_series(
        series,
        revenue_steps=DEFAULT_REVENUE_STEPS,
        redemption_steps=DEFAULT_REDEMPTION_STEPS,
    )


# ===========================================================================
# 1. Healthy chain — every invariant holds
# ===========================================================================


def test_healthy_chain_passes_all_invariants():
    series = _healthy_series()
    report = _check(series)

    assert report.ok is True
    assert report.errors == []
    assert report.periods_checked == 3
    # A well-funded chain has no shortfall warnings either.
    assert report.warnings == []
    assert "hold" in report.summary


def test_assert_series_invariants_returns_report_on_clean_series():
    series = _healthy_series()
    report = assert_series_invariants(
        series,
        revenue_steps=DEFAULT_REVENUE_STEPS,
        redemption_steps=DEFAULT_REDEMPTION_STEPS,
    )
    assert report.ok is True


def test_cumulative_losses_are_monotonic_on_healthy_chain():
    # The healthy chain injects positive losses every period; the series must
    # show non-decreasing cumulative losses (and the invariant must agree).
    series = _healthy_series()
    losses = [s.cumulative_losses for s in series.states]
    assert losses == sorted(losses)
    assert _check(series).by_invariant("loss_monotonicity") == []


# ===========================================================================
# 2. Each invariant fires on a deliberately-corrupted series
# ===========================================================================


def test_step_coverage_fires_when_a_step_is_skipped():
    series = _healthy_series(1)
    result = series.period_results[0]
    execution = result.revenue_execution
    # Drop the first executed step from the trace → it was "silently skipped".
    skipped = execution.steps[0].recipient
    truncated = execution.model_copy(update={"steps": execution.steps[1:]})
    broken_result = result.model_copy(update={"revenue_execution": truncated})
    broken = series.model_copy(update={"period_results": [broken_result]})

    report = _check(broken)

    assert report.ok is False
    coverage = report.by_invariant("step_coverage")
    assert coverage, "expected a step_coverage finding"
    assert any(f.recipient == skipped for f in coverage)


def test_conservation_fires_when_distribution_does_not_match_available():
    series = _healthy_series(1)
    result = series.period_results[0]
    execution = result.revenue_execution
    # Inflate total_distributed so distributed + remaining != available.
    tampered = execution.model_copy(
        update={"total_distributed": execution.total_distributed + 1_000_000.0}
    )
    broken_result = result.model_copy(update={"revenue_execution": tampered})
    broken = series.model_copy(update={"period_results": [broken_result]})

    report = _check(broken)

    assert report.ok is False
    conservation = report.by_invariant("conservation")
    assert conservation
    assert any(f.recipient == "revenue" for f in conservation)


def test_non_negative_fires_on_a_negative_balance():
    series = _healthy_series(1)
    # Force a negative reserve onto the final state (bypassing the clamp via a
    # direct field update on a copy — pydantic ge=0.0 blocks construction, so we
    # patch around it to prove the series-level invariant is a real check).
    final = series.states[-1]
    negative = final.model_construct(
        **{**final.__dict__, "reserve_balance": -5.0}
    )
    broken = series.model_copy(update={"states": series.states[:-1] + [negative]})

    report = _check(broken)

    assert report.ok is False
    non_neg = report.by_invariant("non_negative")
    assert non_neg
    assert any(f.recipient == "reserve_balance" for f in non_neg)


def test_chaining_fires_when_closing_does_not_equal_next_opening():
    series = _healthy_series(2)
    # Tamper the carried-forward opening of period 2 so it no longer equals the
    # closing the period-1 transition produced. The Class A balance now lives in
    # the canonical ``tranches`` list (``class_a_balance`` is a read accessor over
    # it), so the tamper edits the matching tranche entry.
    next_state = series.states[1]
    tampered = next_state.model_copy(
        update={
            "tranches": [
                t.model_copy(update={"balance": t.balance + 12_345.0})
                if t.name == "class_a"
                else t
                for t in next_state.tranches
            ]
        }
    )
    assert tampered.class_a_balance == next_state.class_a_balance + 12_345.0
    broken = series.model_copy(
        update={"states": [series.states[0], tampered, series.states[2]]}
    )

    report = _check(broken)

    assert report.ok is False
    chaining = report.by_invariant("chaining")
    assert chaining
    assert any(f.recipient == "class_a_balance" for f in chaining)


def test_loss_monotonicity_fires_when_losses_decrease():
    series = _healthy_series(2)
    # Make the final state's cumulative losses drop below the prior state's.
    prior = series.states[1].cumulative_losses
    regressed = series.states[2].model_copy(
        update={"cumulative_losses": max(0.0, prior - 1.0)}
    )
    broken = series.model_copy(
        update={"states": [series.states[0], series.states[1], regressed]}
    )

    report = _check(broken)

    assert report.ok is False
    mono = report.by_invariant("loss_monotonicity")
    assert mono
    assert mono[0].recipient == "cumulative_losses"


def test_assert_series_invariants_raises_on_violation():
    series = _healthy_series(2)
    regressed = series.states[2].model_copy(update={"cumulative_losses": 0.0})
    broken = series.model_copy(
        update={"states": [series.states[0], series.states[1], regressed]}
    )
    with pytest.raises(InvariantViolation) as exc:
        assert_series_invariants(
            broken,
            revenue_steps=DEFAULT_REVENUE_STEPS,
            redemption_steps=DEFAULT_REDEMPTION_STEPS,
        )
    # The raised error carries the structured report for inspection.
    assert exc.value.report.ok is False
    assert exc.value.report.errors


# ===========================================================================
# 3. Honest shortfall reporting (the reserve_draw=0 path)
# ===========================================================================


def test_revenue_shortfall_surfaces_as_warning_not_error():
    # A period with negligible interest cannot cover senior interest; with no
    # auto reserve draw (the S6 reserve_draw=0 finding) the waterfall reports an
    # unmet shortfall. S8 must surface that honestly as a *warning*, while the
    # accounting (conservation, non-negativity, chaining) stays clean.
    periods = [
        PeriodInput(
            reporting_date="2026-03-31",
            collections=PeriodCollections(
                interest=100.0, scheduled_principal=1_000.0
            ),
        )
    ]
    series = reconstruct_period_series(
        capital_structure=_CAP_STRUCTURE,
        reserve_target=_RESERVE_TARGET,
        original_pool_balance=_ORIGINAL_POOL,
        seed_reporting_date="2026-02-28",
        periods=periods,
    )

    report = _check(series)

    # The model is not *incorrect* — funds are conserved and balances stay
    # non-negative — so ``ok`` stays True despite the economic shortfall.
    assert report.ok is True
    assert report.errors == []

    shortfalls = report.by_invariant("shortfall")
    assert shortfalls, "expected a shortfall warning"
    assert all(f.severity == "warning" for f in shortfalls)
    revenue_shortfall = next(f for f in shortfalls if f.recipient == "revenue")
    assert revenue_shortfall.observed > 0.0
    assert "warning" in report.summary
