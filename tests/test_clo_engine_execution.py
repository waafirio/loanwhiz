"""The CLO executes through the engine — and degrades honestly where it cannot (#457).

This is the closing child of the CLO structural spine (#454). #456 extracted
Cairn CLO XVII DAC into a committed seed model; this module folds that model
through the **existing** ``run_period`` kernel and pins what actually happens.

What is proved here, precisely
------------------------------
The engine kernel executes a non-RMBS deal with **no new execution path**. Every
seam used below already existed and is the same one the RMBS deals use:

- :func:`loanwhiz.api.main._load_cached_deal_model` — committed seed loader
- :func:`loanwhiz.api.main._run_period_step_kwargs` — extracted waterfalls → ``StepSpec``
- :func:`loanwhiz.api.main._extracted_triggers_to_definitions` — extracted triggers
- :meth:`loanwhiz.primitives.deal_state.DealState.seed_from_prospectus` — opening state
- :func:`loanwhiz.primitives.period_state_machine.run_period` — the fold itself

``run_period`` already accepts ``triggers=`` / ``revenue_steps=`` /
``redemption_steps=``, so wiring a CLO through it is parameterisation, not a new
path. Nothing in this module is CLO-specific machinery; it is CLO-specific *data*
driving deal-agnostic code.

What is **not** proved here — read this before quoting the module
----------------------------------------------------------------
- **This deal is not validated, and no cell reads ``validated``.** Its published
  reports *are* obtainable (see ``docs/data-card.md``); what is missing is an
  authored answer key. Unvalidated for want of a key, never for want of a report.
  Authoring one is a deferred operator decision and is deliberately not done here.
- **This is the engine kernel, not the ingestion path.** The production
  ``_reconstruct_series`` still refuses this deal with a labelled 422 — it has no
  ESMA tape and no ``notes_cash_report_urls``, and that refusal is pinned by
  ``test_clo_deal_registration``. Giving it one would mean a *third* ingestion
  adapter, which is exactly the new execution path #457 was told to surface
  rather than build. The seed below is therefore built by this test from the
  committed capital structure, not served by a production adapter.
- **No number below is a published figure.** They are engine outputs on
  synthetic period inputs, used to prove execution and honest refusal.
"""

from __future__ import annotations

import re

import pytest

from loanwhiz.api.main import (
    _extracted_triggers_to_definitions,
    _load_cached_deal_model,
    _run_period_step_kwargs,
)
from loanwhiz.config import DEAL_REGISTRY
from loanwhiz.domain.inputs import PeriodInputs
from loanwhiz.primitives.deal_state import DealState
from loanwhiz.primitives.period_state_machine import run_period

CLO_DEAL_ID = "cairn-clo-xvii"

#: Synthetic period funds. Deliberately round numbers that could not be mistaken
#: for published figures — this fixture proves the fold runs, not what it earned.
_SYNTHETIC_REVENUE = 8_000_000.0
_SYNTHETIC_PRINCIPAL = 2_000_000.0


def _tranche_key(name: str) -> str:
    """``"Class B-1"`` → ``"class_b_1"`` — the engine's tranche-name spelling."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


@pytest.fixture(scope="module")
def clo_deal() -> dict:
    """The registered CLO's context with its committed seed model attached."""
    deal = dict(DEAL_REGISTRY[CLO_DEAL_ID])
    model = _load_cached_deal_model(deal)
    assert model is not None, "#456 committed this seed; without it nothing here is meaningful"
    deal["_model"] = model
    return deal


def _capital_structure(model, *, include_equity: bool = True) -> dict[str, float]:
    """Opening balances from the seed's extracted capital structure."""
    return {
        f"{_tranche_key(t['name'])}_balance": float(t["size_eur"])
        for t in model.tranche_structure
        if include_equity or "subordinated" not in _tranche_key(t["name"])
    }


