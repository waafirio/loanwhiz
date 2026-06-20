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


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

# Matches a markdown heading at the start of a line:  ## 3.1 Definitions
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


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
) -> dict[str, Section | None]:
    """Classify the header segments into canonical section roles via the LLM.

    This is the **language-agnostic** generalisation that replaces the GL-keyword
    regex (:func:`extract_key_sf_sections`) for non-English / non-standard
    prospectuses (spec: "LLM-semantic section routing"). The markdown is already
    segmented deterministically by ``#`` headers (cheap, language-neutral);
    here the LLM is asked, given the ordered list of segment **titles**, which
    segment index best fills each canonical role — regardless of language or
    numbering. The keyword router stays the deterministic fast path; the
    assembler falls back to this only for roles the keyword router could not
    locate.

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

    numbered = "\n".join(
        f"{i}: {sec.title[:max_title_chars]}" for i, sec in enumerate(sections)
    )
    role_lines = "\n".join(f"- {r}" for r in roles)
    prompt = (
        "You are routing the sections of a structured-finance prospectus "
        "(RMBS/ABS) onto a fixed set of canonical roles. The section titles "
        "below may be in any language (English, Italian, Spanish, ...) and use "
        "any numbering scheme.\n\n"
        "Numbered section titles (index: title):\n"
        f"{numbered}\n\n"
        "For each canonical role, return the index of the single section whose "
        "title best matches it by meaning, or -1 if no section fits. Roles:\n"
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
        result[role] = sections[idx] if 0 <= idx < len(sections) else None
    return result
