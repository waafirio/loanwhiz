---
id: 2026-06-27-ground-truth-quality-engine
title: Ground-truth quality engine for cross-deal testing
status: draft
created: 2026-06-27
updated: 2026-06-27
epics: []
---

# Ground-truth quality engine for cross-deal testing

## Context & intent

**The problem.** LoanWhiz is only useful as a *framework* if you can point it
at many deals and get an honest, automatic readout of what works and what
doesn't. Today that readout is `capability_matrix.py`'s 3-state grid
(`validated` / `ran` / `not-applicable`), which auto-enumerates every
(deal × primitive) cell from real inputs — but it measures **presence, not
quality**. A cell reads `ran` if a deal merely *has* extracted waterfall steps,
not whether the engine reproduces that deal's actual cashflows. Only one cell is
`validated` (green-lion-2024-1), and only because someone hand-built its
reconciliation fixture (`_VALIDATION_BUILDERS`). So quality grading does **not**
scale: add 20 deals and you get 20 more `ran`/`not-applicable` columns and zero
new quality signal.

**What we want.** A testing engine that systematically runs every deal in the
registry through the full pipeline (extraction → execution → reconciliation) and
**grades the output against each deal's own published investor-report figures, to
tolerance** — generalizing the green-lion-2024-1 to-the-cent reconciliation from
a hand-coded one-off into a data-driven, auto-scaling quality scorecard.

**Why this shape (option A — ground-truth reconciliation).** We explicitly chose
rigorous reconciliation over the alternatives:

- *Rejected — LLM-as-judge (option B):* grading extraction faithfulness with a
  model judge scales to any deal with zero fixtures, but it is soft, probabilistic,
  and costs LLM calls on every run. For a structured-finance engine whose whole
  credibility rests on "reproduced a real deal to the cent," a probabilistic grade
  is the wrong primary signal.
- *Rejected — tiered (option C):* a fine eventual shape, but it dilutes the first
  build with a second (LLM) grading path. Land the rigorous spine first; a Tier-2
  judge can be added later without rework.

Ground truth = each deal's **published investor reports** (Notes & Cash Priority
of Payments line items, covenant test results, pool statistics). That is the
deal's own answer key, and reconciling engine output against it is real proof,
not a vibe.

**The load-bearing prerequisite.** Per `MODELING-GAPS.md` A1, the waterfall
*runner* ignores `DealModel.waterfalls` and hard-codes Green Lion's 11-step
revenue + 4-step redemption cascade (`period_state_machine.py`
`DEFAULT_REVENUE_STEPS` / `DEFAULT_REDEMPTION_STEPS`). `run_period` already
*accepts* `revenue_steps` / `redemption_steps`, but the API callers
(`api/main.py` projection + waterfall endpoints, ~line 876) never pass the deal's
extracted steps. Consequence: running *any non-Green-Lion deal's* waterfall
through the engine secretly grades Green Lion's cascade on that deal's data — so
a cross-deal execution-quality score is hollow until the runner consumes
extracted steps. **This must land before the harness produces meaningful
execution grades.**

**Build on what exists — do not rebuild.** The organs are already here; this epic
wires them into one driver:
- `src/loanwhiz/primitives/capability_matrix.py` — auto-enumerates (deal × primitive) cells; extend its 3-state output into a graded quality matrix.
- `tests/breadth_harness.py` — already executes the deal-facing primitives across the whole registry, offline & deterministic, with honest not-applicable reasons. The quality_harness is its quality-grading sibling.
- `src/loanwhiz/primitives/reconciliation_gate.py` + `reconciliation_harness.py` — the reconcile-to-the-cent core (engine output ↔ published report). Generalize from one hand-built deal to data-driven per-deal answer keys.
- `pool_pipeline_harness.py` — coarse pool-level characterisation, reusable for pool-stat checks.

**Constraints / principles.**
- Offline & deterministic where possible: the reconciliation core is offline;
  extraction is the only LLM-dependent step (gate it `integration`/`slow`).
- Preserve the honesty discipline (#193): no wall of green; every
  `not-applicable` cell carries a real, non-empty reason.
- Python 3.12; **no CI workflows exist**, so the test suite is the only gate —
  every leg ships regression-pinned tests. Run local tests with
  `PYTHONPATH=src` (editable-install shadow gotcha).

## Decomposition

### Epic: Ground-truth quality engine for cross-deal testing   (umbrella #<N — filled in phase 4>)

One epic, four children. Children 1 and 2 are independent and run in parallel;
child 3 (the driver) integrates both and is where the engine actually comes
together; child 4 backfills real answer keys so the scorecard shows graded
results rather than just structure. Cross-child ordering: 3 depends on 1 (for
meaningful execution grades) **and** 2 (for the answer-key format); 4 depends on
2 (format) and 3 (driver).

- **Waterfall runner consumes extracted DealModel.waterfalls steps** — Make the
  waterfall execution path pass each deal's *extracted* revenue/redemption steps
  to `run_period` instead of the hard-coded Green Lion defaults (MODELING-GAPS
  A1), so non-Green-Lion deals execute their own cascade. Sequencing: parallel.
  Paths: `src/loanwhiz/api/main.py`, `src/loanwhiz/primitives/period_state_machine.py`.
- **Per-deal ground-truth answer-key format** — Define a data-driven per-deal
  answer key (published Notes & Cash PoP line items, covenant test results, pool
  stats) attachable via the registry/seed, generalizing the hand-built
  green-lion-2024-1 `_VALIDATION_BUILDERS` into config-loaded ground truth a
  reconciler can consume. Sequencing: parallel. Paths:
  `src/loanwhiz/data/**`, `src/loanwhiz/config.py`, `src/loanwhiz/primitives/reconciliation_*.py`.
- **quality_harness driver + graded scorecard API** — Enumerate the whole
  `DEAL_REGISTRY`, run each deal extraction→execution→reconciliation, grade each
  (deal × primitive × check) against its answer key to tolerance
  (pass/fail/score + evidence + honest not-applicable reasons), regression-pinned
  like the breadth harness, and surface it via an API endpoint extending
  `/capability-matrix` into a quality matrix. Sequencing: sequential. After
  #<child-2> (needs the answer-key format; also needs #<child-1> landed for
  meaningful execution grades). Paths: `src/loanwhiz/primitives/**`,
  `src/loanwhiz/api/main.py`, `tests/**`.
- **Backfill answer keys for existing deals with published reports** — Author
  ground-truth answer keys (in the new format) for the existing deals that have
  published reports (Green Lion vintages at minimum) so the scorecard shows real
  graded results, not just structure. Sequencing: sequential. After #<child-3>.
  Paths: `src/loanwhiz/data/**`.

## Filed issues

<Filled in phase 4 — the artifact↔issue link.>
