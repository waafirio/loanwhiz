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

import hashlib
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
    resolve_sections,
    route_sections,
)
from loanwhiz.extraction.waterfall_extractor import extract_all_waterfalls


# ---------------------------------------------------------------------------
# Durable cache locations
# ---------------------------------------------------------------------------
#
# Extracted artifacts are persisted to the repo's ``data/`` tree rather than
# ``/tmp`` so a pre-warmed deal survives a reboot and ships with the repo — the
# demo must never have to re-extract.  ``_REPO_ROOT`` is resolved from this
# module's location (``src/loanwhiz/extraction/assembler.py`` → four parents up
# is the repo root) so the path is correct regardless of the process cwd.
#
# The directories are committed (each carries a ``.gitkeep``); the generated
# artifacts themselves are gitignored (see ``.gitignore``).

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Where the assembled DealModel JSON is cached (one file per deal).
DEFAULT_DEAL_CACHE_DIR = _REPO_ROOT / "data" / "deals"

# Where the Docling markdown (OCR output) is cached, keyed by a hash of the
# prospectus URL.  Caching this separately means a ``force_refresh`` that only
# wants to re-run the fast Gemini extraction does not have to re-run the
# ~18–37 min Docling OCR for the same prospectus.
DEFAULT_DOCLING_CACHE_DIR = _REPO_ROOT / "data" / "docling_cache"


# ---------------------------------------------------------------------------
# Definitions linking (#395)
# ---------------------------------------------------------------------------
#
# The definitions graph was previously passed to the sub-extractors only as
# throwaway LLM prompt context — the resolved terms were discarded and never
# attached to the structured model, so a waterfall step's ``condition`` and a
# trigger's ``metric`` stayed bare strings disconnected from their definitions.
# The interpreter's condition evaluator then fell through to its "unknown
# condition → pay the step" default whenever the prose didn't lexically match,
# so conditional waterfall prose never resolved against the trigger it named.
#
# These helpers close that gap: at assembly time they link each step condition
# and each trigger metric/display-name to the defined terms it references and
# attach the canonical term names onto the serialised structures, so the model
# carries the resolution and the interpreter can gate on the linked term.


def _link_terms_in(text: str | None, term_keys: list[str]) -> list[str]:
    """Return the canonical defined-term names referenced in ``text``.

    Operates on the graph's term *keys* (the canonical names) directly — the
    same case-insensitive, first-appearance-ordered, de-duplicated scan as
    :meth:`DefinitionsGraph.link`, but driven by the names the assembler already
    holds so it never depends on a ``DefinedTerm`` object's shape. Blank text or
    no match yields ``[]``.
    """
    if not text or not str(text).strip():
        return []
    lower_text = str(text).lower()
    hits: list[tuple[int, str]] = []
    for key in term_keys:
        if not key:
            continue
        idx = lower_text.find(key.lower())
        if idx >= 0:
            hits.append((idx, key))
    hits.sort(key=lambda h: (h[0], h[1]))
    seen: set[str] = set()
    ordered: list[str] = []
    for _idx, key in hits:
        if key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def _apply_definitions_links(
    *,
    waterfalls: dict,
    covenants: dict,
    term_keys: list[str],
) -> None:
    """Attach defined-term links onto serialised waterfall steps + triggers.

    Mutates the serialised ``waterfalls`` and ``covenants`` dicts in place:

    - Each waterfall step gains a ``condition_terms`` list — the defined terms
      its ``condition`` references (empty for an unconditional step). The
      interpreter's :meth:`StepSpec.from_extracted` reads this so a linked
      conditional step gates on its resolved trigger instead of the
      "unknown → pay" default.
    - Each trigger gains a ``metric_terms`` list — the defined terms its
      ``metric`` / ``display_name`` references — so the trigger's measurable
      quantity is linked to its definition in the structured model.

    No-op (every link an empty list) when no terms are defined, so a deal with
    an empty definitions graph serialises unchanged apart from the new keys.
    """
    for wf in (waterfalls or {}).values():
        if not isinstance(wf, dict):
            continue
        for step in wf.get("steps", []) or []:
            if isinstance(step, dict):
                step["condition_terms"] = _link_terms_in(
                    step.get("condition"), term_keys
                )

    for trig in (covenants or {}).get("triggers", []) or []:
        if isinstance(trig, dict):
            linked = _link_terms_in(trig.get("metric"), term_keys)
            # Fall back to the human-readable display name (the metric is often
            # a snake_case slug like ``pdl_debit_balance`` that won't match a
            # capitalised defined term, but the display name "PDL Debit Balance"
            # will).
            if not linked:
                linked = _link_terms_in(trig.get("display_name"), term_keys)
            trig["metric_terms"] = linked


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
    completeness_score: float   # 0–1: real coverage of extracted content (see _completeness_score)
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

