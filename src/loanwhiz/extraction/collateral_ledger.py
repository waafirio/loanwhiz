"""Collateral ground-truth ledger — per-period actuals from the investor reports.

This module promotes the S0 ground-truth spike (``scripts/s0_extract_reports.py``)
into production code: it ingests the monthly **collateral** investor reports into
a structured, per-period ledger that S7 (#187) reconciles the reconstructed
COLLATERAL roll-forward against, to the cent.

What these reports are (and are NOT) — spike S0 (#180)
------------------------------------------------------
The Green Lion 2026-1 monthly investor reports are ESMA **Portfolio &
Performance** reports (collateral-side). They carry:

- the pool balance roll-forward (begin/end balance, repayments, prepayments,
  further advances, other balance changes),
- loan counts, the weighted-average current coupon,
- arrears / performance ratios (default amount, CPR, PPR, CDR, payment ratio).

They do **NOT** carry any liability-side figures: no note/tranche balances, no
note factors, no PDL, no reserve account, and no priority-of-payments
distributions (the "Transaction Specific Information" page is a blank header).
The operator decision on #179 ("tapes = spec, split proof") therefore scopes this
ledger to the **collateral** side only: it is a reconciliation target for S7's
collateral reconstruction, **not** a liability seed. Liabilities seed from the
prospectus in S6 (#186) via ``DealState.seed_from_prospectus``. The per-period
``has_liability_section`` probe records this absence as data, so the contract is
encoded rather than assumed.

The two seams
-------------
- **Parse path (offline, unit-tested):** :func:`_period_from_extract` /
  :func:`_ledger_from_extracts` map the raw extract dicts (the
  ``report_extract_full.json`` shape) into typed :class:`CollateralPeriod` /
  :class:`CollateralLedger` models. No network.
- **Extraction + cache (live, integration-gated):**
  :func:`extract_collateral_ledger` returns the ledger for a deal, served from a
  durable on-disk cache (``data/extraction_cache/``). On a cold cache it
  warm-starts from the legacy ``/tmp`` spike cache if present, otherwise calls
  Gemini-on-Vertex per report URL (the same extraction the spike already ran).
  The demo never re-runs Docling/Gemini once the cache is warm.
"""

from __future__ import annotations

import json
import logging
import pathlib
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache locations
# ---------------------------------------------------------------------------
#
# Durable cache lives under the repo's ``data/extraction_cache/`` (gitignored,
# .gitkeep'd) — the same durable-cache convention the sub-extractors adopted in
# PR #152, so the warmed ledger survives a reboot and ships with the repo. The
# legacy ``/tmp`` spike cache (written by ``scripts/s0_extract_reports.py``) is
# read once as a warm-start, then promoted into the durable cache.

_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EXTRACTION_CACHE_DIR = _REPO_ROOT / "data" / "extraction_cache"

# The spike's combined-extract cache (all periods in one file).
LEGACY_SPIKE_CACHE = pathlib.Path("/tmp/loanwhiz_cache/report_extract_full.json")


# ---------------------------------------------------------------------------
# Per-period collateral actuals
# ---------------------------------------------------------------------------


