"""Tests for loanwhiz.primitives.audit_logger.

Covers:
1. ExecutionContext wraps a mock primitive and produces a correct AuditLogEntry.
2. human_review_required flags below threshold=0.7 and does not flag at/above.
3. AuditLog JSONL round-trip (write → read → parse → equal entries).
4. replay() succeeds for a deterministic primitive (hash matches).
5. replay() raises HashMismatchError for a non-deterministic output.
"""

import json
import tempfile
import time
from pathlib import Path

import pytest
from pydantic import BaseModel

from loanwhiz.primitives.audit_logger import (
    AuditLog,
    AuditLogEntry,
    AuditLoggerInput,
    ExecutionContext,
    HashMismatchError,
    LLMCallRecord,
    LLMCallTracker,
    LLMReplayMismatchError,
    TraceNotFoundError,
    replay,
    replay_by_trace_id,
    wrap_primitive,
)
from loanwhiz.primitives.base import (
    AuditEntry,
    BaseInput,
    Citation,
    Primitive,
    PrimitiveResult,
)


# ---------------------------------------------------------------------------
# Shared helpers — a simple deterministic primitive and a non-deterministic one
# ---------------------------------------------------------------------------


class EchoInput(BaseInput):
    """Minimal input for the echo test primitive."""

    message: str


class EchoOutput(BaseModel):
    """Minimal output for the echo test primitive."""

    echoed: str


class EchoPrimitive(Primitive[EchoInput, EchoOutput]):
    """Deterministic primitive that echoes its input."""

    name = "echo_for_audit_test"
    version = "0.1.0"
    description = "Echo primitive used in audit logger tests."

    def execute(self, input: EchoInput) -> PrimitiveResult[EchoOutput]:
        t0 = time.perf_counter()
        output = EchoOutput(echoed=input.message)
        duration_ms = (time.perf_counter() - t0) * 1000.0
        return PrimitiveResult(
            output=output,
            confidence=0.9,
            citations=[Citation(document="test", page_or_row=None, excerpt=input.message)],
            audit_entry=AuditEntry.now(
                primitive_name=self.name,
                version=self.version,
                input_hash=input.input_hash(),
                duration_ms=duration_ms,
            ),
        )


class LowConfidencePrimitive(Primitive[EchoInput, EchoOutput]):
    """Primitive that always returns a low confidence score (0.5)."""

    name = "low_confidence_for_audit_test"
    version = "0.1.0"
    description = "Low-confidence primitive used in audit logger tests."

    def __init__(self, confidence: float = 0.5) -> None:
        self._confidence = confidence

    def execute(self, input: EchoInput) -> PrimitiveResult[EchoOutput]:
        t0 = time.perf_counter()
        output = EchoOutput(echoed=input.message)
        duration_ms = (time.perf_counter() - t0) * 1000.0
        return PrimitiveResult(
            output=output,
            confidence=self._confidence,
            citations=[],
            audit_entry=AuditEntry.now(
                primitive_name=self.name,
                version=self.version,
                input_hash=input.input_hash(),
                duration_ms=duration_ms,
            ),
        )


class NonDeterministicPrimitive(Primitive[EchoInput, EchoOutput]):
    """Primitive that returns a different output on each call (timestamp-based)."""

    name = "non_det_for_audit_test"
    version = "0.1.0"
    description = "Non-deterministic primitive used in audit logger tests."

    def execute(self, input: EchoInput) -> PrimitiveResult[EchoOutput]:
        t0 = time.perf_counter()
        # Each call produces a unique output by appending the current time.
        unique_suffix = str(time.time_ns())
        output = EchoOutput(echoed=f"{input.message}-{unique_suffix}")
        duration_ms = (time.perf_counter() - t0) * 1000.0
        return PrimitiveResult(
            output=output,
            confidence=0.8,
            citations=[],
            audit_entry=AuditEntry.now(
                primitive_name=self.name,
                version=self.version,
                input_hash=input.input_hash(),
                duration_ms=duration_ms,
            ),
        )


class LLMBackedPrimitive(Primitive[EchoInput, EchoOutput]):
    """Simulated LLM-backed primitive that records calls via LLMCallTracker.

    The primitive stores its last tracker so tests can inspect the recorded calls.
    """

    name = "llm_backed_for_audit_test"
    version = "0.1.0"
    description = "Simulated LLM-backed primitive for audit logger tests."

    def __init__(self, model: str = "gemini-2.5-pro", temperature: float = 0.0) -> None:
        self._model = model
        self._temperature = temperature
        self.last_tracker: LLMCallTracker | None = None

    def execute(self, input: EchoInput) -> PrimitiveResult[EchoOutput]:
        t0 = time.perf_counter()
        tracker = LLMCallTracker()
        with tracker:
            prompt = f"Echo this: {input.message}"
            response = f"Echoed: {input.message}"  # deterministic fake response
            tracker.record(
                model=self._model,
                prompt=prompt,
                response=response,
                temperature=self._temperature,
                input_tokens=len(prompt),
                output_tokens=len(response),
            )
        self.last_tracker = tracker
        output = EchoOutput(echoed=input.message)
        duration_ms = (time.perf_counter() - t0) * 1000.0
        return PrimitiveResult(
            output=output,
            confidence=0.85,
            citations=[],
            audit_entry=AuditEntry.now(
                primitive_name=self.name,
                version=self.version,
                input_hash=input.input_hash(),
                duration_ms=duration_ms,
            ),
        )


