"""The recipient ↔ need-calculator contract (#453).

``RecipientType``'s docstring has always promised that each member binds to an
engine need-calculator. Nothing checked it, so #394 broadened the enum for the
global ABS universe and registered no calculators: a deep capital stack mapped
``class_d_interest`` correctly and then computed a need of zero. That was latent
only because no committed deal had a stack that deep; a CLO hits it on the first
period.

Filling the holes is the easy half. This module is the other half — the contract
made checkable, so the *next* vocabulary broadening cannot reopen the gap the
same way. Three layers, strongest first:

1. **Unrepresentable.** ``domain.rules`` asserts both recipient tables total over
   the enum at import, and ``waterfall_interpreter`` asserts the registry covers
   every registry-backed recipient at import. A member added with no declaration,
   or a declared calculator-backed member with no calculator, is an ImportError.
   Nothing can be committed past it.
2. **Refused at the boundary.** ``register_need`` rejects a name outside
   enum ∪ declared legacy spellings, and rejects a calculator for a recipient
   whose need comes from somewhere else.
3. **Pinned here.** These tests fail if either guard is weakened, and they check
   the properties the guards *are*, not the current membership of the enum — so
   they keep holding as the taxonomy grows.

The tests below deliberately state no member counts. A count transcribed into a
test is a second copy of the enum that has to be maintained in step with it;
every assertion here re-derives from ``RecipientType`` at run time.
"""

from __future__ import annotations

import math

import pytest

# `loanwhiz.primitives` must be imported before `loanwhiz.domain` — a
# pre-existing base-branch import cycle (domain/__init__ -> provenance ->
# primitives.base -> primitives/__init__ -> ... -> domain.rules) makes
# `loanwhiz.domain` unimportable as the first loanwhiz import. Unrelated to
# this change; see the PR body.
from loanwhiz.primitives.waterfall_interpreter import (  # isort: skip
    NEED_CALCULATORS,
    TrancheFunds,
    WaterfallFunds,
    compute_need,
    register_need,
)
from loanwhiz.domain.rules import (  # isort: skip
    LEGACY_RECIPIENT_SPELLINGS,
    RECIPIENT_BASIS,
    RECIPIENT_NEED_SOURCE,
    AmountRule,
    NeedSource,
    RecipientType,
    basis_for,
    need_source_for,
    recipients_needing_calculator,
)
from loanwhiz.extraction.taxonomy import (  # isort: skip
    basis_for_recipient,
    map_recipient,
)


def _funds(**overrides) -> WaterfallFunds:
    """A funds context with non-zero pots, so a computed 0 means something.

    Every deeper-stack assertion below needs the funds to be capable of paying:
    otherwise "need is 0" would be an artefact of an empty deal rather than a
    statement about the calculator.
    """
    defaults = dict(
        available_revenue_funds=50_000_000.0,
        available_principal_funds=50_000_000.0,
        days_in_period=90,
    )
    defaults.update(overrides)
    return WaterfallFunds(**defaults)


# ---------------------------------------------------------------------------
# 1. The declaration is total over the enum.
# ---------------------------------------------------------------------------


