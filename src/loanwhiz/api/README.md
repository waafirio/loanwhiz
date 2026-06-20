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

The one known deal id is `green-lion-2026-1`.

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
