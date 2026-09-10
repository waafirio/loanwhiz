"""Model-driven waterfall interpreter — the deal-agnostic execution core.

This module is the reusable, deal-agnostic heart of S4 (#184). It replaces the
hardcoded Green-Lion-specific priority-of-payments logic in ``waterfall_runner``
with a **generic interpreter** that executes an *ordered list of steps* — the
same shape the extraction layer produces in
``DealModel.waterfalls[*].steps`` (``waterfall_extractor.ExtractedWaterfall``).

The interpreter knows nothing about any specific deal. A waterfall is data:

    StepSpec(priority="(d)", recipient="class_a_interest")
    StepSpec(priority="(b)", recipient="operating_fees", pari_passu_group="ops")

and the interpreter walks those steps in order, paying each recipient out of a
running pot of available funds. Three behaviours make it general:

1. **A recipient→need-calculator registry** (`NEED_CALCULATORS`). Each recipient
   *kind* registers a pure function that computes how much that recipient is
   owed this period from the period's funds + deal state (e.g.
   ``class_a_interest`` = balance × rate × days/360; ``reserve_replenishment`` =
   target − balance). An unknown recipient contributes need 0 and is recorded as
   ``not_evaluable`` so an unrecognised extracted step degrades gracefully
   instead of crashing.

2. **Condition → predicate evaluation** (`ConditionEvaluator`). A step may carry
   a free-text ``condition`` (e.g. *"if the Sequential Pay Trigger is not in
   effect"*). The interpreter does **not** parse prose itself — it delegates to
   an injected ``ConditionEvaluator``. This is the seam S5 (#185, the trigger
   engine) plugs into: S4 ships a ``DefaultConditionEvaluator`` that handles the
   conditions Green Lion uses, and accepts any evaluator so #185 can supply the
   real trigger engine over ``DealState`` without either side editing the
   other's internals. **S4 consumes trigger results; S5 produces them.**

3. **Pari-passu groups** (`StepSpec.pari_passu_group`). Steps sharing a group id
   rank equally: when available funds cannot cover the group's combined need, the
   shortfall is split **pro-rata by need** across the group's members.

The interpreter's output (`WaterfallExecution`) carries a full ordered audit
trace (`StepResult` per step) and a ``to_waterfall_result()`` that maps the
distributions into the S1 ``WaterfallResult`` DTO
(``deal_state.WaterfallResult``) consumed by ``DealState.apply_waterfall_result``.

Sequential-pay branch
---------------------
The pro-rata ↔ sequential principal-allocation choice (the *Sequential Pay
Trigger*, a since-closed modelling gap — see ``SYSTEM-STATUS.md``) is
expressed through the same
condition→predicate seam: ``allocate_principal`` reads
``evaluator.sequential_pay_active(funds)`` and allocates either senior-first
(sequential) or pro-rata by outstanding balance.

Pure & deterministic — no LLM, no I/O. Mirrors the immutable, typed-pydantic
conventions of the surrounding primitives.
"""

from __future__ import annotations

import re
from typing import Callable, Protocol, runtime_checkable

from pydantic import BaseModel, Field, computed_field, model_validator

from loanwhiz.domain.rules import (
    RECIPIENT_SPELLINGS,
    RECOGNISED_UNEVALUABLE_RECIPIENTS,
    NeedSource,
    RecipientType,
    basis_for,
    need_source_for,
    recipients_needing_calculator,
)
from loanwhiz.primitives.capital_structure import resolve_strips
from loanwhiz.primitives.deal_state import TranchePayment, WaterfallResult

# Small tolerance for floating-point comparisons (EUR amounts).
_EPS = 1e-6

# Canonical A/B/C tranche names, senior→junior — the layout the legacy
# ``class_{a,b,c}_*`` accessors map onto and the default ``allocate_principal``
# order. The interpreter itself never requires these names; a deal supplies its
# own tranche set as data.
_CANONICAL_TRANCHE_NAMES: tuple[str, ...] = ("class_a", "class_b", "class_c")


# ---------------------------------------------------------------------------
# StepSpec — one normalized step the interpreter executes
# ---------------------------------------------------------------------------


class StepSpec(BaseModel):
    """One normalized priority step the interpreter executes.

    This is the deal-agnostic shape the interpreter consumes. It can be built
    from an extracted ``waterfall_extractor.WaterfallStep`` (the model-driven
    path, via :meth:`from_extracted`) or constructed directly (the builtin
    Green-Lion path and tests).

    Attributes
    ----------
    priority:
        The step label from the source document, e.g. ``"(a)"``. Ordering is by
        list position, not by parsing this label.
    recipient:
        Snake-case recipient identifier, e.g. ``"class_a_interest"``. Used to
        look up the need-calculator in :data:`NEED_CALCULATORS`.
    condition:
        Free-text trigger condition gating this step, or ``None`` when the step
        is unconditional. Evaluated by the injected ``ConditionEvaluator`` — the
        interpreter never parses this prose itself.
    condition_terms:
        Canonical names of the **defined terms** this step's ``condition``
        references, as linked by the definitions graph
        (``DefinitionsGraph.link``) at assembly time. Empty when nothing was
        linked (the default). The evaluator consults these to recognise a named
        trigger even when the raw ``condition`` prose phrases it differently —
        this is what makes conditional waterfall prose resolve against its
        definition instead of falling through to the "unknown → pay" default.
    pari_passu_group:
        An optional group id. Steps sharing a group id rank equally and split a
        shortfall pro-rata by need. ``None`` means the step ranks alone.
    residual:
        When ``True`` this step is a *residual sweep* — it distributes whatever
        funds remain in the pot at its position and never reports a shortfall
        (its need is, by definition, exactly what is left). Used for terminal
        steps like "Deferred Purchase Price to Seller". Mutually exclusive with
        ``pari_passu_group`` in practice (a residual ranks alone).
    """

    priority: str
    recipient: str
    condition: str | None = None
    condition_terms: list[str] = Field(default_factory=list)
    pari_passu_group: str | None = None
    residual: bool = False

    @classmethod
    def from_extracted(cls, step: dict, *, group_prefix: str = "pp") -> "StepSpec":
        """Build a ``StepSpec`` from an extracted step dict.

        Accepts the ``waterfall_extractor.WaterfallStep`` JSON shape (as stored
        in ``DealModel.waterfalls[*]["steps"]``): keys ``priority``,
        ``recipient``, ``condition``, ``is_pari_passu``, and the assembler-added
        ``condition_terms`` (the defined-term names the condition links to —
        absent on legacy/builtin steps, defaulting to ``[]``).

        A step flagged ``is_pari_passu`` is assigned a group id derived from its
        priority label (``"<group_prefix>:<priority>"``) so each pari-passu step
        forms its own single-member group by default. Callers that know two
        steps rank *together* can override ``pari_passu_group`` after
        construction (or build the specs directly). This keeps the extracted
        boolean meaningful — a pari-passu step splits a shortfall pro-rata even
        as a singleton — without inventing group membership the extractor never
        captured (a since-closed modelling gap; see ``SYSTEM-STATUS.md``).
        """
        condition = step.get("condition")
        condition = condition or None  # "" → None
        terms = step.get("condition_terms") or []
        # Be tolerant of the serialised shape: accept a list of bare term names
        # or a list of {"term": ...} dicts (the assembler emits bare names).
        condition_terms = [
            t.get("term", "") if isinstance(t, dict) else str(t)
            for t in terms
        ]
        condition_terms = [t for t in condition_terms if t]
        group: str | None = None
        if step.get("is_pari_passu"):
            group = f"{group_prefix}:{step.get('priority', '')}"
        return cls(
            priority=str(step.get("priority", "")),
            recipient=str(step.get("recipient", "")),
            condition=condition,
            condition_terms=condition_terms,
            pari_passu_group=group,
        )


