"""Tests for the V5 pool-level full-pipeline harness (#211).

All tests are offline (fast suite): they drive the pure ``run_pool_pipeline``
core (and its helpers) over hand-built ``CollateralLedger`` / ``NotesCashReport``
fixtures. The pipeline runs the REAL ``reconstruct_period_series`` engine
end-to-end — the engine path is exercised, not mocked. The Green-Lion convenience
builder (``run_pool_pipeline_green_lion``) is integration-gated (it obtains the
ledger/report via the extraction caches / network) and not exercised here.

The harness is a deliberately COARSE, pool-level characterisation (no loan tape,
monthly↔quarterly join, partly-indicative engine), so these tests assert the
*labelling*, *delta computation*, and *join behaviour* — not a cent-level PASS,
which the data cannot support by construction.
"""

from __future__ import annotations

import pytest

from loanwhiz.extraction.collateral_ledger import CollateralLedger, CollateralPeriod
from loanwhiz.primitives.notes_cash_parser import (
    IssuerAccount,
    NoteClassBalance,
    NotesCashPeriod,
    NotesCashReport,
    PoPStep,
)
from loanwhiz.primitives.pool_pipeline_harness import (
    DEFAULT_COARSE_BAND_PCT,
    GRANULARITY,
    STANDING_CAVEATS,
    LiabilityLineCheck,
    PoolPipelineReport,
    build_period_inputs,
    capital_structure_from_deal_model,
    collections_from_collateral_period,
    run_pool_pipeline,
)

# ---------------------------------------------------------------------------
# Deal figures — a small synthetic seasoned-style deal.
# ---------------------------------------------------------------------------

_CAP_STRUCTURE = {
    "class_a_balance": 850_000_000.0,
    "class_a_rate_pct": 3.62,
    "class_b_balance": 44_800_000.0,
    "class_c_balance": 9_000_000.0,
}
_RESERVE_TARGET = 9_000_000.0
_ORIGINAL_POOL = 903_800_000.0
_SEED_DATE = "2025-08-31"


def _collateral_period(
    *,
    reporting_date: str,
    label: str,
    begin: float,
    end: float,
    repayments: float,
    prepayments: float,
    coupon: float = 3.5,
    default_amount: float = 0.0,
) -> CollateralPeriod:
    return CollateralPeriod(
        reporting_date=reporting_date,
        period_label=label,
        period_start=None,
        period_end=reporting_date,
        pool_balance_begin=begin,
        pool_balance_end=end,
        repayments=repayments,
        prepayments=prepayments,
        wtd_avg_coupon_pct=coupon,
        default_amount=default_amount,
    )


def _ledger() -> CollateralLedger:
    """A 3-month collateral ledger (Sep/Dec straddle one quarter-end each)."""
    return CollateralLedger(
        deal_name="Synthetic Seasoned B.V.",
        periods=[
            _collateral_period(
                reporting_date="2025-09-30",
                label="September 2025",
                begin=903_800_000.0,
                end=893_800_000.0,
                repayments=8_000_000.0,
                prepayments=2_000_000.0,
            ),
            _collateral_period(
                reporting_date="2025-10-31",
                label="October 2025",
                begin=893_800_000.0,
                end=885_800_000.0,
                repayments=7_000_000.0,
                prepayments=1_000_000.0,
            ),
            _collateral_period(
                reporting_date="2025-12-31",
                label="December 2025",
                begin=885_800_000.0,
                end=873_800_000.0,
                repayments=9_000_000.0,
                prepayments=3_000_000.0,
            ),
        ],
    )


