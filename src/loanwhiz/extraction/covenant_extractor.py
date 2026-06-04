"""Covenant and trigger extractor — extract structured trigger objects from a prospectus.

Uses Gemini 2.5 Pro via function/tool calling to extract trigger definitions,
threshold values, and monitoring conditions from three key sections of a
structured finance prospectus:

- Priority of Payments (triggers referenced as conditions in the waterfall)
- Conditions of the Notes (formal trigger definitions)
- Issuer Covenants (negative covenants)

Results are cached to disk to avoid re-invoking Gemini on every run.

Usage
-----
    from loanwhiz.extraction.section_router import route_sections
    from loanwhiz.extraction.definitions_graph import DefinitionsGraph
    from loanwhiz.extraction.covenant_extractor import extract_covenants

    section_map = route_sections(markdown_text)
    definitions = DefinitionsGraph()
    covenants = extract_covenants(section_map, definitions)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel

from loanwhiz.config import GCP_LOCATION, GCP_PROJECT, MODEL_PRO
from loanwhiz.extraction.section_router import SectionMap


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class ExtractedTrigger(BaseModel):
    """A single trigger or covenant condition extracted from a prospectus."""

    name: str                   # "sequential_pay_trigger", "pdl_trigger", etc.
    display_name: str           # "Sequential Pay Trigger"
    description: str            # plain English explanation of the trigger
    metric: str                 # what to measure: "cumulative_loss_rate_pct", "pdl_debit_balance", etc.
    threshold: float | None     # numerical threshold (None if not quantified in prospectus)
    threshold_unit: str | None  # "percentage", "eur", "fraction", etc.
    direction: str              # "above" | "below" | "non_zero"
    consequence: str            # what changes when triggered
    section_reference: str      # "Section 5.2", "Condition 4.6", etc.
    citation: dict              # {document, page_or_row, excerpt}


class ExtractedCovenants(BaseModel):
    """Structured output of the covenant and trigger extraction."""

    deal_name: str
    triggers: list[ExtractedTrigger]
    issuer_covenants: list[str]    # list of negative covenants (plain text)
    extraction_confidence: float


# ---------------------------------------------------------------------------
# Gemini extraction
# ---------------------------------------------------------------------------

_EXTRACT_TOOL_NAME = "record_triggers_and_covenants"

_EXTRACT_TOOL_DESCRIPTION = (
    "Record all triggers, covenant conditions, and issuer covenants found in "
    "the provided prospectus sections. Call this function exactly once with "
    "the complete lists of triggers and issuer covenants."
)

_PROMPT_TEMPLATE = """\
You are a structured finance document analyst specialising in RMBS/ABS prospectuses.

The text below comes from three sections of a structured finance prospectus:
1. Priority of Payments — triggers are referenced as conditions that change the waterfall
2. Conditions of the Notes — formal trigger definitions and thresholds
3. Issuer Covenants — negative covenants the issuer must comply with

Extract ALL triggers and covenant conditions from this text. Focus on:
- Sequential Pay Trigger (switches principal from pro-rata to sequential distribution)
- Principal Deficiency Ledger (PDL) triggers (Class A PDL, Class B PDL)
- Reserve Fund shortfall / triggers
- Clean-Up Call Option (triggered when pool balance < threshold % of original)
- Any other event of default, acceleration trigger, or performance trigger

For each trigger:
- name: snake_case identifier (e.g. "sequential_pay_trigger", "class_a_pdl_trigger")
- display_name: human-readable title (e.g. "Sequential Pay Trigger")
- description: plain English explanation of what the trigger is and when it fires
- metric: the measurable quantity (e.g. "cumulative_loss_rate_pct", "pdl_debit_balance",
  "pool_balance_fraction", "reserve_fund_balance")
- threshold: numerical value if stated (e.g. 10.0 for 10%), or null if not quantified
- threshold_unit: "percentage", "eur", "fraction", "boolean" — or null
- direction: "above" (metric > threshold triggers it), "below" (metric < threshold),
  or "non_zero" (any positive debit balance triggers it)
