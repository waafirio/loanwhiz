"""Cross-jurisdiction / vintage breadth validation (#282, last child of epic #261).

Epic #261's claim is that the *unmodified* primitives run end-to-end across the
breadth set — Dutch / Italian / Spanish RMBS, across vintages — building on the
prereqs now on this branch: #279 (direct-read tape ingestion), #280 (ESMA Annex 2
RREL mapping + tape-native B7 covenants), #281 (loan-level amortisation).

``test_capability_matrix.py`` already pins the matrix's *classifier-derived* cell
**states**; this module is the complementary proof that the live primitives
**actually execute** across the breadth set. ``tests/breadth_harness.py`` drives
the real ``CovenantMonitor`` against each deal's committed extracted triggers
(offline, deterministic) plus the deal-generic #280 / #281 framework legs, and
these tests assert:

  * every applicable primitive ran and returned a governed result;
  * the harness's per-(deal, capability) applicability **matches** the live
    ``build_capability_matrix`` cell state (so "ran end-to-end" and "the matrix
    says ran" can't silently diverge);
  * the breadth set spans the expected jurisdictions AND vintages;
  * the honest not-applicable skips (ES covenants, IT/ES tape-path, ES waterfall)
    carry real reasons — no wall of green.

Runs fully offline over the *real* shipped ``DEAL_REGISTRY`` + committed seeds,
so a regression in a seed or in a prereq primitive path is caught here.
"""

from __future__ import annotations

import pytest

from loanwhiz.api.main import _load_cached_deal_model, _VALIDATION_BUILDERS
from loanwhiz.config import DEAL_REGISTRY
from loanwhiz.primitives.capability_matrix import (
    STATE_NOT_APPLICABLE,
    STATE_RAN,
    STATE_VALIDATED,
    build_capability_matrix,
)

from breadth_harness import (
    framework_runs,
    run_breadth,
    run_loan_level_amortisation,
    run_tape_native_b7,
)

# Deals expected to carry extracted covenant triggers (covenant_monitoring runs).
_COVENANT_RUN_DEALS = {
    "green-lion-2026-1",
    "green-lion-2023-1",
    "green-lion-2024-1",
    "leone-arancio-2023-1",  # Italy — real extracted triggers (#274 refreshed seed)
}
# The minimal Spanish seed carries no triggers → covenant monitoring is N/A.
_COVENANT_NA_DEALS = {"sol-lion-ii"}


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def deal_runs():
    return run_breadth(DEAL_REGISTRY)


@pytest.fixture(scope="module")
def matrix():
    return build_capability_matrix(
        DEAL_REGISTRY,
        seed_loader=_load_cached_deal_model,
        validators=_VALIDATION_BUILDERS,
    )


def _matrix_cell(matrix, deal_id: str, capability_key: str):
    for c in matrix.cells:
        if c.deal_id == deal_id and c.capability_key == capability_key:
            return c
    raise AssertionError(f"no matrix cell for ({deal_id}, {capability_key})")


def _deal_run(deal_runs, deal_id: str):
    for dr in deal_runs:
        if dr.deal_id == deal_id:
            return dr
    raise AssertionError(f"no harness run for {deal_id}")


# ---------------------------------------------------------------------------
# Breadth coverage — the harness sees the whole registered set
# ---------------------------------------------------------------------------


def test_harness_covers_the_full_registered_deal_set(deal_runs):
    seen = {dr.deal_id for dr in deal_runs}
    assert seen == set(DEAL_REGISTRY), f"harness deal set {seen} != registry {set(DEAL_REGISTRY)}"


def test_jurisdictions_span_nl_it_es(deal_runs):
    jurisdictions = {dr.jurisdiction for dr in deal_runs}
    assert {"Netherlands", "Italy", "Spain"} <= jurisdictions, jurisdictions


def test_vintages_span_at_least_three_years_with_nl_2023_2024_2026(deal_runs):
    vintages = {dr.vintage for dr in deal_runs if dr.vintage is not None}
    # The breadth set genuinely spans multiple deal years.
    assert len(vintages) >= 3, f"expected >=3 distinct vintages, got {sorted(vintages)}"
    nl_vintages = {dr.vintage for dr in deal_runs if dr.jurisdiction == "Netherlands"}
    assert {2023, 2024, 2026} <= nl_vintages, f"NL vintages = {sorted(v for v in nl_vintages if v)}"


# ---------------------------------------------------------------------------
# Live execution — applicable primitives actually run with a governed output
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("deal_id", sorted(_COVENANT_RUN_DEALS))
def test_extracted_covenants_run_through_live_monitor(deal_runs, deal_id):
    run = _deal_run(deal_runs, deal_id).run("covenant_monitoring")
    assert run.state == STATE_RAN, f"{deal_id}: {run.reason}"
    # Governed output: triggers actually evaluated, with confidence + citations.
    assert run.detail["trigger_count"] >= 1
    assert run.detail["status_count"] == run.detail["trigger_count"]
    assert run.detail["confidence"] == 1.0
    assert run.detail["citation_count"] >= 1


