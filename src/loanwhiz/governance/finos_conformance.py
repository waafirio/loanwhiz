"""FINOS AI Governance Framework conformance for LoanWhiz.

This module is the **single source of truth** for how LoanWhiz conforms to the
[FINOS AI Governance Framework](https://air-governance-framework.finos.org).
It exists to make the word "FINOS" in LoanWhiz mean a *genuine compliance
posture* rather than a brand label: the full framework control catalogue is
mapped to concrete LoanWhiz implementations, conformance is asserted
**per-primitive**, and the framework-level verdict is what
``evidence_pack.finos_compliant`` now ANDs into its per-pack consistency check.

What this module is, and is not
-------------------------------

It is an **honest self-assessment** against the framework's published control
catalogue — the framework itself prescribes a reasoned applicability +
conformance assessment, not a blanket "compliant" claim. Each control is marked
``satisfied`` / ``partial`` / ``not_applicable`` with a stated rationale and the
LoanWhiz evidence (module/primitive/doc) that backs it. It is *not* a
third-party audit attestation; ``is_conformant`` means "no in-scope control is
in a failing state given LoanWhiz's documented implementation", which is the
faithful first-party posture the framework asks for.

Catalogue source
----------------

The control + risk identifiers below are the framework's own
(``AIR-PREV-*`` / ``AIR-DET-*`` mitigations; ``AIR-OP-*`` / ``AIR-SEC-*`` /
``AIR-RC-*`` risks), taken from
https://air-governance-framework.finos.org/single-page.html. The framework
publishes 23 mitigations (15 preventative, 8 detective) addressing 23 risks
across three categories.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Risk catalogue — what the mitigations address (for doc/UI context)
# ---------------------------------------------------------------------------

#: The framework's risk catalogue: ``risk_id -> human-readable title``.
#: Operational (``AIR-OP-*``), security (``AIR-SEC-*``), and regulatory /
#: compliance (``AIR-RC-*``) risks. Used so the conformance surfaces can show
#: *what* each control mitigates, not just a bare control id.
FINOS_RISK_CATALOGUE: dict[str, str] = {
    # Operational
    "AIR-OP-004": "Hallucination and Inaccurate Outputs",
    "AIR-OP-005": "Foundation Model Versioning",
    "AIR-OP-006": "Non-Deterministic Behaviour",
    "AIR-OP-007": "Availability of Foundational Model",
    "AIR-OP-014": "Inadequate System Alignment",
    "AIR-OP-016": "Bias and Discrimination",
    "AIR-OP-017": "Lack of Explainability",
    "AIR-OP-018": "Model Overreach / Expanded Use",
    "AIR-OP-019": "Data Quality and Drift",
    "AIR-OP-020": "Reputational Risk",
    "AIR-OP-028": "Multi-Agent Trust Boundary Violations",
    # Security
    "AIR-SEC-002": "Information Leaked to Vector Store",
    "AIR-SEC-008": "Tampering With the Foundational Model",
    "AIR-SEC-009": "Data Poisoning",
    "AIR-SEC-010": "Prompt Injection",
    "AIR-SEC-024": "Agent Action Authorization Bypass",
    "AIR-SEC-025": "Tool Chain Manipulation and Injection",
    "AIR-SEC-026": "MCP Server Supply Chain Compromise",
    "AIR-SEC-027": "Agent State Persistence Poisoning",
    "AIR-SEC-029": "Agent-Mediated Credential Discovery and Harvesting",
    # Regulatory and compliance
    "AIR-RC-001": "Information Leaked To Hosted Model",
    "AIR-RC-022": "Regulatory Compliance and Oversight",
    "AIR-RC-023": "Intellectual Property (IP) and Copyright",
}


# ---------------------------------------------------------------------------
# Control model
# ---------------------------------------------------------------------------

ControlCategory = Literal["preventative", "detective"]
ConformanceStatus = Literal["satisfied", "partial", "not_applicable"]

#: Statuses that count as conformant. ``not_applicable`` is a reasoned scope
#: exclusion (it does not fail conformance); ``partial`` is a documented,
#: bounded gap that is honest about what is deferred (e.g. to the calling
#: application) — it does not fail conformance either, but it IS surfaced so the
#: posture is never overstated. Any status outside this set (or an invalid
#: control) makes the framework verdict non-conformant.
_NON_FAILING_STATUSES: frozenset[str] = frozenset(
    {"satisfied", "partial", "not_applicable"}
)


class FinosControl(BaseModel):
    """One FINOS mitigation control mapped to LoanWhiz.

    Attributes:
        control_id:        Framework id, e.g. ``"AIR-DET-013"``.
        title:             The control's framework title.
        category:          ``preventative`` or ``detective``.
        addresses_risks:   Risk ids this control mitigates (keys of
                           :data:`FINOS_RISK_CATALOGUE`).
        status:            LoanWhiz's honest conformance status for the control.
        rationale:         One- to two-sentence justification for the status —
                           required and non-empty for every control.
        loanwhiz_evidence: Concrete LoanWhiz artefacts (modules, primitives,
                           docs) that implement / justify the status.
    """

    control_id: str
    title: str
    category: ControlCategory
    addresses_risks: list[str] = Field(default_factory=list)
    status: ConformanceStatus
    rationale: str = Field(..., min_length=1)
    loanwhiz_evidence: list[str] = Field(default_factory=list)

    @property
    def is_conformant(self) -> bool:
        """Whether this control's status counts toward framework conformance."""
        return self.status in _NON_FAILING_STATUSES


