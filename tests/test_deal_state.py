"""Tests for the canonical ``DealState`` + period-transition contract (S1, #181).

Covers the three deliverables of issue #181:

1. **Schema** — ``DealState`` / ``PeriodCollections`` / ``WaterfallResult`` carry
   every field the issue enumerates, with non-negativity validation and the
   derived quantities (pool factor, loss rate, totals).
2. **Transition contract** — ``apply_collections`` / ``apply_losses`` /
   ``apply_waterfall_result`` and the composing ``transition``; the
   closing→opening identity (``closing[N]`` is a valid ``opening[N+1]``),
   conservation, and non-negativity of the fields the contract owns
   (PDL cap, reserve floor/cap, tranche redemption floor, loss allocation).
3. **Prospectus seed** — ``seed_from_prospectus`` builds the period-0 opening
   state data-driven from passed figures (PDL=0, reserve=target, factor at par).

Green Lion 2026-1 figures are used as a *concrete* deal (sourced as data, not
hardcoded into the module under test):
  - Class A €1.0B, Class B €53.1M, Class C €10.5M
  - Reserve target €10,636,000
  - Original pool balance €1,063,600,000
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from loanwhiz.primitives.deal_state import (
    DealState,
    PeriodCollections,
    TranchePayment,
    WaterfallResult,
)
from loanwhiz.domain.state import TrancheState

# ---------------------------------------------------------------------------
# Concrete deal figures (Green Lion 2026-1) — supplied as data to the seed.
# ---------------------------------------------------------------------------

_CAP_STRUCTURE = {
    "class_a_balance": 1_000_000_000.0,
    "class_a_rate_pct": 3.62,  # extra key — must be ignored by the seed
    "class_b_balance": 53_100_000.0,
    "class_c_balance": 10_500_000.0,
}
_RESERVE_TARGET = 10_636_000.0
_ORIGINAL_POOL = 1_063_600_000.0


def _seed(**overrides) -> DealState:
    kwargs = dict(
        capital_structure=_CAP_STRUCTURE,
        reserve_target=_RESERVE_TARGET,
        original_pool_balance=_ORIGINAL_POOL,
        reporting_date="2026-02-28",
        revolving=True,
    )
    kwargs.update(overrides)
    return DealState.seed_from_prospectus(**kwargs)


# ===========================================================================
# 1. Schema
# ===========================================================================


def test_dealstate_carries_all_enumerated_fields() -> None:
    """DealState exposes every field the issue enumerates."""
    state = _seed()
    # Per-tranche balances.
    assert state.class_a_balance == 1_000_000_000.0
    assert state.class_b_balance == 53_100_000.0
    assert state.class_c_balance == 10_500_000.0
    # Per-class PDL ledgers.
    assert state.class_a_pdl == 0.0
    assert state.class_b_pdl == 0.0
    assert state.class_c_pdl == 0.0
    # Reserve balance + target.
    assert state.reserve_balance == _RESERVE_TARGET
    assert state.reserve_target == _RESERVE_TARGET
    # Cumulative losses, pool, factor, flag, date.
    assert state.cumulative_losses == 0.0
    assert state.pool_balance == _ORIGINAL_POOL
    assert state.pool_factor == 1.0
    assert state.revolving is True
    assert state.reporting_date == "2026-02-28"
    assert state.period_index == 0


def test_period_collections_fields_and_total_principal() -> None:
    c = PeriodCollections(
        interest=2_600_000.0,
        scheduled_principal=4_000_000.0,
        prepayment=1_500_000.0,
        recovery=10_000.0,
        realized_loss=5_000.0,
    )
    assert c.total_principal == 5_500_000.0
    assert c.interest == 2_600_000.0
    assert c.recovery == 10_000.0
    assert c.realized_loss == 5_000.0


def test_waterfall_result_defaults_to_zero() -> None:
    r = WaterfallResult()
    assert r.class_a_principal == 0.0
    assert r.class_b_pdl_replenishment == 0.0
    assert r.reserve_payment == 0.0
    assert r.reserve_draw == 0.0


def test_derived_quantities() -> None:
    seed = _seed()
    pdls = {"class_a": 100.0, "class_b": 50.0, "class_c": 25.0}
    state = seed.model_copy(
        update={
            "tranches": [
                t.model_copy(update={"pdl_balance": pdls[t.name]})
                for t in seed.tranches
            ],
            "cumulative_losses": 1_063_600.0,  # exactly 0.1% of original pool
        }
    )
    assert state.total_pdl == 175.0
    assert state.total_liabilities == 1_063_600_000.0
    assert state.cumulative_loss_rate_pct == pytest.approx(0.1)


def test_dealstate_is_frozen_immutable() -> None:
    state = _seed()
    with pytest.raises(ValidationError):
        state.class_a_balance = 0.0  # type: ignore[misc]


def test_negative_balance_rejected() -> None:
    with pytest.raises(ValidationError):
        DealState(
            reporting_date="2026-02-28",
            class_a_balance=-1.0,
            class_b_balance=0.0,
            class_c_balance=0.0,
            pool_balance=0.0,
            original_pool_balance=_ORIGINAL_POOL,
        )


def test_empty_reporting_date_rejected() -> None:
    with pytest.raises(ValidationError):
        DealState(
            reporting_date="   ",
            class_a_balance=0.0,
            class_b_balance=0.0,
            class_c_balance=0.0,
            pool_balance=0.0,
            original_pool_balance=_ORIGINAL_POOL,
        )


def test_zero_original_pool_rejected() -> None:
    with pytest.raises(ValidationError):
        DealState(
            reporting_date="2026-02-28",
            class_a_balance=0.0,
            class_b_balance=0.0,
            class_c_balance=0.0,
            pool_balance=0.0,
            original_pool_balance=0.0,
        )


# ===========================================================================
# 2a. Seed
# ===========================================================================


def test_seed_from_prospectus_period0() -> None:
    state = _seed()
    # Liabilities seed from the prospectus capital structure.
    assert state.class_a_balance == _CAP_STRUCTURE["class_a_balance"]
    assert state.class_b_balance == _CAP_STRUCTURE["class_b_balance"]
    assert state.class_c_balance == _CAP_STRUCTURE["class_c_balance"]
    # PDLs start at zero; reserve opens fully funded at target.
    assert state.total_pdl == 0.0
    assert state.reserve_balance == state.reserve_target == _RESERVE_TARGET
    assert state.cumulative_losses == 0.0
    # Pool factor at par when opening pool == original pool.
    assert state.pool_factor == 1.0
    assert state.period_index == 0


def test_seed_ignores_extra_capital_structure_keys() -> None:
    """Extra keys (rates etc.) in the capital structure are ignored, not required."""
    state = _seed()
    # class_a_rate_pct was present in _CAP_STRUCTURE but is not a DealState field.
    assert not hasattr(state, "class_a_rate_pct")


def test_seed_missing_tranche_raises() -> None:
    with pytest.raises(KeyError):
        DealState.seed_from_prospectus(
            capital_structure={"class_a_balance": 1.0, "class_b_balance": 2.0},
            reserve_target=_RESERVE_TARGET,
            original_pool_balance=_ORIGINAL_POOL,
            reporting_date="2026-02-28",
        )


def test_seed_with_opening_pool_below_par() -> None:
    state = _seed(opening_pool_balance=_ORIGINAL_POOL / 2.0)
    assert state.pool_balance == _ORIGINAL_POOL / 2.0
    assert state.pool_factor == pytest.approx(0.5)


def test_seed_is_deal_agnostic() -> None:
    """A different deal's figures flow through unchanged — no hardcoded branch."""
    other = DealState.seed_from_prospectus(
        capital_structure={
            "class_a_balance": 500.0,
            "class_b_balance": 300.0,
            "class_c_balance": 200.0,
        },
        reserve_target=50.0,
        original_pool_balance=1000.0,
        reporting_date="2030-01-31",
        revolving=False,
    )
    assert other.total_liabilities == 1000.0
    assert other.reserve_balance == 50.0
    assert other.pool_factor == 1.0
    assert other.revolving is False


