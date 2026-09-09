"""Definitions graph extractor — build a term → definition key-value store.

Extracts all capitalised defined terms from a prospectus Definitions section
(e.g. Section 9.1 in Green Lion 2026-1) using a targeted Gemini 2.5 Pro call
via function calling (forced structured JSON output).

Only the Definitions section is sent to the LLM — never the full prospectus.
Results are cached to disk to avoid re-invoking Gemini on every test run.

Usage
-----
    from loanwhiz.extraction.section_router import route_sections
    from loanwhiz.extraction.definitions_graph import extract_definitions, load_or_extract

    section_map = route_sections(markdown_text)
    graph = extract_definitions(section_map)
    term = graph.resolve("Available Distribution Amount")
    referenced = graph.resolve_all(waterfall_section_text)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from google import genai
from google.genai import types as genai_types

from loanwhiz.config import GCP_LOCATION, GCP_PROJECT, MODEL_PRO
from loanwhiz.extraction.section_router import (
    Section,
    SectionMap,
    route_sections,
    widen_to_definitions,
)
from loanwhiz.extraction.truncation import Truncation, clip

logger = logging.getLogger(__name__)


# A real structured-finance prospectus glossary defines hundreds of terms, and
# the section carrying it runs to tens of thousands of characters. Both floors
# below sit far under any genuine glossary: they are a smoke alarm for "this is
# not a whole glossary", not a quality bar (#548).
_MIN_PLAUSIBLE_TERMS = 50
_MIN_PLAUSIBLE_SECTION_CHARS = 10_000


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GlossaryCoverage:
    """Whether an extracted glossary is plausibly a *whole* glossary.

    Two deals have lost glossary facts at this stage by two different
    mechanisms — Cairn CLO XVII to a real ``max_chars`` truncation, Contego CLO
    XI to section *orphaning* (Docling promotes each defined term to its own
    markdown heading, so the routed ``1. Definitions`` section ends at the first
    of them and 98.7% of the glossary becomes sibling sections nobody sends).
    Both produced a healthy-looking term count over a silent alphabetical
    cliff, and ``' Payment Date ' means:`` was lost by both.

    So this check is deliberately **cause-agnostic**: it asks only whether the
    output looks like a complete glossary, never which mechanism truncated it.
    A repair aimed at either individual cause would not have caught the other,
    and would not catch the third (#548).

    ``reason`` is empty exactly when ``implausible`` is ``False``.
    """

    term_count: int
    source_chars: int
    first_initial: str | None
    last_initial: str | None
    implausible: bool
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "term_count": self.term_count,
            "source_chars": self.source_chars,
            "first_initial": self.first_initial,
            "last_initial": self.last_initial,
            "implausible": self.implausible,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: object) -> "GlossaryCoverage | None":
        """Rebuild from a cache payload; a pre-#548 cache has no such key."""
        if not isinstance(data, dict):
            return None
        return cls(
            term_count=int(data.get("term_count", 0)),
            source_chars=int(data.get("source_chars", 0)),
            first_initial=data.get("first_initial") or None,
            last_initial=data.get("last_initial") or None,
            implausible=bool(data.get("implausible", False)),
            reason=str(data.get("reason", "")),
        )


def assess_glossary(
    terms: dict[str, "DefinedTerm"],
    source_chars: int,
    truncation: Truncation | None,
) -> GlossaryCoverage:
    """Judge whether ``terms`` plausibly represents a whole glossary.

    Three independent signals, any one of which condemns the result. They are
    ordered most-informative first, because ``reason`` reports only the first
    that fires and the truncation record is the most actionable:

    1. **It was truncated.** We know we did not see all of it — no inference
       required. (Cairn.)
    2. **The section handed to us is too small to be a glossary.** This fires
       regardless of what the LLM returned, which is what makes it catch a
       *routing* failure that no character budget can fix. (Contego.)
    3. **Too few terms came back.** The section looked fine and was not cut, so
       the extraction itself under-delivered.

    Initials are reported whatever the verdict: on an alphabetical glossary the
    span from first to last is the cheapest read of where a cliff falls.
    """
    initials = sorted({term[:1].upper() for term in terms if term[:1].isalpha()})
    first_initial = initials[0] if initials else None
    last_initial = initials[-1] if initials else None
    term_count = len(terms)

    reason = ""
    if truncation is not None:
        reason = (
            f"section truncated at {truncation.kept_chars} of "
            f"{truncation.original_chars} chars "
            f"({truncation.discarded_fraction:.0%} discarded); "
            f"nothing after {truncation.last_line!r} was seen"
        )
    elif source_chars < _MIN_PLAUSIBLE_SECTION_CHARS:
        reason = (
            f"the routed definitions section is only {source_chars} chars — "
            f"below the {_MIN_PLAUSIBLE_SECTION_CHARS}-char floor for a real "
            f"glossary, so section selection, not the character budget, is what "
            f"limited this extraction"
        )
    elif term_count < _MIN_PLAUSIBLE_TERMS:
        reason = (
            f"only {term_count} terms extracted from {source_chars} chars, "
            f"below the floor of {_MIN_PLAUSIBLE_TERMS} for a prospectus glossary"
        )

    return GlossaryCoverage(
        term_count=term_count,
        source_chars=source_chars,
        first_initial=first_initial,
        last_initial=last_initial,
        implausible=bool(reason),
        reason=reason,
    )


@dataclass
class DefinedTerm:
    """A single defined term extracted from a prospectus Definitions section."""

    term: str
    definition: str       # full text of the definition
    page_or_section: str  # e.g. "Section 9.1" or page number if available
    excerpt: str          # first 200 chars of definition for citation


@dataclass
class DefinitionsGraph:
    """Key-value store: capitalised term → DefinedTerm.

    Keys are stored in their canonical form (as they appear in the prospectus).
    Lookup is case-insensitive with partial-match fallback.
    """

    terms: dict[str, DefinedTerm] = field(default_factory=dict)
    # Structural record of what the character budget discarded, and a verdict on
    # whether the result is plausibly a whole glossary. Both default to ``None``
    # so every existing construction site (tests, cache loads written before
    # #548) keeps working unchanged.
    truncation: Truncation | None = None
    coverage: GlossaryCoverage | None = None

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------

    def resolve(self, term: str) -> DefinedTerm | None:
        """Case-insensitive lookup with partial-match fallback.

        Resolution order
        ----------------
        1. Strip a leading "the " (or "The ") so callers can pass
           "the Available Distribution Amount" and still match.
        2. Exact case-insensitive match against canonical keys.
        3. Partial match — the query is a substring of a known key OR a
           known key is a substring of the query (longest key wins to
           avoid ambiguous short-key matches).

        Returns ``None`` when no match is found.
        """
        # Reject blank input immediately
        if not term or not term.strip():
            return None

        # 1. Strip leading "the "
        cleaned = re.sub(r"^[Tt]he\s+", "", term).strip()
        if not cleaned:
            return None

        # 2. Exact case-insensitive match
        lower_cleaned = cleaned.lower()
        for key, defined_term in self.terms.items():
            if key.lower() == lower_cleaned:
                return defined_term

        # 3. Partial match — pick the longest matching key
        candidates: list[tuple[int, DefinedTerm]] = []
        for key, defined_term in self.terms.items():
            lower_key = key.lower()
            if lower_cleaned in lower_key or lower_key in lower_cleaned:
                candidates.append((len(key), defined_term))

        if candidates:
            # Return the candidate with the longest key (most specific match)
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1]

        return None

    def resolve_all(self, text: str) -> dict[str, DefinedTerm]:
        """Find all defined terms referenced in the given text.

        Scans ``text`` for sequences of capitalised words that match known
        defined terms (case-insensitive).  Returns a dict of
        ``{canonical_term: DefinedTerm}`` for every term found at least once.
        """
        found: dict[str, DefinedTerm] = {}
        lower_text = text.lower()
        for key, defined_term in self.terms.items():
            if key.lower() in lower_text:
                found[key] = defined_term
        return found

    def link(self, text: str) -> list[DefinedTerm]:
        """Link a free-text fragment to the defined terms it references.

        The list-returning form of :meth:`resolve_all`: returns the
        :class:`DefinedTerm`\\ s referenced in ``text``, **in document order of
        first appearance** and de-duplicated. This is the single resolution
        surface the assembler and the interpreter call to turn a raw step
        ``condition`` (or a trigger ``metric`` / ``display_name``) into the
        structured defined-term links that let conditional waterfall prose
        resolve against its trigger instead of silently no-op'ing.

        Ordering by first appearance (rather than ``self.terms`` insertion
        order) makes the link deterministic from the *text* — two callers
        passing the same condition get the same ordered link regardless of how
        the graph was built. Blank / unmatched text yields ``[]``.
        """
        if not text or not text.strip():
            return []
        lower_text = text.lower()
        # (first-index, canonical-key, DefinedTerm) for every term present.
        hits: list[tuple[int, str, DefinedTerm]] = []
        for key, defined_term in self.terms.items():
            idx = lower_text.find(key.lower())
            if idx >= 0:
                hits.append((idx, key, defined_term))
        # Order by first appearance, then by key for a stable tie-break.
        hits.sort(key=lambda h: (h[0], h[1]))
        seen: set[str] = set()
        ordered: list[DefinedTerm] = []
        for _idx, key, defined_term in hits:
            if key in seen:
                continue
            seen.add(key)
            ordered.append(defined_term)
        return ordered

    def __len__(self) -> int:
        return len(self.terms)


