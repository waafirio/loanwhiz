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

## Projection primitive note

`POST /deal/{id}/project` is backed by the `WaterfallRunner` primitive (the
available deterministic forward-projection primitive), running the deal's
Revenue + Redemption waterfalls against the base-case capital structure with a
per-scenario collection stress factor applied. The request/response is
scenario-shaped so a future dedicated cashflow projector can be swapped in
without changing the API contract.