def test_italian_deal_covenants_execute_end_to_end(deal_runs):
    # The cross-jurisdiction headline: the SAME live primitive that runs on the
    # Dutch deals also runs on the Italian deal's extracted triggers.
    it = _deal_run(deal_runs, "leone-arancio-2023-1")
    assert it.jurisdiction == "Italy"
    assert it.run("covenant_monitoring").state == STATE_RAN


def test_tape_native_b7_triggers_resolve_and_fire():
    # #280: the Annex-2 tape-native (arrears / default / LTV) triggers resolve
    # out of a synthetic Annex-2 period and evaluate through the live monitor.
    run = run_tape_native_b7()
    assert run.state == STATE_RAN
    assert run.detail["trigger_count"] == 3
    assert run.detail["evaluable_count"] == 3  # all three metrics resolved
    # The synthetic period is above every threshold, so all three fire.
    assert set(run.detail["active_triggers"]) == {
        "severe_arrears_trigger",
        "tape_default_rate_trigger",
        "weighted_average_ltv_trigger",
    }


def test_loan_level_amortisation_runs_over_horizon():
    # #281: the loan-level amortisation schedule runs and returns a non-trivial,
    # non-negative pool scheduled-principal series over the horizon.
    run = run_loan_level_amortisation()
    assert run.state == STATE_RAN
    assert run.detail["schedule_length"] == run.detail["horizon"] == 12
    assert run.detail["all_non_negative"] is True
    assert run.detail["total_scheduled_principal"] > 0.0


def test_framework_legs_all_ran():
    states = {r.capability_key: r.state for r in framework_runs()}
    assert states == {
        "tape_native_covenants": STATE_RAN,
        "loan_level_amortisation": STATE_RAN,
    }


# ---------------------------------------------------------------------------
# Cross-check — harness applicability matches the live capability matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("deal_id", sorted(set(DEAL_REGISTRY)))
def test_harness_covenant_state_matches_matrix(deal_runs, matrix, deal_id):
    """The harness's covenant_monitoring applicability == the matrix cell state.

    Both derive from the same input (does the deal's seed carry extracted
    triggers?), so they must agree deal-for-deal — the regression guard that
    ties "the primitive actually ran" to "the matrix says it ran".
    """
    harness_state = _deal_run(deal_runs, deal_id).run("covenant_monitoring").state
    matrix_state = _matrix_cell(matrix, deal_id, "covenant_monitoring").state
    # The matrix can reach 'validated' for engine_validation only, never for
    # covenant_monitoring — so for this capability the two vocabularies align 1:1.
    assert harness_state == matrix_state, (
        f"{deal_id}: harness={harness_state} matrix={matrix_state}"
    )


# ---------------------------------------------------------------------------
# Honest not-applicable — no wall of green
# ---------------------------------------------------------------------------


def test_spanish_covenants_are_honestly_not_applicable(deal_runs):
    es = _deal_run(deal_runs, "sol-lion-ii")
    assert es.jurisdiction == "Spain"
    run = es.run("covenant_monitoring")
    assert run.state == STATE_NOT_APPLICABLE
    assert run.reason.strip()


@pytest.mark.parametrize("deal_id", ["leone-arancio-2023-1", "sol-lion-ii"])
def test_it_es_tape_path_cells_are_not_applicable_with_reason(matrix, deal_id):
    # IT/ES deals carry no public loan tapes → the tape-path capabilities are
    # genuinely not-applicable, each with a real reason (the #193 discipline).
    for cap in ("tape_analytics", "collateral_reconciliation"):
        cell = _matrix_cell(matrix, deal_id, cap)
        assert cell.state == STATE_NOT_APPLICABLE, f"{deal_id}/{cap}: {cell.state}"
        assert cell.reason.strip()


def test_spanish_waterfall_is_not_applicable_with_reason(matrix):
    # Sol-Lion's minimal extraction carries no waterfall steps → not-applicable.
    cell = _matrix_cell(matrix, "sol-lion-ii", "waterfall_execution")
    assert cell.state == STATE_NOT_APPLICABLE
    assert cell.reason.strip()


def test_breadth_is_not_a_wall_of_green(matrix):
    # The honest cross-jurisdiction story: exactly one validated cell, and more
    # not-applicable than validated. Pins the same honesty headline the harness
    # complements with live runs.
    assert matrix.tally[STATE_VALIDATED] == 1
    assert matrix.tally[STATE_NOT_APPLICABLE] > matrix.tally[STATE_VALIDATED]
