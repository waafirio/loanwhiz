"""Tests for the canonical domain schema (``loanwhiz.domain``).

Covers the contract defined in
``docs/superpowers/specs/2026-06-20-canonical-domain-schema-design.md``:

- Public surface: every named type imports cleanly from ``loanwhiz.domain``.
- Construction round-trips for the three aggregates and their sub-types.
- Taxonomy closure: ``RecipientType`` / ``MetricType`` carry exactly the spec's
  values, including the ``unmapped`` escape; out-of-taxonomy values are rejected.
- ``TriggerRule.threshold_unit`` is a normalised Literal (the C8 100x guard).
- ``FieldProvenance`` reuses ``primitives.base.Citation`` and the
  ``ProvenanceMap`` is keyed by dotted field path.
- ``DealRules.compute_completeness()`` implements the spec's field-based rule:
  a fully-populated ``DealRules`` scores 1.0; a structurally-empty one scores
  0.0; an ``unmapped``-only revenue waterfall does not count toward completeness.
- ``DealState.provenance`` is seed-only optional (``None`` on rolled states).
"""

import pytest
from pydantic import ValidationError

from loanwhiz.domain import (
    AmountRule,
    CollectionLegs,
    ConditionRef,
    DealRules,
    DealState,
    FieldProvenance,
    MetricType,
    PeriodInputs,
    RateRule,
    RecipientType,
    ReserveRule,
    RiskSignals,
    StepRule,
    TrancheRule,
    TrancheState,
    TriggerRule,
)
from loanwhiz.primitives.base import Citation


# ---------------------------------------------------------------------------
# Public surface — everything the spec names imports from the package root.
# ---------------------------------------------------------------------------


def test_public_surface_importable():
    import loanwhiz.domain as domain

    expected = {
        "FieldProvenance",
        "ProvenanceMap",
        "DealRules",
        "RecipientType",
        "MetricType",
        "AmountRule",
        "ConditionRef",
        "StepRule",
        "TriggerRule",
        "RateRule",
        "TrancheRule",
        "ReserveRule",
        "PeriodInputs",
        "CollectionLegs",
        "RiskSignals",
        "DealState",
        "TrancheState",
    }
    assert expected.issubset(set(domain.__all__))
    for name in expected:
        assert hasattr(domain, name), f"{name} not exported from loanwhiz.domain"


# ---------------------------------------------------------------------------
# Taxonomy closure — exact value sets, incl. the unmapped escape.
# ---------------------------------------------------------------------------


def test_recipient_type_closed_set():
    assert {r.value for r in RecipientType} == {
        "senior_expenses",
        "servicing_fee",
        "swap_payment",
        "class_a_interest",
        "class_b_interest",
        "class_c_interest",
        "class_a_pdl_cure",
        "class_b_pdl_cure",
        "reserve_replenishment",
        "class_a_principal",
        "class_b_principal",
        "class_c_principal",
        "subordinated_amounts",
        "residual_certificate",
        "unmapped",
    }
    # The explicit escape exists.
    assert RecipientType.unmapped.value == "unmapped"


def test_metric_type_closed_set():
    assert {m.value for m in MetricType} == {
        "cumulative_loss_rate",
        "class_a_pdl",
        "class_b_pdl",
        "reserve_fund_ratio",
        "pool_factor",
        "arrears_90d_ratio",
        "arrears_180d_ratio",
        "wa_ltv",
        "unmapped",
    }
    assert MetricType.unmapped.value == "unmapped"


def test_recipient_type_rejects_out_of_taxonomy_value():
    with pytest.raises(ValidationError):
        AmountRule(calculator="not_a_recipient", basis="residual", raw_text="x")


# ---------------------------------------------------------------------------
# Sub-type construction round-trips.
# ---------------------------------------------------------------------------


def test_amount_rule_and_step_rule_construct():
    amount = AmountRule(
        calculator=RecipientType.class_a_interest,
        basis="interest_accrual",
        raw_text="Class A interest as defined in Condition 5.2(a).",
    )
    step = StepRule(
        order=3,
        priority_label="5.2(a)",
        recipient=RecipientType.class_a_interest,
        amount=amount,
        condition=ConditionRef(trigger_name="seq_trigger", when="not_breached"),
        pari_passu_group="senior_interest",
    )
    assert step.order == 3
    assert step.condition is not None and step.condition.when == "not_breached"
    # Defaults: unconditional, no pari-passu group.
    bare = StepRule(
        order=1,
        priority_label="(a)",
        recipient=RecipientType.senior_expenses,
        amount=AmountRule(
            calculator=RecipientType.senior_expenses,
            basis="report_supplied",
            raw_text="senior fees and expenses",
        ),
    )
    assert bare.condition is None
    assert bare.pari_passu_group is None


