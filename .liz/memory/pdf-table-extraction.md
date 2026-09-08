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