- consequence: what changes when the trigger fires (e.g. "Principal distribution switches
  from pro-rata to sequential", "Reserve Fund must be topped up")
- section_reference: section or condition number where defined (e.g. "Section 5.2",
  "Condition 4.6")
- citation: a dict with keys "document" (prospectus name or "prospectus"),
  "page_or_row" (section ref or "unknown"), and "excerpt" (a verbatim short
  excerpt of at most 150 characters from the text)

Also extract all issuer covenants (negative covenants) as plain text strings.

Set extraction_confidence between 0.0 and 1.0:
- 1.0: all five known Green Lion triggers found with thresholds
- 0.8: four triggers found, some thresholds missing
- 0.6–0.7: two or three triggers found
- below 0.6: fewer than two triggers found

Call the `record_triggers_and_covenants` function with the complete results.

--- PROSPECTUS SECTIONS START ---
{section_text}
--- PROSPECTUS SECTIONS END ---
"""

# Gemini function declaration schema for a single trigger
_TRIGGER_SCHEMA = genai_types.Schema(
    type=genai_types.Type.OBJECT,
    properties={
        "name": genai_types.Schema(
            type=genai_types.Type.STRING,
            description="snake_case identifier, e.g. sequential_pay_trigger",
        ),
        "display_name": genai_types.Schema(
            type=genai_types.Type.STRING,
            description="Human-readable title",
        ),
        "description": genai_types.Schema(
            type=genai_types.Type.STRING,
            description="Plain English explanation of the trigger",
        ),
        "metric": genai_types.Schema(
            type=genai_types.Type.STRING,
            description="Measurable quantity name, e.g. cumulative_loss_rate_pct",
        ),
        "threshold": genai_types.Schema(
            type=genai_types.Type.NUMBER,
            description="Numerical threshold value, or omit if not quantified",
        ),
        "threshold_unit": genai_types.Schema(
            type=genai_types.Type.STRING,
            description="Unit: percentage, eur, fraction, boolean",
        ),
        "direction": genai_types.Schema(
            type=genai_types.Type.STRING,
            description="above | below | non_zero",
        ),
        "consequence": genai_types.Schema(
            type=genai_types.Type.STRING,
            description="What changes when trigger fires",
        ),
        "section_reference": genai_types.Schema(
            type=genai_types.Type.STRING,
            description="Section or condition number, e.g. Section 5.2",
        ),
        "citation": genai_types.Schema(
            type=genai_types.Type.OBJECT,
            description="Citation dict with document, page_or_row, excerpt",
            properties={
                "document": genai_types.Schema(type=genai_types.Type.STRING),
                "page_or_row": genai_types.Schema(type=genai_types.Type.STRING),
                "excerpt": genai_types.Schema(type=genai_types.Type.STRING),
            },
            required=["document", "page_or_row", "excerpt"],
        ),
    },
    required=[
        "name",
        "display_name",
        "description",
        "metric",
        "direction",
        "consequence",
        "section_reference",
        "citation",
    ],
)


def _build_extract_tool() -> genai_types.Tool:
    """Build the Gemini function-calling tool for trigger extraction."""
    return genai_types.Tool(
        function_declarations=[
            genai_types.FunctionDeclaration(
                name=_EXTRACT_TOOL_NAME,
                description=_EXTRACT_TOOL_DESCRIPTION,
                parameters=genai_types.Schema(
                    type=genai_types.Type.OBJECT,
                    properties={
                        "triggers": genai_types.Schema(
                            type=genai_types.Type.ARRAY,
                            description="All triggers and covenant conditions extracted",
                            items=_TRIGGER_SCHEMA,
                        ),
                        "issuer_covenants": genai_types.Schema(
                            type=genai_types.Type.ARRAY,
                            description="Issuer negative covenants as plain text strings",
                            items=genai_types.Schema(type=genai_types.Type.STRING),
                        ),
                        "extraction_confidence": genai_types.Schema(
                            type=genai_types.Type.NUMBER,
                            description="Confidence score 0.0–1.0",
                        ),
                    },
                    required=["triggers", "issuer_covenants", "extraction_confidence"],
                ),
            )
        ]
    )


def _collect_section_text(
    section_map: SectionMap,
    max_chars_per_section: int = 15_000,
) -> tuple[str, str]:
    """Collect text from the three target sections.

    Returns
    -------
    section_text:
        Combined text of all found sections, prefixed with section headers.
    deal_name:
        Best-effort deal name extracted from the document title section,
        defaulting to "Unknown Deal".
    """
    # Keywords tuned to Green Lion 2026-1 but intentionally broad for other RMBS/CLO
    target_keywords = [
        ("priority_of_payments", ["priority of payments", "5.2", "5.3", "revenue priority", "redemption priority"]),
        ("conditions_of_notes", ["conditions of the notes", "conditions of notes", "4.6", "event of default"]),
        ("issuer_covenants", ["issuer covenant", "negative covenant", "undertaking"]),
    ]

    parts: list[str] = []
    for section_label, keywords in target_keywords:
        sections = section_map.find_all(*keywords)
        if sections:
            for sec in sections[:2]:  # at most 2 sub-sections per category
                text = sec.text[:max_chars_per_section]
                parts.append(f"\n\n=== {section_label.upper().replace('_', ' ')}: {sec.title} ===\n{text}")
        # fall through silently if a section is missing — the LLM will extract
        # what it can from the other sections

    # If no specific sections found, send the first 30 k chars of the document
    if not parts:
        parts.append(section_map.full_text[:30_000])

    # Try to extract deal name from title or first heading
    deal_name = "Unknown Deal"
    if section_map.sections:
        first_title = section_map.sections[0].title
        if first_title:
            deal_name = first_title.strip()

    return "".join(parts), deal_name


def extract_covenants(
    section_map: SectionMap,
    definitions: object,  # DefinitionsGraph — accepted but not required for extraction
    cache_path: str | None = None,
    force_refresh: bool = False,
) -> ExtractedCovenants:
    """Extract triggers and covenants from prospectus using Gemini 2.5 Pro.

    Searches: Priority of Payments (triggers referenced there),
    Conditions of the Notes (formal trigger definitions),
    Issuer Covenants section.

    Caches result to ``data/extraction_cache/covenants_{deal_name}.json`` when
    no explicit ``cache_path`` is provided.

    Parameters
    ----------
    section_map:
        A :class:`SectionMap` built from the prospectus markdown.
    definitions:
        A DefinitionsGraph — accepted for API consistency but not required
        for the LLM extraction call (Gemini reads section text directly).
    cache_path:
        Override the default cache location. Auto-derived from ``deal_name``
        when ``None``.
    force_refresh:
        When ``True``, ignore any cached result on disk and re-run the Gemini
        extraction (the fresh result is still written back to the cache).  This
        is what lets a deal-model ``force_refresh`` bust a stale covenant cache.

    Returns
    -------
    ExtractedCovenants
        Populated model with triggers, issuer covenants, and confidence score.

    Raises
    ------
    RuntimeError
        If the Gemini response does not contain the expected function call.
    """
    section_text, deal_name = _collect_section_text(section_map)

    # Resolve cache path
    resolved_cache = Path(cache_path) if cache_path else _default_cache_path(deal_name)

    # Load from cache if available (unless force_refresh busts it)
    if resolved_cache.exists() and not force_refresh:
        data = json.loads(resolved_cache.read_text(encoding="utf-8"))
        return _covenants_from_json(data)

    # Ensure cache directory exists
    resolved_cache.parent.mkdir(parents=True, exist_ok=True)

    client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)
    extract_tool = _build_extract_tool()
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

    # Extract the function call arguments from the response
    function_call = None
    for part in response.candidates[0].content.parts:
        if hasattr(part, "function_call") and part.function_call:
            function_call = part.function_call
            break

    if function_call is None:
        raise RuntimeError(
            "Gemini did not return a function call. Response text: "
            f"{response.text if hasattr(response, 'text') else '<no text>'}"
        )

    args = function_call.args
    raw_triggers: list[dict] = args.get("triggers", [])
    raw_covenants: list[str] = args.get("issuer_covenants", [])
    confidence: float = float(args.get("extraction_confidence", 0.0))

    triggers: list[ExtractedTrigger] = []
    for raw in raw_triggers:
        # Normalise optional fields that Gemini may omit
        citation_raw = raw.get("citation", {})
        if not isinstance(citation_raw, dict):
            citation_raw = {"document": "prospectus", "page_or_row": "unknown", "excerpt": str(citation_raw)}

        trigger = ExtractedTrigger(
            name=raw.get("name", "unknown_trigger"),
            display_name=raw.get("display_name", raw.get("name", "Unknown Trigger")),
            description=raw.get("description", ""),
            metric=raw.get("metric", ""),
            threshold=raw.get("threshold"),  # None if omitted
            threshold_unit=raw.get("threshold_unit"),  # None if omitted
            direction=raw.get("direction", "above"),
            consequence=raw.get("consequence", ""),
            section_reference=raw.get("section_reference", ""),
            citation=citation_raw,
        )
        triggers.append(trigger)

    covenants = ExtractedCovenants(
        deal_name=deal_name,
        triggers=triggers,
        issuer_covenants=[c for c in raw_covenants if isinstance(c, str) and c.strip()],
        extraction_confidence=confidence,
    )

    # Cache to disk
    resolved_cache.write_text(_covenants_to_json(covenants), encoding="utf-8")

    return covenants


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

# Co-located under the repo's managed ``data/`` cache tree so the whole
# extraction cache lifecycle is coherent (a cold ``data/`` wipe clears it too).
# See waterfall_extractor for the full #152 rationale.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CACHE_DIR = _REPO_ROOT / "data" / "extraction_cache"


def _default_cache_path(deal_name: str) -> Path:
    """Derive a cache file path from the deal name."""
    safe = re.sub(r"[^a-zA-Z0-9_\-]", "_", deal_name)
    return _CACHE_DIR / f"covenants_{safe}.json"


def _covenants_to_json(covenants: ExtractedCovenants) -> str:
    """Serialise an ExtractedCovenants to a JSON string."""
    triggers_list = [
        {
            "name": t.name,
            "display_name": t.display_name,
            "description": t.description,
            "metric": t.metric,
            "threshold": t.threshold,
            "threshold_unit": t.threshold_unit,
            "direction": t.direction,
            "consequence": t.consequence,
            "section_reference": t.section_reference,
            "citation": t.citation,
        }
        for t in covenants.triggers
    ]
    payload = {
        "deal_name": covenants.deal_name,
        "triggers": triggers_list,
        "issuer_covenants": covenants.issuer_covenants,
        "extraction_confidence": covenants.extraction_confidence,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _covenants_from_json(data: dict) -> ExtractedCovenants:
    """Deserialise an ExtractedCovenants from a parsed JSON dict."""
    triggers: list[ExtractedTrigger] = []
    for item in data.get("triggers", []):
        citation = item.get("citation", {})
        if not isinstance(citation, dict):
            citation = {"document": "prospectus", "page_or_row": "unknown", "excerpt": str(citation)}
        triggers.append(
            ExtractedTrigger(
                name=item.get("name", ""),
                display_name=item.get("display_name", ""),
                description=item.get("description", ""),
                metric=item.get("metric", ""),
                threshold=item.get("threshold"),
                threshold_unit=item.get("threshold_unit"),
                direction=item.get("direction", "above"),
                consequence=item.get("consequence", ""),
                section_reference=item.get("section_reference", ""),
                citation=citation,
            )
        )
    return ExtractedCovenants(
        deal_name=data.get("deal_name", "Unknown Deal"),
        triggers=triggers,
        issuer_covenants=data.get("issuer_covenants", []),
        extraction_confidence=float(data.get("extraction_confidence", 0.0)),
    )