class ChangingPromptPrimitive(Primitive[EchoInput, EchoOutput]):
    """LLM-backed primitive whose prompt changes on every call (timestamp-based).

    Used to test that replay() detects a prompt_hash sequence change.
    """

    name = "changing_prompt_for_audit_test"
    version = "0.1.0"
    description = "Changing-prompt primitive for audit logger replay tests."

    def __init__(self) -> None:
        self.last_tracker: LLMCallTracker | None = None

    def execute(self, input: EchoInput) -> PrimitiveResult[EchoOutput]:
        t0 = time.perf_counter()
        tracker = LLMCallTracker()
        with tracker:
            # Each call has a unique prompt (timestamp-based)
            unique = str(time.time_ns())
            prompt = f"Echo this (ts={unique}): {input.message}"
            response = "some response"
            tracker.record(model="gemini-2.5-pro", prompt=prompt, response=response)
        self.last_tracker = tracker
        output = EchoOutput(echoed=input.message)
        duration_ms = (time.perf_counter() - t0) * 1000.0
        return PrimitiveResult(
            output=output,
            confidence=0.7,
            citations=[],
            audit_entry=AuditEntry.now(
                primitive_name=self.name,
                version=self.version,
                input_hash=input.input_hash(),
                duration_ms=duration_ms,
            ),
        )


# ---------------------------------------------------------------------------
# 1. ExecutionContext wraps a mock primitive and logs correctly
# ---------------------------------------------------------------------------


class TestExecutionContext:
    def test_log_returns_audit_log_entry(self, tmp_path):
        """ExecutionContext.log() returns an AuditLogEntry instance."""
        primitive = EchoPrimitive()
        inp = EchoInput(message="hello audit")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)
        assert isinstance(entry, AuditLogEntry)

    def test_entry_has_correct_primitive_name(self, tmp_path):
        primitive = EchoPrimitive()
        inp = EchoInput(message="test name")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)
        assert entry.primitive_name == "echo_for_audit_test"

    def test_entry_has_correct_primitive_version(self, tmp_path):
        primitive = EchoPrimitive()
        inp = EchoInput(message="test version")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)
        assert entry.primitive_version == "0.1.0"

    def test_entry_input_hash_is_64_char_hex(self, tmp_path):
        """input_hash must be a 64-character hex string (SHA-256)."""
        primitive = EchoPrimitive()
        inp = EchoInput(message="hash test")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)
        assert len(entry.input_hash) == 64
        assert all(c in "0123456789abcdef" for c in entry.input_hash)

    def test_entry_output_hash_is_64_char_hex(self, tmp_path):
        """output_hash must be a 64-character hex string (SHA-256)."""
        primitive = EchoPrimitive()
        inp = EchoInput(message="output hash test")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)
        assert len(entry.output_hash) == 64
        assert all(c in "0123456789abcdef" for c in entry.output_hash)

    def test_entry_input_hash_is_deterministic_for_same_input(self, tmp_path):
        """Same input always produces the same input_hash."""
        primitive = EchoPrimitive()
        inp = EchoInput(message="deterministic")
        entries = []
        for _ in range(2):
            with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
                result = primitive.execute(inp)
                entries.append(ctx.log(result))
        assert entries[0].input_hash == entries[1].input_hash

    def test_entry_duration_ms_is_non_negative(self, tmp_path):
        primitive = EchoPrimitive()
        inp = EchoInput(message="duration test")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)
        assert entry.duration_ms >= 0.0

    def test_entry_executed_at_is_iso8601(self, tmp_path):
        """executed_at must be a parseable ISO 8601 UTC string."""
        from datetime import datetime

        primitive = EchoPrimitive()
        inp = EchoInput(message="timestamp test")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)
        dt = datetime.fromisoformat(entry.executed_at)
        assert dt is not None

    def test_entry_id_is_valid_uuid4(self, tmp_path):
        """entry_id must be a valid UUID4."""
        import re

        uuid4_pattern = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
            re.IGNORECASE,
        )
        primitive = EchoPrimitive()
        inp = EchoInput(message="uuid test")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)
        assert uuid4_pattern.match(entry.entry_id), f"Not a UUID4: {entry.entry_id}"

    def test_entry_citations_match_result(self, tmp_path):
        """Citations are propagated from the PrimitiveResult."""
        primitive = EchoPrimitive()
        inp = EchoInput(message="cite test")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)
        assert len(entry.citations) == 1
        assert entry.citations[0].excerpt == "cite test"

    def test_jsonl_file_is_written(self, tmp_path):
        """ExecutionContext writes a JSONL file to {log_dir}/{primitive_name}/{date}.jsonl."""
        from datetime import datetime, timezone

        primitive = EchoPrimitive()
        inp = EchoInput(message="file write test")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            ctx.log(result)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        expected_file = tmp_path / "echo_for_audit_test" / f"{today}.jsonl"
        assert expected_file.exists(), f"Expected JSONL file not found: {expected_file}"

    def test_jsonl_file_contains_valid_json(self, tmp_path):
        """Each line in the JSONL file is valid JSON."""
        from datetime import datetime, timezone

        primitive = EchoPrimitive()
        inp = EchoInput(message="json validate test")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            ctx.log(result)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        jsonl_file = tmp_path / "echo_for_audit_test" / f"{today}.jsonl"
        for line in jsonl_file.read_text().splitlines():
            if line.strip():
                record = json.loads(line)
                assert "primitive_name" in record

    def test_model_version_stored_when_provided(self, tmp_path):
        """model_version is stored in the entry when provided."""
        primitive = EchoPrimitive()
        inp = EchoInput(message="model version test")
        with ExecutionContext(
            primitive, log_dir=str(tmp_path), model_version="gemini-2.0-flash", input=inp
        ) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)
        assert entry.model_version == "gemini-2.0-flash"

    def test_model_version_is_none_by_default(self, tmp_path):
        """model_version defaults to None."""
        primitive = EchoPrimitive()
        inp = EchoInput(message="no model version")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)
        assert entry.model_version is None

    def test_replay_command_is_non_empty_string(self, tmp_path):
        """replay_command is a non-empty string."""
        primitive = EchoPrimitive()
        inp = EchoInput(message="replay cmd test")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)
        assert isinstance(entry.replay_command, str)
        assert len(entry.replay_command) > 0

    def test_multiple_executions_append_to_same_file(self, tmp_path):
        """Multiple calls append entries to the same daily JSONL file."""
        from datetime import datetime, timezone

        primitive = EchoPrimitive()
        for msg in ("first", "second", "third"):
            inp = EchoInput(message=msg)
            with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
                result = primitive.execute(inp)
                ctx.log(result)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        jsonl_file = tmp_path / "echo_for_audit_test" / f"{today}.jsonl"
        lines = [l for l in jsonl_file.read_text().splitlines() if l.strip()]
        assert len(lines) == 3


