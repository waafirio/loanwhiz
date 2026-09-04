"""ESMA RTS Annex 5 (Automobile) field-code mapping table.

Annex V of Commission Delegated Regulation (EU) 2020/1224 is the **automobile**
underlying-exposures template, field codes ``AUTL1``–``AUTL84``. Unlike Annexes
II, III, IV, IX and X it is genuinely single-section — there is no collateral
sub-table and no second code prefix.

Recital (4) of the RTS puts both automobile *loans* and automobile *leases* in
this annex: a pool of automobile exposures uses this template "regardless of
whether the underlying automobile underlying exposures are loans or leases", and
a pool made up entirely of automobile leases uses it rather than the leasing
template (Annex VIII). ``AUTL23`` Product Type is the closest disclosed
discriminator, though its enum and definition are lease-flavoured, so it
identifies the lease product form rather than acting as a clean loan/lease
boolean.

The ``vehicle_type`` sentinel has no regulatory anchor
------------------------------------------------------
The tape normaliser detects this annex on a ``vehicle_type`` column. **Annex V
defines no such field** — there is no vehicle type, vehicle category, or
fuel/powertrain field anywhere in it (``AUTL57`` is an energy-performance letter
grade, an efficiency rating, not a powertrain classification). ``vehicle_type``
is therefore registered below as an **extension field** with ``code=None``: the
column resolves, but it yields no locator, so a value sourced from it is visibly
un-anchored rather than carrying a fabricated ``AUTL`` code.

Kept as-is because it is what real tapes carry and what detection has always
used. If Annex VIII (leasing) is added later, note that ``Manufacturer``,
``Model``, ``New Or Used``, ``Product Type``, ``Securitised Residual Value`` and
``Option To Buy Price`` all exist in *both* annexes under different codes, so
none of those names discriminates automobile from leasing — ``AUTL55`` Year Of
Registration is the only field name unique to Annex V.

What Annex V does **not** define
--------------------------------
- **No Interest Rate Type field** — Annexes II (``RREL24``), III and IV
  (``CRPL52``) each have one; Annex V and Annex VIII do not. Downstream
  rate-type breakdowns therefore degrade to absent on an auto tape rather than
  resolving onto a borrowed code.
- No credit rating / score / PD / LGD, no NACE code, no enterprise size and no
  Basel III segment. ``AUTL14`` Obligor Legal Type is the natural-person versus
  legal-entity discriminator.
"""

from __future__ import annotations

from loanwhiz.domain.esma_annex_registry import AnnexField, AnnexSpec, register_annex

__all__ = ["ANNEX5_AUTO", "ANNEX5_AUTO_FIELDS"]


# ---------------------------------------------------------------------------
# The canonical Annex 5 (Automobile) table
# ---------------------------------------------------------------------------
#
# The load-bearing slice of the AUTL template, not all 84 fields. Canonical
# columns are shared with the other annexes wherever the concept is the same
# (``current_balance``, ``reporting_date``, ``province``, ``arrears_balance``,
# ``epc_label``), so pool analytics keyed on those names work across asset
# classes without special-casing.