def _notes_cash() -> NotesCashReport:
    """Quarterly Notes & Cash liability actuals on Sep/Dec quarter-ends.

    The reported note balances are set close to (but not exactly equal to) what
    a clean pool-driven redemption of Class A produces, so the comparison
    exercises both within-band and out-of-band lines deterministically.
    """
    return NotesCashReport(
        deal_name="Synthetic Seasoned B.V.",
        periods=[
            NotesCashPeriod(
                reporting_date="2025-09-30",
                period_label="September 2025",
                note_balances=[
                    NoteClassBalance(
                        note_class="class_a",
                        principal_balance_after_payment=840_000_000.0,
                        pdl_balance_after_payment=0.0,
                    ),
                    NoteClassBalance(
                        note_class="class_b",
                        principal_balance_after_payment=44_800_000.0,
                        pdl_balance_after_payment=0.0,
                    ),
                    NoteClassBalance(
                        note_class="class_c",
                        principal_balance_after_payment=9_000_000.0,
                        pdl_balance_after_payment=0.0,
                    ),
                ],
                revenue_pop=[PoPStep(priority="(d)", recipient="class_a_interest", amount=2_600_000.0)],
                redemption_pop=[PoPStep(priority="(b)", recipient="class_a_principal", amount=10_000_000.0)],
                issuer_accounts=[
                    IssuerAccount(name="reserve_account", balance_end=9_000_000.0, target=9_000_000.0)
                ],
            ),
            NotesCashPeriod(
                reporting_date="2025-12-31",
                period_label="December 2025",
                note_balances=[
                    NoteClassBalance(
                        note_class="class_a",
                        principal_balance_after_payment=820_000_000.0,
                        pdl_balance_after_payment=0.0,
                    ),
                    NoteClassBalance(
                        note_class="class_b",
                        principal_balance_after_payment=44_800_000.0,
                        pdl_balance_after_payment=0.0,
                    ),
                    NoteClassBalance(
                        note_class="class_c",
                        principal_balance_after_payment=9_000_000.0,
                        pdl_balance_after_payment=0.0,
                    ),
                ],
                revenue_pop=[PoPStep(priority="(d)", recipient="class_a_interest", amount=2_550_000.0)],
                redemption_pop=[PoPStep(priority="(b)", recipient="class_a_principal", amount=12_000_000.0)],
                issuer_accounts=[
                    IssuerAccount(name="reserve_account", balance_end=9_000_000.0, target=9_000_000.0)
                ],
            ),
        ],
    )


def _run() -> PoolPipelineReport:
    return run_pool_pipeline(
        _ledger(),
        _notes_cash(),
        _CAP_STRUCTURE,
        reserve_target=_RESERVE_TARGET,
        original_pool_balance=_ORIGINAL_POOL,
        seed_reporting_date=_SEED_DATE,
    )


# ===========================================================================
# 1. collections_from_collateral_period — pool-movement net-reconciliation
# ===========================================================================


def test_collections_principal_from_pool_roll_forward() -> None:
    period = _ledger().periods[0]
    col = collections_from_collateral_period(period)
    # Scheduled / prepayment map directly off the report roll-forward lines.
    assert col.scheduled_principal == 8_000_000.0
    assert col.prepayment == 2_000_000.0
    # total_principal == reported full pool reduction for this period.
    assert col.total_principal == period.pool_balance_begin - period.pool_balance_end
    assert col.recovery == 0.0


def test_collections_interest_is_coupon_approximation() -> None:
    period = _ledger().periods[0]
    col = collections_from_collateral_period(period, days_in_period=30)
    expected = period.pool_balance_begin * (3.5 / 100.0) * (30 / 360.0)
    assert col.interest == pytest.approx(expected)


def test_collections_default_amount_becomes_realized_loss() -> None:
    period = _collateral_period(
        reporting_date="2025-09-30", label="x", begin=100.0, end=90.0,
        repayments=8.0, prepayments=2.0, default_amount=5.0,
    )
    assert collections_from_collateral_period(period).realized_loss == 5.0


def test_collections_no_coupon_yields_zero_interest() -> None:
    period = CollateralPeriod(
        reporting_date="2025-09-30", period_label="x",
        pool_balance_begin=100.0, pool_balance_end=90.0,
        repayments=10.0, prepayments=0.0, wtd_avg_coupon_pct=None,
    )
    assert collections_from_collateral_period(period).interest == 0.0


# ===========================================================================
# 2. capital_structure_from_deal_model — read the prospectus seed
# ===========================================================================


