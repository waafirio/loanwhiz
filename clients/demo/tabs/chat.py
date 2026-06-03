"""Docked-chat handler — wires the demo chat to the planner agent (#81).

The shell (``clients/demo/shell.py``) builds a docked chat column beside the
tabs with a placeholder handler. This module replaces that stub with a real
one: a user message runs through the LoanWhiz planner agent
(:func:`loanwhiz.agent.planner.run_query`), grounded in the **loaded deal**
read from the shared :class:`~clients.demo.shell.DealState`, and the answer
comes back in Gradio **messages format** (``{"role", "content"}`` dicts —
this Gradio build's ``Chatbot`` is messages-only) with the agent's cited
documents appended inline as a "Sources:" suffix.

Chat is **not** a tab (see ``CONTRACT.md`` §5): the shell wires
:func:`chat_respond` from ``_render_chat_panel``, passing the same
``deal_state`` ``gr.State`` as an input so answers are deal-specific.

Design notes
------------
- The handler is a **pure function** of ``(message, history, deal_state)`` so
  it is unit-testable offline by mocking ``run_query`` — it builds no Gradio
  components and touches no module-level state.
- ``run_query`` takes only a question string, so the loaded-deal context is
  carried in as a short text preamble (deal name + periods loaded). This is
  the minimal non-invasive grounding; a structured-context signature would be
  a planner-side change out of this issue's scope.
- **Graceful degradation:** any exception from the agent (e.g. a Vertex
  hiccup, missing credentials) is caught and surfaced as a friendly assistant
  message in the chat rather than crashing the demo app.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a runtime import cycle
    from clients.demo.shell import DealState


def _deal_context_preamble(deal_state: "DealState | None") -> str:
    """Build a short grounding preamble describing the loaded deal.

    Prepended to the user's question so the agent's answer is specific to the
    currently-loaded deal. Returns an empty string when no deal is loaded — in
    that case the agent still answers from its system prompt (the Green Lion
    deal is its default subject), so chat degrades to a generic-but-useful
    reply rather than refusing.
    """
    if deal_state is None or not getattr(deal_state, "loaded", False):
        return ""

    periods = [
        str(t.get("period"))
        for t in getattr(deal_state, "tapes", [])
        if t.get("period")
    ]
    parts = [f"You are analysing the loaded deal: {deal_state.deal_name}."]
    if periods:
        parts.append(
            f"Loaded reporting periods: {', '.join(periods)}."
        )
    if getattr(deal_state, "deal_model", None) is not None:
        parts.append("An extracted deal model (waterfalls, covenants) is available.")
    parts.append("Answer the user's question about this deal.\n\n")
    return " ".join(parts)


def _format_sources(evidence_pack: Any) -> str:
    """Render a "Sources:" suffix from a governance evidence pack's citations.

    Reads ``evidence_pack.all_citations`` — a list of citation dicts
    (``document`` / ``page_or_row`` / ``excerpt`` per
    :class:`loanwhiz.primitives.base.Citation`) — and formats the distinct
    cited documents (with their locator, when present) as a compact Markdown
    block. Returns an empty string when there are no citations, so a
    citation-free answer carries no dangling "Sources:" header.
    """
    citations = getattr(evidence_pack, "all_citations", None) or []

    seen: set[str] = set()
    lines: list[str] = []
    for citation in citations:
        if not isinstance(citation, dict):
            continue
        document = citation.get("document")
        if not document:
            continue
        locator = citation.get("page_or_row")
        label = f"{document} ({locator})" if locator not in (None, "") else str(document)
        if label in seen:
            continue
        seen.add(label)
        lines.append(f"- {label}")

    if not lines:
        return ""
    return "\n\n**Sources:**\n" + "\n".join(lines)


def chat_respond(
    message: str,
    history: list[dict],
    deal_state: "DealState | None" = None,
) -> list[dict]:
    """Answer a chat message via the planner agent, grounded in the loaded deal.

    Appends the user's turn and the assistant's reply to ``history`` in Gradio
    messages format and returns the updated list. The assistant reply is the
    planner agent's answer with the cited documents appended inline as a
    "Sources:" suffix.

    Parameters
    ----------
    message:
        The user's message.
    history:
        Gradio ``messages``-format chat history (list of ``{"role", "content"}``
        dicts). Not mutated in place — a new list is returned.
    deal_state:
        The shared :class:`~clients.demo.shell.DealState`. Read (never mutated)
        to ground the answer in the loaded deal. ``None`` / unloaded degrades
        to a generic reply.

    Returns
    -------
    list[dict]
        ``history`` plus the user turn and the assistant turn.

    Notes
    -----
    Any exception raised by the agent is caught and returned as a friendly
    assistant message so a backend hiccup (e.g. Vertex unavailable) never
    crashes the demo app.
    """
    # Imported lazily so importing this module (and the shell) never pulls in
    # the heavy LangGraph / Vertex stack until a question is actually asked —
    # keeps construction and the offline test suite cheap.
    from loanwhiz.agent.planner import run_query

    new_history = list(history) + [{"role": "user", "content": message}]

    try:
        preamble = _deal_context_preamble(deal_state)
        # save_evidence=False: the docked demo chat is interactive, not an
        # audited batch run — skip the disk write on every keystroke-driven
        # query. The evidence pack is still returned for inline citations.
        response = run_query(preamble + message, save_evidence=False)
        answer = response["answer"]
        sources = _format_sources(response.get("evidence_pack"))
        reply = answer + sources
    except Exception as exc:  # noqa: BLE001 — degrade, don't crash the demo.
        reply = (
            "⚠️ Sorry — I couldn't answer that just now "
            f"(the analysis backend returned an error: {exc}). "
            "Please try again in a moment."
        )

    new_history.append({"role": "assistant", "content": reply})
    return new_history
