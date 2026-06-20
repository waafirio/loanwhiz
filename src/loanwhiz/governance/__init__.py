"""LoanWhiz governance package.

Exports the FINOS-aligned governance evidence layer:

- ``ToolCallRecord`` — per-tool-call audit record within a query.
- ``GovernanceEvidencePack`` — complete governance evidence for one
  agent query (confidence, citations, tool-call log, replayable trace).
- ``EvidencePackLogger`` — disk-backed persistence for evidence packs.
- ``AGENT_MODEL_CARD`` — machine-readable model card for the LoanWhiz
  LangGraph ReAct agent.
- ``FinosControl`` / ``FINOS_CONTROL_CATALOGUE`` — the FINOS AI Governance
  Framework control catalogue mapped to LoanWhiz (the genuine compliance
  posture that ``finos_compliant`` now means).
- ``finos_conformance_summary`` / ``primitive_conformance`` /
  ``is_framework_conformant`` — the framework-level and per-primitive
  conformance assessment.
"""

from loanwhiz.governance.agent_model_card import AGENT_MODEL_CARD
from loanwhiz.governance.evidence_pack import (
    EvidencePackLogger,
    GovernanceEvidencePack,
    ToolCallRecord,
)
from loanwhiz.governance.finos_conformance import (
    FINOS_CONTROL_CATALOGUE,
    FINOS_RISK_CATALOGUE,
    FinosControl,
    finos_conformance_summary,
    is_framework_conformant,
    primitive_conformance,
)

__all__ = [
    "ToolCallRecord",
    "GovernanceEvidencePack",
    "EvidencePackLogger",
    "AGENT_MODEL_CARD",
    "FinosControl",
    "FINOS_CONTROL_CATALOGUE",
    "FINOS_RISK_CATALOGUE",
    "finos_conformance_summary",
    "primitive_conformance",
    "is_framework_conformant",
]
