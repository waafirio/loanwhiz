---
id: 2026-09-08-clo-engine-computed-validation
title: "Make the CLO's reconciliation an engine claim: close the four gaps #496 measured"
status: filed
created: 2026-09-08
updated: 2026-09-08
epics: [510]
---

# Make the CLO's reconciliation an engine claim: close the four gaps #496 measured

## Context & intent

Epic #491 asked whether the engine reproduces Cairn CLO XVII's published
Priority of Payments. #496 ran the grade and answered **no**, without tuning
the answer key or the engine to reach a green cell. The 29-step Interest
cascade distributes EUR 5,434,911.93 of the report's stated EUR 7,255,062.35,
leaving **EUR 1,820,150.42 — 25% of the period — undistributed**.

That verdict is this epic's input. It is unusually well-specified: #496 did not
report "it does not tie", it named the mechanism of each gap. This epic closes
the four, and is therefore engineering rather than research.

### The four gaps, as measured

**1. The recipient spelling never reaches the classifier.**
`step_source_classifier.py:102` tests `recipient in ENGINE_COMPUTED_RECIPIENTS`
against the **raw extracted** recipient string. Cairn's cascade carries
`class_a_notes_interest`; the set holds `class_a_interest`. #503 built exactly
the bridge between those two vocabularies — `_canonical_recipient` — and this
call site does not use it. So every Cairn step falls through to the `else`, and
the following line is where the damage is done:

```python
source[recipient] = "report-supplied"
overrides[recipient] = report_amounts.get(label, 0.0)
```

The step's *need is overwritten with the report's own figure*. That is the
mechanism by which the report is compared against itself, and it is why 52
matching steps constitute no evidence at all.

**2. The published rates are committed and nothing reads them.** #495 recorded
each class's published all-in applied rate into the key's `pool_stats` —
`applied_rate_class_a: 5.008`, `class_b_1: 5.958`, `class_b_2: 6.87`,
`class_c: 6.808`. A grep of `src/` finds no consumer. Meanwhile the seed's
tranche rate is the prospectus string `"3 month EURIBOR + 1.80%"`, which
`capital_structure.numeric_rate_pct` correctly refuses to coerce. So even with
gap 1 closed, `_make_tranche_interest_need` has no rate and #493 makes it
refuse — correctly, and uselessly. The number exists; there is no path from the
answer key to `TrancheFunds.rate_pct`.

**3. Twenty of the report's sixty-two published rows join no cascade step.**
They carry sub-labels the cascade's parent labels do not match — `(A)(i)`,
`(A)(ii)`, `(H)(i)`, `(H)(ii)`, `(CC)(1)(a)`, and two the report re-letters
bare `(a)`. `reconciler._fold_report_revenue_steps` folds only Green Lion's
purely-numeric `(b)(1..n)` wrap artefact. Eight of the twenty carry money and
account for the EUR 1,820,150.42 gap **to the cent**.

**4. The key's period union blocks the grade entirely.** The key unions four
periods — three authored from trustee reports, which publish no Priority of
Payments, and one from the Note Valuation Report, which does — so
`reconcile_against_answer_key` raises a join mismatch before comparing a single
figure. #496 reached its verdict by folding the committed report directly,
bypassing the key.

### Why this shape

**Gap 1 before gap 2, and both before gap 3.** Fixing the fold (3) while every
step is still report-supplied would make the cascade tie *to the cent against
itself* — a green cell that means nothing, and the most dangerous intermediate
state this work can pass through. Gaps 1 and 2 together are what make a single
line engine-computed; only then does closing the fold measure anything. This
ordering is not a preference, it is the difference between a proof and a
tautology.

**The residual sweep is explicitly ruled out.** #496 named it as the fix its
failure invites and rejected it: sweeping the EUR 1.82m into the residual step
would pay named recipients' money to the wrong party and turn a visible failure
silent. No child may close gap 3 that way.