# ===========================================================================
# 2b. apply_collections
# ===========================================================================


def test_apply_collections_advances_pool_and_records() -> None:
    state = _seed()
    c = PeriodCollections(
        interest=2_600_000.0, scheduled_principal=4_000_000.0, prepayment=1_500_000.0
    )
    new = state.apply_collections(c)
    # Pool reduced by scheduled + prepayment principal.
    assert new.pool_balance == _ORIGINAL_POOL - 5_500_000.0
    assert new.pool_factor == pytest.approx(new.pool_balance / _ORIGINAL_POOL)
    assert new.collections == c
    # Original state untouched (immutable).
    assert state.pool_balance == _ORIGINAL_POOL
    assert state.collections is None


def test_apply_collections_floors_pool_at_zero() -> None:
    state = _seed(opening_pool_balance=1_000_000.0)
    c = PeriodCollections(scheduled_principal=2_000_000.0)
    new = state.apply_collections(c)
    assert new.pool_balance == 0.0
    assert new.pool_factor == 0.0


def test_interest_and_recovery_do_not_move_pool() -> None:
    state = _seed()
    c = PeriodCollections(interest=2_600_000.0, recovery=50_000.0)
    new = state.apply_collections(c)
    assert new.pool_balance == _ORIGINAL_POOL