# ---------------------------------------------------------------------------
# WaterfallFunds — the available funds + deal context the needs read from
# ---------------------------------------------------------------------------


class TrancheFunds(BaseModel):
    """One tranche's funds-context for a period's waterfall run, keyed by name.

    The generalised, deal-agnostic carrier of the three per-tranche inputs the
    need-calculators and the principal allocation read — outstanding ``balance``,
    annual coupon ``rate_pct``, and outstanding ``pdl_balance``. ``WaterfallFunds``
    stores a list of these by ``name``; the legacy ``class_{a,b,c}_*`` names
    remain available as accessors over that list.

    Attributes
    ----------
    name:
        Tranche name (matches the ``DealState`` tranche and the recipient
        prefixes like ``class_a_interest`` / ``class_a_principal``).
    balance:
        Outstanding tranche balance (drives interest + principal needs).
    rate_pct:
        Annual coupon rate in percent, or ``None`` when the coupon could not be
        resolved. The two are different answers and must not collapse: ``0.0``
        is a real 0% coupon (owed nothing, evaluable), ``None`` is an unknown
        one, and the interest need reports it ``not_evaluable`` rather than
        accruing zero. A floating coupon the capital structure declined to
        coerce into a number (``"3 month EURIBOR + 1.80%"``) arrives as ``None``.
    pdl_balance:
        Outstanding PDL debit balance (the replenishment need).
    deferred_interest_balance:
        Accrued-but-unpaid (PIK'd) interest rolled up from earlier periods — the
        need of a CLO's ``class_*_deferred_interest`` step, which is a *separate*
        step from the class's current-period coupon (#503). Defaults to ``0.0``,
        and that default is a real answer ("nothing is deferred on this class"),
        exactly as ``pdl_balance``'s is — an absent *tranche* is the unknown, not
        an absent balance.
    days_in_period:
        This tranche's own day count for the period, overriding the deal-wide
        ``WaterfallFunds.days_in_period`` when set (#539). ``None`` — the usual
        case — means "no per-class convention was sourced for this tranche", and
        the deal-wide count applies unchanged.

        It exists because a day-count convention is a **per-class** fact, not a
        per-deal one: Cairn CLO XVII issues Class B in two strips whose
        Conditions state different bases, so B-1 accrues over the actual days
        between two adjusted Payment Dates while B-2 accrues on 30/360 between
        the two unadjusted ones. A single deal-level count cannot express that
        at all. Only the *numerator* varies — every basis the Conditions state
        divides by 360 — which is why this is a day count rather than a formula.
    """

    name: str = Field(..., description="Tranche name.")
    balance: float = Field(default=0.0, ge=0.0)
    rate_pct: float | None = Field(default=None, ge=0.0)
    pdl_balance: float = Field(default=0.0, ge=0.0)
    deferred_interest_balance: float = Field(default=0.0, ge=0.0)
    days_in_period: int | None = Field(default=None, gt=0)


