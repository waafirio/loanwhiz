"""Report verifier primitive — compare investor report figures against waterfall computation.

Extracts key payment figures from a monthly investor report PDF using Gemini 2.5 Flash,
then compares them against the corresponding values in a WaterfallOutput to detect
servicer discrepancies.

This is a key demo primitive: because the Green Lion 2026-1 loan tapes are synthetic,
the servicer's reported figures may differ from the waterfall computation. The verifier
surfaces those discrepancies, answering the question: "Did the servicer apply the
waterfall correctly?"

Five key figures are extracted and compared:
- Class A interest paid
- Class A principal paid
- Reserve fund balance
- Pool balance
- Total collections

Confidence scoring:
- 0.9 if ≥3 figures extracted successfully from the investor report.
- 0.6 if <3 figures extracted (partial/degraded extraction).

Caching:
Gemini extraction results are cached to ``/tmp/loanwhiz_cache/report_{period}.json``
(where ``{period}`` is a filesystem-safe slug of the reporting period) to avoid
repeated API calls during a demo session.
"""

from __future__ import annotations

import json
import logging
import pathlib
import re
import time
from typing import Any

from google import genai
from pydantic import BaseModel, Field

from loanwhiz.config import MODEL_FLASH
from loanwhiz.primitives.base import (
    AuditEntry,
    BaseInput,
    Citation,
    Primitive,
    PrimitiveResult,
)
from loanwhiz.primitives.registry import register_primitive

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CACHE_DIR = pathlib.Path("/tmp/loanwhiz_cache")

# The five figures we ask Gemini to extract. Keys are canonical line-item names;
# values are natural-language descriptions used in the extraction prompt.
_FIGURE_DESCRIPTIONS: dict[str, str] = {
    "class_a_interest_paid": "Class A interest paid (EUR)",
    "class_a_principal_paid": "Class A principal paid / redeemed (EUR)",
    "reserve_fund_balance": "Reserve fund / account balance at end of period (EUR)",
    "pool_balance": "Total pool / portfolio outstanding balance at end of period (EUR)",
    "total_collections": "Total collections received during the period (EUR)",
}

# Confidence thresholds
_CONFIDENCE_HIGH = 0.9   # ≥3 figures extracted
_CONFIDENCE_LOW = 0.6    # <3 figures extracted
_CONFIDENCE_THRESHOLD = 3  # minimum extracted figures for HIGH confidence


# ---------------------------------------------------------------------------
# Output sub-models
# ---------------------------------------------------------------------------


class ReportedFigure(BaseModel):
    """Comparison of one line item between investor report and waterfall computation.

    Attributes:
        line_item:       Canonical name, e.g. ``"class_a_interest_paid"``.
        reported_value:  Value extracted from the investor report (EUR).
        computed_value:  Value from the waterfall runner output (EUR).
        delta:           ``reported_value - computed_value``.
        delta_pct:       ``delta / computed_value * 100`` when ``computed_value != 0``.
                         When both values are zero the percentage is 0.0.
                         When ``computed_value`` is 0 but ``reported_value`` is not,
                         the discrepancy is unbounded — stored as 999.0 (a sentinel
                         indicating "computed was zero, reported non-zero; treat as
                         a mismatch regardless of tolerance").
        match:           ``True`` if ``abs(delta_pct) < tolerance_pct``.
        tolerance_pct:   Tolerance used for the match decision (default 1.0%).
    """

    line_item: str
    reported_value: float
    computed_value: float
    delta: float
    delta_pct: float
    match: bool
    tolerance_pct: float = 1.0


# ---------------------------------------------------------------------------
# Input / Output models
# ---------------------------------------------------------------------------


class ReportVerifierInput(BaseInput):
    """Input schema for the report verifier primitive.

    Attributes:
        investor_report_url: Direct URL to the investor report PDF.
        waterfall_output:    ``WaterfallOutput.model_dump()`` dict for the
                             same reporting period.
        reporting_period:    Human-readable period label, e.g. ``"April 2026"``.
        tolerance_pct:       Match tolerance in percent (default 1.0).
    """

    investor_report_url: str = Field(..., description="PDF URL for the investor report.")
    waterfall_output: dict[str, Any] = Field(
        ...,
        description="WaterfallOutput.model_dump() for the same reporting period.",
    )
    reporting_period: str = Field(..., description='Reporting period, e.g. "April 2026".')
    tolerance_pct: float = Field(
        default=1.0,
        ge=0.0,
        description="Match tolerance in percent. Figures within this tolerance are 'match'.",
    )


