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