# ---------------------------------------------------------------------------
# Gemini extraction
# ---------------------------------------------------------------------------

_EXTRACT_TOOL_NAME = "record_defined_terms"

_EXTRACT_TOOL_DESCRIPTION = (
    "Record every defined term found in the prospectus Definitions section. "
    "Call this function exactly once with the complete list of all terms."
)

_PROMPT_TEMPLATE = """\
You are a structured finance document analyst. The text below is the Definitions \
section of a structured finance prospectus (ABS/RMBS/CLO).

Extract EVERY defined term and its definition from this section. A defined term is \
a capitalised phrase enclosed in quotation marks followed by "means" or "has the \
meaning". Include the full text of each definition — do not truncate. For \
page_or_section, use "Section 9.1" or the section reference if visible in the text, \
otherwise use "Definitions".

Call the `record_defined_terms` function with the complete list.

--- DEFINITIONS SECTION START ---
{section_text}
--- DEFINITIONS SECTION END ---
"""


def extract_definitions(
    section_map: SectionMap,
    max_chars: int = 40_000,
    force_refresh: bool = False,
    section: Section | None = None,
) -> DefinitionsGraph:
    """Extract defined terms from the Definitions section using Gemini 2.5 Pro.

    Sends ONLY the Definitions section (not the full 300-page prospectus) to
    the LLM. Uses function/tool calling to force structured JSON output.

    Parameters
    ----------
    section_map:
        A :class:`SectionMap` built from the prospectus markdown.
    max_chars:
        Maximum characters of the Definitions section to send to the LLM.
        The Green Lion 2026-1 Definitions section is ~30 k chars; the default
        of 40 k leaves headroom.
    force_refresh:
        Accepted so a deal-model ``force_refresh`` can propagate uniformly to
        every sub-extractor.  This function holds no on-disk cache of its own
        (it always calls Gemini against the supplied ``section_map``), so the
        flag is a no-op here — but keeping the parameter avoids a special-case
        at the call site and documents that nothing stale is served.
    section:
        Optional pre-resolved Definitions :class:`Section` (e.g. from the
        assembler's language-agnostic
        :func:`~loanwhiz.extraction.section_router.resolve_sections`). When
        supplied it replaces the English keyword lookup
        ``section_map.find("definitions", "9.1")`` — this is what lets a
        non-English (IT/ES) prospectus, whose definitions heading the English
        keywords don't match, still extract instead of raising ``ValueError``.
        When ``None`` (the default) the keyword lookup runs unchanged.

        Either way the located section is then passed through
        :func:`~loanwhiz.extraction.section_router.widen_to_definitions`, so a
        glossary Docling shattered into sibling headings is recovered before
        the budget is applied (#548). That widening is a strict no-op on a
        section already carrying a real glossary and is idempotent, so a
        section pre-widened by ``resolve_sections`` arrives unchanged and the
        Green Lion / Cairn paths are byte-identical.

    Raises
    ------
    ValueError
        If no Definitions section is found in the prospectus.
    RuntimeError
        If the Gemini response does not contain the expected function call.
    """
    defs_section = section if section is not None else section_map.find("definitions", "9.1")
    if not defs_section:
        raise ValueError("Definitions section not found in prospectus")

    # Rescue a glossary that Docling shattered into sibling sections before any
    # budget is applied — otherwise the cap is measured against a stub and the
    # extraction silently succeeds on 1.3% of the terms (#548). A no-op when the
    # routed section already carries a real glossary, so Cairn and Green Lion are
    # byte-identical, and idempotent when the assembler passes an already-widened
    # section through ``section``.
    defs_section = widen_to_definitions(section_map, defs_section)

    section_text, truncation = clip(defs_section.title, defs_section.text, max_chars)
    if truncation is not None:
        # A definitions section longer than the budget is truncated MID-GLOSSARY,
        # and a glossary is alphabetical — so the terms that survive are a prefix
        # of the alphabet, not a sample. Cairn CLO XVII's runs past this cap, and
        # the result was 25 terms spanning "Acceleration Notice" to "Bankruptcy
        # Exchange Test": a healthy-looking count that silently excluded every
        # coverage test, whose thresholds live under C-F. Say so, so a caller
        # reading a plausible term count knows what it does not contain (#456).
        #
        # What a caller should do about it, for a CLO (#480): the thresholds are
        # not only obtainable here. A monthly trustee report states each coverage
        # test's required level beside the computed ratio, and
        # `collateral_schedule_parser.parse_liability_summary_text` reads them.
        # Raising this cap is therefore not the only route, and for a deal with
        # trustee reports it is not the cheapest one.
        logger.warning(
            "definitions section truncated at %d of %d chars — a glossary is "
            "alphabetical, so no term after %r was seen",
            truncation.kept_chars,
            truncation.original_chars,
            truncation.last_line,
        )

    client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)

    # Define the extraction tool so Gemini is forced into structured output.
    extract_tool = genai_types.Tool(
        function_declarations=[
            genai_types.FunctionDeclaration(
                name=_EXTRACT_TOOL_NAME,
                description=_EXTRACT_TOOL_DESCRIPTION,
                parameters=genai_types.Schema(
                    type=genai_types.Type.OBJECT,
                    properties={
                        "terms": genai_types.Schema(
                            type=genai_types.Type.ARRAY,
                            description="List of all defined terms found in the section",
                            items=genai_types.Schema(
                                type=genai_types.Type.OBJECT,
                                properties={
                                    "term": genai_types.Schema(
                                        type=genai_types.Type.STRING,
                                        description="The capitalised defined term exactly as it appears in quotes",
                                    ),
                                    "definition": genai_types.Schema(
                                        type=genai_types.Type.STRING,
                                        description="Full text of the definition",
                                    ),
                                    "page_or_section": genai_types.Schema(
                                        type=genai_types.Type.STRING,
                                        description='Section reference, e.g. "Section 9.1"',
                                    ),
                                },
                                required=["term", "definition", "page_or_section"],
                            ),
                        ),
                    },
                    required=["terms"],
                ),
            )
        ]
    )

    prompt = _PROMPT_TEMPLATE.format(section_text=section_text)

    response = client.models.generate_content(
        model=MODEL_PRO,
        contents=prompt,
        config=genai_types.GenerateContentConfig(
            tools=[extract_tool],
            tool_config=genai_types.ToolConfig(
                function_calling_config=genai_types.FunctionCallingConfig(
                    mode=genai_types.FunctionCallingConfigMode.ANY,
                    allowed_function_names=[_EXTRACT_TOOL_NAME],
                )
            ),
            temperature=0.0,
        ),
    )

    # Extract the function call arguments from the response.
    function_call = None
    for part in response.candidates[0].content.parts:
        if hasattr(part, "function_call") and part.function_call:
            function_call = part.function_call
            break

    if function_call is None:
        raise RuntimeError(
            f"Gemini did not return a function call. Response text: "
            f"{response.text if hasattr(response, 'text') else '<no text>'}"
        )

    args = function_call.args
    raw_terms: list[dict] = args.get("terms", [])

    graph = DefinitionsGraph(truncation=truncation)
    for raw in raw_terms:
        term_str = raw.get("term", "").strip()
        definition_str = raw.get("definition", "").strip()
        page_sec = raw.get("page_or_section", "Definitions").strip()
        if not term_str or not definition_str:
            continue
        excerpt = definition_str[:200]
        graph.terms[term_str] = DefinedTerm(
            term=term_str,
            definition=definition_str,
            page_or_section=page_sec,
            excerpt=excerpt,
        )

    # Judged after the terms land, so the verdict is about the real output
    # rather than about the inputs we hoped it would have.
    graph.coverage = assess_glossary(graph.terms, len(defs_section.text), truncation)
    if graph.coverage.implausible:
        logger.warning("definitions extraction looks incomplete: %s", graph.coverage.reason)

    return graph


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

