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

import json
import re
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from loanwhiz.extraction import section_router
from loanwhiz.extraction.section_router import (
    _LOAD_BEARING_ROLES,
    _has_payment_list,
    _is_numeric_descendant,
    _section_number,
    _widen_to_payment_list,
    classify_segments_llm,
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
# Descendant-span + payment-list-signal routing (#316 — Sol-Lion ES fix)
# ---------------------------------------------------------------------------

# A prospectus skeleton mirroring the Sol-Lion (ES) failure mode: the waterfall
# *parent* is a generically-titled stub (its body is just a heading line); the
# real enumerated step list lives in a DEEPER child sub-section. The English
# keyword router never matches these titles, and the old next-header span would
# give the parent only its heading line — so the steps were lost (#316).
_CHILD_STEP_LIST_MD = textwrap.dedent("""\
    # Prospectus

    ## 3.4.7.4 Application of Available Funds

    This section governs the application of funds.

    ### 3.4.7.4.2 Application

    On each Payment Date the Available Funds will be applied in the following
    order of priority:

    - (a) first, fees of the Management Company;
    - (b) second, fees of the Paying Agent;
    - (c) third, interest on the Class A Notes;
    - (d) fourth, principal of the Class A Notes;
    - (e) fifth, interest on the Class B Notes;
    - (f) sixth, principal of the Class B Notes.

    ## 4.6.2 Summary Table

    Rank 1, Rank 2, Rank 3 — see body for detail.
""")


def test_descendant_text_spans_into_child_sections() -> None:
    """SectionMap.descendant_text spans a parent down to the next same-or-shallower
    header, capturing child-section steps the narrow `.text` slice omits (#316)."""
    sm = route_sections(_CHILD_STEP_LIST_MD)
    parent = next(s for s in sm.sections if s.title == "3.4.7.4 Application of Available Funds")

    # The narrow .text stops at the next header (the ### child) — heading-only-ish.
    assert "(a) first" not in parent.text, (
        "precondition: narrow .text must NOT already contain the child steps"
    )

    # The descendant span reaches into the ### 3.4.7.4.2 child and carries all steps.
    span = sm.descendant_text(parent)
    step_letters = re.findall(r"^- \(([a-z])\)", span, re.MULTILINE)
    assert step_letters == list("abcdef"), f"expected (a)-(f), got {step_letters}"
    # But it must STOP at the next sibling-or-shallower header (## 4.6.2).
    assert "Summary Table" not in span, "descendant span leaked into the next ## section"


def test_with_descendant_text_does_not_mutate_map() -> None:
    """with_descendant_text returns a widened copy; the original Section and map
    are untouched (the keyword/English path must stay byte-identical)."""
    sm = route_sections(_CHILD_STEP_LIST_MD)
    parent = next(s for s in sm.sections if s.title.startswith("3.4.7.4 Application"))
    original_text = parent.text

    widened = sm.with_descendant_text(parent)
    assert "(a) first" in widened.text
    assert widened.title == parent.title and widened.level == parent.level
    # Original object and map slice unchanged.
    assert parent.text == original_text
    assert sm.sections[1].text == original_text  # same object in the list


def test_has_payment_list_signal() -> None:
    """_has_payment_list fires on an enumerated cascade, not on a rank summary."""
    cascade = "- (a) first, fees;\n- (b) second, interest;\n- (c) third, principal;"
    assert _has_payment_list(cascade) is True

    roman = "(i) first item\n(ii) second item\n(iii) third item\n"
    assert _has_payment_list(roman) is True

    numbered = "1. first\n2. second\n3. third\n"
    assert _has_payment_list(numbered) is True

    summary = "Rank 1, Rank 2, Rank 3 — see body for detail."
    assert _has_payment_list(summary) is False

    # A single stray marker in prose must not flip the signal.
    prose = "The order is set out in clause (a) of the agreement."
    assert _has_payment_list(prose) is False


def test_classify_prefers_payment_list_over_summary_title(tmp_path) -> None:
    """classify_segments_llm feeds the LLM the has_payment_list signal + body
    snippet, and returns the routed section widened to its descendant span (#316).

    The genai boundary is stubbed: the fake client inspects the prompt and routes
    revenue to whichever indexed segment advertises has_payment_list=true (exactly
    the discrimination the real signal enables).
    """
    sm = route_sections(_CHILD_STEP_LIST_MD)

    captured = {}

    class _FakeResp:
        text = ""  # set per-call below

    class _FakeModels:
        def generate_content(self, *, model, contents, config):  # noqa: ARG002
            captured["prompt"] = contents
            # Parse the segment lines; pick the index whose line has
            # has_payment_list=true for the waterfall roles, -1 otherwise.
            idx_with_list = None
            for line in contents.splitlines():
                m = re.match(r"^(\d+): .*has_payment_list=true", line)
                if m:
                    idx_with_list = int(m.group(1))
                    break
            r = _FakeResp()
            picked = idx_with_list if idx_with_list is not None else -1
            r.text = (
                '{"definitions": -1, '
                f'"revenue_priority_of_payments": {picked}, '
                f'"redemption_priority_of_payments": {picked}, '
                '"post_enforcement_priority": -1}'
            )
            return r

    class _FakeClient:
        def __init__(self, *a, **k):
            self.models = _FakeModels()

    # classify_segments_llm does `from google import genai` (the installed
    # package) and `genai.Client(...)`. Patch the real package's Client so the
    # function runs its full prompt-building / response-parsing path against the
    # fake transport — only the network boundary is stubbed.
    from google import genai as _real_genai

    # ``cache_dir=tmp_path`` keeps this hermetic: the determinism cache (#445)
    # would otherwise replay a previous run's answer and the fake transport
    # would never be reached, so ``captured["prompt"]`` below would be empty.
    with patch.object(_real_genai, "Client", _FakeClient):
        result = classify_segments_llm(sm, cache_dir=tmp_path)

    # Prompt carried the signal for the child step-list segment.
    assert "has_payment_list=true" in captured["prompt"]

    rev = result["revenue_priority_of_payments"]
    assert rev is not None, "revenue role was not routed"
    # The routed section is widened to its descendant span → carries the steps.
    step_letters = re.findall(r"^- \(([a-z])\)", rev.text, re.MULTILINE)
    assert step_letters == list("abcdef"), (
        f"routed section did not carry the descendant step list: {step_letters}"
    )

    # Combined-cascade tolerance: revenue and redemption may resolve to one span.
    red = result["redemption_priority_of_payments"]
    assert red is not None and red.start_char == rev.start_char


# ---------------------------------------------------------------------------
# Path-agnostic descendant-span widening at the resolve_sections chokepoint
# (#396 — Sol-Lion ES revenue 0-step fix completed for the KEYWORD path)
# ---------------------------------------------------------------------------

# An ENGLISH generic-parent layout the keyword router *does* match by title
# ("Revenue Priority of Payments") but whose enumerated steps live one level
# deeper in a child sub-section.  This is the exact Sol-Lion (ES) failure mode
# generalised to a title the keyword router hits: the keyword router locates the
# parent, returns its heading-only narrow `.text`, and the waterfall extractor
# sees 0 steps — unless resolve_sections widens it to the descendant span (#396).
_GENERIC_PARENT_KEYWORD_MD = textwrap.dedent("""\
    # Prospectus

    ## 5.2 Revenue Priority of Payments

    This section governs the application of revenue funds.

    ### 5.2.1 Order of Application

    On each Payment Date the Available Funds will be applied:

    - (a) first, fees of the Management Company;
    - (b) second, interest on the Class A Notes;
    - (c) third, principal of the Class A Notes;

    ## 6 Other Provisions

    Unrelated text that must NOT be pulled into the revenue span.
""")

# A FLAT English layout: the keyword-matched section already holds its own
# enumerated steps in its narrow `.text`.  The widening must be a strict no-op
# here — the Green Lion / English path stays byte-identical.
_FLAT_KEYWORD_MD = textwrap.dedent("""\
    # Prospectus

    ## Revenue Priority of Payments

    On each Payment Date the Available Funds will be applied:

    - (a) first, fees;
    - (b) second, interest;
    - (c) third, principal;

    ## Next Section

    Other text.
""")


def test_resolve_sections_widens_keyword_generic_parent() -> None:
    """resolve_sections(use_llm=False) widens a keyword-located generic parent to
    its descendant span so the cascade in the child sub-section reaches the
    waterfall extractor — the Sol-Lion (ES) revenue 0-step fix on the keyword
    path (#396)."""
    sm = route_sections(_GENERIC_PARENT_KEYWORD_MD)

    # Precondition: the keyword router matches the generic parent but its narrow
    # .text is a heading-only stub with no payment list.
    kw = extract_key_sf_sections(sm)["revenue_priority_of_payments"]
    assert kw is not None and kw.title.startswith("5.2 Revenue Priority")
    assert not _has_payment_list(kw.text), (
        "precondition: narrow keyword .text must be a stub with no cascade"
    )

    # resolve_sections widens it — the returned section carries the child steps.
    rev = resolve_sections(sm, use_llm=False)["revenue_priority_of_payments"]
    assert rev is not None
    step_letters = re.findall(r"^- \(([a-z])\)", rev.text, re.MULTILINE)
    assert step_letters == list("abc"), (
        f"widened revenue section did not carry the child cascade: {step_letters}"
    )
    # And it must STOP at the next sibling-or-shallower header (## 6).
    assert "Other Provisions" not in rev.text, (
        "descendant span leaked into the next ## section"
    )


def test_resolve_sections_flat_english_path_byte_identical() -> None:
    """The widening is a strict no-op for a flat English layout whose matched
    section already holds its own steps — the keyword hit is returned verbatim
    (Green Lion / English path stays byte-identical, #396)."""
    sm = route_sections(_FLAT_KEYWORD_MD)
    kw = extract_key_sf_sections(sm)["revenue_priority_of_payments"]
    resolved = resolve_sections(sm, use_llm=False)["revenue_priority_of_payments"]

    assert kw is not None and resolved is not None
    # Same span, same text — not re-widened past its own boundary.
    assert resolved.text == kw.text
    assert resolved.start_char == kw.start_char
    assert resolved.end_char == kw.end_char


def test_widen_to_payment_list_idempotent_on_already_widened() -> None:
    """_widen_to_payment_list does not double-widen an already-widened section
    (the LLM path returns sections whose .text is already the descendant span)."""
    sm = route_sections(_GENERIC_PARENT_KEYWORD_MD)
    parent = next(
        s for s in sm.sections if s.title.startswith("5.2 Revenue Priority")
    )
    widened_once = sm.with_descendant_text(parent)
    assert _has_payment_list(widened_once.text)  # already carries the list

    widened_twice = _widen_to_payment_list(sm, widened_once)
    # Idempotent: a section already holding its list is returned unchanged.
    assert widened_twice.text == widened_once.text
    assert widened_twice.start_char == widened_once.start_char
    assert widened_twice.end_char == widened_once.end_char


def test_widen_to_payment_list_noop_when_no_list_anywhere() -> None:
    """_widen_to_payment_list leaves a genuinely list-free section unchanged —
    no spurious widening that could pull unrelated sibling text in (#396)."""
    md = textwrap.dedent("""\
        # Prospectus

        ## 7.1 Governing Law

        These notes are governed by the laws of Spain.

        ### 7.1.1 Jurisdiction

        The courts of Madrid have exclusive jurisdiction.

        ## 8 Other
        x
    """)
    sm = route_sections(md)
    sec = next(s for s in sm.sections if s.title.startswith("7.1 Governing Law"))
    out = _widen_to_payment_list(sm, sec)
    assert out.text == sec.text  # no list anywhere → unchanged


def test_has_payment_list_ordinal_word_and_nested_markers() -> None:
    """_has_payment_list recognises the real Sol-Lion (ES) marker shapes: an
    ordinal-word cascade ("firstly, … secondly, …") and nested ``(ii)(a)``
    bracket markers — while the stray-prose / summary-rank guards still hold
    (#396)."""
    # Ordinal-word cascade (the real Sol-Lion redemption citations' shape).
    ordinal = (
        "firstly , to the redemption pro rata of the Series A1 Notes;\n"
        "secondly , once all A1 redeemed, to the Series A2 Notes;\n"
        "thirdly , once all A2 redeemed, to the Series A3 Notes;"
    )
    assert _has_payment_list(ordinal) is True

    # Spanish and Italian ordinal cascades.
    assert _has_payment_list("primero, a comisiones;\nsegundo, a intereses;\ntercero, a capital;") is True
    assert _has_payment_list("primo, alle commissioni;\nsecondo, agli interessi;\nterzo, al capitale;") is True

    # Nested (ii)(a) / (ii)(b) bracket markers under an outer order label.
    nested = "(ii)(a) first to A1;\n(ii)(b) second to A2;\n(ii)(c) third to A3;"
    assert _has_payment_list(nested) is True

    # Guards still hold: ordinal adverbs scattered inline in a paragraph (not at
    # line starts) must NOT flip the signal.
    inline = (
        "The party firstly agreed, then secondly considered, and thirdly "
        "resolved the matter all within a single sentence."
    )
    assert _has_payment_list(inline) is False
    # A single ordinal adverb in prose stays False (below the floor anyway).
    assert _has_payment_list("He firstly noted the issue and moved on.") is False
    # Summary rank table still False.
    assert _has_payment_list("Rank 1, Rank 2, Rank 3 — see body for detail.") is False


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


# ---------------------------------------------------------------------------
# Flat-level numbered headings — hierarchy lives in the NUMBER (#438)
# ---------------------------------------------------------------------------

# The real Sol-Lion II (ES) shape. Docling emits EVERY heading of this
# prospectus at the same markdown level (all ``##``), including logical
# children: §3.4.7.2.2 is a sibling of §3.4.7.2 as far as ``#`` counting goes.
# A descendant span computed from levels alone therefore ends at the very next
# heading, so the parent's span equals its own stub and the #396 widening was a
# no-op by construction. The hierarchy that does survive is the dotted number.
_FLAT_NUMBERED_MD = textwrap.dedent("""\
    ## 3.4.7.2 Revenue Priority of Payments

    The Available Funds will be applied as described below.

    ## 3.4.7.2.1 Source

    The Available Funds consist of collections and swap receipts.

    ## 3.4.7.2.2 Application

    On each Payment Date the Available Funds will be applied:

    - (a) first, fees of the Management Company;
    - (b) second, interest on the Class A Notes;
    - (c) third, principal of the Class A Notes;

    ## 3.4.7.3 Exceptional rules

    Unrelated text that must NOT be pulled into the revenue span.
""")

# Same flat levels, but the headings carry NO numbering. The span must behave
# exactly as before — this is the guard that the numeric rule cannot widen a
# span that the level rule alone would have terminated.
_FLAT_UNNUMBERED_MD = textwrap.dedent("""\
    ## Revenue Priority of Payments

    The Available Funds will be applied as described below.

    ## Application

    - (a) first, fees;
    - (b) second, interest;

    ## Exceptional rules

    Unrelated text.
""")


def test_section_number_parses_dotted_headings() -> None:
    """The dotted prefix is read as a tuple; an unnumbered title yields None."""
    assert _section_number("3.4.7.2.2. Application") == (3, 4, 7, 2, 2)
    assert _section_number("3.4.7.2 Revenue Priority of Payments") == (3, 4, 7, 2)
    assert _section_number("Application of Available Funds") is None
    assert _section_number("€ 14,056,500,000") is None


def test_is_numeric_descendant_is_strict() -> None:
    """A section extends its parent's number; a sibling and itself do not."""
    assert _is_numeric_descendant("3.4.7.2.2 Application", (3, 4, 7, 2))
    assert not _is_numeric_descendant("3.4.7.3 Exceptional rules", (3, 4, 7, 2))
    # Not its own descendant.
    assert not _is_numeric_descendant("3.4.7.2 Revenue", (3, 4, 7, 2))
    # An unnumbered heading is never a numeric descendant, and an unnumbered
    # parent never claims one.
    assert not _is_numeric_descendant("Application", (3, 4, 7, 2))
    assert not _is_numeric_descendant("3.4.7.2.2 Application", None)


def test_descendant_span_crosses_same_level_numbered_children() -> None:
    """A parent's span reaches children emitted at its OWN markdown level.

    Without the numeric rule the span stops at §3.4.7.2.1 and carries no
    cascade at all — the reason the ES revenue waterfall extracted 0 steps.
    """
    sm = route_sections(_FLAT_NUMBERED_MD)
    parent = next(s for s in sm.sections if s.title.startswith("3.4.7.2 "))
    # Precondition: all one level, and the narrow text is a stub.
    assert len({s.level for s in sm.sections}) == 1
    assert not _has_payment_list(parent.text)

    span = sm.descendant_text(parent)
    assert _has_payment_list(span)
    assert re.findall(r"^- \(([a-z])\)", span, re.MULTILINE) == list("abc")
    # And it still STOPS at the next non-descendant number.
    assert "Exceptional rules" not in span, (
        "numeric descendant span leaked past its sibling section"
    )


def test_resolve_sections_widens_flat_numbered_parent() -> None:
    """The #396 widening now fires on the real flat-level document shape."""
    sm = route_sections(_FLAT_NUMBERED_MD)
    rev = resolve_sections(sm, use_llm=False)["revenue_priority_of_payments"]
    assert rev is not None
    assert re.findall(r"^- \(([a-z])\)", rev.text, re.MULTILINE) == list("abc")
    assert "Exceptional rules" not in rev.text


def test_unnumbered_flat_headings_keep_the_narrow_span() -> None:
    """The numeric rule is inert without numbering — it can only ever widen.

    A same-level unnumbered sibling still terminates the span, so a document
    with no section numbers behaves exactly as it did before (#438).
    """
    sm = route_sections(_FLAT_UNNUMBERED_MD)
    parent = next(s for s in sm.sections if s.title.startswith("Revenue Priority"))
    span = sm.descendant_text(parent)
    assert span == parent.text
    assert not _has_payment_list(span)
# Determinism cache (#445)
# ---------------------------------------------------------------------------
#
# The acceptance bar for #445: two runs over the same prospectus, with the same
# code, produce the same ``sections_found`` and ``completeness_score``. The
# score is a pure function of the sections (``assembler._completeness_score``),
# so the property to pin here is that the router's answer is replayed rather
# than re-asked. Each test stubs only the genai transport, so the real
# prompt-building, cache-keying and response-parsing paths all run.

_ROUTER_MD = (
    "# Definizioni\n"
    '"Fondi Disponibili" indica la somma dei seguenti importi.\n'
    "# Ordine di Priorita dei Pagamenti\n"
    "- (a) commissioni del fiduciario;\n"
    "- (b) interessi Classe A;\n"
    "- (c) capitale Classe A.\n"
    "# Priorita Post-Escussione\n"
    "- (a) commissioni del fiduciario.\n"
)


class _ScriptedClient:
    """genai.Client stand-in returning a different answer on every call.

    This is the falsifier the issue describes: an LLM that disagrees with itself
    over identical input. Without a cache the router propagates that
    disagreement into ``sections_found``; with one, only the first answer is
    ever observed.
    """

    def __init__(self, *responses: str):
        self._responses = list(responses)
        self.calls = 0
        outer = self

        class _Models:
            def generate_content(self, *, model, contents, config):  # noqa: ARG002
                idx = min(outer.calls, len(outer._responses) - 1)
                outer.calls += 1
                resp = SimpleNamespace(text=outer._responses[idx])
                return resp

        self.models = _Models()

    def __call__(self, *a, **k):  # genai.Client(...) → this same instance
        return self


class _RaisingClient:
    """genai.Client stand-in whose call always fails (credentials/network/quota)."""

    def __init__(self):
        self.calls = 0
        outer = self

        class _Models:
            def generate_content(self, *, model, contents, config):  # noqa: ARG002
                outer.calls += 1
                raise RuntimeError("vertex unavailable")

        self.models = _Models()

    def __call__(self, *a, **k):
        return self


_ANSWER_FULL = (
    '{"definitions": 0, "revenue_priority_of_payments": 1, '
    '"redemption_priority_of_payments": 1, "post_enforcement_priority": 2, '
    '"triggers_covenants": -1, "tranche_table": -1}'
)
# The same document routed differently — the observed #444 failure mode: the
# priority-of-payments sections simply stop being located.
_ANSWER_DEGRADED = (
    '{"definitions": 0, "revenue_priority_of_payments": -1, '
    '"redemption_priority_of_payments": -1, "post_enforcement_priority": -1, '
    '"triggers_covenants": -1, "tranche_table": -1}'
)


def _located(result: dict) -> list[str]:
    """The ``sections_found`` shape: the roles the router actually located."""
    return sorted(k for k, v in result.items() if v is not None)


def test_router_cache_replays_first_answer_when_llm_diverges(tmp_path) -> None:
    """Two runs over one section map agree even when the model does not.

    This is #445's acceptance bar at the router level: the scripted client
    answers fully on call 1 and degrades on call 2, so a second live call would
    drop all three priority-of-payments roles. The cache means there is no
    second call.
    """
    from google import genai as _real_genai

    sm = route_sections(_ROUTER_MD)
    client = _ScriptedClient(_ANSWER_FULL, _ANSWER_DEGRADED)

    with patch.object(_real_genai, "Client", client):
        first = classify_segments_llm(sm, cache_dir=tmp_path)
        second = classify_segments_llm(sm, cache_dir=tmp_path)

    assert client.calls == 1, "second run re-asked the model instead of replaying"
    assert _located(first) == _located(second)
    assert "revenue_priority_of_payments" in _located(first)
    # And the resolved sections are equivalent, not merely equally many.
    for role in first:
        a, b = first[role], second[role]
        assert (a is None) == (b is None)
        if a is not None:
            assert (a.title, a.start_char, a.end_char, a.text) == (
                b.title,
                b.start_char,
                b.end_char,
                b.text,
            )


def test_router_cache_not_written_on_llm_failure(tmp_path) -> None:
    """A failed call degrades to all-None and leaves the cache empty.

    Caching a failure would freeze the deal at silently-degraded coverage for
    every future run — a second, independent source of the same defect.
    """
    from google import genai as _real_genai

    sm = route_sections(_ROUTER_MD)
    client = _RaisingClient()

    with patch.object(_real_genai, "Client", client):
        result = classify_segments_llm(sm, cache_dir=tmp_path)

    assert all(v is None for v in result.values())
    assert list(tmp_path.glob("section-router-*.json")) == []
    assert client.calls == 1

    # The next run retries rather than replaying the failure.
    good = _ScriptedClient(_ANSWER_FULL)
    with patch.object(_real_genai, "Client", good):
        retried = classify_segments_llm(sm, cache_dir=tmp_path)
    assert good.calls == 1
    assert "revenue_priority_of_payments" in _located(retried)


def test_router_caches_a_genuine_no_match_answer(tmp_path) -> None:
    """All-``-1`` is a reproducible answer, so it IS cached (unlike a failure)."""
    from google import genai as _real_genai

    sm = route_sections(_ROUTER_MD)
    client = _ScriptedClient(_ANSWER_DEGRADED, _ANSWER_FULL)

    with patch.object(_real_genai, "Client", client):
        first = classify_segments_llm(sm, cache_dir=tmp_path)
        second = classify_segments_llm(sm, cache_dir=tmp_path)

    assert client.calls == 1
    assert _located(first) == _located(second) == ["definitions"]


def test_router_cache_recomputes_after_delete(tmp_path) -> None:
    """Round-trip: deleting the entry returns the router to re-deriving it."""
    from google import genai as _real_genai

    sm = route_sections(_ROUTER_MD)
    client = _ScriptedClient(_ANSWER_FULL)

    with patch.object(_real_genai, "Client", client):
        classify_segments_llm(sm, cache_dir=tmp_path)
        entries = list(tmp_path.glob("section-router-*.json"))
        assert len(entries) == 1
        entries[0].unlink()
        again = classify_segments_llm(sm, cache_dir=tmp_path)

    assert client.calls == 2, "cache miss after delete did not re-ask the model"
    assert "revenue_priority_of_payments" in _located(again)


def test_router_force_refresh_reasks_and_overwrites(tmp_path) -> None:
    """force_refresh busts the entry, matching every sibling extractor's cache."""
    from google import genai as _real_genai

    sm = route_sections(_ROUTER_MD)
    client = _ScriptedClient(_ANSWER_FULL, _ANSWER_DEGRADED)

    with patch.object(_real_genai, "Client", client):
        classify_segments_llm(sm, cache_dir=tmp_path)
        refreshed = classify_segments_llm(sm, cache_dir=tmp_path, force_refresh=True)
        # The refreshed (degraded) answer is what a third, ordinary run replays.
        third = classify_segments_llm(sm, cache_dir=tmp_path)

    assert client.calls == 2
    assert _located(refreshed) == ["definitions"]
    assert _located(third) == ["definitions"]


def test_router_use_cache_false_neither_reads_nor_writes(tmp_path) -> None:
    from google import genai as _real_genai

    sm = route_sections(_ROUTER_MD)
    client = _ScriptedClient(_ANSWER_FULL, _ANSWER_DEGRADED)

    with patch.object(_real_genai, "Client", client):
        first = classify_segments_llm(sm, cache_dir=tmp_path, use_cache=False)
        second = classify_segments_llm(sm, cache_dir=tmp_path, use_cache=False)

    assert client.calls == 2
    assert list(tmp_path.glob("section-router-*.json")) == []
    assert _located(first) != _located(second)


def test_router_cache_key_tracks_document_and_prompt_inputs() -> None:
    """A different document — or a different prompt shape — is a different key.

    The key is the hash of the rendered prompt, which is what makes it cover the
    document content *and* the prompt revision without a hand-maintained
    version constant. If the key ignored either, one deal's routing would be
    served for another, or a prompt edit would keep serving stale routing.
    """
    from loanwhiz.extraction.section_router import _router_cache_key

    other_md = _ROUTER_MD.replace("Definizioni", "Definiciones")
    k_a = _router_cache_key("prompt-a", "gemini-2.5-flash")
    k_b = _router_cache_key("prompt-b", "gemini-2.5-flash")
    k_model = _router_cache_key("prompt-a", "gemini-2.5-pro")

    assert k_a != k_b, "prompt text is not in the key"
    assert k_a != k_model, "model id is not in the key"
    assert k_a == _router_cache_key("prompt-a", "gemini-2.5-flash"), "key unstable"
    assert route_sections(other_md).sections[0].title != (
        route_sections(_ROUTER_MD).sections[0].title
    )


def test_cached_indices_are_revalidated_against_the_live_map(tmp_path) -> None:
    """A stale index resolves to "not located", never to the wrong section.

    The cache stores indices into the segmentation the prompt was rendered from,
    so resolution must be bounds-checked against the map in hand rather than
    trusted. A hand-written entry with an out-of-range index stands in for a
    cache written against a different segmentation.
    """
    from google import genai as _real_genai

    from loanwhiz.extraction.section_router import (
        _router_cache_key,
        _router_cache_path,
    )

    sm = route_sections(_ROUTER_MD)
    client = _ScriptedClient(_ANSWER_FULL)
    with patch.object(_real_genai, "Client", client):
        classify_segments_llm(sm, cache_dir=tmp_path)

    entry = next(iter(tmp_path.glob("section-router-*.json")))
    stale = json.loads(entry.read_text())
    stale["revenue_priority_of_payments"] = 999
    entry.write_text(json.dumps(stale))

    with patch.object(_real_genai, "Client", client):
        result = classify_segments_llm(sm, cache_dir=tmp_path)

    assert result["revenue_priority_of_payments"] is None
    assert result["definitions"] is not None
    assert _router_cache_path(_router_cache_key("x", "y"), tmp_path).parent == tmp_path


def test_router_cache_corrupt_entry_falls_through_to_the_model(tmp_path) -> None:
    """Unreadable JSON is a miss, not a crash and not a silent all-None."""
    from google import genai as _real_genai

    sm = route_sections(_ROUTER_MD)
    client = _ScriptedClient(_ANSWER_FULL)
    with patch.object(_real_genai, "Client", client):
        classify_segments_llm(sm, cache_dir=tmp_path)

    entry = next(iter(tmp_path.glob("section-router-*.json")))
    entry.write_text("{not json")

    with patch.object(_real_genai, "Client", client):
        result = classify_segments_llm(sm, cache_dir=tmp_path)

    assert client.calls == 2
    assert "revenue_priority_of_payments" in _located(result)
