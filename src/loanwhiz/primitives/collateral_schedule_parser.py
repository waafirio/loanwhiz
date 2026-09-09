"""Trustee-report collateral schedule parser — the per-asset **collateral** tape.

Where :mod:`loanwhiz.primitives.notes_cash_parser` reads the *liability* side of a
quarterly DSA report, this module reads the **per-asset collateral schedule** out
of a monthly CLO trustee report and returns one structured row per asset per
reporting date. It follows that module's shape exactly — the same two seams, the
same deterministic ``pypdf``/regex parse, the same ``PrimitiveResult`` envelope,
committed text fixtures instead of committed PDFs. There is deliberately no
second PDF stack in the tree.

The two seams (mirrors ``notes_cash_parser``)
---------------------------------------------
- **Parse path (offline, unit-tested):** :func:`parse_schedule_text` maps the
  extracted report text into a typed :class:`CollateralSchedule`. Pure; no
  network, no LLM.
- **Extraction (live, integration-gated):** :func:`extract_report_lines` turns a
  report PDF into that text, and :func:`fetch_report_text` fetches one by URL.
  The committed fixtures under ``tests/fixtures/collateral_schedule/`` are the
  output of this seam, regenerated with :func:`write_fixture`.

Why a trustee report can be parsed *generally*
----------------------------------------------
A trustee report is **self-describing**. Its concentration and stratification
pages (Country Concentration, S&P/Fitch Industry Concentration, S&P Rating
Stratification) enumerate exactly the vocabularies its per-asset detail pages
use, and state the totals those details must sum to. So the parse runs in two
passes:

1. **Summary pass** — read the report's own aggregate tables. These give both
   the closed vocabularies (which country names, which industry names exist)
   and the acceptance oracle.
2. **Detail pass** — read the per-asset sections, resolving free-text column
   boundaries against those vocabularies.

:func:`reconcile_schedule` then ties the parsed tape back to pass 1. That check
is the contract: a schedule that does not reconcile to the document's own totals
is refused, never returned, because a silently-wrong tape is worse than none.

Why the text extraction is coordinate-based
-------------------------------------------
These reports draw each table **rotated**, one text-showing operator per cell.
``pypdf``'s plain ``extract_text()`` collapses a whole page into one blob and
loses every row boundary — an asset's fields run into the next asset's with no
separator. ``extract_text(visitor_text=...)`` instead reports one run per
*visual line* with its text matrix, so sorting the runs by the matrix's
cross-axis offset recovers the page's true line order, including the
**continuation lines** that carry wrapped issuer names, facility names and
country values. That recovery is what :func:`extract_report_lines` exists to do;
everything downstream is ordinary line parsing.

How the four detail sections join
---------------------------------
All four sections key on the ``Issue/Facility Identifier`` (a LoanX ``LX``
identifier or a 12-character ISIN), but they are parsed in dependency order
because each resolves an ambiguity in the next:

- **Part III** is the anchor. Its only free-text column is the facility name,
  and the eight flag cells after it are each exactly ``-`` or ``Yes`` — so the
  full facility name is recoverable with no ambiguity at all.
- **Part II** carries facility name, balance, industries, currency and country
  with no delimiters between them. Knowing the facility name from Part III
  fixes the name/balance boundary (the report really does contain a facility
  named ``...TLB4`` immediately followed by ``4,500,000.00``); the industry and
  country vocabularies from pass 1 fix the rest.
- **Part I** carries issuer name and facility name concatenated with no
  separator. Knowing the full facility name and the balance, the issuer is what
  remains once the longest facility-name prefix that is a suffix of the head is
  removed.
- **S&P CCC Obligations** adds seniority and rating for the CCC bucket only.

Every one of those resolutions can fail, and each failure is **counted** on
:class:`ScheduleDefects` rather than dropped — a schedule that quietly loses
assets is the failure mode this module is built to make impossible.
"""

from __future__ import annotations

import os
import re
import time
import urllib.request
from decimal import Decimal
from enum import Enum
from pathlib import Path
from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel, Field, PrivateAttr

from loanwhiz.domain.trustee_report_families import (
    FAMILY_REGISTRY,
    ColumnOrder,
    DocumentKind,
    CountGrain,
    DocumentLayout,
    FurnitureOrder,
    RowGeometry,
    UnknownReportFamilyError,
)
from loanwhiz.domain.trustee_report_registry import (
    SECTION_ACCRUAL_DETAIL,
    SECTION_ASSET_PART_I,
    SECTION_ASSET_PART_II,
    SECTION_ASSET_PART_III,
    SECTION_ASSET_PART_IV,
    SECTION_CCC,
    SECTION_COUNTRY,
    SECTION_EXEC_SUMMARY,
    SECTION_FITCH_INDUSTRY,
    SECTION_IC_DETAIL,
    SECTION_PAR_VALUE_DETAIL,
    SECTION_PROFILE_TESTS,
    SECTION_SP_INDUSTRY,
    SECTION_SP_RATING,
)
from loanwhiz.primitives.base import (
    AuditEntry,
    BaseInput,
    Citation,
    PrimitiveResult,
)

if TYPE_CHECKING:  # pragma: no cover - annotation-only; see liability_provenance
    from loanwhiz.domain.provenance import ProvenanceMap

_PRIMITIVE_NAME = "collateral_schedule_parser"
_PRIMITIVE_VERSION = "0.1.0"
_DETERMINISTIC_CONFIDENCE = 1.0

# ===========================================================================
# Section vocabulary — the report's own page titles
# ===========================================================================

# ``SECTION_*`` are LoanWhiz's section *roles*, re-exported above from the
# family registry. Which title a report prints for each role is a property of
# the collateral administrator rather than of this parser, so it lives on the
# detected family's
# :class:`~loanwhiz.domain.trustee_report_registry.DocumentLayout` — see
# :func:`_resolve_layout`.
#
# The roles split three ways. The per-asset detail sections (Parts I–III and the
# CCC bucket) carry the rows; the summary sections supply the vocabularies and
# the acceptance oracle; and the **liability-side** sections state, about the
# notes the collateral funds, the figures a prospectus can only express as a
# formula — each class's *resolved* current coupon and each coverage test's
# *required level*. They are read through the seam below rather than by a second
# reader, because it is one document.
#
# Titles are matched longest-first (``DocumentLayout.titles``) so ``Part III``
# is never matched as ``Part I``.

#: How much of the report is read to identify its family: the first
#: ``_HEADER_LINES`` lines of each of the first ``_HEADER_PAGES`` pages. The
#: administrator's banner sits on the title page above the table of contents,
#: but a document can carry a cover page ahead of it, so the window spans the
#: leading pages rather than page 1 alone. It stays a window rather than the
#: whole document because a signature phrase quoted in a report's body is not
#: evidence about who published it.
_HEADER_LINES = 12
_HEADER_PAGES = 3

#: How much of a reflowed page's row-major line is read when routing it to a
#: section. The line opens with the table's printed title and its column
#: header; a window keeps a title quoted in a row's free text from claiming the
#: page for another section.
_REFLOWED_TITLE_WINDOW = 120


def _resolve_layout(pages: list[list[str]]) -> DocumentLayout:
    """The layout this report is parsed with, from the report's own header.

    Refuses rather than defaulting when no registered family matches (#494).
    A trustee report parsed under the wrong family's section titles routes every
    section to no pages and parses nothing, which reconciliation cannot
    distinguish from a document that genuinely says nothing — so refusal is the
    only safe answer, and it is not a degraded one.
    """
    header = "\n".join(
        line for page in pages[:_HEADER_PAGES] for line in page[:_HEADER_LINES]
    )
    family = FAMILY_REGISTRY.detect(header)
    if family is None:
        known = ", ".join(f.label for f in FAMILY_REGISTRY.all()) or "none"
        raise UnknownReportFamilyError(
            "trustee report matches no registered report family "
            f"(registered: {known}). The first {_HEADER_PAGES} pages carry none "
            "of their header signatures, so the section titles, page furniture "
            "and coverage-test column orders to parse it with are unknown. "
            "Register the administrator's family rather than parsing it as "
            "another's."
        )
    layout = family.layout(DocumentKind.MONTHLY_REPORT)
    _assert_furniture_order(layout, family.label)
    return layout


#: The furniture ordering each row geometry's parse path implements — the two
#: are not independent choices.
#:
#: ``ROW_PER_LINE`` is ``_data_rows``: it drops furniture *before* testing a
#: line for data, which is safe only because every data row on that path opens
#: with an asset identifier no furniture prefix can produce.
#:
#: ``REFLOWED_ROWS`` is ``_reflowed_rows``: a row is evidenced by its own
#: anchored tail (identifier, then this section's column shape), so the row
#: question is asked **first** and no furniture filter takes part in finding
#: rows at all. That is #494's ordering, and it is what lets BNY's per-page
#: banner ``<deal name> as of <date>`` be rejected for carrying no row's tail
#: rather than by a prefix list a family table may not contain.
_FURNITURE_ORDER_BY_GEOMETRY: Mapping[RowGeometry, FurnitureOrder] = MappingProxyType(
    {
        RowGeometry.ROW_PER_LINE: FurnitureOrder.FURNITURE_FIRST,
        RowGeometry.REFLOWED_ROWS: FurnitureOrder.DATA_FIRST,
    }
)


def _assert_furniture_order(layout: DocumentLayout, family_label: str) -> None:
    """Refuse a family whose declared furniture ordering its parse path does not implement.

    The ordering is declared on the family so it is reviewable, but a
    declaration nothing reads drifts from the code it describes. Checking it
    here makes registering a family whose two declarations disagree a loud
    failure rather than a silent re-run of the defect the ordering exists to
    prevent — a family that says ``data-first`` while taking the path that
    filters furniture first would eat any row opening like a banner (#494).
    """
    implemented = _FURNITURE_ORDER_BY_GEOMETRY[layout.row_geometry]
    if layout.furniture_order is not implemented:
        raise UnknownReportFamilyError(
            f"{family_label} declares furniture_order="
            f"{layout.furniture_order.value!r} for its monthly report with "
            f"row_geometry={layout.row_geometry.value!r}, but that geometry's "
            f"parse path implements {implemented.value!r}. The two declarations "
            "describe one behaviour and disagreeing about it is how a data row "
            "that opens like a banner gets eaten (#494)."
        )

#: The eight Part III flags, in the column order the section header prints them:
#: ``Cov-Lite Loan | DIP Loan | PIK Security | Deferring Security |
#: Current Pay Obligation | Revolving Obligation | Delayed Drawdown Loan |
#: Bridge Loan``.
PART_III_FLAGS: tuple[str, ...] = (
    "cov_lite",
    "dip",
    "pik",
    "deferring",
    "current_pay",
    "revolving",
    "delayed_drawdown",
    "bridge",
)

# ---------------------------------------------------------------------------
# Row-shape regexes
# ---------------------------------------------------------------------------

#: An asset identifier: a LoanX id (``LX`` + exactly six digits) or a
#: 12-character ISIN. Anchored and *exact-width* on purpose — a bare ``LX\d+``
#: would swallow a leading digit of an issuer name, and (the reason the issue's
#: own estimate came in low) an ``LX``-only pattern silently drops every
#: bond-identified asset. The set this excludes is: CUSIPs, SEDOLs, internal
#: trustee identifiers, and any LoanX id that is not six digits. If a future
#: report uses one, the row is *counted* as unrecognised, never skipped.
_IDENTIFIER = r"LX\d{6}|[A-Z]{2}[A-Z0-9]{9}\d"
IDENTIFIER_RE = re.compile(rf"^({_IDENTIFIER})(?=\D|$)")

#: The same identifier, searched for **inside** a row rather than at its start.
#: Derived from one alternation with :data:`IDENTIFIER_RE` on purpose: two
#: hand-maintained copies drifting apart about what an identifier is would
#: reproduce #468 (an ``LX``-only pattern silently skipped 34 ISIN-identified
#: assets and understated par by ~EUR 53m) one family at a time. The left
#: look-behind stops a match starting mid-token.
_EMBEDDED_IDENTIFIER_RE = re.compile(rf"(?<![A-Z0-9])({_IDENTIFIER})(?=\D|$)")

#: A comma-grouped money amount with exactly two decimals.
MONEY = r"\d{1,3}(?:,\d{3})*\.\d{2}"
_MONEY_RE = re.compile(MONEY)

#: The two renderings. The same trustee produces these reports two ways: some
#: months the tables are drawn rotated, so cells arrive concatenated with no
#: separator at all (``...3,000,000.00LoanFloating3.50...``); other months they
#: are drawn as ordinary text and the cells are space-separated. Every pattern
#: below therefore allows optional whitespace between cells, and every free-text
#: column is matched on whitespace-stripped text — which collapses the two
#: renderings onto one parse path instead of forking the parser per layout.
_S = r"\s*"

#: The Part I numeric tail, which begins at the ``Loan``/``Bond`` asset-type
#: cell. Three cells are genuinely optional — the PDF emits no cell at all
#: rather than a blank one — and which are absent depends on the asset:
#:
#: - a **fixed-rate** asset has a spread and a coupon but no index floor and no
#:   index type (verified against the report's own column view, where the Floor
#:   and Index Type columns are one cell shorter than Coupon Type on a page
#:   carrying a fixed-rate bond);
#: - some **floating** assets carry no floor cell;
#: - an asset priced off no published index carries no index type.
#:
#: The optional groups are ordered so a three-decimal run reads as
#: spread/floor/coupon and a two-decimal run as spread/coupon, which the digit
#: count alone makes unambiguous.
_PART_I_TAIL = re.compile(
    rf"^(?P<asset_type>Loan|Bond){_S}(?P<coupon_type>Floating|Fixed){_S}"
    rf"(?P<spread>\d+\.\d{{2}}){_S}"
    rf"(?:(?P<floor>\d+\.\d{{2}}){_S})?"
    rf"(?P<coupon>\d+\.\d{{2}}){_S}"
    rf"(?:(?P<index_type>[A-Za-z][A-Za-z0-9]*(?:\s[A-Za-z0-9]+)*?){_S})?"
    rf"(?P<maturity>\d{{2}}/\d{{2}}/\d{{4}}){_S}(?P<market_value>\d+\.\d{{1,4}})\s*$"
)

#: The eight Part III flag cells, anchored at the end of the row's first line
#: because that is where the section puts them. Anchoring matters: a facility
#: genuinely named ``... Cov-Lite T/L`` contains a hyphen, and an unanchored
#: search for flag-shaped tokens could start inside the name.
_PART_III_FLAGS_RE = re.compile(rf"((?:{_S}(?:Yes|-)){{8}})\s*$")
_FLAG_RE = re.compile(r"(Yes|-)")

#: An aggregate-table row: ``<label><balance><percent><count>``. The label is
#: whatever precedes the balance; the balance is comma-grouped, so the split is
#: unambiguous left-to-right even when the label ends in a digit.
_AGGREGATE_ROW_RE = re.compile(
    rf"^(?P<label>.*?){_S}(?P<balance>{MONEY}){_S}(?P<percent>\d+\.\d{{2}}){_S}(?P<count>\d+)\s*$"
)

