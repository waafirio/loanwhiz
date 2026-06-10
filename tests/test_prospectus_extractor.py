"""Tests for the prospectus_extractor primitive.

The primitive wraps the (LLM-heavy, multi-stage) `extract_deal_model` pipeline
in the standard `Primitive` envelope so the prospectus parser is a first-class,
registered, catalogue-visible primitive like the ESMA tape normaliser. The
Docling + Gemini extraction itself is unavoidably mocked here; what's under test
is the wrapping (registration, confidence = completeness, counts, citations,
audit).
"""

from __future__ import annotations

from loanwhiz.extraction.assembler import DealModel, DealModelMetadata
from loanwhiz.primitives.base import PrimitiveResult
from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY
from loanwhiz.primitives.prospectus_extractor import (
    ProspectusExtractor,
    ProspectusExtractorInput,
    ProspectusExtractorOutput,
)


def _stub_deal_model() -> DealModel:
    return DealModel(
        metadata=DealModelMetadata(
            deal_name="Test Deal 2026-1 B.V.",
            prospectus_url="https://example.com/prospectus.pdf",
            extracted_at="2026-06-09T00:00:00+00:00",
            extraction_duration_sec=1.0,
            sections_found=["waterfall", "triggers", "tranches"],
            completeness_score=0.75,
            cache_path="/tmp/test-deal.json",
        ),
        definitions={"Reserve Fund": {"definition": "...", "page_or_section": "5.1"}},
        waterfalls={"revenue": {}, "redemption": {}},
        covenants={},
        tranche_structure=[{"name": "Class A"}, {"name": "Class B"}],
        trigger_names=["class_a_pdl_trigger", "reserve_fund_shortfall_trigger"],
    )


def test_prospectus_extractor_is_registered():
    """The parser is a first-class registered primitive in the catalogue."""
    assert PRIMITIVE_REGISTRY.get("prospectus_extractor") is not None
    assert ProspectusExtractor.name == "prospectus_extractor"
    meta = ProspectusExtractor.describe()
    assert meta.name == "prospectus_extractor"
    assert meta.input_schema  # typed input schema present
    assert meta.output_schema  # typed output schema present


def test_execute_wraps_extraction_in_primitive_envelope(monkeypatch):
    """execute() runs the extraction pipeline and returns a governed envelope:
    completeness → confidence, real counts, ≥1 citation, valid audit."""
    monkeypatch.setattr(
        "loanwhiz.primitives.prospectus_extractor.extract_deal_model",
        lambda *a, **k: _stub_deal_model(),
    )

    result = ProspectusExtractor().execute(
        ProspectusExtractorInput(
            prospectus_url="https://example.com/prospectus.pdf",
            deal_name="Test Deal 2026-1 B.V.",
        )
    )

    assert isinstance(result, PrimitiveResult)
    assert isinstance(result.output, ProspectusExtractorOutput)
    # completeness_score is surfaced as the primitive's confidence
    assert result.confidence == 0.75
    # real coverage counts derived from the extracted model
    assert result.output.completeness_score == 0.75
    assert result.output.n_tranches == 2
    assert result.output.n_triggers == 2
    assert result.output.n_waterfalls == 2
    # the full typed deal model travels in the output
    assert result.output.deal_model.metadata.deal_name == "Test Deal 2026-1 B.V."
    # grounded + audited
    assert result.citations
    assert result.citations[0].document == "https://example.com/prospectus.pdf"
    assert result.audit_entry.primitive_name == "prospectus_extractor"
    assert result.audit_entry.duration_ms >= 0.0
