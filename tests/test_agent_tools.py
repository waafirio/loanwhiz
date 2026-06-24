"""Unit tests for LangGraph tool wrappers in loanwhiz.agent.tools.

Each tool is tested with mocked primitives so tests are fast and offline.
The SF_TOOLS list and list_available_tools() helper are also verified.
"""

from __future__ import annotations

import types
from contextlib import ExitStack
from unittest.mock import patch

from loanwhiz.agent import SF_TOOL_NODE, SF_TOOLS, list_available_tools
from loanwhiz.agent.tools import (
    DEFAULT_DEAL_ID,
    aggregate_collections,
    check_covenants,
    forecast_trigger_breaches,
    get_deal_model,
    list_deal_tapes,
    load_esma_tape,
    project_cashflows,
    run_waterfall,
    stress_matrix,
    synthesise_cross_source,
    verify_report,
)
from loanwhiz.config import DEAL_REGISTRY
from loanwhiz.primitives.base import AuditEntry, Citation, PrimitiveResult
from loanwhiz.primitives.collections_aggregator import CollectionsOutput
from loanwhiz.primitives.covenant_monitor import CovenantOutput
from loanwhiz.primitives.esma_tape_normaliser import EsmaTapeOutput


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
        data_source="direct",
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
# Audit promotion (#277) — agent-tool primitive calls write an AuditLogEntry
#
# The two direct-execute tools (load_esma_tape, aggregate_collections) route
# their primitive call through audit_result(), so the agent path is governed
# like the REST path. These tests assert an AuditLogEntry actually lands.
# ---------------------------------------------------------------------------


def _audit_entries_under(log_dir) -> int:
    from pathlib import Path

    return sum(
        len([ln for ln in p.read_text().splitlines() if ln.strip()])
        for p in Path(log_dir).rglob("*.jsonl")
    )


def test_load_esma_tape_writes_audit_entry(tmp_path, monkeypatch):
    fake_output = _make_esma_output()
    fake_result = PrimitiveResult[EsmaTapeOutput](
        output=fake_output,
        confidence=0.9,
        citations=[Citation(document="tape.csv", excerpt="test")],
        audit_entry=_FAKE_AUDIT,
    )
    log_dir = tmp_path / "audit"
    monkeypatch.setattr("loanwhiz.agent.tools.AGENT_AUDIT_LOG_DIR", str(log_dir))

    with patch(
        "loanwhiz.agent.tools.EsmaTapeNormaliser.execute", return_value=fake_result
    ):
        result = load_esma_tape.invoke({"file_url": "https://example.com/tape.csv"})

    # Tool output unchanged by the audit wiring.
    assert result["confidence"] == 0.9
    # One AuditLogEntry was written for the call.
    assert _audit_entries_under(log_dir) == 1


def test_aggregate_collections_writes_audit_entry(tmp_path, monkeypatch):
    log_dir = tmp_path / "audit"
    monkeypatch.setattr("loanwhiz.agent.tools.AGENT_AUDIT_LOG_DIR", str(log_dir))

    with patch(
        "loanwhiz.agent.tools.CollectionsAggregator.execute",
        return_value=_collections_result(),
    ):
        result = aggregate_collections.invoke({"deal_id": "green-lion-2026-1"})

    assert result["confidence"] == 0.8
    assert _audit_entries_under(log_dir) == 1


def test_check_covenants_writes_audit_entry(tmp_path, monkeypatch):
    """check_covenants now routes its direct CovenantMonitor.execute() through
    audit_result() — closing the #277 envelope gap so a covenant check reached
    through the agent leaves a durable provenance record, like the REST path."""
    log_dir = tmp_path / "audit"
    monkeypatch.setattr("loanwhiz.agent.tools.AGENT_AUDIT_LOG_DIR", str(log_dir))

    with ExitStack() as stack:
        _patch_covenant_deps(stack, _covenant_result(_make_covenant_output()))
        result = check_covenants.invoke({"deal_id": "green-lion-2026-1"})

    # Tool output unchanged by the audit wiring.
    assert result["confidence"] == 1.0
    # Exactly one AuditLogEntry was persisted for the covenant call.
    assert _audit_entries_under(log_dir) == 1


