# General report extractor — design

**Date:** 2026-06-20
**Status:** Design (mechanism locked: hybrid). Spec for Epic 3 children #271 (general report extractor) + #272 (reconciliation-as-gate).
**Phase:** 3 (extraction layer — the long pole).

Companion docs (on `main`):
- `2026-06-20-canonical-domain-schema-design.md` — the contract this fills.
- `2026-06-20-cold-start-edw-deal-engine-design.md` — the engine + adapters this feeds.

---

## Context & purpose

The engine is deal-agnostic by construction; **extraction is the long pole** for EDW breadth. On prose documents with no canonical schema, governance is the substitute for ground truth — *except* on the report path, where the engine can recompute the distributions and reconcile, giving a real correctness signal.

Today the report side is split between two parsers, neither fit for breadth:
- `notes_cash_parser` — deterministic, rich (full PoP, PDLs, reserve, balances), but **hand-tuned to Green Lion's "Bond Report" layout** (keys on `"b.v."`, class-values-one-per-line, `^\(([a-z]|\d{1,2})\)` step labels). Returns nothing useful on another issuer.
- `report_verifier` — LLM-based (layout-tolerant) but **thin** (5 aggregate figures) and used only for comparison.

This spec defines **one general, governed report extractor** that produces a typed `ParsedReport` across arbitrary issuer layouts, with per-field provenance, and a **reconciliation-as-gate** flow that routes only the *unreconciled* fields to a human.

### Where it sits

