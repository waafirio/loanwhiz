"""The trustee-report family registry: what it refuses, and why.

Two lessons this parser code has already paid for are the reason this registry
exists, and both are asserted here rather than left to the docstrings:

- **#480** — a section-title table that omits a section silently drops that
  section's rows, and a section that parsed nothing reconciles vacuously against
  its own empty result. Per-family tables inherit the hazard and sharpen it: the
  *existing* family is shielded by Cairn's committed fixtures, so dropping one
  of its titles reds the suite, but a newly registered family has no fixtures at
  all. Registration is the only thing between an omitted title and a quietly
  partial parse, so it must refuse.

- **#494** — filtering page furniture before testing a row for its money tail
  ate a real payee row whose text began like the page footer. Detecting a family
  from header text is the same class of matching, so an unrecognised header must
  refuse rather than fall through to a default family.

Registration tests use their own :class:`TrusteeReportFamilyRegistry` instance
rather than the process-wide one, so a refusal under test can never depend on,
or disturb, what the real registry holds.
"""

from __future__ import annotations

# Import a ``primitives`` module before ``loanwhiz.domain`` so the package-init
# graph resolves in the order that avoids a *pre-existing* circular import
# (``domain.__init__`` → ``inputs`` → ``provenance`` → ``primitives.base`` →
# ``primitives.__init__`` → … → ``domain.inputs``). Same guard, same reason as
# ``tests/test_esma_annex_registry.py``: production code always loads
# ``primitives`` first, and this keeps the module runnable in isolation.
import loanwhiz.primitives.base  # noqa: F401  (import-order guard, see above)

import dataclasses
from pathlib import Path

import re

import pytest

from loanwhiz.domain.trustee_report_families import BNY_MELLON, US_BANK
from loanwhiz.domain.trustee_report_registry import (
    FAMILY_REGISTRY,
    REQUIRED_SECTION_KEYS,
    SECTION_CCC,
    SECTION_COUNTRY,
    SECTION_FITCH_INDUSTRY,
    SECTION_NV_INTEREST_POP,
    SECTION_NV_PRINCIPAL_POP,
    SECTION_SP_INDUSTRY,
    CoverageTestRow,
    DocumentKind,
    DocumentLayout,
    FurnitureOrder,
    TrusteeReportFamily,
    TrusteeReportFamilyRegistry,
    UnknownReportFamilyError,
)
from loanwhiz.primitives.collateral_schedule_parser import parse_schedule_text
from loanwhiz.primitives.note_valuation_parser import parse_note_valuation_text

FIXTURE_DIR = Path(__file__).parent / "fixtures"
MONTHLY_FIXTURE = FIXTURE_DIR / "collateral_schedule" / "cairn-clo-xvii-march-2025.txt"

#: A row grammar for the hand-built layouts below. Its pattern is never matched
#: against a document — these tests drive registration and refusal paths — so it
#: only has to be well-formed: a name, a ratio, a required level, an outcome.
_ROW_GRAMMAR = CoverageTestRow(
    pattern=re.compile(
        r"(?P<name>\w+)\s+(?P<ratio>\d+\.\d{2})%\s+(?P<required>\d+\.\d{2})%\s+(?P<result>\w+)"
    ),
    ratio_group="ratio",
    required_group="required",
)
NOTE_VALUATION_FIXTURE = FIXTURE_DIR / "note_valuation" / "cairn-clo-xvii-january-2025.txt"


