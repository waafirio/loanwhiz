"""Tests for the Reconciler reader (#270, epic #257) — the headline proof.

These tests drive the full reconciliation of the LIVE folded engine against
Green Lion 2024-1's OWN published Notes & Cash reports, across all 3 quarterly
periods, using the committed seed ``DealModel`` and the committed text fixtures
(no network, no LLM — fast suite). The Reconciler reads the engine-computed steps
straight off the folded ``DealStateSeries`` (not a second interpretation), so the
proof is "the live cold-start engine lands the published numbers, to the cent".

The headline assertions:

- all 3 quarterly periods' revenue + redemption PoP reconcile **to EUR 0.01**,
  including Class A interest computed by the engine from balances/rates with NO
  report input (and the per-period floating-rate coupon recovered correctly);
- redemption (a) — the revolving-period purchase of new receivables (~€43.49M) —
  reconciles to the cent, proving the #269 cross-waterfall flat-label collision
  is resolved (it previously came out €0.00 through the live path);
- the revenue residual sweep lands on (k) and the engine total ties out;
- each line is honestly labelled engine / report-supplied / residual;
- no Green-Lion-2026-1 fallback constant is consulted for the report deal.
"""

from __future__ import annotations

import pytest

from loanwhiz.primitives.reconciler import (
    DEFAULT_TOLERANCE_EUR,
    PeriodValidation,
    ReconciliationReport,
    fold_green_lion_2024_1,
    load_green_lion_2024_1_report,
    reconcile_series,
    validate_green_lion_2024_1,
)


@pytest.fixture()
def report() -> ReconciliationReport:
    return validate_green_lion_2024_1()


@pytest.fixture()
def march(report: ReconciliationReport) -> PeriodValidation:
    """The March 2026 period (the original single-period fixture)."""
    return next(p for p in report.periods if p.period_label == "March 2026")


# ---------------------------------------------------------------------------
# Offline loading / folding — committed seed + the 3 committed fixtures
# ---------------------------------------------------------------------------


def test_loads_three_committed_periods_offline() -> None:
    rep = load_green_lion_2024_1_report()
    assert rep.deal_name == "Green Lion 2024-1 B.V."
    assert len(rep.periods) == 3
    assert [p.reporting_date for p in rep.periods] == [
        "2025-10-23",
        "2026-01-23",
        "2026-04-23",
    ]


def test_fold_produces_one_result_per_period() -> None:
    series, rep = fold_green_lion_2024_1()
    # states = seed + one closing per period; results = one per period.
    assert len(series.period_results) == len(rep.periods) == 3
    assert len(series.states) == 4


# ---------------------------------------------------------------------------
# The headline — all 3 periods reconcile to the cent through the live fold
# ---------------------------------------------------------------------------


def test_all_three_periods_reconcile_to_the_cent(report: ReconciliationReport) -> None:
    assert report.deal_name == "Green Lion 2024-1 B.V."
    assert report.periods_checked == 3
    assert report.periods_passed == 3
    assert report.passed
    assert report.tolerance_eur == DEFAULT_TOLERANCE_EUR
    for p in report.periods:
        for s in p.revenue.steps + p.redemption.steps:
            assert abs(s.delta) <= DEFAULT_TOLERANCE_EUR, (
                p.period_label,
                s.priority,
                s.delta,
            )


def test_class_a_interest_engine_computed_per_period(
    report: ReconciliationReport,
) -> None:
    """Class A interest is engine-computed AND lands the published amount in every
    quarter — the floating-rate coupon must be recovered per period (#270)."""
    expected = {
        "September 2025": 6_110_333.33,
        "December 2025": 6_281_555.56,
        "March 2026": 6_135_000.00,
    }
    for p in report.periods:
        d = next(s for s in p.revenue.steps if s.priority == "(d)")
        assert d.recipient == "class_a_interest"
        assert d.source == "engine"  # not report-supplied — the independent proof
        assert d.engine_amount == pytest.approx(
            expected[p.period_label], abs=DEFAULT_TOLERANCE_EUR
        )
        assert d.passed


