"""LangGraph tool wrappers for all registered LoanWhiz SF primitives."""

import json

from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode

from loanwhiz.primitives.collections_aggregator import CollectionsAggregator, CollectionsInput
from loanwhiz.primitives.covenant_monitor import CovenantInput, CovenantMonitor
from loanwhiz.primitives.esma_tape_normaliser import EsmaTapeInput, EsmaTapeNormaliser
from loanwhiz.primitives.waterfall_runner import WaterfallInput, WaterfallRunner

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

    Returns pool statistics, weighted averages, arrears breakdown, EPC distribution.
    Use for: understanding pool composition, computing arrears rates, checking EPC mix.
    """
    primitive = EsmaTapeNormaliser()
    result = primitive.execute(EsmaTapeInput(file_url=file_url, reporting_date=reporting_date))
    return result.output.model_dump() | {
        "confidence": result.confidence,
        "citations": [c.model_dump() for c in result.citations],
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
    return result.output.model_dump() | {"confidence": result.confidence}


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
        "confidence": result.confidence
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
    return result.output.model_dump() | {"confidence": result.confidence}


# Collect all tools for the agent
SF_TOOLS = [load_esma_tape, run_waterfall, check_covenants, aggregate_collections]
SF_TOOL_NODE = ToolNode(SF_TOOLS)


def list_available_tools() -> list[dict]:
    """Return tool descriptions for the planner agent's system prompt."""
    return [{"name": t.name, "description": t.description} for t in SF_TOOLS]
