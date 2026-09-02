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
