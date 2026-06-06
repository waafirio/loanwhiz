"""LangGraph tool wrappers for all registered LoanWhiz SF primitives."""

import json
from pathlib import Path

from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode

from loanwhiz.config import DEAL_REGISTRY
from loanwhiz.extraction.assembler import (
    DEFAULT_DEAL_CACHE_DIR,
    DealModel,
    _slug,
)
from loanwhiz.primitives.collections_aggregator import CollectionsAggregator, CollectionsInput
from loanwhiz.primitives.covenant_monitor import CovenantInput, CovenantMonitor
from loanwhiz.primitives.esma_tape_normaliser import EsmaTapeInput, EsmaTapeNormaliser
from loanwhiz.primitives.waterfall_runner import WaterfallInput, WaterfallRunner

# Default deal the chat agent serves. The agent is single-deal today; the
# grounding tools accept a ``deal_id`` for forward generality but fall back to
# this so existing one-deal behaviour is unchanged.
DEFAULT_DEAL_ID = "green-lion-2026-1"

# ---------------------------------------------------------------------------
# Context-bounding for multi-period tool output
# ---------------------------------------------------------------------------

# Above this many distinct reporting periods, multi-period tool output is
# summarised rather than returned verbatim. The covenant monitor emits one
# status row per trigger *per period*, so with ~48 monthly periods (4 years,
# the #128 scale target) and 5 default triggers that is ~240 rows — enough to
# blow up the agent's context/cost/latency. Below the threshold (the 3-period
# Green Lion demo) the payload is returned unchanged.
MAX_VERBATIM_PERIODS = 6


def _bound_covenant_output(output: dict) -> dict:
    """Bound a CovenantOutput dict so it stays context-cheap over many periods.

    ``CovenantMonitor`` returns ``trigger_statuses`` as one row per trigger per
    period. When the data spans more than :data:`MAX_VERBATIM_PERIODS` distinct
    periods, this collapses ``trigger_statuses`` to just the latest period's
    rows and adds a computed ``trend_summary`` (per-trigger first/latest/min/max
    proximity, worst proximity, ever-triggered flag, net trend direction) so the
    agent can still answer trend questions without every period in context.

    Below the threshold the dict is returned unchanged — preserving the exact
    current behaviour for the small Green Lion demo deal.

    The summary is pure Python over data the primitive already produced: no
    extra LLM call, no retrieval.
    """
    statuses = output.get("trigger_statuses", [])
    periods = list(dict.fromkeys(s["period"] for s in statuses))  # ordered, unique
    if len(periods) <= MAX_VERBATIM_PERIODS:
        return output

    latest_period = periods[-1]
    latest_rows = [s for s in statuses if s["period"] == latest_period]

    # Per-trigger trend aggregates across the full (now-summarised) history.
    by_trigger: dict[str, list[dict]] = {}
    for s in statuses:
        by_trigger.setdefault(s["trigger_name"], []).append(s)

    trend_summary = []
    for name, rows in by_trigger.items():
        proximities = [r["proximity_pct"] for r in rows]
        first, latest = rows[0], rows[-1]
        delta = latest["proximity_pct"] - first["proximity_pct"]
        if abs(delta) < 1.0:
            net_trend = "stable"
        elif delta > 0:
            net_trend = "deteriorating"  # higher proximity = closer to breach
        else:
            net_trend = "improving"
        trend_summary.append(
            {
                "trigger_name": name,
                "first_period": first["period"],
                "latest_period": latest["period"],
                "first_proximity_pct": first["proximity_pct"],
                "latest_proximity_pct": latest["proximity_pct"],
                "min_proximity_pct": min(proximities),
                "max_proximity_pct": max(proximities),
                "ever_triggered": any(r["is_triggered"] for r in rows),
                "net_trend": net_trend,
            }
        )

    bounded = dict(output)
    bounded["trigger_statuses"] = latest_rows
    bounded["trend_summary"] = trend_summary
    bounded["periods_summarised"] = (
        f"{len(periods)} periods analysed ({periods[0]} … {latest_period}); "
        f"trigger_statuses shows only the latest period. See trend_summary for "
        f"per-trigger min/max/latest proximity and net trend across all periods."
    )
    return bounded


@tool
def load_esma_tape(file_url: str, reporting_date: str | None = None) -> dict:
    """Load and analyse an ESMA-format loan-level tape CSV.

    Returns pool statistics, weighted averages, arrears breakdown, EPC
    distribution, and the ingestion ``data_source`` (``"deeploans"`` when the
    tape was fetched through the deeploans ETL backend, ``"direct"`` for a
    direct CSV/parquet URL read) so the answer's governance evidence records
    honest data provenance.
    Use for: understanding pool composition, computing arrears rates, checking EPC mix.
    """
    primitive = EsmaTapeNormaliser()
    result = primitive.execute(EsmaTapeInput(file_url=file_url, reporting_date=reporting_date))
    return result.output.model_dump() | {
        "confidence": result.confidence,
        "citations": [c.model_dump() for c in result.citations],
        "duration_ms": result.audit_entry.duration_ms,
    }


