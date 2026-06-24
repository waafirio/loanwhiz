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
    """Clear the process-local job stores between tests (they are module state)."""
    extraction_jobs.reset_jobs()
    extraction_jobs.reset_report_jobs()
    yield
    extraction_jobs.reset_jobs()
    extraction_jobs.reset_report_jobs()


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
    # The single-worker pool may have already run the job to completion by the
    # time this assertion executes (a fast stub finishes before we observe it),
    # so "queued"/"running" is a transient state we cannot reliably catch — the
    # stable invariant is that submit returned a tracked job in a valid lifecycle
    # state plus a future to await. Don't pin a racy transient snapshot.
    assert job.status in ("queued", "running", "succeeded")
    assert future is not None
    future.result(timeout=10)  # deterministic await — never a real long run

    done = extraction_jobs.get_job(DEAL_ID)
    assert done.status == "succeeded"
    assert done.started_at is not None and done.finished_at is not None
    assert done.error is None
    # The count summary is unchanged; the governance signals (#404) are surfaced
    # additively (confidence / human_review_required / audit_entry_id).
    assert done.summary["completeness_score"] == 0.85
    assert done.summary["trigger_count"] == 1
    assert done.summary["citation_count"] == 2
    assert done.summary["sections_found"] == [
        "definitions",
        "revenue_priority_of_payments",
    ]
    assert "confidence" in done.summary
    assert "human_review_required" in done.summary
    assert "audit_entry_id" in done.summary
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
    # No count summary on failure (no model), but the failure governance record's
    # id is surfaced (#404) — the failed run is governed, not silently dropped.
    assert done.summary == {"audit_entry_id": done.summary["audit_entry_id"]}
    assert "completeness_score" not in done.summary


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


# --- governance: the /extract job runs UNDER governance (#404) -----------------
# The on-demand extraction job must emit a real FINOS-aligned audit record with
# the deal's REAL confidence + citations — never the hollow hardcoded 0.9 / empty
# citations an older audit flagged. The audit sink is injected (``audit_fn``) so
# these assert the governance wiring without touching the JSONL store, and the
# real ``audit_extraction_result`` is exercised against a tmp log dir below.


def _recording_audit_fn():
    """A fast in-memory audit recorder matching ``audit_extraction_result``'s
    keyword signature; returns a stand-in entry so the job can surface its id."""
    calls: list[dict] = []

    class _Entry:
        def __init__(self, confidence, threshold=0.7):
            self.entry_id = f"entry-{len(calls)}"
            self.human_review_required = confidence < threshold

    def _audit(*, confidence, **kwargs):
        calls.append({"confidence": confidence, **kwargs})
        return _Entry(confidence)

    _audit.calls = calls
    return _audit


def _low_completeness_model(deal_name, prospectus_url, cache_path):
    """A schema-valid model with a low completeness score and a low-confidence
    waterfall — its governed confidence must come out below the high-quality one."""
    return DealModel.model_validate(
        {
            "metadata": {
                "deal_name": deal_name,
                "prospectus_url": prospectus_url,
                "extracted_at": "2026-06-23T00:00:00+00:00",
                "extraction_duration_sec": 0.01,
                "sections_found": ["definitions"],
                "completeness_score": 0.30,
                "cache_path": cache_path,
            },
            "definitions": {},
            "waterfalls": {
                "revenue": {
                    "waterfall_type": "revenue",
                    "deal_name": deal_name,
                    "steps": [],
                    "extraction_confidence": 0.20,
                }
            },
            "covenants": {
                "deal_name": deal_name,
                "triggers": [],
                "issuer_covenants": [],
                "extraction_confidence": 0.40,
            },
            "tranche_structure": [],
            "trigger_names": [],
        }
    )


