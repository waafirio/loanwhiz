"""Trustee-report family registry — the single resolution point for CLO reports.

LoanWhiz reads CLO trustee reports published on Euronext Dublin. There is no
standard for them: each **collateral administrator** publishes its own layout,
with its own section titles, its own page furniture, and its own column orders.
A report's text is only meaningful relative to the family it was published
under.

Why this module exists
----------------------
:mod:`loanwhiz.primitives.collateral_schedule_parser` and
:mod:`loanwhiz.primitives.note_valuation_parser` were both written against one
administrator's reports (U.S. Bank, via Cairn CLO XVII) and carried its layout
as module-level constants. ``.claude/euronext-doc-api.md`` records four distinct
families already — U.S. Bank, BNY Mellon, Deutsche Bank, Citi+Virtus — so the
second administrator would have been a copy of each parser, and the third would
then have been guaranteed.

This module makes the **family the unit of registration and the scope of
layout resolution**, the same shape
:mod:`loanwhiz.domain.esma_annex_registry` uses for ESMA annexes. One
:class:`TrusteeReportFamily` carries, on a single record:

- how to *detect* the family (:attr:`TrusteeReportFamily.header_signature`), and
- how to *parse* it (:attr:`TrusteeReportFamily.documents`).

Because both live on one record they cannot drift, and
:meth:`TrusteeReportFamilyRegistry.register` refuses a family whose layout does
not cover every section its parsers read — so the incomplete-table failure below
is not registrable.

Two contracts callers depend on
-------------------------------
**An unrecognised report is refused, never parsed as a default family.** This is
the #494 lesson generalised: matching a document against a header phrase is the
same class of matching as filtering a row against a furniture prefix, and a
near-miss there ate a real payee row whose text began like the page footer. A
report that matches no registered signature would, under a default, be parsed
with the wrong section titles and the wrong column orders — and would very often
still *reconcile*, because reconciliation is an oracle about internal
consistency. :meth:`TrusteeReportFamilyRegistry.detect` therefore returns
``None`` and its callers raise :class:`UnknownReportFamilyError`.

**A family whose section table is incomplete fails at registration.** This is
the #480 lesson: a ``_SECTION_TITLES`` list that omits a section silently drops
that section's rows, and a section that parsed nothing reconciles vacuously —
"nothing is wrong" and "I saw nothing" produce the same output. Per-family
tables inherit that hazard and make it sharper, because the *existing* family is
protected by fixtures no new family has: U.S. Bank's titles are all exercised by
Cairn's committed reports, so dropping one reds the suite, while a newly
registered family has no fixtures at all and nothing but this guard between an
omitted title and a quietly partial parse. Registration therefore requires every
key in :data:`REQUIRED_SECTION_KEYS` for every document kind, and refuses rather
than accepting a subset.

Adding an administrator is a **table, not a code change**: write a module with a
:class:`TrusteeReportFamily`, call :func:`register_family`, and import it from
:mod:`loanwhiz.domain.trustee_report_families`. Neither parser changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Mapping

# ---------------------------------------------------------------------------
# Document kinds and section keys
# ---------------------------------------------------------------------------


class DocumentKind(str, Enum):
    """The kinds of trustee report LoanWhiz parses.

    A family publishes a layout per kind because the same administrator's
    monthly and payment-date reports do not share section titles or furniture.
    """

    #: The periodic collateral/compliance report — ``collateral_schedule_parser``.
    MONTHLY_REPORT = "monthly_report"

    #: The payment-date valuation report — ``note_valuation_parser``.
    NOTE_VALUATION_REPORT = "note_valuation_report"


# Section keys for DocumentKind.MONTHLY_REPORT. These are LoanWhiz's *roles*,
# stable across administrators; the printed title each maps to is what varies.
SECTION_ASSET_PART_I = "asset_part_i"
SECTION_ASSET_PART_II = "asset_part_ii"
SECTION_ASSET_PART_III = "asset_part_iii"
SECTION_CCC = "ccc_obligations"
SECTION_COUNTRY = "country_concentration"
SECTION_SP_INDUSTRY = "sp_industry_concentration"
SECTION_FITCH_INDUSTRY = "fitch_industry_concentration"
SECTION_SP_RATING = "sp_rating_stratification"
SECTION_PROFILE_TESTS = "portfolio_profile_tests"
SECTION_EXEC_SUMMARY = "executive_summary"
SECTION_PAR_VALUE_DETAIL = "par_value_tests_detail"
SECTION_IC_DETAIL = "interest_coverage_tests_detail"
SECTION_ACCRUAL_DETAIL = "interest_accrual_detail"
SECTION_ASSET_PART_IV = "asset_part_iv"

# Section keys for DocumentKind.NOTE_VALUATION_REPORT.
SECTION_NV_EXECUTIVE = "executive_summary"
SECTION_NV_DISTRIBUTION = "distribution_summary"
SECTION_NV_INTEREST_POP = "interest_priority_of_payments"
SECTION_NV_PRINCIPAL_POP = "principal_priority_of_payments"

#: Every section key each parser reads, and therefore every one a family must
#: give a printed title for. **This is the guard's contract**: a key added here
#: because a parser learned to read a new section immediately makes every
#: registered family that lacks it fail at import — loudly, at the boundary,
#: rather than by silently dropping that section's rows on one administrator.
REQUIRED_SECTION_KEYS: Mapping[DocumentKind, frozenset[str]] = MappingProxyType(
    {
        DocumentKind.MONTHLY_REPORT: frozenset(
            {
                SECTION_ASSET_PART_I,
                SECTION_ASSET_PART_II,
                SECTION_ASSET_PART_III,
                SECTION_CCC,
                SECTION_COUNTRY,
                SECTION_SP_INDUSTRY,
                SECTION_FITCH_INDUSTRY,
                SECTION_SP_RATING,
                SECTION_PROFILE_TESTS,
                SECTION_EXEC_SUMMARY,
                SECTION_PAR_VALUE_DETAIL,
                SECTION_IC_DETAIL,
                SECTION_ACCRUAL_DETAIL,
                SECTION_ASSET_PART_IV,
            }
        ),
        DocumentKind.NOTE_VALUATION_REPORT: frozenset(
            {
                SECTION_NV_EXECUTIVE,
                SECTION_NV_DISTRIBUTION,
                SECTION_NV_INTEREST_POP,
                SECTION_NV_PRINCIPAL_POP,
            }
        ),
    }
)


#: The ``NotesCashPeriod`` fields the note-valuation parser fills from a
#: family's declared waterfalls. Same contract as :data:`REQUIRED_SECTION_KEYS`,
#: one level down: the parser looks each of these up by name, so a family that
#: declares only one of them would fail with a bare ``KeyError`` deep inside the
#: parse instead of at the boundary, naming nothing.
REQUIRED_WATERFALL_FIELDS: frozenset[str] = frozenset({"revenue", "redemption"})


class CountGrain(str, Enum):
    """What an aggregate table's ``# of Assets`` column actually counts.

    A concentration table states a count beside a balance, and the obvious
    reading — that both describe the same population — is an assumption, not a
    fact about the document. Contego CLO XI's five aggregate tables state
    ``212`` against ``373,537,007.35`` while its three asset sections each
    carry 177 identifiers at that identical balance: the balance column is at
    asset grain and the count column is not. The 212 is the report's
    ``Interest Accrual Detail`` row count — one record per asset per rate
    contract, so an asset accruing under two contracts is two rows.

    Reading 212 as an asset count is how #468 happens quietly: par alone cannot
    see a missed row, because a row you drop can be worth zero, so the count is
    the half of the oracle that catches it — and a count compared against the
    wrong population catches nothing while looking like it does.

    ``ASSET`` is U.S. Bank's shape and the default. ``ACCRUAL_RECORD`` is BNY
    Mellon's; a family declaring it must publish
    :data:`SECTION_ACCRUAL_DETAIL`, since that is the population its stated
    count has to be reconciled against.
    """

    ASSET = "asset"
    ACCRUAL_RECORD = "accrual-record"


class ColumnOrder(str, Enum):
    """Which of a coverage-test table's two percentage columns comes first.

    The report states each coverage test's required level and computed ratio
    twice, in opposite column orders, and the two are like-typed: either order,
    assumed, swaps them in one place and reports a breaching test as passing
    (#480). The order is therefore read off the table's own header, never off
    which section it sits in.
    """

    #: ``Test Description | Threshold | Current | Result`` — the Executive Summary.
    REQUIRED_FIRST = "required-first"

    #: ``… TEST | RATIO | REQUIRED LEVEL | CALCULATION | RESULT`` — detail pages.
    RATIO_FIRST = "ratio-first"


class FurnitureOrder(str, Enum):
    """Whether a section's rows are tested for data before or after furniture.

    The two parsers genuinely differ here, and the difference is load-bearing
    rather than incidental, so the family record states it instead of leaving it
    an undocumented per-module convention.

    ``DATA_FIRST`` is the #494 ordering: ask "is this a data row?" (does it carry
    the anchored money tail?) *before* "is this page furniture?", because the
    U.S. Bank page footer ``U.S. Bank Global Corporate Trust`` also opens the
    real payee row ``U.S. Bank Global Corporate Trust Limited 15,818.69 …``. A
    prefix list is a heuristic about layout; a row's numeric tail is evidence
    about content, and evidence outranks heuristics.

    ``FURNITURE_FIRST`` is safe only where every data row is identified by an
    opening token no furniture prefix can produce — in the collateral schedule,
    an asset identifier. It is recorded rather than assumed so that a family
    whose furniture *can* collide with a row opener is a data change here, not a
    silent re-run of the defect.
    """

    DATA_FIRST = "data-first"
    FURNITURE_FIRST = "furniture-first"


class RowGeometry(str, Enum):
    """How a section's data rows survive text extraction.

    Not a stylistic difference: it decides whether a per-row line exists to be
    matched at all. ``extract_report_lines`` rebuilds lines from text-run
    coordinates, and what that yields depends on how the administrator's PDF
    lays the table out.

    ``ROW_PER_LINE`` is U.S. Bank's shape — one asset per line, so a row is a
    line and the parser iterates lines directly.

    ``REFLOWED_ROWS`` is BNY Mellon's — the page's whole table arrives as one
    long line, row-major, alongside a column-major stack of the same cells that
    is *not* safely zippable (a blank cell desyncs every column after it). The
    parser must therefore re-cut rows out of that line before any per-row test
    applies. Recorded on the family rather than sniffed, so a document whose
    geometry changes is a data change here, not a silently empty parse.
    """

    ROW_PER_LINE = "row-per-line"
    REFLOWED_ROWS = "reflowed-rows"


class IdentifierPosition(str, Enum):
    """Where a data row carries its asset identifier.

    U.S. Bank opens each row with the identifier (``LX189634 BVI Medical …``),
    so an anchored match both finds the id and proves the line is a row. BNY
    Mellon opens with the obligor description and prints the identifier mid-row
    (``Aenova Holding GmbH - Facility B Loan LX237502 Term Loan …``), so an
    anchored pattern matches nothing and the section parses as empty — the #494
    vacuous-reconciliation failure, reached by a different route.

    ``LINE_START`` keeps the anchored match, which stays the stronger test where
    it holds; ``EMBEDDED`` searches the row instead.
    """

    LINE_START = "line-start"
    EMBEDDED = "embedded"


# ---------------------------------------------------------------------------
# Layout record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DocumentLayout:
    """How one family's one document kind is laid out on the page.

    Deliberately validated at *registration* rather than in ``__post_init__``:
    a hand-built incomplete layout must remain constructible so tests can drive
    the parsers' own runtime presence checks — the defence in depth behind this
    guard — without going through the registry.

    Attributes:
        section_titles:
            Section key → the title this family prints for it. Keys are the
            role names above; every key in :data:`REQUIRED_SECTION_KEYS` for
            this kind must be present (enforced at registration).
        furniture_prefixes:
            Line prefixes that mark repeated page furniture — banners, footers,
            the date header, column-header runs.
        furniture_order:
            Whether furniture is filtered before or after the data-row test.
            See :class:`FurnitureOrder`; ``DATA_FIRST`` is the #494 ordering.
        column_order_markers:
            Whitespace-stripped, upper-cased header fingerprint → the column
            order it denotes. Exhaustive by construction: a coverage-test table
            whose header matches no entry is refused rather than read in a
            guessed order.
        waterfalls:
            ``(section key, NotesCashPeriod field)`` pairs, in the order the
            report prints them. Empty for kinds that publish no waterfall.
        unpublished_sections:
            Section key → why this administrator's document does not carry it.
            The declared-absence channel: a required section may be *stated
            absent with a reason* instead of given a title, but never simply
            omitted. BNY Mellon publishes no country stratification at all (its
            country limits are compliance-test rows, a different datum), where
            U.S. Bank prints one. Registration refuses an undeclared omission
            exactly as before, so this widens what a family may *say*, not what
            it may leave unsaid.
        section_table_markers:
            Section key → a fingerprint of the table's own sub-header, for the
            sections whose printed title they share. BNY Mellon prints both the
            S&P and the Fitch industry table under one ``Industry
            Concentrations`` heading, so the title alone cannot route them and
            the pair (title, marker) is what must be unique.
        row_geometry:
            Whether a data row survives extraction as its own line. See
            :class:`RowGeometry`.
        identifier_position:
            Whether a row opens with its asset identifier. See
            :class:`IdentifierPosition`.
        count_grain:
            What this family's aggregate tables count beside the balance they
            state. See :class:`CountGrain`; the balance is always at asset
            grain, so a family whose count is not tells the reconciliation to
            compare it against a different population rather than against the
            asset count.
    """

    section_titles: Mapping[str, str]
    furniture_prefixes: tuple[str, ...] = ()
    furniture_order: FurnitureOrder = FurnitureOrder.DATA_FIRST
    column_order_markers: Mapping[str, ColumnOrder] = field(
        default_factory=lambda: MappingProxyType({})
    )
    waterfalls: tuple[tuple[str, str], ...] = ()
    unpublished_sections: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    section_table_markers: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )
    row_geometry: RowGeometry = RowGeometry.ROW_PER_LINE
    identifier_position: IdentifierPosition = IdentifierPosition.LINE_START
    count_grain: CountGrain = CountGrain.ASSET

    def publishes(self, section_key: str) -> bool:
        """Whether this family's document publishes *section_key* at all.

        The question a consumer must ask before reading zero rows as zero. A
        section declared in :attr:`unpublished_sections` is absent from the
        document by fact, not empty in it — the distinction #451 drew for tape
        columns (``AbsentColumn``) and #494 for whole sections.
        """
        return section_key in self.section_titles

    def unpublished_reason(self, section_key: str) -> str:
        """Why this family does not publish *section_key*.

        Raises:
            KeyError: when the section is not declared unpublished — asking for
                a reason that does not exist is a bug in the caller, not an
                empty string to render.
        """
        return self.unpublished_sections[section_key]

    def table_marker(self, section_key: str) -> str | None:
        """The table fingerprint distinguishing *section_key* within its title.

        ``None`` when the section's printed title already identifies it
        uniquely, which is the common case. See :attr:`section_table_markers`.
        """
        return self.section_table_markers.get(section_key)

    def title(self, section_key: str) -> str:
        """The printed title this family uses for *section_key*.

        Raises:
            KeyError: when the family does not declare the section. Registration
                makes this unreachable for a required key, so it firing means a
                parser read a section nobody declared — a bug worth an exception
                rather than a ``None`` that routes to no pages.
        """
        if section_key in self.unpublished_sections:
            raise KeyError(
                f"{section_key!r} is declared unpublished by this family "
                f"({self.unpublished_sections[section_key]}); ask publishes() "
                "before routing, so an absent section is never read as an empty one"
            )
        return self.section_titles[section_key]

    @property
    def titles(self) -> tuple[str, ...]:
        """Every printed section title, for routing and furniture matching.

        Sorted longest-first so a title that is a prefix of another can never
        shadow it — ``Current Asset Characteristics - Part I`` must not claim
        the ``… - Part III`` pages.
        """
        return tuple(
            sorted(set(self.section_titles.values()), key=len, reverse=True)
        )


# ---------------------------------------------------------------------------
# Family record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrusteeReportFamily:
    """One collateral administrator's report family: how to detect and parse it.

    Attributes:
        family_id: Stable machine identifier, e.g. ``"us_bank"``. Unique in a registry.
        label: Human-readable label, e.g. ``"U.S. Bank"``.
        administrator: The collateral administrator's name as reports print it.
        header_signature:
            Phrases whose **joint** presence in the report's own first page
            identifies this family. Matched case-insensitively. Every phrase
            must appear: a single generic phrase would match other families'
            reports, which is the misdetection this record exists to prevent.
        documents: Document kind → its layout.
    """

    family_id: str
    label: str
    administrator: str
    header_signature: frozenset[str]
    documents: Mapping[DocumentKind, DocumentLayout]

    def layout(self, kind: DocumentKind) -> DocumentLayout:
        """This family's layout for *kind*.

        Raises:
            KeyError: when the family declares no layout for that kind.
                Registration requires every kind, so this is unreachable for a
                registered family.
        """
        return self.documents[kind]

    def matches(self, header_text: str) -> bool:
        """Whether *header_text* carries every phrase in this family's signature.

        Case-insensitive, and a conjunction rather than a disjunction: one
        phrase in common is how two administrators' reports collide.
        """
        lowered = header_text.lower()
        return all(phrase.lower() in lowered for phrase in self.header_signature)


class UnknownReportFamilyError(ValueError):
    """Raised when a report matches no registered family.

    Deliberately an error rather than a degraded parse. A trustee report parsed
    under the wrong family's section titles does not fail loudly — it finds no
    sections, parses nothing, and then reconciles vacuously against its own
    empty result (#494). Refusing is the only outcome that distinguishes "this
    document says nothing" from "I could not read this document".
    """


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TrusteeReportFamilyRegistry:
    """Ordered registry of :class:`TrusteeReportFamily`, validated at registration.

    Detection walks families in registration order and returns the first match,
    so ordering is deterministic and reviewable rather than dict-hash dependent.
    """

    def __init__(self) -> None:
        self._families: list[TrusteeReportFamily] = []

    def register(self, family: TrusteeReportFamily) -> TrusteeReportFamily:
        """Validate and register *family*; returns it so modules can assign the result.

        Raises:
            ValueError: on any condition below. Each is a mistake that would
                otherwise surface as a silently partial or misattributed parse
                much later, so it fails loudly here at the boundary.
        """
        if any(f.family_id == family.family_id for f in self._families):
            raise ValueError(f"family_id {family.family_id!r} is already registered")
        if any(f.label == family.label for f in self._families):
            raise ValueError(f"family label {family.label!r} is already registered")
        if not family.header_signature:
            raise ValueError(
                f"{family.family_id}: header_signature is empty — the family "
                "could never be detected, and an empty conjunction would match "
                "every report"
            )
        if any(not phrase.strip() for phrase in family.header_signature):
            raise ValueError(
                f"{family.family_id}: header_signature contains a blank phrase, "
                "which matches every report"
            )

        # A signature that is a subset of an already-registered family's would
        # shadow it: detection returns the first match, so the broader signature
        # would claim the narrower family's reports depending on registration
        # order. Refuse rather than resolve by convention.
        for other in self._families:
            lowered = {p.lower() for p in family.header_signature}
            other_lowered = {p.lower() for p in other.header_signature}
            if lowered <= other_lowered or other_lowered <= lowered:
                raise ValueError(
                    f"{family.family_id}: header_signature is comparable to "
                    f"{other.family_id}'s ({sorted(other_lowered)}) — one would "
                    "shadow the other, and which wins would depend on "
                    "registration order"
                )

        for kind, required in REQUIRED_SECTION_KEYS.items():
            layout = family.documents.get(kind)
            if layout is None:
                raise ValueError(
                    f"{family.family_id}: no layout for {kind.value} — both "
                    "parsers read every registered family, so a missing kind is "
                    "a parse that would fail at run time on a real document"
                )

            # The #480 guard. An omitted section title does not fail: it routes
            # to no pages, parses no rows, and reconciles vacuously against the
            # nothing it found. The existing family is shielded by its fixtures;
            # a new one has none, so this is the only thing standing between an
            # omitted title and a quietly partial parse.
            # Declared absence is the one sanctioned alternative to a title
            # (#494): an administrator that genuinely does not publish a section
            # says so with a reason, and consumers read that as "absent" rather
            # than as zero rows. Silence is still refused — the whole point is
            # that "I do not publish this" and "I forgot this" stop looking
            # alike.
            declared_absent = set(layout.unpublished_sections)
            missing = sorted(required - set(layout.section_titles) - declared_absent)
            if missing:
                raise ValueError(
                    f"{family.family_id}/{kind.value}: section_titles is missing "
                    f"{missing} — the section would route to no pages, parse no "
                    "rows, and reconcile vacuously against its own empty result. "
                    "Give every required section its printed title, or declare it "
                    "in unpublished_sections with the reason this administrator "
                    "does not print it."
                )

            # A family whose stated counts are not asset counts must publish
            # the population they *are*, or its count oracle is unsatisfiable
            # and the reconciliation silently degrades to a balance-only check
            # — the #468 failure, since a dropped row can be worth zero and
            # only the count would have caught it.
            if (
                layout.count_grain is CountGrain.ACCRUAL_RECORD
                and SECTION_ACCRUAL_DETAIL not in layout.section_titles
            ):
                raise ValueError(
                    f"{family.family_id}/{kind.value}: count_grain is "
                    "accrual-record but no title is given for "
                    f"{SECTION_ACCRUAL_DETAIL!r} — the stated count would have "
                    "no population to reconcile against, leaving par as the "
                    "only check and a zero-balance row free to go missing"
                )

            contradictory = sorted(declared_absent & set(layout.section_titles))
            if contradictory:
                raise ValueError(
                    f"{family.family_id}/{kind.value}: section(s) {contradictory} "
                    "are both given a printed title and declared unpublished — "
                    "the document either carries the section or it does not, and "
                    "which reading won would depend on the consumer"
                )

            unknown_absent = sorted(declared_absent - required)
            if unknown_absent:
                raise ValueError(
                    f"{family.family_id}/{kind.value}: unpublished_sections "
                    f"declares {unknown_absent}, which no parser reads for this "
                    "kind — an absence nobody asks about states nothing, and is "
                    "usually a typo for a key that is required"
                )

            thin = sorted(
                key
                for key, reason in layout.unpublished_sections.items()
                if len(reason.strip()) < 20
            )
            if thin:
                raise ValueError(
                    f"{family.family_id}/{kind.value}: unpublished_sections "
                    f"{thin} give no usable reason — say what the document "
                    "carries instead, never merely that the datum is missing"
                )

            blank = sorted(k for k, v in layout.section_titles.items() if not v.strip())
            if blank:
                raise ValueError(
                    f"{family.family_id}/{kind.value}: blank section title for "
                    f"{blank} — an empty title matches every page"
                )

            # Two sections printed under one title make routing ambiguous: both
            # would claim the same pages, and which won would depend on dict
            # order. That is the ambiguity this registry exists to make
            # impossible, so it is refused rather than resolved by convention.
            # Two sections may share a printed title only when each names the
            # table that tells them apart, so what must be unique is the pair.
            # BNY Mellon prints the S&P and Fitch industry tables under one
            # heading; without a marker both would claim the same pages and
            # which won would depend on dict order.
            seen: dict[tuple[str, str | None], str] = {}
            for key, title in sorted(layout.section_titles.items()):
                location = (title, layout.section_table_markers.get(key))
                if location in seen:
                    marker = location[1]
                    detail = (
                        f"both print as {title!r} with no table marker to tell "
                        "them apart — give each the fingerprint of its own "
                        "table's sub-header in section_table_markers"
                        if marker is None
                        else f"both print as {title!r} under the same table "
                        f"marker {marker!r}"
                    )
                    raise ValueError(
                        f"{family.family_id}/{kind.value}: sections "
                        f"{seen[location]!r} and {key!r} {detail} — page routing "
                        "would be ambiguous"
                    )
                seen[location] = key

            stray_markers = sorted(
                set(layout.section_table_markers) - set(layout.section_titles)
            )
            if stray_markers:
                raise ValueError(
                    f"{family.family_id}/{kind.value}: section_table_markers "
                    f"names {stray_markers}, which this family gives no printed "
                    "title — a marker selects a table within a title it must have"
                )

            undeclared = sorted(
                key for key, _ in layout.waterfalls if key not in layout.section_titles
            )
            absent_waterfall = sorted(
                key for key, _ in layout.waterfalls if key in layout.unpublished_sections
            )
            if absent_waterfall:
                raise ValueError(
                    f"{family.family_id}/{kind.value}: waterfall section(s) "
                    f"{absent_waterfall} are declared unpublished — a waterfall "
                    "the parser must fill cannot be a section the document omits"
                )
            if undeclared:
                raise ValueError(
                    f"{family.family_id}/{kind.value}: waterfall section(s) "
                    f"{undeclared} have no declared title — the waterfall would "
                    "route to no pages"
                )

            if kind is DocumentKind.NOTE_VALUATION_REPORT:
                fields = [field_name for _, field_name in layout.waterfalls]
                duplicated = sorted({f for f in fields if fields.count(f) > 1})
                if duplicated:
                    raise ValueError(
                        f"{family.family_id}/{kind.value}: waterfall field(s) "
                        f"{duplicated} are declared twice — the second would "
                        "silently overwrite the first"
                    )
                absent = sorted(REQUIRED_WATERFALL_FIELDS - set(fields))
                if absent:
                    raise ValueError(
                        f"{family.family_id}/{kind.value}: no waterfall declared "
                        f"for {absent} — the parser looks each up by name, so "
                        "this would fail deep in the parse naming nothing"
                    )

        self._families.append(family)
        return family

    def detect(self, header_text: str) -> TrusteeReportFamily | None:
        """Return the first registered family whose signature *header_text* carries.

        ``None`` when no family matches — the caller must then refuse, rather
        than falling back to an arbitrary layout. See
        :class:`UnknownReportFamilyError`.
        """
        for family in self._families:
            if family.matches(header_text):
                return family
        return None

    def get(self, family_id: str) -> TrusteeReportFamily | None:
        """Return the registered family with this ``family_id``, or ``None``."""
        for family in self._families:
            if family.family_id == family_id:
                return family
        return None

    def all(self) -> tuple[TrusteeReportFamily, ...]:
        """Every registered family, in registration (detection) order."""
        return tuple(self._families)


#: The process-wide family registry. Populated by importing
#: :mod:`loanwhiz.domain.trustee_report_families`, which pulls in each family
#: table module.
FAMILY_REGISTRY = TrusteeReportFamilyRegistry()


def register_family(family: TrusteeReportFamily) -> TrusteeReportFamily:
    """Register *family* in the process-wide :data:`FAMILY_REGISTRY`."""
    return FAMILY_REGISTRY.register(family)
