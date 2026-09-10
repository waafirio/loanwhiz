# Extraction: locating the right section

<!-- Agent-appended entries below. Append only; never reorder or rewrite an
     existing entry (`merge=union` would duplicate it rather than replace it). -->

## 2026-09-02 · pitfall · #438

Treat an extractor's EMPTY result as a routing question before recording it as a
fact about the document, and never let a test pin that emptiness as ground truth
until you know which section the router actually reached. Docling routinely emits
every heading of a prospectus at ONE markdown level, so parent/child logic keyed
on heading level collapses to the parent's own stub: #396's descendant-span
widening was a no-op by construction, and the LLM router — seeing every segment
flagged `has_payment_list=false` — routed on titles alone. Derive hierarchy from
the dotted heading number (`3.4.7.2.2` under `3.4.7.2`) when headings carry one.

Refs: #438

## 2026-09-09 · pattern · #566

An offering document describes a regulation before stating the deal's own
commitment to it, repeating the same threshold figure. Locate a regulatory
undertaking by its specific sub-paragraph citation plus a named party — never by
the figure or the method's name in words: "five per cent." spans four
retainer-less pages of Cairn CLO XVII while `Article 6(3)` appears on one page of
420. Wording varies where the citation does not; three committed deals write it
`Article 6(3)(d)`, `first loss tranche ... pursuant to Article 6(3)(d)`, and
`option 3 (a) of article 6`. Stated both ways they must agree.

Refs: #566
## 2026-09-09 · pitfall · #532

A short-but-plausible extraction and a budget-truncated one present identically
— a definitions map holding only the leading alphabetical range. Measure the
section actually sent before blaming `definitions_graph.py`'s `max_chars`:
Contego's was 3,255 chars against a 40,000 budget, so widening it fixes
nothing. The loss is in `route_sections`, which ends a section at the next
heading while Docling renders many defined terms *as* headings, orphaning the
glossary into siblings. A second document losing facts at one stage by a second
route means the defect is the stage trusting whatever it is handed.

Refs: #532