class WaterfallFunds(BaseModel):
    """Available funds and deal context for one period's waterfall run.

    This is the input the need-calculators and the condition evaluator read
    from. It is a plain value object (no dependency on ``DealState`` — the two
    are kept decoupled so there is no import cycle and S5 can construct funds
    from whatever structural source it holds). All amounts are in the deal
    currency and non-negative; rates are percent per annum.

    Attributes
    ----------
    available_revenue_funds:
        Pot the revenue waterfall distributes (interest + swap receipts).
    available_principal_funds:
        Pot the redemption waterfall distributes (principal collections).
    senior_fees:
        Senior/trustee fee need for the senior-fees step.
    swap_payment:
        Net non-subordinated swap need (0.0 when no swap).
    class_a_balance / class_b_balance / class_c_balance:
        Outstanding tranche balances (drive interest + principal needs).
    class_a_rate_pct / class_b_rate_pct / class_c_rate_pct:
        Per-tranche annual coupon rates in percent; ``None`` when the class is
        absent or its coupon is unresolved.
    class_a_pdl_balance / class_b_pdl_balance / class_c_pdl_balance:
        Outstanding PDL debit balances (the replenishment needs).
    reserve_balance / reserve_target:
        Reserve account current balance and target (top-up need = target − bal).
    liquidity_reserve_balance / liquidity_reserve_target:
        The **separate** liquidity / commingling / set-off reserve, kept distinct
        from the general reserve fund so a deal carrying both tops each up to its
        own target instead of the two sharing one ledger.
    collateral_balance:
        Aggregate collateral (pool) principal balance — the base a percent-of-
        collateral fee accrues on. Deliberately not CLO-specific: a CMBS or Auto
        servicing fee accrues on the same base.
    fee_rates_pct:
        ``recipient → annual fee rate in percent`` for the ``fee_accrual``
        recipients (the senior / subordinated management fees). A recipient
        **absent** from this map has an unknown rate, and its calculator reports
        ``not_evaluable`` rather than a zero fee — the distinction the silent-zero
        bug class turns on.
    days_in_period:
        Deal-wide day count for interest accrual. A tranche carrying its own
        ``TrancheFunds.days_in_period`` (a per-class convention sourced from the
        deal's Conditions, #539) uses that instead; this remains the count for
        every tranche that states none, and the base for fee accrual.
    sequential_pay:
        Whether the Sequential Pay Trigger is in effect this period. The default
        condition evaluator reads this; S5 may instead compute it. ``None``
        means "unknown — let the evaluator decide" (the default treats unknown
        as sequential, the conservative senior-protective stance).
    flags:
        Free-form named booleans the condition evaluator can consult for
        deal-specific gates (e.g. ``{"first_optional_redemption_date": True}``).
    """

    available_revenue_funds: float = Field(default=0.0, ge=0.0)
    available_principal_funds: float = Field(default=0.0, ge=0.0)

    senior_fees: float = Field(default=0.0, ge=0.0)
    swap_payment: float = Field(default=0.0, ge=0.0)

    # Canonical per-tranche funds context, keyed by name. The legacy
    # ``class_{a,b,c}_balance|_rate_pct|_pdl_balance`` names remain available as
    # accessors (and accepted as construction kwargs) over this list — see
    # ``_coerce_class_kwargs`` and the computed fields below.
    tranches: list[TrancheFunds] = Field(default_factory=list)

    reserve_balance: float = Field(default=0.0, ge=0.0)
    reserve_target: float = Field(default=0.0, ge=0.0)

    liquidity_reserve_balance: float = Field(default=0.0, ge=0.0)
    liquidity_reserve_target: float = Field(default=0.0, ge=0.0)

    collateral_balance: float = Field(default=0.0, ge=0.0)
    fee_rates_pct: dict[str, float] = Field(default_factory=dict)

    days_in_period: int = Field(default=90, gt=0)

    sequential_pay: bool | None = None
    flags: dict[str, bool] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _coerce_class_kwargs(cls, data: object) -> object:
        """Accept legacy ``class_<x>_balance|_rate_pct|_pdl_balance`` kwargs.

        Folds any such kwargs into the ``tranches`` list (canonical A/B/C order
        first, then any other names) when ``tranches`` is not supplied directly,
        so existing construction sites and tests are unbroken.
        """
        if not isinstance(data, dict):
            return data
        if data.get("tranches"):
            return data
        balances: dict[str, float] = {}
        rates: dict[str, float | None] = {}
        pdls: dict[str, float] = {}
        for key in list(data.keys()):
            if not key.startswith("class_"):
                continue
            if key.endswith("_pdl_balance"):
                pdls[key[: -len("_pdl_balance")]] = data.pop(key)
            elif key.endswith("_rate_pct"):
                rates[key[: -len("_rate_pct")]] = data.pop(key)
            elif key.endswith("_balance"):
                balances[key[: -len("_balance")]] = data.pop(key)
        if not balances and not rates and not pdls:
            return data
        all_names = list(balances) + list(rates) + list(pdls)
        names = list(_CANONICAL_TRANCHE_NAMES) + [
            n for n in all_names if n not in _CANONICAL_TRANCHE_NAMES
        ]
        seen: set[str] = set()
        tranches: list[dict[str, float | str | None]] = []
        for name in names:
            if name in seen or name not in all_names:
                continue
            seen.add(name)
            tranches.append(
                {
                    "name": name,
                    "balance": balances.get(name, 0.0),
                    # No default: a class folded in from a bare
                    # ``class_x_balance`` kwarg has an UNRESOLVED coupon, not a
                    # 0% one, and defaulting here would service it for free.
                    "rate_pct": rates.get(name),
                    "pdl_balance": pdls.get(name, 0.0),
                }
            )
        data["tranches"] = tranches
        return data

    def tranche(self, name: str) -> TrancheFunds | None:
        """The :class:`TrancheFunds` named ``name``, or ``None`` if absent."""
        for t in self.tranches:
            if t.name == name:
                return t
        return None

    def tranche_strips(self, class_name: str) -> list[TrancheFunds]:
        """Every strip the class ``class_name`` was issued in, in stack order.

        A recipient names a **class**; an issuer sells that class in one or more
        **strips**. Green Lion sells Class B whole, so ``class_b`` is one tranche
        and this returns it alone. Cairn sells Class B in two — ``class_b_1``
        floating and ``class_b_2`` fixed — so ``class_b`` names no tranche at all
        and the class is exactly those two. Split classes are ordinary in CLOs
        (A-1/A-2, B-1/B-2, a refinanced A-R), which is why the resolution lives
        here rather than in an alias table pointing one class at one strip: an
        alias would have to pick a strip, and picking one computes a fraction of
        the class's need and ties to nothing.

        The grammar is the sub-series one :mod:`loanwhiz.extraction.assembler`
        already defines on the document side — a class letter followed by an
        optional series **number** — read here through the slug
        :func:`~loanwhiz.primitives.capital_structure.engine_tranche_name`
        produces from it. Both spellings in the committed corpus are covered:
        hyphen-separated ``"Class B-1"`` slugs to ``class_b_1`` and joined
        ``"Class A1"`` to ``class_a1`` (#456), so matching only one of them would
        drop a real deal's stack on the floor with no error anywhere.

        **A lettered suffix is deliberately not a series.** A refinanced
        ``"Class A-R"`` (``class_a_r``) is conventionally a *replacement* for
        Class A rather than a second strip sold alongside it, so sweeping it in
        would double-count wherever both are present — and the assembler's
        grammar does not treat it as a series either. A deal carrying only
        ``class_a_r`` therefore resolves ``class_a`` to no strips and **refuses**,
        which is the honest answer while "replacement or addition" is undecided;
        deciding it is a modelling question, not a matter for a regex here.

        **An exact match wins outright.** A tranche named for the class itself is
        the class, and the series scan is skipped — so a stack that carried both
        an aggregate ``class_a`` row and its ``class_a_1``/``class_a_2``
        components (an extraction that read a total line as a tranche) reports the
        aggregate once instead of paying the class roughly twice. It also makes
        the single-strip path *structurally* identical to the pre-#538 lookup
        rather than identical only because no committed deal happens to spell
        both.

        Returns ``[]`` when the class was issued in no strip. That is the
        **unknown** answer, and every caller must keep it distinct from a need of
        zero — see :func:`_make_tranche_interest_need` (#493).
        """
        selected = resolve_strips(class_name, [t.name for t in self.tranches])
        if selected == [class_name]:
            # Exact match. Resolved through ``self.tranche`` rather than by
            # filtering, because this collection — unlike ``CapitalStructure``,
            # whose builder refuses duplicates — does **not** enforce unique
            # tranche names, and the pre-#571 code took the first bearer here.
            exact = self.tranche(class_name)
            return [] if exact is None else [exact]
        # Series match. Filter the tranche list rather than indexing a
        # ``{name: tranche}`` dict: a dict silently collapses two strips that
        # share a name, and the need calculators SUM these, so a dropped
        # duplicate under-states the class's need — an error that reads as
        # health, never as a bug (#452).
        picked = set(selected)
        return [t for t in self.tranches if t.name in picked]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_a_balance(self) -> float:
        t = self.tranche("class_a")
        return t.balance if t is not None else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_b_balance(self) -> float:
        t = self.tranche("class_b")
        return t.balance if t is not None else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_c_balance(self) -> float:
        t = self.tranche("class_c")
        return t.balance if t is not None else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_a_rate_pct(self) -> float | None:
        t = self.tranche("class_a")
        return t.rate_pct if t is not None else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_b_rate_pct(self) -> float | None:
        t = self.tranche("class_b")
        return t.rate_pct if t is not None else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_c_rate_pct(self) -> float | None:
        t = self.tranche("class_c")
        return t.rate_pct if t is not None else None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_a_pdl_balance(self) -> float:
        t = self.tranche("class_a")
        return t.pdl_balance if t is not None else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_b_pdl_balance(self) -> float:
        t = self.tranche("class_b")
        return t.pdl_balance if t is not None else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_c_pdl_balance(self) -> float:
        t = self.tranche("class_c")
        return t.pdl_balance if t is not None else 0.0


# ---------------------------------------------------------------------------
# Need-calculator registry
# ---------------------------------------------------------------------------

#: A need-calculator: ``(funds) -> need``, or ``None`` when the funds carry no
#: inputs for this recipient. ``None`` is **not** zero — it means "I cannot
#: evaluate this", and :func:`compute_need` turns it into ``not_evaluable`` so
#: an unconfigured fee never lands an authoritative-looking ``0.0`` in a
#: distribution (#453).
NeedCalculator = Callable[[WaterfallFunds], "float | None"]

#: ``recipient kind → (funds) -> need``. A recipient with no registered
#: calculator contributes 0 and is recorded ``not_evaluable`` in the trace.
#:
#: Keys are canonical :class:`~loanwhiz.domain.rules.RecipientType` values plus
#: the non-canonical spellings declared in
#: :data:`~loanwhiz.domain.rules.RECIPIENT_SPELLINGS`. :func:`register_need`
#: refuses anything else, and the module-tail
#: :func:`_assert_registry_covers_contract` refuses to import if a recipient the
#: contract says needs a calculator has none.
NEED_CALCULATORS: dict[str, NeedCalculator] = {}


