"""Waterfall extractor — extract Priority of Payments sections as structured objects.

Extracts Revenue Priority of Payments (§5.2), Redemption Priority of Payments
(§5.3), and Post-Enforcement Priority of Payments from a structured finance
prospectus using Gemini 2.5 Pro via function calling (forced structured output).

Only the relevant Priority of Payments section (~8 pages) is sent to the LLM.
Defined terms are resolved via the DefinitionsGraph before the prompt is built.
Results are cached to disk to avoid re-invoking Gemini on every test run.

Usage
-----
    from loanwhiz.extraction.section_router import route_sections
    from loanwhiz.extraction.definitions_graph import load_or_extract
    from loanwhiz.extraction.waterfall_extractor import (
        extract_waterfall, extract_all_waterfalls
    )

    section_map = route_sections(markdown_text)
    definitions = load_or_extract(prospectus_url)
    waterfall = extract_waterfall(section_map, definitions, "revenue")
    all_waterfalls = extract_all_waterfalls(section_map, definitions)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from google import genai
from google.genai import types as genai_types
from pydantic import BaseModel

from loanwhiz.config import GCP_LOCATION, GCP_PROJECT, MODEL_PRO
from loanwhiz.extraction.definitions_graph import DefinitionsGraph
from loanwhiz.extraction.section_router import SectionMap


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


class WaterfallStep(BaseModel):
    """A single step in a Priority of Payments waterfall."""

    priority: str           # "(a)", "(b)", etc.
    recipient: str          # "security_trustee_fees", "class_a_interest", etc.
    description: str        # plain text description
    amount_formula: str     # how to compute the amount (prose or formula)
    condition: str | None   # any trigger condition
    is_pari_passu: bool     # true if this step pays multiple parties pro-rata
    citation: dict          # {document, page_or_row, excerpt}


class ExtractedWaterfall(BaseModel):
    """A fully extracted waterfall with all steps and provenance."""

    deal_name: str
    waterfall_type: str     # "revenue", "redemption", "post_enforcement"
    steps: list[WaterfallStep]
    source_section: str     # "Section 5.2" etc.
    extraction_confidence: float   # 0–1: real step-usability coverage (see _extraction_confidence)


def _extraction_confidence(steps: list["WaterfallStep"]) -> float:
    """Real per-waterfall coverage score in ``[0, 1]`` over extracted steps.

    Replaces the old metric (fraction of steps with a *non-empty recipient*),
    which was near-tautological — the materialiser strips recipients to a string,
    so a step almost always has one — and said nothing about whether the step is
    actually *executable* downstream. A step is only usable by the waterfall
    interpreter when it names **both** a recipient (who is paid) and an
    ``amount_formula`` (how much); the score is the fraction of steps that carry
    both, lightly bonused by citation presence (provenance the step can be
    audited against).

    Scoring per step (averaged across all steps):

    - 0.7 — has a non-empty ``recipient`` *and* a non-empty ``amount_formula``
      (the executable core).
    - +0.3 — additionally carries a non-empty citation (auditable provenance).
    - 0.35 — has a recipient but no amount formula (named but not yet
      executable).
    - 0.0 — no recipient.

    An empty step list scores 0.0 (nothing was extracted).
    """
    if not steps:
        return 0.0

    total = 0.0
    for s in steps:
        recipient = (s.recipient or "").strip()
        formula = (s.amount_formula or "").strip()
        citation = s.citation if isinstance(s.citation, dict) else {}
        has_citation = any(str(v).strip() for v in citation.values())
        if recipient and formula:
            total += 1.0 if has_citation else 0.7
        elif recipient:
            total += 0.35
        # no recipient → contributes 0.0
    return total / len(steps)


# ---------------------------------------------------------------------------
# Extraction tool schema
# ---------------------------------------------------------------------------

_EXTRACT_TOOL_NAME = "record_waterfall_steps"

_EXTRACT_TOOL_DESCRIPTION = (
    "Record every step of the Priority of Payments waterfall found in the "
    "prospectus section. Call this function exactly once with the complete "
    "ordered list of all payment steps."
)

# Keywords used to locate each waterfall section via SectionMap.find().
#
# These must match the *content* sub-section that actually holds the payment
# steps — e.g. ``## Revenue Priority of Payments`` — not the numbered parent
# header (``## 5.2 PRIORITIES OF PAYMENTS``), which in the Green Lion 2026-1
# prospectus is an empty heading immediately preceding the content sub-section.
# ``SectionMap.find`` returns the *first* section (in document order) whose
# title contains any keyword, so a numeric keyword like ``"5.2"`` matched the
# empty parent header and the revenue waterfall extracted 0 steps (#122).
# ``"5.3"`` was likewise unsafe: it matches ``## 5.3 LOSS ALLOCATION``, not the
# redemption section. Keep only the descriptive content-section keywords.
_WATERFALL_SECTION_KEYWORDS: dict[str, list[str]] = {
    "revenue": ["revenue priority of payments", "revenue priority"],
    "redemption": ["redemption priority of payments", "redemption priority"],
    "post_enforcement": [
        "post-enforcement priority",
        "post enforcement priority",
        "post-enforcement",
        "post enforcement",
    ],
}

# Human-readable section names for citation and prompt context.
_WATERFALL_SECTION_NAMES: dict[str, str] = {
    "revenue": "Revenue Priority of Payments",
    "redemption": "Redemption Priority of Payments",
    "post_enforcement": "Post-Enforcement Priority of Payments",
}

# Key defined terms to resolve and inject into the prompt for each waterfall type.
_KEY_TERMS: dict[str, list[str]] = {
    "revenue": [
        "Available Revenue Funds",
        "Sequential Pay Trigger",
        "Principal Deficiency Ledger",
        "Notes Payment Date",
        "Interest Rate Swap",
    ],
    "redemption": [
        "Available Redemption Funds",
        "Sequential Pay Trigger",
        "Principal Deficiency Ledger",
        "Optional Redemption Date",
        "Final Redemption Date",
    ],
    "post_enforcement": [
        "Available Enforcement Proceeds",
        "Principal Deficiency Ledger",
        "Enforcement Notice",
    ],
}

_PROMPT_TEMPLATE = """\
You are a structured finance document analyst. The text below is the \
{waterfall_section_name} section of a structured finance prospectus (RMBS/ABS).

