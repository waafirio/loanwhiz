"""Tests for the ``ScenarioGenerator`` synthetic forward-input adapter (#275).

Covers the C5 fix (a single, consistent CDR↔SMM decomposition), determinism,
the canonical ``PeriodInputs`` shape it emits, and that the stream folds through
the *same* ``run_period`` kernel the history path uses.
"""

from __future__ import annotations

import pytest

# Import a ``loanwhiz.primitives`` module first so the package ``__init__`` runs
# and warms the primitives↔domain import cycle before ``loanwhiz.domain`` is
# imported directly. (Importing ``loanwhiz.domain.inputs`` as the very first
# loanwhiz import trips a pre-existing circular import via
# ``primitives.capability_matrix → reconciler → period_state_machine``; the full
# suite avoids it because an alphabetically-earlier module imports primitives
# first. This explicit ordering keeps the module collectable in isolation.)
from loanwhiz.primitives.scenario_generator import (
    ScenarioAssumptions,
    ScenarioGenerator,
    _annual_to_monthly_survival,
)
from loanwhiz.domain.inputs import PeriodInputs
from loanwhiz.primitives.deal_state import DealState
from loanwhiz.primitives.period_state_machine import run_period


def _seed(pool: float = 1_000_000_000.0) -> DealState:
    """A period-0 opening state with a known pool balance."""
    return DealState.seed_from_prospectus(
        {
            "class_a_balance": 900_000_000.0,
            "class_b_balance": 80_000_000.0,
            "class_c_balance": 20_000_000.0,
        },
        reserve_target=10_000_000.0,
        original_pool_balance=pool,
        opening_pool_balance=pool,
        reporting_date="2026-03-31",
    )


# ---------------------------------------------------------------------------
# The C5 fix — one consistent annual→monthly decomposition for BOTH rates
# ---------------------------------------------------------------------------


def test_survival_decomposition_compounds_back_to_annual():
    """The per-period rate compounds over 12 periods back to the annual rate.

    This is the survival-convention property: ``(1 - m)^12 == 1 - annual``.
    """
    for annual in (0.03, 0.15, 0.5):
        monthly = _annual_to_monthly_survival(annual)
        assert (1.0 - monthly) ** 12 == pytest.approx(1.0 - annual)


def test_survival_decomposition_edges():
    """0% annual → 0% monthly; 100% annual → 100% monthly (no NaN/clamp issues)."""
    assert _annual_to_monthly_survival(0.0) == 0.0
    assert _annual_to_monthly_survival(1.0) == 1.0


def test_cdr_and_cpr_use_the_same_decomposition():
    """C5: equal annual CDR and CPR must yield equal monthly decrements.

    The legacy projector decomposed CDR linearly (``/12``) and CPR geometrically,
    so equal annual rates produced *different* monthly fractions. The generator
    uses one shared survival helper, so equal annual rates produce identical
    monthly default and prepayment fractions of the same surviving balance.
    """
    gen = ScenarioGenerator()
    # Equal annual CDR and CPR, zero recovery so the gross default == realized
    # loss, and no scheduled amortisation so the only decrements are SMM/MDR.
    assumptions = ScenarioAssumptions(
        name="equal",
        cpr_pct=10.0,
        cdr_pct=10.0,
        recovery_pct=0.0,
        scheduled_amort_rate=0.0,
    )
    periods = gen.generate(_seed(), assumptions=assumptions, rate_pct=3.0, months=1)
    p = periods[0]
    # prepayment == defaulted balance (== realized_loss at 0% recovery) because
    # both decompose the same annual rate with the same convention.
    assert p.available_principal == pytest.approx(p.realized_loss)


def test_legacy_inconsistency_would_differ():
    """Guard: a linear CDR vs geometric CPR decomposition is NOT what we emit.

    Documents the bug being fixed: linear ``annual/12`` differs from the survival
    decomposition, so had the generator kept the legacy split, equal annual rates
    would NOT produce equal monthly decrements.
    """
    annual = 0.10
    linear_monthly = annual / 12.0
    survival_monthly = _annual_to_monthly_survival(annual)
    assert linear_monthly != pytest.approx(survival_monthly)


# ---------------------------------------------------------------------------
# Shape + determinism
# ---------------------------------------------------------------------------


