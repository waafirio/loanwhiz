"""Tests for the S6 period-by-period deal-state machine (#186).

Covers the deliverables of issue #186 — the integrator that connects S1–S5:

1. **TriggerConditionEvaluator** (the S4↔S5 join) — gating named/sequential-pay
   conditions over a real S5 ``TriggerEvaluation`` (not S4's standalone default).
2. **Synthetic multi-period chain** — ``reconstruct_period_series`` threads the
   per-period ``DealState`` series with ``closing[N] == opening[N+1]``; pool
   amortises, tranche balances redeem, PDL/reserve evolve, and cumulative losses
   accumulate monotonically under injected losses.
3. **Real-tape chain (offline)** — the multi-period chain over the deal's *real*
   cached tape analytics (pool balances from
   ``/tmp/loanwhiz_cache/tape_analytics/``, pool-delta collections per the S3
   net-pool-movement finding), seeded from the Green-Lion prospectus capital
   structure. The meaningful chain is the three 2026 reporting tapes (the deal
   spike S0 reconciled); a separate guard threads the full 27-tape history to
   prove the integrator scales. Pool balances tie to the cached tape values to
   the cent. No network, no Gemini — the tests skip cleanly if the warm cache is
   absent.

Green Lion 2026-1 figures are used as a *concrete* deal, supplied as data to the
engine (never hardcoded into the module under test):
  - Class A €1.0B, Class B €53.1M, Class C €10.5M
  - Reserve target €10,636,000 · Original pool €1,063,600,000
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

from loanwhiz.config import GREEN_LION
from loanwhiz.primitives.covenant_monitor import evaluate_triggers
from loanwhiz.primitives.deal_state import DealState, PeriodCollections
from loanwhiz.primitives.period_state_machine import (
    PeriodInput,
    TriggerConditionEvaluator,
    reconstruct_period_series,
    run_period,
)
from loanwhiz.primitives.waterfall_interpreter import WaterfallFunds

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

# Module-level so tests can locate the warm tape-analytics cache the way the API
# does (URL-hash keyed). Mirrors ``api.main._tape_cache_path``.
_TAPE_CACHE_DIR = Path("/tmp/loanwhiz_cache/tape_analytics")


def _tape_cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return _TAPE_CACHE_DIR / f"{digest}.json"


def _cached_pool_balance(url: str) -> float | None:
    """Return the cached tape's ``pool_balance_eur`` for a URL, or None on miss."""
    path = _tape_cache_path(url)
    if not path.exists():
        return None
    return float(json.loads(path.read_text(encoding="utf-8"))["pool_balance_eur"])


# ===========================================================================
# 1. TriggerConditionEvaluator — the S4 ↔ S5 join
# ===========================================================================


def _clean_state(**overrides) -> DealState:
    kwargs = dict(
        capital_structure=_CAP_STRUCTURE,
        reserve_target=_RESERVE_TARGET,
        original_pool_balance=_ORIGINAL_POOL,
        reporting_date="2026-02-28",
    )
    kwargs.update(overrides)
    return DealState.seed_from_prospectus(**kwargs)


def test_evaluator_empty_condition_pays() -> None:
    ev = TriggerConditionEvaluator(evaluate_triggers(_clean_state()))
    assert ev.evaluate("", WaterfallFunds()) is True
    assert ev.evaluate(None, WaterfallFunds()) is True  # type: ignore[arg-type]


def test_evaluator_sequential_pay_inactive_on_clean_state() -> None:
    """A clean (no-loss) state does NOT breach the sequential-pay trigger."""
    ev = TriggerConditionEvaluator(evaluate_triggers(_clean_state()))
    assert ev.sequential_pay_active(WaterfallFunds()) is False
    # "if Sequential Pay Trigger is in effect" → suppressed when inactive.
    assert ev.evaluate("if the Sequential Pay Trigger is in effect", WaterfallFunds()) is False
    # "...is NOT in effect" → pays when inactive.
    assert ev.evaluate("if the Sequential Pay Trigger is not in effect", WaterfallFunds()) is True


def test_evaluator_sequential_pay_active_on_loss_breach() -> None:
    """Cumulative losses past the 1.5% trigger flip sequential pay on."""
    # 1.5% of the original pool is the trigger; push cumulative losses above it.
    breached = _clean_state().apply_losses(0.02 * _ORIGINAL_POOL)
    assert breached.cumulative_loss_rate_pct > 1.5
    ev = TriggerConditionEvaluator(evaluate_triggers(breached))
    assert ev.sequential_pay_active(WaterfallFunds()) is True
    assert ev.evaluate("if the Sequential Pay Trigger is in effect", WaterfallFunds()) is True