# Co-located under the repo's managed ``data/`` cache tree so the whole
# extraction cache lifecycle is coherent (a cold ``data/`` wipe clears it too).
# See waterfall_extractor for the full #152 rationale.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CACHE_DIR = _REPO_ROOT / "data" / "extraction_cache"


def _default_cache_path(prospectus_url: str) -> Path:
    """Derive a cache file path from the prospectus URL basename."""
    basename = Path(prospectus_url.split("?")[0]).stem  # strip query string
    # Sanitise for filesystem
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", basename)
    return _CACHE_DIR / f"definitions_{safe}.json"


def _graph_to_json(graph: DefinitionsGraph) -> str:
    terms_list = [
        {
            "term": dt.term,
            "definition": dt.definition,
            "page_or_section": dt.page_or_section,
            "excerpt": dt.excerpt,
        }
        for dt in graph.terms.values()
    ]
    payload: dict = {"terms": terms_list}
    # Only written when present, so a graph with nothing to report serialises to
    # exactly the pre-#548 shape and existing cache files stay byte-comparable.
    if graph.truncation is not None:
        payload["truncation"] = graph.truncation.to_dict()
    if graph.coverage is not None:
        payload["coverage"] = graph.coverage.to_dict()
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _graph_from_json(data: dict) -> DefinitionsGraph:
    # ``.get`` returning ``None`` for both keys is what lets a cache written
    # before #548 load unchanged rather than raising.
    graph = DefinitionsGraph(
        truncation=Truncation.from_dict(data.get("truncation")),
        coverage=GlossaryCoverage.from_dict(data.get("coverage")),
    )
    for item in data.get("terms", []):
        term_str = item.get("term", "").strip()
        if not term_str:
            continue
        definition_str = item.get("definition", "")
        graph.terms[term_str] = DefinedTerm(
            term=term_str,
            definition=definition_str,
            page_or_section=item.get("page_or_section", "Definitions"),
            excerpt=item.get("excerpt", definition_str[:200]),
        )
    return graph


