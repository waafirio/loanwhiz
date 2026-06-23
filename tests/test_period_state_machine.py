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


# Green Lion 2026-1 reports exactly these 3 monthly tapes (~€1.05B pool, the
# ones spike S0 reconciled). NOTE: the separate green-lion-2024-2025 dataset is a
# DIFFERENT, much larger vintage (~€110-140B pool) and is no longer chained into
# this deal's config; this explicit date tuple keeps the test robust regardless.
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

    A robustness guard over all of ``GREEN_LION['tape_urls']`` (currently the 3
    2026 tapes): regardless of how many periods are driven, the loop preserves the
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


# ===========================================================================
# 4. Canonical PeriodInputs generalisation (#265)
# ===========================================================================
#
# run_period is generalised to consume the canonical, adapter-agnostic
# domain.PeriodInputs (step_overrides / step_sources) in addition to the legacy
# tape-only PeriodInput. The headline guard: a tape-source PeriodInputs with
# legs present and empty overrides produces a DealStateSeries byte-for-byte
# identical to the legacy PeriodInput path (GL-2026-1 regression lock).

from loanwhiz.domain import PeriodInputs  # noqa: E402
from loanwhiz.domain.inputs import CollectionLegs  # noqa: E402
from loanwhiz.primitives.waterfall_interpreter import StepSpec  # noqa: E402


def _legacy_to_canonical_tape(period: PeriodInput) -> PeriodInputs:
    """Map a legacy tape PeriodInput → an equivalent tape-source PeriodInputs.

    Legs carry the full per-leg breakdown (tape path); the aggregate available
    funds are derived from the legs exactly as the kernel does today. Empty
    step_overrides → the engine must behave identically to the legacy path.
    """
    c = period.collections
    return PeriodInputs(
        reporting_date=period.reporting_date,
        days_in_period=period.days_in_period,
        available_revenue=c.interest,
        available_principal=c.scheduled_principal + c.prepayment + c.recovery,
        realized_loss=c.realized_loss,
        legs=CollectionLegs(
            interest=c.interest,
            scheduled_principal=c.scheduled_principal,
            prepayment=c.prepayment,
            recovery=c.recovery,
            realized_loss=c.realized_loss,
        ),
        source="tape",
    )


def test_gl2026_tape_path_byte_for_byte_unchanged() -> None:
    """REGRESSION LOCK: empty-overrides tape PeriodInputs == legacy PeriodInput.

    The spec's headline guard for migration step 1 — generalising run_period must
    not perturb the GL-2026-1 tape path. We thread the same synthetic GL-2026-1
    chain through (a) the legacy PeriodInput path and (b) a canonical, tape-source
    PeriodInputs (legs present, empty step_overrides), and assert the resulting
    DealStateSeries is byte-for-byte identical (states + per-period executions +
    trigger evaluations).
    """
    legacy_periods = _synthetic_periods()
    canonical_periods = [_legacy_to_canonical_tape(p) for p in legacy_periods]

    common = dict(
        capital_structure=_CAP_STRUCTURE,
        reserve_target=_RESERVE_TARGET,
        original_pool_balance=_ORIGINAL_POOL,
        opening_pool_balance=_ORIGINAL_POOL,
        seed_reporting_date="2026-01-31",
    )
    legacy_series = reconstruct_period_series(periods=legacy_periods, **common)
    canonical_series = reconstruct_period_series(periods=canonical_periods, **common)

    # Byte-for-byte: serialise both series and compare. This covers the full
    # state chain, every waterfall execution trace, and every trigger evaluation.
    assert legacy_series.model_dump_json() == canonical_series.model_dump_json()


def test_run_period_canonical_tape_equals_legacy_single_period() -> None:
    """A single run_period over a tape PeriodInputs == over the legacy PeriodInput."""
    opening = _clean_state(reporting_date="2026-01-31")
    legacy = _synthetic_periods()[1]  # carries a 500k loss
    canonical = _legacy_to_canonical_tape(legacy)
    rates = {k: v for k, v in _CAP_STRUCTURE.items() if k.endswith("rate_pct")}

    legacy_result = run_period(opening, legacy, rates=rates)
    canonical_result = run_period(opening, canonical, rates=rates)
    assert legacy_result.model_dump_json() == canonical_result.model_dump_json()


