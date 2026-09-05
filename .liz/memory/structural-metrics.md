# Metrics computed over a capital structure

<!-- Agent-appended entries below. Append only; never reorder or rewrite an
     existing entry (`merge=union` would duplicate it rather than replace it). -->

## 2026-09-05 · pitfall · #452

When a metric is measured **at a point** in an ordered structure ("the notes at
or senior to Class D"), assert the point itself EXISTS before summing the walk.
A senior-or-equal walk succeeds silently when the named class is absent — it
returns the next point up and publishes that number under the missing class's
name. Ask of every partial computation *which direction it errs in*: a narrowed
denominator reports **health**, not breach, so it never looks like a bug. Where
an input cannot be placed in the structure at all, refuse the whole metric
rather than skipping the row.

Refs: #452

## 2026-09-05 · decision · #452

Do not copy a "classless input defaults to the senior class" convention onto a
metric whose **position is part of its identity**. It is safe for a flag (a
classless PDL fires on any positive balance whatever the class) and unsafe for
a ratio (the same pool over Class A alone and over A+B+C are different numbers,
and the senior point is the highest of them) — so the default silently reports
the most flattering candidate. Map the recognised-but-unplaceable string to the
`unmapped` escape *explicitly*: an alias row resolving to `unmapped` also stops
the string reaching an LLM that would invent a position non-deterministically.

Refs: #452

## 2026-09-05 · pitfall · #457

Before repeating a documented reason for a metric refusing, run it and read
**which layer actually refused**. Refusals stack and the documented cause is
often never reached: a coverage test recorded as unquantifiable-for-want-of-a-
threshold refused earlier on an unplaceable equity tranche. Fixing only the
documented one leaves the test still refusing. Probe the next layer by removing
the blocking input only when doing so provably changes no output, and label that
a counterfactual, never a deal figure.

Refs: #457
