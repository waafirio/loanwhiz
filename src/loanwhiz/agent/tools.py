"""LangGraph tool wrappers for all registered LoanWhiz SF primitives."""

import json

from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode

from loanwhiz.primitives.collections_aggregator import CollectionsAggregator, CollectionsInput
from loanwhiz.primitives.covenant_monitor import CovenantInput, CovenantMonitor
from loanwhiz.primitives.esma_tape_normaliser import EsmaTapeInput, EsmaTapeNormaliser
from loanwhiz.primitives.waterfall_runner import WaterfallInput, WaterfallRunner


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
    return result.output.model_dump() | {"confidence": result.confidence}


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
