"""Tests for loan-level amortisation from the tape (#281).

Covers the per-loan level-payment schedule, the stated-instalment preference,
the degenerate cases (zero rate, blank term, missing columns, fully amortised),
and the non-performing exclusion — the pure analytics that replace the
``ScenarioGenerator`` flat pool-level proxy.
"""

from __future__ import annotations

import pandas as pd
import pytest

from loanwhiz.primitives.loan_level_amortisation import (
    _amortise_one_loan,
    _level_payment,
    _monthly_rate,
    pool_scheduled_principal_schedule,
)

# ---------------------------------------------------------------------------
# Hand-computed annuity reference
# ---------------------------------------------------------------------------


def _annuity_schedule(balance: float, annual_rate_pct: float, term: int, months: int) -> list[float]:
    """Independent reference annuity schedule (Act/360, 30-day month)."""
    i = (annual_rate_pct / 100.0) / 360.0 * 30.0
    instalment = balance * i / (1.0 - (1.0 + i) ** (-term))
    out, bal = [], balance
    for _ in range(months):
        if bal <= 0:
            out.append(0.0)
            continue
        interest = bal * i
        principal = min(instalment - interest, bal)
        out.append(principal)
        bal -= principal
    return out


def test_single_loan_matches_hand_computed_annuity_to_the_cent():
    """A single level-payment loan reproduces the annuity schedule exactly."""
    df = pd.DataFrame(
        [{"current_balance": 100_000.0, "current_interest_rate_pct": 6.0, "remaining_term_months": 12}]
    )
    got = pool_scheduled_principal_schedule(df, 12)
    expected = _annuity_schedule(100_000.0, 6.0, 12, 12)
    assert len(got) == 12
    for g, e in zip(got, expected):
        assert g == pytest.approx(e, abs=0.01)


def test_schedule_repays_full_balance_over_the_term():
    df = pd.DataFrame(
        [{"current_balance": 250_000.0, "current_interest_rate_pct": 4.5, "remaining_term_months": 24}]
    )
    sched = pool_scheduled_principal_schedule(df, 24)
    assert sum(sched) == pytest.approx(250_000.0, abs=0.01)


def test_amortisation_curve_is_principal_back_loaded():
    """Scheduled principal rises over the life of a positive-rate loan."""
    df = pd.DataFrame(
        [{"current_balance": 100_000.0, "current_interest_rate_pct": 6.0, "remaining_term_months": 12}]
    )
    sched = pool_scheduled_principal_schedule(df, 12)
    # Interest-heavy early, principal-heavy late: strictly increasing principal.
    assert all(earlier < later for earlier, later in zip(sched, sched[1:]))


def test_stated_monthly_payment_preferred_over_computed_annuity():
    """The tape's contractual instalment beats the computed level payment."""
    df = pd.DataFrame(
        [
            {
                "current_balance": 100_000.0,
                "current_interest_rate_pct": 6.0,
                "remaining_term_months": 12,
                "scheduled_monthly_payment": 9_000.0,
            }
        ]
    )
    sched = pool_scheduled_principal_schedule(df, 1)
    # interest m1 = 100000 * 6/100/360*30 = 500; principal = 9000 - 500 = 8500.
    assert sched[0] == pytest.approx(8_500.0, abs=0.01)


def test_zero_rate_loan_straight_lines():
    """A 0% loan amortises straight-line P/n, no interest carve-out."""
    df = pd.DataFrame(
        [{"current_balance": 1_200.0, "current_interest_rate_pct": 0.0, "remaining_term_months": 12}]
    )
    sched = pool_scheduled_principal_schedule(df, 12)
    assert all(p == pytest.approx(100.0, abs=1e-9) for p in sched)


def test_blank_or_zero_term_is_a_balloon_not_a_crash():
    """A loan with no remaining term repays its full balance in period 0."""
    df = pd.DataFrame(
        [{"current_balance": 50_000.0, "current_interest_rate_pct": 3.0, "remaining_term_months": 0}]
    )
    sched = pool_scheduled_principal_schedule(df, 6)
    assert sched[0] == pytest.approx(50_000.0, abs=0.01)
    assert sum(sched[1:]) == pytest.approx(0.0, abs=0.01)