def test_generate_emits_one_scenario_input_per_month():
    gen = ScenarioGenerator()
    periods = gen.generate(
        _seed(),
        assumptions=ScenarioAssumptions(name="base"),
        rate_pct=3.62,
        months=6,
    )
    assert len(periods) == 6
    for p in periods:
        assert isinstance(p, PeriodInputs)
        assert p.source == "scenario"
        # A projection computes every line itself — no report overrides.
        assert p.step_overrides == {}
        assert p.legs is not None


def test_generate_zero_months_is_empty():
    gen = ScenarioGenerator()
    assert gen.generate(_seed(), assumptions=ScenarioAssumptions(name="b"), rate_pct=3.0, months=0) == []


def test_generate_is_deterministic():
    gen = ScenarioGenerator()
    a = gen.generate(_seed(), assumptions=ScenarioAssumptions(name="b"), rate_pct=3.0, months=4)
    b = gen.generate(_seed(), assumptions=ScenarioAssumptions(name="b"), rate_pct=3.0, months=4)
    assert [p.model_dump() for p in a] == [p.model_dump() for p in b]


def test_pool_amortises_each_period():
    """Available principal is positive and the implied pool rolls down."""
    gen = ScenarioGenerator()
    periods = gen.generate(
        _seed(1_000_000.0),
        assumptions=ScenarioAssumptions(name="base", cdr_pct=1.0, cpr_pct=10.0),
        rate_pct=3.0,
        months=12,
    )
    # Principal returned shrinks as the pool shrinks (monotone non-increasing).
    principals = [p.available_principal for p in periods]
    assert all(earlier >= later for earlier, later in zip(principals, principals[1:]))
    assert principals[0] > 0.0


def test_recovery_splits_default_into_loss_and_recovered_principal():
    """realized_loss = default × (1 - recovery); recovery joins principal funds."""
    gen = ScenarioGenerator()
    # No prepayment, no scheduled amort — isolate the default/recovery split.
    assumptions = ScenarioAssumptions(
        name="def-only",
        cpr_pct=0.0,
        cdr_pct=20.0,
        recovery_pct=40.0,
        scheduled_amort_rate=0.0,
    )
    p = gen.generate(_seed(1_000_000.0), assumptions=assumptions, rate_pct=0.0, months=1)[0]
    monthly_mdr = _annual_to_monthly_survival(0.20)
    default_balance = 1_000_000.0 * monthly_mdr
    assert p.realized_loss == pytest.approx(default_balance * 0.6)
    # available_principal is the recovered portion only (no scheduled/prepay).
    assert p.available_principal == pytest.approx(default_balance * 0.4)


def test_rate_shift_increases_projected_interest():
    gen = ScenarioGenerator()
    base = gen.generate(_seed(), assumptions=ScenarioAssumptions(name="b", rate_shift_bps=0.0), rate_pct=3.0, months=1)[0]
    shifted = gen.generate(_seed(), assumptions=ScenarioAssumptions(name="s", rate_shift_bps=100.0), rate_pct=3.0, months=1)[0]
    assert shifted.available_revenue > base.available_revenue


# ---------------------------------------------------------------------------
# Integration — the stream folds through the SAME engine the history path uses
# ---------------------------------------------------------------------------


def test_scenario_stream_folds_through_run_period():
    """The generated PeriodInputs are accepted by run_period and advance state."""
    gen = ScenarioGenerator()
    seed = _seed(1_000_000_000.0)
    periods = gen.generate(
        seed,
        assumptions=ScenarioAssumptions(name="base", cpr_pct=15.0, cdr_pct=2.0),
        rate_pct=3.62,
        months=12,
    )
    rates = {"class_a_rate_pct": 3.62}
    state = seed
    states = [seed]
    for period in periods:
        result = run_period(state, period, rates=rates)
        state = result.closing_state
        states.append(state)
    # 12 transitions → 13 states; Class A amortises (non-increasing), losses grow.
    assert len(states) == 13
    class_a = [s.class_a_balance for s in states]
    assert all(earlier >= later for earlier, later in zip(class_a, class_a[1:]))
    assert states[-1].cumulative_losses > 0.0