def register_need(*recipients: str) -> Callable[[NeedCalculator], NeedCalculator]:
    """Decorator registering a need-calculator for one or more recipient kinds.

    A need-calculator is a pure function ``(WaterfallFunds) -> float | None``
    returning the (non-negative) amount this recipient is owed this period, or
    ``None`` when the funds carry no inputs to compute it.

    **This is the boundary guard for the enum ↔ registry contract (#453).**
    Registration is refused for:

    - a name that is neither a :class:`~loanwhiz.domain.rules.RecipientType`
      value nor one of the declared legacy spellings — a calculator keyed by a
      name no canonical step can ever carry is dead code that reads like
      coverage; and
    - a recipient whose declared
      :class:`~loanwhiz.domain.rules.NeedSource` is not registry-backed — a
      calculator for a ``principal_due`` recipient would race
      :func:`allocate_principal`, and one for a ``step_override`` recipient
      would overwrite the servicer's actual with an invented figure.

    #394 broadened ``RecipientType`` and registered nothing; the registry drifted
    from the enum silently because nothing here could say no. Now it can.

    Raises:
        ValueError: if any name in ``recipients`` violates either rule.
    """

    for name in recipients:
        recipient = _canonical_recipient(name)
        if recipient is None:
            raise ValueError(
                f"register_need({name!r}): not a RecipientType value and not a "
                "declared spelling. Add the recipient to RecipientType (with "
                "its RECIPIENT_BASIS / RECIPIENT_NEED_SOURCE rows), or declare "
                "the spelling in LEGACY_/CLO_RECIPIENT_SPELLINGS."
            )
        source = need_source_for(recipient)
        if source not in _REGISTRY_BACKED_SOURCES:
            raise ValueError(
                f"register_need({name!r}): {recipient.value} declares "
                f"NeedSource.{source.value}, whose need does not come from this "
                "registry. Registering one here would override the real source."
            )

    def _decorator(fn: NeedCalculator) -> NeedCalculator:
        for name in recipients:
            NEED_CALCULATORS[name] = fn
        return fn

    return _decorator


#: The need sources whose amount the interpreter looks up in this registry.
_REGISTRY_BACKED_SOURCES = frozenset({NeedSource.calculator, NeedSource.funds_input})


def _canonical_recipient(name: str) -> RecipientType | None:
    """The :class:`RecipientType` ``name`` denotes, or ``None`` if it denotes none.

    Accepts a canonical enum value or a declared non-canonical spelling — the
    two vocabularies the registry is legitimately keyed by. A string in
    :data:`RECOGNISED_UNEVALUABLE_RECIPIENTS` resolves to
    :attr:`RecipientType.unmapped`: we know what it says and have decided the
    engine cannot place it, which is a different answer from ``None`` ("nobody
    spelled this name") and must stay different — see
    :func:`refusal_reason` (#503).
    """
    try:
        return RecipientType(name)
    except ValueError:
        pass
    if name in RECOGNISED_UNEVALUABLE_RECIPIENTS:
        return RecipientType.unmapped
    return RECIPIENT_SPELLINGS.get(name)


def _accrued_interest(balance: float, rate_pct: float, days: int) -> float:
    """Accrued interest: balance × (rate/100) / 360 × ``days``.

    Every day-count basis the Conditions state divides by 360 — Act/360 and
    30/360 alike — so the convention lives entirely in how ``days`` was counted,
    never here. See :mod:`loanwhiz.extraction.day_count_parser`.
    """
    return balance * (rate_pct / 100.0) / 360.0 * days


# --- Calculator factories, one per amount basis --------------------------------
#
# The calculators are GENERATED from ``RecipientType`` rather than written out
# one per member (#453). Nine were missing because each new enum member needed
# a hand-written twin nobody wrote; deriving them means a member added to a
# family is covered the moment it exists. It also keeps the note-class range
# out of this module entirely: the enum bounds the family, so there is no
# hardcoded A–F alphabet here to under-reach the way #397's A–G cap did.


def _make_tranche_interest_need(tranche: str) -> NeedCalculator:
    """Coupon accrual over every strip of class ``tranche``; ``None`` if unanswerable.

    The recipient names a **class**, so the need is the sum of the accruals of
    the strips that class was issued in (:meth:`WaterfallFunds.tranche_strips`).
    A class sold whole is one strip and the sum is that strip's accrual, which is
    why every single-strip deal is unchanged to the byte. A class sold in two —
    Cairn's Class B-1 floating and B-2 fixed, one published ``(H)`` step for both
    — is the sum of the two, and neither strip alone is the answer.

    Each strip is counted on **its own** day-count basis where its Condition
    states one (#539). The two strips above do not share a convention, so a sum
    taken on one day count is wrong however the strips are resolved.

    Three cases refuse, and the whole point is that they stay three. A class with
    **no strips** is *unknown*, not zero: a step paying Class D interest in a deal
    whose funds carry no Class D has an unanswerable need, and answering ``0.0``
    would put an authoritative-looking figure into the distribution. A strip with
    an unresolved coupon (``rate_pct is None``) makes the **whole class**
    unanswerable — every floating class of a real CLO reaches here, because the
    capital structure rightly refuses to coerce a margin like
    ``"3 month EURIBOR + 1.80%"`` into a rate, and accruing 0% would service the
    note stack for free. Summing only the strips that *do* resolve would be worse
    than either: a confidently wrong number where a refusal belongs, since
    nothing downstream could tell the partial sum from a whole one (#493).

    Only a class whose strips are **all** present *with* rates evaluates — and
    then a zero balance or a genuine 0% coupon is a real answer (fully amortised,
    or owed nothing) and still evaluates to 0.
    """

    def _need(funds: WaterfallFunds) -> float | None:
        strips = funds.tranche_strips(tranche)
        if not strips:
            return None
        total = 0.0
        for t in strips:
            if t.rate_pct is None:
                return None
            # Each strip accrues on the day count ITS OWN Condition states, and
            # only falls back to the deal-wide count when it states none (#539).
            # This is where the class-resolution and the per-class convention
            # meet: Cairn's Class B is one published step over a floating strip
            # on Act/360 and a fixed strip on 30/360, so the sum is right only if
            # each term is counted on its own basis. A deal whose strips share one
            # convention carries no per-strip count and is unchanged to the byte.
            days = (
                t.days_in_period
                if t.days_in_period is not None
                else funds.days_in_period
            )
            total += _accrued_interest(t.balance, t.rate_pct, days)
        return total

    _need.__name__ = f"_need_{tranche}_interest"
    return _need


def _make_tranche_pdl_need(tranche: str) -> NeedCalculator:
    """Cure class ``tranche``'s outstanding PDL; ``None`` when it has no strips.

    Resolved over the class's strips for the same reason as the interest need: a
    ledger is kept per strip, and a step curing "the Class B PDL" cures all of
    it. Unlike interest this family cannot refuse mid-sum — a PDL balance is a
    carried figure, always known once the strip exists — so the only refusal is
    a class that was issued in no strip at all.
    """

    def _need(funds: WaterfallFunds) -> float | None:
        strips = funds.tranche_strips(tranche)
        if not strips:
            return None
        return sum(t.pdl_balance for t in strips)

    _need.__name__ = f"_need_{tranche}_pdl_cure"
    return _need


def _make_tranche_deferred_interest_need(tranche: str) -> NeedCalculator:
    """Pay down ``tranche``'s deferred (PIK'd) interest; ``None`` with no tranche.

    Deliberately the same shape as :func:`_make_tranche_pdl_need` rather than
    :func:`_make_tranche_interest_need`: the need is an outstanding *balance*
    carried on the funds, not an accrual the engine recomputes. That is what
    keeps a cascade paying both ``class_c_interest`` and
    ``class_c_deferred_interest`` from charging one period's coupon twice — and
    it is why this family does **not** refuse on an unresolved ``rate_pct``: a
    deferred balance is already-crystallised interest, so it is known even while
    the current coupon is not. Summed over the class's strips, like both of its
    siblings — a class deferring interest defers it on every strip it was issued
    in.
    """

    def _need(funds: WaterfallFunds) -> float | None:
        strips = funds.tranche_strips(tranche)
        if not strips:
            return None
        return sum(t.deferred_interest_balance for t in strips)

    _need.__name__ = f"_need_{tranche}_deferred_interest"
    return _need


