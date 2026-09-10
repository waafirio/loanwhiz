"""Tests for the cross-deal capability matrix (C3, #241, epic #236).

The matrix makes primitive reusability *visible* across deals and jurisdictions:
for each deal-facing primitive capability x each registered deal it computes a
typed cell (``validated`` / ``ran`` / ``not-applicable``) with governance
evidence. The whole point (the #193 honesty discipline) is that it tells the
*true* cross-jurisdiction story — not a wall of green — so these tests pin both
the shape AND the honest per-deal/per-jurisdiction states.

The runner is exercised over the *real* shipped ``DEAL_REGISTRY`` + the committed
seed models (not a fixture), so a regression in the seeds or registry that would
flip a cell's honest state is caught here. A pure-unit test with fakes pins the
runner's contract (state vocabulary, mandatory reasons) independent of the data.
"""

from __future__ import annotations

from pathlib import Path

from functools import lru_cache

import pytest
from fastapi.testclient import TestClient

from loanwhiz.api import app
from loanwhiz.api.main import _load_cached_deal_model
from loanwhiz.config import DEAL_REGISTRY
from loanwhiz.extraction.assembler import DealModel
from loanwhiz.domain.tape_provenance import TapeSourceKind
from loanwhiz.primitives.capability_matrix import (
    ENGINE_STRUCTURAL_CONFIG_KEYS,
    _NO_ANSWER_KEY,
    _NO_ENGINE_SERIES,
    _NO_POP_SECTION,
    _RECONCILE_ERROR_PREFIX,
    _has_pop_section,
    STATE_NOT_APPLICABLE,
    STATE_RAN,
    STATE_VALIDATED,
    CapabilityMatrix,
    _missing_structural_config,
    build_capability_matrix,
    capability_rows,
)
from loanwhiz.primitives.quality_harness import _default_series_provider
from loanwhiz.primitives.reconciliation_answer_key import (
    AnswerKeyPeriod,
    AnswerKeyPopStep,
    CovenantResult,
    DealAnswerKey,
    load_answer_key,
)

client = TestClient(app)

#: The closed set of engine-validation refusals — imported, never transcribed, so
#: a reworded reason cannot silently pass a test asserting the old sentence.
_REFUSAL_VOCABULARY = frozenset({_NO_ANSWER_KEY, _NO_POP_SECTION, _NO_ENGINE_SERIES})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _real_matrix() -> CapabilityMatrix:
    """Build the matrix over the real registry + committed seeds + answer keys."""
    return build_capability_matrix(
        DEAL_REGISTRY,
        seed_loader=_load_cached_deal_model,
        answer_key_loader=load_answer_key,
        series_provider=_default_series_provider(),
    )


def _no_answer_keys(_ctx):
    """Answer-key loader that resolves nothing — the "no committed key" branch."""
    return None


def _no_series(_deal_id, _ctx, _model):
    """Series provider that registers no deal — the "no engine series" branch."""
    return None


def _fake_answer_key(deal_id: str, deal_name: str, *, pop: bool) -> DealAnswerKey:
    """A committed-shaped key, with or without a Priority-of-Payments section.

    ``pop=False`` is the real Cairn CLO XVII shape (#481): a genuine key authored
    from published coverage-test outcomes, carrying covenants and deliberately no
    PoP. It must not be mistaken for engine-validation ground truth.
    """
    return DealAnswerKey(
        deal_id=deal_id,
        deal_name=deal_name,
        periods=[
            AnswerKeyPeriod(
                reporting_date="2025-03-31",
                period_label="March 2025",
                available_revenue_funds=1_000.0 if pop else None,
                revenue_pop=(
                    [AnswerKeyPopStep(priority="(a)", amount=1_000.0)] if pop else []
                ),
                covenants=[] if pop else [CovenantResult(name="par_coverage", passed=True)],
            )
        ],
    )


@lru_cache(maxsize=1)
def _committed_key_and_series():
    """Green Lion 2024-1's committed answer key + committed offline engine series.

    The real reconciling pair, reused under *synthetic* deal ids so a test can
    prove the validated cell follows the injected data rather than any deal id.
    Cached because the fold is the expensive part.
    """
    from loanwhiz.primitives.reconciler import fold_green_lion_2024_1

    series, _ = fold_green_lion_2024_1()
    return load_answer_key("Green Lion 2024-1 B.V."), series


def _cell(matrix: CapabilityMatrix, deal_id: str, capability_key: str):
    for c in matrix.cells:
        if c.deal_id == deal_id and c.capability_key == capability_key:
            return c
    raise AssertionError(f"no cell for ({deal_id}, {capability_key})")


# ---------------------------------------------------------------------------
# Matrix shape
# ---------------------------------------------------------------------------


def test_matrix_is_full_primitives_by_deals_grid() -> None:
    matrix = _real_matrix()
    n_caps = len(matrix.capabilities)
    n_deals = len(matrix.deals)
    # All 5 deals present (3 Green Lion + Leone Arancio + Sol-Lion).
    assert n_deals == len(DEAL_REGISTRY)
    assert n_caps == len(capability_rows())
    # Exactly one cell per (capability, deal) — a full grid, no holes.
    assert len(matrix.cells) == n_caps * n_deals
    pairs = {(c.capability_key, c.deal_id) for c in matrix.cells}
    assert len(pairs) == len(matrix.cells)


def test_every_cell_has_a_valid_state_and_evidence() -> None:
    matrix = _real_matrix()
    valid_states = {STATE_VALIDATED, STATE_RAN, STATE_NOT_APPLICABLE}
    for c in matrix.cells:
        assert c.state in valid_states, c
        assert c.evidence is not None
        assert c.evidence.citation.strip(), c


def test_tally_matches_cells() -> None:
    matrix = _real_matrix()
    assert sum(matrix.tally.values()) == len(matrix.cells)
    for state in (STATE_VALIDATED, STATE_RAN, STATE_NOT_APPLICABLE):
        counted = sum(1 for c in matrix.cells if c.state == state)
        assert matrix.tally.get(state, 0) == counted


# ---------------------------------------------------------------------------
# Honesty contract — the #193 discipline
# ---------------------------------------------------------------------------


def test_every_not_applicable_cell_carries_a_real_reason() -> None:
    matrix = _real_matrix()
    na_cells = [c for c in matrix.cells if c.state == STATE_NOT_APPLICABLE]
    # The cross-jurisdiction story genuinely has not-applicable cells — this
    # would be a wall of green if it didn't.
    assert na_cells, "expected some honest not-applicable cells"
    for c in na_cells:
        assert c.reason.strip(), f"not-applicable cell without a reason: {c}"
        # A not-applicable cell ran nothing, so it carries no confidence.
        assert c.evidence.confidence is None, c


