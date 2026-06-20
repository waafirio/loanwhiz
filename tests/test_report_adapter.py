"""Tests for the :class:`ReportAdapter` (#267).

All offline (fast suite): the adapter is driven from the committed Green Lion
2024-1 March-2026 Notes & Cash text fixture (parsed via ``parse_report_text``)
and the committed extracted ``DealModel`` seed — no network, no LLM. The headline
contract is that the adapter's output feeds the *generalised* ``run_period``
(#265) without error, so the report-driven path folds through the one engine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loanwhiz.domain.inputs import PeriodInputs
from loanwhiz.domain.state import DealState
from loanwhiz.primitives import ReportAdapter
from loanwhiz.primitives.notes_cash_parser import (
    NotesCashPeriod,
    NotesCashReport,
    parse_report_text,
)
from loanwhiz.primitives.report_adapter import (
    DEFAULT_REVENUE_RESIDUAL_LABEL,
    _fold_revenue_pop,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    _REPO_ROOT / "tests" / "fixtures" / "notes_cash" / "green-lion-2024-1-march-2026.txt"
)
SEED_MODEL = (
    _REPO_ROOT / "src" / "loanwhiz" / "data" / "deals" / "seed" / "green-lion-2024-1-bv.json"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def period() -> NotesCashPeriod:
    return parse_report_text(FIXTURE.read_text(encoding="utf-8"), period_label="March 2026")


@pytest.fixture()
def report(period: NotesCashPeriod) -> NotesCashReport:
    return NotesCashReport(deal_name="Green Lion 2024-1 B.V.", periods=[period])


@pytest.fixture()
def deal_model() -> dict:
    """The extracted deal model as a duck-typed object exposing ``.waterfalls``."""

    class _Model:
        def __init__(self, data: dict) -> None:
            self.waterfalls = data["waterfalls"]

    return _Model(json.loads(SEED_MODEL.read_text(encoding="utf-8")))


@pytest.fixture()
def adapter(deal_model) -> ReportAdapter:
    return ReportAdapter.from_deal_model(deal_model)


# ---------------------------------------------------------------------------
# to_inputs — the top-level shape
# ---------------------------------------------------------------------------


def test_to_inputs_returns_seed_and_one_input_per_period(
    adapter: ReportAdapter, report: NotesCashReport
) -> None:
    seed, inputs = adapter.to_inputs(report)
    assert isinstance(seed, DealState)
    assert isinstance(inputs, list)
    assert len(inputs) == len(report.periods) == 1
    assert all(isinstance(i, PeriodInputs) for i in inputs)


def test_to_inputs_empty_report_raises(adapter: ReportAdapter) -> None:
    empty = NotesCashReport(deal_name="Empty Deal", periods=[])
    with pytest.raises(ValueError, match="no periods"):
        adapter.to_inputs(empty)


# ---------------------------------------------------------------------------
# seed — period-0 from the first report's opening balances (B5)
# ---------------------------------------------------------------------------


def test_seed_reconstructs_opening_tranche_balances(
    adapter: ReportAdapter, period: NotesCashPeriod
) -> None:
    seed = adapter.seed(period)
    by_name = {t.name: t for t in seed.tranches}
    assert set(by_name) == {"class_a", "class_b", "class_c"}
    # Revolving period: no principal repaid, so opening == printed closing balance.
    assert by_name["class_a"].balance == pytest.approx(1_000_000_000.00)
    assert by_name["class_b"].balance == pytest.approx(53_100_000.00)
    assert by_name["class_c"].balance == pytest.approx(10_500_000.00)
    # Opening = closing + principal_repaid; assert the reconstruction formula
    # holds against the parsed report for class A.
    nb = period.note_balance("class_a")
    assert by_name["class_a"].balance == pytest.approx(
        (nb.principal_balance_after_payment or 0.0) + (nb.total_principal_payments or 0.0)
    )


def test_seed_carries_provenance(adapter: ReportAdapter, period: NotesCashPeriod) -> None:
    seed = adapter.seed(period)
    # Spec §3: the period-0 seed was extracted, so it carries provenance.
    assert seed.provenance is not None
    assert "tranches" in seed.provenance
    assert seed.provenance["tranches"].source == "report"
    assert seed.provenance["tranches"].citation is not None


def test_seed_reserve_and_pool(adapter: ReportAdapter, period: NotesCashPeriod) -> None:
    seed = adapter.seed(period)
    # Reserve opens at end balance + drawings (no drawings this period → 10.5m).
    assert seed.reserve_balance == pytest.approx(10_500_000.00)
    assert seed.reserve_target == pytest.approx(10_500_000.00)
    # Pool balance = sum of opening tranche balances (no explicit original given).
    assert seed.pool_balance == pytest.approx(1_063_600_000.00)
    assert seed.original_pool_balance == pytest.approx(1_063_600_000.00)
    # No trigger breached → sequential pay not active.
    assert seed.sequential_pay_active is False
    assert seed.cumulative_losses == pytest.approx(0.0)


def test_seed_with_explicit_original_pool_balance(deal_model, period: NotesCashPeriod) -> None:
    adapter = ReportAdapter.from_deal_model(deal_model, original_pool_balance=1_200_000_000.0)
    seed = adapter.seed(period)
    assert seed.original_pool_balance == pytest.approx(1_200_000_000.0)
    # pool_balance stays the reconstructed opening total, distinct from original.
    assert seed.pool_balance == pytest.approx(1_063_600_000.00)


# ---------------------------------------------------------------------------
# period_inputs — funds / source / overrides / sources
# ---------------------------------------------------------------------------


def test_period_inputs_funds_and_source(adapter: ReportAdapter, period: NotesCashPeriod) -> None:
    pi = adapter.period_inputs(period)
    assert pi.source == "report"
    assert pi.legs is None
    assert pi.risk_signals is None
    assert pi.reporting_date == period.reporting_date
    assert pi.available_revenue == pytest.approx(13_615_514.93)
    assert pi.available_principal == pytest.approx(43_486_011.27)
    assert pi.realized_loss == pytest.approx(0.0)


def test_period_inputs_step_sources_canonical_spelling(
    adapter: ReportAdapter, period: NotesCashPeriod
) -> None:
    pi = adapter.period_inputs(period)
    # Canonical spelling only — never the classifier's "report-supplied".
    assert set(pi.step_sources.values()) <= {"engine", "reported", "residual"}
    # (d) is class_a_interest — engine-computed.
    assert pi.step_sources["(d)"] == "engine"
    # (k) is the terminal residual sweep.
    assert pi.step_sources[DEFAULT_REVENUE_RESIDUAL_LABEL] == "residual"
    # (a) security-trustee fees — report-supplied → canonical "reported".
    assert pi.step_sources["(a)"] == "reported"


def test_period_inputs_overrides_keyed_by_priority_label(
    adapter: ReportAdapter, period: NotesCashPeriod
) -> None:
    pi = adapter.period_inputs(period)
    # Overrides cover report-supplied + residual lines, keyed by priority label
    # (NOT recipient — that is what run_period re-keys internally).
    assert "(a)" in pi.step_overrides  # report-supplied
    # Engine-computed (d) class_a_interest has NO override (engine formulates it).
    assert "(d)" not in pi.step_overrides
    # The folded (b) override equals the summed (1)…(14) sub-items.
    folded = _fold_revenue_pop(period)
    assert pi.step_overrides["(b)"] == pytest.approx(folded["(b)"])


def test_period_inputs_label_collision_engine_wins(
    adapter: ReportAdapter, period: NotesCashPeriod
) -> None:
    """Revenue+redemption reuse labels; engine wins, revenue wins reported.

    Revenue (d) is engine-computed Class A interest; redemption (d) is a
    report-supplied principal line. The canonical step maps are a single flat
    label-keyed dict, so the adapter must pin (d) "engine" (keeping its gating)
    and never carry a redemption override on it.
    """
    pi = adapter.period_inputs(period)
    # (d): engine wins over redemption's report-supplied class_b principal line.
    assert pi.step_sources["(d)"] == "engine"
    assert "(d)" not in pi.step_overrides
    # (a): revenue's report-supplied amount wins, not redemption's 43.4m purchase.
    revenue_a = _fold_revenue_pop(period)["(a)"]
    assert pi.step_overrides["(a)"] == pytest.approx(revenue_a)
    assert pi.step_overrides["(a)"] != pytest.approx(43_486_010.58)


def test_revenue_pop_folding_collapses_b_subitems(period: NotesCashPeriod) -> None:
    folded = _fold_revenue_pop(period)
    # The raw PoP prints (1)…(14); folding yields a single (b) and no bare digits.
    assert "(b)" in folded
    assert not any(k.strip("()").isdigit() for k in folded)
    raw_subtotal = sum(
        s.amount for s in period.revenue_pop if s.priority.strip("()").isdigit()
    )
    assert folded["(b)"] == pytest.approx(raw_subtotal)


# ---------------------------------------------------------------------------
# Integration: the adapter's output folds through the real run_period (#265)
# ---------------------------------------------------------------------------


def test_inputs_fold_through_run_period(adapter: ReportAdapter, report: NotesCashReport) -> None:
    """The headline contract: PeriodInputs feed the generalised run_period.

    Bridges the canonical domain seed → the engine's runtime DealState via the
    engine's own ``seed_from_prospectus`` (the report seed supplies the opening
    tranche balances), then folds the first period's PeriodInputs through the real
    ``run_period`` — no mocks. A non-error fold proves the adapter's output is
    fold-compatible (override keying + step-source spelling consumed correctly).
    """
    from loanwhiz.primitives.deal_state import DealState as EngineDealState
    from loanwhiz.primitives.period_state_machine import PeriodResult, run_period

    seed, inputs = adapter.to_inputs(report)
    by_name = {t.name: t for t in seed.tranches}

    engine_seed = EngineDealState.seed_from_prospectus(
        {
            "class_a_balance": by_name["class_a"].balance,
            "class_b_balance": by_name["class_b"].balance,
            "class_c_balance": by_name["class_c"].balance,
            "class_a_rate_pct": 2.454,
        },
        reserve_target=seed.reserve_target,
        original_pool_balance=seed.original_pool_balance,
        reporting_date=seed.reporting_date,
        revolving=True,
    )

    result = run_period(
        engine_seed,
        inputs[0],
        rates={"class_a_rate_pct": 2.454},
    )
    assert isinstance(result, PeriodResult)
    # The fold advanced state to the period's reporting date.
    assert result.closing_state.reporting_date == inputs[0].reporting_date
    # Report-supplied steps were routed (revenue distributed > 0).
    assert result.revenue_execution.total_distributed > 0.0