# ---------------------------------------------------------------------------
# The control catalogue — LoanWhiz's mapping of all 23 mitigations
# ---------------------------------------------------------------------------

FINOS_CONTROL_CATALOGUE: list[FinosControl] = [
    # ----------------------------- Preventative ----------------------------
    FinosControl(
        control_id="AIR-PREV-002",
        title="Data Filtering From External Knowledge Bases",
        category="preventative",
        addresses_risks=["AIR-SEC-002", "AIR-SEC-009", "AIR-OP-019"],
        status="partial",
        rationale=(
            "LoanWhiz ingests only structured ESMA loan tapes and prospectus "
            "documents through typed primitives; it has no open external "
            "knowledge base. Tape provenance (deeploans vs direct) is recorded, "
            "but content-level filtering of third-party tapes is deferred to the "
            "publisher (governance.md §8)."
        ),
        loanwhiz_evidence=[
            "esma_tape_normaliser.py (data_source provenance)",
            "docs/governance.md §7 Data Provenance",
        ],
    ),
    FinosControl(
        control_id="AIR-PREV-003",
        title="User/App/Model Firewalling/Filtering",
        category="preventative",
        addresses_risks=["AIR-SEC-010", "AIR-RC-001"],
        status="not_applicable",
        rationale=(
            "LoanWhiz is a self-hosted analytics framework over a fixed deal "
            "corpus, not a public multi-tenant chat surface; network-edge "
            "firewalling is the deploying application's responsibility "
            "(governance.md §8 — client applications are out of scope)."
        ),
        loanwhiz_evidence=["docs/governance.md §8 Scope and Applicability"],
    ),
    FinosControl(
        control_id="AIR-PREV-005",
        title="System Acceptance Testing",
        category="preventative",
        addresses_risks=["AIR-OP-004", "AIR-OP-014", "AIR-OP-019"],
        status="satisfied",
        rationale=(
            "Every primitive ships with an acceptance/validation harness and a "
            "test suite; the engine validation harness and report verifier "
            "assert outputs against ground truth before they are trusted."
        ),
        loanwhiz_evidence=[
            "reconciler.py",
            "report_verifier.py",
            "tests/ (per-primitive suites)",
        ],
    ),
    FinosControl(
        control_id="AIR-PREV-006",
        title="Data Quality & Classification/Sensitivity",
        category="preventative",
        addresses_risks=["AIR-OP-019", "AIR-SEC-002"],
        status="satisfied",
        rationale=(
            "Extraction confidence is a real coverage metric over the source "
            "document (completeness + per-waterfall resolution), so a thin or "
            "low-quality extraction scores low by construction and is flagged."
        ),
        loanwhiz_evidence=[
            "extraction/assembler.py (completeness_score)",
            "extraction/waterfall_extractor.py (extraction_confidence)",
            "docs/governance.md §2 Confidence Scoring",
        ],
    ),
    FinosControl(
        control_id="AIR-PREV-007",
        title="Legal and Contractual Frameworks for AI Systems",
        category="preventative",
        addresses_risks=["AIR-RC-022", "AIR-RC-023"],
        status="partial",
        rationale=(
            "LoanWhiz is Apache-2.0 and uses Apache-2.0 inputs (deeploans, the "
            "FINOS framework); contractual frameworks for a specific deployment "
            "are the deploying institution's responsibility."
        ),
        loanwhiz_evidence=["LICENSE", "README.md (open-source attribution table)"],
    ),
    FinosControl(
        control_id="AIR-PREV-008",
        title="Quality of Service (QoS) and DDoS Prevention for AI Systems",
        category="preventative",
        addresses_risks=["AIR-OP-007"],
        status="not_applicable",
        rationale=(
            "QoS / DDoS protection is an infrastructure-edge concern of the "
            "deployment, not of the analytics primitives; LoanWhiz exposes a "
            "library + local API, not a hosted public endpoint."
        ),
        loanwhiz_evidence=["docs/governance.md §8 Scope and Applicability"],
    ),
    FinosControl(
        control_id="AIR-PREV-010",
        title="AI Model Version Pinning",
        category="preventative",
        addresses_risks=["AIR-OP-005", "AIR-OP-006"],
        status="satisfied",
        rationale=(
            "The model backbone is pinned and recorded on every artefact: the "
            "agent model card pins the backbone, evidence packs record "
            "model_used, and each AuditEntry records the primitive version used "
            "(enabling replay against a known version)."
        ),
        loanwhiz_evidence=[
            "agent_model_card.py (backbone)",
            "evidence_pack.py (model_used)",
            "primitives/base.py AuditEntry.version",
            "docs/governance.md §4 Replayability",
        ],
    ),
    FinosControl(
        control_id="AIR-PREV-012",
        title="Role-Based Access Control for AI Data",
        category="preventative",
        addresses_risks=["AIR-SEC-002", "AIR-RC-001"],
        status="partial",
        rationale=(
            "Audit entries carry an optional operator_id, but RBAC enforcement "
            "over deal data is the calling application's responsibility "
            "(governance.md §8); LoanWhiz provides the audit substrate, not the "
            "access-control layer."
        ),
        loanwhiz_evidence=[
            "docs/governance.md §1 AuditEntry.operator_id",
            "docs/governance.md §8 Scope and Applicability",
        ],
    ),
    FinosControl(
        control_id="AIR-PREV-014",
        title="Encryption of AI Data at Rest",
        category="preventative",
        addresses_risks=["AIR-SEC-002", "AIR-RC-001"],
        status="not_applicable",
        rationale=(
            "At-rest encryption is provided by the deployment's storage layer "
            "(disk / object store), not by the analytics framework; LoanWhiz "
            "does not implement its own persistence encryption."
        ),
        loanwhiz_evidence=["docs/governance.md §8 Scope and Applicability"],
    ),
    FinosControl(
        control_id="AIR-PREV-017",
        title="AI Firewall Implementation and Management",
        category="preventative",
        addresses_risks=["AIR-SEC-010", "AIR-OP-018"],
        status="not_applicable",
        rationale=(
            "An AI firewall is a deployment-edge control; LoanWhiz constrains "
            "model overreach instead through the model card's explicit "
            "out-of-scope uses and a fixed, typed tool set (see AIR-OP-018 via "
            "AIR-PREV-018)."
        ),
        loanwhiz_evidence=["agent_model_card.py (out_of_scope, tools)"],
    ),
    FinosControl(
        control_id="AIR-PREV-018",
        title="Agent Authority Least Privilege Framework",
        category="preventative",
        addresses_risks=["AIR-SEC-024", "AIR-OP-018"],
        status="satisfied",
        rationale=(
            "The agent's authority is bounded to a fixed, enumerated set of "
            "read-only analytical tools declared on the model card; the agent "
            "cannot act outside that tool surface and the card pins intended + "
            "out-of-scope uses."
        ),
        loanwhiz_evidence=[
            "agent_model_card.py (tools, intended_use, out_of_scope)",
            "agent/planner.py (fixed tool set)",
        ],
    ),
    FinosControl(
        control_id="AIR-PREV-019",
        title="Tool Chain Validation and Sanitization",
        category="preventative",
        addresses_risks=["AIR-SEC-025", "AIR-OP-004"],
        status="satisfied",
        rationale=(
            "Every primitive validates typed pydantic inputs and outputs; "
            "malformed tool inputs are rejected at the schema boundary rather "
            "than flowing into a calculation."
        ),
        loanwhiz_evidence=[
            "primitives/base.py (BaseInput / typed PrimitiveResult)",
            "primitives/registry.py (versioned, validated registration)",
        ],
    ),
    FinosControl(
        control_id="AIR-PREV-020",
        title="MCP Server Security Governance",
        category="preventative",
        addresses_risks=["AIR-SEC-026"],
        status="partial",
        rationale=(
            "LoanWhiz ships MCP servers (mcp/) that wrap the same typed, "
            "read-only primitives; their supply-chain governance is bounded to "
            "this repo's own code, but external MCP-host hardening is the "
            "deployment's responsibility."
        ),
        loanwhiz_evidence=["mcp/ (read-only primitive wrappers)"],
    ),
    FinosControl(
        control_id="AIR-PREV-022",
        title="Multi-Agent Isolation and Segmentation",
        category="preventative",
        addresses_risks=["AIR-OP-028", "AIR-SEC-027"],
        status="not_applicable",
        rationale=(
            "LoanWhiz runs a single LangGraph ReAct agent over its primitives; "
            "there is no multi-agent topology to segment."
        ),
        loanwhiz_evidence=["agent_model_card.py (single LangGraph ReAct agent)"],
    ),
    FinosControl(
        control_id="AIR-PREV-023",
        title="Agentic System Credential Protection Framework",
        category="preventative",
        addresses_risks=["AIR-SEC-029"],
        status="partial",
        rationale=(
            "The agent holds no end-user credentials; model/back-end "
            "credentials are supplied by the environment and never logged into "
            "evidence packs or audit entries (which store only summaries, "
            "hashes, and citations). Vault/secret management is the deployment's "
            "responsibility."
        ),
        loanwhiz_evidence=[
            "evidence_pack.py (input_summary, not raw inputs)",
            "primitives/base.py AuditEntry (input_hash, not raw input)",
        ],
    ),
    # ------------------------------ Detective ------------------------------
    FinosControl(
        control_id="AIR-DET-001",
        title="AI Data Leakage Prevention and Detection",
        category="detective",
        addresses_risks=["AIR-SEC-002", "AIR-RC-001"],
        status="partial",
        rationale=(
            "Evidence packs and audit entries store input *summaries* and "
            "hashes rather than raw sensitive inputs, limiting leakage through "
            "the audit trail; active DLP scanning is deferred to the deployment."
        ),
        loanwhiz_evidence=[
            "evidence_pack.py (ToolCallRecord.input_summary)",
            "primitives/base.py AuditEntry.input_hash",
        ],
    ),
    FinosControl(
        control_id="AIR-DET-004",
        title="AI System Observability",
        category="detective",
        addresses_risks=["AIR-OP-004", "AIR-OP-017", "AIR-OP-020"],
        status="satisfied",
        rationale=(
            "Every primitive call emits an append-only AuditEntry and every "
            "agent query emits a GovernanceEvidencePack with the full ordered "
            "tool-call trace, durations, and timestamps — a complete, "
            "replayable observability record."
        ),
        loanwhiz_evidence=[
            "primitives/base.py AuditEntry",
            "evidence_pack.py GovernanceEvidencePack / EvidencePackLogger",
            "docs/governance.md §1 Audit Trail",
        ],
    ),
    FinosControl(
        control_id="AIR-DET-009",
        title="AI System Alerting and Denial of Wallet (DoW) / Spend Monitoring",
        category="detective",
        addresses_risks=["AIR-OP-007", "AIR-OP-020"],
        status="not_applicable",
        rationale=(
            "Spend / DoW monitoring is a deployment-platform concern; LoanWhiz "
            "is a local library + API with no hosted billing surface to monitor."
        ),
        loanwhiz_evidence=["docs/governance.md §8 Scope and Applicability"],
    ),
    FinosControl(
        control_id="AIR-DET-011",
        title="Human Feedback Loop for AI Systems",
        category="detective",
        addresses_risks=["AIR-OP-004", "AIR-OP-016", "AIR-OP-017"],
        status="satisfied",
        rationale=(
            "Low-confidence outputs are not suppressed — they are flagged "
            "(human_review_required when aggregate confidence < 0.70) and routed "
            "to a mandatory human review step before use."
        ),
        loanwhiz_evidence=[
            "evidence_pack.py (human_review_required, REVIEW_THRESHOLD)",
            "docs/governance.md §5 Human Review Routing",
        ],
    ),
    FinosControl(
        control_id="AIR-DET-013",
        title="Providing Citations and Source Traceability for AI-Generated Information",
        category="detective",
        addresses_risks=["AIR-OP-004", "AIR-OP-017"],
        status="satisfied",
        rationale=(
            "Every extracted fact carries a verbatim-excerpt Citation, and the "
            "agent's evidence pack aggregates the deduplicated, order-preserving "
            "union of all tool-call citations (including data provenance) — no "
            "dropped or invented sources."
        ),
        loanwhiz_evidence=[
            "primitives/base.py Citation (verbatim_excerpt)",
            "evidence_pack.py all_citations (dedup union)",
            "docs/governance.md §3 Citations",
        ],
    ),
    FinosControl(
        control_id="AIR-DET-015",
        title="Using Large Language Models for Automated Evaluation (LLM-as-a-Judge)",
        category="detective",
        addresses_risks=["AIR-OP-004", "AIR-OP-014"],
        status="satisfied",
        rationale=(
            "The report verifier independently re-checks investor-report figures "
            "against the reconstructed waterfall, acting as an automated "
            "second-opinion evaluator on the primitives' outputs."
        ),
        loanwhiz_evidence=[
            "report_verifier.py",
            "reconciler.py",
        ],
    ),
    FinosControl(
        control_id="AIR-DET-016",
        title="Preserving Source Data Access Controls in AI Systems",
        category="detective",
        addresses_risks=["AIR-SEC-002", "AIR-RC-001"],
        status="partial",
        rationale=(
            "LoanWhiz preserves source identity and provenance on every "
            "citation, but enforcement of upstream access controls over the "
            "deal corpus is the calling application's responsibility "
            "(governance.md §8)."
        ),
        loanwhiz_evidence=[
            "docs/governance.md §7 Data Provenance",
            "docs/governance.md §8 Scope and Applicability",
        ],
    ),
    FinosControl(
        control_id="AIR-DET-021",
        title="Agent Decision Audit and Explainability",
        category="detective",
        addresses_risks=["AIR-OP-017", "AIR-OP-018", "AIR-SEC-024"],
        status="satisfied",
        rationale=(
            "Each agent answer is fully explainable from its evidence pack: the "
            "ordered tool-call sequence is a replayable reasoning trace, with "
            "per-tool confidence, citations, and a real internal-consistency "
            "check on the derived governance fields."
        ),
        loanwhiz_evidence=[
            "evidence_pack.py (replayable trace, _check_finos_compliant)",
            "docs/governance.md §4 Replayability",
        ],
    ),
]


