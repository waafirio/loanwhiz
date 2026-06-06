---
id: 2026-06-05-model-builder-spine
title: model-builder-spine
status: filed        # draft → decomposed → filed
created: 2026-06-05
updated: 2026-06-05
epics: [179]         # umbrella issue numbers, filled in phase 4
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

### Epic: Provably-correct deal model-builder (the spine)   (umbrella #<N>)

Reconstruct a single deterministic per-period `DealState` ledger from actual
data and prove it against the investor reports + invariants. Single deal
(Green Lion 2026-1); engines built data-driven so generalisation is free.
Forward projection is explicitly a separate fast-follow epic, not here.

- **S0 — Ground-truth consistency spike** — determine whether the 3 investor
  reports' figures (tranche balances/distributions, PDL, reserve, pool balance)
  reconcile to the loan tapes via the waterfall, and recommend reports-vs-tapes
  as the authoritative spec. Sequencing: sequential. Paths: `scripts/**`, `docs/**`.
  _(Gate: produces a findings + recommendation the operator approves before S2/S3/S7.)_
- **S1 — Canonical DealState + period-transition schema** — define the single
  per-period `DealState` (tranche balances, per-class PDL ledgers, reserve
  balance+target, cumulative losses, pool factor, revolving flag) and the
  opening→closing transition contract every engine and endpoint reads.
  Sequencing: sequential. After S0. Paths: `src/loanwhiz/primitives/waterfall_state.py`.
- **S2 — Investor-report ingestion → ground-truth ledger** — parse the 3 monthly
  investor reports into structured per-period actuals usable as both the
  period-0 seed and the reconciliation target. Sequencing: parallel. After S1.
  (Soft dep: shaped by S0's reports-vs-tapes decision.) Paths:
  `src/loanwhiz/primitives/report_verifier.py`, `src/loanwhiz/extraction/**`.
- **S3 — Collections & loss engine from the tape** — derive per-period interest
  (ex-arrears/defaults), scheduled principal, prepayment, recovery, and realized
  losses from the ESMA tape, properly separated, as waterfall inputs.
  Sequencing: parallel. After S1. (Soft dep: S0 decision.) Paths:
  `src/loanwhiz/primitives/collections_aggregator.py`, `src/loanwhiz/primitives/esma_tape_normaliser.py`.
- **S4 — Model-driven waterfall interpreter** — replace the hardcoded runner with
  a generic interpreter over `DealModel.waterfalls[*].steps`: recipient→need
  registry, condition→predicate eval, pari-passu groups (the reusable core).
  Sequencing: parallel. After S1. Paths: `src/loanwhiz/primitives/waterfall_runner.py`.
- **S5 — Trigger/covenant evaluation engine** — evaluate triggers as predicates
  over `DealState` (sequential-pay, PDL, reserve, clean-up, cumulative-loss),
  fixing the extractor↔monitor metric vocabulary and plumbing structural state;
  drives both conditional waterfall steps and `/compliance`. Sequencing: parallel.
  After S1. Paths: `src/loanwhiz/primitives/covenant_monitor.py`, `src/loanwhiz/api/main.py`.
- **S6 — Period-by-period state machine** — wire & correct
  `MultiPeriodWaterfallRunner` to thread opening→collections(S3)→waterfall(S4,
  gated by S5)→loss/PDL/reserve updates→closing across all periods, seeded from
  the report (per S0). Sequencing: sequential. After S2, S3, S4, S5. Paths:
  `src/loanwhiz/primitives/waterfall_state.py`, `src/loanwhiz/api/main.py`.
- **S7 — Reconciliation harness (the proof of correctness)** — compare the
  reconstructed state to investor-report actuals per reported period within
  tolerances → PASS/FAIL discrepancy report; wire `ReportVerifier`, fix its
  pool/reserve figures. Sequencing: parallel. After S6. Paths:
  `src/loanwhiz/primitives/report_verifier.py`, `tests/**`, `src/loanwhiz/api/main.py`.
- **S8 — Comprehensiveness invariants** — assert every extracted step executed,
  conservation of funds, non-negative PDL/balances, closing[N]==opening[N+1];
  replace the fake `completeness_score`/`extraction_confidence` with real coverage
  metrics. Sequencing: parallel. After S6. Paths: `src/loanwhiz/primitives/**`, `tests/**`.
- **S9 — Unify endpoints on the one ledger** — make `/waterfall` and
  `/compliance` (incl. the proximity-across-periods chart) read the single
  reconstructed `DealState` instead of divergent constants. Sequencing: parallel.
  After S6. Paths: `src/loanwhiz/api/main.py`, `web/**`.

## Filed issues

- Epic "Provably-correct deal model-builder (the spine)" → umbrella **#179**
  - #180 S0 — Ground-truth consistency spike (GATE)
  - #181 S1 — Canonical DealState + period-transition schema (after #180)
  - #182 S2 — Investor-report ingestion → ground-truth ledger (after #181)
  - #183 S3 — Collections & loss engine from the tape (after #181)
  - #184 S4 — Model-driven waterfall interpreter (after #181)
  - #185 S5 — Trigger/covenant evaluation engine (after #181)
  - #186 S6 — Period-by-period state machine (after #182–#185)
  - #187 S7 — Reconciliation harness (after #186)
  - #188 S8 — Comprehensiveness invariants (after #186)
  - #189 S9 — Unify endpoints on the one ledger (after #186)