@tool
def run_waterfall(
    reporting_period: str,
    available_revenue_funds: float,
    available_principal_funds: float,
    senior_fees: float,
    class_a_balance: float,
    class_a_rate_pct: float,
    class_b_balance: float,
    class_c_balance: float,
    reserve_account_balance: float,
    reserve_account_target: float,
    class_a_pdl_balance: float = 0.0,
    class_b_pdl_balance: float = 0.0,
) -> dict:
    """Execute the Green Lion RMBS payment waterfall for a single period.

    Returns computed distributions per tranche following the 11-step Revenue Priority.
    Use for: computing what each tranche should receive given available funds.
    """
    primitive = WaterfallRunner()
    result = primitive.execute(
        WaterfallInput(
            reporting_period=reporting_period,
            available_revenue_funds=available_revenue_funds,
            available_principal_funds=available_principal_funds,
            senior_fees=senior_fees,
            swap_payment=0.0,
            class_a_balance=class_a_balance,
            class_a_rate_pct=class_a_rate_pct,
            class_b_balance=class_b_balance,
            class_c_balance=class_c_balance,
            reserve_account_balance=reserve_account_balance,
            reserve_account_target=reserve_account_target,
            class_a_pdl_balance=class_a_pdl_balance,
            class_b_pdl_balance=class_b_pdl_balance,
        )
    )
    return result.output.model_dump() | {
        "confidence": result.confidence,
        "citations": [c.model_dump() for c in result.citations],
        "duration_ms": result.audit_entry.duration_ms,
    }


@tool
def check_covenants(
    periods_json: str,
    class_a_pdl_balance: float = 0.0,
    class_b_pdl_balance: float = 0.0,
    reserve_account_balance: float = 0.0,
    reserve_account_target: float = 0.0,
    original_pool_balance: float = 1_063_600_000.0,
) -> dict:
    """Check RMBS covenant compliance against trigger thresholds.

    periods_json: JSON list of EsmaTapeOutput dicts (from load_esma_tape).
    Returns trigger status, proximity to breach, active triggers.

    Over many periods (> 6) the output is bounded to keep context cheap:
    ``trigger_statuses`` then shows only the latest period, and a
    ``trend_summary`` carries per-trigger min/max/latest proximity and the net
    trend across all periods. Answer trend questions from ``trend_summary``;
    ``active_triggers``/``near_miss_triggers``/``summary`` always reflect the
    latest period regardless of how many periods were analysed.
    """
    primitive = CovenantMonitor()
    result = primitive.execute(
        CovenantInput(
            periods=json.loads(periods_json),
            triggers=CovenantMonitor.DEFAULT_TRIGGERS,
            class_a_pdl_balance=class_a_pdl_balance,
            class_b_pdl_balance=class_b_pdl_balance,
            reserve_account_balance=reserve_account_balance,
            reserve_account_target=reserve_account_target,
            original_pool_balance=original_pool_balance,
        )
    )
    return _bound_covenant_output(result.output.model_dump()) | {
        "confidence": result.confidence,
        "citations": [c.model_dump() for c in result.citations],
        "duration_ms": result.audit_entry.duration_ms,
    }


@tool
def aggregate_collections(
    tape_file_url: str,
    reporting_period: str,
    prev_pool_balance: float | None = None,
    class_a_balance: float = 1_000_000_000.0,
    class_a_rate_pct: float = 3.62,
) -> dict:
    """Aggregate ESMA loan tape into waterfall-ready collection amounts.

    Returns Available Revenue Funds and Available Principal Funds for the waterfall runner.
    """
    primitive = CollectionsAggregator()
    result = primitive.execute(
        CollectionsInput(
            tape_file_url=tape_file_url,
            reporting_period=reporting_period,
            prev_pool_balance=prev_pool_balance,
            class_a_balance=class_a_balance,
            class_a_rate_pct=class_a_rate_pct,
        )
    )
    return result.output.model_dump() | {
        "confidence": result.confidence,
        "citations": [c.model_dump() for c in result.citations],
        "duration_ms": result.audit_entry.duration_ms,
    }


# ---------------------------------------------------------------------------
# Deal grounding — the extracted deal model + the tape registry
# ---------------------------------------------------------------------------


def _read_cached_deal_model(deal: dict) -> DealModel | None:
    """Read the cached extracted :class:`DealModel` for a deal, or ``None``.

    Reads the assembler's on-disk cache at
    ``{DEFAULT_DEAL_CACHE_DIR}/{slug(deal_name)}.json`` and validates it into a
    :class:`DealModel`. **Never triggers a cold extraction** — a cache miss (no
    file) returns ``None`` rather than invoking the ~10min Docling pipeline.

    Mirrors ``loanwhiz.api.main._load_cached_deal_model`` exactly (same cache
    dir, same slug, same non-blocking contract) so the chat agent and the API
    read the identical artifact. The cache dir is gitignored and warmed at
    deploy time, so a fresh checkout legitimately has no model — hence the
    graceful ``None``.
    """
    cache_path = Path(DEFAULT_DEAL_CACHE_DIR) / f"{_slug(deal['deal_name'])}.json"
    if not cache_path.exists():
        return None
    return DealModel.model_validate_json(cache_path.read_text(encoding="utf-8"))


