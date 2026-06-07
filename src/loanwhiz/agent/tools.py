"""LangGraph tool wrappers for all registered LoanWhiz SF primitives."""

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
def run_waterfall(deal_id: str = DEFAULT_DEAL_ID, period: str | None = None) -> dict:
    """Execute the deal's payment waterfall (priority of payments) and per-tranche distributions.

    Use for cashflow / distribution / waterfall questions. Pass only the
    ``deal_id``; the tool reconstructs the latest reported period from the deal's
    own ledger and runs the 11-step Revenue + Redemption priority of payments
    itself (``period`` is accepted but the reconstructed waterfall uses the
    latest reported period). Returns the per-step cascade and the per-tranche
    interest / principal / closing balances. Do NOT pass funds or balances.
    """
    # Call-time import to reuse the REST /waterfall recipe (the reconstructed
    # ledger) without an import-time cycle.
    from loanwhiz.api.main import deal_waterfall

    resp = deal_waterfall(deal_id)
    data = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
    # Deterministic reconstruction → full confidence; the underlying tape reads
    # are already cited in the per-period analytics.
    return data | {"confidence": 1.0, "citations": [], "duration_ms": 0}


@tool
def check_covenants(deal_id: str = DEFAULT_DEAL_ID) -> dict:
    """Check covenant compliance for a deal: trigger status, proximity to breach, and any active breaches.

    Use this for ANY question about covenants, triggers, breaches, PDL or
    reserve-fund shortfalls, or compliance. Pass only the ``deal_id`` — the tool
    loads the deal's tapes, reconstructs each reporting period's structural state,
    and runs the covenant monitor against the deal's own extracted triggers
    itself. Do NOT pass tape data.

    Over many periods (> 6) the output is bounded to keep context cheap:
    ``trigger_statuses`` then shows only the latest period, and a
    ``trend_summary`` carries per-trigger min/max/latest proximity and the net
    trend across all periods. ``active_triggers``/``near_miss_triggers``/
    ``summary`` always reflect the latest period.
    """
    # Call-time import: reuse the REST /compliance recipe (reconstructed states
    # + the deal's extracted triggers) without an import-time cycle
    # (api.main -> agent.executor -> agent.tools -> api.main).
    from loanwhiz.api.main import (
        _extracted_triggers_to_definitions,
        _normalised_tape_output,
        _reconstruct_series,
    )

    deal = DEAL_REGISTRY.get(deal_id) or DEAL_REGISTRY[DEFAULT_DEAL_ID]
    periods = [_normalised_tape_output(tape["url"]) for tape in deal["tape_urls"]]
    triggers = _extracted_triggers_to_definitions(deal) or CovenantMonitor.DEFAULT_TRIGGERS
    series = _reconstruct_series(deal)
    covenant_input = CovenantInput.from_deal_states(
        series.states,
        periods=periods if periods else None,
        triggers=triggers,
    )
    result = CovenantMonitor().execute(covenant_input)
    return _bound_covenant_output(result.output.model_dump()) | {
        "confidence": result.confidence,
        "citations": [c.model_dump() for c in result.citations],
        "duration_ms": result.audit_entry.duration_ms,
    }


@tool
def aggregate_collections(deal_id: str = DEFAULT_DEAL_ID, period: str | None = None) -> dict:
    """Available revenue and principal funds for a reporting period.

    Use for collections / available-funds questions. Pass only the ``deal_id``
    and an optional ``period`` substring (e.g. "2026-03"); the tool selects the
    matching tape (latest by default) and aggregates it itself. Do NOT pass a
    tape URL.
    """
    deal = DEAL_REGISTRY.get(deal_id) or DEAL_REGISTRY[DEFAULT_DEAL_ID]
    tapes = deal.get("tape_urls") or []
    if not tapes:
        return {
            "error": f"No loan tapes published for {deal_id}; collections cannot be aggregated.",
            "confidence": 0.0,
            "citations": [],
        }
    tape = next((t for t in tapes if period and period in t["date"]), tapes[-1])
    result = CollectionsAggregator().execute(
        CollectionsInput(tape_file_url=tape["url"], reporting_period=tape["date"])
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
