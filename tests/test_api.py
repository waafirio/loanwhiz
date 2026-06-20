"""Tests for the LoanWhiz REST API.

Heavy primitive / agent calls (Gemini, tape downloads, waterfall maths) are
mocked so the unit tests run offline. The one real end-to-end test is marked
``@pytest.mark.integration`` and hits the live primitives.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from loanwhiz.agent.executor import ExecutionResult, ValidationStatus
from loanwhiz.api import app
from loanwhiz.config import GREEN_LION
from loanwhiz.primitives.covenant_monitor import CovenantMonitor

client = TestClient(app)


@pytest.fixture(autouse=True)
def _isolate_deal_model_seed_dir(tmp_path_factory):
    """Point ``DEAL_MODEL_SEED_DIR`` at an empty dir for every test by default.

    The committed deal-model seed (#196) makes ``_load_cached_deal_model`` fall
    back to a shipped model on a runtime-cache miss. Tests that exercise the
    *cold* path (``not_cached`` / ``DEFAULT_TRIGGERS`` fallback) assume a miss
    means "no model", so without this fixture the real Green Lion seed would
    leak in and flip them to ``cached``. Isolating the seed dir to an empty
    per-session tmp path preserves those tests' cold semantics; the seed-aware
    tests override this patch with their own populated dir (or the real dir).
    """
    empty = tmp_path_factory.mktemp("empty_seed_dir")
    with patch("loanwhiz.api.main.DEAL_MODEL_SEED_DIR", str(empty)):
        yield


# The Green Lion deal context references one ESMA tape per monthly reporting
# period; the deal endpoints fan out across all of them. Drive count
# expectations off the config (currently the 3 2026 tapes) rather than a
# hardcoded literal so the suite tracks the deal's real tape set.
GREEN_LION_TAPE_COUNT = len(GREEN_LION["tape_urls"])


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
# Committed deal-model seed fallback (#196 — Overview cold-cache)
# ---------------------------------------------------------------------------


def test_deal_model_served_from_seed_when_runtime_cache_cold(tmp_path):
    """A cold runtime cache + a committed seed → the endpoint serves the seed.

    This is the clean-checkout case: ``data/deals/*.json`` is gitignored and
    empty, but the committed seed under ``src/loanwhiz/data/deals/seed`` lets
    the Overview render Capital Structure / Triggers / Completeness instead of
    a blank 'not extracted' screen. The runtime cache dir is empty; the seed
    dir carries a Green Lion model.
    """
    cache_dir = tmp_path / "cache"
    seed_dir = tmp_path / "seed"
    cache_dir.mkdir()
    seeded = _seed_cached_deal_model(str(seed_dir))

    with patch("loanwhiz.api.main.DEAL_MODEL_CACHE_DIR", str(cache_dir)), patch(
        "loanwhiz.api.main.DEAL_MODEL_SEED_DIR", str(seed_dir)
    ):
        resp = client.get("/deal/green-lion-2026-1/model")

    assert resp.status_code == 200
    body = resp.json()
    assert body["extraction_status"] == "cached"
    assert body["completeness_score"] == seeded["metadata"]["completeness_score"]
    assert body["trigger_names"] == seeded["trigger_names"]
    assert body["deal_model"] is not None
    assert body["deal_model"]["tranche_structure"] == seeded["tranche_structure"]


def test_runtime_cache_wins_over_seed(tmp_path):
    """When both the runtime cache and the seed have the deal, the runtime cache
    wins — a fresh cold extraction must override the shipped seed."""
    cache_dir = tmp_path / "cache"
    seed_dir = tmp_path / "seed"
    cached = _seed_cached_deal_model(str(cache_dir))
    # Give the seed a distinguishable completeness so we can tell which won.
    seeded = _seed_cached_deal_model(str(seed_dir))
    seed_path = seed_dir / "green-lion-2026-1-bv.json"
    seeded["metadata"]["completeness_score"] = 0.25
    seed_path.write_text(json.dumps(seeded), encoding="utf-8")

    with patch("loanwhiz.api.main.DEAL_MODEL_CACHE_DIR", str(cache_dir)), patch(
        "loanwhiz.api.main.DEAL_MODEL_SEED_DIR", str(seed_dir)
    ):
        resp = client.get("/deal/green-lion-2026-1/model")

    assert resp.status_code == 200
    # The runtime cache's completeness (0.75), not the seed's overridden 0.25.
    assert resp.json()["completeness_score"] == cached["metadata"][
        "completeness_score"
    ]


def test_deal_model_not_cached_when_no_seed_and_cold_cache(tmp_path):
    """With both the runtime cache AND the seed dir empty, the endpoint still
    degrades gracefully — ``not_cached`` / ``deal_model=None``, no extraction.

    Guards that a deal without a committed seed keeps the original cold-path
    behaviour rather than 500-ing on a missing seed file."""
    cache_dir = tmp_path / "cache"
    seed_dir = tmp_path / "seed"
    cache_dir.mkdir()
    seed_dir.mkdir()

    with patch("loanwhiz.api.main.DEAL_MODEL_CACHE_DIR", str(cache_dir)), patch(
        "loanwhiz.api.main.DEAL_MODEL_SEED_DIR", str(seed_dir)
    ), patch("loanwhiz.extraction.assembler.extract_deal_model") as mock_extract:
        resp = client.get("/deal/green-lion-2026-1/model")

    assert resp.status_code == 200
    body = resp.json()
    assert body["extraction_status"] == "not_cached"
    assert body["deal_model"] is None
    mock_extract.assert_not_called()


def test_committed_green_lion_seed_exists_and_validates():
    """The committed Green Lion seed artifact ships and validates as a DealModel.

    This is the artifact a clean checkout serves; if it stops being tracked (an
    over-broad ``.gitignore`` rule) or drifts from the schema, the Overview goes
    blank again. Validating it against the real ``DEAL_MODEL_SEED_DIR`` and the
    live ``DealModel`` is the regression guard.
    """
    from loanwhiz.api import main as api_main
    from loanwhiz.config import GREEN_LION
    from loanwhiz.extraction.assembler import DealModel, _slug

    # Resolve the *real* committed seed dir from the package layout — the
    # autouse fixture patches ``api_main.DEAL_MODEL_SEED_DIR`` to an empty tmp
    # dir, so reading the attribute here would point at the wrong place.
    real_seed_dir = (
        Path(api_main.__file__).resolve().parents[1] / "data" / "deals" / "seed"
    )
    seed_path = real_seed_dir / f"{_slug(GREEN_LION['deal_name'])}.json"
    assert seed_path.exists(), f"committed seed missing: {seed_path}"

    model = DealModel.model_validate_json(seed_path.read_text(encoding="utf-8"))
    assert model.metadata.deal_name == GREEN_LION["deal_name"]
    assert model.tranche_structure, "seed has no tranche structure"
    assert model.trigger_names, "seed has no trigger names"
    assert 0.0 <= model.metadata.completeness_score <= 1.0
    # The committed seed must carry no host-specific absolute path.
    assert not Path(model.metadata.cache_path).is_absolute()


# Seasoned deals (#206 / #208) — the same pipeline that produced the 2026-1 seed
# above, run unmodified on the two registered seasoned deals. Sourced from the
# registry (data/deals.json) so the test is data-agnostic: no per-deal literals.
_SEASONED_DEAL_IDS = ("green-lion-2023-1", "green-lion-2024-1")


@pytest.mark.parametrize("deal_id", _SEASONED_DEAL_IDS)
def test_committed_seasoned_deal_seed_exists_and_validates(deal_id):
    """Each seasoned-deal seed ships, validates as a DealModel, and has real shape.

    V2 (#208) of the seasoned-deal-validation epic proves the extraction pipeline
    is data-agnostic: the *unmodified* ``extraction/`` pipeline that produced the
    Green Lion 2026-1 seed also produces shaped models for the 2023-1 / 2024-1
    seasoned deals. These are the artifacts a clean checkout serves to the Overview
    (and that downstream V4/V5 load warm); if either stops being tracked or drifts
    from the schema, the seasoned deal's Overview goes blank. The assertions mirror
    ``test_committed_green_lion_seed_exists_and_validates`` but are driven off the
    registry so no deal-specific content is hardcoded.
    """
    from loanwhiz.api import main as api_main
    from loanwhiz.config import DEAL_REGISTRY
    from loanwhiz.extraction.assembler import DealModel, _slug

    deal_name = DEAL_REGISTRY[deal_id]["deal_name"]

    # Resolve the *real* committed seed dir from the package layout — the autouse
    # fixture patches ``api_main.DEAL_MODEL_SEED_DIR`` to an empty tmp dir.
    real_seed_dir = (
        Path(api_main.__file__).resolve().parents[1] / "data" / "deals" / "seed"
    )
    seed_path = real_seed_dir / f"{_slug(deal_name)}.json"
    assert seed_path.exists(), f"committed seasoned-deal seed missing: {seed_path}"

    model = DealModel.model_validate_json(seed_path.read_text(encoding="utf-8"))

    # Provenance matches the registered deal — no cross-deal mix-up.
    assert model.metadata.deal_name == deal_name
    assert model.metadata.prospectus_url == DEAL_REGISTRY[deal_id]["prospectus_url"]

    # Real extracted shape (the proof the pipeline ran on real content, not an
    # empty skeleton): a tranche structure, triggers, and ≥1 waterfall with steps.
    assert model.tranche_structure, f"{deal_id} seed has no tranche structure"
    assert model.trigger_names, f"{deal_id} seed has no trigger names"
    assert any(
        wf.get("steps") for wf in model.waterfalls.values()
    ), f"{deal_id} seed has no waterfall with extracted steps"

    assert 0.0 <= model.metadata.completeness_score <= 1.0
    # The committed seed must carry no host-specific absolute path.
    assert not Path(model.metadata.cache_path).is_absolute()


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


def _small_reconstructed_series(
    n_transitions: int = 2, *, original_pool_balance: float = 1_063_600_000.0
):
    """Build a real (offline) reconstructed ``DealStateSeries`` for the S9 rewire.

    Threads ``reconstruct_period_series`` (the same S6 engine the endpoints read)
    over hand-built ``PeriodInput``s — no tape downloads — so the wired
    ``/waterfall`` / ``/compliance`` / ``/reconciliation`` behaviour can be tested
    against a genuine amortizing ledger. Each transition collects real interest +
    principal and a small realized loss, so tranche balances amortize and PDL /
    cumulative loss move period to period.
    """
    from loanwhiz.primitives.deal_state import PeriodCollections
    from loanwhiz.primitives.period_state_machine import (
        PeriodInput,
        reconstruct_period_series,
    )

    cap = {
        "class_a_balance": 1_000_000_000.0,
        "class_a_rate_pct": 3.62,
        "class_b_balance": 53_100_000.0,
        "class_c_balance": 10_500_000.0,
    }
    periods = [
        PeriodInput(
            reporting_date=f"2024-0{idx + 2}-28",
            collections=PeriodCollections(
                interest=3_000_000.0,
                scheduled_principal=8_000_000.0,
                prepayment=2_000_000.0,
                recovery=100_000.0,
                realized_loss=500_000.0,
            ),
            days_in_period=30,
        )
        for idx in range(n_transitions)
    ]
    return reconstruct_period_series(
        capital_structure=cap,
        reserve_target=10_636_000.0,
        original_pool_balance=original_pool_balance,
        seed_reporting_date="2024-01-31",
        periods=periods,
    )


def test_deal_compliance_runs_monitor(tmp_path):
    tape_dump = {"row_count": 100, "field_coverage": 0.98}
    compliance_dump = {
        "trigger_statuses": [],
        "active_triggers": [],
        "near_miss_triggers": [],
        "summary": "All covenants within limits.",
    }

    series = _small_reconstructed_series()
    # Empty cache dir → no extracted triggers → fall back to DEFAULT_TRIGGERS.
    # /compliance builds its per-period tape analytics via the on-disk-cached
    # ``_normalised_tape_output`` helper (one call per tape), not a direct
    # ``EsmaTapeNormaliser().execute`` — patch the helper accordingly.
    with patch("loanwhiz.api.main.DEAL_MODEL_CACHE_DIR", str(tmp_path)), patch(
        "loanwhiz.api.main._normalised_tape_output", return_value=tape_dump
    ) as mock_norm, patch(
        "loanwhiz.api.main._reconstruct_series", return_value=series
    ), patch("loanwhiz.api.main.CovenantMonitor") as MockMon:
        MockMon.DEFAULT_TRIGGERS = []
        MockMon.return_value.execute.return_value = _FakeResult(compliance_dump)

        resp = client.get("/deal/green-lion-2026-1/compliance")

    assert resp.status_code == 200
    assert resp.json() == compliance_dump
    # One normalise call per tape in the deal context (via the cached helper).
    assert mock_norm.call_count == GREEN_LION_TAPE_COUNT
    MockMon.return_value.execute.assert_called_once()
    # The monitor was fed the reconstructed per-period states (the one ledger),
    # not a single seeded snapshot.
    covenant_input = MockMon.return_value.execute.call_args.args[0]
    assert covenant_input.period_states == series.states


def test_deal_compliance_uses_green_lion_pool_balance_by_default():
    """A deal with no ``original_pool_balance`` key falls back to Green Lion's.

    The denominator drives the clean-up-call trigger and cumulative-loss-rate;
    Green Lion (no key in its registry context) must keep the closing balance
    of €1,063,600,000 so existing behaviour is unchanged.
    """
    compliance_dump = {"trigger_statuses": [], "summary": "ok"}

    # The reconstructed series is seeded from the resolved (Green Lion default)
    # original pool balance, and the covenant input's denominator comes from
    # that series' first state (the one ledger), not the deal context directly.
    series = _small_reconstructed_series()
    with patch(
        "loanwhiz.api.main.EsmaTapeNormaliser"
    ) as MockNorm, patch(
        "loanwhiz.api.main._reconstruct_series", return_value=series
    ), patch("loanwhiz.api.main.CovenantMonitor") as MockMon:
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

    series = _small_reconstructed_series(original_pool_balance=500_000_000.0)
    with patch.object(api_main, "DEALS", augmented), patch(
        "loanwhiz.api.main.EsmaTapeNormaliser"
    ) as MockNorm, patch(
        "loanwhiz.api.main._reconstruct_series", return_value=series
    ), patch("loanwhiz.api.main.CovenantMonitor") as MockMon:
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

    series = _small_reconstructed_series()
    with patch("loanwhiz.api.main.DEAL_MODEL_CACHE_DIR", str(tmp_path)), patch(
        "loanwhiz.api.main.EsmaTapeNormaliser"
    ) as MockNorm, patch(
        "loanwhiz.api.main._reconstruct_series", return_value=series
    ), patch("loanwhiz.api.main.CovenantMonitor", _SpyMonitor):
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

    series = _small_reconstructed_series()
    with patch("loanwhiz.api.main.DEAL_MODEL_CACHE_DIR", str(tmp_path)), patch(
        "loanwhiz.api.main.EsmaTapeNormaliser"
    ) as MockNorm, patch(
        "loanwhiz.api.main._reconstruct_series", return_value=series
    ), patch("loanwhiz.api.main.CovenantMonitor", _SpyMonitor):
        MockNorm.return_value.execute.return_value = _FakeResult({"row_count": 1})
        resp = client.get("/deal/green-lion-2026-1/compliance")

    assert resp.status_code == 200
    # Fell back to the monitor's hardcoded Green Lion defaults.
    assert captured["triggers"] == CovenantMonitor.DEFAULT_TRIGGERS


def test_deal_compliance_unknown_returns_404():
    resp = client.get("/deal/unknown/compliance")
    assert resp.status_code == 404


def test_deal_compliance_proximity_series_is_non_flat():
    """The structural proximity series moves across periods (the S9 outcome).

    With the real covenant monitor run over the reconstructed per-period states
    (no monitor mock), a structural trigger's proximity is NOT constant across
    periods — proving PDL/reserve/loss now flow from the one ledger instead of a
    single seeded snapshot (which produced a flat curve).
    """
    # Reconstruct with several transitions carrying realized losses so the
    # cumulative-loss / PDL structural metrics genuinely move period to period.
    series = _small_reconstructed_series(n_transitions=4)
    # Real EsmaTapeNormaliser would fetch tapes; feed a minimal period dict so the
    # monitor runs offline. The structural metrics come from period_states (the
    # reconstructed series), so the tape periods only need a reporting_date.
    with patch(
        "loanwhiz.api.main._reconstruct_series", return_value=series
    ), patch("loanwhiz.api.main.EsmaTapeNormaliser") as MockNorm:
        MockNorm.return_value.execute.return_value = _FakeResult(
            {"reporting_date": "2024-01-31"}
        )
        resp = client.get("/deal/green-lion-2026-1/compliance")

    assert resp.status_code == 200
    body = resp.json()
    # The cumulative-loss trigger's proximity should not be identical across all
    # periods (it was flat before per-period states were plumbed in).
    loss_prox = [
        s["proximity_pct"]
        for s in body["trigger_statuses"]
        if s["trigger_name"] == "cumulative_loss_trigger"
        and s["proximity_pct"] is not None
    ]
    assert len(loss_prox) >= 2
    assert len(set(loss_prox)) > 1, "structural proximity series is flat"


# ---------------------------------------------------------------------------
# Reconciliation — read-only over the one ledger (S9, #189)
# ---------------------------------------------------------------------------


def test_deal_reconciliation_surfaces_ledger_invariants():
    """`/reconciliation` exposes the reconstructed series' headline invariants."""
    series = _small_reconstructed_series(n_transitions=3)
    final = series.final_state
    with patch("loanwhiz.api.main._reconstruct_series", return_value=series):
        resp = client.get("/deal/green-lion-2026-1/reconciliation")

    assert resp.status_code == 200
    body = resp.json()
    assert body["deal_id"] == "green-lion-2026-1"
    assert body["period_count"] == len(series.states)
    assert body["final_reporting_date"] == final.reporting_date
    assert body["class_a_balance"] == final.class_a_balance
    assert body["cumulative_losses"] == final.cumulative_losses
    assert body["pool_factor"] == pytest.approx(final.pool_factor)
    assert body["original_pool_balance"] == final.original_pool_balance


def test_deal_reconciliation_unknown_returns_404():
    resp = client.get("/deal/unknown/reconciliation")
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
# Waterfall — sourced from the one reconstructed ledger (S9, #189)
# ---------------------------------------------------------------------------


def test_deal_waterfall_sources_from_reconstructed_ledger():
    """`/waterfall` reports the latest reconstructed period from the one ledger.

    S9 rewire: tranche opening/closing balances are the **amortizing** balances
    of the latest transition's opening/closing DealState (not static constants),
    and the cascade is the S6-recorded execution trace — sourced from
    ``_reconstruct_series``, not a single-period ``WaterfallRunner`` on hardcoded
    reserve=0/0 / pdl=0/0.
    """
    series = _small_reconstructed_series()
    opening = series.states[-2]
    closing = series.states[-1]

    with patch("loanwhiz.api.main._reconstruct_series", return_value=series):
        resp = client.get("/deal/green-lion-2026-1/waterfall")

    assert resp.status_code == 200
    body = resp.json()
    assert body["deal_id"] == "green-lion-2026-1"
    # Reporting period is the latest reconstructed closing date.
    assert body["reporting_period"] == closing.reporting_date
    # The 11-step Revenue Priority of Payments cascade is surfaced from S6's
    # execution trace.
    assert len(body["revenue_waterfall"]) == 11
    # Per-tranche distributions carry the AMORTIZING reconstructed balances.
    dists = {t["tranche"]: t for t in body["tranche_distributions"]}
    assert set(dists) == {"class_a", "class_b", "class_c"}
    assert dists["class_a"]["opening_balance"] == opening.class_a_balance
    assert dists["class_a"]["closing_balance"] == closing.class_a_balance
    # Principal received == the balance redeemed this period (amortization).
    assert dists["class_a"]["principal_received"] == pytest.approx(
        max(0.0, opening.class_a_balance - closing.class_a_balance)
    )


def test_deal_waterfall_balances_amortize_period_to_period():
    """Tranche balances move down across periods — the core S9 outcome.

    The closing Class A balance of the latest period is strictly below the
    seeded (period-0) opening balance, proving the endpoint reads a real
    amortizing ledger rather than the old static prospectus constant.
    """
    series = _small_reconstructed_series(n_transitions=3)
    seed = series.states[0]
    with patch("loanwhiz.api.main._reconstruct_series", return_value=series):
        resp = client.get("/deal/green-lion-2026-1/waterfall")

    assert resp.status_code == 200
    body = resp.json()
    class_a = next(t for t in body["tranche_distributions"] if t["tranche"] == "class_a")
    assert class_a["closing_balance"] < seed.class_a_balance


def test_deal_waterfall_single_tape_returns_empty_cascade():
    """A deal with no reconstructed transition returns an empty cascade.

    The series carries only the seeded period-0 state (no ``period_results``),
    so the endpoint reports the seed date with an empty cascade rather than
    erroring.
    """
    series = _small_reconstructed_series(n_transitions=0)
    with patch("loanwhiz.api.main._reconstruct_series", return_value=series):
        resp = client.get("/deal/green-lion-2026-1/waterfall")

    assert resp.status_code == 200
    body = resp.json()
    assert body["revenue_waterfall"] == []
    assert body["tranche_distributions"] == []
    assert body["reporting_period"] == series.states[0].reporting_date


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
        "data_source": "direct",
    }


def _tape_dump_for(tape: dict) -> dict:
    """A normalised-tape dump for one config tape, derived from its date.

    Gives each tape a distinct, deterministic ``reporting_date`` (the tape's
    own config date) and a balance that declines monotonically over the pool's
    life, so per-period assertions stay meaningful without hardcoding a fixed
    set of three tapes.
    """
    index = GREEN_LION["tape_urls"].index(tape)
    pool_balance = 1_050_000_000.0 - index * 1_000_000.0
    return _tape_output_dump(tape["date"], pool_balance)


def _by_url_normaliser_side_effect():
    """A ``side_effect`` mapping each tape URL to its dump.

    The endpoint normalises ``EsmaTapeInput(file_url=url)`` per tape and the
    per-tape cache is keyed by URL, so keying the fake off ``inp.file_url``
    returns one consistent result per tape regardless of call order or caching.
    """
    by_url = {
        tape["url"]: _FakeResult(_tape_dump_for(tape))
        for tape in GREEN_LION["tape_urls"]
    }
    return lambda inp: by_url[inp.file_url]


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
    tapes = GREEN_LION["tape_urls"]

    with patch("loanwhiz.api.main.EsmaTapeNormaliser") as MockNorm:
        MockNorm.return_value.execute.side_effect = _by_url_normaliser_side_effect()
        resp = client.get("/deal/green-lion-2026-1/tape-analytics")

    assert resp.status_code == 200
    body = resp.json()
    # One analytics object per tape in the deal context, chronological order.
    assert len(body) == GREEN_LION_TAPE_COUNT
    assert MockNorm.return_value.execute.call_count == GREEN_LION_TAPE_COUNT
    assert [p["tape_date"] for p in body] == [t["date"] for t in tapes]
    assert [p["pool_balance_eur"] for p in body] == [
        _tape_dump_for(t)["pool_balance_eur"] for t in tapes
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
        "data_source",
    }
    for period in body:
        assert expected_keys <= set(period)
    # Ingestion provenance surfaces honestly on every period (#239).
    assert all(p["data_source"] == "direct" for p in body)
    # Weighted LTV surfaces through pool_stats.
    assert body[0]["pool_stats"]["wtd_ltv"] == 65.0


def test_deal_tape_analytics_unknown_returns_404():
    resp = client.get("/deal/unknown/tape-analytics")
    assert resp.status_code == 404


def test_deal_tape_analytics_computes_each_tape_once_across_calls(_isolated_tape_cache):
    """Repeated /tape-analytics calls normalise each tape exactly once.

    Two requests over an N-tape deal would, without caching, run the normaliser
    2N times. With the keyed cache (memo + on-disk JSON), each tape is computed
    once: total execute() calls == number of tapes, not 2× that.
    """
    with patch("loanwhiz.api.main.EsmaTapeNormaliser") as MockNorm:
        MockNorm.return_value.execute.side_effect = _by_url_normaliser_side_effect()
        first = client.get("/deal/green-lion-2026-1/tape-analytics")
        second = client.get("/deal/green-lion-2026-1/tape-analytics")

    assert first.status_code == 200
    assert second.status_code == 200
    # Both responses identical (served from cache the second time).
    assert first.json() == second.json()
    # One compute per tape across BOTH requests — not two.
    assert MockNorm.return_value.execute.call_count == GREEN_LION_TAPE_COUNT


def test_deal_tape_analytics_on_disk_cache_survives_fresh_process(_isolated_tape_cache):
    """A populated on-disk cache serves a 'fresh process' (empty memo) without
    re-running the normaliser.

    Simulates a restart: prime the cache via one request, clear the in-process
    memo (as a new process would have), then request again with the normaliser
    patched to raise — proving the second request reads from disk only.
    """
    from loanwhiz.api import main as api_main

    with patch("loanwhiz.api.main.EsmaTapeNormaliser") as MockNorm:
        MockNorm.return_value.execute.side_effect = _by_url_normaliser_side_effect()
        primed = client.get("/deal/green-lion-2026-1/tape-analytics")
    assert primed.status_code == 200
    # On-disk artifacts written, one per tape.
    assert len(list(_isolated_tape_cache.glob("*.json"))) == GREEN_LION_TAPE_COUNT

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


def test_primitives_carry_reachability():
    """Every catalogue entry carries a valid `reachability` field (#197)."""
    resp = client.get("/primitives")
    assert resp.status_code == 200
    body = resp.json()
    assert body  # non-empty
    for entry in body:
        assert "reachability" in entry
        assert entry["reachability"] in {"live", "library-only"}


def test_primitives_reachability_marks_live_vs_library_only():
    """The live/library-only split is honest (#197).

    The four data primitives — each called by a REST endpoint AND exposed as a
    LangGraph agent tool — plus `audit_logger` (now wired into the REST primitive
    path) are marked `live`. `cashflow_projector` / `report_verifier` /
    `multi_period_waterfall_runner` are registered (so they appear in the
    catalogue) but reached by no endpoint or agent tool, so they are
    `library-only` — nothing is advertised as live that a client can't reach.
    """
    resp = client.get("/primitives")
    assert resp.status_code == 200
    by_name = {entry["name"]: entry["reachability"] for entry in resp.json()}

    for name in (
        "esma_tape_normaliser",
        "collections_aggregator",
        "covenant_monitor",
        "waterfall_runner",
        "audit_logger",
    ):
        assert by_name[name] == "live", f"{name} should be live"

    for name in (
        "cashflow_projector",
        "report_verifier",
        "multi_period_waterfall_runner",
    ):
        assert by_name[name] == "library-only", f"{name} should be library-only"


# ---------------------------------------------------------------------------
# audit_logger wired into the REST primitive path (#197)
# ---------------------------------------------------------------------------


def _read_audit_entries(log_dir: Path) -> list[dict]:
    """Read every AuditLogEntry JSONL line written under ``log_dir``."""
    entries: list[dict] = []
    for jsonl in sorted(log_dir.rglob("*.jsonl")):
        for line in jsonl.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


def test_deal_project_writes_audit_entries(tmp_path):
    """A real (un-mocked) deterministic REST primitive call emits AuditLogEntry.

    The audit_logger primitive claims "every primitive call gets an audit entry";
    this drives the un-mocked `/project` waterfall path (pure math, no network)
    and asserts a real `AuditLogEntry` lands under the patched audit log dir,
    carrying the provenance fields the catalogue advertises.
    """
    with patch("loanwhiz.api.main.API_AUDIT_LOG_DIR", str(tmp_path)):
        resp = client.post(
            "/deal/green-lion-2026-1/project",
            json={"scenarios": ["base", "stress"], "months": 12},
        )
    assert resp.status_code == 200

    entries = _read_audit_entries(tmp_path)
    # One waterfall run per requested scenario → at least two audit entries.
    assert len(entries) >= 2
    for entry in entries:
        assert entry["primitive_name"] == "waterfall_runner"
        # Full provenance the audit_logger catalogue claim advertises.
        assert len(entry["input_hash"]) == 64
        assert len(entry["output_hash"]) == 64
        assert 0.0 <= entry["confidence"] <= 1.0
        assert "executed_at" in entry
        assert isinstance(entry["human_review_required"], bool)


def test_audit_side_write_does_not_break_mocked_endpoint(tmp_path):
    """The best-effort audit wrapper never 500s an endpoint.

    The existing endpoint tests mock the primitive with a `_FakeResult` stand-in
    that lacks real `confidence`/`citations`; the audit step must swallow that
    and leave the endpoint response unchanged (no audit entry written).
    """
    waterfall_dump = {
        "reporting_period": "projection+12m (base)",
        "revenue_waterfall": [],
        "redemption_waterfall": [],
        "tranche_distributions": [],
        "total_distributed": 0.0,
        "shortfall": 0.0,
    }
    with patch("loanwhiz.api.main.API_AUDIT_LOG_DIR", str(tmp_path)), patch(
        "loanwhiz.api.main.WaterfallRunner"
    ) as MockRunner:
        MockRunner.return_value.execute.return_value = _FakeResult(waterfall_dump)
        resp = client.post(
            "/deal/green-lion-2026-1/project",
            json={"scenarios": ["base"], "months": 12},
        )
    assert resp.status_code == 200
    # The stand-in result is not a real PrimitiveResult, so the best-effort
    # audit wrote nothing — but crucially the endpoint did not error.
    assert _read_audit_entries(tmp_path) == []


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
    assert len(body) == GREEN_LION_TAPE_COUNT
    for period in body:
        assert period["loan_count"] > 0
        assert period["pool_balance_eur"] > 0
        assert "wtd_ltv" in period["pool_stats"]
        assert "current_pct" in period["arrears_breakdown"]


# ---------------------------------------------------------------------------
# Engine validation  —  GET /deal/{deal_id}/validation  (#212, V6 / epic #206)
# ---------------------------------------------------------------------------
# Offline + deterministic: runs V4's engine_validation_harness against the
# committed seed + committed Notes & Cash fixture (no network, no LLM). These
# are NOT integration-marked — they must run in the fast suite, mirroring
# test_engine_validation_harness.


def test_validation_green_lion_2024_1_reproduces_published_pop():
    """The headline proof, over HTTP: revenue 11/11, redemption 4/4, to the cent."""
    resp = client.get("/deal/green-lion-2024-1/validation")
    assert resp.status_code == 200
    body = resp.json()

    assert body["available"] is True
    assert body["deal_id"] == "green-lion-2024-1"
    assert body["passed"] is True
    assert body["periods_checked"] >= 1
    assert body["periods_passed"] == body["periods_checked"]
    assert body["tolerance_eur"] == pytest.approx(0.01)
    assert body["source_note"]
    assert body["summary"]

    period = body["periods"][0]
    # Revenue: 11 steps, every one reconciled to the cent.
    assert len(period["revenue"]["steps"]) == 11
    assert period["revenue"]["steps_passed"] == 11
    assert period["revenue"]["passed"] is True
    # Redemption: 4 steps, every one reconciled.
    assert len(period["redemption"]["steps"]) == 4
    assert period["redemption"]["steps_passed"] == 4
    assert period["redemption"]["passed"] is True


def test_validation_carries_honest_source_labels():
    """Every step carries an honest engine/report-supplied/residual source label."""
    body = client.get("/deal/green-lion-2024-1/validation").json()
    period = body["periods"][0]

    sources = {s["source"] for s in period["revenue"]["steps"]}
    # The proof must not be a blanket 100% — it mixes engine-computed,
    # report-supplied, and a residual sweep.
    assert "engine" in sources
    assert "report-supplied" in sources
    assert sources <= {"engine", "report-supplied", "residual"}

    # At least the four engine-COMPUTED revenue lines (Class A/B/C interest +
    # reserve/PDL needs) are present — the independent part of the proof.
    engine_steps = [s for s in period["revenue"]["steps"] if s["source"] == "engine"]
    assert len(engine_steps) >= 4
    assert all(s["passed"] for s in engine_steps)


def test_validation_surfaces_redemption_unapplied_rounding():
    """The documented ~€0.69 redemption rounding remainder is surfaced, not hidden."""
    body = client.get("/deal/green-lion-2024-1/validation").json()
    redemption = body["periods"][0]["redemption"]
    # The fixtured period leaves €0.69 of redemption funds unapplied due to
    # rounding — a real published line, presented honestly.
    assert redemption["unapplied_rounding"] == pytest.approx(0.69, abs=0.01)


def test_validation_unfixtured_deal_degrades_gracefully():
    """A registered deal with no committed fixture returns 200 available=false."""
    resp = client.get("/deal/green-lion-2023-1/validation")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["deal_id"] == "green-lion-2023-1"
    assert body["note"]  # honest "no published proof" note
    assert body["periods"] == []
    assert body["passed"] is False


def test_validation_unknown_deal_returns_404():
    resp = client.get("/deal/does-not-exist/validation")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Per-deal structural config resolution + loud GL fallback (#268)
# ---------------------------------------------------------------------------
#
# These tests cover the demotion of the ``_GREEN_LION_*`` constants from a
# silent default to a labelled last-resort fallback consulted ONLY for the
# in-code Green Lion 2026-1 deal. A non-GL deal that supplies its own config
# resolves to its own numbers (with no GL constant consulted); a non-GL deal
# missing required config and lacking a usable extracted model fails loudly
# (HTTP 422). Green Lion's own resolution is unchanged.


def _sponsor_capital_structure() -> dict:
    return {
        "class_a_balance": 480_000_000.0,
        "class_a_rate_pct": 4.10,
        "class_b_balance": 15_000_000.0,
        "class_c_balance": 5_000_000.0,
    }


def _sponsor_deal(**extra) -> dict:
    deal = {
        "deal_name": "Sponsor Deal 2025-1 B.V.",
        "prospectus_url": "https://example.test/sponsor-2025-1-prospectus.pdf",
        "tape_urls": [
            {"date": "2025-11-30", "url": "https://example.test/sponsor-202511.csv"},
            {"date": "2025-12-31", "url": "https://example.test/sponsor-202512.csv"},
        ],
        "investor_report_urls": [],
    }
    deal.update(extra)
    return deal


# --- unit: the resolver itself -----------------------------------------------


def test_resolve_structural_config_uses_deal_context_not_green_lion():
    """A non-GL deal supplying its own config resolves to ITS numbers, no GL.

    This is the spec's "no Green-Lion-2026-1 fallback was consulted" assertion
    applied to per-deal config: the resolved values must equal the deal's own
    context, and must NOT equal any ``_GREEN_LION_*`` constant.
    """
    from loanwhiz.api import main as api_main

    sponsor = _sponsor_deal(
        capital_structure=_sponsor_capital_structure(),
        reserve_account_target=5_000_000.0,
        original_pool_balance=500_000_000.0,
    )

    cap, reserve, pool = api_main._resolve_structural_config("sponsor-2025-1", sponsor)

    assert cap == _sponsor_capital_structure()
    assert reserve == 5_000_000.0
    assert pool == 500_000_000.0
    # None of the resolved values is the Green Lion last-resort constant.
    assert cap is not api_main._GREEN_LION_CAPITAL_STRUCTURE
    assert cap["class_a_balance"] != api_main._GREEN_LION_CLASS_A_BALANCE
    assert reserve != api_main._GREEN_LION_RESERVE_TARGET
    assert pool != api_main._GREEN_LION_ORIGINAL_POOL_BALANCE


def test_resolve_structural_config_green_lion_uses_last_resort_constants():
    """Green Lion (no structural keys) resolves to its labelled constants.

    Regression lock: GL-2026-1's context omits the structural keys on purpose
    because the constants ARE its config — its resolution must be unchanged so
    its output stays byte-identical.
    """
    from loanwhiz.api import main as api_main

    gl = api_main.DEALS["green-lion-2026-1"]
    cap, reserve, pool = api_main._resolve_structural_config("green-lion-2026-1", gl)

    assert cap == api_main._GREEN_LION_CAPITAL_STRUCTURE
    assert reserve == api_main._GREEN_LION_RESERVE_TARGET
    assert pool == api_main._GREEN_LION_ORIGINAL_POOL_BALANCE


@pytest.mark.parametrize(
    "missing_key,supplied",
    [
        ("capital_structure", {"reserve_account_target": 1.0, "original_pool_balance": 2.0}),
        ("reserve_account_target", {"capital_structure": _sponsor_capital_structure(), "original_pool_balance": 2.0}),
        ("original_pool_balance", {"capital_structure": _sponsor_capital_structure(), "reserve_account_target": 1.0}),
    ],
)
def test_resolve_structural_config_non_gl_missing_key_raises_422(missing_key, supplied):
    """A non-GL deal missing any required structural key fails loudly (422).

    The labelled error must name the deal and the missing key — never silently
    borrow Green Lion's number for it.
    """
    from fastapi import HTTPException

    from loanwhiz.api import main as api_main

    sponsor = _sponsor_deal(**supplied)
    with pytest.raises(HTTPException) as exc:
        api_main._resolve_structural_config("sponsor-2025-1", sponsor)
    assert exc.value.status_code == 422
    assert "sponsor-2025-1" in exc.value.detail
    assert missing_key in exc.value.detail


def test_resolve_projection_base_non_gl_missing_raises_422():
    from fastapi import HTTPException

    from loanwhiz.api import main as api_main

    with pytest.raises(HTTPException) as exc:
        api_main._resolve_projection_base("sponsor-2025-1", _sponsor_deal())
    assert exc.value.status_code == 422
    assert "projection_base" in exc.value.detail
    assert "sponsor-2025-1" in exc.value.detail


def test_resolve_projection_base_green_lion_uses_last_resort():
    from loanwhiz.api import main as api_main

    gl = api_main.DEALS["green-lion-2026-1"]
    assert (
        api_main._resolve_projection_base("green-lion-2026-1", gl)
        is api_main._GREEN_LION_PROJECTION_BASE
    )


# --- unit: extracted-model bridge --------------------------------------------


def _build_deal_model(tranches: list[dict]):
    from loanwhiz.extraction.assembler import DealModel, DealModelMetadata

    return DealModel(
        metadata=DealModelMetadata(
            deal_name="Sponsor Deal 2025-1 B.V.",
            prospectus_url="https://example.test/sponsor-2025-1-prospectus.pdf",
            extracted_at="2026-01-01T00:00:00Z",
            extraction_duration_sec=1.0,
            sections_found=[],
            completeness_score=0.5,
            cache_path="/tmp/x.json",
        ),
        definitions={},
        waterfalls={},
        covenants={},
        tranche_structure=tranches,
        trigger_names=[],
    )


def test_extracted_capital_structure_complete_numeric_rate():
    """A complete extracted structure with a numeric coupon resolves to caps."""
    from loanwhiz.api import main as api_main

    model = _build_deal_model(
        [
            {"name": "Class A", "size_eur": 480_000_000.0, "rate": 4.10, "seniority": 0},
            {"name": "Class B", "size_eur": 15_000_000.0, "rate": None, "seniority": 1},
            {"name": "Class C", "size_eur": 5_000_000.0, "rate": None, "seniority": 2},
        ]
    )
    with patch("loanwhiz.api.main._load_cached_deal_model", return_value=model):
        cap = api_main._extracted_capital_structure(_sponsor_deal())

    assert cap == {
        "class_a_balance": 480_000_000.0,
        "class_a_rate_pct": 4.10,
        "class_b_balance": 15_000_000.0,
        "class_c_balance": 5_000_000.0,
    }


def test_extracted_capital_structure_non_numeric_rate_returns_none():
    """A EURIBOR/margin reference coupon is not coerced — bridge yields None.

    The engine needs a numeric ``class_a_rate_pct``; a reference-rate string
    ("3m EURIBOR + 0.42") cannot be turned into one without fabricating a value,
    so the bridge reports "no usable value" and resolution falls through.
    """
    from loanwhiz.api import main as api_main

    model = _build_deal_model(
        [
            {"name": "Class A", "size_eur": 480_000_000.0, "rate": "3m EURIBOR + 0.42", "seniority": 0},
            {"name": "Class B", "size_eur": 15_000_000.0, "rate": None, "seniority": 1},
            {"name": "Class C", "size_eur": 5_000_000.0, "rate": None, "seniority": 2},
        ]
    )
    with patch("loanwhiz.api.main._load_cached_deal_model", return_value=model):
        assert api_main._extracted_capital_structure(_sponsor_deal()) is None


def test_extracted_capital_structure_missing_class_returns_none():
    from loanwhiz.api import main as api_main

    model = _build_deal_model(
        [
            {"name": "Class A", "size_eur": 480_000_000.0, "rate": 4.10, "seniority": 0},
        ]
    )
    with patch("loanwhiz.api.main._load_cached_deal_model", return_value=model):
        assert api_main._extracted_capital_structure(_sponsor_deal()) is None


def test_resolve_structural_config_falls_through_to_extracted_model():
    """With no context key, a complete extracted model supplies capital_structure.

    Resolution tier 2: deals.json has no ``capital_structure`` but the cached
    extracted model does — so the deal resolves to ITS extracted structure, not
    Green Lion's. (Reserve/pool still must be in the context — the extracted
    model carries neither — so they are supplied here.)
    """
    from loanwhiz.api import main as api_main

    model = _build_deal_model(
        [
            {"name": "Class A", "size_eur": 480_000_000.0, "rate": 4.10, "seniority": 0},
            {"name": "Class B", "size_eur": 15_000_000.0, "rate": None, "seniority": 1},
            {"name": "Class C", "size_eur": 5_000_000.0, "rate": None, "seniority": 2},
        ]
    )
    sponsor = _sponsor_deal(
        reserve_account_target=5_000_000.0,
        original_pool_balance=500_000_000.0,
    )
    with patch("loanwhiz.api.main._load_cached_deal_model", return_value=model):
        cap, reserve, pool = api_main._resolve_structural_config(
            "sponsor-2025-1", sponsor
        )

    assert cap == _sponsor_capital_structure()
    assert cap["class_a_balance"] != api_main._GREEN_LION_CLASS_A_BALANCE


# --- integration: endpoints fail loudly for a misconfigured non-GL deal ------


def test_waterfall_misconfigured_non_gl_deal_returns_422():
    """``/waterfall`` on a non-GL deal with no structural config returns 422.

    No ``_reconstruct_series`` patch — the real resolver runs and must raise
    before any tape fetch, so the misconfiguration surfaces as 422 rather than
    silently borrowing Green Lion's structure (or 500-ing on a network call).
    """
    from loanwhiz.api import main as api_main

    augmented = {**api_main.DEALS, "sponsor-2025-1": _sponsor_deal()}
    with patch.object(api_main, "DEALS", augmented):
        resp = client.get("/deal/sponsor-2025-1/waterfall")

    assert resp.status_code == 422
    assert "sponsor-2025-1" in resp.json()["detail"]
    assert "capital_structure" in resp.json()["detail"]


def test_compliance_misconfigured_non_gl_deal_returns_422():
    from loanwhiz.api import main as api_main

    augmented = {**api_main.DEALS, "sponsor-2025-1": _sponsor_deal()}
    # Stub the per-tape normalise (network boundary) — the misconfiguration must
    # surface as the resolver's 422 from ``_reconstruct_series``, not a network
    # error from the tape-analytics fetch.
    with patch.object(api_main, "DEALS", augmented), patch(
        "loanwhiz.api.main._normalised_tape_output", return_value={"row_count": 1}
    ):
        resp = client.get("/deal/sponsor-2025-1/compliance")

    assert resp.status_code == 422
    assert "sponsor-2025-1" in resp.json()["detail"]
    assert "capital_structure" in resp.json()["detail"]


def test_project_misconfigured_non_gl_deal_returns_422():
    from loanwhiz.api import main as api_main

    augmented = {**api_main.DEALS, "sponsor-2025-1": _sponsor_deal()}
    with patch.object(api_main, "DEALS", augmented):
        resp = client.post(
            "/deal/sponsor-2025-1/project", json={"scenarios": ["base"], "months": 12}
        )

    assert resp.status_code == 422
    assert "projection_base" in resp.json()["detail"]


def test_waterfall_self_configured_non_gl_deal_does_not_consult_green_lion(tmp_path):
    """A self-configured non-GL deal seeds the engine with ITS structure.

    Spies on ``reconstruct_period_series`` (the engine entry the resolved config
    feeds) to assert the seeded ``capital_structure`` / reserve / pool are the
    deal's own — and that NO ``_GREEN_LION_*`` constant reached the engine.

    Hits the REAL ``_reconstruct_series`` body (the resolver path), so the
    in-process memo is cleared and the disk cache is pointed at an empty tmp dir
    to guarantee the spy actually fires (no stale cache hit short-circuits it).
    """
    from loanwhiz.api import main as api_main

    sponsor = _sponsor_deal(
        capital_structure=_sponsor_capital_structure(),
        reserve_account_target=5_000_000.0,
        original_pool_balance=500_000_000.0,
    )
    augmented = {**api_main.DEALS, "sponsor-2025-1": sponsor}

    captured = {}

    def _fake_reconstruct(*, capital_structure, reserve_target, original_pool_balance, **kw):
        captured["capital_structure"] = capital_structure
        captured["reserve_target"] = reserve_target
        captured["original_pool_balance"] = original_pool_balance
        # A minimal real series so the endpoint renders without a tape fetch.
        return _small_reconstructed_series(original_pool_balance=original_pool_balance)

    from loanwhiz.primitives.deal_state import PeriodCollections

    # The mocked aggregator's ``execute(...).output.to_period_collections()`` must
    # return a real ``PeriodCollections`` so the (real) ``_reconstruct_series``
    # period loop validates before the spied engine call.
    agg_output = MagicMock()
    agg_output.to_period_collections.return_value = PeriodCollections(
        interest=1_000.0,
        scheduled_principal=1_000.0,
        prepayment=0.0,
        recovery=0.0,
        realized_loss=0.0,
    )
    mock_aggregator = MagicMock()
    mock_aggregator.execute.return_value.output = agg_output

    api_main._RECONSTRUCTION_MEMO.clear()
    with patch.object(api_main, "DEALS", augmented), patch(
        "loanwhiz.api.main.RECONSTRUCTION_CACHE_DIR", str(tmp_path)
    ), patch(
        "loanwhiz.api.main.CollectionsAggregator", return_value=mock_aggregator
    ), patch(
        "loanwhiz.api.main.reconstruct_period_series", side_effect=_fake_reconstruct
    ):
        resp = client.get("/deal/sponsor-2025-1/waterfall")

    assert resp.status_code == 200
    assert captured["capital_structure"] == _sponsor_capital_structure()
    assert captured["reserve_target"] == 5_000_000.0
    assert captured["original_pool_balance"] == 500_000_000.0
    # The Green Lion last-resort constants never reached the engine.
    assert captured["capital_structure"]["class_a_balance"] != api_main._GREEN_LION_CLASS_A_BALANCE
    assert captured["reserve_target"] != api_main._GREEN_LION_RESERVE_TARGET
    assert captured["original_pool_balance"] != api_main._GREEN_LION_ORIGINAL_POOL_BALANCE
