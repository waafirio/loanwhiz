# Making a user-facing marking survive

<!-- Agent-appended entries below. Append only; never reorder or rewrite an
     existing entry (`merge=union` would duplicate it rather than replace it). -->

## 2026-09-10 · pattern · #575

Render a user-facing marking from **inside the loop that renders the items it
marks**, and assert it from a test in whatever language actually runs. Written
once beside a list, it survives only until someone adds an item; living solely
in an author's intention it never survives — #484 labelled synthetic pools
correctly in the data and those pages still render no badge. Where the UI has
no test runner, read the component from pytest and assert the string, as
`tests/test_capability_matrix.py` does to `page-states.tsx`. Pair the ban with
a positive assertion, or the guard passes by deleting what it guards.

Refs: #575

## 2026-09-10 · gotcha · #575

Strip comments before a guard greps a source file for banned constructs, or
the documentation promising the property trips the ban that enforces it: a
header comment reading "no `onSubmit`, no `action=`" fails an `onSubmit` ban
on its own component. This is #471's prose rule one layer down — ban the
assertion, not a fragment — and the fix is to scan the region the claim is
about rather than the prose around it.

Refs: #575
