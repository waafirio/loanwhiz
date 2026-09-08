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

**Routes 2 and 3** are below.

### The second route — published coverage-test results

A deal whose investor reporting is a **monthly trustee report** publishes no
Priority of Payments, so `from_notes_cash_report(...)` has nothing to read. It
does state each coverage test's computed ratio, its required level and its
outcome — which is the `covenants[]` section this schema already carries. Such a
deal therefore earns a key through the **sibling constructor**
`from_trustee_liability_summaries(...)`, not through a new format:

| key | source | issue |
|---|---|---|
| `cairn-clo-xvii-dac.json` (its covenant periods) | Cairn CLO XVII's 3 published monthly trustee reports | #481 |

Two properties of those periods are deliberate and are asserted as tests, because
each is a place an overclaim could hide:

- **They carry no Priority of Payments and no pool statistics.** A trustee report
  states neither, so a period authored from one must claim neither — even though
  the same key now holds a PoP period from a *different* document (route 3). The
  assertion is per period for that reason: a key-level check would stop seeing
  the mixing it exists to prevent.
- **A test the report states as `N/A` is excluded, not coerced.** `passed` is a
  `bool` and cannot express "did not apply", so Class F — stated `N/A` in every
  period — is absent rather than recorded as a pass the trustee never stated.

**What the resulting graded cell does *not* prove.** Every outcome these reports
decide is `Passed`, so a monitor that reported nothing as breached would match
all of them. The row catches a wrong direction, a dropped or disagreeing
threshold, an unresolvable metric and a unit error; it cannot catch a
permanently non-firing monitor. `docs/data-card.md` carries the full statement.

### The third route — a CLO's Note Valuation Report

A CLO publishes no quarterly Notes & Cash report, but its **Note Valuation
Report** carries, on facing sections, an *Interest Priority of Payments* and a
*Principal Priority of Payments* — the same facts under other names. So it is a
sibling reader rather than a second format: `note_valuation_parser` (#494) emits
the existing `NotesCashReport` shape, and `DealAnswerKey.from_note_valuation_report(...)`
authors from it. Where one deal publishes across several documents, each has its
own constructor and `merge_answer_keys(...)` unions them into the one file:

| key | source | issue |
|---|---|---|
| `cairn-clo-xvii-dac.json` (its January 2025 period) | Cairn CLO XVII's published Note Valuation Report (as-of 08/01/2025) | #495 |

Three properties of that period are deliberate:

- **It is a fourth period, not an enrichment of the other three.** The report is
  as-of 08/01/2025 and the exchange carries no January trustee report, so the two
  document sets cover disjoint dates. `merge_answer_keys` refuses a collision
  rather than reconciling one, so an overlapping future filing fails loudly.
- **`pool_stats` carries each class's published all-in applied rate**, as
  `applied_rate_<class key>` — the rate the report says was *applied*, not an
  index fixing. The document publishes no EURIBOR fixing anywhere in its 83
  pages, so none is recorded; no grader resolves these keys today and
  `_grade_pool_stats` reports them under `ungraded_stat_keys`, which is the
  honest surface for a published figure nothing yet checks.
- **A class the report states no rate for is excluded, not coerced** — the
  Subordinated Notes, by the same rule that excludes Class F above.

**What a graded PoP cell here does *not* prove — now measured, not predicted
(#496).** The report states EUR 0.00 of available principal funds for this
period, so every one of its Principal Priority-of-Payments steps is zero. An
engine that never pays anything reproduces that waterfall exactly, so the
redemption row cannot distinguish a correct cascade from a silent one; only the
Interest row, whose steps distribute EUR 7,255,062.35, carries that signal.
State the bound wherever the grade is published (#481).

**And the Interest row, run, does not reconcile.** #496 folded the deal's own
extracted 29-step cascade against this document and reports a shortfall of EUR
1,820,150.42 against the stated available revenue: this report prints its
waterfall as 62 rows, several of them sub-lettered components of one cascade
step (`(A)(i)`, `(H)(i)`, `(H)(ii)`, `(CC)(1)(a)`, and two it re-letters bare
`(a)`), and the reconciler's report-label folding handles only the purely-numeric
`(b)(1..n)` wrap the Dutch RMBS reports produce. Two consequences worth stating
beside the key rather than only in the grade: the rows that *do* join agree
because each step's amount is taken from the report and compared to itself —
no Cairn recipient is in `ENGINE_COMPUTED_RECIPIENTS`, so not one line is
independently computed — and Class B's published EUR 644,398.50, which the
report splits across `(H)(i)` and `(H)(ii)`, reconciles as 0.00-vs-0.00 against
an unjoined parent label.

**A fourth property of the union, found by grading it.** Because three of this
key's four periods come from trustee reports that state no Priority of Payments,
a fold built from the one PoP-bearing document produces one period result against
the key's four, and `reconcile_series` refuses the join rather than grading a
partial answer — so `reconcile_against_answer_key` raises on this key before
comparing a figure. The refusal is correct; it is recorded here because it means
the union's cadence, not any number in it, is what stands between this key and a
gradeable cell. `tests/test_clo_pop_grading.py` holds all of the above. Whoever
takes it on: **do not close a gap from this side.** An answer key edited to fit
the engine grades the engine against itself, which is the failure this whole
directory exists to prevent.

### The discipline all three routes share

**Only deals with genuine published ground truth get a key** (the #193 honesty
discipline). Leone Arancio 2023-1 and Sol-Lion II publish investor reports but
none of the three documents above, so there is nothing for any constructor to
read and inventing a key for them is forbidden. They stay honestly `not-applicable` across every graded check.
`test_answer_keys_exist_exactly_where_published_ground_truth_does` asserts that
in both directions.

**An answer key must never be derived from the engine's own output.** That would
grade the engine against itself and make every cell vacuously green — the most
damaging failure available to this surface, because it would look like success.
No constructor has an engine module anywhere on its path, and the regeneration
regressions are what make that checkable rather than merely intended.

Authoring a key is offline and deterministic end to end — a `pypdf` text extract
of the published PDF (committed under `tests/fixtures/notes_cash/`,
`tests/fixtures/collateral_schedule/` or `tests/fixtures/note_valuation/`), a
regex parser (`notes_cash_parser` / `collateral_schedule_parser` /
`note_valuation_parser`), then the matching constructor. Each parser refuses a
parse that does not tie out to the document's own stated totals, so a constructor
never has to decide whether to trust its input. No LLM, no Vertex, no network in
the committed path. Never hand-edit a key: regenerate it from the
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
  `pool_balance_end`, `principal_collected`, and the `applied_rate_<class>`
  figures route 3 records). A key the harness has no series-grounded analogue
  for is reported under `ungraded_stat_keys` rather than silently dropped.

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
