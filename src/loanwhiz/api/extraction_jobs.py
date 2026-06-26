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
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal, Optional

from loanwhiz.extraction.assembler import DealModel, extract_deal_model
from loanwhiz.primitives.audit_logger import audit_extraction_result
from loanwhiz.primitives.base import Citation

logger = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "succeeded", "failed"]

# Governance identity recorded on every extraction-job audit entry (#404). The
# on-demand /extract job runs the module-level ``extract_deal_model`` primitive
# under governance: each run (success OR failure) emits one FINOS-aligned audit
# record carrying the deal's REAL confidence + citations (never a hardcoded
# placeholder). The audit JSONL lands under ``{audit_log_dir}/{name}/{date}.jsonl``.
_EXTRACTION_PRIMITIVE_NAME = "deal_extraction"
_EXTRACTION_PRIMITIVE_VERSION = "0.1.0"
_EXTRACTION_MODULE_PATH = "loanwhiz.extraction.assembler.extract_deal_model"

# Default audit log dir — patchable in tests (mirrors ``main.API_AUDIT_LOG_DIR``)
# so a test can point it at a tmp_path and assert the governance record was
# written without polluting /tmp.
AUDIT_LOG_DIR = "/tmp/loanwhiz_audit"

# Type of the wrapped extraction primitive — the injection seam. The real
# implementation is ``loanwhiz.extraction.assembler.extract_deal_model``; tests
# pass a fast stub with the same signature so no real extraction runs.
ExtractFn = Callable[..., DealModel]

# Injection seam for the governance audit sink (#404). The real implementation is
# ``loanwhiz.primitives.audit_logger.audit_extraction_result``; tests pass a fast
# in-memory recorder with a compatible signature so the governance wiring is
# asserted without touching the JSONL store.
AuditFn = Callable[..., object]


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

    # Per-waterfall / per-covenant-set extraction confidence (#405): the real
    # step-usability and trigger-extraction quality the extractors already
    # compute, surfaced so an operator polling status can tell a reliable section
    # from noise — not just the deal-level ``completeness_score``. Additive: all
    # pre-existing summary keys are unchanged.
    extraction_confidence = {
        "waterfalls": {
            kind: waterfall.get("extraction_confidence")
            for kind, waterfall in model.waterfalls.items()
        },
        "covenants": model.covenants.get("extraction_confidence"),
    }

    return {
        "completeness_score": model.metadata.completeness_score,
        "trigger_count": len(model.trigger_names),
        "citation_count": citation_count,
        "sections_found": model.metadata.sections_found,
        "extraction_confidence": extraction_confidence,
    }


def _extraction_confidence(model: DealModel) -> float:
    """Honest governance confidence for an extracted :class:`DealModel`.

    The on-demand /extract job runs UNDER governance (#404), so its audit record
    must carry the **real** confidence the extraction primitives produced — never
    the hollow hardcoded ``0.9`` an older audit flagged. We take the conservative
    ``min`` over the genuine quality signals the model already carries:

    - ``metadata.completeness_score`` — the real extraction-coverage metric.
    - each waterfall's ``extraction_confidence`` — per-section step-usability.
    - the covenants' ``extraction_confidence`` — trigger-extraction certainty.

    ``min`` mirrors the evidence-pack convention (aggregate = min of per-tool
    confidences): one weak section drags the whole-model confidence down, which
    is the honest signal for routing to human review. With no per-section signal
    present, the completeness score stands alone.
    """
    signals: list[float] = [float(model.metadata.completeness_score)]
    for waterfall in model.waterfalls.values():
        conf = waterfall.get("extraction_confidence")
        if conf is not None:
            signals.append(float(conf))
    covenant_conf = model.covenants.get("extraction_confidence")
    if covenant_conf is not None:
        signals.append(float(covenant_conf))
    return min(1.0, max(0.0, min(signals)))


