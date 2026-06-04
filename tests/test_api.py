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
from loanwhiz.primitives.covenant_monitor import CovenantMonitor

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
# Deal registry — GET /deals (#131)
# ---------------------------------------------------------------------------


def test_deals_lists_green_lion():
    """GET /deals returns the available deals (id + name); Green Lion present."""
    resp = client.get("/deals")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert {"id": "green-lion-2026-1", "name": "Green Lion 2026-1 B.V."} in body
    # Each entry carries exactly id + name (the selector contract).
    for entry in body:
        assert set(entry) == {"id", "name"}


def test_deals_surfaces_second_registered_deal():
    """Adding a deal to the registry surfaces it in GET /deals — data, not code.

    Patches the module-level ``DEALS`` with an extra deal (as a non-code
    addition would, via config/data) and confirms it appears in the listing
    alongside Green Lion.
    """
    from loanwhiz.api import main as api_main

    extra = {
        "deal_name": "Sponsor Deal 2025-1 B.V.",
        "prospectus_url": "https://example.test/sponsor-2025-1-prospectus.pdf",
        "tape_urls": [],
        "investor_report_urls": [],
    }
    augmented = {**api_main.DEALS, "sponsor-2025-1": extra}
    with patch.object(api_main, "DEALS", augmented):
        resp = client.get("/deals")

    assert resp.status_code == 200
    body = resp.json()
    ids = {entry["id"] for entry in body}
    assert {"green-lion-2026-1", "sponsor-2025-1"} <= ids
    assert {"id": "sponsor-2025-1", "name": "Sponsor Deal 2025-1 B.V."} in body


# ---------------------------------------------------------------------------
# Deal registry — config-driven loading (#131)
# ---------------------------------------------------------------------------


def test_registry_contains_green_lion():
    """The config-driven registry always carries Green Lion as a default."""
    from loanwhiz.config import DEAL_REGISTRY, GREEN_LION

    assert "green-lion-2026-1" in DEAL_REGISTRY
    assert DEAL_REGISTRY["green-lion-2026-1"] is GREEN_LION
    assert DEAL_REGISTRY["green-lion-2026-1"]["deal_name"] == "Green Lion 2026-1 B.V."


def test_registry_merges_deal_from_data_file(tmp_path):
    """A deal added to data/deals.json is merged into the registry — no code.

    Demonstrates the non-code-addition path: write a deals.json with an extra
    deal, point the loader at it, and confirm the new deal joins the in-code
    Green Lion default.
    """
    from loanwhiz.config import _load_deal_registry

    data_file = tmp_path / "deals.json"
    data_file.write_text(
        json.dumps(
            {
                "sponsor-2025-1": {
                    "deal_name": "Sponsor Deal 2025-1 B.V.",
                    "prospectus_url": "https://example.test/p.pdf",
                    "tape_urls": [],
                    "investor_report_urls": [],
                }
            }
        ),
        encoding="utf-8",
    )

    registry = _load_deal_registry(data_file)
    # In-code default still present, plus the data-file deal.
    assert "green-lion-2026-1" in registry
    assert registry["sponsor-2025-1"]["deal_name"] == "Sponsor Deal 2025-1 B.V."


def test_registry_tolerates_missing_data_file(tmp_path):
    """An absent data file yields just the in-code defaults (no crash)."""
    from loanwhiz.config import _load_deal_registry

    registry = _load_deal_registry(tmp_path / "does-not-exist.json")
    assert "green-lion-2026-1" in registry


def test_registry_tolerates_malformed_data_file(tmp_path):
    """A malformed data file is ignored; defaults still load (never takes API down)."""
    from loanwhiz.config import _load_deal_registry

    bad = tmp_path / "deals.json"
    bad.write_text("{ not valid json", encoding="utf-8")
    registry = _load_deal_registry(bad)
    assert "green-lion-2026-1" in registry


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