class CollateralPeriod(BaseModel):
    """One reporting period's collateral actuals from the investor report.

    All monetary fields are in the deal currency (EUR for Green Lion 2026-1).
    The pool roll-forward is self-consistent: ``balance_begin − repayments −
    prepayments + further_advances + other_balance_change == balance_end``
    (S0 verified this to 0.0000 across all 3 periods). ``roll_forward_residual``
    exposes the deviation so S7 can assert it is ~0.

    The key is :attr:`reporting_date` — the ISO period-end date (e.g.
    ``"2026-02-28"``), matching ``DealState.reporting_date`` and the tape
    analytics keys, so S7 joins report ↔ reconstructed state by date.

    Liability-side fields are intentionally absent: these reports carry none
    (see module docstring). :attr:`has_liability_section` records that absence.
    """

    # --- key / metadata ---
    reporting_date: str = Field(
        ..., description="ISO period-end date — the ledger key (e.g. 2026-02-28)."
    )
    period_label: str = Field(
        ..., description='Human-readable period label, e.g. "February 2026".'
    )
    period_start: str | None = Field(
        default=None, description="ISO reporting-period start date."
    )
    period_end: str | None = Field(
        default=None, description="ISO reporting-period end date (== reporting_date)."
    )
    report_published_date: str | None = Field(
        default=None, description="Date the report itself was published."
    )

    # --- pool / collateral figures ---
    loans_begin: int | None = Field(default=None, description="Loan count, start of period.")
    loans_end: int | None = Field(default=None, description="Loan count, end of period.")
    pool_balance_begin: float = Field(
        ..., ge=0.0, description="Pool outstanding balance, start of period (EUR)."
    )
    pool_balance_end: float = Field(
        ..., ge=0.0, description="Pool outstanding balance, end of period (EUR)."
    )
    repayments: float = Field(
        default=0.0, ge=0.0, description="Scheduled principal repayments (EUR, positive)."
    )
    prepayments: float = Field(
        default=0.0, ge=0.0, description="Unscheduled principal / prepayments (EUR, positive)."
    )
    further_advances: float = Field(
        default=0.0, ge=0.0, description="Further advances added to the pool (EUR)."
    )
    other_balance_change: float = Field(
        default=0.0,
        description="Other (non-principal) balance movement (EUR, signed).",
    )

    # --- performance / arrears ratios ---
    wtd_avg_coupon_pct: float | None = Field(
        default=None, description="Weighted-average current coupon (%)."
    )
    default_amount: float | None = Field(
        default=None, description="Defaulted amount this period (EUR)."
    )
    cpr_life_pct: float | None = Field(default=None, description="Life CPR (%).")
    ppr_life_pct: float | None = Field(default=None, description="Life PPR (%).")
    cdr_pct: float | None = Field(default=None, description="CDR (%).")
    payment_ratio_pct: float | None = Field(default=None, description="Payment ratio (%).")

    # --- collateral-only contract probe (spike S0) ---
    has_liability_section: bool = Field(
        default=False,
        description=(
            "Whether the report carries ANY liability-side data (note/tranche "
            "balances, note factors, PDL, reserve account, or priority-of-payments "
            "distributions). False for all Green Lion 2026-1 collateral reports."
        ),
    )

    @field_validator("reporting_date")
    @classmethod
    def _non_empty_date(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("reporting_date must be a non-empty ISO date string")
        return v

    @property
    def principal_collected(self) -> float:
        """Total principal collected = scheduled repayments + prepayments."""
        return self.repayments + self.prepayments

    @property
    def roll_forward_residual(self) -> float:
        """Self-consistency residual of the report's pool roll-forward.

        ``begin − repayments − prepayments + further_advances +
        other_balance_change − end``. Should be ~0 for a self-consistent report
        (S0 verified 0.0000 across all 3 periods).
        """
        return (
            self.pool_balance_begin
            - self.repayments
            - self.prepayments
            + self.further_advances
            + self.other_balance_change
            - self.pool_balance_end
        )

    def pool_factor(self, original_pool_balance: float) -> float:
        """End-of-period pool factor against an original (closing) pool balance."""
        if original_pool_balance <= 0.0:
            return 0.0
        return self.pool_balance_end / original_pool_balance


# ---------------------------------------------------------------------------
# The ledger — all periods keyed by reporting date
# ---------------------------------------------------------------------------


class CollateralLedger(BaseModel):
    """Per-period collateral ground-truth ledger for one deal.

    Periods are held sorted by :attr:`CollateralPeriod.reporting_date`. The
    ledger is the clean reconciliation target S7 reads: look a period up by ISO
    reporting date, assert the reconstructed collateral roll-forward ties to it.

    JSON-serialisable (``model_dump_json``) for the durable cache.
    """

    deal_name: str = Field(..., description="The deal this ledger covers.")
    periods: list[CollateralPeriod] = Field(
        default_factory=list, description="Periods, sorted by reporting_date."
    )

    @field_validator("periods")
    @classmethod
    def _sort_periods(cls, v: list[CollateralPeriod]) -> list[CollateralPeriod]:
        return sorted(v, key=lambda p: p.reporting_date)

    @property
    def reporting_dates(self) -> list[str]:
        """The ISO reporting dates present, in order."""
        return [p.reporting_date for p in self.periods]

    @property
    def by_date(self) -> dict[str, CollateralPeriod]:
        """Map of ISO reporting date → period (the S7 join surface)."""
        return {p.reporting_date: p for p in self.periods}

    def period_for(self, reporting_date: str) -> CollateralPeriod | None:
        """Look a period up by its ISO reporting date, or ``None``."""
        return self.by_date.get(reporting_date)

    def chains_cleanly(self, *, tolerance: float = 0.01) -> bool:
        """Whether consecutive periods chain: ``end[N] == begin[N+1]``.

        The pool balance at the end of period N must equal the opening balance
        of period N+1 (S0 verified this exactly across the Green Lion periods).
        ``tolerance`` is in EUR (default one cent).
        """
        for prev, nxt in zip(self.periods, self.periods[1:]):
            if abs(prev.pool_balance_end - nxt.pool_balance_begin) > tolerance:
                return False
        return True


# ---------------------------------------------------------------------------
# Parse path (offline — the unit-tested seam)
# ---------------------------------------------------------------------------

# Maps the raw extract keys (the ``report_extract_full.json`` shape, produced by
# scripts/s0_extract_reports.py) to CollateralPeriod fields.
_BALANCE_KEYS = ("repayments", "prepayments", "further_advances", "other_balance_change")


def _as_float(value: Any, *, abs_value: bool = False) -> float:
    """Coerce an extract value to float (None/missing → 0.0).

    ``abs_value`` takes the magnitude — the report shows repayments/prepayments
    as reductions but the spike prompt already normalises them positive; this is
    belt-and-braces so a signed extract still lands as a non-negative figure.
    """
    if value is None:
        return 0.0
    try:
        f = float(value)
    except (TypeError, ValueError):
        logger.warning("Non-numeric extract value %r; treating as 0.0", value)
        return 0.0
    return abs(f) if abs_value else f


def _opt_float(value: Any) -> float | None:
    """Coerce to float, preserving ``None`` for genuinely-absent figures."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning("Non-numeric optional extract value %r; treating as None", value)
        return None


def _opt_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _period_from_extract(period_label: str, raw: dict[str, Any]) -> CollateralPeriod:
    """Map one raw extract dict to a :class:`CollateralPeriod`.

    ``raw`` is one period's object from ``report_extract_full.json`` (or the
    equivalent live Gemini extraction). The ISO ``reporting_date`` key is the
    report's period-end (``reporting_period_end``).

    The collateral-only contract probe inverts the report's ``has_tranche_section``
    flag: the reports carry no liability section, so ``has_liability_section`` is
    ``False`` and the (null) liability figures are simply not modelled.
    """
    period_end = raw.get("reporting_period_end") or raw.get("period_end")
    if not period_end:
        raise ValueError(
            f"extract for {period_label!r} has no reporting_period_end — "
            "cannot key the collateral ledger by reporting date"
        )

    return CollateralPeriod(
        reporting_date=str(period_end),
        period_label=period_label,
        period_start=raw.get("reporting_period_start"),
        period_end=str(period_end),
        report_published_date=raw.get("reporting_date"),
        loans_begin=_opt_int(raw.get("loans_begin")),
        loans_end=_opt_int(raw.get("loans_end")),
        pool_balance_begin=_as_float(raw.get("balance_begin")),
        pool_balance_end=_as_float(raw.get("balance_end")),
        repayments=_as_float(raw.get("repayments"), abs_value=True),
        prepayments=_as_float(raw.get("prepayments"), abs_value=True),
        further_advances=_as_float(raw.get("further_advances"), abs_value=True),
        other_balance_change=_as_float(raw.get("other_balance_change")),
        wtd_avg_coupon_pct=_opt_float(raw.get("wtd_avg_coupon_pct")),
        default_amount=_opt_float(raw.get("default_amount_crr")),
        cpr_life_pct=_opt_float(raw.get("cpr_life_pct")),
        ppr_life_pct=_opt_float(raw.get("ppr_life_pct")),
        cdr_pct=_opt_float(raw.get("cdr_pct")),
        payment_ratio_pct=_opt_float(raw.get("payment_ratio_pct")),
        has_liability_section=bool(raw.get("has_tranche_section", False)),
    )


def _ledger_from_extracts(
    deal_name: str, extracts: dict[str, dict[str, Any]]
) -> CollateralLedger:
    """Build a :class:`CollateralLedger` from ``{period_label: extract}`` dicts.

    This is the pure, offline assembly step — the live extraction and the warm
    caches both funnel through here. Periods are sorted by reporting date by the
    model validator.
    """
    periods = [_period_from_extract(label, raw) for label, raw in extracts.items()]
    return CollateralLedger(deal_name=deal_name, periods=periods)


# ---------------------------------------------------------------------------
# Cache + live extraction (the integration-gated seam)
# ---------------------------------------------------------------------------


def _slug(name: str) -> str:
    """Filesystem-safe slug from a deal name (mirrors assembler._slug).

    >>> _slug("Green Lion 2026-1 B.V.")
    'green-lion-2026-1-bv'
    """
    lowered = re.sub(r"[.,]+", "", name.lower())
    replaced = re.sub(r"\s+", "-", lowered)
    return re.sub(r"-{2,}", "-", replaced).strip("-")


def _cache_path(deal_name: str, cache_dir: str | Path) -> Path:
    return Path(cache_dir) / f"collateral-ledger-{_slug(deal_name)}.json"


def _load_durable_cache(path: Path) -> CollateralLedger | None:
    if not path.exists():
        return None
    try:
        return CollateralLedger.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        logger.warning("Durable collateral-ledger cache read failed (%s): %s", path, exc)
        return None


def _write_durable_cache(ledger: CollateralLedger, path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(ledger.model_dump_json(indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Durable collateral-ledger cache write failed (%s): %s", path, exc)


def _load_legacy_spike_cache(
    deal_name: str, path: pathlib.Path = LEGACY_SPIKE_CACHE
) -> CollateralLedger | None:
    """Warm-start from the S0 spike's ``report_extract_full.json`` if present."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data:
            return None
        return _ledger_from_extracts(deal_name, data)
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        logger.warning("Legacy spike cache read failed (%s): %s", path, exc)
        return None


