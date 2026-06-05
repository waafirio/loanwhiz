"""Tests for the S7 reconciliation harness (#187).

All tests are offline (fast suite): they drive the pure
``reconcile_collateral`` core over hand-built ``DealStateSeries`` /
``CollateralLedger`` objects. The Green-Lion convenience builder
(``reconcile_green_lion``) is integration-gated (it obtains the ledger via the
extraction cache / Gemini) and not exercised here.

The fixtures mirror the real 3-period Green Lion collateral extract (the S0
spike cache), so a clean reconstruction reconciles to the cent — the proof of
correctness this harness exists to provide.
"""

from __future__ import annotations

from typing import Any

import pytest

from loanwhiz.extraction.collateral_ledger import CollateralLedger, _ledger_from_extracts
from loanwhiz.primitives.deal_state import DealState, PeriodCollections
from loanwhiz.primitives.period_state_machine import DealStateSeries
from loanwhiz.primitives.reconciliation_harness import (
    DEFAULT_TOLERANCE_EUR,
    LIABILITY_NOTE,
    ReconciliationReport,
    _build_line_check,
    reconcile_collateral,
)

# ---------------------------------------------------------------------------
# Fixtures — the real 3-period Green Lion collateral extract (from S0's cache)
# ---------------------------------------------------------------------------

_FEB: dict[str, Any] = {
    "reporting_period_start": "2026-02-01",
    "reporting_period_end": "2026-02-28",
    "reporting_date": "2026-03-23",
    "loans_begin": 3283,
    "loans_end": 3275,
    "balance_begin": 1053099999.98,
    "balance_end": 1048763811.94,
    "repayments": 1846449.61,
    "prepayments": 2659344.91,
    "further_advances": 0.0,
    "other_balance_change": 169606.48,
    "has_tranche_section": False,
}

_MAR: dict[str, Any] = {
    "reporting_period_start": "2026-03-01",
    "reporting_period_end": "2026-03-31",
    "reporting_date": "2026-04-23",
    "loans_begin": 3275,
    "loans_end": 3261,
    "balance_begin": 1048763811.94,
    "balance_end": 1042493289.74,
    "repayments": 1839613.2,
    "prepayments": 4439265.19,
    "further_advances": 0.0,
    "other_balance_change": 8356.19,
    "has_tranche_section": False,
}

_APR: dict[str, Any] = {
    "reporting_period_start": "2026-04-01",
    "reporting_period_end": "2026-04-30",
    "reporting_date": "2026-05-26",
    "loans_begin": 3261,
    "loans_end": 3237,
    "balance_begin": 1042493289.74,
    "balance_end": 1033412063.04,
    "repayments": 1833436.31,
    "prepayments": 7202449.32,
    "further_advances": 0.0,
    "other_balance_change": -45341.07,
    "has_tranche_section": False,
}

_ORIGINAL_POOL = 1_053_099_999.98


def _ledger() -> CollateralLedger:
    return _ledger_from_extracts(
        "Green Lion 2026-1 B.V.",
        {"February 2026": _FEB, "March 2026": _MAR, "April 2026": _APR},
    )


def _state(
    reporting_date: str,
    pool_balance: float,
    principal_collected: float,
    *,
    period_index: int,
    collections: bool = True,
) -> DealState:
    """Build a DealState whose collateral figures match a report period.

    Liability fields are placeholders (not reconciled here); only
    ``pool_balance``, ``reporting_date`` and ``collections.total_principal`` are
    load-bearing for the collateral reconciliation.
    """
    coll = (
        PeriodCollections(scheduled_principal=principal_collected)
        if collections
        else None
    )
    return DealState(
        reporting_date=reporting_date,
        period_index=period_index,
        class_a_balance=1_000_000_000.0,
        class_b_balance=53_100_000.0,
        class_c_balance=10_500_000.0,
        reserve_balance=5_000_000.0,
        reserve_target=5_000_000.0,
        pool_balance=pool_balance,
        original_pool_balance=_ORIGINAL_POOL,
        collections=coll,
    )


def _full_reduction(extract: dict[str, Any]) -> float:
    """The report's full pool reduction = balance_begin - balance_end.

    Per spike S0 this is what the reconstructed ``DealState.pool_balance`` delta
    (``collections.total_principal``) ties to — NOT ``repayments + prepayments``,
    which differs by the report's ``other_balance_change`` line.
    """
    return extract["balance_begin"] - extract["balance_end"]