def test_amount_rule_basis_is_a_closed_literal():
    with pytest.raises(ValidationError):
        AmountRule(
            calculator=RecipientType.class_a_principal,
            basis="free_form_formula",  # not in the bound basis set
            raw_text="x",
        )


def test_trigger_rule_threshold_unit_normalised_literal():
    trig = TriggerRule(
        name="cumulative_loss_trigger",
        metric=MetricType.cumulative_loss_rate,
        operator=">",
        threshold=4.5,
        threshold_unit="percent",
        consequence="switch to sequential pay",
    )
    assert trig.threshold_unit == "percent"
    # threshold is required-but-nullable (qualitative trigger).
    qualitative = TriggerRule(
        name="event_of_default",
        metric=MetricType.unmapped,
        operator="==",
        threshold=None,
        threshold_unit="fraction",
        consequence="post-enforcement waterfall applies",
    )
    assert qualitative.threshold is None
    # An out-of-set unit is rejected — the single place units are fixed.
    with pytest.raises(ValidationError):
        TriggerRule(
            name="bad_unit",
            metric=MetricType.pool_factor,
            operator="<",
            threshold=0.1,
            threshold_unit="percentage",  # not in the Literal
            consequence="x",
        )


def test_rate_rule_fixed_and_floating():
    fixed = RateRule(kind="fixed", fixed_pct=0.035)
    floating = RateRule(kind="floating", index="EURIBOR_3M", margin_bps=120.0)
    assert fixed.fixed_pct == 0.035
    assert floating.index == "EURIBOR_3M"
    with pytest.raises(ValidationError):
        RateRule(kind="variable")  # not in the Literal


def test_reserve_rule_defaults():
    r = ReserveRule()
    assert r.floor == 0.0
    assert r.pct_of_note_balance is None


# ---------------------------------------------------------------------------
# Provenance — reuses base.Citation; keyed by dotted field path.
# ---------------------------------------------------------------------------


def test_field_provenance_reuses_base_citation():
    cite = Citation(
        document="GreenLion2024-1 Prospectus",
        page_or_row="p.142 §5.2(a)",
        excerpt="Class A Notes shall accrue interest at...",
    )
    fp = FieldProvenance(
        source="prospectus",
        method="ocr+llm",
        confidence=0.82,
        citation=cite,
    )
    assert isinstance(fp.citation, Citation)
    assert fp.reconciled is False  # default
    # Computed values carry no citation.
    computed = FieldProvenance(source="engine", method="computed", confidence=1.0)
    assert computed.citation is None


def test_field_provenance_confidence_bounded():
    with pytest.raises(ValidationError):
        FieldProvenance(source="report", method="llm", confidence=1.5)
    with pytest.raises(ValidationError):
        FieldProvenance(source="report", method="llm", confidence=-0.1)


def test_provenance_map_keyed_by_dotted_path():
    rules = _full_deal_rules()
    rules.provenance = {
        "tranches.class_a.original_balance": FieldProvenance(
            source="prospectus",
            method="deterministic",
            confidence=1.0,
            citation=Citation(
                document="Prospectus",
                page_or_row="p.12",
                excerpt="Class A: EUR 850,000,000",
            ),
        )
    }
    assert "tranches.class_a.original_balance" in rules.provenance
    entry = rules.provenance["tranches.class_a.original_balance"]
    assert isinstance(entry, FieldProvenance)
    # Round-trips through JSON (the sidecar is plain serialisable data).
    reloaded = DealRules.model_validate_json(rules.model_dump_json())
    assert "tranches.class_a.original_balance" in reloaded.provenance


# ---------------------------------------------------------------------------
# DealRules construction + completeness.
# ---------------------------------------------------------------------------