# ---------------------------------------------------------------------------
# 2. human_review_required threshold behaviour
# ---------------------------------------------------------------------------


class TestHumanReviewFlag:
    def test_below_threshold_is_flagged(self, tmp_path):
        """confidence=0.5 < threshold=0.7 → human_review_required=True."""
        primitive = LowConfidencePrimitive(confidence=0.5)
        inp = EchoInput(message="low confidence")
        with ExecutionContext(primitive, log_dir=str(tmp_path), threshold=0.7, input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)
        assert entry.human_review_required is True

    def test_at_threshold_is_not_flagged(self, tmp_path):
        """confidence=0.7 == threshold=0.7 → human_review_required=False (not strictly less)."""
        primitive = LowConfidencePrimitive(confidence=0.7)
        inp = EchoInput(message="at threshold")
        with ExecutionContext(primitive, log_dir=str(tmp_path), threshold=0.7, input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)
        assert entry.human_review_required is False

    def test_above_threshold_is_not_flagged(self, tmp_path):
        """confidence=0.9 > threshold=0.7 → human_review_required=False."""
        primitive = EchoPrimitive()  # confidence=0.9
        inp = EchoInput(message="high confidence")
        with ExecutionContext(primitive, log_dir=str(tmp_path), threshold=0.7, input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)
        assert entry.human_review_required is False

    def test_confidence_zero_is_flagged(self, tmp_path):
        """confidence=0.0 < any positive threshold → human_review_required=True."""
        primitive = LowConfidencePrimitive(confidence=0.0)
        inp = EchoInput(message="zero confidence")
        with ExecutionContext(primitive, log_dir=str(tmp_path), threshold=0.7, input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)
        assert entry.human_review_required is True

    def test_confidence_one_is_not_flagged(self, tmp_path):
        """confidence=1.0 → human_review_required=False regardless of threshold."""
        primitive = LowConfidencePrimitive(confidence=1.0)
        inp = EchoInput(message="full confidence")
        with ExecutionContext(primitive, log_dir=str(tmp_path), threshold=0.7, input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)
        assert entry.human_review_required is False

    def test_just_below_threshold_is_flagged(self, tmp_path):
        """confidence=0.6999 < 0.7 → human_review_required=True."""
        primitive = LowConfidencePrimitive(confidence=0.6999)
        inp = EchoInput(message="just below")
        with ExecutionContext(primitive, log_dir=str(tmp_path), threshold=0.7, input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)
        assert entry.human_review_required is True

    def test_custom_threshold_respected(self, tmp_path):
        """A custom threshold of 0.5 flags confidence=0.4 but not confidence=0.5."""
        primitive_low = LowConfidencePrimitive(confidence=0.4)
        primitive_border = LowConfidencePrimitive(confidence=0.5)
        inp = EchoInput(message="custom threshold")

        with ExecutionContext(primitive_low, log_dir=str(tmp_path), threshold=0.5, input=inp) as ctx:
            result = primitive_low.execute(inp)
            entry_low = ctx.log(result)

        with ExecutionContext(
            primitive_border, log_dir=str(tmp_path), threshold=0.5, input=inp
        ) as ctx:
            result = primitive_border.execute(inp)
            entry_border = ctx.log(result)

        assert entry_low.human_review_required is True
        assert entry_border.human_review_required is False


# ---------------------------------------------------------------------------
# 3. JSONL round-trip
# ---------------------------------------------------------------------------


