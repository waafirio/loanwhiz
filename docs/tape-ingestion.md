# Tape ingestion — the direct-read model

LoanWhiz analyses ESMA loan-level **tapes** (the loan-by-loan data behind a
securitisation). This document is the canonical reference for **how a tape gets
into LoanWhiz**: the ingestion model, the one path it takes, and the provenance
it records.

## The canonical path: direct read

A LoanWhiz tape is **read directly from its source URL**. There is one ingestion
path and it is the direct read — there is no ETL service, message queue, or
backend in the loop.

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

## Provenance

Every ingested tape records where it came from, surfaced through the governance
evidence pack (see [`governance.md` §7](governance.md)):

| Field | Where | Value |
|---|---|---|
| `EsmaTapeOutput.data_source` | `esma_tape_normaliser.py` | always `"direct"` |
| `TapeAnalyticsPeriod.data_source` | `GET /deal/{id}/tape-analytics` | the same, per reporting period |
| Tape citation excerpt | `Citation.excerpt` | `"… (ingested via direct)"`, carried into the agent's citation trail |

`data_source` is currently single-valued (`"direct"`) because direct read is the
only ingestion path. The field is retained as the provenance contract: an
additional ingestion source, if one were ever added, would extend the value here
and in `_load_tape`.

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