class ReportVerifierOutput(BaseModel):
    """Output of the report verifier for one reporting period.

    Attributes:
        reporting_period:    The period this output covers.
        figures_checked:     Total number of line items attempted.
        figures_matched:     Number of line items within tolerance.
        figures_mismatched:  Number of line items outside tolerance.
        line_items:          Per-figure comparison details.
        overall_match:       ``True`` if all figures are within tolerance.
        summary:             Human-readable summary, e.g.
                             ``"3/4 figures match within 1% tolerance; 1 mismatch: class_a_interest_paid"``.
    """

    reporting_period: str
    figures_checked: int
    figures_matched: int
    figures_mismatched: int
    line_items: list[ReportedFigure]
    overall_match: bool
    summary: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _period_slug(period: str) -> str:
    """Convert a reporting period string to a filesystem-safe slug.

    Examples
    --------
    >>> _period_slug("April 2026")
    'april_2026'
    """
    return re.sub(r"[^a-z0-9]+", "_", period.lower()).strip("_")


def _cache_path(period: str) -> pathlib.Path:
    return _CACHE_DIR / f"report_{_period_slug(period)}.json"


def _load_cache(period: str) -> dict[str, float] | None:
    """Return cached extraction dict or None if no cache exists."""
    path = _cache_path(period)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}
        except (json.JSONDecodeError, ValueError, OSError) as exc:
            logger.warning("Cache read failed for %s: %s", period, exc)
    return None


def _write_cache(period: str, figures: dict[str, float]) -> None:
    """Write extracted figures dict to the cache file."""
    path = _cache_path(period)
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(figures, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Cache write failed for %s: %s", period, exc)


def _extract_figures_with_gemini(
    pdf_url: str,
    reporting_period: str,
) -> dict[str, float]:
    """Call Gemini 2.5 Flash to extract key figures from the investor report PDF.

    Returns a dict mapping canonical figure names to extracted float values.
    Missing figures are omitted from the dict (caller handles partial results).

    Parameters
    ----------
    pdf_url:
        Direct URL to the investor report PDF.
    reporting_period:
        Human-readable period label used in the prompt for context.

    Returns
    -------
    dict[str, float]
        Keys are canonical figure names from ``_FIGURE_DESCRIPTIONS``; values
        are extracted EUR amounts. Only successfully extracted figures are
        included.
    """
    figure_list = "\n".join(
        f'  "{k}": {desc}' for k, desc in _FIGURE_DESCRIPTIONS.items()
    )
    prompt = f"""You are a structured finance analyst. Read the investor report PDF for the {reporting_period} payment period and extract ONLY the following numeric figures. Return a JSON object with exactly these keys; set any figure you cannot find to null.

Figures to extract (all in EUR):
{figure_list}

Rules:
- Return ONLY valid JSON, no explanation, no markdown fences.
- Use numeric values only (no currency symbols, no commas in numbers).
- If a figure appears multiple times, use the end-of-period / post-distribution value.
- If a figure is truly absent from the document, set it to null.

Example output format:
{{
  "class_a_interest_paid": 9050000.0,
  "class_a_principal_paid": 5000000.0,
  "reserve_fund_balance": 5000000.0,
  "pool_balance": 1063600000.0,
  "total_collections": 14050000.0
}}"""

    client = genai.Client()

    # Fetch the PDF bytes and pass inline via httpx, or use the URL directly.
    # google-genai supports passing a URL as a Part for models that accept URLs.
    response = client.models.generate_content(
        model=MODEL_FLASH,
        contents=[
            {
                "role": "user",
                "parts": [
                    {"file_data": {"mime_type": "application/pdf", "file_uri": pdf_url}},
                    {"text": prompt},
                ],
            }
        ],
    )

    raw_text = response.text.strip()

    # Strip markdown fences if Gemini wraps the JSON.
    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.IGNORECASE)
    raw_text = re.sub(r"\s*```$", "", raw_text)

    try:
        parsed: dict[str, Any] = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.error("Gemini returned non-JSON for %s: %s", reporting_period, exc)
        return {}

    figures: dict[str, float] = {}
    for key in _FIGURE_DESCRIPTIONS:
        val = parsed.get(key)
        if val is not None:
            try:
                figures[key] = float(val)
            except (TypeError, ValueError):
                logger.warning("Non-numeric value for %s: %r", key, val)

    return figures