class TestJsonlRoundTrip:
    def _make_entry(self, msg: str = "test", confidence: float = 0.9) -> AuditLogEntry:
        """Build an AuditLogEntry without needing a primitive execution."""
        return AuditLogEntry(
            entry_id="12345678-1234-4234-a234-123456789012",
            primitive_name="test_primitive",
            primitive_version="0.1.0",
            executed_at="2026-06-03T12:00:00+00:00",
            duration_ms=5.0,
            input_hash="a" * 64,
            output_hash="b" * 64,
            confidence=confidence,
            citations=[Citation(document="doc.pdf", page_or_row=1, excerpt=msg)],
            human_review_required=confidence < 0.7,
            model_version=None,
            replay_command='python -c "..."',
        )

    def test_to_jsonl_produces_one_line_per_entry(self):
        """AuditLog.to_jsonl() outputs one line per entry."""
        log = AuditLog(entries=[self._make_entry("a"), self._make_entry("b")])
        lines = [l for l in log.to_jsonl().splitlines() if l.strip()]
        assert len(lines) == 2

    def test_from_jsonl_parses_all_entries(self):
        """AuditLog.from_jsonl() recovers all entries."""
        original = AuditLog(entries=[self._make_entry("x"), self._make_entry("y")])
        jsonl = original.to_jsonl()
        recovered = AuditLog.from_jsonl(jsonl)
        assert len(recovered.entries) == 2

    def test_round_trip_preserves_entry_id(self):
        """Round-trip preserves entry_id."""
        entry = self._make_entry()
        log = AuditLog(entries=[entry])
        recovered = AuditLog.from_jsonl(log.to_jsonl())
        assert recovered.entries[0].entry_id == entry.entry_id

    def test_round_trip_preserves_primitive_name(self):
        entry = self._make_entry()
        log = AuditLog(entries=[entry])
        recovered = AuditLog.from_jsonl(log.to_jsonl())
        assert recovered.entries[0].primitive_name == "test_primitive"

    def test_round_trip_preserves_confidence(self):
        entry = self._make_entry(confidence=0.42)
        log = AuditLog(entries=[entry])
        recovered = AuditLog.from_jsonl(log.to_jsonl())
        assert abs(recovered.entries[0].confidence - 0.42) < 1e-9

    def test_round_trip_preserves_human_review_flag(self):
        entry_low = self._make_entry(confidence=0.5)
        entry_high = self._make_entry(confidence=0.9)
        log = AuditLog(entries=[entry_low, entry_high])
        recovered = AuditLog.from_jsonl(log.to_jsonl())
        assert recovered.entries[0].human_review_required is True
        assert recovered.entries[1].human_review_required is False

    def test_round_trip_preserves_citations(self):
        entry = self._make_entry(msg="cite round-trip")
        log = AuditLog(entries=[entry])
        recovered = AuditLog.from_jsonl(log.to_jsonl())
        assert len(recovered.entries[0].citations) == 1
        assert recovered.entries[0].citations[0].excerpt == "cite round-trip"

    def test_from_jsonl_skips_empty_lines(self):
        """from_jsonl() handles trailing newlines and empty lines gracefully."""
        entry = self._make_entry()
        jsonl = AuditLog(entries=[entry]).to_jsonl()
        # Add leading/trailing/internal blank lines
        padded = "\n\n" + jsonl + "\n\n"
        recovered = AuditLog.from_jsonl(padded)
        assert len(recovered.entries) == 1

    def test_from_jsonl_empty_string_gives_empty_log(self):
        """from_jsonl('') returns an AuditLog with no entries."""
        log = AuditLog.from_jsonl("")
        assert log.entries == []

    def test_to_jsonl_empty_log_gives_empty_string(self):
        """to_jsonl() on an empty AuditLog returns an empty string."""
        log = AuditLog(entries=[])
        assert log.to_jsonl() == ""

    def test_round_trip_via_file(self, tmp_path):
        """Write JSONL to a file, read it back, parse — entries match."""
        primitive = EchoPrimitive()
        messages = ["alpha", "beta", "gamma"]
        entries = []
        for msg in messages:
            inp = EchoInput(message=msg)
            with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
                result = primitive.execute(inp)
                entries.append(ctx.log(result))

        # Locate the file
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        jsonl_file = tmp_path / "echo_for_audit_test" / f"{today}.jsonl"
        text = jsonl_file.read_text(encoding="utf-8")
        recovered_log = AuditLog.from_jsonl(text)

        assert len(recovered_log.entries) == 3
        for orig, recov in zip(entries, recovered_log.entries):
            assert orig.entry_id == recov.entry_id
            assert orig.input_hash == recov.input_hash
            assert orig.output_hash == recov.output_hash


# ---------------------------------------------------------------------------
# 4. replay() verifies output hash for a deterministic primitive
# ---------------------------------------------------------------------------


class TestReplayDeterministic:
    def test_replay_succeeds_for_deterministic_primitive(self, tmp_path):
        """replay() returns a PrimitiveResult without raising for deterministic output."""
        primitive = EchoPrimitive()
        inp = EchoInput(message="deterministic replay")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)

        replayed = replay(entry, primitive, inp)
        assert isinstance(replayed, PrimitiveResult)

    def test_replay_returns_same_output(self, tmp_path):
        """The replayed result has the same output value as the original."""
        primitive = EchoPrimitive()
        inp = EchoInput(message="same output")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)

        replayed = replay(entry, primitive, inp)
        assert replayed.output.echoed == "same output"

    def test_replay_output_hash_matches(self, tmp_path):
        """After replay, the output hash of the replayed result equals entry.output_hash."""
        from loanwhiz.primitives.audit_logger import _sha256_of

        primitive = EchoPrimitive()
        inp = EchoInput(message="hash match replay")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)

        replayed = replay(entry, primitive, inp)
        assert _sha256_of(replayed.output) == entry.output_hash

    def test_replay_does_not_write_additional_jsonl(self, tmp_path):
        """replay() does not write to the audit log — it only checks the hash."""
        from datetime import datetime, timezone

        primitive = EchoPrimitive()
        inp = EchoInput(message="no extra write")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        jsonl_file = tmp_path / "echo_for_audit_test" / f"{today}.jsonl"
        line_count_before = len(jsonl_file.read_text().splitlines())

        replay(entry, primitive, inp)

        line_count_after = len(jsonl_file.read_text().splitlines())
        assert line_count_after == line_count_before


# ---------------------------------------------------------------------------
# 5. replay() detects hash mismatch for non-deterministic output
# ---------------------------------------------------------------------------