def test_matrix_is_not_a_wall_of_green() -> None:
    matrix = _real_matrix()
    # The honesty point: more not-applicable than validated; the matrix is mixed.
    assert matrix.tally[STATE_NOT_APPLICABLE] > 0
    assert matrix.tally[STATE_VALIDATED] >= 1
    assert matrix.tally[STATE_NOT_APPLICABLE] > matrix.tally[STATE_VALIDATED]


# ---------------------------------------------------------------------------
# Per-deal / per-jurisdiction story (pins the true cross-jurisdiction shape)
# ---------------------------------------------------------------------------


def test_green_lion_2024_1_engine_validation_survives_the_answer_key_swap() -> None:
    """The #492 regression: 2024-1's cell is unchanged by the move off builders.

    Its state, confidence and every evidence value are re-derived here from the
    committed answer key rather than from ``validate_green_lion_2024_1``, so an
    identical cell is what proves the swap behaviour-preserving.
    """
    cell = _cell(_real_matrix(), "green-lion-2024-1", "engine_validation")
    assert cell.state == STATE_VALIDATED
    assert cell.evidence.confidence == pytest.approx(1.0)
    assert cell.evidence.detail == {
        "passed": True,
        "periods_checked": 3,
        "periods_passed": 3,
        # Every period of this key carries a PoP, so nothing is skipped (#513).
        # Asserted rather than omitted: an all-PoP key reporting a non-zero skip
        # count would mean the grader had started dropping gradeable periods.
        "periods_skipped": 0,
        "tolerance_eur": pytest.approx(0.01),
    }
    assert "reconciled to the cent" in cell.evidence.citation
    # And the reason carries no not-graded clause, because there is nothing to
    # disclose — the clause must appear only where periods really were skipped.
    assert "not graded" not in cell.reason


def test_validated_is_exactly_the_deals_carrying_committed_pop_ground_truth() -> None:
    """``validated`` is a property of committed data, not of a curated list (#492).

    The honest cross-deal story is still not a wall of green — most cells remain
    not-applicable — but the set that IS validated must be *derivable*: exactly
    the registered deals with a committed answer key carrying a
    Priority-of-Payments section AND a committed offline engine series. Asserting
    the set rather than a count is what keeps this true as deals are registered.
    """
    matrix = _real_matrix()
    series_provider = _default_series_provider()

    expected = set()
    for deal_id, ctx in DEAL_REGISTRY.items():
        key = load_answer_key(ctx)
        if key is None or not any(p.revenue_pop or p.redemption_pop for p in key.periods):
            continue
        if series_provider(deal_id, ctx, None) is None:
            continue
        expected.add(deal_id)

    validated = {c.deal_id for c in matrix.cells if c.state == STATE_VALIDATED}
    assert validated == expected
    assert "green-lion-2024-1" in validated, "the headline proof must not regress"
    # Still the minority story, never a wall of green (#193).
    assert matrix.tally[STATE_NOT_APPLICABLE] > matrix.tally[STATE_VALIDATED]
    # Only the engine-validation row can reach it.
    assert {c.capability_key for c in matrix.cells if c.state == STATE_VALIDATED} == {
        "engine_validation"
    }


def test_green_lion_2026_1_synthetic_runs_most_primitives() -> None:
    matrix = _real_matrix()
    # Synthetic demo deal: has 3 tapes + full extracted model → tape, covenant,
    # waterfall, collateral all run.
    for cap in (
        "tape_analytics",
        "covenant_monitoring",
        "waterfall_execution",
        "collateral_reconciliation",
    ):
        assert _cell(matrix, "green-lion-2026-1", cap).state == STATE_RAN
    # But no published PoP report → engine validation is not-applicable.
    assert _cell(matrix, "green-lion-2026-1", "engine_validation").state == STATE_NOT_APPLICABLE


def test_leone_arancio_italian_covenant_and_waterfall_run() -> None:
    matrix = _real_matrix()
    deal_id = "leone-arancio-2023-1"
    # Full Italian model (refreshed seed, #274): real extracted triggers → covenant
    # runs, and the extracted revenue/redemption/post-enforcement waterfalls execute.
    assert _cell(matrix, deal_id, "covenant_monitoring").state == STATE_RAN
    assert _cell(matrix, deal_id, "waterfall_execution").state == STATE_RAN
    # Since #484 the deal carries a synthetic pool fitted to its own investor
    # report, so tape analytics runs. The cell must say what the pool is.
    tape = _cell(matrix, deal_id, "tape_analytics")
    assert tape.state == STATE_RAN
    assert "Generated by LoanWhiz" in tape.reason
    assert "no figure computed from this tape is evidence about a real pool" in tape.reason

def test_sol_lion_spanish_runs_on_extracted_model_and_synthetic_pool() -> None:
    matrix = _real_matrix()
    deal_id = "sol-lion-ii"
    cells = {c.capability_key: c.state for c in matrix.cells if c.deal_id == deal_id}
    # After the #438 re-extraction the Spanish seed carries three real extracted
    # triggers and an executable cascade in ALL THREE waterfall sections, so
    # covenant_monitoring + waterfall_execution run. Since #484 the tape-driven
    # capabilities run too, on a synthetic pool fitted to the deal's own
    # investor report.
    assert cells["covenant_monitoring"] == STATE_RAN
    assert cells["waterfall_execution"] == STATE_RAN
    assert cells["tape_analytics"] == STATE_RAN
    assert cells["collateral_reconciliation"] == STATE_RAN
    # The one thing a synthetic pool must never reach. Sol-Lion II publishes no
    # Notes & Cash report, so there is nothing to reconcile the engine against
    # and no amount of pool data changes that.
    assert cells["engine_validation"] == STATE_NOT_APPLICABLE
    tape = _cell(matrix, deal_id, "tape_analytics")
    assert "Generated by LoanWhiz" in tape.reason

def test_jurisdictions_are_resolved_across_de_it_es() -> None:
    matrix = _real_matrix()
    by_deal = {d.deal_id: d.jurisdiction for d in matrix.deals}
    # Non-Dutch deals carry an explicit jurisdiction key; Green Lion defaults.
    assert by_deal["leone-arancio-2023-1"] == "Italy"
    assert by_deal["sol-lion-ii"] == "Spain"
    assert by_deal["green-lion-2024-1"] == "Netherlands"
    # The same primitive code is shown running across >= 3 jurisdictions.
    assert {"Netherlands", "Italy", "Spain"} <= set(by_deal.values())


