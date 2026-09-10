"""Tests for the :class:`ReportAdapter` (#267).

All offline (fast suite): the adapter is driven from the committed Green Lion
2024-1 March-2026 Notes & Cash text fixture (parsed via ``parse_report_text``)
and the committed extracted ``DealModel`` seed — no network, no LLM. The headline
contract is that the adapter's output feeds the *generalised* ``run_period``
(#265) without error, so the report-driven path folds through the one engine.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import pytest

from loanwhiz.domain.inputs import PeriodInputs
from loanwhiz.domain.state import DealState
from loanwhiz.primitives import ReportAdapter
from loanwhiz.primitives.capital_structure import UnresolvableCapitalStructure
from loanwhiz.primitives.notes_cash_parser import (
    NotesCashPeriod,
    NotesCashReport,
    parse_report_text,
)
from loanwhiz.primitives.report_adapter import (
    DEFAULT_REVENUE_RESIDUAL_LABEL,
    DEFAULT_TRANCHE_CLASSES,
    _fold_revenue_pop,
    _step_labels,
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
    # Canonical spelling only — never the classifier's "report-supplied". Sources
    # are now per-waterfall (#270); check the revenue map.
    assert set(pi.revenue_step_sources.values()) <= {"engine", "reported", "residual"}
    # Revenue (d) is class_a_interest — engine-computed.
    assert pi.revenue_step_sources["(d)"] == "engine"
    # Revenue (k) is the terminal residual sweep.
    assert pi.revenue_step_sources[DEFAULT_REVENUE_RESIDUAL_LABEL] == "residual"
    # Revenue (a) security-trustee fees — report-supplied → canonical "reported".
    assert pi.revenue_step_sources["(a)"] == "reported"


def test_period_inputs_overrides_keyed_by_priority_label(
    adapter: ReportAdapter, period: NotesCashPeriod
) -> None:
    pi = adapter.period_inputs(period)
    # Overrides cover report-supplied + residual lines, keyed by priority label
    # (NOT recipient — that is what run_period re-keys internally), per waterfall.
    assert "(a)" in pi.revenue_step_overrides  # report-supplied
    # Engine-computed (d) class_a_interest has NO override (engine formulates it).
    assert "(d)" not in pi.revenue_step_overrides
    # The folded (b) override equals the summed (1)…(14) sub-items.
    folded = _fold_revenue_pop(period, _step_labels(adapter.revenue_steps))
    assert pi.revenue_step_overrides["(b)"] == pytest.approx(folded.amounts["(b)"])


def test_period_inputs_per_waterfall_maps_resolve_collision(
    adapter: ReportAdapter, period: NotesCashPeriod
) -> None:
    """Revenue+redemption reuse labels; per-waterfall maps keep them separate (#270).

    Revenue (a) is the report-supplied security-trustee-fee line; redemption (a) is
    the revolving-period purchase of new receivables (~€43.49M). The single FLAT
    label map dropped redemption (a) and bled revenue's amount onto the redemption
    waterfall (the #269 collision). With per-waterfall maps, each (a) carries its
    own waterfall's amount and neither corrupts the other.
    """
    pi = adapter.period_inputs(period)
    # Revenue (d): engine-computed Class A interest — no override, keeps its source.
    assert pi.revenue_step_sources["(d)"] == "engine"
    assert "(d)" not in pi.revenue_step_overrides
    # Revenue (a): the report-supplied security-trustee-fee amount.
    revenue_a = _fold_revenue_pop(period, _step_labels(adapter.revenue_steps)).amounts["(a)"]
    assert pi.revenue_step_overrides["(a)"] == pytest.approx(revenue_a)
    # Redemption (a): the ~€43.49M purchase line, NOT corrupted by revenue's (a).
    assert pi.redemption_step_sources["(a)"] == "reported"
    assert pi.redemption_step_overrides["(a)"] == pytest.approx(43_486_010.58)
    # The two waterfalls' (a) overrides are genuinely different (no collision).
    assert pi.redemption_step_overrides["(a)"] != pytest.approx(
        pi.revenue_step_overrides["(a)"]
    )


def test_revenue_pop_folding_collapses_b_subitems(
    adapter: ReportAdapter, period: NotesCashPeriod
) -> None:
    """The (1)…(14) wrap folds onto (b), and #514's general fold still does it.

    Green Lion's is the artefact the fold was written for, and it is the one deal
    the engine is validated against to the cent — so this stays an assertion about
    *this* report even though the fold that satisfies it is now deal-agnostic.
    Nothing is left over: every published row reaches a step.
    """
    folded = _fold_revenue_pop(period, _step_labels(adapter.revenue_steps))
    # The raw PoP prints (1)…(14); folding yields a single (b) and no bare digits.
    assert "(b)" in folded.amounts
    assert not any(k.strip("()").isdigit() for k in folded.amounts)
    raw_subtotal = sum(
        s.amount for s in period.revenue_pop if s.priority.strip("()").isdigit()
    )
    assert folded.amounts["(b)"] == pytest.approx(raw_subtotal)
    # And the fold placed everything — a wrap artefact left unjoined would show
    # up here rather than as a silently smaller (b).
    assert folded.unplaced == []
    assert folded.total == pytest.approx(
        sum(s.amount for s in period.revenue_pop), abs=0.01
    )


# ---------------------------------------------------------------------------
# from_deal_model — cold-start with a thin / missing-waterfall model
# ---------------------------------------------------------------------------


def test_from_deal_model_requires_both_waterfalls() -> None:
    """``from_deal_model`` pulls ``waterfalls["revenue"]`` and
    ``waterfalls["redemption"]`` step lists; a model missing one (the thin
    extraction case — e.g. an ES deal whose redemption PoP did not extract)
    raises ``KeyError`` at construction rather than silently building an
    adapter with an empty waterfall that would then fabricate nothing/garbage
    downstream. Fail loud at the seam, not silent in the fold."""

    class _RevenueOnlyModel:
        waterfalls = {"revenue": {"steps": [{"priority": "(a)", "recipient": "x"}]}}

    with pytest.raises(KeyError):
        ReportAdapter.from_deal_model(_RevenueOnlyModel())


def test_to_inputs_empty_report_message_names_the_deal() -> None:
    """The empty-report ValueError names the deal so a cold-start failure is
    actionable (which deal had no report periods to seed from)."""
    adapter = ReportAdapter(revenue_steps=[], redemption_steps=[])
    empty = NotesCashReport(deal_name="Sol-Lion II RMBS", periods=[])
    with pytest.raises(ValueError, match="Sol-Lion II RMBS"):
        adapter.to_inputs(empty)


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


# ---------------------------------------------------------------------------
# from_deal_model — the tranche list comes from the deal, not from a constant
# (#520)
# ---------------------------------------------------------------------------

CAIRN_SEED_MODEL = (
    _REPO_ROOT / "src" / "loanwhiz" / "data" / "deals" / "seed" / "cairn-clo-xvii-dac.json"
)


def _model_with_structure(seed_path: Path):
    """A duck-typed model carrying both ``waterfalls`` and ``tranche_structure``.

    The real ``DealModel`` the live path passes carries both; the module's older
    ``deal_model`` fixture deliberately carries only ``waterfalls`` (it is the
    thin cold-start case), so the derivation needs its own fixture rather than a
    widened one — keeping the "states no structure" case genuinely tested.
    """
    data = json.loads(seed_path.read_text(encoding="utf-8"))

    class _Model:
        waterfalls = data["waterfalls"]
        tranche_structure = data["tranche_structure"]

    return _Model()


def test_from_deal_model_derives_every_class_the_deal_declares() -> None:
    """Cairn's eight classes all reach the adapter — not the Green Lion triple.

    The defect this pins: ``seed`` builds one ``TrancheState`` per name here and
    every per-class input downstream is looked up **by tranche name**, so a
    hardcoded triple left ``class_b_1``/``class_b_2``/``class_d``/``class_e``/
    ``class_f`` with no tranche for a complete, correct rate map to attach to
    (#512). Nothing errored — the classes simply did not exist.

    Derived from the seed rather than transcribed, so a re-extraction that
    changed the capital structure cannot leave this assertion quietly stale.
    """
    model = _model_with_structure(CAIRN_SEED_MODEL)
    expected = tuple(
        re.sub(r"[^a-z0-9]+", "_", t["name"].lower()).strip("_")
        for t in model.tranche_structure
    )

    derived = ReportAdapter.from_deal_model(model).tranche_classes

    assert derived == expected
    assert len(derived) == 8
    # The five classes the triple could never reach, named explicitly: this is
    # the assertion that reds if the derivation regresses to a prefix.
    assert {"class_b_1", "class_b_2", "class_d", "class_e", "class_f"} <= set(derived)


def test_from_deal_model_orders_the_derived_classes_senior_to_junior() -> None:
    """Senior → junior, by the deal's own seniority ordinals — not document order.

    ``seed`` emits ``TrancheState`` in this order and the engine's waterfall pays
    down the stack in it, so an order taken from the document's row sequence would
    subordinate the wrong class. Cairn's ordinals run 0, 101, 102, 200, … 2600,
    which is exactly the numbering the pre-#478 ``seniority 0/1/2`` shape could
    not express.
    """
    model = _model_with_structure(CAIRN_SEED_MODEL)

    derived = ReportAdapter.from_deal_model(model).tranche_classes

    assert derived[0] == "class_a"
    assert derived[-1] == "subordinated_notes"
    seniorities = [t["seniority"] for t in model.tranche_structure]
    assert seniorities == sorted(seniorities), "fixture assumed to be in senior order"


def test_green_lion_derives_exactly_the_default_triple() -> None:
    """The regression guard: Green Lion's fold must not move at all.

    Green Lion states three classes that normalise to ``class_a/b/c``, so the
    derived list and ``DEFAULT_TRANCHE_CLASSES`` coincide — which is *why* the
    constant was a workable default and why generalising off it is safe. Asserted
    against the constant rather than a literal triple, so the two cannot drift
    apart silently.
    """
    model = _model_with_structure(SEED_MODEL)

    assert ReportAdapter.from_deal_model(model).tranche_classes == DEFAULT_TRANCHE_CLASSES


def test_green_lion_seed_is_unchanged_tranche_for_tranche(
    report: NotesCashReport,
) -> None:
    """The derived list seeds byte-identically to the hardcoded triple it replaces.

    Not just the same *names* — the same ``TrancheState`` balances and PDLs, and
    the same deal-level totals. Comparing two real seeds is what makes this a
    regression test rather than a restatement of the test above.
    """
    derived_seed = ReportAdapter.from_deal_model(
        _model_with_structure(SEED_MODEL)
    ).seed(report.periods[0])
    triple_seed = ReportAdapter.from_deal_model(
        _model_with_structure(SEED_MODEL), tranche_classes=DEFAULT_TRANCHE_CLASSES
    ).seed(report.periods[0])

    assert [(t.name, t.balance, t.pdl_balance) for t in derived_seed.tranches] == [
        (t.name, t.balance, t.pdl_balance) for t in triple_seed.tranches
    ]
    assert derived_seed.pool_balance == triple_seed.pool_balance
    assert derived_seed.original_pool_balance == triple_seed.original_pool_balance
    assert derived_seed.cumulative_losses == triple_seed.cumulative_losses


def test_a_model_stating_no_structure_still_gets_the_default_triple(deal_model) -> None:
    """The fallback survives for a caller that supplies no structure.

    ``deal_model`` is the thin duck-typed model exposing only ``waterfalls`` — the
    cold-start case where there is genuinely nothing to read. The point of #520 is
    that a deeper deal needs no change here, not that the default disappears.
    """
    assert ReportAdapter.from_deal_model(deal_model).tranche_classes == (
        DEFAULT_TRANCHE_CLASSES
    )


def test_direct_construction_still_defaults_to_the_triple() -> None:
    """The dataclass field is untouched — only ``from_deal_model``'s default moved.

    Callers that build an adapter without a model (the cross-jurisdiction
    cold-start path does) have no structure to derive from, so the constant stays
    their default.
    """
    assert ReportAdapter(
        revenue_steps=[], redemption_steps=[]
    ).tranche_classes == DEFAULT_TRANCHE_CLASSES


def test_an_explicit_tranche_classes_still_wins_over_the_derivation() -> None:
    """An explicitly passed list beats the deal's own — ``None`` is the sentinel.

    Distinguishing "passed nothing" from "passed the triple deliberately" is why
    the parameter defaults to ``None`` rather than to the constant: with the
    constant as the default the two are the same call and a caller could never
    narrow an 8-class deal on purpose.
    """
    model = _model_with_structure(CAIRN_SEED_MODEL)

    narrowed = ReportAdapter.from_deal_model(model, tranche_classes=("class_a",))

    assert narrowed.tranche_classes == ("class_a",)


def test_a_stated_but_unplaceable_structure_refuses_rather_than_truncating() -> None:
    """A structure that cannot be placed raises — it does not fall back.

    The fallback is for a deal that states **no** structure. Falling back here
    would put Green Lion's triple on a deal that is not Green Lion: a short stack
    models a smaller deal and reads as health rather than as a bug (#452), which
    is the exact direction ``CapitalStructure`` refuses in (#478). The error names
    the offending row so the failure is actionable.
    """

    class _UnsizedClass:
        waterfalls = {"revenue": {"steps": []}, "redemption": {"steps": []}}
        tranche_structure = [{"name": "Class A", "size_eur": None, "seniority": 0}]

    with pytest.raises(UnresolvableCapitalStructure, match="Class A"):
        ReportAdapter.from_deal_model(_UnsizedClass())


# ---------------------------------------------------------------------------
# seed — the pool balance is an ASSET figure when the report states one (#550)
# ---------------------------------------------------------------------------


def test_seed_pool_balance_falls_back_to_the_liability_total(
    adapter: ReportAdapter, period: NotesCashPeriod
) -> None:
    """With no stated collateral figure the seed keeps the liability proxy.

    This is not a good numerator and is not meant to be: ``covenant_monitor``
    refuses to build a coverage ratio on it precisely because it is the note
    total (#549). Pinned so the fallback stays the *refusable* value rather than
    quietly becoming something that looks like an asset balance.
    """
    seed = adapter.seed(period)
    assert adapter.collateral_principal_amount is None
    assert seed.pool_balance == pytest.approx(sum(t.balance for t in seed.tranches))


def test_seed_prefers_a_stated_collateral_amount_over_the_liability_total(
    adapter: ReportAdapter, period: NotesCashPeriod
) -> None:
    """A resolved Adjusted Collateral Principal Amount becomes the pool balance."""
    stated = 1_234_567_890.12
    with_collateral = dataclasses.replace(adapter, collateral_principal_amount=stated)
    seed = with_collateral.seed(period)
    liability_total = sum(t.balance for t in seed.tranches)

    assert seed.pool_balance == pytest.approx(stated)
    assert seed.pool_balance != pytest.approx(liability_total)


def test_the_seeded_pool_factor_stays_at_par_whichever_figure_is_used(
    adapter: ReportAdapter, period: NotesCashPeriod
) -> None:
    """The factor's denominator must be the same kind of quantity as its numerator.

    ``pool_factor`` is ``pool_balance / original_pool_balance`` and is documented
    as 1.0 at par. Seeding an asset-side collateral figure against the liability
    total put Cairn above par — a pool larger than at closing, rendered on
    ``/compare`` — because only one side of the comparison moved (#514). Both
    seeds must sit at par, so the factor keeps meaning whichever figure resolved.
    """
    without = adapter.seed(period)
    assert without.pool_balance == pytest.approx(without.original_pool_balance)

    with_collateral = dataclasses.replace(
        adapter, collateral_principal_amount=1_234_567_890.12
    ).seed(period)
    assert with_collateral.pool_balance == pytest.approx(
        with_collateral.original_pool_balance
    )


def test_an_explicit_original_pool_balance_still_wins(
    adapter: ReportAdapter, period: NotesCashPeriod
) -> None:
    """A caller holding the true closing balance overrides both fallbacks."""
    seed = dataclasses.replace(
        adapter, collateral_principal_amount=500.0, original_pool_balance=900.0
    ).seed(period)
    assert seed.pool_balance == pytest.approx(500.0)
    assert seed.original_pool_balance == pytest.approx(900.0)


def test_from_deal_model_carries_the_collateral_amount_onto_the_adapter(
    deal_model,
) -> None:
    """The constructor the live path uses must actually pass it through."""
    built = ReportAdapter.from_deal_model(deal_model, collateral_principal_amount=999.0)
    assert built.collateral_principal_amount == 999.0
