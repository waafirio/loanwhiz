---
id: 2026-06-03-api-expansion
title: API Expansion
status: filed
created: 2026-06-03
updated: 2026-06-03
epics: [107]
---

# API Expansion

## Context & intent

### The problem

Building the Demo UI v2 (Next.js, epic #96) surfaced the real bottleneck: **the FastAPI REST API (#22) is far thinner than the backend behind it.** The v2 frontend looks clean but renders thin/empty sections because the API only exposes:

- `GET /deal/{id}/model` → just the deal **config** (name + document URLs), NOT the extracted deal model
- `GET /deal/{id}/compliance` → covenant monitor output
- `POST /deal/{id}/project` → projection (no WAL field)
- `POST /query` → the agent

Everything rich the backend already computes — the **extracted deal model** (waterfall, triggers, completeness) sitting in the pre-warmed cache, **tape analytics** (pool/EPC/arrears trends across 3 periods), the **live waterfall run** (cascade + tranche distributions), report verification — has **no REST endpoint**. The v1 Gradio app only looks richer because it bypasses the API entirely and calls the primitives in-process. Both UIs are gated by the API surface; v2 (the chosen demo surface) can only be as good as the endpoints it can call.

A second, related defect: the deal-model assembler's `_extract_tranches()` returns **`tranches=0`** for Green Lion despite the waterfall (11 steps) and triggers (3) extracting fine — so even once `/deal/{id}/model` returns the extracted model, the tranche section stays empty until that bug is fixed.

### Why this shape

This epic closes the gap between what the backend can do and what the API exposes, then rewires v2 to consume the now-rich endpoints and finally retires the v1 Gradio fallback. Ordering rationale:

- The four backend/API items (#tranches-fix, #model-endpoint, #tape-analytics, #waterfall-endpoint, #wal) are **mutually independent** — different endpoints / a different module — so they run **in parallel**.
- The frontend rewire + Gradio retirement is the **only sequential** child: it depends on all the new endpoints existing, and retiring Gradio is safe only once v2 reaches data parity (the explicit reason retirement was deferred out of epic #96, child #100).

This delivers end-to-end value: not just "more endpoints" but "v2 is now the real, rich demo and Gradio is gone."

### Decisions made with the operator

- **File as an epic and run the fleet** (operator chose this over "file issues + I implement" or "I take it untracked") — autonomous build with `liz:auto`, same mode as epics #75 and #96.
- The `tranches=0` fix is its **own child**, not folded into the model-endpoint child — it's an extraction-layer bug (`assembler._extract_tranches`), distinct from the API-layer endpoint work, and independently parallelizable. The model endpoint should still ship even if the tranche fix lands separately (it returns whatever the assembler produces).

### Alternatives considered and rejected

- **Fold tranches-fix into the /deal/{id}/model endpoint child** — rejected; different layer (extraction vs api), and keeping them separate lets both run in parallel and be reviewed independently.
- **Add a BFF / GraphQL layer** — over-engineering; the existing FastAPI + a few REST endpoints is sufficient and matches what the frontend already calls.
- **Keep both UIs indefinitely** — rejected; v1 Gradio only existed as a fallback for the API gap. Once parity lands, two demo surfaces is confusing; retire Gradio.
- **Stream `/query`** — out of scope; the chat's await-and-append is fine for the demo.

### Relationship to prior epics

Closes the follow-up gap flagged in epic #96's promotion (PR #106). The backend primitives, extraction pipeline, and cache all already exist (epics #6, #12) — this is purely about exposing them over REST and consuming them in the v2 frontend (epic #96).

## Decomposition

### Epic: API Expansion — expose the full backend over REST + rich v2 + retire Gradio   (umbrella #107)

Expand the FastAPI to expose the extracted deal model, tape analytics, and waterfall; add WAL to projection; fix the tranches extraction bug; then rewire the v2 Next.js pages to the rich endpoints and retire the v1 Gradio app.

- **Fix tranches extraction** — make `assembler._extract_tranches()` correctly derive the tranche structure (Class A/B/C: size, rating, rate, subordination) from the prospectus tranche table / extracted waterfall, so the deal model carries tranches instead of an empty list. Sequencing: parallel. Paths: `src/loanwhiz/extraction/assembler.py`, `tests/test_assembler.py`.
- **`/deal/{id}/model` returns the extracted DealModel** — change the endpoint to load and return the cached extracted `DealModel` (tranches, triggers, waterfall, completeness, metadata) instead of the raw config dict; fall back gracefully when the cache is cold. Sequencing: parallel. Paths: `src/loanwhiz/api/main.py`, `tests/test_api.py`.
- **`GET /deal/{id}/tape-analytics` endpoint** — new endpoint returning the 3-period pool analytics (balance, loan count, arrears, weighted LTV, EPC/geographic/property breakdowns) via `EsmaTapeNormaliser` over the deal's tapes. Sequencing: parallel. Paths: `src/loanwhiz/api/main.py`, `tests/test_api.py`.
- **`GET /deal/{id}/waterfall` endpoint** — new endpoint running `CollectionsAggregator` → `WaterfallRunner` for the latest period and returning the 11-step cascade + per-tranche distributions. Sequencing: parallel. Paths: `src/loanwhiz/api/main.py`, `tests/test_api.py`.
- **WAL in the projection response** — add weighted-average-life (per scenario/tranche, from `CashflowProjector.wal_class_a_months`) to the `POST /deal/{id}/project` response. Sequencing: parallel. Paths: `src/loanwhiz/api/main.py`, `tests/test_api.py`.
- **Rewire v2 frontend to the rich endpoints + retire Gradio** — update the Next.js pages (Overview→real deal model, Pool→tape-analytics, Waterfall→waterfall endpoint, Projection→WAL) to consume the new endpoints and drop the partial/empty-state fallbacks; then delete the v1 Gradio app (`clients/demo/`, `demo/`) and point docs at v2 only. Sequencing: sequential. After all five backend children. Paths: `web/app/**`, `web/components/**`, `web/lib/api.ts`, `clients/**`, `demo/**`, `README.md`.

## Filed issues

- Epic "API Expansion" → umbrella #107
  - #108 Fix tranches extraction in deal-model assembler  (parallel)
  - #109 /deal/{id}/model returns the extracted DealModel  (parallel)
  - #110 GET /deal/{id}/tape-analytics endpoint  (parallel)
  - #111 GET /deal/{id}/waterfall endpoint  (parallel)
  - #112 Add WAL to projection response  (parallel)
  - #113 Rewire v2 frontend to rich endpoints + retire Gradio  (sequential, after #108–#112)