# Waterfall types we expect a complete deal model to carry actual steps for. The
# completeness score credits a waterfall only when it extracted at least one
# step — a section header with zero steps is not "complete".
_EXPECTED_WATERFALL_TYPES: tuple[str, ...] = ("revenue", "redemption")

# Relative weights of the four coverage dimensions the completeness score blends.
# Sections + waterfalls dominate (they are the load-bearing extracted content);
# triggers + tranches are presence signals. Weights sum to 1.0.
_COMPLETENESS_WEIGHTS: dict[str, float] = {
    "sections": 0.30,
    "waterfalls": 0.40,
    "triggers": 0.15,
    "tranches": 0.15,
}


def _completeness_score(
    *,
    sections_found: list[str],
    waterfalls: dict,
    covenants,
    tranche_structure: list[dict],
) -> float:
    """Real extraction-coverage score in ``[0, 1]`` over the assembled content.

    This replaces the old metric (fraction of the four expected section
    *headers* present), which could read ``1.0`` even when every waterfall
    section was found but yielded **zero** extracted steps — a model that is
    structurally empty yet scored "complete". The new score blends four coverage
    dimensions (weights in :data:`_COMPLETENESS_WEIGHTS`):

    - **sections** — fraction of :data:`_EXPECTED_SECTIONS` found (the old
      signal, retained but down-weighted).
    - **waterfalls** — fraction of :data:`_EXPECTED_WATERFALL_TYPES` that
      actually extracted **≥1 step** (the dimension the old metric missed: this
      is what makes a header-only-but-empty model score < 1.0).
    - **triggers** — 1.0 when the covenant extraction produced ≥1 trigger, else
      0.0 (a deal model with no triggers cannot drive the conditional waterfall).
    - **tranches** — 1.0 when a non-empty tranche structure was derived, else
      0.0.

    Parameters
    ----------
    sections_found:
        The section keys the router located (``sections_found`` in the caller).
    waterfalls:
        ``{waterfall_type: ExtractedWaterfall}`` — the extractor output (each
        value exposes ``.steps``).
    covenants:
        The ``ExtractedCovenants`` object (exposes ``.triggers``).
    tranche_structure:
        The derived tranche list (one dict per note class).

    Returns
    -------
    float
        Weighted coverage in ``[0, 1]``.
    """
    # Sections coverage.
    sections = len([s for s in _EXPECTED_SECTIONS if s in sections_found]) / len(
        _EXPECTED_SECTIONS
    )

    # Waterfalls coverage — credit only waterfalls that extracted ≥1 step.
    def _step_count(wf) -> int:
        steps = getattr(wf, "steps", None)
        if steps is None and isinstance(wf, dict):
            steps = wf.get("steps")
        return len(steps) if steps else 0

    populated = sum(
        1
        for wt in _EXPECTED_WATERFALL_TYPES
        if wt in waterfalls and _step_count(waterfalls[wt]) > 0
    )
    waterfalls_cov = populated / len(_EXPECTED_WATERFALL_TYPES)

    # Trigger presence.
    triggers = getattr(covenants, "triggers", None) or []
    triggers_cov = 1.0 if len(triggers) > 0 else 0.0

    # Tranche presence.
    tranches_cov = 1.0 if tranche_structure else 0.0

    return (
        _COMPLETENESS_WEIGHTS["sections"] * sections
        + _COMPLETENESS_WEIGHTS["waterfalls"] * waterfalls_cov
        + _COMPLETENESS_WEIGHTS["triggers"] * triggers_cov
        + _COMPLETENESS_WEIGHTS["tranches"] * tranches_cov
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_deal_model(
    prospectus_url: str,
    deal_name: str,
    cache_dir: str = str(DEFAULT_DEAL_CACHE_DIR),
    force_refresh: bool = False,
    docling_cache_dir: str = str(DEFAULT_DOCLING_CACHE_DIR),
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
        Directory in which to store the cached deal model JSON.  Defaults to
        the repo's durable ``data/deals/`` so the artifact survives reboots and
        ships with the repo.  Created automatically if it does not exist.
    force_refresh:
        When ``True``, bypass the deal-model cache and re-run extraction.  This
        propagates to the sub-extractors (definitions / waterfalls / covenants)
        so their own on-disk caches are busted too, and busts the Docling
        markdown cache so the OCR is re-run.  (Pass ``force_refresh=True`` after
        a sub-extractor fix to ensure a re-warm does not serve a stale result.)
    docling_cache_dir:
        Directory in which to cache the Docling markdown (OCR output), keyed by
        a hash of ``prospectus_url``.  Defaults to the repo's durable
        ``data/docling_cache/``.  Reusing this cache lets a ``force_refresh``
        re-run only the fast Gemini extraction without re-running the
        ~18–37 min Docling OCR.

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

    # 1. Download and convert the PDF to markdown via Docling.  The Docling
    #    markdown is cached separately (keyed by prospectus URL) so a
    #    force_refresh re-runs only the fast Gemini extraction, not the
    #    expensive OCR.  force_refresh busts that markdown cache too.
    markdown_text = _download_and_convert(
        prospectus_url,
        cache_dir=docling_cache_dir,
        force_refresh=force_refresh,
    )

    # 2. Route sections — deterministic English-keyword fast path with the
    #    language-agnostic LLM router (#273's classify_segments_llm) as a
    #    fallback for any load-bearing role the keyword router could not locate
    #    (#274 wiring). For an English Green Lion prospectus the keyword router
    #    finds everything, the LLM is never invoked, and the resolved sections
    #    are exactly the keyword hits — so the English path is byte-identical.
    #    For a non-English (IT/ES) prospectus the LLM fills the gaps by meaning,
    #    so the waterfall / definitions / covenant extractors below receive real
    #    sections to extract from instead of raising on the missing English
    #    headings.
    section_map = route_sections(markdown_text)
    key_sections = resolve_sections(section_map)
    sections_found = [k for k, v in key_sections.items() if v is not None]

    # The pre-resolved sections the downstream extractors should use instead of
    # re-running their own English keyword lookups.
    wf_sections = {
        wf: sec
        for wf, role in (
            ("revenue", "revenue_priority_of_payments"),
            ("redemption", "redemption_priority_of_payments"),
            ("post_enforcement", "post_enforcement_priority"),
        )
        if (sec := key_sections.get(role)) is not None
    }
    covenant_sections = [
        (role, sec)
        for role in (
            "revenue_priority_of_payments",
            "redemption_priority_of_payments",
            "post_enforcement_priority",
        )
        if (sec := key_sections.get(role)) is not None
    ]

    # 3. Extract definitions.  force_refresh propagates so any sub-extractor
    #    disk cache is busted (definitions has none of its own, but the param
    #    keeps the propagation uniform).  The resolved definitions section
    #    (keyword or LLM-located) is passed so a non-English deal does not raise.
    definitions_graph = extract_definitions(
        section_map,
        force_refresh=force_refresh,
        section=key_sections.get("definitions"),
    )

    # 4. Extract waterfalls.  force_refresh busts the per-waterfall disk cache
    #    (the #132 fix: a re-warm after the #125 revenue-waterfall fix must not
    #    serve the stale waterfall_{deal}_{type}.json).  The pre-resolved
    #    sections feed the language-agnostic path (#274).
    waterfalls = extract_all_waterfalls(
        section_map,
        definitions_graph,
        deal_name=deal_name,
        force_refresh=force_refresh,
        sections=wf_sections,
    )

    # 5. Extract covenants / triggers.  force_refresh busts the covenants disk
    #    cache too.  The pre-resolved PoP/triggers spans are supplied so a
    #    non-English deal extracts triggers from the right text.
    covenants = extract_covenants(
        section_map,
        definitions_graph,
        force_refresh=force_refresh,
        extra_sections=covenant_sections,
    )

    # 6. Derive the tranche structure (needed both for the model and as a real
    #    coverage signal for the completeness score).
    tranche_structure = _extract_tranches(section_map, waterfalls)

    # 7. Compute completeness — a *real* coverage metric over extracted content,
    #    not merely the fraction of expected section headers present (the old
    #    metric read 1.0 even when a section header was found but yielded zero
    #    extracted steps). See ``_completeness_score``.
    completeness = _completeness_score(
        sections_found=sections_found,
        waterfalls=waterfalls,
        covenants=covenants,
        tranche_structure=tranche_structure,
    )

    # 8. Assemble.
    #    Serialise the waterfalls + covenants, then LINK the definitions graph
    #    into the structured model (#395): attach ``condition_terms`` onto each
    #    step's condition and ``metric_terms`` onto each trigger's metric so the
    #    interpreter can resolve conditional waterfall prose against its defined
    #    term instead of falling through to the "unknown → pay" default.
    serialised_waterfalls = {k: v.model_dump() for k, v in waterfalls.items()}
    serialised_covenants = covenants.model_dump()
    term_keys = list(definitions_graph.terms.keys())
    _apply_definitions_links(
        waterfalls=serialised_waterfalls,
        covenants=serialised_covenants,
        term_keys=term_keys,
    )
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
        waterfalls=serialised_waterfalls,
        covenants=serialised_covenants,
        tranche_structure=tranche_structure,
        trigger_names=[t.name for t in covenants.triggers],
    )

    # 9. Cache to disk.
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


def _docling_cache_path(prospectus_url: str, cache_dir: str) -> Path:
    """Return the Docling-markdown cache path for a prospectus URL.

    Keyed by a SHA-256 hash of the URL so different prospectuses never collide
    and the same prospectus always maps to the same file (durable reuse).
    """
    digest = hashlib.sha256(prospectus_url.encode("utf-8")).hexdigest()
    return Path(cache_dir) / f"{digest}.md"


def _download_and_convert(
    prospectus_url: str,
    cache_dir: str = str(DEFAULT_DOCLING_CACHE_DIR),
    force_refresh: bool = False,
) -> str:
    """Download a prospectus PDF and convert it to markdown via Docling.

    The Docling markdown is cached on disk, keyed by a hash of
    ``prospectus_url``.  On a cache hit (and ``force_refresh`` is ``False``) the
    cached markdown is returned immediately — **no download, no Docling OCR**.
    This is what lets a ``force_refresh`` of the deal model re-run only the fast
    Gemini extraction instead of the ~18–37 min OCR.

    Parameters
    ----------
    prospectus_url:
        HTTP(S) URL of the prospectus PDF.
    cache_dir:
        Directory in which to cache the converted markdown.
    force_refresh:
        When ``True``, ignore any cached markdown and re-download + re-convert.

    Returns
    -------
    str
        Markdown text produced by Docling (or loaded from cache).

    Raises
    ------
    RuntimeError
        If the download fails or Docling conversion raises.
    """
    cache_path = _docling_cache_path(prospectus_url, cache_dir)
    if cache_path.exists() and not force_refresh:
        return cache_path.read_text(encoding="utf-8")

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
            markdown = result.document.export_to_markdown()
        except Exception as exc:
            raise RuntimeError(
                f"Docling conversion failed for PDF downloaded from {prospectus_url}: {exc}"
            ) from exc

    # Persist the OCR output so future (force_)refreshes reuse it.
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(markdown, encoding="utf-8")
    return markdown


# ===========================================================================
# Canonical DealRules assembly (#273 — the generalization).
# ===========================================================================
#
# The functions above produce the legacy ``DealModel`` (free-string recipients /
# metrics), still consumed by the API and the report/reconciler primitives. The
# functions below map an extracted ``DealModel`` (or its parts) onto the
# canonical ``loanwhiz.domain.rules.DealRules`` — the executable program the
# engine folds directly — via the recipient/metric taxonomy mapping, LLM-assisted
# tranche extraction, per-field provenance, and honest field-based completeness
# (spec ``2026-06-20-prospectus-extractor-generalization-design.md``).
#
# Domain + governance imports are kept *inside* the functions (lazy) so importing
# this module's legacy path (e.g. via ``capability_matrix``) does not pull the
# canonical ``loanwhiz.domain`` package at module-load time — that would close the
# documented domain<->primitives import cycle (see
# ``loanwhiz.primitives.__init__.__getattr__``).


def _rate_rule_from_str(rate_str: str | None):
    """Parse a coupon string into a canonical ``RateRule`` (fixed or floating).

    Recognises floating forms like ``"3m EURIBOR + 0.43"`` / ``"EURIBOR + 0.43%"``
    and fixed forms like ``"3.5%"``. Defaults to a 0% fixed coupon when the string
    is absent or unparseable — a usable-but-flagged-by-provenance fallback.
    """
    from loanwhiz.domain.rules import RateRule

    if not rate_str or not rate_str.strip():
        return RateRule(kind="fixed", fixed_pct=0.0)

    text = rate_str.strip()
    lower = text.lower()

    # Floating: an index (EURIBOR/LIBOR/SOFR/...) + a margin.
    index_match = re.search(r"(euribor|libor|sofr|estr|sonia)", lower)
    if index_match:
        index_token = index_match.group(1).upper()
        tenor = re.search(r"(\d+)\s*(?:m|month)", lower)
        index = f"{index_token}_{tenor.group(1)}M" if tenor else index_token
        margin = re.search(r"[+\-]\s*([0-9]*\.?[0-9]+)\s*(%|bps)?", text)
        margin_bps = None
        if margin:
            val = float(margin.group(1))
            unit = (margin.group(2) or "").lower()
            # "+ 0.43" / "+ 0.43%" are percent → bps; "+ 43 bps" already bps.
            margin_bps = val if unit == "bps" else val * 100.0
        return RateRule(kind="floating", index=index, margin_bps=margin_bps)

    # Fixed: a bare percentage.
    pct = re.search(r"([0-9]*\.?[0-9]+)\s*%", text)
    if pct:
        return RateRule(kind="fixed", fixed_pct=float(pct.group(1)) / 100.0)

    return RateRule(kind="fixed", fixed_pct=0.0)


def tranche_rules_from_structure(tranche_structure: list[dict]) -> list:
    """Map the legacy ``tranche_structure`` dicts onto canonical ``TrancheRule``.

    Each dict (``{name, size_eur, rating, rate, seniority}``) becomes a
    ``TrancheRule`` with a parsed ``RateRule``. A tranche whose ``size_eur`` is
    missing is given ``original_balance=0.0`` (the field is required by the
    schema; provenance flags the low confidence) so the seniority skeleton is
    still carried.
    """
    from loanwhiz.domain.rules import TrancheRule

    rules: list[TrancheRule] = []
    for t in tranche_structure:
        rules.append(
            TrancheRule(
                name=t.get("name", "Unknown"),
                seniority=int(t.get("seniority", len(rules))),
                original_balance=float(t.get("size_eur") or 0.0),
                rate=_rate_rule_from_str(t.get("rate")),
                rating=t.get("rating"),
            )
        )
    return sorted(rules, key=lambda r: r.seniority)


def _citation_from_step(raw_citation: dict, default_doc: str):
    """Build a ``primitives.base.Citation`` from an extracted step/trigger citation."""
    from loanwhiz.primitives.base import Citation

    c = raw_citation if isinstance(raw_citation, dict) else {}
    return Citation(
        document=c.get("document") or default_doc,
        page_or_row=c.get("page_or_row"),
        excerpt=c.get("excerpt") or "",
    )


def _step_rules_from_waterfall(
    waterfall: dict,
    *,
    deal_name: str,
    provenance: dict,
    wf_kind: str,
    use_llm: bool,
) -> list:
    """Map one extracted waterfall's steps onto canonical ``StepRule`` objects.

    Each step's free-string recipient is classified onto ``RecipientType`` via the
    taxonomy mapper (``unmapped`` escape); the amount basis is bound from the
    mapped recipient; the prose is retained as ``AmountRule.raw_text``. A free-text
    ``condition`` becomes a ``ConditionRef`` (gate ``not_breached`` for the common
    "if X is not in effect" form, else ``breached``). Per-step provenance is
    written into ``provenance`` keyed ``waterfalls.<kind>.<order>.recipient``.
    """
    from loanwhiz.domain.provenance import FieldProvenance
    from loanwhiz.domain.rules import ConditionRef, StepRule
    from loanwhiz.extraction.taxonomy import build_amount_rule, map_recipient

    steps_in = waterfall.get("steps", []) if isinstance(waterfall, dict) else []
    rules: list[StepRule] = []
    for order, raw in enumerate(steps_in):
        recipient_str = raw.get("recipient", "")
        description = raw.get("description", "")
        mapping = map_recipient(recipient_str, description, use_llm=use_llm)
        amount = build_amount_rule(
            mapping.value, raw.get("amount_formula") or description
        )

        condition = None
        cond_text = (raw.get("condition") or "").strip()
        if cond_text:
            when = "not_breached" if re.search(r"not\b", cond_text, re.I) else "breached"
            condition = ConditionRef(trigger_name=cond_text, when=when)

        group = None
        if raw.get("is_pari_passu"):
            group = f"pp:{raw.get('priority', order)}"

        rule = StepRule(
            order=order,
            priority_label=str(raw.get("priority", f"({order})")),
            recipient=mapping.value,
            amount=amount,
            condition=condition,
            pari_passu_group=group,
        )
        rules.append(rule)

        key = f"waterfalls.{wf_kind}.{order}.recipient"
        provenance[key] = FieldProvenance(
            source="prospectus",
            method=mapping.method,  # type: ignore[arg-type]
            confidence=mapping.confidence,
            citation=_citation_from_step(raw.get("citation", {}), f"{deal_name} Prospectus"),
        )
    return rules


def _trigger_rules_from_covenants(
    covenants: dict,
    *,
    deal_name: str,
    provenance: dict,
    use_llm: bool,
) -> list:
    """Map extracted triggers onto canonical ``TriggerRule`` objects.

    Each free-string metric is classified onto ``MetricType`` (``unmapped``
    escape); ``threshold_unit`` is normalised once here; the extracted
    ``direction`` (above/below/non_zero) becomes the canonical comparison
    operator. Per-trigger provenance is keyed ``triggers.<name>.metric``.
    """
    from loanwhiz.domain.provenance import FieldProvenance
    from loanwhiz.domain.rules import TriggerRule
    from loanwhiz.extraction.taxonomy import map_metric, normalize_threshold_unit

    _OP = {"above": ">", "below": "<", "non_zero": ">"}

    triggers_in = covenants.get("triggers", []) if isinstance(covenants, dict) else []
    rules: list[TriggerRule] = []
    for raw in triggers_in:
        metric_str = raw.get("metric", "")
        mapping = map_metric(metric_str, raw.get("description", ""), use_llm=use_llm)
        threshold = raw.get("threshold")
        # A non_zero trigger fires on any positive balance → threshold 0.
        if threshold is None and raw.get("direction") == "non_zero":
            threshold = 0.0
        unit = normalize_threshold_unit(raw.get("threshold_unit"))

        rule = TriggerRule(
            name=raw.get("name", "unknown_trigger"),
            metric=mapping.value,
            operator=_OP.get(raw.get("direction", "above"), ">"),  # type: ignore[arg-type]
            threshold=float(threshold) if threshold is not None else None,
            threshold_unit=unit,  # type: ignore[arg-type]
            consequence=raw.get("consequence", ""),
        )
        rules.append(rule)

        key = f"triggers.{rule.name}.metric"
        provenance[key] = FieldProvenance(
            source="prospectus",
            method=mapping.method,  # type: ignore[arg-type]
            confidence=mapping.confidence,
            citation=_citation_from_step(raw.get("citation", {}), f"{deal_name} Prospectus"),
        )
    return rules


def _reserve_rule_from(covenants: dict, definitions: dict):
    """Derive a canonical ``ReserveRule`` from the extracted artifacts.

    Best-effort: looks for a reserve-fund trigger/definition mentioning a target
    percentage of note balance; falls back to a flat ``floor=0.0`` reserve when
    nothing quantified is found (provenance reflects the low confidence). The
    engine treats ``pct_of_note_balance`` / ``floor`` per ``ReserveRule``.
    """
    from loanwhiz.domain.rules import ReserveRule

    # Scan trigger consequences / definitions for a "X% of ... note balance" form.
    haystacks: list[str] = []
    for t in (covenants.get("triggers", []) if isinstance(covenants, dict) else []):
        if "reserve" in (t.get("name", "") + t.get("metric", "")).lower():
            haystacks.append(t.get("consequence", "") + " " + t.get("description", ""))
    for term, body in (definitions or {}).items():
        if "reserve" in term.lower():
            d = body.get("definition", "") if isinstance(body, dict) else str(body)
            haystacks.append(d)

    for text in haystacks:
        pct = re.search(r"([0-9]*\.?[0-9]+)\s*%[^.]*?(?:note|principal|balance)", text, re.I)
        if pct:
            return ReserveRule(floor=0.0, pct_of_note_balance=float(pct.group(1)) / 100.0)

    return ReserveRule(floor=0.0, pct_of_note_balance=None)


def build_deal_rules(
    model: "DealModel",
    *,
    deal_id: str | None = None,
    jurisdiction: str = "Unknown",
    currency: str = "EUR",
    use_llm: bool = True,
):
    """Assemble a canonical :class:`~loanwhiz.domain.rules.DealRules` from a ``DealModel``.

    This is the load-bearing generalization (#273): it maps the extracted
    artifacts onto the executable canonical program via the recipient/metric
    taxonomy (with the honest ``unmapped`` escape), parsed tranche rules,
    per-field provenance, and the field-based completeness score. The result is
    returned **wrapped in a governed** :class:`~loanwhiz.primitives.base.PrimitiveResult`
    (confidence + citations + audit) so the governance layer can route low-
    confidence fields to human review.

    Parameters
    ----------
    model:
        The extracted :class:`DealModel` (legacy artifact).
    deal_id:
        Stable deal id; defaults to the slug of the deal name.
    jurisdiction / currency:
        Deal metadata for the canonical record.
    use_llm:
        When ``False`` (tests / offline), the taxonomy mapper takes the
        deterministic-only path — unrecognised strings degrade to ``unmapped``
        rather than calling Gemini.

    Returns
    -------
    PrimitiveResult[DealRules]
        The canonical rules plus governance envelope. Access the rules via
        ``result.output``.
    """
    import time as _time

    from loanwhiz.domain.rules import DealRules
    from loanwhiz.primitives.base import AuditEntry, Citation, PrimitiveResult

    t0 = _time.time()
    deal_name = model.metadata.deal_name
    provenance: dict = {}

    # Tranches → TrancheRule.
    tranches = tranche_rules_from_structure(model.tranche_structure)

    # Waterfalls → StepRule lists (keyed by canonical WaterfallKind).
    _WF_MAP = {
        "revenue": "revenue",
        "redemption": "redemption",
        "post_enforcement": "post_enforcement",
    }
    waterfalls: dict = {}
    for src_kind, canon_kind in _WF_MAP.items():
        wf = model.waterfalls.get(src_kind)
        if wf is not None:
            waterfalls[canon_kind] = _step_rules_from_waterfall(
                wf,
                deal_name=deal_name,
                provenance=provenance,
                wf_kind=canon_kind,
                use_llm=use_llm,
            )
    # The schema requires the three keys present (lists may be empty).
    for canon_kind in ("revenue", "redemption", "post_enforcement"):
        waterfalls.setdefault(canon_kind, [])

    # Triggers → TriggerRule.
    triggers = _trigger_rules_from_covenants(
        model.covenants, deal_name=deal_name, provenance=provenance, use_llm=use_llm
    )

    # Reserve → ReserveRule.
    reserve = _reserve_rule_from(model.covenants, model.definitions)

    rules = DealRules(
        deal_id=deal_id or _slug(deal_name),
        deal_name=deal_name,
        jurisdiction=jurisdiction,
        currency=currency,
        tranches=tranches,
        waterfalls=waterfalls,
        triggers=triggers,
        reserve=reserve,
        provenance=provenance,
    )
    # Honest field-based completeness (unmapped steps don't count).
    rules.completeness = rules.compute_completeness()

    # Governed envelope: confidence is the mean per-field provenance confidence
    # (a weak proxy for overall extraction quality); citations are the distinct
    # per-field citations.
    confidences = [fp.confidence for fp in provenance.values()]
    overall_conf = sum(confidences) / len(confidences) if confidences else 0.0
    citations: list[Citation] = [fp.citation for fp in provenance.values() if fp.citation]

    audit = AuditEntry.now(
        primitive_name="prospectus_extractor.build_deal_rules",
        version="1.0.0",
        input_hash=hashlib.sha256(
            model.model_dump_json().encode("utf-8")
        ).hexdigest(),
        duration_ms=(_time.time() - t0) * 1000.0,
    )

    return PrimitiveResult(
        output=rules,
        confidence=overall_conf,
        citations=citations,
        audit_entry=audit,
    )
