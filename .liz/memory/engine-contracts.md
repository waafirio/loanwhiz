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
