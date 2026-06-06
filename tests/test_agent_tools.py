"""Unit tests for LangGraph tool wrappers in loanwhiz.agent.tools.

Each tool is tested with mocked primitives so tests are fast and offline.
The SF_TOOLS list and list_available_tools() helper are also verified.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from loanwhiz.agent import SF_TOOL_NODE, SF_TOOLS, list_available_tools
from loanwhiz.agent.tools import (
    aggregate_collections,
    check_covenants,
    get_deal_model,
    list_deal_tapes,
    load_esma_tape,
    run_waterfall,
)
from loanwhiz.primitives.base import AuditEntry, Citation, PrimitiveResult
from loanwhiz.primitives.collections_aggregator import CollectionsOutput
from loanwhiz.primitives.covenant_monitor import CovenantOutput
from loanwhiz.primitives.esma_tape_normaliser import EsmaTapeOutput
from loanwhiz.primitives.waterfall_runner import WaterfallOutput


# ---------------------------------------------------------------------------
# Shared audit fixture
# ---------------------------------------------------------------------------

_FAKE_AUDIT = AuditEntry(
    primitive_name="test",
    version="0.1.0",
    input_hash="a" * 64,
    executed_at="2026-04-30T00:00:00+00:00",
    duration_ms=1.0,
)


# ---------------------------------------------------------------------------
# load_esma_tape
# ---------------------------------------------------------------------------


def _make_esma_output() -> EsmaTapeOutput:
    return EsmaTapeOutput(
        reporting_date="2026-04-30",
        asset_class="RMBS",
        transaction_name="Green Lion 2026-1",
        loan_count=1000,
        pool_balance_eur=1_000_000_000.0,
        pool_stats={"wtd_coupon_pct": 3.62},
        arrears_breakdown={"current_pct": 98.0, "arrears_1_2m_pct": 1.0,
                           "arrears_180d_plus_pct": 0.5, "default_pct": 0.5},
        epc_breakdown={"A": 40.0, "B": 30.0, "C": 30.0},
        rate_type_breakdown={"Fixed": 70.0, "Floating": 30.0},
        property_type_breakdown={"House": 60.0, "Apartment": 40.0},
        geographic_breakdown={"ES30": 50.0, "ES51": 50.0},
        annex_detected="Annex 2 (RMBS)",
    )


def test_load_esma_tape_calls_primitive_and_returns_dict():
    fake_output = _make_esma_output()
    fake_citation = Citation(document="tape.csv", excerpt="test")
    fake_result = PrimitiveResult[EsmaTapeOutput](
        output=fake_output,
        confidence=0.9,
        citations=[fake_citation],
        audit_entry=_FAKE_AUDIT,
    )

    with patch(
        "loanwhiz.agent.tools.EsmaTapeNormaliser.execute", return_value=fake_result
    ) as mock_exec:
        result = load_esma_tape.invoke({"file_url": "https://example.com/tape.csv"})

    mock_exec.assert_called_once()
    call_arg = mock_exec.call_args[0][0]
    assert call_arg.file_url == "https://example.com/tape.csv"
    assert call_arg.reporting_date is None

    assert isinstance(result, dict)
    assert result["confidence"] == 0.9
    assert "citations" in result
    assert result["loan_count"] == 1000
    assert result["pool_balance_eur"] == 1_000_000_000.0


def test_load_esma_tape_passes_reporting_date():
    fake_output = _make_esma_output()
    fake_result = PrimitiveResult[EsmaTapeOutput](
        output=fake_output,
        confidence=0.8,
        citations=[],
        audit_entry=_FAKE_AUDIT,
    )

    with patch(
        "loanwhiz.agent.tools.EsmaTapeNormaliser.execute", return_value=fake_result
    ) as mock_exec:
        load_esma_tape.invoke(
            {"file_url": "https://example.com/tape.csv", "reporting_date": "2026-04-30"}
        )

    call_arg = mock_exec.call_args[0][0]
    assert call_arg.reporting_date == "2026-04-30"


# ---------------------------------------------------------------------------
# run_waterfall
# ---------------------------------------------------------------------------


def _make_waterfall_output() -> WaterfallOutput:
    from loanwhiz.primitives.waterfall_runner import TrancheDistribution, WaterfallStep

    return WaterfallOutput(
        reporting_period="April 2026",
        revenue_waterfall=[
            WaterfallStep(
                priority="(a)",
                recipient="senior_fees",
                amount_available=10_000_000.0,
                amount_distributed=50_000.0,
                shortfall=0.0,
            )
        ],
        redemption_waterfall=[
            WaterfallStep(
                priority="(b)",
                recipient="class_a_principal",
                amount_available=5_000_000.0,
                amount_distributed=5_000_000.0,
                shortfall=0.0,
            )
        ],
        tranche_distributions=[
            TrancheDistribution(
                tranche="class_a",
                interest_received=9_050_000.0,
                principal_received=5_000_000.0,
                total_received=14_050_000.0,
                opening_balance=1_000_000_000.0,
                closing_balance=995_000_000.0,
            )
        ],
        total_distributed=14_100_000.0,
        shortfall=0.0,
    )


def test_run_waterfall_calls_primitive_with_all_params():
    fake_output = _make_waterfall_output()
    fake_result = PrimitiveResult[WaterfallOutput](
        output=fake_output,
        confidence=1.0,
        citations=[],
        audit_entry=_FAKE_AUDIT,
    )

    with patch(
        "loanwhiz.agent.tools.WaterfallRunner.execute", return_value=fake_result
    ) as mock_exec:
        result = run_waterfall.invoke(
            {
                "reporting_period": "April 2026",
                "available_revenue_funds": 10_000_000.0,
                "available_principal_funds": 5_000_000.0,
                "senior_fees": 50_000.0,
                "class_a_balance": 1_000_000_000.0,
                "class_a_rate_pct": 3.62,
                "class_b_balance": 53_100_000.0,
                "class_c_balance": 10_500_000.0,
                "reserve_account_balance": 5_000_000.0,
                "reserve_account_target": 5_000_000.0,
            }
        )

    mock_exec.assert_called_once()
    call_arg = mock_exec.call_args[0][0]
    assert call_arg.reporting_period == "April 2026"
    assert call_arg.available_revenue_funds == 10_000_000.0
    assert call_arg.swap_payment == 0.0  # always zero in the tool
    assert call_arg.class_a_pdl_balance == 0.0  # default
    assert call_arg.class_b_pdl_balance == 0.0  # default

    assert isinstance(result, dict)
    assert result["confidence"] == 1.0
    assert result["reporting_period"] == "April 2026"
    assert result["shortfall"] == 0.0


def test_run_waterfall_passes_pdl_balances():
    fake_output = _make_waterfall_output()
    fake_result = PrimitiveResult[WaterfallOutput](
        output=fake_output,
        confidence=1.0,
        citations=[],
        audit_entry=_FAKE_AUDIT,
    )

    with patch(
        "loanwhiz.agent.tools.WaterfallRunner.execute", return_value=fake_result
    ) as mock_exec:
        run_waterfall.invoke(
            {
                "reporting_period": "April 2026",
                "available_revenue_funds": 10_000_000.0,
                "available_principal_funds": 5_000_000.0,
                "senior_fees": 50_000.0,
                "class_a_balance": 1_000_000_000.0,
                "class_a_rate_pct": 3.62,
                "class_b_balance": 53_100_000.0,
                "class_c_balance": 10_500_000.0,
                "reserve_account_balance": 5_000_000.0,
                "reserve_account_target": 5_000_000.0,
                "class_a_pdl_balance": 100_000.0,
                "class_b_pdl_balance": 50_000.0,
            }
        )

    call_arg = mock_exec.call_args[0][0]
    assert call_arg.class_a_pdl_balance == 100_000.0
    assert call_arg.class_b_pdl_balance == 50_000.0


# ---------------------------------------------------------------------------
# check_covenants
# ---------------------------------------------------------------------------


def _make_covenant_output() -> CovenantOutput:
    from loanwhiz.primitives.covenant_monitor import TriggerStatus

    return CovenantOutput(
        trigger_statuses=[
            TriggerStatus(
                trigger_name="cumulative_loss_trigger",
                period="2026-04-30",
                metric_value=0.5,
                threshold=1.5,
                is_triggered=False,
                proximity_pct=33.33,
                direction="stable",
            )
        ],
        active_triggers=[],
        near_miss_triggers=[],
        summary="All 5 covenant triggers are within compliance.",
    )


def test_check_covenants_deserializes_periods_json():
    fake_output = _make_covenant_output()
    fake_result = PrimitiveResult[CovenantOutput](
        output=fake_output,
        confidence=1.0,
        citations=[],
        audit_entry=_FAKE_AUDIT,
    )

    periods = [{"reporting_date": "2026-04-30", "pool_balance_eur": 1_000_000_000.0,
                "arrears_breakdown": {"default_pct": 0.5}}]
    periods_json = json.dumps(periods)

    with patch(
        "loanwhiz.agent.tools.CovenantMonitor.execute", return_value=fake_result
    ) as mock_exec:
        result = check_covenants.invoke({"periods_json": periods_json})

    mock_exec.assert_called_once()
    call_arg = mock_exec.call_args[0][0]
    assert call_arg.periods == periods
    assert call_arg.class_a_pdl_balance == 0.0
    assert call_arg.original_pool_balance == 1_063_600_000.0  # default

    assert isinstance(result, dict)
    assert result["confidence"] == 1.0
    assert result["active_triggers"] == []


def _make_multi_period_covenant_output(n_periods: int) -> CovenantOutput:
    """Synthetic covenant output spanning ``n_periods`` periods × 2 triggers.

    Mimics what ``CovenantMonitor`` returns: one ``TriggerStatus`` row per
    trigger per period. Proximity climbs over time on the loss trigger (a
    deteriorating trend) and holds flat on the reserve trigger, so the
    trend_summary has something real to surface.
    """
    from loanwhiz.primitives.covenant_monitor import TriggerStatus

    statuses = []
    for i in range(n_periods):
        year = 2024 + (i // 12)
        period = f"{year}-{(i % 12) + 1:02d}-28"
        # loss trigger: proximity rises 10 -> ~80 across the history
        loss_prox = 10.0 + (70.0 * i / max(n_periods - 1, 1))
        statuses.append(
            TriggerStatus(
                trigger_name="cumulative_loss_trigger",
                period=period,
                metric_value=round(loss_prox / 100.0 * 1.5, 4),
                threshold=1.5,
                is_triggered=loss_prox > 100.0,
                proximity_pct=round(loss_prox, 4),
                direction="deteriorating" if i else "n/a",
            )
        )
        statuses.append(
            TriggerStatus(
                trigger_name="reserve_fund_trigger",
                period=period,
                metric_value=100.0,
                threshold=100.0,
                is_triggered=False,
                proximity_pct=100.0,
                direction="stable" if i else "n/a",
            )
        )
    return CovenantOutput(
        trigger_statuses=statuses,
        active_triggers=[],
        near_miss_triggers=[],
        summary=f"All triggers within compliance across {n_periods} periods.",
    )


def _covenant_result(output: CovenantOutput) -> PrimitiveResult[CovenantOutput]:
    return PrimitiveResult[CovenantOutput](
        output=output,
        confidence=1.0,
        citations=[],
        audit_entry=_FAKE_AUDIT,
    )


def test_check_covenants_bounds_many_periods():
    """Over many periods the wrapper must NOT return all N×M status rows.

    With 48 periods × 2 triggers the raw primitive output carries 96 rows; the
    bounded payload must surface only the latest period plus a trend summary.
    """
    fake_output = _make_multi_period_covenant_output(48)
    assert len(fake_output.trigger_statuses) == 96  # sanity: primitive is verbose

    with patch(
        "loanwhiz.agent.tools.CovenantMonitor.execute",
        return_value=_covenant_result(fake_output),
    ):
        result = check_covenants.invoke({"periods_json": "[]"})

    # Only the latest period's rows survive in trigger_statuses (2 triggers).
    assert len(result["trigger_statuses"]) == 2
    latest_periods = {s["period"] for s in result["trigger_statuses"]}
    assert latest_periods == {"2027-12-28"}  # period index 47 -> 2027-12

    # A computed trend summary is present, one entry per trigger.
    assert "trend_summary" in result
    names = {t["trigger_name"] for t in result["trend_summary"]}
    assert names == {"cumulative_loss_trigger", "reserve_fund_trigger"}

    # The loss trigger's trend is surfaced: min < max and net deteriorating.
    loss = next(
        t for t in result["trend_summary"] if t["trigger_name"] == "cumulative_loss_trigger"
    )
    assert loss["min_proximity_pct"] < loss["max_proximity_pct"]
    assert loss["latest_proximity_pct"] > loss["first_proximity_pct"]
    assert loss["net_trend"] == "deteriorating"
    assert loss["ever_triggered"] is False

    # An explicit note tells the agent the data was summarised.
    assert "periods_summarised" in result
    assert "48 periods" in result["periods_summarised"]

    # Latest-period aggregates and confidence still pass through unchanged.
    assert result["active_triggers"] == []
    assert result["near_miss_triggers"] == []
    assert result["confidence"] == 1.0


def test_check_covenants_preserves_small_period_payload():
    """At/below the threshold the payload is returned verbatim (Green Lion)."""
    fake_output = _make_multi_period_covenant_output(3)
    raw_row_count = len(fake_output.trigger_statuses)  # 3 periods × 2 triggers = 6

    with patch(
        "loanwhiz.agent.tools.CovenantMonitor.execute",
        return_value=_covenant_result(fake_output),
    ):
        result = check_covenants.invoke({"periods_json": "[]"})

    # All rows preserved; no summarisation artefacts added.
    assert len(result["trigger_statuses"]) == raw_row_count
    assert "trend_summary" not in result
    assert "periods_summarised" not in result
    assert result["confidence"] == 1.0


def test_check_covenants_passes_scalar_overrides():
    fake_output = _make_covenant_output()
    fake_result = PrimitiveResult[CovenantOutput](
        output=fake_output,
        confidence=1.0,
        citations=[],
        audit_entry=_FAKE_AUDIT,
    )

    with patch(
        "loanwhiz.agent.tools.CovenantMonitor.execute", return_value=fake_result
    ) as mock_exec:
        check_covenants.invoke(
            {
                "periods_json": "[]",
                "class_a_pdl_balance": 100_000.0,
                "class_b_pdl_balance": 50_000.0,
                "reserve_account_balance": 5_000_000.0,
                "reserve_account_target": 5_500_000.0,
                "original_pool_balance": 1_100_000_000.0,
            }
        )

    call_arg = mock_exec.call_args[0][0]
    assert call_arg.class_a_pdl_balance == 100_000.0
    assert call_arg.class_b_pdl_balance == 50_000.0
    assert call_arg.reserve_account_balance == 5_000_000.0
    assert call_arg.reserve_account_target == 5_500_000.0
    assert call_arg.original_pool_balance == 1_100_000_000.0


# ---------------------------------------------------------------------------
# aggregate_collections
# ---------------------------------------------------------------------------


def _make_collections_output() -> CollectionsOutput:
    return CollectionsOutput(
        reporting_period="April 2026",
        interest_collected=9_050_000.0,
        swap_receipts=0.0,
        available_revenue_funds=9_050_000.0,
        scheduled_principal=5_000_000.0,
        unscheduled_principal=0.0,
        recoveries=0.0,
        available_principal_funds=5_000_000.0,
        pool_balance_eur=1_000_000_000.0,
        loan_count=1000,
        class_a_interest_due=9_050_000.0,
        senior_fees=50_000.0,
        summary="€9.05m revenue, €5.00m principal collected (April 2026)",
    )


def test_aggregate_collections_calls_primitive():
    fake_output = _make_collections_output()
    fake_result = PrimitiveResult[CollectionsOutput](
        output=fake_output,
        confidence=0.8,
        citations=[],
        audit_entry=_FAKE_AUDIT,
    )

    with patch(
        "loanwhiz.agent.tools.CollectionsAggregator.execute", return_value=fake_result
    ) as mock_exec:
        result = aggregate_collections.invoke(
            {
                "tape_file_url": "https://example.com/tape.csv",
                "reporting_period": "April 2026",
            }
        )

    mock_exec.assert_called_once()
    call_arg = mock_exec.call_args[0][0]
    assert call_arg.tape_file_url == "https://example.com/tape.csv"
    assert call_arg.reporting_period == "April 2026"
    assert call_arg.prev_pool_balance is None  # default
    assert call_arg.class_a_balance == 1_000_000_000.0  # default
    assert call_arg.class_a_rate_pct == 3.62  # default

    assert isinstance(result, dict)
    assert result["confidence"] == 0.8
    assert result["available_revenue_funds"] == 9_050_000.0
    assert result["available_principal_funds"] == 5_000_000.0


def test_aggregate_collections_passes_prev_pool_balance():
    fake_output = _make_collections_output()
    fake_result = PrimitiveResult[CollectionsOutput](
        output=fake_output,
        confidence=0.8,
        citations=[],
        audit_entry=_FAKE_AUDIT,
    )

    with patch(
        "loanwhiz.agent.tools.CollectionsAggregator.execute", return_value=fake_result
    ) as mock_exec:
        aggregate_collections.invoke(
            {
                "tape_file_url": "https://example.com/tape.csv",
                "reporting_period": "April 2026",
                "prev_pool_balance": 1_005_000_000.0,
                "class_a_balance": 990_000_000.0,
                "class_a_rate_pct": 3.75,
            }
        )

    call_arg = mock_exec.call_args[0][0]
    assert call_arg.prev_pool_balance == 1_005_000_000.0
    assert call_arg.class_a_balance == 990_000_000.0
    assert call_arg.class_a_rate_pct == 3.75


# ---------------------------------------------------------------------------
# get_deal_model — reads the cached extracted DealModel (deal grounding)
# ---------------------------------------------------------------------------


def _make_deal_model() -> "object":
    """Build a minimal valid DealModel for the cache-hit path."""
    from loanwhiz.extraction.assembler import DealModel, DealModelMetadata

    return DealModel(
        metadata=DealModelMetadata(
            deal_name="Green Lion 2026-1 B.V.",
            prospectus_url="https://example.com/prospectus.pdf",
            extracted_at="2026-04-30T00:00:00+00:00",
            extraction_duration_sec=12.3,
            sections_found=["definitions", "revenue_priority_of_payments"],
            completeness_score=0.75,
            cache_path="/tmp/green-lion.json",
        ),
        definitions={"Reserve Account Target": {"definition": "1.5% of balance",
                                                "page_or_section": "§4"}},
        waterfalls={"revenue": {"steps": []}},
        covenants={"triggers": [{"name": "cumulative_loss_trigger"}]},
        tranche_structure=[{"name": "Class A", "size_eur": 1_000_000_000.0,
                            "rating": "AAA", "rate": "3.62%", "seniority": 1}],
        trigger_names=["cumulative_loss_trigger"],
    )


def test_get_deal_model_cache_hit_returns_extracted_fields():
    """On a cache hit the tool surfaces tranches/triggers/waterfalls/definitions."""
    from loanwhiz.agent import tools as tools_mod

    fake = _make_deal_model()
    with patch.object(tools_mod, "_read_cached_deal_model", return_value=fake):
        result = get_deal_model.invoke({"deal_id": "green-lion-2026-1"})

    assert result["extraction_status"] == "cached"
    assert result["deal_name"] == "Green Lion 2026-1 B.V."
    assert result["completeness_score"] == 0.75
    assert result["trigger_names"] == ["cumulative_loss_trigger"]
    assert result["tranche_structure"][0]["name"] == "Class A"
    assert "Reserve Account Target" in result["definitions"]
    assert result["covenants"]["triggers"][0]["name"] == "cumulative_loss_trigger"
    assert "revenue" in result["waterfalls"]


def test_get_deal_model_cache_miss_degrades_without_extraction():
    """A cache miss returns a not_cached payload and NEVER triggers extraction."""
    from loanwhiz.agent import tools as tools_mod

    with patch.object(tools_mod, "_read_cached_deal_model", return_value=None) as mock_read, \
         patch("loanwhiz.extraction.assembler.extract_deal_model") as mock_extract:
        result = get_deal_model.invoke({"deal_id": "green-lion-2026-1"})

    mock_read.assert_called_once()
    mock_extract.assert_not_called()  # cold extraction must never run
    assert result["extraction_status"] == "not_cached"
    assert result["deal_id"] == "green-lion-2026-1"
    assert "note" in result
    assert "list_deal_tapes" in result["note"]


def test_get_deal_model_unknown_deal_returns_error():
    result = get_deal_model.invoke({"deal_id": "no-such-deal"})
    assert "error" in result
    assert "green-lion-2026-1" in result["available_deals"]


def test_get_deal_model_defaults_to_green_lion():
    """Called with no deal_id, the tool resolves the default deal."""
    from loanwhiz.agent import tools as tools_mod

    with patch.object(tools_mod, "_read_cached_deal_model", return_value=None):
        result = get_deal_model.invoke({})
    assert result["deal_id"] == "green-lion-2026-1"


# ---------------------------------------------------------------------------
# list_deal_tapes — tape registry access + deal/period selection
# ---------------------------------------------------------------------------


def test_list_deal_tapes_returns_the_three_2026_tapes_for_default_deal():
    """The agent can see all of Green Lion 2026-1's tapes via the registry."""
    result = list_deal_tapes.invoke({})
    assert result["deal_id"] == "green-lion-2026-1"
    assert result["tape_count"] == 3
    assert len(result["tape_urls"]) == 3
    dates = {t["date"] for t in result["tape_urls"]}
    assert dates == {"2026-02-28", "2026-03-31", "2026-04-30"}
    assert "prospectus_url" in result
    assert "investor_report_urls" in result


