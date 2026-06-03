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
import re
from dataclasses import dataclass, field
from pathlib import Path

from google import genai
from google.genai import types as genai_types

from loanwhiz.config import GCP_LOCATION, GCP_PROJECT, MODEL_PRO
from loanwhiz.extraction.section_router import SectionMap, route_sections


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


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

    Raises
    ------
    ValueError
        If no Definitions section is found in the prospectus.
    RuntimeError
        If the Gemini response does not contain the expected function call.
    """
    defs_section = section_map.find("definitions", "9.1")
    if not defs_section:
        raise ValueError("Definitions section not found in prospectus")

    section_text = defs_section.text[:max_chars]

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

    graph = DefinitionsGraph()
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

    return graph


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

_CACHE_DIR = Path("/tmp/loanwhiz_cache")


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
    return json.dumps({"terms": terms_list}, ensure_ascii=False, indent=2)


def _graph_from_json(data: dict) -> DefinitionsGraph:
    graph = DefinitionsGraph()
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

    if cache.exists():
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