def test_capital_structure_from_seed_tranche_structure() -> None:
    model = {
        "tranche_structure": [
            {"name": "Class A", "size_eur": 850_000_000.0, "rate": "3m EURIBOR + 0.45", "seniority": 0},
            {"name": "Class B", "size_eur": 44_800_000.0, "rate": None, "seniority": 1},
            {"name": "Class C", "size_eur": 9_000_000.0, "rate": None, "seniority": 2},
        ]
    }
    cap = capital_structure_from_deal_model(model)
    assert cap["class_a_balance"] == 850_000_000.0
    assert cap["class_b_balance"] == 44_800_000.0
    assert cap["class_c_balance"] == 9_000_000.0
    # A EURIBOR-spread rate is not a usable fixed coupon → no rate key.
    assert "class_a_rate_pct" not in cap


def test_capital_structure_orders_by_seniority() -> None:
    # Out-of-order list must still map senior→A.
    model = {
        "tranche_structure": [
            {"name": "Class C", "size_eur": 9_000_000.0, "seniority": 2},
            {"name": "Class A", "size_eur": 850_000_000.0, "rate": "3.62", "seniority": 0},
            {"name": "Class B", "size_eur": 44_800_000.0, "seniority": 1},
        ]
    }
    cap = capital_structure_from_deal_model(model)
    assert cap["class_a_balance"] == 850_000_000.0
    assert cap["class_a_rate_pct"] == 3.62  # bare-numeric rate parses through.


def test_capital_structure_two_tranche_defaults_class_c_zero() -> None:
    model = {
        "tranche_structure": [
            {"name": "Class A", "size_eur": 100.0, "seniority": 0},
            {"name": "Class B", "size_eur": 10.0, "seniority": 1},
        ]
    }
    cap = capital_structure_from_deal_model(model)
    assert cap["class_c_balance"] == 0.0


def test_capital_structure_no_sizes_raises() -> None:
    with pytest.raises(ValueError):
        capital_structure_from_deal_model({"tranche_structure": []})


# ===========================================================================
# 3. build_period_inputs — engine input list keyed by reporting date
# ===========================================================================


def test_build_period_inputs_one_per_collateral_period() -> None:
    inputs = build_period_inputs(_ledger())
    assert [p.reporting_date for p in inputs] == ["2025-09-30", "2025-10-31", "2025-12-31"]
    assert inputs[0].collections.total_principal == 10_000_000.0


# ===========================================================================
# 4. run_pool_pipeline — the pure core, real engine end-to-end
# ===========================================================================


def test_pipeline_runs_real_engine_and_returns_report() -> None:
    report = _run()
    assert isinstance(report, PoolPipelineReport)
    assert report.deal_name == "Synthetic Seasoned B.V."
    # Two quarterly notes-cash dates overlap the collateral series.
    assert report.periods_compared == 2
    assert {p.reporting_date for p in report.periods} == {"2025-09-30", "2025-12-31"}


def test_pipeline_is_labelled_coarse_pool_level() -> None:
    report = _run()
    assert report.granularity == GRANULARITY
    assert "pool-level" in report.granularity
    # The standing caveats name every precision-loss source.
    assert report.caveats == list(STANDING_CAVEATS)
    assert any("no loan tape" in c or "loan-level" in c for c in report.caveats)
    assert any("uarterly" in c for c in report.caveats)
    # The narrative is a characterisation, not a PASS/FAIL.
    assert "CHARACTERISATION" in report.match_quality
    assert "COARSE" in report.summary
    assert report.coarse_band_pct == DEFAULT_COARSE_BAND_PCT


def test_pipeline_computes_per_line_deltas() -> None:
    report = _run()
    sep = next(p for p in report.periods if p.reporting_date == "2025-09-30")
    a_line = next(c for c in sep.line_checks if c.line_item == "class_a_balance")
    # Reconstructed Class A amortises off the pool reduction; the partly-indicative
    # engine does not tie to the cent (~840.5M vs reported 840M), which is exactly
    # the coarseness this harness exists to characterise — well within the band.
    assert a_line.reconstructed_value == pytest.approx(840_000_000.0, rel=0.01)
    assert a_line.reported_value == 840_000_000.0
    assert a_line.delta == a_line.reconstructed_value - a_line.reported_value
    assert a_line.abs_delta == abs(a_line.delta)
    assert a_line.within_coarse_band is True
    # Reserve seeded at target and reported at target → within band.
    res = next(c for c in sep.line_checks if c.line_item == "reserve_balance")
    assert res.within_coarse_band is True


