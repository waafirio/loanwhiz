"""Tests for the engine-validation harness (V4 / #210) — the headline proof.

These tests drive the full reconciliation of the model-driven waterfall engine
against Green Lion 2024-1's OWN published Notes & Cash report, using the committed
seed ``DealModel`` and the committed V3 report fixture (no network, no LLM — fast
suite). The headline assertions:

- the deal's revenue Priority of Payments reconciles **to the cent**, including
  Class A interest computed by the engine from balances/rates with **no report
  input**;
- the revenue residual sweep lands on step ``(k)`` and the engine total ties to
  Total Available Revenue Funds;
- the redemption PoP reconciles, with the report's own documented
  "Unapplied … due to rounding" €0.69 remainder accounted for honestly (not
  fudged into a step);
- the report distinguishes engine-computed from report-supplied lines.
"""

from __future__ import annotations

import pytest

from loanwhiz.primitives.engine_validation_harness import (
    DEFAULT_TOLERANCE_EUR,
    EngineValidationReport,
    PeriodValidation,
    _fold_report_revenue_steps,
    build_funds_for_period,
    load_green_lion_2024_1_model,
    load_green_lion_2024_1_periods,
    reconcile_engine,
    validate_green_lion_2024_1,
)


@pytest.fixture()
def report() -> EngineValidationReport:
    return validate_green_lion_2024_1()


@pytest.fixture()
def period(report: EngineValidationReport) -> PeriodValidation:
    return report.periods[0]


# ---------------------------------------------------------------------------
# Offline loading — the committed seed + fixture
# ---------------------------------------------------------------------------


def test_loads_seed_model_and_fixture_offline() -> None:
    model = load_green_lion_2024_1_model()
    assert model.metadata.deal_name == "Green Lion 2024-1 B.V."
    assert "revenue" in model.waterfalls and "redemption" in model.waterfalls
    assert len(model.waterfalls["revenue"]["steps"]) == 11
    assert len(model.waterfalls["redemption"]["steps"]) == 4

    periods = load_green_lion_2024_1_periods()
    assert len(periods) == 1
    assert periods[0].reporting_date == "2026-04-23"


# ---------------------------------------------------------------------------
# WaterfallFunds builder
# ---------------------------------------------------------------------------


def test_build_funds_from_report() -> None:
    p = load_green_lion_2024_1_periods()[0]
    funds = build_funds_for_period(p)
    assert funds.available_revenue_funds == pytest.approx(13_615_514.93)
    assert funds.available_principal_funds == pytest.approx(43_486_011.27)
    assert funds.class_a_balance == pytest.approx(1_000_000_000.00)
    assert funds.reserve_balance == pytest.approx(10_500_000.00)
    assert funds.reserve_target == pytest.approx(10_500_000.00)
    # PDLs are zero this period.
    assert funds.class_a_pdl_balance == pytest.approx(0.0)
    # Coupon recovered from the Bond Report (Current Coupon 245.4 bps → 2.454%).
    assert funds.class_a_rate_pct == pytest.approx(2.454, abs=1e-3)


# ---------------------------------------------------------------------------
# (1)…(14) → (b) fold
# ---------------------------------------------------------------------------


def test_revenue_sub_items_fold_into_b() -> None:
    p = load_green_lion_2024_1_periods()[0]
    folded = _fold_report_revenue_steps(p)
    # The fourteen (1)…(14) sub-items collapse into a single (b) = €60,500
    # (only sub-item (7) was non-zero).
    assert folded["(b)"] == pytest.approx(60_500.00)
    # No bare numeric sub-item labels leak through.
    assert not any(lbl.strip("()").isdigit() for lbl in folded)
    # Top-level steps survive the fold.
    assert folded["(c)"] == pytest.approx(6_043_550.85)
    assert folded["(d)"] == pytest.approx(6_135_000.00)
    assert folded["(k)"] == pytest.approx(1_336_466.99)


# ---------------------------------------------------------------------------
# Revenue reconciliation — to the cent
# ---------------------------------------------------------------------------


def test_revenue_reconciles_to_the_cent(period: PeriodValidation) -> None:
    rev = period.revenue
    assert rev.waterfall_type == "revenue"
    assert len(rev.steps) == 11
    assert rev.steps_passed == 11
    assert rev.passed
    # Every step within one cent.
    for s in rev.steps:
        assert abs(s.delta) <= DEFAULT_TOLERANCE_EUR, (s.priority, s.delta)


