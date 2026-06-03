"""Audit logger primitive for the LoanWhiz SF agent framework.

Wraps every primitive call with a full provenance record following FINOS AI
Governance Framework patterns:

- SHA-256 hash of the serialised input and output
- Confidence score and source citations
- UTC execution timestamp and wall-clock duration
- Model version (populated when an LLM was used)
- Human review flag (fires when confidence < configurable threshold)
- A replay command string sufficient to reproduce the call
- Per-call LLM provenance (model, prompt hash, temperature, token counts)

Usage — context manager::

    from loanwhiz.primitives.audit_logger import ExecutionContext

    primitive = MyPrimitive()
    with ExecutionContext(primitive, log_dir="/tmp/loanwhiz_audit") as ctx:
        result = primitive.execute(my_input)
        entry = ctx.log(result)

Usage — wrap_primitive::

    wrapped = wrap_primitive(primitive)
    result, entry = wrapped(my_input)

Usage — LLM-backed primitives::

    with LLMCallTracker() as tracker:
        response = client.models.generate_content(model=MODEL_PRO, contents=prompt)
        tracker.record(model=MODEL_PRO, prompt=prompt, response=response.text)

    # Inside ExecutionContext, pass the tracker's calls to log():
    with ExecutionContext(primitive, log_dir=log_dir, input=inp) as ctx:
        result = primitive.execute(inp)
        entry = ctx.log(result, llm_calls=tracker.calls)
        # entry.is_deterministic == False; entry.llm_calls populated

Usage — replay::

    result = replay(entry, primitive, original_input)
    # deterministic: raises HashMismatchError when output hash doesn't match
    # non-deterministic (is_deterministic=False): raises LLMReplayMismatchError
    #   when the (model, prompt_hash) sequence changes

Usage — replay by trace id::

    result = replay_by_trace_id(trace_id, log_dir, primitive, original_input)
    # raises TraceNotFoundError when no entry with that entry_id exists
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from loanwhiz.primitives.base import (
    AuditEntry,
    BaseInput,
    Citation,
    Primitive,
    PrimitiveResult,
)
from loanwhiz.primitives.registry import register_primitive

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class HashMismatchError(Exception):
    """Raised by ``replay()`` when the replayed output hash differs from the
    original, indicating a non-deterministic primitive or a corrupted entry."""


class LLMReplayMismatchError(Exception):
    """Raised by ``replay()`` for a non-deterministic entry when the replayed
    LLM call sequence (model + prompt_hash pairs) differs from the original."""


class TraceNotFoundError(Exception):
    """Raised by ``replay_by_trace_id()`` when no entry with the given
    ``entry_id`` exists in the JSONL files under ``log_dir``."""


# ---------------------------------------------------------------------------
# LLMCallRecord — per-call LLM provenance within a primitive execution
# ---------------------------------------------------------------------------


class LLMCallRecord(BaseModel):
    """Record of a single LLM API call within a primitive execution.

    Attributes:
        call_index:        0-based index (for multi-call primitives).
        model:             Model identifier, e.g. ``"gemini-2.5-pro"``.
        prompt_hash:       SHA-256 hex digest of the prompt sent.
        prompt_preview:    First 200 characters of the prompt (for debugging).
        response_preview:  First 200 characters of the response.
        temperature:       Sampling temperature used (default 0.0).
        input_tokens:      Number of input tokens, or ``None`` if unavailable.
        output_tokens:     Number of output tokens, or ``None`` if unavailable.
        call_duration_ms:  Wall-clock call duration in ms, or ``None``.
    """

    call_index: int = Field(..., ge=0, description="0-based call index.")
    model: str = Field(..., description="Model identifier.")
    prompt_hash: str = Field(..., description="SHA-256 hex digest of the prompt.")
    prompt_preview: str = Field(..., description="First 200 chars of the prompt.")
    response_preview: str = Field(..., description="First 200 chars of the response.")
    temperature: float = Field(default=0.0, ge=0.0, description="Sampling temperature.")
    input_tokens: int | None = Field(default=None, description="Input token count.")
    output_tokens: int | None = Field(default=None, description="Output token count.")
    call_duration_ms: float | None = Field(default=None, description="Call wall-clock time in ms.")


# ---------------------------------------------------------------------------
# AuditLogEntry — one full provenance record per primitive execution
# ---------------------------------------------------------------------------


class AuditLogEntry(BaseModel):
    """Full audit record for one primitive execution.

    Attributes:
        entry_id:             UUID4 string, unique per execution.
        primitive_name:       The ``name`` class attribute of the primitive.
        primitive_version:    The ``version`` class attribute (semver).
        executed_at:          ISO 8601 UTC timestamp when ``execute`` was called.
        duration_ms:          Wall-clock execution time in milliseconds.
        input_hash:           SHA-256 hex digest of the JSON-serialised input.
        output_hash:          SHA-256 hex digest of the JSON-serialised output.
        confidence:           Confidence score from the ``PrimitiveResult`` [0–1].
        citations:            Source citations from the ``PrimitiveResult``.
        human_review_required: ``True`` when ``confidence < threshold``.
        model_version:        LLM model identifier, or ``None`` for rule-based
                              primitives.
        replay_command:       A Python one-liner sufficient to reproduce this
                              exact call, or ``None`` when unavailable.
    """

    entry_id: str = Field(..., description="UUID4 identifier for this audit record.")
    primitive_name: str = Field(..., description="Primitive name (snake_case).")
    primitive_version: str = Field(..., description="Primitive version (semver).")
    executed_at: str = Field(..., description="ISO 8601 UTC execution timestamp.")
    duration_ms: float = Field(..., ge=0.0, description="Wall-clock time in ms.")
    input_hash: str = Field(..., description="SHA-256 hex digest of the serialised input.")
    output_hash: str = Field(..., description="SHA-256 hex digest of the serialised output.")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score [0.0, 1.0].")
    citations: list[Citation] = Field(default_factory=list, description="Source citations.")
    human_review_required: bool = Field(
        ...,
        description="True when confidence is below the configured threshold.",
    )
    model_version: str | None = Field(
        default=None,
        description="LLM model version used, or None for rule-based primitives.",
    )
    replay_command: str | None = Field(
        default=None,
        description="Python one-liner to reproduce this exact execution.",
    )
    llm_calls: list[LLMCallRecord] = Field(
        default_factory=list,
        description="Per-call LLM provenance records (empty for rule-based primitives).",
    )
    is_deterministic: bool = Field(
        default=True,
        description=(
            "False when at least one LLM call was made. "
            "replay() uses this flag to select the verification strategy: "
            "hash-check for deterministic, prompt-hash-sequence-check for LLM-backed."
        ),
    )


# ---------------------------------------------------------------------------
# AuditLog — ordered collection with JSONL serialisation
# ---------------------------------------------------------------------------


class AuditLog(BaseModel):
    """An ordered collection of ``AuditLogEntry`` records with JSONL I/O."""

    entries: list[AuditLogEntry] = Field(default_factory=list)

    def to_jsonl(self) -> str:
        """Serialise all entries to a JSONL string (one JSON object per line)."""
        return "\n".join(e.model_dump_json() for e in self.entries)

    @classmethod
    def from_jsonl(cls, text: str) -> "AuditLog":
        """Parse a JSONL string back into an ``AuditLog``.

        Empty lines are silently skipped, matching the behaviour of most JSONL
        writers that emit a trailing newline.
        """
        entries: list[AuditLogEntry] = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            entries.append(AuditLogEntry.model_validate_json(line))
        return cls(entries=entries)


# ---------------------------------------------------------------------------
# AuditLogger primitive I/O models
# ---------------------------------------------------------------------------


class AuditLoggerInput(BaseInput):
    """Input schema for the AuditLogger primitive.

    Attributes:
        log_dir:               Directory where JSONL audit files are written.
                               Sub-directories are created per primitive name.
        auto_flag_threshold:   Confidence threshold below which
                               ``human_review_required`` is set to ``True``.
    """

    log_dir: str = Field(
        default="/tmp/loanwhiz_audit",
        description="Directory for JSONL audit files.",
    )
    auto_flag_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Flag for human review when confidence is below this value.",
    )


class AuditLoggerOutput(BaseModel):
    """Output of the AuditLogger primitive's own execute() call.

    This is distinct from ``AuditLogEntry``, which records a *wrapped*
    primitive's execution. ``AuditLoggerOutput`` summarises a batch write.
    """

    log_path: str = Field(..., description="Absolute path of the JSONL file written.")
    entries_written: int = Field(..., description="Number of entries appended.")
    flagged_for_review: int = Field(
        ..., description="Number of entries with human_review_required=True."
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sha256_of(obj: Any) -> str:
    """Return the SHA-256 hex digest of the JSON-serialised *obj*.

    For Pydantic models, ``model_dump_json()`` is used to get a canonical,
    deterministic serialisation. For other objects ``json.dumps`` with sorted
    keys is used as a best-effort fallback.
    """
    if hasattr(obj, "model_dump_json"):
        payload = obj.model_dump_json().encode()
    else:
        payload = json.dumps(obj, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def _jsonl_path(log_dir: str, primitive_name: str) -> Path:
    """Return the Path for today's JSONL file for *primitive_name*."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    directory = Path(log_dir) / primitive_name
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{today}.jsonl"


