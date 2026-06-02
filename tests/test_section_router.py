"""Tests for loanwhiz.extraction.section_router.

Two test layers:

1. ``test_section_map_basic`` — synthetic markdown, no network/PDF needed.
   Runs in plain ``pytest`` with no external dependencies.

2. ``test_revenue_priority_section_found`` — downloads (or loads from cache)
   the Green Lion 2026-1 prospectus, runs Docling, and validates that section
   5.2 (Revenue Priority of Payments) is found and contains the expected text.
   Skipped automatically when the PDF cannot be downloaded or Docling is not
   available, so it is safe for CI environments without internet access.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from loanwhiz.extraction.section_router import (
    Section,
    SectionMap,
    extract_key_sf_sections,
    route_sections,
)

# ---------------------------------------------------------------------------
# Cache path for the Green Lion prospectus markdown
# ---------------------------------------------------------------------------

_CACHE_PATH = Path("/tmp/green-lion-prospectus.md")
_PROSPECTUS_URL = (
    "https://huggingface.co/datasets/Algoritmica/green-lion-2026"
    "/resolve/main/Hackathon_Data/green-lion-2026-1-prospectus.pdf"
)


def _get_or_extract_markdown() -> str | None:
    """Return the Green Lion prospectus as markdown.

    Strategy:
    1. If ``/tmp/green-lion-prospectus.md`` exists, read it (fast path).
    2. Otherwise download the PDF with ``httpx``, convert with Docling, and
       save the result to the cache path.
    3. Return ``None`` if either download or Docling import fails (caller
       should mark the test as skipped).
    """
    if _CACHE_PATH.exists():
        return _CACHE_PATH.read_text(encoding="utf-8")

    try:
        import httpx
    except ImportError:
        return None

    pdf_path = Path("/tmp/green-lion-prospectus.pdf")
    try:
        with httpx.Client(follow_redirects=True, timeout=120) as client:
            response = client.get(_PROSPECTUS_URL)
            response.raise_for_status()
        pdf_path.write_bytes(response.content)
    except Exception:
        return None

    try:
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        result = converter.convert(str(pdf_path))
        markdown = result.document.export_to_markdown()
    except Exception:
        return None

    _CACHE_PATH.write_text(markdown, encoding="utf-8")
    return markdown


# ---------------------------------------------------------------------------
# Synthetic-markdown tests (no network, no PDF)
# ---------------------------------------------------------------------------

_SYNTHETIC_MD = textwrap.dedent("""\
    # Prospectus

    This is the introduction.

    ## 1 General Information

    Some general text.

    ### 1.1 Definitions

    "Available Revenue Funds" means the aggregate of the following amounts...
    firstly, such amounts shall be applied in payment of senior fees.
    (a) Clause one of the waterfall.
    (b) Clause two of the waterfall.

    ## 2 Priority of Payments

    ### 2.1 Revenue Priority of Payments

    Revenue priority text — firstly, pay senior fees.
    (a) Available Revenue Funds are applied in the following order.

    ### 2.2 Redemption Priority of Payments

    Redemption text.

    ## 3 Eligibility Criteria

    The following eligibility criteria apply.

    ## 4 Credit Enhancement

    Credit enhancement is provided by the reserve fund.