#: One Executive Summary note-class row: ``Class <label> Notes`` followed by
#: principal balance, current coupon and periodic interest. The coupon's **five**
#: decimal places are what make the three cells separable when the rendering
#: concatenates them with no delimiter (``248,000,000.004.544002,066,005.33``):
#: money carries exactly two, a coupon exactly five, so the boundaries are fixed
#: by digit count rather than by a separator that is not there.
#:
#: ``N/A`` is admitted for the two rate cells and is **not** a number. The
#: subordinated note has no coupon at all, and the failure this exists to
#: prevent is that absence arriving downstream as ``0.0`` \u2014 a note that pays
#: nothing and a note whose coupon the report does not state are different
#: facts. Matched with :func:`re.finditer`, since one rendering puts every class
#: on one line and the other puts each on its own.
_NOTE_CLASS_ROW_RE = re.compile(
    rf"Class{_S}(?P<label>Subordinated|[A-Z](?:-\d)?){_S}Notes{_S}"
    rf"(?P<balance>{MONEY}){_S}"
    rf"(?P<coupon>\d+\.\d{{5}}|N/A){_S}"
    rf"(?P<interest>{MONEY}|N/A)"
)

#: One coverage-test row, in **either** of the two sections that state it. The
#: name alternation is the guard: the same page prints Collateral Quality Tests
#: in an identical shape (``Fitch Maximum WA Rating Factor Test25.5024.46``),
#: and those carry no ``%`` terminator, so their two figures cannot be split
#: apart at all. Matching coverage tests by name rather than by position also
#: survives the December rendering, which interleaves the two groups row by row.
#:
#: ``CALCULATION`` (``A/B``, ``A/G``) appears only on the detail pages, so it is
#: optional here \u2014 the one regex reads both sections, and which figure is the
#: required level is decided by the header, never by this pattern.
_COVERAGE_TEST_RE = re.compile(
    rf"(?P<name>(?:Class{_S}[A-Z](?:/[A-Z])?{_S}(?:Par{_S}Value|Interest{_S}Coverage)"
    rf"|Reinvestment{_S}Overcollateralisation){_S}Test){_S}"
    rf"(?P<first>\d+\.\d{{2}})%{_S}(?P<second>\d+\.\d{{2}})%{_S}"
    rf"(?:(?P<calculation>[A-Z]/[A-Z]){_S})?"
    rf"(?P<result>Passed|Failed|N/A)"
)

#: The stated totals line under the Executive Summary's note table: aggregate
#: principal balance and aggregate periodic interest, in that order and nothing
#: else on the line. Two independent oracles for one parse.
_STATED_TOTALS_RE = re.compile(rf"^(?P<balance>{MONEY}){_S}(?P<interest>{MONEY})\s*$")

#: Every character Python's ``str.splitlines()`` treats as a line break.
_LINE_BREAKS = re.compile(r"[\r\n\v\f\x1c-\x1e\x85\u2028\u2029]+")

_REPORTING_DATE_RE = re.compile(r"As of\s*:\s*(\d{2}/\d{2}/\d{4})")
_PAGE_MARKER_RE = re.compile(r"^--- page (\d+) ---$")



# ===========================================================================
# Models
# ===========================================================================


class CollateralAsset(BaseModel):
    """One asset in the collateral schedule, at one reporting date.

    Fields sourced from a section the report did not carry for this asset are
    ``None`` — honest absence, never a silent zero. ``seniority`` and
    ``sp_rating`` are populated only for assets in the S&P CCC bucket, which is
    the only detail section that publishes them.
    """

    identifier: str
    issuer_name: str | None = None
    facility_name: str
    principal_balance: Decimal
    asset_type: str | None = None
    coupon_type: str | None = None
    current_spread: Decimal | None = None
    index_floor: Decimal | None = None
    current_coupon: Decimal | None = None
    index_type: str | None = None
    maturity_date: str | None = None
    market_value: Decimal | None = None
    sp_industry: str | None = None
    fitch_industry: str | None = None
    currency: str | None = None
    country: str | None = None
    flags: dict[str, bool] = Field(default_factory=dict)
    seniority: str | None = None
    sp_rating: str | None = None
    fitch_rating: str | None = None
    #: The report's quoted **price per 100 of par**, never a value. Named so
    #: the unit is impossible to mistake: #470 is the correction of a column
    #: that looked like a market value and was a price (``99.72``), wrong by
    #: four orders of magnitude into the OC ratio. A price never reaches a
    #: value column, and this field is why it does not have to.
    market_price_pct: Decimal | None = None
    #: How many purchase lots this asset was bought in, where the report
    #: enumerates them. Not the population its concentration tables count —
    #: that is :attr:`accrual_records` — and kept distinct because the two
    #: differ (Contego: 256 lots, 212 accrual records, 177 assets).
    purchase_lots: int | None = None
    #: How many interest-accrual records this asset carries — one per rate
    #: contract. This is the population a family whose ``CountGrain`` is
    #: ``ACCRUAL_RECORD`` states its ``# of Assets`` counts over, so it is what
    #: makes a stated count checkable at all.
    accrual_records: int | None = None

    def flag(self, name: str) -> bool:
        """Return one Part III flag, defaulting to ``False`` when unparsed."""
        return bool(self.flags.get(name, False))


class AggregateBucket(BaseModel):
    """One row of a report-stated concentration or stratification table."""

    label: str
    balance: Decimal
    percent: Decimal
    count: int


class ProfileTest(BaseModel):
    """One Portfolio Profile Test, as the report states it."""

    name: str
    numerator: Decimal
    denominator: Decimal


class ReportAggregates(BaseModel):
    """What the report says about itself — pass 1, and the acceptance oracle."""

    aggregate_principal_balance: Decimal | None = None
    asset_count: int | None = None
    country: list[AggregateBucket] = Field(default_factory=list)
    sp_industry: list[AggregateBucket] = Field(default_factory=list)
    fitch_industry: list[AggregateBucket] = Field(default_factory=list)
    sp_rating: list[AggregateBucket] = Field(default_factory=list)
    ccc_total: Decimal | None = None
    profile_tests: list[ProfileTest] = Field(default_factory=list)
    #: Names of aggregate tables whose own rows do not sum to the aggregate
    #: balance printed on the same page. Such a table cannot serve as a balance
    #: oracle — not because the parse is doubtful but because the *document*
    #: contradicts itself — so its balances are excluded and the discrepancy is
    #: reported. Its counts are still compared.
    inconsistent_tables: list[str] = Field(default_factory=list)
    #: Section key → the reason this administrator does not publish it, taken
    #: from the family record. Absence *with* a reason is a fact about the
    #: document; absence without one is a parse that failed. Keeping the reason
    #: here is what lets the reconciliation report a check it did not run
    #: rather than silently running one fewer (#494).
    unpublished_sections: dict[str, str] = Field(default_factory=dict)
    #: How many rows the report's own accrual-record section carries, when it
    #: publishes one. This is the population a family whose
    #: :class:`~loanwhiz.domain.trustee_report_registry.CountGrain` is
    #: ``ACCRUAL_RECORD`` states its counts over — Contego's 212 against 177
    #: assets — so it is what that count must be reconciled against.
    accrual_record_count: int | None = None
    #: Section key → how many asset rows that section carried. The asset count
    #: has its own oracle for a family whose stated counts are not asset
    #: counts: the sections that each enumerate the portfolio once must agree
    #: with each other and with the tape. Without it a family like BNY Mellon
    #: would have par checked and its asset count checked against nothing.
    section_asset_counts: dict[str, int] = Field(default_factory=dict)

    def profile_test(self, prefix: str) -> ProfileTest | None:
        """Return the first profile test whose name starts with ``prefix``."""
        for test in self.profile_tests:
            if test.name.startswith(prefix):
                return test
        return None

    def vocabulary(self, table: str) -> list[str]:
        """Labels of one aggregate table, longest first for prefix matching."""
        buckets: list[AggregateBucket] = getattr(self, table)
        return sorted((b.label for b in buckets), key=len, reverse=True)


class ScheduleDefects(BaseModel):
    """Counted parse failures.

    Every field is a count of rows the parser could **not** fully resolve. They
    exist so a degraded parse is visible rather than silent: nothing here is
    dropped quietly, and :func:`reconcile_schedule` refuses while any of them is
    non-zero.
    """

    unrecognised_rows: int = 0
    part_i_tail_unparsed: int = 0
    unresolved_issuer_name: int = 0
    unresolved_industry: int = 0
    unresolved_country: int = 0
    balance_disagreement: int = 0
    identifiers_missing_from_part_i: int = 0
    identifiers_missing_from_part_ii: int = 0
    identifiers_missing_from_part_iii: int = 0
    identifiers_missing_from_part_iv: int = 0
    identifiers_absent_from_the_anchor_section: int = 0
    descriptions_without_an_obligor: int = 0
    accrual_records_without_a_balance: int = 0
    #: A section the parser could not read at all — distinct from a section
    #: that read no rows, and from one the family declares unpublished. Three
    #: different facts that an empty result renders identically (#494).
    section_unreadable: int = 0
    #: A stated market value that is not the stated par at the stated price.
    #: One of the two columns is then not the quantity its header names, which
    #: is #470 exactly, so it is counted rather than absorbed.
    market_value_units: int = 0
    ccc_rows_unjoined: int = 0
    #: The report publishes a distribution the tape cannot be grouped by,
    #: because some asset lacks the attribute. Not a wrong number — a check
    #: that could not run, which must not look like one that passed.
    bucket_attribute_incomplete: int = 0
    #: Not a parse failure: the source document's own summary table disagrees
    #: with itself. Counted here so it is visible rather than silently absorbed.
    source_aggregate_inconsistent: int = 0
    notes: list[str] = Field(default_factory=list)

    @property
    def total(self) -> int:
        """Total number of defects counted across every category."""
        return sum(
            int(getattr(self, name))
            for name, field in type(self).model_fields.items()
            if name != "notes"
        )

    @property
    def blocking(self) -> int:
        """Defects that mean a row was lost, mis-joined or partly unread.

        Excludes the categories that leave every reconciled quantity correct:
        an issuer name the source itself mangled, a summary table the source
        contradicts, a description the source printed without its
        ``obligor - facility`` separator, an accrual record the source printed
        with no balance cell, and a distribution the tape cannot be grouped by.
        Those are surfaced, not fatal: each is a fact about the document, or a
        check that could not run, rather than a row this parser lost.

        The last one is the #494 shape handled deliberately rather than by
        silence: U.S. Bank publishes a rating stratification while only its CCC
        bucket carries a rating, so that check genuinely cannot run. Refusing
        the whole schedule for it would reject a tape whose every figure ties;
        running it anyway would compare a full table against a partial tape and
        report a divergence that is not one. It is counted and noted, so "this
        check did not run" is visible instead of looking like "this check
        passed".
        """
        return (
            self.total
            - self.unresolved_issuer_name
            - self.source_aggregate_inconsistent
            - self.descriptions_without_an_obligor
            - self.bucket_attribute_incomplete
            - self.accrual_records_without_a_balance
        )

    def record(self, category: str, detail: str) -> None:
        """Increment ``category`` and keep a bounded, human-readable note."""
        setattr(self, category, int(getattr(self, category)) + 1)
        if len(self.notes) < 25:
            self.notes.append(f"{category}: {detail}")


class ReconciliationCheck(BaseModel):
    """One comparison of the parsed tape against a report-stated figure."""

    name: str
    expected: str
    actual: str
    ok: bool


class ScheduleReconciliation(BaseModel):
    """The result of tying the parsed tape back to the report's own totals."""

    checks: list[ReconciliationCheck] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True only when every check passed."""
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> list[ReconciliationCheck]:
        """The checks that did not tie out."""
        return [c for c in self.checks if not c.ok]


class CollateralSchedule(BaseModel):
    """One reporting date's collateral schedule, plus its own acceptance oracle."""

    deal_name: str | None = None
    period_label: str
    reporting_date: str | None = None
    assets: list[CollateralAsset] = Field(default_factory=list)
    aggregates: ReportAggregates = Field(default_factory=ReportAggregates)
    defects: ScheduleDefects = Field(default_factory=ScheduleDefects)
    #: What the report's own aggregate tables count beside the balance they
    #: state, taken from the family record. Carried on the schedule because the
    #: oracle needs it: comparing a stated count against the wrong population
    #: is a check that cannot fail, which is worse than no check (#468).
    count_grain: CountGrain = CountGrain.ASSET

    @property
    def total_principal_balance(self) -> Decimal:
        """Sum of every parsed asset's principal balance."""
        return sum((a.principal_balance for a in self.assets), Decimal("0"))

    def by_identifier(self) -> dict[str, CollateralAsset]:
        """The schedule keyed by facility identifier."""
        return {a.identifier: a for a in self.assets}


class ScheduleParseInput(BaseInput):
    """Governance input record for the envelope wrapper."""

    period_label: str
    text: str


class ScheduleReconciliationError(ValueError):
    """Raised when a parsed schedule does not tie out to the report's own totals.

    This is the enforced boundary. The parser refuses rather than returning a
    tape that downstream consumers would treat as authoritative.
    """

    def __init__(self, reconciliation: ScheduleReconciliation, defects: ScheduleDefects):
        self.reconciliation = reconciliation
        self.defects = defects
        lines = [f"{c.name}: expected {c.expected}, got {c.actual}" for c in reconciliation.failures]
        if defects.total:
            lines.append(f"{defects.total} parse defect(s): {'; '.join(defects.notes[:5])}")
        super().__init__(
            "collateral schedule does not reconcile to the report's own stated "
            "totals — refusing to return it. " + " | ".join(lines)
        )


# ===========================================================================
# Seam 1 — PDF → line-oriented text
# ===========================================================================


def extract_report_lines(pdf_bytes: bytes) -> str:
    """Extract a trustee report's text as page-delimited *visual lines*.

    ``pypdf``'s plain ``extract_text()`` is unusable on these reports: the tables
    are drawn rotated, so it returns one undelimited blob per page and every row
    boundary is lost. The ``visitor_text`` callback reports one run per visual
    line together with its text matrix; sorting by the matrix's cross-axis
    offset restores the page's true top-to-bottom line order, which is what puts
    a wrapped continuation line immediately after the row it belongs to.

    Returns text with a ``--- page N ---`` marker before each page's lines.
    """
    from pypdf import PdfReader  # imported lazily: only the live seam needs it

    import io

    reader = PdfReader(io.BytesIO(pdf_bytes))
    out: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        runs: list[tuple[float, str]] = []

        def visit(text: str, cm: Any, tm: Any, font: Any, size: Any) -> None:
            if text and text.strip():
                # A run *is* one visual line, so any line-break character inside
                # it is stray (the reports carry a few CRLFs in their prose
                # pages). Collapsing them to a space keeps the emitted text
                # identical however the fixture is later read back — text mode
                # would otherwise translate them into extra lines and split a
                # row that the in-memory string keeps whole.
                runs.append((round(float(tm[4]), 1), _LINE_BREAKS.sub(" ", text).strip()))

        page.extract_text(visitor_text=visit)
        runs.sort(key=lambda run: run[0])
        out.append(f"--- page {index} ---")
        out.extend(text for _, text in runs)
    return "\n".join(out) + "\n"