def _make_reserve_need(target_field: str, balance_field: str) -> NeedCalculator:
    """Top-up need: ``max(0, target - balance)``.

    Always evaluable — a zero target is a real answer ("this deal tops up no
    such reserve"), not an absent input.
    """

    def _need(funds: WaterfallFunds) -> float | None:
        return max(
            0.0, getattr(funds, target_field) - getattr(funds, balance_field)
        )

    _need.__name__ = f"_need_{target_field.removesuffix('_target')}"
    return _need


def _make_collateral_fee_need(recipient: str) -> NeedCalculator:
    """Act/360 fee accrual on the collateral balance at this recipient's rate.

    ``collateral_balance × rate/100 / 360 × days`` — the shape a CLO's senior and
    subordinated management fees take, and the same shape a CMBS or Auto
    servicing fee takes, which is why the base is the collateral rather than a
    fee-specific field.

    Returns ``None`` when no rate is configured for this recipient. That is the
    honest answer and the load-bearing one: a missing rate is an unknown fee,
    and returning ``0.0`` would report "the manager is owed nothing this period"
    — a silent zero of exactly the class this issue exists to close.
    """

    def _need(funds: WaterfallFunds) -> float | None:
        rate_pct = funds.fee_rates_pct.get(recipient)
        if rate_pct is None:
            return None
        return _accrued_interest(funds.collateral_balance, rate_pct, funds.days_in_period)

    _need.__name__ = f"_need_{recipient}"
    return _need


def _make_funds_input_need(field: str) -> NeedCalculator:
    """Pass through a servicer-actual scalar already carried on the funds."""

    def _need(funds: WaterfallFunds) -> float | None:
        return getattr(funds, field)

    _need.__name__ = f"_need_{field}"
    return _need


#: ``funds_input`` recipient → the ``WaterfallFunds`` field carrying its actual.
#: Irregular by nature (the field names predate the enum), so declared rather
#: than derived; a member missing here fails the import-time assert below.
_FUNDS_INPUT_FIELD: dict[RecipientType, str] = {
    RecipientType.senior_expenses: "senior_fees",
    RecipientType.swap_payment: "swap_payment",
}

#: ``target_shortfall`` recipient → its ``(target, balance)`` fields.
_RESERVE_FIELDS: dict[RecipientType, tuple[str, str]] = {
    RecipientType.reserve_replenishment: ("reserve_target", "reserve_balance"),
    RecipientType.liquidity_reserve_replenishment: (
        "liquidity_reserve_target",
        "liquidity_reserve_balance",
    ),
}

_INTEREST_SUFFIX = "_interest"
_PDL_CURE_SUFFIX = "_pdl_cure"
_DEFERRED_INTEREST_SUFFIX = "_deferred_interest"


def _calculator_for(recipient: RecipientType) -> NeedCalculator | None:
    """Build the calculator ``recipient``'s declared basis calls for, if any."""
    basis = basis_for(recipient)
    if basis == "interest_accrual":
        return _make_tranche_interest_need(
            recipient.value.removesuffix(_INTEREST_SUFFIX)
        )
    if basis == "pdl_balance":
        return _make_tranche_pdl_need(recipient.value.removesuffix(_PDL_CURE_SUFFIX))
    if basis == "deferred_interest_balance":
        return _make_tranche_deferred_interest_need(
            recipient.value.removesuffix(_DEFERRED_INTEREST_SUFFIX)
        )
    if basis == "target_shortfall":
        fields = _RESERVE_FIELDS.get(recipient)
        return _make_reserve_need(*fields) if fields else None
    if basis == "fee_accrual":
        return _make_collateral_fee_need(recipient.value)
    if basis == "report_supplied":
        # Only reachable for a ``funds_input`` recipient — a ``step_override``
        # one is refused by ``register_need`` and never reaches here.
        field = _FUNDS_INPUT_FIELD.get(recipient)
        return _make_funds_input_need(field) if field else None
    return None


def _register_contract_calculators() -> None:
    """Register one calculator per registry-backed recipient, then its aliases.

    Runs once at import. Anything it cannot build is caught by
    :func:`_assert_registry_covers_contract` immediately below — so this
    function is allowed to be permissive, and a family it does not know how to
    generate surfaces as an ImportError rather than as a silent gap.
    """
    for recipient in recipients_needing_calculator():
        calc = _calculator_for(recipient)
        if calc is not None:
            register_need(recipient.value)(calc)
    # The free-string spellings resolve to the same function as their canonical
    # recipient — derived from the one declaration, so a spelling can never
    # drift onto a different formula than the enum value it aliases.
    for spelling, recipient in RECIPIENT_SPELLINGS.items():
        calc = NEED_CALCULATORS.get(recipient.value)
        if calc is not None:
            register_need(spelling)(calc)


def _assert_registry_covers_contract() -> None:
    """Refuse to import if a recipient the contract says needs a calculator lacks one.

    The other half of :func:`register_need`'s guard. That one stops a *wrong*
    registration; this one stops a *missing* one — the actual #394 failure, where
    ``RecipientType`` grew members whose steps then quietly contributed need 0 to
    real distributions. Making the hole an ImportError is what stops the next
    vocabulary broadening reopening it.
    """
    missing = sorted(
        r.value for r in recipients_needing_calculator() if r.value not in NEED_CALCULATORS
    )
    if missing:
        raise ImportError(
            "NEED_CALCULATORS is missing a calculator for "
            f"{missing}. Each declares a registry-backed NeedSource, so the "
            "engine must be able to compute its need; either register one or "
            "change its RECIPIENT_NEED_SOURCE row to say where the need really "
            "comes from."
        )


_register_contract_calculators()
_assert_registry_covers_contract()


def compute_need(recipient: str, funds: WaterfallFunds) -> tuple[float, bool]:
    """Return ``(need, evaluable)`` for a recipient kind.

    ``evaluable`` is ``False`` in two cases, and they mean different things
    while degrading identically:

    - **no calculator is registered** for the recipient — an extracted
      jurisdiction-native label the engine does not recognise; and
    - a calculator ran and returned ``None`` — it is registered, but the funds
      carry no inputs for it this period (a fee with no configured rate, a
      tranche the deal does not have).

    Either way the need is 0 and the step is recorded ``not_evaluable``, so the
    audit trace stays structurally complete and no unanswerable need is ever
    reported as an authoritative ``0.0`` (#453).
    """
    need, evaluable, _reason = _evaluate_need(recipient, funds)
    return need, evaluable


#: Why a step's need was unanswerable. Before #503 these four collapsed into one
#: ``not_evaluable`` bool, so "the engine has never heard of this recipient" and
#: "the engine knows this recipient and the funds carry no input for it" were
#: the same output — and a whole cascade refusing for the FIRST reason looked
#: exactly like one refusing for the fourth.
REFUSAL_UNKNOWN_RECIPIENT = "unknown_recipient"
REFUSAL_RECOGNISED_NOT_EVALUABLE = "recognised_not_evaluable"
REFUSAL_REPORT_SUPPLIED = "report_supplied"
REFUSAL_ALLOCATION_NOT_SUPPLIED = "allocation_not_supplied"
REFUSAL_INPUT_UNAVAILABLE = "input_unavailable"