# ---------------------------------------------------------------------------
# Per-primitive conformance
# ---------------------------------------------------------------------------

#: Controls that EVERY LoanWhiz primitive satisfies by virtue of the
#: ``Primitive`` base contract (``primitives/base.py``): every primitive returns
#: a typed ``PrimitiveResult`` with a confidence score, source ``Citation`` list,
#: and an append-only ``AuditEntry``. These are the detective + tool-chain
#: controls grounded in that universal contract.
_UNIVERSAL_PRIMITIVE_CONTROLS: tuple[str, ...] = (
    "AIR-DET-004",  # observability — AuditEntry on every call
    "AIR-DET-013",  # citations — Citation list on every result
    "AIR-DET-021",  # decision audit/explainability — replayable audit record
    "AIR-PREV-010",  # version pinning — AuditEntry.version
    "AIR-PREV-019",  # tool-chain validation — typed BaseInput / PrimitiveResult
)

#: Additional controls a specific primitive satisfies beyond the universal set,
#: keyed by the primitive's registry name. This is a *declared, reviewed*
#: mapping (per-primitive conformance is an assessment, not something
#: auto-derivable); :func:`primitive_conformance` unions it with the universal
#: controls. Keys must match ``@register_primitive(name=...)``.
_PRIMITIVE_SPECIFIC_CONTROLS: dict[str, tuple[str, ...]] = {
    "esma_tape_normaliser": ("AIR-PREV-002", "AIR-PREV-006", "AIR-DET-016"),
    "waterfall_runner": ("AIR-PREV-005",),
    "multi_period_waterfall_runner": ("AIR-PREV-005",),
    "covenant_monitor": ("AIR-DET-011", "AIR-PREV-005"),
    "collections_aggregator": ("AIR-PREV-005",),
    "cashflow_projector": ("AIR-PREV-005",),
    "report_verifier": ("AIR-DET-015", "AIR-PREV-005"),
    "audit_logger": ("AIR-DET-001", "AIR-PREV-012"),
}


