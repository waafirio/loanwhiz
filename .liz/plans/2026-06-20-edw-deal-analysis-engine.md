---
id: 2026-06-20-edw-deal-analysis-engine
title: EDW deal-analysis engine
status: decomposed
created: 2026-06-20
updated: 2026-06-20
epics: []
---

# EDW deal-analysis engine

## Context & intent

**Goal:** a simple, clean system for analysing European RMBS deals at **EDW
breadth** — many deals, jurisdictions, vintages — not just the one
hand-tuned Green Lion deal the framework can model today.

This plan is the durable capture of a finalized brainstorm. Two companion design
docs carry the full detail and are committed alongside this plan:

- `docs/superpowers/specs/2026-06-20-canonical-domain-schema-design.md` — the
  canonical schema (Phase 1 contract).
- `docs/superpowers/specs/2026-06-20-cold-start-edw-deal-engine-design.md` — the
  engine/architecture (Phase 2).

### The problem, as finalized

LoanWhiz can model exactly one deal end-to-end (Green Lion 2026-1). The live API
leans on hardcoded Green-Lion constants and builtin waterfall step-lists, so no
endpoint can cold-start an arbitrary deal from its own extracted model. A
2026-06-20 audit confirmed the spine was rebuilt well on 2026-06-10 (per-period
state machine, sequential-pay, metric-aliased covenants, one canonical ledger),
but the residual gaps all cluster around **one theme: generalising beyond Green
Lion** — the live API still runs builtins (A1-live), investor reports are parsed
only to compare against and never to *seed* state (B5), `/project` is a faked
single-period stress (A5), and three different code paths execute waterfalls.

### Two reframings that shaped this plan

1. **"Residual modeling" and "test across many deals" are the same problem.**
   You cannot meaningfully run EDW deals across jurisdictions until the engine can
   cold-start an *arbitrary* deal from `extracted model + investor report`. So
   the modeling fixes and the multi-deal goal collapse into one workstream.

2. **The engine is the easy part; extraction is the long pole.** The clean engine
   is deal-agnostic by construction. The real risk and effort is *extraction* —
   turning prose prospectuses and report PDFs into a canonical, governed schema
   across issuers and languages. There is no canonical schema for prose documents
   (unlike the tape side, which has ESMA Annex 2), which is exactly why one must
   be defined. And on prose documents with no ground truth, **governance
   (confidence + citation + audit + human-review) is the substitute for
   correctness** — except on the report path, where the engine can *recompute*
   the distributions and reconcile, giving a real correctness signal.

### Why this shape (and not the alternatives)

- **One engine = `fold(run_period)` over a stream of `PeriodInputs`, fed by
  ingestion adapters.** History, projection, and reconciliation become the same
  fold over different input streams (tape adapter / report adapter / scenario
  generator). This collapses the three duplicate execution paths (`WaterfallRunner`,
  the interpreter, `CashflowProjector`) into one and makes the B6 "divergent
  state" bug class structurally impossible. *Rejected:* wiring the existing
  validation harness in as a second live path — faster, but re-introduces two
  divergent engines.

- **Validate on Green Lion 2024-1, report-driven.** It is English, well-extracted
  (0.925), and has published Notes & Cash reports to reconcile **to the cent** —
  and crucially has *no loan tape*, which forces the report path (B5 seeding) to
  be real. *Rejected:* validating on Leone Arancio (IT) / Sol-Lion (ES) first —
  their non-English extraction yields empty waterfalls, so that silently signs us
  up for the hard extraction problem inside the engine work.

- **A canonical domain schema is the foundation.** Today the same concept exists
  in 4×/4×/3× incompatible typed shapes joined by mapping glue, and a whole class
  of modeling bugs are boundary-mapping bugs (A4 metric-name mismatch, C8
  threshold-unit dropped). Defining `DealRules` / `PeriodInputs` / `DealState`
  once — the shape every extractor fills and the engine consumes directly —
  removes the glue and turns extraction from trial-and-error into "fill a
  validated form."

### Cross-epic narrative & ordering rationale

The critical path is **schema → validate cheaply → do the hard extraction →
clean up**, with governance woven through:

```
1 (schema)  →  2a (locks the contract on GL-2024-1, to the cent)
            →  3 (general extraction — the real work)
            →  2b (engine cleanup + projection) / 4 (EDW breadth)
            →  5 (analyst tools)
   G (governance) woven through 1–3
```

The non-obvious ordering decision: **Phase 2a (engine slice) goes *before* Phase
3 (extraction), even though extraction is the bigger prize.** GL-2024-1 extraction
already exists, so validating `schema → engine → to-the-cent` is essentially
*free* and **locks the canonical contract** before the expensive extraction work
is built against it. Building hard extraction against an unvalidated schema is the
most expensive possible place to discover a schema bug. Phase 2b (delete the old
runner/projector, rewire `/project`) is pure cleanup and deliberately deferred —
it builds nothing new except forward projection and has no urgency before breadth.

### Decisions log

| # | Decision | Rationale |
|---|---|---|
| D1 | First focus = generalisation spine (items 1+4) | Bottleneck for everything else |
| D2 | Validation target = Green Lion 2024-1 (report-driven) | English, reconciles to the cent; no tape forces the report path real |
| D3 | One engine `fold(run_period)` + ingestion adapters | Kills 3 duplicate paths + the B6 divergence class |
| D4 | Full consolidation (delete old runner/projector) | Cleanest end state |
| D5 | Single tape path via deeploans | Canonical ESMA tape parser; direct read demoted to dev fallback |
| D6 | Phase 2 split 2a/2b, extraction between | Lock the contract cheaply before the hard extraction |
| D7 | Governance a uniform cross-cut, in the report extractor from day one | Report path gets a correctness signal (reconciliation) the prospectus path can't |
| D8 | Canonical schema: sidecar provenance · closed taxonomies + `unmapped` · calculator-keys not formulas · aggregate funds + optional legs · ESMA Annex 2 as citation locators | See schema doc |

## Decomposition

Seven epics. Cross-epic order (encoded as `After #<umbrella>` on the dependent
umbrella at filing): `1 → 2 → {3, 4}`, `5 After 1`, `6 After 3`, `7 After 2`.
Recommended filing now: **Epics 1, 2, 5** (schema → contract-lock → governance);
**Epics 3, 4, 6, 7 deferred** to a follow-up pass once 2a lands.

### Epic 1: Canonical domain schema   (umbrella #TBD) — *file now*

The foundation: one canonical typed contract that every extractor fills and the
engine consumes directly, in a new `src/loanwhiz/domain/`. See
`docs/superpowers/specs/2026-06-20-canonical-domain-schema-design.md`.

- **Core canonical types** — define `DealRules` (+ `RecipientType`/`MetricType` taxonomies, `AmountRule`, `TriggerRule`, `TrancheRule`, `RateRule`, `ReserveRule`), `PeriodInputs` (+ `CollectionLegs`, `RiskSignals`), `DealState`. Sequencing: sequential. Paths: `src/loanwhiz/domain/**`.
- **Provenance + completeness** — `FieldProvenance`/`ProvenanceMap` sidecar; field-based completeness scorer (required canonical fields filled). Sequencing: sequential. After core types. Paths: `src/loanwhiz/domain/**`.

### Epic 2: Engine slice — cold-start GL-2024-1 to the cent   (umbrella #TBD) — *file now*

Validate the schema through the live engine on Green Lion 2024-1 (report-driven),
locking the contract before extraction work. After Epic 1.

- **Generalise `run_period` to `PeriodInputs`** (+ `step_overrides`/`step_sources`); regression-lock the GL-2026-1 tape path byte-for-byte. Sequencing: sequential. Paths: `src/loanwhiz/primitives/period_state_machine.py`, `waterfall_interpreter.py`.
- **Shared step-source classifier** — extract the harness's `_build_specs` into a shared module both the live path and the harness use. Sequencing: parallel.
- **`ReportAdapter`** — Notes & Cash → period-0 seed (B5) + `PeriodInputs` + report-supplied overrides. Sequencing: sequential. After "Generalise `run_period`". Paths: `src/loanwhiz/domain/**`, `src/loanwhiz/primitives/**`.
- **Per-deal config resolution** — resolve capital structure / reserve / pool from `deals.json` + extracted model; demote `_GREEN_LION_*` to a labelled last-resort fallback. Sequencing: parallel. Paths: `src/loanwhiz/api/main.py`, `src/loanwhiz/config.py`.
- **Adapter selection + cold-start GL-2024-1** — `/waterfall` + `/compliance` pick the adapter per deal; honest "not modelable" when neither tape nor reports. Sequencing: sequential. After `ReportAdapter` + config resolution. Paths: `src/loanwhiz/api/main.py`.
- **`Reconciler` + validate to the cent** — engine-vs-report reader; reconcile GL-2024-1 across 3 Notes & Cash periods to EUR 0.01; subsume `engine_validation_harness` + `report_verifier`; commit deterministic parser fixtures. Sequencing: sequential. After adapter selection.

