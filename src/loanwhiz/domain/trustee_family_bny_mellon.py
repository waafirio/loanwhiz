"""BNY Mellon's trustee-report family table.

The second registered family, and the one that proved the #531 dispatch is a
registration seam rather than a generalisation of one administrator's habits.
Everything here was read off Contego CLO XI DAC's own documents — two COMPLIANCE
REPORTs (30-Aug-2024, 30-Sep-2024) and one NOTE VALUATION REPORT (payment date
20-Aug-2024) — and nothing in it names that deal: a family table describes an
administrator, and Avoca and Jubilee publish the same shapes.

Three things here are not U.S. Bank's shape, and each is a field the registry
grew to say them rather than a conditional in a parser:

**No country stratification exists.** U.S. Bank prints a ``Country
Concentration`` table; BNY prints none. Its country limits appear only as
compliance-test rows — ``(n)`` to ``(q)``, "Obligors domiciled in a country with
a country ceiling below AA-/A-/BBB-/BB- by S&P" — which are ceiling buckets
against a rating floor, not a per-country balance. Reporting those as a country
stratification would be a different datum wearing the right column's name
(#470), so the section is declared unpublished with that reason instead.

**Two tables share one printed title.** ``Industry Concentrations`` carries the
S&P table and the Fitch table one after the other, and ``Rating Concentrations``
carries three. The printed title cannot route them, so each section names the
fingerprint of its own table's sub-header.

**A row is not a line.** BNY's PDFs extract as a column-major stack of single
cells plus one long row-major line per page carrying the whole table; U.S.
Bank's give one asset per line. The rows have to be re-cut out of that line, and
the identifier sits mid-row (after the obligor description) rather than opening
it. Hence ``REFLOWED_ROWS`` and ``EMBEDDED``.

One deliberate omission: ``furniture_prefixes`` does not cover BNY's per-page
banner ``<deal name> as of <date>``, because a family table may not name a deal.
That line is rejected by the row-shape test instead — it carries no row's
anchored tail — which is the #494 ordering doing the work a prefix list cannot.
"""

from __future__ import annotations

import re
from types import MappingProxyType

from loanwhiz.domain.trustee_report_registry import (
    COVERAGE_OUTCOME,
    CountGrain,
    CoverageTestRow,
    DocumentKind,
    DocumentLayout,
    FurnitureOrder,
    IdentifierPosition,
    MONEY,
    ReportHeader,
    RowGeometry,
    SECTION_ASSET_PART_I,
    SECTION_ASSET_PART_II,
    SECTION_ACCRUAL_DETAIL,
    SECTION_ASSET_PART_III,
    SECTION_ASSET_PART_IV,
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
    TrusteeReportFamily,
    register_family,
)

#: Page furniture that recurs at line start in both document kinds. The banner
#: ``<deal> as of <date>`` is deliberately absent — see the module docstring.
_FURNITURE: tuple[str, ...] = (
    "Page ",
    "Table of Contents",
    "Disclaimer:",
    "This report is for informational purposes only",
    "LEI :",
    "Client Service Manager",
    "Collateral Manager",
)

#: How BNY's monthly report names its deal and its date. Page 1's first line is
#: ``LEI :``, so the deal name is taken from the banner this administrator
#: repeats in every page footer — ``Contego CLO XI DAC as of 30-Aug-2024`` —
#: anchored to the whole line so the 69 copies reflowed into the tail of a data
#: row cannot match. The date is stated once on page 1, in its own format.
_MONTHLY_HEADER = ReportHeader(
    deal_name=re.compile(
        r"\A(?P<deal_name>\S.*?)\s+as of\s+\d{2}-[A-Za-z]{3}-\d{4}\s*\Z"
    ),
    reporting_date=re.compile(r"\AAs of\s+(?P<as_of>\d{2}-[A-Za-z]{3}-\d{4})\s*\Z"),
    date_format="%d-%b-%Y",
)