def _complete_family(**overrides) -> TrusteeReportFamily:
    """A minimal family that registers cleanly, for a test to then break one way.

    Built from the required keys rather than a hand-written list, so a section a
    parser learns to read later is automatically part of this fixture too.
    """
    defaults = dict(
        family_id="test_family",
        label="Test Family",
        administrator="Test Trust Company",
        header_signature=frozenset({"Test Trust Company", "testtrust.example"}),
        documents={
            DocumentKind.MONTHLY_REPORT: DocumentLayout(
                section_titles={
                    key: f"Printed {key}"
                    for key in REQUIRED_SECTION_KEYS[DocumentKind.MONTHLY_REPORT]
                },
                furniture_prefixes=("Page ",),
                furniture_order=FurnitureOrder.FURNITURE_FIRST,
                coverage_row_markers={"TESTHEADER": _ROW_GRAMMAR},
                report_header=US_BANK.layout(
                    DocumentKind.MONTHLY_REPORT
                ).report_header,
            ),
            DocumentKind.NOTE_VALUATION_REPORT: DocumentLayout(
                section_titles={
                    key: f"Printed {key}"
                    for key in REQUIRED_SECTION_KEYS[DocumentKind.NOTE_VALUATION_REPORT]
                },
                furniture_prefixes=("Page ",),
                furniture_order=FurnitureOrder.DATA_FIRST,
                waterfalls=(
                    (SECTION_NV_INTEREST_POP, "revenue"),
                    (SECTION_NV_PRINCIPAL_POP, "redemption"),
                ),
            ),
        },
    )
    defaults.update(overrides)
    return TrusteeReportFamily(**defaults)  # type: ignore[arg-type]


def _drop_monthly_section(family: TrusteeReportFamily, key: str) -> TrusteeReportFamily:
    """The same family with one monthly-report section title removed."""
    monthly = family.documents[DocumentKind.MONTHLY_REPORT]
    trimmed = dataclasses.replace(
        monthly,
        section_titles={k: v for k, v in monthly.section_titles.items() if k != key},
    )
    return dataclasses.replace(
        family, documents={**family.documents, DocumentKind.MONTHLY_REPORT: trimmed}
    )


# ---------------------------------------------------------------------------
# The #480 guard — an incomplete table must fail loudly, not parse a subset
# ---------------------------------------------------------------------------


def test_a_family_missing_a_required_section_is_refused_at_registration() -> None:
    """The #480 guard, on the surface that inherits the hazard.

    An omitted title does not fail on its own: the section routes to no pages,
    parses no rows, and then reconciles vacuously against the nothing it found.
    Cairn's fixtures catch it for U.S. Bank; nothing catches it for a family
    whose reports no test has ever seen, so registration has to.
    """
    registry = TrusteeReportFamilyRegistry()

    with pytest.raises(ValueError) as excinfo:
        registry.register(_drop_monthly_section(_complete_family(), SECTION_CCC))

    message = str(excinfo.value)
    assert SECTION_CCC in message, "the refusal must name the missing section"
    assert "vacuously" in message
    assert registry.all() == (), "a refused family must not be half-registered"


def test_every_required_section_is_individually_guarded() -> None:
    """No required section is exempt — dropping any one of them refuses.

    Asserting one section would leave the guard passing while covering a single
    key; this walks the whole contract, so a key added to
    ``REQUIRED_SECTION_KEYS`` is covered the moment it is added.
    """
    for key in sorted(REQUIRED_SECTION_KEYS[DocumentKind.MONTHLY_REPORT]):
        registry = TrusteeReportFamilyRegistry()
        with pytest.raises(ValueError, match=key):
            registry.register(_drop_monthly_section(_complete_family(), key))


def test_a_family_missing_a_whole_document_kind_is_refused() -> None:
    """Both parsers read every registered family, so a missing kind is a runtime failure."""
    registry = TrusteeReportFamilyRegistry()
    family = _complete_family()
    without_nv = dataclasses.replace(
        family,
        documents={DocumentKind.MONTHLY_REPORT: family.documents[DocumentKind.MONTHLY_REPORT]},
    )

    with pytest.raises(ValueError, match="note_valuation_report"):
        registry.register(without_nv)


def test_a_blank_section_title_is_refused() -> None:
    """An empty title matches every page, which is worse than matching none."""
    registry = TrusteeReportFamilyRegistry()
    family = _complete_family()
    monthly = family.documents[DocumentKind.MONTHLY_REPORT]
    blanked = dataclasses.replace(
        monthly, section_titles={**monthly.section_titles, SECTION_CCC: "   "}
    )

    with pytest.raises(ValueError, match="blank section title"):
        registry.register(
            dataclasses.replace(
                family, documents={**family.documents, DocumentKind.MONTHLY_REPORT: blanked}
            )
        )