def test_redemption_a_reconciles_resolving_collision(
    report: ReconciliationReport,
) -> None:
    """The #269 cross-waterfall flat-label collision is resolved: redemption (a),
    the revolving-period purchase of new receivables, reconciles to the cent in
    every period instead of coming out €0.00."""
    expected = {
        "September 2025": 38_941_269.39,
        "December 2025": 41_719_928.03,
        "March 2026": 43_486_010.58,
    }
    for p in report.periods:
        a = next(s for s in p.redemption.steps if s.priority == "(a)")
        assert a.recipient == "initial_purchase_price_of_new_mortgage_receivables"
        assert a.engine_amount == pytest.approx(
            expected[p.period_label], abs=DEFAULT_TOLERANCE_EUR
        )
        assert a.engine_amount > 1_000_000.0  # not the old €0.00 collision value
        assert a.passed


def test_revenue_residual_lands_on_k_and_ties_out(march: PeriodValidation) -> None:
    rev = march.revenue
    k = next(s for s in rev.steps if s.priority == "(k)")
    assert k.source == "residual"
    assert k.engine_amount == pytest.approx(1_336_466.99, abs=DEFAULT_TOLERANCE_EUR)
    # The engine total ties to Total Available Revenue Funds (no rounding gap).
    assert rev.unapplied_rounding == pytest.approx(0.0, abs=DEFAULT_TOLERANCE_EUR)
    assert rev.engine_total == pytest.approx(13_615_514.93, abs=DEFAULT_TOLERANCE_EUR)
    assert rev.available_funds == pytest.approx(13_615_514.93)


def test_redemption_rounding_remainder_is_honest(march: PeriodValidation) -> None:
    red = march.redemption
    assert red.passed
    # The report's own "Unapplied Redemption Funds due to rounding" = €0.69 is the
    # undistributed remainder — engine + remainder == pot (neither side fudged).
    assert red.unapplied_rounding == pytest.approx(0.69, abs=DEFAULT_TOLERANCE_EUR)
    assert red.engine_total + red.unapplied_rounding == pytest.approx(
        red.available_funds, abs=DEFAULT_TOLERANCE_EUR
    )


# ---------------------------------------------------------------------------
# Source labelling — no fabricated 100%
# ---------------------------------------------------------------------------


def test_each_step_labels_its_source(march: PeriodValidation) -> None:
    sources = {s.source for s in march.revenue.steps} | {
        s.source for s in march.redemption.steps
    }
    assert sources <= {"engine", "report-supplied", "residual"}
    # Both engine-computed AND report-supplied lines exist (not a false 100%).
    assert "engine" in sources
    assert "report-supplied" in sources
    assert "residual" in sources


def test_engine_computed_lines_have_no_report_input(march: PeriodValidation) -> None:
    engine_steps = [s for s in march.revenue.steps if s.source == "engine"]
    assert {s.recipient for s in engine_steps} >= {
        "class_a_interest",
        "class_a_pdl_replenishment",
        "reserve_account_replenishment",
        "class_b_pdl_replenishment",
    }
    assert all(s.passed for s in engine_steps)


def test_summary_is_honest_about_sources(report: ReconciliationReport) -> None:
    text = report.summary()
    assert "PASS" in text
    assert "Green Lion 2024-1 B.V." in text
    assert "3/3 periods" in text
    assert "engine" in text.lower() and "report-supplied" in text.lower()
    assert "engine-computed line(s) matched" in text


# ---------------------------------------------------------------------------
# Reconciler is a reader over an arbitrary folded series
# ---------------------------------------------------------------------------


def test_reconcile_series_join_mismatch_raises() -> None:
    """The reader joins positionally; a series with the wrong number of period
    results vs the report is a malformed join and must fail loudly, not silently
    reconcile a subset."""
    series, rep = fold_green_lion_2024_1()
    # Drop the last period result so the counts disagree.
    series.period_results = series.period_results[:-1]
    with pytest.raises(ValueError, match="join mismatch"):
        reconcile_series(series, rep)