# The live-extraction prompt — lifted from scripts/s0_extract_reports.py so the
# production extractor and the spike pull the identical figures.
_EXTRACT_PROMPT = """You are a structured finance analyst reading a {deal_name} monthly ESMA Portfolio & Performance report for {period}.
Extract EXACTLY this JSON (EUR numbers, no commas/symbols; use null if the figure is genuinely absent from the document):
{{
 "reporting_period_start": "<date>",
 "reporting_period_end": "<date>",
 "reporting_date": "<date the report was published>",
 "loans_begin": <int>, "loans_end": <int>,
 "balance_begin": <float>, "balance_end": <float>,
 "repayments": <float>, "prepayments": <float>, "further_advances": <float>, "other_balance_change": <float>,
 "wtd_avg_coupon_pct": <float>,
 "default_amount_crr": <float>,
 "cpr_life_pct": <float>, "ppr_life_pct": <float>, "cdr_pct": <float>, "payment_ratio_pct": <float>,
 "has_tranche_section": <true/false: does the report contain ANY note/tranche balances, note factors, PDL, reserve account, or priority-of-payments/waterfall distribution tables?>
}}
Repayments=scheduled principal repayments (the "Repayments" line in the Amounts roll-forward). Prepayments=the "Prepayments" line. Report them as POSITIVE numbers even if shown with a -/- reduction sign.
Return ONLY valid JSON, no markdown fences."""


