"""Tests for loanwhiz.agent.executor.

Covers:
- High-confidence tool calls → overall PASSED, no human review.
- A low-confidence (below retry_threshold) tool call → NEEDS_REVIEW,
  human_review_required=True.
- aggregate_confidence == min of step confidences.
- A mid-band (LOW_CONFIDENCE) tool call → overall LOW_CONFIDENCE, review required,
  retry hook noted in the trace.
- Empty tool-call pack → UNGROUNDED: aggregate 0.0, NEEDS_REVIEW, human review.
- reasoning_trace is populated.
- execute_query convenience function.
- __init__ re-exports.
- Integration test (real Gemini call, skipped by default).

The executor consumes the planner's GovernanceEvidencePack, so every unit test
fabricates a pack and mocks loanwhiz.agent.executor.run_query — no Vertex AI
call is made.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from loanwhiz.agent.executor import (
    DAGExecutor,
    ExecutionResult,
    StepValidation,
    ValidationStatus,
    execute_query,
)
from loanwhiz.governance.evidence_pack import GovernanceEvidencePack, ToolCallRecord


# ---------------------------------------------------------------------------
# Helpers — fabricate evidence packs and a mocked run_query response
# ---------------------------------------------------------------------------


def _tool_call(
    name: str,
    confidence: float,
    index: int = 0,
    citations: list[dict] | None = None,
) -> ToolCallRecord:
    """Build a minimal ToolCallRecord with the given confidence.

    ``citations`` defaults to a single source so a grounded test call is
    *auditable* by default — the missing-citations review trigger (#406) only
    fires when a test deliberately passes ``citations=[]``.
    """
    return ToolCallRecord(
        call_index=index,
        tool_name=name,
        input_summary=f"{name} input",
        output_summary="",
        confidence=confidence,
        citations=[{"source": f"{name}.csv"}] if citations is None else citations,
        duration_ms=0.0,
        timestamp="2026-06-03T00:00:00+00:00",
    )


def _pack(question: str, answer: str, tool_calls: list[ToolCallRecord]) -> GovernanceEvidencePack:
    """Build a fully-derived GovernanceEvidencePack via its factory."""
    return GovernanceEvidencePack.create(query=question, answer=answer, tool_calls=tool_calls)


def _patched_run_query(pack: GovernanceEvidencePack, answer: str):
    """Return a patch context manager whose run_query yields the given pack."""
    mock = MagicMock(return_value={"answer": answer, "evidence_pack": pack})
    return patch("loanwhiz.agent.executor.run_query", mock), mock


# ---------------------------------------------------------------------------
# Test 1 — high-confidence tool calls → overall PASSED
# ---------------------------------------------------------------------------


def test_high_confidence_overall_passed() -> None:
    """All steps at/above threshold → PASSED, no human review."""
    answer = "Pool balance is €990,000,000."
    pack = _pack(
        "What is the pool balance?",
        answer,
        [_tool_call("load_esma_tape", 1.0, 0), _tool_call("aggregate_collections", 0.95, 1)],
    )
    ctx, _ = _patched_run_query(pack, answer)
    with ctx:
        result = DAGExecutor().execute("What is the pool balance?")

    assert isinstance(result, ExecutionResult)
    assert result.overall_status == ValidationStatus.PASSED
    assert result.human_review_required is False
    assert all(sv.status == ValidationStatus.PASSED for sv in result.step_validations)
    assert result.answer == answer
    assert result.evidence_pack_id == pack.pack_id


# ---------------------------------------------------------------------------
# Test 2 — low-confidence (below retry_threshold) → NEEDS_REVIEW
# ---------------------------------------------------------------------------


def test_low_confidence_needs_review_and_human_review_required() -> None:
    """A step below retry_threshold → NEEDS_REVIEW, human_review_required True."""
    answer = "Uncertain answer."
    pack = _pack(
        "Murky question?",
        answer,
        [_tool_call("load_esma_tape", 0.95, 0), _tool_call("check_covenants", 0.3, 1)],
    )
    ctx, _ = _patched_run_query(pack, answer)
    with ctx:
        result = DAGExecutor().execute("Murky question?")

    assert result.overall_status == ValidationStatus.NEEDS_REVIEW
    assert result.human_review_required is True
    statuses = {sv.tool_name: sv.status for sv in result.step_validations}
    assert statuses["load_esma_tape"] == ValidationStatus.PASSED
    assert statuses["check_covenants"] == ValidationStatus.NEEDS_REVIEW


# ---------------------------------------------------------------------------
# Test 3 — aggregate_confidence == min of step confidences
# ---------------------------------------------------------------------------


def test_aggregate_confidence_is_min_of_steps() -> None:
    """aggregate_confidence is the minimum of all step confidences."""
    answer = "Answer."
    pack = _pack(
        "Q?",
        answer,
        [
            _tool_call("load_esma_tape", 0.9, 0),
            _tool_call("run_waterfall", 0.6, 1),
            _tool_call("check_covenants", 0.8, 2),
        ],
    )
    ctx, _ = _patched_run_query(pack, answer)
    with ctx:
        result = DAGExecutor().execute("Q?")

    assert result.aggregate_confidence == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# Test 4 — mid-band step → LOW_CONFIDENCE overall + retry hook noted
# ---------------------------------------------------------------------------


def test_mid_band_step_is_low_confidence_with_retry_hook_noted() -> None:
    """A step in [retry_threshold, confidence_threshold) is LOW_CONFIDENCE.

    No step needs review, so overall is LOW_CONFIDENCE; aggregate (0.6) is
    still below confidence_threshold (0.7), so human review is required, and
    the retry hook is surfaced in the trace.
    """
    answer = "Answer."
    pack = _pack("Q?", answer, [_tool_call("run_waterfall", 0.6, 0)])
    ctx, _ = _patched_run_query(pack, answer)
    with ctx:
        result = DAGExecutor().execute("Q?")

    assert result.step_validations[0].status == ValidationStatus.LOW_CONFIDENCE
    assert result.overall_status == ValidationStatus.LOW_CONFIDENCE
    assert result.human_review_required is True
    assert any("retry hook" in line for line in result.reasoning_trace)


# ---------------------------------------------------------------------------
# Test 5 — empty tool-call pack → UNGROUNDED: aggregate 0.0, NEEDS_REVIEW, human review
# ---------------------------------------------------------------------------


def test_no_tool_calls_is_ungrounded_and_routed_to_review() -> None:
    """An answer with no supporting tool calls is ungrounded: it must NOT
    score full confidence. Pinned to 0.0, NEEDS_REVIEW, human review required —
    otherwise an LLM-only refusal/claim sails through the gate as 'passed'."""
    answer = "I answered without tools."
    pack = _pack("Trivial?", answer, [])
    ctx, _ = _patched_run_query(pack, answer)
    with ctx:
        result = DAGExecutor().execute("Trivial?")

    assert result.step_validations == []
    assert result.aggregate_confidence == pytest.approx(0.0)
    assert result.overall_status == ValidationStatus.NEEDS_REVIEW
    assert result.human_review_required is True
    # The trace names the ungrounded condition explicitly.
    assert any("UNGROUNDED" in line for line in result.reasoning_trace)


# ---------------------------------------------------------------------------
# Test 6 — reasoning_trace is populated and human-readable
# ---------------------------------------------------------------------------


def test_reasoning_trace_is_populated() -> None:
    """reasoning_trace is a non-empty list of human-readable strings."""
    answer = "Pool balance is €1B."
    pack = _pack("What is the pool balance?", answer, [_tool_call("load_esma_tape", 1.0, 0)])
    ctx, _ = _patched_run_query(pack, answer)
    with ctx:
        result = DAGExecutor().execute("What is the pool balance?")

    assert isinstance(result.reasoning_trace, list)
    assert len(result.reasoning_trace) >= 2
    assert all(isinstance(line, str) and line for line in result.reasoning_trace)
    # First line describes the tool call.
    assert "load_esma_tape" in result.reasoning_trace[0]
    assert "confidence 1.00" in result.reasoning_trace[0]
    # A synthesis line summarises the count.
    assert any("Answer synthesised from 1 tool call" in line for line in result.reasoning_trace)


# ---------------------------------------------------------------------------
# Test 7 — step_validations carry a note vs the threshold
# ---------------------------------------------------------------------------


def test_step_validation_note_mentions_threshold() -> None:
    """Each StepValidation note records the confidence and the threshold."""
    answer = "Answer."
    pack = _pack("Q?", answer, [_tool_call("load_esma_tape", 0.9, 0)])
    ctx, _ = _patched_run_query(pack, answer)
    with ctx:
        result = DAGExecutor(confidence_threshold=0.7).execute("Q?")

    sv = result.step_validations[0]
    assert isinstance(sv, StepValidation)
    assert "0.90" in sv.note
    assert "0.7" in sv.note


# ---------------------------------------------------------------------------
# Test 8 — execute_query convenience function
# ---------------------------------------------------------------------------


def test_execute_query_convenience_returns_execution_result() -> None:
    """execute_query() returns an ExecutionResult using the given threshold."""
    answer = "Answer."
    pack = _pack("Q?", answer, [_tool_call("load_esma_tape", 0.95, 0)])
    ctx, mock = _patched_run_query(pack, answer)
    with ctx:
        result = execute_query("Q?", confidence_threshold=0.7)

    assert isinstance(result, ExecutionResult)
    assert result.overall_status == ValidationStatus.PASSED
    mock.assert_called_once()


def test_execute_query_threshold_is_honoured() -> None:
    """A high confidence_threshold flips an otherwise-passing step to review."""
    answer = "Answer."
    pack = _pack("Q?", answer, [_tool_call("load_esma_tape", 0.8, 0)])
    ctx, _ = _patched_run_query(pack, answer)
    with ctx:
        # Threshold 0.9 > 0.8 → step no longer passes; 0.8 >= retry 0.5 → LOW_CONFIDENCE.
        result = execute_query("Q?", confidence_threshold=0.9)

    assert result.overall_status == ValidationStatus.LOW_CONFIDENCE
    assert result.human_review_required is True


# ---------------------------------------------------------------------------
# Test 9 — run_query is invoked with save_evidence=True (audit persistence)
# ---------------------------------------------------------------------------


def test_execute_persists_evidence_via_run_query() -> None:
    """execute() asks the planner to persist the evidence pack (save_evidence=True)."""
    answer = "Answer."
    pack = _pack("Q?", answer, [_tool_call("load_esma_tape", 1.0, 0)])
    ctx, mock = _patched_run_query(pack, answer)
    with ctx:
        DAGExecutor().execute("Q?")

    mock.assert_called_once()
    # save_evidence is passed True (positionally or by keyword).
    _, kwargs = mock.call_args
    assert kwargs.get("save_evidence", None) is True or True in mock.call_args.args


# ---------------------------------------------------------------------------
# Test 10 — __init__ re-exports
# ---------------------------------------------------------------------------


def test_agent_init_exports_executor_symbols() -> None:
    """Executor public symbols are importable from loanwhiz.agent."""
    from loanwhiz.agent import DAGExecutor as DAGExecutorAlias
    from loanwhiz.agent import ExecutionResult as ExecutionResultAlias
    from loanwhiz.agent import StepValidation as StepValidationAlias
    from loanwhiz.agent import ValidationStatus as ValidationStatusAlias
    from loanwhiz.agent import execute_query as execute_query_alias

    assert DAGExecutorAlias is DAGExecutor
    assert ExecutionResultAlias is ExecutionResult
    assert StepValidationAlias is StepValidation
    assert ValidationStatusAlias is ValidationStatus
    assert execute_query_alias is execute_query


# ---------------------------------------------------------------------------
# Test 11 — end-to-end: a real low tool confidence threaded by the planner
# fires the executor's human-review gate (#194). Unlike the fabricated-pack
# tests above, this drives the *real* run_query path with only the LLM agent
# faked, so it proves the threaded value actually reaches the gate.
# ---------------------------------------------------------------------------


def test_real_threaded_low_confidence_fires_executor_review() -> None:
    """A ToolMessage carrying confidence 0.4 → executor NEEDS_REVIEW end-to-end."""
    import json

    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    ai_tc = AIMessage(
        content="",
        tool_calls=[{"name": "check_covenants", "args": {"periods_json": "[]"}, "id": "tcL"}],
    )
    tool_result = ToolMessage(
        content=json.dumps({"summary": "near breach", "confidence": 0.4}),
        tool_call_id="tcL",
        name="check_covenants",
    )
    final_ai = AIMessage(content="Covenant proximity is high.")
    fake_result = {
        "messages": [HumanMessage(content="q"), ai_tc, tool_result, final_ai]
    }

    mock_agent = MagicMock()
    mock_agent.invoke.return_value = fake_result

    # Patch at the planner's agent boundary so the *real* run_query runs and
    # threads the real confidence; the executor calls the unpatched run_query.
    with patch("loanwhiz.agent.planner.create_planner_agent", return_value=mock_agent), \
         patch("loanwhiz.agent.planner.EvidencePackLogger"):
        result = DAGExecutor().execute("q")

    assert result.aggregate_confidence == pytest.approx(0.4)
    assert result.human_review_required is True
    assert result.overall_status == ValidationStatus.NEEDS_REVIEW


# ---------------------------------------------------------------------------
# Review gate (#406) — review_reasons is the machine-readable cause set and
# human_review_required == bool(review_reasons). Three cuts: FIRE on low
# confidence, FIRE on a grounded-but-uncited answer (even at high confidence),
# and PASS (cited, high confidence → flag False, reasons empty).
# ---------------------------------------------------------------------------


def test_review_fires_on_low_confidence_with_reason() -> None:
    """Low aggregate confidence → flag True and a confidence review_reason."""
    answer = "Uncertain."
    pack = _pack("Q?", answer, [_tool_call("check_covenants", 0.3, 0)])
    ctx, _ = _patched_run_query(pack, answer)
    with ctx:
        result = DAGExecutor().execute("Q?")

    assert result.human_review_required is True
    assert result.review_reasons  # non-empty
    assert any("confidence" in r.lower() for r in result.review_reasons)
    # Flag and reasons are kept consistent.
    assert result.human_review_required == bool(result.review_reasons)
    # The reasons are threaded into the human-readable trace too.
    assert any("Routed to human review queue" in line for line in result.reasoning_trace)


def test_review_fires_on_ungrounded_with_reason() -> None:
    """An ungrounded answer names the ungrounded cause in review_reasons."""
    answer = "No tools used."
    pack = _pack("Q?", answer, [])
    ctx, _ = _patched_run_query(pack, answer)
    with ctx:
        result = DAGExecutor().execute("Q?")

    assert result.human_review_required is True
    assert any("ungrounded" in r.lower() for r in result.review_reasons)


def test_review_fires_on_missing_citations_even_at_high_confidence() -> None:
    """A grounded, high-confidence answer with ZERO citations still gates.

    Primitive evidence with no traceable source is unauditable, so the gate
    fires on the citations trigger even though confidence is well above
    threshold (the confidence trigger does NOT fire here).
    """
    answer = "High confidence but no source."
    pack = _pack(
        "Q?",
        answer,
        [_tool_call("run_waterfall", 0.99, 0, citations=[])],
    )
    ctx, _ = _patched_run_query(pack, answer)
    with ctx:
        result = DAGExecutor().execute("Q?")

    assert result.aggregate_confidence == pytest.approx(0.99)
    assert result.human_review_required is True
    assert any("citation" in r.lower() for r in result.review_reasons)
    # Only the citations trigger fired — not the confidence one.
    assert not any("confidence" in r.lower() for r in result.review_reasons)


def test_review_passes_on_high_confidence_cited_answer() -> None:
    """High confidence AND citations present → flag False, reasons empty."""
    answer = "Pool balance is €990,000,000."
    pack = _pack(
        "What is the pool balance?",
        answer,
        [
            _tool_call("load_esma_tape", 1.0, 0, citations=[{"source": "tape.csv"}]),
            _tool_call("aggregate_collections", 0.95, 1, citations=[{"source": "coll.csv"}]),
        ],
    )
    ctx, _ = _patched_run_query(pack, answer)
    with ctx:
        result = DAGExecutor().execute("What is the pool balance?")

    assert result.human_review_required is False
    assert result.review_reasons == []
    assert any("No human review required" in line for line in result.reasoning_trace)


# ---------------------------------------------------------------------------
# Integration test — real Gemini call (skipped unless -m integration)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_integration_execute_pool_balance_question() -> None:
    """Real question through the full planner + executor stack.

    Requires valid GCP credentials and network access.
    Skipped by default; run with: pytest -m integration
    """
    result = execute_query("What is the current pool balance?")

    assert isinstance(result, ExecutionResult)
    assert isinstance(result.answer, str)
    assert len(result.answer) > 10
    assert result.evidence_pack_id
    assert isinstance(result.reasoning_trace, list)
    assert len(result.reasoning_trace) >= 1
    assert result.overall_status in set(ValidationStatus)