def _matching_series() -> DealStateSeries:
    """A reconstructed series that ties to the report ledger to the cent.

    Includes a period-0 seed state (no matching report) to confirm a
    reconstructed-only period does not fail the proof. Principal collected is the
    FULL pool reduction (the tape balance delta that advances the state), which
    S0 proved ties to the report roll-forward exactly.
    """
    states = [
        # period-0 prospectus seed — opening balance, no report period for it.
        _state("2026-01-31", _ORIGINAL_POOL, 0.0, period_index=0, collections=False),
        _state("2026-02-28", _FEB["balance_end"], _full_reduction(_FEB), period_index=1),
        _state("2026-03-31", _MAR["balance_end"], _full_reduction(_MAR), period_index=2),
        _state("2026-04-30", _APR["balance_end"], _full_reduction(_APR), period_index=3),
    ]
    return DealStateSeries(states=states, period_results=[])


# ---------------------------------------------------------------------------
# _build_line_check
# ---------------------------------------------------------------------------


class TestBuildLineCheck:
    def test_exact_match_to_the_cent(self):
        c = _build_line_check("pool_balance_end", 100.00, 100.00, 0.01)
        assert c.delta == pytest.approx(0.0)
        assert c.abs_delta == pytest.approx(0.0)
        assert c.delta_pct == pytest.approx(0.0)
        assert c.match is True

    def test_within_one_cent_matches(self):
        c = _build_line_check("x", 100.005, 100.0, 0.01)
        assert c.match is True

    def test_outside_one_cent_mismatch(self):
        c = _build_line_check("x", 100.02, 100.0, 0.01)
        assert c.match is False
        assert c.abs_delta == pytest.approx(0.02)

    def test_delta_pct_none_when_reported_zero_reconstructed_nonzero(self):
        c = _build_line_check("x", 50.0, 0.0, 0.01)
        assert c.delta_pct is None
        assert c.match is False  # EUR gate still catches it

    def test_delta_pct_zero_when_both_zero(self):
        c = _build_line_check("x", 0.0, 0.0, 0.01)
        assert c.delta_pct == pytest.approx(0.0)
        assert c.match is True

    def test_delta_sign(self):
        c = _build_line_check("x", 90.0, 100.0, 0.01)
        assert c.delta == pytest.approx(-10.0)


# ---------------------------------------------------------------------------
# reconcile_collateral — the PASS path (to the cent)
# ---------------------------------------------------------------------------


class TestReconcilePass:
    def test_overall_pass_to_the_cent(self):
        report = reconcile_collateral(_matching_series(), _ledger())
        assert isinstance(report, ReconciliationReport)
        assert report.overall_pass is True
        assert report.periods_checked == 3
        assert report.periods_passed == 3
        assert report.periods_failed == 0
        assert "PASS" in report.summary

    def test_every_line_matches(self):
        report = reconcile_collateral(_matching_series(), _ledger())
        for period in report.periods:
            assert period.period_pass is True
            for check in period.line_checks:
                assert check.match is True, (period.reporting_date, check.line_item)

    def test_pool_and_principal_lines_present(self):
        report = reconcile_collateral(_matching_series(), _ledger())
        for period in report.periods:
            items = {c.line_item for c in period.line_checks}
            assert items == {"pool_balance_end", "principal_collected"}

    def test_join_is_by_reporting_date(self):
        report = reconcile_collateral(_matching_series(), _ledger())
        dates = [p.reporting_date for p in report.periods]
        assert dates == ["2026-02-28", "2026-03-31", "2026-04-30"]

    def test_roll_forward_residual_is_consistent(self):
        report = reconcile_collateral(_matching_series(), _ledger())
        for period in report.periods:
            assert period.roll_forward_consistent is True
            assert abs(period.roll_forward_residual) <= DEFAULT_TOLERANCE_EUR

    def test_principal_collected_uses_full_pool_reduction_not_repay_plus_prepay(self):
        # Regression for the other_balance_change subtlety (spike S0): the
        # reconstructed total_principal is the FULL tape balance delta, which
        # ties to balance_begin - balance_end, NOT repayments + prepayments. The
        # two differ by exactly the report's other_balance_change every period, so
        # a reconciliation that compared against repayments+prepayments would
        # falsely FAIL. This series (full-reduction) PASSES.
        report = reconcile_collateral(_matching_series(), _ledger())
        for ext, date in (
            (_FEB, "2026-02-28"),
            (_MAR, "2026-03-31"),
            (_APR, "2026-04-30"),
        ):
            # confirm the test data actually exercises a non-zero other line
            assert abs(ext["other_balance_change"]) > 0.0
            period = next(p for p in report.periods if p.reporting_date == date)
            pc = next(
                c for c in period.line_checks if c.line_item == "principal_collected"
            )
            assert pc.reported_value == pytest.approx(
                ext["balance_begin"] - ext["balance_end"]
            )
            assert pc.match is True
        assert report.overall_pass is True

    def test_period0_seed_is_reconstructed_only_not_a_failure(self):
        # The 2026-01-31 seed state has no report period; it must show up as
        # reconstructed-only and NOT fail the proof.
        report = reconcile_collateral(_matching_series(), _ledger())
        assert "2026-01-31" in report.unmatched_reconstructed_dates
        assert report.overall_pass is True