class TestReplayNonDeterministic:
    def test_replay_raises_hash_mismatch_error(self, tmp_path):
        """replay() raises HashMismatchError when the output hash differs."""
        primitive = NonDeterministicPrimitive()
        inp = EchoInput(message="non-det")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)

        # The second call produces a different output (unique timestamp suffix).
        with pytest.raises(HashMismatchError):
            replay(entry, primitive, inp)

    def test_hash_mismatch_error_message_contains_primitive_name(self, tmp_path):
        """HashMismatchError message names the primitive."""
        primitive = NonDeterministicPrimitive()
        inp = EchoInput(message="name in error")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)

        with pytest.raises(HashMismatchError, match="non_det_for_audit_test"):
            replay(entry, primitive, inp)

    def test_hash_mismatch_error_is_not_raised_when_hashes_match(self, tmp_path):
        """No error is raised when replaying a deterministic primitive."""
        primitive = EchoPrimitive()
        inp = EchoInput(message="no error expected")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)

        # Should not raise.
        replay(entry, primitive, inp)


# ---------------------------------------------------------------------------
# 6. wrap_primitive convenience function
# ---------------------------------------------------------------------------


class TestWrapPrimitive:
    def test_wrap_primitive_returns_result_and_entry(self, tmp_path):
        """wrap_primitive returns (PrimitiveResult, AuditLogEntry) tuple."""
        primitive = EchoPrimitive()
        wrapped = wrap_primitive(primitive, log_dir=str(tmp_path))
        result, entry = wrapped(EchoInput(message="wrap test"))
        assert isinstance(result, PrimitiveResult)
        assert isinstance(entry, AuditLogEntry)

    def test_wrap_primitive_result_output_is_correct(self, tmp_path):
        """The result from wrap_primitive has the expected output."""
        primitive = EchoPrimitive()
        wrapped = wrap_primitive(primitive, log_dir=str(tmp_path))
        result, _ = wrapped(EchoInput(message="hello from wrap"))
        assert result.output.echoed == "hello from wrap"

    def test_wrap_primitive_entry_human_review_for_low_confidence(self, tmp_path):
        """wrap_primitive correctly flags low-confidence primitives."""
        primitive = LowConfidencePrimitive(confidence=0.5)
        wrapped = wrap_primitive(primitive, log_dir=str(tmp_path), threshold=0.7)
        _, entry = wrapped(EchoInput(message="low conf wrap"))
        assert entry.human_review_required is True


# ---------------------------------------------------------------------------
# 7. LLMCallTracker records calls correctly
# ---------------------------------------------------------------------------


class TestLLMCallTracker:
    def test_tracker_starts_empty(self):
        """A fresh LLMCallTracker has no calls."""
        tracker = LLMCallTracker()
        assert tracker.calls == []

    def test_tracker_used_as_context_manager(self):
        """LLMCallTracker can be used as a context manager."""
        with LLMCallTracker() as tracker:
            assert isinstance(tracker, LLMCallTracker)

    def test_record_single_call(self):
        """record() appends one LLMCallRecord to calls."""
        with LLMCallTracker() as tracker:
            tracker.record(model="gemini-2.5-pro", prompt="hello", response="world")
        assert len(tracker.calls) == 1

    def test_record_returns_llm_call_record(self):
        """record() returns the appended LLMCallRecord."""
        with LLMCallTracker() as tracker:
            result = tracker.record(model="gemini-2.5-pro", prompt="p", response="r")
        assert isinstance(result, LLMCallRecord)

    def test_call_index_is_zero_based(self):
        """The first call gets call_index=0, second gets call_index=1."""
        with LLMCallTracker() as tracker:
            tracker.record(model="m", prompt="p1", response="r1")
            tracker.record(model="m", prompt="p2", response="r2")
        assert tracker.calls[0].call_index == 0
        assert tracker.calls[1].call_index == 1

    def test_prompt_hash_is_sha256_of_prompt(self):
        """prompt_hash is the SHA-256 of the prompt string."""
        import hashlib

        prompt = "What is the waterfall?"
        with LLMCallTracker() as tracker:
            tracker.record(model="gemini-2.5-pro", prompt=prompt, response="answer")
        expected = hashlib.sha256(prompt.encode()).hexdigest()
        assert tracker.calls[0].prompt_hash == expected

    def test_prompt_hash_is_64_hex_chars(self):
        """prompt_hash is a 64-character hex string (SHA-256)."""
        with LLMCallTracker() as tracker:
            tracker.record(model="m", prompt="some prompt", response="r")
        ph = tracker.calls[0].prompt_hash
        assert len(ph) == 64
        assert all(c in "0123456789abcdef" for c in ph)

    def test_prompt_preview_is_first_200_chars(self):
        """prompt_preview is the first 200 characters of the prompt."""
        long_prompt = "x" * 500
        with LLMCallTracker() as tracker:
            tracker.record(model="m", prompt=long_prompt, response="r")
        assert tracker.calls[0].prompt_preview == long_prompt[:200]
        assert len(tracker.calls[0].prompt_preview) == 200

    def test_prompt_preview_short_prompt_not_truncated(self):
        """Short prompts are not truncated."""
        short = "Hi"
        with LLMCallTracker() as tracker:
            tracker.record(model="m", prompt=short, response="r")
        assert tracker.calls[0].prompt_preview == "Hi"

    def test_response_preview_is_first_200_chars(self):
        """response_preview is the first 200 characters of the response."""
        long_response = "y" * 400
        with LLMCallTracker() as tracker:
            tracker.record(model="m", prompt="p", response=long_response)
        assert tracker.calls[0].response_preview == long_response[:200]

    def test_temperature_default_is_zero(self):
        """temperature defaults to 0.0."""
        with LLMCallTracker() as tracker:
            tracker.record(model="m", prompt="p", response="r")
        assert tracker.calls[0].temperature == 0.0

    def test_temperature_custom_value_stored(self):
        """Custom temperature is stored correctly."""
        with LLMCallTracker() as tracker:
            tracker.record(model="m", prompt="p", response="r", temperature=0.7)
        assert abs(tracker.calls[0].temperature - 0.7) < 1e-9

    def test_input_tokens_stored(self):
        """input_tokens is stored when provided."""
        with LLMCallTracker() as tracker:
            tracker.record(model="m", prompt="p", response="r", input_tokens=42)
        assert tracker.calls[0].input_tokens == 42

    def test_output_tokens_stored(self):
        """output_tokens is stored when provided."""
        with LLMCallTracker() as tracker:
            tracker.record(model="m", prompt="p", response="r", output_tokens=99)
        assert tracker.calls[0].output_tokens == 99

    def test_call_duration_ms_stored(self):
        """call_duration_ms is stored when provided."""
        with LLMCallTracker() as tracker:
            tracker.record(model="m", prompt="p", response="r", call_duration_ms=123.4)
        assert abs(tracker.calls[0].call_duration_ms - 123.4) < 0.01

    def test_optional_fields_default_to_none(self):
        """input_tokens, output_tokens, call_duration_ms default to None."""
        with LLMCallTracker() as tracker:
            tracker.record(model="m", prompt="p", response="r")
        rec = tracker.calls[0]
        assert rec.input_tokens is None
        assert rec.output_tokens is None
        assert rec.call_duration_ms is None

    def test_multi_call_sequence(self):
        """Multiple record() calls accumulate in order."""
        with LLMCallTracker() as tracker:
            for i in range(3):
                tracker.record(model="m", prompt=f"prompt {i}", response=f"resp {i}")
        assert len(tracker.calls) == 3
        for i, call in enumerate(tracker.calls):
            assert call.call_index == i
            assert call.prompt_preview == f"prompt {i}"

    def test_model_stored_correctly(self):
        """model identifier is stored verbatim."""
        with LLMCallTracker() as tracker:
            tracker.record(model="gemini-2.5-flash", prompt="p", response="r")
        assert tracker.calls[0].model == "gemini-2.5-flash"


