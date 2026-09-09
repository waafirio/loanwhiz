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

import pytest

from loanwhiz.domain.trustee_report_families import US_BANK
from loanwhiz.domain.trustee_report_registry import (
    FAMILY_REGISTRY,
    REQUIRED_SECTION_KEYS,
    SECTION_CCC,
    SECTION_NV_PRINCIPAL_POP,
    ColumnOrder,
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
                column_order_markers={"TESTHEADER": ColumnOrder.RATIO_FIRST},
            ),
            DocumentKind.NOTE_VALUATION_REPORT: DocumentLayout(
                section_titles={
                    key: f"Printed {key}"
                    for key in REQUIRED_SECTION_KEYS[DocumentKind.NOTE_VALUATION_REPORT]
                },
                furniture_prefixes=("Page ",),
                furniture_order=FurnitureOrder.DATA_FIRST,
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
    assert [f.family_id for f in FAMILY_REGISTRY.all()] == ["us_bank"]


def test_getting_an_unregistered_family_returns_none() -> None:
    assert FAMILY_REGISTRY.get("bny_mellon") is None
    assert FAMILY_REGISTRY.get("us_bank") is US_BANK


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