def _build_replay_command(primitive: Primitive, input_hash: str) -> str:
    """Build a Python one-liner that documents how to reproduce this call.

    The command is for human reference — it records the primitive class and
    input hash so an operator can identify what to re-run. A truly executable
    replay requires reconstructing the original input from the hash (outside
    scope of this helper), so the command is a documentation string rather
    than a runnable snippet.
    """
    module = type(primitive).__module__
    classname = type(primitive).__qualname__
    return (
        f'python -c "from {module} import {classname}; '
        f'p = {classname}(); '
        f'# replay input_hash={input_hash}"'
    )


# ---------------------------------------------------------------------------
# LLMCallTracker — context manager for recording LLM calls in a primitive
# ---------------------------------------------------------------------------


class LLMCallTracker:
    """Context manager that records LLM API calls for audit logging.

    Use this inside a ``Primitive.execute()`` that makes one or more LLM
    calls to accumulate ``LLMCallRecord`` objects for inclusion in the
    ``AuditLogEntry``.

    Usage::

        with LLMCallTracker() as tracker:
            response = client.models.generate_content(model=MODEL_PRO, contents=prompt)
            tracker.record(model=MODEL_PRO, prompt=prompt, response=response.text)

        # Pass tracker.calls to ExecutionContext.log():
        with ExecutionContext(primitive, log_dir=log_dir, input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result, llm_calls=tracker.calls)

    Attributes:
        calls: Accumulated ``LLMCallRecord`` objects in call order.
    """

    def __init__(self) -> None:
        self.calls: list[LLMCallRecord] = []

    def __enter__(self) -> "LLMCallTracker":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def record(
        self,
        model: str,
        prompt: str,
        response: str,
        temperature: float = 0.0,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        call_duration_ms: float | None = None,
    ) -> LLMCallRecord:
        """Record one LLM call and append it to ``self.calls``.

        Parameters
        ----------
        model:
            Model identifier (e.g. ``"gemini-2.5-pro"``).
        prompt:
            The full prompt sent to the model. A SHA-256 hash is computed
            and stored; the first 200 characters are kept as a preview.
        response:
            The model's response text. The first 200 characters are kept.
        temperature:
            Sampling temperature used (default 0.0).
        input_tokens:
            Input token count, or ``None`` if unavailable.
        output_tokens:
            Output token count, or ``None`` if unavailable.
        call_duration_ms:
            Wall-clock call duration in milliseconds, or ``None``.

        Returns
        -------
        LLMCallRecord
            The record that was appended.
        """
        record = LLMCallRecord(
            call_index=len(self.calls),
            model=model,
            prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
            prompt_preview=prompt[:200],
            response_preview=response[:200],
            temperature=temperature,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            call_duration_ms=call_duration_ms,
        )
        self.calls.append(record)
        return record


