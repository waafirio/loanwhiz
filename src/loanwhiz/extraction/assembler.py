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
from loanwhiz.extraction.section_router import (
    SectionMap,
    extract_key_sf_sections,
    route_sections,
)
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
        The deal's note-class structure — one dict per tranche
        ``{name, size_eur, rating, rate, seniority}``, ordered senior→junior.
        Parsed from the prospectus tranche table (the first table of the
        prospectus), falling back to the class references in the waterfall
        steps when no table is found.
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
        tranche_structure=_extract_tranches(section_map, waterfalls),
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


def _extract_tranches(
    section_map: "SectionMap | None" = None,
    waterfalls: dict | None = None,
) -> list[dict]:
    """Derive the deal's tranche (note class) structure.

    A tranche is a *note class* — Class A / B / C — each with a principal
    size, credit rating, and coupon.  This is **not** the same thing as a
    Priority-of-Payments step: the waterfall describes the *order* in which
    cash is paid, while the tranche structure describes the *instruments*
    that exist and their relative seniority.

    Source choice (most reliable first)
    -----------------------------------
    1.  **Prospectus tranche table** (preferred).  The first table of an
        RMBS/ABS prospectus lists every note class against rows such as
        ``Principal Amount``, ``Issue Price``, ``Interest Rate`` and
        ``Expected Ratings``.  Docling renders this as a markdown pipe
        table in ``section_map.full_text``.  This is the only source that
        carries sizes, ratings and coupons, so we parse it when available.
    2.  **Waterfall class references** (fallback).  When no tranche table
        can be located, derive the class names + seniority order from the
        ``class_a_*`` / ``class_b_*`` / ``class_c_*`` recipients that appear
        in the extracted waterfall steps.  Sizes / ratings / coupons are
        ``None`` in this degraded mode, but the seniority skeleton is kept.

    The earlier implementation derived tranches from revenue-waterfall steps
    directly; for Green Lion the revenue waterfall extracted zero steps, so
    that approach returned an empty list even though the prospectus tranche
    table (and the redemption / post-enforcement waterfalls) clearly carry
    the Class A/B/C structure — hence this rewrite.

    Parameters
    ----------
    section_map:
        The routed :class:`SectionMap` whose ``full_text`` holds the Docling
        markdown (incl. the tranche table).  Primary source.
    waterfalls:
        ``{waterfall_type: ExtractedWaterfall}`` — fallback source for class
        names + seniority when no tranche table is found.

    Returns
    -------
    list[dict]
        One dict per tranche, ordered senior→junior:
        ``{name, size_eur, rating, rate, seniority}``.
    """
    if section_map is not None:
        tranches = _parse_tranche_table(section_map.full_text)
        if tranches:
            return tranches

    if waterfalls:
        return _tranches_from_waterfalls(waterfalls)

    return []


# Maps a class letter to its 0-based seniority (A is most senior).
def _seniority_for(letter: str) -> int:
    return ord(letter.upper()) - ord("A")


# A EUR amount such as "€1,000,000,000", "EUR 53,100,000" or "10,500,000".
_AMOUNT_RE = re.compile(
    r"(?:€|EUR\s*)?\s*([0-9][0-9.,]*[0-9]|[0-9])",
)

# A note-class label, e.g. "Class A", "Class A1", "Class A Notes".
_CLASS_RE = re.compile(r"Class\s+([A-Z])\d*", re.IGNORECASE)

# A coupon / interest-rate expression, e.g. "3 month EURIBOR + 0.43%" or "0.43%".
_RATE_RE = re.compile(
    r"((?:\d+\s*(?:month|m)\s*)?EURIBOR\s*[+\-]\s*[0-9.]+\s*%?|[0-9.]+\s*%)",
    re.IGNORECASE,
)

# A rating token, e.g. "AAA", "Aaa", "AA+", "BBB-", "NR", "Unrated".
_RATING_RE = re.compile(r"\b(AAA|Aaa|AA[+-]?|A[+-]?|BBB[+-]?|BB[+-]?|B[+-]?|NR|Unrated)\b")


def _parse_euro_amount(text: str) -> float | None:
    """Parse the first EUR amount in *text* into a float, or ``None``."""
    m = _AMOUNT_RE.search(text)
    if not m:
        return None
    raw = m.group(1).replace(".", "").replace(",", "")
    if not raw.isdigit():
        # Fall back to thousands-separator-as-comma only.
        raw = m.group(1).replace(",", "")
        try:
            return float(raw)
        except ValueError:
            return None
    return float(raw)


def _parse_tranche_table(markdown_text: str) -> list[dict]:
    """Parse the prospectus tranche table out of Docling markdown.

    Handles both layouts Docling emits:

    * **Class-as-column** (most common): the header row lists the classes
      (``| | Class A | Class B | Class C |``) and subsequent rows are
      attributes (``| Principal Amount | €1,000,000,000 | ... |``).
    * **Class-as-row**: each row is one class with its attributes spread
      across columns.

    Returns ``[]`` if no recognisable tranche table is present so the caller
    can fall back to the waterfall source.
    """
    # Locate a pipe table that mentions the classes and a principal/amount row.
    tables = _markdown_tables(markdown_text)
    for table in tables:
        flat = "\n".join(" ".join(row) for row in table)
        if not _CLASS_RE.search(flat):
            continue
        if not re.search(r"principal|amount|nominal", flat, re.IGNORECASE):
            continue

        tranches = _tranches_from_class_column_table(table)
        if tranches:
            return tranches
        tranches = _tranches_from_class_row_table(table)
        if tranches:
            return tranches
    return []