# ---------------------------------------------------------------------------
# Runner contract — pure unit, with injected fakes (data-independent)
# ---------------------------------------------------------------------------


def _fake_model(*, triggers: int, waterfall_steps: int, completeness: float) -> DealModel:
    """Build a minimal in-memory DealModel for the runner-contract tests."""
    return DealModel.model_validate(
        {
            "metadata": {
                "deal_name": "Fake Deal",
                "prospectus_url": "http://example/p.pdf",
                "extracted_at": "2026-01-01T00:00:00Z",
                "extraction_duration_sec": 0.0,
                "sections_found": [],
                "completeness_score": completeness,
                "cache_path": "",
            },
            "definitions": {},
            "waterfalls": (
                {"revenue": {"steps": [{"priority": "(a)", "recipient": "x"}] * waterfall_steps}}
                if waterfall_steps
                else {}
            ),
            "covenants": {
                "deal_name": "Fake Deal",
                "triggers": [{"name": f"t{i}", "metric": "m"} for i in range(triggers)],
                "issuer_covenants": [],
                "extraction_confidence": 0.6,
            },
            "tranche_structure": [],
            "trigger_names": [f"t{i}" for i in range(triggers)],
        }
    )


def test_runner_applicability_is_data_driven_not_hardcoded() -> None:
    # A synthetic deal WITH tapes + full model + a committed answer key and
    # engine series: every capability is live, and engine validation is
    # validated. The key/series pair is Green Lion 2024-1's real committed data
    # supplied under the deal id "rich", so reaching `validated` proves the
    # classifier reads the injected data and holds no deal id of its own (#492).
    full_model = _fake_model(triggers=3, waterfall_steps=11, completeness=0.9)
    committed_key, committed_series = _committed_key_and_series()

    deals = {
        "rich": {
            "deal_name": "Fake Deal",
            "jurisdiction": "Atlantis",
            "tape_urls": [{"url": "u1"}, {"url": "u2"}],
            # The pool-state reconstruction folds each tape period through the
            # engine, so it needs these as well as the tapes. A deal with tapes
            # and no structural config is a real and different case — pinned by
            # ``test_a_registered_tape_alone_does_not_light_the_reconstruction``.
            "capital_structure": {
                "class_a_balance": 1_000.0,
                "class_a_rate_pct": 3.0,
                "class_b_balance": 100.0,
                "class_c_balance": 10.0,
            },
            "reserve_account_target": 50.0,
            "original_pool_balance": 1_110.0,
        },
        "bare": {"deal_name": "Empty Deal", "tape_urls": []},
    }

    def loader(ctx):
        return full_model if ctx.get("tape_urls") else None

    matrix = build_capability_matrix(
        deals,
        seed_loader=loader,
        answer_key_loader=lambda ctx: committed_key if ctx.get("tape_urls") else None,
        series_provider=lambda deal_id, ctx, model: (
            committed_series if deal_id == "rich" else None
        ),
    )

    # Rich deal: everything runs; engine validation is validated.
    rich = {c.capability_key: c for c in matrix.cells if c.deal_id == "rich"}
    assert rich["tape_analytics"].state == STATE_RAN
    assert rich["covenant_monitoring"].state == STATE_RAN
    assert rich["waterfall_execution"].state == STATE_RAN
    assert rich["collateral_reconciliation"].state == STATE_RAN
    assert rich["engine_validation"].state == STATE_VALIDATED

    # Bare deal: no model, no tapes, no answer key → all not-applicable with reasons.
    bare = {c.capability_key: c for c in matrix.cells if c.deal_id == "bare"}
    assert all(c.state == STATE_NOT_APPLICABLE for c in bare.values())
    assert all(c.reason.strip() for c in bare.values())

    # Jurisdiction default applies only when the key is absent.
    juris = {d.deal_id: d.jurisdiction for d in matrix.deals}
    assert juris["rich"] == "Atlantis"
    assert juris["bare"] == "Netherlands"


def test_runner_validated_requires_a_reconciliation_that_passes() -> None:
    """A key the engine does NOT reproduce yields ``ran``, never ``validated``.

    Perturbs one published amount in the committed key by EUR 1_000 and reconciles
    the *real* engine series against it, so the ``ran`` branch is reached through
    the same to-the-cent reconciler the passing branch uses — not a stubbed report
    asserting its own verdict.
    """
    full_model = _fake_model(triggers=1, waterfall_steps=4, completeness=0.5)
    committed_key, committed_series = _committed_key_and_series()
    perturbed = committed_key.model_copy(deep=True)
    perturbed.periods[0].revenue_pop[0].amount += 1_000.0

    matrix = build_capability_matrix(
        {"d": {"deal_name": "Fake Deal", "tape_urls": []}},
        seed_loader=lambda ctx: full_model,
        answer_key_loader=lambda ctx: perturbed,
        series_provider=lambda deal_id, ctx, model: committed_series,
    )
    cell = next(c for c in matrix.cells if c.capability_key == "engine_validation")
    assert cell.state == STATE_RAN  # ran but did not reconcile → not validated
    assert cell.state != STATE_VALIDATED
    assert cell.evidence.detail["passed"] is False


def test_a_partly_pop_bearing_key_validates_on_what_it_graded_and_says_so() -> None:
    """``validated`` on a partly-graded key discloses what it did not grade (#513).

    Cairn's key is the real instance of this shape, but it registers no offline
    engine series, so the committed data cannot reach this branch — hence the
    real engine series here with covenant-only periods added to its key. The
    reconciliation is the real one; only the key's shape is synthetic.

    The bound this pins is the one #481 asks for: a cell reading ``validated``
    over 3 of a key's 5 periods is making a narrower claim than the badge alone
    conveys, so the count and the disclosure both have to reach the operator.
    """
    full_model = _fake_model(triggers=1, waterfall_steps=4, completeness=0.5)
    committed_key, committed_series = _committed_key_and_series()
    covenant_only = AnswerKeyPeriod(
        reporting_date="2023-12-31",
        period_label="December 2023",
        covenants=[CovenantResult(name="class_a_par_value_test", passed=True)],
    )
    partly = committed_key.model_copy(
        update={
            "periods": [
                covenant_only,
                *committed_key.periods,
                covenant_only.model_copy(
                    update={"reporting_date": "2025-06-30", "period_label": "June 2025"}
                ),
            ]
        }
    )

    matrix = build_capability_matrix(
        {"d": {"deal_name": "Fake Deal", "tape_urls": []}},
        seed_loader=lambda ctx: full_model,
        answer_key_loader=lambda ctx: partly,
        series_provider=lambda deal_id, ctx, model: committed_series,
    )
    cell = next(c for c in matrix.cells if c.capability_key == "engine_validation")

    # The graded periods still validate — a skip neither helps nor hurts.
    assert cell.state == STATE_VALIDATED
    assert cell.evidence.detail["passed"] is True
    # But the counts are over the graded set: 3 of 5, never 5 of 5.
    assert cell.evidence.detail["periods_checked"] == 3
    assert cell.evidence.detail["periods_passed"] == 3
    assert cell.evidence.detail["periods_skipped"] == 2
    assert "2 further period(s) of this key were not graded" in cell.reason