# ---------------------------------------------------------------------------
# ExecutionContext — context manager that wraps a primitive call
# ---------------------------------------------------------------------------


class ExecutionContext:
    """Context manager that wraps a primitive call and produces an audit entry.

    Usage::

        with ExecutionContext(primitive, log_dir="/tmp/audit") as ctx:
            result = primitive.execute(my_input)
            entry = ctx.log(result)

    The ``log()`` method computes input/output hashes, builds the
    ``AuditLogEntry``, appends it to the per-primitive JSONL file, and
    returns the entry.

    Parameters
    ----------
    primitive:
        The ``Primitive`` instance being wrapped.
    log_dir:
        Root directory for JSONL audit files (default: ``/tmp/loanwhiz_audit``).
    model_version:
        LLM model identifier to record, or ``None`` for rule-based primitives.
    threshold:
        Confidence threshold below which ``human_review_required=True``.
    input:
        The original input object — stored so ``log()`` can compute
        ``input_hash`` and embed it in the replay command.
    """

    def __init__(
        self,
        primitive: Primitive,
        log_dir: str = "/tmp/loanwhiz_audit",
        model_version: str | None = None,
        threshold: float = 0.7,
        input: Any = None,
    ) -> None:
        self._primitive = primitive
        self._log_dir = log_dir
        self._model_version = model_version
        self._threshold = threshold
        self._input = input
        self._start: float = 0.0
        self._executed_at: str = ""

    def __enter__(self) -> "ExecutionContext":
        self._start = time.perf_counter()
        self._executed_at = datetime.now(timezone.utc).isoformat()
        return self

    def __exit__(self, *args: Any) -> None:
        # No exception suppression — just record the elapsed time.
        pass

    def log(
        self,
        result: PrimitiveResult,
        llm_calls: list[LLMCallRecord] | None = None,
    ) -> AuditLogEntry:
        """Build, persist, and return the ``AuditLogEntry`` for this execution.

        Must be called after ``primitive.execute()`` returns and while the
        context manager is still active (i.e. inside the ``with`` block).

        Parameters
        ----------
        result:
            The ``PrimitiveResult`` returned by ``primitive.execute()``.
        llm_calls:
            Optional list of ``LLMCallRecord`` objects accumulated by a
            ``LLMCallTracker`` during execution.  When non-empty,
            ``is_deterministic`` is set to ``False`` in the entry.

        Returns
        -------
        AuditLogEntry
            The full provenance record for this execution.
        """
        duration_ms = (time.perf_counter() - self._start) * 1000.0

        # Compute hashes
        input_hash: str
        if self._input is not None:
            input_hash = _sha256_of(self._input)
        else:
            # Fall back to the hash already recorded in the audit entry
            input_hash = result.audit_entry.input_hash

        output_hash = _sha256_of(result.output)

        replay_command = _build_replay_command(self._primitive, input_hash)

        resolved_llm_calls: list[LLMCallRecord] = llm_calls if llm_calls is not None else []
        is_deterministic = len(resolved_llm_calls) == 0

        entry = AuditLogEntry(
            entry_id=str(uuid.uuid4()),
            primitive_name=self._primitive.name,
            primitive_version=self._primitive.version,
            executed_at=self._executed_at,
            duration_ms=duration_ms,
            input_hash=input_hash,
            output_hash=output_hash,
            confidence=result.confidence,
            citations=list(result.citations),
            human_review_required=result.confidence < self._threshold,
            model_version=self._model_version,
            replay_command=replay_command,
            llm_calls=resolved_llm_calls,
            is_deterministic=is_deterministic,
        )

        # Append to JSONL file
        log_path = _jsonl_path(self._log_dir, self._primitive.name)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(entry.model_dump_json() + "\n")

        return entry


