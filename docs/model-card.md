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
- Building a definitions graph that resolves cross-references within a single deal's documentation (note: on the primary Green Lion 2026-1 extraction-quality reference this graph resolves 0 terms — see Performance)
- Assembling a machine-executable deal model as the foundation for downstream analytics (waterfall runner, covenant monitor, cashflow projector, report verifier)

**Expected deployment context:** Decision-support tool within a supervised analytical workflow. All extracted outputs are intended to be reviewed by a qualified professional before use in production calculations.

---

## Out-of-Scope Uses

The following uses are explicitly **out of scope** and are not supported:

- **Legal advice or legal document interpretation.** The pipeline extracts text and structure; it does not provide legal analysis or legal opinions.
- **Investment decisions.** Extracted deal models are analytical inputs, not investment recommendations. The pipeline does not assess creditworthiness, risk appetite, or suitability.
- **Regulatory compliance certification.** The pipeline is not a compliance tool. Outputs have not been validated against any regulatory reporting standard. They must not be used as evidence of regulatory compliance without independent review.
- **Cross-deal generalisation without validation.** The pipeline *runs* on 5 deals across 3 jurisdictions, but extraction quality varies and only the downstream waterfall engine on **Green Lion 2024-1** is validated to the cent against external published actuals. Extracted outputs on every other deal — especially the partially-extracted Italian (≈ 0.38) and Spanish (≈ 0.30) prospectuses — must be treated as unvalidated until independently verified.
- **Autonomous decision-making.** The pipeline is a decision-support tool. No output should be used to take automated financial action without human review.

---

## Performance

### Validation Dataset