def test_list_deal_tapes_period_substring_selects_one_month():
    result = list_deal_tapes.invoke({"period": "2026-03"})
    assert result["tape_count"] == 1
    assert result["period_filter"] == "2026-03"
    # Exactly one match → a directly-usable selected_url.
    assert "selected_url" in result
    assert result["tape_urls"][0]["date"] == "2026-03-31"
    assert result["selected_url"] == result["tape_urls"][0]["url"]


def test_list_deal_tapes_period_year_selects_all_of_2026():
    result = list_deal_tapes.invoke({"period": "2026"})
    assert result["tape_count"] == 3
    # Ambiguous (>1 match) → no selected_url.
    assert "selected_url" not in result


def test_list_deal_tapes_missing_period_notes_jan_2026_gap():
    result = list_deal_tapes.invoke({"period": "2026-01"})
    assert result["tape_count"] == 0
    assert "selected_url" not in result
    assert "note" in result


def test_list_deal_tapes_unknown_deal_returns_error():
    from loanwhiz.config import DEAL_REGISTRY

    result = list_deal_tapes.invoke({"deal_id": "no-such-deal"})
    assert "error" in result
    # Tracks the registry (now includes the seasoned deals), not a hardcoded list.
    assert set(result["available_deals"]) == set(DEAL_REGISTRY)


