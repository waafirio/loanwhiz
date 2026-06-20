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

import re
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from loanwhiz.extraction import section_router
from loanwhiz.extraction.section_router import (
    _LOAD_BEARING_ROLES,
    extract_key_sf_sections,
    resolve_sections,
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


# Regression fixture for #122: the real Green Lion prospectus structures the
# Priorities of Payments as a numbered *parent* heading (``## 5.2 PRIORITIES OF
# PAYMENTS``) — whose body is empty — immediately followed by the content
# sub-sections (``## Revenue Priority of Payments`` etc.) that actually hold the
# (a)–(k) payment steps.  The old keyword list ``["revenue priority", "5.2"]``
# made ``SectionMap.find`` return the empty numbered parent (it appears first in
# document order and "5.2" matches its title), so the revenue waterfall fed
# Gemini ~0 chars and extracted 0 steps.  This fixture mirrors that structure so
# the regression is caught without the 1M-char real markdown.
_POP_STRUCTURE_MD = textwrap.dedent("""\
    # Prospectus

    ## 5.1 AVAILABLE FUNDS

    ## Available Revenue Funds

    Available Revenue Funds means the aggregate of the following amounts.

    ## 5.2 PRIORITIES OF PAYMENTS

    ## Revenue Priority of Payments

    On each Notes Payment Date the Available Revenue Funds will be applied in
    the following order of priority:

    - (a) first, fees of the Security Trustee;
    - (b) second, fees of the Paying Agent and Servicer;
    - (c) third, amounts due under the Interest Rate Swap;
    - (d) fourth, interest on the Class A Notes;
    - (e) fifth, to credit the Class A Principal Deficiency Ledger;
    - (f) sixth, interest on the Class B Notes;
    - (g) seventh, to credit the Class B Principal Deficiency Ledger;
    - (h) eighth, to credit the Reserve Fund;
    - (i) ninth, subordinated swap amounts;
    - (j) tenth, amounts due to the Subordinated Loan Provider;
    - (k) eleventh, the deferred purchase price to the Seller.

    ## Redemption Priority of Payments

    On each Notes Payment Date the Available Redemption Funds will be applied:

    - (a) first, Class A principal;
    - (b) second, Class B principal.

    ## Post-Enforcement Priority of Payments

    Following an Enforcement Notice, proceeds are applied:

    - (a) first, Security Trustee fees;
    - (b) second, Class A amounts.

    ## 5.3 LOSS ALLOCATION

    Losses are allocated to the Principal Deficiency Ledgers.
""")


def test_pop_numbered_parent_does_not_mask_content_section() -> None:
    """Regression for #122: revenue PoP resolves to the content sub-section.

    The numbered ``## 5.2 PRIORITIES OF PAYMENTS`` parent header is empty and
    precedes ``## Revenue Priority of Payments`` in document order.  The matcher
    must return the *content* sub-section (with the (a)–(k) steps), not the empty
    parent — otherwise the waterfall extractor sees ~0 chars and yields 0 steps.
    """
    sm = route_sections(_POP_STRUCTURE_MD)
    sf = extract_key_sf_sections(sm)

    rev = sf["revenue_priority_of_payments"]
    assert rev is not None, "revenue_priority_of_payments not found"
    assert rev.title == "Revenue Priority of Payments", (
        f"matched the wrong section: {rev.title!r} "
        "(likely the empty '5.2 PRIORITIES OF PAYMENTS' parent header)"
    )
    # The content section must carry real waterfall text, not just a header line.
    assert "Available Revenue Funds" in rev.text
    step_letters = re.findall(r"^- \(([a-z])\)", rev.text, re.MULTILINE)
    assert step_letters == list("abcdefghijk"), (
        f"expected 11 lettered steps (a)-(k), got {step_letters}"
    )

    # Redemption must not collapse onto '5.3 LOSS ALLOCATION'.
    red = sf["redemption_priority_of_payments"]
    assert red is not None
    assert red.title == "Redemption Priority of Payments", (
        f"redemption matched wrong section: {red.title!r}"
    )
    assert "loss allocation" not in red.text.lower()

    post = sf["post_enforcement_priority"]
    assert post is not None
    assert post.title == "Post-Enforcement Priority of Payments"


def test_waterfall_keywords_match_content_sections() -> None:
    """The waterfall_extractor keyword lists locate the content sub-sections.

    Mirrors test_pop_numbered_parent_does_not_mask_content_section but exercises
    the exact keyword lists the waterfall extractor uses to feed Gemini.
    """
    from loanwhiz.extraction.waterfall_extractor import _WATERFALL_SECTION_KEYWORDS

    sm = route_sections(_POP_STRUCTURE_MD)

    expected_titles = {
        "revenue": "Revenue Priority of Payments",
        "redemption": "Redemption Priority of Payments",
        "post_enforcement": "Post-Enforcement Priority of Payments",
    }
    for wf_type, keywords in _WATERFALL_SECTION_KEYWORDS.items():
        section = sm.find(*keywords)
        assert section is not None, f"{wf_type} section not found with {keywords}"
        assert section.title == expected_titles[wf_type], (
            f"{wf_type}: matched {section.title!r}, expected "
            f"{expected_titles[wf_type]!r}"
        )
        # Each must carry a non-trivial body (not just the header line).
        body = section.text.split("\n", 1)[1] if "\n" in section.text else ""
        assert body.strip(), f"{wf_type} section body is empty"


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
# Two-tier section resolution (keyword fast-path + LLM fallback) — #274 wiring
# ---------------------------------------------------------------------------

# A synthetic *non-English* prospectus skeleton whose load-bearing section
# headings are Italian/Spanish, so the English keyword router
# (extract_key_sf_sections) matches none of them. Mirrors the real IT/ES failure
# mode the LLM router exists to rescue.
_NON_ENGLISH_MD = textwrap.dedent("""\
    # Prospetto

    ## 1 Definizioni

    "Fondi Disponibili" indica la somma dei seguenti importi.

    ## 2 Ordine di Priorità dei Pagamenti — Interessi

    - (a) commissioni del fiduciario;
    - (b) interessi sulle Note di Classe A.

    ## 3 Ordine di Priorità dei Pagamenti — Capitale

    - (a) capitale delle Note di Classe A.

    ## 4 Priorità Post-Escussione

    - (a) commissioni del fiduciario.
""")


# An all-English fixture where the keyword router locates EVERY load-bearing
# role (incl. a Definitions heading) — the byte-identical-English guard case.
_ALL_ROLES_ENGLISH_MD = textwrap.dedent("""\
    # Prospectus

    ## 9.1 Definitions

    "Available Revenue Funds" means the aggregate of the following amounts.

    ## Revenue Priority of Payments

    - (a) first, fees of the Security Trustee;
    - (b) second, interest on the Class A Notes.

    ## Redemption Priority of Payments

    - (a) first, Class A principal.

    ## Post-Enforcement Priority of Payments

    - (a) first, Security Trustee fees.
""")


def test_resolve_sections_english_does_not_call_llm() -> None:
    """English path: every load-bearing role is found by the keyword router, so
    the LLM fallback must NEVER be invoked (the byte-identical-English guard).

    Monkeypatches classify_segments_llm to raise — if resolve_sections calls it
    on an all-found English doc, the test fails loudly instead of silently
    paying for an LLM call (or changing the English result).
    """
    sm = route_sections(_ALL_ROLES_ENGLISH_MD)

    # Precondition: the keyword router already locates every load-bearing role on
    # this English fixture.
    keyword = extract_key_sf_sections(sm)
    assert all(keyword.get(r) is not None for r in _LOAD_BEARING_ROLES), (
        "fixture precondition: keyword router should find all load-bearing roles"
    )

    def _boom(*_args, **_kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("classify_segments_llm must not be called on the English path")

    with patch.object(section_router, "classify_segments_llm", _boom):
        resolved = resolve_sections(sm)

    # Result is exactly the keyword hits (same Section objects).
    for role in _LOAD_BEARING_ROLES:
        assert resolved[role] is keyword[role]


def test_resolve_sections_use_llm_false_skips_fallback() -> None:
    """With use_llm=False the LLM fallback is skipped even when roles are missing
    (offline determinism): unresolved load-bearing roles stay None."""
    sm = route_sections(_NON_ENGLISH_MD)

    def _boom(*_args, **_kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("classify_segments_llm must not be called when use_llm=False")

    with patch.object(section_router, "classify_segments_llm", _boom):
        resolved = resolve_sections(sm, use_llm=False)

    # Italian headings → English keyword router finds neither definitions nor the
    # waterfall sections.
    assert resolved["revenue_priority_of_payments"] is None
    assert resolved["definitions"] is None


def test_resolve_sections_non_english_fallback_fills_missing_roles() -> None:
    """Non-English path: roles the keyword router misses are filled from the LLM
    router's result, and that result threads through unchanged.

    The genai boundary is the only thing stubbed — classify_segments_llm is
    replaced with a fake that maps the canonical roles onto the (Italian)
    sections by index, exactly as the real LLM would return them.
    """
    sm = route_sections(_NON_ENGLISH_MD)

    # Sanity: the English keyword router misses the Italian sections.
    keyword = extract_key_sf_sections(sm)
    assert keyword["revenue_priority_of_payments"] is None
    assert keyword["definitions"] is None

    # Build the section-by-title lookup the fake LLM "returns".
    by_title = {s.title: s for s in sm.sections}
    fake_result = {
        "definitions": by_title["1 Definizioni"],
        "revenue_priority_of_payments": by_title[
            "2 Ordine di Priorità dei Pagamenti — Interessi"
        ],
        "redemption_priority_of_payments": by_title[
            "3 Ordine di Priorità dei Pagamenti — Capitale"
        ],
        "post_enforcement_priority": by_title["4 Priorità Post-Escussione"],
    }

    def _fake_llm(section_map, **_kwargs):
        # Real classify_segments_llm returns {role: Section | None}.
        return dict(fake_result)

    with patch.object(section_router, "classify_segments_llm", _fake_llm):
        resolved = resolve_sections(sm)

    # Every load-bearing role is now filled from the LLM fallback.
    for role in _LOAD_BEARING_ROLES:
        assert resolved[role] is fake_result[role], f"role {role} not filled from LLM"

    # The revenue section the override threads through carries the real step text
    # (so the downstream waterfall extractor would feed Gemini a real span).
    rev = resolved["revenue_priority_of_payments"]
    assert "Note di Classe A" in rev.text


def test_resolve_sections_keyword_hit_not_overridden_by_llm() -> None:
    """A role the keyword router already located is never overridden by the LLM,
    even if the LLM would have returned a different section for it."""
    sm = route_sections(_ALL_ROLES_ENGLISH_MD)
    keyword = extract_key_sf_sections(sm)
    assert all(keyword.get(r) is not None for r in _LOAD_BEARING_ROLES)

    # Force a (pathological) LLM that claims a wrong section for every role.
    wrong = sm.sections[0]

    def _fake_llm(section_map, **_kwargs):
        return {role: wrong for role in _LOAD_BEARING_ROLES}

    with patch.object(section_router, "classify_segments_llm", _fake_llm):
        resolved = resolve_sections(sm)

    # Keyword hits win — the LLM is consulted only for *missing* roles (here none).
    for role in _LOAD_BEARING_ROLES:
        assert resolved[role] is keyword[role]


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