def _markdown_tables(markdown_text: str) -> list[list[list[str]]]:
    """Split markdown into pipe tables; each table is a list of cell-rows.

    Separator rows (``|---|---|``) are dropped.
    """
    tables: list[list[list[str]]] = []
    current: list[list[str]] = []
    for line in markdown_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.count("|") >= 2:
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            # Skip the GFM separator row (---, :--:, etc.).
            if all(set(c) <= set("-: ") and c for c in cells):
                continue
            current.append(cells)
        else:
            if current:
                tables.append(current)
                current = []
    if current:
        tables.append(current)
    return tables


def _tranches_from_class_column_table(table: list[list[str]]) -> list[dict]:
    """Parse a table where each *column* is a note class."""
    # Find the header row that names the classes.
    header_idx = None
    class_cols: dict[int, str] = {}
    for idx, row in enumerate(table):
        cols = {i: _CLASS_RE.search(cell).group(1).upper()
                for i, cell in enumerate(row) if _CLASS_RE.search(cell)}
        if len(cols) >= 2:
            header_idx, class_cols = idx, cols
            break
    if header_idx is None or not class_cols:
        return []

    # Walk attribute rows, slotting each cell into its class column.
    attrs: dict[str, dict] = {
        letter: {"name": f"Class {letter}", "size_eur": None,
                 "rating": None, "rate": None, "seniority": _seniority_for(letter)}
        for letter in class_cols.values()
    }
    for row in table[header_idx + 1:]:
        if not row:
            continue
        label = row[0].lower()
        for col, letter in class_cols.items():
            if col >= len(row):
                continue
            cell = row[col]
            if re.search(r"principal|amount|nominal", label):
                if attrs[letter]["size_eur"] is None:
                    attrs[letter]["size_eur"] = _parse_euro_amount(cell)
            elif "rating" in label:
                m = _RATING_RE.search(cell)
                if m and attrs[letter]["rating"] is None:
                    attrs[letter]["rating"] = m.group(1)
            elif re.search(r"interest|rate|coupon|margin", label):
                m = _RATE_RE.search(cell)
                if m and attrs[letter]["rate"] is None:
                    attrs[letter]["rate"] = m.group(1).strip()

    ordered = sorted(attrs.values(), key=lambda t: t["seniority"])
    # Only accept the parse if at least one tranche carries a size.
    if any(t["size_eur"] is not None for t in ordered):
        return ordered
    return []


def _tranches_from_class_row_table(table: list[list[str]]) -> list[dict]:
    """Parse a table where each *row* is a note class."""
    tranches: list[dict] = []
    seen: set[str] = set()
    for row in table:
        joined = " ".join(row)
        m = _CLASS_RE.search(joined)
        if not m:
            continue
        letter = m.group(1).upper()
        if letter in seen:
            continue
        # Exclude the cell holding the "Class X" label so its letter isn't
        # misread as a rating (e.g. "Class A Notes" -> rating "A").
        attr_cells = [c for c in row if not _CLASS_RE.search(c)]
        size = next((a for a in (_parse_euro_amount(c) for c in attr_cells) if a), None)
        rating = next(
            (r.group(1) for c in attr_cells if (r := _RATING_RE.search(c))), None
        )
        rate = next(
            (r.group(1).strip() for c in attr_cells if (r := _RATE_RE.search(c))), None
        )
        if size is None:
            continue
        seen.add(letter)
        tranches.append({
            "name": f"Class {letter}",
            "size_eur": size,
            "rating": rating,
            "rate": rate,
            "seniority": _seniority_for(letter),
        })
    return sorted(tranches, key=lambda t: t["seniority"]) if tranches else []


def _tranches_from_waterfalls(waterfalls: dict) -> list[dict]:
    """Fallback: derive class names + seniority from waterfall step recipients.

    Scans every waterfall's step recipients for ``class_a`` / ``class_b`` /
    ``class_c`` references and emits one tranche per distinct class, ordered
    senior→junior.  Sizes / ratings / coupons are unavailable from this
    source and left as ``None``.
    """
    letters: set[str] = set()
    # Match e.g. "class_a" in "class_a_notes_principal": a single class letter
    # not immediately followed by another letter.
    pattern = re.compile(r"class_([a-z])(?![a-z])")
    for waterfall in waterfalls.values():
        for step in waterfall.steps:
            for m in pattern.finditer(step.recipient.lower()):
                letters.add(m.group(1).upper())
    tranches = [
        {
            "name": f"Class {letter}",
            "size_eur": None,
            "rating": None,
            "rate": None,
            "seniority": _seniority_for(letter),
        }
        for letter in letters
    ]
    return sorted(tranches, key=lambda t: t["seniority"])


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