# ---------------------------------------------------------------------------
# API endpoint — real TestClient over the real registry (no mocks)
# ---------------------------------------------------------------------------


def test_capability_matrix_endpoint_returns_structured_matrix() -> None:
    resp = client.get("/capability-matrix")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) >= {"capabilities", "deals", "cells", "tally", "note"}
    assert len(body["deals"]) == len(DEAL_REGISTRY)
    assert len(body["cells"]) == len(body["capabilities"]) * len(body["deals"])
    # The endpoint surfaces the honest cross-jurisdiction story.
    # Re-derived from the matrix the endpoint serves, never transcribed (#441).
    assert body["tally"] == _real_matrix().tally
    assert body["tally"]["validated"] >= 1
    assert body["tally"]["not-applicable"] > body["tally"]["validated"]
    # Every cell over the wire carries a non-empty reason.
    assert all(c["reason"].strip() for c in body["cells"])


# ---------------------------------------------------------------------------
# The contracts #457 added: a closed state vocabulary, registry-resolved asset
# class, and not-applicable reasons that are factually true of the deal.
# ---------------------------------------------------------------------------


def test_the_state_vocabulary_is_closed_at_construction() -> None:
    """A fourth cell state is refused by the model, not merely discouraged.

    The three states are the #193 honesty contract. Before this was typed, the
    field was a bare ``str``: a widened vocabulary ("partial", "pending") would
    have been accepted server-side and served to a web client whose own union
    cannot represent it. Refusing it here makes the invalid state unrepresentable
    rather than documented-as-invalid.
    """
    from pydantic import ValidationError

    from loanwhiz.primitives.capability_matrix import CapabilityCell, CellEvidence

    evidence = CellEvidence(confidence=None, citation="test", detail={})
    for state in (STATE_VALIDATED, STATE_RAN, STATE_NOT_APPLICABLE):
        assert CapabilityCell(
            capability_key="k", deal_id="d", state=state, reason="r", evidence=evidence
        ).state == state

    for widened in ("partial", "pending", "not_applicable", "unknown"):
        with pytest.raises(ValidationError):
            CapabilityCell(
                capability_key="k", deal_id="d", state=widened, reason="r", evidence=evidence
            )


def test_asset_class_is_resolved_from_the_registry_not_from_a_deal_id() -> None:
    """A column describes its asset class as data, so a new one costs no code.

    The generality bar for this epic is "would adding CMBS next be cheaper?".
    It is only cheaper if asset class arrives by registration. An unregistered
    deal falls back to the RMBS default rather than failing or blanking.
    """
    from loanwhiz.primitives.capability_matrix import _resolve_asset_class

    assert _resolve_asset_class({"asset_class": "CLO"}) == "CLO"
    assert _resolve_asset_class({"asset_class": "CMBS"}) == "CMBS"
    assert _resolve_asset_class({}) == "RMBS"

    matrix = _real_matrix()
    by_id = {c.deal_id: c for c in matrix.deals}
    assert by_id["cairn-clo-xvii"].asset_class == "CLO"
    assert by_id["green-lion-2024-1"].asset_class == "RMBS"
    # Every column is described; a blank asset class is the silent gap this fills.
    assert all(c.asset_class.strip() for c in matrix.deals)


def test_engine_validation_never_claims_a_published_report_is_missing() -> None:
    """The refusal must not assert anything about what a deal publishes (#455).

    This reason is load-bearing prose, and it can be wrong in two opposite
    directions. "No published report exists" says validation is *impossible* —
    false for Cairn CLO XVII DAC, whose Note Valuation Report carries both
    Priorities of Payments. "No answer key has been authored" says it merely has
    not been done — false for Green Lion 2023-1, which has one committed (#440).

    The registry cannot tell those apart: `investor_report_urls` counts periodic
    reports rather than PoP reports, and a deal may deliberately leave
    `notes_cash_report_urls` unset. So the reason states only the condition this
    classifier actually verified — no committed builder — and says so explicitly.
    """
    matrix = _real_matrix()
    for column in matrix.deals:
        cell = _cell(matrix, column.deal_id, "engine_validation")
        if cell.state == STATE_VALIDATED:
            continue
        # The reason names a committed artifact this repo can check for, and
        # says so explicitly.
        assert cell.reason in _REFUSAL_VOCABULARY, f"{column.deal_id}: {cell.reason!r}"
        assert "not about what the deal publishes" in cell.reason
        assert cell.evidence.detail["has_answer_key"] is (
            load_answer_key(DEAL_REGISTRY[column.deal_id]) is not None
        )
        # Neither overclaim, in the shapes each could return as.
        for overclaim in (
            "No published Notes & Cash",
            "No published periodic report",
            "no external ground truth",
            "no answer key has been authored",
        ):
            assert overclaim not in cell.reason, f"{column.deal_id}: {overclaim!r}"


def test_engine_validation_reasons_come_from_a_closed_verified_vocabulary() -> None:
    """No deal may be told a story true only of another (#457/#471).

    Before #492 there was one refusal, so this held trivially. A key-driven
    classifier distinguishes three preconditions — no key, a key without a PoP
    section, no engine series — and telling a deal the wrong one is exactly the
    failure #471 records. The guard is therefore that every reason is drawn from
    the closed vocabulary AND matches the condition that actually holds, not that
    there is only one of them.
    """
    matrix = _real_matrix()
    for cell in matrix.cells:
        if cell.capability_key != "engine_validation" or cell.state == STATE_VALIDATED:
            continue
        key = load_answer_key(DEAL_REGISTRY[cell.deal_id])
        if key is None:
            expected = _NO_ANSWER_KEY
        elif not any(p.revenue_pop or p.redemption_pop for p in key.periods):
            expected = _NO_POP_SECTION
        else:
            expected = _NO_ENGINE_SERIES
        assert cell.reason == expected, cell.deal_id


