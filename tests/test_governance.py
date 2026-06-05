"""Tests for the LoanWhiz governance evidence pack module.

Covers:
- GovernanceEvidencePack creation with mock tool calls
- aggregate_confidence = min of all tool call confidences
- human_review_required flag at the 0.7 threshold
- to_markdown() produces valid markdown
- JSONL round-trip via EvidencePackLogger
- list_packs() returns the correct number of packs
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from loanwhiz.governance import (
    AGENT_MODEL_CARD,
    EvidencePackLogger,
    GovernanceEvidencePack,
    ToolCallRecord,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_tool_call(
    index: int,
    confidence: float,
    citations: list[dict] | None = None,
) -> ToolCallRecord:
    return ToolCallRecord(
        call_index=index,
        tool_name=f"tool_{index}",
        input_summary=f"input for call {index}",
        output_summary=f"output for call {index}",
        confidence=confidence,
        citations=citations or [],
        duration_ms=42.0 + index,
        timestamp="2026-06-03T08:00:00+00:00",
    )


def _sample_pack(confidences: list[float] | None = None) -> GovernanceEvidencePack:
    if confidences is None:
        confidences = [0.9, 0.8, 0.75]
    tool_calls = [_make_tool_call(i, c) for i, c in enumerate(confidences)]
    return GovernanceEvidencePack.create(
        query="What is the waterfall output for period 3?",
        answer="The senior tranche receives 98.2% of available funds.",
        tool_calls=tool_calls,
    )


# ---------------------------------------------------------------------------
# Test 1 — pack creation with mock tool calls
# ---------------------------------------------------------------------------


class TestGovernanceEvidencePackCreation:
    def test_pack_id_is_set(self) -> None:
        pack = _sample_pack()
        assert pack.pack_id != ""
        assert len(pack.pack_id) == 36  # UUID4 string form

    def test_timestamp_is_iso8601(self) -> None:
        pack = _sample_pack()
        # Should be parseable without error
        from datetime import datetime
        dt = datetime.fromisoformat(pack.timestamp)
        assert dt.tzinfo is not None

    def test_query_and_answer_preserved(self) -> None:
        pack = _sample_pack()
        assert pack.query == "What is the waterfall output for period 3?"
        assert pack.answer == "The senior tranche receives 98.2% of available funds."

    def test_tool_calls_preserved(self) -> None:
        pack = _sample_pack([0.9, 0.8])
        assert len(pack.tool_calls) == 2
        assert pack.tool_calls[0].tool_name == "tool_0"
        assert pack.tool_calls[1].tool_name == "tool_1"

    def test_governance_metadata_defaults(self) -> None:
        pack = _sample_pack()
        assert pack.model_used == "gemini-2.5-flash"
        assert pack.framework_version == "loanwhiz-0.1.0"
        # finos_compliant is now derived; a well-formed pack is compliant.
        assert pack.finos_compliant is True


# ---------------------------------------------------------------------------
# Test 1b — finos_compliant is DERIVED by a real check, not a constant (#194)
# ---------------------------------------------------------------------------


class TestFinosComplianceDerivation:
    def test_wellformed_pack_is_compliant(self) -> None:
        """A pack created via the factory has consistent evidence → compliant."""
        pack = _sample_pack([0.9, 0.6, 0.8])
        assert pack.finos_compliant is True

    def test_no_tool_pack_is_compliant(self) -> None:
        """An empty-tool pack (aggregate 1.0, no citations, no review) is compliant."""
        pack = GovernanceEvidencePack.create(query="q", answer="a", tool_calls=[])
        assert pack.finos_compliant is True

    def test_check_rejects_wrong_aggregate(self) -> None:
        """The derivation actually computes: a mismatched aggregate is non-compliant."""
        tcs = [_make_tool_call(0, 0.9), _make_tool_call(1, 0.6)]
        # Real aggregate is 0.6; assert the check flags an inconsistent 0.9.
        assert (
            GovernanceEvidencePack._check_finos_compliant(
                tool_calls=tcs,
                aggregate_confidence=0.9,  # wrong — should be min = 0.6
                all_citations=[],
                human_review_required=True,
            )
            is False
        )

    def test_check_rejects_wrong_review_flag(self) -> None:
        """A review flag inconsistent with the threshold is non-compliant."""
        tcs = [_make_tool_call(0, 0.5)]  # aggregate 0.5 < 0.7 → review SHOULD be True
        assert (
            GovernanceEvidencePack._check_finos_compliant(
                tool_calls=tcs,
                aggregate_confidence=0.5,
                all_citations=[],
                human_review_required=False,  # wrong
            )
            is False
        )

    def test_check_rejects_out_of_range_confidence(self) -> None:
        """A per-tool confidence outside [0,1] is non-compliant."""
        tcs = [_make_tool_call(0, 1.5)]
        assert (
            GovernanceEvidencePack._check_finos_compliant(
                tool_calls=tcs,
                aggregate_confidence=1.5,
                all_citations=[],
                human_review_required=False,
            )
            is False
        )

    def test_check_rejects_dropped_citations(self) -> None:
        """An all_citations that doesn't match the tool citations is non-compliant."""
        cite = {"document": "tape.csv", "page_or_row": 1, "excerpt": "x"}
        tcs = [_make_tool_call(0, 0.9, citations=[cite])]
        assert (
            GovernanceEvidencePack._check_finos_compliant(
                tool_calls=tcs,
                aggregate_confidence=0.9,
                all_citations=[],  # dropped the real citation
                human_review_required=False,
            )
            is False
        )