# ---------------------------------------------------------------------------
# run_waterfall
# ---------------------------------------------------------------------------


# The deal_id-based ``run_waterfall`` delegates to the REST recipe
# (``loanwhiz.api.main.deal_waterfall``) which reconstructs the latest period
# from the deal's own ledger — it no longer takes funds/balances directly.

_FAKE_WATERFALL_RESPONSE = {
    "deal_id": "green-lion-2026-1",
    "reporting_period": "2026-04-30",
    "available_revenue_funds": 10_000_000.0,
    "available_principal_funds": 5_000_000.0,
    "revenue_waterfall": [
        {"priority": "(d)", "recipient": "class_a_interest",
         "amount_available": 10_000_000.0, "amount_distributed": 9_050_000.0,
         "shortfall": 0.0},
    ],
    "tranche_distributions": [
        {"tranche": "class_a", "interest_received": 9_050_000.0,
         "principal_received": 5_000_000.0, "closing_balance": 995_000_000.0},
    ],
    "shortfall": 0.0,
}


def test_run_waterfall_delegates_to_deal_recipe():
    """The tool delegates to the deal's reconstructed-ledger waterfall recipe
    and stamps full confidence on the deterministic result."""
    with patch(
        "loanwhiz.api.main.deal_waterfall",
        return_value=dict(_FAKE_WATERFALL_RESPONSE),
    ) as mock_recipe:
        result = run_waterfall.invoke({"deal_id": "green-lion-2026-1"})

    mock_recipe.assert_called_once_with("green-lion-2026-1")
    assert isinstance(result, dict)
    assert result["confidence"] == 1.0  # deterministic reconstruction
    assert result["reporting_period"] == "2026-04-30"
    assert result["shortfall"] == 0.0
    assert result["tranche_distributions"][0]["tranche"] == "class_a"


def test_run_waterfall_defaults_to_demo_deal():
    """With no deal_id the tool serves the default demo deal."""
    with patch(
        "loanwhiz.api.main.deal_waterfall",
        return_value=dict(_FAKE_WATERFALL_RESPONSE),
    ) as mock_recipe:
        run_waterfall.invoke({})

    mock_recipe.assert_called_once_with(DEFAULT_DEAL_ID)


# ---------------------------------------------------------------------------
# project_cashflows (#319)
#
# These exercise the REAL engine fold end to end (the tool wraps the live
# /project recipe over the Green Lion deal — no mock of the unit under test),
# matching the planner's `integration` test-level contract for the projection
# path.
# ---------------------------------------------------------------------------


def test_project_cashflows_returns_per_tranche_cashflows_and_wal():
    """The tool returns per-scenario per-period tranche balances + principal
    cashflows + per-tranche WAL, with full confidence on the deterministic fold."""
    result = project_cashflows.invoke(
        {"deal_id": "green-lion-2026-1", "scenarios": ["base"], "months": 6}
    )
    assert result["confidence"] == 1.0
    assert "error" not in result
    proj = result["projections"]["base"]
    # Per-tranche principal cashflows present on each period (#319).
    first_transition = proj["periods"][1]
    for key in ("class_a_principal_eur", "class_b_principal_eur", "class_c_principal_eur"):
        assert key in first_transition
    # Per-tranche WAL present (#319) — A from #275, B/C added here.
    wal = result["wal"]["base"]
    for key in (
        "wal_class_a_months",
        "wal_class_b_months",
        "wal_class_c_months",
    ):
        assert key in wal


def test_project_cashflows_applies_custom_assumptions():
    """Explicit CPR/CDR/recovery override the presets for every scenario — a
    higher CDR / lower recovery yields strictly larger cumulative losses."""
    base = project_cashflows.invoke(
        {"deal_id": "green-lion-2026-1", "scenarios": ["base"], "months": 6}
    )
    stressed = project_cashflows.invoke(
        {
            "deal_id": "green-lion-2026-1",
            "scenarios": ["base"],
            "months": 6,
            "cdr_pct": 8.0,
            "recovery_pct": 20.0,
        }
    )
    base_losses = base["projections"]["base"]["cumulative_losses"]
    stressed_losses = stressed["projections"]["base"]["cumulative_losses"]
    assert stressed_losses > base_losses