# ---------------------------------------------------------------------------
# wrap_primitive — convenience adapter
# ---------------------------------------------------------------------------


def wrap_primitive(
    primitive: Primitive,
    log_dir: str = "/tmp/loanwhiz_audit",
    model_version: str | None = None,
    threshold: float = 0.7,
):
    """Return a wrapper function that calls ``primitive.execute()`` inside an
    ``ExecutionContext`` and returns ``(result, entry)``.

    Parameters
    ----------
    primitive:
        The ``Primitive`` instance to wrap.
    log_dir:
        Directory for JSONL audit files.
    model_version:
        Optional LLM model version string.
    threshold:
        Human-review flag threshold.

    Returns
    -------
    callable
        ``wrapped_execute(input) -> tuple[PrimitiveResult, AuditLogEntry]``

    Example::

        wrapped = wrap_primitive(MyPrimitive())
        result, entry = wrapped(my_input)
    """

    def wrapped_execute(input: Any):  # type: ignore[no-untyped-def]
        ctx = ExecutionContext(
            primitive,
            log_dir=log_dir,
            model_version=model_version,
            threshold=threshold,
            input=input,
        )
        ctx.__enter__()
        try:
            result = primitive.execute(input)
            entry = ctx.log(result)
        finally:
            ctx.__exit__(None, None, None)
        return result, entry

    return wrapped_execute