def _extraction_citations(model: DealModel) -> list[Citation]:
    """Real source citations grounding an extracted :class:`DealModel`.

    Surfaces the per-step (waterfall) and per-trigger (covenant) ``citation``
    objects the model already carries as governed :class:`Citation`s — the same
    provenance ``_summarise`` only *counts*. Each citation's ``document`` /
    ``page_or_section`` becomes a real ``Citation`` so the audit record's
    citation trail is the actual grounded sources, not an empty list.
    """
    citations: list[Citation] = []

    def _as_citation(raw: dict, fallback_excerpt: str) -> Citation | None:
        if not isinstance(raw, dict):
            return None
        document = raw.get("document")
        if not document:
            return None
        return Citation(
            document=str(document),
            page_or_row=raw.get("page_or_section") or raw.get("page_or_row"),
            excerpt=str(raw.get("excerpt") or fallback_excerpt),
        )

    for waterfall in model.waterfalls.values():
        for step in waterfall.get("steps", []) or []:
            raw = step.get("citation")
            if raw:
                cit = _as_citation(
                    raw, str(step.get("description") or step.get("recipient") or "")
                )
                if cit is not None:
                    citations.append(cit)
    for trigger in model.covenants.get("triggers", []) or []:
        raw = trigger.get("citation")
        if raw:
            cit = _as_citation(raw, str(trigger.get("name") or ""))
            if cit is not None:
                citations.append(cit)
    return citations


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
    audit_fn: AuditFn = audit_extraction_result,
    audit_log_dir: Optional[str] = None,
) -> ExtractionJob:
    """Run the wrapped extraction on the worker thread; record terminal state.

    Materialisation is automatic: ``extract_deal_model`` writes the runtime cache
    at ``{cache_dir}/{slug(deal_name)}.json``, which is the single source of truth
    the cold-start ``/deal/{id}/model`` reader serves. No second write here.

    Governance (#404): the job runs UNDER governance — both the success and the
    failure path emit one FINOS-aligned audit record via ``audit_fn`` (default
    :func:`~loanwhiz.primitives.audit_logger.audit_extraction_result`). The record
    carries who/what/when (the ``deal_extraction`` primitive identity + UTC clock +
    duration), the input hash (over prospectus_url / deal_name / force), the
    extracted output, and the **real** confidence + citations the extraction
    primitives produced — never a hardcoded placeholder. The audit call is
    best-effort and additionally guarded here, so a governance side-channel
    failure can never change the job's success/failure outcome.

    Any exception is caught and recorded as ``failed`` with the reason — the
    worker thread never crashes the pool and the poll never 500s.
    """
    started = time.perf_counter()
    log_dir = audit_log_dir if audit_log_dir is not None else AUDIT_LOG_DIR
    audit_input = {
        "prospectus_url": prospectus_url,
        "deal_name": deal_name,
        "force_refresh": force,
    }

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
        confidence = _extraction_confidence(model)
        citations = _extraction_citations(model)

        entry = _safe_audit(
            audit_fn,
            primitive_name=_EXTRACTION_PRIMITIVE_NAME,
            primitive_version=_EXTRACTION_PRIMITIVE_VERSION,
            input=audit_input,
            output=model,
            confidence=confidence,
            citations=citations,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            module_path=_EXTRACTION_MODULE_PATH,
            log_dir=log_dir,
        )
        # Surface the governed confidence + the human-review flag + the audit id
        # alongside the existing count summary (additive — existing keys unchanged).
        summary["confidence"] = confidence
        summary["human_review_required"] = _human_review_required(entry, confidence)
        summary["audit_entry_id"] = _entry_id(entry)

        with _LOCK:
            job.status = "succeeded"
            job.finished_at = _now_iso()
            job.summary = summary
    except Exception as exc:  # noqa: BLE001 — honest failure surfacing is the point
        logger.exception("Extraction job for deal %s failed", job.deal_id)
        reason = f"{type(exc).__name__}: {exc}"
        # A failed extraction also runs under governance: emit a failure audit
        # trail (confidence 0.0, no citations, the error as output) so a
        # creds-less / pipeline failure is recorded, not silently un-governed.
        entry = _safe_audit(
            audit_fn,
            primitive_name=_EXTRACTION_PRIMITIVE_NAME,
            primitive_version=_EXTRACTION_PRIMITIVE_VERSION,
            input=audit_input,
            output={"error": reason},
            confidence=0.0,
            citations=[],
            duration_ms=(time.perf_counter() - started) * 1000.0,
            module_path=_EXTRACTION_MODULE_PATH,
            log_dir=log_dir,
        )
        with _LOCK:
            job.status = "failed"
            job.finished_at = _now_iso()
            job.error = reason
            job.summary = {"audit_entry_id": _entry_id(entry)}
    return job


def _safe_audit(audit_fn: AuditFn, **kwargs) -> object:
    """Call ``audit_fn`` with the governance side-channel fully isolated.

    ``audit_extraction_result`` is already best-effort, but the injection seam
    accepts arbitrary callables (incl. test recorders), so this extra guard
    guarantees the governance call can never change the job's outcome — the
    contract from ``main._audit``.
    """
    try:
        return audit_fn(**kwargs)
    except Exception:  # noqa: BLE001 — audit is a side-channel; never fail the job
        logger.exception("Governance audit for an extraction job failed (ignored)")
        return None


def _entry_id(entry: object) -> Optional[str]:
    """Best-effort read of an audit entry's ``entry_id`` (``None`` when unaudited)."""
    return getattr(entry, "entry_id", None)


def _human_review_required(entry: object, confidence: float) -> bool:
    """The human-review flag — from the audit entry when present, else the rule.

    Prefers the persisted entry's flag (so the surfaced signal matches the
    governance record's threshold), falling back to the default-threshold rule
    when the audit was skipped (best-effort failure).
    """
    flag = getattr(entry, "human_review_required", None)
    if flag is not None:
        return bool(flag)
    return confidence < 0.7


