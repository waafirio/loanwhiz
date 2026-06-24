# LoanWhiz REST API

FastAPI service over the LoanWhiz agent and structured-finance primitives.
This is the interface every client (CLI, notebook, demo UI) calls.

## Running

```bash
# Module entrypoint (honours HOST / PORT env vars, default 0.0.0.0:8000)
python -m loanwhiz.api.run_api

# Or with autoreload during development
uvicorn loanwhiz.api.main:app --reload
```

Interactive docs are then served at `http://localhost:8000/docs`.

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/` | Service info — name, version, known deal ids |
| `GET`  | `/health` | Liveness probe |
| `POST` | `/query` | Answer a natural-language question via the agent |
| `GET`  | `/deal/{deal_id}/model` | Deal context (document URLs, structure) |
| `GET`  | `/deal/{deal_id}/compliance` | Covenant compliance across all periods |
| `POST` | `/deal/{deal_id}/project` | Forward payment-waterfall projection under scenarios |
| `POST` | `/deals` | Register a deal at runtime (self-service onboarding) |
| `POST` | `/deal/{deal_id}/ingest/tape` | Append + validate-load an ESMA tape for a deal |
| `POST` | `/deal/{deal_id}/ingest/report` | Add a Notes & Cash report URL + enqueue its extraction (async) |

`green-lion-2026-1` is the in-code default deal id; more are added as committed
data (`data/deals.json`) or at runtime via `POST /deals` (below).

## Self-service ingest (#399)

Onboarding a deal is a **product action**, not a committed-file edit + restart.
Three routes mutate the config-driven registry and trigger the existing
materialisation paths — no second source of truth.

**Persistence: the runtime overlay.** Registrations and ingest mutations persist
to `data/deals.runtime.json` — a **gitignored runtime overlay**, NOT the
committed, human-curated `data/deals.json`. On cold start the registry merges the
committed file first, then overlays the runtime file (runtime entries add to /
override by `deal_id`), tolerating an absent or malformed runtime file. So a
runtime-registered deal survives a restart while the curated registry is never
mutated at runtime.

### Register a deal — `POST /deals`

`deal_id`, `deal_name`, `prospectus_url` are required; `tape_urls`,
`investor_report_urls`, `notes_cash_report_urls` and the structural keys
(`capital_structure`, `reserve_account_target`, `original_pool_balance`,
`projection_base`, `jurisdiction`) are optional. Returns `201` with the persisted
context; a duplicate `deal_id` returns `409` unless `?force=true` (overwrite); a
missing required field returns `422`.

```bash
curl -X POST http://localhost:8000/deals \
  -H 'Content-Type: application/json' \
  -d '{"deal_id": "blue-tiger-2026-1", "deal_name": "Blue Tiger 2026-1 B.V.",
       "prospectus_url": "https://example/blue-tiger-prospectus.pdf"}'
# 201 — {"deal_id":"blue-tiger-2026-1","deal":{...}}

# Overwrite an existing registration:
curl -X POST 'http://localhost:8000/deals?force=true' -H 'Content-Type: application/json' -d '{...}'
```

### Ingest a tape — `POST /deal/{deal_id}/ingest/tape`

Appends `{date, url}` to the deal's `tape_urls` and **validate-loads** the tape
inline (a bad URL/parse fails loudly with `422`; the tape itself is loaded lazily
by the analytics/waterfall paths on the next call). An unknown deal id returns
`404`; an identical `{date, url}` is idempotent (no duplicate append). Returns the
updated `tape_urls`.

```bash
curl -X POST http://localhost:8000/deal/blue-tiger-2026-1/ingest/tape \
  -H 'Content-Type: application/json' \
  -d '{"date": "2026-05-31", "url": "https://example/blue_tiger_202605_tape.csv"}'
```

### Ingest a report — `POST /deal/{deal_id}/ingest/report` (async)

Adds a Notes & Cash report `{url[, period]}` to the deal and enqueues a background
job that runs the live report extraction (`resolve_parsed_report(allow_live=True)`).
Returns `202` immediately — the request **never** blocks on the minutes-long
network+LLM extraction. The job populates the durable report cache so the offline
`GET /deal/{id}/report-gate` / `/waterfall` paths then resolve the report. An
unknown deal id returns `404`.

```bash
curl -X POST http://localhost:8000/deal/blue-tiger-2026-1/ingest/report \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://example/blue-tiger-may-2026-report.pdf", "period": "May 2026"}'
# 202 — {"deal_id":"blue-tiger-2026-1","status":"queued",...}

# Poll for completion:
curl http://localhost:8000/deal/blue-tiger-2026-1/ingest/report/status
# {"deal_id":"blue-tiger-2026-1","status":"succeeded",...}  (or "running" / "failed")
```

## curl examples

Service info:

```bash
curl http://localhost:8000/
# {"service":"LoanWhiz API","version":"0.1.0","deals":["green-lion-2026-1"]}
```

Health:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

Ask the agent a question (runs the LangGraph agent — needs Gemini credentials):

```bash
curl -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"question": "Is the Green Lion deal in compliance with its covenants?", "confidence_threshold": 0.7}'
```

Deal model:

```bash
curl http://localhost:8000/deal/green-lion-2026-1/model
```

An unknown deal id returns `404`:

```bash
curl -i http://localhost:8000/deal/unknown/model
# HTTP/1.1 404 Not Found
# {"detail":"Deal unknown not found"}
```

Compliance check (downloads + normalises the ESMA tapes, then runs the
covenant monitor):

```bash
curl http://localhost:8000/deal/green-lion-2026-1/compliance
```

Forward projection (defaults to base + stress scenarios over 12 months):

```bash
curl -X POST http://localhost:8000/deal/green-lion-2026-1/project \
  -H 'Content-Type: application/json' \
  -d '{"scenarios": ["base", "stress"], "months": 12}'
```

## Projection engine note

`POST /deal/{id}/project` runs the deal **forward through the same engine the
history endpoints use**: for each scenario a `ScenarioGenerator` produces a
synthetic `PeriodInputs` stream (CPR / CDR / recovery / rate-shift, decomposed
to monthly with one consistent CDR↔SMM survival convention) and that stream is
folded through `period_state_machine.run_period`. Projection is therefore the
same fold as history, not a faked single-period collection-haircut sensitivity.

The response keeps its scenario-shaped contract — `deal_id`, `months`,
`scenarios`, `projections`, `wal` — but each scenario's `projections[scenario]`
now carries the **per-period projected state series** (`periods[]` with pool
balance, tranche balances, reserve, cumulative losses) plus a real Class A
**WAL** derived from the engine-computed amortisation (`wal_class_a_months` /
`wal_class_a_years`, mirrored in the top-level `wal` map).

The projection seed (capital structure, reserve target, original pool balance,
opening pool balance, coupon rate) is resolved from the deal's own
`projection_base` / structural config; a non-Green-Lion deal missing that config
fails loudly (422) rather than borrowing Green Lion's numbers.
