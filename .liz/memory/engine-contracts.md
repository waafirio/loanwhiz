# Engine contracts (closed enums, registries, need calculators)

<!-- Agent-appended entries below. Append only; never reorder or rewrite an
     existing entry (`merge=union` would duplicate it rather than replace it). -->

## 2026-09-05 · pattern · #453

When a registry backs a closed enum, guard **both** directions at import. A
refusing `register_*` stops a wrong entry, but the failure that ships is the
*missing* one and a registration guard is silent about it — assert coverage
too. Kill the lookup's default in the same pass: `.get(member, <fallback>)` is
how a newly added member becomes silently well-formed. Generate entries from
the enum where a family is regular, so no hand-kept letter range under-reaches.

Refs: #453
Refs: #459 — the registration half alone; the coverage half was still open after it.

## 2026-09-05 · pitfall · #453

Adding a member to a closed enum silently widens what an **LLM classifier** may
answer, because the classifier is handed that enum as its options. A near-miss
you deliberately excluded from the engine gains a fresh path onto the member you
just added — and a real number reaches the wrong party. Deny-list the excluded
string explicitly, checked *before* the alias/substring/LLM ladder. Test the
exclusion with the classifier **enabled**: `use_llm=False` asserts the guarantee
on the only path that could not have broken it.

Refs: #453

## 2026-09-06 · pitfall · #471

Before feeding a new tape/record shape into an existing analytics primitive, ask
what each derived metric returns when its column is **absent**. A boolean mask
that degrades to all-`False` reports a *clean* population, not an unknown one —
arrears buckets came back `current_pct: 100.0, default_pct: 0.0` at full
confidence for a tape whose source publishes no arrears column at all,
indistinguishable from a genuinely performing pool. Emit no key rather than a
bucket that means "nothing to report", and make "states it in no form" a
distinct branch from "states it as zero".

Refs: #471

## 2026-09-08 · pattern · #493

Assert a refusal in BOTH directions or it proves nothing. `not_evaluable` is
reached from several layers — unregistered recipient, absent tranche, missing
rate — so a test asserting only "this step refused" passes for whichever layer
refuses first, and keeps passing with the fix reverted. Pair it: supply the one
missing input, change nothing else, and assert the SAME step flips to
evaluable. Check which layer refuses first — a deal's extracted,
jurisdiction-native labels may resolve to no calculator, so the deal that
motivated the fix can be the one it does not change.

Refs: #493
Refs: #535 — same rule at the API refusal: a bare 422 outlived its cause.
## 2026-09-08 · pitfall · #478

Grep an untyped dict shape's **key names**, not a mapper's function name,
before changing one: such a shape gets built in several places and they drift
*asymmetrically*. Three built `capital_structure` here — one refused an
unmappable stack, one silently kept the top three and zero-filled, one was a
hand-copy — and the permissive one is both the dangerous one and the one no
test caught, since dropping classes shrinks a *denominator* and reads as
health. Give the shape a type and one builder, and put the "every input row
survived" check in the builder: a type is satisfied by a shorter list.

Refs: #478

## 2026-09-08 · pitfall · #481

A guard keyed on a **classification table** protects only the vocabulary that
table recognises — enumerate what falls outside it before trusting the guard.
`covenant_monitor` refuses a coverage test carrying no quantified threshold, but
only for names `taxonomy.coverage_metric_for` resolves.
`reinvestment_overcollateralisation_ratio` resolves to none, so it skips that
refusal and lands on `_is_triggered`'s `threshold is None` convention — "any
positive value fires" — reporting a healthy 108.68% ratio as breached. Feed the
guard every name the deal model carries and watch which miss.

Refs: #481

## 2026-09-08 · decision · #503

Whether an extracted string earns an **alias or a new enum member** is not
decided by how close the wording is — it is decided by whether the target's need
is engine-computed. Two steps of one cascade sharing a `funds_input` or
`calculator` member each claim the WHOLE amount, so a plausible alias double-pays;
sharing a `step_override` member is safe, because those needs are keyed by the
step's own label. Before aliasing onto a near neighbour, count how many steps of
the SAME cascade already resolve there — a CLO's capped and uncapped expense
tiers, and a class's coupon and its deferred interest, are distinct steps that
read as synonyms.

Refs: #503

## 2026-09-08 · pitfall · #503

Resolve a closed enum's **own values first**, before any alias/substring/LLM
ladder. A resolver that starts at its alias table answers `unmapped` for a member
the enum declares but no alias row names — so adding members left the engine
(exact lookup) and the extractor (ladder) returning different recipients for one
string, each internally consistent. Where two readers share a vocabulary but not
a code path, assert they agree over the real corpus: neither one's own tests can
see the disagreement.

