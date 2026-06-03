"""Unit tests for the docked-chat handler (``clients/demo/tabs/chat.py``).

Offline only — ``run_query`` is mocked at its module boundary, so no network,
no Vertex, no LangGraph. Verifies the handler returns Gradio messages-format
output, appends cited documents inline as a "Sources:" suffix, carries the
loaded-deal context into the agent call, and degrades gracefully when the
agent raises.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# Ensure src/ and the repo root are importable.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT))

from clients.demo.tabs import chat  # noqa: E402


@contextmanager
def _patch_run_query(*, return_value=None, side_effect=None):
    """Patch ``run_query`` at its source module — the genuine network boundary.

    The handler imports ``run_query`` lazily from ``loanwhiz.agent.planner``
    inside the call (so importing the chat module never pulls in LangGraph /
    Vertex), so we patch the source attribute the lazy import resolves to.
    """
    with patch(
        "loanwhiz.agent.planner.run_query",
        return_value=return_value,
        side_effect=side_effect,
    ) as mock_rq:
        yield mock_rq


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _loaded_state():
    """A minimal loaded DealState-like object (duck-typed for the handler)."""
    return SimpleNamespace(
        deal_name="Green Lion 2026-1 B.V.",
        tapes=[{"period": "2026-02-28"}, {"period": "2026-03-31"}],
        deal_model=None,
        loaded=True,
        load_error=None,
    )


def _evidence_pack(citations):
    """An evidence-pack-like object carrying ``all_citations``."""
    return SimpleNamespace(all_citations=citations)


def _agent_response(answer, citations):
    """An AgentResponse-shaped dict (TypedDict is a plain dict at runtime)."""
    return {"answer": answer, "evidence_pack": _evidence_pack(citations)}


# ---------------------------------------------------------------------------
# Messages-format output + inline citations
# ---------------------------------------------------------------------------


def test_chat_respond_returns_messages_format_with_answer():
    """Handler appends user + assistant turns in messages format."""
    response = _agent_response("The arrears rate is 2.0%.", [])
    with _patch_run_query(return_value=response) as mock_rq:
        out = chat.chat_respond("What is the arrears rate?", [], _loaded_state())

    assert isinstance(out, list)
    assert out[0] == {"role": "user", "content": "What is the arrears rate?"}
    assert out[-1]["role"] == "assistant"
    assert "The arrears rate is 2.0%." in out[-1]["content"]
    mock_rq.assert_called_once()


def test_chat_respond_appends_citations_inline():
    """Cited documents from the evidence pack appear inline as a Sources suffix."""
    citations = [
        {"document": "green_lion_202602.csv", "page_or_row": "row 12", "excerpt": "..."},
        {"document": "Prospectus.pdf", "page_or_row": 88, "excerpt": "..."},
        # Duplicate document+locator — must be deduplicated.
        {"document": "green_lion_202602.csv", "page_or_row": "row 12", "excerpt": "x"},
    ]
    response = _agent_response("Answer.", citations)
    with _patch_run_query(return_value=response):
        out = chat.chat_respond("q", [], _loaded_state())

    content = out[-1]["content"]
    assert "**Sources:**" in content
    assert "green_lion_202602.csv (row 12)" in content
    assert "Prospectus.pdf (88)" in content
    # Deduplicated: the repeated source appears exactly once.
    assert content.count("green_lion_202602.csv (row 12)") == 1


def test_chat_respond_no_citations_no_sources_block():
    """An answer with no citations carries no dangling Sources header."""
    response = _agent_response("Plain answer.", [])
    with _patch_run_query(return_value=response):
        out = chat.chat_respond("q", [], _loaded_state())

    assert "Sources:" not in out[-1]["content"]
    assert out[-1]["content"] == "Plain answer."


def test_chat_respond_preserves_existing_history():
    """Prior turns are preserved; history is not mutated in place."""
    prior = [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "earlier reply"},
    ]
    response = _agent_response("new answer", [])
    with _patch_run_query(return_value=response):
        out = chat.chat_respond("new q", prior, _loaded_state())

    assert len(prior) == 2  # original list untouched
    assert out[:2] == prior
    assert len(out) == 4


# ---------------------------------------------------------------------------
# Loaded-deal context grounding
# ---------------------------------------------------------------------------


def test_chat_respond_carries_deal_context_into_query():
    """The loaded deal name + periods are passed into the agent question."""
    response = _agent_response("ok", [])
    with _patch_run_query(return_value=response) as mock_rq:
        chat.chat_respond("How is the pool doing?", [], _loaded_state())

    sent_question = mock_rq.call_args.args[0]
    assert "Green Lion 2026-1 B.V." in sent_question
    assert "2026-02-28" in sent_question
    assert "How is the pool doing?" in sent_question


def test_chat_respond_no_deal_loaded_still_answers():
    """With no loaded deal, the handler still calls the agent (generic reply)."""
    response = _agent_response("generic answer", [])
    with _patch_run_query(return_value=response) as mock_rq:
        out = chat.chat_respond("hello", [], None)

    # No preamble prepended when nothing is loaded.
    assert mock_rq.call_args.args[0] == "hello"
    assert out[-1]["content"] == "generic answer"


# ---------------------------------------------------------------------------
# Graceful degradation on agent error
# ---------------------------------------------------------------------------


def test_chat_respond_handles_agent_exception():
    """A raising agent yields a friendly assistant message, not a crash."""
    with _patch_run_query(side_effect=RuntimeError("Vertex 503")):
        out = chat.chat_respond("q", [], _loaded_state())

    assert isinstance(out, list)
    assert out[0] == {"role": "user", "content": "q"}
    assert out[-1]["role"] == "assistant"
    content = out[-1]["content"]
    assert "couldn't answer" in content.lower()
    assert "Vertex 503" in content


# ---------------------------------------------------------------------------
# _format_sources unit behaviour
# ---------------------------------------------------------------------------


def test_format_sources_empty():
    """No citations → empty suffix."""
    assert chat._format_sources(_evidence_pack([])) == ""
    assert chat._format_sources(None) == ""


def test_format_sources_skips_locatorless_and_bad_entries():
    """Citations without a document or that aren't dicts are skipped."""
    citations = [
        {"document": "doc_a.csv", "excerpt": "x"},  # no locator
        {"page_or_row": 5},  # no document → skipped
        "not-a-dict",  # skipped
    ]
    out = chat._format_sources(_evidence_pack(citations))
    # doc_a.csv is rendered with no locator → bare name, no empty parens.
    assert "- doc_a.csv" in out
    assert "doc_a.csv ()" not in out
    # The dict without a document and the non-dict entry are dropped entirely.
    assert out.count("- ") == 1