def _fold_one_period(deal: dict, *, include_equity: bool = True):
    """Fold one period of the CLO through ``run_period`` using only existing seams."""
    model = deal["_model"]
    structure = _capital_structure(model, include_equity=include_equity)
    total_notes = sum(float(t["size_eur"]) for t in model.tranche_structure)

    opening = DealState.seed_from_prospectus(
        structure,
        reserve_target=0.0,
        original_pool_balance=total_notes,
        opening_pool_balance=total_notes,
        reporting_date="2025-03-18",
    )
    period = PeriodInputs(
        reporting_date="2025-06-18",
        days_in_period=92,
        available_revenue=_SYNTHETIC_REVENUE,
        available_principal=_SYNTHETIC_PRINCIPAL,
        realized_loss=0.0,
        source="report",
    )
    return run_period(
        opening,
        period,
        rates={},
        triggers=_extracted_triggers_to_definitions(deal),
        principal_classes=tuple(
            k.removesuffix("_balance") for k in structure if "subordinated" not in k
        ),
        **_run_period_step_kwargs(deal),
    )


# ---------------------------------------------------------------------------
# The deal executes.
# ---------------------------------------------------------------------------


class TestTheCloExecutes:
    def test_the_full_capital_structure_seeds_a_deal_state(self, clo_deal):
        """All eight classes seed — including the two B tranches and the equity."""
        model = clo_deal["_model"]
        opening = DealState.seed_from_prospectus(
            _capital_structure(model),
            reserve_target=0.0,
            original_pool_balance=404_100_000.0,
            opening_pool_balance=404_100_000.0,
            reporting_date="2025-03-18",
        )
        names = {t.name for t in opening.tranches}
        assert names == {
            "class_a",
            "class_b_1",
            "class_b_2",
            "class_c",
            "class_d",
            "class_e",
            "class_f",
            "subordinated_notes",
        }
        # The stack ties to the extracted total; a silently dropped tranche would
        # shrink this and, being a *denominator*, would read as extra coverage.
        assert sum(t.balance for t in opening.tranches) == pytest.approx(404_100_000.0)

    def test_both_extracted_cascades_execute_through_run_period(self, clo_deal):
        """The deal's own 29-step interest and 23-step principal waterfalls run."""
        result = _fold_one_period(clo_deal)
        revenue_steps = _run_period_step_kwargs(clo_deal)["revenue_steps"]
        redemption_steps = _run_period_step_kwargs(clo_deal)["redemption_steps"]

        # Every extracted step is executed and traced — no cascade is truncated.
        assert len(result.revenue_execution.steps) == len(revenue_steps)
        assert len(result.redemption_execution.steps) == len(redemption_steps)
        assert result.closing_state.pool_balance >= 0.0

    def test_the_fold_uses_the_deals_own_waterfalls_not_the_rmbs_defaults(self, clo_deal):
        """A CLO folded with Green Lion's default steps would be a silent lie."""
        from loanwhiz.primitives.period_state_machine import (
            DEFAULT_REDEMPTION_STEPS,
            DEFAULT_REVENUE_STEPS,
        )

        kwargs = _run_period_step_kwargs(clo_deal)
        assert kwargs["revenue_steps"] is not DEFAULT_REVENUE_STEPS
        assert kwargs["redemption_steps"] is not DEFAULT_REDEMPTION_STEPS
        assert len(kwargs["revenue_steps"]) > len(DEFAULT_REVENUE_STEPS)


# ---------------------------------------------------------------------------
# ...and degrades honestly.
# ---------------------------------------------------------------------------


