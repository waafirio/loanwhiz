# Extraction guards & false-positive filters

<!-- Agent-appended entries below. Append only; never reorder or rewrite an
     existing entry (`merge=union` would duplicate it rather than replace it). -->

## 2026-09-02 · pitfall · #439

Bound a false-positive guard by the property that separates the two cases —
never by a proxy range that happens to exclude the example in front of you.
#397 killed a phantom `Class O = 42 EUR` tranche by capping the note-class
alphabet at A–G; the real discriminator was "has a corroborating size". The cap
also excluded every conventional *named* class (J junior, M mezzanine, R/X/Z
residual), so an Italian deal's EUR 920m Class J was unseeable and the deal
degraded to one unsized tranche — #397's own test still green. Write down the
set your guard excludes; check real inputs against it, not just the motivator.

Refs: #439
Refs: #456 — same rule, on an LLM's placeholder value.

## 2026-09-05 · pitfall · #456

Widening an extractor guard is not additive: the input it used to drop now
reaches downstream code that has never run on it. Admitting a CLO's unlettered
Subordinated tranche made the row parser read that row's "N/A" rating cell as a
word-bounded "A" and its issue-price cell as a coupon — a rated, coupon-paying
first-loss note, out of a change that only added a name. Re-read what the newly
visible input produces end to end before believing the fix; the rows a guard
used to drop are exactly the ones no fixture ever covered.

Refs: #456
