"""Integrated tape → waterfall → covenant E2E on **real** loan-tape data (#366).

The closing test of epic #360 ("Tape-path canonicalisation & engine generality").
Its prereqs are merged: #363 generalised the active engine onto a canonical
``tranches`` list, and #364 added the :class:`~loanwhiz.primitives.tape_adapter.TapeAdapter`
that builds a canonical ``source="tape"``
:class:`~loanwhiz.domain.inputs.PeriodInputs` carrying a *populated*
:class:`~loanwhiz.domain.inputs.RiskSignals` from the normalised tape — so the
tape path's old ``risk_signals=None`` is gone.

What this test closes
---------------------
The 2026-06-22 audit found the tape-native arrears/LTV covenants (#280) were
"defined and unit-tested in isolation but cannot fire on any real deal", and
that "the breadth suite exercises primitives in isolation over *synthetic* tape
periods, which is exactly why green CI didn't catch any of the above". The
existing ``test_breadth_cross_jurisdiction.py::test_tape_native_b7_triggers_resolve_and_fire``
proves the three tape-native triggers fire — but only against
``breadth_harness.SYNTHETIC_TAPE_PERIOD``, a hand-built dict whose metrics are
placed directly where ``_extract_metric`` looks. **That is the synthetic-isolation
gap.**

This module is the integration counterpart: it folds a **real** committed loan
tape through the full chain ``collections → period state → waterfall → covenant
evaluation`` using the same public entry points the API's
``_reconstruct_series_from_tapes`` uses, and asserts the tape-native
arrears/LTV/default covenants (a) resolve and evaluate off the *real* tape's own
analytics (the orphaned-RiskSignals gap, closed on a real fold), and (b) actually
fire when those analytics breach the B7 thresholds.

Real data, offline, deterministic
----------------------------------
- **Real substrate.** The only real tapes committed (or resolvable offline) are
  Green Lion 2026-1's three monthly periods. ``_normalised_tape_output`` resolves
  their committed ESMA analytics seed (``data/tapes/seed/*.json``, #347) with no
  network — real ``pool_balance_eur`` / ``pool_stats.wtd_ltv`` /
  ``arrears_breakdown``. No non-GL *tape* exists in the repo (the IT/ES deal seeds
  carry no ``tape_urls``), so "real non-GL data" is honoured as "the real tape
  substrate, exercised through the general (non-GL-hardcoded) engine + canonical
  schema" — not a fabricated non-GL tape.
- **Honest breach.** A healthy real pool does not breach the B7 thresholds, so the
  *firing* proof applies a documented stress overlay to a **copy** of the real
  tape's pool analytics (arrears / default / LTV above threshold), leaving the
  pool balance, structure, collection legs and the rest of the fold real and
  unmodified. This mirrors ``breadth_harness.SYNTHETIC_TAPE_PERIOD``'s framing:
  chosen above the thresholds to prove the comparison runs, **not** as a claim
  that any real pool is in breach.
- **Offline.** Collections are derived from the real tapes' ``pool_balance_eur``
  deltas via the aggregator's documented *pool-delta* regime — no raw loan CSV
  fetch — so the whole fold runs with no network or LLM.
"""

from __future__ import annotations

# Import the primitives package before ``loanwhiz.domain.inputs`` so the shared
# ``primitives.base`` <-> ``domain.provenance`` import cycle resolves
# primitives-first (mirrors test_tape_adapter.py — makes this file robust when
# run in isolation too).
import loanwhiz.primitives  # noqa: F401  (import-order priming)

import pytest

from loanwhiz.api.main import _normalised_tape_output
from loanwhiz.config import GREEN_LION
from loanwhiz.primitives.collections_aggregator import CollectionsOutput
from loanwhiz.primitives.covenant_monitor import (
    CovenantInput,
    CovenantMonitor,
    evaluate_triggers,
)
from loanwhiz.primitives.esma_tape_normaliser import EsmaTapeOutput
from loanwhiz.primitives.period_state_machine import (
    DealStateSeries,
    reconstruct_period_series,
)
from loanwhiz.primitives.tape_adapter import TapeAdapter