def test_project_cashflows_bounds_long_horizon():
    """A horizon beyond MAX_VERBATIM_PERIODS collapses per-period rows to
    first/last while keeping the final-state + WAL summary."""
    result = project_cashflows.invoke(
        {"deal_id": "green-lion-2026-1", "scenarios": ["base"], "months": 24}
    )
    proj = result["projections"]["base"]
    assert len(proj["periods"]) == 2  # first + last only
    assert "periods_summarised" in proj
    # Final-state + WAL still cover the full horizon.
    assert "final_class_a_balance" in proj
    assert "wal_class_a_months" in proj


def test_project_cashflows_short_horizon_verbatim():
    """At/under the bound the per-period rows are returned verbatim."""
    result = project_cashflows.invoke(
        {"deal_id": "green-lion-2026-1", "scenarios": ["base"], "months": 4}
    )
    proj = result["projections"]["base"]
    assert len(proj["periods"]) == 5  # seed + 4 transitions, not summarised
    assert "periods_summarised" not in proj


def test_project_cashflows_unknown_deal_errors():
    """A bad deal id returns a graceful tool error, not a crash."""
    result = project_cashflows.invoke({"deal_id": "no-such-deal"})
    assert result["confidence"] == 0.0
    assert "error" in result


def test_project_cashflows_defaults_to_demo_deal():
    """With no deal_id the tool serves the default demo deal and both presets."""
    result = project_cashflows.invoke({})
    assert "error" not in result
    assert set(result["projections"]) == {"base", "stress"}


# ---------------------------------------------------------------------------
# stress_matrix (#323)
#
# These exercise the REAL grid fold end to end (the tool wraps the live
# /stress-matrix recipe over the Green Lion deal — no mock of the unit under
# test), matching the planner's `integration` test-level contract.
# ---------------------------------------------------------------------------


def test_stress_matrix_returns_outcome_surface():
    """The tool returns a tranche-level outcome surface (loss/wal/shortfall/
    first-breach) per cell, with full confidence on the deterministic fold."""
    result = stress_matrix.invoke(
        {
            "deal_id": "green-lion-2026-1",
            "cpr_pct": [10, 20],
            "cdr_pct": [1, 5],
            "months": 6,
        }
    )
    assert result["confidence"] == 1.0
    assert "error" not in result
    assert result["dimensions"]["cells"] == 4
    assert len(result["cells"]) == 4
    for cell in result["cells"]:
        assert {"loss", "wal", "shortfall", "first_breach_period"} <= set(cell)
        assert {"wal_class_a_months", "wal_class_b_months", "wal_class_c_months"} <= set(
            cell["wal"]
        )


def test_stress_matrix_defaults_to_demo_grid():
    """With no axes the tool serves the default demo grid over the default deal."""
    result = stress_matrix.invoke({})
    assert "error" not in result
    # Default grid is 2 CPR × 2 CDR × 1 rate-shift = 4 cells.
    assert result["dimensions"]["cells"] == 4


def test_stress_matrix_unknown_deal_errors():
    """A bad deal id returns a graceful tool error, not a crash."""
    result = stress_matrix.invoke(
        {"deal_id": "no-such-deal", "cpr_pct": [10], "cdr_pct": [1]}
    )
    assert result["confidence"] == 0.0
    assert "error" in result


def test_stress_matrix_oversized_grid_errors_gracefully():
    """An oversized grid (> cell cap) surfaces a graceful error, not a hang."""
    big = [float(x) for x in range(9)]  # 9 × 9 = 81 > 64
    result = stress_matrix.invoke(
        {"deal_id": "green-lion-2026-1", "cpr_pct": big, "cdr_pct": big, "months": 3}
    )
    assert result["confidence"] == 0.0
    assert "error" in result


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


