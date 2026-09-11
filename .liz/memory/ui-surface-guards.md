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
Refs: #613 — a near-edge-only bound survives a new sibling region inserted above it.

## 2026-09-10 · gotcha · #599

Never anchor a slicer on text a rule *inside* that slice tests. Anchoring a
label-table region on `Record<DataSource, string>` looked precise until the
mutant rewriting that annotation deleted the marker: the slicer raised, so the
sweep recorded an error rather than the violation — and a crash is neither a
pass nor a catch. Anchor on the declaration's **name**, which no rule tests.
Ask of every marker: which of my own mutants edits this line?

Refs: #599

## 2026-09-10 · pitfall · #614

"Every surface routes through one total table" claims only what someone
**enumerated**, and the enumeration goes stale. A sixth surface hand-rolled
`=== "projected"` — the construct #599's own rule banned — because it renders a
*different* union and matched no grep for the guarded one. Enumerate consumers
of the **union type**, not of the table, and keep the tables separate: one
table over two vocabularies makes totality unenforceable for both.

Refs: #614
Refs: #599 — the five-surface table this extends.
## 2026-09-10 · pitfall · #617

Bound a markup-stripping regex to a single line. Flattening prose before a
guard scans it is right — the false claim here breaks across two lines at a `*`
comment leader, invisible to a byte-scan — but `<[^>]*>` does not stop at a
newline: between a stray `<` in an ASCII diagram and the next `>` lines later
it deletes everything between, claims included, and the guard then passes by
having nothing left to read. Write `<[^>\n]*>`. Strip a `*` leader only where a
single `*` is followed by whitespace, never `**bold**` — the docs mark section
names with emphasis, and eating it takes the labels being checked with it.

Refs: #617

## 2026-09-11 · pitfall · #623

A visual check run on a port the API does not allowlist passes on an empty
page. The API allows `http://localhost:3000` for CORS, so a worktree server on
another port served the shell while every fetch failed: the page read "Could
not load" and the overlap probe reported *clear* everywhere — a false green
shaped like a fix. Assert a **content signal**, not the defect's absence:
document height and cell count against the same page on the running server.
CDP request interception — fulfil the app's API calls from a server-side
fetch with the permissive header — gets that data without restarting a server
someone else is using.

Refs: #623

## 2026-09-11 · pattern · #623

A viewport-pinned control covers a *different* row at every scroll offset, so
"reserve clearance under the last row" fixes only the offset that was
screenshotted: four offsets over one table put four different balance cells
under it. Lowering its z-index inverts the defect — the control hides behind
the data. So either the content gets a reserved gutter, or the control moves
into chrome that is **reserved layout width**: a sidebar rail qualifies
(content starts where the rail ends, expanded or collapsed), a fixed corner
never does.

Refs: #623
Refs: #565 — the source-guard trade this surface keeps making.
