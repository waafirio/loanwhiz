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

from typing import Callable, Protocol, runtime_checkable

from pydantic import BaseModel, Field, computed_field, model_validator

from loanwhiz.domain.rules import (
    LEGACY_RECIPIENT_SPELLINGS,
    NeedSource,
    RecipientType,
    basis_for,
    need_source_for,
    recipients_needing_calculator,
)
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
        Annual coupon rate in percent.
    pdl_balance:
        Outstanding PDL debit balance (the replenishment need).
    """

    name: str = Field(..., description="Tranche name.")
    balance: float = Field(default=0.0, ge=0.0)
    rate_pct: float = Field(default=0.0, ge=0.0)
    pdl_balance: float = Field(default=0.0, ge=0.0)


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
        Per-tranche annual coupon rates in percent.
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
        Day count for interest accrual (Act/360).
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
        rates: dict[str, float] = {}
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
        tranches: list[dict[str, float | str]] = []
        for name in names:
            if name in seen or name not in all_names:
                continue
            seen.add(name)
            tranches.append(
                {
                    "name": name,
                    "balance": balances.get(name, 0.0),
                    "rate_pct": rates.get(name, 0.0),
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
    def class_a_rate_pct(self) -> float:
        t = self.tranche("class_a")
        return t.rate_pct if t is not None else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_b_rate_pct(self) -> float:
        t = self.tranche("class_b")
        return t.rate_pct if t is not None else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_c_rate_pct(self) -> float:
        t = self.tranche("class_c")
        return t.rate_pct if t is not None else 0.0

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
#: the six legacy spellings declared in
#: :data:`~loanwhiz.domain.rules.LEGACY_RECIPIENT_SPELLINGS`. :func:`register_need`
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
                "declared legacy spelling. Add the recipient to RecipientType "
                "(with its RECIPIENT_BASIS / RECIPIENT_NEED_SOURCE rows), or "
                "declare the spelling in LEGACY_RECIPIENT_SPELLINGS."
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

    Accepts a canonical enum value or a declared legacy spelling — the two
    vocabularies the registry is legitimately keyed by.
    """
    try:
        return RecipientType(name)
    except ValueError:
        return LEGACY_RECIPIENT_SPELLINGS.get(name)


def _accrued_interest(balance: float, rate_pct: float, days: int) -> float:
    """Act/360 accrued interest: balance × (rate/100) / 360 × days."""
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
    """Act/360 accrual on ``tranche``; ``None`` when the deal has no such tranche.

    An absent tranche is *unknown*, not zero: a step paying Class D interest in
    a deal whose funds carry no Class D has an unanswerable need, and answering
    ``0.0`` would put an authoritative-looking figure into the distribution. A
    tranche that is *present* with a zero balance is a different thing — fully
    amortised, genuinely owed nothing — and still evaluates to 0.
    """

    def _need(funds: WaterfallFunds) -> float | None:
        t = funds.tranche(tranche)
        if t is None:
            return None
        return _accrued_interest(t.balance, t.rate_pct, funds.days_in_period)

    _need.__name__ = f"_need_{tranche}_interest"
    return _need


def _make_tranche_pdl_need(tranche: str) -> NeedCalculator:
    """Cure up to ``tranche``'s outstanding PDL; ``None`` when it has no tranche."""

    def _need(funds: WaterfallFunds) -> float | None:
        t = funds.tranche(tranche)
        if t is None:
            return None
        return t.pdl_balance

    _need.__name__ = f"_need_{tranche}_pdl_cure"
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


def _calculator_for(recipient: RecipientType) -> NeedCalculator | None:
    """Build the calculator ``recipient``'s declared basis calls for, if any."""
    basis = basis_for(recipient)
    if basis == "interest_accrual":
        return _make_tranche_interest_need(
            recipient.value.removesuffix(_INTEREST_SUFFIX)
        )
    if basis == "pdl_balance":
        return _make_tranche_pdl_need(recipient.value.removesuffix(_PDL_CURE_SUFFIX))
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
    # The legacy free-string spellings resolve to the same function as their
    # canonical recipient — derived from the one declaration, so a spelling can
    # never drift onto a different formula than the enum value it aliases.
    for spelling, recipient in LEGACY_RECIPIENT_SPELLINGS.items():
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
    calc = NEED_CALCULATORS.get(recipient)
    if calc is None:
        return 0.0, False
    need = calc(funds)
    if need is None:
        return 0.0, False
    return max(0.0, need), True


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
        ``True`` when no need-calculator is registered for the recipient.
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

    def _need_for(spec: StepSpec) -> tuple[float, bool]:
        if spec.recipient in overrides:
            return max(0.0, overrides[spec.recipient]), True
        return compute_need(spec.recipient, funds)

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
                total_need = sum(n for _, n, _ in needs)
                ratio = (
                    1.0
                    if total_need <= available + _EPS
                    else (available / total_need if total_need > _EPS else 0.0)
                )
                distributed_total = 0.0
                for m, m_need, _m_eval in needs:
                    dist = max(0.0, min(m_need, m_need * ratio))
                    dist = min(dist, available - distributed_total)
                    distributed_total += dist
                    group_share[id(m)] = (dist, m_need, pot_before)
                available = max(0.0, available - distributed_total)
            dist, need, pot_before = group_share.get(id(spec), (0.0, 0.0, available))
            _, evaluable = _need_for(spec)
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
        need, evaluable = _need_for(spec)
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
