"""Tests for the LoanWhiz REST API.

Heavy primitive / agent calls (Gemini, tape downloads, waterfall maths) are
mocked so the unit tests run offline. The one real end-to-end test is marked
``@pytest.mark.integration`` and hits the live primitives.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from loanwhiz.agent.executor import ExecutionResult, ValidationStatus
from loanwhiz.api import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Service / health
# ---------------------------------------------------------------------------


def test_root_returns_service_info():
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "LoanWhiz API"
    assert body["version"] == "0.1.0"
    assert "green-lion-2026-1" in body["deals"]


def test_health_returns_ok():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------


def test_cors_preflight_allows_next_origin():
    """An OPTIONS preflight from the Next dev origin is allowed by CORS.

    Confirms CORSMiddleware is registered: a browser preflight carrying the
    Next.js dev origin (http://localhost:3000) gets it echoed back in the
    access-control-allow-origin header.
    """
    resp = client.options(
        "/query",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"


# ---------------------------------------------------------------------------
# Deal model
# ---------------------------------------------------------------------------


def _seed_cached_deal_model(cache_dir: str) -> dict:
    """Write a minimal cached DealModel JSON for Green Lion into ``cache_dir``.

    Returns the dict that was serialised so tests can assert against it. The
    filename mirrors the assembler's slug for the deal name.
    """
    from loanwhiz.api import main as api_main
    from loanwhiz.config import GREEN_LION
    from loanwhiz.extraction.assembler import _slug

    model = {
        "metadata": {
            "deal_name": GREEN_LION["deal_name"],
            "prospectus_url": GREEN_LION["prospectus_url"],
            "extracted_at": "2026-06-03T00:00:00+00:00",
            "extraction_duration_sec": 1.5,
            "sections_found": ["definitions", "revenue_priority_of_payments"],
            "completeness_score": 0.75,
            "cache_path": "",
        },
        "definitions": {
            "Available Distribution Amount": {
                "definition": "The amount available for distribution.",
                "page_or_section": "Section 9.1",
            }
        },
        "waterfalls": {
            "revenue": {
                "waterfall_type": "revenue",
                "deal_name": GREEN_LION["deal_name"],
                "steps": [],
            }
        },
        "covenants": {
            "deal_name": GREEN_LION["deal_name"],
            "triggers": [],
            "issuer_covenants": [],
            "extraction_confidence": 0.6,
        },
        "tranche_structure": [
            {
                "priority": "(a)",
                "recipient": "security_trustee_fees",
                "description": "Pay security trustee fees.",
                "waterfall_type": "revenue",
            }
        ],
        "trigger_names": ["Class A PDL Trigger", "Reserve Account Trigger"],
    }
    slug = _slug(GREEN_LION["deal_name"])
    path = Path(cache_dir) / f"{slug}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model), encoding="utf-8")
    return model


def test_deal_model_returns_deal_context_when_cold(tmp_path):
    """With no cache present the endpoint returns the config and degrades
    gracefully — config fields intact, deal_model null, status not_cached.
    It must NOT block on a cold extraction."""
    with patch("loanwhiz.api.main.DEAL_MODEL_CACHE_DIR", str(tmp_path)):
        resp = client.get("/deal/green-lion-2026-1/model")
    assert resp.status_code == 200
    body = resp.json()
    assert body["deal_name"] == "Green Lion 2026-1 B.V."
    assert "tape_urls" in body
    assert "prospectus_url" in body
    assert body["extraction_status"] == "not_cached"
    assert body["deal_model"] is None
    assert body["completeness_score"] is None
    assert body["trigger_names"] is None


def test_deal_model_returns_extracted_model_when_cached(tmp_path):
    """When the extracted DealModel is cached the endpoint returns it —
    tranches, trigger_names, completeness, metadata — alongside the config."""
    seeded = _seed_cached_deal_model(str(tmp_path))
    with patch("loanwhiz.api.main.DEAL_MODEL_CACHE_DIR", str(tmp_path)):
        resp = client.get("/deal/green-lion-2026-1/model")

    assert resp.status_code == 200
    body = resp.json()
    # Config still present (nothing the frontend uses breaks).
    assert body["deal_name"] == "Green Lion 2026-1 B.V."
    assert "tape_urls" in body
    assert "prospectus_url" in body
    # Extracted model surfaced.
    assert body["extraction_status"] == "cached"
    assert body["completeness_score"] == 0.75
    assert body["trigger_names"] == seeded["trigger_names"]
    assert body["deal_model"] is not None
    assert body["deal_model"]["tranche_structure"] == seeded["tranche_structure"]
    assert body["deal_model"]["trigger_names"] == seeded["trigger_names"]
    assert body["deal_model"]["waterfalls"] == seeded["waterfalls"]
    assert (
        body["deal_model"]["metadata"]["completeness_score"]
        == seeded["metadata"]["completeness_score"]
    )


def test_deal_model_does_not_trigger_extraction_when_cold(tmp_path):
    """A cold cache must never invoke the expensive extract_deal_model path."""
    with patch("loanwhiz.api.main.DEAL_MODEL_CACHE_DIR", str(tmp_path)), patch(
        "loanwhiz.extraction.assembler.extract_deal_model"
    ) as mock_extract:
        resp = client.get("/deal/green-lion-2026-1/model")
    assert resp.status_code == 200
    assert resp.json()["extraction_status"] == "not_cached"
    mock_extract.assert_not_called()


def test_deal_model_unknown_returns_404():
    resp = client.get("/deal/unknown/model")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Deal unknown not found"


# ---------------------------------------------------------------------------
# Query (agent mocked)
# ---------------------------------------------------------------------------


def test_query_wraps_execute_query():
    fake = ExecutionResult(
        question="Is the deal compliant?",
        answer="Yes, all covenants are within limits.",
        overall_status=ValidationStatus.PASSED,
        step_validations=[],
        aggregate_confidence=0.95,
        human_review_required=False,
        evidence_pack_id="pack-123",
        reasoning_trace=["Called covenant_monitor → confidence 0.95 ✓"],
    )
    with patch("loanwhiz.api.main.execute_query", return_value=fake) as m:
        resp = client.post(
            "/query",
            json={"question": "Is the deal compliant?", "confidence_threshold": 0.8},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["question"] == "Is the deal compliant?"
    assert body["answer"] == "Yes, all covenants are within limits."
    assert body["overall_status"] == "passed"
    assert body["aggregate_confidence"] == 0.95
    assert body["human_review_required"] is False
    assert body["evidence_pack_id"] == "pack-123"
    assert body["reasoning_trace"] == ["Called covenant_monitor → confidence 0.95 ✓"]
    # confidence_threshold flows through to the agent.
    m.assert_called_once_with("Is the deal compliant?", confidence_threshold=0.8)


def test_query_default_confidence_threshold():
    fake = ExecutionResult(
        question="q",
        answer="a",
        overall_status=ValidationStatus.PASSED,
        step_validations=[],
        aggregate_confidence=1.0,
        human_review_required=False,
        evidence_pack_id="pack-1",
        reasoning_trace=[],
    )
    with patch("loanwhiz.api.main.execute_query", return_value=fake) as m:
        resp = client.post("/query", json={"question": "q"})
    assert resp.status_code == 200
    m.assert_called_once_with("q", confidence_threshold=0.7)


# ---------------------------------------------------------------------------
# Compliance (primitives mocked)
# ---------------------------------------------------------------------------


class _FakeResult:
    """Stand-in for a PrimitiveResult whose ``output`` model_dumps to a dict."""

    def __init__(self, dump: dict):
        self._dump = dump

    @property
    def output(self):
        return self

    def model_dump(self):
        return self._dump


def test_deal_compliance_runs_monitor():
    tape_dump = {"row_count": 100, "field_coverage": 0.98}
    compliance_dump = {
        "trigger_statuses": [],
        "active_triggers": [],
        "near_miss_triggers": [],
        "summary": "All covenants within limits.",
    }

    with patch(
        "loanwhiz.api.main.EsmaTapeNormaliser"
    ) as MockNorm, patch("loanwhiz.api.main.CovenantMonitor") as MockMon:
        MockNorm.return_value.execute.return_value = _FakeResult(tape_dump)
        MockMon.DEFAULT_TRIGGERS = []
        MockMon.return_value.execute.return_value = _FakeResult(compliance_dump)

        resp = client.get("/deal/green-lion-2026-1/compliance")

    assert resp.status_code == 200
    assert resp.json() == compliance_dump
    # One normalise call per tape in the deal context.
    assert MockNorm.return_value.execute.call_count == 3
    MockMon.return_value.execute.assert_called_once()


def test_deal_compliance_unknown_returns_404():
    resp = client.get("/deal/unknown/compliance")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Project (primitive mocked)
# ---------------------------------------------------------------------------


def test_deal_project_runs_waterfall():
    waterfall_dump = {
        "reporting_period": "projection+12m (base)",
        "revenue_waterfall": [],
        "redemption_waterfall": [],
        "tranche_distributions": [],
        "total_distributed": 0.0,
        "shortfall": 0.0,
    }

    with patch("loanwhiz.api.main.WaterfallRunner") as MockRunner:
        MockRunner.return_value.execute.return_value = _FakeResult(waterfall_dump)
        resp = client.post(
            "/deal/green-lion-2026-1/project",
            json={"scenarios": ["base", "stress"], "months": 6},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["deal_id"] == "green-lion-2026-1"
    assert body["months"] == 6
    assert body["scenarios"] == ["base", "stress"]
    assert set(body["projections"]) == {"base", "stress"}
    # One waterfall run per requested scenario.
    assert MockRunner.return_value.execute.call_count == 2


def test_deal_project_defaults():
    waterfall_dump = {
        "reporting_period": "projection",
        "revenue_waterfall": [],
        "redemption_waterfall": [],
        "tranche_distributions": [],
        "total_distributed": 0.0,
        "shortfall": 0.0,
    }
    with patch("loanwhiz.api.main.WaterfallRunner") as MockRunner:
        MockRunner.return_value.execute.return_value = _FakeResult(waterfall_dump)
        resp = client.post("/deal/green-lion-2026-1/project", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["months"] == 12
    assert body["scenarios"] == ["base", "stress"]


def test_deal_project_includes_wal_per_scenario():
    """The project response surfaces Class A WAL for each scenario.

    Mocks the waterfall to return a Class A principal distribution; the handler
    derives the Class A weighted-average life and exposes it both on each
    per-scenario projection and in a top-level per-scenario ``wal`` map, without
    dropping the existing waterfall projection fields.
    """
    waterfall_dump = {
        "reporting_period": "projection+6m (base)",
        "revenue_waterfall": [],
        "redemption_waterfall": [],
        "tranche_distributions": [
            {
                "tranche": "class_a",
                "interest_received": 100.0,
                "principal_received": 1_000.0,
                "total_received": 1_100.0,
                "opening_balance": 10_000.0,
                "closing_balance": 9_000.0,
            }
        ],
        "total_distributed": 1_100.0,
        "shortfall": 0.0,
    }

    with patch("loanwhiz.api.main.WaterfallRunner") as MockRunner:
        MockRunner.return_value.execute.return_value = _FakeResult(waterfall_dump)
        resp = client.post(
            "/deal/green-lion-2026-1/project",
            json={"scenarios": ["base", "stress"], "months": 6},
        )

    assert resp.status_code == 200
    body = resp.json()

    # Existing fields stay intact.
    assert set(body["projections"]) == {"base", "stress"}

    # WAL surfaced per scenario, both inline and in the top-level map.
    assert set(body["wal"]) == {"base", "stress"}
    for scenario in ("base", "stress"):
        proj = body["projections"][scenario]
        # Existing waterfall fields are not dropped.
        assert "tranche_distributions" in proj
        assert "shortfall" in proj
        # WAL additively present on the projection.
        assert proj["wal_class_a_months"] == 6.0
        assert proj["wal_class_a_years"] == pytest.approx(0.5)
        # And in the top-level per-scenario WAL map.
        assert body["wal"][scenario]["wal_class_a_months"] == 6.0
        assert body["wal"][scenario]["wal_class_a_years"] == pytest.approx(0.5)


def test_deal_project_wal_zero_when_no_class_a_principal():
    """WAL is 0.0 when no Class A principal is returned (no divide-by-zero)."""
    waterfall_dump = {
        "reporting_period": "projection+12m (base)",
        "revenue_waterfall": [],
        "redemption_waterfall": [],
        "tranche_distributions": [],
        "total_distributed": 0.0,
        "shortfall": 0.0,
    }
    with patch("loanwhiz.api.main.WaterfallRunner") as MockRunner:
        MockRunner.return_value.execute.return_value = _FakeResult(waterfall_dump)
        resp = client.post(
            "/deal/green-lion-2026-1/project",
            json={"scenarios": ["base"], "months": 12},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["projections"]["base"]["wal_class_a_months"] == 0.0
    assert body["wal"]["base"]["wal_class_a_years"] == 0.0


def test_deal_project_unknown_returns_404():
    resp = client.post("/deal/unknown/project", json={})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Waterfall (primitives mocked)
# ---------------------------------------------------------------------------


class _FakeOutput:
    """Stand-in for a PrimitiveResult whose ``output`` is a plain object.

    Unlike ``_FakeResult`` (which model_dumps to a dict), the waterfall handler
    reads typed attributes off ``.output`` (``revenue_waterfall`` etc.), so the
    fake exposes them as attributes via ``SimpleNamespace``.
    """

    def __init__(self, output):
        self._output = output

    @property
    def output(self):
        return self._output


def test_deal_waterfall_runs_chain():
    from types import SimpleNamespace

    collections_out = SimpleNamespace(
        available_revenue_funds=4_000_000.0,
        available_principal_funds=10_000_000.0,
        senior_fees=50_000.0,
        pool_balance_eur=1_000_000_000.0,
    )
    step = SimpleNamespace(
        priority="(d)",
        recipient="class_a_interest",
        amount_available=4_000_000.0,
        amount_distributed=3_000_000.0,
        shortfall=0.0,
        condition=None,
    )
    tranche = SimpleNamespace(
        tranche="class_a",
        interest_received=3_000_000.0,
        principal_received=10_000_000.0,
        total_received=13_000_000.0,
        opening_balance=1_000_000_000.0,
        closing_balance=990_000_000.0,
    )
    waterfall_out = SimpleNamespace(
        reporting_period="2026-04-30",
        revenue_waterfall=[step],
        tranche_distributions=[tranche],
        total_distributed=13_000_000.0,
        shortfall=0.0,
    )

    with patch("loanwhiz.api.main.CollectionsAggregator") as MockAgg, patch(
        "loanwhiz.api.main.WaterfallRunner"
    ) as MockRunner:
        MockAgg.return_value.execute.return_value = _FakeOutput(collections_out)
        MockRunner.return_value.execute.return_value = _FakeOutput(waterfall_out)

        resp = client.get("/deal/green-lion-2026-1/waterfall")

    assert resp.status_code == 200
    body = resp.json()
    assert body["deal_id"] == "green-lion-2026-1"
    assert body["reporting_period"] == "2026-04-30"
    assert body["available_revenue_funds"] == 4_000_000.0
    assert body["available_principal_funds"] == 10_000_000.0
    # Revenue cascade steps surfaced with their amounts.
    assert len(body["revenue_waterfall"]) == 1
    assert body["revenue_waterfall"][0]["recipient"] == "class_a_interest"
    assert body["revenue_waterfall"][0]["amount_distributed"] == 3_000_000.0
    # Per-tranche distributions surfaced.
    assert len(body["tranche_distributions"]) == 1
    assert body["tranche_distributions"][0]["tranche"] == "class_a"
    assert body["tranche_distributions"][0]["total_received"] == 13_000_000.0
    assert body["total_distributed"] == 13_000_000.0
    assert body["shortfall"] == 0.0
    # Latest tape aggregated, plus the prior tape for prev_pool_balance.
    assert MockAgg.return_value.execute.call_count == 2
    MockRunner.return_value.execute.assert_called_once()


def test_deal_waterfall_unknown_returns_404():
    resp = client.get("/deal/unknown/waterfall")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tape analytics (primitive mocked)
# ---------------------------------------------------------------------------


def _tape_output_dump(reporting_date: str, pool_balance: float) -> dict:
    """A full EsmaTapeOutput-shaped dict for the tape-analytics endpoint."""
    return {
        "reporting_date": reporting_date,
        "asset_class": "RMBS",
        "transaction_name": "Green Lion 2026-1 B.V.",
        "loan_count": 1000,
        "pool_balance_eur": pool_balance,
        "pool_stats": {"wtd_ltv": 65.0, "wtd_coupon_pct": 3.6},
        "arrears_breakdown": {
            "current_pct": 98.0,
            "arrears_1_2m_pct": 1.0,
            "arrears_180d_plus_pct": 0.5,
            "default_pct": 0.5,
        },
        "epc_breakdown": {"A": 40.0, "B": 60.0},
        "rate_type_breakdown": {"Fixed": 100.0},
        "property_type_breakdown": {"House": 70.0, "Apartment": 30.0},
        "geographic_breakdown": {"NL-NH": 50.0, "NL-ZH": 50.0},
        "annex_detected": "Annex 2 (RMBS)",
    }


@pytest.fixture
def _isolated_tape_cache(tmp_path):
    """Point the tape-analytics cache at a clean tmp dir and empty memo.

    Keeps the analytics-cache tests deterministic: each test starts cold (no
    on-disk artifact, no in-process memo) and never touches the shared
    ``/tmp/loanwhiz_cache/tape_analytics`` dir.
    """
    from loanwhiz.api import main as api_main

    saved_memo = dict(api_main._TAPE_ANALYTICS_MEMO)
    api_main._TAPE_ANALYTICS_MEMO.clear()
    with patch("loanwhiz.api.main.TAPE_ANALYTICS_CACHE_DIR", str(tmp_path)):
        yield tmp_path
    api_main._TAPE_ANALYTICS_MEMO.clear()
    api_main._TAPE_ANALYTICS_MEMO.update(saved_memo)


def test_deal_tape_analytics_returns_periods(_isolated_tape_cache):
    dumps = [
        _tape_output_dump("2026-02-28", 1_050_000_000.0),
        _tape_output_dump("2026-03-31", 1_040_000_000.0),
        _tape_output_dump("2026-04-30", 1_033_412_063.0),
    ]
    results = iter(_FakeResult(d) for d in dumps)

    with patch("loanwhiz.api.main.EsmaTapeNormaliser") as MockNorm:
        MockNorm.return_value.execute.side_effect = lambda _inp: next(results)
        resp = client.get("/deal/green-lion-2026-1/tape-analytics")

    assert resp.status_code == 200
    body = resp.json()
    # One analytics object per tape in the deal context, chronological order.
    assert len(body) == 3
    assert MockNorm.return_value.execute.call_count == 3
    assert [p["tape_date"] for p in body] == ["2026-02-28", "2026-03-31", "2026-04-30"]
    assert [p["pool_balance_eur"] for p in body] == [
        1_050_000_000.0,
        1_040_000_000.0,
        1_033_412_063.0,
    ]
    # Each period carries the expected analytics keys.
    expected_keys = {
        "tape_date",
        "reporting_date",
        "loan_count",
        "pool_balance_eur",
        "pool_stats",
        "arrears_breakdown",
        "epc_breakdown",
        "geographic_breakdown",
        "property_type_breakdown",
    }
    for period in body:
        assert expected_keys <= set(period)
    # Weighted LTV surfaces through pool_stats.
    assert body[0]["pool_stats"]["wtd_ltv"] == 65.0


def test_deal_tape_analytics_unknown_returns_404():
    resp = client.get("/deal/unknown/tape-analytics")
    assert resp.status_code == 404


def test_deal_tape_analytics_computes_each_tape_once_across_calls(_isolated_tape_cache):
    """Repeated /tape-analytics calls normalise each tape exactly once.

    Two requests over a 3-tape deal would, without caching, run the normaliser
    6 times. With the keyed cache (memo + on-disk JSON), each tape is computed
    once: total execute() calls == number of tapes, not 2× that.
    """
    dumps = [
        _tape_output_dump("2026-02-28", 1_050_000_000.0),
        _tape_output_dump("2026-03-31", 1_040_000_000.0),
        _tape_output_dump("2026-04-30", 1_033_412_063.0),
    ]
    results = iter(_FakeResult(d) for d in dumps)

    with patch("loanwhiz.api.main.EsmaTapeNormaliser") as MockNorm:
        MockNorm.return_value.execute.side_effect = lambda _inp: next(results)
        first = client.get("/deal/green-lion-2026-1/tape-analytics")
        second = client.get("/deal/green-lion-2026-1/tape-analytics")

    assert first.status_code == 200
    assert second.status_code == 200
    # Both responses identical (served from cache the second time).
    assert first.json() == second.json()
    # One compute per tape across BOTH requests — not two.
    assert MockNorm.return_value.execute.call_count == 3


def test_deal_tape_analytics_on_disk_cache_survives_fresh_process(_isolated_tape_cache):
    """A populated on-disk cache serves a 'fresh process' (empty memo) without
    re-running the normaliser.

    Simulates a restart: prime the cache via one request, clear the in-process
    memo (as a new process would have), then request again with the normaliser
    patched to raise — proving the second request reads from disk only.
    """
    from loanwhiz.api import main as api_main

    dumps = [
        _tape_output_dump("2026-02-28", 1_050_000_000.0),
        _tape_output_dump("2026-03-31", 1_040_000_000.0),
        _tape_output_dump("2026-04-30", 1_033_412_063.0),
    ]
    results = iter(_FakeResult(d) for d in dumps)
    with patch("loanwhiz.api.main.EsmaTapeNormaliser") as MockNorm:
        MockNorm.return_value.execute.side_effect = lambda _inp: next(results)
        primed = client.get("/deal/green-lion-2026-1/tape-analytics")
    assert primed.status_code == 200
    # On-disk artifacts written, one per tape.
    assert len(list(_isolated_tape_cache.glob("*.json"))) == 3

    # Simulate a fresh process: memo empty, but on-disk cache present.
    api_main._TAPE_ANALYTICS_MEMO.clear()
    with patch("loanwhiz.api.main.EsmaTapeNormaliser") as MockNorm2:
        MockNorm2.return_value.execute.side_effect = AssertionError(
            "normaliser must not run when the on-disk cache is warm"
        )
        resp = client.get("/deal/green-lion-2026-1/tape-analytics")

    assert resp.status_code == 200
    assert resp.json() == primed.json()


# ---------------------------------------------------------------------------
# Integration — real primitives (hits network: tape downloads). Deselect with
# `-m "not integration"`.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_deal_compliance_integration():
    resp = client.get("/deal/green-lion-2026-1/compliance")
    assert resp.status_code == 200
    body = resp.json()
    assert "summary" in body
    assert "active_triggers" in body


@pytest.mark.integration
def test_deal_waterfall_integration():
    """Real CollectionsAggregator -> WaterfallRunner over the live tapes."""
    resp = client.get("/deal/green-lion-2026-1/waterfall")
    assert resp.status_code == 200
    body = resp.json()
    # The Revenue Priority of Payments has 11 steps (a)-(k).
    assert len(body["revenue_waterfall"]) == 11
    # Class A / B / C distributions.
    assert {t["tranche"] for t in body["tranche_distributions"]} == {
        "class_a",
        "class_b",
        "class_c",
    }
    assert body["available_revenue_funds"] > 0


@pytest.mark.integration
def test_deal_tape_analytics_integration():
    resp = client.get("/deal/green-lion-2026-1/tape-analytics")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 3
    for period in body:
        assert period["loan_count"] > 0
        assert period["pool_balance_eur"] > 0
        assert "wtd_ltv" in period["pool_stats"]
        assert "current_pct" in period["arrears_breakdown"]
