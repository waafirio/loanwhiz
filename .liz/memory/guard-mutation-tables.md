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

## 2026-09-10 · pitfall · #573

Bound a source-guard's region at the **next declaration**, never at end of
file. A rule scoped to `src[index("function Foo"):]` is satisfied by anything
appended after `Foo`, so a sibling component's `{fact.reason}` counts toward
the rule about `Foo` and the guard passes while `Foo` itself went quiet. It
reads correct for exactly as long as `Foo` stays last in the file. The tell
that a bound is real: write the mutant that strips the token from the
component and re-supplies it from a decoy declared just after — it must red.

Refs: #573
Refs: #599 — re-derived independently; ui-surface-guards.md says when #568's opposite advice still holds.

## 2026-09-10 · pitfall · #599

Re-run the sweep against the API you just **widened**, not the one you swept
before. Closing guard holes means giving a component escape hatches so callers
can adopt it — a `className` for a caller's layout, an `enabled` flag to stop a
hoisted hook firing — and each is a new one-word way to neutralise what the
guard protects: `className="hidden"` renders the marking invisibly,
`enabled={false}` shows "not reported" forever. Both passed every rule written
minutes earlier. A rule pinning a call and not its arguments pins the half that
cannot vary.

Refs: #599