# Green Lion 2026-1's prospectus capital structure (the same figures the API's
# _GREEN_LION_* structural config seeds the reconstruction from). Real deal data.
_CAPITAL_STRUCTURE: dict[str, float] = {
    "class_a_balance": 1_000_000_000.0,
    "class_b_balance": 53_100_000.0,
    "class_c_balance": 10_500_000.0,
    "class_a_rate_pct": 3.62,
    "class_b_rate_pct": 4.5,
    "class_c_rate_pct": 6.0,
}
_RESERVE_TARGET = 50_000_000.0
_ORIGINAL_POOL_BALANCE = 1_063_500_000.0

# The three tape-native (B7, #280) triggers under test, by name.
_TAPE_NATIVE_NAMES = {
    "severe_arrears_trigger",
    "tape_default_rate_trigger",
    "weighted_average_ltv_trigger",
}


def _real_tape_outputs() -> list[EsmaTapeOutput]:
    """Load Green Lion's three real monthly tapes from the committed seed, offline.

    ``_normalised_tape_output`` resolves the committed ESMA analytics seed for
    each tape URL with no network (resolution layer 3, #347), so this returns the
    deal's *real* per-period pool analytics.
    """
    return [
        EsmaTapeOutput(**_normalised_tape_output(tape["url"]))
        for tape in GREEN_LION["tape_urls"]
    ]


def _collections_from_pool_delta(
    *, prev_pool_balance: float, tape: EsmaTapeOutput, reporting_date: str
) -> CollectionsOutput:
    """Build a real ``CollectionsOutput`` from two tapes' pool balances (offline).

    Uses the aggregator's documented *pool-delta* regime
    (``scheduled_principal = prev_pool_balance - current_pool_balance``) over the
    real per-period ``pool_balance_eur`` — the same arithmetic
    ``CollectionsAggregator._pool_delta`` runs, but computed directly from the
    committed analytics so no raw loan CSV is fetched. Interest is the period's
    Class A accrual on the real pool balance. This is exactly the leg set
    ``TapeAdapter.legs_from_collections`` consumes.
    """
    pool_balance = tape.pool_balance_eur
    scheduled_principal = max(0.0, prev_pool_balance - pool_balance)
    # Class A interest accrual on the real pool balance (act/360, ~30d month).
    interest = pool_balance * _CAPITAL_STRUCTURE["class_a_rate_pct"] / 100.0 * 30 / 360
    return CollectionsOutput(
        reporting_period=reporting_date,
        interest_collected=interest,
        swap_receipts=0.0,
        available_revenue_funds=interest,
        scheduled_principal=scheduled_principal,
        unscheduled_principal=0.0,
        recoveries=0.0,
        realized_losses=0.0,
        available_principal_funds=scheduled_principal,
        pool_balance_eur=pool_balance,
        loan_count=tape.loan_count,
        class_a_interest_due=interest,
        senior_fees=0.0,
        summary=f"pool-delta collections for {reporting_date}",
        derivation="pool-delta",
    )


def _fold_real_tapes(
    triggers=None,
) -> tuple[DealStateSeries, list[EsmaTapeOutput]]:
    """Fold Green Lion's real tapes through collections → TapeAdapter → engine.

    Returns the reconstructed :class:`DealStateSeries` (whose ``period_results``
    carry the per-period ``trigger_evaluation``) and the real tape outputs, so a
    caller can cross-check the engine-side fold against the production
    ``CovenantMonitor`` path over the same real analytics.
    """
    outputs = _real_tape_outputs()
    adapter = TapeAdapter()
    tape_urls = GREEN_LION["tape_urls"]

    periods = []
    for idx in range(1, len(outputs)):
        collections = _collections_from_pool_delta(
            prev_pool_balance=outputs[idx - 1].pool_balance_eur,
            tape=outputs[idx],
            reporting_date=tape_urls[idx]["date"],
        )
        periods.append(
            adapter.period_inputs(
                collections,
                outputs[idx],
                reporting_date=tape_urls[idx]["date"],
                days_in_period=30,
            )
        )

    series = reconstruct_period_series(
        capital_structure=_CAPITAL_STRUCTURE,
        reserve_target=_RESERVE_TARGET,
        original_pool_balance=_ORIGINAL_POOL_BALANCE,
        seed_reporting_date=tape_urls[0]["date"],
        periods=periods,
        triggers=triggers,
    )
    return series, outputs