#: The coverage tests BNY states in **both** renderings, and only those. The
#: Compliance Tests table also carries collateral-quality and portfolio-profile
#: rows the detail sections do not restate; matching them would put tests in one
#: rendering and not the other, which the cross-rendering check refuses. This
#: administrator names the reinvestment covenant ``Reinvestment Par Value Test``
#: where U.S. Bank names it ``Reinvestment Overcollateralisation Test``.
_TEST_NAME = (
    r"(?P<name>(?:Class\s*[A-Z](?:/[A-Z])?\s*(?:Par\s*Value|Interest\s*Coverage)"
    r"|Reinvestment\s*Par\s*Value)\s*Test)"
)

#: What BNY prints between a test's name and its percentages, which U.S. Bank
#: prints nowhere: the ratio's numerator and denominator, as money.
_NUMERATOR_DENOMINATOR = rf"\s*{MONEY}\s*{MONEY}"

#: The required level, printed as its comparison operator and then the figure.
#: The operator is matched and discarded rather than skipped over: a row whose
#: direction this pattern cannot read is one whose required level it should not
#: claim to have read either.
_REQUIREMENT = r"\s*[<>]=?\s*(?P<required>\d+\.\d{2})%"

#: Coverage-test header fingerprints, whitespace-stripped and upper-cased →
#: the grammar each denotes. Exhaustive by construction (#480): a table whose
#: header matches neither is refused rather than read in a guessed order.
#:
#: **The two headers do not agree on where the ratio sits, and that is the
#: whole reason this is a grammar rather than an order.** Each prints three
#: like-typed percentage columns. On the detail pages the computed ratio comes
#: first and the third column is the level it must clear. In the Compliance
#: Tests table the first column is the *prior* period's outcome — August's
#: 26.19% is September's prior — so reading the ratio as the first percentage
#: would grade every test against last month's figure, and silently: the value
#: is real, in range, and of the right type.
_MONTHLY_ROW_MARKERS: MappingProxyType = MappingProxyType(
    {
        # Par Value Tests and Interest Coverage Tests:
        # "… Numerator Denominator | Actual | Cushion | Target | Result"
        "TESTDESCRIPTIONNUMERATORDENOMINATORACTUALCUSHIONTARGETRESULT": CoverageTestRow(
            pattern=re.compile(
                _TEST_NAME
                + _NUMERATOR_DENOMINATOR
                + r"\s*(?P<actual>\d+\.\d{2})%\s*(?P<cushion>\d+\.\d{2})%"
                + _REQUIREMENT
                + rf"\s*(?P<result>{COVERAGE_OUTCOME})"
            ),
            ratio_group="actual",
            required_group="required",
        ),
        # Compliance Tests:
        # "… Numerator Denominator | Prior Outcome | Outcome | Requirement | Result"
        "TESTNAMENUMERATORDENOMINATORPRIOROUTCOMEOUTCOMEREQUIREMENTRESULT": CoverageTestRow(
            pattern=re.compile(
                _TEST_NAME
                + _NUMERATOR_DENOMINATOR
                + r"\s*(?P<prior>\d+\.\d{2})%\s*(?P<outcome>\d+\.\d{2})%"
                + _REQUIREMENT
                + rf"\s*(?P<result>{COVERAGE_OUTCOME})"
            ),
            ratio_group="outcome",
            required_group="required",
        ),
    }
)

#: Why BNY publishes no country stratification. Stated as what the document
#: *does* carry, so a reader can judge whether it answers their question.
_NO_COUNTRY_TABLE = (
    "BNY prints no per-country balance table. Country exposure appears only as "
    "compliance-test rows (n)-(q), 'Obligors domiciled in a country with a "
    "country ceiling below AA-/A-/BBB-/BB- by S&P', which bucket obligors "
    "against a sovereign rating floor rather than stratifying balance by "
    "country. Reporting those as a country stratification would attribute a "
    "different datum to the column (#470)."
)

