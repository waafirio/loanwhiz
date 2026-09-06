# Tape ingestion — direct read, and derivation

LoanWhiz analyses ESMA loan-level **tapes** (the loan-by-loan data behind a
securitisation). This document is the canonical reference for **how a tape gets
into LoanWhiz**: the ingestion model, the paths it takes, and the provenance
it records.

There are two channels, and one seam. Every tape — however it arrives — is
loaded by `loanwhiz.primitives.esma_tape_normaliser._load_tape`, which decides
the channel from the tape's **identifier** rather than from configuration held
somewhere else:

| Channel | When | `data_source` |
|---|---|---|
| **Direct read** | the tape is a published CSV/parquet file | `"direct"` |
| **Derivation** | no published tape file exists; the rows are reconstructed at ingest from a source document the deal registers | `"derived"` |

## The first path: direct read

A published LoanWhiz tape is **read directly from its source URL**. There is no
ETL service, message queue, or backend in the loop.

```
deal["tape_urls"]  ──►  _load_tape(file_url, period)  ──►  pandas.DataFrame
   (per-period            (esma_tape_normaliser)              + data_source="direct"
    source URLs)
```

`loanwhiz.primitives.esma_tape_normaliser._load_tape` is the single ingestion
entry point. Given a tape URL it:

1. **Dispatches on the file extension** (the query string is stripped first, so
   signed URLs like `…/tape.parquet?token=…` still route correctly):
   - `.parquet` / `.pq` → `pandas.read_parquet(file_url)`
   - anything else → `pandas.read_csv(file_url, low_memory=False)`
2. **Optionally slices by reporting period.** Combined multi-month tapes (e.g.
   `Overall_2024_2025_all_months.parquet`) carry many `reporting_date` values in
   one file. When `period` is set and a `reporting_date` column is present, the
   frame is filtered to that single cut-off. A `period` that matches no rows is a
   `ValueError` (fail loud, never silently empty).
3. **Tags provenance.** The loaded frame is returned with `data_source="direct"`.

The sources this covers are exactly the ones LoanWhiz deals use: **HuggingFace
CSV/parquet** tapes and local `file://` paths. Any `http(s)://` or `file://` URL
to a CSV or parquet tape works.

## Worked example: Green Lion 2026-1

The validated tape-driven deal is **Green Lion 2026-1 B.V.** (a Dutch RMBS
deal). Its monthly ESMA tapes are published on HuggingFace and loaded by
`loanwhiz.data.green_lion`, which is the direct-read path for that deal:

```python
from loanwhiz.data import green_lion

# Each entry is {"date": <reporting_date>, "url": <direct HuggingFace CSV URL>}.
tapes = green_lion.list_tapes()

# Loads the named monthly tape straight from HuggingFace via pandas.read_csv.
df = green_lion.load_tape("2026-04-30")
```

`green_lion.load_tape` is `pandas.read_csv(<HuggingFace URL>)` under the hood —
the same direct read `_load_tape` performs. Feeding a Green Lion tape URL through
`EsmaTapeNormaliser` produces an `EsmaTapeOutput` with `data_source="direct"`.

## The second path: derivation from a source document

Some deals publish no machine-readable tape at all, yet publish the loan-level
detail in another form. **Cairn CLO XVII DAC** is the worked case: European CLOs
are private transactions for the purposes of the EU Securitisation Regulation,
so nothing is filed to a securitisation repository, but the monthly trustee
report carries a full per-asset collateral schedule.

A derived tape has no upstream URL, so its identity is a **URI whose scheme
names the derivation and whose body is the source document**:

```
derived+trustee-report:https://…/monthly-report.pdf#period=December%202024
└──── scheme ────────┘└──── the real source document ────┘└── which cut ──┘
```