def test_two_sections_printed_under_one_title_are_refused() -> None:
    """Ambiguous page routing is refused rather than resolved by dict order."""
    registry = TrusteeReportFamilyRegistry()
    family = _complete_family()
    monthly = family.documents[DocumentKind.MONTHLY_REPORT]
    titles = dict(monthly.section_titles)
    first, second = sorted(titles)[:2]
    titles[second] = titles[first]

    with pytest.raises(ValueError, match="ambiguous"):
        registry.register(
            dataclasses.replace(
                family,
                documents={
                    **family.documents,
                    DocumentKind.MONTHLY_REPORT: dataclasses.replace(
                        monthly, section_titles=titles
                    ),
                },
            )
        )


def test_a_waterfall_pointing_at_an_undeclared_section_is_refused() -> None:
    """A waterfall whose section has no title would route to no pages."""
    registry = TrusteeReportFamilyRegistry()
    family = _complete_family()
    nv = family.documents[DocumentKind.NOTE_VALUATION_REPORT]
    broken = dataclasses.replace(nv, waterfalls=(("a_section_nobody_declared", "revenue"),))

    with pytest.raises(ValueError, match="a_section_nobody_declared"):
        registry.register(
            dataclasses.replace(
                family,
                documents={**family.documents, DocumentKind.NOTE_VALUATION_REPORT: broken},
            )
        )


# ---------------------------------------------------------------------------
# Signature hygiene
# ---------------------------------------------------------------------------


def test_a_family_declaring_only_one_waterfall_is_refused() -> None:
    """The parser looks each waterfall up by name, so a gap must refuse here.

    Without this the omission surfaces as a bare ``KeyError`` deep inside the
    parse, naming neither the family nor the field — the boundary is where a
    registration mistake should be reported.
    """
    registry = TrusteeReportFamilyRegistry()
    family = _complete_family()
    nv = family.documents[DocumentKind.NOTE_VALUATION_REPORT]
    only_one = dataclasses.replace(
        nv, waterfalls=((SECTION_NV_PRINCIPAL_POP, "redemption"),)
    )

    with pytest.raises(ValueError, match="revenue"):
        registry.register(
            dataclasses.replace(
                family,
                documents={**family.documents, DocumentKind.NOTE_VALUATION_REPORT: only_one},
            )
        )


def test_a_waterfall_field_declared_twice_is_refused() -> None:
    """Two sections filling one field would silently overwrite each other."""
    registry = TrusteeReportFamilyRegistry()
    family = _complete_family()
    nv = family.documents[DocumentKind.NOTE_VALUATION_REPORT]
    duplicated = dataclasses.replace(
        nv,
        waterfalls=(
            (SECTION_NV_INTEREST_POP, "revenue"),
            (SECTION_NV_PRINCIPAL_POP, "revenue"),
        ),
    )

    with pytest.raises(ValueError, match="declared twice"):
        registry.register(
            dataclasses.replace(
                family,
                documents={**family.documents, DocumentKind.NOTE_VALUATION_REPORT: duplicated},
            )
        )


def test_an_empty_header_signature_is_refused() -> None:
    """An empty conjunction matches every report, so it would claim all of them."""
    registry = TrusteeReportFamilyRegistry()
    with pytest.raises(ValueError, match="header_signature is empty"):
        registry.register(_complete_family(header_signature=frozenset()))


def test_a_blank_signature_phrase_is_refused() -> None:
    """A blank phrase is in every document's header."""
    registry = TrusteeReportFamilyRegistry()
    with pytest.raises(ValueError, match="blank phrase"):
        registry.register(_complete_family(header_signature=frozenset({"Real Phrase", "  "})))


def test_a_signature_that_would_shadow_another_family_is_refused() -> None:
    """Detection returns the first match, so comparable signatures are order-dependent.

    A second administrator whose signature is a subset of the first's would be
    claimed by whichever registered earlier — a misdetection that then parses
    the report under another family's section titles.
    """
    registry = TrusteeReportFamilyRegistry()
    registry.register(_complete_family())

    with pytest.raises(ValueError, match="shadow"):
        registry.register(
            _complete_family(
                family_id="broader",
                label="Broader Family",
                header_signature=frozenset({"Test Trust Company"}),
            )
        )


def test_a_duplicate_family_id_is_refused() -> None:
    registry = TrusteeReportFamilyRegistry()
    registry.register(_complete_family())
    with pytest.raises(ValueError, match="already registered"):
        registry.register(
            _complete_family(header_signature=frozenset({"Other", "other.example"}))
        )