def test_run_period_report_step_override_is_distributed() -> None:
    """A report-supplied step_override amount is distributed for its step.

    On the report path the engine has no formula for some lines (fees, swaps), so
    PeriodInputs.step_overrides carries the reported amount keyed by priority
    label. run_period must translate that (priority -> recipient) and feed it to
    the interpreter so the step distributes the reported figure.
    """
    opening = _clean_state(reporting_date="2026-01-31")
    rates = {k: v for k, v in _CAP_STRUCTURE.items() if k.endswith("rate_pct")}

    # A minimal revenue waterfall: a report-supplied senior-fees step (no engine
    # formula need to match) followed by an engine-computed Class A interest step.
    revenue_steps = [
        StepSpec(priority="(a)", recipient="senior_fees"),
        StepSpec(priority="(b)", recipient="class_a_interest"),
    ]
    reported_fee = 123_456.0
    period = PeriodInputs(
        reporting_date="2026-02-28",
        days_in_period=28,
        available_revenue=5_000_000.0,
        available_principal=0.0,
        realized_loss=0.0,
        step_overrides={"(a)": reported_fee},
        step_sources={"(a)": "reported", "(b)": "engine"},
        source="report",
    )
    result = run_period(
        opening,
        period,
        rates=rates,
        revenue_steps=revenue_steps,
        redemption_steps=[],
    )
    # The report-supplied fee was distributed exactly as reported (not 0, not the
    # engine's senior_fees=0 default).
    assert math.isclose(
        result.revenue_execution.distributed_to("senior_fees"), reported_fee, abs_tol=0.01
    )
    # The engine-computed Class A interest step still ran (non-zero need).
    assert result.revenue_execution.distributed_to("class_a_interest") > 0.0


def test_run_period_report_clears_extracted_condition() -> None:
    """A report-sourced step's extracted condition is NOT re-applied.

    The report is the post-resolution actual: re-gating a step the report already
    paid would double-count. So a step carrying a condition that would normally
    suppress it (sequential-pay inactive on a clean state) must still pay on the
    report path, because run_period clears the condition for report-sourced steps.
    """
    opening = _clean_state(reporting_date="2026-01-31")  # clean → seq-pay inactive
    rates = {k: v for k, v in _CAP_STRUCTURE.items() if k.endswith("rate_pct")}

    # This step would be GATED on the tape path: "if Sequential Pay Trigger is in
    # effect" is False on a clean state, so the interpreter would suppress it.
    gated_step = StepSpec(
        priority="(a)",
        recipient="class_b_principal",
        condition="if the Sequential Pay Trigger is in effect",
    )
    reported_amt = 250_000.0
    period = PeriodInputs(
        reporting_date="2026-02-28",
        days_in_period=28,
        available_revenue=0.0,
        available_principal=1_000_000.0,
        realized_loss=0.0,
        step_overrides={"(a)": reported_amt},
        step_sources={"(a)": "reported"},
        source="report",
    )
    result = run_period(
        opening,
        period,
        rates=rates,
        revenue_steps=[],
        redemption_steps=[gated_step],
        principal_classes=(),  # no computed sequential/pro-rata alloc — use override
    )
    step = result.redemption_execution.steps[0]
    # The condition was cleared → the step is NOT gated, and pays the reported
    # amount.
    assert step.gated is False
    assert math.isclose(step.amount_distributed, reported_amt, abs_tol=0.01)


def test_run_period_tape_path_keeps_condition_gating() -> None:
    """A tape-path PeriodInputs (source='tape') KEEPS live condition gating.

    The condition-clearing is report-only. On the tape path a sequential-pay
    condition must still gate against the live trigger state — proving the
    clearing doesn't leak into the tape path.
    """
    opening = _clean_state(reporting_date="2026-01-31")  # clean → seq-pay inactive
    rates = {k: v for k, v in _CAP_STRUCTURE.items() if k.endswith("rate_pct")}

    gated_step = StepSpec(
        priority="(a)",
        recipient="class_b_principal",
        condition="if the Sequential Pay Trigger is in effect",
    )
    period = PeriodInputs(
        reporting_date="2026-02-28",
        days_in_period=28,
        available_revenue=0.0,
        available_principal=1_000_000.0,
        realized_loss=0.0,
        legs=CollectionLegs(
            interest=0.0,
            scheduled_principal=1_000_000.0,
            prepayment=0.0,
            recovery=0.0,
            realized_loss=0.0,
        ),
        source="tape",  # tape path → conditions preserved
    )
    result = run_period(
        opening,
        period,
        rates=rates,
        revenue_steps=[],
        redemption_steps=[gated_step],
        principal_classes=("class_b",),
    )
    step = result.redemption_execution.steps[0]
    # Clean state → sequential-pay inactive → the "in effect" condition is False →
    # the step IS gated (suppressed) on the tape path.
    assert step.gated is True
    assert step.amount_distributed == 0.0


# ===========================================================================
# Engine generality: run_period over a non-A/B/C tranche structure (#363)
# ===========================================================================