def test_pipeline_flags_out_of_band_line() -> None:
    # Make the reported Class A wildly different so its line falls out of band.
    nc = _notes_cash()
    nc.periods[0].note_balances[0].principal_balance_after_payment = 500_000_000.0
    report = run_pool_pipeline(
        _ledger(), nc, _CAP_STRUCTURE,
        reserve_target=_RESERVE_TARGET, original_pool_balance=_ORIGINAL_POOL,
        seed_reporting_date=_SEED_DATE,
    )
    sep = next(p for p in report.periods if p.reporting_date == "2025-09-30")
    a_line = next(c for c in sep.line_checks if c.line_item == "class_a_balance")
    assert a_line.within_coarse_band is False
    assert report.lines_within_band < report.lines_total


def test_pipeline_surfaces_unmatched_dates() -> None:
    report = _run()
    # The monthly October collateral period has no quarterly notes-cash period.
    assert "2025-10-31" in report.unmatched_reconstructed_dates
    # All notes-cash dates matched here → none unmatched on that side.
    assert report.unmatched_notes_cash_dates == []


def test_pipeline_surfaces_unmatched_notes_cash_date() -> None:
    # Add a notes-cash period with no collateral period on that date.
    nc = _notes_cash()
    nc.periods.append(
        NotesCashPeriod(
            reporting_date="2026-03-31",
            period_label="March 2026",
            note_balances=[
                NoteClassBalance(note_class="class_a", principal_balance_after_payment=800_000_000.0)
            ],
        )
    )
    nc = NotesCashReport(deal_name=nc.deal_name, periods=nc.periods)
    report = run_pool_pipeline(
        _ledger(), nc, _CAP_STRUCTURE,
        reserve_target=_RESERVE_TARGET, original_pool_balance=_ORIGINAL_POOL,
        seed_reporting_date=_SEED_DATE,
    )
    assert "2026-03-31" in report.unmatched_notes_cash_dates
    assert "2026-03-31" in report.summary  # surfaced in the one-line summary.


def test_pipeline_empty_overlap_characterises_not_fails() -> None:
    # Notes-cash dates that never coincide with the collateral series.
    nc = NotesCashReport(
        deal_name="Synthetic Seasoned B.V.",
        periods=[
            NotesCashPeriod(
                reporting_date="2030-01-31",
                period_label="January 2030",
                note_balances=[
                    NoteClassBalance(note_class="class_a", principal_balance_after_payment=1.0)
                ],
            )
        ],
    )
    report = run_pool_pipeline(
        _ledger(), nc, _CAP_STRUCTURE,
        reserve_target=_RESERVE_TARGET, original_pool_balance=_ORIGINAL_POOL,
        seed_reporting_date=_SEED_DATE,
    )
    assert report.periods_compared == 0
    assert report.within_band_pct is None
    assert report.lines_total == 0
    # Empty overlap is characterised as an expected cadence mismatch, not a crash.
    assert "No overlapping" in report.match_quality
    assert "2030-01-31" in report.unmatched_notes_cash_dates


def test_pipeline_within_band_pct_aggregates_across_periods() -> None:
    report = _run()
    assert report.lines_total > 0
    expected = report.lines_within_band / report.lines_total * 100.0
    assert report.within_band_pct == pytest.approx(expected)


def test_line_check_reported_zero_reconstructed_nonzero_out_of_band() -> None:
    # A reported-0 line with a non-zero reconstruction is unbounded → out of band.
    from loanwhiz.primitives.pool_pipeline_harness import _build_line_check

    check: LiabilityLineCheck = _build_line_check("x", 100.0, 0.0, 5.0)
    assert check.delta_pct is None
    assert check.within_coarse_band is False