# ---------------------------------------------------------------------------
# 8. AuditLogEntry llm_calls and is_deterministic field behaviour
# ---------------------------------------------------------------------------


class TestAuditLogEntryLLMFields:
    def _make_entry_with_llm_calls(self) -> AuditLogEntry:
        """Build an AuditLogEntry with two LLMCallRecord entries."""
        calls = [
            LLMCallRecord(
                call_index=0,
                model="gemini-2.5-pro",
                prompt_hash="a" * 64,
                prompt_preview="first prompt",
                response_preview="first response",
                temperature=0.0,
                input_tokens=10,
                output_tokens=20,
            ),
            LLMCallRecord(
                call_index=1,
                model="gemini-2.5-flash",
                prompt_hash="b" * 64,
                prompt_preview="second prompt",
                response_preview="second response",
                temperature=0.3,
            ),
        ]
        return AuditLogEntry(
            entry_id="12345678-1234-4234-a234-123456789012",
            primitive_name="test_llm_primitive",
            primitive_version="0.1.0",
            executed_at="2026-06-03T12:00:00+00:00",
            duration_ms=150.0,
            input_hash="c" * 64,
            output_hash="d" * 64,
            confidence=0.85,
            citations=[],
            human_review_required=False,
            llm_calls=calls,
            is_deterministic=False,
        )

    def test_llm_calls_default_is_empty_list(self):
        """AuditLogEntry.llm_calls defaults to an empty list."""
        entry = AuditLogEntry(
            entry_id="12345678-1234-4234-a234-123456789012",
            primitive_name="p",
            primitive_version="0.1.0",
            executed_at="2026-06-03T12:00:00+00:00",
            duration_ms=1.0,
            input_hash="a" * 64,
            output_hash="b" * 64,
            confidence=1.0,
            human_review_required=False,
        )
        assert entry.llm_calls == []

    def test_is_deterministic_default_is_true(self):
        """AuditLogEntry.is_deterministic defaults to True."""
        entry = AuditLogEntry(
            entry_id="12345678-1234-4234-a234-123456789012",
            primitive_name="p",
            primitive_version="0.1.0",
            executed_at="2026-06-03T12:00:00+00:00",
            duration_ms=1.0,
            input_hash="a" * 64,
            output_hash="b" * 64,
            confidence=1.0,
            human_review_required=False,
        )
        assert entry.is_deterministic is True

    def test_is_deterministic_false_stored(self):
        """is_deterministic=False is stored correctly."""
        entry = self._make_entry_with_llm_calls()
        assert entry.is_deterministic is False

    def test_llm_calls_count(self):
        """Two LLMCallRecord objects are stored."""
        entry = self._make_entry_with_llm_calls()
        assert len(entry.llm_calls) == 2

    def test_llm_call_fields_preserved(self):
        """LLMCallRecord fields are preserved correctly."""
        entry = self._make_entry_with_llm_calls()
        assert entry.llm_calls[0].model == "gemini-2.5-pro"
        assert entry.llm_calls[0].call_index == 0
        assert entry.llm_calls[1].model == "gemini-2.5-flash"
        assert entry.llm_calls[1].call_index == 1

    def test_llm_calls_jsonl_round_trip(self):
        """AuditLogEntry with llm_calls serialises and deserialises via JSONL."""
        entry = self._make_entry_with_llm_calls()
        log = AuditLog(entries=[entry])
        jsonl = log.to_jsonl()
        recovered = AuditLog.from_jsonl(jsonl)
        assert len(recovered.entries) == 1
        rec = recovered.entries[0]
        assert rec.is_deterministic is False
        assert len(rec.llm_calls) == 2
        assert rec.llm_calls[0].model == "gemini-2.5-pro"
        assert rec.llm_calls[0].prompt_hash == "a" * 64
        assert rec.llm_calls[1].model == "gemini-2.5-flash"
        assert rec.llm_calls[1].input_tokens is None

    def test_existing_entry_without_llm_calls_parses(self):
        """An AuditLogEntry without llm_calls/is_deterministic fields parses correctly.

        This is the backward-compatibility test: existing JSONL files written
        before this extension must still parse via Pydantic's default-fill.
        """
        # Build a minimal JSON line as it would have appeared before the extension.
        import json

        old_style = json.dumps({
            "entry_id": "12345678-1234-4234-a234-123456789012",
            "primitive_name": "old_primitive",
            "primitive_version": "0.1.0",
            "executed_at": "2026-06-03T12:00:00+00:00",
            "duration_ms": 5.0,
            "input_hash": "a" * 64,
            "output_hash": "b" * 64,
            "confidence": 0.9,
            "citations": [],
            "human_review_required": False,
            "model_version": None,
            "replay_command": None,
        })
        entry = AuditLogEntry.model_validate_json(old_style)
        assert entry.llm_calls == []
        assert entry.is_deterministic is True

    def test_execution_context_log_with_llm_calls(self, tmp_path):
        """ExecutionContext.log() accepts llm_calls and sets is_deterministic=False."""
        primitive = EchoPrimitive()
        inp = EchoInput(message="llm context test")
        tracker = LLMCallTracker()
        tracker.record(model="gemini-2.5-pro", prompt="test prompt", response="test resp")

        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result, llm_calls=tracker.calls)

        assert entry.is_deterministic is False
        assert len(entry.llm_calls) == 1
        assert entry.llm_calls[0].model == "gemini-2.5-pro"

    def test_execution_context_log_without_llm_calls_is_deterministic(self, tmp_path):
        """ExecutionContext.log() without llm_calls sets is_deterministic=True."""
        primitive = EchoPrimitive()
        inp = EchoInput(message="deterministic context test")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)
        assert entry.is_deterministic is True
        assert entry.llm_calls == []


