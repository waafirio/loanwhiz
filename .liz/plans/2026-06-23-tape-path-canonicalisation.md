---
id: 2026-06-23-tape-path-canonicalisation
title: Tape-path canonicalisation & residual-gap closure (post-2026-06-22 audit)
status: filed
created: 2026-06-23
updated: 2026-06-23
epics: [360, 361, 362]
---

# Tape-path canonicalisation & residual-gap closure (post-2026-06-22 audit)

## Context & intent

This plan is the durable capture of the **next leg** chosen after the
2026-06-22 honest foundation audit of LoanWhiz (`main` @ `a5a5ddf`). That
audit (10 grounded read-only passes + direct spot-verification) found the
core genuinely real — not Potemkin — but surfaced a cluster of residual
gaps that all trace to **one root cause**, plus a few independent
correctness/wiring loose ends.

### Where we actually are (the audit, in brief)

The full 2026-06-20 "EDW deal-analysis engine" roadmap (epics #256→#262)
plus #342 is promoted. Genuinely real and load-bearing:

- **One waterfall engine** (`period_state_machine.run_period`; duplicate
  engines deleted in #259), model-driven on the report path, true
  multi-period amortisation, junior-first loss→PDL allocation, working
  sequential-pay trigger.
- **Covenant monitoring** over real reconstructed per-period state, with
  honest `not_evaluable` instead of the old fake "100% funded".
- **Projection** as a real multi-period fold with engine-derived WAL, a
  `/stress-matrix` grid (#323), and enforced series invariants.
- **Reconciliation** of GL-2024-1 engine-vs-report **to the cent**, with
  per-line `engine | report-supplied | residual` provenance.
- **Governance threading is real** (`planner.py:276-317` threads true
  confidence/citations/duration; `human_review_required` fires;
  `finos_compliant` derived; FINOS posture #278 honestly relabelled).
- **Deal-comparison tool (#283)** and **ESMA Annex 2 mapping (#280)** are
  production-grade.
- 1095 tests, zero skips/xfails.

### The one root theme

**The canonical-schema migration (#256) was completed on the REPORT path
but NOT the TAPE path.** The report path uses the canonical
`PeriodInputs` / `RiskSignals` / `domain.state.DealState`
(`tranches: list[TrancheState]`); the tape path still runs the older
collections + `primitives.DealState` route with hardcoded `class_a/b/c`.
That single asymmetry is the root cause of most residual gaps:

- **Rigid tranche schema** — the active engine (`primitives/deal_state.py`)
  hardcodes `class_a/b/c`; the bridge silently zero-fills missing
  tranches, so a 4+ tranche or single-tranche (Sol-Lion) deal reconstructs
  **wrong, silently**.
- **Tape→`RiskSignals` orphaned** — arrears/LTV signals are computed by the
  tape normaliser but never assembled into `PeriodInputs`
  (`report_adapter.py:387: risk_signals=None`; no `source="tape"`
  constructor anywhere). So the tape-native arrears/LTV covenants (#280)
  are defined and unit-tested **in isolation but cannot fire on any real
  deal**.
- **Loan-level amortisation (#281) is projection-only** — it works forward
  but historical reconstruction still uses the pool/collections proxy; the
  "replace the proxy" claim is half-done.
- **No integrated tape→waterfall→covenant test on real non-GL data** — the
  breadth suite exercises primitives in isolation over *synthetic* tape
  periods, which is exactly why green CI didn't catch any of the above.

### Independent loose ends (not the root theme, but real)

- **IT/ES extraction is over-claimed.** The **Leone Arancio (IT) seed is
  corrupted** — `completeness_score: 1.0` but it contains *Green Lion's*
  verbatim waterfall (23 revenue / 5 redemption / 12 post-enforcement
  steps) and 40 "Green Lion" citations. A cold-start of that deal would
  silently run the wrong deal's cascade. **Sol-Lion II (ES)** is still
  empty-waterfall at 0.30 completeness (section-routing bug #316). So
  "non-English extraction (#274)" did not actually land for either.
- **Reconciliation-as-gate (#272)** is fully built and tested but **never
  called by the API** — the "route only unreconciled fields to human
  review" inversion is dead code on the report path.
- **Governance envelope not uniform (#277 partial)** — the agent's
  `check_covenants` tool skips `audit_result()`, the MCP server doesn't
  persist audit entries, and the agent model card lists 4 tools while 11
  ship.
- **`threshold_unit` has no runtime guard** — normalisation happens only at
  extraction; a consumption-side mistake is a latent 100× misread.

### Why this shape (3 epics, not 5 workstreams)

The audit named five workstreams; this plan groups them into **three
coherent epics** by what they share:

1. **Tape-path canonicalisation & engine generality** — the root-cause
   fix. Highest leverage: completing it unlocks real EDW breadth, makes
   tape-native covenants fire, removes the silent-wrong-reconstruction
   risk, and closes the synthetic-isolation test gap. *Everything else is
   smaller.*
2. **IT/ES extraction reality** — make "many deals, many jurisdictions"
   actually true. The corrupted Leone seed is also a standalone
   integrity bug worth fixing immediately, independent of the rest.
3. **Seam hardening: gate wiring, governance uniformity, unit guard** —
   the independent correctness/wiring loose ends, grouped because they are
   all "close a built-but-unwired seam" and are mutually independent.

### Cross-epic ordering rationale

- **Epic 1 is the foundation** and the highest-leverage unlock.
- **Epic 3 is fully independent** of 1 and 2 — its three children touch
  disjoint seams and can run in parallel any time.
- **Epic 2's extraction fixes** (Leone repair, Sol-Lion #316) are
  independent of Epic 1 and run in parallel; only the **cross-jurisdiction
  cold-start validation** child depends on Epic 1's engine generality +
  tape adapter, so it carries an explicit `After #360`.

```
Epic 1 (tape canonicalisation) ──┐
Epic 3 (seam hardening)  ─────────┼─ all parallel
Epic 2 (IT/ES extraction) ────────┘  except 2c (validation) After Epic 1
```

## Decomposition

Three epics, 10 children. Cross-epic order: Epic 1 and Epic 3 are fully
parallel; Epic 2's extraction fixes run in parallel too, with only child
**2c** gated `After` the Epic-1 umbrella (it needs the generalised engine
+ tape adapter to validate end-to-end).

### Epic 1: Tape-path canonicalisation & engine generality   (umbrella #360)

The root-cause fix: bring the tape path onto the canonical schema the
report path already uses, and generalise the engine off its hardcoded
A/B/C tranche assumption. Completing this unlocks real EDW breadth, makes
tape-native covenants fire on real deals, removes the silent
wrong-reconstruction risk, and closes the synthetic-isolation test gap.

- **Generalise the engine tranche schema** — migrate the active engine
  from hardcoded `class_a/b/c` (`primitives/deal_state.py`) onto the
  canonical `tranches: list[TrancheState]` that `domain.state` already
  defines; thread it through `period_state_machine`, `waterfall_interpreter`,
  and `covenant_monitor`; regression-lock GL's A/B/C output byte-for-byte.
  Sequencing: sequential. Paths: `src/loanwhiz/primitives/deal_state.py`,
  `src/loanwhiz/primitives/period_state_machine.py`,
  `src/loanwhiz/primitives/waterfall_interpreter.py`,
  `src/loanwhiz/primitives/covenant_monitor.py`.
- **Tape→canonical `PeriodInputs` adapter** — build the missing
  `source="tape"` adapter that constructs canonical `PeriodInputs`
  (collection legs **and** a populated `RiskSignals` — arrears_90d/180d,
  wa_ltv, default_pct, pool_balance) from the normalised tape, so the tape
  path folds through the same kernel and schema as the report path (no
  more `risk_signals=None`). Sequencing: sequential. After #363. Paths:
  `src/loanwhiz/primitives/**`, `src/loanwhiz/domain/**`,
  `src/loanwhiz/api/main.py`.
- **Loan-level amortisation in historical reconstruction** — use
  `loan_level_amortisation` in the tape-driven collections/period path
  (today it is wired into projection only), replacing the pool-level
  proxy for history. Sequencing: parallel. After #364. Paths:
  `src/loanwhiz/primitives/collections_aggregator.py`,
  `src/loanwhiz/primitives/loan_level_amortisation.py`,
  `src/loanwhiz/primitives/period_state_machine.py`.
- **Integrated tape→waterfall→covenant E2E on real non-GL data** — an
  integration test (and any wiring it forces) that folds a *real* tape
  through collections → period state → waterfall → covenant evaluation,
  proving the tape-native arrears/LTV covenants (#280) actually fire;
  closes the "primitives tested only in synthetic isolation" gap.
  Sequencing: sequential. After #364. Paths: `tests/**`,
  `src/loanwhiz/api/main.py`.

### Epic 2: IT/ES extraction reality   (umbrella #361)

Make "many deals, many jurisdictions" actually true for the two
non-English deals. The corrupted Leone seed is also a standalone
integrity bug worth fixing immediately.

- **Repair the corrupted Leone Arancio (IT) seed** — remove the
  Green-Lion contamination (the seed claims `completeness_score: 1.0` but
  carries GL's verbatim waterfall + 40 GL citations); restore an honest
  extraction (real IT `DealRules` if extractable, else an honest partial
  with truthful completeness — never a false 1.0). Sequencing: parallel.
  Paths: `src/loanwhiz/data/deals/seed/**`, `src/loanwhiz/extraction/**`.
- **Resolve Sol-Lion II (ES) empty-waterfall section-routing #316** — tune
  the payment-list signal / LLM section router so the Spanish prospectus
  yields ≥1 executable waterfall instead of empty `waterfalls: {}` at 0.30
  completeness. Sequencing: parallel. Paths:
  `src/loanwhiz/extraction/section_router.py`,
  `src/loanwhiz/extraction/assembler.py`, `src/loanwhiz/data/deals/seed/**`.
- **Cross-jurisdiction cold-start validation (IT+ES)** — once extraction
  is honest, validate Leone + Sol-Lion cold-start end-to-end through the
  generalised engine (depends on Epic 1). Sequencing: sequential.
  After #367. Also After #360. Paths: `tests/**`,
  `src/loanwhiz/api/main.py`.

### Epic 3: Seam hardening — gate wiring, governance uniformity, unit guard   (umbrella #362)

The independent built-but-unwired loose ends. All three children touch
disjoint seams and run in parallel.

- **Wire reconciliation-as-gate (#272) into the report path** — invoke
  `reconcile_as_gate` in the report cold-start path so reconciled fields
  are auto-trusted and only unreconciled + low-confidence fields route to
  human review (the gate is built and tested but `main.py` never calls it).
  Sequencing: parallel. Paths: `src/loanwhiz/api/main.py`,
  `src/loanwhiz/primitives/reconciliation_gate.py`.
- **Governance envelope uniformity (#277 finish)** — add `audit_result()`
  to the agent `check_covenants` tool, persist the MCP server's audit
  entries to disk (matching the REST pattern), and refresh the agent model
  card to list all 11 shipped tools (it lists 4). Sequencing: parallel.
  Paths: `src/loanwhiz/agent/tools.py`, `mcp/**`,
  `src/loanwhiz/governance/agent_model_card.py`.
- **Runtime `threshold_unit` guard** — add a consumption-side check that
  converts/asserts a trigger's `threshold_unit` to the monitor's canonical
  unit before evaluation, closing the latent 100× misread risk
  (normalisation today happens only at extraction). Sequencing: parallel.
  Paths: `src/loanwhiz/api/main.py`,
  `src/loanwhiz/primitives/covenant_monitor.py`,
  `src/loanwhiz/domain/rules.py`.

## Filed issues

- Epic "Tape-path canonicalisation & engine generality" → umbrella #360
  - #363 Generalise the engine tranche schema (class_a/b/c → tranches: list[TrancheState])
  - #364 Tape→canonical PeriodInputs adapter (collection legs + RiskSignals)  [After #363]
  - #365 Loan-level amortisation in historical reconstruction  [After #364]
  - #366 Integrated tape→waterfall→covenant E2E on real non-GL data  [After #364]
- Epic "IT/ES extraction reality" → umbrella #361
  - #367 Repair the corrupted Leone Arancio (IT) seed
  - #368 Resolve Sol-Lion II (ES) empty-waterfall section-routing (#316)
  - #369 Cross-jurisdiction cold-start validation (IT + ES)  [After #360; also needs #367, #368]
- Epic "Seam hardening — gate wiring, governance uniformity, unit guard" → umbrella #362
  - #370 Wire reconciliation-as-gate (#272) into the report path
  - #371 Governance envelope uniformity (check_covenants audit + MCP persistence + model card)
  - #372 Runtime threshold_unit guard