def _full_deal_rules() -> DealRules:
    """A structurally-complete DealRules satisfying all five completeness checks."""
    return DealRules(
        deal_id="GL-2024-1",
        deal_name="Green Lion 2024-1",
        jurisdiction="IE",
        tranches=[
            TrancheRule(
                name="Class A",
                seniority=0,
                original_balance=850_000_000.0,
                rate=RateRule(kind="floating", index="EURIBOR_3M", margin_bps=85.0),
                rating="AAA",
            ),
            TrancheRule(
                name="Class B",
                seniority=1,
                original_balance=100_000_000.0,
                rate=RateRule(kind="fixed", fixed_pct=0.045),
            ),
        ],
        waterfalls={
            "revenue": [
                StepRule(
                    order=1,
                    priority_label="(a)",
                    recipient=RecipientType.senior_expenses,
                    amount=AmountRule(
                        calculator=RecipientType.senior_expenses,
                        basis="report_supplied",
                        raw_text="senior fees",
                    ),
                ),
                StepRule(
                    order=2,
                    priority_label="(b)",
                    recipient=RecipientType.class_a_interest,
                    amount=AmountRule(
                        calculator=RecipientType.class_a_interest,
                        basis="interest_accrual",
                        raw_text="Class A interest",
                    ),
                ),
            ],
            "redemption": [
                StepRule(
                    order=1,
                    priority_label="(a)",
                    recipient=RecipientType.class_a_principal,
                    amount=AmountRule(
                        calculator=RecipientType.class_a_principal,
                        basis="principal_due",
                        raw_text="Class A principal",
                    ),
                ),
            ],
        },
        triggers=[
            TriggerRule(
                name="cumulative_loss",
                metric=MetricType.cumulative_loss_rate,
                operator=">",
                threshold=4.5,
                threshold_unit="percent",
                consequence="switch to sequential pay",
            )
        ],
        reserve=ReserveRule(floor=5_000_000.0, pct_of_note_balance=0.015),
    )


def test_deal_rules_constructs_and_completeness_full():
    rules = _full_deal_rules()
    assert rules.currency == "EUR"  # default
    assert rules.completeness == 0.0  # field default; not auto-computed
    assert rules.compute_completeness() == 1.0
    # Caller assigns it explicitly (the helper is a pure read).
    rules.completeness = rules.compute_completeness()
    assert rules.completeness == 1.0


def test_completeness_structurally_empty_scores_zero():
    empty = DealRules(
        deal_id="EMPTY",
        deal_name="Empty",
        jurisdiction="IE",
        tranches=[],
        waterfalls={},
        triggers=[],
        reserve=ReserveRule(),  # floor=0.0, no pct → not resolvable
    )
    assert empty.compute_completeness() == 0.0


def test_completeness_unmapped_revenue_step_does_not_count():
    rules = _full_deal_rules()
    full = rules.compute_completeness()
    assert full == 1.0
    # Replace the revenue waterfall with only an unmapped step: check 2 fails.
    rules.waterfalls["revenue"] = [
        StepRule(
            order=1,
            priority_label="(a)",
            recipient=RecipientType.unmapped,
            amount=AmountRule(
                calculator=RecipientType.unmapped,
                basis="report_supplied",
                raw_text="some exotic, non-evaluable step",
            ),
        )
    ]
    degraded = rules.compute_completeness()
    assert degraded < full
    assert degraded == pytest.approx(4 / 5)


def test_completeness_monotone_in_required_fields():
    rules = _full_deal_rules()
    # Drop the only quantified trigger → completeness falls by exactly one check.
    rules.triggers = [
        TriggerRule(
            name="qualitative_only",
            metric=MetricType.unmapped,
            operator="==",
            threshold=None,
            threshold_unit="fraction",
            consequence="x",
        )
    ]
    assert rules.compute_completeness() == pytest.approx(4 / 5)


# ---------------------------------------------------------------------------
# PeriodInputs — tape vs report path.
# ---------------------------------------------------------------------------


def test_period_inputs_report_path_minimal():
    pi = PeriodInputs(
        reporting_date="2024-06-30",
        days_in_period=91,
        available_revenue=12_500_000.0,
        available_principal=40_000_000.0,
        realized_loss=250_000.0,
        source="report",
    )
    assert pi.legs is None  # report path: no per-leg breakdown
    assert pi.risk_signals is None
    assert pi.step_overrides == {}
    assert pi.step_sources == {}
    # Per-waterfall maps (#270) default empty too — tape path / flat-map callers
    # are unchanged.
    assert pi.revenue_step_overrides == {}
    assert pi.revenue_step_sources == {}
    assert pi.redemption_step_overrides == {}
    assert pi.redemption_step_sources == {}
    assert pi.provenance == {}