# ===========================================================================
# 2c. apply_losses
# ===========================================================================


def test_apply_losses_zero_is_noop() -> None:
    state = _seed()
    assert state.apply_losses(0.0) is state


def test_apply_losses_negative_treated_as_zero() -> None:
    state = _seed()
    assert state.apply_losses(-100.0) is state


def test_apply_losses_allocates_junior_first() -> None:
    state = _seed()
    # Loss smaller than Class C balance: only Class C's PDL is debited.
    new = state.apply_losses(1_000_000.0)
    assert new.class_c_pdl == 1_000_000.0
    assert new.class_b_pdl == 0.0
    assert new.class_a_pdl == 0.0
    assert new.cumulative_losses == 1_000_000.0


def test_apply_losses_cascades_when_junior_exhausted() -> None:
    state = _seed()
    # Loss exceeds Class C (10.5M); residual spills to Class B.
    loss = 10_500_000.0 + 2_000_000.0
    new = state.apply_losses(loss)
    assert new.class_c_pdl == 10_500_000.0  # capped at C's balance
    assert new.class_b_pdl == 2_000_000.0
    assert new.class_a_pdl == 0.0
    assert new.cumulative_losses == loss


def test_apply_losses_caps_each_pdl_at_balance_residual_dropped() -> None:
    """A loss exceeding total structural capacity caps PDLs; cumulative records full."""
    state = _seed()
    total_capacity = 1_000_000_000.0 + 53_100_000.0 + 10_500_000.0
    loss = total_capacity + 1_000_000.0
    new = state.apply_losses(loss)
    # Each PDL capped at its tranche balance.
    assert new.class_c_pdl == 10_500_000.0
    assert new.class_b_pdl == 53_100_000.0
    assert new.class_a_pdl == 1_000_000_000.0
    assert new.total_pdl == total_capacity
    # Cumulative losses still record the full realized loss.
    assert new.cumulative_losses == loss


# ===========================================================================
# 2d. apply_waterfall_result
# ===========================================================================


def test_apply_waterfall_redeems_tranches() -> None:
    state = _seed()
    r = WaterfallResult(class_a_principal=5_000_000.0)
    new = state.apply_waterfall_result(r)
    assert new.class_a_balance == 1_000_000_000.0 - 5_000_000.0
    assert new.class_b_balance == 53_100_000.0  # untouched


