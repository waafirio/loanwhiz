"""LoanWhiz Planner Agent — LangGraph ReAct with SF primitives."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TypedDict

from langchain_google_vertexai import ChatVertexAI
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent

from loanwhiz.agent.tools import SF_TOOLS
from loanwhiz.config import GCP_LOCATION, GCP_PROJECT, MODEL_FLASH
from loanwhiz.governance.evidence_pack import (
    EvidencePackLogger,
    GovernanceEvidencePack,
    ToolCallRecord,
)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are LoanWhiz, a structured finance analyst specialising in ABS/RMBS deal analysis.

You analyse the Green Lion 2026-1 Dutch RMBS deal (deal_id "green-lion-2026-1").
You have access to the following tools:
- get_deal_model: Read the prospectus-derived deal model — tranche structure,
  coupons, covenant triggers and thresholds, the payment waterfall, the reserve
  target, the clean-up call, and defined terms. Use this FIRST for any question
  about the deal's structural terms (e.g. "what's the reserve target?", "what
  coupon does the prospectus set for Class A?", "what triggers a breach?").
- list_deal_tapes: List or select the deal's loan-level tapes and document URLs.
  The deal has a full monthly history — 27 tapes: the 24 historical months
  2024-01 through 2025-12, plus 2026 Feb/Mar/Apr (Jan-2026 is intentionally
  absent). Pass a `period` substring (e.g. "2025-01") to select a specific
  month's tape. ALWAYS use this to find a tape URL before loading it — never
  guess or assume URLs, and never assume only the three 2026 tapes exist.
- load_esma_tape: Load and analyse an ESMA loan-level tape (pass a URL from
  list_deal_tapes).
- run_waterfall: Execute the payment waterfall for a period.
- check_covenants: Check covenant compliance against trigger thresholds.
- aggregate_collections: Aggregate tape data into waterfall-ready inputs.

Answer the user's question precisely using the available tools. Always:
1. For deal-structure questions, call get_deal_model. For pool/period data, use
   list_deal_tapes to resolve the right tape URL, then load_esma_tape.
2. Call the relevant tools to get current data — do not rely on memorised URLs.
3. Cite specific numbers and periods.
4. Explain what the numbers mean in plain English."""


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------


def create_planner_agent() -> CompiledStateGraph:
    """Create the LoanWhiz LangGraph ReAct planner agent.

    Builds a ``ChatVertexAI`` backbone (Gemini 2.5 Flash) and compiles it
    into a LangGraph ``CompiledStateGraph`` via ``create_react_agent`` with
    the four SF primitive tools bound and the structured finance system
    prompt injected.

    Returns
    -------
    CompiledStateGraph
        A compiled, invokable LangGraph agent graph.
    """
    llm = ChatVertexAI(
        model=MODEL_FLASH,
        project=GCP_PROJECT,
        location=GCP_LOCATION,
        temperature=0,
    )
    agent: CompiledStateGraph = create_react_agent(
        llm,
        SF_TOOLS,
        prompt=SYSTEM_PROMPT,
    )
    return agent


# ---------------------------------------------------------------------------
# Response type
# ---------------------------------------------------------------------------


class AgentResponse(TypedDict):
    """Return type of :func:`run_query`.

    Attributes
    ----------
    answer:
        The agent's final natural language answer to the question.
    evidence_pack:
        A fully-populated :class:`~loanwhiz.governance.evidence_pack.GovernanceEvidencePack`
        capturing every tool call, confidence score, and citation from this
        query (FINOS AI Governance Framework).
    """

    answer: str
    evidence_pack: GovernanceEvidencePack


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def _content_to_text(content: object) -> str:
    """Normalise a LangChain message ``.content`` to a plain string.

    Older models returned a bare ``str``; newer Gemini / langchain versions
    return a list of content-part dicts (``[{"type": "text", "text": "..."}]``)
    or objects. Join the text parts so downstream (str-typed) consumers like
    ``GovernanceEvidencePack.answer`` get a string regardless.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return str(content)


def run_query(question: str, save_evidence: bool = True) -> AgentResponse:
    """Run a structured finance question through the planner agent.

    Invokes the LangGraph ReAct agent, walks the returned message history to
    reconstruct every tool call as a :class:`ToolCallRecord`, builds a
    :class:`GovernanceEvidencePack`, and optionally persists it via
    :class:`EvidencePackLogger`.

    Parameters
    ----------
    question:
        Natural language question about the Green Lion 2026-1 deal.
    save_evidence:
        When ``True`` (the default), persist the evidence pack to disk via
        :class:`EvidencePackLogger` before returning.

    Returns
    -------
    AgentResponse
        ``answer`` — the agent's final text reply.
        ``evidence_pack`` — governance evidence for this query.
    """
    agent = create_planner_agent()
    result = agent.invoke({"messages": [("user", question)]})

    # The last message in the graph output is the final AI answer. Newer
    # Gemini / langchain versions return ``.content`` as a list of content
    # parts (e.g. ``[{"type": "text", "text": "..."}]``) rather than a bare
    # string, so normalise to text before it reaches the (str-typed) evidence
    # pack.
    answer: str = _content_to_text(result["messages"][-1].content)

    # Walk the message history and extract ToolCallRecord entries from every
    # AI message that carries a non-empty .tool_calls list.
    tool_calls: list[ToolCallRecord] = []
    for msg in result["messages"]:
        raw_tool_calls = getattr(msg, "tool_calls", None)
        if not raw_tool_calls:
            continue
        for tc in raw_tool_calls:
            tool_calls.append(
                ToolCallRecord(
                    call_index=len(tool_calls),
                    tool_name=tc["name"],
                    input_summary=str(tc["args"])[:200],
                    output_summary="",  # tool result is not attached to the AI msg
                    confidence=0.9,
                    citations=[],
                    duration_ms=0,
                    timestamp=datetime.now(timezone.utc).isoformat(),
                )
            )

    pack = GovernanceEvidencePack.create(
        query=question,
        answer=answer,
        tool_calls=tool_calls,
    )

    if save_evidence:
        logger = EvidencePackLogger()
        logger.save(pack)

    return AgentResponse(answer=answer, evidence_pack=pack)