def test_class_a_interest_is_engine_computed(period: PeriodValidation) -> None:
    # The headline: Class A interest is COMPUTED by the engine from balance ×
    # rate × days/360, with NO report override, and lands on the published amount.
    d = next(s for s in period.revenue.steps if s.priority == "(d)")
    assert d.recipient == "class_a_interest"
    assert d.source == "engine"  # not report-supplied
    assert d.engine_amount == pytest.approx(6_135_000.00, abs=DEFAULT_TOLERANCE_EUR)
    assert d.report_amount == pytest.approx(6_135_000.00)
    assert d.passed


def test_revenue_residual_lands_on_k_and_ties_out(period: PeriodValidation) -> None:
    rev = period.revenue
    k = next(s for s in rev.steps if s.priority == "(k)")
    assert k.source == "residual"
    assert k.engine_amount == pytest.approx(1_336_466.99, abs=DEFAULT_TOLERANCE_EUR)
    # The engine's total ties to Total Available Revenue Funds (no rounding gap).
    assert rev.unapplied_rounding == pytest.approx(0.0, abs=DEFAULT_TOLERANCE_EUR)
    assert rev.engine_total == pytest.approx(13_615_514.93, abs=DEFAULT_TOLERANCE_EUR)
    assert rev.available_funds == pytest.approx(13_615_514.93)


def test_engine_computed_lines_have_no_override(period: PeriodValidation) -> None:
    # All the engine-sourced steps reconcile — the independent part of the proof.
    engine_steps = [s for s in period.revenue.steps if s.source == "engine"]
    assert {s.recipient for s in engine_steps} >= {
        "class_a_interest",
        "class_a_pdl_replenishment",
        "reserve_account_replenishment",
        "class_b_pdl_replenishment",
    }
    assert all(s.passed for s in engine_steps)


# ---------------------------------------------------------------------------
# Redemption reconciliation — honest treatment of the rounding remainder
# ---------------------------------------------------------------------------


def test_redemption_reconciles_with_rounding_remainder(period: PeriodValidation) -> None:
    red = period.redemption
    assert red.waterfall_type == "redemption"
    assert red.steps_passed == len(red.steps)
    assert red.passed
    # The revolving-period purchase (a) routes the published amount to the cent.
    a = next(s for s in red.steps if s.priority == "(a)")
    assert a.engine_amount == pytest.approx(43_486_010.58, abs=DEFAULT_TOLERANCE_EUR)
    # The report's own "Unapplied Redemption Funds due to rounding" = €0.69 is
    # accounted for as the undistributed remainder — engine + remainder == pot.
    assert red.unapplied_rounding == pytest.approx(0.69, abs=DEFAULT_TOLERANCE_EUR)
    assert red.engine_total + red.unapplied_rounding == pytest.approx(
        red.available_funds, abs=DEFAULT_TOLERANCE_EUR
    )


# ---------------------------------------------------------------------------
# Top-level report — PASS verdict, counts, summary, honesty disclosure
# ---------------------------------------------------------------------------


def test_overall_report_passes(report: EngineValidationReport) -> None:
    assert report.deal_name == "Green Lion 2024-1 B.V."
    assert report.periods_checked == 1
    assert report.periods_passed == 1
    assert report.passed
    assert report.tolerance_eur == DEFAULT_TOLERANCE_EUR


def test_summary_is_honest_about_sources(report: EngineValidationReport) -> None:
    text = report.summary()
    assert "PASS" in text
    assert "Green Lion 2024-1 B.V." in text
    # The disclosure naming engine-computed vs report-supplied is present.
    assert "engine" in text.lower() and "report-supplied" in text.lower()
    assert "engine-computed line(s) matched" in text


def test_each_step_labels_its_source(period: PeriodValidation) -> None:
    sources = {s.source for s in period.revenue.steps} | {
        s.source for s in period.redemption.steps
    }
    assert sources <= {"engine", "report-supplied", "residual"}
    # No fabricated 100%: both engine-computed AND report-supplied lines exist.
    assert "engine" in sources
    assert "report-supplied" in sources


def test_reconcile_engine_iterates_all_periods() -> None:
    # The core takes the parser's full period list, so it generalises as more
    # report fixtures land.
    model = load_green_lion_2024_1_model()
    periods = load_green_lion_2024_1_periods()
    rep = reconcile_engine(periods, model)
    assert rep.periods_checked == len(periods)
    assert rep.passed