def fetch_report_text(url: str, *, timeout: int = 60) -> str:
    """Fetch a trustee report PDF and extract its text. Network; not unit-tested."""
    request = urllib.request.Request(url, headers={"User-Agent": "loanwhiz/0.1"})
    data = urllib.request.urlopen(request, timeout=timeout).read()  # noqa: S310
    return extract_report_lines(data)


def write_fixture(url: str, destination: str | Path) -> Path:
    """Regenerate one committed text fixture from its source report URL."""
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fetch_report_text(url), encoding="utf-8")
    return path


# ===========================================================================
# Page/section splitting
# ===========================================================================


def _split_pages(text: str) -> list[list[str]]:
    """Split extracted text into per-page line lists."""
    pages: list[list[str]] = []
    current: list[str] | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if _PAGE_MARKER_RE.match(line.strip()):
            current = []
            pages.append(current)
            continue
        if current is None:
            continue
        if line.strip():
            current.append(line.strip())
    return pages


def _page_section(lines: list[str], layout: DocumentLayout) -> str | None:
    """Return the section title a page belongs to, located by header text.

    Deliberately header-driven: page numbers move between reports (they are
    already wrong by one in this issue's own description), section titles do
    not.
    """
    # The table of contents names every section on one page; its entries are
    # dot-leadered, so excluding those lines keeps it from being classified as
    # whichever section it happens to list first.
    head = " ".join(line for line in lines[:12] if ". . ." not in line)
    # A reflowed page need not open with its title: Contego's Asset Information
    # IV pages begin with a column-major stack of lot numbers, so the title is
    # nowhere near the first twelve lines and the page would route to no
    # section at all — 256 lot rows read as zero, which is the silence #494 is
    # about. The row-major line carries the table's own title at its head, so
    # that is where a reflowed page is asked.
    reflowed = _reflowed_line(lines)
    if reflowed is not None:
        head = f"{head} {reflowed[:_REFLOWED_TITLE_WINDOW]}"
    for title in layout.titles:
        if title in head:
            return title
    return None


def _pages_for(
    pages: list[list[str]], layout: DocumentLayout, section_key: str
) -> list[list[str]]:
    """Every page belonging to one section role, in document order."""
    title = layout.title(section_key)
    return [lines for lines in pages if _page_section(lines, layout) == title]


def _published_pages(
    pages: list[list[str]], layout: DocumentLayout, section_key: str
) -> list[list[str]] | None:
    """A section's pages, or ``None`` when this family does not publish it.

    ``None`` and ``[]`` are deliberately different answers, and keeping them
    apart is the whole of #494: a section the administrator does not print is
    **absent**, while a section that routed to no pages is a parse that found
    nothing. Collapsing the two is how a missing title reads as a clean
    reconciliation. Callers must handle ``None`` by recording the declared
    reason, never by treating it as an empty table.
    """
    if not layout.publishes(section_key):
        return None
    return _pages_for(pages, layout, section_key)


def _report_header(pages: list[list[str]]) -> tuple[str | None, str | None]:
    """The deal name and reporting date a report states about itself.

    One implementation for both sides of the document — the collateral schedule
    and the liability summary describe the same report, so a divergence between
    two copies of this would be a report whose two halves disagree about which
    period they are.
    """
    deal_name = pages[0][0] if pages and pages[0] else None
    for lines in pages[:5]:
        for line in lines:
            match = _REPORTING_DATE_RE.search(line)
            if match:
                return deal_name, match.group(1)
    return deal_name, None
def _report_document_name(deal_name: str | None, period_label: str) -> str:
    """The citation document string for one report."""
    return (
        f"{deal_name} — Monthly Trustee Report ({period_label})"
        if deal_name
        else f"Monthly Trustee Report ({period_label})"
    )


def _is_furniture(line: str, layout: DocumentLayout) -> bool:
    """True for repeated page furniture (footers, banners, headers).

    Safe to ask *before* the data-row question only because every data row in
    this document opens with an asset identifier, which no furniture prefix can
    produce — the family states that as
    :attr:`~loanwhiz.domain.trustee_report_registry.FurnitureOrder.FURNITURE_FIRST`
    rather than leaving it an unwritten assumption. Where a row's opening text
    *can* look like furniture, the order must invert (#494); the Note Valuation
    Report is that case.
    """
    if line.startswith(layout.furniture_prefixes):
        return True
    return any(title in line for title in layout.titles)


def _data_rows(pages: list[list[str]], layout: DocumentLayout) -> list[list[str]]:
    """Group a section's lines into rows: an identifier line plus continuations.

    A line starting with an asset identifier opens a row; any following line
    that does not is a **continuation** of it — the wrapped remainder of that
    row's issuer name, facility name or country. This is the hazard the issue
    named, and it is handled structurally here rather than by heuristics
    downstream.
    """
    rows: list[list[str]] = []
    for lines in pages:
        current: list[str] | None = None
        for line in lines:
            if _is_furniture(line, layout):
                continue
            if IDENTIFIER_RE.match(line):
                current = [line]
                rows.append(current)
            elif current is not None:
                current.append(line)
        # A continuation cannot cross a page boundary in these reports.
    return rows


# ===========================================================================
# Reflowed pages — re-cutting rows out of a row-major line
# ===========================================================================

#: How many identifiers one line must carry before it is read as the page's
#: whole table rather than as a single row. Three separates a row-major line
#: (dozens) from a per-row line (one) with margin to spare.
_REFLOW_MIN_IDENTIFIERS = 3


def _reflowed_line(
    lines: list[str], evidence: re.Pattern[str] = _EMBEDDED_IDENTIFIER_RE
) -> str | None:
    """The page's row-major line, or ``None`` when the page has no such line.

    Geometry is declared per **family** and detected per **page**, because a
    document is not uniform. Contego's ``Interest Accrual Detail`` runs to
    seven pages: six extract as one row-major line, and one extracts as one row
    per line. A parser that trusted the family's declaration alone would read
    six pages and silently skip the seventh's forty rows — the count would come
    in short with the par unchanged, which is exactly the shape #468 was.

    The declaration says which geometry to *expect*; the page says which it is.

    *evidence* is what a repetition of this section's rows looks like: an asset
    identifier on the detail pages, and the numeric tail of an aggregate row on
    the concentration pages, which carry no identifiers at all. Passing it in
    keeps one detection rule for every section rather than a per-section guess
    at which line is the table.
    """
    if not lines:
        return None
    candidate = max(lines, key=len)
    found = evidence.findall(candidate)
    return candidate if len(found) >= _REFLOW_MIN_IDENTIFIERS else None


def _repeated_header(texts: list[str]) -> str:
    """The column-header run a section repeats at the head of every page.

    A row-major line opens with the section's own printed column header, so the
    first row's description would otherwise arrive with the header glued to its
    front. The header is *derived* rather than declared: it is the longest
    prefix every one of the section's pages shares, which is what a repeated
    header is. Truncated at the first identifier so a section whose pages
    happen to open with the same obligor cannot swallow a row.

    Returns ``""`` for a single-page section, where repetition cannot show what
    is header and what is data. Callers that need descriptions treat that as a
    refusal rather than guessing; callers that need only the anchored tail (the
    CCC cross-check, the accrual census) are unaffected, since the tail is
    matched from each identifier forward and never from the head of the line.
    """
    if len(texts) < 2:
        return ""
    prefix = os.path.commonprefix(texts)
    first_id = _EMBEDDED_IDENTIFIER_RE.search(texts[0])
    if first_id is not None:
        prefix = prefix[: first_id.start()]
    return prefix


def _section_row_texts(pages: list[list[str]], layout: DocumentLayout) -> list[str]:
    """One scannable text per page for a section, whatever geometry it came in.

    A reflowed page contributes its row-major line with the repeated header
    removed; any other page contributes each of its non-furniture lines. Both
    are then scanned identically — identifier, then the row's own anchored tail
    — so the two geometries share one parse path instead of forking it.
    """
    reflowed = [line for lines in pages if (line := _reflowed_line(lines)) is not None]
    header = _repeated_header(reflowed)
    texts: list[str] = []
    for lines in pages:
        line = _reflowed_line(lines)
        if line is not None:
            texts.append(line[len(header) :] if header and line.startswith(header) else line)
            continue
        # A page of this document that did not reflow still wraps: Contego's
        # September accrual page splits one row after its identifier, leaving
        # the period and balance on the next line. Joining the page's lines
        # bridges that, and is safe because a row is evidenced by its own
        # anchored tail — furniture and stray cells produce none, so no
        # furniture filter takes part in finding rows here at all. That is the
        # data-first ordering (#494), and it is what a prefix list cannot do.
        texts.append(" ".join(lines))
    return texts


def _cut_rows(
    text: str, tail: re.Pattern[str]
) -> list[tuple[str, str, re.Match[str]]]:
    """Cut one text into ``(identifier, description, tail match)`` triples.

    The identifier sits mid-row here, so on its own it cannot say where a row
    begins or ends: an identifier followed by text that is not this section's
    column shape is not a row of this section. The **tail** decides. A row runs
    from wherever the previous row ended to the end of its own tail, and the
    description is what precedes its identifier — so a row whose tail does not
    match is not silently absorbed into its neighbour. It yields no row, and
    the count then comes in short against the report's own stated one, which is
    the whole point of having a count in the oracle at all (#468).
    """
    rows: list[tuple[str, str, re.Match[str]]] = []
    cursor = 0
    for identifier in _EMBEDDED_IDENTIFIER_RE.finditer(text):
        if identifier.start() < cursor:
            continue
        matched = tail.match(text, identifier.end())
        if matched is None:
            continue
        rows.append(
            (identifier.group(1), text[cursor : identifier.start()].strip(), matched)
        )
        cursor = matched.end()
    return rows


def _reflowed_rows(
    pages: list[list[str]], layout: DocumentLayout, tail: re.Pattern[str]
) -> list[tuple[str, str, re.Match[str]]]:
    """Every ``(identifier, description, tail match)`` a section yields."""
    rows: list[tuple[str, str, re.Match[str]]] = []
    for text in _section_row_texts(pages, layout):
        rows.extend(_cut_rows(text, tail))
    return rows


# ---------------------------------------------------------------------------
# BNY Mellon's column grammars
# ---------------------------------------------------------------------------

#: A rating cell, including the ``***`` this administrator prints for "no
#: rating from this agency". ``***`` is not a rating and never becomes one: it
#: reaches :class:`CollateralAsset` as ``None``, because an unrated asset and
#: an asset the report declines to rate are the same fact and neither is a
#: rating that happens to sort last.
_BNY_RATING = r"\*\*\*|[A-Z]{1,3}[+-]?"
_BNY_RATE_PCT = r"\*\*\*|\d+\.\d{2}"

#: ``Asset Information I``: ``<Loan Type> <Market Price> <Par/Quantity>
#: <Principal Balance> <Unfunded Amount> <Security Level> <Maturity Date>``.
#:
#: ``Loan Type`` is **optional**, and that is a fact about the document rather
#: than defensive coding: the report prints the cell for loans (``Term Loan``,
#: ``Delayed Draw Loan``) and emits no cell at all for its 28 bond positions.
#: Requiring it would drop every bond — #468's failure exactly, reached through
#: a column rather than through an identifier pattern.
#:
#: ``Market Price`` is a **price per 100 of par** (75.0–101.652 across this
#: report), not a value. It is captured under a name that says so; #470 is the
#: correction of a column that looked like a market value and was a price,
#: wrong by four orders of magnitude into the OC ratio.
_BNY_PART_I_TAIL = re.compile(
    rf"(?:{_S}(?P<asset_type>[A-Za-z][A-Za-z ]*?))?{_S}"
    rf"(?P<market_price_pct>\d+\.\d{{4}}){_S}"
    rf"(?P<par>{MONEY}){_S}(?P<principal_balance>{MONEY}){_S}(?P<unfunded>{MONEY}){_S}"
    rf"(?P<seniority>Senior Secured|Senior Unsecured|Second Lien|Subordinated|Mezzanine)"
    rf"{_S}(?P<maturity_date>\d{{2}}-[A-Z][a-z]{{2}}-\d{{4}})"
)

#: ``Asset Information II``: ``<Issue CCY> <Principal Balance> <DIP/Corp
#: Rescue> <Cov-Lite> <Loan PIK> <Lien Type> <Avg Life> <Country>``.
#:
#: The tail deliberately **stops at Avg Life** and does not read ``Country``.
#: Country is the row's last cell, free text, with the next row's obligor name
#: immediately after it and no delimiter between them — and BNY publishes no
#: country table, so there is no vocabulary to resolve the boundary against
#: (that absence is declared on the family). Reading it here would mean
#: guessing where a country ends. ``Asset Information IV`` prints the same
#: country followed by a rating cell, which bounds it, so country is taken
#: from there instead.
_BNY_PART_II_TAIL = re.compile(
    rf"{_S}(?P<currency>[A-Z]{{3}}){_S}(?P<principal_balance>{MONEY})"
    rf"(?P<flags>(?:{_S}(?:Yes|-)){{3}}){_S}"
    rf"(?:(?P<lien_type>[A-Z][A-Za-z ]*?Lien|Unsecured){_S})?(?P<avg_life>\d+\.\d{{2}})"
)

#: ``Asset Information IV``: lot level, ``<Country> <Country Rating S&P>
#: <Fitch>`` then eight flag cells. The country is bounded on its right by a
#: rating, which is what makes it readable here and not in Part II.
_BNY_PART_IV_TAIL = re.compile(
    rf"{_S}(?P<country>[A-Z][A-Za-z .'\-]*?){_S}(?P<country_sp>{_BNY_RATING})"
    rf"{_S}(?P<country_fitch>{_BNY_RATING})"
    rf"(?P<flags>(?:{_S}(?:Yes|-)){{8}})"
)

#: ``Interest Accrual Detail``: ``<Payment Period> <Principal Balance>``, which
#: is enough of an anchored tail to prove a row. This section is the population
#: BNY's aggregate tables count — one record per asset per rate contract, so an
#: asset accruing under two contracts is two rows here and two in every
#: ``# of Assets`` column.
#:
#: The period cell is matched as *any* single hyphenated word rather than
#: against the five values these two reports happen to print (``Quarterly``,
#: ``Semi-Annual``, ``Monthly``, ``Bi-Monthly``, ``Annually``). A closed list
#: here would silently drop the rows of any period outside it, and dropping
#: rows from the very population the count oracle is measured against is the
#: one failure this section exists to catch. The money cell after it is what
#: makes the tail evidence rather than a guess.
_BNY_ACCRUAL_TAIL = re.compile(
    rf"{_S}(?P<period>[A-Za-z][A-Za-z-]*){_S}(?P<principal_balance>{MONEY})"
)