def _extract_computed_values(waterfall_output: dict[str, Any]) -> dict[str, float]:
    """Extract the five key figures from a WaterfallOutput dict.

    WaterfallOutput carries per-tranche distributions and waterfall step details.
    We map the canonical figure names to the closest available computed fields.

    Figures that cannot be derived from WaterfallOutput alone (e.g. pool_balance,
    reserve_fund_balance) fall back to a caller-supplied extra key if present in
    the dict, otherwise to 0.0 with a warning.

    Parameters
    ----------
    waterfall_output:
        ``WaterfallOutput.model_dump()`` dict.

    Returns
    -------
    dict[str, float]
        Canonical figure name → computed value.
    """
    computed: dict[str, float] = {}

    # Locate the Class A tranche distribution.
    tranche_distributions: list[dict[str, Any]] = waterfall_output.get(
        "tranche_distributions", []
    )
    class_a: dict[str, Any] = next(
        (t for t in tranche_distributions if t.get("tranche") == "class_a"), {}
    )

    computed["class_a_interest_paid"] = float(class_a.get("interest_received", 0.0))
    computed["class_a_principal_paid"] = float(class_a.get("principal_received", 0.0))

    # total_collections — WaterfallOutput.total_distributed is the closest proxy.
    computed["total_collections"] = float(waterfall_output.get("total_distributed", 0.0))

    # reserve_fund_balance — not directly in WaterfallOutput; accept from extra key
    # or fall back to 0.0.  Callers may enrich the dict with "reserve_fund_balance"
    # derived from WaterfallInput.reserve_account_balance.
    computed["reserve_fund_balance"] = float(
        waterfall_output.get("reserve_fund_balance", 0.0)
    )
    if "reserve_fund_balance" not in waterfall_output:
        logger.info(
            "reserve_fund_balance not in waterfall_output; computed value set to 0.0. "
            "Enrich the dict with WaterfallInput.reserve_account_balance if needed."
        )

    # pool_balance — not in WaterfallOutput; accept from extra key or 0.0.
    computed["pool_balance"] = float(waterfall_output.get("pool_balance", 0.0))
    if "pool_balance" not in waterfall_output:
        logger.info(
            "pool_balance not in waterfall_output; computed value set to 0.0. "
            "Enrich the dict with the pool balance from the ESMA tape if needed."
        )

    return computed


def _build_reported_figure(
    line_item: str,
    reported_value: float,
    computed_value: float,
    tolerance_pct: float,
) -> ReportedFigure:
    """Construct a ReportedFigure with delta and match status."""
    delta = reported_value - computed_value
    if computed_value != 0.0:
        delta_pct = delta / computed_value * 100.0
    else:
        # Avoid division by zero: if both are 0 it's a perfect match; otherwise
        # the discrepancy is unbounded.  Use 999.0 as a finite sentinel that
        # (a) JSON-serializes cleanly, (b) exceeds any reasonable tolerance_pct,
        # and (c) is unambiguous in the output (not null / not inf).
        delta_pct = 0.0 if reported_value == 0.0 else 999.0

    match = abs(delta_pct) < tolerance_pct

    return ReportedFigure(
        line_item=line_item,
        reported_value=reported_value,
        computed_value=computed_value,
        delta=delta,
        delta_pct=delta_pct,
        match=match,
        tolerance_pct=tolerance_pct,
    )


def _build_summary(
    figures_checked: int,
    figures_matched: int,
    mismatched_items: list[ReportedFigure],
    tolerance_pct: float,
) -> str:
    """Build a human-readable summary string."""
    tol_str = f"{tolerance_pct:.4g}%"
    if not mismatched_items:
        return (
            f"{figures_matched}/{figures_checked} figures match within {tol_str} tolerance."
        )

    mismatch_names = ", ".join(
        f"{f.line_item} (Δ={f.delta:+,.0f} EUR, {f.delta_pct:+.2f}%)"
        for f in mismatched_items
    )
    return (
        f"{figures_matched}/{figures_checked} figures match within {tol_str} tolerance; "
        f"{len(mismatched_items)} mismatch: {mismatch_names}"
    )


# ---------------------------------------------------------------------------
# Primitive
# ---------------------------------------------------------------------------


