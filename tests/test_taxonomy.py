"""Tests for loanwhiz.extraction.taxonomy — the recipient/metric taxonomy mapper.

All deterministic-path tests run offline (no network, no LLM). The LLM-fallback
path is exercised with the Gemini classify stubbed via ``use_llm=False`` (forces
the ``unmapped`` escape for a novel string) and via a patched ``_llm_classify``.
"""

from __future__ import annotations

# Import the primitives package before any loanwhiz.domain import so the
# domain<->primitives module graph is populated in the cycle-safe order
# (a pre-existing import-order sensitivity; see
# loanwhiz.primitives.__init__.__getattr__). Harmless when domain is
# already imported by an earlier-collected test.
import loanwhiz.primitives  # noqa: F401  (import-order guard)

from unittest.mock import patch

import pytest

from loanwhiz.domain.rules import MetricType, RecipientType
from loanwhiz.extraction import taxonomy
from loanwhiz.extraction.taxonomy import (
    basis_for_recipient,
    build_amount_rule,
    map_metric,
    map_recipient,
    normalize_threshold_unit,
)

# ---------------------------------------------------------------------------
# Recipient mapping — every standard Green Lion recipient maps non-unmapped.
# ---------------------------------------------------------------------------

# (extracted free string, expected canonical recipient) — drawn from the seeded
# Green Lion 2026-1 waterfalls (revenue / redemption / post-enforcement).
_GL_RECIPIENTS: list[tuple[str, RecipientType]] = [
    ("security_trustee_fees", RecipientType.senior_expenses),
    ("various_fees_and_expenses", RecipientType.senior_expenses),
    ("various_agents_and_creditors", RecipientType.senior_expenses),
    ("issuer_expense_account_replenishment", RecipientType.senior_expenses),
    ("swap_counterparty_payments", RecipientType.swap_payment),
    ("swap_counterparty", RecipientType.swap_payment),
    ("class_a_interest", RecipientType.class_a_interest),
    ("class_a_pdl_replenishment", RecipientType.class_a_pdl_cure),
    ("class_b_pdl_replenishment", RecipientType.class_b_pdl_cure),
    ("reserve_account_replenishment", RecipientType.reserve_replenishment),
    ("class_a_notes_principal", RecipientType.class_a_principal),
    ("class_b_notes_principal", RecipientType.class_b_principal),
    ("class_c_notes_principal", RecipientType.class_c_principal),
    ("class_a_notes_principal_and_interest", RecipientType.class_a_principal),
    ("subordinated_swap_payments", RecipientType.subordinated_amounts),
    ("deferred_purchase_price_instalment", RecipientType.residual_certificate),
    ("deferred_purchase_price_instalment_to_seller", RecipientType.residual_certificate),
]


@pytest.mark.parametrize("raw,expected", _GL_RECIPIENTS)
def test_gl_recipient_maps_to_canonical(raw: str, expected: RecipientType) -> None:
    m = map_recipient(raw, use_llm=False)
    assert m.value == expected, f"{raw!r} -> {m.value} (expected {expected})"
    assert m.value != RecipientType.unmapped
    assert m.method == "deterministic"
    assert m.confidence > 0.0


def test_recipient_substring_pdl_class_refinement() -> None:
    assert map_recipient("class_b_pdl_topup", use_llm=False).value == (
        RecipientType.class_b_pdl_cure
    )
    # Bare "pdl" with no class defaults to the senior class.
    assert map_recipient("pdl_cure_amount", use_llm=False).value == (
        RecipientType.class_a_pdl_cure
    )


def test_unknown_recipient_degrades_to_unmapped_offline() -> None:
    m = map_recipient("exotic_synthetic_cdo_equity_kicker", use_llm=False)
    assert m.value == RecipientType.unmapped
    assert m.confidence == 0.0


def test_empty_recipient_is_unmapped() -> None:
    assert map_recipient("", use_llm=False).value == RecipientType.unmapped
    assert map_recipient("   ", use_llm=False).value == RecipientType.unmapped


