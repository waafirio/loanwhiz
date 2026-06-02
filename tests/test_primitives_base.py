"""Tests for loanwhiz.primitives.base.

Covers:
- Abstract enforcement: instantiating a subclass without execute() raises TypeError.
- Concrete roundtrip: EchoPrimitive produces a well-typed PrimitiveResult.
- PrimitiveResult.confidence validation (must be in [0.0, 1.0]).
- AuditEntry.input_hash is a valid 64-char hex SHA-256 digest.
- Citation fields are correctly stored and accessible.
- Primitive.describe() returns PrimitiveMetadata with correct schema dicts.
- BaseInput.input_hash() returns a deterministic 64-char hex string.
"""

import time

import pytest
from pydantic import BaseModel, ValidationError

from loanwhiz.primitives import (
    AuditEntry,
    BaseInput,
    Citation,
    Primitive,
    PrimitiveMetadata,
    PrimitiveResult,
)


# ---------------------------------------------------------------------------
# Helpers — a minimal concrete primitive used across tests.
# ---------------------------------------------------------------------------


class EchoInput(BaseInput):
    """Echo primitive input: just a message string."""

    message: str


class EchoOutput(BaseModel):
    """Echo primitive output: the message echoed back."""

    echoed: str


class EchoPrimitive(Primitive[EchoInput, EchoOutput]):
    """Trivial primitive that echoes its input back as output."""

    name = "echo"
    version = "0.1.0"
    description = "Echoes the input message as output."

    def execute(self, input: EchoInput) -> PrimitiveResult[EchoOutput]:
        t0 = time.perf_counter()
        output = EchoOutput(echoed=input.message)
        duration_ms = (time.perf_counter() - t0) * 1000.0
        return PrimitiveResult(
            output=output,
            confidence=1.0,
            citations=[
                Citation(
                    document="test_input",
                    page_or_row=None,
                    excerpt=input.message,
                )
            ],
            audit_entry=AuditEntry.now(
                primitive_name=self.name,
                version=self.version,
                input_hash=input.input_hash(),
                duration_ms=duration_ms,
            ),
        )


# ---------------------------------------------------------------------------
# 1. Abstract enforcement
# ---------------------------------------------------------------------------


def test_primitive_is_abstract_cannot_instantiate_base():
    """Primitive itself cannot be instantiated — it is abstract."""
    with pytest.raises(TypeError):
        Primitive()  # type: ignore[abstract]


def test_subclass_without_execute_cannot_instantiate():
    """A subclass that does not implement execute() cannot be instantiated."""

    class IncompleteP(Primitive[EchoInput, EchoOutput]):
        name = "incomplete"
        version = "0.1.0"
        description = "Missing execute."

    with pytest.raises(TypeError):
        IncompleteP()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# 2. Concrete roundtrip
# ---------------------------------------------------------------------------


def test_echo_primitive_execute_returns_correct_output():
    """EchoPrimitive.execute() returns a PrimitiveResult with the right output."""
    p = EchoPrimitive()
    result = p.execute(EchoInput(message="hello"))
    assert isinstance(result, PrimitiveResult)
    assert result.output.echoed == "hello"


def test_echo_primitive_result_has_correct_types():
    """PrimitiveResult fields have the correct types."""
    p = EchoPrimitive()
    result = p.execute(EchoInput(message="structured finance"))
    assert isinstance(result.output, EchoOutput)
    assert isinstance(result.confidence, float)
    assert isinstance(result.citations, list)
    assert isinstance(result.audit_entry, AuditEntry)


# ---------------------------------------------------------------------------
# 3. Confidence validation
# ---------------------------------------------------------------------------


def test_confidence_exactly_zero_is_valid():
    output = EchoOutput(echoed="x")
    audit = AuditEntry.now("echo", "0.1.0", "a" * 64, 0.0)
    r = PrimitiveResult(output=output, confidence=0.0, citations=[], audit_entry=audit)
    assert r.confidence == 0.0


def test_confidence_exactly_one_is_valid():
    output = EchoOutput(echoed="x")
    audit = AuditEntry.now("echo", "0.1.0", "a" * 64, 0.0)
    r = PrimitiveResult(output=output, confidence=1.0, citations=[], audit_entry=audit)
    assert r.confidence == 1.0


def test_confidence_above_one_raises_validation_error():
    output = EchoOutput(echoed="x")
    audit = AuditEntry.now("echo", "0.1.0", "a" * 64, 0.0)
    with pytest.raises(ValidationError):
        PrimitiveResult(output=output, confidence=1.01, citations=[], audit_entry=audit)


def test_confidence_below_zero_raises_validation_error():
    output = EchoOutput(echoed="x")
    audit = AuditEntry.now("echo", "0.1.0", "a" * 64, 0.0)
    with pytest.raises(ValidationError):
        PrimitiveResult(output=output, confidence=-0.01, citations=[], audit_entry=audit)


# ---------------------------------------------------------------------------
# 4. AuditEntry — input_hash and executed_at
# ---------------------------------------------------------------------------


