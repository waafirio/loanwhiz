"""BNY Mellon detects, and every parse of it refuses loudly rather than emptily.

Registering a second family changes what happens to its documents: before
#533 a BNY report matched nothing and raised ``UnknownReportFamilyError``
telling the reader to register the administrator. Now it *is* registered, so
the parsers reach it — and the hazard that replaces the old refusal is the one
this seam has paid for three times, most recently in #494: a document parsed
under column logic that was written for someone else finds no rows, then
reconciles vacuously against the nothing it found.

So the property worth pinning is not "BNY parses" — the column work for BNY's
sections is not in this PR — but that **registration did not turn a loud
refusal into a quiet empty answer**. Each parse below refuses, and the
assertions are on the refusal naming what is actually missing, because a
refusal that says nothing is only marginally better than a wrong number.

The fixtures are the genuine first two pages of Contego CLO XI DAC's own
documents, kept as header excerpts rather than whole reports: detection reads
only the first three pages, and a truncated file would misrepresent the
document if a later test asked it for rows.
"""

from __future__ import annotations

import loanwhiz.primitives.base  # noqa: F401  (import-order guard)

from pathlib import Path

import pytest

from loanwhiz.domain.trustee_report_families import BNY_MELLON, FAMILY_REGISTRY, US_BANK
from loanwhiz.domain.trustee_report_registry import (
    DocumentKind,
    DocumentLayout,
    FurnitureOrder,
    RowGeometry,
    SECTION_COUNTRY,
    SECTION_FITCH_INDUSTRY,
    SECTION_SP_INDUSTRY,
    UnknownReportFamilyError,
)
from loanwhiz.primitives.collateral_schedule_parser import (
    _assert_furniture_order,
    parse_schedule_text,
)
from loanwhiz.primitives.note_valuation_parser import parse_note_valuation_text

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "trustee_family"
COMPLIANCE = FIXTURE_DIR / "contego-clo-xi-compliance-august-2024-header-excerpt.txt"
NOTE_VALUATION = (
    FIXTURE_DIR / "contego-clo-xi-note-valuation-august-2024-header-excerpt.txt"
)


def _header(path: Path) -> str:
    """The detection window: the first 12 lines of each of the first 3 pages."""
    pages = path.read_text().split("--- page ")[1:4]
    return "\n".join("\n".join(p.split("\n")[1:13]) for p in pages)


@pytest.mark.parametrize("fixture", [COMPLIANCE, NOTE_VALUATION], ids=["monthly", "nv"])
def test_a_real_bny_report_detects_as_bny(fixture: Path) -> None:
    """Both document kinds carry the signature, read off the documents themselves."""
    assert FAMILY_REGISTRY.detect(_header(fixture)) is BNY_MELLON


@pytest.mark.parametrize("fixture", [COMPLIANCE, NOTE_VALUATION], ids=["monthly", "nv"])
def test_a_real_bny_report_does_not_match_us_bank(fixture: Path) -> None:
    """The signatures are disjoint in fact, not merely by construction.

    Neither U.S. Bank phrase occurs anywhere in a BNY report, so a
    misdetection here could only come from a signature edited to be loose.
    """
    assert not US_BANK.matches(_header(fixture))


def test_a_truncated_bny_monthly_report_still_refuses_rather_than_parsing_empty() -> None:
    """#533's property, kept after the refusal it originally caught was satisfied.

    #533 pinned this on the furniture-order guard, because that is what the
    monthly parser refused BNY with before it could read BNY's rows. #555
    satisfied that guard honestly — the reflowed path genuinely implements the
    data-first ordering the family declares — so the *guard* no longer fires
    here. The property it was protecting is unchanged and is what this now
    asserts directly: a BNY document the parser cannot read must refuse
    loudly, naming what is missing, and must never return a schedule of no
    assets that reconciles vacuously against the nothing it found (#494).

    The fixture is a header excerpt: two pages, no asset sections at all. That
    is the case where an empty answer would look most plausible.
    """
    with pytest.raises(ValueError) as excinfo:
        parse_schedule_text(COMPLIANCE.read_text(), period_label="August 2024")

    message = str(excinfo.value)
    assert "missing" in message.lower(), "the refusal must name what is absent"
    assert "section" in message.lower()


def test_the_furniture_order_guard_still_fires_when_declarations_disagree() -> None:
    """Satisfying the guard for BNY did not retire it.

    The ordering and the geometry describe one behaviour: a family taking the
    per-line path must declare ``furniture-first``, and one taking the reflowed
    path must declare ``data-first``. A layout claiming the other combination
    is refused, so the declaration cannot drift away from the code it
    describes — which is the whole reason it is declared rather than assumed.
    """
    disagreeing = DocumentLayout(
        section_titles=BNY_MELLON.layout(DocumentKind.MONTHLY_REPORT).section_titles,
        furniture_order=FurnitureOrder.FURNITURE_FIRST,
        row_geometry=RowGeometry.REFLOWED_ROWS,
    )

    with pytest.raises(UnknownReportFamilyError) as excinfo:
        _assert_furniture_order(disagreeing, "Test Family")

    message = str(excinfo.value)
    assert "furniture-first" in message and "reflowed-rows" in message
    assert "#494" in message


def test_parsing_a_bny_note_valuation_report_refuses_rather_than_returning_empty() -> None:
    """Strict parsing refuses on its own oracle, naming the absent sections.

    This is the assertion that would catch the real regression: a future change
    that made the parse *succeed* on nothing at all would return a period whose
    waterfalls are empty and whose totals are absent, and nothing downstream
    distinguishes that from a period in which no money moved.
    """
    with pytest.raises(Exception) as excinfo:
        parse_note_valuation_text(
            NOTE_VALUATION.read_text(),
            period_label="August 2024",
            reporting_date="2024-08-20",
            strict=True,
        )

    message = str(excinfo.value)
    assert "reconcile" in message.lower() or "stated" in message.lower()
    assert "interest_pop_present" in message or "principal_pop_present" in message


def test_bny_declares_the_country_section_absent_with_its_reason() -> None:
    """The declared-absence channel, on the family that motivated it.

    A future reader asking "why is there no country stratification for this
    deal?" must find an answer stating what the document carries instead —
    country-ceiling compliance rows — rather than an empty table.
    """
    monthly = BNY_MELLON.layout(DocumentKind.MONTHLY_REPORT)

    assert monthly.publishes(SECTION_COUNTRY) is False
    reason = monthly.unpublished_reason(SECTION_COUNTRY)
    assert "country ceiling" in reason
    assert "#470" in reason, "the reason must say why the near datum is not it"


def test_bny_routes_two_industry_tables_under_one_printed_title() -> None:
    """One heading, two tables — resolved by marker, not by a per-deal branch."""
    monthly = BNY_MELLON.layout(DocumentKind.MONTHLY_REPORT)

    assert monthly.title(SECTION_SP_INDUSTRY) == "Industry Concentrations"
    assert monthly.title(SECTION_FITCH_INDUSTRY) == "Industry Concentrations"
    assert monthly.table_marker(SECTION_SP_INDUSTRY) != monthly.table_marker(
        SECTION_FITCH_INDUSTRY
    )
