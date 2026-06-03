---
id: 2026-06-04-scale-and-framework
title: Scale-readiness & Framework Surface
status: filed
created: 2026-06-04
updated: 2026-06-04
epics: [128, 129]
---

# Scale-readiness & Framework Surface

## Context & intent

Two operator-driven needs, planned together because they're the run-up to demo day (10 June) and both are additive on the now-complete stack (8 promoted epics; v2 Next.js UI on the FastAPI; chat fixed via #127).

### Trigger

The hackathon organisers are synthesising **Green Lion 2024–2026 loan-level data** (ready by Friday) and sponsors may supply **more deals**. Operator's directive: the system (backend + UI) must cope with much more data, and the UI must surface the elements the *challenge* actually asks for — the **framework primitives** — not just deal data.

### Epic A — Scale-readiness (why this shape)

Today's data is 3 monthly tapes × ~3,200 loans. The incoming shape (operator's stated assumption): **monthly data going back ~4 years (~48 periods), multiple deals.** Where the current system strains, and the response:

- **`/deal/{id}/tape-analytics` is the bottleneck** — it loops over *every* tape synchronously per request (each a HuggingFace fetch + pandas parse). 3 tapes ≈ seconds; ~48 ≈ 30–90s → UI timeouts. → **Cache computed analytics** (compute once, serve fast).
- **Multi-deal is hardcoded** — `DEALS = {"green-lion-2026-1": GREEN_LION}`, `config.GREEN_LION` a single dict. Multiple deals are now confirmed in-scope. → **Config-driven deal registry** (backend + API), surfaced via a UI deal selector.
- **Extraction artifacts live in `/tmp` and re-OCR on `force_refresh`** — operator flagged the laptop grinding. Clarified: a server *restart* already reads the cache (no re-extract); the real gaps are (1) `/tmp` is wiped on reboot and (2) `force_refresh` re-runs the ~18–37 min Docling OCR. → **Persist extracted deal-model artifacts durably (out of `/tmp`, committed) and cache the Docling markdown separately** so re-extraction is cheap and the demo never waits. Pre-warm each deal once.
- **UI is built for 3 periods** — categorical charts, unpaginated tables. ~48 periods needs a **time-axis** and **pagination/virtualization**, plus the deal selector.
- **Agent context** — `/query` must stay selective over ~48 periods rather than dumping all into the prompt (cost/latency/context).

### Epic B — Framework surface (why this shape)

The challenge is a "Structured Finance Agent **Framework**." The v2 UI currently shows *deal data* — a consumer of the framework — but never the framework itself: the primitives, their typed contracts, the governance/auditability. That's precisely what judges (esp. Luca Borella, FINOS) are evaluating. We already built the substrate (the `PRIMITIVE_REGISTRY` #34, the catalogue #30, `GovernanceEvidencePack` #23) — it just isn't exposed over the API or in the UI. So: expose it.

- `GET /primitives` — the registry catalogue (`PRIMITIVE_REGISTRY.describe()`): name, version, description, typed input/output, tags, confidence logic.
- `GET /governance/{pack_id}` — a query's evidence pack (tool-call DAG, confidence, citations, audit trail) — the "auditable agents" the challenge emphasises.
- A **Framework** UI section: a Primitives catalogue page + a governance/evidence (reasoning-trace) view.

### Cross-epic notes

- Both epics add endpoints to `src/loanwhiz/api/main.py` and pages under `web/` — parallel children there will produce additive merge conflicts at PR time, resolved at merge (the established pattern this session). Kept self-contained per child to minimise.
- Run both under `liz:auto` (operator-confirmed), same autonomous mode as prior epics.
- Operator confirmed: **both epics, equal priority.**

### Alternatives considered / rejected

- **Async/streaming tape loading instead of caching** — caching is simpler and the bigger win; re-fetching 48 tapes is wasteful regardless of concurrency. Cache first.
- **A real DB for analytics** — over-engineering for a hackathon; a keyed on-disk cache (parquet/json) suffices.
- **Letting the demo re-extract live** — rejected; ~18–37 min Docling OCR. Durable pre-warmed artifacts.
- **A heavyweight "framework console"** — kept lean: catalogue + evidence views over the registry/data we already have, not a new subsystem.