def _strip_fences(text: str) -> str:
    """Strip markdown code fences a model may wrap JSON in."""
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    return re.sub(r"\s*```$", "", text)


def _extract_extracts_with_gemini(
    deal_name: str, report_urls: list[dict[str, str]]
) -> dict[str, dict[str, Any]]:  # pragma: no cover - network/integration only
    """Extract raw per-period dicts from the report PDFs via Gemini-on-Vertex.

    Mirrors ``scripts/s0_extract_reports.py``: one Gemini 2.5 Pro call per report
    URL, returning ``{period_label: extract_dict}``. Not exercised in the fast
    suite — :func:`extract_collateral_ledger` only reaches here on a cold cache,
    which the integration test triggers explicitly.
    """
    from google import genai

    from loanwhiz.config import GCP_LOCATION, GCP_PROJECT, MODEL_PRO

    client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)
    out: dict[str, dict[str, Any]] = {}
    for entry in report_urls:
        period, url = entry["period"], entry["url"]
        resp = client.models.generate_content(
            model=MODEL_PRO,
            contents=[
                {
                    "role": "user",
                    "parts": [
                        {"file_data": {"mime_type": "application/pdf", "file_uri": url}},
                        {"text": _EXTRACT_PROMPT.format(deal_name=deal_name, period=period)},
                    ],
                }
            ],
        )
        out[period] = json.loads(_strip_fences(resp.text))
    return out


