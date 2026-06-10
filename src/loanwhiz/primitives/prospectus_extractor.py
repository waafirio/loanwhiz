"""Prospectus extractor primitive.

Wraps the multi-stage extraction pipeline (`extraction.assembler.extract_deal_model`
— Docling OCR → section router → Gemini definitions / waterfalls / covenants →
assembly) in the standard `Primitive` envelope, so the prospectus *parser* is a
first-class, registered, catalogue-visible primitive alongside the ESMA tape
normaliser — the symmetric "parse a source document into typed, cited, governed
output" operation, just for the prospectus rather than the loan tape.

This is the framework's *compile* step: it turns a ~300-page prospectus PDF into
a typed, machine-runnable `DealModel` that the deterministic primitives
(waterfall runner, covenant monitor, …) then execute against. Because the
extraction is LLM-driven and multi-minute, it is registered as **library-only**
reachability (it is reached by the extraction pipeline, not yet by a REST
endpoint or agent tool) — surfaced honestly in the catalogue, not advertised as
a live callable tool.

The primitive's `confidence` is the deal model's real `completeness_score`
(coverage of the expected SF sections), and its citation grounds the output in
the prospectus URL — so the governance envelope (confidence, citations, audit)
travels with the parser exactly as it does with every other primitive.
"""

from __future__ import annotations

import time

from pydantic import BaseModel, Field

from loanwhiz.extraction.assembler import DealModel, extract_deal_model
from loanwhiz.primitives.base import (
    AuditEntry,
    BaseInput,
    Citation,
    Primitive,
    PrimitiveResult,
)
from loanwhiz.primitives.registry import register_primitive


class ProspectusExtractorInput(BaseInput):
    """Inputs for the prospectus extractor.

    Attributes:
        prospectus_url: URL of the prospectus PDF (HTTP/HTTPS).
        deal_name:      Human-readable deal name (also keys the on-disk cache).
        force_refresh:  Bypass the deal-model cache and re-run extraction.
    """

    prospectus_url: str = Field(..., description="URL of the prospectus PDF.")
    deal_name: str = Field(..., description="Human-readable deal name.")
    force_refresh: bool = Field(
        default=False, description="Bypass the cache and re-extract."
    )


class ProspectusExtractorOutput(BaseModel):
    """Typed output: the compiled deal model plus coverage counts.

    Attributes:
        deal_model:         The full extracted, typed deal model.
        completeness_score: Real coverage of the expected SF sections (0–1).
        n_tranches:         Number of tranches in the capital structure.
        n_triggers:         Number of extracted trigger/covenant names.
        n_waterfalls:       Number of extracted waterfalls (revenue/redemption/…).
        n_definitions:      Number of defined terms extracted.
        sections_found:     The prospectus sections the pipeline resolved.
    """

    deal_model: DealModel
    completeness_score: float = Field(..., ge=0.0, le=1.0)
    n_tranches: int = Field(..., ge=0)
    n_triggers: int = Field(..., ge=0)
    n_waterfalls: int = Field(..., ge=0)
    n_definitions: int = Field(..., ge=0)
    sections_found: list[str] = Field(default_factory=list)


@register_primitive(
    name="prospectus_extractor",
    version="0.1.0",
    description=(
        "Compile a prospectus PDF into a typed, cited DealModel (Docling + "
        "Gemini); confidence is the real section-coverage completeness score."
    ),
    tags=["extraction", "prospectus", "parser", "compile"],
)
class ProspectusExtractor(
    Primitive[ProspectusExtractorInput, ProspectusExtractorOutput]
):
    """Parse a prospectus into a typed, governed deal model."""

    name = "prospectus_extractor"
    version = "0.1.0"
    description = (
        "Compile a prospectus PDF into a typed, cited DealModel (Docling + "
        "Gemini); confidence is the real section-coverage completeness score."
    )

    def execute(  # type: ignore[override]
        self, input: ProspectusExtractorInput
    ) -> PrimitiveResult[ProspectusExtractorOutput]:
        t0 = time.perf_counter()
        input_hash = input.input_hash()

        deal_model = extract_deal_model(
            prospectus_url=input.prospectus_url,
            deal_name=input.deal_name,
            force_refresh=input.force_refresh,
        )

        completeness = max(0.0, min(1.0, float(deal_model.metadata.completeness_score)))
        n_tranches = len(deal_model.tranche_structure)
        n_triggers = len(deal_model.trigger_names)
        n_waterfalls = len(deal_model.waterfalls)
        n_definitions = len(deal_model.definitions)

        output = ProspectusExtractorOutput(
            deal_model=deal_model,
            completeness_score=completeness,
            n_tranches=n_tranches,
            n_triggers=n_triggers,
            n_waterfalls=n_waterfalls,
            n_definitions=n_definitions,
            sections_found=list(deal_model.metadata.sections_found),
        )

        citation = Citation(
            document=input.prospectus_url,
            page_or_row=f"sections: {', '.join(deal_model.metadata.sections_found) or 'none'}",
            excerpt=(
                f"Compiled deal model for {deal_model.metadata.deal_name}: "
                f"{n_tranches} tranches, {n_triggers} triggers, "
                f"{n_waterfalls} waterfalls, {n_definitions} definitions "
                f"(completeness {completeness:.2f})"
            ),
        )

        audit = AuditEntry.now(
            primitive_name=self.name,
            version=self.version,
            input_hash=input_hash,
            duration_ms=(time.perf_counter() - t0) * 1000.0,
        )

        return PrimitiveResult[ProspectusExtractorOutput](
            output=output,
            confidence=completeness,
            citations=[citation],
            audit_entry=audit,
        )