def test_a_key_without_a_priority_of_payments_section_is_not_ground_truth() -> None:
    """A genuine key carrying covenants and no PoP — Cairn's shape until #495.

    Such a key grades a `covenants` row on /quality-matrix and must still refuse
    here, with the reason that names *which* input is missing — never the no-key
    one. The key is synthetic on purpose: Cairn's committed one now carries a PoP
    section too, and this branch must stay covered by something after the one
    deal that exercised it moved off it.
    """
    key = _fake_answer_key("cairn-clo-xvii", "Cairn CLO XVII DAC", pop=False)
    _, committed_series = _committed_key_and_series()
    matrix = build_capability_matrix(
        {"cairn-clo-xvii": {"deal_name": "Cairn CLO XVII DAC", "tape_urls": []}},
        seed_loader=lambda ctx: None,
        answer_key_loader=lambda ctx: key,
        series_provider=lambda deal_id, ctx, model: committed_series,
    )
    cell = _cell(matrix, "cairn-clo-xvii", "engine_validation")
    assert cell.state == STATE_NOT_APPLICABLE
    assert cell.reason == _NO_POP_SECTION
    assert cell.evidence.detail == {"has_answer_key": True, "has_pop_section": False}


def test_a_pop_bearing_key_without_an_engine_series_refuses_on_the_series() -> None:
    """The #440 half nobody sees until it bites: a key alone grades nothing.

    A committed PoP-bearing key with no registered offline fold must say the
    series is what is missing — the deal HAS published ground truth here, so the
    no-key reason would be false of it.
    """
    key = _fake_answer_key("future-deal", "Future Deal 2027-1 B.V.", pop=True)
    matrix = build_capability_matrix(
        {"future-deal": {"deal_name": "Future Deal 2027-1 B.V.", "tape_urls": []}},
        seed_loader=lambda ctx: None,
        answer_key_loader=lambda ctx: key,
        series_provider=_no_series,
    )
    cell = _cell(matrix, "future-deal", "engine_validation")
    assert cell.state == STATE_NOT_APPLICABLE
    assert cell.reason == _NO_ENGINE_SERIES
    assert cell.evidence.detail == {
        "has_answer_key": True,
        "has_pop_section": True,
        "has_engine_series": False,
    }


def test_a_broken_key_series_pair_degrades_one_cell_not_the_endpoint() -> None:
    """A malformed pair must not 500 the matrix — it refuses, naming the error.

    Making the cell data-driven widened who can raise here: before #492 only a
    registered builder could, and there was one. `reconcile_series` raises on a
    join mismatch (a key whose period count differs from the fold's), which is
    exactly what a freshly committed key gets wrong, so the whole matrix must not
    depend on every committed pair being well-formed.
    """
    committed_key, committed_series = _committed_key_and_series()
    short = committed_key.model_copy(deep=True)
    del short.periods[1:]  # 1 key period vs the fold's 3 → join mismatch

    matrix = build_capability_matrix(
        {"d": {"deal_name": "Mismatched Deal", "tape_urls": []}},
        seed_loader=lambda ctx: None,
        answer_key_loader=lambda ctx: short,
        series_provider=lambda deal_id, ctx, model: committed_series,
    )
    cell = _cell(matrix, "d", "engine_validation")
    assert cell.state == STATE_NOT_APPLICABLE
    assert cell.reason.startswith(_RECONCILE_ERROR_PREFIX)
    assert "not about what the deal publishes" in cell.reason
    assert cell.evidence.detail["reconcile_error"] == "ValueError"


def test_no_deal_id_is_hardcoded_in_the_engine_validation_classifier() -> None:
    """Withdraw the committed data and the headline proof goes with it (#492).

    ``validated`` must be earned by the injected key + series, so the same real
    registry with no answer keys must reach it for nobody. This is the falsifier
    for "the cell is data-driven": a residual hardcode would survive this.
    """
    matrix = build_capability_matrix(
        DEAL_REGISTRY,
        seed_loader=_load_cached_deal_model,
        answer_key_loader=_no_answer_keys,
        series_provider=_default_series_provider(),
    )
    assert matrix.tally[STATE_VALIDATED] == 0
    for cell in matrix.cells:
        if cell.capability_key == "engine_validation":
            assert cell.reason == _NO_ANSWER_KEY


def test_tape_reasons_do_not_overclaim_that_no_loan_level_data_exists() -> None:
    """No tape reason may claim a deal publishes no loan-level data.

    #457 retracted "no loan tapes published for this deal": ``tape_urls`` encodes
    what *this repo* has registered, never what an issuer discloses. The claim is
    now checked across every deal and every tape-driven cell, in both directions
    — a deal whose cell is ``ran`` must not have acquired the wider claim either.
    """
    # Ban the retracted CLAIM, not any fragment of it: the surviving
    # not-applicable reason ends "...it does not claim the deal publishes no
    # loan-level collateral detail in another form", so a crude "publishes no
    # loan" ban would flag the very sentence that refuses the claim.
    retracted_claims = (
        "no loan tapes published",
        "no loan tapes are published",
        "this deal publishes no loan",
        "loan-level esma tapes are not published",
    )
    matrix = _real_matrix()
    for column in matrix.deals:
        for capability_key in ("tape_analytics", "collateral_reconciliation"):
            reason = _cell(matrix, column.deal_id, capability_key).reason.lower()
            for retracted in retracted_claims:
                assert retracted not in reason, (column.deal_id, capability_key, reason)


def test_the_user_facing_no_tape_card_does_not_carry_the_retracted_claim() -> None:
    """#457 fixed the cell reasons; the UI kept saying the retracted thing.

    ``NoTapesNotice`` is the user-facing counterpart of a ``not-applicable``
    tape cell, and it still rendered "No loan tapes published for this deal" and
    "its loan-level ESMA tapes are not published" — a claim about what an issuer
    discloses, made by a component that only knows what LoanWhiz registered.
    Guarded from Python because the repo has no JS test runner; the file is a
    string in either language.
    """
    card = (
        Path(__file__).resolve().parents[1] / "web" / "components" / "page-states.tsx"
    ).read_text(encoding="utf-8")
    for retracted in (
        "No loan tapes published",
        "loan tapes published for this deal",
        "ESMA tapes are not published",
    ):
        assert retracted not in card, retracted
    # And it still says the true thing, so this cannot pass by deleting the card.
    assert "No loan tape is registered for this deal" in card


