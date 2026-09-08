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
