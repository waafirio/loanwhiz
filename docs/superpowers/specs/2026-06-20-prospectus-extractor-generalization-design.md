# Prospectus extractor generalization — design

**Date:** 2026-06-20
**Status:** Design (mechanism locked: LLM-semantic section routing). Spec for Epic 3 children #273 (generalization) + #274 (non-English IT/ES).
**Phase:** 3 (extraction layer — the long pole). Companion to `2026-06-20-report-extractor-design.md`.

Fills the canonical contract: `2026-06-20-canonical-domain-schema-design.md` (`DealRules`, `RecipientType`/`MetricType` taxonomies).

---

## Context & purpose

The prospectus extractor turns a ~300-page prospectus into the deal's **rules** (`DealRules`: waterfall steps, triggers, tranches, reserve). It works on English Green Lion deals but **fails on non-English** — Leone Arancio (IT) extracts at 0.375 with *empty* waterfalls; Sol-Lion (ES) at 0.30 with no capital structure. The breakage is structural, not incidental:

- **The section router is GL/English-tuned** (`extraction/section_router.py`): regex keyword lists keyed to Green Lion's section numbering/titles. Non-English headings match nothing.
- **Extracted steps are prose**, but the engine computes amounts via need-calculators keyed to the canonical **`RecipientType`** — so a step is only *executable* if extraction maps it onto that taxonomy.
- **Tranche parsing is regex over markdown pipe-tables** — brittle to each issuer's table layout.

This spec generalizes the pipeline to produce canonical `DealRules` across jurisdictions/languages.

---

## Locked decision — LLM-semantic section routing

Replace the GL-keyword regex router with **LLM-semantic section identification**:

1. **Segment** the Docling markdown deterministically by its `#` headers (cheap, language-agnostic).
2. **Classify** each segment with the LLM into the canonical sections — *definitions, revenue-PoP, redemption-PoP, post-enforcement-PoP, triggers/covenants, tranche table* — **regardless of language or numbering**.

This kills the GL-keyword brittleness (the exact thing that fails on IT/ES) and mirrors the report extractor's hybrid (deterministic segment + LLM classify). (Chosen over broadening the keyword map, which would need per-jurisdiction keyword additions forever and still miss non-standard structures.)

---

## Architecture

```
prospectus PDF
   ▼ Docling OCR (multilingual)              [existing]
markdown
   ▼ segment by # headers                    [existing route_sections, kept]
segments
   ▼ LLM-semantic section classification     [NEW — replaces GL-keyword extract_key_sf_sections]
{definitions, revenue_pop, redemption_pop, triggers, tranche_table, ...}
   ▼ LLM extraction per section, mapping to the CANONICAL taxonomies
DealRules { tranches[], waterfalls{}, triggers[], reserve, provenance, completeness }
```

### The load-bearing piece — taxonomy mapping

The generalization that makes extraction *executable*:

- **Each waterfall step's recipient → canonical `RecipientType`** (the closed enum, with the **`unmapped`** escape). The extraction prompt carries the enum; the LLM classifies each step. `unmapped` → `AmountRule.basis="report_supplied"` / not-evaluable, and **does not count toward completeness** — so a deal's exotic step degrades *honestly* instead of silently mis-mapping (the boundary-bug class the canonical schema exists to kill).
- **Each trigger's metric → canonical `MetricType`** (same closed-enum + `unmapped` treatment); `threshold_unit` normalized once at extraction.
- **Each step's amount → `AmountRule.calculator`** bound to the recipient (interest_accrual / pdl_balance / target_shortfall / principal_due / report_supplied / residual). Prose retained as `raw_text` for audit; never a free-form eval.

### Tranche extraction

LLM-assisted extraction of `TrancheRule` (name, seniority, original_balance, rate, rating) — not just the regex pipe-table — with provenance; falls back to waterfall class-references when no table is found (as today).

---

## Governance

- **Governed `PrimitiveResult`** (the Epic 5 envelope is on `main`): confidence + citations + audit travel with the extraction.
- **Per-field provenance** (`FieldProvenance` in the `ProvenanceMap`): each extracted value gets confidence + a page/section `Citation`; the locator is verified against the source span where feasible.
- **Field-based completeness** (`DealRules.compute_completeness`): a non-English *partial* extraction shows as honest low completeness with `unmapped` steps — never a faked 1.0. This is the signal that says "this deal needs human help," not a silent wrong model.

---

## Non-English (#274)

Gemini is multilingual and Docling OCRs non-Latin/accented text; the failure was the router + the GL assumptions, which the above removes.

- **Validate on Leone Arancio (IT) + Sol-Lion (ES):** target getting each from its current empty/stub state to **≥1 executable waterfall + a capital structure** — accepting that some steps land `unmapped` (honest) rather than forcing a wrong mapping.
- **Definitions and trigger thresholds** in IT/ES are extracted via the same LLM path; `MetricType` mapping + `threshold_unit` normalization make them comparable to English deals.
- Success = the deal **cold-starts** (its `DealRules` drives the engine), with completeness honestly reflecting coverage — not a demand for 100% extraction.

---

## Determinism

The assembler already caches extracted models (`data/deals/`, Docling markdown cache) and ships committed seeds (`scripts/seed_deal_models.py`, `src/loanwhiz/data/deals/seed/`). Refresh the seeds for the newly-extractable IT/ES deals so CI/demo never re-runs the multi-minute Docling+LLM path.

---

## Maps to Epic 3 issues

- **#273 — Prospectus extractor generalization:** LLM-semantic section routing; recipient/metric **taxonomy mapping** with `unmapped`; LLM tranche extraction; governed `PrimitiveResult` + per-field provenance; field-based completeness. (English deals stay green; this is purely the generalization.)
- **#274 — Non-English extraction:** run the generalized pipeline on Leone Arancio (IT) + Sol-Lion (ES) to a cold-startable `DealRules`; commit refreshed seeds; characterize residual `unmapped`/low-completeness honestly.

---

## Validation

- **Green Lion (EN) regression:** the existing extracted models stay at/above current completeness (the generalization must not degrade the working English path).
- **IT/ES forward:** Leone + Sol-Lion produce `DealRules` with ≥1 executable waterfall + capital structure; the model folds through the engine (cold-start) without inventing values; completeness reflects real coverage.

---

## Open / deferred

- Deeper per-jurisdiction definition/cross-reference linking (the `definitions_graph` is still flat) — incremental, not blocking cold-start.
- The richest validation (to-the-cent on a non-GL deal) waits on that deal having published actuals to reconcile against (report-side, Phase 4 breadth).
