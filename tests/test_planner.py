"""Tests for loanwhiz.agent.planner.

Covers:
- create_planner_agent() returns a CompiledStateGraph
- run_query() with a mocked LLM returns a valid AgentResponse
- Tool calls in the message history are tracked in the GovernanceEvidencePack
- EvidencePackLogger.save is called when save_evidence=True
- Integration test (real Gemini call, skipped by default): pool balance question
"""

from __future__ import annotations

import tempfile
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from loanwhiz.agent.planner import AgentResponse, create_planner_agent, run_query
from loanwhiz.governance.evidence_pack import GovernanceEvidencePack


# ---------------------------------------------------------------------------
# Helpers — fake agent result factories
# ---------------------------------------------------------------------------


def _fake_agent_result(answer: str, ai_messages: list[AIMessage] | None = None) -> dict[str, Any]:
    """Build a dict that mimics the dict returned by CompiledStateGraph.invoke().

    The structure is ``{"messages": [HumanMessage, *ai_messages]}`` where the
    last message carries the final answer.
    """
    if ai_messages is None:
        ai_messages = [AIMessage(content=answer)]
    messages: list = [HumanMessage(content="test question")]
    messages.extend(ai_messages)
    return {"messages": messages}


# ---------------------------------------------------------------------------
# Test 1 — create_planner_agent() returns a CompiledStateGraph
# ---------------------------------------------------------------------------


def test_create_planner_agent_returns_compiled_graph() -> None:
    """create_planner_agent() should return a CompiledStateGraph without hitting Vertex AI."""
    from langgraph.graph.state import CompiledStateGraph

    with patch("loanwhiz.agent.planner.ChatVertexAI") as mock_llm_cls:
        # create_react_agent calls llm.bind_tools() internally; fake that out.
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm_cls.return_value = mock_llm

        agent = create_planner_agent()

    assert isinstance(agent, CompiledStateGraph)


# ---------------------------------------------------------------------------
# Test 2 — run_query() with a mocked LLM returns a valid AgentResponse
# ---------------------------------------------------------------------------


def test_run_query_with_mocked_llm_returns_agent_response() -> None:
    """run_query() should return an AgentResponse dict with 'answer' and 'evidence_pack'."""
    expected_answer = "The pool balance is €990,000,000."
    fake_result = _fake_agent_result(expected_answer)

    mock_agent = MagicMock()
    mock_agent.invoke.return_value = fake_result

    with patch("loanwhiz.agent.planner.create_planner_agent", return_value=mock_agent), \
         patch("loanwhiz.agent.planner.EvidencePackLogger"):
        response = run_query("What is the current pool balance?", save_evidence=False)

    assert isinstance(response, dict)
    assert "answer" in response
    assert "evidence_pack" in response
    assert response["answer"] == expected_answer
    assert isinstance(response["evidence_pack"], GovernanceEvidencePack)


def test_run_query_evidence_pack_has_correct_query() -> None:
    """The evidence_pack returned by run_query() should record the original question."""
    question = "What is the arrears rate?"
    fake_result = _fake_agent_result("The arrears rate is 1.5%.")

    mock_agent = MagicMock()
    mock_agent.invoke.return_value = fake_result

    with patch("loanwhiz.agent.planner.create_planner_agent", return_value=mock_agent), \
         patch("loanwhiz.agent.planner.EvidencePackLogger"):
        response = run_query(question, save_evidence=False)

    assert response["evidence_pack"].query == question
    assert response["evidence_pack"].answer == "The arrears rate is 1.5%."


# ---------------------------------------------------------------------------
# Test 3 — Tool calls in AI messages are tracked in the GovernanceEvidencePack
# ---------------------------------------------------------------------------


def test_tool_calls_tracked_in_evidence_pack() -> None:
    """ToolCallRecord entries are extracted from AIMessages that carry .tool_calls."""
    # Simulate the agent making one tool call then returning a final answer.
    ai_with_tool_call = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "load_esma_tape",
                "args": {"file_url": "https://example.com/tape.csv"},
                "id": "tc_001",
            }
        ],
    )
    final_ai = AIMessage(content="Pool balance is €1B.")
    fake_result = _fake_agent_result("Pool balance is €1B.", [ai_with_tool_call, final_ai])

    mock_agent = MagicMock()
    mock_agent.invoke.return_value = fake_result

    with patch("loanwhiz.agent.planner.create_planner_agent", return_value=mock_agent), \
         patch("loanwhiz.agent.planner.EvidencePackLogger"):
        response = run_query("What is the pool balance?", save_evidence=False)

    pack = response["evidence_pack"]
    assert len(pack.tool_calls) == 1
    assert pack.tool_calls[0].tool_name == "load_esma_tape"
    assert pack.tool_calls[0].call_index == 0
    assert "tape.csv" in pack.tool_calls[0].input_summary