class TestDeclarationTotality:
    """Every recipient declares a basis and a need source — no member excepted."""

    def test_every_recipient_declares_a_basis(self) -> None:
        undeclared = sorted(r.value for r in RecipientType if r not in RECIPIENT_BASIS)
        assert undeclared == []

    def test_every_recipient_declares_a_need_source(self) -> None:
        undeclared = sorted(
            r.value for r in RecipientType if r not in RECIPIENT_NEED_SOURCE
        )
        assert undeclared == []

    def test_declared_basis_is_a_valid_amount_rule_basis(self) -> None:
        """A basis nothing can construct an ``AmountRule`` with is not a binding.

        `AmountBasis` is a Literal, so a typo in the table would type-check
        nowhere and surface only when the assembler tried to build the rule.
        """
        for recipient in RecipientType:
            rule = AmountRule(
                calculator=recipient, basis=basis_for(recipient), raw_text="x"
            )
            assert rule.basis == basis_for(recipient)

    def test_taxonomy_delegates_rather_than_keeping_a_second_table(self) -> None:
        """`basis_for_recipient` is the same binding, not a copy that can drift."""
        for recipient in RecipientType:
            assert basis_for_recipient(recipient) == basis_for(recipient)

    def test_unmapped_is_report_supplied_and_needs_no_calculator(self) -> None:
        """The escape stays an escape: it never resolves to an engine formula."""
        assert basis_for(RecipientType.unmapped) == "report_supplied"
        assert need_source_for(RecipientType.unmapped) is NeedSource.step_override
        assert RecipientType.unmapped not in recipients_needing_calculator()


class TestGuardsActuallyFire:
    """Exercise the guards, not just the state they currently enforce.

    ``TestDeclarationTotality`` asserts the tables *are* total — which stays
    green if someone deletes the guard, because the tables happen to be total
    today. These tests doctor the tables and call the guards directly, so they
    red when the mechanism is removed rather than when its effect happens to
    lapse. Without them the import-time asserts are untested code claiming to
    be the contract's teeth.
    """

    def test_missing_basis_row_is_refused_at_import(self, monkeypatch) -> None:
        from loanwhiz.domain import rules

        monkeypatch.delitem(rules.RECIPIENT_BASIS, RecipientType.class_d_interest)
        with pytest.raises(ImportError, match="RECIPIENT_BASIS is not total"):
            rules._assert_recipient_tables_total()

    def test_missing_need_source_row_is_refused_at_import(self, monkeypatch) -> None:
        from loanwhiz.domain import rules

        monkeypatch.delitem(
            rules.RECIPIENT_NEED_SOURCE, RecipientType.class_d_interest
        )
        with pytest.raises(ImportError, match="RECIPIENT_NEED_SOURCE is not total"):
            rules._assert_recipient_tables_total()

    def test_the_refusal_names_the_undeclared_member(self, monkeypatch) -> None:
        """A guard that fires without saying what is wrong costs a debug cycle."""
        from loanwhiz.domain import rules

        monkeypatch.delitem(rules.RECIPIENT_BASIS, RecipientType.class_e_principal)
        with pytest.raises(ImportError, match="class_e_principal"):
            rules._assert_recipient_tables_total()

    def test_an_unregistered_calculator_backed_recipient_is_refused_at_import(
        self, monkeypatch
    ) -> None:
        """The #394 failure itself: an enum member the registry never covered."""
        from loanwhiz.primitives import waterfall_interpreter as wi

        monkeypatch.delitem(wi.NEED_CALCULATORS, "class_d_interest")
        with pytest.raises(ImportError, match="class_d_interest"):
            wi._assert_registry_covers_contract()

    def test_the_registry_guard_is_silent_when_the_contract_is_met(self) -> None:
        """A guard that fires unconditionally pins nothing."""
        from loanwhiz.domain import rules
        from loanwhiz.primitives import waterfall_interpreter as wi

        rules._assert_recipient_tables_total()
        wi._assert_registry_covers_contract()


# ---------------------------------------------------------------------------
# 2. The registry matches the declaration, in both directions.
# ---------------------------------------------------------------------------