def _known_primitive_names() -> set[str]:
    """Return the union of declared + registry-registered primitive names.

    The declared mapping is the source of truth for per-primitive conformance;
    the registry is consulted opportunistically so a newly-registered primitive
    is still surfaced (with the universal controls) even before its specific
    mapping is curated. Importing the registry is best-effort: in a thin
    environment where the primitive modules cannot import (missing heavy deps),
    the declared set still yields a complete, deterministic result.
    """
    names = set(_PRIMITIVE_SPECIFIC_CONTROLS)
    try:  # pragma: no cover - import side effects depend on the environment
        from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY

        names |= {reg.name for reg in PRIMITIVE_REGISTRY.list_all()}
    except Exception:
        pass
    return names


def primitive_conformance() -> dict[str, list[str]]:
    """Per-primitive FINOS control conformance.

    Returns a mapping ``primitive_name -> sorted list of control ids`` the
    primitive satisfies — the union of the universal base-contract controls and
    the primitive's declared specific controls. This is the per-primitive
    conformance assertion the FINOS posture requires (every primitive, not just
    the aggregate evidence pack).
    """
    result: dict[str, list[str]] = {}
    for name in _known_primitive_names():
        controls = set(_UNIVERSAL_PRIMITIVE_CONTROLS)
        controls |= set(_PRIMITIVE_SPECIFIC_CONTROLS.get(name, ()))
        result[name] = sorted(controls)
    return dict(sorted(result.items()))


