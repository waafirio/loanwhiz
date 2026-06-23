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
    """GET /deals returns the available deals (id + name + facets); Green Lion present."""
    resp = client.get("/deals")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    green_lion = next(
        (e for e in body if e["id"] == "green-lion-2026-1"), None
    )
    assert green_lion is not None
    assert green_lion["name"] == "Green Lion 2026-1 B.V."
    # Vintage is recovered from the deal name (#344); jurisdiction falls back
    # to "Unknown" when the registry carries none.
    assert green_lion["vintage"] == 2026
    assert isinstance(green_lion["jurisdiction"], str)
    # Each entry carries id + name + the jurisdiction/vintage filtering facets
    # (the picker contract — #344).
    for entry in body:
        assert set(entry) == {"id", "name", "jurisdiction", "vintage"}
        assert isinstance(entry["jurisdiction"], str)
        assert entry["vintage"] is None or isinstance(entry["vintage"], int)


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
    by_id = {entry["id"]: entry for entry in body}
    assert {"green-lion-2026-1", "sponsor-2025-1"} <= set(by_id)
    sponsor = by_id["sponsor-2025-1"]
    assert sponsor["name"] == "Sponsor Deal 2025-1 B.V."
    # Facets are derived even for a data-only deal (no explicit jurisdiction).
    assert sponsor["jurisdiction"] == "Unknown"
    assert sponsor["vintage"] == 2025


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
# Project (forward fold over ScenarioGenerator → run_period, #275)
#
# /project now runs the deal forward through the SAME engine the history
# endpoints use: a ScenarioGenerator emits a synthetic PeriodInputs stream that
# is folded through period_state_machine.run_period — no faked single-period
# WaterfallRunner scaling. These tests drive the real (un-mocked) engine.
# ---------------------------------------------------------------------------


def test_deal_project_runs_forward_fold():
    """/project returns a per-scenario, multi-period projected state series."""
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
    for scenario in ("base", "stress"):
        proj = body["projections"][scenario]
        # The real fold yields one seed state + one closing state per month.
        assert len(proj["periods"]) == 7  # seed + 6 transitions
        # Class A amortises across the projected series (non-increasing).
        class_a = [p["class_a_balance"] for p in proj["periods"]]
        assert all(earlier >= later for earlier, later in zip(class_a, class_a[1:]))


