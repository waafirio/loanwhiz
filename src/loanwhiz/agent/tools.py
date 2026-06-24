"""LangGraph tool wrappers for all registered LoanWhiz SF primitives."""

from pathlib import Path

from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode

from loanwhiz.config import DEAL_REGISTRY
from loanwhiz.extraction import collateral_ledger as _collateral_ledger
from loanwhiz.extraction.assembler import (
    DEFAULT_DEAL_CACHE_DIR,
    DealModel,
    _slug,
)
from loanwhiz.extraction.collateral_ledger import CollateralLedger
from loanwhiz.primitives import notes_cash_parser as _notes_cash_parser
from loanwhiz.primitives.audit_logger import audit_result
from loanwhiz.primitives.base import PrimitiveResult
from loanwhiz.primitives.collections_aggregator import CollectionsAggregator, CollectionsInput
from loanwhiz.primitives.covenant_monitor import CovenantInput, CovenantMonitor
from loanwhiz.primitives.esma_tape_normaliser import EsmaTapeInput, EsmaTapeNormaliser
from loanwhiz.primitives.portfolio_monitor import (
    PortfolioMonitor,
    PortfolioMonitorInput,
)
from loanwhiz.primitives.proximity_trend_monitor import (
    ProximityTrendInput,
    ProximityTrendMonitor,
    ProximityTrendOutput,
)