def _evaluate_need(
    recipient: str, funds: WaterfallFunds
) -> tuple[float, bool, str | None]:
    """``(need, evaluable, refusal_reason)`` — the one place the four are decided.

    :func:`compute_need` and :func:`refusal_reason` are both thin views over
    this, so the amount and the explanation can never disagree about whether a
    step evaluated.
    """
    canonical = _canonical_recipient(recipient)
    if canonical is None:
        # Nobody spelled this name. NOT the same as an unanswerable need, and
        # the distinction is the point (#503).
        return 0.0, False, REFUSAL_UNKNOWN_RECIPIENT
    if canonical is RecipientType.unmapped:
        # We recognise the string and have decided the engine cannot place it.
        return 0.0, False, REFUSAL_RECOGNISED_NOT_EVALUABLE

    calc = NEED_CALCULATORS.get(recipient)
    if calc is None:
        # A declared recipient whose need legitimately comes from elsewhere, and
        # none was supplied. Which elsewhere matters to whoever reads this: a
        # principal step is waiting on ``allocate_principal``, a cure or an
        # expense tier on a reported amount. Collapsing them would put an
        # operator looking for the wrong missing input.
        if need_source_for(canonical) is NeedSource.allocation:
            return 0.0, False, REFUSAL_ALLOCATION_NOT_SUPPLIED
        return 0.0, False, REFUSAL_REPORT_SUPPLIED

    need = calc(funds)
    if need is None:
        # Registered, ran, and refused for want of an input: an unresolved
        # coupon (#493), an unconfigured fee rate, a tranche the deal lacks.
        return 0.0, False, REFUSAL_INPUT_UNAVAILABLE
    return max(0.0, need), True, None


def refusal_reason(recipient: str, funds: WaterfallFunds) -> str | None:
    """Why ``recipient``'s need is unanswerable this period; ``None`` if it is not.

    One of the four ``REFUSAL_*`` constants. The pair a reader most needs kept
    apart is :data:`REFUSAL_UNKNOWN_RECIPIENT` (a vocabulary gap — fix the
    spelling tables) and :data:`REFUSAL_INPUT_UNAVAILABLE` (a genuine unknown —
    the deal carries no such input), because a deal refusing wholly for the
    first reason never reaches the second and the two look identical in a
    ``not_evaluable`` tally.
    """
    return _evaluate_need(recipient, funds)[2]


# ---------------------------------------------------------------------------
# Condition evaluation seam (the S5 / #185 plug point)
# ---------------------------------------------------------------------------


@runtime_checkable
class ConditionEvaluator(Protocol):
    """Predicate interface the interpreter uses to gate conditional steps.

    The interpreter never parses condition prose itself — it asks an evaluator.
    This is the clean seam S5 (#185, the trigger engine over ``DealState``)
    plugs into: implement these two methods and the interpreter composes with
    the real trigger engine. S4 ships :class:`DefaultConditionEvaluator`; S5 can
    supply its own without touching the interpreter (and without S4 reaching
    into S5's trigger internals).
    """

    def evaluate(
        self,
        condition: str,
        funds: WaterfallFunds,
        terms: list[str] | None = None,
    ) -> bool:
        """Return ``True`` if a step carrying ``condition`` should pay.

        ``terms`` carries the canonical defined-term names the condition was
        linked to (``StepSpec.condition_terms``). An evaluator may use them to
        recognise a named trigger when the raw ``condition`` prose phrases it
        differently. It is optional and defaults to ``None`` so existing
        evaluators (and call sites) that ignore the link keep working.
        """
        ...

    def sequential_pay_active(self, funds: WaterfallFunds) -> bool:
        """Return ``True`` when the Sequential Pay Trigger is in effect."""
        ...


class DefaultConditionEvaluator:
    """The default condition evaluator — handles the Green-Lion conditions.

    A small, transparent evaluator covering the conditions actually present in
    the Green Lion 2026-1 waterfalls, so S4 is independently testable and the
    sequential-pay branch works today. S5 (#185) is expected to replace this
    with the real trigger engine over ``DealState``; the interpreter accepts any
    object satisfying :class:`ConditionEvaluator`.

    Semantics
    ---------
    - An **empty / unknown** condition → ``True`` (the step is unconditional, or
      we cannot prove it should be suppressed — pay it).
    - A condition mentioning the **sequential pay trigger** — either lexically in
      the raw prose, **or** via a linked defined term in ``terms`` (the
      ``condition_terms`` the definitions graph resolved) — gates on
      :meth:`sequential_pay_active`, honouring negation ("*not* in effect").
    - A condition naming a **flag** present in ``funds.flags`` gates on that
      flag (negation honoured).
    - ``sequential_pay_active`` reads ``funds.sequential_pay``; when that is
      ``None`` (unknown) it defaults to ``True`` — the senior-protective stance
      (sequential pay protects senior noteholders).
    """

    # Phrases that indicate the condition references the sequential pay trigger.
    _SEQ_MARKERS = ("sequential pay", "sequential payment", "sequential_pay")
    # Phrases that flip the polarity of a condition.
    _NEG_MARKERS = ("not ", "no longer", "absence", "unless", "is not")

    def _references_sequential_pay(self, text: str, terms: list[str] | None) -> bool:
        """True when the condition references the sequential pay trigger.

        Checks both the raw prose (the lexical ``_SEQ_MARKERS``) and the linked
        defined-term names (``condition_terms``), so a condition whose prose the
        markers don't catch still resolves when the definitions graph linked it
        to the Sequential Pay Trigger term. The latter is the #395 link: without
        it the condition fell through to the "unknown → pay" default.
        """
        if any(marker in text for marker in self._SEQ_MARKERS):
            return True
        for term in terms or []:
            lowered = term.lower()
            if any(marker in lowered for marker in self._SEQ_MARKERS):
                return True
        return False

    def evaluate(
        self,
        condition: str,
        funds: WaterfallFunds,
        terms: list[str] | None = None,
    ) -> bool:
        text = (condition or "").strip().lower()
        if not text:
            return True

        negated = any(neg in text for neg in self._NEG_MARKERS)

        if self._references_sequential_pay(text, terms):
            active = self.sequential_pay_active(funds)
            # "if Sequential Pay Trigger is *not* in effect" → pay when inactive.
            return (not active) if negated else active

        # Flag-named conditions: the condition text contains a flag key.
        for flag_name, flag_val in funds.flags.items():
            if flag_name.lower() in text:
                return (not flag_val) if negated else flag_val

        # Unknown condition prose: do not suppress — pay the step. (Conservative
        # for distribution; an unknown gate that silently zeroed a senior step
        # would be the more dangerous failure.)
        return True

    def sequential_pay_active(self, funds: WaterfallFunds) -> bool:
        if funds.sequential_pay is None:
            return True
        return bool(funds.sequential_pay)


# ---------------------------------------------------------------------------
# Execution result models
# ---------------------------------------------------------------------------


class StepResult(BaseModel):
    """The outcome of executing one step (one audit-trace entry).

    Attributes
    ----------
    priority / recipient / condition / condition_terms / pari_passu_group:
        Echoed from the :class:`StepSpec` (``condition_terms`` is the linked
        defined-term names that gated the step — empty when nothing was linked).
    amount_available:
        Funds available *before* this step (or this pari-passu group) deducted.
    need:
        The recipient's computed need this period.
    amount_distributed:
        Funds actually distributed to this recipient.
    shortfall:
        ``max(0, need − amount_distributed)``.
    gated:
        ``True`` when the step's condition predicate was ``False`` and the step
        was suppressed (distributed 0 regardless of need).
    not_evaluable:
        ``True`` when the need was unanswerable — either no need-calculator is
        registered for the recipient, or one ran and refused for want of an
        input (an unresolved tranche coupon, an unconfigured fee rate, a tranche
        the deal does not have). ``need`` is 0 in both cases, and this flag is
        what separates that from a genuine "owed nothing".
    not_evaluable_reason:
        Which of those it was — one of the ``REFUSAL_*`` constants, or ``None``
        when the step evaluated. The flag says a step refused; this says whether
        it refused because the engine has no word for the recipient
        (``unknown_recipient`` — a vocabulary gap) or because the need is
        genuinely unanswerable (``input_unavailable``). Mirrors
        ``covenant_monitor.CovenantStatus.not_evaluable_reason`` (#503).
    """

    priority: str
    recipient: str
    condition: str | None = None
    condition_terms: list[str] = Field(default_factory=list)
    pari_passu_group: str | None = None
    amount_available: float
    need: float
    amount_distributed: float
    shortfall: float
    gated: bool = False
    not_evaluable: bool = False
    not_evaluable_reason: str | None = None