# ---------------------------------------------------------------------------
# 9. replay() for non-deterministic (LLM-backed) primitives
# ---------------------------------------------------------------------------


class TestReplayNonDeterministicBranch:
    def _make_llm_entry(
        self,
        prompt_hashes: list[str],
        models: list[str] | None = None,
    ) -> AuditLogEntry:
        """Build a non-deterministic AuditLogEntry with the given prompt hash sequence."""
        if models is None:
            models = ["gemini-2.5-pro"] * len(prompt_hashes)
        calls = [
            LLMCallRecord(
                call_index=i,
                model=models[i],
                prompt_hash=prompt_hashes[i],
                prompt_preview="preview",
                response_preview="resp",
            )
            for i in range(len(prompt_hashes))
        ]
        return AuditLogEntry(
            entry_id="12345678-1234-4234-a234-123456789012",
            primitive_name="llm_backed_for_audit_test",
            primitive_version="0.1.0",
            executed_at="2026-06-03T12:00:00+00:00",
            duration_ms=200.0,
            input_hash="e" * 64,
            output_hash="f" * 64,
            confidence=0.85,
            citations=[],
            human_review_required=False,
            llm_calls=calls,
            is_deterministic=False,
        )

    def test_replay_non_deterministic_does_not_raise_hash_mismatch(self, tmp_path):
        """replay() with is_deterministic=False does not raise HashMismatchError."""
        primitive = LLMBackedPrimitive()
        inp = EchoInput(message="non-det replay")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            original_calls = list(primitive.last_tracker.calls)
            entry = ctx.log(result, llm_calls=original_calls)

        # Pass the same (model, prompt_hash) sequence → no error
        replayed = replay(entry, primitive, inp, replayed_llm_calls=original_calls)
        assert isinstance(replayed, PrimitiveResult)

    def test_replay_non_deterministic_returns_primitive_result(self, tmp_path):
        """replay() returns a PrimitiveResult for a non-deterministic primitive."""
        primitive = LLMBackedPrimitive()
        inp = EchoInput(message="result type")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            original_calls = list(primitive.last_tracker.calls)
            entry = ctx.log(result, llm_calls=original_calls)

        replayed = replay(entry, primitive, inp, replayed_llm_calls=original_calls)
        assert isinstance(replayed, PrimitiveResult)

    def test_replay_non_deterministic_raises_on_prompt_hash_change(self, tmp_path):
        """replay() raises LLMReplayMismatchError when prompt_hash sequence differs."""
        primitive = ChangingPromptPrimitive()
        inp = EchoInput(message="changing prompt")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            original_calls = list(primitive.last_tracker.calls)  # snapshot
            entry = ctx.log(result, llm_calls=original_calls)

        # Second call produces a different prompt (unique timestamp suffix) → mismatch
        primitive.execute(inp)  # new unique prompt → different prompt_hash
        with pytest.raises(LLMReplayMismatchError):
            replay(entry, primitive, inp, replayed_llm_calls=primitive.last_tracker.calls)

    def test_replay_non_deterministic_mismatch_error_message_contains_name(self, tmp_path):
        """LLMReplayMismatchError message names the primitive."""
        primitive = ChangingPromptPrimitive()
        inp = EchoInput(message="name in error")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            original_calls = list(primitive.last_tracker.calls)  # snapshot
            entry = ctx.log(result, llm_calls=original_calls)

        primitive.execute(inp)  # new unique prompt
        with pytest.raises(LLMReplayMismatchError, match="changing_prompt_for_audit_test"):
            replay(entry, primitive, inp, replayed_llm_calls=primitive.last_tracker.calls)

    def test_replay_deterministic_still_raises_hash_mismatch(self, tmp_path):
        """replay() with is_deterministic=True still raises HashMismatchError on mismatch."""
        primitive = NonDeterministicPrimitive()
        inp = EchoInput(message="det replay check")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)

        # is_deterministic=True (no llm_calls), output will differ → HashMismatchError
        with pytest.raises(HashMismatchError):
            replay(entry, primitive, inp)

    def test_replay_non_deterministic_no_llm_calls_attribute_treated_as_empty(self):
        """replay() with is_deterministic=False treats missing llm_calls attr as empty."""
        # Build a non-deterministic entry with no original llm_calls (edge case)
        entry = AuditLogEntry(
            entry_id="12345678-1234-4234-a234-123456789012",
            primitive_name="echo_for_audit_test",
            primitive_version="0.1.0",
            executed_at="2026-06-03T12:00:00+00:00",
            duration_ms=1.0,
            input_hash="a" * 64,
            output_hash="b" * 64,
            confidence=0.9,
            human_review_required=False,
            llm_calls=[],  # empty — sequence is []
            is_deterministic=False,
        )
        primitive = EchoPrimitive()
        inp = EchoInput(message="empty sequence")

        # EchoPrimitive result has no llm_calls attr → treated as [] → matches []
        result = replay(entry, primitive, inp)
        assert isinstance(result, PrimitiveResult)