def test_recipient_normalisation_is_punctuation_insensitive() -> None:
    assert map_recipient("Class A Interest", use_llm=False).value == (
        RecipientType.class_a_interest
    )
    assert map_recipient("class-a-interest", use_llm=False).value == (
        RecipientType.class_a_interest
    )


# ---------------------------------------------------------------------------
# Global-ABS breadth (#394) — deeper capital stacks + collapse-onto-existing.
# ---------------------------------------------------------------------------

# Deeper-stack interest / principal recipients, each via the deterministic path.
_DEEP_RECIPIENTS: list[tuple[str, RecipientType]] = [
    ("class_d_interest", RecipientType.class_d_interest),
    ("class_e_interest", RecipientType.class_e_interest),
    ("class_f_interest", RecipientType.class_f_interest),
    ("class_d_notes_principal", RecipientType.class_d_principal),
    ("class_e_notes_principal", RecipientType.class_e_principal),
    ("class_f_notes_principal", RecipientType.class_f_principal),
    ("class_c_pdl_replenishment", RecipientType.class_c_pdl_cure),
]


@pytest.mark.parametrize("raw,expected", _DEEP_RECIPIENTS)
def test_deep_stack_recipient_maps_deterministically(
    raw: str, expected: RecipientType
) -> None:
    m = map_recipient(raw, use_llm=False)
    assert m.value == expected, f"{raw!r} -> {m.value} (expected {expected})"
    assert m.value != RecipientType.unmapped
    assert m.method == "deterministic"


def test_deep_stack_recipients_bind_existing_basis() -> None:
    # The load-bearing invariant: a new RecipientType binds to an existing engine
    # basis key (never silently falls through to report_supplied).
    for r in (
        RecipientType.class_d_interest,
        RecipientType.class_e_interest,
        RecipientType.class_f_interest,
    ):
        assert basis_for_recipient(r) == "interest_accrual"
    for r in (
        RecipientType.class_d_principal,
        RecipientType.class_e_principal,
        RecipientType.class_f_principal,
    ):
        assert basis_for_recipient(r) == "principal_due"
    assert basis_for_recipient(RecipientType.class_c_pdl_cure) == "pdl_balance"
    assert (
        basis_for_recipient(RecipientType.liquidity_reserve_replenishment)
        == "target_shortfall"
    )


def test_generic_class_letter_refine_resolves_beyond_exact_aliases() -> None:
    # A class-E redemption phrasing that isn't an exact alias still resolves to
    # principal of the named class via the generic class_<letter>_... handler.
    assert map_recipient("class_e_notes_redemption", use_llm=False).value == (
        RecipientType.class_e_principal
    )
    # Coupon cue resolves to interest of the named class.
    assert map_recipient("class_f_coupon", use_llm=False).value == (
        RecipientType.class_f_interest
    )


def test_class_c_pdl_class_refinement() -> None:
    assert map_recipient("class_c_pdl_topup", use_llm=False).value == (
        RecipientType.class_c_pdl_cure
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Senior-cost vocabulary collapses onto senior_expenses (no new enum).
        ("paying_agent_fees", RecipientType.senior_expenses),
        ("cash_manager_fees", RecipientType.senior_expenses),
        ("account_bank_fees", RecipientType.senior_expenses),
        ("withholding_tax_gross_up", RecipientType.senior_expenses),
        # Cap/floor/basis-swap counterparties collapse onto swap_payment.
        ("cap_counterparty", RecipientType.swap_payment),
        ("basis_swap_counterparty", RecipientType.swap_payment),
        # Liquidity / commingling / set-off reserve top-ups.
        ("liquidity_reserve_replenishment", RecipientType.liquidity_reserve_replenishment),
        ("commingling_reserve_replenishment", RecipientType.liquidity_reserve_replenishment),
        ("set_off_reserve_replenishment", RecipientType.liquidity_reserve_replenishment),
    ],
)
def test_collapse_onto_existing_recipients(
    raw: str, expected: RecipientType
) -> None:
    m = map_recipient(raw, use_llm=False)
    assert m.value == expected, f"{raw!r} -> {m.value} (expected {expected})"
    assert m.method == "deterministic"