def test_multiple_tool_calls_all_tracked() -> None:
    """Multiple tool calls across multiple AI messages are all captured in order."""
    ai1 = AIMessage(
        content="",
        tool_calls=[
            {"name": "load_esma_tape", "args": {"file_url": "url1"}, "id": "tc1"}
        ],
    )
    ai2 = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "run_waterfall",
                "args": {
                    "reporting_period": "April 2026",
                    "available_revenue_funds": 9_000_000.0,
                },
                "id": "tc2",
            }
        ],
    )
    final_ai = AIMessage(content="The waterfall ran successfully.")
    fake_result = _fake_agent_result("The waterfall ran successfully.", [ai1, ai2, final_ai])

    mock_agent = MagicMock()
    mock_agent.invoke.return_value = fake_result

    with patch("loanwhiz.agent.planner.create_planner_agent", return_value=mock_agent), \
         patch("loanwhiz.agent.planner.EvidencePackLogger"):
        response = run_query("Run the waterfall.", save_evidence=False)

    pack = response["evidence_pack"]
    assert len(pack.tool_calls) == 2
    assert pack.tool_calls[0].tool_name == "load_esma_tape"
    assert pack.tool_calls[0].call_index == 0
    assert pack.tool_calls[1].tool_name == "run_waterfall"
    assert pack.tool_calls[1].call_index == 1


def test_no_tool_calls_yields_empty_pack_tool_list() -> None:
    """When the agent answers without calling any tools, tool_calls is empty."""
    fake_result = _fake_agent_result("I cannot answer that.")

    mock_agent = MagicMock()
    mock_agent.invoke.return_value = fake_result

    with patch("loanwhiz.agent.planner.create_planner_agent", return_value=mock_agent), \
         patch("loanwhiz.agent.planner.EvidencePackLogger"):
        response = run_query("An unanswerable question.", save_evidence=False)

    assert response["evidence_pack"].tool_calls == []
    assert response["evidence_pack"].aggregate_confidence == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Test 4 — EvidencePackLogger.save is called when save_evidence=True
# ---------------------------------------------------------------------------


def test_evidence_pack_saved_when_save_evidence_true() -> None:
    """EvidencePackLogger.save() is called exactly once when save_evidence=True."""
    fake_result = _fake_agent_result("Pool balance is €1B.")

    mock_agent = MagicMock()
    mock_agent.invoke.return_value = fake_result

    with patch("loanwhiz.agent.planner.create_planner_agent", return_value=mock_agent), \
         patch("loanwhiz.agent.planner.EvidencePackLogger") as mock_logger_cls:
        mock_logger = MagicMock()
        mock_logger_cls.return_value = mock_logger
        mock_logger.save.return_value = "/tmp/loanwhiz_governance/packs/2026-06-03.jsonl"

        run_query("What is the pool balance?", save_evidence=True)

    mock_logger.save.assert_called_once()
    saved_pack = mock_logger.save.call_args[0][0]
    assert isinstance(saved_pack, GovernanceEvidencePack)


def test_evidence_pack_not_saved_when_save_evidence_false() -> None:
    """EvidencePackLogger.save() is NOT called when save_evidence=False."""
    fake_result = _fake_agent_result("No save.")

    mock_agent = MagicMock()
    mock_agent.invoke.return_value = fake_result

    with patch("loanwhiz.agent.planner.create_planner_agent", return_value=mock_agent), \
         patch("loanwhiz.agent.planner.EvidencePackLogger") as mock_logger_cls:
        mock_logger = MagicMock()
        mock_logger_cls.return_value = mock_logger

        run_query("A question.", save_evidence=False)

    mock_logger.save.assert_not_called()


def test_evidence_pack_saved_to_custom_log_dir() -> None:
    """When save_evidence=True, a GovernanceEvidencePack is saved to disk in a temp dir."""
    fake_result = _fake_agent_result("Answer.")

    mock_agent = MagicMock()
    mock_agent.invoke.return_value = fake_result

    with tempfile.TemporaryDirectory() as tmpdir, \
         patch("loanwhiz.agent.planner.create_planner_agent", return_value=mock_agent), \
         patch("loanwhiz.agent.planner.EvidencePackLogger") as mock_logger_cls:
        mock_logger = MagicMock()
        mock_logger_cls.return_value = mock_logger

        run_query("Question.", save_evidence=True)

    mock_logger.save.assert_called_once()


# ---------------------------------------------------------------------------
# Test — __init__.py re-exports
# ---------------------------------------------------------------------------


def test_agent_init_exports_planner_symbols() -> None:
    """create_planner_agent, run_query, and AgentResponse are importable from loanwhiz.agent."""
    from loanwhiz.agent import AgentResponse as AgentResponseAlias
    from loanwhiz.agent import create_planner_agent as cpa
    from loanwhiz.agent import run_query as rq

    assert callable(cpa)
    assert callable(rq)
    assert AgentResponseAlias is AgentResponse


# ---------------------------------------------------------------------------
# Integration test — real Gemini call (skipped unless -m integration)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_integration_pool_balance_question() -> None:
    """Real question against Green Lion 2026-1 via Gemini 2.5 Flash.

    Requires valid GCP credentials and network access.
    Skipped by default; run with: pytest -m integration
    """
    response = run_query(
        "What is the current pool balance?",
        save_evidence=False,
    )

    assert isinstance(response["answer"], str)
    assert len(response["answer"]) > 10, "Answer should be non-trivial"
    assert isinstance(response["evidence_pack"], GovernanceEvidencePack)
    # A real Gemini run should call at least one tool to fetch the tape.
    assert len(response["evidence_pack"].tool_calls) >= 1