# ---------------------------------------------------------------------------
# 10. replay_by_trace_id — load entry from JSONL and replay
# ---------------------------------------------------------------------------


class TestReplayByTraceId:
    def test_replay_by_trace_id_finds_and_replays(self, tmp_path):
        """replay_by_trace_id() locates the entry and returns a PrimitiveResult."""
        primitive = EchoPrimitive()
        inp = EchoInput(message="trace replay")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)

        replayed = replay_by_trace_id(entry.entry_id, str(tmp_path), primitive, inp)
        assert isinstance(replayed, PrimitiveResult)

    def test_replay_by_trace_id_output_matches(self, tmp_path):
        """The output from replay_by_trace_id matches the original."""
        primitive = EchoPrimitive()
        inp = EchoInput(message="trace output check")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            entry = ctx.log(result)

        replayed = replay_by_trace_id(entry.entry_id, str(tmp_path), primitive, inp)
        assert replayed.output.echoed == "trace output check"

    def test_replay_by_trace_id_not_found_raises(self, tmp_path):
        """replay_by_trace_id() raises TraceNotFoundError for an unknown trace_id."""
        with pytest.raises(TraceNotFoundError):
            replay_by_trace_id(
                "00000000-0000-4000-a000-000000000000",
                str(tmp_path),
                EchoPrimitive(),
                EchoInput(message="ghost"),
            )

    def test_replay_by_trace_id_finds_entry_among_multiple(self, tmp_path):
        """replay_by_trace_id() finds the right entry when multiple entries exist."""
        primitive = EchoPrimitive()
        entries = []
        inputs = ["alpha", "beta", "gamma"]
        for msg in inputs:
            inp = EchoInput(message=msg)
            with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
                result = primitive.execute(inp)
                entries.append(ctx.log(result))

        # Replay only the second entry (beta)
        replayed = replay_by_trace_id(entries[1].entry_id, str(tmp_path), primitive, EchoInput(message="beta"))
        assert replayed.output.echoed == "beta"

    def test_replay_by_trace_id_raises_trace_not_found_for_empty_log_dir(self, tmp_path):
        """replay_by_trace_id() raises TraceNotFoundError when log_dir has no JSONL files."""
        with pytest.raises(TraceNotFoundError):
            replay_by_trace_id(
                "12345678-1234-4234-a234-123456789012",
                str(tmp_path / "nonexistent"),
                EchoPrimitive(),
                EchoInput(message="x"),
            )

    def test_replay_by_trace_id_error_message_contains_trace_id(self, tmp_path):
        """TraceNotFoundError message contains the requested trace_id."""
        fake_id = "99999999-9999-4999-a999-999999999999"
        with pytest.raises(TraceNotFoundError, match=fake_id):
            replay_by_trace_id(fake_id, str(tmp_path), EchoPrimitive(), EchoInput(message="x"))

    def test_replay_by_trace_id_non_deterministic_entry(self, tmp_path):
        """replay_by_trace_id() works for a non-deterministic entry with matching prompt sequence."""
        primitive = LLMBackedPrimitive()
        inp = EchoInput(message="trace llm replay")
        with ExecutionContext(primitive, log_dir=str(tmp_path), input=inp) as ctx:
            result = primitive.execute(inp)
            original_calls = list(primitive.last_tracker.calls)
            entry = ctx.log(result, llm_calls=original_calls)

        # Pass the same (model, prompt_hash) sequence → no error
        replayed = replay_by_trace_id(
            entry.entry_id,
            str(tmp_path),
            primitive,
            inp,
            replayed_llm_calls=original_calls,
        )
        assert isinstance(replayed, PrimitiveResult)