class TestRegistryMatchesContract:
    def test_every_registry_backed_recipient_has_a_calculator(self) -> None:
        """THE assertion this module exists for.

        A recipient whose declared need source is the registry, with nothing in
        the registry, is the #394 defect exactly: the step maps, then contributes
        need 0 to a real distribution.
        """
        missing = sorted(
            r.value
            for r in recipients_needing_calculator()
            if r.value not in NEED_CALCULATORS
        )
        assert missing == []

    def test_no_calculator_for_a_recipient_supplied_elsewhere(self) -> None:
        """The reverse drift: a calculator that would override the real source.

        Principal is allocated by ``allocate_principal`` and fed in as
        ``need_overrides``; a report-supplied step's amount is the servicer's
        actual. A calculator registered for either would quietly win over it in
        some code path and lose in another.
        """
        wrongly_registered = sorted(
            r.value
            for r in RecipientType
            if r not in recipients_needing_calculator() and r.value in NEED_CALCULATORS
        )
        assert wrongly_registered == []

    def test_every_registry_key_is_a_declared_name(self) -> None:
        """No calculator keyed by a name no canonical step can carry.

        A registry entry under an undeclared spelling reads as coverage in a
        `len(NEED_CALCULATORS)` glance while being unreachable in practice.
        """
        declared = {r.value for r in RecipientType} | set(LEGACY_RECIPIENT_SPELLINGS)
        assert sorted(set(NEED_CALCULATORS) - declared) == []

    def test_legacy_spelling_resolves_to_its_canonical_recipients_calculator(
        self,
    ) -> None:
        """An alias must compute the same thing as the value it aliases.

        Before #453 the two were registered independently, so nothing stopped
        ``class_a_pdl_replenishment`` and ``class_a_pdl_cure`` drifting onto
        different formulas.
        """
        for spelling, recipient in LEGACY_RECIPIENT_SPELLINGS.items():
            assert NEED_CALCULATORS[spelling] is NEED_CALCULATORS[recipient.value]

    def test_every_legacy_spelling_also_maps_through_the_taxonomy(self) -> None:
        """The engine and the extractor agree on what each legacy string means."""
        for spelling, recipient in LEGACY_RECIPIENT_SPELLINGS.items():
            assert map_recipient(spelling, use_llm=False).value is recipient

    def test_engine_computed_recipients_do_not_drift_from_the_contract(self) -> None:
        """`step_source_classifier` keeps a third hand-maintained copy of this set.

        Editing that module is outside this issue's declared paths, so the copy
        stays — but it is pinned here as a subset of the contract, so it fails
        loudly if it ever names something the engine cannot actually compute.
        """
        from loanwhiz.primitives.step_source_classifier import (
            ENGINE_COMPUTED_RECIPIENTS,
        )

        computable = {
            r.value
            for r, src in RECIPIENT_NEED_SOURCE.items()
            if src is NeedSource.calculator
        } | {
            spelling
            for spelling, r in LEGACY_RECIPIENT_SPELLINGS.items()
            if need_source_for(r) is NeedSource.calculator
        }
        assert sorted(ENGINE_COMPUTED_RECIPIENTS - computable) == []


# ---------------------------------------------------------------------------
# 3. The boundary guard refuses the drift rather than reporting it.
# ---------------------------------------------------------------------------


class TestRegistrationGuard:
    def test_undeclared_recipient_name_is_refused(self) -> None:
        with pytest.raises(ValueError, match="not a RecipientType value"):
            register_need("class_z_interest")(lambda funds: 1.0)

    def test_typo_of_a_real_recipient_is_refused(self) -> None:
        """The realistic form of the mistake — a near-miss, not a nonsense name."""
        with pytest.raises(ValueError, match="not a RecipientType value"):
            register_need("class_d_interests")(lambda funds: 1.0)

    def test_calculator_for_an_allocation_recipient_is_refused(self) -> None:
        with pytest.raises(ValueError, match="NeedSource.allocation"):
            register_need("class_a_principal")(lambda funds: 1.0)

    def test_calculator_for_a_report_supplied_recipient_is_refused(self) -> None:
        with pytest.raises(ValueError, match="NeedSource.step_override"):
            register_need("subordinated_amounts")(lambda funds: 1.0)

    def test_a_refused_registration_leaves_the_registry_untouched(self) -> None:
        """The guard must not half-apply a multi-name registration."""
        before = dict(NEED_CALCULATORS)
        with pytest.raises(ValueError):
            register_need("class_d_interest", "class_z_interest")(lambda funds: 1.0)
        assert NEED_CALCULATORS == before