def test_succeeded_job_emits_governance_record_with_real_confidence(tmp_path):
    """A succeeded extraction emits one audit record carrying the model's REAL
    min-of-signals confidence and its REAL citations, and surfaces the governed
    confidence / human-review flag / audit id on the job summary."""
    audit = _recording_audit_fn()
    _, future = extraction_jobs.submit_extraction(
        DEAL_ID,
        prospectus_url="http://example/p.pdf",
        deal_name="Green Lion 2026-1 B.V.",
        cache_dir=str(tmp_path),
        extract_fn=_materialising_extract_fn,
        audit_fn=audit,
    )
    future.result(timeout=10)

    # Exactly one governance record was emitted for the successful run.
    assert len(audit.calls) == 1
    call = audit.calls[0]
    assert call["primitive_name"] == "deal_extraction"
    # The _fake_model has completeness 0.85 and covenant extraction_confidence 0.70
    # → honest confidence is min(0.85, 0.70) = 0.70, NOT a hardcoded 0.9.
    assert call["confidence"] == pytest.approx(0.70)
    assert call["confidence"] != 0.9
    # Real citations were threaded (one waterfall step + one trigger citation).
    assert len(call["citations"]) == 2
    assert all(c.document == "Prospectus" for c in call["citations"])
    # Input provenance hashes the actual job inputs.
    assert call["input"]["deal_name"] == "Green Lion 2026-1 B.V."
    assert call["output"] is not None

    done = extraction_jobs.get_job(DEAL_ID)
    assert done.status == "succeeded"
    # Governed signals surfaced additively; existing count keys untouched.
    assert done.summary["confidence"] == pytest.approx(0.70)
    assert done.summary["human_review_required"] is False  # 0.70 >= 0.7
    assert done.summary["audit_entry_id"] is not None
    assert done.summary["completeness_score"] == 0.85
    assert done.summary["citation_count"] == 2


def test_failed_job_emits_failure_governance_record(tmp_path):
    """A failed extraction still runs under governance: a failure audit record
    (confidence 0.0, no citations, the error captured) is emitted and its id
    surfaced — the failure is recorded, not silently un-governed."""
    audit = _recording_audit_fn()
    _, future = extraction_jobs.submit_extraction(
        DEAL_ID,
        prospectus_url="http://example/p.pdf",
        deal_name="Green Lion 2026-1 B.V.",
        cache_dir=str(tmp_path),
        extract_fn=_raising_extract_fn,
        audit_fn=audit,
    )
    future.result(timeout=10)

    assert len(audit.calls) == 1
    call = audit.calls[0]
    assert call["confidence"] == 0.0
    assert call["citations"] == []
    assert "missing GCP credentials" in call["output"]["error"]

    done = extraction_jobs.get_job(DEAL_ID)
    assert done.status == "failed"
    assert "missing GCP credentials" in done.error
    assert done.summary["audit_entry_id"] is not None


def test_governance_confidence_is_not_hardcoded(tmp_path):
    """The governed confidence varies with extraction quality — a low-completeness,
    low-per-section model yields a strictly lower confidence (and a human-review
    flag) than the high-quality model. Proves confidence is honest, not a constant."""
    high_audit = _recording_audit_fn()
    extraction_jobs.submit_extraction(
        "deal-high",
        prospectus_url="http://example/p.pdf",
        deal_name="High Quality Deal",
        cache_dir=str(tmp_path),
        extract_fn=_materialising_extract_fn,
        audit_fn=high_audit,
    )[1].result(timeout=10)

    low_audit = _recording_audit_fn()

    def _low_extract_fn(*, prospectus_url, deal_name, cache_dir, force_refresh):
        return _low_completeness_model(
            deal_name, prospectus_url, str(Path(cache_dir) / "m.json")
        )

    extraction_jobs.submit_extraction(
        "deal-low",
        prospectus_url="http://example/p.pdf",
        deal_name="Low Quality Deal",
        cache_dir=str(tmp_path),
        extract_fn=_low_extract_fn,
        audit_fn=low_audit,
    )[1].result(timeout=10)

    high_conf = high_audit.calls[0]["confidence"]
    low_conf = low_audit.calls[0]["confidence"]
    # min(0.30, 0.20, 0.40) == 0.20 for the low model; 0.85 for the high one.
    assert low_conf == pytest.approx(0.20)
    assert low_conf < high_conf
    # Low confidence flags the run for human review on the job summary.
    assert extraction_jobs.get_job("deal-low").summary["human_review_required"] is True