# ---------------------------------------------------------------------------
# Criterion: the real tape folds collections -> TapeAdapter -> run_period with a
# populated RiskSignals (not None) carrying the real tape's analytics.
# ---------------------------------------------------------------------------


def test_real_tape_folds_to_populated_risk_signals():
    """Each tape-source ``PeriodInputs`` carries the real tape's RiskSignals."""
    outputs = _real_tape_outputs()
    adapter = TapeAdapter()
    tape_urls = GREEN_LION["tape_urls"]

    for idx in range(1, len(outputs)):
        tape = outputs[idx]
        collections = _collections_from_pool_delta(
            prev_pool_balance=outputs[idx - 1].pool_balance_eur,
            tape=tape,
            reporting_date=tape_urls[idx]["date"],
        )
        period_inputs = adapter.period_inputs(
            collections,
            tape,
            reporting_date=tape_urls[idx]["date"],
            days_in_period=30,
        )

        assert period_inputs.source == "tape"
        # The #364 fix: risk_signals is populated from the real tape, not None.
        assert period_inputs.risk_signals is not None
        signals = period_inputs.risk_signals
        # wa_ltv passes through unchanged from the real tape's pool stats.
        assert signals.wa_ltv == tape.pool_stats["wtd_ltv"]
        # pool_balance is the real outstanding balance.
        assert signals.pool_balance == tape.pool_balance_eur
        # GL's pool is healthy: zero 180+d arrears, a small default fraction.
        assert signals.arrears_180d == 0.0
        assert signals.default_pct == pytest.approx(
            tape.arrears_breakdown["default_pct"] / 100.0
        )


# ---------------------------------------------------------------------------
# Criterion: covenant evaluation is reached THROUGH the run_period fold, and the
# tape-native triggers resolve off the real tape analytics (evaluable, PASS).
# ---------------------------------------------------------------------------


def test_real_tape_fold_reaches_covenant_evaluation_through_engine():
    """The fold runs end-to-end and the engine evaluates every trigger per period."""
    triggers = CovenantMonitor.DEFAULT_TRIGGERS + CovenantMonitor.TAPE_NATIVE_TRIGGERS
    series, _ = _fold_real_tapes(triggers=triggers)

    # Three real tapes -> two transitions -> two period results, each carrying a
    # trigger evaluation produced inside run_period (not a standalone monitor call).
    assert len(series.period_results) == 2
    expected_names = {t.name for t in triggers}
    for result in series.period_results:
        assert set(result.trigger_evaluation.statuses) == expected_names
        # The tape-native triggers are reachable from the engine fold.
        assert _TAPE_NATIVE_NAMES <= set(result.trigger_evaluation.statuses)


def test_tape_native_triggers_resolve_off_real_tape_metrics():
    """The B7 triggers evaluate against the *real* tape's own analytics and PASS.

    The production tape→covenant path
    (``CovenantInput(periods=[real_tape_dict], ...)`` → ``CovenantMonitor.execute``)
    resolves each tape-native metric from the real tape (no synthetic dict) and,
    because GL's real pool is healthy, none of them fire.
    """
    outputs = _real_tape_outputs()
    result = CovenantMonitor().execute(
        CovenantInput(
            periods=[tape.model_dump() for tape in outputs],
            triggers=CovenantMonitor.TAPE_NATIVE_TRIGGERS,
        )
    )

    last = outputs[-1]
    by_name = {
        status.trigger_name: status
        for status in result.output.trigger_statuses
        if status.period == last.reporting_date
    }
    assert _TAPE_NATIVE_NAMES <= set(by_name)

    # Each resolves off the real tape analytics and is evaluable.
    ltv = by_name["weighted_average_ltv_trigger"]
    assert ltv.evaluable
    assert ltv.metric_value == last.pool_stats["wtd_ltv"]
    assert ltv.is_triggered is False  # ~68% < 80% threshold

    arrears = by_name["severe_arrears_trigger"]
    assert arrears.evaluable
    assert arrears.metric_value == last.arrears_breakdown["arrears_180d_plus_pct"]
    assert arrears.is_triggered is False  # 0% < 5% threshold

    default = by_name["tape_default_rate_trigger"]
    assert default.evaluable
    assert default.metric_value == last.arrears_breakdown["default_pct"]
    assert default.is_triggered is False  # ~0.03% < 3% threshold