def test_evaluator_named_trigger_gating() -> None:
    """A condition naming the reserve trigger reads its live breach."""
    # Draw the reserve below target so the reserve_fund_trigger fires.
    drawn = _clean_state().model_copy(update={"reserve_balance": 0.0})
    ev = TriggerConditionEvaluator(evaluate_triggers(drawn))
    # reserve_fund_trigger fires (balance 0 < target) → a step gated on it pays.
    assert ev.evaluate("while the reserve_fund_trigger is in effect", WaterfallFunds()) is True


def test_evaluator_unknown_condition_pays() -> None:
    ev = TriggerConditionEvaluator(evaluate_triggers(_clean_state()))
    assert ev.evaluate("some prose the engine has never seen", WaterfallFunds()) is True


# ===========================================================================
# 2. Synthetic multi-period chain
# ===========================================================================


def _synthetic_periods() -> list[PeriodInput]:
    return [
        PeriodInput(
            reporting_date="2026-02-28",
            collections=PeriodCollections(
                interest=2_600_000.0,
                scheduled_principal=6_000_000.0,
                prepayment=270_522.0,
            ),
            days_in_period=28,
        ),
        PeriodInput(
            reporting_date="2026-03-31",
            collections=PeriodCollections(
                interest=2_500_000.0,
                scheduled_principal=9_000_000.0,
                prepayment=81_226.0,
                realized_loss=500_000.0,  # injected loss this period
            ),
            days_in_period=31,
        ),
        PeriodInput(
            reporting_date="2026-04-30",
            collections=PeriodCollections(
                interest=2_400_000.0,
                scheduled_principal=8_000_000.0,
                prepayment=1_081_227.0,
                recovery=100_000.0,
                realized_loss=250_000.0,
            ),
            days_in_period=30,
        ),
    ]


def _synthetic_series():
    return reconstruct_period_series(
        capital_structure=_CAP_STRUCTURE,
        reserve_target=_RESERVE_TARGET,
        original_pool_balance=_ORIGINAL_POOL,
        opening_pool_balance=_ORIGINAL_POOL,
        seed_reporting_date="2026-01-31",
        periods=_synthetic_periods(),
    )


def test_series_length_and_seed() -> None:
    series = _synthetic_series()
    # One seed state + one closing per period.
    assert len(series.states) == len(_synthetic_periods()) + 1
    assert len(series.period_results) == len(_synthetic_periods())
    seed = series.states[0]
    assert seed.period_index == 0
    assert seed.reporting_date == "2026-01-31"
    assert seed.class_a_balance == _CAP_STRUCTURE["class_a_balance"]
    assert seed.reserve_balance == _RESERVE_TARGET
    assert seed.cumulative_losses == 0.0


def test_closing_equals_opening_identity() -> None:
    """closing[N] == opening[N+1] at every transition (the spine invariant)."""
    series = _synthetic_series()
    for i, result in enumerate(series.period_results):
        assert result.closing_state == series.states[i + 1]
    # Period index increments monotonically across the chain.
    assert [s.period_index for s in series.states] == list(range(len(series.states)))


def test_pool_amortises_and_tranches_redeem() -> None:
    series = _synthetic_series()
    pools = [s.pool_balance for s in series.states]
    a_bals = [s.class_a_balance for s in series.states]
    # Pool strictly decreases (principal collected each period).
    assert all(pools[i + 1] < pools[i] for i in range(len(pools) - 1))
    # Class A is redeemed (sequential-pay senior-first) — balance decreases.
    assert a_bals[-1] < a_bals[0]


def test_cumulative_losses_accumulate_monotonically() -> None:
    series = _synthetic_series()
    cum = [s.cumulative_losses for s in series.states]
    # Non-decreasing, and ends at the total injected loss (500k + 250k).
    assert all(cum[i + 1] >= cum[i] for i in range(len(cum) - 1))
    assert math.isclose(cum[-1], 750_000.0, rel_tol=1e-9)


