"""Unit tests for the LoanWhiz chat interface (no network / no real Gemini calls)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# Ensure src/ is on the path before importing app
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import gradio as gr

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tape_context() -> list[dict]:
    """Return a minimal tape context list that mirrors load_deal_context output."""
    return [
        {
            "date": "2026-02-28",
            "data": {
                "pool_balance_eur": 1_000_000_000.0,
                "loan_count": 5000,
                "arrears_breakdown": {
                    "current_pct": 98.0,
                    "arrears_1_2m_pct": 1.0,
                    "arrears_180d_plus_pct": 0.5,
                    "default_pct": 0.5,
                },
                "epc_breakdown": {"A": 40.0, "B": 30.0, "C": 20.0, "D": 10.0},
            },
        }
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_sample_questions_nonempty() -> None:
    """SAMPLE_QUESTIONS must contain at least one entry."""
    from clients.chat.app import SAMPLE_QUESTIONS

    assert isinstance(SAMPLE_QUESTIONS, list), "SAMPLE_QUESTIONS should be a list"
    assert len(SAMPLE_QUESTIONS) > 0, "SAMPLE_QUESTIONS must not be empty"


def test_answer_question_mocked() -> None:
    """answer_question() returns a non-empty string when Gemini is mocked."""
    mock_response = SimpleNamespace(text="The current arrears rate is 2%.")
    mock_model = MagicMock()
    mock_model.generate_content.return_value = mock_response
    mock_client = MagicMock()
    mock_client.models = mock_model

    with (
        patch("clients.chat.app.genai.Client", return_value=mock_client),
        patch("clients.chat.app.load_deal_context", return_value=_make_tape_context()),
    ):
        from clients.chat import app  # noqa: PLC0415

        # Reset module-level cache so load_deal_context mock takes effect
        app._TAPE_CACHE = []

        result = app.answer_question("What is the arrears rate?", [])

    assert isinstance(result, str), "answer_question should return a string"
    assert len(result) > 0, "answer_question should return a non-empty string"
    assert result == "The current arrears rate is 2%."


def test_create_interface_returns_blocks() -> None:
    """create_interface() must return a gr.Blocks instance."""
    from clients.chat.app import create_interface

    demo = create_interface()
    assert isinstance(demo, gr.Blocks), (
        f"create_interface() should return gr.Blocks, got {type(demo)}"
    )
