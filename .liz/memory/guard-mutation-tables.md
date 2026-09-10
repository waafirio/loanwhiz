# Guarding a guard: mutation tables over source assertions

<!-- Agent-appended entries below. Append only; never reorder or rewrite an
     existing entry (`merge=union` would duplicate it rather than replace it). -->

## 2026-09-10 · pitfall · #573

Anchor a mutation-table rewrite on text **unique to the file**, and make the
harness assert that uniqueness. `str.replace(old, new, 1)` takes the first
match, and a shorter-indented anchor is a *substring* of a deeper-indented
line: two mutants anchored on `      <Badge variant="destructive"` rewrote an
unrelated component 300 lines above the region under test, so the region slice
never saw the edit and both mutants "survived". A surviving mutant reads as a
hole in the surface; this one was a hole in the harness. Count the matches and
refuse anything but exactly one.

Refs: #573
