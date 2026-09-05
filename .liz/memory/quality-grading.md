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

## 2026-09-05 · pitfall · #457

A not-applicable reason is an **assertion about the world**, and one literal
covering a whole branch will be false for some member of it. Say only what the
input encodes: an empty `tape_urls` means no tape is registered, never that the
deal publishes no loan-level data. Split two causes on a **registry fact**, never
a deal id — "nothing is published" claims grading is impossible, "no answer key
is authored" names a deferred decision. Assert the retracted wording is *absent*;
a test checking only the state passes while the prose lies.

Refs: #457