def extract_collateral_ledger(
    deal_context: dict[str, Any],
    *,
    force_refresh: bool = False,
    cache_dir: str | Path = DEFAULT_EXTRACTION_CACHE_DIR,
    legacy_cache: pathlib.Path = LEGACY_SPIKE_CACHE,
) -> CollateralLedger:
    """Return the per-period collateral ground-truth ledger for a deal.

    Resolution order (cheapest first):

    1. **Durable cache** ``data/extraction_cache/collateral-ledger-{slug}.json`` —
       on hit (and not ``force_refresh``), load + validate + return. No network.
    2. **Warm-start** from the S0 spike cache (``/tmp/.../report_extract_full.json``)
       if present — build the ledger from it and persist to the durable cache, so
       the trusted spike artifact is promoted once and then owned.
    3. **Live extraction** via Gemini-on-Vertex per report URL (slow; the demo
       avoids this once the cache is warm). Persisted to the durable cache.

    Parameters
    ----------
    deal_context:
        A deal-context dict (e.g. ``GREEN_LION``) with at least ``deal_name`` and
        ``investor_report_urls`` (``[{"period": str, "url": str}, ...]``).
    force_refresh:
        Bypass both caches and re-run live extraction.
    cache_dir:
        Durable cache directory (default the repo's ``data/extraction_cache/``).
    legacy_cache:
        The S0 spike's combined-extract cache path (warm-start source).

    Returns
    -------
    CollateralLedger
        The deal's collateral ledger, sorted by reporting date.
    """
    deal_name = deal_context["deal_name"]
    path = _cache_path(deal_name, cache_dir)

    if not force_refresh:
        cached = _load_durable_cache(path)
        if cached is not None:
            return cached

        warm = _load_legacy_spike_cache(deal_name, legacy_cache)
        if warm is not None:
            _write_durable_cache(warm, path)
            return warm

    extracts = _extract_extracts_with_gemini(
        deal_name, deal_context["investor_report_urls"]
    )
    ledger = _ledger_from_extracts(deal_name, extracts)
    _write_durable_cache(ledger, path)
    return ledger
