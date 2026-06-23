"""Cross-jurisdiction cold-start validation — IT + ES through the generalised
engine (#369, the final child of epic #361 "IT/ES extraction reality").

The epic's prereqs are merged into this branch: the Italian (#367) and Spanish
(#368) seeds are honestly extracted, and (via promoted epic #360) the engine is
the tranche-general ``waterfall_interpreter.interpret`` driven by canonical
``StepSpec`` objects + the canonical tape/report adapters. This module is the
final proof: each non-Dutch deal **cold-starts end-to-end through the
generalised engine from its own committed extracted model**, and the test pins
*both* halves honestly — what genuinely works, AND what is genuinely
not-modelable (the #193 honesty discipline this whole epic exists to protect).

What works (the cross-jurisdiction headline):
  * Every extracted waterfall step in each deal's *own* seed builds into a
    canonical :class:`StepSpec` via :meth:`StepSpec.from_extracted` and executes
    through the SAME deal-agnostic :func:`interpret` kernel the Dutch deals use —
    Italian 23/5/12 (revenue/redemption/post-enforcement) and Spanish 8/7
    (redemption/post-enforcement) — producing a deterministic 1:1 audit trace
    with the extracted ordering / conditions / pari-passu preserved.

What is genuinely NOT cold-start-modelable (asserted as honest, reasoned facts,
never papered over):
  * Every IT/ES extracted recipient uses a *jurisdiction-native* label
    (``class_a_notes_interest``, ``series_a1_notes_redemption``, ``expenses``, …)
    that the NL-derived canonical need-calculator registry does not resolve, so
    every step is recorded ``not_evaluable`` and nothing is distributed. The
    *numeric* distribution cannot cold-start without a recipient→canonical
    mapping the extracted seeds do not carry — the engine surfaces this honestly
    rather than fabricating amounts.
  * The Spanish revenue priority-of-payments has **0 enumerable steps** (the
    prospectus income section yielded none) — an empty trace, asserted as the
    honest fact it is.
  * Neither deal has a loan tape or a Notes & Cash report, so the multi-period
    folded ledger (``/deal/{id}/waterfall`` etc.) is genuinely unavailable — the
    API returns the labelled ``_not_modelable_deal`` 422, not an empty cascade.

Runs fully offline over the *real* shipped ``DEAL_REGISTRY`` + committed seeds,
so a regression in a seed, the engine, or the adapter is caught here. The engine
(``waterfall_interpreter`` / ``period_state_machine``) is exercised UNMODIFIED —
the headline is that the generalised code ingests jurisdiction-native extracted
models with no per-deal special-casing.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from loanwhiz.api import app
from loanwhiz.api.main import _load_cached_deal_model
from loanwhiz.config import DEAL_REGISTRY
from loanwhiz.primitives.waterfall_interpreter import (
    NEED_CALCULATORS,
    StepSpec,
    WaterfallExecution,
    WaterfallFunds,
    compute_need,
    interpret,
)

client = TestClient(app)

# The two non-Dutch deals and their jurisdiction, plus the per-section extracted
# step counts the committed seeds carry today. These counts ARE the contract:
# they encode the honest extraction reality (IT richer than ES; ES has no
# enumerable revenue PoP), so a re-seed that silently inflated or deleted steps
# trips this test.
_COLD_START_DEALS = {
    "leone-arancio-2023-1": {
        "jurisdiction": "Italy",
        "revenue": 23,
        "redemption": 5,
        "post_enforcement": 12,
    },
    "sol-lion-ii": {
        "jurisdiction": "Spain",
        "revenue": 0,  # honest: no enumerable revenue-PoP steps extracted
        "redemption": 8,
        "post_enforcement": 7,
    },
}

_WATERFALL_SECTIONS = ("revenue", "redemption", "post_enforcement")

# A funds context with non-zero pots so that, IF any recipient resolved to a
# need-calculator, it WOULD distribute — making the "everything is not_evaluable"
# assertion meaningful (it is not an artefact of an empty pot). Deal-generic; not
# a claim about either deal's real cash.
_PROBE_FUNDS = WaterfallFunds(
    available_revenue_funds=10_000_000.0,
    available_principal_funds=10_000_000.0,
    class_a_balance=100_000_000.0,
    class_a_rate_pct=1.0,
    reserve_target=1_000_000.0,
    days_in_period=90,
)


# ---------------------------------------------------------------------------
# Helpers — the cold-start drive: extracted model → canonical StepSpec → engine
# ---------------------------------------------------------------------------


def _model_for(deal_id: str):
    """Load the deal's committed extracted model via the real API loader."""
    model = _load_cached_deal_model(dict(DEAL_REGISTRY[deal_id]))
    assert model is not None, f"no committed seed model for {deal_id}"
    return model


