# Published figures & doc drift

<!-- Agent-appended entries below. Append only; never reorder or rewrite an
     existing entry (`merge=union` would duplicate it rather than replace it). -->

## 2026-09-02 · pitfall · #441

Re-derive every published figure from the running system before quoting it —
never from a prior doc, a sibling PR body, or a committed artifact. Three drift
paths bit at once: a tally moved because *data* changed with no code diff
(refreshed seeds flipped 3 capability cells, 1/9/15 → 1/12/12); a stored metric
kept its pre-change value (a seed's `completeness_score` 0.75 was the superseded
section-header ratio, current code scores it 0.925); and two PR bodies
disagreed with the output they themselves committed (0.93 vs 0.925). Hit the
endpoint or recompute — authoritative once is not true now.

Refs: #441