@tool
def get_deal_model(deal_id: str = DEFAULT_DEAL_ID) -> dict:
    """Read the prospectus-derived deal model (terms, triggers, waterfall, definitions).

    Use this FIRST for any question about the deal's *structural terms* rather
    than the loan pool: the reserve account target, tranche sizes/coupons, the
    payment-waterfall order, covenant trigger thresholds, the clean-up call, or
    any defined term from the prospectus. The data is extracted from the deal's
    prospectus PDF.

    Returns on a cache hit: ``deal_name``, ``completeness_score``,
    ``trigger_names``, ``tranche_structure`` (note classes senior→junior),
    ``covenants`` (triggers + issuer covenants), ``waterfalls`` (revenue /
    redemption / post-enforcement), and ``definitions`` (term → meaning).

    When the prospectus has not been extracted in this environment, returns
    ``extraction_status="not_cached"`` with a note — the deal-config document
    URLs are still reachable via ``list_deal_tapes``. This tool never triggers a
    fresh extraction, so it always returns promptly.
    """
    deal = DEAL_REGISTRY.get(deal_id)
    if deal is None:
        return {
            "error": f"deal {deal_id!r} not found",
            "available_deals": list(DEAL_REGISTRY),
        }

    model = _read_cached_deal_model(deal)
    if model is None:
        return {
            "deal_id": deal_id,
            "deal_name": deal["deal_name"],
            "extraction_status": "not_cached",
            "note": (
                "The prospectus has not been extracted in this environment, so "
                "structural terms (tranches, triggers, waterfall, definitions) "
                "are unavailable. The deal's document URLs (prospectus, tapes, "
                "investor reports) are still available via list_deal_tapes."
            ),
        }

    return {
        "deal_id": deal_id,
        "deal_name": deal["deal_name"],
        "extraction_status": "cached",
        "completeness_score": model.metadata.completeness_score,
        "trigger_names": model.trigger_names,
        "tranche_structure": model.tranche_structure,
        "covenants": model.covenants,
        "waterfalls": model.waterfalls,
        "definitions": model.definitions,
    }


@tool
def list_deal_tapes(deal_id: str = DEFAULT_DEAL_ID, period: str | None = None) -> dict:
    """List (and optionally select) the deal's loan-level tapes and documents.

    Use this to find the right tape URL BEFORE calling ``load_esma_tape`` or
    ``aggregate_collections`` — do not guess or hardcode URLs. Green Lion 2026-1
    reports three monthly tapes (2026 Feb/Mar/Apr; Jan-2026 is intentionally
    absent), each keyed by its month-end ``date``.

    Parameters
    ----------
    deal_id:
        Registry deal id (defaults to the Green Lion demo deal).
    period:
        Optional substring matched against each tape's ``date`` (``YYYY-MM-DD``).
        ``"2026-03"`` selects Mar-2026; ``"2026"`` selects all of 2026; a full
        ``"2026-03-31"`` selects exactly that month. Omit to list every tape.

    Returns ``tape_urls`` (all matching ``{date, url}`` entries), plus
    ``prospectus_url`` and ``investor_report_urls``. When a ``period`` filter
    matches exactly one tape, ``selected_url`` carries that tape's URL for
    direct use. An unknown ``deal_id`` returns an ``error`` plus the list of
    ``available_deals``.
    """
    deal = DEAL_REGISTRY.get(deal_id)
    if deal is None:
        return {
            "error": f"deal {deal_id!r} not found",
            "available_deals": list(DEAL_REGISTRY),
        }

    tapes = deal.get("tape_urls", [])
    matches = tapes
    if period is not None:
        matches = [t for t in tapes if period in t["date"]]

    result: dict = {
        "deal_id": deal_id,
        "deal_name": deal["deal_name"],
        "prospectus_url": deal.get("prospectus_url"),
        "investor_report_urls": deal.get("investor_report_urls", []),
        "tape_count": len(matches),
        "tape_urls": matches,
    }
    if period is not None:
        result["period_filter"] = period
        if len(matches) == 1:
            result["selected_url"] = matches[0]["url"]
        elif not matches:
            result["note"] = (
                f"No tape matches period {period!r}. "
                "Note Jan-2026 (2026-01) has no tape in either source repo."
            )
    return result


# Collect all tools for the agent
SF_TOOLS = [
    load_esma_tape,
    run_waterfall,
    check_covenants,
    aggregate_collections,
    get_deal_model,
    list_deal_tapes,
]
SF_TOOL_NODE = ToolNode(SF_TOOLS)


def list_available_tools() -> list[dict]:
    """Return tool descriptions for the planner agent's system prompt."""
    return [{"name": t.name, "description": t.description} for t in SF_TOOLS]