def _extracted_steps(model, section: str) -> list[dict]:
    """The raw extracted step dicts for one waterfall section (or [] if absent)."""
    wf = model.waterfalls.get(section)
    if not isinstance(wf, dict):
        return []
    return list(wf.get("steps", []))


def _cold_start_section(model, section: str) -> tuple[list[StepSpec], WaterfallExecution]:
    """Cold-start one waterfall section through the generalised engine.

    Builds canonical :class:`StepSpec` objects from the deal's *own* extracted
    step dicts and runs them through the unmodified :func:`interpret` kernel —
    exactly the model-driven path epic #360 generalised. Returns the built specs
    and the resulting governed :class:`WaterfallExecution` trace.
    """
    raw = _extracted_steps(model, section)
    specs = [StepSpec.from_extracted(step) for step in raw]
    pot = (
        _PROBE_FUNDS.available_revenue_funds
        if section == "revenue"
        else _PROBE_FUNDS.available_principal_funds
    )
    execution = interpret(specs, _PROBE_FUNDS, available=pot)
    return specs, execution


# ---------------------------------------------------------------------------
# Coverage — both non-Dutch deals are present and resolve their jurisdiction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("deal_id,spec", _COLD_START_DEALS.items())
def test_cold_start_deal_resolves_with_jurisdiction(deal_id: str, spec: dict) -> None:
    assert deal_id in DEAL_REGISTRY
    assert DEAL_REGISTRY[deal_id]["jurisdiction"] == spec["jurisdiction"]
    # Its committed extracted model loads via the real API loader (no network).
    assert _model_for(deal_id) is not None


def test_cold_start_spans_italy_and_spain() -> None:
    juris = {DEAL_REGISTRY[d]["jurisdiction"] for d in _COLD_START_DEALS}
    assert juris == {"Italy", "Spain"}


# ---------------------------------------------------------------------------
# What works — extracted steps build + execute through the generalised engine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("deal_id,spec", _COLD_START_DEALS.items())
def test_extracted_steps_build_into_canonical_specs(deal_id: str, spec: dict) -> None:
    """Each section's extracted steps build into canonical StepSpec objects.

    This is the schema cold-start: a jurisdiction-native extracted waterfall maps
    onto the deal-agnostic StepSpec the engine consumes, with the extracted
    priority / recipient / condition preserved.
    """
    model = _model_for(deal_id)
    for section in _WATERFALL_SECTIONS:
        raw = _extracted_steps(model, section)
        assert len(raw) == spec[section], (
            f"{deal_id}/{section}: extracted step count drifted from the seed"
        )
        specs = [StepSpec.from_extracted(step) for step in raw]
        assert len(specs) == len(raw)
        for built, src in zip(specs, raw):
            assert built.priority == str(src.get("priority", ""))
            assert built.recipient == str(src.get("recipient", ""))


@pytest.mark.parametrize("deal_id,spec", _COLD_START_DEALS.items())
def test_extracted_waterfall_executes_through_generalised_engine(
    deal_id: str, spec: dict
) -> None:
    """The cross-jurisdiction headline: the SAME ``interpret`` kernel that runs
    the Dutch deals also runs each non-Dutch deal's own extracted waterfalls.

    For each section the extracted steps drive ``interpret`` to a deterministic,
    governed 1:1 audit trace (one StepResult per extracted step), with the
    extracted ordering preserved. ES's empty revenue PoP yields an empty trace —
    the honest 0-step fact, not an error.
    """
    model = _model_for(deal_id)
    for section in _WATERFALL_SECTIONS:
        specs, execution = _cold_start_section(model, section)
        # 1:1 trace with the extracted steps, ordering preserved.
        assert len(execution.steps) == len(specs) == spec[section]
        assert [s.recipient for s in execution.steps] == [s.recipient for s in specs]
        # Deterministic: a second run produces the identical trace.
        _, again = _cold_start_section(model, section)
        assert [s.model_dump() for s in again.steps] == [
            s.model_dump() for s in execution.steps
        ]


def test_spanish_revenue_pop_is_honestly_empty() -> None:
    """ES has 0 enumerable revenue-PoP steps — an honest empty trace, not a fail.

    The Spanish prospectus income section yielded no enumerable steps. We assert
    the empty fact directly so a future re-seed that silently fabricated revenue
    steps would trip here (the #193 honesty guard).
    """
    model = _model_for("sol-lion-ii")
    assert _extracted_steps(model, "revenue") == []
    specs, execution = _cold_start_section(model, "revenue")
    assert specs == []
    assert execution.steps == []
    assert execution.total_distributed == 0.0