**Using the report's published rate is legitimate; using its amount is not.** A
coupon rate is a deal input, exactly as a tranche balance is, and Green Lion
works identically — `class_a_rate_pct` is an input to `WaterfallFunds` and the
engine computes `balance × rate/100 / 360 × days` from it. What is circular is
taking the report's *distributed amount* as the need, which is what happens
today. Gap 2 must wire the rate, never the amount.

### The honest limit on what "validated" can mean here

A CLO's waterfall carries many steps no deal model can derive — management
fees, administrative expense caps, coverage-test cures. Those remain
report-supplied by their nature, and no amount of work changes that. So a
validated CLO rests on a **narrower engine-computed core** than a validated
RMBS: the note interest and note principal. That is a real difference in what
the word would mean for this deal, and it belongs in the data card rather than
in a footnote nobody reads. This epic must not let "validated" quietly come to
mean something weaker than it means for Green Lion without saying so.

### What this epic does not promise

That Cairn will reconcile. Closing all four gaps makes the question *askable*
on the engine's own terms for the first time; it does not decide the answer.
The cascade may still disagree, and if it does that remains the finding. The
same discipline #491 established holds: no child may adjust the answer key, and
the child that grades is not the child that wires.

## Decomposition

### Epic: Make the CLO's reconciliation an engine claim   (umbrella #510)

- **Canonicalise the recipient before classifying a step's source** — Make
  `step_source_classifier` resolve a step's recipient through
  `_canonical_recipient` before testing `ENGINE_COMPUTED_RECIPIENTS`, so a
  deal spelling its recipients in the document's vocabulary is not silently
  reclassified report-supplied and handed its own answer. Sequencing: parallel.
  Paths: `src/loanwhiz/primitives/step_source_classifier.py`, `tests/**`.
- **Wire the published applied rates onto the engine's tranches** — Give the
  fold a path from the answer key's `applied_rate_<class>` pool stats to
  `TrancheFunds.rate_pct`, so a class whose prospectus coupon is a floating
  margin can still accrue from the period's published all-in rate. Sequencing:
  parallel. Paths: `src/loanwhiz/primitives/reconciler.py`,
  `src/loanwhiz/primitives/period_state_machine.py`, `tests/**`.
- **Fold the report's sub-labelled rows onto their parent steps** — Generalise
  `_fold_report_revenue_steps` beyond Green Lion's numeric `(b)(1..n)` wrap to
  the alphabetic and mixed sub-labels this report uses, so the 20 unjoined rows
  reach the steps they belong to — without a residual sweep. Sequencing:
  sequential. After the two above. Paths:
  `src/loanwhiz/primitives/reconciler.py`, `tests/**`.
- **Grade the key's PoP-bearing periods instead of refusing the union** — Make
  `reconcile_against_answer_key` reconcile the periods that carry a Priority of
  Payments and report the others as not-applicable with a real reason, rather
  than raising a join mismatch for the whole key. Sequencing: parallel. Paths:
  `src/loanwhiz/primitives/reconciliation_answer_key.py`, `tests/**`.
- **Re-grade and record the verdict** — Re-run the grade with all four gaps
  closed and report what it says, pass or fail, updating `docs/data-card.md`
  and `docs/model-card.md` — including what a validated CLO's narrower
  engine-computed core does and does not claim. Sequencing: sequential. After
  all of the above. Paths: `docs/**`, `tests/**`.

## Filed issues

- Epic "Make the CLO's reconciliation an engine claim" → umbrella **#510**
  - #511 Canonicalise the recipient before classifying a step's source — parallel, `liz:priority:1`
  - #512 Wire the published applied rates onto the engine's tranches — parallel, `liz:priority:1`
  - #513 Grade the key's PoP-bearing periods instead of refusing the union — parallel, `liz:priority:1`
  - #514 Fold the report's sub-labelled rows onto their parent steps — sequential, after #512
  - #515 Re-grade the CLO and record the verdict — sequential, after #514