_MONTHLY = DocumentLayout(
    section_titles=MappingProxyType(
        {
            SECTION_ASSET_PART_I: "Asset Information I",
            SECTION_ASSET_PART_II: "Asset Information II",
            SECTION_ASSET_PART_III: "Asset Information III",
            SECTION_ASSET_PART_IV: "Asset Information IV",
            SECTION_ACCRUAL_DETAIL: "Interest Accrual Detail",
            SECTION_CCC: "CCC Obligations",
            SECTION_SP_INDUSTRY: "Industry Concentrations",
            SECTION_FITCH_INDUSTRY: "Industry Concentrations",
            SECTION_SP_RATING: "Rating Concentrations",
            SECTION_PROFILE_TESTS: "Compliance Tests",
            SECTION_EXEC_SUMMARY: "Compliance Summary",
            SECTION_PAR_VALUE_DETAIL: "Par Value Tests",
            SECTION_IC_DETAIL: "Interest Coverage Tests",
        }
    ),
    unpublished_sections=MappingProxyType({SECTION_COUNTRY: _NO_COUNTRY_TABLE}),
    #: Whitespace-stripped, upper-cased fingerprints of each table's own
    #: sub-header label, for the sections that share a printed title. The S&P
    #: rating section takes the *asset* rating table: the stratification is of
    #: the schedule's assets, and BNY prints an issuer table beside it.
    section_table_markers=MappingProxyType(
        {
            SECTION_SP_INDUSTRY: "S&PINDUSTRY",
            SECTION_FITCH_INDUSTRY: "FITCHINDUSTRY",
            SECTION_SP_RATING: "S&PASSETRATING",
        }
    ),
    furniture_prefixes=_FURNITURE,
    furniture_order=FurnitureOrder.DATA_FIRST,
    coverage_row_markers=_MONTHLY_ROW_MARKERS,
    report_header=_MONTHLY_HEADER,
    coverage_summary_section=SECTION_PROFILE_TESTS,
    row_geometry=RowGeometry.REFLOWED_ROWS,
    identifier_position=IdentifierPosition.EMBEDDED,
    count_grain=CountGrain.ACCRUAL_RECORD,
)

_NOTE_VALUATION = DocumentLayout(
    section_titles=MappingProxyType(
        {
            SECTION_NV_EXECUTIVE: "Compliance Summary",
            SECTION_NV_DISTRIBUTION: "Distributions",
            SECTION_NV_INTEREST_POP: "Application of Interest Proceeds",
            SECTION_NV_PRINCIPAL_POP: "Application of Principal Proceeds",
        }
    ),
    furniture_prefixes=_FURNITURE + ("Priorities of Payments (Waterfall)",),
    furniture_order=FurnitureOrder.DATA_FIRST,
    waterfalls=(
        (SECTION_NV_INTEREST_POP, "revenue"),
        (SECTION_NV_PRINCIPAL_POP, "redemption"),
    ),
    row_geometry=RowGeometry.REFLOWED_ROWS,
    identifier_position=IdentifierPosition.EMBEDDED,
)

#: BNY Mellon. The signature pairs the administrator's name with the contact
#: block every report opens with; neither phrase appears anywhere in a U.S. Bank
#: report, and neither of U.S. Bank's appears in these.
BNY_MELLON: TrusteeReportFamily = register_family(
    TrusteeReportFamily(
        family_id="bny_mellon",
        label="BNY Mellon",
        administrator="BNY Mellon Corporate Trustee Services",
        header_signature=frozenset({"BNY Mellon", "Client Service Manager"}),
        documents=MappingProxyType(
            {
                DocumentKind.MONTHLY_REPORT: _MONTHLY,
                DocumentKind.NOTE_VALUATION_REPORT: _NOTE_VALUATION,
            }
        ),
    )
)
