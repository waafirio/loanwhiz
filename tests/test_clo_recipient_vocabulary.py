"""The CLO's extracted recipient vocabulary resolves — and refuses legibly (#503).

Before this issue every one of Cairn CLO XVII DAC's 65 distinct extracted
recipients resolved to ``None`` in ``waterfall_interpreter._canonical_recipient``,
so all three cascades refused at the *registry* — one layer above the coupon
refusal (#493) that should have been the honest answer. A grader run against
that state would have had nothing to reconcile while looking like it ran.

What is pinned here
-------------------
1. **Nothing falls through.** Every recipient is either a canonical
   ``RecipientType`` or a *declared* unevaluable — never merely unmatched.
2. **The two readers agree.** The engine (exact tables) and the extraction
   taxonomy (alias → substring → class-refine → LLM) return the same member for
   every string, so a step means the same thing on both sides of the import
   cycle.
3. **No cascade double-claims.** Two steps of one cascade never share a member
   whose need the *engine* computes, which would pay one accrual twice.
4. **The refusal reason survives.** "Nobody spelled this name" and "the need is
   genuinely unanswerable" are different outputs, which is the honesty
   constraint the issue set.

The vocabulary is pinned on a committed fixture rather than on the seed model,
which a re-extraction may regenerate; :func:`test_seed_vocabulary_is_covered`
keeps the two from drifting apart silently.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

# `loanwhiz.primitives` must be imported before `loanwhiz.domain` — a
# pre-existing base-branch import cycle (domain/__init__ -> provenance ->
# primitives.base -> primitives/__init__ -> ... -> domain.rules) makes
# `loanwhiz.domain` unimportable as the first loanwhiz import. Same ordering
# note as `test_recipient_need_contract.py`; unrelated to this change.
from loanwhiz.primitives.waterfall_interpreter import (  # isort: skip
    REFUSAL_ALLOCATION_NOT_SUPPLIED,
    REFUSAL_INPUT_UNAVAILABLE,
    REFUSAL_RECOGNISED_NOT_EVALUABLE,
    REFUSAL_REPORT_SUPPLIED,
    REFUSAL_UNKNOWN_RECIPIENT,
    TrancheFunds,
    WaterfallFunds,
    _canonical_recipient,
    compute_need,
    refusal_reason,
)
from loanwhiz.domain.rules import (  # isort: skip
    RECIPIENT_SPELLINGS,
    RECOGNISED_UNEVALUABLE_RECIPIENTS,
    NeedSource,
    RecipientType,
    need_source_for,
)
from loanwhiz.extraction.taxonomy import map_recipient  # isort: skip

_FIXTURE = (
    Path(__file__).parent / "fixtures" / "clo_recipients" / "cairn-recipients.json"
)
_SEED = (
    Path(__file__).parents[1]
    / "src"
    / "loanwhiz"
    / "data"
    / "deals"
    / "seed"
    / "cairn-clo-xvii-dac.json"
)

#: Needs the ENGINE computes. Two steps of one cascade sharing one of these each
#: claim the whole amount; a ``step_override`` need is keyed by the step's own
#: priority label, so sharing one of those is safe by construction.
_ENGINE_OWNED = (NeedSource.calculator, NeedSource.funds_input)


@pytest.fixture(scope="module")
def cascades() -> dict[str, list[str]]:
    """``cascade name -> recipients, in payment order`` from the committed fixture."""
    return json.loads(_FIXTURE.read_text())["cascades"]


@pytest.fixture(scope="module")
def recipients(cascades) -> list[str]:
    """Every distinct recipient across the three cascades."""
    return sorted({r for steps in cascades.values() for r in steps})


def _funds(**kwargs) -> WaterfallFunds:
    return WaterfallFunds(available_funds=10_000_000.0, days_in_period=90, **kwargs)


class TestVocabularyIsClosed:
    """Every extracted recipient is *declared*, one way or the other."""

    def test_the_fixture_still_describes_the_deal(self, cascades, recipients):
        """Guard the guard: an emptied fixture would make every census vacuous.

        A census passes by finding nothing, so "the vocabulary is clean" and "I
        was handed no vocabulary" are one output unless something pins the size
        (#494's lesson, on this surface).
        """
        assert set(cascades) == {"revenue", "redemption", "post_enforcement"}
        assert all(steps for steps in cascades.values())
        # The refusal this issue exists to move was measured over 65 distinct
        # recipients; a fixture materially smaller is not describing this deal.
        assert len(recipients) > 50

    def test_no_recipient_falls_through(self, recipients):
        """The census. Nothing resolves by accident and nothing is merely unmatched."""
        unresolved = [r for r in recipients if _canonical_recipient(r) is None]
        assert unresolved == [], (
            "these recipients resolve to nothing — add a spelling row, a member, "
            f"or a declared-unevaluable row: {unresolved}"
        )

    def test_every_unmapped_recipient_is_declared_not_merely_unmatched(self, recipients):
        """``unmapped`` must be a decision, never a fall-through.

        This is the honesty constraint's other half: a step is allowed to refuse,
        but the refusal has to be one somebody wrote down.
        """
        for name in recipients:
            if _canonical_recipient(name) is RecipientType.unmapped:
                assert name in RECOGNISED_UNEVALUABLE_RECIPIENTS, (
                    f"{name!r} degrades to unmapped without being declared "
                    "unevaluable — that is a fall-through wearing a decision's face"
                )

    def test_seed_vocabulary_is_covered(self):
        """The committed seed introduces no recipient the fixture has not seen.

        Without this the fixture could drift into describing a deal that no
        longer exists while every assertion above stayed green.
        """
        seed = json.loads(_SEED.read_text())
        live = {
            step["recipient"]
            for waterfall in seed["waterfalls"].values()
            for step in waterfall["steps"]
        }
        pinned = {
            r
            for steps in json.loads(_FIXTURE.read_text())["cascades"].values()
            for r in steps
        }
        assert live - pinned == set(), (
            "the seed carries recipients the fixture does not: regenerate "
            f"{_FIXTURE.name} and re-check the assertions here"
        )


class TestTheTwoReadersAgree:
    """The engine and the extractor resolve every string identically."""

    def test_engine_and_taxonomy_return_the_same_member(self, recipients):
        """One declaration, two readers — they cannot be allowed to drift.

        The tables live in ``domain.rules`` precisely because the interpreter
        cannot import ``extraction.taxonomy``; this is what proves the merge back
        into ``_RECIPIENT_ALIASES`` actually happened.
        """
        disagreements = [
            (name, _canonical_recipient(name), map_recipient(name, use_llm=False).value)
            for name in recipients
            if _canonical_recipient(name) is not map_recipient(name, use_llm=False).value
        ]
        assert disagreements == []

    def test_a_cross_reference_never_becomes_class_interest(self):
        """The mis-mapping this issue had to *remove*, not merely fail to add.

        ``…_l_shortfall_for_class_c_coverage_tests`` carries a class letter and
        the word "interest", so the taxonomy's class refinement read it as Class
        C interest — accruing a full period's coupon out of principal proceeds.
        Declaring it unevaluable is what stops the refinement ever seeing it.
        """
        for name in (
            "interest_proceeds_priority_of_payments_l_shortfall_for_class_c_coverage_tests",
            "interest_proceeds_priority_of_payments_o_shortfall_for_class_d_coverage_tests",
            "interest_proceeds_priority_of_payments_r_shortfall_for_class_e_par_value_test",
            "interest_proceeds_priority_of_payments_u_shortfall_for_class_f_par_value_test",
        ):
            assert _canonical_recipient(name) is RecipientType.unmapped
            assert map_recipient(name, use_llm=False).value is RecipientType.unmapped


class TestNoCascadeDoubleClaims:
    """No engine-computed need is claimed twice within one cascade."""

    def test_engine_owned_members_are_claimed_once_per_cascade(self, cascades):
        for cascade, steps in cascades.items():
            counts: collections.Counter = collections.Counter()
            for name in steps:
                member = _canonical_recipient(name)
                if member is not None and need_source_for(member) in _ENGINE_OWNED:
                    counts[member.value] += 1
            shared = {m: n for m, n in counts.items() if n > 1}
            assert shared == {}, (
                f"{cascade}: {shared} — each of these steps would claim the whole "
                "engine-computed amount, so the cascade pays it more than once"
            )

    def test_deferred_interest_is_not_the_current_accrual(self):
        """A class's deferred step and its coupon step are different needs.

        The tempting alias — ``class_c_notes_deferred_interest`` →
        ``class_c_interest`` — is what the extraction taxonomy's substring ladder
        does on its own, and a CLO cascade pays *both* steps.
        """
        for letter in "cdef":
            deferred = _canonical_recipient(f"class_{letter}_notes_deferred_interest")
            current = _canonical_recipient(f"class_{letter}_notes_interest")
            assert deferred is RecipientType(f"class_{letter}_deferred_interest")
            assert current is RecipientType(f"class_{letter}_interest")
            assert deferred is not current

    def test_deferred_interest_reads_the_balance_not_a_fresh_accrual(self):
        """It pays down a carried balance, so it evaluates even with no coupon.

        The load-bearing consequence: a floating-rate CLO class whose coupon is
        unresolved still has a *known* deferred balance, because that interest
        crystallised in earlier periods.
        """
        funds = _funds(
            tranches=[
                TrancheFunds(
                    name="class_c",
                    balance=50_000_000.0,
                    rate_pct=None,
                    deferred_interest_balance=1_234.0,
                )
            ]
        )
        assert compute_need("class_c_deferred_interest", funds) == (1_234.0, True)
        # …while the current-period coupon on the same class stays unanswerable.
        assert compute_need("class_c_interest", funds) == (0.0, False)


class TestAliasIdentity:
    """A spelling lands on the member it names — the right class, the right leg."""

    @pytest.mark.parametrize("letter", list("abcdef"))
    def test_notes_spellings_keep_their_class_and_leg(self, letter):
        assert _canonical_recipient(f"class_{letter}_notes_interest") is RecipientType(
            f"class_{letter}_interest"
        )
        assert _canonical_recipient(f"class_{letter}_notes_principal") is RecipientType(
            f"class_{letter}_principal"
        )

    def test_manager_fee_spellings_keep_their_rank(self):
        """Senior and subordinated management fees rank differently and pay
        different creditors; collapsing them pays a real number to the wrong one."""
        assert (
            _canonical_recipient("investment_manager_senior_fee")
            is RecipientType.senior_management_fee
        )
        assert (
            _canonical_recipient("investment_manager_subordinated_fee")
            is RecipientType.subordinated_management_fee
        )

    def test_the_incentive_fee_stays_unevaluable(self):
        """#453's decision, restated in the CLO's own spelling.

        The incentive fee turns on an equity IRR hurdle the engine holds no
        running cashflow for, and ``senior_management_fee`` is one plausible hop
        away for a classifier.
        """
        for name in (
            "incentive_investment_management_fee",
            "vat_on_incentive_investment_management_fee",
        ):
            assert _canonical_recipient(name) is RecipientType.unmapped


class TestRefusalsStayDistinguishable:
    """The four reasons a need is unanswerable are four different outputs."""

    def test_an_unknown_recipient_says_so(self):
        assert (
            refusal_reason("no_such_recipient_anywhere", _funds())
            == REFUSAL_UNKNOWN_RECIPIENT
        )

    def test_a_declared_unevaluable_is_not_an_unknown_one(self):
        """The distinction the issue insisted on: recognised-and-declined is a
        different answer from never-heard-of."""
        assert (
            refusal_reason("redeem_notes", _funds())
            == REFUSAL_RECOGNISED_NOT_EVALUABLE
        )

    def test_a_named_recipient_awaiting_a_reported_amount_says_so(self):
        assert (
            refusal_reason("class_c_coverage_test_cure", _funds())
            == REFUSAL_REPORT_SUPPLIED
        )

    def test_a_principal_step_waiting_on_the_allocation_is_not_report_supplied(self):
        """Two different missing inputs, two different answers.

        A principal step is waiting on ``allocate_principal``; a cure is waiting
        on a reported amount. Collapsing them sends whoever reads the trace
        looking for the wrong thing.
        """
        assert (
            refusal_reason("class_a_notes_principal", _funds())
            == REFUSAL_ALLOCATION_NOT_SUPPLIED
        )

    def test_an_unresolved_coupon_refuses_at_the_input_not_the_vocabulary(self):
        """#493's refusal, now actually reachable for a CLO.

        This is the whole point of the issue: the interest step must refuse
        because the coupon is unknown, not because nobody spelled the recipient.
        """
        funds = _funds(
            tranches=[TrancheFunds(name="class_a", balance=1e8, rate_pct=None)]
        )
        assert (
            refusal_reason("class_a_notes_interest", funds) == REFUSAL_INPUT_UNAVAILABLE
        )

    def test_a_resolved_coupon_evaluates(self):
        """The counterpart — without it the assertion above would pass on a
        recipient that can never evaluate at all."""
        funds = _funds(
            tranches=[TrancheFunds(name="class_a", balance=1e8, rate_pct=3.0)]
        )
        assert refusal_reason("class_a_notes_interest", funds) is None
        need, evaluable = compute_need("class_a_notes_interest", funds)
        assert evaluable and need > 0.0

    def test_cairn_interest_steps_refuse_below_the_registry(self, cascades):
        """No Cairn interest step refuses for want of a name any more.

        The measurement the issue opened with was 65 unresolved of 65. What must
        be true now is not that the steps evaluate — every Cairn class floats, so
        #493 rightly refuses the coupon — but that the refusal moved *down*.
        """
        funds = _funds(
            tranches=[
                TrancheFunds(name=f"class_{letter}", balance=1e8, rate_pct=None)
                for letter in "abcdef"
            ]
        )
        interest_steps = [
            r for r in cascades["revenue"] if r.endswith("_notes_interest")
        ]
        assert interest_steps, "the revenue cascade should carry note interest steps"
        for name in interest_steps:
            assert refusal_reason(name, funds) == REFUSAL_INPUT_UNAVAILABLE, (
                f"{name} refuses for the wrong reason — it should be an "
                "unresolved coupon, not an unrecognised recipient"
            )


class TestTheDeclarationCannotContradictItself:
    """The import-time coherence guard, exercised rather than assumed."""

    def test_no_spelling_shadows_a_canonical_value(self):
        assert set(RECIPIENT_SPELLINGS) & {r.value for r in RecipientType} == set()

    def test_no_string_is_both_a_spelling_and_unevaluable(self):
        assert set(RECIPIENT_SPELLINGS) & RECOGNISED_UNEVALUABLE_RECIPIENTS == set()

    def test_no_canonical_value_is_declared_unevaluable(self):
        assert RECOGNISED_UNEVALUABLE_RECIPIENTS & {r.value for r in RecipientType} == (
            set()
        )
