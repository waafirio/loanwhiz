# Cold-start EDW deal engine — one fold, two adapters

**Date:** 2026-06-20
**Status:** Design (approved in brainstorm; pending written-spec review)
**Validation target:** Green Lion 2024-1 (report-driven), reconciled to the cent.

---

## Context

LoanWhiz can model exactly one deal end-to-end: Green Lion 2026-1. The live API
leans on hardcoded Green-Lion constants (capital structure, reserve target,
original pool balance, builtin waterfall step-lists in `api/main.py`), so no
endpoint can cold-start an *arbitrary* deal from its own extracted model.

The near-term goal is to analyse **EDW (European DataWarehouse) RMBS deals
across jurisdictions and vintages** — and the dominant reality of EDW is that
deals publish **investor reports**, while loan-level tapes require separate
licensing. So the unlock is a **report-driven** modelling path, not just the
existing tape-driven one.

A review of the current engine (see `MODELING-GAPS.md` plus a 2026-06-20 code
audit) found that the spine was rebuilt well on 2026-06-10 (per-period state
machine, sequential-pay, metric-aliased covenants, one canonical ledger), but
three structural problems remain and all point at the same simplification:

- **A1 (live):** the engine *can* interpret an extracted model, but live
  endpoints still run Green-Lion builtins.
- **A5:** `/project` is a faked single-period stress sensitivity; the real
  `CashflowProjector` is dead code and duplicates the waterfall engine.
- **B5:** investor reports are parsed only to *compare against*, never to
  *seed* period-0 state — "why Tier-1 falls back to constants."
- Plus three execution paths doing the same job (`WaterfallRunner`, the
  interpreter, `CashflowProjector`).

The owner is not tied to the current code and wants **a simple, clean system
for analysing EDW deals**. This spec crystallises that clean core and proves it
on Green Lion 2024-1 (English, well-extracted, with published Notes & Cash
reports to validate to the cent — and, crucially, *no tape*, which forces the
report path to be real).

### Decisions taken in brainstorming

1. First focus = the generalisation spine (items 1 + 4 are the same problem).
2. Validation target = **Green Lion 2024-1** (prove the architecture; defer
   non-English extraction + EDW ingestion to a follow-on spec).
3. Architecture = **one engine (`fold(run_period)`) + ingestion adapters**, not
   parallel pipelines.
4. Scope = **full consolidation**: delete the old `WaterfallRunner` and
   `CashflowProjector`, make `/project` a scenario-generator over the same fold,
   and route the Green-Lion-2026-1 tape path onto the one engine.

---

## Goal & success criteria

Make the live API model **any** deal from `{extracted DealRules + a document
package}`, through a single engine, with Green-Lion constants demoted to a
labelled fallback. Concretely, this spec is done when:

1. **Cold-start (report path):** `/waterfall` + `/compliance` for Green Lion
   2024-1 are served by the one engine, seeded and fed from its Notes & Cash
   reports, with **zero** Green-Lion-2026-1 constants consulted (assert no
   fallback hit).
2. **To the cent:** the engine-computed waterfall lines for GL-2024-1 reconcile
   to the published Notes & Cash report across all 3 quarterly periods within
   EUR 0.01 — the same figures the offline harness produces today, now from the
   live engine.
3. **Tape path unchanged:** Green Lion 2026-1 (`/waterfall`, `/compliance`)
   produces byte-identical output to today, now via the same engine.
4. **Projection real:** `/project` runs the engine forward over a
   scenario-generated input stream (no faked horizon scaling), deal-config'd.
5. **One engine:** `WaterfallRunner`, `CashflowProjector`,
   `MultiPeriodWaterfallRunner`/`WaterfallState` are deleted; nothing else
   executes a waterfall.

---

## Scope

**In:**
- The clean core (`DealRules`, `PeriodInputs`, `run_period`, `DealStateSeries`).
- `ReportAdapter` (Notes & Cash / investor report → seed + period inputs, B5).
- `ScenarioGenerator` (synthetic future periods → `/project`).
- Per-deal config resolution replacing the `_GREEN_LION_*` constants.
- Deletion of the duplicate execution paths.
- `Reconciler` reader (engine vs report actuals) — subsumes the offline
  `engine_validation_harness` and the dead `report_verifier`.

**Out (explicit deferrals to a follow-on "EDW ingestion + breadth" spec):**
- **EDW / deeploans live ingestion** (connector exists, no live instance).
- **Non-English extraction** (Leone Arancio / Sol-Lion have empty waterfalls).
- **B7 tape-native covenants** — irrelevant to GL-2024-1 (no tape).
- **Loan-level projection** — the `ScenarioGenerator` here is pool-level (done
  *correctly*); loan-level amortisation from the tape is a later enhancement.