def _patch_covenant_deps(stack: ExitStack, fake_result):
    """Patch the call-time REST-recipe deps of ``check_covenants`` so the tool
    runs fully offline. Returns the mocked ``CovenantMonitor.execute``.

    The deal_id-based tool reconstructs each period's state from the deal's own
    tapes; here we stub tape normalisation + reconstruction and force the
    monitor output, so the test exercises the tool's wiring + bounding only.
    """
    stack.enter_context(patch(
        "loanwhiz.api.main._normalised_tape_output",
        return_value={"reporting_date": "2026-04-30"},
    ))
    stack.enter_context(patch(
        "loanwhiz.api.main._reconstruct_series",
        return_value=types.SimpleNamespace(states=[]),
    ))
    stack.enter_context(patch(
        "loanwhiz.api.main._extracted_triggers_to_definitions",
        return_value=[],
    ))
    return stack.enter_context(patch(
        "loanwhiz.agent.tools.CovenantMonitor.execute",
        return_value=fake_result,
    ))


def test_check_covenants_runs_for_deal():
    """The deal_id-based tool runs the monitor for the deal and passes the
    bounded output + confidence through."""
    fake_result = _covenant_result(_make_covenant_output())

    with ExitStack() as stack:
        mock_exec = _patch_covenant_deps(stack, fake_result)
        result = check_covenants.invoke({"deal_id": "green-lion-2026-1"})

    mock_exec.assert_called_once()
    assert isinstance(result, dict)
    assert result["confidence"] == 1.0
    assert result["active_triggers"] == []


def test_check_covenants_unknown_deal_errors():
    """An unknown deal_id returns an explicit error and never runs the monitor
    — NO silent fall-back to the default deal (answering about the wrong deal
    is worse than a clean miss)."""
    with ExitStack() as stack:
        mock_exec = _patch_covenant_deps(stack, _covenant_result(_make_covenant_output()))
        result = check_covenants.invoke({"deal_id": "no-such-deal"})

    mock_exec.assert_not_called()
    assert "not found" in result["error"]
    assert result["confidence"] == 0.0
    assert "green-lion-2026-1" in result["available_deals"]


# ---------------------------------------------------------------------------
# forecast_trigger_breaches (#322)
# ---------------------------------------------------------------------------


def _make_deteriorating_covenant_output() -> CovenantOutput:
    """A 3-period covenant output where one trigger trends toward breach (40 →
    60 → 80 proximity) and another holds flat well clear (10 each)."""
    from loanwhiz.primitives.covenant_monitor import TriggerStatus

    statuses = []
    for i, prox in enumerate((40.0, 60.0, 80.0)):
        statuses.append(
            TriggerStatus(
                trigger_name="cumulative_loss_trigger",
                period=f"2026-{i + 1:02d}-28",
                metric_value=prox / 100.0 * 1.5,
                threshold=1.5,
                is_triggered=False,
                proximity_pct=prox,
                direction="deteriorating" if i else "n/a",
            )
        )
        statuses.append(
            TriggerStatus(
                trigger_name="clean_up_call",
                period=f"2026-{i + 1:02d}-28",
                metric_value=10.0,
                threshold=10.0,
                is_triggered=False,
                proximity_pct=10.0,
                direction="stable" if i else "n/a",
            )
        )
    return CovenantOutput(
        trigger_statuses=statuses,
        active_triggers=[],
        near_miss_triggers=["cumulative_loss_trigger"],
        summary="loss trigger near miss",
    )


def test_forecast_trigger_breaches_returns_ranked_projection():
    """The forecast tool runs the covenant monitor over the deal's series, then
    the proximity-trend monitor, returning a ranked projection list. The
    covenant output is a deteriorating series so a projection surfaces and the
    deteriorating trigger ranks most-urgent."""
    fake_result = _covenant_result(_make_deteriorating_covenant_output())

    with ExitStack() as stack:
        mock_exec = _patch_covenant_deps(stack, fake_result)
        result = forecast_trigger_breaches.invoke({"deal_id": "green-lion-2026-1"})

    mock_exec.assert_called_once()
    assert isinstance(result, dict)
    assert result["confidence"] == 1.0
    assert isinstance(result["projections"], list) and result["projections"]
    names = [p["trigger_name"] for p in result["projections"]]
    assert "cumulative_loss_trigger" in names
    # Ranking puts the deteriorating loss trigger ahead of the flat one.
    assert names[0] == "cumulative_loss_trigger"
    assert result["most_urgent"] == "cumulative_loss_trigger"