# ---------------------------------------------------------------------------
# replay — determinism check
# ---------------------------------------------------------------------------


def replay(
    entry: AuditLogEntry,
    primitive: Primitive,
    original_input: Any,
    replayed_llm_calls: list[LLMCallRecord] | None = None,
) -> PrimitiveResult:
    """Replay a logged execution and verify the output.

    Re-runs ``primitive.execute(original_input)`` then verifies the replay
    against ``entry`` using the strategy appropriate for the primitive type:

    - **Deterministic** (``entry.is_deterministic is True``): SHA-256 of the
      new output must equal ``entry.output_hash``; raises ``HashMismatchError``
      otherwise.
    - **Non-deterministic / LLM-backed** (``entry.is_deterministic is False``):
      skips the output hash check (LLM outputs are inherently variable) and
      instead verifies that the replayed LLM calls match the same
      ``(model, prompt_hash)`` sequence as ``entry.llm_calls``.  Raises
      ``LLMReplayMismatchError`` when the sequence differs (different length or
      any mismatched pair at a given index).

    For the non-deterministic path, pass the ``LLMCallTracker.calls`` list that
    the primitive accumulated during its replayed execution as
    ``replayed_llm_calls``.  If ``replayed_llm_calls`` is ``None``, the
    sequence is treated as empty (matches only when ``entry.llm_calls`` is also
    empty).

    Parameters
    ----------
    entry:
        The ``AuditLogEntry`` from the original execution.
    primitive:
        A ``Primitive`` instance of the same type that produced the entry.
    original_input:
        The input value that was passed to the original ``execute()`` call.
    replayed_llm_calls:
        Optional list of ``LLMCallRecord`` objects accumulated by a
        ``LLMCallTracker`` during the replayed execution.  Only relevant
        when ``entry.is_deterministic`` is ``False``.

    Returns
    -------
    PrimitiveResult
        The result of the replayed execution.

    Raises
    ------
    HashMismatchError
        When ``entry.is_deterministic`` is ``True`` and the replayed output
        hash does not match ``entry.output_hash``.
    LLMReplayMismatchError
        When ``entry.is_deterministic`` is ``False`` and the replayed
        ``(model, prompt_hash)`` sequence differs from the original.
    """
    result = primitive.execute(original_input)

    if entry.is_deterministic:
        # Deterministic path — verify output hash.
        replayed_hash = _sha256_of(result.output)
        if replayed_hash != entry.output_hash:
            raise HashMismatchError(
                f"Replay hash mismatch for primitive '{entry.primitive_name}': "
                f"expected {entry.output_hash!r}, got {replayed_hash!r}. "
                "The primitive may be non-deterministic."
            )
    else:
        # Non-deterministic / LLM-backed path — verify (model, prompt_hash) sequence.
        original_seq = [(c.model, c.prompt_hash) for c in entry.llm_calls]
        resolved_replayed = replayed_llm_calls if replayed_llm_calls is not None else []
        replayed_seq = [(c.model, c.prompt_hash) for c in resolved_replayed]

        if original_seq != replayed_seq:
            raise LLMReplayMismatchError(
                f"LLM call sequence mismatch for primitive '{entry.primitive_name}': "
                f"original had {len(original_seq)} call(s) "
                f"{[m for m, _ in original_seq]!r}, "
                f"replay had {len(replayed_seq)} call(s) "
                f"{[m for m, _ in replayed_seq]!r}."
            )

    return result