Refs: #503
Refs: #549 — a docstring claiming "mirrors X" was the unchecked half.
Refs: #615 — same rule on a comparative verdict: `winner_deal_id` beside a note that the loser had no data.

## 2026-09-08 · pitfall · #511

Canonicalise **both sides** of a membership test, never only the incoming value.
A frozenset named for "recipients the engine can compute" read as canonical but
held three legacy spellings whose canonical forms were absent from it, so
resolving just the extracted name would have fixed the CLO steps and silently
reclassified the RMBS ones. Derive the compared set through the same resolver,
and exclude its "recognised but unplaceable" answer — letting `unmapped` into a
computable set would classify every denied string computable at once. Check what
a declared set is spelled in before trusting the name it is filed under.

Refs: #511
## 2026-09-08 · gotcha · #512

Check what the **report path** actually seeds before wiring a per-class input
onto it. `ReportAdapter.seed` iterates `DEFAULT_TRANCHE_CLASSES` — the canonical
`class_a/b/c` triple — and `_funds_from_state` looks inputs up by **tranche
name**, so a deal whose stack is `class_b_1`/`class_d`..`class_f` has no tranche
to attach to. A complete, correct input map can reach nothing with no error
anywhere: the class just keeps refusing. Assert the map and the per-tranche
arrival separately; one passing does not imply the other.

Refs: #512

## 2026-09-09 · pitfall · #520

A defect catalogue keyed on **one syntax misses the same bug written in
another**. #478 listed three sites collapsing an N-class stack, all found by
grepping *dict keys*; a fourth survived in `_primitives_seed_from_report_seed`,
spelled as constructor **arguments** (`class_a_balance=`) that grep could not
reach. Corollary: generalising a list off its producer does not generalise its
consumers — `ReportAdapter` resolved eight classes while the bridge below
delivered three. Assert arrival where the value is *read*: the list and the
arrival are two assertions, and only the second failed.

Refs: #520
Refs: #539 — the carrier is also what a plan must scope, not just the two ends.
## 2026-09-09 · pitfall · #511

