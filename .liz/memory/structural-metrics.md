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
Refs: #493 — same rule, re-learned when a test would have asserted the refusal.
Refs: #523 — the documented missing threshold was the second refusal, not the first.
Refs: #549 — and the first refusal itself named the wrong cause.

## 2026-09-08 · pitfall · #478

Derive a checker's field list from the **instance**, never a fixed tuple of
names. A hardcoded `class_a/b/c` list read through accessors answering `0.0`
for an absent class cannot be both short and failing: on an 8-class CLO it
compared 0.0 to 0.0 for the two classes the deal lacks, never looked at the
seven it has, and passed **vacuously** — silent exactly where the deal was
least like the reference one. Read the values off the canonical collection
too: `getattr(state, f"{name}_balance")` resolves only for the names that
happen to have accessors.

Refs: #478

## 2026-09-09 · pitfall · #549

Before refusing an input as unplaceable, check whether a sibling module already
places it. `covenant_monitor._tranche_class_rank` claimed to mirror
`assembler._seniority_for` but omitted its residual branch, so a CLO's equity
tranche read as "no recognisable class letter" — a *correct* refusal naming a
cause that was not the deal's, voiding eight coverage tests above the missing
threshold that actually blocked them. Treat "Mirrors <fn>" as an unchecked
claim: import the half that drifts (the vocabulary), pin the rest with a test
walking the exporter's own list, and assert a residual moves no ratio.

Refs: #549
