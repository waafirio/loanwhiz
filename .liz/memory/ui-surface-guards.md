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

Pick a guard's region bounds by rule *class*. #568 ("slice to end-of-file —
over-reach fails loudly") and #573 ("an EOF-reaching component slice lets a
sibling satisfy the rule") are both right, about different rules. A positive
rule about one component reads a **bounded** slice whose end anchor must be
*found*, so a fallback to EOF asserts rather than passes. A ban reads whole
files, written narrowly enough that over-reach cannot false-positive — match a
quoted channel name inside a conditional, never the bare construct.

Refs: #599

## 2026-09-10 · gotcha · #599

A rule about one table must read *that table*, and its slicer must not be
anchored on the text the rule tests. Greping a whole module for `  derived: `
was answered by a second total table keyed on the same channels, so deleting
the real label passed; re-anchoring the slice on `Record<DataSource, string>`
then let a mutant rewriting that annotation delete the marker, and the guard
raised instead of reporting — a crash in a mutant sweep is neither pass nor
catch. Anchor on the declaration's name.

Refs: #599
