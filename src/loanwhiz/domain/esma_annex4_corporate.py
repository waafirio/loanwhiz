"""ESMA RTS Annex 4 (Corporate) field-code mapping table.

Annex IV of Commission Delegated Regulation (EU) 2020/1224 is the **corporate**
underlying-exposures template, field codes ``CRPL1``–``CRPL101`` plus a
collateral-level section ``CRPC1``–``CRPC20``. It is the template a CLO's
collateral — broadly syndicated and leveraged loans to corporate obligors —
is disclosed under.

Why this annex, and not "Annex 8 (SME)"
---------------------------------------
The tape normaliser previously labelled the ``company_size`` signature
``"Annex 8 (SME)"``. That was wrong on both counts, and the correction is the
substance of this module:

- **Annex VIII is leasing** (``LESL``), not SME.
- **There is no standalone SME annex.** Art. 2(1)(c) of 2020/1224 assigns
  "Annex IV for corporate underlying exposures, *including underlying exposures
  to micro, small- and medium-sized enterprises*". Recital (6) notes the SME
  terminology derives from Commission Recommendation 2003/361/EC "in order to
  provide continuity with existing templates" — i.e. the older standalone SME
  template was folded into corporate, with enterprise size expressed as a
  *field* (``CRPL16``) rather than as its own annex.

So a tape carrying a company-size column is a **corporate** tape, and the field
that column actually corresponds to is ``CRPL16`` Enterprise Size.

CLO-relevant fields
-------------------
Annex IV carries the disclosure a CLO collateral report leans on: ``CRPL29``
Leveraged Transaction (per the ECB leveraged-transactions guidance), ``CRPL30``
"Managed by CLO", ``CRPL31`` Payment in Kind, ``CRPL27`` Debt Instrument
Seniority, and ``CRPC8`` Lien in the collateral section.

What Annex IV does **not** define
---------------------------------
Two fields a CLO analyst reaches for are simply absent from the template, and
this module does not invent codes for them:

- **No obligor or facility credit rating** — no rating, score, PD or LGD field
  exists anywhere in Annex IV. ``CRPL15`` Obligor Basel III Segment and the
  financials block (``CRPL17``–``CRPL21``) are the nearest disclosed proxies.
- **No covenant-lite flag** — the string "covenant" does not occur in the annex.

Both, and the other columns a real corporate tape carries that Annex IV does not
define, are modelled as **extension fields** (``AnnexField.code=None``) at the
end of the table below: they resolve their column so the value is usable, and
yield **no locator** so provenance is visibly absent rather than fabricated.
Never give one a code to make a citation look complete.

CLO *structural* disclosure (concentration limits, PIK restrictions,
reinvestment, CLO-manager data) lives in Annex XIV sections ``SESC``/``SESL``,
not in this loan-level template.
"""

from __future__ import annotations

from loanwhiz.domain.esma_annex_registry import AnnexField, AnnexSpec, register_annex

__all__ = ["ANNEX4_CORPORATE", "ANNEX4_CORPORATE_FIELDS"]


# ---------------------------------------------------------------------------
# The canonical Annex 4 (Corporate) table
# ---------------------------------------------------------------------------
#
# Codes are the ESMA RTS Annex IV corporate (CRPL) loan-level template, plus the
# collateral-level (CRPC) section where a concept only exists there. The subset
# below is the load-bearing slice for CLO collateral analytics, not the full
# ~120-field template. Canonical columns are shared with Annex 2 wherever the
# concept is genuinely the same (``current_balance``, ``reporting_date``,
# ``province``, ``arrears_balance``), so downstream pool analytics keyed on those
# names work across asset classes without special-casing.

