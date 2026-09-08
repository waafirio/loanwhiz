"""``DealRules`` — the canonical *program* extracted from a deal's prospectus.

This is the static, period-invariant contract of a securitisation: its capital
structure (:class:`TrancheRule`), its payment waterfalls (:class:`StepRule`
sequences), its triggers / covenants (:class:`TriggerRule`), and its reserve
account (:class:`ReserveRule`). It is filled by the prospectus extractor and
consumed directly by the ``fold(run_period)`` engine — there is no mapping glue
because there is nothing to map *to*.

Two locked design decisions shape this module
(``docs/superpowers/specs/2026-06-20-canonical-domain-schema-design.md``):

- **The recipient and metric taxonomies are closed enums with an explicit
  ``unmapped`` escape** (decision 2). Each :class:`RecipientType` value binds to
  one engine need-calculator, which is what makes an extracted step
  *executable*. A deal's exotic step degrades honestly to ``unmapped``
  ("report-supplied / not-evaluable") instead of silently mis-mapping — open
  strings would reintroduce the boundary-mapping bug class (e.g. an extractor
  metric name matching none of the monitor's sentinels → silent ``0.0``).
- **A step's amount is a bound calculator-key, never a free-form formula**
  (decision 3). :class:`AmountRule.basis` selects one of a fixed set of engine
  formulas; the prose is retained only as ``raw_text`` for audit. Free formulas
  would be unbounded ``eval`` — a trap.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from loanwhiz.domain.provenance import ProvenanceMap

# ---------------------------------------------------------------------------
# Canonical recipient taxonomy — each value binds to one need-calculator.
# Ordered roughly senior -> junior.
# ---------------------------------------------------------------------------


class RecipientType(str, Enum):
    """Who a waterfall step pays — the closed set of engine-evaluable recipients.

    Each value declares **where its per-period need comes from**, ordered
    roughly senior → junior. ``unmapped`` is the explicit escape for a step
    whose recipient the engine cannot evaluate: it degrades honestly to
    "report-supplied / not-evaluable" rather than mis-mapping to a wrong
    calculator.

    That declaration is not prose: every member has a row in
    :data:`RECIPIENT_BASIS` and :data:`RECIPIENT_NEED_SOURCE`, both asserted
    **exhaustive at import time** (see :func:`_assert_recipient_tables_total`).
    A member added without those rows fails to import — the hole #394 opened
    (enum members broadened, calculators never registered, deep-stack steps
    silently contributing need 0) cannot be reopened by adding a member
    alone (#453).

    The set is broadened beyond the original English Green-Lion RMBS coverage
    for the global ABS universe (#394): deeper capital stacks reach Class F
    interest/principal and Class C PDL cure, and a ``liquidity_reserve_
    replenishment`` covers liquidity / commingling / set-off reserve top-ups.
    A CLO adds the senior and subordinated **management fees** (#453) — the
    only new engine formula since #394, ``fee_accrual`` on the collateral
    balance, which a CMBS/Auto servicing fee shares. The **incentive**
    management fee is deliberately absent: it is subject to an equity IRR
    hurdle the engine holds no inputs for, so it stays ``unmapped``.

    A CLO's *Priorities of Payments* broaden it once more (#503) with three
    families the RMBS vocabulary has no word for: separately-capped senior
    expense tiers, **deferred (PIK'd) interest** on the mezzanine classes, and
    **coverage- / par-value-test cures**. Each is a member rather than an alias
    onto a near neighbour because the near neighbour is already claimed by an
    earlier step in the same cascade — see the note above the tier members.
    """

    senior_expenses = "senior_expenses"  # issuer costs, admin, trustee, agents, tax
    # A CLO splits the senior-cost block the RMBS deals carry as ONE aggregate
    # into separately capped tiers, each its own step with its own reported
    # amount (#503). They are distinct members rather than aliases onto
    # ``senior_expenses`` for a mechanical reason: that value's need is the
    # ``funds_input`` scalar ``WaterfallFunds.senior_fees``, so two steps
    # sharing it would each claim the WHOLE scalar and the cascade would pay
    # senior costs twice. A ``step_override`` need is keyed by the step's own
    # priority label (``PeriodInputs.step_overrides``), so any number of steps
    # may share one of these members without ever double-claiming.
    issuer_tax_and_profit = "issuer_tax_and_profit"  # corporate tax + issuer profit
    administrative_expenses = "administrative_expenses"  # capped administrative tier
    # The *uncapped* tail of an expense tier, paid lower in the cascade once the
    # capped tier above is exhausted — a second step, so a second member.
    senior_expenses_uncapped = "senior_expenses_uncapped"
    servicing_fee = "servicing_fee"
    senior_management_fee = "senior_management_fee"  # CLO collateral manager, senior
    swap_payment = "swap_payment"
    class_a_interest = "class_a_interest"
    class_b_interest = "class_b_interest"
    class_c_interest = "class_c_interest"
    class_d_interest = "class_d_interest"  # deeper-stack interest (auto/consumer/CLO)
    class_e_interest = "class_e_interest"
    class_f_interest = "class_f_interest"
    # Deferred (PIK'd) interest on a mezzanine class — the accrued-but-unpaid
    # balance rolled up in earlier periods, paid at its OWN step, junior to the
    # class's current-period coupon. Kept distinct from ``class_*_interest``
    # precisely because a CLO cascade pays both: aliasing the deferred step onto
    # the current-accrual member would charge one period's Act/360 accrual twice
    # and report the second as if the engine had computed it (#503).
    class_c_deferred_interest = "class_c_deferred_interest"
    class_d_deferred_interest = "class_d_deferred_interest"
    class_e_deferred_interest = "class_e_deferred_interest"
    class_f_deferred_interest = "class_f_deferred_interest"
    class_a_pdl_cure = "class_a_pdl_cure"  # PDL replenishment, senior
    class_b_pdl_cure = "class_b_pdl_cure"
    class_c_pdl_cure = "class_c_pdl_cure"  # deeper PDL ledger
    # Coverage- / par-value-test cures: interest proceeds diverted to redeem
    # notes until a failing overcollateralisation or interest-coverage test is
    # restored. The engine holds no collateral par value and no per-class test
    # threshold, so the cure AMOUNT is report-supplied — but the recipient is
    # named, so the step reports which test it cures rather than degrading to
    # ``unmapped`` and losing the attachment point (#452's rule, applied to the
    # recipient side).
    class_ab_coverage_test_cure = "class_ab_coverage_test_cure"
    class_c_coverage_test_cure = "class_c_coverage_test_cure"
    class_d_coverage_test_cure = "class_d_coverage_test_cure"
    class_e_par_value_test_cure = "class_e_par_value_test_cure"
    class_f_par_value_test_cure = "class_f_par_value_test_cure"
    liquidity_reserve_replenishment = "liquidity_reserve_replenishment"  # liquidity/commingling/set-off reserve top-up
    reserve_replenishment = "reserve_replenishment"
    class_a_principal = "class_a_principal"
    class_b_principal = "class_b_principal"
    class_c_principal = "class_c_principal"
    class_d_principal = "class_d_principal"  # deeper-stack principal
    class_e_principal = "class_e_principal"
    class_f_principal = "class_f_principal"
    # CLO collateral manager, subordinated — ranks below the notes, above equity.
    subordinated_management_fee = "subordinated_management_fee"
    subordinated_amounts = "subordinated_amounts"  # subordinated swap, deferred fees
    residual_certificate = "residual_certificate"  # deferred purchase price / residual
    unmapped = "unmapped"  # explicit escape -> report-supplied / not-evaluable


# ---------------------------------------------------------------------------
# The recipient contract — basis, need source, legacy spellings.
#
# Declared HERE, beside the enum, and nowhere else. ``extraction.taxonomy``
# and ``primitives.waterfall_interpreter`` both read these tables rather than
# keeping their own copy: the drift this closes (#453) existed precisely
# because the enum lived here, the amount-basis binding lived in taxonomy, and
# the engine's need-calculator registry was keyed by a third, undeclared
# vocabulary of free strings.
# ---------------------------------------------------------------------------

#: The fixed engine formulas an :class:`AmountRule` may select. ``report_supplied``
#: means there is no engine formula (the amount comes from
#: ``PeriodInputs.step_overrides``); ``residual`` is the terminal sweep.
AmountBasis = Literal[
    "interest_accrual",  # balance x rate x days / basis
    "fee_accrual",  # collateral balance x fee rate x days / basis (#453)
    "pdl_balance",  # cure up to outstanding PDL
    "deferred_interest_balance",  # pay down accrued-but-unpaid (PIK'd) interest
    "target_shortfall",  # reserve: max(0, target - balance)
    "principal_due",  # amortisation / sequential / pro-rata
    "report_supplied",  # no engine formula — amount from PeriodInputs.step_overrides
    "residual",  # whatever remains (terminal step)
]


class NeedSource(str, Enum):
    """Where the engine gets a recipient's per-period need.

    The enum's promise used to be prose — "each value binds to exactly one
    engine need-calculator" — which was false for most members and enforced
    nowhere. This makes it a checkable, per-member declaration, and splits the
    four genuinely different answers apart so "no calculator" stops meaning
    both "legitimately supplied elsewhere" and "nobody registered one" (#453).

    Attributes:
        calculator:    An engine formula over deal data.
                       ``waterfall_interpreter.NEED_CALCULATORS`` **must** hold
                       one keyed by the member's own value.
        funds_input:   A servicer-actual scalar carried on ``WaterfallFunds``
                       (``senior_fees``, ``swap_payment``). A registered
                       calculator passes it through, so the registry must hold
                       one, but the number is the report's, not the engine's.
        allocation:    Supplied by ``waterfall_interpreter.allocate_principal``
                       and fed in as ``interpret(need_overrides=...)``. The
                       registry must **not** hold one — a calculator here would
                       race the sequential↔pro-rata allocation.
        step_override: No engine formula; the amount comes from
                       ``PeriodInputs.step_overrides``.
        residual:      The terminal "whatever remains" sweep; the need is
                       whatever is left in the pot, by definition.
    """

    calculator = "calculator"
    funds_input = "funds_input"
    allocation = "allocation"
    step_override = "step_override"
    residual = "residual"


#: Recipient → the fixed engine formula that computes its amount. Exhaustive
#: over :class:`RecipientType`, asserted at import.
RECIPIENT_BASIS: dict["RecipientType", AmountBasis] = {
    RecipientType.senior_expenses: "report_supplied",
    RecipientType.issuer_tax_and_profit: "report_supplied",
    RecipientType.administrative_expenses: "report_supplied",
    RecipientType.senior_expenses_uncapped: "report_supplied",
    RecipientType.servicing_fee: "report_supplied",
    RecipientType.senior_management_fee: "fee_accrual",
    RecipientType.swap_payment: "report_supplied",
    RecipientType.class_a_interest: "interest_accrual",
    RecipientType.class_b_interest: "interest_accrual",
    RecipientType.class_c_interest: "interest_accrual",
    RecipientType.class_d_interest: "interest_accrual",
    RecipientType.class_e_interest: "interest_accrual",
    RecipientType.class_f_interest: "interest_accrual",
    RecipientType.class_c_deferred_interest: "deferred_interest_balance",
    RecipientType.class_d_deferred_interest: "deferred_interest_balance",
    RecipientType.class_e_deferred_interest: "deferred_interest_balance",
    RecipientType.class_f_deferred_interest: "deferred_interest_balance",
    RecipientType.class_a_pdl_cure: "pdl_balance",
    RecipientType.class_b_pdl_cure: "pdl_balance",
    RecipientType.class_c_pdl_cure: "pdl_balance",
    RecipientType.class_ab_coverage_test_cure: "report_supplied",
    RecipientType.class_c_coverage_test_cure: "report_supplied",
    RecipientType.class_d_coverage_test_cure: "report_supplied",
    RecipientType.class_e_par_value_test_cure: "report_supplied",
    RecipientType.class_f_par_value_test_cure: "report_supplied",
    RecipientType.liquidity_reserve_replenishment: "target_shortfall",
    RecipientType.reserve_replenishment: "target_shortfall",
    RecipientType.class_a_principal: "principal_due",
    RecipientType.class_b_principal: "principal_due",
    RecipientType.class_c_principal: "principal_due",
    RecipientType.class_d_principal: "principal_due",
    RecipientType.class_e_principal: "principal_due",
    RecipientType.class_f_principal: "principal_due",
    RecipientType.subordinated_management_fee: "fee_accrual",
    RecipientType.subordinated_amounts: "report_supplied",
    RecipientType.residual_certificate: "residual",
    RecipientType.unmapped: "report_supplied",
}

#: Recipient → where its need comes from. Exhaustive over
#: :class:`RecipientType`, asserted at import.
RECIPIENT_NEED_SOURCE: dict["RecipientType", NeedSource] = {
    # Servicer-actual scalars the interpreter reads off ``WaterfallFunds``.
    RecipientType.senior_expenses: NeedSource.funds_input,
    RecipientType.swap_payment: NeedSource.funds_input,
    # No engine formula at all — the amount comes from step_overrides.
    RecipientType.servicing_fee: NeedSource.step_override,
    RecipientType.subordinated_amounts: NeedSource.step_override,
    RecipientType.unmapped: NeedSource.step_override,
    # The CLO's separately-capped senior tiers and its test cures. Each is
    # per-step by construction (step_overrides is keyed by priority label), so
    # a cascade may carry several without any of them double-claiming.
    RecipientType.issuer_tax_and_profit: NeedSource.step_override,
    RecipientType.administrative_expenses: NeedSource.step_override,
    RecipientType.senior_expenses_uncapped: NeedSource.step_override,
    RecipientType.class_ab_coverage_test_cure: NeedSource.step_override,
    RecipientType.class_c_coverage_test_cure: NeedSource.step_override,
    RecipientType.class_d_coverage_test_cure: NeedSource.step_override,
    RecipientType.class_e_par_value_test_cure: NeedSource.step_override,
    RecipientType.class_f_par_value_test_cure: NeedSource.step_override,
    # Engine formulas over deal data.
    RecipientType.senior_management_fee: NeedSource.calculator,
    RecipientType.subordinated_management_fee: NeedSource.calculator,
    RecipientType.class_a_interest: NeedSource.calculator,
    RecipientType.class_b_interest: NeedSource.calculator,
    RecipientType.class_c_interest: NeedSource.calculator,
    RecipientType.class_d_interest: NeedSource.calculator,
    RecipientType.class_e_interest: NeedSource.calculator,
    RecipientType.class_f_interest: NeedSource.calculator,
    RecipientType.class_c_deferred_interest: NeedSource.calculator,
    RecipientType.class_d_deferred_interest: NeedSource.calculator,
    RecipientType.class_e_deferred_interest: NeedSource.calculator,
    RecipientType.class_f_deferred_interest: NeedSource.calculator,
    RecipientType.class_a_pdl_cure: NeedSource.calculator,
    RecipientType.class_b_pdl_cure: NeedSource.calculator,
    RecipientType.class_c_pdl_cure: NeedSource.calculator,
    RecipientType.liquidity_reserve_replenishment: NeedSource.calculator,
    RecipientType.reserve_replenishment: NeedSource.calculator,
    # Principal: allocate_principal owns the sequential ↔ pro-rata split and
    # feeds it in as need_overrides. A registered calculator would race it.
    RecipientType.class_a_principal: NeedSource.allocation,
    RecipientType.class_b_principal: NeedSource.allocation,
    RecipientType.class_c_principal: NeedSource.allocation,
    RecipientType.class_d_principal: NeedSource.allocation,
    RecipientType.class_e_principal: NeedSource.allocation,
    RecipientType.class_f_principal: NeedSource.allocation,
    # The terminal sweep.
    RecipientType.residual_certificate: NeedSource.residual,
}

#: Non-canonical recipient spellings the **engine** accepts as registry keys.
#:
#: The interpreter's ``NEED_CALCULATORS`` predates the canonical enum and is
#: keyed by the free strings the extractor emits, six of which are not enum
#: values. They are legitimate — real deal models spell steps this way — but
#: they were declared only as rows buried in ``taxonomy._RECIPIENT_ALIASES``,
#: which the interpreter cannot import (it would close the
#: ``domain -> primitives -> extraction -> domain`` cycle). Declaring them here
#: lets ``register_need`` refuse anything outside enum ∪ this table, which is
#: what makes the #394 drift *unregistrable* rather than merely detectable.
#:
#: ``taxonomy._RECIPIENT_ALIASES`` merges this table rather than restating it,
#: so the two cannot disagree.
LEGACY_RECIPIENT_SPELLINGS: dict[str, "RecipientType"] = {
    "senior_fees": RecipientType.senior_expenses,
    "security_trustee_fees": RecipientType.senior_expenses,
    "class_a_pdl_replenishment": RecipientType.class_a_pdl_cure,
    "class_b_pdl_replenishment": RecipientType.class_b_pdl_cure,
    "class_c_pdl_replenishment": RecipientType.class_c_pdl_cure,
    "reserve_account_replenishment": RecipientType.reserve_replenishment,
}

#: The CLO Priorities-of-Payments spellings, same contract as the table above.
#:
#: Declared here for the same reason and read through the same merged view: a
#: CLO prospectus writes "Class A Notes Interest" where the enum says
#: ``class_a_interest``, and before #503 every one of Cairn CLO XVII's extracted
#: recipients resolved to ``None`` — so the whole cascade refused at the
#: registry, one layer *above* the coupon refusal that should have been the
#: honest answer.
#:
#: **Only one-to-one spellings belong here.** A string whose payee the engine
#: cannot place goes in :data:`RECOGNISED_UNEVALUABLE_RECIPIENTS` instead; a
#: string naming a recipient the enum does not model earns a member. Aliasing a
#: near-miss onto an existing member is the failure this issue exists to avoid,
#: because the near neighbour is usually already claimed by an earlier step of
#: the same cascade.
CLO_RECIPIENT_SPELLINGS: dict[str, "RecipientType"] = {
    # "Class A Notes Interest" / "…Principal" — the plain spelling gap. Every
    # class of the 8-class stack, both cascades.
    **{
        f"class_{letter}_notes_interest": RecipientType(f"class_{letter}_interest")
        for letter in "abcdef"
    },
    **{
        f"class_{letter}_notes_principal": RecipientType(f"class_{letter}_principal")
        for letter in "abcdef"
    },
    # Deferred (PIK'd) interest — its OWN member, never the current accrual.
    **{
        f"class_{letter}_notes_deferred_interest": RecipientType(
            f"class_{letter}_deferred_interest"
        )
        for letter in "cdef"
    },
    # Separately capped senior tiers.
    "taxes_and_issuer_profit": RecipientType.issuer_tax_and_profit,
    "issuer_taxes_and_profit": RecipientType.issuer_tax_and_profit,
    "trustee_fees_and_expenses": RecipientType.senior_expenses,
    # Every "beyond the capped tier" spelling lands on ONE member. It is a
    # step_override, so a cascade carrying several of these tails gives each its
    # own reported amount; routing them to ``senior_expenses`` instead would
    # have each claim the whole ``senior_fees`` scalar.
    "administrative_expenses_uncapped": RecipientType.senior_expenses_uncapped,
    "trustee_fees_and_expenses_uncapped": RecipientType.senior_expenses_uncapped,
    "unpaid_administrative_expenses": RecipientType.senior_expenses_uncapped,
    "unpaid_trustee_fees_and_expenses": RecipientType.senior_expenses_uncapped,
    # Collateral-manager fees. Only the spellings that name WHICH of the three
    # fees they pay; the incentive fee stays unevaluable (equity IRR hurdle).
    "investment_manager_senior_fee": RecipientType.senior_management_fee,
    "senior_investment_management_fee": RecipientType.senior_management_fee,
    "investment_manager_subordinated_fee": RecipientType.subordinated_management_fee,
    # Hedge counterparty — the CLO's word for the swap leg.
    "hedge_payments": RecipientType.swap_payment,
    "hedge_counterparty_payments": RecipientType.swap_payment,
    # The equity tier. Declared explicitly rather than left to the extraction
    # taxonomy's "subordinated" substring rule, which the engine cannot see —
    # every one is a compound the engine has no formula for (notes interest AND
    # the incentive fee; fees AND advances), so ``subordinated_amounts`` and its
    # report-supplied need is the honest landing place, not a fee calculator.
    "subordinated_notes_and_incentive_fee": RecipientType.subordinated_amounts,
    "subordinated_notes_interest_and_incentive_fee": RecipientType.subordinated_amounts,
    "subordinated_notes_principal_and_interest": RecipientType.subordinated_amounts,
    "subordinated_notes_pro_rata": RecipientType.subordinated_amounts,
    "subordinated_investment_management_fees_and_advances": (
        RecipientType.subordinated_amounts
    ),
}

#: Every non-canonical spelling the engine accepts, as one merged view.
#:
#: ``_canonical_recipient`` and ``register_need`` read THIS, so a spelling added
#: to either table above is accepted by the engine the moment it exists, and
#: ``taxonomy._RECIPIENT_ALIASES`` merges the same view — the two vocabularies
#: cannot drift apart because there is only one.
RECIPIENT_SPELLINGS: dict[str, "RecipientType"] = {
    **LEGACY_RECIPIENT_SPELLINGS,
    **CLO_RECIPIENT_SPELLINGS,
}

#: Strings we RECOGNISE and have decided the engine cannot evaluate (#503).
#:
#: The deny half of the closed vocabulary, and the reason this issue could not
#: be closed by aliasing alone. A CLO's redemption cascade is largely written as
#: cross-references — "the amounts referred to in paragraphs (A) through (I) of
#: the Interest Proceeds Priority of Payments, to the extent not paid in full
#: thereunder" — which name a *shortfall at another step*, not a payee. Left to
#: fall through they are worse than unrecognised: the extraction taxonomy's
#: class-letter refinement reads
#: ``interest_proceeds_priority_of_payments_l_shortfall_for_class_c_coverage_tests``
#: as Class C **interest** and would accrue a full period's coupon out of
#: principal proceeds.
#:
#: Naming them is what makes the two refusals distinguishable, which is the
#: honesty constraint this issue set: a step here refuses because its need is
#: genuinely unanswerable, never because nobody spelled its name. Same rule
#: #452 applied to unplaceable coverage *metrics*, applied to recipients.
RECOGNISED_UNEVALUABLE_RECIPIENTS: frozenset[str] = frozenset(
    {
        # Redemption-cascade cross-references into the interest cascade.
        "interest_proceeds_priority_of_payments_a_to_i_shortfall",
        "interest_proceeds_priority_of_payments_j_shortfall",
        "interest_proceeds_priority_of_payments_k_shortfall",
        "interest_proceeds_priority_of_payments_l_shortfall_for_class_c_coverage_tests",
        "interest_proceeds_priority_of_payments_m_shortfall",
        "interest_proceeds_priority_of_payments_n_shortfall",
        "interest_proceeds_priority_of_payments_o_shortfall_for_class_d_coverage_tests",
        "interest_proceeds_priority_of_payments_p_shortfall",
        "interest_proceeds_priority_of_payments_q_shortfall",
        "interest_proceeds_priority_of_payments_r_shortfall_for_class_e_par_value_test",
        "interest_proceeds_priority_of_payments_s_shortfall",
        "interest_proceeds_priority_of_payments_t_shortfall",
        "interest_proceeds_priority_of_payments_u_shortfall_for_class_f_par_value_test",
        "interest_proceeds_priority_of_payments_v_shortfall",
        "interest_proceeds_priority_of_payments_x_to_bb_shortfall",
        # The incentive fee and its VAT — an equity IRR hurdle the engine holds
        # no running equity cashflow for (the #453 decision, restated in the
        # CLO's own spelling so the classifier is never asked).
        "incentive_investment_management_fee",
        "vat_on_incentive_investment_management_fee",
        # Reinvestment, not a distribution: principal proceeds spent buying
        # collateral rather than paid to a creditor.
        "purchase_of_substitute_collateral",
        "purchase_of_substitute_collateral_post_reinvestment",
        # Redemption steps that name no class, so no attachment point. The
        # sequential ↔ pro-rata split across the stack is allocate_principal's
        # to make; guessing a class here would put a real number on the wrong
        # note.
        "redeem_notes",
        "special_redemption_amount",
        # Cures whose trigger the engine cannot quantify, and accounts that are
        # neither a reserve top-up nor a creditor.
        "effective_date_rating_event_cure",
        "reinvestment_overcollateralisation_test_cure",
        "collateral_enhancement_account",
        # The CLO's *expense* reserve, which is not the deal reserve the engine
        # models: ``reserve_target`` / ``reserve_balance`` describe a different
        # account, so the substring rule's `reserve_replenishment` would compute
        # a real shortfall for the wrong one.
        "expense_reserve_account",
        # A defaulted counterparty's termination payment — the amount depends on
        # a close-out valuation the engine holds nothing for.
        "defaulted_hedge_termination_payments",
    }
)


def _assert_recipient_tables_total() -> None:
    """Refuse to import if either recipient table is not total over the enum.

    This is the guard that makes the #453 defect class unrepresentable rather
    than merely documented. Before it, ``basis_for_recipient`` ended in
    ``.get(recipient, "report_supplied")``: a member added to
    :class:`RecipientType` with no binding *silently* became report-supplied,
    which is exactly how #394 broadened the vocabulary and left nine engine
    holes behind. An ImportError is loud, immediate and impossible to ship past.
    """
    for name, table in (
        ("RECIPIENT_BASIS", RECIPIENT_BASIS),
        ("RECIPIENT_NEED_SOURCE", RECIPIENT_NEED_SOURCE),
    ):
        missing = sorted(r.value for r in RecipientType if r not in table)
        if missing:
            raise ImportError(
                f"{name} is not total over RecipientType — no binding declared "
                f"for {missing}. Every recipient must declare where its need "
                f"comes from; see NeedSource."
            )


def _assert_recipient_vocabulary_coherent() -> None:
    """Refuse to import if the spelling and deny tables contradict each other.

    Same shape as the guard above, for the vocabulary #503 added. Three ways the
    three tables can disagree, each of which would ship a silently wrong answer
    rather than a loud one:

    - a spelling that is **also** a canonical enum value — ``RecipientType(name)``
      wins in ``_canonical_recipient``, so the alias row is dead code that reads
      as if it were in force;
    - a string declared **both** a spelling and unevaluable — the two tables give
      opposite answers and which one wins is an ordering accident;
    - a spelling declared **unevaluable** downstream, which would make the engine
      and the extractor disagree about the same string.
    """
    canonical = {r.value for r in RecipientType}

    shadowed = sorted(set(RECIPIENT_SPELLINGS) & canonical)
    if shadowed:
        raise ImportError(
            f"RECIPIENT_SPELLINGS shadows canonical RecipientType values "
            f"{shadowed}. A canonical value resolves as itself, so these rows "
            f"never take effect — delete them or rename the member."
        )

    both = sorted(set(RECIPIENT_SPELLINGS) & RECOGNISED_UNEVALUABLE_RECIPIENTS)
    if both:
        raise ImportError(
            f"{both} are declared BOTH a recipient spelling and unevaluable. "
            f"A string is either one the engine can place or one it refuses; "
            f"declaring both makes which answer wins an ordering accident."
        )

    denied_canonical = sorted(RECOGNISED_UNEVALUABLE_RECIPIENTS & canonical)
    if denied_canonical:
        raise ImportError(
            f"{denied_canonical} are canonical RecipientType values declared "
            f"unevaluable. Remove the member or the deny row — a member the "
            f"engine refuses to resolve is a member that should not exist."
        )


_assert_recipient_tables_total()
_assert_recipient_vocabulary_coherent()


def basis_for(recipient: "RecipientType") -> AmountBasis:
    """The fixed engine formula key bound to ``recipient``.

    Total by construction — :func:`_assert_recipient_tables_total` ran at
    import, so this indexes rather than defaulting.
    """
    return RECIPIENT_BASIS[recipient]


def need_source_for(recipient: "RecipientType") -> NeedSource:
    """Where the engine gets ``recipient``'s per-period need."""
    return RECIPIENT_NEED_SOURCE[recipient]


def recipients_needing_calculator() -> frozenset["RecipientType"]:
    """The recipients ``NEED_CALCULATORS`` must hold an entry for.

    Both :attr:`NeedSource.calculator` and :attr:`NeedSource.funds_input` are
    registry-backed; they differ in where the *number* originates, not in
    whether the interpreter looks one up.
    """
    return frozenset(
        r
        for r, src in RECIPIENT_NEED_SOURCE.items()
        if src in (NeedSource.calculator, NeedSource.funds_input)
    )


# ---------------------------------------------------------------------------
# Canonical metric taxonomy — triggers / covenants.
# ---------------------------------------------------------------------------


class MetricType(str, Enum):
    """The metric a trigger / covenant tests — the closed set of engine metrics.

    ``unmapped`` is the explicit escape for a deal-specific metric the engine
    does not compute; like :class:`RecipientType.unmapped`, it makes the schema
    additive (new values can land as deals are onboarded) without ever silently
    mis-mapping an unknown metric onto a known sentinel.

    Broadened for the global ABS universe (#394): finer arrears buckets
    (30d/60d alongside 90d/180d), a ``cumulative_default_rate`` kept **distinct**
    from ``cumulative_loss_rate`` (gross default ≠ net realised loss — collapsing
    the two onto one sentinel is exactly the silent-mis-map bug the closed enum
    exists to prevent), and a deeper ``class_c_pdl`` ledger.

    **Coverage tests, per attachment point (#452).** A coverage test *is* a
    trigger, so overcollateralisation (``class_<x>_oc_ratio``) and interest
    coverage (``class_<x>_ic_ratio``) are canonical metrics rather than
    ``unmapped``. They are enumerated **per attachment point** because a deal
    tests coverage at several points in its stack — a single OC/IC pair could
    not say *which* point it measured, and an open attachment-point string
    would reintroduce the free-string boundary bug this closed enum exists to
    kill. The shape is asset-class agnostic: any deal that tests coverage at a
    lettered attachment point uses it, CLO or not. A stack deeper than Class F
    still degrades to ``unmapped`` via the escape.

    This **reverses** the earlier judgement that CLO OC/IC "can only ever
    report-supply". Both now resolve from structural state at the covenant
    monitor: OC from the pool balance over the notes at-or-senior-to the
    attachment point, IC from period interest collections over the interest due
    on those same notes. The metrics the engine still holds no inputs for
    (card-ABS payment-rate / portfolio-yield, excess-spread, DSCR) stay
    ``unmapped`` — honest degradation, not a gap.
    """

    cumulative_loss_rate = "cumulative_loss_rate"
    cumulative_default_rate = "cumulative_default_rate"  # gross default ≠ net loss
    class_a_pdl = "class_a_pdl"
    class_b_pdl = "class_b_pdl"
    class_c_pdl = "class_c_pdl"  # deeper PDL ledger
    reserve_fund_ratio = "reserve_fund_ratio"
    pool_factor = "pool_factor"
    arrears_30d_ratio = "arrears_30d_ratio"
    arrears_60d_ratio = "arrears_60d_ratio"
    arrears_90d_ratio = "arrears_90d_ratio"
    arrears_180d_ratio = "arrears_180d_ratio"
    wa_ltv = "wa_ltv"
    # ---- Coverage tests, one member per attachment point (#452) ----
    # Overcollateralisation: collateral balance / notes at-or-senior-to the
    # point. Interest coverage: interest collections / interest due on those
    # same notes. Both are evaluated on the covenant monitor's percent scale.
    class_a_oc_ratio = "class_a_oc_ratio"
    class_b_oc_ratio = "class_b_oc_ratio"
    class_c_oc_ratio = "class_c_oc_ratio"
    class_d_oc_ratio = "class_d_oc_ratio"
    class_e_oc_ratio = "class_e_oc_ratio"
    class_f_oc_ratio = "class_f_oc_ratio"
    class_a_ic_ratio = "class_a_ic_ratio"
    class_b_ic_ratio = "class_b_ic_ratio"
    class_c_ic_ratio = "class_c_ic_ratio"
    class_d_ic_ratio = "class_d_ic_ratio"
    class_e_ic_ratio = "class_e_ic_ratio"
    class_f_ic_ratio = "class_f_ic_ratio"
    unmapped = "unmapped"


# ---------------------------------------------------------------------------
# Amount, condition, step.
# ---------------------------------------------------------------------------


class AmountRule(BaseModel):
    """How much a waterfall step pays — a bound calculator key, not a formula.

    Attributes:
        calculator: The recipient whose engine need-calculator computes the
                    amount.
        basis:      Which fixed engine formula computes the amount.
                    ``"report_supplied"`` means there is no engine formula — the
                    amount comes from ``PeriodInputs.step_overrides``;
                    ``"residual"`` is the terminal "whatever remains" step.
        raw_text:   The verbatim prose the amount was extracted from, retained
                    for audit only (never executed).
    """

    calculator: RecipientType = Field(
        ..., description="Binds to the engine's need-calculator for this recipient."
    )
    basis: AmountBasis = Field(
        ..., description="Which fixed engine formula computes the amount."
    )
    raw_text: str = Field(..., description="Verbatim prose, for audit.")


class ConditionRef(BaseModel):
    """A gate on a step, referencing a :class:`TriggerRule` by name.

    Attributes:
        trigger_name: The ``name`` of a :class:`TriggerRule` in
                      ``DealRules.triggers``.
        when:         The gate direction — the step applies when the named
                      trigger is ``"breached"`` or ``"not_breached"``.
    """

    trigger_name: str = Field(..., description="References a TriggerRule by name.")
    when: Literal["breached", "not_breached"] = Field(
        ..., description="Gate direction."
    )


class StepRule(BaseModel):
    """One step in a payment waterfall.

    Attributes:
        order:           Absolute order within the waterfall.
        priority_label:  The prospectus's own label for the step, e.g. ``"(a)"``
                         or ``"5.2(a)"``. Also the key used in
                         ``PeriodInputs.step_overrides`` / ``step_sources``.
        recipient:       Who the step pays.
        amount:          How much it pays.
        condition:       The gate, if any. ``None`` = unconditional.
        pari_passu_group: Equal-ranking parties share a group id; ``None`` for a
                          step that ranks alone.
    """

    order: int = Field(..., description="Absolute order within the waterfall.")
    priority_label: str = Field(..., description='Prospectus label, e.g. "5.2(a)".')
    recipient: RecipientType = Field(..., description="Who the step pays.")
    amount: AmountRule = Field(..., description="How much the step pays.")
    condition: ConditionRef | None = Field(
        default=None, description="Gate on the step; None = unconditional."
    )
    pari_passu_group: str | None = Field(
        default=None, description="Equal-ranking parties share a group id."
    )


# ---------------------------------------------------------------------------
# Triggers / covenants.
# ---------------------------------------------------------------------------


class TriggerRule(BaseModel):
    """A covenant / performance trigger tested each period.

    ``threshold_unit`` is normalised **once, here** — the single locked place
    units are fixed, so a dropped or mismatched unit (the C8 ``100x`` bug) cannot
    re-enter at a boundary downstream. The *consumption* side enforces the same
    contract at the covenant-monitor seam:
    :func:`loanwhiz.primitives.covenant_monitor.to_canonical_threshold` (called
    from ``api.main._map_extracted_trigger``) converts the threshold onto the
    monitor's canonical percent scale before evaluation, so a unit mistake fails
    loudly at the monitor rather than silently misreading by 100x.

    Attributes:
        name:           Unique name; referenced by :class:`ConditionRef`.
        metric:         The canonical metric tested.
        operator:       The comparison against ``threshold``.
        threshold:      The numeric threshold, or ``None`` for a qualitative /
                        not-yet-quantified trigger.
        threshold_unit: The unit ``threshold`` is expressed in — normalised once.
        consequence:    Plain-language effect when the trigger fires, e.g.
                        ``"switch to sequential pay"``.
    """

    name: str = Field(..., description="Unique trigger name.")
    metric: MetricType = Field(..., description="Canonical metric tested.")
    operator: Literal["<", "<=", ">", ">=", "=="] = Field(
        ..., description="Comparison against threshold."
    )
    threshold: float | None = Field(
        ..., description="Numeric threshold; None = qualitative / not quantified."
    )
    threshold_unit: Literal["percent", "fraction", "bps", "eur"] = Field(
        ..., description="Unit of threshold — normalised ONCE, here."
    )
    consequence: str = Field(
        ..., description='Effect when the trigger fires, e.g. "switch to sequential pay".'
    )


# ---------------------------------------------------------------------------
# Tranches, rate, reserve.
# ---------------------------------------------------------------------------


class RateRule(BaseModel):
    """A tranche's coupon — fixed or floating.

    Attributes:
        kind:       ``"fixed"`` or ``"floating"``.
        fixed_pct:  The fixed coupon (e.g. ``0.035`` for 3.5%) when ``kind`` is
                    ``"fixed"``.
        index:      The reference index (e.g. ``"EURIBOR_3M"``) when floating.
        margin_bps: The margin over ``index`` in basis points when floating.
    """

    kind: Literal["fixed", "floating"] = Field(..., description="Coupon kind.")
    fixed_pct: float | None = Field(
        default=None, description="Fixed coupon fraction when kind == 'fixed'."
    )
    index: str | None = Field(
        default=None, description='Reference index, e.g. "EURIBOR_3M", when floating.'
    )
    margin_bps: float | None = Field(
        default=None, description="Margin over index in bps when floating."
    )


class TrancheRule(BaseModel):
    """One note class in the capital structure.

    Attributes:
        name:             Class name, e.g. ``"Class A"``.
        seniority:        ``0`` = most senior; higher = more junior.
        original_balance: Issued balance at closing.
        rate:             The tranche's coupon.
        rating:           Credit rating string, if rated.
    """

    name: str = Field(..., description='Class name, e.g. "Class A".')
    seniority: int = Field(..., description="0 = most senior.")
    original_balance: float = Field(..., description="Issued balance at closing.")
    rate: RateRule = Field(..., description="The tranche's coupon.")
    rating: str | None = Field(default=None, description="Credit rating, if rated.")


class ReserveRule(BaseModel):
    """The cash reserve account's sizing rule.

    The target is ``max(floor, pct_of_note_balance * note_balance)``.

    Attributes:
        floor:               Absolute minimum reserve balance.
        pct_of_note_balance: Target as a fraction of the note balance, if the
                             reserve is sized as a percentage; ``None`` for a
                             flat-floor reserve.
    """

    floor: float = Field(default=0.0, description="Absolute minimum reserve balance.")
    pct_of_note_balance: float | None = Field(
        default=None,
        description="Target = max(floor, pct * note_balance); None for flat floor.",
    )


# ---------------------------------------------------------------------------
# The aggregate.
# ---------------------------------------------------------------------------

# The three named waterfalls. A redemption (principal) waterfall and a revenue
# (interest) waterfall run each period; the post-enforcement waterfall replaces
# them after an event of default.
WaterfallKind = Literal["revenue", "redemption", "post_enforcement"]


class DealRules(BaseModel):
    """The canonical, period-invariant program for one deal.

    Filled by the prospectus extractor; consumed directly by the engine.
    ``provenance`` is the sidecar map (keyed by dotted field path) the governance
    layer reads; ``completeness`` is the field-based score (see
    :func:`compute_completeness`) that replaces the old header-count metric.
    """

    deal_id: str = Field(..., description="Stable deal identifier.")
    deal_name: str = Field(..., description="Human-readable deal name.")
    jurisdiction: str = Field(..., description="Governing jurisdiction.")
    currency: str = Field(default="EUR", description="Deal currency.")
    tranches: list[TrancheRule] = Field(..., description="Capital structure.")
    waterfalls: dict[WaterfallKind, list[StepRule]] = Field(
        ..., description="The named payment waterfalls."
    )
    triggers: list[TriggerRule] = Field(..., description="Covenants / triggers.")
    reserve: ReserveRule = Field(..., description="Reserve account sizing rule.")
    provenance: ProvenanceMap = Field(
        default_factory=dict, description="Sidecar provenance, keyed by dotted path."
    )
    completeness: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Fraction of required canonical fields filled (see compute_completeness).",
    )

    def compute_completeness(self) -> float:
        """Return the field-based completeness fraction for these rules.

        Completeness is the fraction of the **required canonical fields** that
        are populated with non-null, in-taxonomy values — the minimum set to
        drive the engine (spec "Completeness — honest, field-based"). This
        replaces the old header-count metric, which read ``1.0`` on a
        structurally empty model.

        The five required conditions, each worth ``1/5``:

        1. ≥1 tranche with an ``original_balance`` and a ``rate``.
        2. A ``revenue`` waterfall with ≥1 step whose ``recipient != unmapped``.
        3. A ``redemption`` waterfall with ≥1 step.
        4. A resolvable ``reserve`` target (a ``floor`` or a
           ``pct_of_note_balance``).
        5. ≥1 trigger with a non-null ``threshold``.

        A step with ``recipient == unmapped`` does **not** count toward
        condition 2 — an exotic, non-evaluable step adds no engine capability.

        This is a pure read over the current field values; it does not mutate
        ``self.completeness``. Callers assign the result explicitly.
        """
        checks: list[bool] = [
            # 1. At least one usable tranche.
            any(
                t.original_balance is not None and t.rate is not None
                for t in self.tranches
            ),
            # 2. A revenue waterfall with an evaluable (non-unmapped) step.
            any(
                step.recipient != RecipientType.unmapped
                for step in self.waterfalls.get("revenue", [])
            ),
            # 3. A redemption waterfall with at least one step.
            len(self.waterfalls.get("redemption", [])) >= 1,
            # 4. A resolvable reserve target.
            self.reserve.floor > 0.0 or self.reserve.pct_of_note_balance is not None,
            # 5. At least one quantified trigger.
            any(trigger.threshold is not None for trigger in self.triggers),
        ]
        return sum(checks) / len(checks)