It slots in front of the `ReportAdapter` (built in #267):

```
report PDF ──► [Report Extractor] ──► ParsedReport (typed, per-field provenance)
                                          │
                                  [ReportAdapter #267]   (typed → canonical)
                                          ▼
                            (seed: DealState, inputs: PeriodInputs[])
                                          ▼
                                   fold(run_period)  ──►  DealStateSeries
                                          ▼
                                   [Reconciler #270]  ──► reconciled fields ✓ / unreconciled → human review
```

Clean separation of concerns: **extract** (PDF → typed `ParsedReport`) vs **adapt** (`ParsedReport` → canonical inputs, already built in #267) vs **reconcile** (#270). This extractor owns only the first arrow.

---

## Locked decision — hybrid mechanism

**Deterministic fast-path for recognized layouts + LLM structured-output fallback for unknown ones.** (Chosen over LLM-only.)

- A small **format registry**: each entry is `(matches(text) -> bool, parse(text) -> ParsedReport)`. The existing `notes_cash_parser` becomes the first registered deterministic parser (Green Lion Bond Report). Deterministic parses are free, CI-stable, and already reconcile to the cent.
- When **no registered format matches**, fall back to **Docling/OCR → LLM structured-output** (function-call / typed-JSON against the `ParsedReport` schema, retried on validation failure) — the general path for any issuer.
- Order: **deterministic-first, LLM-second.** A deterministic hit short-circuits the LLM (and its cost + nondeterminism).

Rationale: keeps the precise/free path where formats are known (Green Lion, and any issuer we choose to write a deterministic parser for), while the LLM path gives breadth on day one for everything else.

---

## `ParsedReport` schema (the extractor's output)

A general typed model — generalizes today's `NotesCashReport`. **Every field is optional** (a report carries what it carries — quarterly Notes & Cash has full PoP; a monthly investor report has coarser aggregates), and every extracted value is provenanced.

```python
class ParsedReportPeriod(BaseModel):
    reporting_date: str
    # opening / closing structural figures (for the period-0 seed, B5)
    note_balances: list[NoteBalance]          # per class: opening?, closing?, pdl?
    reserve_balance: float | None
    reserve_target: float | None
    pool_balance: float | None
    # available funds + the actual PoP the report published
    available_revenue: float | None
    available_principal: float | None
    revenue_pop: list[ReportedStep]           # priority_label -> amount (as printed)
    redemption_pop: list[ReportedStep]
    triggers: list[ReportedTrigger]           # reported breach state, if printed

class ParsedReport(BaseModel):
    deal_name: str
    report_type: Literal["notes_and_cash", "investor_report", "unknown"]
    periods: list[ParsedReportPeriod]         # sorted by reporting_date
    provenance: ProvenanceMap                 # per dotted field path (schema §0)
    extraction_method: Literal["deterministic", "ocr+llm"]
```

`ReportAdapter` (#267) already maps a parsed report → `(seed, PeriodInputs[])`; this schema is the general shape it consumes (its current `NotesCashReport` input becomes one concrete case).

---

## Governance (uniform envelope + per-field provenance)

The Epic 5 governance work is now on `main`, so the extractor is built governed from the start:

- **First-class governed primitive** — returns `PrimitiveResult[ParsedReport]` (confidence + citations + audit). Registered in the catalogue.
- **Per-field provenance** (`FieldProvenance` in the `ProvenanceMap`, keyed by dotted path):
  - **deterministic parse** → `source="report"`, `method="deterministic"`, `confidence=1.0`, citation = the matched line/section.
  - **LLM parse** → `method="ocr+llm"`, `confidence` from the model's per-field certainty, citation = the page/line the model cited. The citation excerpt is **verified against the source span** where feasible (the locator must point at text that actually contains the value) — an unverifiable citation drops confidence.

---

## Reconciliation-as-gate (the killer feature — #272)

The report path can do what the prospectus path cannot: **recompute the distributions and check them.**

Flow: `extract → ReportAdapter → fold(run_period) → Reconciler(#270)` compares each **engine-computed** line (Class A/B interest, PDL cure, reserve replenishment) against the report's **stated** actual.

- A field whose engine-computed value matches the report **to the cent** → its `FieldProvenance.reconciled = True` (the strong correctness signal — stronger than any confidence heuristic).
- A field that **does not reconcile** (or was never reconcilable — e.g. an exotic report-supplied line with no engine formula) **and** is low-confidence → **routed to human review**.
- Fields that are reconciled need **no** human review regardless of extraction confidence.

This inverts the review burden: instead of a human checking everything an LLM extracted, they check only the handful the engine couldn't confirm. Deterministic + reconciled fields are auto-trusted.

---

## Determinism for CI

LLM extraction is nondeterministic, but to-the-cent CI must be reproducible:

- **Cache parsed reports** keyed by a hash of the report bytes/URL (mirrors the Docling + `report_verifier` caches).
- **Commit fixtures** for the validation deals (Green Lion 2024-1's 3 Notes & Cash periods) so CI reads the cache, never the live LLM. (For GL-2024-1 the deterministic parser already gives this for free; the cache matters for the LLM path on other deals.)

---

## Consolidation

- `report_verifier` (the thin 5-figure LLM extractor) → **subsumed** (its envelope + caching patterns fold into this extractor; #270 already subsumes its reconciliation half).
- `notes_cash_parser` → **retained as the first deterministic format-registry entry**, not deleted.

---

## Maps to Epic 3 issues

- **#271 — General report extractor:** the format registry + the Docling/OCR→LLM fallback + `ParsedReport` schema + governed `PrimitiveResult` + per-field provenance + the determinism cache. Deterministic-first, LLM-second.
- **#272 — Reconciliation-as-gate:** wire `Reconciler` (#270) as the automated gate — mark `reconciled` fields, route only unreconciled + low-confidence to human review.

(#273/#274 — prospectus extractor generalization + non-English — are a separate design.)

---

## Validation

- **GL-2024-1** stays to-the-cent via the deterministic path (regression-locked by #270's reconciliation).
- **Add one non-GL report** (an EDW deal, or Leone Arancio's Italian investor report) through the **LLM path**; assert the `ParsedReport` validates, the fold runs, and reconciled fields are marked — accepting that some fields route to human review (that is the gate working, not a failure).

---

## Open / deferred

- Writing additional **deterministic format parsers** beyond Green Lion is optional and incremental — the LLM path covers everything until someone chooses to add a fast-path.
- Prospectus-side extraction generalization (#273/#274) is its own design.
- Multi-language report OCR quality (non-English investor reports) is a known risk carried with the Phase-4 breadth work.