---

## Architecture

Every part of the system is one of three things: an **adapter** that produces
period inputs, the **one engine** that folds them, or a **reader** over the
resulting series.

```
       prospectus ──► extract ──► DealRules        (steps, triggers, tranches, reserve target)
                                      │
 ┌── TapeAdapter ───────┐             │
 │  (loan tape, bottom-up)  ┐         ▼
 ├── ReportAdapter ─────┐   ├─► PeriodInputs[] ─► fold(run_period, seed, rules) ─► DealStateSeries
 │  (report, top-down)   │  │        ▲                                                 │
 └── ScenarioGenerator ─┘   ┘    DealRules                                             ▼
    (synthetic, projection)                                          readers: CovenantMonitor
                                                                              Reconciler (engine vs actuals)
                                                                              (UI / comparison tool)
```

**The collapsing insight:** *history, projection, and reconciliation are the
same fold with different input streams.* History = inputs from a Tape/Report
adapter. Projection = inputs from the scenario generator. Reconciliation = a
reader comparing the fold's engine-computed steps to the report's actuals.
There is no second engine and no divergent state (this is what makes B6
structurally impossible to regress).

### Core types & interfaces

- **`DealRules`** — the program, from prospectus extraction (today's
  `DealModel`, narrowed to what the engine executes): ordered `Step`s
  (`priority`, `recipient`, `amount_formula`, `condition`, `pari_passu_group`),
  `Trigger`s, `Tranche`s, reserve-target formula. No deal-specific Python.

- **`PeriodInputs`** — uniform per-period exogenous inputs, produced by *any*
  adapter. Supersedes today's tape-only `PeriodCollections`:
  ```
  reporting_date
  available_revenue, available_principal      # or the legs, when known
  realized_loss
  step_overrides: {priority -> amount}         # report-supplied lines the engine can't formula
  step_sources:   {priority -> engine|report-supplied|residual}
  risk_signals:   {arrears, wa_ltv, ...}       # optional, tape-only (future B7)
  ```
  Tape periods carry empty `step_overrides` → engine behaviour identical to
  today. Report periods carry overrides for fees/swaps; engine-computed lines
  (interest, PDL, reserve) are still *modelled* and are what the Reconciler
  checks. The step classification reuses the harness's existing
  `_build_specs` logic (`engine_validation_harness.py:378`), extracted into a
  shared module so the live path and any validation cannot drift.

- **`run_period(state, inputs, rules) -> (state', PeriodResult)`** — the pure,
  deterministic, deal-agnostic kernel. This is today's
  `period_state_machine.run_period` (`period_state_machine.py:326`) generalised
  to accept `step_overrides` and clear extracted conditions when the inputs are
  report-actuals (the report is post-resolution; re-gating would double-count).

- **`DealStateSeries = fold(run_period, seed, inputs[])`** — the single source
  of truth all readers consume.

- **Adapters** — `TapeAdapter` and `ReportAdapter`, each returning
  `(seed: DealState, inputs: PeriodInputs[])`.
  - `TapeAdapter` (seed from prospectus/config; inputs from
    `collections_aggregator`) delegates **all loan-tape parsing to deeploans**
    — the single canonical ESMA tape parser. There is deliberately **one** tape
    ingestion path, not two: the direct CSV/parquet read in
    `esma_tape_normaliser` is demoted to a labelled dev/demo fallback (it exists
    only because there is no live deeploans instance) and is removed once
    deeploans is wired in the follow-on EDW spec.
  - `ReportAdapter` (seed from the *first report's opening balances* — B5;
    inputs + overrides from the Notes & Cash parser).

- **`ScenarioGenerator`** — given the last known state + `DealRules` +
  assumptions (CPR / CDR / recovery / rate shift), yields a synthetic
  `PeriodInputs[]` for the fold. Pool-level, with a *single, consistent*
  CDR↔SMM decomposition (fixes C5). This is the absorbed `CashflowProjector`.

- **Readers** — `CovenantMonitor` (already a series reader post-A4/B6) and
  `Reconciler` (engine-computed steps vs report actuals, EUR tolerance). The
  registered `waterfall_runner` MCP primitive becomes a thin single-period
  wrapper over `run_period` (preserving the MCP tool surface).

---

## Worked data flows

**Report-driven (GL-2024-1):** `deals.json(GL-2024-1)` → resolve `DealRules`
(extracted model) + config → `ReportAdapter` parses the 3 quarterly Notes & Cash
reports → seed period-0 from the first report's opening balances; build
`PeriodInputs` with `step_overrides` for report-supplied lines → `fold` →
`DealStateSeries` → `/waterfall` + `/compliance` read it; `Reconciler` asserts
engine-computed steps == report to the cent.