def _bny_accrual_tail(pages: list[list[str]], layout: DocumentLayout) -> re.Pattern[str]:
    """The accrual tail, with the balance cell made optional by the document.

    Contego's September report prints one accrual row — Rubix Group's extended
    add-facility — carrying a payment period and **no balance cell at all**.
    Requiring the balance drops that row, and a row dropped out of the very
    population the count oracle measures against is the one failure this
    section exists to catch: the count came in at 214 against a stated 215
    while par was untouched, which is #468's shape exactly.

    So the period vocabulary is read off the document first — every word this
    report uses in a period cell that *does* carry a balance — and the second
    pass admits those same words with the balance optional. The vocabulary is
    evidence from the report rather than a list of the five values these two
    happen to print, and the balance stays required for any word the report has
    not already shown to be a period. A row admitted with no balance is
    recorded as a defect, never silently completed with a zero.
    """
    vocabulary: set[str] = set()
    for text in _section_row_texts(pages, layout):
        for _, _, matched in _cut_rows(text, _BNY_ACCRUAL_TAIL):
            vocabulary.add(matched.group("period"))
    if not vocabulary:
        return _BNY_ACCRUAL_TAIL
    alternation = "|".join(re.escape(word) for word in sorted(vocabulary, key=len, reverse=True))
    return re.compile(
        rf"{_S}(?P<period>{alternation})(?:{_S}(?P<principal_balance>{MONEY}))?(?=\s|$)"
    )

#: ``CCC Obligations``: ``<S&P Rating> <Fitch Rating> <Market Price> <Market
#: Value> <Principal Balance>``. Read only as a **unit cross-check**: this is
#: the one BNY table printing a price and a value side by side for the same
#: asset, so ``value == par * price / 100`` is checkable against the document
#: rather than against the parser's own arithmetic (#470).
_BNY_CCC_TAIL = re.compile(
    rf"{_S}(?P<sp_rating>{_BNY_RATING}){_S}(?P<fitch_rating>{_BNY_RATING}){_S}"
    rf"(?P<market_price_pct>\d+\.\d{{4}}){_S}(?P<market_value>{MONEY}){_S}"
    rf"(?P<principal_balance>{MONEY})"
)


def _vocabulary_alternation(labels: list[str]) -> str:
    """A regex alternation over free-text labels, longest first.

    The labels come from the report's own concentration tables — the closed
    vocabulary its detail pages draw on, which is why a two-pass parse can
    resolve a free-text column that has no delimiter after it. Whitespace
    inside a label is matched as ``\\s+`` because the extraction preserves the
    double spaces a wrapped cell leaves behind (``Diversified  telecommunication
    services``).
    """
    return "|".join(
        r"\s+".join(re.escape(word) for word in label.split())
        for label in sorted(labels, key=len, reverse=True)
    )


def _bny_part_iii_tail(aggregates: ReportAggregates) -> re.Pattern[str] | None:
    """``Asset Information III``'s tail, built from the report's own vocabularies.

    Both industry columns are free text with no delimiter after them, so the
    boundary is resolved against the closed vocabulary the report's own
    concentration tables enumerate — the reason pass 1 runs first. With no
    vocabulary there is nothing to resolve against and the section is refused
    rather than cut at a guess.
    """
    sp = [bucket.label for bucket in aggregates.sp_industry]
    fitch = [bucket.label for bucket in aggregates.fitch_industry]
    if not sp or not fitch:
        return None
    return re.compile(
        rf"{_S}(?P<principal_balance>{MONEY}){_S}(?P<sp_rating>{_BNY_RATING}){_S}"
        rf"(?P<sp_recovery>{_BNY_RATE_PCT}){_S}(?P<sp_industry>{_vocabulary_alternation(sp)})"
        rf"{_S}(?P<fitch_rating>{_BNY_RATING}){_S}"
        rf"(?P<fitch_recovery>{_BNY_RATE_PCT}){_S}"
        rf"(?P<fitch_industry>{_vocabulary_alternation(fitch)})"
    )


#: The asset-type group labels ``Asset Information I`` prints between its loan
#: block and its bond block. They sit in the row-major line immediately before
#: the first row of each block, so the first row of a block carries one on the
#: front of its description.
_BNY_GROUP_LABELS = ("Loan ", "Bond ")


def _split_description(description: str) -> tuple[str | None, str]:
    """Split BNY's ``<obligor> - <facility>`` description on its first dash.

    Unlike U.S. Bank's, this administrator prints a separator, so the split is
    exact rather than recovered — the obligor is what precedes the first
    ``" - "``. A description with no separator keeps the whole string as the
    facility name and reports no obligor rather than inventing one.

    A leading asset-type group label is removed first, and only when removing
    it leaves a separator behind: an obligor genuinely named ``Loan …`` keeps
    its name, because the strip has to earn itself on the row it is applied to.
    """
    cleaned = _collapse(description)
    for label in _BNY_GROUP_LABELS:
        if cleaned.startswith(label) and " - " in cleaned[len(label) :]:
            cleaned = cleaned[len(label) :]
            break
    obligor, separator, facility = cleaned.partition(" - ")
    if not separator:
        return None, cleaned
    return obligor.strip() or None, facility.strip()


# ===========================================================================
# Pass 1 — the report's own aggregates
# ===========================================================================


def _parse_aggregate_table(
    pages: list[list[str]], layout: DocumentLayout
) -> tuple[list[AggregateBucket], Decimal | None, int | None]:
    """Parse a concentration/stratification table into buckets plus its total."""
    buckets: list[AggregateBucket] = []
    total_balance: Decimal | None = None
    total_count: int | None = None
    for lines in pages:
        for line in lines:
            if _is_furniture(line, layout):
                continue
            if line.startswith("Aggregate "):
                match = _MONEY_RE.search(line)
                if match:
                    total_balance = Decimal(match.group(0).replace(",", ""))
                continue
            match = _AGGREGATE_ROW_RE.match(line)
            if not match:
                continue
            label = match.group("label").strip()
            balance = Decimal(match.group("balance").replace(",", ""))
            percent = Decimal(match.group("percent"))
            count = int(match.group("count"))
            if not label:
                # The table's own total row: no label, 100.00%.
                total_balance = balance
                total_count = count
                continue
            buckets.append(
                AggregateBucket(label=label, balance=balance, percent=percent, count=count)
            )
    return buckets, total_balance, total_count


# ---------------------------------------------------------------------------
# BNY Mellon's aggregate tables
# ---------------------------------------------------------------------------

#: One reflowed aggregate row: ``<label> <count> <balance> <percent>%``. Note
#: the column order — this administrator states the **count before** the
#: balance where the other states it last, which is exactly why the order is
#: read off the family's own table rather than assumed from the other family's
#: habits (#480). The label is whatever precedes the count, so the split is
#: unambiguous left to right even for a label ending in a digit.
_BNY_AGGREGATE_TAIL = re.compile(
    rf"(?P<count>\d{{1,4}}){_S}(?P<balance>{MONEY}){_S}(?P<percent>\d+\.\d{{2}})%"
)

#: The table's own total row, which this administrator prints two ways: with a
#: ``Total:`` label and no percentage on the rating tables, and as a bare
#: ``<count> <balance> 100.00%`` row on the industry tables.
_BNY_AGGREGATE_TOTAL = re.compile(rf"Total:{_S}(?P<count>\d{{1,4}}){_S}(?P<balance>{MONEY})")


def _marker_pattern(marker: str) -> re.Pattern[str]:
    """A regex matching one table's sub-header fingerprint in a reflowed line.

    The family records each marker whitespace-stripped and upper-cased, because
    that is the only form stable across the extraction's stray double spaces.
    Matching it back against real text therefore means allowing whitespace
    between every character, which is what this rebuilds.
    """
    return re.compile(r"\s*".join(re.escape(character) for character in marker), re.IGNORECASE)


def _table_segment(text: str, marker: str, siblings: list[str]) -> str | None:
    """The slice of a reflowed line belonging to one of several tables on it.

    BNY prints two industry tables under one ``Industry Concentrations`` title
    and three under ``Rating Concentrations``, so the printed title cannot route
    them and the pair (title, sub-header marker) is what is unique — the
    distinction #533 grew ``section_table_markers`` for. A segment runs from its
    own marker to whichever sibling marker comes next, or to the end.
    """
    found = _marker_pattern(marker).search(text)
    if found is None:
        return None
    end = len(text)
    for sibling in siblings:
        if sibling == marker:
            continue
        other = _marker_pattern(sibling).search(text, found.end())
        if other is not None:
            end = min(end, other.start())
    return text[found.end() : end]


def _trim_to_cell(label: str, cells: set[str]) -> str:
    """Strip a column-header run off the front of a reflowed table's first label.

    A row-major line opens with the table's own column header (``# of Assets
    Principal % of APB``), so the first row's label arrives with that glued to
    its front while every later label is clean. The page's *other* rendering
    settles it: the same table is also emitted as a column-major stack of
    single cells, so each true label appears somewhere on the page as a line of
    its own.

    That stack is used here only to **bound one label**, never zipped back
    against the row-major line — zipping is what a single blank cell desyncs
    (#533). A label already present as a cell is returned untouched, so the
    repair cannot fire on a label that needs none.
    """
    if label in cells or not label:
        return label
    words = label.split(" ")
    for start in range(1, len(words)):
        candidate = " ".join(words[start:])
        if candidate in cells:
            return candidate
    return label


def _parse_reflowed_aggregate_table(
    pages: list[list[str]], layout: DocumentLayout, section_key: str
) -> tuple[list[AggregateBucket], Decimal | None, int | None]:
    """Parse one concentration table out of a row-major page.

    Same two answers as the per-line reader — the buckets and the table's own
    stated total — reached the same way rows are reached everywhere on this
    geometry: match the row's anchored numeric tail, and take the label as what
    precedes it.
    """
    marker = layout.section_table_markers.get(section_key)
    siblings = list(layout.section_table_markers.values())
    buckets: list[AggregateBucket] = []
    total_balance: Decimal | None = None
    total_count: int | None = None
    for lines in pages:
        text = _reflowed_line(lines, _BNY_AGGREGATE_TAIL)
        if text is None:
            continue
        segment = _table_segment(text, marker, siblings) if marker else text
        if segment is None:
            continue
        stated = _BNY_AGGREGATE_TOTAL.search(segment)
        if stated is not None:
            total_count = int(stated.group("count"))
            total_balance = Decimal(stated.group("balance").replace(",", ""))
            segment = segment[: stated.start()]
        cells = {_collapse(line) for line in lines if line is not text}
        cursor = 0
        for matched in _BNY_AGGREGATE_TAIL.finditer(segment):
            label = _trim_to_cell(_collapse(segment[cursor : matched.start()]), cells)
            cursor = matched.end()
            balance = Decimal(matched.group("balance").replace(",", ""))
            percent = Decimal(matched.group("percent"))
            count = int(matched.group("count"))
            if not label:
                # The bare total row the industry tables print: no label, and
                # 100.00% of the balance by construction.
                total_balance = balance
                total_count = count
                continue
            buckets.append(
                AggregateBucket(label=label, balance=balance, percent=percent, count=count)
            )
    return buckets, total_balance, total_count


def _parse_profile_tests(pages: list[list[str]], layout: DocumentLayout) -> list[ProfileTest]:
    """Parse the Portfolio Profile Tests page into stated numerator/denominator.

    Each test prints ``<name><result%><numerator><denominator><Min|Max><trigger%>``
    followed by ``Pass``/``Fail``; the name may wrap onto the following line.
    Only the name, numerator and denominator are read here — they are what the
    flag reconciliation needs.
    """
    tests: list[ProfileTest] = []
    pattern = re.compile(
        rf"^(?P<name>.*?){_S}(?P<result>\d+\.\d{{2}})%{_S}(?P<numerator>{MONEY}){_S}"
        rf"(?P<denominator>{MONEY}){_S}(?:Minimum|Maximum)"
    )
    for lines in pages:
        for line in lines:
            if _is_furniture(line, layout):
                continue
            match = pattern.match(line)
            if not match:
                continue
            name = match.group("name").strip()
            if not name:
                continue
            tests.append(
                ProfileTest(
                    name=name,
                    numerator=Decimal(match.group("numerator").replace(",", "")),
                    denominator=Decimal(match.group("denominator").replace(",", "")),
                )
            )
    return tests


def _aggregate_table(
    pages: list[list[str]], layout: DocumentLayout, section_key: str
) -> tuple[list[AggregateBucket], Decimal | None, int | None]:
    """One concentration table, read the way this family's pages are laid out."""
    if layout.row_geometry is RowGeometry.REFLOWED_ROWS:
        return _parse_reflowed_aggregate_table(pages, layout, section_key)
    return _parse_aggregate_table(pages, layout)


def _parse_aggregates(pages: list[list[str]], layout: DocumentLayout) -> ReportAggregates:
    """Read every summary table the report publishes about itself."""
    aggregates = ReportAggregates()

    country_pages = _published_pages(pages, layout, SECTION_COUNTRY)
    if country_pages is None:
        # Declared absent with a reason (#494), not empty. Recorded so the
        # oracle can say "this administrator publishes no country table"
        # instead of quietly running one fewer check than it did last month.
        aggregates.unpublished_sections[SECTION_COUNTRY] = layout.unpublished_reason(
            SECTION_COUNTRY
        )
    else:
        country, total, count = _aggregate_table(country_pages, layout, SECTION_COUNTRY)
        aggregates.country = country
        aggregates.aggregate_principal_balance = total
        aggregates.asset_count = count

    aggregates.sp_industry, industry_total, industry_count = _aggregate_table(
        _pages_for(pages, layout, SECTION_SP_INDUSTRY), layout, SECTION_SP_INDUSTRY
    )
    aggregates.fitch_industry, _, _ = _aggregate_table(
        _pages_for(pages, layout, SECTION_FITCH_INDUSTRY), layout, SECTION_FITCH_INDUSTRY
    )
    rating, rating_total, rating_count = _aggregate_table(
        _pages_for(pages, layout, SECTION_SP_RATING), layout, SECTION_SP_RATING
    )
    if aggregates.aggregate_principal_balance is None:
        aggregates.aggregate_principal_balance = industry_total
    if aggregates.asset_count is None:
        aggregates.asset_count = industry_count
    aggregates.sp_rating = rating
    if aggregates.aggregate_principal_balance is None:
        aggregates.aggregate_principal_balance = rating_total
    if aggregates.asset_count is None:
        aggregates.asset_count = rating_count

    aggregates.profile_tests = _parse_profile_tests(
        _pages_for(pages, layout, SECTION_PROFILE_TESTS), layout
    )

    # A table whose rows do not sum to the aggregate balance printed on its own
    # page cannot be a balance oracle. This is checkable from the document alone,
    # with no reference to the parse — which is what makes excluding it honest
    # rather than convenient.
    stated = aggregates.aggregate_principal_balance
    if stated is not None:
        for table in ("country", "sp_industry", "fitch_industry", "sp_rating"):
            buckets: list[AggregateBucket] = getattr(aggregates, table)
            if buckets and sum(b.balance for b in buckets) != stated:
                aggregates.inconsistent_tables.append(table)
    return aggregates


# ===========================================================================
# Pass 2 — the per-asset detail sections
# ===========================================================================