ANNEX5_AUTO_FIELDS: tuple[AnnexField, ...] = (
    # --- Identifiers & deal-level ---
    AnnexField(
        code="AUTL1",
        field_name="unique_identifier",
        description="Unique identifier for the securitisation (RTS Art. 11(1)).",
        canonical_column="unique_identifier",
        synonyms=("autl1",),
    ),
    AnnexField(
        code="AUTL2",
        field_name="loan_identifier",
        description="Original underlying exposure identifier.",
        canonical_column="loan_identifier",
        synonyms=("loan_id", "underlying_exposure_identifier", "autl2"),
    ),
    AnnexField(
        code="AUTL4",
        field_name="obligor_identifier",
        description="Original obligor identifier.",
        canonical_column="obligor_identifier",
        synonyms=("obligor_id", "borrower_id", "autl4"),
    ),
    AnnexField(
        code="AUTL6",
        field_name="reporting_date",
        description="Data cut-off / reporting reference date for the tape.",
        canonical_column="reporting_date",
        synonyms=("data_cut_off_date", "pool_cut_off_date", "autl6"),
    ),
    # --- Obligor attributes ---
    AnnexField(
        code="AUTL10",
        field_name="geographic_region",
        description="Geographic region (NUTS-3) of the obligor.",
        canonical_column="province",
        synonyms=("geographic_region", "region", "nuts3", "autl10"),
    ),
    AnnexField(
        code="AUTL13",
        field_name="credit_impaired_obligor",
        description="Credit-impaired obligor flag (Securitisation Reg. Art. 20(11)).",
        canonical_column="credit_impaired_flag",
        synonyms=("credit_impaired_obligor", "autl13"),
    ),
    AnnexField(
        code="AUTL14",
        field_name="obligor_legal_type",
        description=(
            "Obligor legal type (PUBL / LLCO / PNTR / INDV / GOVT / OTHR) — the "
            "natural-person versus legal-entity discriminator."
        ),
        canonical_column="obligor_legal_type",
        synonyms=("autl14",),
    ),
    # --- Facility characteristics ---
    AnnexField(
        code="AUTL23",
        field_name="product_type",
        description=(
            "Lease/finance product form (PPUR / PHIR / HIRP / LEAP / FNLS / "
            "OPLS / OTHR). Lease-flavoured; not a clean loan-versus-lease flag."
        ),
        canonical_column="product_type",
        synonyms=("autl23",),
    ),
    AnnexField(
        code="AUTL24",
        field_name="origination_date",
        description="Origination date of the underlying exposure.",
        canonical_column="origination_date",
        synonyms=("autl24",),
    ),
    AnnexField(
        code="AUTL25",
        field_name="maturity_date",
        description="Maturity of the underlying exposure, or expiry of the lease.",
        canonical_column="maturity_date",
        synonyms=("autl25",),
    ),
    AnnexField(
        code="AUTL26",
        field_name="original_term",
        description="Original contractual term of the exposure (months).",
        canonical_column="original_term_months",
        synonyms=("original_term", "autl26"),
    ),
    # --- Balances & pricing ---
    AnnexField(
        code="AUTL28",
        field_name="currency_denomination",
        description="Currency the underlying exposure is denominated in.",
        canonical_column="currency",
        synonyms=("currency_denomination", "autl28"),
    ),
    AnnexField(
        code="AUTL29",
        field_name="original_principal_balance",
        description=(
            "Principal balance (or discounted lease balance, inclusive of "
            "capitalised fees) at origination."
        ),
        canonical_column="original_balance",
        synonyms=("original_principal_balance", "autl29"),
    ),
    AnnexField(
        code="AUTL30",
        field_name="current_principal_balance",
        description="Current outstanding principal balance of the exposure.",
        canonical_column="current_balance",
        synonyms=(
            "current_principal_balance",
            "outstanding_balance",
            "current_balance_eur",
            "autl30",
        ),
    ),
    AnnexField(
        code="AUTL38",
        field_name="balloon_amount",
        description="Balloon amount payable at the end of the exposure.",
        canonical_column="balloon_amount",
        synonyms=("autl38",),
    ),
    AnnexField(
        code="AUTL39",
        field_name="down_payment_amount",
        description=(
            "Down payment at origination, including the value of traded-in "
            "vehicles."
        ),
        canonical_column="down_payment_amount",
        synonyms=("autl39",),
    ),
    # --- Interest (note: Annex V has no interest-rate-type field) ---
    AnnexField(
        code="AUTL40",
        field_name="current_interest_rate",
        description="Total gross current interest or discount rate applicable (%).",
        canonical_column="current_interest_rate_pct",
        synonyms=("current_interest_rate", "coupon", "interest_rate_pct", "autl40"),
    ),
    AnnexField(
        code="AUTL41",
        field_name="current_interest_rate_index",
        description="Reference index the current interest rate floats over.",
        canonical_column="interest_rate_index",
        synonyms=("current_interest_rate_index", "base_rate", "autl41"),
    ),
    AnnexField(
        code="AUTL43",
        field_name="current_interest_rate_margin",
        description=(
            "Margin over (or under, if negative) the reference index for a "
            "floating-rate exposure."
        ),
        canonical_column="current_interest_rate_margin",
        synonyms=("margin", "spread", "interest_rate_margin", "autl43"),
    ),
    # --- Vehicle attributes ---
    AnnexField(
        code=None,
        field_name="vehicle_type",
        description=(
            "Issuer-supplied vehicle type. NOT an ESMA RTS Annex V field — the "
            "automobile template defines no vehicle-type, category or fuel "
            "field. Retained because real tapes carry it and annex detection "
            "keys on it; it resolves but yields no regulatory locator."
        ),
        canonical_column="vehicle_type",
        synonyms=("vehicle_category",),
    ),
    AnnexField(
        code="AUTL53",
        field_name="manufacturer",
        description="Brand name of the vehicle manufacturer.",
        canonical_column="manufacturer",
        synonyms=("autl53",),
    ),
    AnnexField(
        code="AUTL54",
        field_name="model",
        description="Name of the vehicle model.",
        canonical_column="model",
        synonyms=("autl54",),
    ),
    AnnexField(
        code="AUTL55",
        field_name="year_of_registration",
        description=(
            "Year the vehicle was registered. The only Annex V field name unique "
            "to this annex — the RTS-anchored discriminator against Annex VIII."
        ),
        canonical_column="year_of_registration",
        synonyms=("autl55",),
    ),
    AnnexField(
        code="AUTL56",
        field_name="new_or_used",
        description="Whether the vehicle is new, used, a demo, or other.",
        canonical_column="new_or_used",
        synonyms=("autl56",),
    ),
    AnnexField(
        code="AUTL57",
        field_name="energy_performance_certificate",
        description=(
            "Energy Performance Certificate value (A–G). An efficiency grade, "
            "not a fuel or powertrain classification."
        ),
        canonical_column="epc_label",
        synonyms=("epc", "epc_rating", "autl57"),
    ),
    AnnexField(
        code="AUTL59",
        field_name="original_loan_to_value",
        description=(
            "Exposure balance at origination relative to the vehicle value at "
            "origination (%)."
        ),
        canonical_column="original_ltv",
        synonyms=("original_loan_to_value", "autl59"),
    ),
    AnnexField(
        code="AUTL60",
        field_name="original_valuation_amount",
        description=(
            "List price of the vehicle at origination; for a non-new vehicle, "
            "its trade or sale value."
        ),
        canonical_column="original_valuation_amount",
        synonyms=("autl60",),
    ),
    # --- Residual value ---
    AnnexField(
        code="AUTL61",
        field_name="original_residual_value",
        description="Estimated residual value of the vehicle at lease origination.",
        canonical_column="original_residual_value",
        synonyms=("autl61",),
    ),
    AnnexField(
        code="AUTL63",
        field_name="securitised_residual_value",
        description="The residual value amount that has been securitised.",
        canonical_column="securitised_residual_value",
        synonyms=("autl63",),
    ),
    # --- Arrears / performance / default ---
    AnnexField(
        code="AUTL68",
        field_name="arrears_balance",
        description="Current balance of arrears on the exposure.",
        canonical_column="arrears_balance",
        synonyms=("current_arrears_balance", "arrears_amount", "autl68"),
    ),
    AnnexField(
        code="AUTL69",
        field_name="number_of_days_in_arrears",
        description="Number of days the exposure is currently in arrears.",
        canonical_column="days_in_arrears",
        synonyms=("number_of_days_in_arrears", "arrears_days", "autl69"),
    ),
    AnnexField(
        code="AUTL70",
        field_name="account_status",
        description=(
            "Account status (PERF performing / DFLT defaulted / RESS restructured "
            "/ … ). The automobile template's performance state."
        ),
        canonical_column="account_status",
        synonyms=("autl70",),
    ),
    AnnexField(
        code="AUTL71",
        field_name="reason_for_default_or_foreclosure",
        description=(
            "Reason for default or foreclosure (UPXX / PDXX / UPPD), per CRR "
            "Art. 178."
        ),
        canonical_column="default_reason",
        synonyms=("reason_for_default", "autl71"),
    ),
    AnnexField(
        code="AUTL72",
        field_name="default_amount",
        description="Gross default amount before recoveries and sale proceeds.",
        canonical_column="default_amount",
        synonyms=("autl72",),
    ),
    AnnexField(
        code="AUTL73",
        field_name="default_date",
        description="Date of default on the underlying exposure.",
        canonical_column="default_date",
        synonyms=("autl73",),
    ),
    AnnexField(
        code="AUTL76",
        field_name="cumulative_recoveries",
        description="Cumulative recoveries on the exposure, net of costs.",
        canonical_column="cumulative_recoveries",
        synonyms=("autl76",),
    ),
)


# ---------------------------------------------------------------------------
# The registered annex specification
# ---------------------------------------------------------------------------

#: Annex 5 (Automobile) — automobile loans and leases, ``AUTL`` codes, single
#: section. Detected on ``vehicle_type``, which is an extension column rather
#: than an RTS field (see the module docstring).
ANNEX5_AUTO: AnnexSpec = register_annex(
    AnnexSpec(
        annex_id="annex_5",
        label="Annex 5 (Auto)",
        asset_class="Auto",
        code_prefixes=("AUTL",),
        signature_columns=frozenset({"vehicle_type"}),
        fields=ANNEX5_AUTO_FIELDS,
    )
)
