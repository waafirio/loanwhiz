# Quality grading & ground-truth answer keys

<!-- Agent-appended entries below. Append only; never reorder or rewrite an
     existing entry (`merge=union` would duplicate it rather than replace it). -->

## 2026-09-02 · gotcha · #440

Backfilling a deal's ground truth is a **two-part** change, not one. Committing
`data/deals/answer_keys/<slug>.json` only gives the deal a key; `/quality-matrix`
still grades it `not-applicable` until the deal is ALSO registered in
`quality_harness._default_series_provider()`, because the harness folds the
engine series from a committed offline builder — the answer key carries published
ground truth, never the opening balances the fold seeds from. Add the
`fold_<deal>()` builder in `reconciler.py` and its `builders[...]` entry in the
same PR, then assert a `passed` cell; a key alone silently grades nothing.

Refs: #440