class WaterfallExecution(BaseModel):
    """The full result of interpreting one waterfall.

    Attributes
    ----------
    steps:
        Ordered :class:`StepResult` audit trace, one per executed step.
    remaining:
        Funds left in the pot after the last step (the residual).
    total_distributed:
        Sum of ``amount_distributed`` across all steps.
    total_shortfall:
        Sum of ``shortfall`` across all steps.
    """

    steps: list[StepResult]
    remaining: float
    total_distributed: float
    total_shortfall: float

    def distributed_to(self, recipient: str) -> float:
        """Total distributed to a recipient kind across the trace."""
        return sum(
            s.amount_distributed for s in self.steps if s.recipient == recipient
        )


# ---------------------------------------------------------------------------
# The interpreter core
# ---------------------------------------------------------------------------


def interpret(
    steps: list[StepSpec],
    funds: WaterfallFunds,
    *,
    available: float,
    evaluator: ConditionEvaluator | None = None,
    need_overrides: dict[str, float] | None = None,
) -> WaterfallExecution:
    """Execute an ordered list of steps against a pot of available funds.

    The generic, deal-agnostic core. Walks ``steps`` in list order, paying each
    recipient out of the running ``available`` pot:

    - If a step carries a ``condition`` and the ``evaluator`` returns ``False``,
      the step is **gated**: it distributes 0 (recorded with ``gated=True``) and
      the pot is untouched.
    - Otherwise the recipient's **need** is computed (via the registry, or an
      entry in ``need_overrides`` which takes precedence — this is how the
      caller injects pre-computed sequential/pro-rata principal allocations).
    - **Pari-passu groups**: consecutive *or non-consecutive* steps sharing a
      ``pari_passu_group`` are paid together — when ``available`` cannot cover
      the group's combined need, the shortfall is split **pro-rata by need**.
      The group's funding is resolved against the pot at its first member's
      position; each member is then emitted in its own position with its
      pro-rata share, so the trace stays 1:1 with the input steps.

    Parameters
    ----------
    steps:
        Ordered step list (priority order is list order).
    funds:
        The period's funds + deal context (drives need calculators).
    available:
        The pot to distribute (e.g. ``funds.available_revenue_funds``).
    evaluator:
        Condition predicate. Defaults to :class:`DefaultConditionEvaluator`.
    need_overrides:
        ``recipient → need`` overrides that bypass the registry. Used to feed
        the sequential-pay principal allocation (computed by
        :func:`allocate_principal`) back into the trace.

    Returns
    -------
    WaterfallExecution
        Ordered audit trace + residual / totals.
    """
    ev = evaluator if evaluator is not None else DefaultConditionEvaluator()
    overrides = need_overrides or {}

    results: list[StepResult] = []

    def _need_for(spec: StepSpec) -> tuple[float, bool, str | None]:
        if spec.recipient in overrides:
            return max(0.0, overrides[spec.recipient]), True, None
        return _evaluate_need(spec.recipient, funds)

    def _eval_condition(spec: StepSpec) -> bool:
        """Run the evaluator over a step's condition + its linked terms.

        Passes ``spec.condition_terms`` through to the evaluator so a linked
        condition resolves against its defined term. Falls back to the legacy
        two-arg call for evaluators (e.g. an S5 trigger engine) whose
        ``evaluate`` predates the optional ``terms`` parameter — the link is
        additive, never a hard dependency on the evaluator implementation.
        """
        try:
            return ev.evaluate(spec.condition, funds, spec.condition_terms)
        except TypeError:
            return ev.evaluate(spec.condition, funds)

    def _is_gated(spec: StepSpec) -> bool:
        return spec.condition is not None and not _eval_condition(spec)

    # Pre-compute, per pari-passu group, the pro-rata distribution each member
    # receives — resolved against the pot available at the group's *first*
    # member position. A group may be non-contiguous; each member is then
    # emitted at its own position in the trace using its pre-computed share, so
    # the trace stays 1:1 with the input steps and ordering is preserved.
    # ``group_share[id(spec)]`` → (distributed, need, pot_before) per member.
    group_share: dict[int, tuple[float, float, float]] = {}
    group_first_idx: dict[str, int] = {}
    for i, spec in enumerate(steps):
        g = spec.pari_passu_group
        if g and g not in group_first_idx and not _is_gated(spec):
            group_first_idx[g] = i

    for idx, spec in enumerate(steps):
        gated = _is_gated(spec)
        if gated:
            results.append(
                StepResult(
                    priority=spec.priority,
                    recipient=spec.recipient,
                    condition=spec.condition,
                    condition_terms=spec.condition_terms,
                    pari_passu_group=spec.pari_passu_group,
                    amount_available=available,
                    need=0.0,
                    amount_distributed=0.0,
                    shortfall=0.0,
                    gated=True,
                )
            )
            continue

        if spec.pari_passu_group:
            g = spec.pari_passu_group
            if group_first_idx.get(g) == idx:
                # First member of the group: resolve the whole group's split
                # against the pot available *now*, deduct it once, and stash
                # each member's share for emission at its own position.
                members = [
                    s
                    for s in steps
                    if s.pari_passu_group == g and not _is_gated(s)
                ]
                pot_before = available
                needs = [(m, *_need_for(m)) for m in members]
                total_need = sum(n for _, n, _, _ in needs)
                ratio = (
                    1.0
                    if total_need <= available + _EPS
                    else (available / total_need if total_need > _EPS else 0.0)
                )
                distributed_total = 0.0
                for m, m_need, _m_eval, _m_reason in needs:
                    dist = max(0.0, min(m_need, m_need * ratio))
                    dist = min(dist, available - distributed_total)
                    distributed_total += dist
                    group_share[id(m)] = (dist, m_need, pot_before)
                available = max(0.0, available - distributed_total)
            dist, need, pot_before = group_share.get(id(spec), (0.0, 0.0, available))
            _, evaluable, reason = _need_for(spec)
            results.append(
                StepResult(
                    priority=spec.priority,
                    recipient=spec.recipient,
                    condition=spec.condition,
                    condition_terms=spec.condition_terms,
                    pari_passu_group=spec.pari_passu_group,
                    amount_available=pot_before,
                    need=need,
                    amount_distributed=dist,
                    shortfall=max(0.0, need - dist),
                    not_evaluable=not evaluable,
                    not_evaluable_reason=reason,
                )
            )
            continue

        # Residual sweep: distribute whatever remains, no shortfall.
        if spec.residual:
            dist = max(0.0, available)
            results.append(
                StepResult(
                    priority=spec.priority,
                    recipient=spec.recipient,
                    condition=spec.condition,
                    condition_terms=spec.condition_terms,
                    pari_passu_group=spec.pari_passu_group,
                    amount_available=available,
                    need=dist,
                    amount_distributed=dist,
                    shortfall=0.0,
                )
            )
            available = 0.0
            continue

        # Plain single-recipient step.
        need, evaluable, reason = _need_for(spec)
        dist = min(need, available)
        dist = max(0.0, dist)
        results.append(
            StepResult(
                priority=spec.priority,
                recipient=spec.recipient,
                condition=spec.condition,
                condition_terms=spec.condition_terms,
                pari_passu_group=spec.pari_passu_group,
                amount_available=available,
                need=need,
                amount_distributed=dist,
                shortfall=max(0.0, need - dist),
                not_evaluable=not evaluable,
                not_evaluable_reason=reason,
            )
        )
        available = max(0.0, available - dist)

    total_distributed = sum(s.amount_distributed for s in results)
    total_shortfall = sum(s.shortfall for s in results)
    return WaterfallExecution(
        steps=results,
        remaining=available,
        total_distributed=total_distributed,
        total_shortfall=total_shortfall,
    )