A reconciliation that **ties** proves nothing until you know where the engine's
half came from. Cairn's Class A interest matched the published EUR 3,277,457.78
to the cent — because, with no published rate wired, the fold fell back to an
**amount-recovered** coupon back-solved from the very figure being checked. The
tie was the report agreeing with itself one layer down, and the test asserting
the tie was standing on the exact circularity it was written to detect. Wiring
the genuinely published rate (#512) made the agreement vanish and uncovered a
real day-count defect (#521) the false tie had been hiding.

Two rules follow. **Pin the input provenance, not just the output**: assert the
need against `size x published rate x day count` so an amount-recovered fallback
creeping back in reds immediately — an output-only assertion cannot tell a
computed figure from a copied one. And when you remove a circularity, **expect
green to turn red and read that as the result**: a previously-passing assertion
that breaks is the measurement beginning to work, not a regression to tune away.
Check *why* a number agrees before recording that it agrees.

Refs: #511, #512, #521

## 2026-09-09 · pattern · #539

Ask what **scope** a document states a fact at, and attach it there. A convention
can vary per class within one deal — Cairn states Act/360 for its floating notes
and 30/360 for the one fixed strip of the *same* class — so a deal-level field
cannot express it, and fails by averaging rather than erroring. Parse the scope
too: read which limb and which classes each rule names, and bound the block at
the next heading. The limb after a rule often enumerates every class for an
unrelated purpose, so a block running to end-of-text spreads that rule over all
of them while still looking like a clean parse.

Refs: #539

## 2026-09-09 · pitfall · #549

A refusal that keeps the value is not a refusal. `not_evaluable` protects the
*grade*, but the screen still renders `metric_value` beside it, and a number
shown next to "could not evaluate" is read as the measurement — suppress both
and put the cause in the reason. The tell that a numerator is not what it claims
is an identity, not a rounding: an overcollateralisation ratio reading exactly
100.00 at its junior-most attachment point is notes over notes. Detect that
arithmetically over the values in hand rather than by provenance — it needs no
plumbing, and it is a proof rather than a heuristic.

Refs: #549

## 2026-09-10 · pitfall · #571

Before keying a collection by a field to factor a lookup out of it, check the
collection **guarantees that field unique**. Re-keying a tranche list as
`{name: tranche}` collapsed two strips issued under the same name, and the need
calculators SUM strips, so the class's need halved — `CapitalStructure` refuses
duplicates, `WaterfallFunds` never has, so every committed deal passed green.
Ask which direction a dropped row errs in: a smaller need pays less, which reads
as health (#452). Filter the collection; never index a dict built from it.

Refs: #571
Refs: #492 — the same non-preservation, in the widening direction.
Refs: #572 — filtering is not enough: compare multiplicity, not membership.

## 2026-09-10 · pattern · #572

Match a refusal's blast radius to what actually failed to resolve. A refusal
written for one deal-level field is a whole-request 422; reused per item in a
collection it denies the caller every item that *did* resolve. Moving a check
into a loop means re-scoping it — refuse the cell, keep the record — leaving
the request-level refusal for when no honest partial exists at all. Emit a
cell for every field even when it refuses: a dropped field and a refused one
render alike, so "I could not resolve this" reads as "nothing to report".

Refs: #572
Refs: #494 — the same conflation, between a missing section and a clean one.
## 2026-09-10 · decision · #563

Joining two sources on one of several candidate vocabularies, choose the axis by
**measuring the unjoined share on each**, never by which has the better
acceptance oracle — two CLOs tie out S&P *and* Fitch per bucket, so the oracle
discriminated nothing while the joins differed threefold. Classify the residue:
**vintage drift** (GICS 2023 renamed `Food & Staples Retailing`) means the axis
is wrong, genuine absence means it is right. Fold orthography only, and prove it
injective over each **published table**, not your examples — Cairn's December
Fitch table prints `Building and materials` beside `Buildings and materials`.

Refs: #563
Refs: #562 — same fold, opposite failure: `B.V.` vs `BV` split one borrower.

## 2026-09-10 · pitfall · #562

A guard that passes by **finding nothing** needs a test that deletes its
*call*, not only one that calls it. Every census test here invoked
`_assert_every_asset_placed` directly, so the file still passed with the
builder's invocation removed — "the checker found nothing" and "nothing ran
the checker" are one silence. Assert the wiring where they differ: make the
builder emit a lossy result and require it to raise. #478 says put the check
*in* the builder; this is how you know it is still plugged in.

Refs: #562
Refs: #478 — the guard this one keeps wired.

## 2026-09-10 · pitfall · #564

What counts as orthography is axis-dependent — never reuse another axis's fold.
`industry_taxonomy.canonical_label` strips punctuation, right for `Aerospace &
Defense` and catastrophic for ratings: `B`, `B+` and `B-` all fold to `b`,
merging three notches and making a book read better than it is. Punctuation is
presentation in an industry name and the *datum* in a rating notch. Give each
axis its own fold, then prove it injective over each deal's own published
vocabulary — Contego prints `B` and `B+`, so the check fires on real data.

Refs: #564
Refs: #563 — the injectivity check this reuses, applied to a second axis.
Refs: #568 — same rule on a screen: refusals render first, uncollapsed, unclamped.

## 2026-09-10 · pitfall · #598

A registered calculator is **necessary and not sufficient** for "the engine
computed this": ask whether it returns a figure on a funds context shaped as
`_funds_from_state` shapes one, since one reading a field nothing writes either
refuses or returns a confident `0.00` against a published `0.00`. Ask the engine
rather than listing formulas to distrust — the formula is the wrong grain:
`reserve_replenishment` and `liquidity_reserve_replenishment` share a basis and
differ only in the reserve pair they read. Then guard the probe against the real
builder: an expectation read off the code's own constant can never red.

Refs: #598
## 2026-09-10 · pitfall · #601

A hardcoded convention hides in a helper's **signature**, not its body.
`_days_between(prev, cur)` read as a date utility; the defect was that it took
no basis, so no caller could ask for the second convention at all. Check what
an entrypoint can be *asked* before what it answers — and expect no failing
test, since such a helper is often unreachable on registered data (no deal
reaches the tape loop here). Converging it, make the conformance test an input
the two differ on **today**; agreement where both already agree cannot fail.

Refs: #601
Refs: #539 — the same assumption one carrier out, in a deal-level field.

## 2026-09-10 · pitfall · #614

A resolver that refuses names **the first** unmet precondition, not the work.
Contego's 422 named a senior coupon; sourcing it revealed a reserve target,
then an original pool balance, then a collections leg that cannot represent an
eight-class split-B stack at all — architectural, not configuration, and
invisible until the three before it were fixed. Before scoping a fix off a
refusal, drive the path to a **result** with the named value stubbed in: the
question is not "does this key resolve" but "does this deal reach a series".

Refs: #614
Refs: #493 — the layered-refusal design this is the read-side consequence of.
## 2026-09-10 · pitfall · #615

A score that presents an **overall** judgement must require every input that
would substantiate it, and test presence on the payload the reader sees. Two
traps hid one unmeasured deal: its `risk_summary` row existed as an all-null
shell, so a row-count check read it as present; and `has_performance` is a
property of **which path** reconstruction took — a deal reaches `reported` by
routing to the report path — not of how complete its data is. Gate on the
series' own `points` and the risk row's `latest_period`, and empty winner,
name, ranking and reasons together: `ranking[0]` is a winner by another name.

Refs: #615
