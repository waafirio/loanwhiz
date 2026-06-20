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
    # Senior expenses / issuer costs / trustee / agents.
    "security_trustee_fees": RecipientType.senior_expenses,
    "security_trustee": RecipientType.senior_expenses,
    "trustee_fees": RecipientType.senior_expenses,
    "various_fees_and_expenses": RecipientType.senior_expenses,
    "various_agents_and_creditors": RecipientType.senior_expenses,
    "issuer_expenses": RecipientType.senior_expenses,
    "issuer_expense_account_replenishment": RecipientType.senior_expenses,
    "senior_expenses": RecipientType.senior_expenses,
    "senior_fees": RecipientType.senior_expenses,
    "agents_fees": RecipientType.senior_expenses,
    # Servicing.
    "servicing_fee": RecipientType.servicing_fee,
    "servicer_fee": RecipientType.servicing_fee,
    "servicing_fees": RecipientType.servicing_fee,
    # Swap (non-subordinated).
    "swap_counterparty_payments": RecipientType.swap_payment,
    "swap_counterparty": RecipientType.swap_payment,
    "swap_payment": RecipientType.swap_payment,
    "interest_rate_swap": RecipientType.swap_payment,
    # Interest.
    "class_a_interest": RecipientType.class_a_interest,
    "class_b_interest": RecipientType.class_b_interest,
    "class_c_interest": RecipientType.class_c_interest,
    # PDL cure / replenishment.
    "class_a_pdl_replenishment": RecipientType.class_a_pdl_cure,
    "class_a_pdl_cure": RecipientType.class_a_pdl_cure,
    "class_b_pdl_replenishment": RecipientType.class_b_pdl_cure,
    "class_b_pdl_cure": RecipientType.class_b_pdl_cure,
    # Reserve replenishment.
    "reserve_account_replenishment": RecipientType.reserve_replenishment,
    "reserve_fund_replenishment": RecipientType.reserve_replenishment,
    "reserve_replenishment": RecipientType.reserve_replenishment,
    # Principal.
    "class_a_notes_principal": RecipientType.class_a_principal,
    "class_a_principal": RecipientType.class_a_principal,
    "class_b_notes_principal": RecipientType.class_b_principal,
    "class_b_principal": RecipientType.class_b_principal,
    "class_c_notes_principal": RecipientType.class_c_principal,
    "class_c_principal": RecipientType.class_c_principal,
    # Combined principal+interest (post-enforcement) — map to principal of the
    # most-senior class named; the senior interest is paid in the same step.
    "class_a_notes_principal_and_interest": RecipientType.class_a_principal,
    "class_b_notes_principal_and_interest": RecipientType.class_b_principal,
    # Subordinated amounts.
    "subordinated_swap_payments": RecipientType.subordinated_amounts,
    "subordinated_swap": RecipientType.subordinated_amounts,
    "subordinated_amounts": RecipientType.subordinated_amounts,
    "deferred_fees": RecipientType.subordinated_amounts,
    # Residual / deferred purchase price.
    "deferred_purchase_price_instalment": RecipientType.residual_certificate,
    "deferred_purchase_price_instalment_to_seller": RecipientType.residual_certificate,
    "deferred_purchase_price": RecipientType.residual_certificate,
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
    "cumulative_loss_rate": MetricType.cumulative_loss_rate,
    "cumulative_loss_rate_pct": MetricType.cumulative_loss_rate,
    "cumulative_net_loss_ratio": MetricType.cumulative_loss_rate,
    "loss_rate": MetricType.cumulative_loss_rate,
    "class_a_pdl": MetricType.class_a_pdl,
    "class_b_pdl": MetricType.class_b_pdl,
    "reserve_fund_ratio": MetricType.reserve_fund_ratio,
    "reserve_fund_balance": MetricType.reserve_fund_ratio,
    "reserve_fund_shortfall": MetricType.reserve_fund_ratio,
    "pool_factor": MetricType.pool_factor,
    "pool_balance_fraction": MetricType.pool_factor,
    "arrears_90d_ratio": MetricType.arrears_90d_ratio,
    "arrears_90_ratio": MetricType.arrears_90d_ratio,
    "arrears_180d_ratio": MetricType.arrears_180d_ratio,
    "wa_ltv": MetricType.wa_ltv,
    "weighted_average_ltv": MetricType.wa_ltv,
}

_METRIC_SUBSTRINGS: list[tuple[str, MetricType]] = [
    ("cumulative_loss", MetricType.cumulative_loss_rate),
    ("class_a_pdl", MetricType.class_a_pdl),
    ("class_b_pdl", MetricType.class_b_pdl),
    ("reserve_fund", MetricType.reserve_fund_ratio),
    ("pool_factor", MetricType.pool_factor),
    ("pool_balance", MetricType.pool_factor),
    ("arrears_180", MetricType.arrears_180d_ratio),
    ("arrears_90", MetricType.arrears_90d_ratio),
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
    RecipientType.class_a_pdl_cure: "pdl_balance",
    RecipientType.class_b_pdl_cure: "pdl_balance",
    RecipientType.reserve_replenishment: "target_shortfall",
    RecipientType.class_a_principal: "principal_due",
    RecipientType.class_b_principal: "principal_due",
    RecipientType.class_c_principal: "principal_due",
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
    """Lowercase, collapse non-alphanumerics to single underscores, strip ends."""
    s = (raw or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def _refine_pdl_class(normalised: str) -> RecipientType:
    """Pick the right PDL-cure class from a normalised string mentioning PDL."""
    if "class_b" in normalised or "_b_" in normalised or normalised.endswith("_b"):
        return RecipientType.class_b_pdl_cure
    return RecipientType.class_a_pdl_cure


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

    # Substring rules.
    for pattern, value in _RECIPIENT_SUBSTRINGS:
        if pattern in normalised:
            if pattern == "pdl":
                value = _refine_pdl_class(normalised)
            return TaxonomyMapping(value, 0.9, "deterministic")

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
        return TaxonomyMapping(hit, 1.0, "deterministic")

    # A bare "pdl_debit_balance" with no class — default to class A (senior).
    if "pdl" in normalised and "class" not in normalised:
        return TaxonomyMapping(MetricType.class_a_pdl, 0.7, "deterministic")

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