Extract EVERY payment step from this waterfall in order. Each step is typically \
identified by a letter in parentheses: (a), (b), (c), etc. For each step:

- priority: the letter label, e.g. "(a)", "(b)"
- recipient: a snake_case identifier for the recipient/purpose, e.g. \
"security_trustee_fees", "class_a_interest", "class_a_pdl_replenishment"
- description: the full plain-text description of who gets paid and why
- amount_formula: how the payment amount is calculated (prose or formula; may \
be "as accrued" or reference a formula in the prospectus)
- condition: any conditional trigger for this step (e.g. "if Sequential Pay \
Trigger is not in effect") — null if unconditional
- is_pari_passu: true if this step pays multiple parties on a pro-rata basis \
simultaneously; false otherwise

For the citation field, use:
- document: "Green Lion 2026-1 Prospectus"
- page_or_row: the section reference visible in the text (e.g. "Section 5.2(a)")
- excerpt: the first 150 characters of the relevant paragraph for that step

{definitions_block}

Call the `record_waterfall_steps` function with the complete ordered list.

--- {waterfall_section_name_upper} SECTION START ---
{section_text}
--- {waterfall_section_name_upper} SECTION END ---
"""

_DEFINITIONS_BLOCK_TEMPLATE = """\
The following defined terms are referenced in this section. Use their \
definitions to inform your extraction:

{terms_text}
"""


# ---------------------------------------------------------------------------
# Gemini extraction
# ---------------------------------------------------------------------------


def _build_extract_tool() -> genai_types.Tool:
    """Build the Gemini FunctionDeclaration for structured waterfall extraction."""
    citation_schema = genai_types.Schema(
        type=genai_types.Type.OBJECT,
        properties={
            "document": genai_types.Schema(
                type=genai_types.Type.STRING,
                description="Name of the source document",
            ),
            "page_or_row": genai_types.Schema(
                type=genai_types.Type.STRING,
                description="Section or page reference within the document",
            ),
            "excerpt": genai_types.Schema(
                type=genai_types.Type.STRING,
                description="First 150 characters of the relevant paragraph",
            ),
        },
        required=["document", "page_or_row", "excerpt"],
    )

    step_schema = genai_types.Schema(
        type=genai_types.Type.OBJECT,
        properties={
            "priority": genai_types.Schema(
                type=genai_types.Type.STRING,
                description='Step label, e.g. "(a)", "(b)"',
            ),
            "recipient": genai_types.Schema(
                type=genai_types.Type.STRING,
                description="snake_case identifier for the recipient/purpose",
            ),
            "description": genai_types.Schema(
                type=genai_types.Type.STRING,
                description="Full plain-text description of the payment step",
            ),
            "amount_formula": genai_types.Schema(
                type=genai_types.Type.STRING,
                description="How the payment amount is calculated",
            ),
            "condition": genai_types.Schema(
                type=genai_types.Type.STRING,
                description="Conditional trigger, or empty string if unconditional",
            ),
            "is_pari_passu": genai_types.Schema(
                type=genai_types.Type.BOOLEAN,
                description="True if this step pays multiple parties pro-rata",
            ),
            "citation": citation_schema,
        },
        required=[
            "priority",
            "recipient",
            "description",
            "amount_formula",
            "condition",
            "is_pari_passu",
            "citation",
        ],
    )

    return genai_types.Tool(
        function_declarations=[
            genai_types.FunctionDeclaration(
                name=_EXTRACT_TOOL_NAME,
                description=_EXTRACT_TOOL_DESCRIPTION,
                parameters=genai_types.Schema(
                    type=genai_types.Type.OBJECT,
                    properties={
                        "steps": genai_types.Schema(
                            type=genai_types.Type.ARRAY,
                            description="Ordered list of all waterfall payment steps",
                            items=step_schema,
                        ),
                        "source_section": genai_types.Schema(
                            type=genai_types.Type.STRING,
                            description='Section reference, e.g. "Section 5.2"',
                        ),
                    },
                    required=["steps", "source_section"],
                ),
            )
        ]
    )


def _build_definitions_block(
    definitions: DefinitionsGraph,
    waterfall_type: str,
    section_text: str,
) -> str:
    """Build the definitions context block to inject into the prompt.

    Resolves the key terms listed in _KEY_TERMS[waterfall_type] as well as
    any additional terms found via definitions.resolve_all(section_text).
    Returns an empty string if no definitions are available.
    """
    resolved: dict[str, str] = {}

    # 1. Key terms for this waterfall type
    for term in _KEY_TERMS.get(waterfall_type, []):
        dt = definitions.resolve(term)
        if dt:
            resolved[dt.term] = dt.excerpt or dt.definition[:300]

    # 2. Any additional defined terms referenced in the section text
    found = definitions.resolve_all(section_text)
    for canonical_term, dt in found.items():
        if canonical_term not in resolved:
            resolved[canonical_term] = dt.excerpt or dt.definition[:300]

    if not resolved:
        return ""

    terms_text = "\n\n".join(
        f'"{term}": {definition}' for term, definition in resolved.items()
    )
    return _DEFINITIONS_BLOCK_TEMPLATE.format(terms_text=terms_text)


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

# Extraction sub-caches live under the repo's managed ``data/`` cache tree
# (alongside ``data/deals`` and ``data/docling_cache``), not a volatile ``/tmp``
# path. This keeps the whole cache lifecycle coherent: a cold rebuild that wipes
# ``data/`` clears these too, so a stale sub-cache can no longer outlive a
# deal-model rebuild and re-serve an outdated extraction. This was the #152
# revenue-waterfall=0 regression: a pre-#125 ``waterfall_*_revenue.json`` left
# in ``/tmp`` survived every ``data/``-only cold rebuild (force_refresh defaults
# to False), so the corrected section router never got a chance to re-extract.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_CACHE_DIR = _REPO_ROOT / "data" / "extraction_cache"


def _safe_name(name: str) -> str:
    """Sanitise a string for use in a filename."""
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", name).strip("_")


def _cache_path_for(deal_name: str, waterfall_type: str, cache_path: str | None) -> Path:
    """Return the cache path for a waterfall extraction result."""
    if cache_path:
        return Path(cache_path)
    safe_deal = _safe_name(deal_name)
    return _CACHE_DIR / f"waterfall_{safe_deal}_{waterfall_type}.json"


def _waterfall_to_dict(waterfall: ExtractedWaterfall) -> dict:
    """Serialise an ExtractedWaterfall to a JSON-compatible dict."""
    return {
        "deal_name": waterfall.deal_name,
        "waterfall_type": waterfall.waterfall_type,
        "source_section": waterfall.source_section,
        "extraction_confidence": waterfall.extraction_confidence,
        "steps": [
            {
                "priority": step.priority,
                "recipient": step.recipient,
                "description": step.description,
                "amount_formula": step.amount_formula,
                "condition": step.condition,
                "is_pari_passu": step.is_pari_passu,
                "citation": step.citation,
            }
            for step in waterfall.steps
        ],
    }


def _waterfall_from_dict(data: dict) -> ExtractedWaterfall:
    """Deserialise an ExtractedWaterfall from a dict (loaded from JSON)."""
    steps = [
        WaterfallStep(
            priority=s.get("priority", ""),
            recipient=s.get("recipient", ""),
            description=s.get("description", ""),
            amount_formula=s.get("amount_formula", ""),
            condition=s.get("condition") or None,
            is_pari_passu=bool(s.get("is_pari_passu", False)),
            citation=s.get("citation", {}),
        )
        for s in data.get("steps", [])
    ]
    return ExtractedWaterfall(
        deal_name=data.get("deal_name", ""),
        waterfall_type=data.get("waterfall_type", ""),
        steps=steps,
        source_section=data.get("source_section", ""),
        extraction_confidence=float(data.get("extraction_confidence", 0.0)),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_waterfall(
    section_map: SectionMap,
    definitions: DefinitionsGraph,
    waterfall_type: str = "revenue",
    deal_name: str = "deal",
    cache_path: str | None = None,
    max_chars: int = 20_000,
    force_refresh: bool = False,
) -> ExtractedWaterfall:
    """Extract waterfall steps from the prospectus using Gemini 2.5 Pro.

    Sends only the Priority of Payments section (~8 pages) to the LLM.
    Uses function calling to force structured JSON output.
    Resolves defined terms via the definitions graph and injects them into
    the prompt as context.
    Caches the result to disk to avoid re-running Gemini in tests.

    Parameters
    ----------
    section_map:
        A :class:`SectionMap` built from the prospectus markdown.
    definitions:
        A :class:`DefinitionsGraph` with resolved defined terms.
    waterfall_type:
        One of ``"revenue"``, ``"redemption"``, ``"post_enforcement"``.
    deal_name:
        Name of the deal, used in the cache filename and the result object.
    cache_path:
        Override the default cache location. Auto-derived when ``None``.
    max_chars:
        Maximum characters of the section text to send to Gemini.
    force_refresh:
        When ``True``, ignore any cached result on disk and re-run the Gemini
        extraction (the fresh result is still written back to the cache).  This
        is what lets a deal-model ``force_refresh`` bust a stale waterfall cache.

    Returns
    -------
    ExtractedWaterfall
        Pydantic model with all payment steps and provenance.

    Raises
    ------
    ValueError
        If the requested waterfall section is not found in the prospectus.
    RuntimeError
        If Gemini does not return the expected function call.
    """
    if waterfall_type not in _WATERFALL_SECTION_KEYWORDS:
        raise ValueError(
            f"Unknown waterfall_type {waterfall_type!r}. "
            f"Must be one of: {list(_WATERFALL_SECTION_KEYWORDS)}"
        )

    # Check cache first (unless force_refresh busts it).
    resolved_cache = _cache_path_for(deal_name, waterfall_type, cache_path)
    if resolved_cache.exists() and not force_refresh:
        data = json.loads(resolved_cache.read_text(encoding="utf-8"))
        return _waterfall_from_dict(data)

    # Locate the section.
    keywords = _WATERFALL_SECTION_KEYWORDS[waterfall_type]
    section = section_map.find(*keywords)
    if section is None:
        raise ValueError(
            f"No {waterfall_type!r} waterfall section found in the prospectus. "
            f"Tried keywords: {keywords}"
        )

    section_text = section.text[:max_chars]
    section_name = _WATERFALL_SECTION_NAMES[waterfall_type]

    # Build definitions context block.
    definitions_block = _build_definitions_block(definitions, waterfall_type, section_text)

    # Build prompt.
    prompt = _PROMPT_TEMPLATE.format(
        waterfall_section_name=section_name,
        waterfall_section_name_upper=section_name.upper(),
        definitions_block=definitions_block,
        section_text=section_text,
    )

    # Call Gemini with forced function calling.
    client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)
    extract_tool = _build_extract_tool()

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

    # Extract function call from response.
    function_call = None
    for part in response.candidates[0].content.parts:
        if hasattr(part, "function_call") and part.function_call:
            function_call = part.function_call
            break

    if function_call is None:
        raise RuntimeError(
            f"Gemini did not return a function call for {waterfall_type!r} waterfall. "
            f"Response: {response.text if hasattr(response, 'text') else '<no text>'}"
        )

    args = function_call.args
    raw_steps: list[dict] = args.get("steps", [])
    source_section: str = args.get("source_section", section.title)

    # Materialise WaterfallStep objects.
    steps: list[WaterfallStep] = []
    for raw in raw_steps:
        condition_raw = raw.get("condition", "")
        condition = condition_raw if condition_raw else None

        citation_raw = raw.get("citation", {})
        if not isinstance(citation_raw, dict):
            citation_raw = {}

        steps.append(
            WaterfallStep(
                priority=raw.get("priority", "").strip(),
                recipient=raw.get("recipient", "").strip(),
                description=raw.get("description", "").strip(),
                amount_formula=raw.get("amount_formula", "").strip(),
                condition=condition,
                is_pari_passu=bool(raw.get("is_pari_passu", False)),
                citation=citation_raw,
            )
        )

    # Compute extraction confidence as a real per-waterfall coverage metric over
    # how *usable* the extracted steps are (not merely the near-tautological
    # fraction with a non-empty recipient). See ``_extraction_confidence``.
    extraction_confidence = _extraction_confidence(steps)

    waterfall = ExtractedWaterfall(
        deal_name=deal_name,
        waterfall_type=waterfall_type,
        steps=steps,
        source_section=source_section,
        extraction_confidence=extraction_confidence,
    )

    # Cache to disk.
    resolved_cache.parent.mkdir(parents=True, exist_ok=True)
    resolved_cache.write_text(
        json.dumps(_waterfall_to_dict(waterfall), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return waterfall


def extract_all_waterfalls(
    section_map: SectionMap,
    definitions: DefinitionsGraph,
    deal_name: str = "deal",
    cache_dir: str | None = None,
    force_refresh: bool = False,
) -> dict[str, ExtractedWaterfall]:
    """Extract revenue, redemption, and post-enforcement waterfalls.

    Parameters
    ----------
    section_map:
        A :class:`SectionMap` built from the prospectus markdown.
    definitions:
        A :class:`DefinitionsGraph` with resolved defined terms.
    deal_name:
        Name of the deal, used in cache filenames and result objects.
    cache_dir:
        Override the default cache directory. When provided, cache files
        are written to ``<cache_dir>/waterfall_{deal_name}_{type}.json``.
    force_refresh:
        When ``True``, bust each waterfall's disk cache and re-extract.

    Returns
    -------
    dict[str, ExtractedWaterfall]
        Keys: ``"revenue"``, ``"redemption"``, ``"post_enforcement"``.
        Only includes waterfall types whose sections were found in the
        prospectus (missing sections are silently skipped).
    """
    results: dict[str, ExtractedWaterfall] = {}
    for waterfall_type in ("revenue", "redemption", "post_enforcement"):
        cache_path: str | None = None
        if cache_dir:
            safe_deal = _safe_name(deal_name)
            cache_path = str(
                Path(cache_dir) / f"waterfall_{safe_deal}_{waterfall_type}.json"
            )
        try:
            results[waterfall_type] = extract_waterfall(
                section_map=section_map,
                definitions=definitions,
                waterfall_type=waterfall_type,
                deal_name=deal_name,
                cache_path=cache_path,
                force_refresh=force_refresh,
            )
        except ValueError:
            # Section not found in this prospectus — skip silently.
            pass
    return results