@register_primitive(
    name="report_verifier",
    version="0.1.0",
    description="Verify investor report figures against waterfall-computed distributions",
    tags=["verification", "investor_report", "waterfall", "audit"],
)
class ReportVerifier(Primitive[ReportVerifierInput, ReportVerifierOutput]):
    """Verify investor report figures against waterfall-computed distributions.

    Extracts five key payment figures from a monthly investor report PDF using
    Gemini 2.5 Flash, then compares them against the corresponding values from
    a WaterfallOutput. Line items outside the configured tolerance are flagged
    as mismatches.

    This is the "did the servicer apply the waterfall correctly?" primitive — the
    key audit demo for Green Lion 2026-1.
    """

    name = "report_verifier"
    version = "0.1.0"
    description = "Verify investor report figures against waterfall-computed distributions"

    def execute(  # type: ignore[override]
        self, input: ReportVerifierInput
    ) -> PrimitiveResult[ReportVerifierOutput]:
        """Run the report verifier.

        Steps
        -----
        1. Check the per-period cache; load figures if cached.
        2. If not cached: call Gemini 2.5 Flash to extract figures from the PDF.
        3. Write the cache.
        4. Extract computed values from ``waterfall_output``.
        5. Build ``ReportedFigure`` list with delta / delta_pct / match.
        6. Compute confidence (0.9 if ≥3 figures, else 0.6).
        7. Return ``PrimitiveResult`` with audit entry and citations.

        Parameters
        ----------
        input:
            Validated ``ReportVerifierInput``.

        Returns
        -------
        PrimitiveResult[ReportVerifierOutput]
        """
        t0 = time.perf_counter()
        input_hash = input.input_hash()

        # ---------------------------------------------------------------
        # 1. Load or extract reported figures
        # ---------------------------------------------------------------
        reported_figures = _load_cache(input.reporting_period)
        cache_hit = reported_figures is not None

        if not cache_hit:
            reported_figures = _extract_figures_with_gemini(
                pdf_url=input.investor_report_url,
                reporting_period=input.reporting_period,
            )
            _write_cache(input.reporting_period, reported_figures)

        # ---------------------------------------------------------------
        # 2. Extract computed values from waterfall output
        # ---------------------------------------------------------------
        computed_values = _extract_computed_values(input.waterfall_output)

        # ---------------------------------------------------------------
        # 3. Build ReportedFigure list
        # ---------------------------------------------------------------
        line_items: list[ReportedFigure] = []
        for key in _FIGURE_DESCRIPTIONS:
            reported_val = reported_figures.get(key)
            if reported_val is None:
                # Figure not extracted — skip (counts against figure total for confidence)
                continue
            computed_val = computed_values.get(key, 0.0)
            line_items.append(
                _build_reported_figure(
                    line_item=key,
                    reported_value=reported_val,
                    computed_value=computed_val,
                    tolerance_pct=input.tolerance_pct,
                )
            )

        # ---------------------------------------------------------------
        # 4. Aggregates
        # ---------------------------------------------------------------
        figures_checked = len(line_items)
        figures_matched = sum(1 for f in line_items if f.match)
        figures_mismatched = figures_checked - figures_matched
        overall_match = all(f.match for f in line_items)
        mismatched_items = [f for f in line_items if not f.match]

        summary = _build_summary(
            figures_checked=figures_checked,
            figures_matched=figures_matched,
            mismatched_items=mismatched_items,
            tolerance_pct=input.tolerance_pct,
        )

        output = ReportVerifierOutput(
            reporting_period=input.reporting_period,
            figures_checked=figures_checked,
            figures_matched=figures_matched,
            figures_mismatched=figures_mismatched,
            line_items=line_items,
            overall_match=overall_match,
            summary=summary,
        )

        # ---------------------------------------------------------------
        # 5. Confidence
        # ---------------------------------------------------------------
        confidence = _CONFIDENCE_HIGH if figures_checked >= _CONFIDENCE_THRESHOLD else _CONFIDENCE_LOW

        # ---------------------------------------------------------------
        # 6. Citations
        # ---------------------------------------------------------------
        citations = [
            Citation(
                document=f"Green Lion 2026-1 Investor Report — {input.reporting_period}",
                page_or_row=None,
                excerpt=(
                    f"Figures extracted via Gemini 2.5 Flash from {input.investor_report_url} "
                    f"({'cache hit' if cache_hit else 'live extraction'})"
                ),
            ),
            Citation(
                document="WaterfallRunner output",
                page_or_row="tranche_distributions",
                excerpt=(
                    "Computed Class A distributions from deterministic waterfall execution."
                ),
            ),
        ]

        # ---------------------------------------------------------------
        # 7. Audit entry
        # ---------------------------------------------------------------
        duration_ms = (time.perf_counter() - t0) * 1000.0
        audit = AuditEntry.now(
            primitive_name=self.name,
            version=self.version,
            input_hash=input_hash,
            duration_ms=duration_ms,
        )

        return PrimitiveResult[ReportVerifierOutput](
            output=output,
            confidence=confidence,
            citations=citations,
            audit_entry=audit,
        )