# ---------------------------------------------------------------------------
# Test 2 — aggregate_confidence = min of tool call confidences
# ---------------------------------------------------------------------------


class TestAggregateConfidence:
    def test_aggregate_is_min(self) -> None:
        confidences = [0.9, 0.6, 0.8]
        pack = _sample_pack(confidences)
        assert pack.aggregate_confidence == pytest.approx(0.6)

    def test_aggregate_with_single_call(self) -> None:
        pack = _sample_pack([0.75])
        assert pack.aggregate_confidence == pytest.approx(0.75)

    def test_aggregate_with_no_tool_calls(self) -> None:
        pack = GovernanceEvidencePack.create(
            query="test",
            answer="answer",
            tool_calls=[],
        )
        assert pack.aggregate_confidence == pytest.approx(1.0)

    def test_aggregate_with_all_equal(self) -> None:
        pack = _sample_pack([0.85, 0.85, 0.85])
        assert pack.aggregate_confidence == pytest.approx(0.85)


# ---------------------------------------------------------------------------
# Test 3 — human_review_required flag at the 0.7 threshold
# ---------------------------------------------------------------------------


class TestHumanReviewRequired:
    def test_review_required_when_below_threshold(self) -> None:
        pack = _sample_pack([0.9, 0.65, 0.8])  # min = 0.65 < 0.7
        assert pack.human_review_required is True

    def test_review_not_required_when_above_threshold(self) -> None:
        pack = _sample_pack([0.9, 0.8, 0.75])  # min = 0.75 >= 0.7
        assert pack.human_review_required is False

    def test_review_not_required_at_exactly_threshold(self) -> None:
        pack = _sample_pack([0.9, 0.7])  # min = 0.7, NOT < 0.7
        assert pack.human_review_required is False

    def test_review_required_just_below_threshold(self) -> None:
        pack = _sample_pack([0.9, 0.6999])  # min just below 0.7
        assert pack.human_review_required is True

    def test_no_tool_calls_no_review_required(self) -> None:
        pack = GovernanceEvidencePack.create(
            query="test", answer="answer", tool_calls=[]
        )
        assert pack.human_review_required is False  # aggregate = 1.0


# ---------------------------------------------------------------------------
# Test 4 — to_markdown() produces valid markdown
# ---------------------------------------------------------------------------


