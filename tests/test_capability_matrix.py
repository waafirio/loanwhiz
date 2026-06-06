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

import pytest
from fastapi.testclient import TestClient

from loanwhiz.api import app
from loanwhiz.api.main import _load_cached_deal_model, _VALIDATION_BUILDERS
from loanwhiz.config import DEAL_REGISTRY
from loanwhiz.extraction.assembler import DealModel
from loanwhiz.primitives.capability_matrix import (
    STATE_NOT_APPLICABLE,
    STATE_RAN,
    STATE_VALIDATED,
    CapabilityMatrix,
    build_capability_matrix,
    capability_rows,
)

client = TestClient(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _real_matrix() -> CapabilityMatrix:
    """Build the matrix over the real registry + committed seeds + builders."""
    return build_capability_matrix(
        DEAL_REGISTRY,
        seed_loader=_load_cached_deal_model,
        validators=_VALIDATION_BUILDERS,
    )


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


def test_green_lion_2024_1_engine_validation_is_validated_and_unique() -> None:
    matrix = _real_matrix()
    cell = _cell(matrix, "green-lion-2024-1", "engine_validation")
    assert cell.state == STATE_VALIDATED
    assert cell.evidence.detail.get("passed") is True
    assert cell.evidence.detail.get("tolerance_eur") == pytest.approx(0.01)
    # It is the ONLY validated cell — the single externally-reconciled proof.
    validated = [c for c in matrix.cells if c.state == STATE_VALIDATED]
    assert len(validated) == 1
    assert validated[0].deal_id == "green-lion-2024-1"


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


def test_leone_arancio_italian_covenant_runs_tape_and_waterfall_na() -> None:
    matrix = _real_matrix()
    deal_id = "leone-arancio-2023-1"
    # Partial Italian model: real extracted triggers → covenant runs.
    assert _cell(matrix, deal_id, "covenant_monitoring").state == STATE_RAN
    # No waterfall extracted, no tapes → not-applicable, each with a real reason.
    wf = _cell(matrix, deal_id, "waterfall_execution")
    assert wf.state == STATE_NOT_APPLICABLE
    assert "waterfall" in wf.reason.lower()
    tape = _cell(matrix, deal_id, "tape_analytics")
    assert tape.state == STATE_NOT_APPLICABLE
    assert "tape" in tape.reason.lower()


def test_sol_lion_spanish_is_mostly_not_applicable() -> None:
    matrix = _real_matrix()
    deal_id = "sol-lion-ii"
    states = [c.state for c in matrix.cells if c.deal_id == deal_id]
    # Minimal Spanish extraction (0 triggers, no waterfall, no tapes) → all N/A.
    assert all(s == STATE_NOT_APPLICABLE for s in states), states


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
    # A synthetic deal WITH tapes + full model + a validation builder: every
    # capability is live, and engine validation is validated.
    full_model = _fake_model(triggers=3, waterfall_steps=11, completeness=0.9)

    class _Report:
        passed = True
        deal_name = "Fake Deal"
        periods_checked = 2
        periods_passed = 2
        tolerance_eur = 0.01

    deals = {
        "rich": {
            "deal_name": "Fake Deal",
            "jurisdiction": "Atlantis",
            "tape_urls": [{"url": "u1"}, {"url": "u2"}],
        },
        "bare": {"deal_name": "Empty Deal", "tape_urls": []},
    }

    def loader(ctx):
        return full_model if ctx.get("tape_urls") else None

    matrix = build_capability_matrix(
        deals,
        seed_loader=loader,
        validators={"rich": lambda: _Report()},
    )

    # Rich deal: everything runs; engine validation is validated.
    rich = {c.capability_key: c for c in matrix.cells if c.deal_id == "rich"}
    assert rich["tape_analytics"].state == STATE_RAN
    assert rich["covenant_monitoring"].state == STATE_RAN
    assert rich["waterfall_execution"].state == STATE_RAN
    assert rich["collateral_reconciliation"].state == STATE_RAN
    assert rich["engine_validation"].state == STATE_VALIDATED

    # Bare deal: no model, no tapes, no builder → all not-applicable with reasons.
    bare = {c.capability_key: c for c in matrix.cells if c.deal_id == "bare"}
    assert all(c.state == STATE_NOT_APPLICABLE for c in bare.values())
    assert all(c.reason.strip() for c in bare.values())

    # Jurisdiction default applies only when the key is absent.
    juris = {d.deal_id: d.jurisdiction for d in matrix.deals}
    assert juris["rich"] == "Atlantis"
    assert juris["bare"] == "Netherlands"


def test_runner_validated_requires_passing_builder() -> None:
    # A builder that does NOT pass must not produce a validated cell.
    full_model = _fake_model(triggers=1, waterfall_steps=4, completeness=0.5)

    class _Failing:
        passed = False
        deal_name = "Fake Deal"
        periods_checked = 1
        periods_passed = 0
        tolerance_eur = 0.01

    matrix = build_capability_matrix(
        {"d": {"deal_name": "Fake Deal", "tape_urls": []}},
        seed_loader=lambda ctx: full_model,
        validators={"d": lambda: _Failing()},
    )
    cell = next(c for c in matrix.cells if c.capability_key == "engine_validation")
    assert cell.state == STATE_RAN  # ran but did not reconcile → not validated
    assert cell.state != STATE_VALIDATED


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
    assert body["tally"]["validated"] == 1
    assert body["tally"]["not-applicable"] > body["tally"]["validated"]
    # Every cell over the wire carries a non-empty reason.
    assert all(c["reason"].strip() for c in body["cells"])