`loanwhiz.primitives.derived_tape` owns that scheme. On a cold cache it fetches
the report, extracts its text, parses the collateral schedule
(`collateral_schedule_parser`), **refuses it unless it reconciles to the
report's own stated aggregates**, and resolves the rows onto canonical Annex 4
columns (`collateral_tape_mapping`). The result is cached under
`data/extraction_cache/derived-tape-*.json`, which is an accelerator and never
an authority — delete it and the same tape is re-derived from the same report.

Two consequences worth stating plainly:

- **The annex is stated, not sniffed.** Annex 4's entire detection signature is
  `enterprise_size`, which no trustee report publishes. Rather than widen the
  signature or fabricate the column, a derived tape declares the annex it
  targeted and `_resolve_annex` prefers that declaration. Detection remains the
  right answer for a tape of unknown origin; a derivation is not of unknown
  origin.
- **Registration is ordinary.** The three URIs sit in `deals.json` under
  `tape_urls` like any other tape, and `POST /deal/{id}/ingest/tape` accepts one
  at runtime — it validate-loads through `_load_tape`, so an unreconcilable
  source is a `422` rather than a persisted lie. No second endpoint exists.

### Adding the next derived source

Register a member on `DerivedTapeScheme`, its source kind, and its deriver. An
import-time guard refuses a member missing from either table. Nothing in the
module special-cases Cairn.

## Provenance

Every ingested tape records where it came from, surfaced through the governance
evidence pack (see [`governance.md` §7](governance.md)):

| Field | Where | Value |
|---|---|---|
| `EsmaTapeOutput.data_source` | `esma_tape_normaliser.py` | `"direct"` or `"derived"` |
| `TapeAnalyticsPeriod.data_source` | `GET /deal/{id}/tape-analytics` | the same, per reporting period |
| Tape citation excerpt | `Citation.excerpt` | `"… (ingested via direct)"` / `"… (ingested via derived)"`, plus the source kind's full disclosure sentence for a derived tape |
| `TapeSourceKind` | `domain/tape_provenance.py` | `derived_from_investor_report` for a derived tape; **not declared** for a published file |

### Channel is not the same question as kind

`data_source` says **how the tape arrived**. `TapeSourceKind` says **what it
is** — and the two are kept apart on purpose.

A derived tape is `DERIVED_FROM_INVESTOR_REPORT`, and every surface that reports
provenance renders that kind's `disclosure` string verbatim rather than
composing its own wording, so the claim cannot drift between the citation, the
capability-matrix cell and the data card.

A published tape read through the direct path has **no declared kind**. That is
deliberate and is *not* a synonym for `FILED_ARTICLE_7_1_A`: this repo holds no
evidence about whether a given published file is its originator's Article 7(1)(a)
disclosure or a redistribution of it, and defaulting to "filed" would make the
system's most consequential claim by omission. A surface rendering provenance
must therefore handle three answers — derived, filed, and not declared.

**Why this matters here specifically.** Cairn *does* file real Article 7(1)(a)
Loan Reports; LoanWhiz does not have them. A derived tape that a reader could
mistake for that filing would be provenance laundering, so the distinction is
carried by the tape's identifier rather than by a label someone must remember to
attach: a derived tape cannot be loaded and come back tagged `"direct"`.

## Why direct read (and not a deeploans backend)

An earlier design considered routing tape ingestion through
[deeploans](https://github.com/Algoritmica-ai/deeploans), Algoritmica's
open-source ESMA ETL framework. Verification of the upstream project found it is
**serve-only** (a BigQuery-backed, GET-only FastAPI backend, populated by
out-of-band Airflow ETL DAGs — no upload/ingest endpoint) and the public
instance serves **SME** data, whereas LoanWhiz's deals are **RMBS**. deeploans
therefore cannot ingest an arbitrary LoanWhiz tape on demand.

Direct read is consequently the **canonical** tape ingestion path. deeploans
remains a credited Apache-2.0 upstream input of the project, but it is decoupled
from runtime — it is not on the ingestion path and is not a dependency.
