# Tape ingestion — direct read, derivation, and synthetic pools

LoanWhiz analyses ESMA loan-level **tapes** (the loan-by-loan data behind a
securitisation). This document is the canonical reference for **how a tape gets
into LoanWhiz**: the ingestion model, the paths it takes, and the provenance
it records.

There are three channels, and one seam. Every tape — however it arrives — is
loaded by `loanwhiz.primitives.esma_tape_normaliser._load_tape`, which decides
the channel from the tape's **identifier** rather than from configuration held
somewhere else:

| Channel | When | `data_source` |
|---|---|---|
| **Direct read** | the tape is a published CSV/parquet file | `"direct"` |
| **Derivation** | no published tape file exists; the rows are reconstructed at ingest from a source document the deal registers | `"derived"` |
| **Synthetic** | the tape is a published CSV/parquet file whose rows LoanWhiz generated | `"synthetic"` |

The channel is not chosen by the loader. `channel_for(url)` reads it off the
tape's source kind, which is read off the identifier's scheme, and the facts
table pairing kind to channel is guarded for total coverage at import. So there
is no branch anywhere that can hand `"direct"` to a tape that is not one — which
is the property the third row exists for. A synthetic tape travels the *same*
pandas branch as a direct read, reading the same kind of file; the identifier is
the only thing separating them.

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

1. **Strips any provenance scheme, then dispatches on the file extension** (the
   query string is stripped too, so signed URLs like `…/tape.parquet?token=…`
   still route correctly). Scheme first, format second: reading the extension
   off the raw identifier would send `synthetic:…/x.parquet` to the CSV reader.
   - `.parquet` / `.pq` → `pandas.read_parquet(underlying_url(file_url))`
   - anything else → `pandas.read_csv(underlying_url(file_url), low_memory=False)`
2. **Optionally slices by reporting period.** Combined multi-month tapes (e.g.
   `Overall_2024_2025_all_months.parquet`) carry many `reporting_date` values in
   one file. When `period` is set and a `reporting_date` column is present, the
   frame is filtered to that single cut-off. A `period` that matches no rows is a
   `ValueError` (fail loud, never silently empty).
3. **Tags provenance.** The channel is resolved from the identifier before
   either branch runs, so a file read down this path is `"direct"` only when it
   declares no scheme.

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

`green_lion.load_tape` is `pandas.read_csv(underlying_url(<identifier>))` under
the hood — the same read `_load_tape` performs, on the same resolved URL.
Green Lion 2026-1's three tapes are
**synthetic**, and their registered identifiers say so, so feeding one through
`EsmaTapeNormaliser` produces an `EsmaTapeOutput` with
`data_source="synthetic"`. Until #483 they reported `"direct"`: the word
"synthetic" was in the filename and in the data card, neither of which any
claim-making surface reads.

### A reader must not hold the identifier rules

A registered tape URL is an **identifier**, not a path, so nothing may read one
without first resolving it. Two forms are allowed, and no third:

- **Go through the seam** — `esma_tape_normaliser._load_tape`. Required for any
  reader that can be handed *any* registered tape, because a derived identifier
  names a source document to be reconstructed, not a file to parse. The
  collections aggregator (behind `GET /deal/{id}/collections` and the agent
  tool) reads this way: the CLO deal registers derived tapes.
- **Resolve, then read** — `underlying_url(url)` before `pandas`. Allowed only
  where every identifier the reader can receive names a published file, as for
  `loanwhiz.data.green_lion` and `demo/run_green_lion.py`, which serve Green
  Lion's tapes alone.

Calling `pandas` on the raw identifier is the third form, and it raises. It also
used to be invisible: the readers that did it are reachable only with the
network, so the offline suite never executed them and stayed green while every
live load failed — the same shape as the undeclared `pypdf` import.
`tests/test_tape_seam_bypass.py` closes that, generally rather than reader by
reader: it walks the AST of `src/`, `demo/`, `scripts/` and `mcp/` for `pandas`
reads whose path never passed `underlying_url`, so a bypassing reader added
tomorrow reds even if nothing offline calls it. Its second layer drives every
registered identifier through every known reader with `pandas` and the deriver
replaced by tripwires, giving those live paths their first offline coverage.

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

## The third path: a synthetic pool

Some deals publish no loan-level data in any form. Where LoanWhiz generates a
pool so a comparison has something to compare, that fact must survive into every
surface that makes a claim — and a filename does not, which is precisely how a
generated tape came to report the provenance of a filed regulatory one.

A synthetic tape therefore carries the same kind of declaration a derived one
does, in the identifier:

```
synthetic:https://…/green_lion_2026_1_synthetic_loan_tape.csv
└─ scheme ┘└──────── an ordinary published tape file ────────┘
```

Unlike a derived URI, the body is a real file and is read exactly like any
other tape — same pandas call, same annex detection, same figures. The scheme
changes what the platform *claims* about the pool and nothing about what it
computes: strip the prefix (`tape_provenance.underlying_url`) and the
byte-identical URL comes back.