def test_audit_entry_input_hash_is_64_char_hex():
    """AuditEntry.input_hash must be a 64-character hex string (SHA-256)."""
    inp = EchoInput(message="test")
    h = inp.input_hash()
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_audit_entry_input_hash_is_deterministic():
    """The same input always produces the same hash."""
    inp1 = EchoInput(message="deterministic")
    inp2 = EchoInput(message="deterministic")
    assert inp1.input_hash() == inp2.input_hash()


def test_audit_entry_input_hash_differs_for_different_inputs():
    """Different inputs produce different hashes."""
    h1 = EchoInput(message="aaa").input_hash()
    h2 = EchoInput(message="bbb").input_hash()
    assert h1 != h2


def test_audit_entry_rejects_non_hex_input_hash():
    """AuditEntry rejects a non-hex input_hash."""
    with pytest.raises(ValidationError):
        AuditEntry(
            primitive_name="echo",
            version="0.1.0",
            input_hash="z" * 64,  # 'z' is not hex
            executed_at="2026-06-03T00:00:00+00:00",
            duration_ms=1.0,
        )


def test_audit_entry_rejects_wrong_length_hash():
    """AuditEntry rejects a hash that is not exactly 64 chars."""
    with pytest.raises(ValidationError):
        AuditEntry(
            primitive_name="echo",
            version="0.1.0",
            input_hash="a" * 32,  # too short
            executed_at="2026-06-03T00:00:00+00:00",
            duration_ms=1.0,
        )


def test_audit_entry_now_sets_executed_at_as_iso_string():
    """AuditEntry.now() populates executed_at as a parseable ISO string."""
    from datetime import datetime

    entry = AuditEntry.now("echo", "0.1.0", "a" * 64, 5.0)
    # Should parse without raising.
    dt = datetime.fromisoformat(entry.executed_at)
    assert dt is not None


def test_audit_entry_duration_ms_is_non_negative():
    """duration_ms must be >= 0."""
    with pytest.raises(ValidationError):
        AuditEntry(
            primitive_name="echo",
            version="0.1.0",
            input_hash="a" * 64,
            executed_at="2026-06-03T00:00:00+00:00",
            duration_ms=-1.0,
        )


# ---------------------------------------------------------------------------
# 5. Citation fields
# ---------------------------------------------------------------------------


def test_citation_fields_stored_correctly():
    c = Citation(
        document="green-lion-2026-1-prospectus.pdf",
        page_or_row=42,
        excerpt="Revenue Priority of Payments — step 3: …",
    )
    assert c.document == "green-lion-2026-1-prospectus.pdf"
    assert c.page_or_row == 42
    assert "step 3" in c.excerpt


def test_citation_page_or_row_can_be_none():
    c = Citation(document="some-tape.csv", page_or_row=None, excerpt="row data")
    assert c.page_or_row is None


def test_citation_page_or_row_can_be_string():
    c = Citation(document="tape.csv", page_or_row="row_42", excerpt="some value")
    assert c.page_or_row == "row_42"


def test_echo_primitive_result_has_one_citation():
    """EchoPrimitive attaches exactly one citation per call."""
    p = EchoPrimitive()
    result = p.execute(EchoInput(message="cite me"))
    assert len(result.citations) == 1
    assert result.citations[0].excerpt == "cite me"


# ---------------------------------------------------------------------------
# 6. Primitive.describe() — PrimitiveMetadata
# ---------------------------------------------------------------------------


def test_describe_returns_primitive_metadata():
    meta = EchoPrimitive.describe()
    assert isinstance(meta, PrimitiveMetadata)


def test_describe_has_correct_name_and_version():
    meta = EchoPrimitive.describe()
    assert meta.name == "echo"
    assert meta.version == "0.1.0"
    assert meta.description == "Echoes the input message as output."


def test_describe_input_schema_is_non_empty_dict():
    meta = EchoPrimitive.describe()
    assert isinstance(meta.input_schema, dict)
    assert len(meta.input_schema) > 0


def test_describe_output_schema_is_non_empty_dict():
    meta = EchoPrimitive.describe()
    assert isinstance(meta.output_schema, dict)
    assert len(meta.output_schema) > 0


def test_describe_input_schema_contains_message_property():
    """The input schema should describe the 'message' field."""
    meta = EchoPrimitive.describe()
    # Pydantic v2 JSON schema uses 'properties' at the top level.
    props = meta.input_schema.get("properties", {})
    assert "message" in props


def test_describe_output_schema_contains_echoed_property():
    """The output schema should describe the 'echoed' field."""
    meta = EchoPrimitive.describe()
    props = meta.output_schema.get("properties", {})
    assert "echoed" in props


# ---------------------------------------------------------------------------
# 7. Import surface
# ---------------------------------------------------------------------------


def test_public_imports_available():
    """All documented public symbols are importable from loanwhiz.primitives."""
    from loanwhiz.primitives import (  # noqa: F401
        AuditEntry,
        BaseInput,
        Citation,
        Primitive,
        PrimitiveMetadata,
        PrimitiveResult,
    )