# ---------------------------------------------------------------------------
# Multilingual deterministic mapping (#394) — proves alias + diacritic fold,
# not the LLM (use_llm=False throughout).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Reserve fund — incl. the accented FR form the diacritic fold rescues.
        ("Fondo di riserva", RecipientType.reserve_replenishment),
        ("Fondo de reserva", RecipientType.reserve_replenishment),
        ("Fonds de réserve", RecipientType.reserve_replenishment),
        ("Reservefonds", RecipientType.reserve_replenishment),
        # Servicing.
        ("Commissioni di servicing", RecipientType.servicing_fee),
        ("Comisión de administración", RecipientType.servicing_fee),
        # Senior expenses / trustee.
        ("Commissioni e spese", RecipientType.senior_expenses),
        ("Comisiones y gastos", RecipientType.senior_expenses),
        ("Treuhänder", RecipientType.senior_expenses),
    ],
)
def test_multilingual_recipient_maps_on_deterministic_path(
    raw: str, expected: RecipientType
) -> None:
    m = map_recipient(raw, use_llm=False)
    assert m.value == expected, f"{raw!r} -> {m.value} (expected {expected})"
    assert m.method == "deterministic"


def test_normalise_diacritic_fold_is_ascii_noop() -> None:
    # The fold must be a strict no-op on pure ASCII (regression guard for the
    # existing English alias keys / tests).
    for ascii_key in (
        "class_a_interest",
        "security_trustee_fees",
        "reserve_account_replenishment",
        "cumulative_loss_rate_pct",
    ):
        assert taxonomy._normalise(ascii_key) == ascii_key
    # And it actually folds accents.
    assert taxonomy._normalise("Fonds de réserve") == "fonds_de_reserve"


# ---------------------------------------------------------------------------
# LLM fallback — only fires for an unrecognised string, with unmapped escape.
# ---------------------------------------------------------------------------


def test_recipient_llm_fallback_used_for_novel_string() -> None:
    # A non-English novel recipient the alias table cannot match; the stubbed LLM
    # classifies it as class_a_interest.
    with patch.object(
        taxonomy, "_llm_classify", return_value=("class_a_interest", 0.83)
    ) as stub:
        m = map_recipient("interessi_classe_a", description="interest on senior notes")
    stub.assert_called_once()
    assert m.value == RecipientType.class_a_interest
    assert m.method == "llm"
    assert m.confidence == pytest.approx(0.83)


def test_recipient_llm_decline_falls_to_unmapped() -> None:
    with patch.object(taxonomy, "_llm_classify", return_value=None):
        m = map_recipient("etwas_völlig_unbekanntes")
    assert m.value == RecipientType.unmapped


def test_recipient_llm_not_called_when_deterministic_hit() -> None:
    with patch.object(taxonomy, "_llm_classify") as stub:
        map_recipient("class_a_interest")
    stub.assert_not_called()


# ---------------------------------------------------------------------------
# Metric mapping.
# ---------------------------------------------------------------------------


def test_gl_metrics_map_to_canonical() -> None:
    assert map_metric("cumulative_loss_rate_pct", use_llm=False).value == (
        MetricType.cumulative_loss_rate
    )
    assert map_metric("reserve_fund_balance", use_llm=False).value == (
        MetricType.reserve_fund_ratio
    )
    assert map_metric("pool_balance_fraction", use_llm=False).value == (
        MetricType.pool_factor
    )
    # A bare PDL debit balance (the seeded GL trigger metric) → class A PDL.
    assert map_metric("pdl_debit_balance", use_llm=False).value == MetricType.class_a_pdl


def test_unknown_metric_degrades_to_unmapped_offline() -> None:
    assert map_metric("weather_index_basis", use_llm=False).value == MetricType.unmapped


