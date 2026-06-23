"""Tests for ``TapeAdapter`` — the ESMA tape → canonical ``PeriodInputs`` adapter (#364).

Covers: RiskSignals derivation (percent→balance, the conservative ``arrears_90d``
union, the zero-pool / zero-pct edge cases), the legs mapping, ``source=="tape"``,
the ESMA RREL provenance anchors, and the **byte-for-byte safety property** — a
tape-source ``PeriodInputs`` reduces through ``_normalize_period`` to the exact
same ``_NormalizedPeriod`` the equivalent legacy ``PeriodInput`` produces, so the
live tape reconstruction's engine output is unchanged by the migration.
"""

from __future__ import annotations

import pytest

# Import the primitives package before ``loanwhiz.domain.inputs`` so the shared
# ``primitives.base`` ↔ ``domain.provenance`` import cycle resolves
# primitives-first (a pre-existing import-order sensitivity in the package; the
# full suite primes it via an earlier-collected module, but importing
# primitives first here makes this file robust when run in isolation too).
import loanwhiz.primitives  # noqa: F401  (import-order priming)
from loanwhiz.primitives.collections_aggregator import CollectionsOutput
from loanwhiz.primitives.deal_state import PeriodCollections
from loanwhiz.primitives.esma_tape_normaliser import EsmaTapeOutput
from loanwhiz.primitives.period_state_machine import PeriodInput, _normalize_period
from loanwhiz.primitives.tape_adapter import TapeAdapter

from loanwhiz.domain.inputs import PeriodInputs, RiskSignals


def _tape(
    *,
    pool_balance_eur: float = 1_000_000.0,
    wtd_ltv: float | None = 72.5,
    arrears_180d_plus_pct: float = 4.0,
    default_pct: float = 2.0,
    transaction_name: str | None = "Test Deal 2026-1",
) -> EsmaTapeOutput:
    """A minimal ``EsmaTapeOutput`` with the fields the adapter reads."""
    return EsmaTapeOutput(
        reporting_date="2026-03-31",
        asset_class="RMBS",
        transaction_name=transaction_name,
        loan_count=100,
        pool_balance_eur=pool_balance_eur,
        pool_stats={} if wtd_ltv is None else {"wtd_ltv": wtd_ltv},
        arrears_breakdown={
            "current_pct": round(
                100.0 - arrears_180d_plus_pct - default_pct, 4
            ),
            "arrears_1_2m_pct": 0.0,
            "arrears_180d_plus_pct": arrears_180d_plus_pct,
            "default_pct": default_pct,
        },
        epc_breakdown=None,
        rate_type_breakdown=None,
        property_type_breakdown=None,
        geographic_breakdown=None,
        annex_detected="Annex 2 (RMBS)",
        data_source="direct",
    )


def _collections(
    *,
    interest: float = 30_000.0,
    scheduled: float = 50_000.0,
    prepayment: float = 12_000.0,
    recovery: float = 3_000.0,
    realized_loss: float = 1_500.0,
    pool_balance: float = 1_000_000.0,
) -> CollectionsOutput:
    """A ``CollectionsOutput`` with separated legs (per-loan derivation shape)."""
    arf = interest  # swap_receipts 0 for a plain deal
    apf = scheduled + prepayment + recovery
    return CollectionsOutput(
        reporting_period="2026-03-31",
        interest_collected=interest,
        swap_receipts=0.0,
        available_revenue_funds=arf,
        scheduled_principal=scheduled,
        unscheduled_principal=prepayment,
        recoveries=recovery,
        realized_losses=realized_loss,
        available_principal_funds=apf,
        pool_balance_eur=pool_balance,
        loan_count=100,
        derivation="per-loan",
        class_a_interest_due=9_000.0,
        senior_fees=50_000.0,
        summary="test",
    )


# ---------------------------------------------------------------------------
# RiskSignals derivation
# ---------------------------------------------------------------------------