def test_forecast_trigger_breaches_unknown_deal_errors():
    """Unknown deal_id returns an explicit error without running the monitor —
    no silent fall-back (see check_covenants)."""
    with ExitStack() as stack:
        mock_exec = _patch_covenant_deps(
            stack, _covenant_result(_make_covenant_output())
        )
        result = forecast_trigger_breaches.invoke({"deal_id": "no-such-deal"})

    mock_exec.assert_not_called()
    assert "not found" in result["error"]
    assert result["confidence"] == 0.0
# verify_report (#320 — report_verifier wired as an agent tool)
# ---------------------------------------------------------------------------


_FAKE_VERIFICATION_RESPONSE = types.SimpleNamespace(
    model_dump=lambda: {
        "deal_id": "green-lion-2026-1",
        "reporting_period": "April 2026",
        "investor_report_url": "https://example.com/report-april-2026.pdf",
        "figures_checked": 2,
        "figures_matched": 1,
        "figures_mismatched": 1,
        "line_items": [
            {
                "line_item": "class_a_interest_paid",
                "reported_value": 9_050_000.0,
                "computed_value": 9_000_000.0,
                "delta": 50_000.0,
                "delta_pct": 0.56,
                "match": False,
                "tolerance_pct": 1.0,
            },
        ],
        "overall_match": False,
        "summary": "1/2 figures match within 1% tolerance; 1 mismatch.",
        "confidence": 0.9,
        "citations": [{"document": "Green Lion Investor Report"}],
    }
)


def test_verify_report_delegates_to_endpoint_recipe():
    """The tool delegates to the /report-verification recipe and passes through
    the break report + governance evidence."""
    with patch(
        "loanwhiz.api.main.deal_report_verification",
        return_value=_FAKE_VERIFICATION_RESPONSE,
    ) as mock_recipe:
        result = verify_report.invoke({"deal_id": "green-lion-2026-1"})

    mock_recipe.assert_called_once_with(
        "green-lion-2026-1", period=None, tolerance_pct=1.0
    )
    assert isinstance(result, dict)
    assert result["confidence"] == 0.9
    assert result["overall_match"] is False
    assert result["line_items"][0]["line_item"] == "class_a_interest_paid"
    assert result["citations"]  # governance evidence travels with the answer


def test_verify_report_passes_period_and_tolerance():
    """Period filter + tolerance are threaded through to the endpoint recipe."""
    with patch(
        "loanwhiz.api.main.deal_report_verification",
        return_value=_FAKE_VERIFICATION_RESPONSE,
    ) as mock_recipe:
        verify_report.invoke(
            {"deal_id": "green-lion-2026-1", "period": "march 2026", "tolerance_pct": 2.0}
        )

    mock_recipe.assert_called_once_with(
        "green-lion-2026-1", period="march 2026", tolerance_pct=2.0
    )


def test_verify_report_unknown_deal_errors():
    """An unknown deal returns an explicit error and never calls the recipe —
    no silent fall-back to the default deal."""
    with patch(
        "loanwhiz.api.main.deal_report_verification",
    ) as mock_recipe:
        result = verify_report.invoke({"deal_id": "no-such-deal"})

    mock_recipe.assert_not_called()
    assert "not found" in result["error"]
    assert result["confidence"] == 0.0
    assert "green-lion-2026-1" in result["available_deals"]


def test_verify_report_no_investor_reports_returns_error():
    """When the endpoint raises 422 (no investor reports), the tool surfaces the
    detail as an error dict rather than crashing."""
    from fastapi import HTTPException

    with patch(
        "loanwhiz.api.main.deal_report_verification",
        side_effect=HTTPException(status_code=422, detail="no investor_report_urls"),
    ):
        result = verify_report.invoke({"deal_id": "green-lion-2026-1"})

    assert "investor_report" in result["error"]
    assert result["confidence"] == 0.0


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

    with ExitStack() as stack:
        _patch_covenant_deps(stack, _covenant_result(fake_output))
        result = check_covenants.invoke({"deal_id": "green-lion-2026-1"})

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

    with ExitStack() as stack:
        _patch_covenant_deps(stack, _covenant_result(fake_output))
        result = check_covenants.invoke({"deal_id": "green-lion-2026-1"})

    # All rows preserved; no summarisation artefacts added.
    assert len(result["trigger_statuses"]) == raw_row_count
    assert "trend_summary" not in result
    assert "periods_summarised" not in result
    assert result["confidence"] == 1.0