def test_audit_side_channel_failure_never_fails_the_job(tmp_path):
    """A raising audit sink must not change the job's success outcome — governance
    is a side-channel (the main._audit contract). The job still succeeds; the
    surfaced audit id is None and the review flag falls back to the threshold rule."""
    def _raising_audit(**kwargs):
        raise RuntimeError("audit store unwritable")

    _, future = extraction_jobs.submit_extraction(
        DEAL_ID,
        prospectus_url="http://example/p.pdf",
        deal_name="Green Lion 2026-1 B.V.",
        cache_dir=str(tmp_path),
        extract_fn=_materialising_extract_fn,
        audit_fn=_raising_audit,
    )
    future.result(timeout=10)

    done = extraction_jobs.get_job(DEAL_ID)
    assert done.status == "succeeded"  # audit failure did NOT break the job
    assert done.summary["audit_entry_id"] is None
    assert done.summary["confidence"] == pytest.approx(0.70)
    assert done.summary["human_review_required"] is False  # fallback rule, 0.70>=0.7


def test_real_audit_extraction_result_writes_jsonl(tmp_path):
    """End-to-end with the REAL ``audit_extraction_result`` sink (default), pointed
    at a tmp audit dir: one governed JSONL record lands with the honest confidence
    and the model's real citations — the catalogue claim made true for /extract."""
    audit_dir = tmp_path / "audit"
    _, future = extraction_jobs.submit_extraction(
        DEAL_ID,
        prospectus_url="http://example/p.pdf",
        deal_name="Green Lion 2026-1 B.V.",
        cache_dir=str(tmp_path / "cache"),
        extract_fn=_materialising_extract_fn,
        audit_log_dir=str(audit_dir),
    )
    future.result(timeout=10)

    from loanwhiz.primitives.audit_logger import AuditLog

    jsonl_files = list((audit_dir / "deal_extraction").glob("*.jsonl"))
    assert len(jsonl_files) == 1
    log = AuditLog.from_jsonl(jsonl_files[0].read_text(encoding="utf-8"))
    assert len(log.entries) == 1
    entry = log.entries[0]
    assert entry.primitive_name == "deal_extraction"
    assert entry.confidence == pytest.approx(0.70)
    assert entry.confidence != 0.9
    assert len(entry.citations) == 2
    assert entry.human_review_required is False
    # The job surfaced the persisted entry's id.
    assert extraction_jobs.get_job(DEAL_ID).summary["audit_entry_id"] == entry.entry_id


# --- re-submit semantics + store isolation (#384 docstring contract) ----------


def _blocking_extract_fn(gate):
    """An extract_fn that blocks until ``gate`` is set, so a job stays ``running``
    long enough to test the in-flight re-submit branch deterministically."""

    def _fn(*, prospectus_url, deal_name, cache_dir, force_refresh):
        gate.wait(timeout=10)
        return _fake_model(deal_name, prospectus_url, str(Path(cache_dir) / "m.json"))

    return _fn


def test_resubmit_while_running_returns_same_job_no_second_run(tmp_path):
    """A second submit for a deal whose job is still in-flight (and force=False)
    returns the SAME job with a ``None`` future — no second run is scheduled.
    The idempotent-enqueue branch of submit_extraction."""
    import threading

    gate = threading.Event()
    job1, fut1 = extraction_jobs.submit_extraction(
        DEAL_ID,
        prospectus_url="http://example/p.pdf",
        deal_name="Green Lion 2026-1 B.V.",
        cache_dir=str(tmp_path),
        extract_fn=_blocking_extract_fn(gate),
    )
    try:
        # While job1 is still queued/running, a non-force re-submit is idempotent.
        job2, fut2 = extraction_jobs.submit_extraction(
            DEAL_ID,
            prospectus_url="http://example/p.pdf",
            deal_name="Green Lion 2026-1 B.V.",
            cache_dir=str(tmp_path),
            extract_fn=_blocking_extract_fn(gate),
        )
        assert job2 is job1  # same job object, not a new one
        assert fut2 is None  # no second run scheduled
    finally:
        gate.set()
        if fut1 is not None:
            fut1.result(timeout=10)