def _row_text(row: list[str]) -> tuple[str, str, str]:
    """Split a row into its identifier, its first line, and its continuation.

    A wrapped row is a *matrix*, not a string: the first line holds the first
    line of every cell, and the continuation holds the remainder of each cell
    that overflowed — **in column order**, concatenated with no separator. So a
    row whose facility name and Fitch industry both wrap yields one continuation
    reading ``<name remainder><industry remainder>``. Consumers therefore walk
    the columns left to right, taking each cell's remainder off the front of the
    continuation as they go, rather than appending the continuation to the end
    of the line (which would splice a name fragment in after the country).
    """
    match = IDENTIFIER_RE.match(row[0])
    assert match is not None  # guaranteed by _data_rows
    identifier = match.group(1)
    head, cont = _advance(row[0][len(identifier) :], "".join(row[1:]))
    return identifier, head, cont


def _advance(head: str, cont: str) -> tuple[str, str]:
    """Promote the continuation into the head once the head is exhausted.

    Normally a row's first line carries the first line of *every* cell, so it is
    never exhausted mid-row and this is a no-op. It fires on a row the renderer
    broke apart — a facility name containing a tab character does this — where
    the identifier lands alone on its line and the cells follow on the next. In
    that shape the cells really are in linear order, so advancing is exactly
    right; in the wrapped shape the head still has cells left, so it never
    triggers and the column-order splice is preserved.
    """
    if head.strip():
        return head, cont
    return cont, ""


def _join(*fragments: str) -> str:
    """Rejoin a wrapped cell's fragments, restoring the space the wrap ate.

    A text cell wraps at a space, and the renderer emits neither that space nor
    (once each visual line is stripped) any trailing one — so ``ASMODEE GROUP``
    / ``AB FLOATING`` / ``12/15/2029`` would rejoin as one run-on token. A single
    space is therefore inserted at a junction where neither side already has
    whitespace, and never where one does: ``AI Sirona T/L B3 `` +
    ``(Zentiva) (3/24)`` must not gain a second space.
    """
    joined = ""
    for fragment in fragments:
        if not fragment:
            continue
        if joined and not joined[-1].isspace() and not fragment[0].isspace():
            joined += " "
        joined += fragment
    return joined.strip()


def _squash(text: str) -> str:
    """Text with every whitespace character removed.

    All free-text matching runs on squashed text. That is what makes one parse
    path cover both renderings *and* both wrap behaviours: whitespace at a line
    junction is simply not emitted (a country renders as ``United`` / ``Kingdom``
    and rejoins as ``UnitedKingdom``), so any comparison sensitive to it would
    have to special-case every junction.
    """
    return re.sub(r"\s+", "", text)


def _collapse(text: str) -> str:
    """Text with runs of whitespace collapsed to one space, ends trimmed.

    Distinct from :func:`_squash`, deliberately. Squashing is right for
    *matching* free text across a wrap that swallowed the space; it is wrong
    for a label the parse then hands on, because a bucket label has to stay
    comparable with the same words as they appear in a detail row.
    """
    return re.sub(r"\s+", " ", text).strip()


def _cut_after(text: str, count: int) -> int | None:
    """Index in ``text`` just past its ``count``-th non-whitespace character."""
    if count <= 0:
        return 0
    seen = 0
    for index, char in enumerate(text):
        if not char.isspace():
            seen += 1
            if seen == count:
                return index + 1
    return None


def _cut_before(text: str, count: int) -> int | None:
    """Index in ``text`` starting its final ``count`` non-whitespace characters."""
    if count <= 0:
        return len(text)
    seen = 0
    for index in range(len(text) - 1, -1, -1):
        if not text[index].isspace():
            seen += 1
            if seen == count:
                return index
    return None


def _consume(head: str, cont: str, term: str) -> tuple[str, str] | None:
    """Consume ``term`` from ``head``, following it into ``cont`` if it wrapped.

    Returns the remaining ``(head, cont)``, or ``None`` when ``term`` is not
    there. A wrapped cell's first part ends ``head``'s share of it and the
    remainder opens ``cont``; the largest such split is the true wrap point.
    """
    goal, in_head = _squash(term), _squash(head)
    if in_head.startswith(goal):
        cut = _cut_after(head, len(goal))
        return (head[cut:], cont) if cut is not None else None
    in_cont = _squash(cont)
    for size in range(len(goal) - 1, 0, -1):
        if not in_head.startswith(goal[:size]):
            continue
        if not in_cont.startswith(goal[size:]):
            continue
        head_cut = _cut_after(head, size)
        cont_cut = _cut_after(cont, len(goal) - size)
        if head_cut is None or cont_cut is None:
            continue
        return head[head_cut:], cont[cont_cut:]
    return None


def _consume_any(
    head: str, cont: str, vocabulary: list[str]
) -> tuple[str, str, str] | None:
    """Consume the longest vocabulary term present, wrap-tolerant.

    Returns ``(term, head, cont)``. The vocabulary comes from the report's own
    concentration tables, which is what makes an undelimited free-text column
    splittable at all.
    """
    for term in vocabulary:
        taken = _consume(head, cont, term)
        if taken is not None:
            return term, taken[0], taken[1]
    return None


def _parse_part_iii(
    pages: list[list[str]], layout: DocumentLayout, defects: ScheduleDefects
) -> dict[str, dict[str, Any]]:
    """Part III → the authoritative facility name plus the eight boolean flags.

    Part III is parsed first because it is the only section whose free-text
    column has a delimiter on both sides: the eight flag cells are each exactly
    ``-`` or ``Yes``, so the facility name is whatever precedes them. Every
    other section's name/balance and issuer/name boundaries are resolved against
    the name recovered here.
    """
    out: dict[str, dict[str, Any]] = {}
    for row in _data_rows(pages, layout):
        identifier = IDENTIFIER_RE.match(row[0]).group(1)  # type: ignore[union-attr]
        segments = [row[0][len(identifier) :], *row[1:]]
        located = None
        for index, segment in enumerate(segments):
            found = _PART_III_FLAGS_RE.search(segment)
            if found is not None:
                located = (index, found)
        if located is None:
            defects.record("unrecognised_rows", f"Part III flags unreadable for {identifier}")
            continue
        index, flags_match = located
        cells = _FLAG_RE.findall(flags_match.group(1))
        if len(cells) != len(PART_III_FLAGS):
            defects.record("unrecognised_rows", f"Part III flag count for {identifier}")
            continue
        # The name is the only free-text column here, so it is every other
        # fragment of the row, in order, with the flag cells excised.
        name = _join(
            *segments[:index],
            segments[index][: flags_match.start()],
            *segments[index + 1 :],
        )
        out[identifier] = {
            "facility_name": name,
            "flags": {key: cell == "Yes" for key, cell in zip(PART_III_FLAGS, cells)},
        }
    return out


def _parse_part_ii(
    pages: list[list[str]],
    layout: DocumentLayout,
    names: dict[str, dict[str, Any]],
    aggregates: ReportAggregates,
    defects: ScheduleDefects,
) -> dict[str, dict[str, Any]]:
    """Part II → balance, S&P/Fitch industry, currency and country.

    The columns are undelimited, so each boundary is resolved against something
    the report itself states: the facility name from Part III, then the industry
    and country vocabularies from the concentration tables (pass 1).
    """
    sp_vocabulary = aggregates.vocabulary("sp_industry")
    fitch_vocabulary = aggregates.vocabulary("fitch_industry")
    country_vocabulary = aggregates.vocabulary("country")

    out: dict[str, dict[str, Any]] = {}
    for row in _data_rows(pages, layout):
        identifier, head, cont = _row_text(row)
        known = names.get(identifier)

        # Column 1 — the facility name, consumed in full from the authoritative
        # Part III value (following it into the continuation if it wrapped).
        # Taking the *whole* name rather than guessing a prefix is what keeps a
        # name legitimately ending in a digit — ``...TLB4`` sits immediately
        # before ``4,500,000.00`` — from swallowing the balance's leading digit.
        if known:
            taken = _consume(head, cont, str(known["facility_name"]))
            if taken is not None:
                head, cont = taken

        head, cont = _advance(head, cont)
        balance_match = re.match(rf"^\s*({MONEY})", head)
        if not balance_match:
            defects.record("unrecognised_rows", f"Part II balance unreadable for {identifier}")
            continue
        balance = Decimal(balance_match.group(1).replace(",", ""))
        head = head[balance_match.end() :]

        head, cont = _advance(head, cont)
        taken_sp = _consume_any(head, cont, sp_vocabulary)
        if taken_sp is None:
            sp_industry = None
            defects.record("unresolved_industry", f"S&P industry for {identifier}")
        else:
            sp_industry, head, cont = taken_sp
        head, cont = _advance(head, cont)
        taken_fitch = _consume_any(head, cont, fitch_vocabulary)
        if taken_fitch is None:
            fitch_industry = None
            defects.record("unresolved_industry", f"Fitch industry for {identifier}")
        else:
            fitch_industry, head, cont = taken_fitch

        # Currency has no table of its own, so it is whatever alphabetic run
        # precedes a country the report's own Country Concentration lists.
        currency: str | None = None
        country: str | None = None
        head, cont = _advance(head, cont)
        head = head.lstrip()
        for size in range(1, len(head) + 1):
            candidate = head[:size]
            if not candidate.replace(" ", "").isalpha():
                break
            taken_country = _consume_any(head[size:], cont, country_vocabulary)
            if taken_country is not None:
                country, head, cont = taken_country
                currency = candidate
                break
        if country is None:
            defects.record("unresolved_country", f"country for {identifier}: {head[:40]!r}")

        out[identifier] = {
            "principal_balance": balance,
            "sp_industry": sp_industry,
            "fitch_industry": fitch_industry,
            "currency": currency,
            "country": country,
        }
    return out


def _split_issuer_and_name(
    head: str, cont: str, facility_name: str
) -> tuple[str | None, bool]:
    """Split a Part I row's leading text into the issuer name.

    ``head`` is ``<issuer line 1><facility-name fragment>`` with no separator at
    all, and ``cont`` is ``<issuer remainder><facility-name remainder>`` — issuer
    first, because that is the column order. The facility name is known in full
    from Part III, so the cut is: the largest split of the name whose left half
    ends ``head`` and whose right half ends ``cont``. Whatever is left on each
    side is the issuer, in order.
    """
    goal = _squash(facility_name)
    in_head, in_cont = _squash(head), _squash(cont)
    for size in range(len(goal), 0, -1):
        if not in_head.endswith(goal[:size]):
            continue
        if not in_cont.endswith(goal[size:]):
            continue
        head_cut = _cut_before(head, size)
        cont_cut = _cut_before(cont, len(goal) - size)
        if head_cut is None or cont_cut is None:
            continue
        issuer = _join(head[:head_cut], cont[:cont_cut])
        if issuer:
            return issuer, True
    return ((head + cont).strip() or None), False


def _parse_part_i(
    pages: list[list[str]],
    layout: DocumentLayout,
    names: dict[str, dict[str, Any]],
    part_ii: dict[str, dict[str, Any]],
    defects: ScheduleDefects,
) -> dict[str, dict[str, Any]]:
    """Part I → issuer name and the asset's economics."""
    out: dict[str, dict[str, Any]] = {}
    for row in _data_rows(pages, layout):
        identifier, line, cont = _row_text(row)
        anchor = re.search(rf"(?:Loan|Bond){_S}(?:Floating|Fixed)", line)
        if not anchor:
            defects.record("part_i_tail_unparsed", f"no asset-type anchor for {identifier}")
            continue
        head, tail = line[: anchor.start()], line[anchor.start() :]
        match = _PART_I_TAIL.match(tail)
        if not match:
            defects.record("part_i_tail_unparsed", f"tail {tail[:48]!r} for {identifier}")
            continue
        fields = match.groupdict()

        # The balance sits at the end of the head. Its left boundary is
        # ambiguous in isolation (a facility name may end in a digit), so prefer
        # Part II's balance, whose boundary Part III already disambiguated.
        expected = part_ii.get(identifier, {}).get("principal_balance")
        balance: Decimal | None = None
        if expected is not None:
            formatted = f"{expected:,.2f}"
            if _squash(head).endswith(_squash(formatted)):
                cut = _cut_before(head, len(_squash(formatted)))
                if cut is not None:
                    head, balance = head[:cut], expected
        if balance is None:
            candidates = list(_MONEY_RE.finditer(head))
            if not candidates:
                defects.record("unrecognised_rows", f"Part I balance unreadable for {identifier}")
                continue
            last = candidates[-1]
            balance = Decimal(last.group(0).replace(",", ""))
            head = head[: last.start()]
            if expected is not None and balance != expected:
                defects.record(
                    "balance_disagreement",
                    f"{identifier}: Part I {balance} vs Part II {expected}",
                )

        facility_name = str(names.get(identifier, {}).get("facility_name", ""))
        if facility_name:
            issuer, resolved = _split_issuer_and_name(head, cont, facility_name)
        else:
            issuer, resolved = ((head + cont).strip() or None), False
        if not resolved:
            defects.record("unresolved_issuer_name", f"{identifier}: {head[:48]!r}")

        out[identifier] = {
            "issuer_name": issuer,
            "principal_balance": balance,
            "asset_type": fields["asset_type"],
            "coupon_type": fields["coupon_type"],
            "current_spread": Decimal(fields["spread"]),
            "index_floor": Decimal(fields["floor"]) if fields.get("floor") else None,
            "current_coupon": Decimal(fields["coupon"]),
            "index_type": (fields.get("index_type") or "").strip() or None,
            "maturity_date": fields["maturity"],
            "market_value": Decimal(fields["market_value"]),
        }
    return out


def _parse_ccc(
    pages: list[list[str]],
    layout: DocumentLayout,
    names: dict[str, dict[str, Any]],
    defects: ScheduleDefects,
) -> tuple[dict[str, dict[str, Any]], Decimal | None]:
    """S&P CCC Obligations → seniority and rating, plus the bucket's own total."""
    out: dict[str, dict[str, Any]] = {}
    total: Decimal | None = None
    # Not anchored at the end: a wrapped facility name puts its remainder after
    # the market value, so the tail is matched wherever it sits on the row.
    pattern = re.compile(
        rf"{MONEY}{_S}(?P<seniority>[A-Za-z][A-Za-z ]*?){_S}"
        rf"(?P<rating>CCC\+|CCC-|CCC|CC|C|D|SD){_S}(?P<market_value>\d+\.\d{{1,4}})"
    )
    for lines in pages:
        for line in lines:
            if _is_furniture(line, layout) or IDENTIFIER_RE.match(line):
                continue
            stripped = line.strip()
            if _MONEY_RE.fullmatch(stripped):
                total = Decimal(stripped.replace(",", ""))
    for row in _data_rows(pages, layout):
        identifier, head, cont = _row_text(row)
        match = pattern.search(head + cont)
        if not match:
            defects.record("ccc_rows_unjoined", f"CCC row unreadable for {identifier}")
            continue
        out[identifier] = {
            "seniority": match.group("seniority").strip(),
            "sp_rating": match.group("rating"),
        }
        if identifier not in names:
            defects.record("ccc_rows_unjoined", f"CCC {identifier} absent from Part III")
    return out, total


# ===========================================================================
# The parse entry point
# ===========================================================================