# ---------------------------------------------------------------------------
# The #494 guard — an unrecognised header refuses, never defaults
# ---------------------------------------------------------------------------


def test_detect_returns_none_when_no_family_matches() -> None:
    """``None``, so the caller must decide — never an arbitrary first family."""
    registry = TrusteeReportFamilyRegistry()
    registry.register(_complete_family())

    assert registry.detect("Contego CLO XI DAC\nCOMPLIANCE REPORT\nClient Service Manager") is None


def test_a_partial_signature_match_does_not_detect() -> None:
    """The signature is a conjunction: one phrase in common is not identification.

    This is the near-miss #494 warns about — a header that shares a generic
    phrase (every trustee has a "Global Corporate Trust" department) but is a
    different administrator's document.
    """
    assert FAMILY_REGISTRY.detect("Some Other Bank\nGlobal Corporate Trust\n") is None


@pytest.mark.parametrize(
    "fixture", [MONTHLY_FIXTURE, NOTE_VALUATION_FIXTURE], ids=["monthly", "note-valuation"]
)
def test_the_committed_cairn_reports_detect_as_us_bank(fixture: Path) -> None:
    """Both document kinds of the deal LoanWhiz actually reads are identified."""
    header = "\n".join(fixture.read_text(encoding="utf-8").splitlines()[:20])
    assert FAMILY_REGISTRY.detect(header) is US_BANK


def test_a_report_of_no_known_family_is_refused_by_both_parsers() -> None:
    """The refusal reaches the caller as an error, not as an empty parse.

    A report parsed under the wrong family's titles finds no sections and parses
    nothing, and reconciliation cannot tell that from a document that genuinely
    says nothing. Refusing is the only outcome that distinguishes them.
    """
    foreign = (
        "--- page 1 ---\n"
        "Contego CLO XI DAC\n"
        "COMPLIANCE REPORT\n"
        "Client Service Manager: someone@example.com\n"
        "--- page 2 ---\n"
        "Some Section\n"
    )

    with pytest.raises(UnknownReportFamilyError, match="no registered report family"):
        parse_schedule_text(foreign, period_label="March 2025")

    with pytest.raises(UnknownReportFamilyError, match="no registered trustee-report family"):
        parse_note_valuation_text(foreign, period_label="January 2025")


def test_a_cover_page_ahead_of_the_banner_still_detects() -> None:
    """Detection spans the leading pages, so a prepended page does not blind it.

    Page numbers move between documents; the administrator's banner does not,
    but it can be pushed off page 1. Keying on page 1 alone would refuse a
    report this parser can read perfectly well.
    """
    text = MONTHLY_FIXTURE.read_text(encoding="utf-8")
    shifted = "--- page 0 ---\nCairn CLO XVII DAC\nInserted cover page\n" + text

    schedule = parse_schedule_text(shifted, period_label="March 2025")
    assert schedule.assets, "the shifted report must still parse"


# ---------------------------------------------------------------------------
# Layout behaviour the parsers depend on
# ---------------------------------------------------------------------------


def test_titles_are_ordered_longest_first() -> None:
    """``Part I`` must never claim the ``Part III`` pages.

    The routing loop returns the first title found in a page's header text, so a
    title that is a prefix of another has to be tried last.
    """
    titles = US_BANK.layout(DocumentKind.MONTHLY_REPORT).titles
    assert titles == tuple(sorted(titles, key=len, reverse=True))
    assert titles.index("Current Asset Characteristics - Part III") < titles.index(
        "Current Asset Characteristics - Part I"
    )


def test_a_family_declaring_an_unimplemented_furniture_order_is_refused() -> None:
    """The declared ordering is checked against what the parser implements.

    ``furniture_order`` is on the record so a reviewer can see it, but a
    declaration nothing reads drifts from the code it describes. The collateral
    parser drops furniture before testing a line for data — safe only because
    every data row opens with an asset identifier — so a family needing the
    other order must fail loudly rather than silently re-run #494.
    """
    import loanwhiz.primitives.collateral_schedule_parser as module

    monthly = US_BANK.layout(DocumentKind.MONTHLY_REPORT)
    inverted = dataclasses.replace(monthly, furniture_order=FurnitureOrder.DATA_FIRST)

    with pytest.raises(UnknownReportFamilyError, match="furniture_order"):
        module._assert_furniture_order(inverted, US_BANK.label)