# ---------------------------------------------------------------------------
# SF_TOOLS membership and structure
# ---------------------------------------------------------------------------


def test_sf_tools_has_expected_tools():
    tool_names = [t.name for t in SF_TOOLS]
    assert tool_names == [
        "load_esma_tape",
        "run_waterfall",
        "check_covenants",
        "aggregate_collections",
        "get_deal_model",
        "list_deal_tapes",
    ]


def test_sf_tools_has_exactly_six_tools():
    assert len(SF_TOOLS) == 6


def test_sf_tool_node_is_tool_node_instance():
    from langgraph.prebuilt import ToolNode

    assert isinstance(SF_TOOL_NODE, ToolNode)


def test_sf_tools_all_have_non_empty_descriptions():
    for t in SF_TOOLS:
        assert t.description, f"Tool {t.name!r} has empty description"


def test_sf_tools_all_have_invoke_method():
    """LangGraph StructuredTool exposes .invoke() — verify each tool has it."""
    for t in SF_TOOLS:
        assert hasattr(t, "invoke"), f"Tool {t.name!r} has no .invoke() method"


# ---------------------------------------------------------------------------
# list_available_tools
# ---------------------------------------------------------------------------


def test_list_available_tools_returns_list_of_dicts():
    result = list_available_tools()
    assert isinstance(result, list)
    assert len(result) == 6