def test_deal_compliance_runs_monitor(tmp_path):
    tape_dump = {"row_count": 100, "field_coverage": 0.98}
    compliance_dump = {
        "trigger_statuses": [],
        "active_triggers": [],
        "near_miss_triggers": [],
        "summary": "All covenants within limits.",
    }

    # Empty cache dir → no extracted triggers → fall back to DEFAULT_TRIGGERS.
    with patch("loanwhiz.api.main.DEAL_MODEL_CACHE_DIR", str(tmp_path)), patch(
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


def test_deal_compliance_uses_green_lion_pool_balance_by_default():
    """A deal with no ``original_pool_balance`` key falls back to Green Lion's.

    The denominator drives the clean-up-call trigger and cumulative-loss-rate;
    Green Lion (no key in its registry context) must keep the closing balance
    of €1,063,600,000 so existing behaviour is unchanged.
    """
    compliance_dump = {"trigger_statuses": [], "summary": "ok"}

    with patch(
        "loanwhiz.api.main.EsmaTapeNormaliser"
    ) as MockNorm, patch("loanwhiz.api.main.CovenantMonitor") as MockMon:
        MockNorm.return_value.execute.return_value = _FakeResult({"row_count": 1})
        MockMon.DEFAULT_TRIGGERS = []
        MockMon.return_value.execute.return_value = _FakeResult(compliance_dump)

        resp = client.get("/deal/green-lion-2026-1/compliance")

    assert resp.status_code == 200
    covenant_input = MockMon.return_value.execute.call_args.args[0]
    assert covenant_input.original_pool_balance == 1_063_600_000.0


def test_deal_compliance_resolves_pool_balance_from_deal_context():
    """A deal carrying ``original_pool_balance`` overrides the Green Lion default.

    Mirrors the #151 ``capital_structure`` resolution: a deal added as data can
    supply its own pool balance, and ``/compliance`` threads it into the
    covenant monitor as the loss-rate / clean-up-call denominator.
    """
    from loanwhiz.api import main as api_main

    sponsor = {
        "deal_name": "Sponsor Deal 2025-1 B.V.",
        "prospectus_url": "https://example.test/sponsor-2025-1-prospectus.pdf",
        "tape_urls": [
            {"date": "2025-12-31", "url": "https://example.test/sponsor-202512.csv"},
        ],
        "investor_report_urls": [],
        "original_pool_balance": 500_000_000.0,
    }
    augmented = {**api_main.DEALS, "sponsor-2025-1": sponsor}
    compliance_dump = {"trigger_statuses": [], "summary": "ok"}

    with patch.object(api_main, "DEALS", augmented), patch(
        "loanwhiz.api.main.EsmaTapeNormaliser"
    ) as MockNorm, patch("loanwhiz.api.main.CovenantMonitor") as MockMon:
        MockNorm.return_value.execute.return_value = _FakeResult({"row_count": 1})
        MockMon.DEFAULT_TRIGGERS = []
        MockMon.return_value.execute.return_value = _FakeResult(compliance_dump)

        resp = client.get("/deal/sponsor-2025-1/compliance")

    assert resp.status_code == 200
    covenant_input = MockMon.return_value.execute.call_args.args[0]
    assert covenant_input.original_pool_balance == 500_000_000.0


def _seed_cached_deal_model_with_triggers(cache_dir: str, triggers: list[dict]) -> None:
    """Seed a cached Green Lion DealModel whose covenants carry ``triggers``."""
    model = _seed_cached_deal_model(cache_dir)
    from loanwhiz.config import GREEN_LION
    from loanwhiz.extraction.assembler import _slug

    model["covenants"]["triggers"] = triggers
    slug = _slug(GREEN_LION["deal_name"])
    (Path(cache_dir) / f"{slug}.json").write_text(json.dumps(model), encoding="utf-8")


def test_deal_compliance_uses_extracted_triggers(tmp_path):
    """When the cached deal model carries extracted triggers, the monitor is
    fed those (mapped onto TriggerDefinition), NOT the hardcoded defaults."""
    extracted = [
        {
            "name": "custom_loss_trigger",
            "display_name": "Custom Loss Trigger",
            "description": "Fires when the cumulative loss rate exceeds 3.5%.",
            "metric": "default_pct",
            "threshold": 3.5,
            "threshold_unit": "percentage",
            "direction": "above",
            "consequence": "Principal switches to sequential.",
            "section_reference": "Section 6.1",
            "citation": {
                "document": "Custom Deal Prospectus",
                "page_or_row": "Section 6.1",
                "excerpt": "If the loss rate exceeds 3.5% ...",
            },
        },
        {
            "name": "custom_pdl_trigger",
            "display_name": "Custom PDL Trigger",
            "description": "Any debit balance on the PDL fires the trigger.",
            "metric": "pdl_class_a",
            "threshold": None,
            "threshold_unit": None,
            "direction": "non_zero",
            "consequence": "Distributions diverted to cure the PDL.",
            "section_reference": "Section 6.2",
            "citation": {},
        },
    ]
    _seed_cached_deal_model_with_triggers(str(tmp_path), extracted)

    captured: dict = {}

    class _SpyMonitor:
        DEFAULT_TRIGGERS = CovenantMonitor.DEFAULT_TRIGGERS

        def execute(self, input):  # noqa: A002 - mirror primitive signature
            captured["triggers"] = input.triggers
            return _FakeResult({"summary": "ok"})

    with patch("loanwhiz.api.main.DEAL_MODEL_CACHE_DIR", str(tmp_path)), patch(
        "loanwhiz.api.main.EsmaTapeNormaliser"
    ) as MockNorm, patch("loanwhiz.api.main.CovenantMonitor", _SpyMonitor):
        MockNorm.return_value.execute.return_value = _FakeResult({"row_count": 1})
        resp = client.get("/deal/green-lion-2026-1/compliance")

    assert resp.status_code == 200
    fed = captured["triggers"]
    # The deal's own extracted triggers reached the monitor — not the defaults.
    assert [t.name for t in fed] == ["custom_loss_trigger", "custom_pdl_trigger"]
    assert {t.name for t in fed} != {
        t.name for t in CovenantMonitor.DEFAULT_TRIGGERS
    }
    # Mapping: "above"/threshold pass through; "non_zero" → above + None.
    loss, pdl = fed
    assert loss.direction == "above" and loss.threshold == 3.5
    assert loss.metric == "default_pct"
    assert pdl.direction == "above" and pdl.threshold is None
    # Citation rebuilt from the (empty) dict using section_reference / display.
    assert pdl.citation.document == "prospectus"
    assert pdl.citation.page_or_row == "Section 6.2"
    assert pdl.citation.excerpt == "Custom PDL Trigger"


def test_deal_compliance_falls_back_when_no_extracted_triggers(tmp_path):
    """Empty covenants.triggers (and cache miss) → fall back to DEFAULT_TRIGGERS."""
    # Seeded model exists but carries an empty triggers list.
    _seed_cached_deal_model(str(tmp_path))

    captured: dict = {}

    class _SpyMonitor:
        DEFAULT_TRIGGERS = CovenantMonitor.DEFAULT_TRIGGERS

        def execute(self, input):  # noqa: A002
            captured["triggers"] = input.triggers
            return _FakeResult({"summary": "ok"})

    with patch("loanwhiz.api.main.DEAL_MODEL_CACHE_DIR", str(tmp_path)), patch(
        "loanwhiz.api.main.EsmaTapeNormaliser"
    ) as MockNorm, patch("loanwhiz.api.main.CovenantMonitor", _SpyMonitor):
        MockNorm.return_value.execute.return_value = _FakeResult({"row_count": 1})
        resp = client.get("/deal/green-lion-2026-1/compliance")

    assert resp.status_code == 200
    # Fell back to the monitor's hardcoded Green Lion defaults.
    assert captured["triggers"] == CovenantMonitor.DEFAULT_TRIGGERS


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


def test_deal_project_default_base_is_green_lion():
    """With no ``projection_base`` on the deal, the projection uses the Green
    Lion base (unchanged default branch)."""
    from loanwhiz.api import main as api_main

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
    # The waterfall ran on the Green Lion projection base (default branch).
    gl = api_main._GREEN_LION_PROJECTION_BASE
    wf_input = MockRunner.return_value.execute.call_args.args[0]
    assert wf_input.class_a_balance == gl["class_a_balance"]
    assert wf_input.class_b_balance == gl["class_b_balance"]
    assert wf_input.class_c_balance == gl["class_c_balance"]
    assert wf_input.class_a_rate_pct == gl["class_a_rate_pct"]
    assert wf_input.reserve_account_balance == gl["reserve_account_balance"]
    assert wf_input.reserve_account_target == gl["reserve_account_target"]


def test_deal_project_uses_resolved_deal_base():
    """The projection runs against the *selected* deal's projection base.

    Regression for #160: ``deal_project`` previously always read the
    module-level ``_GREEN_LION_PROJECTION_BASE``, so projections ignored the
    selected deal's own capital structure / pool balance. With a second deal
    registered carrying an explicit ``projection_base``, the ``WaterfallRunner``
    must be driven by *that* deal's base — not Green Lion's.
    """
    from loanwhiz.api import main as api_main

    sponsor_base = {
        "current_pool_balance": 500_000_000.0,
        "class_a_balance": 480_000_000.0,
        "class_b_balance": 15_000_000.0,
        "class_c_balance": 5_000_000.0,
        "class_a_rate_pct": 4.10,
        "reserve_account_balance": 5_000_000.0,
        "reserve_account_target": 5_000_000.0,
    }
    sponsor = {
        "deal_name": "Sponsor Deal 2025-1 B.V.",
        "prospectus_url": "https://example.test/sponsor-2025-1-prospectus.pdf",
        "tape_urls": [
            {"date": "2025-12-31", "url": "https://example.test/sponsor-202512.csv"},
        ],
        "investor_report_urls": [],
        "projection_base": sponsor_base,
    }
    augmented = {**api_main.DEALS, "sponsor-2025-1": sponsor}

    waterfall_dump = {
        "reporting_period": "projection+12m (base)",
        "revenue_waterfall": [],
        "redemption_waterfall": [],
        "tranche_distributions": [],
        "total_distributed": 0.0,
        "shortfall": 0.0,
    }
    with patch.object(api_main, "DEALS", augmented), patch(
        "loanwhiz.api.main.WaterfallRunner"
    ) as MockRunner:
        MockRunner.return_value.execute.return_value = _FakeResult(waterfall_dump)
        resp = client.post(
            "/deal/sponsor-2025-1/project",
            json={"scenarios": ["base"], "months": 12},
        )

    assert resp.status_code == 200
    assert resp.json()["deal_id"] == "sponsor-2025-1"

    # The waterfall ran on the sponsor's projection base, not Green Lion's.
    wf_input = MockRunner.return_value.execute.call_args.args[0]
    assert wf_input.class_a_balance == 480_000_000.0
    assert wf_input.class_b_balance == 15_000_000.0
    assert wf_input.class_c_balance == 5_000_000.0
    assert wf_input.class_a_rate_pct == 4.10
    assert wf_input.reserve_account_balance == 5_000_000.0
    assert wf_input.reserve_account_target == 5_000_000.0
    # And the collection sizing derives from the sponsor's pool balance.
    assert wf_input.available_revenue_funds == pytest.approx(
        500_000_000.0 * 0.04 * (12 / 12.0)
    )
    assert wf_input.available_principal_funds == pytest.approx(
        500_000_000.0 * 0.10 * (12 / 12.0)
    )


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


def test_deal_waterfall_uses_resolved_deal_not_green_lion():
    """The waterfall runs against the *selected* deal's tapes + capital structure.

    Regression for #151: the handler previously read ``GREEN_LION["tape_urls"]``
    and the hard-coded Green Lion capital-structure constants, so it returned
    Green Lion's waterfall for any deal. With a second deal registered (its own
    tapes + an explicit ``capital_structure``), the aggregator and runner must be
    driven by *that* deal's values — not Green Lion's.
    """
    from types import SimpleNamespace

    from loanwhiz.api import main as api_main

    sponsor = {
        "deal_name": "Sponsor Deal 2025-1 B.V.",
        "prospectus_url": "https://example.test/sponsor-2025-1-prospectus.pdf",
        "tape_urls": [
            {"date": "2025-11-30", "url": "https://example.test/sponsor-202511.csv"},
            {"date": "2025-12-31", "url": "https://example.test/sponsor-202512.csv"},
        ],
        "investor_report_urls": [],
        "capital_structure": {
            "class_a_balance": 500_000_000.0,
            "class_a_rate_pct": 4.10,
            "class_b_balance": 25_000_000.0,
            "class_c_balance": 5_000_000.0,
        },
    }
    augmented = {**api_main.DEALS, "sponsor-2025-1": sponsor}

    collections_out = SimpleNamespace(
        available_revenue_funds=2_000_000.0,
        available_principal_funds=6_000_000.0,
        senior_fees=25_000.0,
        pool_balance_eur=500_000_000.0,
    )
    waterfall_out = SimpleNamespace(
        reporting_period="2025-12-31",
        revenue_waterfall=[],
        tranche_distributions=[],
        total_distributed=0.0,
        shortfall=0.0,
    )

    with patch.object(api_main, "DEALS", augmented), patch(
        "loanwhiz.api.main.CollectionsAggregator"
    ) as MockAgg, patch("loanwhiz.api.main.WaterfallRunner") as MockRunner:
        MockAgg.return_value.execute.return_value = _FakeOutput(collections_out)
        MockRunner.return_value.execute.return_value = _FakeOutput(waterfall_out)

        resp = client.get("/deal/sponsor-2025-1/waterfall")

    assert resp.status_code == 200
    body = resp.json()
    assert body["deal_id"] == "sponsor-2025-1"
    assert body["reporting_period"] == "2025-12-31"

    # Tapes resolved from the SELECTED deal, not Green Lion: the latest call
    # aggregates the sponsor's latest tape URL.
    agg_calls = MockAgg.return_value.execute.call_args_list
    latest_input = agg_calls[-1].args[0]
    assert latest_input.tape_file_url == "https://example.test/sponsor-202512.csv"
    # Capital structure resolved from the deal context (sponsor's values).
    assert latest_input.class_a_balance == 500_000_000.0
    assert latest_input.class_a_rate_pct == 4.10

    # The waterfall ran on the sponsor's capital structure too.
    wf_input = MockRunner.return_value.execute.call_args.args[0]
    assert wf_input.class_a_balance == 500_000_000.0
    assert wf_input.class_b_balance == 25_000_000.0
    assert wf_input.class_c_balance == 5_000_000.0
    assert wf_input.class_a_rate_pct == 4.10


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
# Primitives registry catalogue
# ---------------------------------------------------------------------------


def test_primitives_returns_catalogue():
    """GET /primitives returns a non-empty catalogue with the expected fields."""
    resp = client.get("/primitives")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) > 0
    # Every entry carries the catalogue fields.
    expected_keys = {
        "name",
        "version",
        "description",
        "author",
        "tags",
        "class_name",
        "input_schema",
        "output_schema",
        "confidence",
    }
    for entry in body:
        assert expected_keys <= set(entry)
        assert isinstance(entry["name"], str) and entry["name"]
        assert isinstance(entry["version"], str) and entry["version"]
        assert isinstance(entry["description"], str) and entry["description"]
        assert isinstance(entry["tags"], list)