### Epic 3: Extraction layer   (umbrella #TBD) — *deferred*

The long pole: governed extraction filling the canonical contract across issuers
and languages. After Epic 2.

- **General report extractor** — Docling/OCR + LLM structured-output against the canonical schema; first-class governed primitive with per-field confidence + citation. Sequencing: sequential.
- **Reconciliation-as-gate** — extract → reconcile (engine-recomputed vs report-stated) → route only *unreconciled* fields to human review. Sequencing: sequential. After "General report extractor".
- **Prospectus extractor generalisation** — executable `DealRules` across jurisdictions; map steps to the canonical recipient taxonomy; de-tune the GL section router; robust tranche parsing. Sequencing: parallel.
- **Non-English extraction** — Leone Arancio (IT) + Sol-Lion (ES) to usable `DealRules`. Sequencing: sequential. After "Prospectus extractor generalisation".

### Epic 4: Engine cleanup + forward projection   (umbrella #TBD) — *deferred*

Cleanup, not model-building. After Epic 2.

- **`ScenarioGenerator` → `/project` over the fold** — projection as synthetic `PeriodInputs` through the same fold; consistent CDR↔SMM decomposition (C5). Sequencing: sequential.
- **Delete the duplicate engines** — remove `WaterfallRunner`, `CashflowProjector`, `MultiPeriodWaterfallRunner`/`WaterfallState`; collapse the MCP `waterfall_runner` onto `run_period`. Sequencing: sequential. After `ScenarioGenerator`.

### Epic 5: Governance cross-cut   (umbrella #TBD) — *file now*

Make governance uniform. After Epic 1; runs alongside 2/3.

- **Uniform envelope** — apply the `PrimitiveResult` envelope to every adapter/extractor/reader (close the report-side bypass); promote `audit_logger` from "in-progress" to wrapping every primitive call. Sequencing: parallel.
- **Resolve the FINOS posture** — operator decision: real compliance target (map the full control set, assert per-primitive) *or* design-inspiration (rename the overclaiming `finos_compliant`); apply consistently across code/docs/UI. Sequencing: parallel. *(carries an operator decision)*

### Epic 6: EDW ingestion + breadth   (umbrella #TBD) — *deferred*

After Epic 3.

- **deeploans as sole tape parser** — verify its ingestion model (arbitrary tape on demand vs serve-only); consolidate `TapeAdapter` onto deeploans; remove the direct-read fallback; stand up a live instance. Sequencing: sequential.
- **Tape-native covenants + Annex 2 mapping** — B7 (arrears/LTV triggers); full ESMA Annex 2 field-code mapping table. Sequencing: parallel.
- **Loan-level projection** — replace the pool-level proxy with loan-level amortisation from the tape. Sequencing: parallel.
- **Cross-jurisdiction / vintage validation** — run the breadth set end-to-end. Sequencing: sequential. After "deeploans as sole tape parser" + "Tape-native covenants".

### Epic 7: Analyst-facing tools   (umbrella #TBD) — *deferred*

After Epic 2 (reads `DealStateSeries`). Ideation-heavy; per-issue planning will scope.

- **Deal comparison tool** — chatbot + visual comparison of terms/triggers/performance across deals; reads `DealStateSeries`. Sequencing: parallel.
- **Trader/analyst tool ideation** — scope additional tools informed by Phases 2–4. Sequencing: parallel.

## Filed issues

_To be filled in Phase 4._