def test_the_coverage_test_row_grammar_comes_from_the_family() -> None:
    """Swap the family's markers and the parse refuses — so the table is the source.

    The existing coverage-test-header regression corrupts the *document*, which a
    parser holding a private hardcoded copy of the markers would pass identically.
    This changes the *family* instead and leaves the document alone, so it fails
    unless the parser really reads the order off the detected layout.

    Refusing is the right failure: reading two like-typed percentage columns in a
    guessed order reports a breaching coverage test as passing (#480).
    """
    import loanwhiz.primitives.collateral_schedule_parser as module

    monthly = US_BANK.layout(DocumentKind.MONTHLY_REPORT)
    unmatched = dataclasses.replace(
        monthly,
        coverage_row_markers={"AHEADERTHISREPORTNEVERPRINTS": _ROW_GRAMMAR},
    )
    monkeypatched = pytest.MonkeyPatch()
    monkeypatched.setattr(module, "_resolve_layout", lambda pages: unmatched)
    try:
        with pytest.raises(ValueError, match="no recognised coverage-test column header"):
            module.parse_liability_summary_text(
                MONTHLY_FIXTURE.read_text(encoding="utf-8"), period_label="March 2025"
            )
    finally:
        monkeypatched.undo()

    # And the untouched family still parses the same document, so the refusal
    # above is the markers doing the work rather than the document being broken.
    assert module.parse_liability_summary_text(
        MONTHLY_FIXTURE.read_text(encoding="utf-8"), period_label="March 2025"
    ).coverage_tests


def test_the_note_valuation_waterfalls_come_from_the_family() -> None:
    """The two Priorities of Payments are a table entry, not a hand-unrolled pair."""
    layout = US_BANK.layout(DocumentKind.NOTE_VALUATION_REPORT)
    assert [field for _, field in layout.waterfalls] == ["revenue", "redemption"]
    for section_key, _ in layout.waterfalls:
        assert section_key in layout.section_titles


def test_the_real_registry_holds_exactly_the_imported_families() -> None:
    """The loader module is the one place families are registered.

    Guards against a family registered by an import side effect somewhere else,
    which would make detection order depend on what happened to be imported.
    """
    assert US_BANK in FAMILY_REGISTRY.all()
    assert BNY_MELLON in FAMILY_REGISTRY.all()
    assert [f.family_id for f in FAMILY_REGISTRY.all()] == ["bny_mellon", "us_bank"]


def test_getting_an_unregistered_family_returns_none() -> None:
    # Deutsche Bank administers CVC Cordatus and publishes a third report shape;
    # it is named here precisely because nothing registers it, so this assertion
    # keeps meaning "absent" rather than quietly becoming a second lookup of a
    # family that has since landed.
    assert FAMILY_REGISTRY.get("deutsche_bank") is None
    assert FAMILY_REGISTRY.get("us_bank") is US_BANK
    assert FAMILY_REGISTRY.get("bny_mellon") is BNY_MELLON


def test_a_declared_section_key_the_layout_lacks_raises_rather_than_routing_nowhere() -> None:
    """``title`` refuses an undeclared role instead of returning a null locator.

    Registration makes this unreachable for a required key, so it firing means a
    parser read a section nobody declared — worth an exception rather than a
    silent route to zero pages.
    """
    with pytest.raises(KeyError):
        US_BANK.layout(DocumentKind.NOTE_VALUATION_REPORT).title("a_role_that_does_not_exist")

    # The Note Valuation layout genuinely has no monthly-report sections.
    with pytest.raises(KeyError):
        US_BANK.layout(DocumentKind.NOTE_VALUATION_REPORT).title(SECTION_CCC)
    assert US_BANK.layout(DocumentKind.NOTE_VALUATION_REPORT).title(SECTION_NV_PRINCIPAL_POP)


# ---------------------------------------------------------------------------
# Declared absence — a section an administrator does not publish at all (#533)
# ---------------------------------------------------------------------------