def test_list_available_tools_has_name_and_description():
    result = list_available_tools()
    for entry in result:
        assert "name" in entry, f"Missing 'name' key in {entry}"
        assert "description" in entry, f"Missing 'description' key in {entry}"
        assert isinstance(entry["name"], str)
        assert isinstance(entry["description"], str)
        assert entry["name"], "name must be non-empty"
        assert entry["description"], "description must be non-empty"


def test_list_available_tools_names_match_sf_tools():
    tool_names = [t.name for t in SF_TOOLS]
    listed_names = [e["name"] for e in list_available_tools()]
    assert listed_names == tool_names


# ---------------------------------------------------------------------------
# Tool schema validity (LangGraph tool format)
# ---------------------------------------------------------------------------


def test_tools_have_valid_schemas():
    """Each tool must expose a JSON-schema-compatible args_schema."""
    for t in SF_TOOLS:
        schema = t.get_input_schema()
        assert schema is not None, f"Tool {t.name!r} has no input schema"
        # Pydantic model — check it can produce a JSON schema dict.
        json_schema = schema.model_json_schema()
        assert isinstance(json_schema, dict)
        assert "properties" in json_schema or "type" in json_schema, (
            f"Tool {t.name!r} schema is malformed: {json_schema}"
        )


# ---------------------------------------------------------------------------
# Agent __init__ re-exports
# ---------------------------------------------------------------------------