def load_or_extract(
    prospectus_url: str,
    cache_path: str | None = None,
    force_refresh: bool = False,
) -> DefinitionsGraph:
    """Load cached definitions graph or extract fresh from the prospectus.

    Strategy
    --------
    1. If ``cache_path`` (or the auto-derived path) exists, deserialise and
       return immediately — no network, no LLM call.
    2. Otherwise:
       a. Download the prospectus PDF with ``httpx``.
       b. Convert to markdown with Docling.
       c. Parse with :func:`route_sections`.
       d. Call :func:`extract_definitions` (Gemini).
       e. Serialise to ``cache_path`` for future runs.
       f. Return the graph.

    Parameters
    ----------
    prospectus_url:
        URL of the prospectus PDF (HuggingFace or similar).
    cache_path:
        Override the default cache location. Auto-derived from ``prospectus_url``
        when ``None``.
    force_refresh:
        When ``True``, ignore any cached definitions on disk and re-run the
        download → Docling → Gemini pipeline (the fresh graph is written back
        to the cache).

    Returns
    -------
    DefinitionsGraph
        Populated definitions graph.

    Raises
    ------
    ImportError
        If ``httpx`` or ``docling`` are not installed.
    RuntimeError
        If download or Docling conversion fails.
    """
    cache = Path(cache_path) if cache_path else _default_cache_path(prospectus_url)

    if cache.exists() and not force_refresh:
        data = json.loads(cache.read_text(encoding="utf-8"))
        return _graph_from_json(data)

    # Ensure cache directory exists
    cache.parent.mkdir(parents=True, exist_ok=True)

    # Download PDF
    try:
        import httpx
    except ImportError as exc:
        raise ImportError("httpx is required for load_or_extract; install it first") from exc

    pdf_path = cache.parent / f"{cache.stem}.pdf"
    try:
        with httpx.Client(follow_redirects=True, timeout=120) as client:
            resp = client.get(prospectus_url)
            resp.raise_for_status()
        pdf_path.write_bytes(resp.content)
    except Exception as exc:
        raise RuntimeError(f"Failed to download prospectus from {prospectus_url}: {exc}") from exc

    # Convert to markdown with Docling
    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:
        raise ImportError("docling is required for load_or_extract; install it first") from exc

    try:
        converter = DocumentConverter()
        result = converter.convert(str(pdf_path))
        markdown = result.document.export_to_markdown()
    except Exception as exc:
        raise RuntimeError(f"Docling conversion failed for {pdf_path}: {exc}") from exc

    # Parse sections and extract definitions
    section_map = route_sections(markdown)
    graph = extract_definitions(section_map)

    # Cache to disk
    cache.write_text(_graph_to_json(graph), encoding="utf-8")

    return graph