def _with_monthly(family: TrusteeReportFamily, **layout_overrides) -> TrusteeReportFamily:
    """The same family with its monthly layout replaced field-wise."""
    monthly = family.documents[DocumentKind.MONTHLY_REPORT]
    return dataclasses.replace(
        family,
        documents={
            **family.documents,
            DocumentKind.MONTHLY_REPORT: dataclasses.replace(
                monthly, **layout_overrides
            ),
        },
    )


_REASON = (
    "This administrator prints no such table; the exposure appears only as "
    "compliance-test rows against a rating floor, which is a different datum."
)


def test_a_section_declared_unpublished_registers_and_reads_as_absent() -> None:
    """The sanctioned alternative to a title, for a section that does not exist.

    An administrator who genuinely does not publish a section had, before this,
    only two options: invent a title that routes to no pages, or be refused. The
    first is the #494 failure — zero rows reconciling vacuously — so the family
    must be able to say "absent, and here is why" and have consumers read that
    as a fact rather than as an empty table.
    """
    registry = TrusteeReportFamilyRegistry()
    family = _with_monthly(
        _drop_monthly_section(_complete_family(), SECTION_COUNTRY),
        unpublished_sections={SECTION_COUNTRY: _REASON},
    )

    registry.register(family)

    monthly = family.documents[DocumentKind.MONTHLY_REPORT]
    assert monthly.publishes(SECTION_COUNTRY) is False
    assert monthly.publishes(SECTION_CCC) is True
    assert monthly.unpublished_reason(SECTION_COUNTRY) == _REASON


def test_an_undeclared_missing_section_is_still_refused() -> None:
    """Declared absence widens what a family may say, not what it may omit.

    The whole value of the channel is that "I do not publish this" stops looking
    like "I forgot this". If silence still registered, it would have removed the
    #480 guard rather than given it a second answer.
    """
    registry = TrusteeReportFamilyRegistry()

    with pytest.raises(ValueError) as excinfo:
        registry.register(_drop_monthly_section(_complete_family(), SECTION_COUNTRY))

    assert SECTION_COUNTRY in str(excinfo.value)
    assert "unpublished_sections" in str(excinfo.value), (
        "the refusal must name the channel that would make this legal"
    )


def test_a_section_both_titled_and_declared_unpublished_is_refused() -> None:
    """The document either carries the section or it does not."""
    registry = TrusteeReportFamilyRegistry()

    with pytest.raises(ValueError) as excinfo:
        registry.register(
            _with_monthly(
                _complete_family(), unpublished_sections={SECTION_CCC: _REASON}
            )
        )

    assert SECTION_CCC in str(excinfo.value)
    assert "both" in str(excinfo.value)


def test_declaring_absence_of_a_section_no_parser_reads_is_refused() -> None:
    """An absence nobody asks about states nothing, and is usually a typo."""
    registry = TrusteeReportFamilyRegistry()

    with pytest.raises(ValueError) as excinfo:
        registry.register(
            _with_monthly(
                _complete_family(),
                unpublished_sections={"contry_concentration": _REASON},
            )
        )

    assert "contry_concentration" in str(excinfo.value)


def test_an_unpublished_section_with_no_usable_reason_is_refused() -> None:
    """The reason is the deliverable — a reader judges whether it answers them."""
    registry = TrusteeReportFamilyRegistry()

    with pytest.raises(ValueError) as excinfo:
        registry.register(
            _with_monthly(
                _drop_monthly_section(_complete_family(), SECTION_COUNTRY),
                unpublished_sections={SECTION_COUNTRY: "n/a"},
            )
        )

    assert SECTION_COUNTRY in str(excinfo.value)


def test_title_refuses_an_unpublished_section_naming_the_reason() -> None:
    """Routing must not silently receive a title for a section that is absent."""
    monthly = _with_monthly(
        _drop_monthly_section(_complete_family(), SECTION_COUNTRY),
        unpublished_sections={SECTION_COUNTRY: _REASON},
    ).documents[DocumentKind.MONTHLY_REPORT]

    with pytest.raises(KeyError) as excinfo:
        monthly.title(SECTION_COUNTRY)

    assert "publishes()" in str(excinfo.value)


