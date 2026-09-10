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