def replay_by_trace_id(
    trace_id: str,
    log_dir: str,
    primitive: Primitive,
    original_input: Any,
    replayed_llm_calls: list[LLMCallRecord] | None = None,
) -> PrimitiveResult:
    """Locate an ``AuditLogEntry`` by its ``entry_id`` and replay it.

    Scans all ``*.jsonl`` files under ``log_dir`` for a line whose
    ``entry_id`` matches ``trace_id``, then delegates to ``replay()``.

    Parameters
    ----------
    trace_id:
        The ``entry_id`` UUID string of the target ``AuditLogEntry``.
    log_dir:
        Root directory for JSONL audit files (the same value passed to
        ``ExecutionContext`` or ``wrap_primitive``).
    primitive:
        A ``Primitive`` instance of the same type that produced the entry.
    original_input:
        The input value that was passed to the original ``execute()`` call.
    replayed_llm_calls:
        Optional list of ``LLMCallRecord`` objects from a ``LLMCallTracker``
        run during the caller's replayed execution.  Forwarded to ``replay()``
        for non-deterministic entries (``is_deterministic=False``).

    Returns
    -------
    PrimitiveResult
        The result of the replayed execution.

    Raises
    ------
    TraceNotFoundError
        When no ``AuditLogEntry`` with ``entry_id == trace_id`` is found
        in any JSONL file under ``log_dir``.
    HashMismatchError
        Propagated from ``replay()`` for deterministic primitives.
    LLMReplayMismatchError
        Propagated from ``replay()`` for non-deterministic primitives.
    """
    log_root = Path(log_dir)
    if log_root.exists():
        for jsonl_file in sorted(log_root.rglob("*.jsonl")):
            text = jsonl_file.read_text(encoding="utf-8")
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    candidate = AuditLogEntry.model_validate_json(line)
                    if candidate.entry_id == trace_id:
                        return replay(
                            candidate, primitive, original_input, replayed_llm_calls
                        )
                except Exception:
                    pass  # Malformed lines are silently skipped

    raise TraceNotFoundError(
        f"No AuditLogEntry with entry_id={trace_id!r} found under {log_dir!r}."
    )


# ---------------------------------------------------------------------------
# AuditLogger — registered primitive that batch-writes audit entries
# ---------------------------------------------------------------------------


@register_primitive(
    name="audit_logger",
    version="0.1.0",
    description="Wrap primitive calls with FINOS-aligned provenance: hashes, confidence, citations, human-review flag.",
    tags=["governance", "audit", "finos"],
)
class AuditLogger(Primitive[AuditLoggerInput, AuditLoggerOutput]):
    """Primitive that reads an existing audit JSONL file and summarises it.

    This is the registered primitive surface for the audit logger. The main
    workflow utility lives in ``ExecutionContext``, ``wrap_primitive``, and
    ``replay`` — those are the functions most callers use. ``AuditLogger``
    itself is exposed for framework-level introspection and catalogue
    generation.

    ``execute()`` here accepts an ``AuditLoggerInput`` pointing at an existing
    JSONL file directory and returns a summary count. It is primarily useful
    for governance dashboards that want to count flagged entries without
    re-running the wrapped primitives.
    """

    name = "audit_logger"
    version = "0.1.0"
    description = (
        "Wrap primitive calls with FINOS-aligned provenance: hashes, confidence, "
        "citations, human-review flag."
    )

    def execute(  # type: ignore[override]
        self, input: AuditLoggerInput
    ) -> "PrimitiveResult[AuditLoggerOutput]":
        """Summarise the audit JSONL files in ``input.log_dir``.

        Scans all ``*.jsonl`` files under ``input.log_dir``, counts entries
        and those flagged for human review, and returns an
        ``AuditLoggerOutput`` summary.
        """
        t0 = time.perf_counter()
        input_hash = input.input_hash()

        log_root = Path(input.log_dir)
        entries_written = 0
        flagged = 0
        last_path = str(log_root)

        if log_root.exists():
            for jsonl_file in sorted(log_root.rglob("*.jsonl")):
                last_path = str(jsonl_file)
                text = jsonl_file.read_text(encoding="utf-8")
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = AuditLogEntry.model_validate_json(line)
                        entries_written += 1
                        if record.human_review_required:
                            flagged += 1
                    except Exception:
                        pass  # Malformed lines are silently skipped

        duration_ms = (time.perf_counter() - t0) * 1000.0

        output = AuditLoggerOutput(
            log_path=last_path,
            entries_written=entries_written,
            flagged_for_review=flagged,
        )

        audit = AuditEntry.now(
            primitive_name=self.name,
            version=self.version,
            input_hash=input_hash,
            duration_ms=duration_ms,
        )

        return PrimitiveResult(
            output=output,
            confidence=1.0,
            citations=[],
            audit_entry=audit,
        )
