"""Section router — parse Docling markdown output into a navigable section tree.

Docling's label-based header detection does not work reliably on prospectus PDFs
(validated against Green Lion 2026-1).  Instead we use regex on the raw markdown
output: every line matching ``^#{1,6}\\s+`` is a section boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Section:
    """A single section extracted from a Docling markdown document."""

    title: str
    level: int          # heading level (1–6, from ## count)
    start_char: int     # offset of the ``#`` into SectionMap.full_text
    end_char: int       # exclusive; equals start_char of next section or len(full_text)
    text: str           # section body text (header line + body)


@dataclass
class SectionMap:
    """Navigable section index over a Docling markdown document."""

    full_text: str
    sections: list[Section] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------

    def find(self, *keywords: str) -> Section | None:
        """Return the first section whose title contains any keyword (case-insensitive)."""
        lower_kws = [kw.lower() for kw in keywords]
        for sec in self.sections:
            lower_title = sec.title.lower()
            if any(kw in lower_title for kw in lower_kws):
                return sec
        return None

    def find_all(self, *keywords: str) -> list[Section]:
        """Return all sections whose title contains any keyword (case-insensitive)."""
        lower_kws = [kw.lower() for kw in keywords]
        return [
            sec for sec in self.sections
            if any(kw in sec.title.lower() for kw in lower_kws)
        ]

    def get_text(self, section: Section) -> str:
        """Return section body text (convenience — same as ``section.text``)."""
        return section.text

    # ------------------------------------------------------------------
    # Descendant-span helpers (#316)
    # ------------------------------------------------------------------

    def _descendant_end_char(self, section: Section) -> int:
        """Char offset where ``section``'s descendant span ends (exclusive).

        :func:`route_sections` ends each :class:`Section`'s ``.text`` at the
        *next header of any level*, so a parent heading's ``.text`` excludes
        its child sub-sections.  For a prospectus that titles its waterfall
        parent generically (e.g. Sol-Lion's §3.4.7.4 whose 12 steps actually
        live in the child §3.4.7.4.2), the parent ``.text`` is just the heading
        line and the waterfall extractor sees ~0 steps (#316).

        This returns the offset of the next header whose ``level <= section.level``
        (i.e. the next sibling-or-shallower heading), or ``len(full_text)`` if
        none follows — the *parent-plus-all-descendants* span.
        """
        # Find this section's index in document order (by start_char, which is
        # unique per header).
        try:
            idx = next(
                i for i, s in enumerate(self.sections)
                if s.start_char == section.start_char and s.level == section.level
            )
        except StopIteration:
            # Section not part of this map — fall back to its own narrow end.
            return section.end_char

        for s in self.sections[idx + 1:]:
            if s.level <= section.level:
                return s.start_char
        return len(self.full_text)

    def descendant_text(self, section: Section) -> str:
        """Return ``section`` plus all of its deeper sub-sections as one span.

        Unlike ``section.text`` (which stops at the next header of *any* level),
        this spans from the section heading down to the next header of the same
        or a shallower level — so a generically-titled parent carries the steps
        that live in its child sub-sections (#316).
        """
        return self.full_text[section.start_char:self._descendant_end_char(section)]

    def with_descendant_text(self, section: Section) -> Section:
        """Return a copy of ``section`` whose ``.text`` is the descendant span.

        Used by the LLM router so a routed parent section feeds the downstream
        waterfall extractor its full child-section step list rather than a
        heading-only stub.  Leaves the underlying :class:`SectionMap` and the
        original :class:`Section` untouched (the keyword/English path is
        unaffected).
        """
        return Section(
            title=section.title,
            level=section.level,
            start_char=section.start_char,
            end_char=self._descendant_end_char(section),
            text=self.descendant_text(section),
        )


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

# Matches a markdown heading at the start of a line:  ## 3.1 Definitions
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

# Matches an enumerated payment-list marker at (roughly) the start of a line:
# ``(a)`` / ``(b)`` lettered cascades, ``(i)``/``(ii)`` roman, or a bare
# ``1.``/``2.`` numbered list — the shape a Priority-of-Payments waterfall takes
# in any language.  Used as a deterministic "this segment holds the real step
# list" signal so the LLM router can prefer the section that *contains* the
# cascade over a generically-titled summary table or a stub parent (#316).
#
# The leading marker also tolerates a deeper order-label prefix — e.g. the real
# Sol-Lion (ES) combined cascade nests its lettered steps under an outer ``(i)``
# / ``(ii)`` order, so the steps surface in the Docling markdown as ``(ii)(a)``,
# ``(ii)(b)`` …  Without the optional ``(?:\([ivxlcdm]+\)\s*)?`` prefix those
# nested markers would not be counted and the section would score ``False``,
# re-creating the 0-step revenue failure (#396).
_PAYMENT_LIST_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\(\s*[ivxlcdm]+\s*\)\s*)?"
    r"(?:\(\s*[a-zA-Z]+\s*\)|\(\s*[ivxlcdm]+\s*\)|\d{1,2}[.)])\s+",
    re.MULTILINE,
)

# Secondary, language-aware signal: an ordinal-word cascade ("firstly, …;
# secondly, …; thirdly, …").  Some prospectuses (Sol-Lion's redemption order
# among them — its real citations read "firstly , to the redemption …",
# "secondly , once all Series A1 Notes …") spell the order out in words rather
# than (or alongside) bracketed letters, so a combined ``(i)…(ii)…`` block whose
# bracket markers are sparse still reads as a genuine cascade.  English plus the
# common IT/ES forms; matched at a (rough) line start and immediately followed
# by a comma — the discriminating shape of an enumerated step, not a stray
# adverb mid-sentence.
_ORDINAL_WORD_RE = re.compile(
    r"^\s*(?:[-*]\s*)?"
    r"(?:first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth)ly\b"
    r"|^\s*(?:[-*]\s*)?(?:primero|segundo|tercero|cuarto|quinto|sexto)\b"
    r"|^\s*(?:[-*]\s*)?(?:primo|secondo|terzo|quarto|quinto|sesto)\b",
    re.MULTILINE | re.IGNORECASE,
)

# A segment needs at least this many enumerated markers to count as carrying a
# real cascade — one stray ``(a)`` in prose shouldn't flip the signal, but a
# genuine waterfall always lists several ordered steps.
_PAYMENT_LIST_MIN_MARKERS = 3


def _has_payment_list(text: str) -> bool:
    """True if ``text`` contains an enumerated payment-step list (#316, #396).

    Deterministic, language-neutral signal: counts ``(a)``/``(b)`` (or roman /
    nested ``(ii)(a)`` / numbered) list markers; ``True`` once at least
    :data:`_PAYMENT_LIST_MIN_MARKERS` distinct ordered markers appear.  A summary
    pointer-table (rank numbers only) or a stub parent heading scores ``False``;
    the real cascade scores ``True``.

    Falls back to an ordinal-word cascade ("firstly, … secondly, …") so a
    combined order whose steps are spelled out in words rather than bracketed
    letters — the real Sol-Lion (ES) shape — still scores ``True`` (#396).
    """
    if len(_PAYMENT_LIST_RE.findall(text)) >= _PAYMENT_LIST_MIN_MARKERS:
        return True
    return len(_ORDINAL_WORD_RE.findall(text)) >= _PAYMENT_LIST_MIN_MARKERS


def route_sections(markdown_text: str) -> SectionMap:
    """Parse Docling markdown output into a navigable :class:`SectionMap`.

    Algorithm
    ---------
    1.  Find every ``#{1,6}`` header line via regex.
    2.  Build a :class:`Section` per match whose ``start_char`` is the position
        of the ``#`` character and ``end_char`` is the position of the *next*
        header (or the end of the document).
    3.  ``text`` is the raw substring ``full_text[start_char:end_char]``.

    Character positions are byte-neutral Unicode code-unit offsets so callers
    can pass them directly to Gemini's character-range API.
    """
    sections: list[Section] = []
    matches = list(_HEADER_RE.finditer(markdown_text))

    for i, m in enumerate(matches):
        hashes = m.group(1)
        title = m.group(2).strip()
        level = len(hashes)
        start_char = m.start()
        end_char = matches[i + 1].start() if i + 1 < len(matches) else len(markdown_text)
        text = markdown_text[start_char:end_char]
        sections.append(Section(title=title, level=level, start_char=start_char,
                                end_char=end_char, text=text))

    return SectionMap(full_text=markdown_text, sections=sections)


# ---------------------------------------------------------------------------
# Semantic SF section lookup
# ---------------------------------------------------------------------------

def extract_key_sf_sections(section_map: SectionMap) -> dict[str, Section | None]:
    """Return key structured-finance sections by semantic name.

    The keyword lists are tuned to the Green Lion 2026-1 prospectus section
    numbering and titles but are intentionally broad so they generalise to
    other RMBS/CLO prospectuses that follow the same ESMA disclosure conventions.
    """
    # NOTE: prefer the descriptive content sub-section title over a bare section
    # number.  ``SectionMap.find`` returns the first section (in document order)
    # whose title contains any keyword, so a numeric keyword like ``"5.2"`` can
    # match an empty numbered parent header (``## 5.2 PRIORITIES OF PAYMENTS``)
    # that precedes the actual content sub-section (``## Revenue Priority of
    # Payments``) and yields a near-empty span — the root cause of #122 (revenue
    # waterfall extracted 0 steps).  ``"5.3"`` was similarly unsafe (it matches
    # ``## 5.3 LOSS ALLOCATION``).  Match on the content-section titles instead.
    return {
        "definitions": section_map.find("definitions", "9.1"),
        "revenue_priority_of_payments": section_map.find(
            "revenue priority of payments", "revenue priority"
        ),
        "redemption_priority_of_payments": section_map.find(
            "redemption priority of payments", "redemption priority"
        ),
        "post_enforcement_priority": section_map.find("post-enforcement", "post enforcement"),
        "credit_enhancement": section_map.find("credit enhancement", "credit structure"),
        "conditions_of_notes": section_map.find("conditions of the notes"),
        "eligibility_criteria": section_map.find("eligibility"),
        "available_funds": section_map.find("available funds", "5.1"),
    }


# ---------------------------------------------------------------------------
# LLM-semantic section classification (language-agnostic)
# ---------------------------------------------------------------------------

# The canonical section roles the assembler needs, regardless of source language
# or numbering. Mirrors the keys :func:`extract_key_sf_sections` returns for the
# roles that are load-bearing for ``DealRules`` assembly.
CANONICAL_SECTION_ROLES: tuple[str, ...] = (
    "definitions",
    "revenue_priority_of_payments",
    "redemption_priority_of_payments",
    "post_enforcement_priority",
    "triggers_covenants",
    "tranche_table",
)


def classify_segments_llm(
    section_map: SectionMap,
    *,
    roles: tuple[str, ...] = CANONICAL_SECTION_ROLES,
    max_title_chars: int = 120,
    body_snippet_chars: int = 280,
) -> dict[str, Section | None]:
    """Classify the header segments into canonical section roles via the LLM.

    This is the **language-agnostic** generalisation that replaces the GL-keyword
    regex (:func:`extract_key_sf_sections`) for non-English / non-standard
    prospectuses (spec: "LLM-semantic section routing"). The markdown is already
    segmented deterministically by ``#`` headers (cheap, language-neutral);
    here the LLM is asked, given the ordered list of segment **titles** *plus a
    body snippet and a deterministic "has enumerated payment list" signal per
    segment* (#316), which segment index best fills each canonical role —
    regardless of language or numbering. The body/signal is what lets the router
    distinguish a generically-titled real step-list (e.g. Sol-Lion's
    "Application" section) from a summary pointer-table or a stub parent that
    *titles* like a waterfall but holds no steps. The keyword router stays the
    deterministic fast path; the assembler falls back to this only for roles the
    keyword router could not locate.

    Each returned :class:`Section` for a *located* role carries its **descendant
    span** as ``.text`` (parent heading down to the next same-or-shallower
    header) rather than the narrow next-header slice, so a routed parent whose
    steps live in a child sub-section feeds the downstream waterfall extractor
    the full step list (#316).

    Returns a ``{role: Section | None}`` map. On any LLM error (no credentials,
    network, malformed response) returns all-``None`` so the caller degrades to
    the keyword result rather than crashing — never raises into the extraction
    path.

    Parameters
    ----------
    section_map:
        The header-segmented :class:`SectionMap`.
    roles:
        The canonical roles to fill (defaults to :data:`CANONICAL_SECTION_ROLES`).
    max_title_chars:
        Truncate each segment title to this many characters in the prompt.
    body_snippet_chars:
        How many characters of each segment's descendant span to show the LLM as
        a body snippet (after the heading line) so it can judge content, not just
        the title.
    """
    sections = section_map.sections
    if not sections:
        return {role: None for role in roles}

    try:
        from google import genai
        from google.genai import types as genai_types

        from loanwhiz.config import GCP_LOCATION, GCP_PROJECT, MODEL_FLASH
    except Exception:
        return {role: None for role in roles}

    # Per-segment context: title + a body snippet (taken from the descendant
    # span so a parent's snippet reflects its child steps) + the deterministic
    # has_payment_list signal. This is what lets the LLM tell a real (possibly
    # generically-titled) step list apart from a summary table / stub (#316).
    seg_lines: list[str] = []
    for i, sec in enumerate(sections):
        span = section_map.descendant_text(sec)
        # Snippet = the span after its heading line (the steps, not the title).
        body = span.split("\n", 1)[1] if "\n" in span else ""
        snippet = " ".join(body.split())[:body_snippet_chars]
        has_list = _has_payment_list(span)
        seg_lines.append(
            f"{i}: title={sec.title[:max_title_chars]!r} | "
            f"has_payment_list={str(has_list).lower()} | "
            f"snippet={snippet!r}"
        )
    numbered = "\n".join(seg_lines)
    role_lines = "\n".join(f"- {r}" for r in roles)
    prompt = (
        "You are routing the sections of a structured-finance prospectus "
        "(RMBS/ABS) onto a fixed set of canonical roles. The section titles "
        "below may be in any language (English, Italian, Spanish, ...) and use "
        "any numbering scheme. Each section line gives its index, title, a "
        "'has_payment_list' flag (true when the section body contains an "
        "enumerated payment cascade — (a), (b), (c)... — not just a summary "
        "rank table), and a short body snippet.\n\n"
        "Numbered sections (index: title | has_payment_list | snippet):\n"
        f"{numbered}\n\n"
        "For each canonical role, return the index of the single section that "
        "best fills it by MEANING and CONTENT — not title alone. For the "
        "priority-of-payments / waterfall roles "
        "(revenue_priority_of_payments, redemption_priority_of_payments, "
        "post_enforcement_priority) STRONGLY PREFER a section with "
        "has_payment_list=true that actually enumerates the steps, even when "
        "its title is generic (e.g. 'Application'); do NOT pick a summary table "
        "or a stub parent heading that merely titles like a waterfall but has "
        "has_payment_list=false. If a single combined cascade serves both the "
        "revenue and the redemption (capital) order, you may return the SAME "
        "index for both of those roles. Return -1 for a role with no fitting "
        "section. Roles:\n"
        f"{role_lines}\n\n"
        'Respond as compact JSON mapping each role to an index, e.g. '
        '{"definitions": 12, "revenue_priority_of_payments": 30, ...}. '
        "Use -1 for a role with no matching section."
    )

    try:
        client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)
        response = client.models.generate_content(
            model=MODEL_FLASH,
            contents=prompt,
            config=genai_types.GenerateContentConfig(temperature=0.0),
        )
        text = (response.text or "").strip()
    except Exception:
        return {role: None for role in roles}

    import json

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {role: None for role in roles}
    try:
        mapping = json.loads(match.group(0))
    except (ValueError, json.JSONDecodeError):
        return {role: None for role in roles}

    result: dict[str, Section | None] = {}
    for role in roles:
        idx = mapping.get(role, -1)
        try:
            idx = int(idx)
        except (TypeError, ValueError):
            idx = -1
        if 0 <= idx < len(sections):
            # Return the routed section widened to its descendant span, so a
            # generically-titled parent feeds the waterfall extractor the steps
            # that live in its child sub-sections (#316).
            result[role] = section_map.with_descendant_text(sections[idx])
        else:
            result[role] = None
    return result


# ---------------------------------------------------------------------------
# Two-tier section resolution (deterministic keyword fast-path + LLM fallback)
# ---------------------------------------------------------------------------

# The load-bearing roles the assembler's extractors need located before they can
# run. These are the roles for which a *miss* by the deterministic keyword router
# aborts or silently empties extraction (definitions hard-raises; each waterfall
# raises a swallowed ``ValueError`` → empty waterfalls). For each, the canonical
# role name in :data:`CANONICAL_SECTION_ROLES` (the LLM router's vocabulary) is
# the same string the keyword router (:func:`extract_key_sf_sections`) returns —
# so a gap in the keyword result can be filled directly from the LLM result.
_LOAD_BEARING_ROLES: tuple[str, ...] = (
    "definitions",
    "revenue_priority_of_payments",
    "redemption_priority_of_payments",
    "post_enforcement_priority",
)

# The load-bearing roles whose downstream extractor needs the *enumerated step
# list*, not just the heading.  These are the roles the descendant-span widening
# below applies to — ``definitions`` is excluded (it is not a payment cascade and
# its own resolution path is unaffected).
_WATERFALL_ROLES: tuple[str, ...] = (
    "revenue_priority_of_payments",
    "redemption_priority_of_payments",
    "post_enforcement_priority",
)


def _widen_to_payment_list(
    section_map: SectionMap, section: Section
) -> Section:
    """Widen ``section`` to its descendant span iff that recovers the step list.

    The Sol-Lion (ES) failure mode: a router (keyword *or* LLM) locates a
    waterfall role on a generically-titled **parent** heading whose own body is a
    heading-plus-prose stub, while the enumerated cascade lives one level deeper
    in a child sub-section.  ``route_sections`` ends each :class:`Section`'s
    ``.text`` at the *next header of any level*, so the parent's narrow ``.text``
    carries no steps and the downstream waterfall extractor sees ~0 (#396).

    This is the path-agnostic completion of the #316 descendant-span rescue,
    which was wired only into the LLM fallback (:func:`classify_segments_llm`
    already returns its routed sections widened).  Applied at the
    :func:`resolve_sections` chokepoint, it rescues the **keyword** fast path too
    — the actual Sol-Lion trigger, reproduced live as a 0-step revenue stub.

    Guard rails keep it a strict no-op for the cases that don't need it:

    - **Only widen when it helps.** If the narrow ``.text`` *already* contains a
      payment list, the section holds its own steps and is returned unchanged —
      so the English flat-section path (Green Lion) is byte-identical.
    - **Only widen when the descendant span recovers a list.** If neither the
      narrow text nor the descendant span has a payment list, widening would not
      help and could pull unrelated sibling text in, so the section is returned
      unchanged.
    - **Idempotent.** An already-widened section (the LLM path's output) whose
      ``.text`` is the descendant span already has the list, so it is returned
      unchanged — never double-widened.
    """
    if _has_payment_list(section.text):
        return section
    if _has_payment_list(section_map.descendant_text(section)):
        return section_map.with_descendant_text(section)
    return section


def resolve_sections(
    section_map: SectionMap,
    *,
    use_llm: bool = True,
) -> dict[str, Section | None]:
    """Resolve the load-bearing sections, keyword-first with an LLM fallback.

    This is the wiring that makes :func:`classify_segments_llm` part of the
    production extraction path (#274). The deterministic English-keyword router
    (:func:`extract_key_sf_sections`) stays the **fast path** — it is free,
    deterministic, and already correct for the English Green Lion prospectuses.
    Only when it leaves a :data:`_LOAD_BEARING_ROLES` role unresolved (the
    non-English / non-standard case — e.g. an Italian "priorità dei pagamenti"
    or Spanish "orden de prelación" heading that no English keyword matches) is
    the **language-agnostic** LLM router consulted, and only its result for the
    *still-missing* roles is merged in. A role the keyword router already
    located is never overridden.

    Consequences:

    - **English path is byte-identical.** When the keyword router finds every
      load-bearing role (the Green Lion case), ``use_llm`` is moot — the LLM is
      never invoked — and the returned sections are exactly the keyword hits.
    - **Non-English path is rescued.** When the keyword router misses (IT/ES),
      the LLM fills the gap by *meaning*, so the downstream waterfall /
      definitions extractors receive a real section to extract from instead of
      raising.
    - **Generic-parent layouts are rescued on either path.** After resolution,
      each located waterfall role is widened to its descendant span when (and
      only when) its narrow ``.text`` is a heading-only stub but the cascade
      lives in a child sub-section (:func:`_widen_to_payment_list`).  This is the
      keyword-path completion of the #316 descendant-span rescue and is the
      actual Sol-Lion (ES) fix: the deterministic keyword router lands on the
      generic ``Application of Available Funds`` parent, whose narrow text holds
      no steps, so revenue extracted 0 (#396).  The widening is a strict no-op
      for the flat-section English path and idempotent for the already-widened
      LLM output.

    Parameters
    ----------
    section_map:
        The header-segmented :class:`SectionMap`.
    use_llm:
        When ``False`` (offline / tests), the LLM fallback is skipped entirely —
        the result is the deterministic keyword router's output verbatim, with
        any unresolved load-bearing role left ``None``. :func:`classify_segments_llm`
        also degrades to all-``None`` on a credentials/network error, so the
        fallback never raises into the extraction path regardless.

    Returns
    -------
    dict[str, Section | None]
        Map over :data:`_LOAD_BEARING_ROLES` (plus the remaining keys
        :func:`extract_key_sf_sections` returns, passed through unchanged), each
        the resolved :class:`Section` or ``None`` when neither tier located it.
    """
    keyword = extract_key_sf_sections(section_map)

    missing = [r for r in _LOAD_BEARING_ROLES if keyword.get(r) is None]
    if missing and use_llm:
        llm = classify_segments_llm(section_map)
        for role in missing:
            if llm.get(role) is not None:
                keyword[role] = llm[role]

    # Path-agnostic descendant-span rescue (#396): widen any located waterfall
    # role whose narrow ``.text`` is a heading-only stub but whose child
    # sub-section carries the cascade.  This fires for keyword-located roles (the
    # Sol-Lion trigger) as well as LLM-located ones (idempotent there), and is a
    # strict no-op for flat-section English layouts.
    for role in _WATERFALL_ROLES:
        sec = keyword.get(role)
        if sec is not None:
            keyword[role] = _widen_to_payment_list(section_map, sec)

    return keyword
