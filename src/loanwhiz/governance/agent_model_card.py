"""Model card for the LoanWhiz LangGraph agent.

This module carries the machine-readable model card for the LangGraph
ReAct agent that orchestrates the LoanWhiz primitives.  It is distinct
from the extraction-pipeline model card (which will live in docs/) and
the primitive-level audit records produced by ``evidence_pack.py``.

Following FINOS AI Governance Framework conventions, the card documents
the agent's intended use, out-of-scope uses, backbone model, tools,
confidence threshold, and human-review routing logic.
"""

AGENT_MODEL_CARD: dict = {
    "name": "LoanWhiz Agent v0.1",
    "type": "LangGraph ReAct agent",
    "backbone": "Gemini 2.5 Flash (Vertex AI, project=loanwhiz)",
    "tools": [
        "load_esma_tape",
        "run_waterfall",
        "check_covenants",
        "aggregate_collections",
    ],
    "intended_use": "Structured finance Q&A over RMBS deal data",
    "out_of_scope": [
        "Legal advice",
        "Investment recommendations",
        "Regulatory compliance",
    ],
    "confidence_threshold": 0.7,
    "human_review_routing": (
        "Queries with aggregate_confidence < 0.7 flagged for review"
    ),
    "finos_governance": "Follows FINOS AI Governance Framework",
    "limitations": [
        "Validated on Green Lion 2026-1 Dutch RMBS only",
        "Synthetic loan tapes",
    ],
}