def test_check_covenants_defaults_to_demo_deal():
    """With no deal_id the covenant tool serves the default demo deal."""
    fake_result = _covenant_result(_make_covenant_output())

    with ExitStack() as stack:
        mock_exec = _patch_covenant_deps(stack, fake_result)
        result = check_covenants.invoke({})

    mock_exec.assert_called_once()
    assert result["confidence"] == 1.0


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


def _collections_result() -> PrimitiveResult[CollectionsOutput]:
    return PrimitiveResult[CollectionsOutput](
        output=_make_collections_output(),
        confidence=0.8,
        citations=[],
        audit_entry=_FAKE_AUDIT,
    )


def test_aggregate_collections_runs_for_deal():
    """Default (no period) selects the deal's latest registered tape and
    aggregates it — the tool picks the tape itself from the registry."""
    tapes = DEAL_REGISTRY["green-lion-2026-1"]["tape_urls"]
    latest = tapes[-1]

    with patch(
        "loanwhiz.agent.tools.CollectionsAggregator.execute",
        return_value=_collections_result(),
    ) as mock_exec:
        result = aggregate_collections.invoke({"deal_id": "green-lion-2026-1"})

    mock_exec.assert_called_once()
    call_arg = mock_exec.call_args[0][0]
    assert call_arg.tape_file_url == latest["url"]
    assert call_arg.reporting_period == latest["date"]

    assert isinstance(result, dict)
    assert result["confidence"] == 0.8
    assert result["available_revenue_funds"] == 9_050_000.0
    assert result["available_principal_funds"] == 5_000_000.0


def test_aggregate_collections_selects_tape_by_period():
    """A ``period`` substring selects the matching tape, not the latest."""
    tapes = DEAL_REGISTRY["green-lion-2026-1"]["tape_urls"]
    march = next(t for t in tapes if "2026-03" in t["date"])

    with patch(
        "loanwhiz.agent.tools.CollectionsAggregator.execute",
        return_value=_collections_result(),
    ) as mock_exec:
        aggregate_collections.invoke(
            {"deal_id": "green-lion-2026-1", "period": "2026-03"}
        )

    call_arg = mock_exec.call_args[0][0]
    assert call_arg.tape_file_url == march["url"]
    assert call_arg.reporting_period == march["date"]


def test_aggregate_collections_unknown_deal_errors():
    """Unknown deal_id → explicit error, primitive never runs (no silent
    fall-back to the default deal)."""
    with patch(
        "loanwhiz.agent.tools.CollectionsAggregator.execute"
    ) as mock_exec:
        result = aggregate_collections.invoke({"deal_id": "no-such-deal"})

    mock_exec.assert_not_called()
    assert "not found" in result["error"]
    assert result["confidence"] == 0.0


def test_aggregate_collections_no_tapes_errors():
    """A registered deal that ships no loan tapes → explicit, honest error."""
    no_tape_deal = next(
        (d for d, ctx in DEAL_REGISTRY.items() if not ctx.get("tape_urls")), None
    )
    assert no_tape_deal is not None, "expected a tape-less registered deal"

    with patch(
        "loanwhiz.agent.tools.CollectionsAggregator.execute"
    ) as mock_exec:
        result = aggregate_collections.invoke({"deal_id": no_tape_deal})

    mock_exec.assert_not_called()
    assert "No loan tapes" in result["error"]
    assert result["confidence"] == 0.0


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
        "project_cashflows",
        "check_covenants",
        "forecast_trigger_breaches",
        "aggregate_collections",
        "verify_report",
        "get_deal_model",
        "list_deal_tapes",
        "stress_matrix",
        "monitor_portfolio",
        "synthesise_cross_source",
    ]


def test_sf_tools_has_exactly_twelve_tools():
    assert len(SF_TOOLS) == 12


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
    assert len(result) == 12


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


# ---------------------------------------------------------------------------
# synthesise_cross_source (#403) — cross-source synthesis bundle
# ---------------------------------------------------------------------------
#
# The tool composes the underlying functions of get_deal_model /
# aggregate_collections / verify_report (their plain ``.func`` callables), so
# the tests stub those three at that seam — no LLM, no network, no real
# primitive execution.


