"""Tests for the ESMA RTS Annex 2 (RMBS) field-code mapping table."""

from __future__ import annotations

# Import a ``primitives`` module before ``loanwhiz.domain`` so the package-init
# graph resolves in the order that avoids a *pre-existing* circular import
# (``domain.__init__`` → ``inputs`` → ``provenance`` → ``primitives.base`` →
# ``primitives.__init__`` → … → ``domain.inputs``). Production code always loads
# ``primitives`` first, and the full test suite does too via earlier-collected
# modules; this import makes THIS module robust when run in isolation
# (``pytest tests/test_esma_annex2.py``) without depending on collection order.
# (The same latent cycle affects the existing ``tests/test_domain_schema.py``.)
import loanwhiz.primitives.base  # noqa: F401  (import-order guard, see above)
import pytest

from loanwhiz.domain.esma_annex2 import (
    ANNEX2_RMBS_FIELDS,
    Annex2Field,
    canonical_column_for,
    code_for_column,
    field_for_code,
    field_for_name,
    locator_for,
)


def test_table_is_non_empty_and_well_formed() -> None:
    assert len(ANNEX2_RMBS_FIELDS) > 0
    for rec in ANNEX2_RMBS_FIELDS:
        assert isinstance(rec, Annex2Field)
        assert rec.code.upper().startswith("RREL")
        assert rec.field_name
        assert rec.description
        assert rec.canonical_column


def test_codes_are_unique() -> None:
    codes = [r.code.lower() for r in ANNEX2_RMBS_FIELDS]
    assert len(codes) == len(set(codes)), "duplicate RREL code in the table"


def test_field_names_are_unique() -> None:
    names = [r.field_name.lower() for r in ANNEX2_RMBS_FIELDS]
    assert len(names) == len(set(names)), "duplicate field_name in the table"


def test_load_bearing_columns_are_mapped() -> None:
    # Every column the normaliser consumes must resolve to an RREL code.
    for col in (
        "current_balance",
        "current_interest_rate_pct",
        "remaining_term_months",
        "seasoning_months",
        "cltomv_current",
        "arrears_bucket",
        "default_crr_flag",
        "epc_label",
        "rate_type",
        "property_type",
        "province",
        "transaction_name",
        "reporting_date",
    ):
        assert code_for_column(col) is not None, f"{col} not mapped to a RREL code"


def test_b7_signal_fields_are_mapped() -> None:
    # The tape-native covenant (B7) signals: arrears severity, LTV, default.
    assert code_for_column("cltomv_current") is not None
    assert code_for_column("arrears_bucket") is not None
    assert code_for_column("default_crr_flag") is not None


def test_field_for_code_roundtrips() -> None:
    rec = field_for_code("RREL18")
    assert rec is not None
    assert rec.field_name == "current_balance"
    assert rec.canonical_column == "current_balance"


def test_field_for_code_is_case_insensitive() -> None:
    assert field_for_code("rrel18") is field_for_code("RREL18")


def test_field_for_code_unknown_returns_none() -> None:
    assert field_for_code("RREL9999") is None
    assert field_for_code("not-a-code") is None


def test_field_for_name_roundtrips() -> None:
    rec = field_for_name("current_loan_to_value")
    assert rec is not None
    assert rec.code == "RREL40"
    assert rec.canonical_column == "cltomv_current"


def test_code_for_column_resolves_synonyms() -> None:
    # An issuer-specific column-name variant resolves to the same RREL code.
    canonical_code = code_for_column("current_balance")
    assert code_for_column("outstanding_balance") == canonical_code
    assert code_for_column("current_principal_balance") == canonical_code
    # LTV synonyms
    ltv_code = code_for_column("cltomv_current")
    assert code_for_column("current_ltv") == ltv_code
    assert code_for_column("cltv") == ltv_code


def test_code_for_column_is_case_insensitive() -> None:
    assert code_for_column("CURRENT_BALANCE") == code_for_column("current_balance")


def test_code_for_column_unknown_returns_none() -> None:
    assert code_for_column("totally_made_up_column") is None


def test_canonical_column_for_resolves_synonyms_to_canonical() -> None:
    assert canonical_column_for("outstanding_balance") == "current_balance"
    assert canonical_column_for("current_ltv") == "cltomv_current"
    # canonical name resolves to itself
    assert canonical_column_for("current_balance") == "current_balance"
    assert canonical_column_for("unknown_col") is None


def test_canonical_column_never_shadowed_by_a_synonym() -> None:
    # Every canonical column must resolve to its own record, even if some other
    # record lists a colliding synonym.
    for rec in ANNEX2_RMBS_FIELDS:
        resolved = canonical_column_for(rec.canonical_column)
        assert resolved == rec.canonical_column


def test_locator_for_returns_rrel_anchored_string() -> None:
    loc = locator_for("current_loan_to_value")
    assert loc is not None
    assert loc.startswith("RREL40")
    assert "loan-to-value" in loc.lower()


def test_locator_for_unknown_returns_none() -> None:
    assert locator_for("not_a_field") is None


@pytest.mark.parametrize(
    "field_name", [r.field_name for r in ANNEX2_RMBS_FIELDS]
)
def test_every_field_has_a_locator(field_name: str) -> None:
    loc = locator_for(field_name)
    assert loc is not None
    assert "RREL" in loc.upper()