def test_stress_is_worse_than_base_for_class_a():
    """Higher CDR + rate shift → more losses and a worse Class A outcome."""
    gen = ScenarioGenerator()
    seed = _seed(1_000_000_000.0)
    rates = {"class_a_rate_pct": 3.62}

    def _run(assumptions: ScenarioAssumptions) -> DealState:
        state = seed
        for period in gen.generate(seed, assumptions=assumptions, rate_pct=3.62, months=12):
            state = run_period(state, period, rates=rates).closing_state
        return state

    base = _run(ScenarioAssumptions(name="base", cpr_pct=15.0, cdr_pct=2.0))
    stress = _run(
        ScenarioAssumptions(name="stress", cpr_pct=15.0, cdr_pct=8.0, rate_shift_bps=100.0)
    )
    assert stress.cumulative_losses > base.cumulative_losses


# ---------------------------------------------------------------------------
# Loan-level scheduled-amortisation schedule (#281)
# ---------------------------------------------------------------------------


def test_none_schedule_is_byte_identical_to_the_proxy():
    """Omitting the schedule (or passing None) reproduces the flat-proxy output."""
    gen = ScenarioGenerator()
    seed = _seed()
    assumptions = ScenarioAssumptions(name="base")
    default = gen.generate(seed, assumptions=assumptions, rate_pct=3.62, months=6)
    explicit_none = gen.generate(
        seed, assumptions=assumptions, rate_pct=3.62, months=6, scheduled_principal_schedule=None
    )
    assert [p.model_dump() for p in default] == [p.model_dump() for p in explicit_none]


def test_supplied_schedule_drives_scheduled_principal():
    """Period k's scheduled-principal leg equals schedule[k] (capped at the balance)."""
    gen = ScenarioGenerator()
    seed = _seed(1_000_000_000.0)
    # A zero-prepay, zero-default scenario isolates the scheduled-principal leg.
    assumptions = ScenarioAssumptions(name="flat", cpr_pct=0.0, cdr_pct=0.0)
    schedule = [5_000_000.0, 6_000_000.0, 7_000_000.0]
    periods = gen.generate(
        seed,
        assumptions=assumptions,
        rate_pct=3.62,
        months=3,
        scheduled_principal_schedule=schedule,
    )
    got = [p.legs.scheduled_principal for p in periods]
    assert got == pytest.approx(schedule)


def test_schedule_shorter_than_months_zero_pads():
    """A schedule shorter than the horizon contributes zero scheduled principal late."""
    gen = ScenarioGenerator()
    seed = _seed(1_000_000_000.0)
    assumptions = ScenarioAssumptions(name="flat", cpr_pct=0.0, cdr_pct=0.0)
    periods = gen.generate(
        seed,
        assumptions=assumptions,
        rate_pct=3.62,
        months=3,
        scheduled_principal_schedule=[10_000_000.0],
    )
    sched = [p.legs.scheduled_principal for p in periods]
    assert sched[0] == pytest.approx(10_000_000.0)
    assert sched[1:] == pytest.approx([0.0, 0.0])


def test_schedule_capped_at_opening_balance():
    """A schedule entry above the opening pool balance is capped, never negative."""
    gen = ScenarioGenerator()
    seed = _seed(1_000_000.0)
    assumptions = ScenarioAssumptions(name="flat", cpr_pct=0.0, cdr_pct=0.0)
    periods = gen.generate(
        seed,
        assumptions=assumptions,
        rate_pct=3.62,
        months=1,
        scheduled_principal_schedule=[10_000_000.0],  # > pool balance
    )
    assert periods[0].legs.scheduled_principal == pytest.approx(1_000_000.0)


def test_schedule_folds_through_run_period_and_amortises():
    """A loan-level schedule still folds through the unchanged kernel; Class A amortises."""
    gen = ScenarioGenerator()
    seed = _seed(1_000_000_000.0)
    rates = {"class_a_rate_pct": 3.62}
    schedule = [20_000_000.0] * 12
    state = seed
    states = [seed]
    for period in gen.generate(
        seed,
        assumptions=ScenarioAssumptions(name="base"),
        rate_pct=3.62,
        months=12,
        scheduled_principal_schedule=schedule,
    ):
        state = run_period(state, period, rates=rates).closing_state
        states.append(state)
    assert len(states) == 13
    class_a = [s.class_a_balance for s in states]
    assert all(earlier >= later for earlier, later in zip(class_a, class_a[1:]))