The pipeline has been **run across 5 deals in 3 jurisdictions** (Dutch / Italian
/ Spanish RMBS — see [docs/data-card.md](data-card.md)), but extraction quality
and external validation differ sharply per deal and are reported honestly. The
primary extraction-quality reference is the **Green Lion 2026-1 B.V.** prospectus
(Dutch RMBS, Annex 2 ESMA format), detailed step-by-step below; the per-deal
completeness across the whole set is summarised in
[Cross-deal extraction completeness](#cross-deal-extraction-completeness).

Note the distinction the rest of this card relies on: **the *extraction*
pipeline's "validation" is about how completely it parses a prospectus into the
deal model. It is separate from the *downstream waterfall engine's* to-the-cent
reconciliation against published actuals** (Green Lion 2024-1's Notes & Cash) —
that engine validation is covered by `engine_validation_harness.py` and the
Validation view, not by this extraction model card.

### Extraction Results

| Section | Result | Notes |
|---|---|---|
| Revenue Priority of Payments (section 5.2) | Correct — 11 steps extracted | All steps correctly identified as ordered, conditionally-specified payment steps with citations |
| Redemption Priority of Payments | Partially extracted | Sequential ordering correct; some conditional branching requires review |
| Definitions section | Not populated on this deal | The definitions-graph extractor runs, but on the Green Lion 2026-1 prospectus it resolves **0 terms** into the cached model (`definitions: {}`). The capability exists; the count for this deal is zero. Waterfall steps therefore carry their conditional clauses as prose rather than resolved defined-term references. |
| Trigger and covenant thresholds | 3 triggers extracted | The cached model extracts **3** triggers — Class A PDL, Class B PDL, and reserve-fund shortfall — with numeric thresholds. Some monitoring conditions expressed in natural language still require human interpretation. |
| Credit enhancement structure | Partially extracted | Tranche subordination hierarchy correct; reserve account conditions require review |

### Summary

The extraction pipeline correctly resolves the primary waterfall structure of a Dutch RMBS prospectus in a single zero-shot pass. The Definitions section is the weakest surface — on this deal it resolves no terms into the model — and is the primary area requiring human review before the extracted model is used in production calculations.

### Cross-deal extraction completeness

The same extractor runs unchanged on all five registered deals. Completeness is
the real coverage metric from `extraction/assembler.py` (fraction of expected key
sections — waterfall, definitions, triggers, tranches — located). It degrades
honestly on the non-English prospectuses; the model is **not** claimed clean
where it is partial.

| Deal | Jurisdiction | Completeness | What the extractor resolved |
|---|---|---|---|
| Green Lion 2023-1 B.V. | Netherlands | **1.0** | Full waterfall (revenue/redemption/post-enforcement), 4 triggers |
| Green Lion 2024-1 B.V. | Netherlands | **0.925** | Full waterfall, 3 triggers (the deal the downstream engine validates to the cent) |
| Green Lion 2026-1 B.V. | Netherlands | **0.75** | Full waterfall, 3 triggers, **0 definitions** |
| Leone Arancio RMBS 2023-1 S.r.l. | Italy | **≈ 0.38** | Real *cited* triggers (performance trigger, PDL shortfall) + issuer covenants; **no waterfall** |
| Sol-Lion II RMBS Fondo de Titulización | Spain | **≈ 0.30** | Minimal — **no waterfall, no triggers** resolved into the model |

These map directly onto the capability matrix's `validated` / `ran` /
`not-applicable` cells (`GET /capability-matrix`, Showcase view): the cross-deal
story is "the same governed primitives ran on every deal", **not** "every deal was
validated". Exactly one cell is validated (Green Lion 2024-1's engine vs. its own
published Notes & Cash); the non-English deals are honest `ran` cells with real
reasons for their gaps.

---

## Limitations

1. **Partial cross-deal coverage.** The pipeline runs on 5 deals across 3 jurisdictions (Dutch / Italian / Spanish RMBS), but extraction completeness ranges from clean (Dutch, 0.75–1.0) to partial (Italian ≈ 0.38) to minimal (Spanish ≈ 0.30), and only Green Lion 2024-1 is externally validated (engine to the cent). Other asset classes (CLOs, CMBS, US RMBS, ABS) are untested. The capability matrix (1 validated / 9 ran / 15 not-applicable) is the honest source of truth — never read the coverage as "validated across all deals".

2. **Cross-reference resolution.** Prospectus definitions frequently reference other defined terms. The pipeline resolves one level of cross-reference; deeply nested chains (term A → term B → term C) may not resolve fully and require human review.

3. **Table extraction from scanned PDFs.** Docling's table extraction degrades significantly on scanned (image-based) PDFs. Deal models extracted from scanned documents should be treated as low-confidence and reviewed line-by-line.

4. **Jurisdiction- and language-specific extraction.** Extraction is strongest on the Dutch (English-language) prospectuses. The Italian (Leone Arancio) and Spanish (Sol-Lion II) prospectuses extract only partially — cited triggers at best, no waterfall — confirming that civil-law drafting style and non-English source text materially degrade extraction quality. The Definitions section resolves 0 terms even on the Dutch deals.

5. **Conditional waterfall logic.** Complex conditional branches in payment waterfalls (e.g. "subject to the PDL being zero") are extracted as natural language strings rather than executable boolean logic unless explicitly parsed.

6. **No version history.** The pipeline extracts from a single document version. Amendments, supplements, and side letters are not automatically reconciled with the base prospectus.

7. **LLM non-determinism.** Gemini 2.5 Pro's outputs are non-deterministic. Two extraction runs on the same document may produce marginally different outputs. The pipeline mitigates this by scoping each LLM call to a specific section and using structured output schemas.

---

## Confidence Scoring

Every extraction primitive produces a confidence score in `[0.0, 1.0]`. The
score is a **real coverage metric** over what was actually extracted, not a
fixed weighted blend:

| Signal | Computed as | What it measures |
|---|---|---|
| **Deal-model completeness** | `‖expected ∩ found‖ / ‖expected‖` (`extraction/assembler.py`) | Fraction of the expected key sections (waterfall, definitions, triggers, tranches) located and extracted. On Green Lion 2026-1 this is **0.75** (3 of 4; the definitions graph extracts 0 terms). |
| **Per-waterfall coverage** | `non_empty_recipients / len(steps)` (`extraction/waterfall_extractor.py`) | Fraction of a waterfall's ordered steps that resolved to a concrete recipient. |

The pipeline does **not** apply a `0.40·coverage + 0.30·resolution +
0.30·llm_self_score` weighting, and does not fold an LLM self-rating into the
final score. See [docs/governance.md](governance.md) §2 for the full
derivation, including how the agent's evidence pack aggregates per-tool
confidence as `min(...)`.

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
