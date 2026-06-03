"""Deal model assembler — orchestrates all extraction primitives into one JSON artifact.

Combines:
- Section router (Docling markdown → SectionMap)
- Definitions graph (defined terms)
- Waterfall extractor (Priority of Payments steps)
- Covenant extractor (triggers and issuer covenants)

into a single :class:`DealModel` Pydantic object per deal, cached to disk so
the full extraction pipeline (Docling + three Gemini calls) runs at most once
per deal.

Usage
-----
    from loanwhiz.extraction.assembler import extract_deal_model

    model = extract_deal_model(
        prospectus_url="https://...",
        deal_name="Green Lion 2026-1 B.V.",
    )
    # Subsequent calls load from cache — no Docling, no Gemini.
    model2 = extract_deal_model(prospectus_url="https://...", deal_name="Green Lion 2026-1 B.V.")
    assert model == model2
"""

from __future__ import annotations

import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from loanwhiz.extraction.covenant_extractor import extract_covenants
from loanwhiz.extraction.definitions_graph import extract_definitions
from loanwhiz.extraction.section_router import extract_key_sf_sections, route_sections
from loanwhiz.extraction.waterfall_extractor import extract_all_waterfalls


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class DealModelMetadata(BaseModel):
    """Provenance and quality metadata for a :class:`DealModel`."""

    deal_name: str
    prospectus_url: str
    extracted_at: str           # ISO 8601
    extraction_duration_sec: float
    sections_found: list[str]
    completeness_score: float   # 0–1: fraction of expected sections found
    cache_path: str


class DealModel(BaseModel):
    """Complete extracted deal model — one artifact per deal.

    Combines the outputs of all four extraction primitives into a single
    JSON-serialisable object. Fields:

    metadata:
        Provenance, timing, completeness score.
    definitions:
        ``{term: {definition, page_or_section}}`` — every defined term.
    waterfalls:
        ``{waterfall_type: ExtractedWaterfall.model_dump()}`` — revenue,
        redemption, post_enforcement.
    covenants:
        ``ExtractedCovenants.model_dump()`` — triggers and issuer covenants.
    tranche_structure:
        Derived from waterfall steps — one dict per step (convenience view
        for downstream consumers that just want the payment hierarchy).
    trigger_names:
        Quick list of all trigger names from the covenants.
    """

    metadata: DealModelMetadata
    definitions: dict           # term → {definition, page_or_section}
    waterfalls: dict            # waterfall_type → ExtractedWaterfall.model_dump()
    covenants: dict             # ExtractedCovenants.model_dump()
    tranche_structure: list[dict]   # derived from waterfall + covenant extraction
    trigger_names: list[str]        # quick list of all trigger names


# ---------------------------------------------------------------------------
# Expected sections for completeness scoring
# ---------------------------------------------------------------------------

