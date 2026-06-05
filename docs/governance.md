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
- `finos_compliant` is the result of a **real consistency check**
  (`_check_finos_compliant`) over the above — every per-tool confidence a
  valid probability, the aggregate equal to the `min`, the citation trail
  exactly the dedup union, and the review flag matching the threshold
  rule. It is no longer a hardcoded `True`; a pack with inconsistent
  evidence is reported as non-compliant.

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

## 7. Scope and Applicability

This governance framework applies to:

- The LoanWhiz Extraction Pipeline (Gemini 2.5 Pro, zero-shot)
- All LoanWhiz computational primitives (waterfall runner, covenant monitor, report verifier, cashflow projector, audit logger)
- All derivative agent workflows built on LoanWhiz primitives

It does not apply to:

- External data sources used as inputs (deeploans, HuggingFace datasets)
- The Gemini 2.5 Pro model itself (governed by Google's model policies)
- Client applications built on top of the LoanWhiz REST API (governed by the client application's own policies)

---

## 8. FINOS AI Governance Framework Alignment

| FINOS Pattern | LoanWhiz Implementation |
|---|---|
| Audit trail | `AuditEntry` per primitive call (§1) |
| Confidence scoring | Coverage-derived confidence on every primitive; agent answers aggregate it as `min(per-tool confidence)` (§2) |
| Citations | Verbatim citations on all extracted facts (§3) |
| Replayability | Input hash + model version in every `AuditEntry` (§4) |
| Human review routing | `human_review_required` flag; confidence < 0.7 triggers mandatory review (§5) |
| Model risk classification | Decision-support tier; no autonomous decisions (§6) |
| Model card | [docs/model-card.md](model-card.md) |
| Data card | [docs/data-card.md](data-card.md) |

**Reference:** [https://github.com/finos/ai-governance-framework](https://github.com/finos/ai-governance-framework)