## Decomposition

### Epic: Scale-readiness — handle 4-years-monthly, multi-deal data   (umbrella #128)

Make the backend + UI cope with ~48-period, multi-deal data: cache tape analytics, a config-driven deal registry, durable extraction artifacts, time-series/paginated UI, and bounded agent context.

- **Cache tape-analytics** — compute the per-period pool analytics once and serve from a keyed cache (memory + on-disk) so `/deal/{id}/tape-analytics` doesn't re-fetch/re-parse every tape per request. Sequencing: parallel. Paths: `src/loanwhiz/api/main.py`, `src/loanwhiz/primitives/esma_tape_normaliser.py`.
- **Config-driven multi-deal registry** — replace the hardcoded single-deal `DEALS`/`GREEN_LION` with a registry of deals defined in config/data (id, name, prospectus, tape URLs, report URLs), so adding a deal is data, not code; expose available deals via the API. Sequencing: parallel. Paths: `src/loanwhiz/config.py`, `src/loanwhiz/api/main.py`.
- **Durable extraction artifacts + Docling-markdown cache** — persist extracted deal models out of `/tmp` to a durable, committed location, and cache the Docling markdown separately so `force_refresh` of the LLM extraction doesn't re-run the ~18–37 min OCR. Sequencing: parallel. Paths: `src/loanwhiz/extraction/assembler.py`, `data/deals/**`.
- **Bound agent context over many periods** — keep `/query` selective (e.g. latest + summary, not all ~48 periods dumped into the prompt) so cost/latency/context stay sane at scale. Sequencing: parallel. Paths: `src/loanwhiz/agent/planner.py`, `src/loanwhiz/agent/tools.py`.
- **Scale the UI — time-series, pagination, deal selector** — Pool/Projection charts use a real time-axis for ~48 periods; long tables paginate/virtualize; a deal selector (from the registry) drives all pages. Sequencing: sequential. After the multi-deal registry and tape-analytics cache children. Paths: `web/app/**`, `web/components/**`, `web/lib/api.ts`.

### Epic: Framework surface — expose primitives & governance in the UI   (umbrella #129)

Surface the framework itself — the registered primitives and the governance/evidence trail — over the API and in a Framework UI section, aligning the demo with what the challenge judges.

- **`GET /primitives` endpoint** — return the primitive registry catalogue (`PRIMITIVE_REGISTRY.describe()`): name, version, description, typed input/output schema, tags, confidence logic. Sequencing: parallel. Paths: `src/loanwhiz/api/main.py`.
- **`GET /governance/{pack_id}` endpoint** — return a stored `GovernanceEvidencePack` (tool-call trace, confidence, citations, audit trail). Sequencing: parallel. Paths: `src/loanwhiz/api/main.py`, `src/loanwhiz/governance/evidence_pack.py`.
- **Primitives catalogue page** — a "Framework" nav section + a page listing the registered primitives with their typed contracts/tags from `GET /primitives`. Sequencing: sequential. After the `/primitives` endpoint. Paths: `web/app/**`, `web/components/**`, `web/lib/api.ts`.
- **Governance / evidence view** — a UI surface for a query's evidence pack (reasoning-trace, confidence, citations, audit) from `GET /governance/{pack_id}`; link it from the chat answer. Sequencing: sequential. After the `/governance` endpoint. Paths: `web/app/**`, `web/components/**`, `web/lib/api.ts`.

## Filed issues

- Epic "Scale-readiness" → umbrella #128
  - #130 Cache tape-analytics  (parallel)
  - #131 Config-driven multi-deal registry  (parallel)
  - #132 Durable extraction artifacts + Docling-markdown cache + force_refresh busting  (parallel)
  - #133 Bound agent context over many periods  (parallel)
  - #134 Scale the UI — time-series, pagination, deal selector  (sequential, after #130 #131)
- Epic "Framework surface" → umbrella #129
  - #135 GET /primitives endpoint  (parallel)
  - #136 GET /governance/{pack_id} endpoint  (parallel)
  - #137 Primitives catalogue page  (sequential, after #135)
  - #138 Governance / evidence view  (sequential, after #136)
