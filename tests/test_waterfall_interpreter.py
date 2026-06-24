"""Tests for the model-driven waterfall interpreter (S4 / #184).

Covers the reusable, deal-agnostic core:

- step ordering (senior steps consume funds before junior steps see them),
- condition gating (a false predicate suppresses a step; a true one pays it),
- pari-passu groups (pro-rata shortfall split; full-pay when funds suffice),
- the sequential-pay branch (sequential ↔ pro-rata principal allocation),
- the injected ``ConditionEvaluator`` seam (S5 / #185 composition),
- the ``to_waterfall_result()`` round-trip through the S1 ``WaterfallResult``
  consumed by ``DealState.apply_waterfall_result``.
"""

from __future__ import annotations

import math

import pytest

from loanwhiz.primitives.deal_state import DealState, WaterfallResult
from loanwhiz.primitives.waterfall_interpreter import (
    ConditionEvaluator,
    DefaultConditionEvaluator,
    StepSpec,
    TrancheFunds,
    WaterfallFunds,
    allocate_principal,
    compute_need,
    interpret,
    to_waterfall_result,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _funds(**overrides) -> WaterfallFunds:
    defaults = dict(
        available_revenue_funds=10_000_000.0,
        available_principal_funds=5_000_000.0,
        senior_fees=50_000.0,
        swap_payment=0.0,
        class_a_balance=1_000_000_000.0,
        class_a_rate_pct=3.62,
        class_b_balance=53_100_000.0,
        class_c_balance=10_500_000.0,
        days_in_period=90,
    )
    defaults.update(overrides)
    return WaterfallFunds(**defaults)


# ---------------------------------------------------------------------------
# Need-calculator registry
# ---------------------------------------------------------------------------


class TestNeedCalculators:
    def test_class_a_interest_act_360(self):
        funds = _funds()
        need, evaluable = compute_need("class_a_interest", funds)
        expected = 1_000_000_000.0 * 0.0362 / 360.0 * 90
        assert evaluable
        assert math.isclose(need, expected, rel_tol=1e-9)

    def test_reserve_replenishment_is_target_minus_balance(self):
        funds = _funds(reserve_balance=4_000_000.0, reserve_target=5_000_000.0)
        need, evaluable = compute_need("reserve_replenishment", funds)
        assert evaluable
        assert math.isclose(need, 1_000_000.0, rel_tol=1e-9)

    def test_unknown_recipient_is_not_evaluable(self):
        need, evaluable = compute_need("totally_unknown_recipient", _funds())
        assert need == 0.0
        assert evaluable is False


# ---------------------------------------------------------------------------
# Step ordering
# ---------------------------------------------------------------------------


class TestStepOrdering:
    def test_senior_step_consumes_funds_before_junior(self):
        """A senior step consumes the pot before a junior step sees the rest."""
        funds = _funds(available_revenue_funds=100_000.0, senior_fees=70_000.0)
        steps = [
            StepSpec(priority="(a)", recipient="senior_fees"),
            StepSpec(priority="(d)", recipient="class_a_interest"),
        ]
        ex = interpret(steps, funds, available=funds.available_revenue_funds)
        senior = ex.steps[0]
        junior = ex.steps[1]
        assert senior.amount_distributed == 70_000.0
        # Junior step sees only the €30k remaining → big shortfall on interest.
        assert junior.amount_available == pytest.approx(30_000.0)
        assert junior.amount_distributed == pytest.approx(30_000.0)
        assert junior.shortfall > 0.0

    def test_trace_is_one_to_one_with_input_steps(self):
        funds = _funds()
        steps = [
            StepSpec(priority="(a)", recipient="senior_fees"),
            StepSpec(priority="(d)", recipient="class_a_interest"),
            StepSpec(priority="(e)", recipient="class_a_pdl_replenishment"),
        ]
        ex = interpret(steps, funds, available=funds.available_revenue_funds)
        assert [s.priority for s in ex.steps] == ["(a)", "(d)", "(e)"]


# ---------------------------------------------------------------------------
# Condition gating
# ---------------------------------------------------------------------------


class TestConditionGating:
    def test_unconditional_step_pays(self):
        funds = _funds()
        steps = [StepSpec(priority="(a)", recipient="senior_fees")]
        ex = interpret(steps, funds, available=1_000_000.0)
        assert ex.steps[0].amount_distributed == 50_000.0
        assert ex.steps[0].gated is False

    def test_false_predicate_gates_step_to_zero(self):
        """A step whose condition predicate is False pays 0 and is marked gated."""

        class AlwaysFalse:
            def evaluate(self, condition, funds):
                return False

            def sequential_pay_active(self, funds):
                return True

        funds = _funds()
        steps = [
            StepSpec(priority="(a)", recipient="senior_fees", condition="if X"),
            StepSpec(priority="(d)", recipient="class_a_interest"),
        ]
        ex = interpret(steps, funds, available=10_000_000.0, evaluator=AlwaysFalse())
        gated = ex.steps[0]
        assert gated.gated is True
        assert gated.amount_distributed == 0.0
        # The gated step did NOT consume funds → the next step sees the full pot.
        assert ex.steps[1].amount_available == pytest.approx(10_000_000.0)

    def test_true_predicate_pays_conditional_step(self):
        class AlwaysTrue:
            def evaluate(self, condition, funds):
                return True

            def sequential_pay_active(self, funds):
                return True

        funds = _funds()
        steps = [
            StepSpec(priority="(a)", recipient="senior_fees", condition="if X")
        ]
        ex = interpret(steps, funds, available=1_000_000.0, evaluator=AlwaysTrue())
        assert ex.steps[0].gated is False
        assert ex.steps[0].amount_distributed == 50_000.0


class TestDefaultConditionEvaluator:
    def test_empty_condition_is_unconditional(self):
        ev = DefaultConditionEvaluator()
        assert ev.evaluate("", _funds()) is True

    def test_sequential_pay_negated_condition(self):
        ev = DefaultConditionEvaluator()
        active = _funds(sequential_pay=True)
        inactive = _funds(sequential_pay=False)
        # "if Sequential Pay Trigger is not in effect" → pay only when inactive.
        cond = "if the Sequential Pay Trigger is not in effect"
        assert ev.evaluate(cond, inactive) is True
        assert ev.evaluate(cond, active) is False

    def test_flag_condition(self):
        ev = DefaultConditionEvaluator()
        cond = "during Revolving Period"
        on = _funds(flags={"revolving period": True})
        off = _funds(flags={"revolving period": False})
        assert ev.evaluate(cond, on) is True
        assert ev.evaluate(cond, off) is False

    def test_sequential_pay_unknown_defaults_active(self):
        ev = DefaultConditionEvaluator()
        assert ev.sequential_pay_active(_funds(sequential_pay=None)) is True

    def test_default_evaluator_satisfies_protocol(self):
        assert isinstance(DefaultConditionEvaluator(), ConditionEvaluator)


class TestConditionTermsLinking:
    """The #395 definitions link: ``StepSpec.condition_terms`` + the evaluator's
    consumption of it.

    Before the link, a conditional step whose raw prose the lexical
    ``_SEQ_MARKERS`` did not catch fell through to the evaluator's
    "unknown condition → pay" default. The linked defined-term name now lets the
    evaluator recognise the Sequential Pay Trigger even when the prose phrases it
    differently.
    """

    def test_from_extracted_populates_condition_terms(self):
        spec = StepSpec.from_extracted(
            {
                "priority": "(g)",
                "recipient": "class_b_interest",
                "condition": "while the trigger is in effect",
                "condition_terms": ["Sequential Pay Trigger"],
            }
        )
        assert spec.condition_terms == ["Sequential Pay Trigger"]

    def test_from_extracted_defaults_condition_terms_empty(self):
        spec = StepSpec.from_extracted(
            {"priority": "(a)", "recipient": "senior_fees", "condition": "if X"}
        )
        assert spec.condition_terms == []

    def test_from_extracted_accepts_dict_shaped_terms(self):
        """Tolerant of a list of {"term": ...} dicts as well as bare names."""
        spec = StepSpec.from_extracted(
            {
                "priority": "(g)",
                "recipient": "class_b_interest",
                "condition": "x",
                "condition_terms": [{"term": "Sequential Pay Trigger"}],
            }
        )
        assert spec.condition_terms == ["Sequential Pay Trigger"]

    def test_linked_term_gates_when_prose_does_not_match(self):
        """The core #395 fix.

        The raw prose ("while the trigger is in effect") contains none of the
        lexical sequential-pay markers, so without the link the evaluator would
        fall through to "unknown → pay". The linked ``condition_terms`` name the
        Sequential Pay Trigger, so the evaluator now gates on its state.
        """
        ev = DefaultConditionEvaluator()
        cond = "while the trigger is in effect"
        terms = ["Sequential Pay Trigger"]
        # No link → falls through to the "unknown → pay" default.
        assert ev.evaluate(cond, _funds(sequential_pay=True)) is True
        assert ev.evaluate(cond, _funds(sequential_pay=False)) is True
        # Linked → gates on the Sequential Pay Trigger state.
        assert ev.evaluate(cond, _funds(sequential_pay=True), terms) is True
        assert ev.evaluate(cond, _funds(sequential_pay=False), terms) is False

    def test_linked_condition_gates_through_interpret(self):
        """End-to-end through ``interpret`` with the real DefaultConditionEvaluator.

        A linked conditional step suppresses (pays 0, gated) when its Sequential
        Pay Trigger is inactive, and pays when active — driven purely by the
        linked term, since the raw prose has no lexical marker.
        """
        steps = [
            StepSpec(
                priority="(g)",
                recipient="senior_fees",
                condition="while the trigger applies",
                condition_terms=["Sequential Pay Trigger"],
            ),
        ]
        # Inactive trigger → the linked step is gated (pays 0).
        ex_inactive = interpret(
            steps, _funds(sequential_pay=False), available=1_000_000.0
        )
        assert ex_inactive.steps[0].gated is True
        assert ex_inactive.steps[0].amount_distributed == 0.0
        assert ex_inactive.steps[0].condition_terms == ["Sequential Pay Trigger"]
        # Active trigger → the linked step pays.
        ex_active = interpret(
            steps, _funds(sequential_pay=True), available=1_000_000.0
        )
        assert ex_active.steps[0].gated is False
        assert ex_active.steps[0].amount_distributed == 50_000.0

    def test_legacy_two_arg_evaluator_still_works(self):
        """An evaluator whose ``evaluate`` predates the optional ``terms`` param
        (TypeError on the 3-arg call) still drives interpret via the fallback."""

        class TwoArgEvaluator:
            def evaluate(self, condition, funds):
                return False

            def sequential_pay_active(self, funds):
                return True

        steps = [
            StepSpec(
                priority="(a)",
                recipient="senior_fees",
                condition="if X",
                condition_terms=["Sequential Pay Trigger"],
            ),
        ]
        ex = interpret(
            steps, _funds(), available=1_000_000.0, evaluator=TwoArgEvaluator()
        )
        assert ex.steps[0].gated is True


# ---------------------------------------------------------------------------
# Pari-passu groups
# ---------------------------------------------------------------------------


class TestPariPassu:
    def test_full_pay_when_funds_suffice(self):
        funds = _funds(senior_fees=300.0, swap_payment=100.0)
        steps = [
            StepSpec(priority="(b)", recipient="senior_fees", pari_passu_group="ops"),
            StepSpec(priority="(b)", recipient="swap_payment", pari_passu_group="ops"),
        ]
        ex = interpret(steps, funds, available=1_000.0)
        by_recipient = {s.recipient: s for s in ex.steps}
        assert by_recipient["senior_fees"].amount_distributed == 300.0
        assert by_recipient["swap_payment"].amount_distributed == 100.0
        assert ex.total_shortfall == 0.0

    def test_shortfall_split_pro_rata_by_need(self):
        """200 across needs 300 + 100 → 150 / 50 (pro-rata by need)."""
        funds = _funds(senior_fees=300.0, swap_payment=100.0)
        steps = [
            StepSpec(priority="(b)", recipient="senior_fees", pari_passu_group="ops"),
            StepSpec(priority="(b)", recipient="swap_payment", pari_passu_group="ops"),
        ]
        ex = interpret(steps, funds, available=200.0)
        by_recipient = {s.recipient: s for s in ex.steps}
        assert by_recipient["senior_fees"].amount_distributed == pytest.approx(150.0)
        assert by_recipient["swap_payment"].amount_distributed == pytest.approx(50.0)
        # Pot exhausted by the group.
        assert ex.remaining == pytest.approx(0.0)

    def test_pari_passu_group_does_not_overpay_pot(self):
        funds = _funds(senior_fees=300.0, swap_payment=100.0)
        steps = [
            StepSpec(priority="(b)", recipient="senior_fees", pari_passu_group="ops"),
            StepSpec(priority="(b)", recipient="swap_payment", pari_passu_group="ops"),
        ]
        ex = interpret(steps, funds, available=200.0)
        assert ex.total_distributed == pytest.approx(200.0)

    def test_from_extracted_flags_pari_passu(self):
        spec = StepSpec.from_extracted(
            {
                "priority": "(b)",
                "recipient": "operating_fees",
                "condition": "",
                "is_pari_passu": True,
            }
        )
        assert spec.pari_passu_group is not None
        assert spec.condition is None  # "" → None


# ---------------------------------------------------------------------------
# Sequential-pay branch (A3)
# ---------------------------------------------------------------------------


class TestSequentialPayBranch:
    def test_sequential_pays_senior_first(self):
        """Sequential mode: Class A absorbs principal up to its balance first."""
        funds = _funds(
            class_a_balance=3_000_000.0,
            class_b_balance=2_000_000.0,
            class_c_balance=1_000_000.0,
            sequential_pay=True,
        )
        alloc = allocate_principal(funds, available=4_000_000.0)
        assert alloc["class_a"] == pytest.approx(3_000_000.0)  # full Class A
        assert alloc["class_b"] == pytest.approx(1_000_000.0)  # remainder to B
        assert alloc["class_c"] == pytest.approx(0.0)

    def test_pro_rata_splits_by_outstanding_balance(self):
        """Pro-rata mode: split principal across classes by outstanding balance."""
        funds = _funds(
            class_a_balance=6_000_000.0,
            class_b_balance=3_000_000.0,
            class_c_balance=1_000_000.0,
            sequential_pay=False,
        )
        alloc = allocate_principal(funds, available=5_000_000.0)
        # Ratios 6:3:1 of 5M → 3.0M / 1.5M / 0.5M.
        assert alloc["class_a"] == pytest.approx(3_000_000.0)
        assert alloc["class_b"] == pytest.approx(1_500_000.0)
        assert alloc["class_c"] == pytest.approx(500_000.0)
        assert sum(alloc.values()) == pytest.approx(5_000_000.0)

    def test_sequential_vs_pro_rata_differ(self):
        """The branch actually changes allocation between the two modes."""
        base = dict(
            class_a_balance=6_000_000.0,
            class_b_balance=3_000_000.0,
            class_c_balance=1_000_000.0,
        )
        seq = allocate_principal(_funds(sequential_pay=True, **base), available=5e6)
        pro = allocate_principal(_funds(sequential_pay=False, **base), available=5e6)
        # Sequential gives Class B nothing (Class A not yet retired); pro-rata does.
        assert seq["class_b"] == pytest.approx(0.0)
        assert pro["class_b"] > 0.0

    def test_classes_restriction_excludes_unlisted_class(self):
        """Restricting `classes` keeps principal off classes with no redemption step.

        Green Lion repays only Class A/B from principal (Class C from revenue);
        the runner restricts the allocation accordingly so the residual is
        correct and Class C principal is not silently lost.
        """
        funds = _funds(
            class_a_balance=2_000_000.0,
            class_b_balance=3_000_000.0,
            class_c_balance=1_000_000.0,
            sequential_pay=True,
        )
        alloc = allocate_principal(
            funds, available=10_000_000.0, classes=("class_a", "class_b")
        )
        assert alloc["class_a"] == pytest.approx(2_000_000.0)
        assert alloc["class_b"] == pytest.approx(3_000_000.0)
        # Class C has no allocation key — it is excluded entirely.
        assert "class_c" not in alloc
        # Only €5M allocated; the remaining €5M is the caller's residual.
        assert sum(alloc.values()) == pytest.approx(5_000_000.0)

    def test_allocation_capped_at_balance(self):
        """No class is repaid more than its outstanding balance."""
        funds = _funds(
            class_a_balance=1_000_000.0,
            class_b_balance=500_000.0,
            class_c_balance=0.0,
            sequential_pay=True,
        )
        alloc = allocate_principal(funds, available=10_000_000.0)
        assert alloc["class_a"] == pytest.approx(1_000_000.0)
        assert alloc["class_b"] == pytest.approx(500_000.0)
        assert alloc["class_c"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Injected evaluator seam (S5 / #185)
# ---------------------------------------------------------------------------


class TestInjectedEvaluatorSeam:
    def test_custom_evaluator_drives_sequential_pay(self):
        """A custom evaluator overrides the default sequential-pay decision."""

        class ProRataEvaluator:
            """Stand-in for an S5 trigger engine that says 'not sequential'."""

            def evaluate(self, condition, funds):
                return True

            def sequential_pay_active(self, funds):
                return False  # force pro-rata regardless of funds.sequential_pay

        funds = _funds(
            class_a_balance=6_000_000.0,
            class_b_balance=3_000_000.0,
            class_c_balance=1_000_000.0,
            sequential_pay=True,  # default would say sequential
        )
        alloc = allocate_principal(
            funds, available=5_000_000.0, evaluator=ProRataEvaluator()
        )
        # Custom evaluator forced pro-rata → Class B gets principal.
        assert alloc["class_b"] > 0.0

    def test_custom_evaluator_gates_interpret_steps(self):
        class GateRevolving:
            def evaluate(self, condition, funds):
                return "revolving" not in condition.lower()

            def sequential_pay_active(self, funds):
                return True

        funds = _funds()
        steps = [
            StepSpec(
                priority="(a)",
                recipient="senior_fees",
                condition="during Revolving Period",
            ),
            StepSpec(priority="(d)", recipient="class_a_interest"),
        ]
        ex = interpret(steps, funds, available=10_000_000.0, evaluator=GateRevolving())
        assert ex.steps[0].gated is True
        assert ex.steps[0].amount_distributed == 0.0


# ---------------------------------------------------------------------------
# WaterfallResult round-trip through the S1 seam
# ---------------------------------------------------------------------------


class TestWaterfallResultSeam:
    def test_to_waterfall_result_shape(self):
        funds = _funds(class_a_pdl_balance=200_000.0)
        rev_steps = [
            StepSpec(priority="(a)", recipient="senior_fees"),
            StepSpec(priority="(e)", recipient="class_a_pdl_replenishment"),
            StepSpec(priority="(f)", recipient="reserve_replenishment"),
        ]
        rev = interpret(rev_steps, funds, available=10_000_000.0)
        alloc = allocate_principal(funds, available=5_000_000.0)
        result = to_waterfall_result(revenue=rev, principal_allocation=alloc)
        assert isinstance(result, WaterfallResult)
        assert result.class_a_pdl_replenishment == pytest.approx(200_000.0)
        assert result.class_a_principal == pytest.approx(alloc["class_a"])

    def test_result_applies_to_deal_state(self):
        """The interpreter's result is consumed by DealState.apply_waterfall_result."""
        funds = _funds(
            class_a_balance=10_000_000.0,
            class_b_balance=5_000_000.0,
            class_c_balance=1_000_000.0,
            class_a_pdl_balance=100_000.0,
            sequential_pay=True,
        )
        rev = interpret(
            [StepSpec(priority="(e)", recipient="class_a_pdl_replenishment")],
            funds,
            available=10_000_000.0,
        )
        alloc = allocate_principal(funds, available=2_000_000.0)
        result = to_waterfall_result(revenue=rev, principal_allocation=alloc)

        state = DealState.seed_from_prospectus(
            {
                "class_a_balance": 10_000_000.0,
                "class_b_balance": 5_000_000.0,
                "class_c_balance": 1_000_000.0,
            },
            reserve_target=500_000.0,
            original_pool_balance=16_000_000.0,
            reporting_date="2026-04-30",
        )
        # Seed a PDL so replenishment has something to cure.
        state = state.model_copy(
            update={
                "tranches": [
                    t.model_copy(update={"pdl_balance": 100_000.0})
                    if t.name == "class_a"
                    else t
                    for t in state.tranches
                ]
            }
        )
        assert state.class_a_pdl == 100_000.0  # tamper landed on the tranche list
        closing = state.apply_waterfall_result(result)

        # Class A principal of €2M (sequential) reduced its balance.
        assert closing.class_a_balance == pytest.approx(8_000_000.0)
        # PDL replenishment cured the €100k debit.
        assert closing.class_a_pdl == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Engine generality: WaterfallFunds round-trips by tranche name (#363)
# ---------------------------------------------------------------------------


class TestWaterfallFundsGenerality:
    def test_class_accessors_equal_tranche_list_values(self):
        funds = WaterfallFunds(
            tranches=[
                TrancheFunds(name="class_a", balance=100.0, rate_pct=3.0, pdl_balance=1.0),
                TrancheFunds(name="class_b", balance=50.0, rate_pct=4.0, pdl_balance=2.0),
            ]
        )
        assert funds.class_a_balance == 100.0
        assert funds.class_a_rate_pct == 3.0
        assert funds.class_a_pdl_balance == 1.0
        assert funds.class_b_balance == 50.0
        assert funds.class_c_balance == 0.0  # absent tranche → 0

    def test_legacy_class_kwargs_fold_into_tranches(self):
        funds = WaterfallFunds(
            class_a_balance=100.0,
            class_a_rate_pct=3.0,
            class_a_pdl_balance=1.0,
            class_b_balance=50.0,
        )
        assert {t.name for t in funds.tranches} >= {"class_a", "class_b"}
        assert funds.tranche("class_a").balance == 100.0
        assert funds.tranche("class_a").rate_pct == 3.0
        # The accessors read the same folded values.
        assert funds.class_a_pdl_balance == 1.0

    def test_allocate_principal_by_name_non_abc(self):
        """``allocate_principal`` allocates across custom tranche names sequentially."""
        funds = WaterfallFunds(
            tranches=[
                TrancheFunds(name="senior", balance=1_000.0),
                TrancheFunds(name="junior", balance=500.0),
            ],
            sequential_pay=True,
        )
        alloc = allocate_principal(
            funds, available=1_200.0, classes=("senior", "junior")
        )
        # Sequential: senior paid to its balance first, residual to junior.
        assert alloc["senior"] == pytest.approx(1_000.0)
        assert alloc["junior"] == pytest.approx(200.0)

    def test_to_waterfall_result_keys_by_name_non_abc(self):
        """``to_waterfall_result`` carries non-A/B/C principal by name."""
        result = to_waterfall_result(
            principal_allocation={"senior": 1_000.0, "junior": 200.0}
        )
        by = {t.name: t for t in result.tranches}
        # Canonical A/B/C entries always present (byte-stable), plus the custom ones.
        assert by["senior"].principal == pytest.approx(1_000.0)
        assert by["junior"].principal == pytest.approx(200.0)
        assert by["class_a"].principal == 0.0