def test_apply_waterfall_tranche_redemption_floored() -> None:
    state = _seed()
    r = WaterfallResult(class_c_principal=20_000_000.0)  # > C balance
    new = state.apply_waterfall_result(r)
    assert new.class_c_balance == 0.0


def test_apply_waterfall_replenishes_pdl_capped() -> None:
    seed = _seed()
    state = seed.model_copy(
        update={
            "tranches": [
                t.model_copy(update={"pdl_balance": 1_000_000.0})
                if t.name == "class_b"
                else t
                for t in seed.tranches
            ]
        }
    )
    assert state.class_b_pdl == 1_000_000.0  # tamper landed on the tranche list
    # Offer more than owed — replenishment caps at the outstanding PDL.
    r = WaterfallResult(class_b_pdl_replenishment=5_000_000.0)
    new = state.apply_waterfall_result(r)
    assert new.class_b_pdl == 0.0


def test_apply_waterfall_reserve_topup_capped_at_target() -> None:
    state = _seed().model_copy(update={"reserve_balance": 5_000_000.0})
    r = WaterfallResult(reserve_payment=100_000_000.0)  # huge top-up
    new = state.apply_waterfall_result(r)
    assert new.reserve_balance == _RESERVE_TARGET  # capped at target


def test_apply_waterfall_reserve_over_target_not_clawed_back() -> None:
    """A balance already above target is not reduced by a zero/partial top-up."""
    state = _seed().model_copy(update={"reserve_balance": _RESERVE_TARGET + 1_000_000.0})
    new = state.apply_waterfall_result(WaterfallResult(reserve_payment=0.0))
    assert new.reserve_balance == _RESERVE_TARGET + 1_000_000.0


def test_apply_waterfall_reserve_draw_floored_at_zero() -> None:
    state = _seed().model_copy(update={"reserve_balance": 1_000.0})
    r = WaterfallResult(reserve_draw=5_000.0)  # draw more than present
    new = state.apply_waterfall_result(r)
    assert new.reserve_balance == 0.0


def test_apply_waterfall_reserve_net_payment_then_draw() -> None:
    state = _seed().model_copy(update={"reserve_balance": 0.0})
    r = WaterfallResult(reserve_payment=1_000_000.0, reserve_draw=400_000.0)
    new = state.apply_waterfall_result(r)
    assert new.reserve_balance == 600_000.0


# ===========================================================================
# 2e. transition + closing→opening identity
# ===========================================================================


def test_transition_composes_all_legs() -> None:
    state = _seed()
    collections = PeriodCollections(
        interest=2_600_000.0,
        scheduled_principal=4_000_000.0,
        prepayment=1_500_000.0,
        realized_loss=0.0,
    )
    result = WaterfallResult(class_a_principal=5_500_000.0)
    closing = state.transition(collections=collections, waterfall_result=result)
    # Asset side advanced.
    assert closing.pool_balance == _ORIGINAL_POOL - 5_500_000.0
    assert closing.collections == collections
    # Liability side: Class A redeemed by the waterfall.
    assert closing.class_a_balance == 1_000_000_000.0 - 5_500_000.0


def test_transition_uses_collections_realized_loss_by_default() -> None:
    state = _seed()
    collections = PeriodCollections(realized_loss=3_000_000.0)
    closing = state.transition(
        collections=collections, waterfall_result=WaterfallResult()
    )
    # Loss allocated junior-first from collections.realized_loss.
    assert closing.class_c_pdl == 3_000_000.0
    assert closing.cumulative_losses == 3_000_000.0


def test_transition_explicit_realized_loss_overrides() -> None:
    state = _seed()
    collections = PeriodCollections(realized_loss=3_000_000.0)
    closing = state.transition(
        collections=collections,
        waterfall_result=WaterfallResult(),
        realized_loss=1_000_000.0,
    )
    assert closing.cumulative_losses == 1_000_000.0


