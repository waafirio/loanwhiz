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
Trigger*, ``MODELING-GAPS.md`` A3) is expressed through the same
condition→predicate seam: ``allocate_principal`` reads
``evaluator.sequential_pay_active(funds)`` and allocates either senior-first
(sequential) or pro-rata by outstanding balance.

Pure & deterministic — no LLM, no I/O. Mirrors the immutable, typed-pydantic
conventions of the surrounding primitives.
"""

from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from loanwhiz.primitives.deal_state import WaterfallResult

# Small tolerance for floating-point comparisons (EUR amounts).
_EPS = 1e-6


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
    pari_passu_group: str | None = None
    residual: bool = False

    @classmethod
    def from_extracted(cls, step: dict, *, group_prefix: str = "pp") -> "StepSpec":
        """Build a ``StepSpec`` from an extracted step dict.

        Accepts the ``waterfall_extractor.WaterfallStep`` JSON shape (as stored
        in ``DealModel.waterfalls[*]["steps"]``): keys ``priority``,
        ``recipient``, ``condition``, ``is_pari_passu``.

        A step flagged ``is_pari_passu`` is assigned a group id derived from its
        priority label (``"<group_prefix>:<priority>"``) so each pari-passu step
        forms its own single-member group by default. Callers that know two
        steps rank *together* can override ``pari_passu_group`` after
        construction (or build the specs directly). This keeps the extracted
        boolean meaningful — a pari-passu step splits a shortfall pro-rata even
        as a singleton — without inventing group membership the extractor never
        captured (``MODELING-GAPS.md`` C9).
        """
        condition = step.get("condition")
        condition = condition or None  # "" → None
        group: str | None = None
        if step.get("is_pari_passu"):
            group = f"{group_prefix}:{step.get('priority', '')}"
        return cls(
            priority=str(step.get("priority", "")),
            recipient=str(step.get("recipient", "")),
            condition=condition,
            pari_passu_group=group,
        )


# ---------------------------------------------------------------------------
# WaterfallFunds — the available funds + deal context the needs read from
# ---------------------------------------------------------------------------


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

    class_a_balance: float = Field(default=0.0, ge=0.0)
    class_b_balance: float = Field(default=0.0, ge=0.0)
    class_c_balance: float = Field(default=0.0, ge=0.0)

    class_a_rate_pct: float = Field(default=0.0, ge=0.0)
    class_b_rate_pct: float = Field(default=0.0, ge=0.0)
    class_c_rate_pct: float = Field(default=0.0, ge=0.0)

    class_a_pdl_balance: float = Field(default=0.0, ge=0.0)
    class_b_pdl_balance: float = Field(default=0.0, ge=0.0)
    class_c_pdl_balance: float = Field(default=0.0, ge=0.0)

    reserve_balance: float = Field(default=0.0, ge=0.0)
    reserve_target: float = Field(default=0.0, ge=0.0)

    days_in_period: int = Field(default=90, gt=0)

    sequential_pay: bool | None = None
    flags: dict[str, bool] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Need-calculator registry
# ---------------------------------------------------------------------------

#: ``recipient kind → (funds) -> need``. A recipient with no registered
#: calculator contributes 0 and is recorded ``not_evaluable`` in the trace.
NEED_CALCULATORS: dict[str, Callable[[WaterfallFunds], float]] = {}


def register_need(*recipients: str) -> Callable[
    [Callable[[WaterfallFunds], float]], Callable[[WaterfallFunds], float]
]:
    """Decorator registering a need-calculator for one or more recipient kinds.

    A need-calculator is a pure function ``(WaterfallFunds) -> float`` returning
    the (non-negative) amount this recipient is owed this period.
    """

    def _decorator(
        fn: Callable[[WaterfallFunds], float],
    ) -> Callable[[WaterfallFunds], float]:
        for name in recipients:
            NEED_CALCULATORS[name] = fn
        return fn

    return _decorator


def _accrued_interest(balance: float, rate_pct: float, days: int) -> float:
    """Act/360 accrued interest: balance × (rate/100) / 360 × days."""
    return balance * (rate_pct / 100.0) / 360.0 * days


@register_need("senior_fees", "security_trustee_fees")
def _need_senior_fees(funds: WaterfallFunds) -> float:
    return funds.senior_fees


@register_need("swap_payment")
def _need_swap_payment(funds: WaterfallFunds) -> float:
    return funds.swap_payment


@register_need("class_a_interest")
def _need_class_a_interest(funds: WaterfallFunds) -> float:
    return _accrued_interest(
        funds.class_a_balance, funds.class_a_rate_pct, funds.days_in_period
    )


@register_need("class_b_interest")
def _need_class_b_interest(funds: WaterfallFunds) -> float:
    return _accrued_interest(
        funds.class_b_balance, funds.class_b_rate_pct, funds.days_in_period
    )


@register_need("class_c_interest")
def _need_class_c_interest(funds: WaterfallFunds) -> float:
    return _accrued_interest(
        funds.class_c_balance, funds.class_c_rate_pct, funds.days_in_period
    )


@register_need("class_a_pdl_replenishment")
def _need_class_a_pdl(funds: WaterfallFunds) -> float:
    return funds.class_a_pdl_balance


@register_need("class_b_pdl_replenishment")
def _need_class_b_pdl(funds: WaterfallFunds) -> float:
    return funds.class_b_pdl_balance


@register_need("class_c_pdl_replenishment")
def _need_class_c_pdl(funds: WaterfallFunds) -> float:
    return funds.class_c_pdl_balance


@register_need("reserve_replenishment", "reserve_account_replenishment")
def _need_reserve(funds: WaterfallFunds) -> float:
    return max(0.0, funds.reserve_target - funds.reserve_balance)


def compute_need(recipient: str, funds: WaterfallFunds) -> tuple[float, bool]:
    """Return ``(need, evaluable)`` for a recipient kind.

    ``evaluable`` is ``False`` when no calculator is registered for the
    recipient — the need is 0 and the step is recorded ``not_evaluable``, so an
    unrecognised extracted recipient never crashes the run.
    """
    calc = NEED_CALCULATORS.get(recipient)
    if calc is None:
        return 0.0, False
    return max(0.0, calc(funds)), True


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

    def evaluate(self, condition: str, funds: WaterfallFunds) -> bool:
        """Return ``True`` if a step carrying ``condition`` should pay."""
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
    - A condition mentioning the **sequential pay trigger** gates on
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

    def evaluate(self, condition: str, funds: WaterfallFunds) -> bool:
        text = (condition or "").strip().lower()
        if not text:
            return True

        negated = any(neg in text for neg in self._NEG_MARKERS)

        if any(marker in text for marker in self._SEQ_MARKERS):
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
    priority / recipient / condition / pari_passu_group:
        Echoed from the :class:`StepSpec`.
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

    def _is_gated(spec: StepSpec) -> bool:
        return spec.condition is not None and not ev.evaluate(spec.condition, funds)

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

    The Sequential Pay Trigger branch (``MODELING-GAPS.md`` A3). Reads
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
    balances = {
        "class_a": funds.class_a_balance,
        "class_b": funds.class_b_balance,
        "class_c": funds.class_c_balance,
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

    - **Principal** per class — taken from ``principal_allocation`` when given
      (the :func:`allocate_principal` output), else summed from the redemption
      execution's ``class_{a,b,c}_principal`` recipient distributions.
    - **PDL replenishment** per class — summed from the revenue execution's
      ``class_{a,b,c}_pdl_replenishment`` recipient distributions.
    - **Reserve payment** — summed from the revenue execution's
      ``reserve_replenishment`` / ``reserve_account_replenishment`` distributions.
      (``reserve_draw`` is left 0 here — a draw is sourced by the caller when it
      tops up ``available_revenue_funds`` from the reserve, not by the
      distribution trace.)

    Any field with no corresponding recipient defaults to 0.0, so the result is
    always a valid ``WaterfallResult``.
    """
    rev = revenue or WaterfallExecution(
        steps=[], remaining=0.0, total_distributed=0.0, total_shortfall=0.0
    )
    red = redemption or WaterfallExecution(
        steps=[], remaining=0.0, total_distributed=0.0, total_shortfall=0.0
    )

    if principal_allocation is not None:
        class_a_principal = principal_allocation.get("class_a", 0.0)
        class_b_principal = principal_allocation.get("class_b", 0.0)
        class_c_principal = principal_allocation.get("class_c", 0.0)
    else:
        class_a_principal = red.distributed_to("class_a_principal")
        class_b_principal = red.distributed_to("class_b_principal")
        class_c_principal = red.distributed_to("class_c_principal")

    reserve_payment = rev.distributed_to("reserve_replenishment") + rev.distributed_to(
        "reserve_account_replenishment"
    )

    return WaterfallResult(
        class_a_principal=max(0.0, class_a_principal),
        class_b_principal=max(0.0, class_b_principal),
        class_c_principal=max(0.0, class_c_principal),
        class_a_pdl_replenishment=rev.distributed_to("class_a_pdl_replenishment"),
        class_b_pdl_replenishment=rev.distributed_to("class_b_pdl_replenishment"),
        class_c_pdl_replenishment=rev.distributed_to("class_c_pdl_replenishment"),
        reserve_payment=reserve_payment,
        reserve_draw=0.0,
    )