def test_primitives_includes_known_primitive():
    """The catalogue includes a known primitive with its registered metadata."""
    resp = client.get("/primitives")
    assert resp.status_code == 200
    by_name = {entry["name"]: entry for entry in resp.json()}

    assert "esma_tape_normaliser" in by_name
    esma = by_name["esma_tape_normaliser"]
    assert esma["class_name"] == "EsmaTapeNormaliser"
    assert "esma" in esma["tags"]
    # Typed I/O schemas surfaced for the UI.
    assert esma["input_schema"]
    assert esma["output_schema"]


def test_primitives_registry_fully_populated():
    """All primitive modules are imported, so non-API-path primitives appear too.

    The deal endpoints only import four primitives; the catalogue must still
    include the ones registered solely via the /primitives import side effects
    (e.g. report_verifier, cashflow_projector, audit_logger).
    """
    resp = client.get("/primitives")
    assert resp.status_code == 200
    names = {entry["name"] for entry in resp.json()}
    assert {
        "esma_tape_normaliser",
        "waterfall_runner",
        "covenant_monitor",
        "collections_aggregator",
        "report_verifier",
        "cashflow_projector",
        "audit_logger",
    } <= names


# ---------------------------------------------------------------------------
# Governance evidence pack (#136) — real logger, temp store
# ---------------------------------------------------------------------------


