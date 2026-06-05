---
id: 2026-06-05-model-builder-spine
title: model-builder-spine
status: draft        # draft → decomposed → filed
created: 2026-06-05
updated: 2026-06-05
epics: []            # umbrella issue numbers, filled in phase 4
---

# model-builder-spine

## Context & intent

### What this is

A deep modeling audit (2026-06-05, captured in `MODELING-GAPS.md`) found that
LoanWhiz's structured-finance modeling is largely **decorative**: the extracted
deal model never drives the cashflow computation (the `WaterfallRunner` hardcodes
Green Lion's steps and ignores `DealModel.waterfalls`), and the period-to-period
state machine that *would* reconstruct PDL / reserve / tranche balances over time
(`MultiPeriodWaterfallRunner` + `WaterfallState`) is fully built but **never
wired into the API** — so PDLs and losses are permanently 0, tranche balances
never amortize, and `/waterfall`, `/compliance`, `/project` each compute
disconnected snapshots from three divergent sources. The empty Compliance
"proximity across periods" chart is a symptom of this, not the disease.

This epic builds **the spine**: a single, deterministic, period-by-period
`DealState` ledger that is reconstructed forward from actual data and **validated
against ground truth (the investor reports)** — i.e. a *provably comprehensive
and correct* model-builder, not a demo that merely renders.

### Why this shape

The operator's goal — "provably comprehensive and correct" — is the design
constraint, and it simplifies the architecture by forcing two things:

1. **One canonical state object.** Every structural quantity (tranche balances,
   per-class PDL ledgers, reserve balance + target, cumulative losses, pool
   factor, revolving flag) lives in a single per-period `DealState`. Period-N
   *closing* state == period-N+1 *opening* state. All endpoints read this one
   ledger instead of three sets of constants. This kills the
   mutually-inconsistent-snapshots class of bug at the root.

2. **A ground truth to check against.** "Provable" means an automated PASS/FAIL
   gate, not a vibe:
   - **Correct** = the reconstructed tranche distributions, PDL, reserve, and
     balances *reconcile to the investor reports* within tolerance.
   - **Comprehensive** = invariants assert every extracted waterfall step
     executed, funds are conserved (Σ distributions + shortfall == available),
     PDL/balances are non-negative, and closing[N] == opening[N+1]. This replaces
     the meaningless `completeness_score` (which can read 1.0 with zero steps).

The data flow the spine implements:

```
seed (investor report, period-0)
  → for each period:
       opening DealState
       + collections/losses derived from the tape        (S3)
       → run the waterfall by interpreting the model     (S4)
         with conditional steps gated by triggers        (S5)
       → update PDL / reserve / balances → closing state (S6)
  → reconcile computed vs reported actuals  ⇒ PASS/FAIL   (S7)
  → assert comprehensiveness invariants                  (S8)
  → all endpoints read the one ledger                    (S9)
```

### The load-bearing risk, and how we gate it (operator decision)

The proof is only as strong as the ground truth. **S0 is a consistency spike**
that runs FIRST and gates everything downstream: does Algoritmica's synthetic
investor-report data actually tie to the tapes via the waterfall? Two outcomes
were weighed for "if it doesn't tie":

- **Reports = spec** (back out implied assumptions so the model reconciles to the
  reports — what a real analyst trusts), vs
- **Tapes = spec** (compute first-principles from tapes, flag report deltas as
  findings).

**Operator decision: do not pre-commit — decide after the spike.** S0 produces a
findings + recommendation that the operator reviews before the dependent inputs
(S2/S3) and the reconciliation harness (S7) are built. So S0 → operator decision
→ rest. This is an explicit `liz:hold`-style gate, by design.

### Single deal now, generalisable components

The operator's steer: we are **not** chasing a second deal yet — but we build the
components data-driven so generalisation is free later. Concretely: the
**waterfall interpreter (S4)** executes `DealModel.waterfalls[*].steps`
generically (a recipient→need-calculator registry + condition→predicate eval), and
the **trigger engine (S5)** evaluates predicates over `DealState`; Green Lion's
specifics live in the extracted model / a per-deal structural spec, not in Python
branches. So the *engines* are deal-agnostic while the *demo* is single-deal.

### Alternatives considered and rejected

- **Fold forward projection into this epic** — rejected. Reconstructing the
  deal's actual past (and proving it) is a different concern from predicting its
  future. Projection (route `/project` through the real `CashflowProjector`, real
  CPR/CDR/severity/rate scenarios, wire it off the latest reconstructed state)
  becomes a **separate fast-follow epic** that sits on this spine. Keeping them
  apart keeps the proof tight.
- **Keep patching the hardcoded runner** — rejected. The hardcoded runner is the
  disease; incremental patches (e.g. just feeding PDL into one endpoint) leave the
  three-divergent-sources inconsistency and never become provable.
- **Quick metric-alias fix for the empty chart** — that one-file change is noted
  in `MODELING-GAPS.md` as a stopgap if a live demo is needed before this lands;
  it is NOT a substitute for S5/S6 and is out of scope here.

### Cross-child ordering rationale

`S0` (spike, gates the proof approach) → `S1` (the `DealState` schema everything
imports) → then the four inputs `S2/S3/S4/S5` fan out in parallel → `S6` (the
state machine that integrates them) → then `S7/S8/S9` (prove + surface) fan out.
`S2/S3` and the reconciliation `S7` carry a soft dependency on S0's operator
decision (reports-vs-tapes-as-spec), captured narratively here and as `After`
links in the decomposition.

## Decomposition

<Filled in phase 2.>

## Filed issues

<Filled in phase 4 — the artifact↔issue link.>
