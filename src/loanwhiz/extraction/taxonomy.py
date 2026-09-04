"""Taxonomy mapping — extracted prose recipients / metrics → canonical enums.

This is the load-bearing generalization that makes an extracted step
*executable*. The engine computes per-recipient need via calculators keyed on
the canonical :class:`~loanwhiz.domain.rules.RecipientType`; a trigger is only
comparable when its metric is the canonical
:class:`~loanwhiz.domain.rules.MetricType`. The prospectus extractor, however,
emits **free-string** recipients/metrics (``"security_trustee_fees"``,
``"pdl_debit_balance"``, …) that differ per issuer and per language. This module
classifies each free string onto the closed canonical enum, with an explicit
``unmapped`` escape so an exotic / unrecognised string **degrades honestly**
(report-supplied / not-evaluable) instead of silently mis-mapping onto a wrong
calculator — the boundary-bug class the canonical schema exists to kill
(``docs/superpowers/specs/2026-06-20-prospectus-extractor-generalization-design.md``).

Two-stage classification, deterministic-first:

1. **Deterministic alias table** — covers the standard English / Green-Lion
   recipient + metric vocabulary (and obvious normalisations). Pure, offline,
   the path the cached English deals always take.
2. **LLM classify** — only for a string the alias table does not recognise.
   The prompt carries the closed enum + the ``unmapped`` escape, so a genuinely
   novel (e.g. non-English, exotic) recipient is classified by meaning rather
   than by keyword. Falls back to ``unmapped`` on any LLM error — never raises,
   never invents a mapping.

The amount *basis* (which fixed engine formula computes the step amount) is
derived from the mapped recipient via :func:`basis_for_recipient`, so an
``unmapped`` recipient always lands ``basis="report_supplied"`` and the prose is
retained as :attr:`AmountRule.raw_text` for audit (never executed).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass

# Import from the leaf module (not the ``loanwhiz.domain`` package __init__) to
# avoid widening the base-branch domain<->primitives import cycle: the package
# __init__ pulls in provenance -> primitives.base -> the whole primitives
# package, which (when imported before primitives) trips a partial-init cycle.
from loanwhiz.domain.rules import AmountRule, MetricType, RecipientType

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaxonomyMapping:
    """The outcome of classifying one free string onto a canonical enum value.

    Attributes:
        value:      The canonical enum value (``RecipientType`` or ``MetricType``);
                    ``*.unmapped`` when no confident mapping exists.
        confidence: Certainty in the mapping in ``[0, 1]`` — ``1.0`` for an exact
                    deterministic alias hit, the LLM's reported confidence for an
                    LLM hit, ``0.0`` for ``unmapped``.
        method:     ``"deterministic"`` (alias table) or ``"llm"`` (classified) —
                    feeds ``FieldProvenance.method``.
    """

    value: RecipientType | MetricType
    confidence: float
    method: str  # "deterministic" | "llm"


# ---------------------------------------------------------------------------
# Recipient alias table — free string → RecipientType.
# ---------------------------------------------------------------------------
#
# Keys are *normalised* recipient strings (see ``_normalise``). The standard
# English / Green-Lion vocabulary plus obvious synonyms. Substring rules (below)
# catch the "class_a_*" family generically so a new "class_a_<x>" recipient need
# not be enumerated exhaustively.

_RECIPIENT_ALIASES: dict[str, RecipientType] = {
    # Senior expenses / issuer costs / trustee / agents / tax. The wider global-
    # ABS senior-cost vocabulary (paying agent, cash manager, account bank,
    # registrar, corporate services, tax / withholding gross-up) collapses here:
    # all are senior admin costs the engine has no formula for (report_supplied),
    # so they are alias rows onto an existing value, not new enum members (#394).
    "security_trustee_fees": RecipientType.senior_expenses,
    "security_trustee": RecipientType.senior_expenses,
    "trustee_fees": RecipientType.senior_expenses,
    "note_trustee_fees": RecipientType.senior_expenses,
    "various_fees_and_expenses": RecipientType.senior_expenses,
    "various_agents_and_creditors": RecipientType.senior_expenses,
    "issuer_expenses": RecipientType.senior_expenses,
    "issuer_expense_account_replenishment": RecipientType.senior_expenses,
    "senior_expenses": RecipientType.senior_expenses,
    "senior_fees": RecipientType.senior_expenses,
    "agents_fees": RecipientType.senior_expenses,
    "paying_agent_fees": RecipientType.senior_expenses,
    "cash_manager_fees": RecipientType.senior_expenses,
    "account_bank_fees": RecipientType.senior_expenses,
    "registrar_fees": RecipientType.senior_expenses,
    "corporate_services_fees": RecipientType.senior_expenses,
    "administration_fees": RecipientType.senior_expenses,
    "issuer_tax": RecipientType.senior_expenses,
    "tax_and_withholding": RecipientType.senior_expenses,
    "withholding_tax_gross_up": RecipientType.senior_expenses,
    # IT/ES/FR/DE/NL senior-cost / trustee terms (diacritic-folded form).
    "spese": RecipientType.senior_expenses,
    "commissioni_e_spese": RecipientType.senior_expenses,
    "gastos": RecipientType.senior_expenses,
    "comisiones_y_gastos": RecipientType.senior_expenses,
    "frais": RecipientType.senior_expenses,
    "frais_et_commissions": RecipientType.senior_expenses,
    "kosten": RecipientType.senior_expenses,
    "gebuhren_und_auslagen": RecipientType.senior_expenses,
    "treuhander": RecipientType.senior_expenses,
    "vergoedingen_en_kosten": RecipientType.senior_expenses,
    # Servicing.
    "servicing_fee": RecipientType.servicing_fee,
    "servicer_fee": RecipientType.servicing_fee,
    "servicing_fees": RecipientType.servicing_fee,
    "commissioni_di_servicing": RecipientType.servicing_fee,
    "comision_de_administracion": RecipientType.servicing_fee,
    "commission_de_gestion": RecipientType.servicing_fee,
    "servicegebuhr": RecipientType.servicing_fee,
    "servicingvergoeding": RecipientType.servicing_fee,
    # Swap (non-subordinated). Cap / floor / basis-swap counterparties collapse
    # onto the swap calculator (same report_supplied treatment) — alias, not a
    # new enum value (#394).
    "swap_counterparty_payments": RecipientType.swap_payment,
    "swap_counterparty": RecipientType.swap_payment,
    "swap_payment": RecipientType.swap_payment,
    "interest_rate_swap": RecipientType.swap_payment,
    "cap_counterparty": RecipientType.swap_payment,
    "floor_counterparty": RecipientType.swap_payment,
    "basis_swap_counterparty": RecipientType.swap_payment,
    "hedge_counterparty": RecipientType.swap_payment,
    # Interest.
    "class_a_interest": RecipientType.class_a_interest,
    "class_b_interest": RecipientType.class_b_interest,
    "class_c_interest": RecipientType.class_c_interest,
    "class_d_interest": RecipientType.class_d_interest,
    "class_e_interest": RecipientType.class_e_interest,
    "class_f_interest": RecipientType.class_f_interest,
    # PDL cure / replenishment.
    "class_a_pdl_replenishment": RecipientType.class_a_pdl_cure,
    "class_a_pdl_cure": RecipientType.class_a_pdl_cure,
    "class_b_pdl_replenishment": RecipientType.class_b_pdl_cure,
    "class_b_pdl_cure": RecipientType.class_b_pdl_cure,
    "class_c_pdl_replenishment": RecipientType.class_c_pdl_cure,
    "class_c_pdl_cure": RecipientType.class_c_pdl_cure,
    # Reserve replenishment. The general reserve fund keeps ``reserve_
    # replenishment``; liquidity / commingling / set-off reserve top-ups (same
    # target-shortfall mechanic) map onto ``liquidity_reserve_replenishment``.
    "reserve_account_replenishment": RecipientType.reserve_replenishment,
    "reserve_fund_replenishment": RecipientType.reserve_replenishment,
    "reserve_replenishment": RecipientType.reserve_replenishment,
    "fondo_di_riserva": RecipientType.reserve_replenishment,
    "fondo_de_reserva": RecipientType.reserve_replenishment,
    "fonds_de_reserve": RecipientType.reserve_replenishment,
    "reservefonds": RecipientType.reserve_replenishment,
    "liquidity_reserve_replenishment": RecipientType.liquidity_reserve_replenishment,
    "liquidity_reserve_fund_replenishment": RecipientType.liquidity_reserve_replenishment,
    "liquidity_facility_repayment": RecipientType.liquidity_reserve_replenishment,
    "commingling_reserve_replenishment": RecipientType.liquidity_reserve_replenishment,
    "set_off_reserve_replenishment": RecipientType.liquidity_reserve_replenishment,
    # Principal.
    "class_a_notes_principal": RecipientType.class_a_principal,
    "class_a_principal": RecipientType.class_a_principal,
    "class_b_notes_principal": RecipientType.class_b_principal,
    "class_b_principal": RecipientType.class_b_principal,
    "class_c_notes_principal": RecipientType.class_c_principal,
    "class_c_principal": RecipientType.class_c_principal,
    "class_d_notes_principal": RecipientType.class_d_principal,
    "class_d_principal": RecipientType.class_d_principal,
    "class_e_notes_principal": RecipientType.class_e_principal,
    "class_e_principal": RecipientType.class_e_principal,
    "class_f_notes_principal": RecipientType.class_f_principal,
    "class_f_principal": RecipientType.class_f_principal,
    # Combined principal+interest (post-enforcement) — map to principal of the
    # most-senior class named; the senior interest is paid in the same step.
    "class_a_notes_principal_and_interest": RecipientType.class_a_principal,
    "class_b_notes_principal_and_interest": RecipientType.class_b_principal,
    # Subordinated amounts. Subordinated / defaulting-hedge termination payments
    # rank junior and collapse here (#394).
    "subordinated_swap_payments": RecipientType.subordinated_amounts,
    "subordinated_swap": RecipientType.subordinated_amounts,
    "subordinated_amounts": RecipientType.subordinated_amounts,
    "subordinated_hedge_termination": RecipientType.subordinated_amounts,
    "deferred_fees": RecipientType.subordinated_amounts,
    # Residual / deferred purchase price / seller deferred consideration.
    "deferred_purchase_price_instalment": RecipientType.residual_certificate,
    "deferred_purchase_price_instalment_to_seller": RecipientType.residual_certificate,
    "deferred_purchase_price": RecipientType.residual_certificate,
    "deferred_consideration": RecipientType.residual_certificate,
    "seller_deferred_consideration": RecipientType.residual_certificate,
    "residual_certificate": RecipientType.residual_certificate,
    "residual": RecipientType.residual_certificate,
}

# Substring rules applied when no exact alias hit. Ordered most-specific first;
# the first whose pattern is contained in the normalised string wins.
_RECIPIENT_SUBSTRINGS: list[tuple[str, RecipientType]] = [
    ("pdl", RecipientType.class_a_pdl_cure),  # refined by class below
    ("reserve", RecipientType.reserve_replenishment),
    ("swap", RecipientType.swap_payment),
    ("trustee", RecipientType.senior_expenses),
    ("servic", RecipientType.servicing_fee),
    ("deferred_purchase", RecipientType.residual_certificate),
    ("subordinated", RecipientType.subordinated_amounts),
    ("residual", RecipientType.residual_certificate),
]


# ---------------------------------------------------------------------------
# Metric alias table — free string → MetricType.
# ---------------------------------------------------------------------------

_METRIC_ALIASES: dict[str, MetricType] = {
    # Cumulative realised loss (net of recoveries).
    "cumulative_loss_rate": MetricType.cumulative_loss_rate,
    "cumulative_loss_rate_pct": MetricType.cumulative_loss_rate,
    "cumulative_net_loss_ratio": MetricType.cumulative_loss_rate,
    "loss_rate": MetricType.cumulative_loss_rate,
    "perdite_cumulate": MetricType.cumulative_loss_rate,
    "perdidas_acumuladas": MetricType.cumulative_loss_rate,
    "pertes_cumulees": MetricType.cumulative_loss_rate,
    # Cumulative gross default — kept DISTINCT from realised loss (#394). Many
    # ABS triggers test cumulative *default* (gross), not *loss* (net of
    # recoveries); collapsing the two would be the silent wrong-sentinel bug.
    "cumulative_default_rate": MetricType.cumulative_default_rate,
    "cumulative_default_ratio": MetricType.cumulative_default_rate,
    "cumulative_gross_default_rate": MetricType.cumulative_default_rate,
    "default_rate_cumulative": MetricType.cumulative_default_rate,
    "tasso_di_insolvenza_cumulato": MetricType.cumulative_default_rate,
    "tasa_de_morosidad_acumulada": MetricType.cumulative_default_rate,
    "taux_de_defaut_cumule": MetricType.cumulative_default_rate,
    "class_a_pdl": MetricType.class_a_pdl,
    "class_b_pdl": MetricType.class_b_pdl,
    "class_c_pdl": MetricType.class_c_pdl,
    "reserve_fund_ratio": MetricType.reserve_fund_ratio,
    "reserve_fund_balance": MetricType.reserve_fund_ratio,
    "reserve_fund_shortfall": MetricType.reserve_fund_ratio,
    "pool_factor": MetricType.pool_factor,
    "pool_balance_fraction": MetricType.pool_factor,
    "clean_up_call": MetricType.pool_factor,
    "clean_up_call_threshold": MetricType.pool_factor,
    "arrears_30d_ratio": MetricType.arrears_30d_ratio,
    "arrears_30_ratio": MetricType.arrears_30d_ratio,
    "arrears_60d_ratio": MetricType.arrears_60d_ratio,
    "arrears_60_ratio": MetricType.arrears_60d_ratio,
    "arrears_90d_ratio": MetricType.arrears_90d_ratio,
    "arrears_90_ratio": MetricType.arrears_90d_ratio,
    "arrears_180d_ratio": MetricType.arrears_180d_ratio,
    "wa_ltv": MetricType.wa_ltv,
    "weighted_average_ltv": MetricType.wa_ltv,
    # ---- Coverage tests (#452) ----
    # Only the CLASSLESS forms live here. The per-class forms
    # (``class_b_oc_test``, ``Class C Interest Coverage Ratio``, …) are resolved
    # generically by ``_refine_coverage_metric`` so every phrasing need not be
    # enumerated — the same division of labour as
    # ``_refine_class_recipient`` on the recipient side.
    #
    # A classless coverage test names no attachment point, so it cannot say
    # WHICH point it measured. It resolves to the Class A (senior) test, the
    # same senior-default convention the classless PDL string takes, and
    # ``map_metric`` reports the lower confidence that guess deserves.
    "overcollateralisation_ratio": MetricType.class_a_oc_ratio,
    "overcollateralization_ratio": MetricType.class_a_oc_ratio,
    "overcollateralisation_test": MetricType.class_a_oc_ratio,
    "overcollateralization_test": MetricType.class_a_oc_ratio,
    "oc_ratio": MetricType.class_a_oc_ratio,
    "oc_test": MetricType.class_a_oc_ratio,
    "par_value_test": MetricType.class_a_oc_ratio,
    "par_value_ratio": MetricType.class_a_oc_ratio,
    "interest_coverage_ratio": MetricType.class_a_ic_ratio,
    "interest_coverage_test": MetricType.class_a_ic_ratio,
    "ic_ratio": MetricType.class_a_ic_ratio,
    "ic_test": MetricType.class_a_ic_ratio,
}

_METRIC_SUBSTRINGS: list[tuple[str, MetricType]] = [
    # Order: most-specific first. Default must precede loss so a
    # "cumulative_default" string isn't swallowed by the "cumulative_loss"
    # pattern, and the day-bucketed arrears patterns precede the bare "arrears".
    ("cumulative_default", MetricType.cumulative_default_rate),
    ("cumulative_loss", MetricType.cumulative_loss_rate),
    ("class_a_pdl", MetricType.class_a_pdl),
    ("class_b_pdl", MetricType.class_b_pdl),
    ("class_c_pdl", MetricType.class_c_pdl),
    ("reserve_fund", MetricType.reserve_fund_ratio),
    ("pool_factor", MetricType.pool_factor),
    ("pool_balance", MetricType.pool_factor),
    ("arrears_180", MetricType.arrears_180d_ratio),
    ("arrears_90", MetricType.arrears_90d_ratio),
    ("arrears_60", MetricType.arrears_60d_ratio),
    ("arrears_30", MetricType.arrears_30d_ratio),
    ("ltv", MetricType.wa_ltv),
]


# ---------------------------------------------------------------------------
# Amount basis binding — mapped recipient → fixed engine formula key.
# ---------------------------------------------------------------------------

_BASIS_FOR_RECIPIENT: dict[RecipientType, str] = {
    RecipientType.senior_expenses: "report_supplied",
    RecipientType.servicing_fee: "report_supplied",
    RecipientType.swap_payment: "report_supplied",
    RecipientType.class_a_interest: "interest_accrual",
    RecipientType.class_b_interest: "interest_accrual",
    RecipientType.class_c_interest: "interest_accrual",
    RecipientType.class_d_interest: "interest_accrual",
    RecipientType.class_e_interest: "interest_accrual",
    RecipientType.class_f_interest: "interest_accrual",
    RecipientType.class_a_pdl_cure: "pdl_balance",
    RecipientType.class_b_pdl_cure: "pdl_balance",
    RecipientType.class_c_pdl_cure: "pdl_balance",
    RecipientType.liquidity_reserve_replenishment: "target_shortfall",
    RecipientType.reserve_replenishment: "target_shortfall",
    RecipientType.class_a_principal: "principal_due",
    RecipientType.class_b_principal: "principal_due",
    RecipientType.class_c_principal: "principal_due",
    RecipientType.class_d_principal: "principal_due",
    RecipientType.class_e_principal: "principal_due",
    RecipientType.class_f_principal: "principal_due",
    RecipientType.subordinated_amounts: "report_supplied",
    RecipientType.residual_certificate: "residual",
    RecipientType.unmapped: "report_supplied",
}


def basis_for_recipient(recipient: RecipientType) -> str:
    """Return the canonical :class:`AmountRule` ``basis`` key for a recipient.

    The binding is fixed: an interest recipient accrues, a PDL recipient cures up
    to its balance, a reserve recipient tops up to target, a principal recipient
    amortises, a residual recipient sweeps the remainder, and everything the
    engine has no formula for (senior expenses, swap, subordinated, ``unmapped``)
    is ``report_supplied`` — the amount comes from ``PeriodInputs.step_overrides``
    rather than an engine calculator.
    """
    return _BASIS_FOR_RECIPIENT.get(recipient, "report_supplied")


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _normalise(raw: str) -> str:
    """Lowercase, fold diacritics, collapse non-alphanumerics to underscores.

    The diacritic fold (Unicode NFKD decomposition + drop of combining marks,
    applied before the ASCII-only collapse) is what makes the non-English alias
    keys hit deterministically (#394): without it ``"fonds de réserve"`` would
    normalise to ``"fonds_de_r_serve"`` (the ``é`` lost to the collapse) and
    never match a stored alias. With the fold it becomes ``"fonds_de_reserve"``.

    The fold is a strict no-op for pure-ASCII input — NFKD leaves ASCII
    code points unchanged and there are no combining marks to drop — so every
    existing English alias key and its tests are unaffected.
    """
    s = (raw or "").strip().lower()
    # Fold diacritics: decompose (é → e + ◌́) then drop combining marks.
    s = "".join(
        ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch)
    )
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def _refine_pdl_class(normalised: str) -> RecipientType:
    """Pick the right PDL-cure class from a normalised string mentioning PDL.

    Recognises the Class A/B/C ledgers; an unspecified class defaults to A (the
    senior PDL the sequential/PDL trigger keys on).
    """
    if "class_c" in normalised or "_c_" in normalised or normalised.endswith("_c"):
        return RecipientType.class_c_pdl_cure
    if "class_b" in normalised or "_b_" in normalised or normalised.endswith("_b"):
        return RecipientType.class_b_pdl_cure
    return RecipientType.class_a_pdl_cure


# Generic deeper-class recipient parse: ``class_<letter>_<...interest|principal>``.
# Lets a deal name a class beyond the exact-alias rows (e.g. an unusual
# ``class_e_notes_redemption``) resolve to the right interest/principal recipient
# without exhaustively enumerating every phrasing. PDL is handled by
# ``_refine_pdl_class`` (more specific, checked first in ``map_recipient``).
_CLASS_INTEREST_BY_LETTER: dict[str, RecipientType] = {
    "a": RecipientType.class_a_interest,
    "b": RecipientType.class_b_interest,
    "c": RecipientType.class_c_interest,
    "d": RecipientType.class_d_interest,
    "e": RecipientType.class_e_interest,
    "f": RecipientType.class_f_interest,
}
_CLASS_PRINCIPAL_BY_LETTER: dict[str, RecipientType] = {
    "a": RecipientType.class_a_principal,
    "b": RecipientType.class_b_principal,
    "c": RecipientType.class_c_principal,
    "d": RecipientType.class_d_principal,
    "e": RecipientType.class_e_principal,
    "f": RecipientType.class_f_principal,
}
_CLASS_LETTER_RE = re.compile(r"class_([a-f])(?![a-z])")

# Coverage metrics by attachment point (#452) — the metric-side counterpart of
# the recipient tables above. A coverage test is named for the point in the
# stack it measures, so the class letter IS the metric's identity.
_CLASS_OC_BY_LETTER: dict[str, MetricType] = {
    "a": MetricType.class_a_oc_ratio,
    "b": MetricType.class_b_oc_ratio,
    "c": MetricType.class_c_oc_ratio,
    "d": MetricType.class_d_oc_ratio,
    "e": MetricType.class_e_oc_ratio,
    "f": MetricType.class_f_oc_ratio,
}
_CLASS_IC_BY_LETTER: dict[str, MetricType] = {
    "a": MetricType.class_a_ic_ratio,
    "b": MetricType.class_b_ic_ratio,
    "c": MetricType.class_c_ic_ratio,
    "d": MetricType.class_d_ic_ratio,
    "e": MetricType.class_e_ic_ratio,
    "f": MetricType.class_f_ic_ratio,
}

# The coverage values a CLASSLESS alias row lands on. Membership here means
# "this was a senior-default guess, not an exact identification", which is what
# ``map_metric`` reports as confidence — an honest 0.7 rather than a 1.0 the
# input never earned.
_CLASSLESS_COVERAGE_DEFAULTS: frozenset[MetricType] = frozenset(
    {MetricType.class_a_oc_ratio, MetricType.class_a_ic_ratio}
)

# Cues that a metric string names a coverage test. "par value" is the CLO
# prospectus's own name for the overcollateralisation test; "principal
# coverage" is the same test again under a third name.
# A standalone a-f letter token — the second class of a combined coverage test.
_BARE_LETTER_RE = re.compile(r"(?<![a-z])([a-f])(?![a-z])")

_OC_CUE_RE = re.compile(r"overcollateral|over_collateral|par_value|principal_coverage|(?<![a-z])oc(?![a-z])")
_IC_CUE_RE = re.compile(r"interest_coverage|interest_cover(?![a-z])|(?<![a-z])ic(?![a-z])")


def _refine_coverage_metric(normalised: str) -> MetricType | None:
    """Resolve a ``class_<letter>`` overcollateralisation / interest-coverage metric.

    Returns ``None`` when the string carries no coverage cue, no class letter,
    or **more than one distinct class letter** — each of which is a case the
    caller should pass to the LLM / ``unmapped`` escape rather than guess at.

    The multi-letter refusal is the load-bearing one. A combined "Class A/B
    Overcollateralisation Test" measures the A+B attachment point, so guessing
    the first letter would understate the denominator and therefore **overstate**
    the ratio — a coverage test reported as healthier than it is. Refusing to
    place an ambiguous string is the honest failure direction; a confident wrong
    attachment point is the silent one.
    """
    letters = set(_CLASS_LETTER_RE.findall(normalised))
    # A combined test names its further classes as BARE letter tokens, which the
    # ``class_``-prefixed pattern cannot see: "Class A/B Overcollateralisation
    # Test" normalises to ``class_a_b_...`` and "Class C and D Par Value Test"
    # to ``class_c_and_d_...``. Scanning for standalone a-f tokens catches both.
    # A single-class string is unaffected — in ``class_e_par_value_test`` the
    # only standalone letter is the class letter itself.
    letters |= set(_BARE_LETTER_RE.findall(normalised))
    if len(letters) != 1:
        return None
    letter = letters.pop()
    # Interest coverage is checked first: an "interest coverage" string carries
    # no OC cue, but a bare "oc"/"ic" token is short enough that order matters.
    if _IC_CUE_RE.search(normalised):
        return _CLASS_IC_BY_LETTER.get(letter)
    if _OC_CUE_RE.search(normalised):
        return _CLASS_OC_BY_LETTER.get(letter)
    return None


def _refine_class_recipient(normalised: str) -> RecipientType | None:
    """Resolve a generic ``class_<letter>_...`` interest/principal recipient.

    Returns ``None`` when the string does not carry a recognisable class letter
    (a-f) plus an interest/principal/redemption cue, so the caller can fall
    through to the LLM / ``unmapped`` path. ``principal`` / ``redemption`` /
    ``amortisation`` cues win over ``interest`` when both appear, matching the
    "principal and interest" post-enforcement convention (map to principal of
    the named class; the senior interest is paid in the same step).
    """
    m = _CLASS_LETTER_RE.search(normalised)
    if m is None:
        return None
    letter = m.group(1)
    if re.search(r"principal|redempt|amortis|amortiz", normalised):
        return _CLASS_PRINCIPAL_BY_LETTER.get(letter)
    if "interest" in normalised or "coupon" in normalised:
        return _CLASS_INTEREST_BY_LETTER.get(letter)
    return None


# ---------------------------------------------------------------------------
# Public API — recipient
# ---------------------------------------------------------------------------


def map_recipient(
    raw: str,
    description: str = "",
    *,
    use_llm: bool = True,
) -> TaxonomyMapping:
    """Classify a free-string recipient onto :class:`RecipientType`.

    Deterministic alias / substring first; LLM classify only for an unrecognised
    string (and only when ``use_llm`` is true — tests pass ``use_llm=False`` to
    force the deterministic-only path). Always returns a mapping — ``unmapped``
    with confidence ``0.0`` when nothing matches and the LLM declines / errors.

    Parameters
    ----------
    raw:
        The extracted recipient identifier, e.g. ``"security_trustee_fees"``.
    description:
        The step's plain-text description, used as extra context for the LLM
        classify path (ignored on the deterministic path).
    use_llm:
        When ``False``, skip the LLM fallback entirely (offline / test path).
    """
    normalised = _normalise(raw)
    if not normalised:
        return TaxonomyMapping(RecipientType.unmapped, 0.0, "deterministic")

    # Exact alias.
    hit = _RECIPIENT_ALIASES.get(normalised)
    if hit is not None:
        return TaxonomyMapping(hit, 1.0, "deterministic")

    # Substring rules. PDL is most-specific (checked here via its own pattern);
    # the generic class_<letter>_<interest|principal> refine runs next so a
    # deeper-stack class that escaped the exact-alias rows still resolves before
    # the broader fee/reserve substrings or the LLM fallback.
    for pattern, value in _RECIPIENT_SUBSTRINGS:
        if pattern in normalised:
            if pattern == "pdl":
                value = _refine_pdl_class(normalised)
            return TaxonomyMapping(value, 0.9, "deterministic")

    class_hit = _refine_class_recipient(normalised)
    if class_hit is not None:
        return TaxonomyMapping(class_hit, 0.9, "deterministic")

    # LLM fallback for a genuinely-novel string.
    if use_llm:
        llm = _llm_classify(
            raw=raw,
            description=description,
            options=[e.value for e in RecipientType],
            kind="waterfall step recipient",
        )
        if llm is not None:
            try:
                return TaxonomyMapping(RecipientType(llm[0]), llm[1], "llm")
            except ValueError:
                pass

    return TaxonomyMapping(RecipientType.unmapped, 0.0, "deterministic")


# ---------------------------------------------------------------------------
# Public API — metric
# ---------------------------------------------------------------------------


def map_metric(raw: str, description: str = "", *, use_llm: bool = True) -> TaxonomyMapping:
    """Classify a free-string trigger metric onto :class:`MetricType`.

    Same deterministic-first + LLM-fallback + ``unmapped`` escape contract as
    :func:`map_recipient`.
    """
    normalised = _normalise(raw)
    if not normalised:
        return TaxonomyMapping(MetricType.unmapped, 0.0, "deterministic")

    hit = _METRIC_ALIASES.get(normalised)
    if hit is not None:
        # A classless coverage alias names no attachment point, so the Class A
        # landing is a senior-default guess, not an exact match — report it at
        # the same confidence the classless PDL default carries rather than
        # claiming certainty the string does not support.
        if hit in _CLASSLESS_COVERAGE_DEFAULTS:
            return TaxonomyMapping(hit, 0.7, "deterministic")
        return TaxonomyMapping(hit, 1.0, "deterministic")

    # A bare "pdl_debit_balance" with no class — default to class A (senior).
    if "pdl" in normalised and "class" not in normalised:
        return TaxonomyMapping(MetricType.class_a_pdl, 0.7, "deterministic")

    # A per-attachment-point coverage test (#452). Checked before the generic
    # substring pass so the class letter — which is the metric's identity — is
    # read rather than discarded.
    coverage = _refine_coverage_metric(normalised)
    if coverage is not None:
        return TaxonomyMapping(coverage, 0.9, "deterministic")

    for pattern, value in _METRIC_SUBSTRINGS:
        if pattern in normalised:
            return TaxonomyMapping(value, 0.9, "deterministic")

    if use_llm:
        llm = _llm_classify(
            raw=raw,
            description=description,
            options=[e.value for e in MetricType],
            kind="trigger / covenant metric",
        )
        if llm is not None:
            try:
                return TaxonomyMapping(MetricType(llm[0]), llm[1], "llm")
            except ValueError:
                pass

    return TaxonomyMapping(MetricType.unmapped, 0.0, "deterministic")


#: Every per-attachment-point coverage metric, as one set.
_COVERAGE_METRICS: frozenset[MetricType] = frozenset(
    list(_CLASS_OC_BY_LETTER.values()) + list(_CLASS_IC_BY_LETTER.values())
)


def coverage_metric_for(raw: str) -> MetricType | None:
    """The coverage :class:`MetricType` a free string names, or ``None``.

    The single classification point for coverage vocabulary, shared by the
    extraction path and the covenant monitor (#452). The monitor keeps its own
    alias map for the *tape* vocabulary, but routing coverage strings back
    through :func:`map_metric` means the two sides cannot drift into two
    vocabularies for one concept — a row added to the tables above is
    understood by the monitor with no second edit.

    Deterministic only: ``use_llm=False``, so this never makes a network call on
    the monitor's evaluation path. A string naming no coverage test returns
    ``None`` and the caller falls back to its own resolution.
    """
    mapped = map_metric(raw, use_llm=False)
    return mapped.value if mapped.value in _COVERAGE_METRICS else None  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# threshold_unit normalisation — done ONCE here (canonical contract).
# ---------------------------------------------------------------------------

_UNIT_ALIASES: dict[str, str] = {
    "percent": "percent",
    "percentage": "percent",
    "pct": "percent",
    "%": "percent",
    "fraction": "fraction",
    "ratio": "fraction",
    "bps": "bps",
    "basis_points": "bps",
    "eur": "eur",
    "euro": "eur",
    "euros": "eur",
    "currency": "eur",
}


def normalize_threshold_unit(raw: str | None) -> str:
    """Normalise an extracted threshold unit to the canonical enum.

    Canonical units are ``percent | fraction | bps | eur`` (see
    :class:`~loanwhiz.domain.rules.TriggerRule`). An unrecognised / missing unit
    defaults to ``"fraction"`` (the engine's native ratio form) — conservative,
    and never an out-of-enum value that would fail validation downstream.
    """
    if not raw:
        return "fraction"
    # Check the raw token first so symbol units like "%" (which normalise to the
    # empty string) are still recognised, then fall back to the normalised form.
    stripped = raw.strip().lower()
    if stripped in _UNIT_ALIASES:
        return _UNIT_ALIASES[stripped]
    return _UNIT_ALIASES.get(_normalise(raw), "fraction")


# ---------------------------------------------------------------------------
# AmountRule builder
# ---------------------------------------------------------------------------


def build_amount_rule(recipient: RecipientType, raw_text: str) -> AmountRule:
    """Build an :class:`AmountRule` for a mapped recipient.

    The ``basis`` is bound from the recipient via :func:`basis_for_recipient`;
    the prose is retained verbatim as ``raw_text`` for audit (never executed).
    """
    return AmountRule(
        calculator=recipient,
        basis=basis_for_recipient(recipient),  # type: ignore[arg-type]
        raw_text=raw_text or "",
    )


# ---------------------------------------------------------------------------
# LLM classify (only invoked for unrecognised strings)
# ---------------------------------------------------------------------------


def _llm_classify(
    *,
    raw: str,
    description: str,
    options: list[str],
    kind: str,
) -> tuple[str, float] | None:
    """Classify ``raw`` onto one of ``options`` via Gemini; ``None`` on any error.

    Returns ``(chosen_value, confidence)`` or ``None`` if the call fails or the
    model declines. The caller treats ``None`` (and any out-of-enum value) as the
    ``unmapped`` escape — this function never raises into the extraction path.
    """
    try:
        from google import genai
        from google.genai import types as genai_types

        from loanwhiz.config import GCP_LOCATION, GCP_PROJECT, MODEL_FLASH
    except Exception:
        return None

    prompt = (
        "You are classifying a structured-finance "
        f"{kind} onto a fixed canonical taxonomy.\n\n"
        f'The extracted identifier is: "{raw}"\n'
        f'Its description / context is: "{description}"\n\n'
        "Choose EXACTLY ONE canonical value from this closed list that best "
        "matches the meaning (regardless of the source language):\n"
        + "\n".join(f"- {o}" for o in options)
        + '\n\nIf none genuinely matches, choose "unmapped". '
        'Respond as compact JSON: {"value": "<canonical_value>", '
        '"confidence": <0..1>}. Do not invent a value outside the list.'
    )

    try:
        client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)
        response = client.models.generate_content(
            model=MODEL_FLASH,
            contents=prompt,
            config=genai_types.GenerateContentConfig(temperature=0.0),
        )
        text = (response.text or "").strip()
    except Exception:
        return None

    # Tolerate a code-fence-wrapped or chatty response — extract the JSON object.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        payload = json.loads(match.group(0))
    except (ValueError, json.JSONDecodeError):
        return None

    value = payload.get("value")
    if not isinstance(value, str) or value not in options:
        return None
    try:
        confidence = float(payload.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    return value, max(0.0, min(1.0, confidence))