# ---------------------------------------------------------------------------
# 4. The holes the contract was hiding: the deeper stack now computes.
# ---------------------------------------------------------------------------


def _tranche(name: str, **kw) -> TrancheFunds:
    return TrancheFunds(name=name, **kw)


class TestDeeperStackNeeds:
    @pytest.mark.parametrize("letter", ["d", "e", "f"])
    def test_deeper_stack_interest_accrues_act_360(self, letter: str) -> None:
        funds = _funds(
            tranches=[_tranche(f"class_{letter}", balance=20_000_000.0, rate_pct=8.5)]
        )
        need, evaluable = compute_need(f"class_{letter}_interest", funds)
        assert evaluable
        assert math.isclose(
            need, 20_000_000.0 * 0.085 / 360.0 * 90, rel_tol=1e-9
        )

    @pytest.mark.parametrize("letter", ["a", "b", "c"])
    def test_pdl_cure_is_reachable_under_its_canonical_name(self, letter: str) -> None:
        """The canonical name computed nothing before #453 — only the alias did."""
        funds = _funds(
            tranches=[_tranche(f"class_{letter}", pdl_balance=750_000.0)]
        )
        need, evaluable = compute_need(f"class_{letter}_pdl_cure", funds)
        assert evaluable
        assert math.isclose(need, 750_000.0, rel_tol=1e-9)

    def test_liquidity_reserve_tops_up_to_its_own_target(self) -> None:
        """Distinct from the general reserve fund, not sharing its ledger."""
        funds = _funds(
            reserve_balance=5_000_000.0,
            reserve_target=5_000_000.0,
            liquidity_reserve_balance=1_000_000.0,
            liquidity_reserve_target=2_500_000.0,
        )
        need, evaluable = compute_need("liquidity_reserve_replenishment", funds)
        assert evaluable
        assert math.isclose(need, 1_500_000.0, rel_tol=1e-9)
        assert compute_need("reserve_replenishment", funds) == (0.0, True)

    def test_absent_tranche_is_not_evaluable_not_a_zero_need(self) -> None:
        """A Class D step in a deal with no Class D has an unanswerable need.

        Reporting 0 there would state that Class D noteholders are owed nothing
        this period, which the funds do not say.
        """
        funds = _funds(tranches=[_tranche("class_a", balance=1e9, rate_pct=3.0)])
        assert compute_need("class_d_interest", funds) == (0.0, False)

    def test_present_tranche_with_zero_balance_is_evaluable_zero(self) -> None:
        """Fully amortised is a real answer, and must not read as unknown."""
        funds = _funds(tranches=[_tranche("class_d", balance=0.0, rate_pct=8.5)])
        assert compute_need("class_d_interest", funds) == (0.0, True)


# ---------------------------------------------------------------------------
# 5. The CLO management fees.
# ---------------------------------------------------------------------------