def test_period_inputs_per_waterfall_maps_carry_distinct_amounts():
    """The revenue and redemption waterfalls can carry the SAME label with
    DIFFERENT amounts via the per-waterfall maps (#270) — the flat map cannot."""
    pi = PeriodInputs(
        reporting_date="2024-06-30",
        days_in_period=91,
        available_revenue=12_500_000.0,
        available_principal=40_000_000.0,
        realized_loss=0.0,
        revenue_step_overrides={"(a)": 12_345.0},
        revenue_step_sources={"(a)": "reported"},
        redemption_step_overrides={"(a)": 43_486_010.58},
        redemption_step_sources={"(a)": "reported"},
        source="report",
    )
    # Same label, different amount per waterfall — no collision.
    assert pi.revenue_step_overrides["(a)"] == 12_345.0
    assert pi.redemption_step_overrides["(a)"] == 43_486_010.58


def test_period_inputs_tape_path_with_legs_and_signals():
    pi = PeriodInputs(
        reporting_date="2024-06-30",
        days_in_period=91,
        available_revenue=12_500_000.0,
        available_principal=40_000_000.0,
        realized_loss=250_000.0,
        legs=CollectionLegs(
            interest=12_500_000.0,
            scheduled_principal=35_000_000.0,
            prepayment=5_000_000.0,
            recovery=200_000.0,
            realized_loss=250_000.0,
        ),
        step_overrides={"(a)": 1_000_000.0},
        step_sources={"(a)": "reported"},
        risk_signals=RiskSignals(
            arrears_90d=0.012,
            arrears_180d=0.004,
            wa_ltv=0.68,
            default_pct=0.009,
            pool_balance=940_000_000.0,
        ),
        source="tape",
    )
    assert pi.legs is not None and pi.legs.recovery == 200_000.0
    assert pi.risk_signals is not None and pi.risk_signals.wa_ltv == 0.68
    assert pi.step_sources["(a)"] == "reported"


def test_period_inputs_source_is_closed_literal():
    with pytest.raises(ValidationError):
        PeriodInputs(
            reporting_date="2024-06-30",
            days_in_period=91,
            available_revenue=1.0,
            available_principal=1.0,
            realized_loss=0.0,
            source="prospectus",  # not in {tape, report, scenario}
        )
    with pytest.raises(ValidationError):
        PeriodInputs(
            reporting_date="2024-06-30",
            days_in_period=91,
            available_revenue=1.0,
            available_principal=1.0,
            realized_loss=0.0,
            source="tape",
            step_sources={"(a)": "guessed"},  # not in the step-source Literal
        )


# ---------------------------------------------------------------------------
# DealState — seed-only optional provenance.
# ---------------------------------------------------------------------------


def test_deal_state_seed_carries_provenance_rolled_does_not():
    seed = DealState(
        reporting_date="2024-03-31",
        tranches=[
            TrancheState(name="Class A", balance=850_000_000.0, pdl_balance=0.0),
            TrancheState(name="Class B", balance=100_000_000.0, pdl_balance=0.0),
        ],
        reserve_balance=5_000_000.0,
        reserve_target=5_000_000.0,
        pool_balance=950_000_000.0,
        original_pool_balance=950_000_000.0,
        cumulative_losses=0.0,
        sequential_pay_active=False,
        provenance={
            "tranches.class_a.balance": FieldProvenance(
                source="prospectus", method="deterministic", confidence=1.0
            )
        },
    )
    assert seed.provenance is not None
    # A rolled (engine-computed) state defaults to no provenance.
    rolled = DealState(
        reporting_date="2024-06-30",
        tranches=[TrancheState(name="Class A", balance=810_000_000.0, pdl_balance=0.0)],
        reserve_balance=5_000_000.0,
        reserve_target=5_000_000.0,
        pool_balance=910_000_000.0,
        original_pool_balance=950_000_000.0,
        cumulative_losses=250_000.0,
        sequential_pay_active=False,
    )
    assert rolled.provenance is None