def test_closing_is_a_valid_opening_for_next_period() -> None:
    """The S6 seam invariant: closing[N] is a valid opening[N+1] DealState."""
    state = _seed()
    closing = state.transition(
        collections=PeriodCollections(scheduled_principal=6_270_522.20),
        waterfall_result=WaterfallResult(class_a_principal=6_270_522.20),
        next_reporting_date="2026-03-31",
        next_revolving=True,
    )
    # It is a DealState — usable verbatim as the next opening.
    assert isinstance(closing, DealState)
    assert closing.reporting_date == "2026-03-31"
    assert closing.period_index == 1
    # Feed it straight back in as opening[N+1].
    closing2 = closing.transition(
        collections=PeriodCollections(scheduled_principal=9_081_226.70),
        waterfall_result=WaterfallResult(class_a_principal=9_081_226.70),
        next_reporting_date="2026-04-30",
    )
    assert closing2.period_index == 2
    assert closing2.reporting_date == "2026-04-30"


def test_transition_chain_conserves_principal() -> None:
    """Pool reduction across a chain equals total principal collected."""
    state = _seed()
    p1 = 6_270_522.20
    p2 = 9_081_226.70
    s1 = state.transition(
        collections=PeriodCollections(scheduled_principal=p1),
        waterfall_result=WaterfallResult(class_a_principal=p1),
        next_reporting_date="2026-03-31",
    )
    s2 = s1.transition(
        collections=PeriodCollections(scheduled_principal=p2),
        waterfall_result=WaterfallResult(class_a_principal=p2),
        next_reporting_date="2026-04-30",
    )
    assert s2.pool_balance == pytest.approx(_ORIGINAL_POOL - (p1 + p2))
    # Class A redeemed by the same total.
    assert s2.class_a_balance == pytest.approx(1_000_000_000.0 - (p1 + p2))


def test_transition_preserves_metadata_when_no_rollover() -> None:
    state = _seed()
    closing = state.transition(
        collections=PeriodCollections(),
        waterfall_result=WaterfallResult(),
    )
    # No next_* passed → period metadata carries forward unchanged.
    assert closing.reporting_date == "2026-02-28"
    assert closing.period_index == 0
    assert closing.revolving is True


def test_non_negativity_holds_across_extreme_transition() -> None:
    """No field goes negative even under a loss-and-redeem-everything period."""
    state = _seed(opening_pool_balance=5_000_000.0)
    closing = state.transition(
        collections=PeriodCollections(
            scheduled_principal=10_000_000.0, realized_loss=2_000_000_000.0
        ),
        waterfall_result=WaterfallResult(
            class_a_principal=2_000_000_000.0,
            class_b_principal=2_000_000_000.0,
            class_c_principal=2_000_000_000.0,
            reserve_draw=2_000_000_000.0,
        ),
    )
    for field in (
        closing.pool_balance,
        closing.class_a_balance,
        closing.class_b_balance,
        closing.class_c_balance,
        closing.reserve_balance,
        closing.class_a_pdl,
        closing.class_b_pdl,
        closing.class_c_pdl,
    ):
        assert field >= 0.0
    assert not math.isnan(closing.pool_factor)


# ===========================================================================
# 4. Canonical tranche-list storage + backward-compatible accessors (#363)
# ===========================================================================


def test_tranches_is_canonical_store_in_seniority_order() -> None:
    """The seed lays liabilities out as a tranches list in A/B/C order."""
    state = _seed()
    assert [t.name for t in state.tranches] == ["class_a", "class_b", "class_c"]
    assert state.tranches[0].balance == _CAP_STRUCTURE["class_a_balance"]
    assert state.tranches[1].balance == _CAP_STRUCTURE["class_b_balance"]
    assert state.tranches[2].balance == _CAP_STRUCTURE["class_c_balance"]
    assert all(t.pdl_balance == 0.0 for t in state.tranches)