def _patch_synthesis_sources(stack: ExitStack, deal_model, pool, report):
    """Stub the three per-source tool functions the synthesis tool composes.

    Each arg is either a dict (returned as-is) or an Exception instance (raised
    when the source function is called, to exercise the per-source crash guard).
    """
    def _ret(value):
        def _fn(*_args, **_kwargs):
            if isinstance(value, Exception):
                raise value
            return value
        return _fn

    stack.enter_context(
        patch("loanwhiz.agent.tools.get_deal_model.func", side_effect=_ret(deal_model))
    )
    stack.enter_context(
        patch("loanwhiz.agent.tools.aggregate_collections.func", side_effect=_ret(pool))
    )
    stack.enter_context(
        patch("loanwhiz.agent.tools.verify_report.func", side_effect=_ret(report))
    )


def _ok_deal_model() -> dict:
    return {
        "deal_id": DEFAULT_DEAL_ID,
        "deal_name": "Green Lion 2026-1 B.V.",
        "extraction_status": "cached",
        "completeness_score": 0.9,
        "trigger_names": ["reserve_fund_trigger"],
        "tranche_structure": [{"class": "A"}],
        "covenants": {},
        "waterfalls": {},
        "definitions": {"Reserve Target": "1.5% of note balance"},
    }


def _ok_pool() -> dict:
    return {
        "reporting_period": "April 2026",
        "available_revenue_funds": 9_000_000.0,
        "pool_balance_eur": 990_000_000.0,
        "confidence": 0.85,
        "citations": [{"document": "tape.csv", "page_or_row": 1, "excerpt": "balance"}],
        "duration_ms": 1.0,
    }


def _ok_report() -> dict:
    return {
        "overall_match": True,
        "summary": "Report ties out to the engine.",
        "confidence": 0.8,
        "citations": [{"document": "report.pdf", "page_or_row": 2, "excerpt": "class A"}],
        "duration_ms": 1.0,
    }


def test_synthesise_cross_source_bundles_all_three_sources():
    """All three sources present → one bundle, each block source-tagged + available."""
    with ExitStack() as stack:
        _patch_synthesis_sources(stack, _ok_deal_model(), _ok_pool(), _ok_report())
        result = synthesise_cross_source.invoke({"deal_id": DEFAULT_DEAL_ID})

    assert result["deal_model"]["source"] == "prospectus deal-model"
    assert result["pool"]["source"] == "loan tape"
    assert result["report"]["source"] == "investor report"
    assert result["deal_model"]["available"] is True
    assert result["pool"]["available"] is True
    assert result["report"]["available"] is True
    assert set(result["sources_available"]) == {
        "prospectus deal-model",
        "loan tape",
        "investor report",
    }
    assert result["sources_missing"] == []
    # The original analytical content survives on each block.
    assert result["pool"]["available_revenue_funds"] == 9_000_000.0
    assert result["deal_model"]["definitions"]["Reserve Target"] == "1.5% of note balance"


def test_synthesise_cross_source_carries_per_source_citations():
    """Each block keeps its own citations; the top-level union carries them all."""
    with ExitStack() as stack:
        _patch_synthesis_sources(stack, _ok_deal_model(), _ok_pool(), _ok_report())
        result = synthesise_cross_source.invoke({"deal_id": DEFAULT_DEAL_ID})

    assert result["pool"]["citations"] == [
        {"document": "tape.csv", "page_or_row": 1, "excerpt": "balance"}
    ]
    assert result["report"]["citations"] == [
        {"document": "report.pdf", "page_or_row": 2, "excerpt": "class A"}
    ]
    # Top-level union = both source citations.
    docs = {c["document"] for c in result["citations"]}
    assert docs == {"tape.csv", "report.pdf"}
    # Bundle confidence is the most-conservative available source (min).
    assert result["confidence"] == 0.8


