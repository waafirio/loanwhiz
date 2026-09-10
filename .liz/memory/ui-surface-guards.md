# Guarding a UI surface from a suite with no JS runner

<!-- Agent-appended entries below. Append only; never reorder or rewrite an
     existing entry (`merge=union` would duplicate it rather than replace it). -->

## 2026-09-09 · pitfall · #568

A ban-list guard scoped to a **slice** of a shared `.tsx` file passes vacuously
the moment the slice is empty: delete or reword the marker the slice starts at
and every `assert x not in region` is asserting over `""`. Whole-file guards
(`test_mcp_page.py`) do not have this failure mode, so the habit does not
transfer. Assert the marker is present *before* slicing, and pin one piece of
the guarded content, so the guard cannot pass by the region vanishing. Take the
slice to end-of-file rather than to a closing marker — an over-reaching region
fails loudly, an under-reaching one passes silently.

Refs: #568

## 2026-09-10 · pattern · #599

The corpus now says both "slice to end-of-file" (#568) and "bound at the next
declaration" (#573, `guard-mutation-tables.md`). Choose by rule *class*, not by
the entry you read last. A **positive** rule — this component renders X — takes
the bounded slice, because anything appended after the component satisfies it
otherwise. A **ban** takes whole files, and is then written narrowly enough
that over-reach cannot false-positive: match a quoted channel name inside a
conditional, never the bare construct. A ban needing a region is written too
loosely, which is the actual failure #568 was working around.

Refs: #599

## 2026-09-10 · gotcha · #599

Never anchor a slicer on text a rule *inside* that slice tests. Anchoring a
label-table region on `Record<DataSource, string>` looked precise until the
mutant rewriting that annotation deleted the marker: the slicer raised, so the
sweep recorded an error rather than the violation — and a crash is neither a
pass nor a catch. Anchor on the declaration's **name**, which no rule tests.
Ask of every marker: which of my own mutants edits this line?

Refs: #599
