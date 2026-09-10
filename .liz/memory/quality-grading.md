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
Refs: #514 — closing the join turned the vacuous pass into a visible failure.
Refs: #538 — the tie-out itself goes blind where a residual sweep absorbs.
Refs: #515 — re-measured independently; all four hold and the count is the bound.

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

## 2026-09-09 · pitfall · #514

Fixing one copy of a mirrored function moves no number when the two copies sit on
**opposite sides of the same comparison**. `report_adapter`'s report-to-cascade
fold becomes each report-supplied step's *need*; `reconciler`'s becomes the
published figure that need is checked against, so repairing only the reconciler's
leaves the engine distributing the same total and merely turns vacuous
0.00-vs-0.00 passes into failures. Before changing a reader, trace whether its
twin feeds the *other* side of the assertion: if it does, the fix is one shared
function, not two that agree.

Refs: #514

## 2026-09-09 · pitfall · #538

A cascade with a residual sweep cannot report a step-level error in its total.
The pot is fixed, so a step that over-claims is funded by starving the sweep
beneath it and `distributed == available` still holds: the tie-out #496 asks for
goes green at the exact moment two steps become wrong by equal and opposite
amounts. Read a tie-out as "no money escaped", never as "every step is right",
and keep `steps_passed` and the per-step deltas as the completeness check. When
a gap does split this way, assert both deltas AND that they cancel — a later fix
to one then reds the pair instead of silently re-balancing the total.

Refs: #538
Refs: #564 — same rule for a bucket's obligor-tier split, not just a cascade.

## 2026-09-09 · pitfall · #515

Read an `engine_computed` count as a property of the **declaration**, not of the
deal: the classifier answers from an authored allowlist, so it stops where that
list stops rather than where the data does. Cairn's Classes D, E and F reach the
fold with a seeded balance, a published applied rate and a parsed day count and
reproduce their published interest to the cent, yet grade `report-supplied`
because `ENGINE_COMPUTED_RECIPIENTS` ends at `class_c_interest`. Before
publishing the count, check what the *un*counted members lack; if the answer is
"nothing" say so beside the figure, and never widen the set to raise it.

Refs: #515
Refs: #598 — the answer was "nothing"; the authored set was deleted, not widened.


## 2026-09-09 · decision · #534

Keep "carries a committed answer key" and "whose graded cell passes" as two
sets, never one. Committing genuine ground truth for a deal the engine cannot
yet grade reds any tally asserting key-count == pass-count, and the cheap fix is
to withhold the key — suppressing real ground truth to protect a green number.
Split the sets, and assert the *reason* the unmatched deal is not-applicable —
its unmatched names, not its grade — so the cell says why it did not grade and a
later fix reds the line instead of passing quietly.

Refs: #534

## 2026-09-09 · pitfall · #525

Before treating a per-subject count as evidence of a defect, compute the same
count for a subject you already trust. "46 of 55 structural rows are blank for
the CLO" survived an epic body, a child issue and a dispatch as a measured gap
the coupon work would close; the same query against both externally validated
deals returns the same blanks, because the null is `StructuralCell.value` — a
cross-deal comparable scalar that a waterfall step or qualitative trigger has
for no deal. A count with no control subject measures the schema, not the deal.
The control is one query, and it runs before the fix, not after it fails.

Refs: #525

## 2026-09-09 · pitfall · #535

A capability verified on one deal is a claim about that deal, not the asset
class — and the general form reaches you through epic body, issue and dispatch
alike. Probe every screen per deal id before asserting against it: "the CLO
screens now serve 200" was true of the first CLO and false of the second, which
still 422s on an input its extraction never read. Where the second subject
stops is the finding; a test pinning that stopping point by name beats one
asserting the symmetry that was expected.

Refs: #535

## 2026-09-10 · pitfall · #565

A ban over source text is unfalsified until a mutant proves it fires, and the
survivors keep the identifier while moving the render: a count demoted out of
its `<Badge>` into prose below the figure, a block put behind `{false ? … :
null}`. Both leave every grepped token in the file. Slice the element you mean
(`<Badge>…</Badge>`, one `<dd>`) instead of grepping it, ban a literal
condition, and assert every check is the one catching some mutant — a rule no
mutant reaches has never been observed to fail.

Refs: #565
Refs: #562 — the same silence one layer down: a checker nothing calls.
## 2026-09-09 · pitfall · #567

What an extractor *can* read and what the committed data *carries* are two
facts, and a consumer built over the registry sees only the second. #566's
fixtures prove its parser reads both CLOs' retention undertakings, yet only
Cairn's seed carries the parsed block — so a record over the registry verifies
one deal and must refuse the other by name. Grep the committed artefacts for
the key before building on "the extractor handles X": a brief summarising an
extractor's reach describes the parser, not the corpus.

Refs: #567
Refs: #535 — the per-deal form of the same gap.