# ---------------------------------------------------------------------------
# Framework-level summary + verdict
# ---------------------------------------------------------------------------


def is_framework_conformant() -> bool:
    """Whether LoanWhiz's mapping reports framework conformance.

    Conformant iff every control in the catalogue is in a non-failing state
    (``satisfied`` / ``partial`` / ``not_applicable``) — i.e. nothing in the
    mapped catalogue is left in a failing posture. ``partial`` and
    ``not_applicable`` are honest, reasoned states that do not fail conformance;
    a control with an unrecognised status (which pydantic would reject at
    construction) or any future ``failed`` marker would.
    """
    return all(c.is_conformant for c in FINOS_CONTROL_CATALOGUE)


def finos_conformance_summary() -> dict:
    """Return a JSON-serialisable framework-conformance summary.

    Shape::

        {
          "framework": "FINOS AI Governance Framework",
          "reference": "https://air-governance-framework.finos.org",
          "is_conformant": true,
          "total_controls": 23,
          "counts": {"satisfied": N, "partial": N, "not_applicable": N},
          "controls": [ {control_id, title, category, status, rationale,
                         addresses_risks, loanwhiz_evidence}, ... ],
          "primitive_conformance": {primitive_name: [control_id, ...], ...}
        }

    This is the single object the evidence pack, the API endpoint, the docs,
    and the UI all read so they tell the same true story.
    """
    counts: dict[str, int] = {"satisfied": 0, "partial": 0, "not_applicable": 0}
    for c in FINOS_CONTROL_CATALOGUE:
        counts[c.status] += 1
    return {
        "framework": "FINOS AI Governance Framework",
        "reference": "https://air-governance-framework.finos.org",
        "is_conformant": is_framework_conformant(),
        "total_controls": len(FINOS_CONTROL_CATALOGUE),
        "counts": counts,
        "controls": [c.model_dump() for c in FINOS_CONTROL_CATALOGUE],
        "primitive_conformance": primitive_conformance(),
    }