# ---------------------------------------------------------------------------
# Report-ingest jobs (#399)
# ---------------------------------------------------------------------------
# The self-service report-ingest path mirrors the prospectus-extraction job above:
# live report extraction (PDF fetch + OCR/LLM) is a minutes-long network run, so it
# must NOT block the request thread (the same no-hang guarantee #384 established).
# ``POST /deal/{id}/ingest/report`` enqueues a background job that calls #398's
# ``report_extractor.resolve_parsed_report(..., allow_live=True)``, which populates
# the durable report cache the offline ``/report-gate`` / ``/waterfall`` GETs then
# serve — closing the self-service loop with one source of truth.
#
# The job store is keyed SEPARATELY from the prospectus jobs (``_REPORT_JOBS``) so a
# deal can have an in-flight report ingest and an in-flight prospectus extraction
# tracked independently; both run on the shared single-worker ``_EXECUTOR``.

# Injection seam for the report resolver. The real implementation is
# ``loanwhiz.primitives.report_extractor.resolve_parsed_report``; tests pass a fast
# stub with a compatible signature so NO real network/LLM extraction runs.
ResolveReportFn = Callable[..., object]

_REPORT_JOBS: dict[str, ExtractionJob] = {}


def get_report_job(deal_id: str) -> Optional[ExtractionJob]:
    """Return the most recent report-ingest job for ``deal_id``, or ``None``."""
    with _LOCK:
        return _REPORT_JOBS.get(deal_id)


def reset_report_jobs() -> None:
    """Clear the in-process report-ingest store. Test-only isolation helper."""
    with _LOCK:
        _REPORT_JOBS.clear()


def _run_report_ingest(
    job: ExtractionJob,
    *,
    deal_id: str,
    deal: dict,
    cache_dir: str,
    allow_live: bool,
    resolve_fn: ResolveReportFn,
) -> ExtractionJob:
    """Run the report resolver on the worker thread; record terminal state.

    On success the resolver has populated the durable report cache (one source of
    truth — the offline ``/report-gate`` reader then serves it). Any exception
    (download, OCR, LLM, ``ReportUnavailable``) is caught and recorded as ``failed``
    with the reason — the worker thread never crashes the pool and the poll never
    500s.
    """
    with _LOCK:
        job.status = "running"
        job.started_at = _now_iso()

    try:
        resolve_fn(deal_id, deal, cache_dir=cache_dir, allow_live=allow_live)
        with _LOCK:
            job.status = "succeeded"
            job.finished_at = _now_iso()
    except Exception as exc:  # noqa: BLE001 — honest failure surfacing is the point
        logger.exception("Report-ingest job for deal %s failed", job.deal_id)
        with _LOCK:
            job.status = "failed"
            job.finished_at = _now_iso()
            job.error = f"{type(exc).__name__}: {exc}"
    return job


def submit_report_ingest(
    deal_id: str,
    *,
    deal: dict,
    cache_dir: str,
    allow_live: bool = True,
    force: bool = False,
    resolve_fn: Optional[ResolveReportFn] = None,
) -> tuple[ExtractionJob, Optional[Future]]:
    """Enqueue a background report ingest for ``deal_id`` and return immediately.

    Mirrors :func:`submit_extraction`: returns ``(job, future)`` where ``future`` is
    ``None`` when an already-running job is returned unchanged (idempotent enqueue),
    otherwise the :class:`~concurrent.futures.Future` for the scheduled run so tests
    can await completion deterministically without a real long run.

    Re-submit semantics match the prospectus job: a ``queued``/``running`` job for
    this deal (without ``force``) is returned unchanged; a finished job or any
    ``force`` submit is replaced.

    ``resolve_fn`` is the injection seam; when ``None`` it resolves to
    ``report_extractor.resolve_parsed_report`` **at call time** so a test's patch is
    honoured. Tests pass a fast stub — no real network/LLM extraction runs.
    """
    if resolve_fn is None:
        from loanwhiz.primitives.report_extractor import resolve_parsed_report

        resolve_fn = resolve_parsed_report

    with _LOCK:
        existing = _REPORT_JOBS.get(deal_id)
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
        _REPORT_JOBS[deal_id] = job

    future = _EXECUTOR.submit(
        _run_report_ingest,
        job,
        deal_id=deal_id,
        deal=deal,
        cache_dir=cache_dir,
        allow_live=allow_live,
        resolve_fn=resolve_fn,
    )
    return job, future


def submit_extraction(
    deal_id: str,
    *,
    prospectus_url: str,
    deal_name: str,
    cache_dir: str,
    force: bool = False,
    extract_fn: Optional[ExtractFn] = None,
    audit_fn: Optional[AuditFn] = None,
    audit_log_dir: Optional[str] = None,
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
    if audit_fn is None:
        audit_fn = audit_extraction_result

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
        audit_fn=audit_fn,
        audit_log_dir=audit_log_dir,
    )
    return job, future
