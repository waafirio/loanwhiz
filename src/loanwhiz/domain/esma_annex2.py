"""ESMA RTS Annex 2 (RMBS) field-code mapping table.

The tape side of LoanWhiz already has a *canonical schema* — the regulatory
**ESMA Securitisation RTS Annex 2** loan-level template for residential real
estate (RMBS) underlying exposures. Every column in an ESMA Annex 2 loan tape
carries a stable **RREL field code** (``RREL1`` … ``RRELnn``); a deeploans /
HuggingFace tape exposes those as human-readable column names that vary subtly
across issuers and vintages. This module is the **single canonical mapping**
between the regulatory RREL code, the semantic meaning of the field, and the
``canonical_column`` name LoanWhiz normalises to.

Why this module exists (Phase 4 of the EDW design)
--------------------------------------------------
The canonical-schema design doc
(``docs/superpowers/specs/2026-06-20-canonical-domain-schema-design.md``,
decision D8 / "ESMA Annex 2 anchoring") fixed the *mechanism* — RREL field codes
live in :class:`loanwhiz.primitives.base.Citation`'s ``page_or_row`` as citation
*locators* on ``RiskSignals`` / ``CollectionLegs`` provenance — but deferred
"Full ESMA Annex 2 field-code mapping table → Phase 4." This module is that
table. It is the one place the RREL↔field↔column relationship is declared, so:

- the tape normaliser resolves issuer column names onto canonical names through
  one source rather than ad-hoc per-column ``col_map`` lookups, and
- covenant / provenance code can cite the regulatory locator for a value via
  :func:`locator_for` rather than hand-writing ``"RREL… <field>"`` strings.

Scope: **Annex 2 (RMBS)** specifically — the load-bearing fields the LoanWhiz
tape normaliser and the tape-native (B7) covenant signals consume. The record
shape admits other annexes (Auto/SME) later without breaking callers; this
module ships the RMBS table the current deal model needs.

The mapping is intentionally *additive and forgiving*: a tape column that is not
in the table simply does not resolve (callers keep their existing behaviour),
and a field can carry ``synonyms`` so issuer/vintage column-name drift resolves
onto one canonical name.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Record shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Annex2Field:
    """One ESMA RTS Annex 2 (RMBS) field-code mapping record.

    Attributes:
        code:
            The ESMA RTS Annex 2 RREL field code, e.g. ``"RREL18"``. Stable
            across issuers and vintages — the regulatory anchor.
        field_name:
            The semantic field name LoanWhiz uses to refer to this datum
            (snake_case), e.g. ``"current_balance"``.
        description:
            One-line human-readable description of what the field carries.
        canonical_column:
            The canonical (lower-cased) tape column name LoanWhiz normalises
            this field to. Often equals ``field_name`` but differs where the
            historical Green-Lion column name is the canonical one
            (e.g. ``"cltomv_current"`` for current LTV).
        synonyms:
            Alternative (lower-cased) column names seen across issuers/vintages
            that resolve onto the same canonical column. Empty when the
            canonical column is the only spelling.
    """

    code: str
    field_name: str
    description: str
    canonical_column: str
    synonyms: tuple[str, ...] = field(default=())


# ---------------------------------------------------------------------------
# The canonical Annex 2 (RMBS) table
# ---------------------------------------------------------------------------
#
# Codes follow the ESMA Securitisation RTS Annex 2 residential-real-estate
# (RREL) loan-level template. The subset below covers the fields the LoanWhiz
# tape normaliser and the tape-native (B7) covenant signals read; it is the
# load-bearing slice, not the full ~120-field template. Add a row here rather
# than scattering column-name handling across the normaliser/covenant code.

ANNEX2_RMBS_FIELDS: tuple[Annex2Field, ...] = (
    # --- Identifiers & deal-level ---
    Annex2Field(
        code="RREL1",
        field_name="loan_identifier",
        description="Unique identifier for the underlying exposure (loan).",
        canonical_column="loan_identifier",
        synonyms=("loan_id", "underlying_exposure_identifier", "rrel1"),
    ),
    Annex2Field(
        code="RREL3",
        field_name="transaction_name",
        description="Name of the securitisation / transaction the loan belongs to.",
        canonical_column="transaction_name",
        synonyms=("deal_name", "rrel3"),
    ),
    Annex2Field(
        code="RREL5",
        field_name="reporting_date",
        description="Data cut-off / reporting reference date for the tape.",
        canonical_column="reporting_date",
        synonyms=("data_cut_off_date", "pool_cut_off_date", "rrel5"),
    ),
    # --- Geography & collateral attributes ---
    Annex2Field(
        code="RREL15",
        field_name="geographic_region",
        description="Geographic region (NUTS-2 / province) of the property.",
        canonical_column="province",
        synonyms=("geographic_region", "region", "nuts2", "rrel15"),
    ),
    Annex2Field(
        code="RREL16",
        field_name="property_type",
        description="Type of the residential property securing the loan.",
        canonical_column="property_type",
        synonyms=("rrel16",),
    ),
    Annex2Field(
        code="RREL17",
        field_name="energy_performance_certificate",
        description="Energy Performance Certificate (EPC) rating of the property.",
        canonical_column="epc_label",
        synonyms=("epc", "epc_rating", "energy_performance_certificate_value", "rrel17"),
    ),
    # --- Loan economics ---
    Annex2Field(
        code="RREL18",
        field_name="current_balance",
        description="Current outstanding principal balance of the loan (EUR).",
        canonical_column="current_balance",
        synonyms=(
            "current_principal_balance",
            "outstanding_balance",
            "current_balance_eur",
            "rrel18",
        ),
    ),
    Annex2Field(
        code="RREL22",
        field_name="current_interest_rate",
        description="Current interest rate / coupon of the loan (%).",
        canonical_column="current_interest_rate_pct",
        synonyms=(
            "current_interest_rate",
            "coupon",
            "interest_rate_pct",
            "rrel22",
        ),
    ),
    Annex2Field(
        code="RREL24",
        field_name="interest_rate_type",
        description="Interest-rate type (Fixed / Floating).",
        canonical_column="rate_type",
        synonyms=("interest_rate_type", "rrel24"),
    ),
    Annex2Field(
        code="RREL30",
        field_name="remaining_term",
        description="Remaining contractual term to maturity (months).",
        canonical_column="remaining_term_months",
        synonyms=("remaining_term", "remaining_maturity_months", "rrel30"),
    ),
    Annex2Field(
        code="RREL31",
        field_name="seasoning",
        description="Seasoning — months elapsed since loan origination.",
        canonical_column="seasoning_months",
        synonyms=("seasoning", "loan_age_months", "rrel31"),
    ),
    # --- LTV / valuation ---
    Annex2Field(
        code="RREL40",
        field_name="current_loan_to_value",
        description="Current loan-to-value ratio (current balance / current valuation, %).",
        canonical_column="cltomv_current",
        synonyms=(
            "current_ltv",
            "current_loan_to_value",
            "cltv",
            "current_ltv_pct",
            "rrel40",
        ),
    ),
    # --- Arrears / performance / default ---
    Annex2Field(
        code="RREL62",
        field_name="arrears_balance",
        description="Current balance of arrears on the loan (EUR).",
        canonical_column="arrears_balance",
        synonyms=("current_arrears_balance", "arrears_amount", "rrel62"),
    ),
    Annex2Field(
        code="RREL63",
        field_name="number_of_days_in_arrears",
        description="Number of days the loan is currently in arrears.",
        canonical_column="days_in_arrears",
        synonyms=("number_of_days_in_arrears", "arrears_days", "rrel63"),
    ),
    Annex2Field(
        code="RREL64",
        field_name="arrears_bucket",
        description="Arrears severity bucket (Performing / <29d / 180+d, etc.).",
        canonical_column="arrears_bucket",
        synonyms=("arrears_status", "delinquency_bucket", "rrel64"),
    ),
    Annex2Field(
        code="RREL66",
        field_name="default_status",
        description="Default / credit-impaired status flag for the loan.",
        canonical_column="default_crr_flag",
        synonyms=("default_flag", "default_status", "credit_impaired_flag", "rrel66"),
    ),
)


# ---------------------------------------------------------------------------
# Derived lookup indices (built once at import)
# ---------------------------------------------------------------------------


def _build_indices() -> (
    tuple[dict[str, Annex2Field], dict[str, Annex2Field], dict[str, Annex2Field]]
):
    """Build the code / field-name / column lookup indices.

    The column index maps both each record's ``canonical_column`` and every
    ``synonym`` onto that record, all lower-cased, so issuer/vintage column-name
    drift resolves onto one canonical field. A synonym that collides with an
    existing canonical column never shadows it (canonical columns are indexed
    last and win).
    """
    by_code: dict[str, Annex2Field] = {}
    by_field: dict[str, Annex2Field] = {}
    by_column: dict[str, Annex2Field] = {}
    for rec in ANNEX2_RMBS_FIELDS:
        by_code[rec.code.lower()] = rec
        by_field[rec.field_name.lower()] = rec
        for syn in rec.synonyms:
            # Don't let a synonym shadow a canonical column added in another row.
            by_column.setdefault(syn.lower(), rec)
    # Index canonical columns last so they always win over a synonym collision.
    for rec in ANNEX2_RMBS_FIELDS:
        by_column[rec.canonical_column.lower()] = rec
    return by_code, by_field, by_column


_BY_CODE, _BY_FIELD, _BY_COLUMN = _build_indices()


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------


def field_for_code(code: str) -> Annex2Field | None:
    """Return the :class:`Annex2Field` for an RREL code, or ``None`` if unknown.

    Matched case-insensitively (``"rrel18"`` and ``"RREL18"`` both resolve).
    """
    return _BY_CODE.get(code.strip().lower())


def field_for_name(field_name: str) -> Annex2Field | None:
    """Return the :class:`Annex2Field` for a semantic field name, or ``None``."""
    return _BY_FIELD.get(field_name.strip().lower())


def code_for_column(column: str) -> str | None:
    """Return the RREL code for a tape column name, or ``None`` if unmapped.

    The column is matched (case-insensitively) against every record's
    ``canonical_column`` and its ``synonyms``, so an issuer/vintage column-name
    variant resolves onto the same regulatory code as the canonical spelling.
    """
    rec = _BY_COLUMN.get(column.strip().lower())
    return rec.code if rec is not None else None


def canonical_column_for(column: str) -> str | None:
    """Resolve a (possibly issuer-specific) tape column to its canonical column.

    Returns the canonical column name when *column* is a known canonical name or
    a registered synonym; ``None`` when the column is not in the Annex 2 table.
    A column already in canonical form resolves to itself.
    """
    rec = _BY_COLUMN.get(column.strip().lower())
    return rec.canonical_column if rec is not None else None


def locator_for(field_name: str) -> str | None:
    """Return the citation *locator* string for a semantic field, or ``None``.

    The locator is the ``"<RREL code> · <description>"`` string that belongs in
    :class:`loanwhiz.primitives.base.Citation`'s ``page_or_row`` so a tape-sourced
    value (a ``RiskSignals`` field, a covenant metric) is traceable to the
    regulatory Annex 2 field it came from. ``None`` when the field is not mapped.
    """
    rec = _BY_FIELD.get(field_name.strip().lower())
    if rec is None:
        return None
    return f"{rec.code} · {rec.description}"