def test_missing_optional_columns_degrade_gracefully():
    """Only current_balance is required; missing rate/term => straight-line balloon-ish."""
    df = pd.DataFrame([{"current_balance": 10_000.0}])
    sched = pool_scheduled_principal_schedule(df, 6)
    # No term column -> term 0 -> full balance repaid period 0.
    assert sched[0] == pytest.approx(10_000.0, abs=0.01)


def test_missing_balance_column_yields_zeros():
    df = pd.DataFrame([{"current_interest_rate_pct": 5.0, "remaining_term_months": 12}])
    assert pool_scheduled_principal_schedule(df, 4) == [0.0, 0.0, 0.0, 0.0]


def test_non_performing_loans_excluded():
    """Defaulted / 180+ arrears loans pay no scheduled principal."""
    df = pd.DataFrame(
        [
            {"current_balance": 100_000.0, "current_interest_rate_pct": 6.0, "remaining_term_months": 12, "default_crr_flag": "Y"},
            {"current_balance": 100_000.0, "current_interest_rate_pct": 6.0, "remaining_term_months": 12, "default_crr_flag": "N", "arrears_bucket": "180+d"},
        ]
    )
    assert sum(pool_scheduled_principal_schedule(df, 12)) == pytest.approx(0.0, abs=0.01)


def test_pool_schedule_sums_across_loans():
    """The pool schedule is the per-period sum of each loan's scheduled principal."""
    df = pd.DataFrame(
        [
            {"current_balance": 100_000.0, "current_interest_rate_pct": 0.0, "remaining_term_months": 10},
            {"current_balance": 50_000.0, "current_interest_rate_pct": 0.0, "remaining_term_months": 10},
        ]
    )
    sched = pool_scheduled_principal_schedule(df, 10)
    # Both straight-line: 10000 + 5000 = 15000 per period.
    assert all(p == pytest.approx(15_000.0, abs=1e-6) for p in sched)


def test_fully_amortised_tail_is_zero_padded():
    """A loan that repays before the horizon pads zeros for the remaining periods."""
    df = pd.DataFrame(
        [{"current_balance": 1_000.0, "current_interest_rate_pct": 0.0, "remaining_term_months": 2}]
    )
    sched = pool_scheduled_principal_schedule(df, 6)
    assert sched[:2] == pytest.approx([500.0, 500.0], abs=1e-9)
    assert sched[2:] == [0.0, 0.0, 0.0, 0.0]


def test_months_zero_returns_empty():
    df = pd.DataFrame([{"current_balance": 1.0}])
    assert pool_scheduled_principal_schedule(df, 0) == []


def test_negative_months_raises():
    with pytest.raises(ValueError):
        pool_scheduled_principal_schedule(pd.DataFrame([{"current_balance": 1.0}]), -1)


# ---------------------------------------------------------------------------
# Helper-level unit coverage
# ---------------------------------------------------------------------------


def test_monthly_rate_convention():
    # 6% annual, Act/360, 30-day month -> 0.5% monthly.
    assert _monthly_rate(6.0) == pytest.approx(0.005, abs=1e-9)
    assert _monthly_rate(0.0) == 0.0
    assert _monthly_rate(-1.0) == 0.0


def test_level_payment_zero_rate_is_straight_line():
    assert _level_payment(1_200.0, 0.0, 12) == pytest.approx(100.0)


def test_level_payment_zero_term_returns_full_balance():
    assert _level_payment(5_000.0, 0.005, 0) == pytest.approx(5_000.0)


def test_amortise_one_loan_negative_amortisation_pays_no_principal():
    """An instalment that doesn't cover interest repays zero scheduled principal."""
    # 100000 @ 6% monthly interest = 500; instalment 400 < interest -> 0 principal.
    out = _amortise_one_loan(100_000.0, 6.0, 12, stated_instalment=400.0, months=3)
    assert out == [0.0, 0.0, 0.0]