def test_deal_project_defaults():
    """Default request (no body) projects base + stress over 12 months."""
    resp = client.post("/deal/green-lion-2026-1/project", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["months"] == 12
    assert body["scenarios"] == ["base", "stress"]
    assert set(body["projections"]) == {"base", "stress"}


def test_deal_project_includes_wal_per_scenario():
    """A real Class A WAL is surfaced per scenario, inline and in the top map.

    WAL is derived from the engine-computed Class A amortisation across the
    projected series (not the faked "full horizon if any principal"), so it lands
    strictly inside ``(0, months]`` when Class A actually amortises.
    """
    months = 12
    resp = client.post(
        "/deal/green-lion-2026-1/project",
        json={"scenarios": ["base", "stress"], "months": months},
    )
    assert resp.status_code == 200
    body = resp.json()

    assert set(body["projections"]) == {"base", "stress"}
    assert set(body["wal"]) == {"base", "stress"}
    for scenario in ("base", "stress"):
        proj = body["projections"][scenario]
        # WAL additively present on the projection and in the top-level map.
        assert "wal_class_a_months" in proj
        assert "wal_class_a_years" in proj
        wal_months = body["wal"][scenario]["wal_class_a_months"]
        # A real engine-derived WAL: positive and within the horizon.
        assert 0.0 < wal_months <= months
        assert body["wal"][scenario]["wal_class_a_years"] == pytest.approx(
            wal_months / 12.0
        )


def test_deal_project_wal_zero_when_no_class_a_principal():
    """WAL is 0.0 when no Class A principal is returned (no divide-by-zero).

    A zero-month horizon returns the seed state alone — no transitions, so no
    Class A principal repaid and a WAL of 0.0.
    """
    resp = client.post(
        "/deal/green-lion-2026-1/project",
        json={"scenarios": ["base"], "months": 0},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["projections"]["base"]["wal_class_a_months"] == 0.0
    assert body["wal"]["base"]["wal_class_a_years"] == 0.0


def test_deal_project_stress_worse_than_base():
    """Stress (higher CDR + rate shift) yields more cumulative losses than base."""
    resp = client.post(
        "/deal/green-lion-2026-1/project",
        json={"scenarios": ["base", "stress"], "months": 12},
    )
    assert resp.status_code == 200
    body = resp.json()
    base_loss = body["projections"]["base"]["cumulative_losses"]
    stress_loss = body["projections"]["stress"]["cumulative_losses"]
    assert stress_loss > base_loss


def test_deal_project_unknown_returns_404():
    resp = client.post("/deal/unknown/project", json={})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Forward projection — custom assumptions, per-tranche cashflows, per-tranche
# WAL (#319). All additive over the #275 ScenarioGenerator-over-the-fold surface.
# ---------------------------------------------------------------------------


def test_deal_project_per_tranche_cashflows():
    """Each period carries per-tranche principal cashflow = the per-period drop
    in that tranche's outstanding balance, floored at 0; period-0 (seed) is 0."""
    resp = client.post(
        "/deal/green-lion-2026-1/project",
        json={"scenarios": ["base"], "months": 6},
    )
    assert resp.status_code == 200
    periods = resp.json()["projections"]["base"]["periods"]
    # Seed period has zero cashflow (no transition yet).
    seed = periods[0]
    assert seed["class_a_principal_eur"] == 0.0
    assert seed["class_b_principal_eur"] == 0.0
    assert seed["class_c_principal_eur"] == 0.0
    # Each subsequent period's per-tranche cashflow equals the balance drop.
    for tranche in ("class_a", "class_b", "class_c"):
        for prior, cur in zip(periods, periods[1:]):
            expected = max(0.0, prior[f"{tranche}_balance"] - cur[f"{tranche}_balance"])
            assert cur[f"{tranche}_principal_eur"] == pytest.approx(expected)


def test_deal_project_per_tranche_wal():
    """Per-tranche WAL (A/B/C) is surfaced additively; Class A keys are unchanged."""
    resp = client.post(
        "/deal/green-lion-2026-1/project",
        json={"scenarios": ["base"], "months": 12},
    )
    assert resp.status_code == 200
    body = resp.json()
    wal = body["wal"]["base"]
    for key in (
        "wal_class_a_months",
        "wal_class_a_years",
        "wal_class_b_months",
        "wal_class_b_years",
        "wal_class_c_months",
        "wal_class_c_years",
    ):
        assert key in wal
    # Class A still in the (0, months] window (the #275 invariant).
    assert 0.0 < wal["wal_class_a_months"] <= 12
    # The per-scenario projection also carries the full WAL block inline.
    assert body["projections"]["base"]["wal"] == wal


def test_deal_project_custom_assumptions_change_result():
    """Caller-supplied CDR/recovery override the preset and worsen losses."""
    base = client.post(
        "/deal/green-lion-2026-1/project",
        json={"scenarios": ["base"], "months": 6},
    ).json()
    overridden = client.post(
        "/deal/green-lion-2026-1/project",
        json={
            "scenarios": ["base"],
            "months": 6,
            "assumptions": {"base": {"cdr_pct": 8.0, "recovery_pct": 20.0}},
        },
    ).json()
    assert (
        overridden["projections"]["base"]["cumulative_losses"]
        > base["projections"]["base"]["cumulative_losses"]
    )


def test_deal_project_partial_override_keeps_preset_fields():
    """An override of only CDR leaves CPR/recovery/rate-shift at the base preset
    — i.e. it does not reset the un-overridden fields to defaults."""
    # Overriding CDR to the base preset's own value (0.03) must reproduce base.
    base = client.post(
        "/deal/green-lion-2026-1/project",
        json={"scenarios": ["base"], "months": 6},
    ).json()
    same = client.post(
        "/deal/green-lion-2026-1/project",
        json={
            "scenarios": ["base"],
            "months": 6,
            "assumptions": {"base": {"cdr_pct": 0.03}},
        },
    ).json()
    assert (
        same["projections"]["base"]["cumulative_losses"]
        == pytest.approx(base["projections"]["base"]["cumulative_losses"])
    )


def test_deal_project_backward_compatible_without_assumptions():
    """A no-``assumptions`` request returns the prior payload shape — existing
    balance + Class A WAL fields all present."""
    resp = client.post(
        "/deal/green-lion-2026-1/project",
        json={"scenarios": ["base", "stress"], "months": 6},
    )
    assert resp.status_code == 200
    body = resp.json()
    for scenario in ("base", "stress"):
        proj = body["projections"][scenario]
        period = proj["periods"][0]
        for key in (
            "pool_balance_eur",
            "class_a_balance",
            "class_b_balance",
            "class_c_balance",
            "reserve_balance",
            "cumulative_losses",
        ):
            assert key in period
        assert "wal_class_a_months" in proj
        assert "wal_class_a_years" in proj


def test_deal_project_invalid_assumption_returns_422():
    """A CPR outside [0, 100] is a validation error (422), not a 500."""
    resp = client.post(
        "/deal/green-lion-2026-1/project",
        json={
            "scenarios": ["base"],
            "months": 6,
            "assumptions": {"base": {"cpr_pct": 150.0}},
        },
    )
    assert resp.status_code == 422


def test_deal_project_default_base_is_green_lion():
    """With no ``projection_base``/config, the projection seeds from Green Lion.

    The seed (period-0 state) is built from Green Lion's capital structure and
    current pool balance — the period-0 state in the response carries those
    opening tranche balances.
    """
    from loanwhiz.api import main as api_main

    resp = client.post(
        "/deal/green-lion-2026-1/project",
        json={"scenarios": ["base"], "months": 12},
    )
    assert resp.status_code == 200
    body = resp.json()

    gl_cap = api_main._GREEN_LION_CAPITAL_STRUCTURE
    gl_base = api_main._GREEN_LION_PROJECTION_BASE
    seed = body["projections"]["base"]["periods"][0]
    assert seed["period"] == 0
    assert seed["class_a_balance"] == gl_cap["class_a_balance"]
    assert seed["class_b_balance"] == gl_cap["class_b_balance"]
    assert seed["class_c_balance"] == gl_cap["class_c_balance"]
    # Pool opens at the deal's CURRENT balance (the forward starting point).
    assert seed["pool_balance_eur"] == gl_base["current_pool_balance"]


def test_deal_project_uses_resolved_deal_base():
    """The projection seeds from the *selected* deal's own structure/pool.

    Regression for #160: ``deal_project`` must drive the fold off the selected
    deal's capital structure and projection base — not Green Lion's. A second
    deal carrying its own ``capital_structure`` / ``projection_base`` /
    ``reserve_account_target`` / ``original_pool_balance`` must seed from that.
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
        "capital_structure": {
            "class_a_balance": 480_000_000.0,
            "class_b_balance": 15_000_000.0,
            "class_c_balance": 5_000_000.0,
            "class_a_rate_pct": 4.10,
        },
        "reserve_account_target": 5_000_000.0,
        "original_pool_balance": 500_000_000.0,
    }
    augmented = {**api_main.DEALS, "sponsor-2025-1": sponsor}

    with patch.object(api_main, "DEALS", augmented):
        resp = client.post(
            "/deal/sponsor-2025-1/project",
            json={"scenarios": ["base"], "months": 12},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["deal_id"] == "sponsor-2025-1"

    # The fold seeded from the sponsor's structure / pool, not Green Lion's.
    seed = body["projections"]["base"]["periods"][0]
    assert seed["class_a_balance"] == 480_000_000.0
    assert seed["class_b_balance"] == 15_000_000.0
    assert seed["class_c_balance"] == 5_000_000.0
    assert seed["pool_balance_eur"] == 500_000_000.0


def test_deal_project_uses_loan_level_tape_schedule(monkeypatch):
    """/project drives scheduled amortisation off the deal's tape, not the proxy (#281).

    A deal whose tape amortises (here a synthetic single-loan tape) projects a
    different scheduled-principal path than the flat 1%/month proxy would. We
    patch the tape loader so the test is deterministic and offline, then assert
    the engine folded the tape-derived schedule (the per-period available
    principal tracks the loan-level curve, not a flat pool fraction).
    """
    import pandas as pd

    from loanwhiz.api import main as api_main

    # A clean single-loan tape: 500m balance, 0% rate, 5-month term → exactly
    # 100m straight-line scheduled principal per period for 5 periods.
    fake_tape = pd.DataFrame(
        [{"current_balance": 500_000_000.0, "current_interest_rate_pct": 0.0, "remaining_term_months": 5}]
    )
    monkeypatch.setattr(api_main, "_load_tape", lambda url, period: (fake_tape, "direct"))

    tape_deal = {
        "deal_name": "Tape Deal 2025-1 B.V.",
        "prospectus_url": "https://example.test/tape-2025-1.pdf",
        "tape_urls": [
            {"date": "2025-12-31", "url": "https://example.test/tape-2025-1.csv"}
        ],
        "investor_report_urls": [],
        "projection_base": {
            "current_pool_balance": 500_000_000.0,
            "class_a_balance": 480_000_000.0,
            "class_b_balance": 15_000_000.0,
            "class_c_balance": 5_000_000.0,
            "class_a_rate_pct": 4.10,
            "reserve_account_balance": 5_000_000.0,
            "reserve_account_target": 5_000_000.0,
        },
        "capital_structure": {
            "class_a_balance": 480_000_000.0,
            "class_b_balance": 15_000_000.0,
            "class_c_balance": 5_000_000.0,
            "class_a_rate_pct": 4.10,
        },
        "reserve_account_target": 5_000_000.0,
        "original_pool_balance": 500_000_000.0,
    }
    augmented = {**api_main.DEALS, "tape-deal-2025-1": tape_deal}

    with patch.object(api_main, "DEALS", augmented):
        resp = client.post(
            "/deal/tape-deal-2025-1/project",
            # cpr/cdr defaults still apply; the scheduled-principal LEG is what
            # the tape drives, isolated by reading the leg directly below.
            json={"scenarios": ["base"], "months": 5},
        )

    assert resp.status_code == 200
    periods = resp.json()["projections"]["base"]["periods"]
    final_pool = periods[-1]["pool_balance_eur"]
    # The tape schedule repays ~100m/period of scheduled principal (plus
    # scenario prepay/default), draining the 500m pool to near zero over 5
    # periods. The flat 1%/month proxy would peel only ~5m/period of scheduled
    # principal, leaving the pool an order of magnitude higher — so the
    # tape-driven path is unmistakably faster.
    assert final_pool < 1_000_000.0  # < 0.2% of the opening 500m pool
    # Sanity: what the proxy alone would have left after 5 periods of ~1%
    # scheduled amortisation is far larger — guards against the schedule being
    # silently ignored.
    proxy_residual_estimate = 500_000_000.0 * (0.99 ** 5)
    assert final_pool < proxy_residual_estimate / 10.0


def test_deal_project_no_tape_falls_back_to_proxy(monkeypatch):
    """A no-tape deal still projects via the constant-rate proxy (no regression).

    The loader must NOT be invoked for a deal with no ``tape_urls`` — the
    fold uses the proxy and the endpoint returns a healthy amortising series.
    """
    from loanwhiz.api import main as api_main

    def _boom(url, period):  # pragma: no cover - asserts it isn't called
        raise AssertionError("tape loader must not run for a no-tape deal")

    monkeypatch.setattr(api_main, "_load_tape", _boom)

    no_tape_deal = {
        "deal_name": "Report Deal 2024-1 B.V.",
        "prospectus_url": "https://example.test/report-2024-1.pdf",
        "tape_urls": [],
        "investor_report_urls": ["https://example.test/report.pdf"],
        "projection_base": {
            "current_pool_balance": 200_000_000.0,
            "class_a_balance": 190_000_000.0,
            "class_b_balance": 7_000_000.0,
            "class_c_balance": 3_000_000.0,
            "class_a_rate_pct": 3.5,
            "reserve_account_balance": 3_000_000.0,
            "reserve_account_target": 3_000_000.0,
        },
        "capital_structure": {
            "class_a_balance": 190_000_000.0,
            "class_b_balance": 7_000_000.0,
            "class_c_balance": 3_000_000.0,
            "class_a_rate_pct": 3.5,
        },
        "reserve_account_target": 3_000_000.0,
        "original_pool_balance": 200_000_000.0,
    }
    augmented = {**api_main.DEALS, "report-deal-2024-1": no_tape_deal}

    with patch.object(api_main, "DEALS", augmented):
        resp = client.post(
            "/deal/report-deal-2024-1/project",
            json={"scenarios": ["base"], "months": 6},
        )

    assert resp.status_code == 200
    periods = resp.json()["projections"]["base"]["periods"]
    class_a = [p["class_a_balance"] for p in periods]
    assert all(earlier >= later for earlier, later in zip(class_a, class_a[1:]))


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
# Report verification (#320 — report_verifier wired live; Gemini mocked)
# ---------------------------------------------------------------------------
#
# The endpoint diffs the deal's investor-report figures (extracted via Gemini,
# patched here) against the engine-computed distributions of the SAME
# reconstructed ledger ``/waterfall`` reads. We patch ``_extract_figures_with_gemini``
# (the primitive's network boundary) so the tests are offline + deterministic,
# and patch ``_reconstruct_series`` so the computed side is the small real fold.


def _patch_gemini(reported: dict[str, float]):
    """Patch the report_verifier's Gemini extraction to return ``reported``."""
    return patch(
        "loanwhiz.primitives.report_verifier._extract_figures_with_gemini",
        return_value=dict(reported),
    )


def _no_report_cache(monkeypatch):
    """Point the verifier's per-period cache at an empty tmp dir so it extracts.

    Without this the verifier may read a stale ``/tmp/loanwhiz_cache`` entry from
    a prior run instead of calling the patched extractor.
    """
    import pathlib
    import tempfile

    import loanwhiz.primitives.report_verifier as rv

    monkeypatch.setattr(
        rv, "_CACHE_DIR", pathlib.Path(tempfile.mkdtemp()) / "nonexistent"
    )


def test_report_verification_returns_break_report(monkeypatch):
    """`/report-verification` diffs reported vs computed and flags breaks.

    The reported Class A interest is set wildly off the engine-computed value, so
    the line item is flagged as a mismatch (a "break"); the response carries the
    per-line-item comparison, overall_match, summary, confidence, and citations.
    """
    _no_report_cache(monkeypatch)
    series = _small_reconstructed_series()
    # Reported figures: deliberately-wrong Class A interest (a break) plus three
    # figures the engine can source (pool/reserve/collections enrichment).
    reported = {
        "class_a_interest_paid": 999_999_999.0,  # nowhere near computed → break
        "class_a_principal_paid": 1.0,           # also a break
        "pool_balance": series.states[-1].pool_balance,        # matches → ok
        "reserve_fund_balance": series.states[-1].reserve_balance,  # matches → ok
    }

    with patch("loanwhiz.api.main._reconstruct_series", return_value=series), _patch_gemini(reported):
        resp = client.get("/deal/green-lion-2026-1/report-verification")

    assert resp.status_code == 200
    body = resp.json()
    assert body["deal_id"] == "green-lion-2026-1"
    assert body["investor_report_url"].endswith(".pdf")
    # Four reported figures all had a computed counterpart → four checked.
    assert body["figures_checked"] == 4
    by_item = {li["line_item"]: li for li in body["line_items"]}
    # The two deliberately-wrong figures are flagged as breaks.
    assert by_item["class_a_interest_paid"]["match"] is False
    assert by_item["class_a_principal_paid"]["match"] is False
    # The pool/reserve figures fed back exactly → matches within tolerance.
    assert by_item["pool_balance"]["match"] is True
    assert by_item["reserve_fund_balance"]["match"] is True
    assert body["overall_match"] is False
    assert body["figures_mismatched"] == 2
    assert 0.0 <= body["confidence"] <= 1.0
    assert isinstance(body["citations"], list) and body["citations"]


def test_report_verification_period_filter_selects_report(monkeypatch):
    """An explicit ``period`` query selects the matching monthly investor report."""
    _no_report_cache(monkeypatch)
    series = _small_reconstructed_series()
    reported = {"class_a_interest_paid": 1.0}

    with patch("loanwhiz.api.main._reconstruct_series", return_value=series), _patch_gemini(reported):
        resp = client.get(
            "/deal/green-lion-2026-1/report-verification", params={"period": "march 2026"}
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["reporting_period"] == "March 2026"
    assert "march-2026" in body["investor_report_url"]


def test_report_verification_unknown_deal_returns_404():
    resp = client.get("/deal/unknown/report-verification")
    assert resp.status_code == 404


def test_report_verification_no_investor_reports_returns_422(monkeypatch):
    """A deal with no published investor reports → 422 naming the gap."""
    _no_report_cache(monkeypatch)
    series = _small_reconstructed_series()
    deal_no_reports = dict(GREEN_LION)
    deal_no_reports["investor_report_urls"] = []

    with patch("loanwhiz.api.main._require_deal", return_value=deal_no_reports), patch(
        "loanwhiz.api.main._reconstruct_series", return_value=series
    ):
        resp = client.get("/deal/green-lion-2026-1/report-verification")

    assert resp.status_code == 422
    assert "investor_report" in resp.json()["detail"]


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
    """Point the tape-analytics cache (and seed dir) at clean tmp dirs.

    Keeps the analytics-cache tests deterministic: each test starts cold (no
    on-disk artifact, no in-process memo) and never touches the shared
    ``/tmp/loanwhiz_cache/tape_analytics`` dir. The committed seed dir
    (``TAPE_ANALYTICS_SEED_DIR``, #347) is redirected to an *empty* tmp dir too,
    so these tests still exercise the live-normaliser path rather than being
    short-circuited by the real committed seeds — the seed-served behaviour has
    its own dedicated tests below.
    """
    from loanwhiz.api import main as api_main

    saved_memo = dict(api_main._TAPE_ANALYTICS_MEMO)
    api_main._TAPE_ANALYTICS_MEMO.clear()
    cache_dir = tmp_path / "cache"
    seed_dir = tmp_path / "seed"
    cache_dir.mkdir()
    seed_dir.mkdir()
    with patch("loanwhiz.api.main.TAPE_ANALYTICS_CACHE_DIR", str(cache_dir)), patch(
        "loanwhiz.api.main.TAPE_ANALYTICS_SEED_DIR", str(seed_dir)
    ):
        yield cache_dir
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


def _seed_tape(seed_dir: Path, url: str, dump: dict) -> None:
    """Write a committed-seed-shaped ``{sha256(url)}.json`` for *url* (#347)."""
    name = api_main._tape_cache_name(url)
    (seed_dir / name).write_text(json.dumps(dump), encoding="utf-8")


def test_deal_tape_analytics_served_from_seed_without_network(tmp_path):
    """The flagship deal renders pool analytics from the committed seed offline.

    The seed dir is populated for every GL-2026-1 tape and the normaliser is
    patched to RAISE — proving that with no live network and no runtime cache
    the endpoint still returns full analytics from the committed seed (#347).
    """
    seed_dir = tmp_path / "seed"
    cache_dir = tmp_path / "cache"
    seed_dir.mkdir()
    cache_dir.mkdir()
    tapes = GREEN_LION["tape_urls"]
    for tape in tapes:
        _seed_tape(seed_dir, tape["url"], _tape_dump_for(tape))

    saved_memo = dict(api_main._TAPE_ANALYTICS_MEMO)
    api_main._TAPE_ANALYTICS_MEMO.clear()
    try:
        with patch("loanwhiz.api.main.TAPE_ANALYTICS_CACHE_DIR", str(cache_dir)), patch(
            "loanwhiz.api.main.TAPE_ANALYTICS_SEED_DIR", str(seed_dir)
        ), patch("loanwhiz.api.main.EsmaTapeNormaliser") as MockNorm:
            MockNorm.return_value.execute.side_effect = AssertionError(
                "normaliser must not run when a committed seed exists (offline)"
            )
            resp = client.get("/deal/green-lion-2026-1/tape-analytics")
    finally:
        api_main._TAPE_ANALYTICS_MEMO.clear()
        api_main._TAPE_ANALYTICS_MEMO.update(saved_memo)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == GREEN_LION_TAPE_COUNT
    assert [p["tape_date"] for p in body] == [t["date"] for t in tapes]
    # No live network call happened — the normaliser was never constructed.
    MockNorm.assert_not_called()


def test_deal_tape_analytics_degrades_to_partial_when_a_tape_is_unresolvable(tmp_path):
    """An un-resolvable tape is skipped (partial 200), not a fatal 500 (#347).

    Only the first tape is seeded; the rest have no seed and the normaliser is
    patched to raise (offline). The endpoint returns HTTP 200 with just the
    seeded period rather than blanking the whole Pool page.
    """
    seed_dir = tmp_path / "seed"
    cache_dir = tmp_path / "cache"
    seed_dir.mkdir()
    cache_dir.mkdir()
    tapes = GREEN_LION["tape_urls"]
    first = tapes[0]
    _seed_tape(seed_dir, first["url"], _tape_dump_for(first))

    saved_memo = dict(api_main._TAPE_ANALYTICS_MEMO)
    api_main._TAPE_ANALYTICS_MEMO.clear()
    try:
        with patch("loanwhiz.api.main.TAPE_ANALYTICS_CACHE_DIR", str(cache_dir)), patch(
            "loanwhiz.api.main.TAPE_ANALYTICS_SEED_DIR", str(seed_dir)
        ), patch("loanwhiz.api.main.EsmaTapeNormaliser") as MockNorm:
            MockNorm.return_value.execute.side_effect = RuntimeError("offline")
            resp = client.get("/deal/green-lion-2026-1/tape-analytics")
    finally:
        api_main._TAPE_ANALYTICS_MEMO.clear()
        api_main._TAPE_ANALYTICS_MEMO.update(saved_memo)

    assert resp.status_code == 200
    body = resp.json()
    # Only the seeded tape survives; the rest are skipped, not fatal.
    assert len(body) == 1
    assert body[0]["tape_date"] == first["date"]


def test_error_response_carries_cors_headers(tmp_path):
    """A genuine 500 still carries CORS headers so the browser sees the error.

    An unhandled route exception is converted to a 500; without the #347
    exception handler that 500 bypasses ``CORSMiddleware`` and the browser
    reports an opaque CORS error. With the handler, the 500 response carries
    ``access-control-allow-origin`` for the allowed dev origin.
    """
    seed_dir = tmp_path / "seed"
    cache_dir = tmp_path / "cache"
    seed_dir.mkdir()
    cache_dir.mkdir()

    saved_memo = dict(api_main._TAPE_ANALYTICS_MEMO)
    api_main._TAPE_ANALYTICS_MEMO.clear()
    try:
        # Force the route to raise inside the per-tape resolve's *caller* by
        # making the helper itself blow up — patch _tape_analytics_period to
        # raise so the whole route 500s (the degradation helper is bypassed).
        with patch("loanwhiz.api.main.TAPE_ANALYTICS_CACHE_DIR", str(cache_dir)), patch(
            "loanwhiz.api.main.TAPE_ANALYTICS_SEED_DIR", str(seed_dir)
        ), patch(
            "loanwhiz.api.main._tape_analytics_period",
            side_effect=RuntimeError("boom"),
        ):
            # raise_server_exceptions=False so the TestClient returns the 500
            # response (with CORS headers) instead of re-raising the exception.
            local_client = TestClient(app, raise_server_exceptions=False)
            resp = local_client.get(
                "/deal/green-lion-2026-1/tape-analytics",
                headers={"Origin": "http://localhost:3000"},
            )
    finally:
        api_main._TAPE_ANALYTICS_MEMO.clear()
        api_main._TAPE_ANALYTICS_MEMO.update(saved_memo)

    assert resp.status_code == 500
    assert (
        resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
    )


def test_http_exception_still_renders_cleanly_with_cors():
    """The #347 catch-all must NOT swallow HTTPException — 404 stays a clean 404.

    A 404 from ``_require_deal`` must keep its status + carry CORS headers, not
    be turned into a 500 by the new exception handler.
    """
    local_client = TestClient(app, raise_server_exceptions=False)
    resp = local_client.get(
        "/deal/unknown/tape-analytics",
        headers={"Origin": "http://localhost:3000"},
    )
    assert resp.status_code == 404
    assert (
        resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
    )


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
    (e.g. report_verifier, audit_logger).
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
    LangGraph agent tool — plus `audit_logger` (wired into the REST primitive
    path) are marked `live`. `report_verifier` is now `live` too (#320): the
    `/deal/{id}/report-verification` endpoint and the `verify_report` agent tool
    reach it, so nothing is advertised as live that a client can't reach. (The
    duplicate engines `cashflow_projector` / `multi_period_waterfall_runner` were
    deleted in #276, so they no longer appear in the catalogue at all.)
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
        "report_verifier",
    ):
        assert by_name[name] == "live", f"{name} should be live"

    # The deleted duplicate engines must not reappear in the catalogue.
    assert "cashflow_projector" not in by_name
    assert "multi_period_waterfall_runner" not in by_name


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


def test_deal_project_no_longer_audits_waterfall_runner(tmp_path):
    """/project folds the engine forward; it no longer calls ``WaterfallRunner``.

    Before #275 the faked single-period path ran one ``WaterfallRunner.execute``
    per scenario through ``_audit``, leaving ``waterfall_runner`` audit entries.
    /project now runs the ``ScenarioGenerator → run_period`` fold (the same
    engine history uses), which is not routed through the REST ``_audit`` hook —
    so no ``waterfall_runner`` audit entry is written by this endpoint. This
    documents that deliberate change (the per-call audit coverage for the
    reconstruction loop is asserted separately below).
    """
    with patch("loanwhiz.api.main.API_AUDIT_LOG_DIR", str(tmp_path)):
        resp = client.post(
            "/deal/green-lion-2026-1/project",
            json={"scenarios": ["base", "stress"], "months": 12},
        )
    assert resp.status_code == 200

    entries = _read_audit_entries(tmp_path)
    assert not any(e.get("primitive_name") == "waterfall_runner" for e in entries)


def test_reconstruct_series_audits_collections_aggregator(tmp_path):
    """The collections_aggregator calls in the reconstruction loop are audited.

    #277: audit_logger must wrap *every* primitive call. The aggregator call in
    `_reconstruct_series`'s per-tape loop previously bypassed `_audit`. This drives
    the loop with the network-fetching aggregator + the downstream S6 reconstruction
    mocked, and asserts one AuditLogEntry lands per tape transition (3 tapes → 2).
    """
    from unittest.mock import MagicMock

    from loanwhiz.api import main as api_main
    from loanwhiz.primitives.base import AuditEntry, Citation, PrimitiveResult
    from loanwhiz.primitives.collections_aggregator import CollectionsOutput

    fake_collections = CollectionsOutput(
        reporting_period="2026-02-28",
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
        summary="mock period",
    )
    fake_result = PrimitiveResult[CollectionsOutput](
        output=fake_collections,
        confidence=0.8,
        citations=[Citation(document="tape.csv", excerpt="mock")],
        audit_entry=AuditEntry(
            primitive_name="collections_aggregator",
            version="0.1.0",
            input_hash="b" * 64,
            executed_at="2026-04-30T00:00:00+00:00",
            duration_ms=1.0,
        ),
    )

    deal = {
        "tape_urls": [
            {"url": "https://example/t0.csv", "date": "2026-01-31"},
            {"url": "https://example/t1.csv", "date": "2026-02-28"},
            {"url": "https://example/t2.csv", "date": "2026-03-31"},
        ]
    }

    # S6's result is serialised to the cache path, so the stub must json-dump.
    fake_series = MagicMock()
    fake_series.model_dump_json.return_value = "{}"

    # Isolate: fresh memo, tmp cache dir, mocked aggregator + S6 reconstruction.
    api_main._RECONSTRUCTION_MEMO.clear()
    with patch("loanwhiz.api.main.API_AUDIT_LOG_DIR", str(tmp_path)), patch(
        "loanwhiz.api.main._reconstruction_cache_path",
        return_value=tmp_path / "recon.json",
    ), patch(
        "loanwhiz.api.main.CollectionsAggregator.execute", return_value=fake_result
    ), patch(
        "loanwhiz.api.main.reconstruct_period_series", return_value=fake_series
    ):
        # Green Lion 2026-1 id so _resolve_structural_config resolves via the
        # labelled GL last-resort fallback (the deal stub carries no structural
        # config); the tape path is the one under test here.
        api_main._reconstruct_series(api_main._GREEN_LION_DEAL_ID, deal)

    api_main._RECONSTRUCTION_MEMO.clear()

    entries = _read_audit_entries(tmp_path)
    # 3 tapes → 2 transitions → 2 aggregator calls → 2 audit entries.
    assert len(entries) == 2
    for entry in entries:
        assert entry["primitive_name"] == "collections_aggregator"
        assert len(entry["input_hash"]) == 64


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
    # The framework-conformance summary rides along, explaining the boolean.
    assert body["finos_conformance"]["is_conformant"] is True
    assert body["finos_conformance"]["total_controls"] == 23


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


def test_finos_conformance_endpoint_returns_full_catalogue():
    """GET /governance/finos-conformance returns the mapped control catalogue."""
    resp = client.get("/governance/finos-conformance")
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_conformant"] is True
    assert body["total_controls"] == 23
    assert len(body["controls"]) == 23
    # Per-primitive conformance is asserted, not just the aggregate.
    assert body["primitive_conformance"]
    # The static path is not captured by the /governance/{pack_id} route.
    assert body["framework"] == "FINOS AI Governance Framework"


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
# Offline + deterministic: runs the Reconciler over the LIVE folded series
# against the committed seed + the 3 committed Notes & Cash fixtures (no network,
# no LLM). These are NOT integration-marked — they must run in the fast suite,
# mirroring test_reconciler (#270 subsumed the offline engine_validation_harness).


def test_validation_green_lion_2024_1_reproduces_published_pop():
    """The headline proof, over HTTP: every period revenue 11/11, redemption 4/4,
    to the cent — across all 3 quarterly Notes & Cash periods (#270)."""
    resp = client.get("/deal/green-lion-2024-1/validation")
    assert resp.status_code == 200
    body = resp.json()

    assert body["available"] is True
    assert body["deal_id"] == "green-lion-2024-1"
    assert body["passed"] is True
    assert body["periods_checked"] == 3
    assert body["periods_passed"] == 3
    assert body["tolerance_eur"] == pytest.approx(0.01)
    assert body["source_note"]
    assert body["summary"]

    for period in body["periods"]:
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
    """The documented redemption rounding remainder is surfaced, not hidden.

    The March 2026 period leaves €0.69 of redemption funds unapplied due to
    rounding — a real published line, presented honestly. (Each quarter has its
    own small remainder; we pin March's known €0.69.)"""
    body = client.get("/deal/green-lion-2024-1/validation").json()
    march = next(p for p in body["periods"] if p["period_label"] == "March 2026")
    redemption = march["redemption"]
    assert redemption["unapplied_rounding"] == pytest.approx(0.69, abs=0.01)
    # Every period surfaces its own non-negative remainder honestly.
    for p in body["periods"]:
        assert p["redemption"]["unapplied_rounding"] >= 0.0


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

    from loanwhiz.primitives.collections_aggregator import CollectionsOutput

    # The mocked aggregator's ``execute(...).output`` must be a real
    # ``CollectionsOutput`` so the (real) ``_reconstruct_series`` period loop —
    # which now builds a canonical ``PeriodInputs`` via the tape adapter from the
    # collection legs — validates before the spied engine call. (The tape
    # analytics fetch degrades to ``None`` here, so risk_signals is simply
    # omitted; the legs drive the fold.)
    agg_output = CollectionsOutput(
        reporting_period="2026-02-28",
        interest_collected=1_000.0,
        swap_receipts=0.0,
        available_revenue_funds=1_000.0,
        scheduled_principal=1_000.0,
        unscheduled_principal=0.0,
        recoveries=0.0,
        realized_losses=0.0,
        available_principal_funds=1_000.0,
        pool_balance_eur=500_000_000.0,
        loan_count=1000,
        class_a_interest_due=1_000.0,
        senior_fees=50_000.0,
        summary="mock period",
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


# ---------------------------------------------------------------------------
# Adapter selection + cold-start GL-2024-1 + not-modelable (#269)
#
# `_reconstruct_series` selects the ingestion adapter per deal: a deal with loan
# tapes uses the tape path; a deal with only published reports uses the
# report-driven path (ReportAdapter -> run_period fold, seeded from the report);
# a deal with neither is "not modelable" (a labelled 422, not a silent empty
# cascade). The headline is cold-starting Green Lion 2024-1 (no tape) through the
# live /waterfall + /compliance endpoints, offline, with no Green-Lion-2026-1
# fallback consulted. To-the-cent reconciliation is the next child (#270).
# ---------------------------------------------------------------------------

# The real committed seed dir (the autouse fixture patches the module attribute to
# an empty tmp dir, so the report path's _load_cached_deal_model would otherwise
# miss the GL-2024-1 model and report "not modelable").
import loanwhiz.api.main as api_main  # noqa: E402

_REAL_SEED_DIR = str(
    Path(api_main.__file__).resolve().parents[1] / "data" / "deals" / "seed"
)


def test_reconstruct_series_selects_tape_path_for_tape_deal():
    """A deal with non-empty ``tape_urls`` routes to the tape builder."""
    sentinel = object()
    api_main._RECONSTRUCTION_MEMO.clear()
    with patch.object(
        api_main, "_reconstruct_series_from_tapes", return_value=sentinel
    ) as tapes, patch.object(
        api_main, "_reconstruct_series_from_reports"
    ) as reports:
        result = api_main._reconstruct_series(
            "d", {"tape_urls": [{"date": "2024-01-31", "url": "x"}]}
        )
    assert result is sentinel
    tapes.assert_called_once()
    reports.assert_not_called()


def test_tape_path_folds_canonical_period_inputs_with_risk_signals(tmp_path):
    """The tape path now folds canonical ``source="tape"`` ``PeriodInputs`` (#364).

    Captures what the (real) ``_reconstruct_series_from_tapes`` loop hands to
    ``reconstruct_period_series`` and asserts:

    - each period is a canonical ``domain.PeriodInputs`` with ``source=="tape"``;
    - it carries a populated ``RiskSignals`` derived from the period's tape
      analytics (no more ``risk_signals=None`` on the tape path);
    - its collection ``legs`` reduce, via ``_normalize_period``, to the EXACT
      same ``PeriodCollections`` the legacy ``PeriodInput.to_period_collections``
      path produced — the byte-for-byte safety property that keeps GL's
      reconstructed series unchanged by the migration.
    """
    from loanwhiz.primitives.collections_aggregator import CollectionsOutput
    from loanwhiz.primitives.period_state_machine import _normalize_period

    collections = CollectionsOutput(
        reporting_period="2026-03-31",
        interest_collected=9_050_000.0,
        swap_receipts=0.0,
        available_revenue_funds=9_050_000.0,
        scheduled_principal=5_000_000.0,
        unscheduled_principal=1_200_000.0,
        recoveries=300_000.0,
        realized_losses=150_000.0,
        available_principal_funds=6_500_000.0,
        pool_balance_eur=1_000_000_000.0,
        loan_count=1000,
        class_a_interest_due=9_050_000.0,
        senior_fees=50_000.0,
        summary="mock period",
    )
    mock_aggregator = MagicMock()
    mock_aggregator.execute.return_value.output = collections

    tape_dump = {
        "reporting_date": "2026-03-31",
        "asset_class": "RMBS",
        "transaction_name": "Green Lion 2026-1 B.V.",
        "loan_count": 1000,
        "pool_balance_eur": 1_000_000_000.0,
        "pool_stats": {"wtd_ltv": 71.0},
        "arrears_breakdown": {
            "current_pct": 96.0,
            "arrears_1_2m_pct": 1.0,
            "arrears_180d_plus_pct": 2.0,
            "default_pct": 1.0,
        },
        "epc_breakdown": None,
        "rate_type_breakdown": None,
        "property_type_breakdown": None,
        "geographic_breakdown": None,
        "annex_detected": "Annex 2 (RMBS)",
        "data_source": "direct",
    }

    deal = {
        "tape_urls": [
            {"url": "https://example/t0.csv", "date": "2026-02-28"},
            {"url": "https://example/t1.csv", "date": "2026-03-31"},
        ]
    }

    captured = {}

    def _spy_reconstruct(*, periods, **kw):
        captured["periods"] = periods
        return _small_reconstructed_series()

    api_main._RECONSTRUCTION_MEMO.clear()
    with patch("loanwhiz.api.main.RECONSTRUCTION_CACHE_DIR", str(tmp_path)), patch(
        "loanwhiz.api.main.CollectionsAggregator", return_value=mock_aggregator
    ), patch(
        "loanwhiz.api.main._normalised_tape_output", return_value=tape_dump
    ), patch(
        "loanwhiz.api.main.reconstruct_period_series", side_effect=_spy_reconstruct
    ):
        api_main._reconstruct_series(api_main._GREEN_LION_DEAL_ID, deal)

    api_main._RECONSTRUCTION_MEMO.clear()

    periods = captured["periods"]
    # 2 tapes → 1 transition → 1 canonical PeriodInputs.
    assert len(periods) == 1
    pi = periods[0]
    assert isinstance(pi, api_main.CanonicalPeriodInputs)
    assert pi.source == "tape"

    # RiskSignals is populated from the tape analytics (no risk_signals=None).
    assert pi.risk_signals is not None
    assert pi.risk_signals.pool_balance == 1_000_000_000.0
    assert pi.risk_signals.wa_ltv == 71.0
    # 1% default of €1bn = €10m; 2% 180d+ = €20m; arrears_90d = their union.
    assert pi.risk_signals.default_pct == pytest.approx(10_000_000.0)
    assert pi.risk_signals.arrears_180d == pytest.approx(20_000_000.0)
    assert pi.risk_signals.arrears_90d == pytest.approx(30_000_000.0)

    # Byte-for-byte: the canonical legs reduce to the same collections the legacy
    # PeriodInput path produced.
    legacy_collections = collections.to_period_collections()
    assert _normalize_period(pi).collections == legacy_collections


def test_reconstruct_series_selects_report_path_for_report_only_deal():
    """A deal with no tape but a Notes & Cash report set routes to the report builder."""
    sentinel = object()
    api_main._RECONSTRUCTION_MEMO.clear()
    with patch.object(
        api_main, "_reconstruct_series_from_reports", return_value=sentinel
    ) as reports, patch.object(
        api_main, "_reconstruct_series_from_tapes"
    ) as tapes:
        result = api_main._reconstruct_series(
            "d",
            {
                "tape_urls": [],
                "notes_cash_report_urls": [{"period": "Q1", "url": "r"}],
            },
        )
    assert result is sentinel
    reports.assert_called_once()
    tapes.assert_not_called()


def test_reconstruct_series_not_modelable_for_no_inputs_deal():
    """A deal with neither tape nor reports raises a labelled 422 (not modelable)."""
    from fastapi import HTTPException

    api_main._RECONSTRUCTION_MEMO.clear()
    with pytest.raises(HTTPException) as exc:
        api_main._reconstruct_series("orphan", {"tape_urls": []})
    assert exc.value.status_code == 422
    assert "not modelable" in exc.value.detail
    assert "orphan" in exc.value.detail


def test_no_inputs_deal_waterfall_returns_422_not_modelable():
    """``/waterfall`` for a deal with no tape and no reports degrades honestly (422)."""
    augmented = dict(api_main.DEALS)
    augmented["orphan-2025"] = {
        "deal_name": "Orphan 2025 B.V.",
        "prospectus_url": "https://example.test/orphan.pdf",
        "tape_urls": [],
        "investor_report_urls": [],
    }
    api_main._RECONSTRUCTION_MEMO.clear()
    with patch.object(api_main, "DEALS", augmented):
        resp = client.get("/deal/orphan-2025/waterfall")
    assert resp.status_code == 422
    assert "not modelable" in resp.json()["detail"]


def test_report_deal_without_committed_model_is_not_modelable():
    """A report-listed deal with no committed model / offline loader is 422 (not 200 empty).

    Leone Arancio has a ``notes_cash_report_urls`` list but no committed extracted
    model and no offline report loader, so it cannot be cold-started in the request
    path (we never fetch a PDF live) — surfaced honestly as not-modelable rather
    than a silent empty cascade.
    """
    api_main._RECONSTRUCTION_MEMO.clear()
    resp = client.get("/deal/leone-arancio-2023-1/waterfall")
    assert resp.status_code == 422
    assert "not modelable" in resp.json()["detail"]


def test_green_lion_2024_1_cold_start_waterfall():
    """GL-2024-1 (no tape) cold-starts through /waterfall via the report path, offline.

    Reads the committed seed model + the committed Notes & Cash fixture (no
    network), folds the report-driven series, and serves a NON-EMPTY cascade with
    the engine-computed Class A interest line — the headline cold-start.
    """
    api_main._RECONSTRUCTION_MEMO.clear()
    with patch("loanwhiz.api.main.DEAL_MODEL_SEED_DIR", _REAL_SEED_DIR):
        resp = client.get("/deal/green-lion-2024-1/waterfall")
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    assert body["deal_id"] == "green-lion-2024-1"
    # A real report-driven cascade: revenue steps executed and a non-empty
    # available-funds figure (the report's published available revenue).
    assert body["revenue_waterfall"], "report-driven waterfall is empty"
    assert body["available_revenue_funds"] > 0.0
    # The engine COMPUTED the Class A interest line (not a report-supplied
    # placeholder) — the proof the report path runs the real engine.
    class_a = next(
        td for td in body["tranche_distributions"] if td["tranche"] == "class_a"
    )
    assert class_a["interest_received"] > 0.0
    assert class_a["opening_balance"] > 0.0


def test_green_lion_2024_1_cold_start_compliance():
    """GL-2024-1 cold-starts through /compliance via the report-driven series, offline."""
    api_main._RECONSTRUCTION_MEMO.clear()
    with patch("loanwhiz.api.main.DEAL_MODEL_SEED_DIR", _REAL_SEED_DIR):
        resp = client.get("/deal/green-lion-2024-1/compliance")
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    # The monitor ran over the report-driven series (its standard output shape).
    assert "summary" in body
    assert "trigger_statuses" in body


def test_green_lion_2024_1_cold_start_consults_no_green_lion_fallback():
    """The GL-2024-1 cold-start uses its own model, never the _GREEN_LION_* fallback.

    ``_resolve_structural_config`` / ``_resolve_projection_base`` are the ONLY
    paths that read the Green-Lion-2026-1 last-resort constants. The report path
    seeds from the report and folds the extracted steps, so it must never call
    them — that is the design spec's honesty success criterion (zero GL-2026-1
    constants consulted for a cold-started deal).
    """
    api_main._RECONSTRUCTION_MEMO.clear()
    with patch("loanwhiz.api.main.DEAL_MODEL_SEED_DIR", _REAL_SEED_DIR), patch.object(
        api_main, "_resolve_structural_config"
    ) as resolve_struct, patch.object(
        api_main, "_resolve_projection_base"
    ) as resolve_proj:
        resp = client.get("/deal/green-lion-2024-1/waterfall")
    assert resp.status_code == 200, resp.json()
    resolve_struct.assert_not_called()
    resolve_proj.assert_not_called()


def test_report_path_seed_bridge_maps_every_field():
    """The domain->primitives seed bridge maps every field, no value invented."""
    from loanwhiz.domain.state import DealState as DomainDealState, TrancheState

    domain_seed = DomainDealState(
        reporting_date="2025-09-30",
        tranches=[
            TrancheState(name="class_a", balance=900.0, pdl_balance=1.0),
            TrancheState(name="class_b", balance=80.0, pdl_balance=2.0),
            TrancheState(name="class_c", balance=20.0, pdl_balance=3.0),
        ],
        reserve_balance=10.0,
        reserve_target=12.0,
        pool_balance=1000.0,
        original_pool_balance=1100.0,
        cumulative_losses=5.0,
        sequential_pay_active=False,
    )
    seed = api_main._primitives_seed_from_report_seed(domain_seed)
    assert seed.reporting_date == "2025-09-30"
    assert (seed.class_a_balance, seed.class_b_balance, seed.class_c_balance) == (
        900.0,
        80.0,
        20.0,
    )
    assert (seed.class_a_pdl, seed.class_b_pdl, seed.class_c_pdl) == (1.0, 2.0, 3.0)
    assert seed.reserve_balance == 10.0
    assert seed.reserve_target == 12.0
    assert seed.pool_balance == 1000.0
    assert seed.original_pool_balance == 1100.0
    assert seed.cumulative_losses == 5.0


def test_green_lion_2026_1_still_uses_tape_path():
    """The in-code Green Lion 2026-1 (has tapes) still selects the tape builder.

    Regression guard for the adapter-selection refactor: the tape deal must route
    to ``_reconstruct_series_from_tapes`` (its output is unchanged), never the new
    report path.
    """
    sentinel = object()
    api_main._RECONSTRUCTION_MEMO.clear()
    with patch.object(
        api_main, "_reconstruct_series_from_tapes", return_value=sentinel
    ) as tapes, patch.object(
        api_main, "_reconstruct_series_from_reports"
    ) as reports:
        result = api_main._reconstruct_series(
            "green-lion-2026-1", api_main.DEALS["green-lion-2026-1"]
        )
    assert result is sentinel
    tapes.assert_called_once()
    reports.assert_not_called()


# ---------------------------------------------------------------------------
# Scenario / stress matrix (#323) — a grid of forward projections across a
# CPR × CDR (× rate-shift) matrix, returning a tranche-level outcome surface
# (loss / WAL / shortfall / first-breach) per cell. Driven THROUGH the #319
# projection fold; all tests run offline over the deterministic engine.
# ---------------------------------------------------------------------------


def test_stress_matrix_grid_shape_and_cells():
    """A 2×2 CPR×CDR grid returns 4 well-formed cells + echoed axes/dimensions."""
    resp = client.post(
        "/deal/green-lion-2026-1/stress-matrix",
        json={"cpr_pct": [10, 20], "cdr_pct": [1, 5], "months": 6},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["deal_id"] == "green-lion-2026-1"
    assert body["months"] == 6
    assert body["axes"] == {
        "cpr_pct": [10.0, 20.0],
        "cdr_pct": [1.0, 5.0],
        "rate_shift_bps": [0.0],
    }
    assert body["dimensions"] == {"cpr": 2, "cdr": 2, "rate_shift": 1, "cells": 4}
    assert len(body["cells"]) == 4
    # Every cell carries the full tranche-level outcome surface.
    for cell in body["cells"]:
        assert set(cell) == {
            "cpr_pct",
            "cdr_pct",
            "rate_shift_bps",
            "loss",
            "wal",
            "shortfall",
            "first_breach_period",
            "first_breach_label",
            "first_breach_trigger",
        }
        assert {"wal_class_a_months", "wal_class_b_months", "wal_class_c_months"} <= set(
            cell["wal"]
        )
    # The 4 cells are exactly the Cartesian product of the two axes.
    coords = {(c["cpr_pct"], c["cdr_pct"]) for c in body["cells"]}
    assert coords == {(10.0, 1.0), (10.0, 5.0), (20.0, 1.0), (20.0, 5.0)}


def test_stress_matrix_default_grid_is_2d():
    """Omitting rate_shift_bps yields a 2-D CPR×CDR grid (rate-shift axis = [0.0])."""
    resp = client.post(
        "/deal/green-lion-2026-1/stress-matrix",
        json={"cpr_pct": [15], "cdr_pct": [2, 4, 6], "months": 4},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["axes"]["rate_shift_bps"] == [0.0]
    assert body["dimensions"]["cells"] == 3
    assert all(c["rate_shift_bps"] == 0.0 for c in body["cells"])


def test_stress_matrix_3d_includes_rate_shift_axis():
    """Supplying rate_shift_bps makes the grid 3-D (cells = cpr×cdr×rate_shift)."""
    resp = client.post(
        "/deal/green-lion-2026-1/stress-matrix",
        json={
            "cpr_pct": [10, 20],
            "cdr_pct": [1, 5],
            "rate_shift_bps": [0, 100],
            "months": 4,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["dimensions"] == {"cpr": 2, "cdr": 2, "rate_shift": 2, "cells": 8}
    assert len(body["cells"]) == 8


def test_stress_matrix_higher_cdr_means_more_loss():
    """The outcome surface is monotone in CDR: higher CDR → strictly higher loss
    at fixed CPR / recovery (the stress signal an analyst reads off the grid)."""
    resp = client.post(
        "/deal/green-lion-2026-1/stress-matrix",
        json={"cpr_pct": [15], "cdr_pct": [1, 5], "months": 12},
    )
    assert resp.status_code == 200
    cells = {c["cdr_pct"]: c for c in resp.json()["cells"]}
    assert cells[5.0]["loss"] > cells[1.0]["loss"]


def test_stress_matrix_oversized_grid_returns_422():
    """A grid whose cell count exceeds the cap returns a labelled 422, not a hang."""
    # 9 × 9 × 1 = 81 > 64-cell cap.
    big_axis = [float(x) for x in range(9)]
    resp = client.post(
        "/deal/green-lion-2026-1/stress-matrix",
        json={"cpr_pct": big_axis, "cdr_pct": big_axis, "months": 3},
    )
    assert resp.status_code == 422
    assert "cell" in resp.json()["detail"].lower()


def test_stress_matrix_unknown_deal_returns_404():
    resp = client.post(
        "/deal/unknown/stress-matrix",
        json={"cpr_pct": [10], "cdr_pct": [1]},
    )
    assert resp.status_code == 404


def test_stress_matrix_invalid_axis_value_returns_422():
    """An out-of-bounds CPR axis value is a 422 (validation), not a 500."""
    resp = client.post(
        "/deal/green-lion-2026-1/stress-matrix",
        json={"cpr_pct": [150], "cdr_pct": [1]},
    )
    assert resp.status_code == 422


def test_stress_matrix_first_breach_discriminates_stress():
    """first_breach is the earliest covenant trigger fire over the projected
    series, via the SAME covenant engine /compliance uses. A heavy-stress cell
    breaches at a real period index naming the firing trigger; a benign cell does
    not breach over the horizon (None) — so the surface separates the two."""
    resp = client.post(
        "/deal/green-lion-2026-1/stress-matrix",
        json={
            "cpr_pct": [5],
            "cdr_pct": [0.1, 20],
            "months": 36,
        },
    )
    assert resp.status_code == 200
    cells = {c["cdr_pct"]: c for c in resp.json()["cells"]}
    heavy = cells[20.0]
    benign = cells[0.1]
    # Heavy stress breaches at a real (int) period naming the firing trigger.
    assert isinstance(heavy["first_breach_period"], int)
    assert heavy["first_breach_period"] >= 0
    assert heavy["first_breach_trigger"] is not None
    assert heavy["first_breach_label"] is not None
    # Benign stress does not breach over the horizon.
    assert benign["first_breach_period"] is None
    assert benign["first_breach_trigger"] is None


def test_stress_matrix_does_not_change_project_endpoint():
    """The matrix is additive: /project still returns its #319 shape unchanged."""
    resp = client.post(
        "/deal/green-lion-2026-1/project",
        json={"scenarios": ["base"], "months": 6},
    )
    assert resp.status_code == 200
    assert "periods" in resp.json()["projections"]["base"]
# GET /relative-value-screener — cross-deal relative-value screener (#324)
# ---------------------------------------------------------------------------


def test_relative_value_screener_endpoint_returns_scorecard():
    """The screener endpoint returns the cross-deal scorecard over the real seeds.

    The autouse fixture points ``DEAL_MODEL_SEED_DIR`` at an empty dir, so we
    override it back to the real committed seeds (mirroring the cold-start
    endpoint tests) to exercise the real cross-deal cohort.
    """
    with patch("loanwhiz.api.main.DEAL_MODEL_SEED_DIR", _REAL_SEED_DIR):
        resp = client.get("/relative-value-screener")
    assert resp.status_code == 200, resp.json()
    body = resp.json()
    # Shape: the four named relative-value dimensions, a weights map, ranked rows.
    assert body["dimensions"] == [
        "subordination_ce",
        "wal",
        "trigger_headroom",
        "pool_quality",
    ]
    assert abs(sum(body["weights"].values()) - 1.0) < 1e-9
    assert body["tally"]["tranches_scored"] >= 3
    assert body["tranches"], "no tranches scored over the real seeds"


def test_relative_value_screener_endpoint_is_honest_and_ranked():
    """Each row carries all four dimensions; unavailable ones are not fabricated."""
    with patch("loanwhiz.api.main.DEAL_MODEL_SEED_DIR", _REAL_SEED_DIR):
        body = client.get("/relative-value-screener").json()
    for row in body["tranches"]:
        assert set(row["factors"]) == {
            "subordination_ce",
            "wal",
            "trigger_headroom",
            "pool_quality",
        }
        for factor in row["factors"].values():
            assert factor["reason"].strip()
            if not factor["available"]:
                # Honesty contract: no fabricated value/score when unavailable.
                assert factor["value"] is None
                assert factor["score"] is None
    # Scored rows form a contiguous 1..N rank prefix (best→worst by composite).
    scored = [r for r in body["tranches"] if r["composite_score"] is not None]
    assert [r["rank"] for r in scored] == list(range(1, len(scored) + 1))


def test_relative_value_screener_endpoint_is_deterministic_and_offline():
    """Two calls return identical rankings; no tape fetch happens in the path.

    Determinism + offline is the same contract as /capability-matrix. We assert
    determinism directly and offline-ness by patching the tape-series builders
    to blow up if the request path ever touches them.
    """
    with patch("loanwhiz.api.main.DEAL_MODEL_SEED_DIR", _REAL_SEED_DIR), patch.object(
        api_main, "_reconstruct_series_from_tapes", side_effect=AssertionError("tape fetched")
    ):
        first = client.get("/relative-value-screener").json()
        second = client.get("/relative-value-screener").json()
    assert [(r["deal_id"], r["tranche_name"], r["rank"]) for r in first["tranches"]] == [
        (r["deal_id"], r["tranche_name"], r["rank"]) for r in second["tranches"]
    ]
