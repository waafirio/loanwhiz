"""Tests for the on-demand extraction job subsystem + endpoints (#384).

Every test stubs the long extraction via the ``extract_fn`` injection seam — no
test ever runs a real ~20–37 min Docling/Vertex extraction. The job pool's
``Future`` is awaited so transitions are deterministic without a real long run.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from loanwhiz.api import app
from loanwhiz.api import extraction_jobs
from loanwhiz.config import GREEN_LION
from loanwhiz.extraction.assembler import DealModel, _slug

client = TestClient(app)

DEAL_ID = "green-lion-2026-1"


@pytest.fixture(autouse=True)
def _reset_jobs():
    """Clear the process-local job store between tests (it is module state)."""
    extraction_jobs.reset_jobs()
    yield
    extraction_jobs.reset_jobs()


def _fake_model(deal_name: str, prospectus_url: str, cache_path: str) -> DealModel:
    """Build a minimal schema-valid DealModel with one cited waterfall step.

    Mirrors the shape ``extract_deal_model`` produces so the governed summary
    (completeness/trigger/citation counts) is exercised against a real model.
    """
    return DealModel.model_validate(
        {
            "metadata": {
                "deal_name": deal_name,
                "prospectus_url": prospectus_url,
                "extracted_at": "2026-06-23T00:00:00+00:00",
                "extraction_duration_sec": 0.01,
                "sections_found": ["definitions", "revenue_priority_of_payments"],
                "completeness_score": 0.85,
                "cache_path": cache_path,
            },
            "definitions": {},
            "waterfalls": {
                "revenue": {
                    "waterfall_type": "revenue",
                    "deal_name": deal_name,
                    "steps": [
                        {
                            "priority": "(a)",
                            "recipient": "security_trustee_fees",
                            "description": "Pay security trustee fees.",
                            "citation": {"document": "Prospectus", "page_or_section": "9.1"},
                        }
                    ],
                }
            },
            "covenants": {
                "deal_name": deal_name,
                "triggers": [
                    {
                        "name": "Class A PDL Trigger",
                        "citation": {"document": "Prospectus", "page_or_section": "10.2"},
                    }
                ],
                "issuer_covenants": [],
                "extraction_confidence": 0.7,
            },
            "tranche_structure": [{"name": "Class A", "rating": "AAA"}],
            "trigger_names": ["Class A PDL Trigger"],
        }
    )


def _materialising_extract_fn(*, prospectus_url, deal_name, cache_dir, force_refresh):
    """Stub that writes the model to the cache exactly as the real primitive does.

    Records the call so the force-propagation test can assert ``force_refresh``.
    """
    _materialising_extract_fn.calls.append(
        {"prospectus_url": prospectus_url, "deal_name": deal_name,
         "cache_dir": cache_dir, "force_refresh": force_refresh}
    )
    cache_path = Path(cache_dir) / f"{_slug(deal_name)}.json"
    model = _fake_model(deal_name, prospectus_url, str(cache_path))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    return model


_materialising_extract_fn.calls = []


def _raising_extract_fn(*, prospectus_url, deal_name, cache_dir, force_refresh):
    """Stub simulating a missing-GCP-creds / pipeline failure."""
    raise RuntimeError("missing GCP credentials")


# --- subsystem (unit) ---------------------------------------------------------


def test_submit_transitions_to_succeeded_and_materialises_cache(tmp_path):
    """submit → queued → running → succeeded, with the model written to cache_dir
    and a governed summary (completeness/trigger/citation counts) recorded."""
    _materialising_extract_fn.calls.clear()
    job, future = extraction_jobs.submit_extraction(
        DEAL_ID,
        prospectus_url="http://example/p.pdf",
        deal_name="Green Lion 2026-1 B.V.",
        cache_dir=str(tmp_path),
        force=False,
        extract_fn=_materialising_extract_fn,
    )
    # The pool may already have started the run by now; "queued" is transient.
    assert job.status in ("queued", "running")
    assert future is not None
    future.result(timeout=10)  # deterministic await — never a real long run

    done = extraction_jobs.get_job(DEAL_ID)
    assert done.status == "succeeded"
    assert done.started_at is not None and done.finished_at is not None
    assert done.error is None
    assert done.summary == {
        "completeness_score": 0.85,
        "trigger_count": 1,
        "citation_count": 2,
        "sections_found": ["definitions", "revenue_priority_of_payments"],
    }
    # Materialised into the same cache the cold-start reader serves.
    cache_file = tmp_path / f"{_slug('Green Lion 2026-1 B.V.')}.json"
    assert cache_file.exists()


def test_failure_surfaces_as_failed_with_reason(tmp_path):
    """A raising extract_fn (e.g. missing creds) → failed status with the reason —
    the worker never crashes the pool."""
    job, future = extraction_jobs.submit_extraction(
        DEAL_ID,
        prospectus_url="http://example/p.pdf",
        deal_name="Green Lion 2026-1 B.V.",
        cache_dir=str(tmp_path),
        extract_fn=_raising_extract_fn,
    )
    future.result(timeout=10)
    done = extraction_jobs.get_job(DEAL_ID)
    assert done.status == "failed"
    assert "missing GCP credentials" in done.error
    assert done.summary is None


def test_force_propagates_force_refresh(tmp_path):
    """?force=true → force_refresh=True reaches the wrapped primitive."""
    _materialising_extract_fn.calls.clear()
    _, future = extraction_jobs.submit_extraction(
        DEAL_ID,
        prospectus_url="http://example/p.pdf",
        deal_name="Green Lion 2026-1 B.V.",
        cache_dir=str(tmp_path),
        force=True,
        extract_fn=_materialising_extract_fn,
    )
    future.result(timeout=10)
    assert _materialising_extract_fn.calls[-1]["force_refresh"] is True


def test_get_job_none_when_never_submitted():
    assert extraction_jobs.get_job("never-submitted") is None


# --- endpoints (integration over the FastAPI app) -----------------------------


def test_post_extract_returns_202_immediately_and_materialises(tmp_path):
    """POST returns 202 without invoking a real pipeline; the stub then writes the
    cache and GET /deal/{id}/model subsequently reports cached (one source of
    truth, end to end)."""
    with patch("loanwhiz.api.main.DEAL_MODEL_CACHE_DIR", str(tmp_path)), patch(
        "loanwhiz.api.extraction_jobs.extract_deal_model", _materialising_extract_fn
    ):
        resp = client.post(f"/deal/{DEAL_ID}/extract")
        assert resp.status_code == 202
        body = resp.json()
        assert body["deal_id"] == DEAL_ID
        assert body["status"] in ("queued", "running", "succeeded")

        # Drain the single-worker pool so the job finishes deterministically.
        extraction_jobs._EXECUTOR.submit(lambda: None).result(timeout=10)

        status = client.get(f"/deal/{DEAL_ID}/extract/status").json()
        assert status["status"] == "succeeded"
        assert status["summary"]["trigger_count"] == 1

        # The cold-start reader now serves the materialised model.
        model_resp = client.get(f"/deal/{DEAL_ID}/model")
        assert model_resp.json()["extraction_status"] == "cached"


def test_post_extract_failure_polls_failed_without_hanging(tmp_path):
    """A creds failure: POST still returns 202 promptly, then status polls failed."""
    with patch("loanwhiz.api.main.DEAL_MODEL_CACHE_DIR", str(tmp_path)), patch(
        "loanwhiz.api.extraction_jobs.extract_deal_model", _raising_extract_fn
    ):
        resp = client.post(f"/deal/{DEAL_ID}/extract")
        assert resp.status_code == 202
        extraction_jobs._EXECUTOR.submit(lambda: None).result(timeout=10)
        status = client.get(f"/deal/{DEAL_ID}/extract/status").json()
        assert status["status"] == "failed"
        assert "missing GCP credentials" in status["error"]


def test_post_extract_unknown_deal_404():
    resp = client.post("/deal/does-not-exist/extract")
    assert resp.status_code == 404


def test_status_none_when_no_prior_job():
    resp = client.get(f"/deal/{DEAL_ID}/extract/status")
    assert resp.status_code == 200
    assert resp.json()["status"] == "none"


# --- one extraction path, not a fork (#384) ----------------------------------


def test_offline_scripts_and_job_share_the_same_primitive():
    """The on-demand job and the offline driver both reach the identical
    ``extract_deal_model`` — there is no fork of the extraction path."""
    import scripts.extract_c2_deals as driver
    from loanwhiz.extraction import assembler

    assert extraction_jobs.extract_deal_model is assembler.extract_deal_model
    assert driver.extract_deal_model is assembler.extract_deal_model