def _seed_evidence_pack(log_dir: str):
    """Create and persist a known GovernanceEvidencePack into ``log_dir``.

    Uses the real ``EvidencePackLogger`` (only the log directory is redirected
    to a temp path) so the GET endpoint's load path is exercised end-to-end
    without running an agent query. Returns the persisted pack.
    """
    from loanwhiz.governance import (
        EvidencePackLogger,
        GovernanceEvidencePack,
        ToolCallRecord,
    )

    pack = GovernanceEvidencePack.create(
        query="Is the deal compliant?",
        answer="Yes, all covenants are within limits.",
        tool_calls=[
            ToolCallRecord(
                call_index=0,
                tool_name="covenant_monitor",
                input_summary="Run covenants over latest period",
                output_summary="No active triggers",
                confidence=0.92,
                citations=[{"source": "investor_report_2026-04", "page": 3}],
                duration_ms=120.0,
                timestamp="2026-06-03T00:00:00+00:00",
            )
        ],
    )
    EvidencePackLogger(log_dir=log_dir).save(pack)
    return pack


def test_governance_pack_returns_stored_pack(tmp_path):
    """A stored pack is returned in full by GET /governance/{pack_id}."""
    pack = _seed_evidence_pack(str(tmp_path))

    with patch("loanwhiz.api.main.GOVERNANCE_LOG_DIR", str(tmp_path)):
        resp = client.get(f"/governance/{pack.pack_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["pack_id"] == pack.pack_id
    assert body["query"] == "Is the deal compliant?"
    assert body["answer"] == "Yes, all covenants are within limits."
    # Aggregate confidence is the min of tool confidences; one call at 0.92.
    assert body["aggregate_confidence"] == pytest.approx(0.92)
    assert body["human_review_required"] is False
    # Tool-call trace surfaced.
    assert len(body["tool_calls"]) == 1
    call = body["tool_calls"][0]
    assert call["tool_name"] == "covenant_monitor"
    assert call["confidence"] == pytest.approx(0.92)
    # Deduplicated citation trail surfaced.
    assert body["all_citations"] == [
        {"source": "investor_report_2026-04", "page": 3}
    ]
    # Governance metadata present.
    assert body["finos_compliant"] is True
    assert body["framework_version"] == pack.framework_version
    assert body["model_used"] == pack.model_used


def test_governance_pack_flags_human_review_when_low_confidence(tmp_path):
    """A low-confidence pack round-trips with human_review_required True."""
    from loanwhiz.governance import (
        EvidencePackLogger,
        GovernanceEvidencePack,
        ToolCallRecord,
    )

    pack = GovernanceEvidencePack.create(
        query="What's the risk?",
        answer="Uncertain.",
        tool_calls=[
            ToolCallRecord(
                call_index=0,
                tool_name="waterfall_runner",
                input_summary="Project stress scenario",
                output_summary="Shortfall in junior tranche",
                confidence=0.4,
                citations=[],
                duration_ms=50.0,
                timestamp="2026-06-03T00:00:00+00:00",
            )
        ],
    )
    EvidencePackLogger(log_dir=str(tmp_path)).save(pack)

    with patch("loanwhiz.api.main.GOVERNANCE_LOG_DIR", str(tmp_path)):
        resp = client.get(f"/governance/{pack.pack_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["aggregate_confidence"] == pytest.approx(0.4)
    assert body["human_review_required"] is True


def test_governance_pack_unknown_returns_404(tmp_path):
    """An unknown pack id returns 404."""
    with patch("loanwhiz.api.main.GOVERNANCE_LOG_DIR", str(tmp_path)):
        resp = client.get("/governance/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Evidence pack does-not-exist not found"


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
