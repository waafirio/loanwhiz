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

import re
from types import MappingProxyType

from loanwhiz.domain.trustee_report_registry import (
    SECTION_ASSET_PART_I,
    SECTION_ASSET_PART_II,
    SECTION_ASSET_PART_III,
    SECTION_CCC,
    SECTION_COUNTRY,
    SECTION_EXEC_SUMMARY,
    SECTION_FITCH_INDUSTRY,
    SECTION_ACCRUAL_DETAIL,
    SECTION_ASSET_PART_IV,
    SECTION_IC_DETAIL,
    SECTION_NV_DISTRIBUTION,
    SECTION_NV_EXECUTIVE,
    SECTION_NV_INTEREST_POP,
    SECTION_NV_PRINCIPAL_POP,
    SECTION_PAR_VALUE_DETAIL,
    SECTION_PROFILE_TESTS,
    SECTION_SP_INDUSTRY,
    SECTION_SP_RATING,
    COVERAGE_OUTCOME,
    CoverageTestRow,
    DocumentKind,
    DocumentLayout,
    FurnitureOrder,
    ReportHeader,
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

#: How U.S. Bank's monthly report names its deal and its date. The deal name is
#: page 1's opening line; the date is restated lower down in a delimited form
#: (``As of : 16/12/2024``) which is preferred over page 1's prose rendering
#: (``As of 16 December, 2024``) because it is unambiguous about day and month.
_MONTHLY_HEADER = ReportHeader(
    deal_name=re.compile(r"\A(?P<deal_name>\S.*?)\s*\Z"),
    reporting_date=re.compile(r"As of\s*:\s*(?P<as_of>\d{2}/\d{2}/\d{4})"),
    date_format="%d/%m/%Y",
)

#: One coverage-test row, as U.S. Bank prints it in both renderings: the test's
#: name, two like-typed percentage columns, an optional calculation label, and
#: the stated outcome. The name alternation is this administrator's own — it
#: prints ``Reinvestment Overcollateralisation Test`` where BNY Mellon prints
#: ``Reinvestment Par Value Test`` for the same covenant — which is why the
#: pattern belongs to the family rather than to the parser.
_MONTHLY_ROW = re.compile(
    r"(?P<name>(?:Class\s*[A-Z](?:/[A-Z])?\s*(?:Par\s*Value|Interest\s*Coverage)"
    r"|Reinvestment\s*Overcollateralisation)\s*Test)\s*"
    r"(?P<first>\d+\.\d{2})%\s*(?P<second>\d+\.\d{2})%\s*"
    r"(?:(?P<calculation>[A-Z]/[A-Z])\s*)?"
    rf"(?P<result>{COVERAGE_OUTCOME})"
)

#: Coverage-test header fingerprints, whitespace-stripped and upper-cased.
#: Exhaustive by construction: a table whose header matches neither is refused
#: rather than read in a guessed order (#480). Both renderings share one row
#: pattern and differ only in which of its two percentage columns is the ratio —
#: which is precisely what the old two-valued ``ColumnOrder`` said, now said as
#: a group name so a third like-typed column cannot break it.
_MONTHLY_ROW_MARKERS = MappingProxyType(
    {
        # Executive Summary: "Test Description | Threshold | Current | Result".
        "TESTDESCRIPTIONTHRESHOLDCURRENTRESULT": CoverageTestRow(
            pattern=_MONTHLY_ROW, ratio_group="second", required_group="first"
        ),
        # Detail pages: "… TEST | RATIO | REQUIRED LEVEL | CALCULATION | RESULT".
        "TESTRATIOREQUIREDLEVELCALCULATIONRESULT": CoverageTestRow(
            pattern=_MONTHLY_ROW, ratio_group="first", required_group="second"
        ),
    }
)

#: Why U.S. Bank's monthly report carries no interest-accrual section. Stated
#: as what the document *does* carry, so a reader can judge whether it answers
#: their question. The section matters because a family whose aggregate tables
#: count accrual records rather than assets has to reconcile that count against
#: this population; U.S. Bank's count is an asset count, so it needs no such
#: page and declares the absence rather than leaving it unsaid (#494).
#: Why U.S. Bank's monthly report carries no purchase-lot section. The lot
#: grain is where BNY prints country and several obligation flags; U.S. Bank
#: prints both on its per-asset pages, so nothing is lost by its absence and
#: saying so keeps "does not publish" distinct from "the title is missing".
_NO_LOT_DETAIL = (
    "U.S. Bank prints no purchase-lot section. Its portfolio is enumerated "
    "once per asset, and the country and obligation flags BNY carries at lot "
    "grain appear on Current Asset Characteristics Parts II and III instead, "
    "so there is no lot-level population in this document to read."
)

_NO_ACCRUAL_DETAIL = (
    "U.S. Bank prints no per-asset interest-accrual page. Each asset's rate "
    "basis, spread and index appear once on Current Asset Characteristics - "
    "Part II, at asset grain, so there is no separate rate-contract population "
    "to read; its concentration tables count assets, which the asset sections "
    "already enumerate."
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
                    unpublished_sections=MappingProxyType(
                        {
                            SECTION_ACCRUAL_DETAIL: _NO_ACCRUAL_DETAIL,
                            SECTION_ASSET_PART_IV: _NO_LOT_DETAIL,
                        }
                    ),
                    furniture_prefixes=_MONTHLY_FURNITURE,
                    # Safe here only because every data row opens with an asset
                    # identifier, which no furniture prefix can produce.
                    furniture_order=FurnitureOrder.FURNITURE_FIRST,
                    coverage_row_markers=_MONTHLY_ROW_MARKERS,
                    report_header=_MONTHLY_HEADER,
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