def test_synthesise_cross_source_uncached_deal_model_is_honest_gap():
    """A not_cached deal-model is an explicit unavailable block — no fabricated terms."""
    not_cached = {
        "deal_id": DEFAULT_DEAL_ID,
        "deal_name": "Green Lion 2026-1 B.V.",
        "extraction_status": "not_cached",
        "note": "prospectus not extracted",
    }
    with ExitStack() as stack:
        _patch_synthesis_sources(stack, not_cached, _ok_pool(), _ok_report())
        result = synthesise_cross_source.invoke({"deal_id": DEFAULT_DEAL_ID})

    assert result["deal_model"]["available"] is False
    assert result["deal_model"]["extraction_status"] == "not_cached"
    assert "prospectus deal-model" in result["sources_missing"]
    assert "loan tape" in result["sources_available"]
    # No fabricated structural terms leaked in.
    assert "definitions" not in result["deal_model"]


def test_synthesise_cross_source_missing_tape_and_report_are_honest_gaps():
    """A source returning an error → explicit unavailable block, others survive."""
    pool_err = {"error": "No loan tapes published", "confidence": 0.0, "citations": []}
    report_err = {"error": "deal publishes no investor reports", "confidence": 0.0, "citations": []}
    with ExitStack() as stack:
        _patch_synthesis_sources(stack, _ok_deal_model(), pool_err, report_err)
        result = synthesise_cross_source.invoke({"deal_id": DEFAULT_DEAL_ID})

    assert result["pool"]["available"] is False
    assert result["report"]["available"] is False
    assert result["deal_model"]["available"] is True
    assert set(result["sources_missing"]) == {"loan tape", "investor report"}
    assert result["sources_available"] == ["prospectus deal-model"]


def test_synthesise_cross_source_per_source_crash_does_not_lose_others():
    """If one source function raises, it becomes an honest error block — not a crash."""
    with ExitStack() as stack:
        _patch_synthesis_sources(
            stack, _ok_deal_model(), RuntimeError("tape read blew up"), _ok_report()
        )
        result = synthesise_cross_source.invoke({"deal_id": DEFAULT_DEAL_ID})

    assert result["pool"]["available"] is False
    assert "tape read blew up" in result["pool"]["error"]
    # The other two sources are unaffected.
    assert result["deal_model"]["available"] is True
    assert result["report"]["available"] is True


def test_synthesise_cross_source_all_missing_is_not_confident():
    """An all-missing bundle reports confidence 0.0 (never reads as confident)."""
    err = {"error": "unavailable", "confidence": 0.0, "citations": []}
    not_cached = {"extraction_status": "not_cached", "note": "x"}
    with ExitStack() as stack:
        _patch_synthesis_sources(stack, not_cached, err, err)
        result = synthesise_cross_source.invoke({"deal_id": DEFAULT_DEAL_ID})

    assert result["sources_available"] == []
    assert result["confidence"] == 0.0
    assert result["citations"] == []


def test_synthesise_cross_source_unknown_deal_errors():
    """A bad deal_id returns {error, available_deals} and never falls back to the default."""
    result = synthesise_cross_source.invoke({"deal_id": "no-such-deal"})
    assert "error" in result
    assert "no-such-deal" in result["error"]
    assert "available_deals" in result
    assert result["confidence"] == 0.0
    # Crucially: no per-source blocks were assembled for the wrong deal.
    assert "deal_model" not in result


def test_synthesise_cross_source_includes_attribution_guidance():
    """The bundle instructs the consumer to attribute claims + report gaps honestly."""
    with ExitStack() as stack:
        _patch_synthesis_sources(stack, _ok_deal_model(), _ok_pool(), _ok_report())
        result = synthesise_cross_source.invoke({"deal_id": DEFAULT_DEAL_ID})

    guidance = result["synthesis_guidance"].lower()
    assert "source" in guidance
    assert "sources_missing" in guidance or "unavailable" in guidance


def test_system_prompt_documents_cross_source_synthesis():
    """The prompt names the synthesis tool and mandates per-source attribution + honest gaps."""
    from loanwhiz.agent.planner import SYSTEM_PROMPT

    assert "synthesise_cross_source" in SYSTEM_PROMPT
    # Honesty bar: attribute each claim to its source, and report missing
    # sources rather than fabricating across the gap.
    lowered = SYSTEM_PROMPT.lower()
    assert "attribute" in lowered
    assert "fabricate" in lowered or "fabricating" in lowered