class TestRiskSignals:
    def test_pool_balance_and_ltv_pass_through(self) -> None:
        signals, _ = TapeAdapter.risk_signals_from_tape(
            _tape(pool_balance_eur=2_000_000.0, wtd_ltv=68.0)
        )
        assert signals.pool_balance == 2_000_000.0
        assert signals.wa_ltv == 68.0

    def test_arrears_percent_to_balance_and_default_to_fraction(self) -> None:
        # 180d+ 4% of €1,000,000 = €40,000 (balance); default 2% → 0.02 fraction.
        signals, _ = TapeAdapter.risk_signals_from_tape(
            _tape(pool_balance_eur=1_000_000.0, arrears_180d_plus_pct=4.0, default_pct=2.0)
        )
        assert signals.arrears_180d == pytest.approx(40_000.0)
        # default_pct is the defaulted FRACTION of the pool (0–1), per the
        # canonical RiskSignals contract — not a balance.
        assert signals.default_pct == pytest.approx(0.02)

    def test_arrears_90d_is_180d_union_default_balance(self) -> None:
        # arrears_90d is a *balance*: ≥180d ∪ defaulted = €40,000 + €20,000.
        signals, _ = TapeAdapter.risk_signals_from_tape(
            _tape(pool_balance_eur=1_000_000.0, arrears_180d_plus_pct=4.0, default_pct=2.0)
        )
        assert signals.arrears_90d == pytest.approx(60_000.0)
        assert signals.arrears_90d >= signals.arrears_180d

    def test_zero_pool_yields_zero_balances_not_fabricated(self) -> None:
        # Zero pool → zero arrears *balances* (nothing to apportion). The default
        # *fraction* is independent of pool size, but the percentages here are
        # also nonzero; the balances are what must collapse to 0.
        signals, _ = TapeAdapter.risk_signals_from_tape(
            _tape(pool_balance_eur=0.0, arrears_180d_plus_pct=4.0, default_pct=2.0)
        )
        assert signals.pool_balance == 0.0
        assert signals.arrears_180d == 0.0
        assert signals.arrears_90d == 0.0
        # The defaulted fraction is still the reported 2% (a fraction, not a
        # balance) — it does not depend on the pool balance.
        assert signals.default_pct == pytest.approx(0.02)

    def test_zero_pct_yields_zero(self) -> None:
        signals, _ = TapeAdapter.risk_signals_from_tape(
            _tape(arrears_180d_plus_pct=0.0, default_pct=0.0)
        )
        assert signals.arrears_90d == 0.0
        assert signals.arrears_180d == 0.0
        assert signals.default_pct == 0.0

    def test_missing_wtd_ltv_defaults_to_zero(self) -> None:
        signals, _ = TapeAdapter.risk_signals_from_tape(_tape(wtd_ltv=None))
        assert signals.wa_ltv == 0.0

    def test_provenance_carries_annex2_rrel_anchors(self) -> None:
        _, prov = TapeAdapter.risk_signals_from_tape(_tape())
        # Every RiskSignals field has a tape-sourced provenance entry whose
        # citation locator is an ESMA RREL code.
        expected = {
            "risk_signals.pool_balance": "RREL18",
            "risk_signals.wa_ltv": "RREL40",
            "risk_signals.default_pct": "RREL66",
            "risk_signals.arrears_180d": "RREL64",
            "risk_signals.arrears_90d": "RREL64",
        }
        for key, code in expected.items():
            assert key in prov, key
            entry = prov[key]
            assert entry.source == "tape"
            assert entry.method == "deterministic"
            assert entry.confidence == 1.0
            assert entry.citation is not None
            assert entry.citation.page_or_row == code


# ---------------------------------------------------------------------------
# Legs + PeriodInputs assembly
# ---------------------------------------------------------------------------


class TestPeriodInputs:
    def test_legs_map_one_to_one(self) -> None:
        legs = TapeAdapter.legs_from_collections(_collections())
        assert legs.interest == 30_000.0
        assert legs.scheduled_principal == 50_000.0
        assert legs.prepayment == 12_000.0
        assert legs.recovery == 3_000.0
        assert legs.realized_loss == 1_500.0

    def test_period_inputs_is_tape_sourced_with_legs_and_signals(self) -> None:
        pi = TapeAdapter().period_inputs(
            _collections(),
            _tape(),
            reporting_date="2026-03-31",
            days_in_period=90,
        )
        assert isinstance(pi, PeriodInputs)
        assert pi.source == "tape"
        assert pi.legs is not None
        assert isinstance(pi.risk_signals, RiskSignals)
        assert pi.reporting_date == "2026-03-31"
        assert pi.days_in_period == 90
        # Aggregates come straight from the CollectionsOutput.
        assert pi.available_revenue == 30_000.0
        assert pi.available_principal == 65_000.0
        # No report-supplied overrides on the tape path.
        assert pi.step_overrides == {}
        assert pi.revenue_step_overrides == {}
        assert pi.redemption_step_overrides == {}

    def test_period_inputs_carries_risk_provenance(self) -> None:
        pi = TapeAdapter().period_inputs(
            _collections(),
            _tape(),
            reporting_date="2026-03-31",
            days_in_period=90,
        )
        assert "risk_signals.pool_balance" in pi.provenance
        assert pi.provenance["risk_signals.wa_ltv"].citation.page_or_row == "RREL40"


# ---------------------------------------------------------------------------
# Byte-for-byte safety: tape PeriodInputs ↔ legacy PeriodInput equivalence.
# ---------------------------------------------------------------------------


class TestNormalizeEquivalence:
    def test_reduces_identically_to_legacy_period_input(self) -> None:
        """A tape-source ``PeriodInputs`` with ``legs`` must reduce through
        ``_normalize_period`` to the same ``_NormalizedPeriod`` the equivalent
        legacy ``PeriodInput`` produces — the property that guarantees the live
        tape reconstruction's engine output is unchanged by the migration."""
        collections = _collections()
        tape_pi = TapeAdapter().period_inputs(
            collections,
            _tape(),
            reporting_date="2026-03-31",
            days_in_period=90,
        )

        legacy_pi = PeriodInput(
            reporting_date="2026-03-31",
            collections=collections.to_period_collections(),
            days_in_period=90,
        )

        canon = _normalize_period(tape_pi)
        legacy = _normalize_period(legacy_pi)

        # The legs-derived collections are identical.
        assert canon.collections == legacy.collections
        assert canon.available_revenue == legacy.available_revenue
        assert canon.available_principal == legacy.available_principal
        assert canon.reporting_date == legacy.reporting_date
        assert canon.days_in_period == legacy.days_in_period
        # The tape path carries no report-supplied overrides → engine unchanged.
        assert not canon.revenue_step_overrides
        assert not canon.redemption_step_overrides
        assert canon.report_sourced is False