class TestToMarkdown:
    def test_returns_string(self) -> None:
        pack = _sample_pack()
        md = pack.to_markdown()
        assert isinstance(md, str)

    def test_contains_pack_id(self) -> None:
        pack = _sample_pack()
        md = pack.to_markdown()
        assert pack.pack_id in md

    def test_contains_query(self) -> None:
        pack = _sample_pack()
        md = pack.to_markdown()
        assert "What is the waterfall output for period 3?" in md

    def test_contains_answer(self) -> None:
        pack = _sample_pack()
        md = pack.to_markdown()
        assert "The senior tranche receives 98.2% of available funds." in md

    def test_contains_tool_names(self) -> None:
        pack = _sample_pack([0.9, 0.8])
        md = pack.to_markdown()
        assert "tool_0" in md
        assert "tool_1" in md

    def test_contains_confidence(self) -> None:
        pack = _sample_pack([0.9, 0.6])
        md = pack.to_markdown()
        # aggregate_confidence is min(0.9, 0.6) = 0.6
        assert "0.600" in md

    def test_human_review_flag_shown(self) -> None:
        pack = _sample_pack([0.9, 0.65])  # triggers review
        md = pack.to_markdown()
        assert "YES" in md or "flagged" in md.lower()

    def test_has_markdown_headers(self) -> None:
        pack = _sample_pack()
        md = pack.to_markdown()
        assert "# Governance Evidence Pack" in md
        assert "## Query" in md
        assert "## Answer" in md
        assert "## Confidence" in md
        assert "## Tool Call Audit Log" in md
        assert "## Citation Trail" in md

    def test_citations_shown(self) -> None:
        citation = {"source": "green_lion_prospectus.pdf", "page": 42}
        tc = ToolCallRecord(
            call_index=0,
            tool_name="load_esma_tape",
            input_summary="tape for period 1",
            output_summary="1200 loans loaded",
            confidence=0.9,
            citations=[citation],
            duration_ms=100.0,
            timestamp="2026-06-03T08:00:00+00:00",
        )
        pack = GovernanceEvidencePack.create(
            query="q", answer="a", tool_calls=[tc]
        )
        md = pack.to_markdown()
        assert "green_lion_prospectus.pdf" in md


# ---------------------------------------------------------------------------
# Citation deduplication tests
# ---------------------------------------------------------------------------


class TestCitationDeduplication:
    def test_duplicate_citations_collapsed(self) -> None:
        citation = {"source": "green_lion.pdf", "page": 1}
        tc0 = _make_tool_call(0, 0.9, citations=[citation])
        tc1 = _make_tool_call(1, 0.8, citations=[citation])  # same citation
        pack = GovernanceEvidencePack.create(
            query="q", answer="a", tool_calls=[tc0, tc1]
        )
        assert len(pack.all_citations) == 1
        assert pack.all_citations[0] == citation

    def test_distinct_citations_preserved(self) -> None:
        c0 = {"source": "prospectus.pdf", "page": 1}
        c1 = {"source": "tape.csv", "row": 42}
        tc0 = _make_tool_call(0, 0.9, citations=[c0])
        tc1 = _make_tool_call(1, 0.8, citations=[c1])
        pack = GovernanceEvidencePack.create(
            query="q", answer="a", tool_calls=[tc0, tc1]
        )
        assert len(pack.all_citations) == 2

    def test_first_seen_order_preserved(self) -> None:
        c0 = {"source": "a.pdf"}
        c1 = {"source": "b.pdf"}
        c_dup = {"source": "a.pdf"}  # duplicate of c0
        tc0 = _make_tool_call(0, 0.9, citations=[c0, c1])
        tc1 = _make_tool_call(1, 0.8, citations=[c_dup])
        pack = GovernanceEvidencePack.create(
            query="q", answer="a", tool_calls=[tc0, tc1]
        )
        assert len(pack.all_citations) == 2
        assert pack.all_citations[0]["source"] == "a.pdf"
        assert pack.all_citations[1]["source"] == "b.pdf"