def test_resubmit_after_finished_replaces_job(tmp_path):
    """Once a job has finished (succeeded/failed), a fresh submit replaces it with
    a new job that actually runs again (a new future)."""
    job1, fut1 = extraction_jobs.submit_extraction(
        DEAL_ID,
        prospectus_url="http://example/p.pdf",
        deal_name="Green Lion 2026-1 B.V.",
        cache_dir=str(tmp_path),
        extract_fn=_materialising_extract_fn,
    )
    fut1.result(timeout=10)
    assert extraction_jobs.get_job(DEAL_ID).status == "succeeded"

    job2, fut2 = extraction_jobs.submit_extraction(
        DEAL_ID,
        prospectus_url="http://example/p.pdf",
        deal_name="Green Lion 2026-1 B.V.",
        cache_dir=str(tmp_path),
        extract_fn=_materialising_extract_fn,
    )
    assert job2 is not job1  # finished job was replaced
    assert fut2 is not None  # a new run was scheduled
    fut2.result(timeout=10)


def test_force_resubmit_replaces_in_flight_job(tmp_path):
    """A force=True submit replaces even an in-flight job (it must re-run),
    scheduling a new future rather than returning the running one."""
    import threading

    gate = threading.Event()
    fut2 = None
    job1, fut1 = extraction_jobs.submit_extraction(
        DEAL_ID,
        prospectus_url="http://example/p.pdf",
        deal_name="Green Lion 2026-1 B.V.",
        cache_dir=str(tmp_path),
        extract_fn=_blocking_extract_fn(gate),
    )
    try:
        job2, fut2 = extraction_jobs.submit_extraction(
            DEAL_ID,
            prospectus_url="http://example/p.pdf",
            deal_name="Green Lion 2026-1 B.V.",
            cache_dir=str(tmp_path),
            force=True,
            extract_fn=_materialising_extract_fn,
        )
        assert job2 is not job1  # force replaces the in-flight job
        assert fut2 is not None
    finally:
        gate.set()
        if fut1 is not None:
            fut1.result(timeout=10)
        if fut2 is not None:
            fut2.result(timeout=10)


def test_reset_jobs_clears_the_store(tmp_path):
    """``reset_jobs`` empties the process-local store — the isolation primitive
    the autouse fixture relies on so cases don't leak job state into each other."""
    _, future = extraction_jobs.submit_extraction(
        DEAL_ID,
        prospectus_url="http://example/p.pdf",
        deal_name="Green Lion 2026-1 B.V.",
        cache_dir=str(tmp_path),
        extract_fn=_materialising_extract_fn,
    )
    future.result(timeout=10)
    assert extraction_jobs.get_job(DEAL_ID) is not None
    extraction_jobs.reset_jobs()
    assert extraction_jobs.get_job(DEAL_ID) is None


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


# --- report-ingest job subsystem (#399) --------------------------------------
# Mirrors the prospectus-extraction job tests: the report resolver is ALWAYS
# stubbed via the ``resolve_fn`` injection seam — no real network/LLM extraction.


_DEAL = {
    "deal_name": "Green Lion 2026-1 B.V.",
    "notes_cash_report_urls": [{"url": "http://example/report.pdf"}],
}


def _make_resolve_stub(cache_dir_holder=None, *, fail: bool = False):
    """Build a fast resolve_fn stub recording its calls (and optionally raising)."""
    calls: list[dict] = []

    def _resolve(deal_id, deal, *, cache_dir, allow_live):
        calls.append(
            {"deal_id": deal_id, "deal": deal, "cache_dir": cache_dir, "allow_live": allow_live}
        )
        if fail:
            raise RuntimeError("live extraction unavailable")
        # Simulate the durable-cache population the real resolver does.
        if cache_dir_holder is not None:
            Path(cache_dir).mkdir(parents=True, exist_ok=True)
            (Path(cache_dir) / "report-ingest-marker.json").write_text("{}", encoding="utf-8")
        return object()

    _resolve.calls = calls
    return _resolve