def test_a_waterfall_section_cannot_be_declared_unpublished() -> None:
    """A waterfall the parser must fill cannot be a section the document omits."""
    registry = TrusteeReportFamilyRegistry()
    family = _complete_family()
    nv = family.documents[DocumentKind.NOTE_VALUATION_REPORT]
    broken = dataclasses.replace(
        nv,
        section_titles={
            k: v for k, v in nv.section_titles.items() if k != SECTION_NV_INTEREST_POP
        },
        unpublished_sections={SECTION_NV_INTEREST_POP: _REASON},
    )

    with pytest.raises(ValueError) as excinfo:
        registry.register(
            dataclasses.replace(
                family,
                documents={
                    **family.documents,
                    DocumentKind.NOTE_VALUATION_REPORT: broken,
                },
            )
        )

    assert SECTION_NV_INTEREST_POP in str(excinfo.value)


# ---------------------------------------------------------------------------
# Two tables under one printed title (#533)
# ---------------------------------------------------------------------------


def test_two_sections_sharing_a_title_with_no_marker_are_refused() -> None:
    """Unchanged from #531 — a bare shared title is still ambiguous routing."""
    registry = TrusteeReportFamilyRegistry()
    monthly = _complete_family().documents[DocumentKind.MONTHLY_REPORT]
    shared = {
        **monthly.section_titles,
        SECTION_SP_INDUSTRY: "Industry Concentrations",
        SECTION_FITCH_INDUSTRY: "Industry Concentrations",
    }

    with pytest.raises(ValueError) as excinfo:
        registry.register(_with_monthly(_complete_family(), section_titles=shared))

    message = str(excinfo.value)
    assert "Industry Concentrations" in message
    assert "table marker" in message, "the refusal must name the way to resolve it"


def test_two_sections_sharing_a_title_register_when_each_names_its_table() -> None:
    """One heading, two tables — the pair (title, marker) is what must be unique.

    BNY prints the S&P and Fitch industry tables under a single ``Industry
    Concentrations`` heading. Refusing that would have forced a per-deal
    conditional in the parser, which is the fork the registry exists to prevent.
    """
    registry = TrusteeReportFamilyRegistry()
    monthly = _complete_family().documents[DocumentKind.MONTHLY_REPORT]
    family = _with_monthly(
        _complete_family(),
        section_titles={
            **monthly.section_titles,
            SECTION_SP_INDUSTRY: "Industry Concentrations",
            SECTION_FITCH_INDUSTRY: "Industry Concentrations",
        },
        section_table_markers={
            SECTION_SP_INDUSTRY: "S&PINDUSTRY",
            SECTION_FITCH_INDUSTRY: "FITCHINDUSTRY",
        },
    )

    registry.register(family)

    layout = family.documents[DocumentKind.MONTHLY_REPORT]
    assert layout.table_marker(SECTION_SP_INDUSTRY) == "S&PINDUSTRY"
    assert layout.table_marker(SECTION_FITCH_INDUSTRY) == "FITCHINDUSTRY"
    assert layout.table_marker(SECTION_CCC) is None
    assert layout.titles.count("Industry Concentrations") == 1, (
        "a shared title must be offered to routing once, not once per section"
    )


def test_two_sections_sharing_a_title_and_a_marker_are_refused() -> None:
    """A marker that does not distinguish resolves nothing."""
    registry = TrusteeReportFamilyRegistry()
    monthly = _complete_family().documents[DocumentKind.MONTHLY_REPORT]

    with pytest.raises(ValueError) as excinfo:
        registry.register(
            _with_monthly(
                _complete_family(),
                section_titles={
                    **monthly.section_titles,
                    SECTION_SP_INDUSTRY: "Industry Concentrations",
                    SECTION_FITCH_INDUSTRY: "Industry Concentrations",
                },
                section_table_markers={
                    SECTION_SP_INDUSTRY: "SAME",
                    SECTION_FITCH_INDUSTRY: "SAME",
                },
            )
        )

    assert "same table marker" in str(excinfo.value)


def test_a_table_marker_for_an_untitled_section_is_refused() -> None:
    """A marker selects a table within a title the family must actually have."""
    registry = TrusteeReportFamilyRegistry()

    with pytest.raises(ValueError) as excinfo:
        registry.register(
            _with_monthly(
                _complete_family(), section_table_markers={"not_a_section": "X"}
            )
        )

    assert "not_a_section" in str(excinfo.value)