def test_italian_waterfall_is_the_richest_cold_start() -> None:
    """IT carries the fullest extracted cascade — all three sections non-empty.

    Pins the honest asymmetry the seeds encode: the Italian extraction is richer
    than the Spanish one (which has no revenue PoP), and the engine cold-starts
    all of it.
    """
    model = _model_for("leone-arancio-2023-1")
    for section in _WATERFALL_SECTIONS:
        specs, execution = _cold_start_section(model, section)
        assert len(execution.steps) == len(specs) > 0


# ---------------------------------------------------------------------------
# What is honestly NOT cold-start-modelable — no wall of green
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("deal_id,spec", _COLD_START_DEALS.items())
def test_numeric_distribution_is_honestly_not_modelable(
    deal_id: str, spec: dict
) -> None:
    """Every IT/ES step runs but is ``not_evaluable`` — numeric distribution is
    not cold-start-modelable, and the engine says so rather than fabricating it.

    The extracted recipients are jurisdiction-native labels with no entry in the
    NL-derived ``NEED_CALCULATORS`` registry. The engine resolves a 0 need with
    ``evaluable=False`` for each (``compute_need``), records the step
    ``not_evaluable``, and distributes nothing — the honest cold-start boundary.
    A probe pot is funded, so "nothing distributed" is a real not-evaluable
    result, not an empty-pot artefact.
    """
    model = _model_for(deal_id)
    total_steps = 0
    for section in _WATERFALL_SECTIONS:
        specs, execution = _cold_start_section(model, section)
        total_steps += len(execution.steps)
        for step_result, src_spec in zip(execution.steps, specs):
            assert step_result.not_evaluable is True, (
                f"{deal_id}/{section}: recipient {src_spec.recipient!r} unexpectedly "
                f"resolved a canonical need-calculator"
            )
            # The honest *reason*: the recipient is not in the canonical registry.
            assert src_spec.recipient not in NEED_CALCULATORS
            need, evaluable = compute_need(src_spec.recipient, _PROBE_FUNDS)
            assert evaluable is False and need == 0.0
            assert step_result.amount_distributed == 0.0
        assert execution.total_distributed == 0.0
    # Sanity: we actually exercised steps (so the assertion isn't vacuous for IT;
    # ES's revenue section is legitimately empty but redemption/post are not).
    assert total_steps == spec["revenue"] + spec["redemption"] + spec["post_enforcement"]


def test_no_it_es_recipient_resolves_a_canonical_calculator() -> None:
    """Belt-and-braces: across BOTH deals and ALL sections, not one extracted
    recipient matches a canonical need-calculator key.

    This is the single fact behind the "numeric distribution not modelable"
    story — pinned once, deal- and section-agnostic, so the honest boundary
    can't silently erode (e.g. a future canonical-recipient rename that
    accidentally started resolving a native label).
    """
    resolved: list[str] = []
    for deal_id in _COLD_START_DEALS:
        model = _model_for(deal_id)
        for section in _WATERFALL_SECTIONS:
            for step in _extracted_steps(model, section):
                recipient = str(step.get("recipient", ""))
                if recipient in NEED_CALCULATORS:
                    resolved.append(f"{deal_id}/{section}:{recipient}")
    assert resolved == [], (
        "an IT/ES extracted recipient now resolves a canonical need-calculator — "
        f"the cold-start numeric boundary changed: {resolved}"
    )


# ---------------------------------------------------------------------------
# Honest serving boundary — the multi-period ledger is genuinely unavailable
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("deal_id", _COLD_START_DEALS)
@pytest.mark.parametrize("endpoint", ["waterfall", "reconciliation"])
def test_multi_period_ledger_is_honest_not_modelable_422(
    deal_id: str, endpoint: str
) -> None:
    """``/deal/{id}/{waterfall,reconciliation}`` returns the labelled 422.

    Neither non-Dutch deal has a loan tape or a Notes & Cash report, so the
    folded multi-period ``DealStateSeries`` cannot be cold-started (the "+report
    where applicable" clause does NOT apply to IT/ES). The API degrades honestly
    with ``_not_modelable_deal`` — a 422 that names the deal and the reason —
    rather than serving an empty cascade that would read as an all-clear result.
    """
    resp = client.get(f"/deal/{deal_id}/{endpoint}")
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "not modelable" in detail.lower()
    assert deal_id in detail