def test_agent_init_exports():
    """Verify all three public names are importable from loanwhiz.agent."""
    from loanwhiz.agent import SF_TOOL_NODE, SF_TOOLS, list_available_tools

    assert SF_TOOLS is not None
    assert SF_TOOL_NODE is not None
    assert callable(list_available_tools)


def test_grounding_tools_importable_from_tools_module():
    """The new grounding tools are importable from loanwhiz.agent.tools."""
    from loanwhiz.agent.tools import get_deal_model, list_deal_tapes

    assert get_deal_model is not None
    assert list_deal_tapes is not None


# ---------------------------------------------------------------------------
# System prompt grounding (the regrounded planner prompt)
# ---------------------------------------------------------------------------


def test_system_prompt_is_regrounded():
    """The prompt must name the new tools and drop the 3 hardcoded tape URLs."""
    from loanwhiz.agent.planner import SYSTEM_PROMPT

    # New grounding tools are documented.
    assert "get_deal_model" in SYSTEM_PROMPT
    assert "list_deal_tapes" in SYSTEM_PROMPT
    # The previously-hardcoded Feb/Mar/Apr-2026 tape CSV URLs are gone — the
    # agent must resolve tapes via list_deal_tapes, not memorised URLs.
    assert "green_lion_202602_1_synthetic_loan_tape.csv" not in SYSTEM_PROMPT
    assert "green_lion_202603_1_synthetic_loan_tape.csv" not in SYSTEM_PROMPT
    assert "green_lion_2026_1_synthetic_loan_tape.csv" not in SYSTEM_PROMPT
    # The agent is told the deal reports its three 2026 monthly tapes (and to
    # resolve them via list_deal_tapes rather than hardcoded URLs).
    assert "Feb/Mar/Apr" in SYSTEM_PROMPT