class TestHonestDegradation:
    def test_no_trigger_ever_reports_as_satisfied_or_breached(self, clo_deal):
        """The #193 bar: an ungradeable test must never resolve to a verdict.

        Every one of this deal's triggers is currently not-evaluable. The failure
        this guards is not a red test — it is a *green* one: a coverage test that
        quietly reports ``is_triggered=False`` reads as "the deal passes", which
        is the falsely-healthy direction #452 warns about.
        """
        statuses = _fold_one_period(clo_deal).trigger_evaluation.statuses
        assert statuses, "the extracted triggers must actually reach the evaluator"
        for name, status in statuses.items():
            assert not status.evaluable, f"{name} became evaluable — re-check the reason below"
            assert not status.is_triggered, f"{name} reported a verdict it cannot support"

    def test_every_not_evaluable_trigger_carries_a_real_reason(self, clo_deal):
        """A refusal with no reason is the silent blank the honesty contract bans."""
        statuses = _fold_one_period(clo_deal).trigger_evaluation.statuses
        for name, status in statuses.items():
            reason = status.not_evaluable_reason
            assert reason and reason.strip(), f"{name} refused without saying why"
            # A reason that does not name the metric is not traceable to a cause.
            assert status.trigger_name == name

    def test_every_extracted_trigger_is_cited_to_the_prospectus(self, clo_deal):
        """Governance: no trigger reaches the engine without a source citation."""
        for definition in _extracted_triggers_to_definitions(clo_deal):
            citation = definition.citation
            assert citation is not None, f"{definition.name} carries no citation"
            assert citation.document.strip(), f"{definition.name} cites no document"
            assert citation.excerpt.strip(), f"{definition.name} cites no excerpt"


class TestCoverageTestsResolveOntoTheStructuralMetrics:
    """The 8 per-class coverage tests reach #452's OC/IC metrics, not a fallback."""

    def test_coverage_metric_names_map_onto_the_oc_ic_vocabulary(self, clo_deal):
        from loanwhiz.primitives.covenant_monitor import (
            _canonical_metric,
            _COVERAGE_METRIC_RE,
        )

        resolved = {
            definition.metric: _canonical_metric(definition.metric)
            for definition in _extracted_triggers_to_definitions(clo_deal)
        }
        coverage = {
            raw: canonical
            for raw, canonical in resolved.items()
            if canonical and _COVERAGE_METRIC_RE.match(canonical)
        }
        # The deal's own CLO spelling ("par value test") resolves onto the
        # engine's structural OC/IC vocabulary via #452's alias map — without it
        # these would fall through to an unresolved metric and lose their meaning.
        assert coverage["class_a_b_par_value_ratio"] == "class_b_oc_ratio"
        assert coverage["class_a_b_interest_coverage_ratio"] == "class_b_ic_ratio"
        assert len(coverage) == 8

    def test_coverage_tests_refuse_at_the_placement_layer_on_the_real_stack(self, clo_deal):
        """The *first* refusal is #452's tranche placement, not the missing threshold.

        ``docs/data-card.md`` records that the coverage thresholds sit past the
        definitions extractor's budget, so the tests cannot be quantified. True —
        but on the real eight-class stack that is not the refusal that fires. The
        equity tranche carries no class letter, so #452 refuses the whole metric
        first (correctly: it will not narrow a denominator it cannot place).
        Pinned because the two reasons are separately fixable, and reporting the
        wrong one misdescribes what this deal needs.
        """
        statuses = _fold_one_period(clo_deal).trigger_evaluation.statuses
        oc = statuses["class_d_par_value_test"]
        assert oc.metric_value is None
        assert "subordinated_notes" in (oc.not_evaluable_reason or "")

    def test_without_the_equity_tranche_the_oc_ladder_computes_then_refuses(self, clo_deal):
        """Counterfactual — evidence the metric itself is sound on a CLO stack.

        Not the deal's real structure: the equity tranche is removed so the
        placement refusal above does not fire. It is junior to every attachment
        point, so it enters no coverage denominator and removing it changes no
        ratio — which is what makes this a clean probe of the *next* layer.

        The result is a correctly-ordered OC ladder that falls with seniority,
        and every test then refuses again for the documented reason: no threshold
        was captured. A quantified pass here would be the wall of green #457 was
        told is worse than an empty column.
        """
        statuses = _fold_one_period(clo_deal, include_equity=False).trigger_evaluation.statuses
        ladder = [
            statuses[f"class_{letter}_par_value_test"].metric_value
            for letter in ("c", "d", "e", "f")
        ]
        assert all(value is not None for value in ladder)
        assert ladder == sorted(ladder, reverse=True), "OC must fall as attachment deepens"
        for letter in ("c", "d", "e", "f"):
            status = statuses[f"class_{letter}_par_value_test"]
            assert not status.evaluable and not status.is_triggered
            assert "no quantified threshold" in (status.not_evaluable_reason or "")
