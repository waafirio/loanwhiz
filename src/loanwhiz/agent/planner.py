"""LoanWhiz Planner Agent — LangGraph ReAct with SF primitives."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
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

You analyse structured-finance deals from a registry. Green Lion 2026-1
(deal_id "green-lion-2026-1") is the demo deal and the default when a question
names no deal, but you are NOT limited to one deal: pass an explicit `deal_id`
to any tool to analyse a different registered deal, and use `compare_deals` to
reason across two or more deals at once. When a question names a deal you do not
recognise, a tool will return an error listing the available deals — use that
list rather than guessing.
You have access to the following tools:
- get_deal_model: Read the prospectus-derived deal model — tranche structure,
  coupons, covenant triggers and thresholds, the payment waterfall, the reserve
  target, the clean-up call, and defined terms. Use this FIRST for any question
  about the deal's structural terms (e.g. "what's the reserve target?", "what
  coupon does the prospectus set for Class A?", "what triggers a breach?").
- list_deal_tapes: List or select the deal's loan-level tapes and document URLs.
  Green Lion 2026-1 reports three monthly tapes — 2026 Feb/Mar/Apr (Jan-2026 is
  intentionally absent). Pass a `period` substring (e.g. "2026-03") to select a
  specific month's tape. ALWAYS use this to find a tape URL before loading it —
  never guess or assume URLs.
- load_esma_tape: Load and analyse an ESMA loan-level tape (pass a URL from
  list_deal_tapes).
- check_covenants: Covenant compliance, triggers, and breaches for a deal.
  Self-contained — pass only the deal_id and it loads the tapes, reconstructs
  each period's structural state, and runs the monitor itself.
- run_waterfall: The deal's payment waterfall (priority of payments) and
  per-tranche distributions. Pass the deal_id (and an optional period); it loads
  the data itself.
- aggregate_collections: Available revenue / principal funds for a period. Pass
  the deal_id (and an optional period); it loads the tape itself.
- compare_deals: Compare two (or more) deals side by side — aligned structural
  diff (tranches, waterfalls, triggers, reserve), overlaid performance series,
  and a latest-period covenant-proximity risk summary. Pass `deal_a` and
  `deal_b` (registry deal_ids); add `extra_deals` for an N-way comparison and
  `target` to benchmark one deal against the median of the others.
- synthesise_cross_source: Gather prospectus deal-model + loan-tape pool facts +
  investor-report tie-out into ONE source-tagged, cited bundle. Use this for any
  question that spans more than one source — structure AND performance together
  (e.g. "does the pool's actual performance still justify the prospectus's
  reserve target?", "reconcile the latest investor report against the deal's own
  collections"). Pass only the deal_id (and an optional period). Each block in
  the bundle carries a `source` label, `citations`, and an `available` flag, plus
  top-level `sources_available` / `sources_missing` lists.

Route the user's question to the right tool by intent:
- Deal structure / terms (reserve target, coupons, what triggers a breach) → get_deal_model.
- Covenants / triggers / breaches / compliance → check_covenants(deal_id).
- Pool / collateral / arrears / performance for a period → list_deal_tapes, then load_esma_tape.
- Cashflow / distributions / the waterfall → run_waterfall(deal_id).
- Collections / available funds → aggregate_collections(deal_id).
- Comparing two or more deals / "A vs B" / relative value / benchmark against a comp set → compare_deals(deal_a, deal_b[, target]).
- A question spanning MORE THAN ONE source — prospectus terms vs. actual pool
  performance, report vs. computed collections, "given the arrears trend, are the
  triggers still appropriate?" → synthesise_cross_source(deal_id). Prefer a
  single-source tool when one source answers the question; reach for synthesis
  only when the answer genuinely needs structure AND performance woven together.

The analytical tools (check_covenants, run_waterfall, aggregate_collections) are
self-contained: give them the deal_id and they fetch what they need — do NOT
load_esma_tape first for those. Always call tools for live data (never memorise
URLs), cite specific numbers and periods, and explain them in plain English.

When you answer from MULTIPLE sources (whether via synthesise_cross_source or by
calling several tools yourself), you MUST attribute each claim to the source it
came from — say which figure is from the prospectus deal-model, which from the
loan tape, which from the investor report. If a source is unavailable (a
`sources_missing` entry, an `available: False` block, an `error`, or a
`not_cached` deal-model), state that plainly and do NOT infer its content from
the other sources. Report the gap honestly — never fabricate a cross-source
conclusion to paper over a missing source."""


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