def _reflowed_schedule(
    pages: list[list[str]],
    layout: DocumentLayout,
    *,
    aggregates: ReportAggregates,
    defects: ScheduleDefects,
    deal_name: str | None,
    reporting_date: str | None,
    period_label: str,
    strict: bool,
) -> CollateralSchedule:
    """The row-major parse path, for a family whose rows are not lines.

    Same contract as the per-line path and the same refusal: the schedule is
    reconciled against the report's own stated figures and never returned when
    it does not tie.
    """
    assets = _bny_assets(pages, layout, aggregates, defects)
    aggregates.accrual_record_count = sum(asset.accrual_records or 0 for asset in assets)
    for section_key, name in (
        (SECTION_ASSET_PART_I, "Asset Information I"),
        (SECTION_ASSET_PART_II, "Asset Information II"),
        (SECTION_ASSET_PART_III, "Asset Information III"),
    ):
        aggregates.section_asset_counts[name] = _section_identifier_count(
            pages, layout, section_key
        )

    _record_uncheckable_buckets(aggregates, assets, defects)
    schedule = CollateralSchedule(
        deal_name=deal_name,
        period_label=period_label,
        reporting_date=reporting_date,
        assets=assets,
        aggregates=aggregates,
        defects=defects,
        count_grain=layout.count_grain,
    )
    if strict:
        reconciliation = reconcile_schedule(schedule)
        if not reconciliation.ok or defects.blocking:
            raise ScheduleReconciliationError(reconciliation, defects)
    return schedule


def _section_identifier_count(
    pages: list[list[str]], layout: DocumentLayout, section_key: str
) -> int:
    """How many distinct asset identifiers one section carries.

    Counted from the identifiers rather than from the rows the tail matched, on
    purpose: this is the check that the *tail* did not quietly drop a row whose
    columns it could not read. A count taken from the same match it is meant to
    audit would agree with itself by construction (#511).
    """
    identifiers: set[str] = set()
    for text in _section_row_texts(_pages_for(pages, layout, section_key), layout):
        identifiers.update(found.group(1) for found in _EMBEDDED_IDENTIFIER_RE.finditer(text))
    return len(identifiers)


def parse_schedule_text(
    text: str,
    *,
    period_label: str,
    strict: bool = True,
) -> CollateralSchedule:
    """Parse a trustee report's text into a per-asset collateral schedule.

    Pure and deterministic — no network, no LLM. Sections are located by their
    header text, never by page number.

    With ``strict`` (the default) the schedule is reconciled against the
    report's own stated totals and a divergence raises
    :class:`ScheduleReconciliationError`. That refusal is the contract: a tape
    that does not tie out must never reach a consumer that would trust it. Pass
    ``strict=False`` only to inspect a failing parse.
    """
    pages = _split_pages(text)
    if not pages:
        raise ValueError("no pages found — text is not extracted trustee-report output")

    layout = _resolve_layout(pages)
    deal_name, reporting_date = _report_header(pages)

    defects = ScheduleDefects()
    aggregates = _parse_aggregates(pages, layout)

    part_iii_pages = _pages_for(pages, layout, SECTION_ASSET_PART_III)
    part_ii_pages = _pages_for(pages, layout, SECTION_ASSET_PART_II)
    part_i_pages = _pages_for(pages, layout, SECTION_ASSET_PART_I)
    ccc_pages = _pages_for(pages, layout, SECTION_CCC)
    if not (part_i_pages and part_ii_pages and part_iii_pages):
        raise ValueError(
            "report is missing at least one per-asset detail section "
            "(located by header, not page number)"
        )

    if layout.row_geometry is RowGeometry.REFLOWED_ROWS:
        return _reflowed_schedule(
            pages,
            layout,
            aggregates=aggregates,
            defects=defects,
            deal_name=deal_name,
            reporting_date=reporting_date,
            period_label=period_label,
            strict=strict,
        )

    names = _parse_part_iii(part_iii_pages, layout, defects)
    part_ii = _parse_part_ii(part_ii_pages, layout, names, aggregates, defects)
    part_i = _parse_part_i(part_i_pages, layout, names, part_ii, defects)
    ccc, ccc_total = _parse_ccc(ccc_pages, layout, names, defects)
    aggregates.ccc_total = ccc_total

    assets: list[CollateralAsset] = []
    for identifier, name_row in names.items():
        two = part_ii.get(identifier)
        one = part_i.get(identifier)
        if two is None:
            defects.record("identifiers_missing_from_part_ii", identifier)
            continue
        if one is None:
            defects.record("identifiers_missing_from_part_i", identifier)
        merged: dict[str, Any] = {
            "identifier": identifier,
            "facility_name": name_row["facility_name"],
            "flags": name_row["flags"],
            **two,
            **(one or {}),
            **ccc.get(identifier, {}),
        }
        assets.append(CollateralAsset(**merged))

    for table in aggregates.inconsistent_tables:
        buckets: list[AggregateBucket] = getattr(aggregates, table)
        defects.record(
            "source_aggregate_inconsistent",
            f"the report's own {table} table sums to "
            f"{sum(b.balance for b in buckets)} but the same page states "
            f"{aggregates.aggregate_principal_balance}; its balances are excluded "
            "from the oracle and its counts are still checked",
        )

    _record_uncheckable_buckets(aggregates, assets, defects)
    schedule = CollateralSchedule(
        deal_name=deal_name,
        period_label=period_label,
        reporting_date=reporting_date,
        assets=assets,
        aggregates=aggregates,
        defects=defects,
    )
    if strict:
        reconciliation = reconcile_schedule(schedule)
        if not reconciliation.ok or defects.blocking:
            raise ScheduleReconciliationError(reconciliation, defects)
    return schedule


# ---------------------------------------------------------------------------
# BNY Mellon — assembling the schedule
# ---------------------------------------------------------------------------

#: ``Asset Information II``'s three flag cells, in the order its header prints
#: them, and ``Asset Information IV``'s eight. Named separately because they
#: come off different sections at different grains: II is per asset, IV is per
#: purchase lot, so IV's are folded across an asset's lots with ``any``.
_BNY_PART_II_FLAGS: tuple[str, ...] = ("dip", "cov_lite", "pik")
_BNY_PART_IV_FLAGS: tuple[str, ...] = (
    "bridge_loan",
    "swapped",
    "non_discount",
    "hedged_obligation",
    "discount_obligation",
    "deferring",
    "current_pay",
    "interest_rate_swap",
)


def _flag_cells(text: str, names: tuple[str, ...]) -> dict[str, bool]:
    """Read a run of ``Yes``/``-`` cells into named flags."""
    values = _FLAG_RE.findall(text)
    return {name: value == "Yes" for name, value in zip(names, values, strict=False)}


def _bny_assets(
    pages: list[list[str]],
    layout: DocumentLayout,
    aggregates: ReportAggregates,
    defects: ScheduleDefects,
) -> list[CollateralAsset]:
    """Assemble BNY's four asset sections into one row per asset.

    ``Asset Information I`` is the anchor: it is the section that enumerates
    the portfolio, one row per asset, and its identifiers are what every other
    section is joined onto. An identifier present in a later section and absent
    from I is recorded rather than added, and an asset missing from a later
    section keeps ``None`` in that section's fields — an honest absence, never
    a zero that reads as a fact.
    """
    part_i = {
        identifier: (description, matched)
        for identifier, description, matched in _reflowed_rows(
            _pages_for(pages, layout, SECTION_ASSET_PART_I), layout, _BNY_PART_I_TAIL
        )
    }
    part_ii = {
        identifier: matched
        for identifier, _, matched in _reflowed_rows(
            _pages_for(pages, layout, SECTION_ASSET_PART_II), layout, _BNY_PART_II_TAIL
        )
    }
    part_iii_tail = _bny_part_iii_tail(aggregates)
    part_iii: dict[str, re.Match[str]] = {}
    if part_iii_tail is None:
        defects.record(
            "section_unreadable",
            "Asset Information III carries two free-text industry columns with "
            "no delimiter after them, and the report's own industry tables — "
            "the closed vocabulary that resolves those boundaries — parsed "
            "empty, so the section is refused rather than cut at a guess",
        )
    else:
        part_iii = {
            identifier: matched
            for identifier, _, matched in _reflowed_rows(
                _pages_for(pages, layout, SECTION_ASSET_PART_III), layout, part_iii_tail
            )
        }

    lots = _bny_lots(pages, layout)
    accruals = _bny_accrual_records(pages, layout, defects)
    market_values = _bny_ccc_market_values(pages, layout, defects)

    assets: list[CollateralAsset] = []
    for identifier, (description, one) in part_i.items():
        issuer_name, facility_name = _split_description(description)
        if issuer_name is None:
            defects.record("descriptions_without_an_obligor", identifier)
        two = part_ii.get(identifier)
        three = part_iii.get(identifier)
        if two is None:
            defects.record("identifiers_missing_from_part_ii", identifier)
        if three is None and part_iii_tail is not None:
            defects.record("identifiers_missing_from_part_iii", identifier)
        country, lot_flags, lot_count = lots.get(identifier, (None, {}, 0))
        if lot_count == 0:
            defects.record("identifiers_missing_from_part_iv", identifier)
        flags = dict(lot_flags)
        if two is not None:
            flags.update(_flag_cells(two.group("flags"), _BNY_PART_II_FLAGS))
        assets.append(
            CollateralAsset(
                identifier=identifier,
                issuer_name=issuer_name,
                facility_name=facility_name,
                principal_balance=Decimal(one.group("principal_balance").replace(",", "")),
                asset_type=one.group("asset_type") or None,
                maturity_date=one.group("maturity_date"),
                seniority=one.group("seniority"),
                market_price_pct=Decimal(one.group("market_price_pct")),
                market_value=market_values.get(identifier),
                currency=two.group("currency") if two is not None else None,
                sp_rating=_bny_rating(three.group("sp_rating")) if three is not None else None,
                fitch_rating=(
                    _bny_rating(three.group("fitch_rating")) if three is not None else None
                ),
                sp_industry=_collapse(three.group("sp_industry")) if three is not None else None,
                fitch_industry=(
                    _collapse(three.group("fitch_industry")) if three is not None else None
                ),
                country=country,
                flags=flags,
                purchase_lots=lot_count or None,
                accrual_records=accruals.get(identifier),
            )
        )

    for identifier in set(part_ii) | set(part_iii):
        if identifier not in part_i:
            defects.record("identifiers_absent_from_the_anchor_section", identifier)
    return assets


def _bny_rating(cell: str) -> str | None:
    """A rating cell, with this administrator's ``***`` read as *no rating*.

    ``***`` is what BNY prints where an agency does not rate the asset. It is
    not a rating and must never become one: returned as ``None`` so a consumer
    asking "what does S&P say about this asset?" gets "nothing" rather than a
    token that sorts, groups and compares like a rating band.
    """
    return None if cell == "***" else cell


def _bny_lots(
    pages: list[list[str]], layout: DocumentLayout
) -> dict[str, tuple[str | None, dict[str, bool], int]]:
    """Country, folded flags and lot count per asset, from ``Asset Information IV``.

    This section is at **purchase-lot** grain, so an asset holds several rows.
    Country is the same on every lot of an asset; the flags are folded with
    ``any``, since an asset one of whose lots is a discount obligation is a
    discount obligation. Country is read here rather than from Part II because
    here a rating cell bounds it on the right — in Part II the next row's
    obligor name follows it with no delimiter and no vocabulary to resolve
    against, this administrator publishing no country table.
    """
    lots: dict[str, tuple[str | None, dict[str, bool], int]] = {}
    for identifier, _, matched in _reflowed_rows(
        _pages_for(pages, layout, SECTION_ASSET_PART_IV), layout, _BNY_PART_IV_TAIL
    ):
        country, flags, count = lots.get(identifier, (None, {}, 0))
        folded = dict(flags)
        for name, value in _flag_cells(matched.group("flags"), _BNY_PART_IV_FLAGS).items():
            folded[name] = folded.get(name, False) or value
        lots[identifier] = (country or _collapse(matched.group("country")), folded, count + 1)
    return lots


def _bny_accrual_records(
    pages: list[list[str]], layout: DocumentLayout, defects: ScheduleDefects
) -> dict[str, int]:
    """How many accrual records each asset carries.

    This is the population BNY's aggregate tables count. Held per asset rather
    than as one total because the *distribution* is what the per-bucket checks
    need: a bucket's stated count is the accrual records of the assets in it,
    while its stated balance is those assets' par. One table, two grains.
    """
    section = _pages_for(pages, layout, SECTION_ACCRUAL_DETAIL)
    tail = _bny_accrual_tail(section, layout)
    records: dict[str, int] = {}
    for identifier, _, matched in _reflowed_rows(section, layout, tail):
        records[identifier] = records.get(identifier, 0) + 1
        if matched.groupdict().get("principal_balance") is None:
            defects.record(
                "accrual_records_without_a_balance",
                f"{identifier}: the report prints this accrual record with a "
                "payment period and no balance cell. It is counted, because it "
                "is one of the records the aggregate tables count, and its "
                "balance is left absent rather than completed with a zero",
            )
    return records


def _bny_ccc_market_values(
    pages: list[list[str]], layout: DocumentLayout, defects: ScheduleDefects
) -> dict[str, Decimal]:
    """Market values from ``CCC Obligations``, unit-checked against the price.

    This is the one BNY table printing a market **price** and a market
    **value** for the same asset, which makes the units checkable against the
    document rather than against the parser's own arithmetic: the value must be
    the par times the price over 100. #470 is the correction of a column that
    looked like a market value and was a price (``99.72``), wrong by four
    orders of magnitude into the OC ratio — so a divergence here is recorded as
    a defect rather than absorbed.
    """
    values: dict[str, Decimal] = {}
    for identifier, _, matched in _reflowed_rows(
        _pages_for(pages, layout, SECTION_CCC), layout, _BNY_CCC_TAIL
    ):
        price = Decimal(matched.group("market_price_pct"))
        value = Decimal(matched.group("market_value").replace(",", ""))
        par = Decimal(matched.group("principal_balance").replace(",", ""))
        implied = (par * price / Decimal(100)).quantize(Decimal("0.01"))
        if abs(implied - value) > _MARKET_VALUE_TOLERANCE:
            defects.record(
                "market_value_units",
                f"{identifier}: the report states market value {value} but "
                f"par {par} at price {price} implies {implied}; one of the two "
                "columns is not what its header says it is (#470)",
            )
        values[identifier] = value
    return values


#: How far a market value may sit from par x price / 100 before the units are
#: in doubt. One cent per side of the rounding, which is the most a correctly
#: derived value can differ by; anything larger is a different quantity, not a
#: rounding.
_MARKET_VALUE_TOLERANCE = Decimal("0.02")


# ===========================================================================
# The contract — reconcile the tape to the report's own stated totals
# ===========================================================================


#: The aggregate tables :func:`reconcile_schedule` checks per bucket, paired
#: with the asset attribute each groups the tape by and the label it reports
#: under. One list, read by the reconciliation and by the parse-time record of
#: which of them could not be checked — two readers of one fact, so they cannot
#: disagree about which tables were skipped.
_BUCKET_TABLES: tuple[tuple[str, str], ...] = (
    ("country", "country"),
    ("sp_industry", "S&P industry"),
    ("fitch_industry", "Fitch industry"),
    ("sp_rating", "S&P rating"),
)