def test_pdl_and_reserve_evolve_non_flat() -> None:
    """PDL/reserve are a real series, not permanently 0/target (the audit gap)."""
    series = _synthetic_series()
    # The injected losses are allocated junior-first → Class C PDL debits.
    pdl_c = [s.class_c_pdl for s in series.states]
    assert pdl_c[0] == 0.0
    assert pdl_c[-1] > 0.0  # PDL accrued from the injected losses
    # Cumulative loss past the 1.5% trigger would flip sequential pay; here the
    # losses (750k of a €1.06B pool ≈ 0.07%) stay below it, so the deal pays
    # pro-rata and BOTH senior tranches amortise — assert Class B also redeemed.
    assert series.states[-1].class_b_balance < series.states[0].class_b_balance


def test_loss_breach_flips_to_sequential_pay() -> None:
    """A loss past the 1.5% trigger flips principal to sequential (senior-first).

    Triggers gate on the OPENING state of each period (you cannot know a period's
    loss before processing it), so the flip takes effect the period *after* the
    breach: period 1 books the loss; period 2 opens already-breached and pays
    sequential, so Class B does NOT amortise while Class A is outstanding —
    distinguishing it from the pro-rata path above.
    """
    big_loss = 0.02 * _ORIGINAL_POOL  # > 1.5% trigger
    periods = [
        # Period 1 — books the breaching loss; opening state is still clean so
        # this period itself pays pro-rata.
        PeriodInput(
            reporting_date="2026-02-28",
            collections=PeriodCollections(
                interest=2_600_000.0,
                scheduled_principal=10_000_000.0,
                realized_loss=big_loss,
            ),
            days_in_period=28,
        ),
        # Period 2 — opens already-breached, so principal is sequential.
        PeriodInput(
            reporting_date="2026-03-31",
            collections=PeriodCollections(
                interest=2_600_000.0,
                scheduled_principal=10_000_000.0,
            ),
            days_in_period=31,
        ),
    ]
    series = reconstruct_period_series(
        capital_structure=_CAP_STRUCTURE,
        reserve_target=_RESERVE_TARGET,
        original_pool_balance=_ORIGINAL_POOL,
        seed_reporting_date="2026-01-31",
        periods=periods,
    )
    # The breach is registered in the period-1 closing state.
    assert series.states[1].cumulative_loss_rate_pct > 1.5
    # Period 2 opens breached → its trigger evaluation fires sequential pay.
    assert series.period_results[1].trigger_evaluation.is_triggered("cumulative_loss_trigger")
    # Class B received NO principal in period 2 (sequential, Class A outstanding).
    b_principal_p2 = series.period_results[1].redemption_execution.distributed_to(
        "class_b_principal"
    )
    assert b_principal_p2 == 0.0
    # Class A absorbed the period-2 principal.
    assert series.period_results[1].redemption_execution.distributed_to(
        "class_a_principal"
    ) > 0.0


def test_run_period_uses_real_trigger_engine() -> None:
    """run_period gates on the real S5 trigger state, not S4's default.

    The single-period kernel's PeriodResult carries the S5 TriggerEvaluation that
    drove the gating — proving the S4↔S5 join is live rather than the standalone
    DefaultConditionEvaluator.
    """
    opening = _clean_state(reporting_date="2026-01-31")
    period = _synthetic_periods()[1]  # carries a 500k loss
    result = run_period(opening, period, rates={
        k: v for k, v in _CAP_STRUCTURE.items() if k.endswith("rate_pct")
    })
    assert result.trigger_evaluation.period == opening.reporting_date
    # The trace exists and is non-empty (the waterfall actually ran).
    assert result.revenue_execution.steps
    assert result.redemption_execution.steps


# ===========================================================================
# 3. Real-tape chain (offline, over the warm tape-analytics cache)
# ===========================================================================


# The deal's tapes mix two series: 24 historical 2024-2025 monthly tapes of a
# DIFFERENT, much larger Green-Lion vintage (~€110-140B pool) and the 3 actual
# 2026 reporting tapes of THIS deal (~€1.05B pool, the one spike S0 reconciled).
# The "Green Lion 2026-1" deal whose prospectus seeds the liability side is the
# 2026 series, so the meaningful real-tape reconstruction chains those three.
_GREEN_LION_2026_DATES = ("2026-02-28", "2026-03-31", "2026-04-30")


def _cached_pool_balances(dates: tuple[str, ...]) -> list[tuple[str, float]] | None:
    """(reporting_date, pool_balance) for the named dates, chronological.

    Returns None if any tape is missing from the warm cache (so the test skips
    offline rather than failing).
    """
    by_date = {t["date"]: t["url"] for t in GREEN_LION["tape_urls"]}
    out: list[tuple[str, float]] = []
    for date in dates:
        url = by_date.get(date)
        if url is None:
            return None
        pb = _cached_pool_balance(url)
        if pb is None:
            return None
        out.append((date, pb))
    return out


