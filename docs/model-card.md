# Model Card: LoanWhiz Extraction Pipeline v0.1

> Governance artefact following FINOS AI Governance Framework templates.
> See also: [docs/governance.md](governance.md) · [docs/data-card.md](data-card.md)

---

## Model Identity

| Field | Value |
|---|---|
| **Model name** | LoanWhiz Extraction Pipeline v0.1 |
| **Model type** | LLM-powered structured extraction (zero-shot) |
| **Underlying model** | Gemini 2.5 Pro via Vertex AI |
| **Framework** | LoanWhiz v0.1 (Apache 2.0) |
| **Version date** | 2026-06-03 |
| **Maintainer** | LoanWhiz contributors |
| **License** | Apache 2.0 |

---

## Task

Extract machine-executable deal models from structured finance prospectus PDFs. The pipeline reads a prospectus (typically an ABS or RMBS offering document), locates key sections (Priority of Payments, Definitions, Covenant and Trigger thresholds, Credit Enhancement), and extracts their content into structured Python objects that can be executed, validated, and audited.

The primary artefact is a `deal_model.json` containing:

- **Waterfall** — ordered, conditionally-specified payment steps (Revenue Priority of Payments, Redemption Priority of Payments, Post-Enforcement Priority of Payments)
- **Definitions** — key-value store of defined terms with page citations
- **Triggers** — threshold values, monitoring conditions, breach logic
- **Tranches** — note classes, subordination structure, credit enhancement

---

## Training Data

**None.** The extraction pipeline is zero-shot. It uses Gemini 2.5 Pro's pre-trained capabilities without any fine-tuning, domain-specific training, or retrieval-augmented generation over a curated corpus. The model has not been trained, fine-tuned, or aligned on LoanWhiz data.

The pipeline uses:
- **Docling** (IBM, Apache 2.0) for structure-aware PDF-to-markdown conversion
- **Gemini 2.5 Pro** for LLM-based extraction from identified prospectus sections
- **Section routing** to scope each LLM call to the relevant portion of the document, reducing hallucination risk and token cost

---

## Intended Use

**Primary audience:** Structured finance professionals — analysts, portfolio managers, trustees, and compliance officers — who need to extract and operationalise deal rules from ABS and RMBS prospectuses.

**Intended tasks:**
- Extracting waterfall rules from Priority of Payments sections for computational execution
- Extracting covenant and trigger definitions with threshold values and monitoring conditions
- Building a definitions graph that resolves cross-references within a single deal's documentation
- Assembling a machine-executable deal model as the foundation for downstream analytics (waterfall runner, covenant monitor, cashflow projector, report verifier)

**Expected deployment context:** Decision-support tool within a supervised analytical workflow. All extracted outputs are intended to be reviewed by a qualified professional before use in production calculations.

---

## Out-of-Scope Uses

The following uses are explicitly **out of scope** and are not supported:

- **Legal advice or legal document interpretation.** The pipeline extracts text and structure; it does not provide legal analysis or legal opinions.
- **Investment decisions.** Extracted deal models are analytical inputs, not investment recommendations. The pipeline does not assess creditworthiness, risk appetite, or suitability.
- **Regulatory compliance certification.** The pipeline is not a compliance tool. Outputs have not been validated against any regulatory reporting standard. They must not be used as evidence of regulatory compliance without independent review.
- **Cross-deal generalisation without validation.** The pipeline has been validated on one deal (Green Lion 2026-1). Its outputs on other deals must be treated as unvalidated until independently verified.
- **Autonomous decision-making.** The pipeline is a decision-support tool. No output should be used to take automated financial action without human review.

---

## Performance

### Validation Dataset

Validated on the **Green Lion 2026-1 B.V.** prospectus (Dutch RMBS, Annex 2 ESMA format). See [docs/data-card.md](data-card.md) for full dataset details.

### Extraction Results

| Section | Result | Notes |
|---|---|---|
| Revenue Priority of Payments (section 5.2) | Correct — 11 steps extracted | All steps correctly identified as ordered, conditionally-specified payment steps with citations |
| Redemption Priority of Payments | Partially extracted | Sequential ordering correct; some conditional branching requires review |
| Definitions section | Requires review | Defined terms extracted; cross-reference resolution (defined-term-within-definition chains) may miss nested references |
| Trigger and covenant thresholds | Partially extracted | Numeric thresholds extracted correctly; some monitoring conditions expressed in natural language require human interpretation |
| Credit enhancement structure | Partially extracted | Tranche subordination hierarchy correct; reserve account conditions require review |

