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
