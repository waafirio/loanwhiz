"""In-process background extraction jobs for the on-demand onboarding path (#384).

Today a deal's extracted :class:`~loanwhiz.extraction.assembler.DealModel` is
produced **offline**: a human runs ``scripts/extract_c2_deals.py`` /
``scripts/seed_deal_models.py`` with GCP creds set and waits out the ~20–37 min
Docling+Vertex run. The serving API deliberately never cold-extracts inline
(``GET /deal/{id}/model`` reads the cache read-only and returns ``not_cached`` on
a miss rather than blocking a request for half an hour).

This module makes onboarding a *product action* without regressing that
no-hang guarantee: it runs the **same** governed ``extract_deal_model`` primitive
on a small background thread pool and tracks per-deal job state in an in-process
store, so ``POST /deal/{id}/extract`` can return ``202`` immediately and
``GET /deal/{id}/extract/status`` can poll ``queued|running|succeeded|failed``.

Design (per the approved plan for #384):

- **No new infra dependency.** A ``ThreadPoolExecutor(max_workers=1)`` keeps the
  long blocking call off the request thread; an in-process dict is the status
  store. This is honest about the demo's single-process Uvicorn deployment — the
  store is process-local and resets on restart. That is acceptable because the
  *durable* output is the materialised cache the cold-start reader already serves
  (one source of truth): ``extract_deal_model`` writes
  ``{cache_dir}/{slug(deal_name)}.json`` and ``GET /deal/{id}/model`` reads it.
- **One extraction path, not a fork.** The job wraps the identical
  ``extract_deal_model`` the offline scripts call. The ``extract_fn`` parameter is
  an **injection seam** so tests stub the long run — tests must never invoke a
  real ~20–37 min Docling/Vertex extraction.
- **Honest failure surfacing.** Any exception (missing GCP creds, download, OCR,
  LLM) raised deep in the pipeline propagates up, is caught here, and recorded as
  a ``failed`` status with the reason. The request thread never touches the long
  call, so a creds-less environment yields a fast ``202`` then a ``failed`` poll —
  never a hung request and never an unhandled worker crash.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal, Optional

from loanwhiz.extraction.assembler import DealModel, extract_deal_model

logger = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "succeeded", "failed"]

# Type of the wrapped extraction primitive — the injection seam. The real
# implementation is ``loanwhiz.extraction.assembler.extract_deal_model``; tests
# pass a fast stub with the same signature so no real extraction runs.
ExtractFn = Callable[..., DealModel]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _summarise(model: DealModel) -> dict:
    """Governed confidence/citation summary surfaced on a ``succeeded`` job.

    Counts the citations attached to the extracted waterfall steps and covenant
    triggers — the same provenance the model carries — alongside the completeness
    score and trigger count, so an operator polling status sees the governed
    quality signal without re-reading the full model.
    """
    citation_count = 0
    for waterfall in model.waterfalls.values():
        for step in waterfall.get("steps", []) or []:
            if step.get("citation"):
                citation_count += 1
    for trigger in model.covenants.get("triggers", []) or []:
        if trigger.get("citation"):
            citation_count += 1

    return {
        "completeness_score": model.metadata.completeness_score,
        "trigger_count": len(model.trigger_names),
        "citation_count": citation_count,
        "sections_found": model.metadata.sections_found,
    }


@dataclass
class ExtractionJob:
    """State of one on-demand extraction job for a deal.

    One job per deal id is tracked (the most recent submit); see
    :func:`submit_extraction` for the re-submit semantics.
    """

    deal_id: str
    status: JobStatus
    force: bool
    submitted_at: str
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    error: Optional[str] = None        # reason on ``failed``
    summary: Optional[dict] = None     # governed summary on ``succeeded``

    def to_response(self) -> dict:
        """Serialise to the public status-poll body."""
        return {
            "deal_id": self.deal_id,
            "status": self.status,
            "force": self.force,
            "submitted_at": self.submitted_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
            "summary": self.summary,
        }


# Module-level state. Process-local by design (see module docstring). The lock
# guards the store and the per-job status mutation the worker thread performs.
_JOBS: dict[str, ExtractionJob] = {}
_LOCK = threading.Lock()
_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="extraction-job")


def get_job(deal_id: str) -> Optional[ExtractionJob]:
    """Return the most recent job for ``deal_id``, or ``None`` if never submitted."""
    with _LOCK:
        return _JOBS.get(deal_id)


def reset_jobs() -> None:
    """Clear the in-process store. Test-only helper for isolation between cases."""
    with _LOCK:
        _JOBS.clear()


def _run_extraction(
    job: ExtractionJob,
    *,
    prospectus_url: str,
    deal_name: str,
    cache_dir: str,
    force: bool,
    extract_fn: ExtractFn,
) -> ExtractionJob:
    """Run the wrapped extraction on the worker thread; record terminal state.

    Materialisation is automatic: ``extract_deal_model`` writes the runtime cache
    at ``{cache_dir}/{slug(deal_name)}.json``, which is the single source of truth
    the cold-start ``/deal/{id}/model`` reader serves. No second write here.

    Any exception is caught and recorded as ``failed`` with the reason — the
    worker thread never crashes the pool and the poll never 500s.
    """
    with _LOCK:
        job.status = "running"
        job.started_at = _now_iso()

    try:
        model = extract_fn(
            prospectus_url=prospectus_url,
            deal_name=deal_name,
            cache_dir=cache_dir,
            force_refresh=force,
        )
        summary = _summarise(model)
        with _LOCK:
            job.status = "succeeded"
            job.finished_at = _now_iso()
            job.summary = summary
    except Exception as exc:  # noqa: BLE001 — honest failure surfacing is the point
        logger.exception("Extraction job for deal %s failed", job.deal_id)
        with _LOCK:
            job.status = "failed"
            job.finished_at = _now_iso()
            job.error = f"{type(exc).__name__}: {exc}"
    return job


def submit_extraction(
    deal_id: str,
    *,
    prospectus_url: str,
    deal_name: str,
    cache_dir: str,
    force: bool = False,
    extract_fn: Optional[ExtractFn] = None,
) -> tuple[ExtractionJob, Optional[Future]]:
    """Enqueue a background extraction for ``deal_id`` and return immediately.

    Returns ``(job, future)``. ``future`` is ``None`` when an already-running job
    is returned unchanged (idempotent enqueue); otherwise it is the
    :class:`~concurrent.futures.Future` for the scheduled run so tests can await
    completion deterministically without a real long run.

    Re-submit semantics: while a job is ``running`` (or ``queued``) for this deal
    and ``force`` is not set, the in-flight job is returned unchanged rather than
    starting a second run. A finished (``succeeded``/``failed``) job — or any
    ``force`` submit — is replaced by a fresh job.

    ``extract_fn`` is the injection seam; when ``None`` it resolves to the
    module-level :func:`extract_deal_model` **at call time** (not bind time) so a
    test's ``patch("loanwhiz.api.extraction_jobs.extract_deal_model", ...)`` is
    honoured. Tests pass a fast stub so no real extraction runs.
    """
    if extract_fn is None:
        extract_fn = extract_deal_model

    with _LOCK:
        existing = _JOBS.get(deal_id)
        if (
            existing is not None
            and existing.status in ("queued", "running")
            and not force
        ):
            return existing, None

        job = ExtractionJob(
            deal_id=deal_id,
            status="queued",
            force=force,
            submitted_at=_now_iso(),
        )
        _JOBS[deal_id] = job

    future = _EXECUTOR.submit(
        _run_extraction,
        job,
        prospectus_url=prospectus_url,
        deal_name=deal_name,
        cache_dir=cache_dir,
        force=force,
        extract_fn=extract_fn,
    )
    return job, future