### Summary

The extraction pipeline correctly resolves the primary waterfall structure of a Dutch RMBS prospectus in a single zero-shot pass. The Definitions section and cross-reference resolution chain are the primary areas requiring human review before the extracted model is used in production calculations.

---

## Limitations

1. **Single-deal validation.** The pipeline has been validated on one deal (Green Lion 2026-1, Dutch RMBS). Performance on other deal types (CLOs, CMBS, US RMBS, ABS, synthetic securitisations) is untested.

2. **Cross-reference resolution.** Prospectus definitions frequently reference other defined terms. The pipeline resolves one level of cross-reference; deeply nested chains (term A → term B → term C) may not resolve fully and require human review.

3. **Table extraction from scanned PDFs.** Docling's table extraction degrades significantly on scanned (image-based) PDFs. Deal models extracted from scanned documents should be treated as low-confidence and reviewed line-by-line.

4. **Jurisdiction-specific language.** The Definitions section extraction has been tested on Dutch law governed documents. Civil law versus common law distinctions in drafting style may affect extraction quality.

5. **Conditional waterfall logic.** Complex conditional branches in payment waterfalls (e.g. "subject to the PDL being zero") are extracted as natural language strings rather than executable boolean logic unless explicitly parsed.

6. **No version history.** The pipeline extracts from a single document version. Amendments, supplements, and side letters are not automatically reconciled with the base prospectus.

7. **LLM non-determinism.** Gemini 2.5 Pro's outputs are non-deterministic. Two extraction runs on the same document may produce marginally different outputs. The pipeline mitigates this by scoping each LLM call to a specific section and using structured output schemas.

---

## Confidence Scoring

Every extraction primitive produces a confidence score in `[0.0, 1.0]`. The score is a weighted combination of three signals:

| Signal | Weight | Description |
|---|---|---|
| **Section coverage** | 40% | Fraction of expected sections found in the document (waterfall, definitions, triggers, tranches) |
| **Cross-reference resolution rate** | 30% | Fraction of defined-term references that were successfully resolved to a definition |
| **LLM self-assessment** | 30% | The model's own confidence estimate, elicited via structured output schema alongside the extraction result |

**Thresholds:**

| Score range | Interpretation | Required action |
|---|---|---|
| ≥ 0.85 | High confidence | Review recommended before production use |
| 0.70 – 0.84 | Medium confidence | Review required before production use |
| < 0.70 | Low confidence | Human review mandatory; do not use in calculations without sign-off |

Confidence scores are included in every `AuditEntry` produced by the pipeline. See [docs/governance.md](governance.md) for the full audit trail specification.

---

## Human Oversight

All extracted waterfall objects, trigger definitions, and deal model components should be reviewed by a qualified structured finance professional before use in production calculations. This requirement is not optional.

The pipeline is designed to accelerate extraction, not to replace expert judgement. Specific review checkpoints:

1. **Waterfall step completeness** — confirm all Priority of Payments steps are present and correctly ordered
2. **Defined-term resolution** — confirm that key terms used in the waterfall (e.g. "Available Distribution Amount") resolve to the correct definitions
3. **Trigger thresholds** — confirm numeric thresholds against the prospectus source
4. **Conditional logic** — review any natural-language conditional strings for executability

The pipeline's confidence scoring is designed to surface low-confidence extractions for mandatory human review before they reach any calculation or reporting step.

---

## Governance

This model card follows [FINOS AI Governance Framework](https://github.com/finos/ai-governance-framework) templates.

- **Audit trail:** Every primitive call produces an `AuditEntry` (input hash, timestamp, model version, duration, confidence score). See [docs/governance.md](governance.md).
- **Replayability:** Audit entries include model version and input hash, enabling reproduction of any extraction result.
- **Human review routing:** Outputs with confidence < 0.7 are flagged for mandatory human review before use in calculations.
- **Model risk classification:** Decision-support tool; not an autonomous decision-maker.

---

## Citation

If you use the LoanWhiz Extraction Pipeline in research or production, please cite:

```
LoanWhiz Extraction Pipeline v0.1 (2026). LoanWhiz contributors.
Apache 2.0. https://github.com/waafirio/loanwhiz
```