# ---------------------------------------------------------------------------
# Test 5 — JSONL round-trip via EvidencePackLogger
# ---------------------------------------------------------------------------


class TestEvidencePackLoggerRoundTrip:
    def test_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = EvidencePackLogger(log_dir=tmpdir)
            pack = _sample_pack()
            path = logger.save(pack)

            assert Path(path).exists()

            loaded = logger.load(pack.pack_id)
            assert loaded is not None
            assert loaded.pack_id == pack.pack_id
            assert loaded.query == pack.query
            assert loaded.answer == pack.answer
            assert loaded.aggregate_confidence == pytest.approx(
                pack.aggregate_confidence
            )

    def test_load_missing_pack_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = EvidencePackLogger(log_dir=tmpdir)
            result = logger.load("nonexistent-id")
            assert result is None

    def test_jsonl_line_is_valid_json(self) -> None:
        pack = _sample_pack()
        line = pack.to_jsonl_line()
        parsed = json.loads(line)
        assert parsed["pack_id"] == pack.pack_id
        assert parsed["query"] == pack.query

    def test_model_validate_json_round_trip(self) -> None:
        pack = _sample_pack()
        serialised = pack.to_jsonl_line()
        reconstructed = GovernanceEvidencePack.model_validate_json(serialised)
        assert reconstructed.pack_id == pack.pack_id
        assert reconstructed.aggregate_confidence == pytest.approx(
            pack.aggregate_confidence
        )
        assert reconstructed.human_review_required == pack.human_review_required
        assert len(reconstructed.tool_calls) == len(pack.tool_calls)


# ---------------------------------------------------------------------------
# Test 6 — list_packs() returns the correct number of packs
# ---------------------------------------------------------------------------


class TestEvidencePackLoggerListPacks:
    def test_list_packs_returns_saved_packs(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = EvidencePackLogger(log_dir=tmpdir)
            for _ in range(5):
                logger.save(_sample_pack())

            packs = logger.list_packs(limit=10)
            assert len(packs) == 5

    def test_list_packs_honours_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = EvidencePackLogger(log_dir=tmpdir)
            for _ in range(7):
                logger.save(_sample_pack())

            packs = logger.list_packs(limit=3)
            assert len(packs) == 3

    def test_list_packs_empty_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = EvidencePackLogger(log_dir=tmpdir)
            packs = logger.list_packs()
            assert packs == []

    def test_list_packs_returns_govenancepack_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = EvidencePackLogger(log_dir=tmpdir)
            logger.save(_sample_pack())

            packs = logger.list_packs()
            assert len(packs) == 1
            assert isinstance(packs[0], GovernanceEvidencePack)


# ---------------------------------------------------------------------------
# Bonus — agent model card sanity check
# ---------------------------------------------------------------------------


class TestAgentModelCard:
    def test_required_keys_present(self) -> None:
        required = [
            "name",
            "type",
            "backbone",
            "tools",
            "intended_use",
            "out_of_scope",
            "confidence_threshold",
            "human_review_routing",
            "finos_governance",
            "limitations",
        ]
        for key in required:
            assert key in AGENT_MODEL_CARD, f"Missing key: {key}"

    def test_confidence_threshold_matches_implementation(self) -> None:
        # The threshold in the model card must match the implementation logic.
        threshold = AGENT_MODEL_CARD["confidence_threshold"]
        pack_below = _sample_pack([threshold - 0.01])
        pack_at = _sample_pack([threshold])
        assert pack_below.human_review_required is True
        assert pack_at.human_review_required is False

    def test_tools_list_is_non_empty(self) -> None:
        assert len(AGENT_MODEL_CARD["tools"]) > 0

    def test_out_of_scope_is_list(self) -> None:
        assert isinstance(AGENT_MODEL_CARD["out_of_scope"], list)
