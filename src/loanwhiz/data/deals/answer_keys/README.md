# Per-deal ground-truth answer keys

This directory holds **data-driven, per-deal answer keys** — the published
ground truth a reconciler grades the engine against (epic #425, format defined
in #427). It is the data-driven generalization of the old hand-built
`api/main.py:_VALIDATION_BUILDERS` map: instead of bespoke Python per deal, a
deal's answer key is committed JSON here.

## Naming

One file per deal, named by the deal-name **slug** — the same slug the committed
seed model uses (`../seed/<slug>.json`):

```
Green Lion 2024-1 B.V.  ->  green-lion-2024-1-bv.json
```

(Slug rule: lower-case, strip `.`/`,`, spaces → `-`; see
`loanwhiz.primitives.notes_cash_parser._slug`.)

A deal with no committed answer key resolves to `None` via `load_answer_key(...)`
and the caller degrades honestly (no fabricated ground truth). Keys are committed by
one of two routes. **Route 1** — a deal's published quarterly Notes & Cash
reports via `DealAnswerKey.from_notes_cash_report(...)`, so `/quality-matrix`
grades its revenue + redemption Priority-of-Payments to the cent:

| key | source | issue |
|---|---|---|
| `green-lion-2024-1-bv.json` | Green Lion 2024-1's 3 published quarterly reports | #429 |
| `green-lion-2023-1-bv.json` | Green Lion 2023-1's 3 published quarterly reports | #440 |

**Route 2** is below.

### The second route — published coverage-test results

A deal whose investor reporting is a **monthly trustee report** publishes no
Priority of Payments, so `from_notes_cash_report(...)` has nothing to read. It
does state each coverage test's computed ratio, its required level and its
outcome — which is the `covenants[]` section this schema already carries. Such a
deal therefore earns a key through the **sibling constructor**
`from_trustee_liability_summaries(...)`, not through a new format:

| key | source | issue |
|---|---|---|
| `cairn-clo-xvii-dac.json` | Cairn CLO XVII's 3 published monthly trustee reports | #481 |

Two properties of that key are deliberate and are asserted as tests, because
each is a place an overclaim could hide:

- **It carries no Priority of Payments and no pool statistics.** The Note
  Valuation Report would supply the former, but it is unregistered and unparsed;
  a key claiming a PoP section would claim a reconciliation nothing performs.
- **A test the report states as `N/A` is excluded, not coerced.** `passed` is a
  `bool` and cannot express "did not apply", so Class F — stated `N/A` in every
  period — is absent rather than recorded as a pass the trustee never stated.

**What the resulting graded cell does *not* prove.** Every outcome these reports
decide is `Passed`, so a monitor that reported nothing as breached would match
all of them. The row catches a wrong direction, a dropped or disagreeing
threshold, an unresolvable metric and a unit error; it cannot catch a
permanently non-firing monitor. `docs/data-card.md` carries the full statement.

### The discipline both routes share

**Only deals with genuine published ground truth get a key** (the #193 honesty
discipline). Leone Arancio 2023-1 and Sol-Lion II publish investor reports but
neither a Notes & Cash report nor a trustee report this repo parses, so there is
nothing for either constructor to read and inventing a key for them is
forbidden. They stay honestly `not-applicable` across every graded check.
`test_answer_keys_exist_exactly_where_published_ground_truth_does` asserts that
in both directions.

**An answer key must never be derived from the engine's own output.** That would
grade the engine against itself and make every cell vacuously green — the most
damaging failure available to this surface, because it would look like success.
Neither constructor has an engine module anywhere on its path, and the
regeneration regressions are what make that checkable rather than merely
intended.

Authoring a key is offline and deterministic end to end — a `pypdf` text extract
of the published PDF (committed under `tests/fixtures/notes_cash/` or
`tests/fixtures/collateral_schedule/`), a regex parser (`notes_cash_parser` /
`collateral_schedule_parser`), then the matching constructor. No LLM, no Vertex,
no network in the committed path. Never hand-edit a key: regenerate it from the
fixtures, which is what the faithfulness regressions in
`tests/test_quality_harness.py` assert — byte-for-byte, so editing a single
published threshold reds.

## Format

The schema is `loanwhiz.primitives.reconciliation_answer_key.DealAnswerKey`
(`format_version: 1`). Top level:

| field | meaning |
|---|---|
| `format_version` | answer-key schema version (`1`). |
| `deal_id` | canonical deal id used in `/deal/{deal_id}/...` routes. |
| `deal_name` | deal name as published (matches the seed model's). |
| `tolerance_eur` | absolute EUR reconciliation tolerance (default `0.01` — "to the cent"). |
| `periods[]` | published ground truth, one entry per reporting period. |

Each `periods[]` entry (`AnswerKeyPeriod`) carries all three ground-truth
categories the deal's investor report publishes:

- **Notes & Cash Priority-of-Payments** — `revenue_pop[]` / `redemption_pop[]`
  (each step `{priority, amount, recipient?}`) plus `available_revenue_funds` /
  `available_principal_funds`. These feed the to-the-cent reconciler today via
  `reconcile_against_answer_key(...)`.
- **Covenant test results** — `covenants[]` (each `{name, threshold?, actual?,
  passed, note?}`).
- **Pool statistics** — `pool_stats` (a `{name: value}` map, e.g.
  `pool_balance_end`, `principal_collected`).

## Consuming an answer key

```python
from loanwhiz.config import DEAL_REGISTRY
from loanwhiz.primitives.reconciliation_answer_key import (
    load_answer_key,
    reconcile_against_answer_key,
)

deal = DEAL_REGISTRY["green-lion-2024-1"]
key = load_answer_key(deal)          # -> DealAnswerKey | None
if key is not None:
    report = reconcile_against_answer_key(folded_series, key)  # ReconciliationReport
```

## Example shape

```json
{
  "format_version": 1,
  "deal_id": "example-deal-2024-1",
  "deal_name": "Example Deal 2024-1 B.V.",
  "tolerance_eur": 0.01,
  "periods": [
    {
      "reporting_date": "2025-09-30",
      "period_label": "September 2025",
      "available_revenue_funds": 1000000.0,
      "available_principal_funds": 5000000.0,
      "revenue_pop": [
        {"priority": "(a)", "amount": 12345.67, "recipient": "Senior expenses"}
      ],
      "redemption_pop": [
        {"priority": "(a)", "amount": 4500000.0, "recipient": "Class A redemption"}
      ],
      "covenants": [
        {"name": "sequential_pay", "threshold": 1.5, "actual": 0.4, "passed": true}
      ],
      "pool_stats": {"pool_balance_end": 95000000.0}
    }
  ]
}
```