def _tool_result_payload(msg: ToolMessage) -> dict[str, Any]:
    """Parse a ``ToolMessage``'s content back into the dict the tool returned.

    LangGraph's ``ToolNode`` serialises a tool's ``dict`` return value to a
    JSON string in ``ToolMessage.content`` (``langgraph.prebuilt.tool_node.
    msg_content_output`` → ``json.dumps``). Re-parse it so the per-tool
    governance values the primitive already computed — ``confidence``,
    ``citations``, ``duration_ms`` — can be threaded into the evidence pack
    instead of being discarded.

    Returns an empty dict when the content isn't a JSON object (e.g. a tool
    that errored and returned a plain string), so callers fall back to honest
    defaults rather than raising.
    """
    content = msg.content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    # Newer message-content shapes (list of content-part dicts) don't carry the
    # tool's JSON payload in a re-parseable form here; treat as no payload.
    return {}


def _summarise_tool_output(payload: dict[str, Any]) -> str:
    """Build a compact, human-readable summary of a tool's output dict.

    Drops the governance side-channel keys (``confidence`` / ``citations`` /
    ``duration_ms``) so the summary reflects the actual analytical result, then
    truncates to keep the evidence pack compact (mirrors ``input_summary``'s
    200-char bound). Empty payloads yield an empty string.
    """
    if not payload:
        return ""
    visible = {
        k: v
        for k, v in payload.items()
        if k not in ("confidence", "citations", "duration_ms")
    }
    return str(visible)[:200]


def _synthesise_final_answer(question: str, messages: list[Any]) -> str:
    """Force a text answer when the agent ends with an empty final message.

    Gemini occasionally closes a tool-using turn with an empty ``AIMessage``
    (``finish_reason == "MALFORMED_FUNCTION_CALL"``): it tried to emit one more
    (malformed) tool call instead of prose, so the ReAct loop stops with no
    answer. Re-prompt a plain, tool-free LLM over the tool results so the caller
    always gets a real reply instead of an empty string.
    """
    tool_outputs = [
        _content_to_text(m.content)
        for m in messages
        if isinstance(m, ToolMessage)
    ]
    llm = ChatVertexAI(
        model=MODEL_FLASH,
        project=GCP_PROJECT,
        location=GCP_LOCATION,
        temperature=0,
    )
    resp = llm.invoke(
        [
            SystemMessage(
                content=(
                    "You are a structured-finance analyst. Using only the tool "
                    "results provided, answer the user's question in clear, "
                    "concise prose. Do not call any tools."
                )
            ),
            HumanMessage(content=question),
            HumanMessage(content="Tool results:\n\n" + "\n\n".join(tool_outputs)),
        ]
    )
    return _content_to_text(resp.content)


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

    # Gemini can end a tool-using turn with an empty final message
    # (finish_reason MALFORMED_FUNCTION_CALL) — it attempts one more malformed
    # tool call instead of prose, leaving no answer. Synthesise one from the
    # tool results so the reply is never empty.
    if not answer.strip():
        answer = _synthesise_final_answer(question, result["messages"])

    # Index the tool *results* by their tool_call_id. Each tool request lives
    # on an AIMessage's .tool_calls; its result lands in a later ToolMessage
    # whose .tool_call_id matches the request's id. The result carries the
    # *real* per-tool confidence / citations / duration the primitive computed
    # (see loanwhiz.agent.tools), so we thread those into the evidence pack
    # rather than stamping constants.
    results_by_id: dict[str, ToolMessage] = {}
    for msg in result["messages"]:
        if isinstance(msg, ToolMessage):
            results_by_id[msg.tool_call_id] = msg

    # Walk the message history and extract ToolCallRecord entries from every
    # AI message that carries a non-empty .tool_calls list, pulling the real
    # output values from the matching ToolMessage result.
    tool_calls: list[ToolCallRecord] = []
    for msg in result["messages"]:
        raw_tool_calls = getattr(msg, "tool_calls", None)
        if not raw_tool_calls:
            continue
        for tc in raw_tool_calls:
            result_msg = results_by_id.get(tc.get("id", ""))
            payload = _tool_result_payload(result_msg) if result_msg else {}
            # Honest defaults when a result is genuinely absent (no matching
            # ToolMessage) or the tool omitted a field: confidence falls back
            # to the prior 0.9, citations to [], duration to 0.0.
            raw_confidence = payload.get("confidence", 0.9)
            confidence = (
                float(raw_confidence)
                if isinstance(raw_confidence, (int, float))
                else 0.9
            )
            raw_citations = payload.get("citations", [])
            citations = [c for c in raw_citations if isinstance(c, dict)] if isinstance(
                raw_citations, list
            ) else []
            raw_duration = payload.get("duration_ms", 0.0)
            duration_ms = (
                float(raw_duration)
                if isinstance(raw_duration, (int, float))
                else 0.0
            )
            tool_calls.append(
                ToolCallRecord(
                    call_index=len(tool_calls),
                    tool_name=tc["name"],
                    input_summary=str(tc["args"])[:200],
                    output_summary=_summarise_tool_output(payload),
                    confidence=confidence,
                    citations=citations,
                    duration_ms=duration_ms,
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