class TestManagementFees:
    @pytest.mark.parametrize(
        "recipient,rate", [("senior_management_fee", 0.20), ("subordinated_management_fee", 0.25)]
    )
    def test_fee_accrues_on_the_collateral_balance(
        self, recipient: str, rate: float
    ) -> None:
        funds = _funds(collateral_balance=400_000_000.0, fee_rates_pct={recipient: rate})
        need, evaluable = compute_need(recipient, funds)
        assert evaluable
        assert math.isclose(
            need, 400_000_000.0 * (rate / 100.0) / 360.0 * 90, rel_tol=1e-9
        )

    def test_unconfigured_fee_rate_is_not_evaluable_not_a_zero_fee(self) -> None:
        """The silent-zero case this issue is about, in its new clothes.

        A CLO with collateral but no rate configured must not report that the
        collateral manager is owed nothing — that number would flow into a
        distribution and reconcile against the trustee report as a real figure.
        """
        funds = _funds(collateral_balance=400_000_000.0)
        assert compute_need("senior_management_fee", funds) == (0.0, False)

    def test_each_fee_reads_its_own_rate(self) -> None:
        """A rate configured for one manager fee must not leak into the other."""
        funds = _funds(
            collateral_balance=400_000_000.0,
            fee_rates_pct={"senior_management_fee": 0.20},
        )
        assert compute_need("senior_management_fee", funds)[1] is True
        assert compute_need("subordinated_management_fee", funds) == (0.0, False)

    def test_zero_collateral_with_a_rate_is_evaluable_zero(self) -> None:
        """Post-wind-down is a real answer; unknown-rate is not."""
        funds = _funds(fee_rates_pct={"senior_management_fee": 0.20})
        assert compute_need("senior_management_fee", funds) == (0.0, True)

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Senior Management Fee", RecipientType.senior_management_fee),
            ("senior_collateral_management_fee", RecipientType.senior_management_fee),
            (
                "Subordinated Management Fee",
                RecipientType.subordinated_management_fee,
            ),
            (
                "collateral_management_fee_subordinated",
                RecipientType.subordinated_management_fee,
            ),
        ],
    )
    def test_qualified_fee_prose_maps_deterministically(
        self, raw: str, expected: RecipientType
    ) -> None:
        mapping = map_recipient(raw, use_llm=False)
        assert mapping.value is expected
        assert mapping.method == "deterministic"

    @pytest.mark.parametrize(
        "raw",
        [
            "Incentive Management Fee",
            "incentive_fee",
            "Incentive Collateral Management Fee",
            "deferred_incentive_management_fee",
        ],
    )
    def test_incentive_fee_never_reaches_the_classifier(self, raw: str) -> None:
        """The incentive fee needs an equity IRR the engine cannot compute.

        ``use_llm=True`` is the point of this test, not an oversight. The LLM
        fallback is handed every ``RecipientType`` value as an option, so once
        ``senior_management_fee`` exists the incentive fee is one plausible hop
        from a real accrual paid to the wrong creditor. The deny table short-
        circuits before the ladder, so no LLM is called at all — which is why
        this passes offline with no credentials.
        """
        mapping = map_recipient(raw, use_llm=True)
        assert mapping.value is RecipientType.unmapped
        assert mapping.confidence == 0.0
        assert mapping.method == "deterministic"
        assert basis_for(mapping.value) == "report_supplied"

    def test_bare_management_fee_hits_no_deterministic_rule(self) -> None:
        """No ``management`` substring rule: an unqualified fee is not guessed.

        On the deterministic path it lands ``unmapped``. It is deliberately NOT
        deny-listed — unlike the incentive fee it *is* engine-evaluable once
        qualified, so letting the classifier read it in context is the designed
        behaviour, and this test pins only the deterministic half.
        """
        mapping = map_recipient("management_fee", use_llm=False)
        assert mapping.value is RecipientType.unmapped


# ---------------------------------------------------------------------------
# 6. The honest-degradation channel itself.
# ---------------------------------------------------------------------------


class TestNoneMeansNotEvaluable:
    def test_a_calculator_returning_none_degrades_rather_than_reporting_zero(
        self,
    ) -> None:
        """``None`` and ``0.0`` must not collapse onto the same trace outcome."""
        funds = _funds()
        assert compute_need("senior_management_fee", funds) == (0.0, False)

    def test_an_unregistered_recipient_still_degrades_the_same_way(self) -> None:
        """A jurisdiction-native label the engine does not know is unchanged."""
        assert compute_need("commissioni_e_spese", _funds()) == (0.0, False)

    def test_a_negative_need_is_clamped_not_propagated(self) -> None:
        funds = _funds(reserve_balance=9_000_000.0, reserve_target=1_000_000.0)
        assert compute_need("reserve_replenishment", funds) == (0.0, True)