""")


def test_section_map_basic() -> None:
    """route_sections builds correct Section objects from synthetic markdown."""
    sm = route_sections(_SYNTHETIC_MD)

    # There should be sections
    assert len(sm.sections) > 0, "Expected at least one section"

    # Level detection
    h1s = [s for s in sm.sections if s.level == 1]
    h2s = [s for s in sm.sections if s.level == 2]
    h3s = [s for s in sm.sections if s.level == 3]
    assert h1s, "Expected at least one level-1 section"
    assert h2s, "Expected at least one level-2 section"
    assert h3s, "Expected at least one level-3 section"

    # Title extraction
    titles = [s.title for s in sm.sections]
    assert any("Definitions" in t for t in titles)
    assert any("Eligibility Criteria" in t for t in titles)

    # Character range sanity — start_char < end_char
    for sec in sm.sections:
        assert sec.start_char < sec.end_char, f"Invalid range for section '{sec.title}'"
        # text must equal the full_text slice
        assert sec.text == sm.full_text[sec.start_char:sec.end_char]

    # get_text is a convenience wrapper
    first = sm.sections[0]
    assert sm.get_text(first) == first.text


def test_find_case_insensitive() -> None:
    """SectionMap.find is case-insensitive and matches any keyword."""
    sm = route_sections(_SYNTHETIC_MD)

    found = sm.find("definitions")
    assert found is not None
    assert "Definitions" in found.title

    found_upper = sm.find("DEFINITIONS")
    assert found_upper is not None and found_upper.title == found.title


def test_find_all() -> None:
    """SectionMap.find_all returns all matching sections."""
    sm = route_sections(_SYNTHETIC_MD)

    # Both "Revenue Priority" and "Redemption Priority" contain "priority"
    results = sm.find_all("priority")
    assert len(results) >= 2


def test_find_multiple_keywords() -> None:
    """find(*keywords) matches on any keyword (OR semantics)."""
    sm = route_sections(_SYNTHETIC_MD)

    # "5.2" is not in synthetic doc, but "revenue priority" is
    found = sm.find("revenue priority", "5.2")
    assert found is not None
    assert "Revenue Priority" in found.title


def test_find_returns_none_for_unknown() -> None:
    """find returns None for a keyword that matches no section."""
    sm = route_sections(_SYNTHETIC_MD)
    assert sm.find("zzz-nonexistent-section-xyz") is None


def test_extract_key_sf_sections_keys() -> None:
    """extract_key_sf_sections returns a dict with all 8 semantic keys."""
    sm = route_sections(_SYNTHETIC_MD)
    result = extract_key_sf_sections(sm)

    expected_keys = {
        "definitions",
        "revenue_priority_of_payments",
        "redemption_priority_of_payments",
        "post_enforcement_priority",
        "credit_enhancement",
        "conditions_of_notes",
        "eligibility_criteria",
        "available_funds",
    }
    assert set(result.keys()) == expected_keys

    # Spot-check a few that should hit in synthetic doc
    assert result["definitions"] is not None
    assert result["eligibility_criteria"] is not None
    assert result["credit_enhancement"] is not None


def test_section_text_includes_header_line() -> None:
    """Section.text begins with the markdown header line."""
    sm = route_sections(_SYNTHETIC_MD)
    defs = sm.find("definitions")
    assert defs is not None
    # The first line of text is the header
    first_line = defs.text.split("\n")[0]
    assert first_line.startswith("#")
    assert "Definitions" in first_line


# ---------------------------------------------------------------------------
# Integration test against Green Lion prospectus (requires network + Docling)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_revenue_priority_section_found() -> None:
    """Revenue Priority of Payments section is found and contains expected text.

    Validates the known ground truth: section 5.2 in the Green Lion 2026-1
    prospectus is at char ~699,437 in the Docling markdown output.
    """
    markdown = _get_or_extract_markdown()
    if markdown is None:
        pytest.skip("Green Lion prospectus unavailable (no network or Docling not installed)")

    sm = route_sections(markdown)

    # The section should be findable
    sf = extract_key_sf_sections(sm)
    rev = sf["revenue_priority_of_payments"]
    assert rev is not None, (
        "revenue_priority_of_payments not found — check keyword list in "
        "extract_key_sf_sections against the actual prospectus section title"
    )

    # Text content validation — known ground truth from Docling validation
    text_lower = rev.text.lower()
    assert "available revenue funds" in text_lower, (
        f"'Available Revenue Funds' not found in section '{rev.title}'"
    )
    assert "firstly" in text_lower or "(a)" in rev.text, (
        f"Neither 'firstly' nor '(a)' found in section '{rev.title}'"
    )

    # Sanity check on character position — should be deep in a 1M+ char doc
    assert rev.start_char > 100_000, (
        f"Section starts suspiciously early at char {rev.start_char}"
    )
