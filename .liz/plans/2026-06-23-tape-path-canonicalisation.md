---
id: 2026-06-23-tape-path-canonicalisation
title: Tape-path canonicalisation & residual-gap closure (post-2026-06-22 audit)
status: draft
created: 2026-06-23
updated: 2026-06-23
epics: []
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
  tape adapter, so it carries an explicit `After #<Epic-1 umbrella>`.

```
Epic 1 (tape canonicalisation) ──┐
Epic 3 (seam hardening)  ─────────┼─ all parallel
Epic 2 (IT/ES extraction) ────────┘  except 2c (validation) After Epic 1
```

## Decomposition

<filled in Phase 2>

## Filed issues

<filled in Phase 4>
