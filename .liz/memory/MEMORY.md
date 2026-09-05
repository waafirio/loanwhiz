# Memory index

One line per entry, grouped by the file it lives in. A line is a *locator*:
triage by opening the entry, not by reading the line.

- [Quality grading & ground-truth answer keys](quality-grading.md)
  - [2026-09-02 · gotcha · #440](#2026-09-02--gotcha--440) — keywords: answer_keys, quality_harness, _default_series_provider, fold_green_lion, not-applicable, backfill
- [Extraction guards & false-positive filters](extraction-guards.md)
  - [2026-09-02 · pitfall · #439](#2026-09-02--pitfall--439) — keywords: _CLASS_LETTERS, tranche, note class, Class J, Class O, false positive, guard, proxy range
  - [2026-09-05 · pitfall · #456](#2026-09-05--pitfall--456) — keywords: widening a guard, newly visible input, N/A cell, scavenged rating, Subordinated Notes, untested downstream
- [Extraction: locating the right section](extraction-section-routing.md) — keywords: descendant span, heading level, section number, Docling, has_payment_list, LLM router, empty result
- [Published figures & doc drift](published-figures.md) — keywords: stale tally, completeness_score, capability matrix, re-derive, stored metric, published figure
- [Registering a deal (deals.json)](deal-registration.md) — keywords: deals.json, notes_cash_report_urls, routing promise, registry key, asset_class, answer key invariant
- [Metrics computed over a capital structure](structural-metrics.md)
  - [2026-09-05 · pitfall · #452](#2026-09-05--pitfall--452) — keywords: attachment point, senior-or-equal walk, denominator, coverage ratio, OC, IC, silent health, unplaceable tranche
  - [2026-09-05 · decision · #452](#2026-09-05--decision--452) — keywords: classless default, senior class, positional metric, unmapped escape, alias row, LLM invents a position
- [Extraction determinism & LLM caching](extraction-determinism.md) — keywords: classify_segments_llm, section router, determinism cache, prompt hash, force_refresh, sections_found, completeness_score, degrade-to-default
- [Regulatory mapping tables (ESMA annexes, field codes, locators)](regulatory-mapping-tables.md) — keywords: AnnexField, extension field, code=None, field code, locator, ESMA RTS, annex numbering, CRPL, AUTL, RREL, borrowed code
- [Engine contracts (closed enums, registries, need calculators)](engine-contracts.md)
  - [2026-09-05 · pattern · #453](#2026-09-05--pattern--453) — keywords: NEED_CALCULATORS, register_need, RecipientType, closed enum, registry coverage, import-time assert, silent default, generated calculators
  - [2026-09-05 · pitfall · #453](#2026-09-05--pitfall--453) — keywords: LLM classifier options, deny list, enum growth, near-miss, incentive fee, use_llm=False, unmapped escape
