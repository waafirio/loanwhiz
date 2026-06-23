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

    Each value binds to exactly one engine need-calculator, ordered roughly
    senior → junior. ``unmapped`` is the explicit escape for a step whose
    recipient the engine cannot evaluate: it degrades honestly to
    "report-supplied / not-evaluable" rather than mis-mapping to a wrong
    calculator.
    """

    senior_expenses = "senior_expenses"  # issuer costs, admin, trustee
    servicing_fee = "servicing_fee"
    swap_payment = "swap_payment"
    class_a_interest = "class_a_interest"
    class_b_interest = "class_b_interest"
    class_c_interest = "class_c_interest"
    class_a_pdl_cure = "class_a_pdl_cure"  # PDL replenishment, senior
    class_b_pdl_cure = "class_b_pdl_cure"
    reserve_replenishment = "reserve_replenishment"
    class_a_principal = "class_a_principal"
    class_b_principal = "class_b_principal"
    class_c_principal = "class_c_principal"
    subordinated_amounts = "subordinated_amounts"  # subordinated swap, deferred fees
    residual_certificate = "residual_certificate"  # deferred purchase price / residual
    unmapped = "unmapped"  # explicit escape -> report-supplied / not-evaluable


# ---------------------------------------------------------------------------
# Canonical metric taxonomy — triggers / covenants.
# ---------------------------------------------------------------------------


class MetricType(str, Enum):
    """The metric a trigger / covenant tests — the closed set of engine metrics.

    ``unmapped`` is the explicit escape for a deal-specific metric the engine
    does not compute; like :class:`RecipientType.unmapped`, it makes the schema
    additive (new values can land as deals are onboarded) without ever silently
    mis-mapping an unknown metric onto a known sentinel.
    """

    cumulative_loss_rate = "cumulative_loss_rate"
    class_a_pdl = "class_a_pdl"
    class_b_pdl = "class_b_pdl"
    reserve_fund_ratio = "reserve_fund_ratio"
    pool_factor = "pool_factor"
    arrears_90d_ratio = "arrears_90d_ratio"
    arrears_180d_ratio = "arrears_180d_ratio"
    wa_ltv = "wa_ltv"
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
    basis: Literal[
        "interest_accrual",  # balance x rate x days / basis
        "pdl_balance",  # cure up to outstanding PDL
        "target_shortfall",  # reserve: max(0, target - balance)
        "principal_due",  # amortisation / sequential / pro-rata
        "report_supplied",  # no engine formula — amount from PeriodInputs.step_overrides
        "residual",  # whatever remains (terminal step)
    ] = Field(..., description="Which fixed engine formula computes the amount.")
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