def test_report_ingest_transitions_to_succeeded(tmp_path):
    """submit_report_ingest → queued → running → succeeded against a stubbed resolver,
    with allow_live=True and the patched cache_dir forwarded to the resolver."""
    resolve = _make_resolve_stub(cache_dir_holder=True)
    job, future = extraction_jobs.submit_report_ingest(
        DEAL_ID,
        deal=_DEAL,
        cache_dir=str(tmp_path),
        allow_live=True,
        resolve_fn=resolve,
    )
    assert job.status in ("queued", "running")
    assert future is not None
    future.result(timeout=10)

    done = extraction_jobs.get_report_job(DEAL_ID)
    assert done.status == "succeeded"
    assert done.started_at is not None and done.finished_at is not None
    assert done.error is None
    # The resolver was invoked with allow_live=True and the forwarded cache_dir.
    assert resolve.calls[-1]["allow_live"] is True
    assert resolve.calls[-1]["cache_dir"] == str(tmp_path)
    # The durable cache was populated by the (stubbed) resolver.
    assert (tmp_path / "report-ingest-marker.json").exists()


def test_report_ingest_failure_surfaces_reason(tmp_path):
    """A raising resolve_fn → failed status with the reason; pool never crashes."""
    resolve = _make_resolve_stub(fail=True)
    _, future = extraction_jobs.submit_report_ingest(
        DEAL_ID, deal=_DEAL, cache_dir=str(tmp_path), resolve_fn=resolve
    )
    future.result(timeout=10)
    done = extraction_jobs.get_report_job(DEAL_ID)
    assert done.status == "failed"
    assert "live extraction unavailable" in done.error


def test_report_ingest_job_store_is_separate_from_extraction(tmp_path):
    """Report-ingest jobs are keyed separately from prospectus-extraction jobs —
    one deal can have both tracked independently."""
    extraction_jobs.submit_extraction(
        DEAL_ID,
        prospectus_url="http://example/p.pdf",
        deal_name="Green Lion 2026-1 B.V.",
        cache_dir=str(tmp_path),
        extract_fn=_materialising_extract_fn,
    )[1].result(timeout=10)
    extraction_jobs.submit_report_ingest(
        DEAL_ID, deal=_DEAL, cache_dir=str(tmp_path), resolve_fn=_make_resolve_stub()
    )[1].result(timeout=10)

    assert extraction_jobs.get_job(DEAL_ID) is not extraction_jobs.get_report_job(DEAL_ID)
    assert extraction_jobs.get_job(DEAL_ID).status == "succeeded"
    assert extraction_jobs.get_report_job(DEAL_ID).status == "succeeded"


def test_report_ingest_idempotent_while_running(tmp_path):
    """A re-submit while running (no force) returns the in-flight job, no second run."""
    # A resolver that blocks until released, so the first job stays 'running'.
    import threading

    release = threading.Event()

    def _blocking(deal_id, deal, *, cache_dir, allow_live):
        release.wait(timeout=10)
        return object()

    job1, fut1 = extraction_jobs.submit_report_ingest(
        DEAL_ID, deal=_DEAL, cache_dir=str(tmp_path), resolve_fn=_blocking
    )
    job2, fut2 = extraction_jobs.submit_report_ingest(
        DEAL_ID, deal=_DEAL, cache_dir=str(tmp_path), resolve_fn=_blocking
    )
    try:
        assert job2 is job1  # same in-flight job returned
        assert fut2 is None  # idempotent enqueue — no second future
    finally:
        release.set()
        if fut1 is not None:
            fut1.result(timeout=10)


def test_get_report_job_none_when_never_submitted():
    assert extraction_jobs.get_report_job("never-submitted") is None