def test_the_clo_tape_cells_report_the_derived_tape_without_claiming_a_filing() -> None:
    """The positive branch is where provenance laundering would happen.

    Registering the derived tape flips ``tape_analytics`` to ``ran``. That cell
    must say what the tape *is* — reconstructed by LoanWhiz from a trustee report
    — and must not describe it in the vocabulary of a published or filed ESMA
    tape. Cairn does file real Article 7(1)(a) Loan Reports; this is not one of
    them, and no reader may be able to conclude otherwise from this cell.
    """
    cell = _cell(_real_matrix(), "cairn-clo-xvii", "tape_analytics")
    assert cell.state == STATE_RAN
    assert cell.evidence.detail["tape_source_kinds"] == {
        TapeSourceKind.DERIVED_FROM_INVESTOR_REPORT.value: 3
    }
    # The disclosure is quoted verbatim, not paraphrased, so it cannot drift.
    assert TapeSourceKind.DERIVED_FROM_INVESTOR_REPORT.disclosure in cell.reason
    assert "NOT filed under Article 7(1)(a)" in cell.reason
    # And none of the wording that would read as a regulatory filing. Each entry
    # is a positive claim; the reason's own "are not published tape files" must
    # not trip it, so the ban is on the assertion rather than on the noun.
    for laundering in (
        "ESMA tape URL",
        "loan tape(s) published",
        "filed under Article 7(1)(a) in",
        "regulatory filing",
    ):
        assert laundering not in cell.reason


def test_a_registered_tape_alone_does_not_light_the_reconstruction() -> None:
    """The collateral cell must not claim a reconstruction the endpoint refuses.

    Cairn now registers three tape periods but no structural config, so
    ``_resolve_structural_config`` answers a labelled 422. Reporting ``ran`` off
    tape count alone would be #457's mirror-image overclaim: one false reason
    swapped for another. The cell stays ``not-applicable`` and names the true
    cause — configuration, not an absent tape.
    """
    cell = _cell(_real_matrix(), "cairn-clo-xvii", "collateral_reconciliation")
    assert cell.state == STATE_NOT_APPLICABLE
    assert cell.evidence.detail["tape_count"] == 3
    # Since #478 the eight-class stack resolves, so the cell no longer claims the
    # structure is missing — it names the senior coupon, which is what the
    # resolver actually refuses on. Naming a key the deal in fact supplies is the
    # #457 failure in miniature: a true-sounding reason for the wrong layer.
    assert cell.evidence.detail["missing_structural_config"] == [
        "class_a_rate_pct",
        "reserve_account_target",
        "original_pool_balance",
    ]
    # It must not read as "there is no tape" — that is the retracted claim.
    assert "No machine-readable ESMA loan tape is registered" not in cell.reason
    assert "tape(s) are registered" in cell.reason


def test_cairn_cells_revert_when_the_derived_tape_is_deregistered() -> None:
    """Registering the tape is reversible, and reverting restores the old reasons.

    The round-trip half of the persistence contract: ``tape_urls`` is a durable
    registry edit, so the state it moves the matrix out of must still be
    reachable. Removing the entries returns both tape-driven cells to exactly the
    #457 wording — which also proves the new reasons are driven by the registry
    rather than by anything hardcoded about this deal.
    """
    def _matrix(ctx):
        return build_capability_matrix(
            {"cairn-clo-xvii": ctx},
            seed_loader=_load_cached_deal_model,
            answer_key_loader=load_answer_key,
            series_provider=_default_series_provider(),
        )

    ctx = dict(DEAL_REGISTRY["cairn-clo-xvii"])
    assert ctx["tape_urls"], "precondition: the derived tapes are registered"
    assert ctx["notes_cash_report_urls"], "precondition: the NVR is registered (#495)"
    ctx["tape_urls"] = []

    matrix = _matrix(ctx)
    for capability_key in ("tape_analytics", "collateral_reconciliation"):
        cell = _cell(matrix, "cairn-clo-xvii", capability_key)
        assert cell.state == STATE_NOT_APPLICABLE
        assert "No machine-readable ESMA loan tape is registered" in cell.reason
        assert cell.evidence.citation == "Deal registry context: tape_urls is empty."
        assert cell.evidence.detail["tape_count"] == 0

    # The waterfall qualifier does NOT return yet, and that is the point since
    # #495: the Note Valuation Report is a registered ingestible source in its
    # own right, so "no registered tape or Notes & Cash report" would be false
    # of this deal while it is still set. The reason tracks the sources rather
    # than the tape alone.
    waterfall = _cell(matrix, "cairn-clo-xvii", "waterfall_execution")
    assert "no registered tape or Notes & Cash report" not in waterfall.reason

    # Deregister the other source too and it does return — naming the absent
    # source, not the absent configuration.
    ctx["notes_cash_report_urls"] = []
    waterfall = _cell(_matrix(ctx), "cairn-clo-xvii", "waterfall_execution")
    assert "no registered tape or Notes & Cash report" in waterfall.reason


@pytest.mark.parametrize(
    ("revenue", "redemption", "has_pop"),
    [
        (True, True, True),
        (True, False, True),
        (False, True, True),
        (False, False, False),  # a covenants-only key — Cairn's shape until #495
    ],
)
def test_the_two_ground_truth_surfaces_agree_on_which_keys_carry_pop(
    revenue: bool, redemption: bool, has_pop: bool
) -> None:
    """`/capability-matrix` and `/quality-matrix` must not drift apart (#492).

    Both decide "does this key carry Priority-of-Payments ground truth?", but each
    spells the predicate out for itself — ``capability_matrix._has_pop_section``
    mirrors the check inside ``quality_harness._reconcile_deal`` rather than
    importing it. #492 created that second copy, so something has to hold them
    together: a deal graded on its PoP checks but refused by engine validation
    (or the reverse) is the two honesty surfaces telling an operator different
    stories about the same deal.

    Driven off synthetic keys, not the registry: every committed key today carries
    PoP, so a registry-only assertion would pass while the predicates disagreed —
    it can see no counter-example. The covenants-only row is the one that fires.
    """
    from loanwhiz.primitives.quality_harness import _reconcile_deal

    _, committed_series = _committed_key_and_series()
    key = DealAnswerKey(
        deal_id="d",
        deal_name="Synthetic Deal",
        periods=[
            AnswerKeyPeriod(
                reporting_date="2025-03-31",
                period_label="March 2025",
                revenue_pop=(
                    [AnswerKeyPopStep(priority="(a)", amount=1.0)] if revenue else []
                ),
                redemption_pop=(
                    [AnswerKeyPopStep(priority="(a)", amount=1.0)] if redemption else []
                ),
                covenants=[CovenantResult(name="par_coverage", passed=True)],
            )
        ],
    )

    assert _has_pop_section(key) is has_pop

    # The harness's own verdict on the same key, through its real entry point.
    _, _, error = _reconcile_deal(
        "d", {"deal_name": "Synthetic Deal"}, None, key, lambda *_a: committed_series
    )
    harness_saw_pop = error != "answer key carries no Priority-of-Payments to reconcile"
    assert harness_saw_pop is has_pop, error


