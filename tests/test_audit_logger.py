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
    replay,
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