def test_class_accessors_equal_tranche_list_values() -> None:
    """``.class_{a,b,c}_balance|_pdl`` return the matching tranche-list values."""
    seed = _seed()
    # Put a distinct PDL on each tranche so the accessor mapping is unambiguous.
    pdls = {"class_a": 11.0, "class_b": 22.0, "class_c": 33.0}
    state = seed.model_copy(
        update={
            "tranches": [
                t.model_copy(update={"pdl_balance": pdls[t.name]})
                for t in seed.tranches
            ]
        }
    )
    by_name = {t.name: t for t in state.tranches}
    assert state.class_a_balance == by_name["class_a"].balance
    assert state.class_b_balance == by_name["class_b"].balance
    assert state.class_c_balance == by_name["class_c"].balance
    assert state.class_a_pdl == by_name["class_a"].pdl_balance == 11.0
    assert state.class_b_pdl == by_name["class_b"].pdl_balance == 22.0
    assert state.class_c_pdl == by_name["class_c"].pdl_balance == 33.0
    # getattr (the path series_invariants uses) resolves the accessors too.
    assert getattr(state, "class_b_pdl") == 22.0


def test_class_kwargs_construction_folds_into_tranches() -> None:
    """Building with legacy ``class_*`` kwargs populates the tranche list."""
    state = DealState(
        reporting_date="2026-02-28",
        class_a_balance=100.0,
        class_b_balance=50.0,
        class_c_balance=10.0,
        class_a_pdl=5.0,
        pool_balance=160.0,
        original_pool_balance=160.0,
    )
    assert [t.name for t in state.tranches] == ["class_a", "class_b", "class_c"]
    assert state.class_a_balance == 100.0
    assert state.class_a_pdl == 5.0
    assert state.total_liabilities == 160.0


def test_non_abc_tranche_structure_accessors_and_loss_allocation() -> None:
    """A non-A/B/C structure stores + amortises losses across its own tranches."""
    state = DealState(
        reporting_date="2026-02-28",
        tranches=[
            TrancheState(name="senior", balance=1000.0, pdl_balance=0.0),
            TrancheState(name="mezz", balance=400.0, pdl_balance=0.0),
            TrancheState(name="junior", balance=100.0, pdl_balance=0.0),
        ],
        reserve_balance=0.0,
        reserve_target=0.0,
        cumulative_losses=0.0,
        pool_balance=1500.0,
        original_pool_balance=1500.0,
    )
    # No A/B/C tranches → the legacy accessors fall back to 0 cleanly.
    assert state.class_a_balance == 0.0
    assert state.total_liabilities == 1500.0
    # A 150 loss with an explicit junior→senior order absorbs junior-first.
    after = state.apply_losses(150.0, allocation=("junior", "mezz", "senior"))
    by = {t.name: t for t in after.tranches}
    assert by["junior"].pdl_balance == 100.0  # junior fully absorbs to its balance
    assert by["mezz"].pdl_balance == 50.0  # residual cascades up to mezz
    assert by["senior"].pdl_balance == 0.0
    assert after.cumulative_losses == 150.0


def test_waterfall_result_round_trips_by_name() -> None:
    """``WaterfallResult`` carries per-tranche outcomes by name with accessors."""
    # Built from the generalised tranche list.
    result = WaterfallResult(
        tranches=[
            TranchePayment(name="class_a", principal=10.0, pdl_replenishment=1.0),
            TranchePayment(name="class_b", principal=5.0, pdl_replenishment=2.0),
        ],
        reserve_payment=3.0,
    )
    assert result.class_a_principal == 10.0
    assert result.class_b_pdl_replenishment == 2.0
    assert result.class_c_principal == 0.0  # absent tranche → 0

    # Built from legacy class_* kwargs → same tranche list + accessor values.
    legacy = WaterfallResult(
        class_a_principal=10.0,
        class_a_pdl_replenishment=1.0,
        class_b_principal=5.0,
        class_b_pdl_replenishment=2.0,
        reserve_payment=3.0,
    )
    assert {t.name for t in legacy.tranches} >= {"class_a", "class_b"}
    assert legacy.class_a_principal == 10.0
    assert legacy.class_b_pdl_replenishment == 2.0
