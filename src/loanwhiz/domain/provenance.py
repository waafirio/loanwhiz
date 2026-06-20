"""Provenance sidecar for the canonical domain schema.

Provenance is a **sidecar map**, not per-field wrappers (locked design decision
1 in ``docs/superpowers/specs/2026-06-20-canonical-domain-schema-design.md``).
The engine reads the plain typed values on ``DealRules`` / ``PeriodInputs`` /
``DealState`` and never sees these objects; the governance layer and the
human-review gate read a parallel :data:`ProvenanceMap` keyed by dotted field
path. This keeps the engine's hot path clean while still letting every value be
traced back to where it came from, how it got there, and how confident we are.

``FieldProvenance.citation`` reuses :class:`loanwhiz.primitives.base.Citation`
(spec: "reuse base.Citation{document, page_or_row, excerpt}") rather than
redefining a parallel citation type — a citation means the same thing whether it
grounds a primitive result or a canonical field.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from loanwhiz.primitives.base import Citation

# ---------------------------------------------------------------------------
# FieldProvenance — one entry per extracted/derived field.
# ---------------------------------------------------------------------------


class FieldProvenance(BaseModel):
    """Where one canonical field's value came from, and how trustworthy it is.

    Attributes:
        source:     The kind of artefact the value originated from.
                    ``"engine"`` / ``"computed"`` values are derived, not
                    extracted; ``"reconciled"`` marks a value confirmed by a
                    cross-check.
        method:     How the value was obtained — a deterministic parse, an
                    OCR+LLM pass, a pure LLM extraction, or an engine
                    computation.
        confidence: Certainty in the value, in ``[0.0, 1.0]``. Engine-computed /
                    deterministic values carry ``1.0`` (or are simply absent
                    from the map — absence means "derived, not extracted").
        citation:   The source reference that grounds the value, when one
                    exists. ``None`` for purely computed values.
        reconciled: ``True`` once a cross-check (the Reconciler matching an
                    engine-recomputed line to a report-stated line to the cent)
                    has confirmed the value. This is the **strong correctness
                    signal**; the human-review gate routes only *unreconciled,
                    low-confidence* fields to a person.
    """

    source: Literal["prospectus", "report", "tape", "config", "engine", "reconciled"] = (
        Field(..., description="Originating artefact / derivation class.")
    )
    method: Literal["deterministic", "ocr+llm", "llm", "computed"] = Field(
        ..., description="How the value was obtained."
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Certainty in the value, in [0.0, 1.0].",
    )
    citation: Citation | None = Field(
        default=None,
        description="Source reference grounding the value; None for computed values.",
    )
    reconciled: bool = Field(
        default=False,
        description="True once a cross-check confirmed the value (report path).",
    )


# ---------------------------------------------------------------------------
# ProvenanceMap — the sidecar, keyed by dotted field path.
# ---------------------------------------------------------------------------

# Keyed by dotted field path, e.g. "tranches.class_a.original_balance".
# A plain dict alias rather than a model: it is attached as a field on the
# canonical aggregates and indexed directly by callers.
ProvenanceMap = dict[str, FieldProvenance]
