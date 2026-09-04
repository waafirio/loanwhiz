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