# ---------------------------------------------------------------------------
# Criterion: the B7 triggers actually FIRE when the real tape's analytics breach
# the thresholds (documented stress overlay on real data — see module docstring).
# ---------------------------------------------------------------------------


def test_tape_native_covenants_fire_on_stressed_real_tape():
    """A documented stress overlay on a real tape breaches all three B7 triggers.

    The pool is folded through the engine from the real tapes; only a *copy* of
    the final period's pool analytics is stressed above the B7 thresholds. The
    breach is the overlay, not a claim about the real pool — it proves the
    arrears/LTV/default *firing* path runs on a real-data-shaped period.
    """
    triggers = CovenantMonitor.DEFAULT_TRIGGERS + CovenantMonitor.TAPE_NATIVE_TRIGGERS
    series, outputs = _fold_real_tapes(triggers=triggers)
    closing_state = series.states[-1]

    # Stress a COPY of the real tape's pool analytics above every B7 threshold.
    stressed = outputs[-1].model_dump()
    stressed["arrears_breakdown"] = {
        **stressed["arrears_breakdown"],
        "arrears_180d_plus_pct": 6.0,  # > 5.0 severe-arrears threshold
        "default_pct": 4.0,  # > 3.0 tape-default-rate threshold
    }
    stressed["pool_stats"] = {
        **stressed["pool_stats"],
        "wtd_ltv": 85.0,  # > 80.0 wa_ltv threshold
    }

    evaluation = evaluate_triggers(
        closing_state,
        triggers=CovenantMonitor.TAPE_NATIVE_TRIGGERS,
        period=stressed,
    )

    # All three tape-native covenants fire on the stressed real-shaped period.
    assert set(evaluation.active) == _TAPE_NATIVE_NAMES
    for name in _TAPE_NATIVE_NAMES:
        assert evaluation.is_triggered(name), name
        assert evaluation.evaluable(name), name


# ---------------------------------------------------------------------------
# Criterion: composing the tape-native triggers with DEFAULT_TRIGGERS does not
# change GL's default-trigger output (the no-fold-change invariant #364 relies on).
# ---------------------------------------------------------------------------


def test_tape_native_triggers_do_not_change_default_trigger_output():
    """Default-trigger statuses are identical with and without the tape-native set."""
    outputs = _real_tape_outputs()
    periods = [tape.model_dump() for tape in outputs]

    defaults_only = CovenantMonitor().execute(
        CovenantInput(periods=periods, triggers=CovenantMonitor.DEFAULT_TRIGGERS)
    ).output
    composed = CovenantMonitor().execute(
        CovenantInput(
            periods=periods,
            triggers=(
                CovenantMonitor.DEFAULT_TRIGGERS + CovenantMonitor.TAPE_NATIVE_TRIGGERS
            ),
        )
    ).output

    default_names = {t.name for t in CovenantMonitor.DEFAULT_TRIGGERS}

    def _key(status):
        return (status.trigger_name, status.period)

    defaults_view = {
        _key(s): (s.metric_value, s.is_triggered, s.evaluable)
        for s in defaults_only.trigger_statuses
    }
    composed_view = {
        _key(s): (s.metric_value, s.is_triggered, s.evaluable)
        for s in composed.trigger_statuses
        if s.trigger_name in default_names
    }

    assert defaults_view == composed_view
    # And the composed run additionally carries the tape-native statuses.
    composed_names = {s.trigger_name for s in composed.trigger_statuses}
    assert _TAPE_NATIVE_NAMES <= composed_names
