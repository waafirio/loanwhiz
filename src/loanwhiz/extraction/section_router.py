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
    return {
        "definitions": section_map.find("definitions", "9.1"),
        "revenue_priority_of_payments": section_map.find("revenue priority", "5.2"),
        "redemption_priority_of_payments": section_map.find("redemption priority", "5.3"),
        "post_enforcement_priority": section_map.find("post-enforcement", "post enforcement"),
        "credit_enhancement": section_map.find("credit enhancement", "credit structure"),
        "conditions_of_notes": section_map.find("conditions of the notes"),
        "eligibility_criteria": section_map.find("eligibility"),
        "available_funds": section_map.find("available funds", "5.1"),
    }
