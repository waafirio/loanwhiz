# Registering a deal (deals.json)

<!-- Agent-appended entries below. Append only; never reorder or rewrite an
     existing entry (`merge=union` would duplicate it rather than replace it). -->

## 2026-09-05 · pitfall · #455

Before setting a `deals.json` key, grep what *reads* it — several are routing
promises, not URL slots. `notes_cash_report_urls` makes `_reconstruct_series`
pick the report path, and `test_answer_keys_exist_exactly_where_published_reports_do`
asserts `{deals with that key} == {deals with a committed answer key}`, so setting
it for a sourced-but-unextracted deal reds a suite three files away. Register the
document when the promise can be kept (at extraction), and pin the deliberate
absence with the *reason* — "published but not yet extracted" and "no such report
exists" are opposite findings that the same empty key would otherwise flatten.

Refs: #455
Refs: #484 — `tape_urls` is the same shape: it selects the series adapter.

## 2026-09-08 · pitfall · #483

Changing a registered tape's URL changes every artifact keyed *by* that URL.
The committed tape-analytics seeds and the runtime cache are both named
`sha256(tape_url).json`, so re-identifying a tape orphans its seed and the
offline demo quietly drops that period — `_tape_analytics_period` degrades on
any per-tape error rather than raising. Regenerate and re-key in the same
commit, then diff the old seed against the new and assert only the field you
meant to change moved. Grep for what hashes a registry *value*, not just what
reads the key.

Refs: #483

## 2026-09-08 · pitfall · #479

A missing config tier is not automatically an *extraction* tier. The committed
deal model is a **prospectus** extraction — definitions, waterfalls, covenants,
tranche structure — so it states closing-date terms and no as-of-date fact: no
pool balance, no current coupon, ever. A config value that is a reported figure
(`projection_base.current_pool_balance`) can only be *derived*, from a tape or a
report. Read `DealModel`'s fields before promising an "extracted-model path": a
422 claiming none is available sends the reader to run an extraction that could
never have helped.

Refs: #479

## 2026-09-09 · decision · #524

Ranking two sources for one deal answers *precedence*, never *coverage* — and
the second is the one that bites. Registered sources need not cover the same
periods (Cairn's derived tapes and its Note Valuation Report overlap on **no**
period), so preferring one can silently shorten a deal's series, and a short
series reads as data where a blank screen reads as a gap. Compare what each
source covers before writing the rule, then return the displaced periods from
the rule itself. `_set_aside_tape_periods` is the shape: empty when nothing was
displaced, so "folds everything" and "narrowed" stay distinguishable.

Refs: #524

## 2026-09-09 · pitfall · #523

Assert a provenanced field at the seam that computes it, never through
`resolve_parsed_report`. `_splice_periods` builds a fresh `ParsedReport` out of
each period's parse and never copies the sidecar, so any deal served from
committed fixtures resolves with `provenance == {}` however carefully the
format's parser filled it — Green Lion included. The values themselves survive
the splice, so the symptom reads as "my provenance edit did not work" rather
than as a lossy splice, and the wrong thing gets rewritten.

Refs: #523