**Adding a synthetic dataset needs no code change**, in any asset class: prefix
its URL with `synthetic:` when registering it.

### A synthetic pool LoanWhiz generates, rather than one it is handed

Green Lion 2026-1's three tapes arrived ready-made. Four deals — Green Lion
2023-1 and 2024-1, Leone Arancio 2023-1 and Sol-Lion II — publish no loan tape
in any form, so LoanWhiz generates one for each (#484). The rows are generated
**once and committed**, not materialised per read: a synthetic pool comes from
parameters fixed at authoring time, so there is nothing to defer to read time,
and committing the rows puts the exact data behind every comparison chart in
git next to the fit spec that produced it.

Two scripts, both offline and deterministic:

- `scripts/investor_report_pool_fit.py` reads the deal's own published investor
  report — via a committed text extract under
  `tests/fixtures/investor_reports/` — into a **fit spec** at
  `src/loanwhiz/data/pool_fits/<deal>.json`, in which every figure names the
  report section it came from.
- `scripts/generate_synthetic_tapes.py` builds the pool from that spec and
  **refuses to write it** unless it reproduces every fitted aggregate. A
  synthetic pool that contradicts its own deal's investor report would make the
  comparison charts confidently wrong rather than honestly empty, so this is a
  refusal, not a warning.

`docs/data-card.md` records, per deal, what each pool was fitted to and what
its source states in no form.

### Registering a tape that lives in this repo

A committed tape has no upstream URL, and neither obvious identifier works: an
absolute path is machine-specific, and the committed tape-analytics seed is
named `sha256(tape_url).json`, so it would key every seed to the machine that
wrote it; a bare relative path is resolved by pandas against the *caller's*
working directory, and `run-demo-v2.sh` starts `uvicorn` without changing
directory — so the read would fail wherever the demo was launched from, and
silently, since `_tape_analytics_period` degrades on a per-tape error rather
than raising.

So the registry holds a **package-relative** body and the seam resolves it:

```
synthetic:data/tapes/synthetic/green_lion_2023_1_202604_synthetic_loan_tape.csv.gz
└ scheme ┘└──── resolved against the loanwhiz package root at load ────┘
```

`_resolve_committed_tape` rewrites that body to an absolute path *before*
`underlying_url` strips the scheme, so the registry string stays stable across
machines, the seeds stay valid, and the read no longer depends on where the
process was started. An identifier that already names a URL or an absolute path
is returned unchanged.

### Adding the next derived source

Register a member on `TapeScheme` (in `domain/tape_provenance.py`) with its
source kind, then its deriver in `derived_tape._DERIVERS`. Two import-time
guards refuse a half-registration: one for a scheme with no kind, one for a
scheme claiming the derived-from-investor-report kind with no deriver. Nothing
in the module special-cases Cairn.

## Provenance

Every ingested tape records where it came from, surfaced through the governance
evidence pack (see [`governance.md` §7](governance.md)):

| Field | Where | Value |
|---|---|---|
| `EsmaTapeOutput.data_source` | `esma_tape_normaliser.py` | `"direct"`, `"derived"` or `"synthetic"` |
| `TapeAnalyticsPeriod.data_source` | `GET /deal/{id}/tape-analytics` | the same, per reporting period (required — no default) |
| Tape citation excerpt | `Citation.excerpt` | `"… (ingested via <channel>)"`, plus the source kind's full disclosure sentence for any tape that declares one |
| `TapeSourceKind` | `domain/tape_provenance.py` | `derived_from_investor_report`, `synthetic_generated`, or **not declared** for an undeclared published file |
| `TapeSourceKind.describes_real_assets` | `domain/tape_provenance.py` | `False` for a synthetic tape — the predicate to branch on before treating pool figures as evidence |

### Channel is not the same question as kind

`data_source` says **how the tape arrived**. `TapeSourceKind` says **what it
is** — and the two are kept apart on purpose.

A derived tape is `DERIVED_FROM_INVESTOR_REPORT` and a generated one is
`SYNTHETIC_GENERATED`; every surface that reports provenance renders that kind's
`disclosure` string verbatim rather than composing its own wording, so the claim
cannot drift between the citation, the capability-matrix cell and the data card.

The two kinds are not degrees of the same thing. Both answer `False` to
`is_regulatory_filing`, and they differ on `describes_real_assets`: a derived
tape describes real loans that someone else stated, a synthetic one describes
none. A surface that folds the second question into the first would read a
trustee-report tape as fabricated, which is its own dishonesty.

A published tape read through the direct path has **no declared kind**. That is
deliberate and is *not* a synonym for `FILED_ARTICLE_7_1_A`: this repo holds no
evidence about whether a given published file is its originator's Article 7(1)(a)
disclosure or a redistribution of it, and defaulting to "filed" would make the
system's most consequential claim by omission. A surface rendering provenance
must therefore handle four answers — derived, synthetic, filed, and not
declared.

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