def _bucket_attribute_incomplete(assets: list[CollateralAsset], table: str) -> bool:
    """Whether the tape can be grouped by *table*'s attribute at all.

    An asset missing the attribute would fall out of every bucket, so a
    comparison would under-count each one and report a divergence that is a gap
    in the tape rather than a disagreement with the document.
    """
    return any(getattr(asset, table) is None for asset in assets)


def _record_uncheckable_buckets(
    aggregates: ReportAggregates, assets: list[CollateralAsset], defects: ScheduleDefects
) -> None:
    """Record each published distribution the tape cannot be checked against.

    Called once per parse, from the parse — not from the reconciliation, which
    must stay free of side effects so that asking the same schedule twice
    cannot answer differently the second time. "This check did not run" has to
    be visible, or it is indistinguishable from "this check passed" (#494).
    """
    for table, label in _BUCKET_TABLES:
        if not getattr(aggregates, table):
            continue
        if _bucket_attribute_incomplete(assets, table):
            defects.record(
                "bucket_attribute_incomplete",
                f"the report publishes a {label} table but not every parsed "
                f"asset carries a {table}, so its per-bucket distribution "
                "cannot be checked",
            )


def _check(name: str, expected: Any, actual: Any) -> ReconciliationCheck:
    return ReconciliationCheck(
        name=name, expected=str(expected), actual=str(actual), ok=expected == actual
    )


def _count_weight(asset: CollateralAsset, grain: CountGrain) -> int:
    """How much one asset contributes to a stated count, at the declared grain.

    One, where the tables count assets. Where they count accrual records — one
    per rate contract — an asset accruing under two contracts contributes two,
    which is the whole of Contego's 212 against 177.
    """
    if grain is CountGrain.ACCRUAL_RECORD:
        return asset.accrual_records or 0
    return 1


def _group_by(
    assets: list[CollateralAsset], attribute: str, grain: CountGrain = CountGrain.ASSET
) -> dict[str, tuple[Decimal, int]]:
    """Balance and stated-count weight per bucket of one attribute.

    The two are summed at **different grains on purpose**, because that is what
    the document does: a BNY concentration table states a balance over assets
    and a count over accrual records, in adjacent columns of one row. Summing
    both at one grain reproduces the table's balance and contradicts its count,
    or the reverse — never both (#484).
    """
    grouped: dict[str, tuple[Decimal, int]] = {}
    for asset in assets:
        key = getattr(asset, attribute)
        if key is None:
            continue
        balance, count = grouped.get(key, (Decimal("0"), 0))
        grouped[key] = (
            balance + asset.principal_balance,
            count + _count_weight(asset, grain),
        )
    return grouped


def reconcile_schedule(schedule: CollateralSchedule) -> ScheduleReconciliation:
    """Tie the parsed tape back to the aggregates the report states about itself.

    This is the acceptance oracle, and it is the reason the parse can be trusted
    by anything downstream. It compares, where the report publishes them:

    - asset count and aggregate principal balance;
    - the country distribution, balance and count per country;
    - the S&P industry distribution, balance and count per industry;
    - the S&P CCC bucket's own stated total;
    - the Portfolio Profile Test numerators for current-pay and for
      revolving/delayed-drawdown obligations, which is what proves the Part III
      flags were genuinely parsed rather than defaulted to ``False``.
    """
    aggregates = schedule.aggregates
    assets = schedule.assets
    checks: list[ReconciliationCheck] = []

    grain = schedule.count_grain
    if aggregates.asset_count is not None:
        if grain is CountGrain.ACCRUAL_RECORD:
            # The stated count is not an asset count for this administrator,
            # so it is checked against the population it actually describes.
            # Contego states 212 against 177 assets at an identical balance:
            # comparing it to the asset count would fail a correct parse, and
            # quietly "fixing" that by dropping the check would leave par as
            # the only oracle — and a row worth zero can go missing under par
            # without moving it (#468).
            checks.append(
                _check(
                    "accrual record count",
                    aggregates.asset_count,
                    sum(asset.accrual_records or 0 for asset in assets),
                )
            )
        else:
            checks.append(_check("asset count", aggregates.asset_count, len(assets)))

    # The asset count then needs an oracle of its own, since no aggregate table
    # states one. The sections that each enumerate the portfolio once must
    # agree with each other and with the tape.
    for section, stated in sorted(aggregates.section_asset_counts.items()):
        checks.append(_check(f"asset count · {section}", len(assets), stated))
    if aggregates.aggregate_principal_balance is not None:
        checks.append(
            _check(
                "aggregate principal balance",
                aggregates.aggregate_principal_balance,
                schedule.total_principal_balance,
            )
        )

    for table, label in _BUCKET_TABLES:
        buckets: list[AggregateBucket] = getattr(aggregates, table)
        if not buckets:
            continue
        if _bucket_attribute_incomplete(assets, table):
            # The table exists but the tape cannot populate its attribute for
            # every asset, so a per-bucket comparison would under-count every
            # bucket and read as a divergence in the parse rather than as the
            # gap it is. The parse records it as a defect exactly once; this
            # function only reads, so calling it twice cannot inflate a count.
            continue
        grouped = _group_by(assets, table, grain)
        trustworthy = table not in aggregates.inconsistent_tables
        for bucket in buckets:
            balance, count = grouped.get(bucket.label, (Decimal("0"), 0))
            if trustworthy:
                checks.append(_check(f"{label} balance · {bucket.label}", bucket.balance, balance))
            checks.append(_check(f"{label} count · {bucket.label}", bucket.count, count))
        extra = set(grouped) - {b.label for b in buckets}
        if extra:
            checks.append(_check(f"{label} labels not in the report's table", set(), extra))

    if aggregates.ccc_total is not None:
        parsed = sum(
            (a.principal_balance for a in assets if a.sp_rating is not None), Decimal("0")
        )
        checks.append(_check("S&P CCC bucket total", aggregates.ccc_total, parsed))

    # The flag reconciliation. Profile Test (h) is Current Pay Obligations and
    # (i) is Revolving Obligations or Delayed Drawdown Collateral; each states a
    # numerator the flagged assets' par must equal. Without this, a Part III
    # parse that silently produced all-``False`` flags would still reconcile.
    for prefix, flags in (("(h)", ("current_pay",)), ("(i)", ("revolving", "delayed_drawdown"))):
        test = aggregates.profile_test(prefix)
        if test is None:
            continue
        parsed = sum(
            (a.principal_balance for a in assets if any(a.flag(f) for f in flags)),
            Decimal("0"),
        )
        checks.append(_check(f"profile test {prefix} numerator", test.numerator, parsed))

    return ScheduleReconciliation(checks=checks)


# ===========================================================================
# Governed (PrimitiveResult-returning) surface — the #277 envelope wrapper
# ===========================================================================


def parse_schedule_text_result(
    text: str,
    *,
    period_label: str,
) -> PrimitiveResult[CollateralSchedule]:
    """Envelope-returning wrapper over :func:`parse_schedule_text`.

    The parse is deterministic (``pypdf``/regex, no LLM) *and* reconciled to the
    source document's own totals before it is returned, so the envelope
    confidence is ``1.0`` — the framework's rule-based convention. One
    :class:`Citation` per detail section grounds the schedule in the pages it was
    read from.
    """
    started = time.perf_counter()
    parse_input = ScheduleParseInput(period_label=period_label, text=text)
    schedule = parse_schedule_text(text, period_label=period_label)
    duration_ms = (time.perf_counter() - started) * 1000.0

    # Citations name each section as *this report* prints it, so the locator
    # stays checkable against the document a reader opens.
    layout = _resolve_layout(_split_pages(text))
    document = _report_document_name(schedule.deal_name, period_label)
    citations = [
        Citation(
            document=document,
            page_or_row=section,
            excerpt=(
                "Per-asset collateral schedule parsed deterministically from the "
                "extracted trustee-report text and reconciled against the report's "
                "own stated aggregates."
            ),
        )
        for section in (
            layout.title(SECTION_ASSET_PART_I),
            layout.title(SECTION_ASSET_PART_II),
            layout.title(SECTION_ASSET_PART_III),
            layout.title(SECTION_CCC),
        )
    ]
    audit = AuditEntry.now(
        primitive_name=_PRIMITIVE_NAME,
        version=_PRIMITIVE_VERSION,
        input_hash=parse_input.input_hash(),
        duration_ms=duration_ms,
    )
    return PrimitiveResult[CollateralSchedule](
        output=schedule,
        confidence=_DETERMINISTIC_CONFIDENCE,
        citations=citations,
        audit_entry=audit,
    )


# ===========================================================================
# The liability side — what the report states about the notes
# ===========================================================================
#
# Everything above reads the collateral. This part reads the same document's
# statements about the notes that collateral funds, and it exists because a
# prospectus cannot answer the question the engine asks.
#
# The offering circular gives Class A's coupon as ``3 month EURIBOR + 1.80%``.
# That is a **margin, not a rate**: resolving it needs the period's index
# fixing, and inventing one is exactly what the engine's rate parser refuses to
# do. The trustee report states the resolved figure — ``4.54400`` for March
# 2025 — because by the time it is written the fixing has happened.
#
# The same is true of the coverage tests. #456 recorded their required levels
# as unobtainable, and was right about the circular: the levels live in an
# alphabetical definitions glossary that runs past the extractor's 40,000
# character budget, so every test defined under C-F was lost. It was wrong
# about the *document* — the Par Value and Interest Coverage Tests Detail pages
# state each required level beside the computed ratio, every month.
#
# **These are report-derived facts, not prospectus terms**, and the distinction
# is load-bearing rather than pedantic: a required level read off one month's
# trustee report is that month's stated figure, where a prospectus term is the
# deal's contractual definition. :func:`liability_provenance` can therefore only
# emit ``source="report"`` — see the constant's note.
#
# Two things make the parse trustworthy rather than merely plausible:
#
# 1. **Two independent stated totals.** The Executive Summary states aggregate
#    principal balance *and* aggregate periodic interest, and the per-class rows
#    must sum to both. One total alone would be satisfied by two transposed
#    rows; two totals over different quantities are not.
# 2. **Two renderings of the same coverage tests, in opposite column order.**
#    The Executive Summary prints ``Threshold`` then ``Current``; the detail
#    pages print ``RATIO`` then ``REQUIRED LEVEL``. Requiring the two to agree
#    is a genuine cross-check, and it is the reason the column order is read
#    from each section's header instead of assumed — a silent swap would report
#    a breaching test as passing, which is the worst failure available here.


class CoverageTestOutcome(str, Enum):
    """The result a trustee report states for a coverage test.

    Closed on purpose. A row whose outcome is none of these does not match
    :data:`_COVERAGE_TEST_RE` at all, so it is absent from one rendering and
    present in the other — which the cross-rendering check in
    :func:`reconcile_liability_summary` refuses. An unknown outcome therefore
    surfaces as a refusal rather than as a row quietly dropped.
    """

    PASSED = "Passed"
    FAILED = "Failed"
    NOT_APPLICABLE = "N/A"


# ``ColumnOrder`` and the header fingerprints that select it now live with the
# family: which of a coverage-test table's two percentage columns comes first is
# a property of the administrator's layout, and the fingerprint table is
# exhaustive by construction so a header matching none of a family's entries is
# refused rather than read in a guessed order (#480).

#: The provenance source every figure this seam emits carries — a **constant,
#: exposed through no parameter**. A coupon or a required level taken from a
#: trustee report is a report-derived fact; presenting one as an extracted
#: prospectus term would lend it a contractual authority it does not have. The
#: cheapest way to guarantee that is to leave the caller no way to say
#: otherwise, so :func:`liability_provenance` takes no ``source`` argument and
#: there is no code path in this module that writes any other value.
_LIABILITY_PROVENANCE_SOURCE: Final[str] = "report"


def _trigger_key(name: str) -> str:
    """Canonical key for a coverage test, from the name the report prints.

    ``"Class A/B Par Value Test"`` becomes ``"class_a_b_par_value_test"`` — the
    shape the extracted deal model already uses for its trigger names, so the
    two are comparable without a translation table. That correspondence is
    pinned by a test rather than asserted here.
    """
    return re.sub(r"[^a-z0-9]+", "_", " ".join(name.split()).lower()).strip("_")


class NoteClassFigures(BaseModel):
    """One note class, as the report's Executive Summary states it."""

    note_class: str = Field(..., description="The class label, e.g. 'A', 'B-1', 'Subordinated'.")
    principal_balance: Decimal
    coupon_pct: Decimal | None = Field(
        default=None,
        description=(
            "Current coupon in percent, already resolved to a number by the "
            "trustee. None when the report states no coupon for this class — "
            "which is a different fact from a coupon of zero."
        ),
    )
    periodic_interest: Decimal | None = Field(
        default=None,
        description="Interest for the period. None when the report states none.",
    )

    @property
    def class_key(self) -> str:
        """Canonical dotted-path segment for this class, e.g. ``class_b_1``."""
        return _trigger_key(f"Class {self.note_class}")


class CoverageTestResult(BaseModel):
    """One coverage test, with the required level the report states beside it."""

    name: str
    current_pct: Decimal = Field(..., description="The computed ratio, in percent.")
    required_pct: Decimal = Field(..., description="The level the test requires, in percent.")
    result: CoverageTestOutcome
    stated_in: str = Field(..., description="The report section this reading came from.")

    @property
    def trigger_key(self) -> str:
        """Canonical key, matching the extracted model's trigger names."""
        return _trigger_key(self.name)


class ReportLiabilitySummary(BaseModel):
    """One reporting date's liability-side figures, plus its acceptance oracle."""

    #: The printed section titles this summary was parsed under — the detected
    #: family's, never a default. **Private on purpose**: :func:`liability_provenance`
    #: needs them to cite each figure's section as the document prints it, but a
    #: serialised field would change the bytes of every consumer of this model,
    #: and this parse's byte-for-byte stability is the contract the report-family
    #: generalisation was held to (#531). A private attribute is carried on the
    #: instance and excluded from ``model_dump``.
    _section_titles: dict[str, str] = PrivateAttr(default_factory=dict)

    deal_name: str | None = None
    period_label: str
    reporting_date: str | None = None
    note_classes: list[NoteClassFigures] = Field(default_factory=list)

    #: Coverage tests as the **detail** pages state them (``RATIO``, then
    #: ``REQUIRED LEVEL``). This is the authoritative reading.
    coverage_tests: list[CoverageTestResult] = Field(default_factory=list)

    #: The same tests as the **Executive Summary** states them, in the opposite
    #: column order. Kept so the cross-check is a property of the model rather
    #: than a step that ran once inside the parser and left no evidence.
    summary_coverage_tests: list[CoverageTestResult] = Field(default_factory=list)

    stated_total_balance: Decimal | None = None
    stated_total_periodic_interest: Decimal | None = None

    #: Whether this summary passed :func:`reconcile_liability_summary`. Recorded
    #: by the parser, which is the only thing that knows; **not** a claim any
    #: caller can make. It defaults to ``False`` because a summary that has not
    #: been through the check has not passed it, and it is what
    #: :func:`liability_provenance` reports as ``FieldProvenance.reconciled`` —
    #: the signal the human-review gate routes unverified fields by.
    reconciled: bool = False

    @property
    def total_note_balance(self) -> Decimal:
        """Sum of every parsed class's principal balance."""
        return sum((c.principal_balance for c in self.note_classes), Decimal("0"))

    @property
    def total_periodic_interest(self) -> Decimal:
        """Sum of the periodic interest the report states, over classes stating one."""
        return sum(
            (c.periodic_interest for c in self.note_classes if c.periodic_interest is not None),
            Decimal("0"),
        )

    def note_class(self, label: str) -> NoteClassFigures | None:
        """One class by its label, or None."""
        return next((c for c in self.note_classes if c.note_class == label), None)

    def coverage_test(self, trigger_key: str) -> CoverageTestResult | None:
        """One coverage test by its canonical key, or None."""
        return next((t for t in self.coverage_tests if t.trigger_key == trigger_key), None)