# ---------------------------------------------------------------------------
# reconcile_collateral — the FAIL path
# ---------------------------------------------------------------------------


class TestReconcileFail:
    def test_perturbed_pool_balance_fails_with_right_delta(self):
        series = _matching_series()
        # Perturb March's reconstructed pool balance by exactly 1 EUR.
        states = list(series.states)
        bad = states[2].model_copy(
            update={"pool_balance": states[2].pool_balance + 1.0}
        )
        states[2] = bad
        perturbed = DealStateSeries(states=states, period_results=[])

        report = reconcile_collateral(perturbed, _ledger())
        assert report.overall_pass is False
        assert report.periods_failed == 1
        assert "FAIL" in report.summary

        mar = next(p for p in report.periods if p.reporting_date == "2026-03-31")
        assert mar.period_pass is False
        pool_check = next(c for c in mar.line_checks if c.line_item == "pool_balance_end")
        assert pool_check.match is False
        assert pool_check.delta == pytest.approx(1.0)
        assert pool_check.abs_delta == pytest.approx(1.0)

    def test_perturbed_principal_collected_fails(self):
        series = _matching_series()
        states = list(series.states)
        bad_coll = PeriodCollections(scheduled_principal=999.0)
        states[1] = states[1].model_copy(update={"collections": bad_coll})
        perturbed = DealStateSeries(states=states, period_results=[])

        report = reconcile_collateral(perturbed, _ledger())
        assert report.overall_pass is False
        feb = next(p for p in report.periods if p.reporting_date == "2026-02-28")
        pc = next(c for c in feb.line_checks if c.line_item == "principal_collected")
        assert pc.match is False

    def test_tolerance_widening_can_pass_a_small_discrepancy(self):
        series = _matching_series()
        states = list(series.states)
        states[2] = states[2].model_copy(
            update={"pool_balance": states[2].pool_balance + 0.5}
        )
        perturbed = DealStateSeries(states=states, period_results=[])
        # Default 1-cent gate fails; a 1-EUR gate passes.
        assert reconcile_collateral(perturbed, _ledger()).overall_pass is False
        assert reconcile_collateral(
            perturbed, _ledger(), tolerance_eur=1.0
        ).overall_pass is True


# ---------------------------------------------------------------------------
# Unmatched periods
# ---------------------------------------------------------------------------


class TestUnmatchedPeriods:
    def test_reported_period_with_no_reconstruction_fails_the_proof(self):
        # Drop the April reconstructed state — April is reported but not
        # reconstructed → a proof gap → overall FAIL, surfaced not dropped.
        series = _matching_series()
        states = [s for s in series.states if s.reporting_date != "2026-04-30"]
        partial = DealStateSeries(states=states, period_results=[])

        report = reconcile_collateral(partial, _ledger())
        assert "2026-04-30" in report.unmatched_report_dates
        assert report.overall_pass is False
        assert report.periods_checked == 2

    def test_no_overlap_is_not_a_vacuous_pass(self):
        # A series whose dates never intersect the ledger must not "pass" on
        # zero checked periods.
        states = [_state("2099-01-01", 1.0, 0.0, period_index=0)]
        series = DealStateSeries(states=states, period_results=[])
        report = reconcile_collateral(series, _ledger())
        assert report.periods_checked == 0
        assert report.overall_pass is False
        assert len(report.unmatched_report_dates) == 3


# ---------------------------------------------------------------------------
# Liability-side disclosure + reserve_draw caveat
# ---------------------------------------------------------------------------


class TestLiabilityNote:
    def test_report_carries_liability_note(self):
        report = reconcile_collateral(_matching_series(), _ledger())
        assert report.liability_note == LIABILITY_NOTE

    def test_liability_note_mentions_invariants_and_reserve_draw(self):
        report = reconcile_collateral(_matching_series(), _ledger())
        note = report.liability_note.lower()
        assert "invariant" in note
        assert "reserve_draw=0" in report.liability_note
        assert "s8" in note

    def test_report_is_json_serialisable(self):
        report = reconcile_collateral(_matching_series(), _ledger())
        dumped = report.model_dump_json()
        reloaded = ReconciliationReport.model_validate_json(dumped)
        assert reloaded.overall_pass == report.overall_pass
        assert reloaded.summary == report.summary