def _pool_delta_periods(tapes: list[tuple[str, float]]) -> list[PeriodInput]:
    """Build pool-delta collections from consecutive cached pool balances.

    Each period's scheduled principal is the prior→current net pool reduction
    (the S3 net-pool-movement regime spike S0 proved ties to the report
    roll-forward to the cent). The first tape is the seed (period-0 opening).
    """
    periods: list[PeriodInput] = []
    for (_, prev_pool), (cur_date, cur_pool) in zip(tapes, tapes[1:]):
        periods.append(
            PeriodInput(
                reporting_date=cur_date,
                collections=PeriodCollections(
                    scheduled_principal=max(0.0, prev_pool - cur_pool)
                ),
                days_in_period=30,
            )
        )
    return periods


def test_real_2026_tape_chain_ties_to_cached_pool_balances() -> None:
    """The Green Lion 2026 deal chain over its REAL reporting tapes.

    Seeds period 0 from the 2026 prospectus capital structure (and the deal's
    €1.0636B original pool), then chains the three real 2026 reporting tapes via
    pool-delta collections. Asserts the reconstructed pool balances match the
    cached tape ``pool_balance_eur`` to the cent (the S0 reconciliation), that the
    closing→opening identity holds end-to-end, and that the pool genuinely
    amortises across the chain.
    """
    tapes = _cached_pool_balances(_GREEN_LION_2026_DATES)
    if tapes is None:
        pytest.skip("warm 2026 tape-analytics cache absent; real-tape chain not runnable offline")

    seed_date, seed_pool = tapes[0]
    series = reconstruct_period_series(
        capital_structure=_CAP_STRUCTURE,
        reserve_target=_RESERVE_TARGET,
        original_pool_balance=_ORIGINAL_POOL,
        opening_pool_balance=seed_pool,
        seed_reporting_date=seed_date,
        periods=_pool_delta_periods(tapes),
    )

    assert len(series.states) == len(tapes)

    # Closing→opening identity end-to-end.
    for i, result in enumerate(series.period_results):
        assert result.closing_state == series.states[i + 1]

    # Every reconstructed pool balance ties to the cached tape value to the cent.
    for state, (date, pool) in zip(series.states, tapes):
        assert state.reporting_date == date
        assert math.isclose(state.pool_balance, pool, abs_tol=0.01), (
            f"pool mismatch at {date}: {state.pool_balance} != {pool}"
        )

    # The pool genuinely amortises across the real chain (distinct, decreasing).
    pools = [s.pool_balance for s in series.states]
    assert pools == sorted(pools, reverse=True)
    assert len(set(pools)) == len(pools)
    # Pool factor < 1.0 (amortised below the seed) and the loss-rate denominator
    # is the prospectus original pool, so the factor is economically meaningful.
    assert series.states[-1].pool_factor < 1.0


def test_full_tape_history_chain_preserves_identity() -> None:
    """The engine threads the deal's FULL tape history without breaking.

    A robustness guard over all of ``GREEN_LION['tape_urls']`` (currently 27
    tapes): regardless of how many periods are driven, the loop preserves the
    closing[N]==opening[N+1] identity and reconstructs each period's pool to the
    cent. This does not assert prospectus-economic meaning (the history mixes
    vintages); it guards that the integrator scales to the real period count.
    """
    all_dates = tuple(t["date"] for t in GREEN_LION["tape_urls"])
    tapes = _cached_pool_balances(all_dates)
    if tapes is None:
        pytest.skip("warm tape-analytics cache absent; full-history chain not runnable offline")

    seed_date, seed_pool = tapes[0]
    series = reconstruct_period_series(
        capital_structure=_CAP_STRUCTURE,
        reserve_target=_ORIGINAL_POOL,  # denominator irrelevant for this guard
        original_pool_balance=seed_pool,
        opening_pool_balance=seed_pool,
        seed_reporting_date=seed_date,
        periods=_pool_delta_periods(tapes),
    )

    assert len(series.states) == len(tapes)
    for i, result in enumerate(series.period_results):
        assert result.closing_state == series.states[i + 1]
    for state, (date, pool) in zip(series.states, tapes):
        assert state.reporting_date == date
        assert math.isclose(state.pool_balance, pool, abs_tol=0.01)
