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
and the caller degrades honestly (no fabricated ground truth). Two keys are
committed, each authored from its deal's published quarterly Notes & Cash reports
via `DealAnswerKey.from_notes_cash_report(...)`, so `/quality-matrix` grades both
deals' revenue + redemption Priority-of-Payments to the cent:

| key | source | issue |
|---|---|---|
| `green-lion-2024-1-bv.json` | Green Lion 2024-1's 3 published quarterly reports | #429 |
| `green-lion-2023-1-bv.json` | Green Lion 2023-1's 3 published quarterly reports | #440 |

**Only deals with genuine published ground truth get a key** (the #193 honesty
discipline). The two Green Lion vintages above are the only deals in
`DEAL_REGISTRY` carrying `notes_cash_report_urls`; Leone Arancio 2023-1 and
Sol-Lion II publish investor reports but **no Notes & Cash report**, so there is
nothing for `from_notes_cash_report(...)` to read and inventing a key for them is
forbidden. They stay honestly `not-applicable` across every graded check.

Authoring a key is offline and deterministic end to end — a `pypdf` text extract
of the published PDF (committed under `tests/fixtures/notes_cash/`), the
`notes_cash_parser` regex parser, then `from_notes_cash_report(...)`. No LLM, no
Vertex, no network in the committed path. Never hand-edit a key: regenerate it
from the fixtures, which is what the faithfulness regressions in
`tests/test_quality_harness.py` assert.

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
