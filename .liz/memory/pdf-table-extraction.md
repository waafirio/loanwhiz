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