# ---------------------------------------------------------------------------
# Global-ABS metric breadth (#394) — finer arrears, gross-default, deeper PDL.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("arrears_30d_ratio", MetricType.arrears_30d_ratio),
        ("arrears_30_ratio", MetricType.arrears_30d_ratio),
        ("arrears_60d_ratio", MetricType.arrears_60d_ratio),
        ("class_c_pdl", MetricType.class_c_pdl),
        ("cumulative_default_rate", MetricType.cumulative_default_rate),
        ("cumulative_default_ratio", MetricType.cumulative_default_rate),
        ("cumulative_gross_default_rate", MetricType.cumulative_default_rate),
        ("clean_up_call_threshold", MetricType.pool_factor),
    ],
)
def test_new_metric_maps_deterministically(raw: str, expected: MetricType) -> None:
    m = map_metric(raw, use_llm=False)
    assert m.value == expected, f"{raw!r} -> {m.value} (expected {expected})"
    assert m.value != MetricType.unmapped
    assert m.method == "deterministic"


def test_cumulative_default_distinct_from_loss() -> None:
    # The anti-mis-map assertion: gross default must NOT collapse onto net loss.
    default = map_metric("cumulative_default_rate", use_llm=False).value
    loss = map_metric("cumulative_loss_rate", use_llm=False).value
    assert default == MetricType.cumulative_default_rate
    assert loss == MetricType.cumulative_loss_rate
    assert default != loss
    # The substring path also keeps the two distinct (default precedes loss).
    assert map_metric("class_a_cumulative_default_test", use_llm=False).value == (
        MetricType.cumulative_default_rate
    )
    assert map_metric("class_a_cumulative_loss_test", use_llm=False).value == (
        MetricType.cumulative_loss_rate
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Perdite cumulate", MetricType.cumulative_loss_rate),
        ("Pérdidas acumuladas", MetricType.cumulative_loss_rate),
        ("Pertes cumulées", MetricType.cumulative_loss_rate),
        ("Tasso di insolvenza cumulato", MetricType.cumulative_default_rate),
        ("Taux de défaut cumulé", MetricType.cumulative_default_rate),
    ],
)
def test_multilingual_metric_maps_on_deterministic_path(
    raw: str, expected: MetricType
) -> None:
    m = map_metric(raw, use_llm=False)
    assert m.value == expected, f"{raw!r} -> {m.value} (expected {expected})"
    assert m.method == "deterministic"


def test_metric_llm_fallback() -> None:
    with patch.object(taxonomy, "_llm_classify", return_value=("wa_ltv", 0.9)):
        assert map_metric("loan_to_value_pondéré").value == MetricType.wa_ltv


# ---------------------------------------------------------------------------
# threshold_unit normalisation — done once, here.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("percentage", "percent"),
        ("%", "percent"),
        ("pct", "percent"),
        ("ratio", "fraction"),
        ("basis_points", "bps"),
        ("EUR", "eur"),
        ("euros", "eur"),
        (None, "fraction"),
        ("nonsense-unit", "fraction"),
    ],
)
def test_normalize_threshold_unit(raw: str | None, expected: str) -> None:
    assert normalize_threshold_unit(raw) == expected


# ---------------------------------------------------------------------------
# AmountRule basis binding.
# ---------------------------------------------------------------------------


def test_basis_for_recipient_bindings() -> None:
    assert basis_for_recipient(RecipientType.class_a_interest) == "interest_accrual"
    assert basis_for_recipient(RecipientType.class_a_pdl_cure) == "pdl_balance"
    assert basis_for_recipient(RecipientType.reserve_replenishment) == "target_shortfall"
    assert basis_for_recipient(RecipientType.class_a_principal) == "principal_due"
    assert basis_for_recipient(RecipientType.residual_certificate) == "residual"
    # Everything the engine has no formula for is report_supplied.
    assert basis_for_recipient(RecipientType.senior_expenses) == "report_supplied"
    assert basis_for_recipient(RecipientType.unmapped) == "report_supplied"


def test_build_amount_rule_unmapped_is_report_supplied() -> None:
    rule = build_amount_rule(RecipientType.unmapped, "some exotic prose")
    assert rule.calculator == RecipientType.unmapped
    assert rule.basis == "report_supplied"
    assert rule.raw_text == "some exotic prose"


def test_build_amount_rule_interest() -> None:
    rule = build_amount_rule(RecipientType.class_a_interest, "all interest due")
    assert rule.basis == "interest_accrual"
    assert rule.calculator == RecipientType.class_a_interest