def test_run_period_drives_non_abc_tranche_structure_end_to_end() -> None:
    """The engine runs a 4-tranche, non-A/B/C-named structure through run_period.

    Proves the active engine is no longer hardcoded to three Class A/B/C fields:
    the opening state carries a custom tranche set, the interpreter computes
    per-tranche interest + principal by name, and the closing state's tranches
    amortise — none of which is possible with the old scalar triplet.
    """
    from loanwhiz.domain.state import TrancheState

    opening = DealState(
        reporting_date="2026-01-31",
        tranches=[
            TrancheState(name="senior", balance=8_000_000.0, pdl_balance=0.0),
            TrancheState(name="mezz_1", balance=1_000_000.0, pdl_balance=0.0),
            TrancheState(name="mezz_2", balance=600_000.0, pdl_balance=0.0),
            TrancheState(name="junior", balance=400_000.0, pdl_balance=0.0),
        ],
        reserve_balance=200_000.0,
        reserve_target=200_000.0,
        cumulative_losses=0.0,
        pool_balance=10_000_000.0,
        pool_factor=1.0,
        original_pool_balance=10_000_000.0,
    )

    rates = {
        "senior_rate_pct": 3.0,
        "mezz_1_rate_pct": 5.0,
        "mezz_2_rate_pct": 6.0,
        "junior_rate_pct": 8.0,
    }
    # Interest steps for every tranche + principal steps for the two senior-most.
    revenue_steps = [
        StepSpec(priority="(a)", recipient="senior_interest"),
        StepSpec(priority="(b)", recipient="mezz_1_interest"),
        StepSpec(priority="(c)", recipient="mezz_2_interest"),
        StepSpec(priority="(d)", recipient="junior_interest"),
    ]
    redemption_steps = [
        StepSpec(priority="(a)", recipient="senior_principal"),
        StepSpec(priority="(b)", recipient="mezz_1_principal"),
    ]

    # The interpreter's need-registry only knows the canonical class_* interest
    # recipients, so the custom-named interest steps are recorded not_evaluable
    # (need 0) — that is the deal-agnostic degradation, and it does not crash.
    period = PeriodInput(
        reporting_date="2026-02-28",
        collections=PeriodCollections(
            interest=300_000.0,
            scheduled_principal=500_000.0,
            prepayment=0.0,
            recovery=0.0,
            realized_loss=0.0,
        ),
        days_in_period=28,
    )

    result = run_period(
        opening,
        period,
        rates=rates,
        revenue_steps=revenue_steps,
        redemption_steps=redemption_steps,
        principal_classes=("senior", "mezz_1"),
    )

    closing = result.closing_state
    by = {t.name: t for t in closing.tranches}
    # The custom tranche set is preserved end-to-end (no A/B/C coercion lost it).
    assert set(by) == {"senior", "mezz_1", "mezz_2", "junior"}
    # Clean opening state (loss rate 0% < 1.5%) → the sequential-pay trigger is
    # NOT in effect → principal is split PRO-RATA across the two principal-eligible
    # tranches by outstanding balance: senior 8M / mezz_1 1M of the 9M total, so
    # €500k splits 8/9 : 1/9. This per-name allocation is exactly what the old
    # hardcoded-A/B/C engine could not express.
    assert result.trigger_evaluation.is_triggered("cumulative_loss_trigger") is False
    assert by["senior"].balance == pytest.approx(8_000_000.0 - 500_000.0 * 8 / 9)
    assert by["mezz_1"].balance == pytest.approx(1_000_000.0 - 500_000.0 * 1 / 9)
    # The two tranches with no principal step are untouched.
    assert by["mezz_2"].balance == pytest.approx(600_000.0)
    assert by["junior"].balance == pytest.approx(400_000.0)


def test_run_period_non_abc_funds_view_is_name_keyed() -> None:
    """``_funds_from_state`` exposes every tranche by name (no A/B/C hardcode)."""
    from loanwhiz.domain.state import TrancheState
    from loanwhiz.primitives.period_state_machine import _funds_from_state

    state = DealState(
        reporting_date="2026-01-31",
        tranches=[
            TrancheState(name="senior", balance=8_000_000.0, pdl_balance=0.0),
            TrancheState(name="junior", balance=2_000_000.0, pdl_balance=500.0),
        ],
        reserve_balance=0.0,
        reserve_target=0.0,
        cumulative_losses=0.0,
        pool_balance=10_000_000.0,
        original_pool_balance=10_000_000.0,
    )
    funds = _funds_from_state(
        state,
        PeriodCollections(interest=1.0, scheduled_principal=2.0),
        rates={"senior_rate_pct": 3.0, "junior_rate_pct": 8.0},
        days_in_period=30,
        senior_fees=0.0,
    )
    assert {t.name for t in funds.tranches} == {"senior", "junior"}
    senior = funds.tranche("senior")
    junior = funds.tranche("junior")
    assert senior is not None and senior.balance == 8_000_000.0 and senior.rate_pct == 3.0
    assert junior is not None and junior.pdl_balance == 500.0
