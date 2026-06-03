"""LoanWhiz Chat — Structured Finance Q&A Interface

Run: python clients/chat/app.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

import gradio as gr
from google import genai

from loanwhiz.config import GCP_PROJECT, GCP_LOCATION, MODEL_FLASH, GREEN_LION
from loanwhiz.primitives.esma_tape_normaliser import EsmaTapeNormaliser, EsmaTapeInput
from loanwhiz.primitives.covenant_monitor import CovenantMonitor, CovenantInput
from loanwhiz.primitives.collections_aggregator import CollectionsAggregator, CollectionsInput

# ---------------------------------------------------------------------------
# Deal context cache
# ---------------------------------------------------------------------------

_TAPE_CACHE: list[dict] = []


def load_deal_context() -> list[dict]:
    """Load Green Lion tape data for use in Q&A context.

    Fetches all three monthly ESMA tapes, normalises them via
    ``EsmaTapeNormaliser``, and returns a list of dicts with ``date`` and
    ``data`` keys.  Results are cached in-process so repeated calls are fast.
    """
    global _TAPE_CACHE
    if _TAPE_CACHE:
        return _TAPE_CACHE

    tapes: list[dict] = []
    normaliser = EsmaTapeNormaliser()
    for tape_info in GREEN_LION["tape_urls"]:
        result = normaliser.execute(EsmaTapeInput(file_url=tape_info["url"]))
        tapes.append({"date": tape_info["date"], "data": result.output.model_dump()})
    _TAPE_CACHE = tapes
    return tapes


# ---------------------------------------------------------------------------
# Q&A logic
# ---------------------------------------------------------------------------

def answer_question(question: str, history: list) -> str:
    """Answer a structured finance question using LoanWhiz primitives + Gemini.

    Parameters
    ----------
    question:
        The user's natural language question about the Green Lion deal.
    history:
        The Gradio chat history list (list of ``[user, assistant]`` pairs);
        unused by the prompt but kept for API compatibility.

    Returns
    -------
    str
        The model's answer, grounded in the tape data.
    """
    tapes = load_deal_context()

    context = (
        "You are a structured finance analyst for the Green Lion 2026-1 deal "
        "(Dutch RMBS, ING Bank N.V. originator).\n\n"
        "Deal data available:\n"
        + json.dumps(
            [
                {
                    "period": t["date"],
                    "pool_balance": t["data"]["pool_balance_eur"],
                    "loan_count": t["data"]["loan_count"],
                    "arrears": t["data"]["arrears_breakdown"],
                    "epc": t["data"]["epc_breakdown"],
                }
                for t in tapes
            ],
            indent=2,
        )
        + f"\n\nAnswer the user's question precisely using the data above. "
        "Cite specific numbers.\nQuestion: "
        + question
    )

    client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)
    response = client.models.generate_content(model=MODEL_FLASH, contents=context)
    return response.text


# ---------------------------------------------------------------------------
# Sample questions
# ---------------------------------------------------------------------------

SAMPLE_QUESTIONS: list[str] = [
    "What is the current arrears profile of the Green Lion pool?",
    "How has the pool balance changed from February to April 2026?",
    "What percentage of the pool has EPC rating A or better?",
    "Are there any defaults in the pool? What is the default rate?",
    "What is the weighted average LTV of the portfolio?",
]


# ---------------------------------------------------------------------------
# Gradio interface
# ---------------------------------------------------------------------------

def create_interface() -> gr.Blocks:
    """Build and return the Gradio Blocks demo for the LoanWhiz chat UI."""
    with gr.Blocks(title="LoanWhiz — Structured Finance Q&A") as demo:
        gr.Markdown(
            "# LoanWhiz\n"
            "### Structured Finance Agent Framework — Green Lion 2026-1 Demo"
        )

        with gr.Row():
            with gr.Column(scale=2):
                chatbot = gr.Chatbot(height=400, label="Q&A")
                msg = gr.Textbox(
                    placeholder="Ask a question about the Green Lion deal...",
                    label="Question",
                )

                with gr.Row():
                    submit = gr.Button("Ask", variant="primary")
                    clear = gr.Button("Clear")

            with gr.Column(scale=1):
                gr.Markdown("### Sample Questions")
                for q in SAMPLE_QUESTIONS:
                    gr.Button(q, size="sm").click(
                        fn=lambda x=q: x,
                        outputs=msg,
                    )

        def respond(message: str, history: list) -> tuple[str, list]:
            answer = answer_question(message, history)
            history = history + [[message, answer]]
            return "", history

        submit.click(respond, [msg, chatbot], [msg, chatbot])
        msg.submit(respond, [msg, chatbot], [msg, chatbot])
        clear.click(lambda: [], None, chatbot)

    return demo


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    demo = create_interface()
    demo.launch(server_name="0.0.0.0", server_port=7860)