_EXPECTED_SECTIONS: list[str] = [
    "definitions",
    "revenue_priority_of_payments",
    "conditions_of_notes",
    "available_funds",
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_deal_model(
    prospectus_url: str,
    deal_name: str,
    cache_dir: str = "/tmp/loanwhiz_cache/deals",
    force_refresh: bool = False,
) -> DealModel:
    """Extract complete deal model from a prospectus PDF.

    Orchestrates: Docling → section router → definitions → waterfalls → covenants.
    Caches to ``{cache_dir}/{slug(deal_name)}.json``.  Subsequent calls with the
    same ``deal_name`` load from cache unless ``force_refresh=True``.

    Parameters
    ----------
    prospectus_url:
        URL of the prospectus PDF (HTTP or HTTPS).
    deal_name:
        Human-readable deal name, e.g. ``"Green Lion 2026-1 B.V."``.  Used to
        derive the cache filename and stored in the metadata.
    cache_dir:
        Directory in which to store the cached deal model JSON.
        Created automatically if it does not exist.
    force_refresh:
        When ``True``, bypass the cache and re-run the full extraction pipeline.

    Returns
    -------
    DealModel
        Populated deal model.  On cache hit the object is identical to what
        was persisted by the original run.

    Raises
    ------
    RuntimeError
        If the prospectus PDF cannot be downloaded.
    ValueError
        If a required extraction step fails (e.g. Definitions section missing).
    """
    cache_path = Path(cache_dir) / f"{_slug(deal_name)}.json"

    if cache_path.exists() and not force_refresh:
        return DealModel.model_validate_json(cache_path.read_text(encoding="utf-8"))

    t0 = time.time()

    # 1. Download and convert the PDF to markdown via Docling.
    markdown_text = _download_and_convert(prospectus_url)

    # 2. Route sections.
    section_map = route_sections(markdown_text)
    key_sections = extract_key_sf_sections(section_map)
    sections_found = [k for k, v in key_sections.items() if v is not None]

    # 3. Extract definitions.
    definitions_graph = extract_definitions(section_map)

    # 4. Extract waterfalls.
    waterfalls = extract_all_waterfalls(
        section_map,
        definitions_graph,
        deal_name=deal_name,
    )

    # 5. Extract covenants / triggers.
    covenants = extract_covenants(section_map, definitions_graph)

    # 6. Compute completeness.
    completeness = len(
        [s for s in _EXPECTED_SECTIONS if s in sections_found]
    ) / len(_EXPECTED_SECTIONS)

    # 7. Assemble.
    model = DealModel(
        metadata=DealModelMetadata(
            deal_name=deal_name,
            prospectus_url=prospectus_url,
            extracted_at=datetime.now(timezone.utc).isoformat(),
            extraction_duration_sec=time.time() - t0,
            sections_found=sections_found,
            completeness_score=completeness,
            cache_path=str(cache_path),
        ),
        definitions={
            t: {
                "definition": d.definition,
                "page_or_section": d.page_or_section,
            }
            for t, d in definitions_graph.terms.items()
        },
        waterfalls={k: v.model_dump() for k, v in waterfalls.items()},
        covenants=covenants.model_dump(),
        tranche_structure=_extract_tranches(waterfalls),
        trigger_names=[t.name for t in covenants.triggers],
    )

    # 8. Cache to disk.
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(model.model_dump_json(indent=2), encoding="utf-8")

    return model


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _slug(name: str) -> str:
    """Derive a filesystem-safe slug from a deal name.

    Lowercases the name, strips periods and commas, then replaces spaces
    with hyphens.  Consecutive hyphens are collapsed to one; leading/trailing
    hyphens are stripped.

    Examples
    --------
    >>> _slug("Green Lion 2026-1 B.V.")
    'green-lion-2026-1-bv'
    >>> _slug("Deal, Inc.")
    'deal-inc'
    """
    lowered = name.lower()
    # Remove periods and commas (they don't contribute to word boundaries).
    stripped = re.sub(r"[.,]+", "", lowered)
    # Replace spaces with hyphens.
    replaced = re.sub(r"\s+", "-", stripped)
    # Collapse runs of hyphens.
    collapsed = re.sub(r"-{2,}", "-", replaced)
    return collapsed.strip("-")


def _extract_tranches(waterfalls: dict) -> list[dict]:
    """Derive a tranche-structure list from the revenue waterfall steps.

    Each dict in the returned list represents one payment step from the
    revenue waterfall (or the first available waterfall when no revenue
    waterfall exists).  The step is the most natural proxy for the tranche
    hierarchy in a structured finance deal — one entry per priority class.

    Returns an empty list when no waterfalls are available.

    Parameters
    ----------
    waterfalls:
        Output of :func:`extract_all_waterfalls` — ``{waterfall_type: ExtractedWaterfall}``.

    Returns
    -------
    list[dict]
        One dict per step: ``{priority, recipient, description, waterfall_type}``.
    """
    # Prefer the revenue waterfall as the canonical tranche hierarchy.
    waterfall = waterfalls.get("revenue") or next(iter(waterfalls.values()), None)
    if waterfall is None:
        return []

    return [
        {
            "priority": step.priority,
            "recipient": step.recipient,
            "description": step.description,
            "waterfall_type": waterfall.waterfall_type,
        }
        for step in waterfall.steps
    ]


def _download_and_convert(prospectus_url: str) -> str:
    """Download a prospectus PDF and convert it to markdown via Docling.

    Parameters
    ----------
    prospectus_url:
        HTTP(S) URL of the prospectus PDF.

    Returns
    -------
    str
        Markdown text produced by Docling.

    Raises
    ------
    RuntimeError
        If the download fails or Docling conversion raises.
    """
    try:
        import requests
    except ImportError as exc:
        raise ImportError(
            "requests is required for extract_deal_model; install it with "
            "`pip install requests`"
        ) from exc

    try:
        from docling.document_converter import DocumentConverter
    except ImportError as exc:
        raise ImportError(
            "docling is required for extract_deal_model; install it with "
            "`pip install docling`"
        ) from exc

    try:
        resp = requests.get(prospectus_url, timeout=60)
        resp.raise_for_status()
    except Exception as exc:
        raise RuntimeError(
            f"Failed to download prospectus from {prospectus_url}: {exc}"
        ) from exc

    # Use a TemporaryDirectory so the PDF is cleaned up even on exception.
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = str(Path(tmpdir) / "prospectus.pdf")
        Path(pdf_path).write_bytes(resp.content)

        try:
            converter = DocumentConverter()
            result = converter.convert(pdf_path)
            return result.document.export_to_markdown()
        except Exception as exc:
            raise RuntimeError(
                f"Docling conversion failed for PDF downloaded from {prospectus_url}: {exc}"
            ) from exc
