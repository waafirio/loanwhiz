"""Model card for the LoanWhiz LangGraph agent.

This module carries the machine-readable model card for the LangGraph
ReAct agent that orchestrates the LoanWhiz primitives.  It is distinct
from the extraction-pipeline model card (which will live in docs/) and
the primitive-level audit records produced by ``evidence_pack.py``.

Following FINOS AI Governance Framework conventions, the card documents
the agent's intended use, out-of-scope uses, backbone model, tools,
confidence threshold, and human-review routing logic.

The ``finos_governance`` block is structured (not a bare slogan): it cites
``governance/finos_conformance.py`` as the single source of truth for the
mapped FINOS control catalogue and carries the satisfied / partial /
not-applicable counts so the card never overstates the posture.
"""

from loanwhiz.governance.finos_conformance import finos_conformance_summary

_CONFORMANCE = finos_conformance_summary()

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
    "finos_governance": {
        "framework": _CONFORMANCE["framework"],
        "reference": _CONFORMANCE["reference"],
        "is_conformant": _CONFORMANCE["is_conformant"],
        "controls_mapped": _CONFORMANCE["total_controls"],
        "counts": _CONFORMANCE["counts"],
        "source_of_truth": "loanwhiz.governance.finos_conformance",
        "summary": (
            "Conforms to the FINOS AI Governance Framework control catalogue; "
            "see governance/finos_conformance.py for the per-control mapping "
            "and per-primitive conformance."
        ),
    },
    "limitations": [
        "Validated on Green Lion 2026-1 Dutch RMBS only",
        "Synthetic loan tapes",
    ],
}