**Tape-driven (GL-2026-1):** `TapeAdapter` (`esma_tape_normaliser` +
`collections_aggregator`) → `PeriodInputs` (empty overrides) → same `fold` →
same readers. Output identical to today.

**Projection (`/project`):** take the last state of either series →
`ScenarioGenerator(assumptions)` → synthetic `PeriodInputs[]` → same `fold` →
projected `DealStateSeries`. WAL etc. fall out of the series, not a faked
horizon.

---

## Consolidation: what gets deleted

- `cashflow_projector.py` — absorbed into `ScenarioGenerator` feeding the fold.
- The standalone `WaterfallRunner` snapshot path + `_GREEN_LION_*` builtin
  step-lists/constants in `api/main.py` — replaced by `DealRules` + config; the
  interpreter (`waterfall_interpreter.py`) and `run_period` survive as the core.
- `MultiPeriodWaterfallRunner` / `WaterfallState` (already deprecated/superseded
  by `period_state_machine`).
- `report_verifier.py` + `engine_validation_harness.py` reconciliation logic —
  folded into the `Reconciler` reader (the GL-2024-1 "to the cent" proof becomes
  `Reconciler` passing over the live series).

Green-Lion-2026-1 constants survive only as a **labelled last-resort fallback**
so a misconfigured deal fails *loudly* rather than silently borrowing GL's
numbers.

---

## Migration sequence (detailed plan via writing-plans)

1. Introduce `PeriodInputs` (+ `step_overrides`/`step_sources`) and generalise
   `run_period`; prove GL-2026-1 tape path unchanged (regression lock).
2. Extract the step-source classifier into a shared module; add `ReportAdapter`
   + report-based period-0 seeding (B5).
3. Resolve per-deal config from `deals.json` + extracted model; demote
   `_GREEN_LION_*` to labelled fallback.
4. Route `/waterfall` + `/compliance` adapter-selection by deal; cold-start
   GL-2024-1; `Reconciler` to the cent.
5. `ScenarioGenerator` → rewire `/project` over the fold; delete
   `CashflowProjector`.
6. Delete the remaining duplicate paths; collapse the MCP `waterfall_runner`
   onto `run_period`.

---

## Validation & testing

- **Integration (headline):** cold-start GL-2024-1 through live `/waterfall` +
  `/compliance`; `Reconciler` matches published Notes & Cash to EUR 0.01 across
  3 periods — equal to the current offline harness output (regression-lock the
  expected figures).
- **Regression guard:** GL-2026-1 tape-path output byte-identical pre/post.
- **Honesty:** a deal with neither tape nor reports returns "not modelable";
  GL-2024-1 asserts **no** Green-Lion-2026-1 fallback was consulted.
- **Determinism:** commit cached parser fixtures for GL-2024-1's 3 Notes & Cash
  periods (parsing is Gemini-based) and GL-2026-1's tapes, so CI is deterministic.
- **Projection:** `/project` over the fold reproduces a hand-checked pool-level
  scenario; CDR↔SMM decomposition is internally consistent (C5 closed).
- **Unit:** `ReportAdapter` maps a known period → expected seed/funds/overrides;
  `run_period` with empty overrides == prior kernel.

---

## Risks & mitigations

- **Deletion blast radius (full consolidation).** Mitigate by sequencing: the
  new path is proven (GL-2026-1 regression-locked, GL-2024-1 to the cent)
  *before* any delete; deletes land last.
- **Report-parse non-determinism.** Commit cached fixtures; the live path reads
  the cache in CI.
- **Period-0 seed from report vs prospectus.** Decision: seed liabilities from
  the **first report's opening balances** (closest to actual; B5's intent);
  prospectus capital structure is the tape-path seed.
- **MCP/demo surface.** The `waterfall_runner` MCP tool must keep returning a
  `PrimitiveResult` — keep it as a thin single-period wrapper over `run_period`.

---

## Deferred / follow-on (the "EDW ingestion + breadth" spec)

EDW/deeploans live ingestion (consolidate the `TapeAdapter` onto deeploans as
the sole tape parser and remove the direct-read fallback — first verify whether
deeploans ingests an arbitrary tape on demand or only serves pre-ETL'd deals);
non-English extraction (IT/ES); B7 tape-native covenants; loan-level projection.
With the clean core landed, each of these is "add an adapter / improve
extraction / add a reader" — not an engine change.
This is also the on-ramp to item 2 (the deal-comparison tool), which reads the
same `DealStateSeries` across deals.