# Audit log dir for primitive calls reached through the agent tools — mirrors
# the REST API's ``API_AUDIT_LOG_DIR`` so the agent path is governed like the
# endpoint path (#277: audit_logger wraps every primitive call). Best-effort
# via ``audit_result`` — a failed audit never takes down the tool call.
AGENT_AUDIT_LOG_DIR = "/tmp/loanwhiz_audit"

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
    distribution, and the ingestion ``data_source`` (always ``"direct"`` — the
    tape is read directly from its source CSV/parquet URL, LoanWhiz's canonical
    tape ingestion path) so the answer's governance evidence records honest
    data provenance.
    Use for: understanding pool composition, computing arrears rates, checking EPC mix.
    """
    primitive = EsmaTapeNormaliser()
    tape_input = EsmaTapeInput(file_url=file_url, reporting_date=reporting_date)
    result = primitive.execute(tape_input)
    audit_result(primitive, tape_input, result, log_dir=AGENT_AUDIT_LOG_DIR)
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


def _bound_projection(payload: dict) -> dict:
    """Bound a ``/project`` payload so multi-period output stays context-cheap (#319).

    The endpoint returns one per-period row per scenario (seed + ``months``
    transitions). For a long horizon (> :data:`MAX_VERBATIM_PERIODS`) that is a
    lot of rows × scenarios for the agent to hold; this collapses each scenario's
    ``periods`` to just the first and last rows and keeps the final-state summary
    + per-tranche WAL the agent actually reasons over, adding a
    ``periods_summarised`` note. Below the threshold the payload is returned
    unchanged (the small Green Lion demo horizons).
    """
    projections = payload.get("projections")
    if not isinstance(projections, dict):
        return payload

    bounded_projections: dict[str, dict] = {}
    summarised = False
    for scenario, proj in projections.items():
        periods = proj.get("periods", [])
        if len(periods) <= MAX_VERBATIM_PERIODS:
            bounded_projections[scenario] = proj
            continue
        summarised = True
        bounded = dict(proj)
        bounded["periods"] = [periods[0], periods[-1]]
        bounded["periods_summarised"] = (
            f"{len(periods)} periods projected; periods shows only the first and "
            f"last. Final-state balances and per-tranche WAL summarise the full "
            f"horizon."
        )
        bounded_projections[scenario] = bounded

    if not summarised:
        return payload
    out = dict(payload)
    out["projections"] = bounded_projections
    return out


@tool
def project_cashflows(
    deal_id: str = DEFAULT_DEAL_ID,
    scenarios: list[str] | None = None,
    months: int = 12,
    cpr_pct: float | None = None,
    cdr_pct: float | None = None,
    recovery_pct: float | None = None,
    rate_shift_bps: float | None = None,
) -> dict:
    """Forward cashflow projection: per-tranche balances, cashflows, and WAL under CPR/CDR/recovery.

    Use for ANY forward-looking / "what if" / projection / WAL / amortisation
    question — "how does Class A amortise", "what's the WAL under a CDR of 5%",
    "project the tranches forward 24 months". Pass only the ``deal_id`` (plus
    optional knobs); the tool seeds the deal's current state and folds a
    CPR/CDR/recovery scenario stream through the SAME engine the live history
    path uses. Do NOT pass pool balances or schedules.

    Parameters
    ----------
    deal_id:
        Registry deal id (defaults to the Green Lion demo deal).
    scenarios:
        Named scenario presets to project (default ``["base", "stress"]``).
    months:
        Projection horizon in months (default 12).
    cpr_pct / cdr_pct / recovery_pct / rate_shift_bps:
        Optional explicit assumptions. When ANY is supplied, they override the
        named presets for EVERY requested scenario (an ad-hoc projection at the
        caller's CPR/CDR/recovery). Omit them to use the presets unchanged.

    Returns per-scenario per-period tranche balances and principal cashflows
    (``class_{a,b,c}_principal_eur``), a final-state summary, and per-tranche WAL
    (A/B/C) in months and years. Over a long horizon (> 6 periods) the per-period
    rows are summarised to first/last to keep the answer context-cheap; the WAL
    and final-state summary still cover the full horizon.
    """
    # Call-time import to reuse the REST /project recipe (projection-base + the
    # one engine fold) without an import-time cycle, mirroring run_waterfall.
    from loanwhiz.api.main import ProjectRequest, deal_project

    if scenarios is None:
        scenarios = ["base", "stress"]

    # When the caller supplies explicit assumptions, apply them to every
    # requested scenario as a per-scenario override (the endpoint merges each
    # present field over the named preset).
    assumptions: dict[str, dict] | None = None
    if any(v is not None for v in (cpr_pct, cdr_pct, recovery_pct, rate_shift_bps)):
        override = {
            "cpr_pct": cpr_pct,
            "cdr_pct": cdr_pct,
            "recovery_pct": recovery_pct,
            "rate_shift_bps": rate_shift_bps,
        }
        assumptions = {scenario: override for scenario in scenarios}

    try:
        req = ProjectRequest(scenarios=scenarios, months=months, assumptions=assumptions)
        payload = deal_project(deal_id, req)
    except Exception as exc:  # noqa: BLE001 — surface a graceful tool error
        # A bad deal_id raises an HTTPException (404/422); other failures are
        # turned into an honest tool error rather than crashing the agent.
        return {
            "error": f"projection failed for deal {deal_id!r}: {exc}",
            "confidence": 0.0,
            "citations": [],
        }

    # Deterministic engine fold → full confidence; the underlying tape reads are
    # cited in the per-period analytics (same posture as run_waterfall).
    return _bound_projection(payload) | {
        "confidence": 1.0,
        "citations": [],
        "duration_ms": 0,
    }


@tool
def stress_matrix(
    deal_id: str = DEFAULT_DEAL_ID,
    cpr_pct: list[float] | None = None,
    cdr_pct: list[float] | None = None,
    rate_shift_bps: list[float] | None = None,
    recovery_pct: float | None = None,
    months: int = 12,
) -> dict:
    """Scenario / stress matrix: a CPR × CDR (× rate-shift) grid of forward projections.

    Use for ANY "stress matrix", "scenario grid", "sensitivity table", or
    "how do loss / WAL / shortfall / breach move across CPR and CDR" question —
    when the analyst wants a *surface* of outcomes rather than one projection.
    Each grid cell is one forward projection through the SAME engine the
    ``project_cashflows`` tool uses; the result is a tranche-level outcome surface
    per ``(cpr, cdr, rate_shift)`` cell: cumulative ``loss``, per-tranche ``wal``
    (A/B/C), total waterfall ``shortfall``, and ``first_breach_period`` /
    ``first_breach_trigger`` (the earliest projected period any covenant trigger
    fires, or ``None`` if none does over the horizon). Pass only the ``deal_id``
    plus the axes; do NOT pass pool balances or schedules.

    Parameters
    ----------
    deal_id:
        Registry deal id (defaults to the Green Lion demo deal).
    cpr_pct / cdr_pct:
        The grid axes — lists of CPR (%) and CDR (%) values. Default to a small
        illustrative grid (``[10, 20]`` × ``[1, 5]``) when omitted.
    rate_shift_bps:
        Optional third axis (bps). Defaults to ``[0.0]`` → a 2-D CPR×CDR grid;
        supply multiple values for a 3-D matrix.
    recovery_pct:
        Recovery on defaults (%), held constant across the grid (the base preset
        when omitted).
    months:
        Projection horizon in months (default 12).

    Returns the echoed ``axes``, grid ``dimensions``, and a flat ``cells`` list a
    client can pivot into a surface. A grid exceeding the cell cap returns a
    graceful error rather than running. Bad ``deal_id`` returns a graceful error.
    """
    # Call-time import to reuse the REST /stress-matrix recipe (the #319
    # projection fold + the covenant engine) without an import-time cycle,
    # mirroring project_cashflows / run_waterfall.
    from loanwhiz.api.main import StressMatrixRequest, deal_stress_matrix

    if cpr_pct is None:
        cpr_pct = [10.0, 20.0]
    if cdr_pct is None:
        cdr_pct = [1.0, 5.0]

    try:
        req = StressMatrixRequest(
            cpr_pct=cpr_pct,
            cdr_pct=cdr_pct,
            rate_shift_bps=rate_shift_bps if rate_shift_bps is not None else [0.0],
            recovery_pct=recovery_pct,
            months=months,
        )
        payload = deal_stress_matrix(deal_id, req)
    except Exception as exc:  # noqa: BLE001 — surface a graceful tool error
        # A bad deal_id raises an HTTPException (404); an oversized / malformed
        # grid raises a 422 — both become an honest tool error rather than a
        # crash the agent can't recover from.
        return {
            "error": f"stress matrix failed for deal {deal_id!r}: {exc}",
            "confidence": 0.0,
            "citations": [],
        }

    # Deterministic engine fold per cell → full confidence (same posture as
    # project_cashflows); the underlying tape reads are cited in the analytics.
    return payload | {"confidence": 1.0, "citations": [], "duration_ms": 0}


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

    deal = DEAL_REGISTRY.get(deal_id)
    if deal is None:
        # Do NOT silently fall back to the default deal — answering covenant
        # questions about the wrong deal is worse than an explicit miss.
        return {
            "error": f"deal {deal_id!r} not found",
            "available_deals": list(DEAL_REGISTRY),
            "confidence": 0.0,
            "citations": [],
        }
    periods = [_normalised_tape_output(tape["url"]) for tape in deal["tape_urls"]]
    triggers = _extracted_triggers_to_definitions(deal) or CovenantMonitor.DEFAULT_TRIGGERS
    series = _reconstruct_series(deal)
    covenant_input = CovenantInput.from_deal_states(
        series.states,
        periods=periods if periods else None,
        triggers=triggers,
    )
    monitor = CovenantMonitor()
    result = monitor.execute(covenant_input)
    audit_result(monitor, covenant_input, result, log_dir=AGENT_AUDIT_LOG_DIR)
    return _bound_covenant_output(result.output.model_dump()) | {
        "confidence": result.confidence,
        "citations": [c.model_dump() for c in result.citations],
        "duration_ms": result.audit_entry.duration_ms,
    }


def _deal_proximity_trend_result(
    deal: dict,
) -> PrimitiveResult[ProximityTrendOutput] | None:
    """Per-deal covenant early-warning result (the forecast chain), or ``None``.

    Loads the deal's tapes, reconstructs each period's structural state, runs the
    covenant monitor over the full reporting series, then fits each trigger's
    proximity-to-breach trend — returning the full
    :class:`PrimitiveResult` (so callers keep the trend monitor's confidence,
    citations, and duration). Returns ``None`` when the deal cannot be evaluated
    offline (no tapes / reconstruction unavailable in this environment) so
    callers can report an honest gap rather than fabricate a forecast. Shared by
    ``forecast_trigger_breaches`` (single deal) and ``monitor_portfolio`` (across
    the registry).
    """
    from loanwhiz.api.main import (
        _extracted_triggers_to_definitions,
        _normalised_tape_output,
        _reconstruct_series,
    )

    try:
        periods = [_normalised_tape_output(tape["url"]) for tape in deal["tape_urls"]]
        triggers = (
            _extracted_triggers_to_definitions(deal)
            or CovenantMonitor.DEFAULT_TRIGGERS
        )
        series = _reconstruct_series(deal)
    except Exception:
        # No cached model / unreachable tapes / reconstruction gap — honest None.
        return None

    covenant_input = CovenantInput.from_deal_states(
        series.states,
        periods=periods if periods else None,
        triggers=triggers,
    )
    covenant_result = CovenantMonitor().execute(covenant_input)
    # The trend monitor analyses the already-evaluated covenant series.
    return ProximityTrendMonitor().execute(
        ProximityTrendInput.from_covenant_output(covenant_result.output)
    )


def _deal_proximity_trend(deal: dict) -> ProximityTrendOutput | None:
    """Per-deal proximity-trend output (the portfolio loader's view), or ``None``.

    Thin wrapper over :func:`_deal_proximity_trend_result` that hands the
    portfolio monitor exactly the :class:`ProximityTrendOutput` it rolls up (the
    monitor attaches its own audit/citations at the portfolio level).
    """
    result = _deal_proximity_trend_result(deal)
    return result.output if result is not None else None


@tool
def forecast_trigger_breaches(deal_id: str = DEFAULT_DEAL_ID) -> dict:
    """Early-warning forecast: project periods-to-breach per covenant trigger, ranked by urgency.

    Use this for FORWARD-LOOKING covenant questions — "which trigger breaches
    first?", "how many periods until the reserve-fund trigger fires?", "is any
    covenant trending toward breach?". Pass only the ``deal_id`` — the tool loads
    the deal's tapes, reconstructs each period's structural state, runs the
    covenant monitor over the full reporting series, then fits each trigger's
    proximity-to-breach trend and projects when (if ever) it crosses the
    threshold. Triggers are returned ranked most-urgent first (already-breached,
    then soonest projected breach, then no-projected-breach).

    This is the trend/projection companion to ``check_covenants`` (which reports
    current per-period status). Do NOT pass tape data.
    """
    deal = DEAL_REGISTRY.get(deal_id)
    if deal is None:
        # Do NOT silently fall back to the default deal (see check_covenants).
        return {
            "error": f"deal {deal_id!r} not found",
            "available_deals": list(DEAL_REGISTRY),
            "confidence": 0.0,
            "citations": [],
        }
    trend_result = _deal_proximity_trend_result(deal)
    if trend_result is None:
        return {
            "deal_id": deal_id,
            "deal_name": deal["deal_name"],
            "forecast_status": "unavailable",
            "note": (
                "The deal's reporting series could not be reconstructed in this "
                "environment (model/tapes not cached), so no early-warning "
                "forecast is available."
            ),
            "confidence": 0.0,
            "citations": [],
        }
    return trend_result.output.model_dump() | {
        "confidence": trend_result.confidence,
        "citations": [c.model_dump() for c in trend_result.citations],
        "duration_ms": trend_result.audit_entry.duration_ms,
    }


@tool
def aggregate_collections(deal_id: str = DEFAULT_DEAL_ID, period: str | None = None) -> dict:
    """Available revenue and principal funds for a reporting period.

    Use for collections / available-funds questions. Pass only the ``deal_id``
    and an optional ``period`` substring (e.g. "2026-03"); the tool selects the
    matching tape (latest by default) and aggregates it itself. Do NOT pass a
    tape URL.
    """
    deal = DEAL_REGISTRY.get(deal_id)
    if deal is None:
        # Do NOT silently fall back to the default deal (see check_covenants).
        return {
            "error": f"deal {deal_id!r} not found",
            "available_deals": list(DEAL_REGISTRY),
            "confidence": 0.0,
            "citations": [],
        }
    tapes = deal.get("tape_urls") or []
    if not tapes:
        return {
            "error": f"No loan tapes published for {deal_id}; collections cannot be aggregated.",
            "confidence": 0.0,
            "citations": [],
        }
    tape = next((t for t in tapes if period and period in t["date"]), tapes[-1])
    aggregator = CollectionsAggregator()
    collections_input = CollectionsInput(tape_file_url=tape["url"], reporting_period=tape["date"])
    result = aggregator.execute(collections_input)
    audit_result(aggregator, collections_input, result, log_dir=AGENT_AUDIT_LOG_DIR)
    return result.output.model_dump() | {
        "confidence": result.confidence,
        "citations": [c.model_dump() for c in result.citations],
        "duration_ms": result.audit_entry.duration_ms,
    }


@tool
def verify_report(
    deal_id: str = DEFAULT_DEAL_ID,
    period: str | None = None,
    tolerance_pct: float = 1.0,
) -> dict:
    """Verify a deal's investor report against the engine-computed distributions.

    Use this for ANY question about whether the servicer applied the waterfall
    correctly, whether the published investor-report figures tie out to the
    deal's computation, or to find line-item "breaks" between the reported and
    computed numbers. Pass only the ``deal_id`` and an optional ``period``
    substring (e.g. "april 2026" or "2026") selecting which monthly investor
    report to check — the tool reconstructs the deal's folded ledger itself and
    diffs the report's Class A interest/principal, pool balance, reserve balance,
    and total collections against the engine output. Do NOT pass a report URL or
    waterfall data.

    Returns per-line-item comparisons (reported vs computed, delta, delta_pct,
    and a match flag at the given ``tolerance_pct``), an ``overall_match`` flag,
    a human-readable ``summary``, and the governance evidence (confidence +
    citations). An unknown ``deal_id`` returns an ``error`` dict listing the
    available deals; a deal with no published investor reports returns an
    ``error`` explaining the gap.
    """
    # Call-time import to reuse the REST /report-verification recipe (the live
    # fold + investor-report selection) without an import-time cycle
    # (api.main -> agent.executor -> agent.tools -> api.main).
    from fastapi import HTTPException

    from loanwhiz.api.main import deal_report_verification

    deal = DEAL_REGISTRY.get(deal_id)
    if deal is None:
        # Do NOT silently fall back to the default deal — answering a
        # verification question about the wrong deal is worse than an explicit
        # miss (mirrors check_covenants / aggregate_collections).
        return {
            "error": f"deal {deal_id!r} not found",
            "available_deals": list(DEAL_REGISTRY),
            "confidence": 0.0,
            "citations": [],
        }

    try:
        resp = deal_report_verification(
            deal_id, period=period, tolerance_pct=tolerance_pct
        )
    except HTTPException as exc:
        # e.g. the deal publishes no investor reports (422) — surface the
        # endpoint's own detail rather than crashing the tool call.
        return {
            "error": str(exc.detail),
            "confidence": 0.0,
            "citations": [],
        }

    data = resp.model_dump() if hasattr(resp, "model_dump") else dict(resp)
    confidence = data.pop("confidence", None)
    citations = data.pop("citations", [])
    return data | {
        "confidence": confidence,
        "citations": citations,
        "duration_ms": 0,
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


# ---------------------------------------------------------------------------
# Investor-report reader — the durable report cache (#402)
# ---------------------------------------------------------------------------


def _read_cached_notes_cash(deal: dict) -> "_notes_cash_parser.NotesCashReport | None":
    """Read the deal's cached Notes & Cash (liability) report set, or ``None``.

    Reads the durable on-disk cache the ingestion layer (#398/#399) writes at
    ``{DEFAULT_EXTRACTION_CACHE_DIR}/notes-cash-{slug(deal_name)}.json`` via the
    same private seam ``parse_notes_cash_report`` uses on a cache hit. **Never
    triggers a cold extraction** — a cache miss (no file) returns ``None`` rather
    than fetching/parsing PDFs over the network. The cache dir is gitignored and
    warmed out-of-band, so a fresh checkout legitimately has no report — hence
    the graceful ``None``. This is the patch point the agent-tool tests stub.
    """
    path = _notes_cash_parser._cache_path(
        deal["deal_name"], _notes_cash_parser.DEFAULT_EXTRACTION_CACHE_DIR
    )
    return _notes_cash_parser._load_durable_cache(path)


def _read_cached_collateral_ledger(deal: dict) -> CollateralLedger | None:
    """Read the deal's cached collateral ledger (monthly investor report), or ``None``.

    Mirrors :func:`_read_cached_notes_cash` for the collateral side: reads the
    durable cache ``{DEFAULT_EXTRACTION_CACHE_DIR}/collateral-ledger-{slug}.json``
    via ``collateral_ledger``'s own private cache seam. **Never triggers a cold
    extraction** (which would fetch each report PDF and call Gemini) — a cache
    miss returns ``None``. Patch point for the tests.
    """
    path = _collateral_ledger._cache_path(
        deal["deal_name"], _collateral_ledger.DEFAULT_EXTRACTION_CACHE_DIR
    )
    return _collateral_ledger._load_durable_cache(path)


def _period_matches(period: str, *candidates: str | None) -> bool:
    """Whether the ``period`` substring matches any of the candidate labels.

    Matched case-insensitively against the ISO reporting date and the
    human-readable period label (e.g. ``"2026-04"`` or ``"april 2026"``),
    mirroring the ``period`` semantics of ``list_deal_tapes`` /
    ``aggregate_collections``.
    """
    needle = period.lower()
    return any(needle in c.lower() for c in candidates if c)


@tool
def read_investor_report(deal_id: str = DEFAULT_DEAL_ID, period: str | None = None) -> dict:
    """Read what a deal's investor / notes-cash reports actually SAID for a period.

    Use this for ANY question about the *contents* of a deal's published reports —
    "what did the investor report say about arrears in Jan 2025?", "what was the
    reserve balance the report published?", "what PDL did the bond report show?",
    "what did the report distribute at each waterfall step?", "what triggers did
    the report mark breached?". This is the *read* companion to ``verify_report``
    (which only *diffs* the report against the engine); use ``read_investor_report``
    when the analyst wants the reported figures themselves, grounded with
    citations to the source report.

    It reads the deal's durable report cache directly and surfaces, per reporting
    period, whichever report families are cached:

    - **Notes & Cash report** (liability ground truth): per-class note/PDL
      balances, the reserve account target/balance, the revenue & redemption
      priority-of-payments steps actually distributed, and trigger states.
    - **Collateral ledger** (the monthly investor report): pool roll-forward
      (begin/end balances, repayments/prepayments), arrears/default amount, life
      CPR/PPR, CDR, payment ratio, and weighted-average coupon.

    Parameters
    ----------
    deal_id:
        Registry deal id (defaults to the Green Lion demo deal).
    period:
        Optional substring matched against each period's ISO reporting date AND
        its human-readable label — ``"2026-04"``, ``"april 2026"``, or ``"2026"``
        all work. Omit to return every reported period.

    Returns one entry per matching reporting period (each with the cached figures
    above + a ``Citation`` to the source report), the deal's ``available_periods``,
    and the governance envelope (``confidence``/``citations``). This tool NEVER
    triggers a live extraction, so it always returns promptly: when no report is
    cached in this environment it returns ``reports_status="not_cached"`` with a
    note (the deal's document URLs remain reachable via ``list_deal_tapes``); when
    a ``period`` filter matches nothing it returns ``available_periods`` and a note
    rather than fabricating data. An unknown ``deal_id`` returns an ``error`` plus
    the list of ``available_deals``.
    """
    deal = DEAL_REGISTRY.get(deal_id)
    if deal is None:
        # Do NOT silently fall back to the default deal — reading the wrong deal's
        # report is worse than an explicit miss (mirrors get_deal_model et al.).
        return {
            "error": f"deal {deal_id!r} not found",
            "available_deals": list(DEAL_REGISTRY),
            "confidence": 0.0,
            "citations": [],
        }

    notes_cash = _read_cached_notes_cash(deal)
    ledger = _read_cached_collateral_ledger(deal)

    if notes_cash is None and ledger is None:
        return {
            "deal_id": deal_id,
            "deal_name": deal["deal_name"],
            "reports_status": "not_cached",
            "note": (
                "No investor report has been extracted for this deal in this "
                "environment, so the reported figures (arrears, PDL, reserve, "
                "priority-of-payments distributions, trigger states, pool "
                "roll-forward) are unavailable. The deal's document URLs "
                "(prospectus, tapes, investor reports) are still available via "
                "list_deal_tapes."
            ),
            "confidence": 0.0,
            "citations": [],
        }

    # Merge whichever report families are cached, keyed by ISO reporting date so
    # the liability (Notes & Cash) and collateral (ledger) sides of the same
    # period land in one entry.
    by_date: dict[str, dict] = {}

    if notes_cash is not None:
        for p in notes_cash.periods:
            by_date.setdefault(
                p.reporting_date,
                {"reporting_date": p.reporting_date, "period_label": p.period_label},
            )["notes_cash"] = p.model_dump()

    if ledger is not None:
        for p in ledger.periods:
            entry = by_date.setdefault(
                p.reporting_date,
                {"reporting_date": p.reporting_date, "period_label": p.period_label},
            )
            entry.setdefault("period_label", p.period_label)
            entry["collateral_ledger"] = p.model_dump()

    all_periods = [by_date[d] for d in sorted(by_date)]
    available_periods = [
        {"reporting_date": e["reporting_date"], "period_label": e.get("period_label")}
        for e in all_periods
    ]

    selected = all_periods
    if period is not None:
        selected = [
            e
            for e in all_periods
            if _period_matches(period, e["reporting_date"], e.get("period_label"))
        ]
        if not selected:
            return {
                "deal_id": deal_id,
                "deal_name": deal["deal_name"],
                "reports_status": "cached",
                "period_filter": period,
                "available_periods": available_periods,
                "note": (
                    f"No reported period matches {period!r}. See available_periods "
                    "for the reporting dates that ARE cached for this deal."
                ),
                "confidence": 1.0,
                "citations": [],
            }

    # One citation per surfaced period — the parse is deterministic (pypdf/regex),
    # so the governance confidence is 1.0 (the framework's rule-based convention).
    citations = [
        {
            "document": f"{deal['deal_name']} — investor report ({e.get('period_label') or e['reporting_date']})",
            "page_or_row": e["reporting_date"],
            "excerpt": (
                "Reported figures read from the deal's durable investor-report "
                "cache (deterministic extraction; no live re-extraction)."
            ),
        }
        for e in selected
    ]

    return {
        "deal_id": deal_id,
        "deal_name": deal["deal_name"],
        "reports_status": "cached",
        "period_filter": period,
        "available_periods": available_periods,
        "periods": selected,
        "confidence": 1.0,
        "citations": citations,
        "duration_ms": 0,
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


@tool
def monitor_portfolio() -> dict:
    """Cross-deal covenant watchlist: which deal is breaching or about to breach first.

    Use this for PORTFOLIO-LEVEL / multi-deal questions — "across all my deals,
    which one needs attention?", "is any deal breaching a covenant?", "rank the
    book by covenant urgency". Takes no arguments: it monitors the whole deal
    registry. For each deal it runs the same early-warning chain as
    ``forecast_trigger_breaches`` (reconstruct the series, run the covenant
    monitor, fit each trigger's proximity-to-breach trend), then rolls every
    deal's projection up into one watchlist ranked most-urgent first
    (already-breached deals, then soonest projected breach, then clear, then
    deals that could not be evaluated offline).

    Returns the watchlist ``rows`` (one per deal: ``watch_status``,
    ``worst_trigger_proximity_pct``, ``most_urgent_trigger``,
    ``periods_to_breach``, ``rank``, and an honest ``evaluable``/``reason``),
    the ``tally``, the ``most_urgent_deal``, and a plain-English ``summary``. A
    deal whose model/tapes are not cached in this environment is reported
    ``watch_status='unavailable'`` with a reason — never a fabricated status.
    This is the cross-deal companion to the single-deal
    ``forecast_trigger_breaches``.
    """
    result = PortfolioMonitor(proximity_loader=_deal_proximity_trend).execute(
        PortfolioMonitorInput(deals=DEAL_REGISTRY)
    )
    return result.output.model_dump() | {
        "confidence": result.confidence,
        "citations": [c.model_dump() for c in result.citations],
        "duration_ms": result.audit_entry.duration_ms,
    }


# ---------------------------------------------------------------------------
# Cross-source synthesis (#403)
# ---------------------------------------------------------------------------
#
# The per-source tools above each answer from ONE source (the prospectus
# deal-model, the loan tape, or the investor report). A question that spans
# structure *and* performance — "does the pool's actual performance still
# justify the prospectus's reserve target?", "reconcile the investor report
# against the deal's own collections" — needs all three woven into one
# grounded answer. Routing such a question to a single tool either ignores the
# other sources or, worse, lets the model fabricate the cross-source link in
# prose. ``synthesise_cross_source`` closes that gap: it gathers each source's
# grounded facts into ONE provenance-tagged bundle so the agent can only
# synthesise over facts that already carry their source + citations, and it
# reports explicitly which sources were available vs. missing — never a silent
# omission, never a fabricated value.

# The three grounding sources the synthesis bundle draws on, in
# senior→performance order (structure first, then how the pool is actually
# doing, then what the servicer reported). Each maps to an existing per-source
# tool whose underlying function this tool composes (``.func`` — the plain
# callable behind the ``@tool`` wrapper), so source access stays DRY and the
# governance posture (honest gaps, no default-deal fallback) is inherited.
_SYNTHESIS_SOURCES = (
    ("deal_model", "prospectus deal-model"),
    ("pool", "loan tape"),
    ("report", "investor report"),
)


def _is_unavailable_block(payload: dict) -> bool:
    """Whether a per-source payload represents an honest gap rather than data.

    A source block is "unavailable" when the underlying tool reported an
    explicit gap rather than analytical content: an ``error`` (e.g. unknown
    deal, no tapes, deal publishes no report) or the deal-model's
    ``extraction_status == "not_cached"`` cache-miss. Used to split the bundle
    into ``sources_available`` / ``sources_missing`` so the agent can degrade
    honestly.
    """
    if "error" in payload:
        return True
    if payload.get("extraction_status") == "not_cached":
        return True
    return False


@tool
def synthesise_cross_source(deal_id: str = DEFAULT_DEAL_ID, period: str | None = None) -> dict:
    """Gather prospectus + loan-tape + investor-report facts into one cited, source-tagged bundle.

    Use this for ANY question that spans MORE THAN ONE source — structure
    *and* performance together. Examples: "does the pool's actual performance
    still justify the prospectus's reserve target?", "reconcile the latest
    investor report against the deal's own collections", "given the arrears
    trend, are the prospectus triggers still appropriate?". For a question that
    a single source answers (just the reserve target → ``get_deal_model``; just
    arrears → the tape tools; just report tie-out → ``verify_report``), prefer
    that single tool — this tool is for the genuinely cross-source case.

    Pass only the ``deal_id`` and an optional ``period`` substring (e.g.
    "2026-03"); the tool fetches each source itself by composing the existing
    per-source tools (``get_deal_model`` for the prospectus deal-model,
    ``aggregate_collections`` for the loan-tape pool facts, ``verify_report``
    for the investor report). It does NOT call an LLM — it only assembles
    grounded, already-cited facts.

    Returns a dict with one block per source, each carrying its ``source``
    label and the underlying tool's own ``citations`` / ``confidence``:

    - ``deal_model`` — prospectus-derived terms (reserve target, triggers,
      tranche structure, definitions), or an explicit ``not_cached`` block when
      the prospectus has not been extracted in this environment.
    - ``pool`` — loan-tape collections / available funds for the period, or an
      explicit ``error`` block when no tape matches.
    - ``report`` — investor-report tie-out (reported vs. computed), or an
      explicit ``error`` block when the deal publishes no report.

    Plus ``sources_available`` / ``sources_missing`` (which blocks carry data
    vs. an honest gap) and a ``synthesis_guidance`` instruction. An unknown
    ``deal_id`` returns the standard ``{"error", "available_deals"}`` shape —
    the tool never silently falls back to the default deal.
    """
    deal = DEAL_REGISTRY.get(deal_id)
    if deal is None:
        # Mirror every other tool: a bad deal_id is an explicit miss, never a
        # silent fall-back to the default deal (answering about the wrong deal
        # is worse than an honest error).
        return {
            "error": f"deal {deal_id!r} not found",
            "available_deals": list(DEAL_REGISTRY),
            "confidence": 0.0,
            "citations": [],
        }

    # Compose the underlying per-source tool functions (the plain callables
    # behind the @tool wrappers). Each already returns governance-tagged output
    # and degrades honestly on a missing source; we only add the ``source``
    # provenance label and split available vs. missing. A per-source crash is
    # caught and turned into an honest error block rather than taking down the
    # whole synthesis — one absent source must not lose the others.
    raw: dict[str, dict] = {}
    raw["deal_model"] = get_deal_model.func(deal_id)
    try:
        raw["pool"] = aggregate_collections.func(deal_id, period)
    except Exception as exc:  # noqa: BLE001 — surface an honest per-source gap
        raw["pool"] = {"error": f"pool/collections unavailable: {exc}", "confidence": 0.0, "citations": []}
    try:
        raw["report"] = verify_report.func(deal_id, period)
    except Exception as exc:  # noqa: BLE001 — surface an honest per-source gap
        raw["report"] = {"error": f"investor report unavailable: {exc}", "confidence": 0.0, "citations": []}

    bundle: dict = {"deal_id": deal_id, "deal_name": deal["deal_name"]}
    if period is not None:
        bundle["period_filter"] = period

    sources_available: list[str] = []
    sources_missing: list[str] = []
    for key, label in _SYNTHESIS_SOURCES:
        payload = raw[key]
        unavailable = _is_unavailable_block(payload)
        block = dict(payload)
        block["source"] = label
        block["available"] = not unavailable
        bundle[key] = block
        (sources_missing if unavailable else sources_available).append(label)

    bundle["sources_available"] = sources_available
    bundle["sources_missing"] = sources_missing
    bundle["synthesis_guidance"] = (
        "Combine these sources into ONE grounded answer. Attribute every claim "
        "to the `source` of the block it came from (e.g. 'per the prospectus "
        "deal-model …', 'the loan tape shows …'). For any source listed in "
        "`sources_missing`, state plainly that it was unavailable and do NOT "
        "infer its content from the other sources — report the gap honestly "
        "rather than fabricating a cross-source conclusion."
    )
    # The bundle's own confidence is the most-conservative of the available
    # sources (mirrors the executor's min-aggregate); 0.0 when nothing is
    # available so an all-missing bundle never reads as confident.
    confidences = [
        c
        for c in (
            raw[key].get("confidence")
            for key, label in _SYNTHESIS_SOURCES
            if label in sources_available
        )
        if isinstance(c, (int, float))
    ]
    bundle["confidence"] = min(confidences) if confidences else 0.0
    # Citations are already carried per-source on each block; the top-level
    # list is the union so a caller threading citations into the evidence pack
    # gets them all.
    bundle["citations"] = [
        c
        for key, _label in _SYNTHESIS_SOURCES
        for c in (raw[key].get("citations") or [])
        if isinstance(c, dict)
    ]
    return bundle


# Collect all tools for the agent
SF_TOOLS = [
    load_esma_tape,
    run_waterfall,
    project_cashflows,
    check_covenants,
    forecast_trigger_breaches,
    aggregate_collections,
    verify_report,
    get_deal_model,
    list_deal_tapes,
    stress_matrix,  # #323 — appended (additive; keeps existing ordering stable)
    monitor_portfolio,  # #326 — appended (additive; keeps existing ordering stable)
    read_investor_report,  # #402 — appended (additive; keeps existing ordering stable)
    synthesise_cross_source,  # #403 — appended (additive; keeps existing ordering stable)
]
SF_TOOL_NODE = ToolNode(SF_TOOLS)


def list_available_tools() -> list[dict]:
    """Return tool descriptions for the planner agent's system prompt."""
    return [{"name": t.name, "description": t.description} for t in SF_TOOLS]
