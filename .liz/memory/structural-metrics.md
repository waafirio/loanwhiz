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
