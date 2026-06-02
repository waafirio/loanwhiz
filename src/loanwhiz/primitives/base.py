"""Base primitive interface for the LoanWhiz structured finance agent framework.

Every SF primitive — ESMA tape normaliser, waterfall runner, covenant monitor,
report verifier, etc. — implements the ``Primitive`` abstract class defined here.

Design:
- Typed I/O via Pydantic v2 ``BaseModel`` subclasses (one per primitive).
- ``Primitive[InputT, OutputT]`` is generic so each concrete class declares its
  own exact types without runtime casts.
- ``PrimitiveResult`` bundles the output with cross-cutting metadata: a
  confidence score (0–1), source citations, and a structured audit entry that
  records the input hash, timestamp, and execution duration for governance.
- ``describe()`` is a classmethod that returns machine-readable metadata for the
  primitive registry; judges / operators can introspect the framework without
  instantiating primitives.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, ClassVar, Generic, TypeVar

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Generic type variables — each primitive declares its own InputT / OutputT.
# ---------------------------------------------------------------------------

InputT = TypeVar("InputT", bound="BaseInput")
OutputT = TypeVar("OutputT", bound=BaseModel)


# ---------------------------------------------------------------------------
# BaseInput — convenience base for all primitive input schemas.
# ---------------------------------------------------------------------------


class BaseInput(BaseModel):
    """Base class for all primitive input schemas.

    Subclass this for every primitive's ``InputT``.  The only behaviour added
    over a plain ``BaseModel`` is the ``input_hash()`` helper, which the
    ``execute`` implementation uses to populate ``AuditEntry.input_hash``.
    """

    model_config = {"frozen": True}

    def input_hash(self) -> str:
        """Return the SHA-256 hex digest of the canonical JSON serialisation."""
        payload = self.model_dump_json(by_alias=False).encode()
        return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Citation — a source reference attached to a result.
# ---------------------------------------------------------------------------


class Citation(BaseModel):
    """A source reference that grounds a primitive's output.

    Attributes:
        document:    Human-readable document name (e.g. a prospectus filename,
                     ESMA tape CSV name, investor report title).
        page_or_row: Optional locator within the document — a page number, row
                     index, ESMA field code, or any string that pinpoints the
                     relevant fragment.
        excerpt:     The verbatim or lightly summarised text / value that was
                     relied upon.
    """

    document: str = Field(..., description="Document name or URL.")
    page_or_row: int | str | None = Field(
        default=None,
        description="Page number, row index, or other locator within the document.",
    )
    excerpt: str = Field(..., description="Verbatim or summarised excerpt.")


# ---------------------------------------------------------------------------
# AuditEntry — execution metadata for the governance trail.
# ---------------------------------------------------------------------------


class AuditEntry(BaseModel):
    """Execution metadata recorded for every primitive invocation.

    Attributes:
        primitive_name: The ``name`` class attribute of the primitive.
        version:        The ``version`` class attribute (semver).
        input_hash:     SHA-256 hex digest of the JSON-serialised input.
        executed_at:    ISO 8601 UTC timestamp of when ``execute`` was called.
        duration_ms:    Wall-clock execution time in milliseconds.
    """

    primitive_name: str = Field(..., description="Primitive name.")
    version: str = Field(..., description="Primitive version (semver).")
    input_hash: str = Field(
        ...,
        description="SHA-256 hex digest of the serialised input.",
        min_length=64,
        max_length=64,
    )
    executed_at: str = Field(..., description="ISO 8601 UTC timestamp.")
    duration_ms: float = Field(..., ge=0.0, description="Wall-clock time in ms.")

    @field_validator("input_hash")
    @classmethod
    def _must_be_hex(cls, v: str) -> str:
        try:
            int(v, 16)
        except ValueError as exc:
            raise ValueError("input_hash must be a hex string") from exc
        return v

    @field_validator("executed_at")
    @classmethod
    def _must_be_iso(cls, v: str) -> str:
        # Validate that the string is parseable as an ISO 8601 timestamp.
        datetime.fromisoformat(v)
        return v

    @classmethod
    def now(
        cls,
        primitive_name: str,
        version: str,
        input_hash: str,
        duration_ms: float,
    ) -> "AuditEntry":
        """Convenience constructor that fills ``executed_at`` from the current UTC clock."""
        return cls(
            primitive_name=primitive_name,
            version=version,
            input_hash=input_hash,
            executed_at=datetime.now(timezone.utc).isoformat(),
            duration_ms=duration_ms,
        )


# ---------------------------------------------------------------------------
# PrimitiveResult — the envelope returned by every execute() call.
# ---------------------------------------------------------------------------


class PrimitiveResult(BaseModel, Generic[OutputT]):
    """The envelope returned by every ``Primitive.execute()`` call.

    Attributes:
        output:      The typed output produced by the primitive.
        confidence:  How certain the primitive is about its output (0.0–1.0).
                     1.0 = deterministic / rule-based; lower values signal
                     model uncertainty or data quality issues.
        citations:   Source references that ground the output.
        audit_entry: Execution metadata for governance and audit logs.
    """

    output: OutputT
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score in [0.0, 1.0].",
    )
    citations: list[Citation] = Field(
        default_factory=list,
        description="Source references that ground the output.",
    )
    audit_entry: AuditEntry = Field(..., description="Execution metadata.")


# ---------------------------------------------------------------------------
# PrimitiveMetadata — returned by Primitive.describe() for the registry.
# ---------------------------------------------------------------------------


class PrimitiveMetadata(BaseModel):
    """Machine-readable description of a primitive for the registry.

    Attributes:
        name:          The primitive's ``name`` class attribute.
        version:       The primitive's ``version`` class attribute (semver).
        description:   The primitive's ``description`` class attribute.
        input_schema:  JSON Schema dict for the primitive's ``InputT``.
        output_schema: JSON Schema dict for the primitive's ``OutputT``.
    """

    name: str
    version: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


# ---------------------------------------------------------------------------
# Primitive — the abstract base class every SF primitive implements.
# ---------------------------------------------------------------------------


class Primitive(ABC, Generic[InputT, OutputT]):
    """Abstract base class for all LoanWhiz structured-finance primitives.

    **How to implement a concrete primitive:**

    1. Define your typed input schema (subclass ``BaseInput``).
    2. Define your typed output schema (subclass ``pydantic.BaseModel``).
    3. Subclass ``Primitive[YourInput, YourOutput]``.
    4. Set ``name``, ``version``, and ``description`` class attributes.
    5. Implement ``execute(input: YourInput) -> PrimitiveResult[YourOutput]``.

    Example::

        class TapeInput(BaseInput):
            tape_path: str

        class TapeOutput(BaseModel):
            row_count: int
            field_coverage: float

        class EsmaTapeNormaliser(Primitive[TapeInput, TapeOutput]):
            name = "esma_tape_normaliser"
            version = "0.1.0"
            description = "Normalises an ESMA loan-level data tape."

            def execute(self, input: TapeInput) -> PrimitiveResult[TapeOutput]:
                ...

    Class attributes:
        name:        Short identifier, snake_case (used in ``AuditEntry``).
        version:     Semver string, e.g. ``"0.1.0"``.
        description: One-sentence description for the registry.
    """

    name: ClassVar[str]
    version: ClassVar[str]
    description: ClassVar[str]

    @abstractmethod
    def execute(self, input: InputT) -> PrimitiveResult[OutputT]:  # type: ignore[type-var]
        """Run the primitive against the given typed input.

        Implementations must:
        - Compute the input hash via ``input.input_hash()``.
        - Record the wall-clock duration in milliseconds.
        - Populate ``AuditEntry`` (use ``AuditEntry.now(...)`` for convenience).
        - Return a ``PrimitiveResult`` with ``confidence`` in [0.0, 1.0].
        - Attach ``Citation`` objects for every document fragment relied upon.

        Args:
            input: Validated input matching the primitive's ``InputT`` schema.

        Returns:
            A ``PrimitiveResult[OutputT]`` with output, confidence, citations,
            and audit entry.
        """

    @classmethod
    def describe(cls) -> PrimitiveMetadata:
        """Return machine-readable metadata for the primitive registry.

        Introspects the class's ``name``, ``version``, ``description``, and
        the Pydantic JSON schemas of the generic type parameters.  Because
        Python's runtime generics don't expose the bound type arguments
        directly, the schema dicts are extracted from ``__orig_bases__`` when
        the concrete class is a non-abstract ``Primitive`` subclass.

        Returns:
            A ``PrimitiveMetadata`` instance.  ``input_schema`` and
            ``output_schema`` are ``{}`` when called on the abstract base
            itself (no concrete type arguments available at that level).
        """
        input_schema: dict[str, Any] = {}
        output_schema: dict[str, Any] = {}

        # Walk __orig_bases__ to find the Primitive[InputT, OutputT] parameter.
        for base in getattr(cls, "__orig_bases__", []):
            args = getattr(base, "__args__", None)
            if args and len(args) == 2:
                in_type, out_type = args
                if isinstance(in_type, type) and issubclass(in_type, BaseModel):
                    input_schema = in_type.model_json_schema()
                if isinstance(out_type, type) and issubclass(out_type, BaseModel):
                    output_schema = out_type.model_json_schema()
                break

        return PrimitiveMetadata(
            name=cls.name,
            version=cls.version,
            description=cls.description,
            input_schema=input_schema,
            output_schema=output_schema,
        )