def test_the_collections_leg_mirror_agrees_with_the_api() -> None:
    """The matrix's collections-leg keys and the endpoint's must not drift.

    ``_collections_leg_gaps`` mirrors ``api.main._collections_tranche_args``
    rather than importing it (the endpoint's version raises an
    ``HTTPException`` and lives a layer up), and the sibling mirror beside it
    has carried a cross-check since it was written for exactly this reason.

    Without this, the leg growing a class would leave the matrix reporting
    ``ran`` for a fold the endpoint answers with a 422 — the #457 overclaim
    arriving *through* the check added to prevent it. Comparing the tuples
    directly is the whole test: there is no behaviour to exercise, only two
    lists that must stay identical.
    """
    from loanwhiz.api.main import _COLLECTIONS_LEG_REQUIRED_KEYS

    from loanwhiz.primitives.capability_matrix import _COLLECTIONS_LEG_KEYS

    assert _COLLECTIONS_LEG_KEYS == _COLLECTIONS_LEG_REQUIRED_KEYS, (
        "the capability matrix's collections-leg mirror has drifted from the "
        "endpoint it mirrors"
    )


def test_missing_structural_config_agrees_with_the_api_resolver() -> None:
    """The matrix's predicate and the endpoint's resolver must not drift.

    ``_missing_structural_config`` mirrors ``_resolve_structural_config`` rather
    than importing it (the resolver raises an ``HTTPException`` and lives a layer
    up). A mirror is only safe while something checks it, so this asserts both
    the verdict and the named keys over every registered deal — including the
    coupon rule, where an extracted ``"3m EURIBOR + 0.43"`` yields no usable
    ``class_a_rate_pct`` and therefore no capital structure.
    """
    from loanwhiz.api.main import _extracted_capital_structure, _resolve_structural_config

    for deal_id, ctx in DEAL_REGISTRY.items():
        model = _load_cached_deal_model(ctx)
        missing = _missing_structural_config(deal_id, ctx, model)
        try:
            _resolve_structural_config(deal_id, dict(ctx))
            resolver_ok = True
        except Exception:  # noqa: BLE001 — the labelled 422 is the signal
            resolver_ok = False
        assert (missing == ()) is resolver_ok, (deal_id, missing)

        if deal_id == "green-lion-2026-1":
            continue
        # ``capital_structure`` resolves from the extracted stack (tier 2), and
        # its senior coupon is a separate tier (#478) — so the key the mirror
        # names for a deal that has its classes but not its rate is the coupon.
        expected: list[str] = []
        for key in ENGINE_STRUCTURAL_CONFIG_KEYS:
            declared = ctx.get(key)
            if key != "capital_structure":
                if declared is None:
                    expected.append(key)
                continue
            structure = (
                declared if declared is not None else _extracted_capital_structure(ctx)
            )
            if structure is None:
                expected.append(key)
                continue
            senior = next(
                (k[: -len("_balance")] for k in structure if k.endswith("_balance")),
                None,
            )
            if senior is None:
                expected.append(key)
            elif (
                structure.get(f"{senior}_rate_pct") is None
                # #614: a committed synthetic fixing resolves the senior coupon,
                # so the key is no longer missing. Mirrored here rather than
                # dropped, because the mirror is what this test exists to check:
                # the resolver gained a tier and the predicate has to gain it too.
                and ctx.get("synthetic_index_fixing") is None
            ):
                expected.append(f"{senior}_rate_pct")
        assert missing == tuple(expected), (deal_id, missing, tuple(expected))


def test_the_clo_column_reports_no_validated_cell() -> None:
    """No answer key is authored, so nothing about this deal may read validated."""
    matrix = _real_matrix()
    clo_cells = [c for c in matrix.cells if c.deal_id == "cairn-clo-xvii"]
    assert clo_cells, "the CLO must have a column at all"
    assert all(c.state != STATE_VALIDATED for c in clo_cells)
    # And every ungraded cell says why, in its own words.
    assert all(
        c.reason.strip() for c in clo_cells if c.state == STATE_NOT_APPLICABLE
    )


def test_waterfall_ran_does_not_contradict_an_endpoint_that_refuses_the_deal() -> None:
    """A `ran` cell must not read as "the endpoints will serve this deal".

    `ran` is a claim about the engine, which executes the extracted steps for any
    deal. The per-deal endpoints need a period source to cold-start from, and a
    deal with neither a registered tape nor a Notes & Cash report gets a labelled
    422 instead. Reporting only the first half left the matrix and the endpoint
    contradicting each other, which is the kind of flattering half-truth the
    honesty contract exists to prevent.

    The qualifier is deliberately one-directional: it fires only where the
    registry proves there is no source, and stays silent otherwise rather than
    claiming the endpoint works (serving also depends on a cached report, which
    the registry does not determine).
    """
    matrix = _real_matrix()
    for column in matrix.deals:
        cell = _cell(matrix, column.deal_id, "waterfall_execution")
        if cell.state != STATE_RAN:
            continue
        has_source = cell.evidence.detail["has_ingestible_source"]
        registry_has_source = bool(
            DEAL_REGISTRY[column.deal_id].get("tape_urls")
            or DEAL_REGISTRY[column.deal_id].get("notes_cash_report_urls")
        )
        assert has_source is registry_has_source
        # Extended for the second way the registry can prove a refusal: a deal
        # may register a period source and still be short of the structural
        # config the reconstruction folds it through. Registering the derived
        # tape moved Cairn from the first refusal to the second, and falling
        # silent there would have read as "solved".
        missing_config = cell.evidence.detail["missing_structural_config"]
        if not has_source:
            assert "per-deal endpoints cannot yet serve this deal" in cell.reason
            assert "no registered tape or Notes & Cash report" in cell.reason
        elif missing_config:
            assert "per-deal endpoints cannot yet serve this deal" in cell.reason
            for key in missing_config:
                assert key in cell.reason
        else:
            assert "cannot yet serve" not in cell.reason


