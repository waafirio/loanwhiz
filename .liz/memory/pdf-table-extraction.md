# Extracting tables from PDFs

<!-- Agent-appended entries below. Append only; never reorder or rewrite an
     existing entry (`merge=union` would duplicate it rather than replace it). -->

## 2026-09-06 · pitfall · #469

Rebuild a PDF table's rows from text-run coordinates, not `extract_text()`'s
line breaks: a rotated table collapses to one blob per page with every row
boundary gone, and `extraction_mode="layout"` discards rotated text outright.
Sort a page's `visitor_text=` runs by the text matrix's cross-axis offset
(`tm[4]`) to recover visual line order. Then treat a wrapped row as a matrix,
not a string — its continuation carries each overflowing cell's remainder in
COLUMN order, so consume columns left to right, taking each remainder off the
front; appending it to the row's end files a name fragment under the last column.

Refs: #469

## 2026-09-08 · pitfall · #494

Ask "is this a data row?" **before** "is this page furniture?". A furniture
filter keyed on banner prefixes eats any data row that opens with the same
words — the U.S. Bank footer `U.S. Bank Global Corporate Trust` also opens the
payee row `U.S. Bank Global Corporate Trust Limited 15,818.69 …`, and the step
silently under-reported. Match the row's own shape (its anchored numeric tail)
first, and only ask the furniture question of what is left over. A prefix list
is a heuristic about *layout*; a row's numeric tail is evidence about *content*,
and evidence outranks heuristics.

Refs: #494
## 2026-09-08 · pitfall · #480

Read a table's column ORDER off its own header, never off which section it sits
in, and refuse an unrecognised header rather than reading two like-typed columns
in a guessed order. One trustee report states each coverage test's required
level and computed ratio twice — Executive Summary as `Threshold · Current`,
detail page as `RATIO · REQUIRED LEVEL` — so either order, assumed, swaps them
in one place and reports a breaching test as passing. Where a document states
the same pair twice, parse BOTH and require agreement: the redundancy is a free
cross-check, not duplicated work.

Refs: #480

## 2026-09-09 · pitfall · #533

Registering a report family supplies titles and furniture, not rows: whether a
row survives extraction *as a line* is a separate axis, and it decides whether
a newly registered deal parses at all. Measure it before sizing the work. U.S.
Bank yields one asset per line, identifier first, so an anchored per-line match
both finds the id and proves the line is a row; BNY yields one row-major line
per page holding the whole table, beside a column-major stack of single cells
that cannot be zipped back — one blank cell desyncs every column after it —
with the identifier mid-row. Re-cut rows on the row's own anchored tail.

Refs: #533


## 2026-09-09 · pitfall · #555

Detect row geometry per **page**, not per document, even where a family record
declares it. Contego's Interest Accrual Detail runs seven pages: six extract as
one row-major line and one as one row per line, so a parser reading only each
page's longest line skips the seventh's forty rows. Rows wrap across lines
there too, so join a non-reflowed page's lines before scanning. The declaration
says which geometry to expect; the page says which it is, and rows it silently
omits move a count while leaving par untouched.

Refs: #555


## 2026-09-09 · pitfall · #534

A table's like-typed columns are not always two, and one may restate the
**prior period**: BNY prints `Prior Outcome | Outcome | Requirement`, so taking
the ratio as the first percentage grades against last month's figure — real, in
range, right type, silently wrong. Chain across periods to find the current
column (this report's first equals the previous report's second) before
declaring which it is, and model the row as a grammar with **named groups**, not
a two-valued "which comes first" order: an enum cannot name one column among
three, and one administrator's own two headers need not agree.

Refs: #534

## 2026-09-10 · pitfall · #600

Decide what **closes** a row, not only what continues it. A grouper treating
every non-identifier line as a wrapped remainder eats the section's own total
line — which sits directly beneath the section's *last* row, so the damage lands
on exactly one obligor per report, the alphabetically last one, and par still
ties because the figure welded on was the report's own aggregate. Refuse a line
that is nothing but a money amount: a continuation carries the overflow of a
free-text cell, so it always carries text. Ask that only after the identifier
test, so a data row is still classified on its own shape first.

Refs: #600

## 2026-09-11 · pitfall · #626

A blank extraction surface is not evidence the extractor is blank. Cairn's nine
coverage tests had parsed correctly for months — thresholds, ratios, outcomes,
pinned in a committed golden — while `/compliance` reported all ten triggers
`threshold: None, evaluable: False`, because the trigger set is built from the
*prospectus* extraction and nothing joined the trustee's table onto it. Measure
the surface against the parser's own output before sizing parser work: the
defect was a missing key join, and the fix registered a fixture rather than
touching a regex. The blank surface had also been read as evidence the parser
was the expensive part; it was already done and committed.

Refs: #626

## 2026-09-11 · pitfall · #626

Split a table's columns by what they MEASURE before deciding which source wins.
A coverage test's required level and its computed ratio are both percentages on
one row, and it is tempting to take both from whichever source you trust more.
They are not the same kind of fact: the level is a covenant, identical in three
consecutive trustee reports, so a report from any month supplies it; the ratio
measures one period, so the nearest report may state a DIFFERENT month's. Taking
the pair together would have overwritten an engine ratio that reproduced the
period's own report to the cent with a figure from the month before — #524's
wrong-as-of-date failure, reached from the opposite direction and while trying to
avoid it. Let the report fill only what the engine cannot compute, and put the
borrowed figure's own date on the wire beside it.

Refs: #626
