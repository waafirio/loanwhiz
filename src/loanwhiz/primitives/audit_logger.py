"""Audit logger primitive for the LoanWhiz SF agent framework.

Wraps every primitive call with a full provenance record following FINOS AI
Governance Framework patterns:

- SHA-256 hash of the serialised input and output
- Confidence score and source citations
- UTC execution timestamp and wall-clock duration
- Model version (populated when an LLM was used)
- Human review flag (fires when confidence < configurable threshold)
- A replay command string sufficient to reproduce the call

Usage — context manager::

    from loanwhiz.primitives.audit_logger import ExecutionContext

    primitive = MyPrimitive()
    with ExecutionContext(primitive, log_dir="/tmp/loanwhiz_audit") as ctx:
        result = primitive.execute(my_input)
        entry = ctx.log(result)

Usage — wrap_primitive::

    wrapped = wrap_primitive(primitive)
    result, entry = wrapped(my_input)

Usage — replay::

    result = replay(entry, primitive, original_input)
    # raises HashMismatchError when the output hash doesn't match
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

    def log(self, result: PrimitiveResult) -> AuditLogEntry:
        """Build, persist, and return the ``AuditLogEntry`` for this execution.

        Must be called after ``primitive.execute()`` returns and while the
        context manager is still active (i.e. inside the ``with`` block).

        Parameters
        ----------
        result:
            The ``PrimitiveResult`` returned by ``primitive.execute()``.

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
) -> PrimitiveResult:
    """Replay a logged execution and verify the output hash matches.

    Re-runs ``primitive.execute(original_input)`` and compares the SHA-256
    of the new output to ``entry.output_hash``. For deterministic (rule-based)
    primitives the hashes must match. For LLM-backed primitives they may
    differ — the caller decides whether to treat a mismatch as an error.

    Parameters
    ----------
    entry:
        The ``AuditLogEntry`` from the original execution.
    primitive:
        A ``Primitive`` instance of the same type that produced the entry.
    original_input:
        The input value that was passed to the original ``execute()`` call.

    Returns
    -------
    PrimitiveResult
        The result of the replayed execution.

    Raises
    ------
    HashMismatchError
        When the replayed output hash does not match ``entry.output_hash``.
    """
    result = primitive.execute(original_input)
    replayed_hash = _sha256_of(result.output)
    if replayed_hash != entry.output_hash:
        raise HashMismatchError(
            f"Replay hash mismatch for primitive '{entry.primitive_name}': "
            f"expected {entry.output_hash!r}, got {replayed_hash!r}. "
            "The primitive may be non-deterministic."
        )
    return result


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
        from loanwhiz.primitives.base import AuditEntry, PrimitiveResult

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

        from loanwhiz.primitives.base import Citation as _Citation

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