# ---------------------------------------------------------------------------
# Sequential-pay branch (the A3 fix)
# ---------------------------------------------------------------------------


def allocate_principal(
    funds: WaterfallFunds,
    *,
    available: float,
    classes: tuple[str, ...] = ("class_a", "class_b", "class_c"),
    evaluator: ConditionEvaluator | None = None,
) -> dict[str, float]:
    """Allocate available principal across tranches: sequential ↔ pro-rata.

    The Sequential Pay Trigger branch (a since-closed modelling gap; see
    ``SYSTEM-STATUS.md``). Reads
    ``evaluator.sequential_pay_active(funds)`` and returns ``{class → principal}``:

    - **Sequential** (trigger active): pay senior-first. Class A is paid down to
      its outstanding balance, then Class B, then Class C — the strict
      seniority order. This is the senior-protective mode.
    - **Pro-rata** (trigger inactive): split ``available`` across the eligible
      classes **pro-rata by outstanding balance**, capped at each class's
      balance (any rounding residual cascades senior-first). Subordinate
      tranches amortise alongside the senior one.

    Each class's principal is capped at its outstanding balance (you cannot repay
    more than is owed); any leftover after the most-junior class is dropped (it
    becomes the residual the caller routes elsewhere, e.g. deferred purchase
    price).

    Returns
    -------
    dict[str, float]
        ``{"class_a": amt, "class_b": amt, "class_c": amt}`` (only the keys in
        ``classes``).
    """
    ev = evaluator if evaluator is not None else DefaultConditionEvaluator()
    # Per-tranche outstanding balances, read by name from the funds' tranche list
    # (no hardcoded A/B/C) — a class with no matching tranche contributes 0.
    balances = {
        c: (funds.tranche(c).balance if funds.tranche(c) is not None else 0.0)
        for c in classes
    }
    alloc = {c: 0.0 for c in classes}
    pot = max(0.0, available)

    if ev.sequential_pay_active(funds):
        # Sequential: senior-first, each to its balance.
        for c in classes:
            if pot <= _EPS:
                break
            pay = min(pot, balances.get(c, 0.0))
            alloc[c] = pay
            pot -= pay
        return alloc

    # Pro-rata by outstanding balance.
    eligible = [c for c in classes if balances.get(c, 0.0) > _EPS]
    total_bal = sum(balances.get(c, 0.0) for c in eligible)
    if total_bal <= _EPS:
        return alloc
    if pot >= total_bal:
        # Enough to fully repay everyone.
        for c in eligible:
            alloc[c] = balances[c]
        return alloc
    # Split pro-rata, capped at balance.
    for c in eligible:
        alloc[c] = min(balances[c], pot * (balances[c] / total_bal))
    # Cascade any rounding residual senior-first.
    distributed = sum(alloc[c] for c in eligible)
    residual = max(0.0, pot - distributed)
    for c in eligible:
        if residual <= _EPS:
            break
        room = balances[c] - alloc[c]
        add = min(room, residual)
        alloc[c] += add
        residual -= add
    return alloc


# ---------------------------------------------------------------------------
# Mapping a revenue + redemption execution → the S1 WaterfallResult DTO
# ---------------------------------------------------------------------------


def to_waterfall_result(
    *,
    revenue: WaterfallExecution | None = None,
    redemption: WaterfallExecution | None = None,
    principal_allocation: dict[str, float] | None = None,
) -> WaterfallResult:
    """Map interpreter executions into the S1 ``WaterfallResult`` DTO.

    Folds the distributions the interpreter recorded into the shape
    ``DealState.apply_waterfall_result`` (S1, ``deal_state.py``) consumes:

    - **Principal** per tranche — taken from ``principal_allocation`` when given
      (the :func:`allocate_principal` output, ``{tranche_name: amount}``), else
      summed from the redemption execution's ``<tranche>_principal`` recipient
      distributions.
    - **PDL replenishment** per tranche — summed from the revenue execution's
      ``<tranche>_pdl_replenishment`` recipient distributions.
    - **Reserve payment** — summed from the revenue execution's
      ``reserve_replenishment`` / ``reserve_account_replenishment`` distributions.
      (``reserve_draw`` is left 0 here — a draw is sourced by the caller when it
      tops up ``available_revenue_funds`` from the reserve, not by the
      distribution trace.)

    Deal-agnostic: the per-tranche outcomes are keyed by tranche *name* rather
    than hardcoded to A/B/C. Tranche names are discovered from
    ``principal_allocation`` (when given) and from the executions' recipient
    lines (``<name>_principal`` / ``<name>_notes_principal`` /
    ``<name>_pdl_replenishment``), ordered canonical-A/B/C-first then any other
    names in discovery order — so Green Lion's A/B/C result is byte-stable while
    a non-A/B/C structure round-trips.
    """
    rev = revenue or WaterfallExecution(
        steps=[], remaining=0.0, total_distributed=0.0, total_shortfall=0.0
    )
    red = redemption or WaterfallExecution(
        steps=[], remaining=0.0, total_distributed=0.0, total_shortfall=0.0
    )

    def _principal_from_exec(name: str) -> float:
        # Extracted deal models spell the recipient either ``<name>_principal``
        # (the builtin / tape spelling) or ``<name>_notes_principal`` (Green Lion
        # 2024-1's extracted redemption PoP, #270) — accept both.
        return red.distributed_to(f"{name}_principal") + red.distributed_to(
            f"{name}_notes_principal"
        )

    # Discover tranche names from the principal source and the revenue PDL lines.
    pdl_names = {
        s.recipient[: -len("_pdl_replenishment")]
        for s in rev.steps
        if s.recipient.endswith("_pdl_replenishment")
    }
    if principal_allocation is not None:
        principal_names: set[str] = set(principal_allocation)
    else:
        principal_names = {
            s.recipient[: -len("_principal")]
            for s in red.steps
            if s.recipient.endswith("_principal")
        } | {
            s.recipient[: -len("_notes_principal")]
            for s in red.steps
            if s.recipient.endswith("_notes_principal")
        }
    discovered = principal_names | pdl_names
    ordered_names = list(_CANONICAL_TRANCHE_NAMES) + sorted(
        n for n in discovered if n not in _CANONICAL_TRANCHE_NAMES
    )

    tranches: list[TranchePayment] = []
    for name in ordered_names:
        if principal_allocation is not None:
            principal = principal_allocation.get(name, 0.0)
        else:
            principal = _principal_from_exec(name)
        replenishment = rev.distributed_to(f"{name}_pdl_replenishment")
        # Canonical A/B/C tranches always get an entry (byte-stable result);
        # other discovered names only when they carry a non-zero outcome.
        if (
            name in _CANONICAL_TRANCHE_NAMES
            or principal > 0.0
            or replenishment > 0.0
        ):
            tranches.append(
                TranchePayment(
                    name=name,
                    principal=max(0.0, principal),
                    pdl_replenishment=replenishment,
                )
            )

    reserve_payment = rev.distributed_to("reserve_replenishment") + rev.distributed_to(
        "reserve_account_replenishment"
    )

    return WaterfallResult(
        tranches=tranches,
        reserve_payment=reserve_payment,
        reserve_draw=0.0,
    )