class LiabilitySummaryParseInput(BaseInput):
    """Governance input record for the envelope wrapper."""

    period_label: str
    text: str


class LiabilitySummaryReconciliationError(ValueError):
    """Raised when the liability summary does not tie out to the report's own figures.

    The same enforced boundary as :class:`ScheduleReconciliationError`, for the
    same reason: a coupon or a required level that reaches the engine wrong is
    worse than one that never arrives.
    """

    def __init__(self, reconciliation: ScheduleReconciliation):
        self.reconciliation = reconciliation
        lines = [f"{c.name}: expected {c.expected}, got {c.actual}" for c in reconciliation.failures]
        super().__init__(
            "trustee-report liability summary does not reconcile to the report's "
            "own stated figures — refusing to return it. " + " | ".join(lines)
        )


def _column_order(
    pages: list[list[str]], layout: DocumentLayout, section: str
) -> ColumnOrder:
    """Read a coverage-test table's column order off its own header.

    Never inferred from position or from which section it is: the Executive
    Summary and the detail pages state the same pairs in opposite orders, so a
    parser that assumed either would silently swap a computed ratio with the
    level it must clear. An unrecognised header is refused, because reading two
    percentages in an unknown order is not a degraded answer — it is a wrong one.
    """
    found: set[ColumnOrder] = set()
    for lines in pages:
        for line in lines:
            squashed = _squash(line).upper()
            for marker, order in layout.column_order_markers.items():
                if marker in squashed:
                    found.add(order)
    if len(found) == 1:
        return found.pop()
    if not found:
        raise ValueError(
            f"{section}: no recognised coverage-test column header. Expected one "
            f"of {sorted(layout.column_order_markers)}; refusing to read two percentage "
            "columns in a guessed order."
        )
    raise ValueError(
        f"{section}: the section states two different column orders "
        f"({sorted(o.value for o in found)}); refusing rather than picking one."
    )


def _parse_note_classes(pages: list[list[str]], layout: DocumentLayout) -> list[NoteClassFigures]:
    """Every ``Class <label> Notes`` row the Executive Summary states.

    Deliberately does **not** de-duplicate. A class appearing twice would make
    the balances sum past the report's stated total, and the reconciliation
    below refuses on exactly that — where silently keeping the first occurrence
    would return a plausible tape built from a page read twice.
    """
    classes: list[NoteClassFigures] = []
    for lines in pages:
        for line in lines:
            for match in _NOTE_CLASS_ROW_RE.finditer(line):
                coupon = match.group("coupon")
                interest = match.group("interest")
                classes.append(
                    NoteClassFigures(
                        note_class=match.group("label"),
                        principal_balance=_decimal(match.group("balance")),
                        coupon_pct=None if coupon == "N/A" else _decimal(coupon),
                        periodic_interest=None if interest == "N/A" else _decimal(interest),
                    )
                )
    return classes


def _parse_coverage_tests(
    pages: list[list[str]], layout: DocumentLayout, section: str
) -> list[CoverageTestResult]:
    """Every coverage test one section states, read in that section's own order."""
    order = _column_order(pages, layout, section)
    results: list[CoverageTestResult] = []
    for lines in pages:
        for line in lines:
            for match in _COVERAGE_TEST_RE.finditer(line):
                first = _decimal(match.group("first"))
                second = _decimal(match.group("second"))
                if order is ColumnOrder.REQUIRED_FIRST:
                    required, current = first, second
                else:
                    current, required = first, second
                results.append(
                    CoverageTestResult(
                        name=" ".join(match.group("name").split()),
                        current_pct=current,
                        required_pct=required,
                        result=CoverageTestOutcome(match.group("result")),
                        stated_in=section,
                    )
                )
    return results


def _parse_stated_totals(
    pages: list[list[str]], layout: DocumentLayout
) -> tuple[Decimal | None, Decimal | None]:
    """The aggregate balance and periodic interest the Executive Summary states.

    Two distinct candidate lines mean the section states its totals twice and
    disagrees with itself; that is refused rather than resolved by taking the
    first, since neither is more authoritative than the other.
    """
    seen: list[tuple[Decimal, Decimal]] = []
    for lines in pages:
        for line in lines:
            match = _STATED_TOTALS_RE.match(line.strip())
            if match:
                pair = (_decimal(match.group("balance")), _decimal(match.group("interest")))
                if pair not in seen:
                    seen.append(pair)
    if not seen:
        return None, None
    if len(seen) > 1:
        raise ValueError(
            "Executive Summary states more than one distinct pair of aggregate "
            f"totals ({seen}); refusing rather than choosing between them."
        )
    return seen[0]


def _decimal(token: str) -> Decimal:
    """A report money/percentage token as an exact :class:`~decimal.Decimal`."""
    return Decimal(token.replace(",", ""))


def parse_liability_summary_text(
    text: str,
    *,
    period_label: str,
    strict: bool = True,
) -> ReportLiabilitySummary:
    """Parse a trustee report's text into its liability-side summary.

    Pure and deterministic — no network, no LLM, and no second reader: the text
    is the output of :func:`extract_report_lines`, the same seam the collateral
    schedule is parsed from. Sections are located by header text, never by page
    number.

    With ``strict`` (the default) the summary is reconciled against the report's
    own stated totals and its own second rendering of the coverage tests, and a
    divergence raises :class:`LiabilitySummaryReconciliationError`. Pass
    ``strict=False`` only to inspect a failing parse.
    """
    pages = _split_pages(text)
    if not pages:
        raise ValueError("no pages found — text is not extracted trustee-report output")

    layout = _resolve_layout(pages)

    deal_name, reporting_date = _report_header(pages)

    exec_pages = _pages_for(pages, layout, SECTION_EXEC_SUMMARY)
    par_value_pages = _pages_for(pages, layout, SECTION_PAR_VALUE_DETAIL)
    ic_pages = _pages_for(pages, layout, SECTION_IC_DETAIL)
    if not exec_pages:
        raise ValueError(
            "report has no Executive Summary section (located by header, not page "
            "number) — the per-class balances and coupons are stated there"
        )
    if not (par_value_pages or ic_pages):
        raise ValueError(
            "report has no coverage-test detail section (located by header, not "
            "page number) — the required levels are stated there"
        )

    stated_balance, stated_interest = _parse_stated_totals(exec_pages, layout)
    detail_tests: list[CoverageTestResult] = []
    if par_value_pages:
        detail_tests += _parse_coverage_tests(
            par_value_pages, layout, layout.title(SECTION_PAR_VALUE_DETAIL)
        )
    if ic_pages:
        detail_tests += _parse_coverage_tests(ic_pages, layout, layout.title(SECTION_IC_DETAIL))

    summary = ReportLiabilitySummary(
        deal_name=deal_name,
        period_label=period_label,
        reporting_date=reporting_date,
        note_classes=_parse_note_classes(exec_pages, layout),
        coverage_tests=detail_tests,
        summary_coverage_tests=_parse_coverage_tests(
            exec_pages, layout, layout.title(SECTION_EXEC_SUMMARY)
        ),
        stated_total_balance=stated_balance,
        stated_total_periodic_interest=stated_interest,
    )
    summary._section_titles = dict(layout.section_titles)
    if strict:
        reconciliation = reconcile_liability_summary(summary)
        if not reconciliation.ok:
            raise LiabilitySummaryReconciliationError(reconciliation)
        summary.reconciled = True
    return summary


def reconcile_liability_summary(summary: ReportLiabilitySummary) -> ScheduleReconciliation:
    """Tie the liability summary back to what the report states about itself.

    Four families of check, and the last two are the ones that matter:

    - the per-class balances sum to the stated aggregate balance;
    - the stated periodic interest sums to the stated aggregate interest —
      a second oracle over a different quantity, so two transposed class rows
      cannot satisfy both;
    - the Executive Summary and the detail pages name the **same** set of
      coverage tests, so a test that failed to parse in one rendering cannot be
      quietly absent from the result;
    - and for each, the two renderings agree on the required level, the current
      level and the outcome — despite stating them in opposite column order.
    """
    checks: list[ReconciliationCheck] = []

    if summary.stated_total_balance is not None:
        checks.append(
            _check(
                "stated total note balance",
                summary.stated_total_balance,
                summary.total_note_balance,
            )
        )
    if summary.stated_total_periodic_interest is not None:
        checks.append(
            _check(
                "stated total periodic interest",
                summary.stated_total_periodic_interest,
                summary.total_periodic_interest,
            )
        )

    detail = {t.trigger_key: t for t in summary.coverage_tests}
    stated = {t.trigger_key: t for t in summary.summary_coverage_tests}
    checks.append(
        _check("coverage tests named in both renderings", sorted(stated), sorted(detail))
    )
    for key in sorted(set(detail) & set(stated)):
        checks.append(
            _check(f"required level · {key}", stated[key].required_pct, detail[key].required_pct)
        )
        checks.append(
            _check(f"current level · {key}", stated[key].current_pct, detail[key].current_pct)
        )
        checks.append(_check(f"result · {key}", stated[key].result, detail[key].result))

    return ScheduleReconciliation(checks=checks)


def liability_provenance(summary: ReportLiabilitySummary) -> ProvenanceMap:
    """Per-field provenance for every figure this summary states.

    Keyed by dotted field path, the sidecar shape
    :mod:`loanwhiz.domain.provenance` already defines — this adds no parallel
    provenance record.

    **There is no ``source`` parameter, and no ``reconciled`` one either** —
    that is the point. Every entry carries ``source="report"``, so a coupon
    lifted from a trustee report cannot be presented as an extracted prospectus
    term by any caller. ``reconciled`` is read off the summary, where the parser
    recorded whether the check actually ran and passed: it is the signal the
    human-review gate routes *unreconciled, low-confidence* fields to a person
    by, so a caller able to assert it could route a never-verified figure past
    the reviewer who would have caught it.

    A class whose coupon or periodic interest the report does not state gets
    **no key** for it, rather than a key with a null value: absence of a fact
    and a fact that happens to be absent read identically once a map has an
    entry for both.
    """
    # Imported here rather than at module scope: ``loanwhiz.domain``'s package
    # ``__init__`` participates in an import cycle with ``loanwhiz.primitives``,
    # and this module is imported by ``collateral_tape_mapping``, which is
    # itself on the domain side of that cycle. Deferring costs one lookup per
    # call and keeps this module importable from anywhere.
    from loanwhiz.domain.provenance import FieldProvenance

    document = _report_document_name(summary.deal_name, summary.period_label)

    def entry(section: str, excerpt: str) -> FieldProvenance:
        return FieldProvenance(
            source=_LIABILITY_PROVENANCE_SOURCE,
            method="deterministic",
            confidence=_DETERMINISTIC_CONFIDENCE,
            citation=Citation(document=document, page_or_row=section, excerpt=excerpt),
            reconciled=summary.reconciled,
        )

    # The section as the report prints it, carried on the summary by the parser
    # that detected the family. A summary not produced by that parser has no
    # document behind it, so the role key stands in — it is a locator, never a
    # figure, and every caller of this function parses first.
    exec_section = summary._section_titles.get(SECTION_EXEC_SUMMARY, SECTION_EXEC_SUMMARY)

    provenance: ProvenanceMap = {}
    for note in summary.note_classes:
        base = f"tranches.{note.class_key}"
        provenance[f"{base}.principal_balance"] = entry(
            exec_section,
            f"Class {note.note_class} principal balance stated by the trustee.",
        )
        if note.coupon_pct is not None:
            provenance[f"{base}.coupon_pct"] = entry(
                exec_section,
                f"Class {note.note_class} current coupon as resolved and stated by "
                "the trustee for this period — a report-derived rate, not the "
                "prospectus's index-plus-margin term.",
            )
        if note.periodic_interest is not None:
            provenance[f"{base}.periodic_interest"] = entry(
                exec_section,
                f"Class {note.note_class} interest for the period, stated by the trustee.",
            )

    for test in summary.coverage_tests:
        base = f"covenants.{test.trigger_key}"
        provenance[f"{base}.required_pct"] = entry(
            test.stated_in,
            f"{test.name} required level, stated beside the computed ratio and "
            "cross-checked against the Executive Summary's own statement of it.",
        )
        provenance[f"{base}.current_pct"] = entry(
            test.stated_in, f"{test.name} ratio as computed and stated by the trustee."
        )
    return provenance



def parse_liability_summary_text_result(
    text: str,
    *,
    period_label: str,
) -> PrimitiveResult[ReportLiabilitySummary]:
    """Envelope-returning wrapper over :func:`parse_liability_summary_text`.

    The parse is deterministic *and* reconciled against the report's own stated
    totals and its own second rendering of the coverage tests before it is
    returned, so the envelope confidence is ``1.0`` — the framework's rule-based
    convention. One :class:`Citation` per source section.
    """
    started = time.perf_counter()
    parse_input = LiabilitySummaryParseInput(period_label=period_label, text=text)
    summary = parse_liability_summary_text(text, period_label=period_label)
    duration_ms = (time.perf_counter() - started) * 1000.0
    layout = _resolve_layout(_split_pages(text))

    document = _report_document_name(summary.deal_name, period_label)
    citations = [
        Citation(
            document=document,
            page_or_row=section,
            excerpt=(
                "Liability-side figures parsed deterministically from the extracted "
                "trustee-report text and reconciled against the report's own stated "
                "totals and its second rendering of the same coverage tests."
            ),
        )
        for section in (
            layout.title(SECTION_EXEC_SUMMARY),
            layout.title(SECTION_PAR_VALUE_DETAIL),
            layout.title(SECTION_IC_DETAIL),
        )
    ]
    audit = AuditEntry.now(
        primitive_name=_PRIMITIVE_NAME,
        version=_PRIMITIVE_VERSION,
        input_hash=parse_input.input_hash(),
        duration_ms=duration_ms,
    )
    return PrimitiveResult[ReportLiabilitySummary](
        output=summary,
        confidence=_DETERMINISTIC_CONFIDENCE,
        citations=citations,
        audit_entry=audit,
    )
