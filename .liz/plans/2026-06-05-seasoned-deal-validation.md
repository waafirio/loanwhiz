---
id: 2026-06-05-seasoned-deal-validation
title: seasoned-deal-validation
status: draft        # draft → decomposed → filed
created: 2026-06-05
updated: 2026-06-05
epics: []            # umbrella issue numbers, filled in phase 4
---

# seasoned-deal-validation

## Context & intent

### What this is

Load the **real, seasoned ING Green Lion deals — 2023-1 and 2024-1 — into LoanWhiz as
first-class selectable deals, and validate our model against their actual published
reports.** These are genuinely separate deals (from each other and from the synthetic
demo deal, Green Lion 2026-1) — each is validated only against *its own* data; there
is no cross-deal splicing.

### Why this exists (the two payoffs)

1. **Gold-standard external validation of the waterfall engine — on real liability
   actuals.** During the spine's S0 gate we established that the synthetic 2026-1
   deal has *no* liability ground truth (its quarterly Notes & Cash report's first
   period falls after our data window), so 2026-1's liabilities can only be
   invariant-validated (option A). But the **seasoned** deals (2023-1 since 2023,
   2024-1 since 2024) each publish a full quarterly **DSA Notes & Cash Report** —
   Bond Report (note balances), **Revenue & Redemption Priority of Payments with
   actual per-step distributions**, Issuer Transaction Accounts (reserve/cash), and
   Transaction Triggers. That is exactly the liability ground truth 2026-1 lacks.
   Crucially, the report publishes **both the available funds and the resulting
   distributions**, so we can feed its *own* available funds into our model-driven
   waterfall interpreter and check it reproduces its *own* published distributions —
   **proving the engine on real data, to the cent, without needing the loan tapes.**

2. **It makes "data-agnostic / multi-deal" *real*.** The potemkin audit
   (`DEMO-RISKS.md`) flagged "add any RMBS, no code change" as an over-claim — the
   registry plumbing exists but ships one deal and the runner hardcodes Green Lion.
   Registering 2023-1 and 2024-1 as working, selectable deals (each with its own
   extracted model) demonstrates genuine generality and retires the single-deal
   selector potemkin — for real, not by softening the claim.

### The hard data constraint (and how it shapes the work)

For the seasoned deals we have, **publicly**: the prospectus, the monthly
**collateral** investor reports (pool-level aggregates), and the quarterly **Notes &
Cash** (liability) reports. We do **NOT** have their loan-level ESMA tapes (those live
in a private securitisation repository). That constraint is why "Both" validation
modes the operator chose split cleanly:

- **Engine validation (strong, self-contained):** notes-cash report's *own* available
  revenue/principal funds → our interpreter (using that deal's extracted prospectus
  waterfall) → reconcile to the report's *own* published per-step distributions,
  across the full quarterly history. Needs no tapes. This is the headline proof.
- **Pool-level full pipeline (weaker, end-to-end):** reconstruct collateral state from
  the pool-level monthly collateral reports and run the waterfall end-to-end →
  compare to the notes-cash actuals. Honest but coarser (pool-level, not loan-level),
  so labelled as such.

### Why this shape, and what was rejected

- **Rejected: use a seasoned deal's liabilities to "complete" 2026-1's proof.** That
  splices two different pools and proves nothing about either — explicitly ruled out
  by the operator. Each deal stands alone; 2026-1's proof remains option A.
- **Rejected: loan-level reconstruction of the seasoned deals.** Impossible without
  their tapes; the notes-cash report's published available-funds line is the right
  (and sufficient) input for engine validation.
- **Chosen surface: selectable UI deals** (not just an internal harness) — because the
  second payoff (real multi-deal) only lands if a judge can *select* 2024-1 and see
  it work, and the validation view is far more convincing shown in-product.

### Cross-epic narrative & sequencing

This epic is a **stretch after the spine** (operator decision) and **hard-depends on
the spine's model-driven waterfall interpreter, S4 (#184)** — there is nothing to
validate until the interpreter executes an extracted deal's waterfall. It also reuses
the spine's `DealState` (S1/#181), report-ingestion patterns (S2/#182), and trigger
engine (S5/#185). It complements the **demo-readiness** epic's D5 (deal-selector
robustness, #198) — the real seasoned deals are what make multi-deal support
meaningful. Build order within the epic: register → extract models + build the
notes-cash parser (parallel) → engine-validation harness + pool-level pipeline → UI.

## Decomposition

<Filled in phase 2.>

## Filed issues

<Filled in phase 4 — the artifact↔issue link.>