def test_the_clo_waterfall_cell_names_both_halves() -> None:
    """The CLO specifically: the engine runs it, the endpoints do not serve it."""
    cell = _cell(_real_matrix(), "cairn-clo-xvii", "waterfall_execution")
    assert cell.state == STATE_RAN
    assert "executes against period funds" in cell.reason
    assert "per-deal endpoints cannot yet serve this deal" in cell.reason


# ---------------------------------------------------------------------------
# Synthetic pool data must never reach a `validated` cell (#483, epic #482)
# ---------------------------------------------------------------------------
#
# Green Lion 2024-1 is the repo's only `validated` deal, and it is also one of
# the tape-less deals the sibling issue (#484) will give a synthetic pool. So
# "a validated deal owns no synthetic tape" is the wrong invariant — it would
# be false the moment #484 lands, and pinning it would red honest work.
#
# The right one is narrower and survives that: `validated` is reachable only
# through engine validation, whose evidence is the deal's own published Notes &
# Cash Priority of Payments — it never reads `tape_urls`. The two are therefore
# separable. Separable is not separated, so these tests put a synthetic tape on
# the validated deal and assert the separation holds in fact.

_SYNTHETIC_TAPE = {
    "date": "2024-06-30",
    "url": "synthetic:https://example.invalid/green_lion_2024_1_synthetic_pool.csv",
}


def _registry_with_synthetic_tape_on_the_validated_deal() -> dict:
    """The real registry, with a synthetic tape added to green-lion-2024-1."""
    assert "green-lion-2024-1" in DEAL_REGISTRY
    registry = {k: dict(v) for k, v in DEAL_REGISTRY.items()}
    registry["green-lion-2024-1"]["tape_urls"] = [_SYNTHETIC_TAPE]
    return registry


def _matrix_with_synthetic_pool() -> CapabilityMatrix:
    # Wired like `_live_matrix()` above. These six tests arrived from epic #482
    # against the pre-#492 signature (`validators=_VALIDATION_BUILDERS`), which
    # #492 replaced with the answer-key registry on epic #491. Both sides edited
    # different regions of this file, so git merged it cleanly and the
    # composition still raised `NameError` — a merge-semantic break no branch
    # could see alone.
    return build_capability_matrix(
        _registry_with_synthetic_tape_on_the_validated_deal(),
        seed_loader=_load_cached_deal_model,
        answer_key_loader=load_answer_key,
        series_provider=_default_series_provider(),
    )


def test_the_validated_deal_is_still_validated_with_a_synthetic_pool() -> None:
    """A synthetic tape must not disturb a validation it played no part in.

    The guard has to fail in both directions to be worth anything: if adding a
    synthetic tape silently *demoted* the one validated cell, the matrix would
    be lying in the other direction.
    """
    cell = _cell(_matrix_with_synthetic_pool(), "green-lion-2024-1", "engine_validation")
    assert cell.state == STATE_VALIDATED


def test_the_validated_cell_cites_the_pop_report_not_the_tape() -> None:
    """`validated` must rest on the published PoP reconciliation, not the pool.

    This is the assertion that makes the separation real rather than incidental:
    it is not enough that the state stayed `validated`, the *evidence* must be
    free of the synthetic tape. Were engine validation ever re-keyed onto pool
    statistics, the state alone would not notice.
    """
    cell = _cell(_matrix_with_synthetic_pool(), "green-lion-2024-1", "engine_validation")
    rendered = f"{cell.reason} {cell.evidence.citation} {cell.evidence.detail}"
    assert "Notes & Cash" in cell.evidence.citation
    assert _SYNTHETIC_TAPE["url"] not in rendered
    assert "synthetic" not in rendered.lower()


def test_no_tape_consuming_cell_is_validated_for_a_synthetic_pool() -> None:
    """Cells that read `tape_urls` must never reach `validated` off a synthetic pool."""
    matrix = _matrix_with_synthetic_pool()
    for capability_key in ("tape_analytics", "collateral_reconciliation"):
        cell = _cell(matrix, "green-lion-2024-1", capability_key)
        assert cell.state != STATE_VALIDATED, (
            f"{capability_key} reached {STATE_VALIDATED!r} for a deal whose only "
            "pool data is synthetic"
        )


def test_validated_is_reachable_only_through_engine_validation() -> None:
    """The structural reason the two are separable, asserted over the whole matrix.

    Every other capability is either tape-fed or config-fed; none of them has a
    path to `validated` at all. Pinning that here means a future capability that
    grows one has to come past this test.
    """
    for matrix in (_real_matrix(), _matrix_with_synthetic_pool()):
        validated = {c.capability_key for c in matrix.cells if c.state == STATE_VALIDATED}
        assert validated <= {"engine_validation"}


def test_a_synthetic_pool_reads_as_synthetic_in_its_cell() -> None:
    """The matrix reason must disclose synthetic provenance without a document.

    The governance requirement in #483: a reader who never opens the data card
    must still be told. The cell quotes the source kind's own disclosure, so the
    wording cannot drift from the tape citation's.
    """
    cell = _cell(_matrix_with_synthetic_pool(), "green-lion-2024-1", "tape_analytics")
    assert TapeSourceKind.SYNTHETIC_GENERATED.disclosure in cell.reason
    assert cell.evidence.detail["tape_source_kinds"] == {
        TapeSourceKind.SYNTHETIC_GENERATED.value: 1
    }


def test_the_qualifier_never_calls_a_synthetic_tape_an_unpublished_file() -> None:
    """#457 / #471: ban the whole retracted claim, not a fragment of it.

    The qualifier used to read "N of them are not published tape files". That is
    true of a derived tape and false of a synthetic one — a synthetic tape *is*
    a published file; what it is not is a record of anybody's loans. Asserting
    the state alone would pass while the sentence lied, so this asserts the
    retracted wording is absent.
    """
    cell = _cell(_matrix_with_synthetic_pool(), "green-lion-2024-1", "tape_analytics")
    assert "are not published tape files" not in cell.reason.lower()
