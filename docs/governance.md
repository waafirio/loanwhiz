# LoanWhiz Governance Pattern Document

> How LoanWhiz implements the [FINOS AI Governance Framework](https://github.com/finos/ai-governance-framework).
> See also: [docs/model-card.md](model-card.md) · [docs/data-card.md](data-card.md)

---

## Overview

LoanWhiz is a structured finance agent framework. Its extraction pipeline (Gemini 2.5 Pro via Vertex AI) and computational primitives operate on sensitive financial documents and data. Governance is not a post-hoc addition; it is baked into the primitive interface specification.

This document describes how LoanWhiz implements the FINOS AI Governance Framework's key patterns: audit trail, confidence scoring, citations, replayability, human review routing, and model risk classification.

**Reference:** [https://github.com/finos/ai-governance-framework](https://github.com/finos/ai-governance-framework)

---

## 1. Audit Trail

### Pattern

Every LoanWhiz primitive call produces an `AuditEntry`. No primitive may return an output without also producing a corresponding audit entry.

### AuditEntry Schema

```python
@dataclass
class AuditEntry:
    primitive_name: str       # e.g. "waterfall_extractor", "covenant_monitor"
    primitive_version: str    # semver, e.g. "0.1.0"
    model_name: str           # e.g. "gemini-2.5-pro"
    model_version: str        # model version string from the API response
    input_hash: str           # SHA-256 of the canonical serialisation of inputs
    output_hash: str          # SHA-256 of the canonical serialisation of outputs
    timestamp: str            # ISO 8601 UTC, e.g. "2026-06-03T12:34:56Z"
    duration_ms: int          # wall-clock time for the primitive call
    confidence: float         # in [0.0, 1.0]; see Confidence Scoring section
    citations: list[Citation] # source document citations for all extracted facts
    human_review_required: bool  # True when confidence < 0.7
    operator_id: str | None   # set when the audit entry is associated with a
                              # specific operator session (optional)
```

### Storage

Audit entries are serialised to newline-delimited JSON (`.jsonl`) in the `audit/` directory of the deal's working directory, one file per primitive run. The file name follows the pattern `<timestamp>_<primitive>_<input_hash[:8]>.jsonl`.

Audit entries are append-only. They are never deleted or overwritten. A deal's full audit trail is the concatenation of all `.jsonl` files in its `audit/` directory in timestamp order.

### Invariants

- An output without a corresponding audit entry is invalid and must not be used.
- Audit entries must be written before the output is returned to the caller.
- An audit entry's `input_hash` must be reproducible: given the same inputs, the same hash must be produced.

---

## 2. Confidence Scoring

### Pattern

Confidence scoring is mandatory on all LoanWhiz primitives. A primitive that does not produce a confidence score does not conform to the primitive interface specification.

### Scoring Method (Extraction Pipeline)

Extraction confidence is a **real coverage metric**, derived directly from
what the pipeline actually resolved against the source document — not a
synthetic blend or a bare LLM self-rating.

| Signal | Where it is computed | What it measures |
|---|---|---|
| **Deal-model completeness** | `extraction/assembler.py` — `completeness_score = ‖expected ∩ found‖ / ‖expected‖` | Fraction of the expected key SF sections (waterfall, definitions, triggers, tranches) actually located and extracted from the prospectus. |
| **Per-waterfall coverage** | `extraction/waterfall_extractor.py` — `extraction_confidence = non_empty_recipients / len(steps)` | Fraction of a waterfall's ordered steps that resolved to a concrete recipient (rather than an unparsed prose stub). |

These are concrete ratios over the extracted artefact, so a thin extraction
scores low *because it is thin*. On the validated Green Lion 2026-1 deal the
deal-model `completeness_score` is **0.75** (3 of the 4 expected sections
resolved; the definitions graph extracts 0 terms — see the model card's
extraction-results table).

> The pipeline does **not** apply a fixed `0.40·coverage + 0.30·resolution +
> 0.30·llm_self_score` weighting. Confidence is the coverage ratios above; the
> LLM is not asked to self-grade the final score.

### Scoring Method (Computational Primitives)

For non-extraction primitives (waterfall runner, covenant monitor), each
`PrimitiveResult` carries a `confidence` derived from the run itself —
input completeness (fraction of expected input fields populated) and
output coverage (fraction of expected output items produced and passing
the primitive's internal consistency checks). This per-primitive
confidence is what the agent's governance evidence pack aggregates (§2,
Agent-Query Scoring).

### Scoring Method (Agent Query / Evidence Pack)

When the LangGraph agent answers a query it emits a **governance evidence
pack** (`governance/evidence_pack.py`) whose confidence fields are derived,
not asserted:

- `aggregate_confidence = min(per-tool confidence)` over the tools the
  agent actually called (`1.0` for a no-tool answer) — a conservative
  floor, so any single low-confidence primitive pulls the whole answer down.
- `human_review_required = aggregate_confidence < 0.70`.
- `all_citations` is the order-preserving deduplicated union of the tool
  calls' own citations (no dropped or invented sources).
- `finos_compliant` MEANS **FINOS framework conformance**: it is the
  conjunction of (a) a real per-pack consistency check
  (`_check_finos_compliant`) over the above — every per-tool confidence a
  valid probability, the aggregate equal to the `min`, the citation trail
  exactly the dedup union, and the review flag matching the threshold rule —
  and (b) LoanWhiz conforming to the FINOS control catalogue
  (`finos_conformance.is_framework_conformant`, see §9). It is no longer a
  hardcoded `True`; a pack with inconsistent evidence, or a framework
  non-conformance, is reported as non-compliant. The pack also carries a
  `finos_conformance` summary explaining the boolean.

### Human Review Routing

| Confidence | Required action |
|---|---|
| ≥ 0.85 | Output may proceed; review recommended |
| 0.70 – 0.84 | Output may proceed; review required before production use |
| < 0.70 | **Human review mandatory.** `human_review_required = True` in the `AuditEntry`. Output must not be used in calculations or reports without a sign-off from a qualified reviewer. |

Low-confidence outputs are not suppressed — they are returned with the `human_review_required` flag set and must be routed through a human review step in the calling workflow before use. Suppressing rather than flagging would hide uncertainty; flagging preserves the analyst's ability to correct the output.

---

## 3. Citations

### Pattern

All LoanWhiz extractions must cite their source. A citation is an assertion that a specific extracted fact is grounded in a specific location in the source document.

### Citation Schema

```python
@dataclass
class Citation:
    document_name: str    # e.g. "green-lion-2026-1-prospectus.pdf"
    document_version: str # content hash of the source document
    section: str          # e.g. "5.2 Revenue Priority of Payments"
    page: int | None      # page number in the source PDF (None if not determinable)
    char_offset: int | None  # character offset in the extracted markdown
    verbatim_excerpt: str    # verbatim text from the source that supports the claim
```

### Requirements

- Every item in a waterfall, every defined term, every trigger threshold, and every extracted fact must carry at least one `Citation`.
- The `verbatim_excerpt` must be copied directly from the source document text without paraphrase. This is the primary auditability mechanism.
- If a fact cannot be cited to a specific source location, its confidence contribution is reduced and a note is added to the `AuditEntry`.

### Why Verbatim Excerpts

Verbatim excerpts allow:
1. A reviewer to locate the source text independently
2. A later audit to detect if the source document has been amended
3. A legal review to verify that the extraction faithfully represents the document

Paraphrased citations are not acceptable because they introduce a second layer of interpretation that cannot be independently verified.

---

## 4. Replayability

### Pattern

Any extraction result must be reproducible given the same inputs and model version.

### Implementation

- The `AuditEntry.input_hash` (SHA-256 of the canonical serialisation of all inputs, including the source document content hash and all parameters) uniquely identifies the extraction request.
- The `AuditEntry.model_version` records the exact model version returned by the Vertex AI API.
- Given the same `input_hash` and `model_version`, a new extraction run will produce outputs that are semantically equivalent, though not byte-identical due to LLM non-determinism.

### Limitations

LLMs are inherently non-deterministic. Temperature=0 sampling reduces but does not eliminate variance. Replayability means that the same extraction request will produce outputs with the same structure and coverage, not necessarily the same verbatim text. Any material difference between two replay runs is a signal that the extraction is operating in a low-confidence region for that section.

---

## 5. Human Review Routing

### Policy

Outputs with confidence < 0.7 must be reviewed by a qualified structured finance professional before use in any calculation, report, or decision. This is not a recommendation; it is a requirement of the LoanWhiz primitive interface specification.

### Implementation

1. The calling workflow checks `AuditEntry.human_review_required` on every primitive output.
2. If `True`, the output is routed to a human review queue (implemented by the calling application).
3. The human reviewer signs off by recording their approval in the audit trail (an appended `ReviewEntry` to the deal's audit log).
4. Only after a `ReviewEntry` is present for the output's `input_hash` may the output proceed to calculation or reporting steps.

### What Reviewers Are Checking

For extraction outputs flagged for review, the reviewer confirms:
- All waterfall steps are present and correctly ordered
- Key defined terms resolve to the correct definitions
- Numeric thresholds match the prospectus source
- Any natural-language conditional strings are correctly interpreted

For computational outputs flagged for review, the reviewer confirms:
- Input data is complete and correctly normalised
- Output values are internally consistent
- Edge cases (empty pools, zero collections periods) are handled correctly

---

## 6. Model Risk Classification

### Classification

The LoanWhiz Extraction Pipeline is classified as a **decision-support tool**. It is not an autonomous decision-maker.

| Attribute | Value |
|---|---|
| **Risk tier** | Medium (decision-support with mandatory human review for low-confidence outputs) |
| **Autonomy level** | None — all outputs require human review before production use |
| **Reversibility** | High — extracted outputs are records; no irreversible actions are taken by the pipeline |
| **Domain** | Structured finance (ABS/RMBS prospectus analysis) |
| **Regulatory context** | Not a regulated activity; outputs are analytical inputs only |

### Model Risk Mitigations

| Risk | Mitigation |
|---|---|
| Hallucinated extraction facts | Verbatim citation requirement; reviewer signs off before use |
| LLM non-determinism | Input hash + model version enable replay comparison |
| Low coverage extractions | Confidence scoring surfaces incomplete extractions before use |
| Scope creep (tool used beyond intended domain) | Model card's out-of-scope uses section; training for intended users |
| Stale extractions (source document amended) | Document content hash in citations; version tracking in deal model |
| Single-point-of-failure (one model) | Confidence scoring + human review acts as second opinion |

---

## 7. Data Provenance

### Pattern

Governance does not stop at the model — it extends to **where the data came from**. Every normalised ESMA tape records which ingestion path produced it, and that provenance is carried through to the agent's evidence pack so an auditor can see, per answer, where the underlying loan tape was sourced.

### Direct read — the canonical ingestion path

LoanWhiz's canonical (and only) ESMA tape ingestion path is the **direct read**: a loan tape is loaded straight from its source URL. See [`docs/tape-ingestion.md`](tape-ingestion.md) for the full model.

- A tape URL (HuggingFace CSV/parquet, local `file://`) is read directly by `esma_tape_normaliser._load_tape`, which dispatches on the file extension (`.parquet`/`.pq` → `pandas.read_parquet`, otherwise `pandas.read_csv`).
- The result is tagged `data_source="direct"` and carried through the evidence pack.

> **Note on deeploans.** [deeploans](https://github.com/Algoritmica-ai/deeploans) is Algoritmica's open-source, Apache-2.0 ESMA loan-level ETL — the hackathon organiser's own tool, credited as a project input. It is **not** on LoanWhiz's live ingestion path: the upstream backend is serve-only (BigQuery-backed, batch-ETL'd) and serves SME data, so it cannot ingest LoanWhiz's RMBS tapes on demand. LoanWhiz therefore reads tapes directly; deeploans is a decoupled upstream credit, not a runtime dependency.

### Recorded provenance

| Field | Where | Value |
|---|---|---|
| `EsmaTapeOutput.data_source` | `esma_tape_normaliser.py` | always `"direct"` — the tape was read directly from its source URL |
| `TapeAnalyticsPeriod.data_source` | `GET /deal/{id}/tape-analytics` | the same provenance, per reporting period |
| Tape citation excerpt | `Citation.excerpt` (`"… (ingested via direct)"`) | the human-readable provenance carried into the agent's deduplicated citation trail |

The Governance view and the chat panel's evidence slide-over surface this per answer, so the FINOS audit trail (§1) and citation trail (§3) now include honest data provenance — not just *what* the agent computed, but *where the data it computed on came from*.

---

## 8. Scope and Applicability

This governance framework applies to:

- The LoanWhiz Extraction Pipeline (Gemini 2.5 Pro, zero-shot)
- All LoanWhiz computational primitives (waterfall runner, covenant monitor, report verifier, cashflow projector, audit logger)
- All derivative agent workflows built on LoanWhiz primitives

It does not apply to:

- The internals of external data sources (the HuggingFace datasets the tapes are read from are governed by their own publishers) — though **which** source produced each tape *is* tracked, as data provenance (§7).
- The Gemini 2.5 Pro model itself (governed by Google's model policies)
- Client applications built on top of the LoanWhiz REST API (governed by the client application's own policies)

---

## 9. FINOS AI Governance Framework Conformance

LoanWhiz treats FINOS as a **real compliance target**, not a brand label. The
framework's full control catalogue is mapped to concrete LoanWhiz
implementations in **[`src/loanwhiz/governance/finos_conformance.py`](../src/loanwhiz/governance/finos_conformance.py)**
— the single source of truth that the code (`evidence_pack.finos_compliant`),
the API (`GET /governance/finos-conformance`), the model card, and the
Governance UI all read so they tell the same story.

`finos_compliant` on an evidence pack now MEANS framework conformance: it is the
conjunction of (a) the pack's own evidence being internally consistent and
(b) LoanWhiz conforming to the control catalogue below.

### Conformance posture

This is an **honest first-party self-assessment** against the framework's
published catalogue — exactly what the framework prescribes (a reasoned
applicability + conformance assessment, not a blanket "compliant" claim). Each
control is `satisfied`, `partial`, or `not_applicable` with a stated rationale.
`partial` and `not_applicable` are reasoned, bounded states (often deferring a
deployment-edge concern to the calling application per §8); they do not fail
conformance, but they are surfaced so the posture is never overstated. It is
*not* a third-party audit attestation.

The framework publishes **23 mitigation controls** (15 preventative
`AIR-PREV-*` + 8 detective `AIR-DET-*`) addressing 23 risks. LoanWhiz's current
mapping: **10 satisfied · 7 partial · 6 not applicable**.

### Preventative controls (`AIR-PREV-*`)

| Control | Title | Status | LoanWhiz evidence |
|---|---|---|---|
| AIR-PREV-002 | Data Filtering From External Knowledge Bases | partial | Typed ESMA/prospectus ingestion only; tape provenance recorded (§7) |
| AIR-PREV-003 | User/App/Model Firewalling/Filtering | n/a | Deployment-edge concern (§8) |
| AIR-PREV-005 | System Acceptance Testing | satisfied | Per-primitive validation harness + report verifier + test suites |
| AIR-PREV-006 | Data Quality & Classification/Sensitivity | satisfied | Coverage-derived extraction confidence (§2) |
| AIR-PREV-007 | Legal and Contractual Frameworks | partial | Apache-2.0 stack; deployment contracts are the institution's |
| AIR-PREV-008 | QoS and DDoS Prevention | n/a | Infrastructure-edge concern (§8) |
| AIR-PREV-010 | AI Model Version Pinning | satisfied | Pinned backbone; `model_used` + `AuditEntry.version` (§4) |
| AIR-PREV-012 | Role-Based Access Control for AI Data | partial | `AuditEntry.operator_id` substrate; RBAC is the caller's (§8) |
| AIR-PREV-014 | Encryption of AI Data at Rest | n/a | Storage-layer concern of the deployment (§8) |
| AIR-PREV-017 | AI Firewall Implementation | n/a | Overreach constrained via model card + fixed tool set instead |
| AIR-PREV-018 | Agent Authority Least Privilege | satisfied | Fixed, enumerated read-only tool set on the model card |
| AIR-PREV-019 | Tool Chain Validation and Sanitization | satisfied | Typed pydantic `BaseInput`/`PrimitiveResult` at every boundary |
| AIR-PREV-020 | MCP Server Security Governance | partial | `mcp/` wraps the same read-only primitives; host hardening is the deployment's |
| AIR-PREV-022 | Multi-Agent Isolation and Segmentation | n/a | Single LangGraph ReAct agent — no multi-agent topology |
| AIR-PREV-023 | Agentic System Credential Protection | partial | No end-user creds; packs store summaries/hashes, not raw inputs |

### Detective controls (`AIR-DET-*`)

| Control | Title | Status | LoanWhiz evidence |
|---|---|---|---|
| AIR-DET-001 | AI Data Leakage Prevention and Detection | partial | Audit trail stores input *summaries* + hashes, not raw inputs |
| AIR-DET-004 | AI System Observability | satisfied | `AuditEntry` per call + full evidence-pack tool-call trace (§1) |
| AIR-DET-009 | Alerting and Denial of Wallet / Spend Monitoring | n/a | Deployment-platform concern (§8) |
| AIR-DET-011 | Human Feedback Loop | satisfied | `human_review_required` routing below 0.70 (§5) |
| AIR-DET-013 | Citations and Source Traceability | satisfied | Verbatim citations; dedup union in the evidence pack (§3) |
| AIR-DET-015 | LLM-as-a-Judge / Automated Evaluation | satisfied | Report verifier re-checks figures vs reconstructed waterfall |
| AIR-DET-016 | Preserving Source Data Access Controls | partial | Provenance preserved per citation; upstream ACL enforcement is the caller's |
| AIR-DET-021 | Agent Decision Audit and Explainability | satisfied | Replayable reasoning trace + consistency check (§4) |

### Per-primitive conformance

Conformance is asserted **per-primitive**, not only at the aggregate evidence
pack. Every registered primitive satisfies a universal set by virtue of the
`Primitive` base contract (it returns a typed `PrimitiveResult` with a
confidence score, `Citation` list, and append-only `AuditEntry`):

- `AIR-DET-004` (observability), `AIR-DET-013` (citations),
  `AIR-DET-021` (decision audit), `AIR-PREV-010` (version pinning),
  `AIR-PREV-019` (tool-chain validation).

Individual primitives add controls on top — e.g. `report_verifier` →
`AIR-DET-015`, `covenant_monitor` → `AIR-DET-011`, `esma_tape_normaliser` →
`AIR-PREV-002`/`AIR-PREV-006`/`AIR-DET-016`. The full per-primitive map is
returned by `primitive_conformance()` and `GET /governance/finos-conformance`.

### Out-of-scope controls and why

Six controls are `not_applicable`. They are deployment-edge or topology
concerns that a self-hosted analytics library does not own:
firewalling/QoS/DDoS (`AIR-PREV-003`/`008`, `AIR-DET-009`), at-rest encryption
(`AIR-PREV-014`), AI-firewall management (`AIR-PREV-017`), and multi-agent
isolation (`AIR-PREV-022` — LoanWhiz runs a single agent). Marking these
honestly out-of-scope, with a reason, is itself the FINOS-faithful posture.

### Cross-references

| Surface | Where |
|---|---|
| Control catalogue (source of truth) | [`finos_conformance.py`](../src/loanwhiz/governance/finos_conformance.py) |
| `finos_compliant` derivation | [`evidence_pack.py`](../src/loanwhiz/governance/evidence_pack.py) |
| Conformance API | `GET /governance/finos-conformance` |
| Model card | [docs/model-card.md](model-card.md) |
| Data card | [docs/data-card.md](data-card.md) |

**Reference:** [https://air-governance-framework.finos.org](https://air-governance-framework.finos.org) · [https://github.com/finos/ai-governance-framework](https://github.com/finos/ai-governance-framework)
