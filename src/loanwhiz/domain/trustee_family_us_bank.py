"""U.S. Bank's trustee-report layout.

U.S. Bank Global Corporate Trust publishes CLO "Monthly Report" and "Note
Valuation Report" documents whose first page carries ``Global Corporate Trust``
above ``www.usbank.com/clo`` (older deals: ``/cdo``, which is why the signature
matches the host and not the path). Cairn CLO XVII DAC is the deal LoanWhiz
reads through this family, and its committed fixtures are what exercise every
title below.

Every value here was moved verbatim out of
:mod:`loanwhiz.primitives.collateral_schedule_parser` and
:mod:`loanwhiz.primitives.note_valuation_parser`, where it was a module-level
constant. Nothing was retuned in the move: the committed goldens
(``tests/test_trustee_report_goldens.py``) assert that this deal's parse is
byte-identical either side of it.
"""

from __future__ import annotations

from types import MappingProxyType

from loanwhiz.domain.trustee_report_registry import (
    SECTION_ASSET_PART_I,
    SECTION_ASSET_PART_II,
    SECTION_ASSET_PART_III,
    SECTION_CCC,
    SECTION_COUNTRY,
    SECTION_EXEC_SUMMARY,
    SECTION_FITCH_INDUSTRY,
    SECTION_IC_DETAIL,
    SECTION_NV_DISTRIBUTION,
    SECTION_NV_EXECUTIVE,
    SECTION_NV_INTEREST_POP,
    SECTION_NV_PRINCIPAL_POP,
    SECTION_PAR_VALUE_DETAIL,
    SECTION_PROFILE_TESTS,
    SECTION_SP_INDUSTRY,
    SECTION_SP_RATING,
    ColumnOrder,
    DocumentKind,
    DocumentLayout,
    FurnitureOrder,
    TrusteeReportFamily,
    register_family,
)

#: The monthly report's page furniture. Listing the date header alongside the
#: banners means it can never be absorbed as a row continuation, whichever
#: rendering a report uses.
_MONTHLY_FURNITURE: tuple[str, ...] = (
    "www.",
    "U.S. Bank",
    "Page ",
    "As of",
    "Next Payment",
)

#: The Note Valuation Report's page furniture: banners, footers and the column
#: headers the report repeats on every page of a section. The last three are
#: verbatim column-header runs, i.e. a layout fingerprint rather than a banner.
_NOTE_VALUATION_FURNITURE: tuple[str, ...] = (
    "www.usbank.com",
    "U.S. Bank Global Corporate Trust",
    "Page ",
    "As of",
    "Next Payment",
    "Available",
    "Document Report Payment for",
    "Reference Reference Amount Disbursements",
    "Payments (EUR)",
    "Original Face Opening Principal of Original Interest Amount Rate",
    "Issue Name Value Balance Payment Balance Due Payable Current",
    "Closing Balance Accrued Total Interest",
)

#: Coverage-test header fingerprints, whitespace-stripped and upper-cased so one
#: entry covers both renderings. Exhaustive by construction: a table whose
#: header matches neither is refused rather than read in a guessed order (#480).
_MONTHLY_ORDER_MARKERS = MappingProxyType(
    {
        "TESTDESCRIPTIONTHRESHOLDCURRENTRESULT": ColumnOrder.REQUIRED_FIRST,
        "TESTRATIOREQUIREDLEVELCALCULATIONRESULT": ColumnOrder.RATIO_FIRST,
    }
)

US_BANK: TrusteeReportFamily = register_family(
    TrusteeReportFamily(
        family_id="us_bank",
        label="U.S. Bank",
        administrator="U.S. Bank Global Corporate Trust",
        # Both phrases, jointly. "Global Corporate Trust" alone is a generic
        # trustee-department name; the host pins it to this administrator, and
        # the path is omitted so older deals' /cdo reports match too.
        header_signature=frozenset({"Global Corporate Trust", "usbank.com"}),
        documents=MappingProxyType(
            {
                DocumentKind.MONTHLY_REPORT: DocumentLayout(
                    section_titles=MappingProxyType(
                        {
                            SECTION_ASSET_PART_I: "Current Asset Characteristics - Part I",
                            SECTION_ASSET_PART_II: "Current Asset Characteristics - Part II",
                            SECTION_ASSET_PART_III: "Current Asset Characteristics - Part III",
                            SECTION_CCC: "S&P CCC Obligations",
                            SECTION_COUNTRY: "Country Concentration",
                            SECTION_SP_INDUSTRY: "S&P Industry Concentration",
                            SECTION_FITCH_INDUSTRY: "Fitch Industry Concentration",
                            SECTION_SP_RATING: "S&P Rating Stratification",
                            SECTION_PROFILE_TESTS: "Portfolio Profile Tests",
                            SECTION_EXEC_SUMMARY: "Executive Summary",
                            SECTION_PAR_VALUE_DETAIL: "Par Value Tests Detail",
                            SECTION_IC_DETAIL: "Interest Coverage Tests Detail",
                        }
                    ),
                    furniture_prefixes=_MONTHLY_FURNITURE,
                    # Safe here only because every data row opens with an asset
                    # identifier, which no furniture prefix can produce.
                    furniture_order=FurnitureOrder.FURNITURE_FIRST,
                    column_order_markers=_MONTHLY_ORDER_MARKERS,
                ),
                DocumentKind.NOTE_VALUATION_REPORT: DocumentLayout(
                    section_titles=MappingProxyType(
                        {
                            SECTION_NV_EXECUTIVE: "Executive Summary",
                            SECTION_NV_DISTRIBUTION: "Distribution Summary",
                            SECTION_NV_INTEREST_POP: "Interest Priority of Payments",
                            SECTION_NV_PRINCIPAL_POP: "Principal Priority of Payments",
                        }
                    ),
                    furniture_prefixes=_NOTE_VALUATION_FURNITURE,
                    # The #494 ordering: this report's footer banner also opens a
                    # real payee row, so the money tail is checked first.
                    furniture_order=FurnitureOrder.DATA_FIRST,
                    waterfalls=(
                        (SECTION_NV_INTEREST_POP, "revenue"),
                        (SECTION_NV_PRINCIPAL_POP, "redemption"),
                    ),
                ),
            }
        ),
    )
)
