# Quality grading & ground-truth answer keys

<!-- Agent-appended entries below. Append only; never reorder or rewrite an
     existing entry (`merge=union` would duplicate it rather than replace it). -->

## 2026-09-02 · gotcha · #440

Backfilling a deal's ground truth is a **two-part** change, not one. Committing
`data/deals/answer_keys/<slug>.json` only gives the deal a key; `/quality-matrix`
still grades it `not-applicable` until the deal is ALSO registered in
`quality_harness._default_series_provider()`, because the harness folds the
engine series from a committed offline builder — the answer key carries published
ground truth, never the opening balances the fold seeds from. Add the
`fold_<deal>()` builder in `reconciler.py` and its `builders[...]` entry in the
same PR, then assert a `passed` cell; a key alone silently grades nothing.

Refs: #440
Refs: #492 — same pairing needed when the capability matrix left the builder map.
Refs: #495 — committing a CLO's PoP key changed no cell's grade, only its reason.
Refs: #496 — the pairing is unavailable when the key's period count exceeds the foldable documents.

## 2026-09-05 · pitfall · #457

A not-applicable reason is an **assertion about the world**, and one literal
covering a whole branch will be false for some member of it. Say only what the
input encodes: an empty `tape_urls` means no tape is registered, never that the
deal publishes no loan-level data. Split two causes on a **registry fact**, never
a deal id — "nothing is published" claims grading is impossible, "no answer key
is authored" names a deferred decision. Assert the retracted wording is *absent*;
a test checking only the state passes while the prose lies.

Refs: #457
Refs: #483 — re-learned the moment the branch grew a second member.

## 2026-09-06 · pitfall · #471

Supplying the missing input moves the overclaim into the **positive** branch.
When a cell reads `not-applicable` for want of an input and you finally provide
one, the reason that replaces it is the new risk: check every *other*
precondition the capability needs before reporting it ran, or the cell claims
work the endpoint still refuses. Registering a loan tape flipped one cell
honestly and would have flipped a second onto a reconstruction that 422s for
want of structural config. Say which precondition is missing, never re-use the
"no input" wording — those are different findings.

Refs: #471

## 2026-09-06 · gotcha · #471

A grep-for-absence over prose must ban the **claim, not a fragment of it**: a
reason ending "it does not claim the deal publishes no loan-level detail" trips
a naive `"publishes no loan"` ban, so the test flags the sentence that *refuses*
the claim. Ban the whole assertion, lower-cased, and let the retracted wording
list be the specification.

Refs: #471

## 2026-09-08 · pitfall · #492

Replacing a hand-built map with the registry it generalizes is **not**
behaviour-preserving by default. Enumerate every row the DATA admits that the
map did not, before accepting a framing where one named regression covers the
swap. Green Lion 2023-1 had held a PoP-bearing key and an offline fold since
#440, so converging `/capability-matrix` moved it `not-applicable` →
`validated` and falsified the "1 validated" tally four committed docs restate.
Grep every transcription of the old count and fix it in the same PR: a
converged surface whose docs still quote the map's answer claims worse.

Refs: #492
## 2026-09-08 · pitfall · #481

Before publishing a graded cell, check whether the published outcomes **vary**:
ground truth in which everything passed cannot tell a correct engine from one
that never fires, and the grade reads identical either way. State that bound
where the grade is published. Apply the mutant that should NOT red alongside
those that should — what a cell cannot detect is the honest half of the finding.

Refs: #481

## 2026-09-08 · pattern · #495

When one entity's ground truth comes from **several documents**, give each
document its own constructor and union them — never one reading both, or neither
document's regressions have anything left to hold.

Assert what each record may claim **per period, not per key**. A key-level "this
key carries no Priority of Payments" stops covering anything the moment a second
document supplies one; the per-period form gets *stricter*, because it also pins
that the two documents stay unmixed.

Refs: #495

## 2026-09-08 · pitfall · #496

A reconciliation can pass every step and still be wrong twice over. Assert three
things beside the deltas, never `steps_passed` alone: the **tie-out** (distributed
+ rounding == available funds — the join loses money silently); the **source
classification** (a step whose amount is taken from the report is compared to
itself, so zero engine-computed lines proves routing, not computation); and the
**unjoined published rows** (a label the report prints as sub-lettered components
matches no step, so a real payment reconciles 0.00-vs-0.00 against its parent).
On a failing grade, pin the figures and change neither side: the gap is it.

Refs: #496

## 2026-09-08 · pattern · #512

Route a published *input* through the document, not the answer key, even when
the key is where the figure was committed. `quality_harness` folds from the
**series provider**, never the key, so an issue phrased "wire the key's
`pool_stats` into the fold" is satisfied by reading the document the key was
authored from and asserting the two equal in a test. The key stays ground truth
and becomes checked rather than ungraded; putting it on the engine's input side
would make the grade partly self-referential.

Refs: #512

## 2026-09-08 · pitfall · #513

When a grader covers only **part** of a collection, make the ungraded part a
**different kind of record** — never an instance of the graded kind with empty
contents. An empty instance passes (`all([])` is `True`; a zero total ties out
against a zero pot), so "graded and passed" and "nothing to compare" become one
output and the ungraded fraction reads as free green. Keep the graded list
graded — every count and verdict reads it — and give skips their own list and
reason. Prefer this to widening the *inputs*: the shape refused may be correct.

Refs: #513
Refs: #494 — same failure one level down: presence and correctness differ.