ANNEX4_CORPORATE_FIELDS: tuple[AnnexField, ...] = (
    # --- Identifiers & deal-level ---
    AnnexField(
        code="CRPL1",
        field_name="unique_identifier",
        description="Unique identifier for the securitisation (RTS Art. 11(1)).",
        canonical_column="unique_identifier",
        synonyms=("crpl1",),
    ),
    AnnexField(
        code="CRPL2",
        field_name="loan_identifier",
        description="Original underlying exposure identifier.",
        canonical_column="loan_identifier",
        synonyms=("loan_id", "underlying_exposure_identifier", "facility_id", "crpl2"),
    ),
    AnnexField(
        code="CRPL4",
        field_name="obligor_identifier",
        description="Original obligor identifier — the borrowing corporate entity.",
        canonical_column="obligor_identifier",
        synonyms=("obligor_id", "borrower_id", "borrower_identifier", "crpl4"),
    ),
    AnnexField(
        code="CRPL6",
        field_name="reporting_date",
        description="Data cut-off / reporting reference date for the tape.",
        canonical_column="reporting_date",
        synonyms=("data_cut_off_date", "pool_cut_off_date", "crpl6"),
    ),
    # --- Obligor attributes ---
    AnnexField(
        code="CRPL10",
        field_name="geographic_region",
        description="Geographic region (NUTS-3) of the obligor.",
        canonical_column="province",
        synonyms=("geographic_region", "region", "nuts3", "crpl10"),
    ),
    AnnexField(
        code="CRPL12",
        field_name="credit_impaired_obligor",
        description="Credit-impaired obligor flag (Securitisation Reg. Art. 20(11)).",
        canonical_column="credit_impaired_flag",
        synonyms=("credit_impaired_obligor", "crpl12",),
    ),
    AnnexField(
        code="CRPL14",
        field_name="nace_industry_code",
        description="Obligor industry NACE code (Regulation (EC) No 1893/2006).",
        canonical_column="industry_code",
        synonyms=("nace", "nace_code", "crpl14"),
    ),
    AnnexField(
        code="CRPL15",
        field_name="obligor_basel_iii_segment",
        description="Obligor Basel III segment (CORP / SMEX / RETL / OTHR).",
        canonical_column="basel_segment",
        synonyms=("obligor_basel_iii_segment", "basel_iii_segment", "crpl15"),
    ),
    AnnexField(
        code="CRPL16",
        field_name="enterprise_size",
        description=(
            "Enterprise size per Commission Recommendation 2003/361/EC "
            "(MICE / SMAE / MEDE / LARE / NATP / OTHR)."
        ),
        canonical_column="enterprise_size",
        synonyms=("company_size", "obligor_size", "sme_flag", "crpl16"),
    ),
    # --- Facility characteristics ---
    AnnexField(
        code="CRPL24",
        field_name="debt_type",
        description="Type of debt instrument (loan, guarantee, revolver, etc.).",
        canonical_column="debt_type",
        synonyms=("crpl24",),
    ),
    AnnexField(
        code="CRPL27",
        field_name="debt_instrument_seniority",
        description=(
            "Debt instrument seniority (SNDB senior / MZZD mezzanine / "
            "JUND junior / SBOD subordinated / OTHR)."
        ),
        canonical_column="seniority",
        synonyms=("debt_instrument_seniority", "lien_seniority", "crpl27"),
    ),
    AnnexField(
        code="CRPL28",
        field_name="syndicated",
        description="Whether the underlying exposure is syndicated.",
        canonical_column="syndicated_flag",
        synonyms=("syndicated", "crpl28"),
    ),
    AnnexField(
        code="CRPL29",
        field_name="leveraged_transaction",
        description=(
            "Whether the underlying exposure is a leveraged transaction "
            "(ECB leveraged-transactions guidance)."
        ),
        canonical_column="leveraged_transaction_flag",
        synonyms=("leveraged_transaction", "leveraged_loan_flag", "crpl29"),
    ),
    AnnexField(
        code="CRPL30",
        field_name="managed_by_clo",
        description="Whether the underlying exposure is managed by the CLO manager.",
        canonical_column="managed_by_clo",
        synonyms=("clo_managed", "crpl30"),
    ),
    AnnexField(
        code="CRPL31",
        field_name="payment_in_kind",
        description=(
            "Whether the exposure currently pays in kind (interest capitalised "
            "into principal)."
        ),
        canonical_column="pik_flag",
        synonyms=("payment_in_kind", "pik", "crpl31"),
    ),
    AnnexField(
        code="CRPL34",
        field_name="maturity_date",
        description="Maturity date of the underlying exposure.",
        canonical_column="maturity_date",
        synonyms=("crpl34",),
    ),
    # --- Balances & pricing ---
    AnnexField(
        code="CRPL37",
        field_name="currency_denomination",
        description="Currency the underlying exposure is denominated in.",
        canonical_column="currency",
        synonyms=("currency_denomination", "crpl37"),
    ),
    AnnexField(
        code="CRPL38",
        field_name="original_principal_balance",
        description="Original principal balance at origination.",
        canonical_column="original_balance",
        synonyms=("original_principal_balance", "crpl38"),
    ),
    AnnexField(
        code="CRPL39",
        field_name="current_principal_balance",
        description="Current outstanding principal balance of the exposure.",
        canonical_column="current_balance",
        synonyms=(
            "current_principal_balance",
            "outstanding_balance",
            "par_balance",
            "crpl39",
        ),
    ),
    AnnexField(
        code="CRPL41",
        field_name="market_value",
        description=(
            "Market value of the security — for CLO securitisations, the market "
            "value of the underlying exposure."
        ),
        canonical_column="market_value",
        synonyms=("crpl41",),
    ),
    AnnexField(
        code="CRPL43",
        field_name="purchase_price",
        description="Purchase price of the exposure relative to par.",
        canonical_column="purchase_price",
        synonyms=("crpl43",),
    ),
    # --- Interest ---
    AnnexField(
        code="CRPL52",
        field_name="interest_rate_type",
        description="Interest-rate type (fixed / floating / index-linked, etc.).",
        canonical_column="rate_type",
        synonyms=("interest_rate_type", "crpl52"),
    ),
    AnnexField(
        code="CRPL53",
        field_name="current_interest_rate",
        description="Total gross current interest rate applicable to the exposure (%).",
        canonical_column="current_interest_rate_pct",
        synonyms=("current_interest_rate", "coupon", "interest_rate_pct", "crpl53"),
    ),
    AnnexField(
        code="CRPL54",
        field_name="current_interest_rate_index",
        description="Reference index the current interest rate floats over.",
        canonical_column="interest_rate_index",
        synonyms=("current_interest_rate_index", "base_rate", "crpl54"),
    ),
    AnnexField(
        code="CRPL56",
        field_name="current_interest_rate_margin",
        description=(
            "Margin over (or under, if negative) the reference index for a "
            "floating-rate exposure."
        ),
        canonical_column="current_interest_rate_margin",
        synonyms=("margin", "spread", "interest_rate_margin", "crpl56"),
    ),
    # --- Arrears / performance / default ---
    AnnexField(
        code="CRPL77",
        field_name="arrears_balance",
        description="Current balance of arrears on the exposure.",
        canonical_column="arrears_balance",
        synonyms=("current_arrears_balance", "arrears_amount", "crpl77"),
    ),
    AnnexField(
        code="CRPL78",
        field_name="number_of_days_in_arrears",
        description="Number of days the exposure is currently in arrears.",
        canonical_column="days_in_arrears",
        synonyms=("number_of_days_in_arrears", "arrears_days", "crpl78"),
    ),
    AnnexField(
        code="CRPL79",
        field_name="account_status",
        description=(
            "Account status (PERF performing / DFLT defaulted / RESS restructured "
            "/ … ). The corporate template's performance state; there is no "
            "separate default flag."
        ),
        canonical_column="account_status",
        synonyms=("crpl79",),
    ),
    AnnexField(
        code="CRPL80",
        field_name="reason_for_default_or_foreclosure",
        description=(
            "Reason for default or foreclosure (UPXX unlikely-to-pay / PDXX past "
            "due / UPPD both), per CRR Art. 178."
        ),
        canonical_column="default_reason",
        synonyms=("reason_for_default", "crpl80"),
    ),
    AnnexField(
        code="CRPL81",
        field_name="default_amount",
        description="Gross default amount before recoveries and sale proceeds.",
        canonical_column="default_amount",
        synonyms=("crpl81",),
    ),
    AnnexField(
        code="CRPL82",
        field_name="default_date",
        description="Date of default on the underlying exposure.",
        canonical_column="default_date",
        synonyms=("crpl82",),
    ),
    AnnexField(
        code="CRPL84",
        field_name="cumulative_recoveries",
        description="Cumulative recoveries on the exposure, net of costs.",
        canonical_column="cumulative_recoveries",
        synonyms=("crpl84",),
    ),
    # --- Collateral-level section (CRPC) ---
    AnnexField(
        code="CRPC8",
        field_name="lien",
        description=(
            "Highest lien position held by the originator in relation to the "
            "collateral."
        ),
        canonical_column="lien_position",
        synonyms=("lien", "crpc8"),
    ),
    AnnexField(
        code="CRPC9",
        field_name="collateral_type",
        description="Type of collateral securing the exposure.",
        canonical_column="collateral_type",
        synonyms=("crpc9",),
    ),
    # --- Extension fields (``code=None``) ------------------------------------
    #
    # Real corporate tapes — a trustee report's collateral schedule especially —
    # carry columns Annex IV simply does not define. Each resolves its column and
    # yields **no locator**, so the value is usable while its provenance is
    # visibly absent rather than fabricated (the ``vehicle_type`` precedent,
    # #451). Never give one of these a code to make a citation look complete.
    AnnexField(
        code=None,
        field_name="obligor_name",
        description=(
            "Obligor's name as the source states it. Distinct from ``CRPL4``, "
            "which is an obligor *identifier*: a name is a different datum, and "
            "the RTS identifier is not the obligor's real name."
        ),
        canonical_column="obligor_name",
        synonyms=("issuer_name", "borrower_name"),
    ),
    AnnexField(
        code=None,
        field_name="facility_name",
        description=(
            "Facility's name as the source states it. Annex IV names no facility "
            "description field; ``CRPL2`` is an identifier, not a name."
        ),
        canonical_column="facility_name",
        synonyms=("facility_description",),
    ),
    AnnexField(
        code=None,
        field_name="industry_classification",
        description=(
            "Obligor industry under a classification the source does not state. "
            "NOT ``CRPL14``, which is specifically a NACE code (Reg. (EC) No "
            "1893/2006) — resolving a free-text industry there would assert a "
            "NACE conformance the source does not carry."
        ),
        canonical_column="industry_classification",
        synonyms=("industry",),
    ),
    AnnexField(
        code=None,
        field_name="sp_industry",
        description=(
            "S&P industry classification. A different scheme from NACE, so it is "
            "not ``CRPL14`` however closely the concepts read."
        ),
        canonical_column="sp_industry",
        synonyms=("s&p_industry", "standard_and_poors_industry"),
    ),
    AnnexField(
        code=None,
        field_name="fitch_industry",
        description=(
            "Fitch industry classification. A different scheme from NACE, so it "
            "is not ``CRPL14``."
        ),
        canonical_column="fitch_industry",
        synonyms=(),
    ),
    AnnexField(
        code=None,
        field_name="obligor_country",
        description=(
            "Obligor's country. NOT ``CRPL10``, which is a NUTS-3 *sub-national* "
            "region: a country is a coarser geography, and mapping one onto the "
            "other asserts a precision the source does not have."
        ),
        canonical_column="obligor_country",
        synonyms=("country", "country_of_incorporation"),
    ),
    AnnexField(
        code=None,
        field_name="market_price_pct",
        description=(
            "Market price as a percentage of par (e.g. ``99.72``). NOT "
            "``CRPL41``, which is a market *value* — an amount in the exposure's "
            "currency. The value is derivable as ``current_balance x price / "
            "100``, but the source states the price, not the amount."
        ),
        canonical_column="market_price_pct",
        synonyms=("market_price", "price"),
    ),
    AnnexField(
        code=None,
        field_name="index_floor",
        description=(
            "Floor applied to the reference index for a floating-rate exposure. "
            "Annex IV defines no index-floor field."
        ),
        canonical_column="index_floor",
        synonyms=("floor",),
    ),
    AnnexField(
        code=None,
        field_name="sp_rating",
        description=(
            "S&P credit rating of the exposure. Annex IV defines no rating, "
            "score, PD or LGD field anywhere (see the module docstring)."
        ),
        canonical_column="sp_rating",
        synonyms=("s&p_rating",),
    ),
    AnnexField(
        code=None,
        field_name="cov_lite_flag",
        description=(
            "Covenant-lite loan flag. The string \"covenant\" does not occur in "
            "Annex IV; there is no field to carry this."
        ),
        canonical_column="cov_lite_flag",
        synonyms=("cov_lite", "covenant_lite"),
    ),
    AnnexField(
        code=None,
        field_name="dip_flag",
        description="Debtor-in-possession loan flag. Not defined in Annex IV.",
        canonical_column="dip_flag",
        synonyms=("dip",),
    ),
    AnnexField(
        code=None,
        field_name="deferring_flag",
        description=(
            "Deferring-security flag. Distinct from ``CRPL31`` payment-in-kind, "
            "which the source reports separately, and not defined in Annex IV."
        ),
        canonical_column="deferring_flag",
        synonyms=("deferring",),
    ),
    AnnexField(
        code=None,
        field_name="current_pay_flag",
        description="Current-pay obligation flag. Not defined in Annex IV.",
        canonical_column="current_pay_flag",
        synonyms=("current_pay",),
    ),
    AnnexField(
        code=None,
        field_name="revolving_flag",
        description=(
            "Revolving-obligation flag. ``CRPL24`` debt type can carry a "
            "revolver classification, but this is a separate boolean the source "
            "states alongside its own asset type."
        ),
        canonical_column="revolving_flag",
        synonyms=("revolving",),
    ),
    AnnexField(
        code=None,
        field_name="delayed_drawdown_flag",
        description="Delayed-drawdown loan flag. Not defined in Annex IV.",
        canonical_column="delayed_drawdown_flag",
        synonyms=("delayed_drawdown",),
    ),
    AnnexField(
        code=None,
        field_name="bridge_flag",
        description="Bridge-loan flag. Not defined in Annex IV.",
        canonical_column="bridge_flag",
        synonyms=("bridge",),
    ),
)


# ---------------------------------------------------------------------------
# The registered annex specification
# ---------------------------------------------------------------------------

#: Annex 4 (Corporate) — corporate underlying exposures including SMEs and
#: leveraged loans, ``CRPL`` codes with a ``CRPC`` collateral section. Detected
#: by the enterprise-size column, which is ``CRPL16`` — the field the older
#: ``"Annex 8 (SME)"`` label was really pointing at.
ANNEX4_CORPORATE: AnnexSpec = register_annex(
    AnnexSpec(
        annex_id="annex_4",
        label="Annex 4 (Corporate)",
        asset_class="Corporate",
        code_prefixes=("CRPL", "CRPC"),
        signature_columns=frozenset({"enterprise_size"}),
        fields=ANNEX4_CORPORATE_FIELDS,
    )
)
