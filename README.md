# LoanWhiz

Structured finance agent framework — SF-native primitives, deal model extraction, waterfall execution, and LangGraph orchestration. Built for the Barcelona AI Tinkerers Structured Finance Hackathon 2026 (demo day: 10 June).

---

## Architecture

```
CLIENTS
  Demo UI (web/) — Next.js dashboard + docked chat  |  REST API (FastAPI)
                         |
                         v
          LANGGRAPH AGENT SERVICE
    Planner -> DAG executor -> Validator -> Confidence scorer
    Human review router -> Audit trail
    [Governance: FINOS AI Governance Framework]
            |                         |
            v                         v
    SF PRIMITIVES            DEAL MODEL (JSON, per deal)
      esma_tape_normaliser     definitions{}
      waterfall_runner         waterfall[]  <- prospectus section 5.2
      covenant_monitor         triggers[]
      report_verifier          tranches[]
      cashflow_projector       pool_eligibility{}
      audit_logger
      collections_aggregator
            |                         ^
            +-------- DATA LAYER -----+
      deeploans ETL + MCP server  (ESMA tape ingestion, multi-annex)
      Docling extraction pipeline (prospectus -> deal model JSON)
      HuggingFace: Algoritmica/green-lion-2026 (2026 deal package)
                 + Algoritmica/green-lion-2024-2025 (24-month history)
```

---

## Quickstart

### Prerequisites

- Python 3.12
- A GCP project with Vertex AI API enabled
- Application Default Credentials configured (`gcloud auth application-default login`)

### Installation

```bash
git clone https://github.com/waafirio/loanwhiz.git
cd loanwhiz
pip install -e .
```

### Run the smoke test

Verifies that Vertex AI / Gemini 2.5 Flash is reachable under your credentials:

```bash
pytest tests/test_smoke.py
```

### Run against the Green Lion demo (CLI)

`demo/run_green_lion.py` is a standalone CLI walkthrough — no UI, useful as a backup or for headless runs:

```bash
python demo/run_green_lion.py
```

This loads the Green Lion 2026-1 deal package (prospectus, three monthly ESMA tapes, three monthly investor reports), runs the full extraction and execution pipeline, and prints a structured summary.

---

## Demo UI

The demo UI is a Next.js dashboard in `web/` served over the FastAPI REST API. One command starts both (API on :8000, UI on :3000):

```bash
./scripts/run-demo-v2.sh
```

Then open http://localhost:3000. The dashboard shares one loaded deal across five views, plus a docked chat panel:

1. **Overview** — the extracted deal model (tranche structure, trigger names, completeness).
2. **Pool & Performance** — 3-period pool analytics and arrears / EPC / geographic distributions.
3. **Waterfall** — the revenue priority cascade and per-tranche distributions for the latest period.
4. **Compliance & Covenants** — the live covenant monitor across reporting periods.
5. **Projection** — a single-period **stress sensitivity**: the waterfall re-run on the base-case capital structure under base vs stressed collection factors, with Class A WAL. (This is a sensitivity, not a multi-month CPR/CDR projection — the dedicated forward `cashflow_projector` is not yet wired.)

The docked chat panel answers ad-hoc deal questions grounded in the loaded deal model and tapes.

---

## GCP / Vertex AI Setup

1. Create a GCP project (or reuse an existing one).
2. Enable the Vertex AI API:
   ```bash
   gcloud services enable aiplatform.googleapis.com
   ```
3. Authenticate with Application Default Credentials:
   ```bash
   gcloud auth application-default login
   ```
4. Set your active project:
   ```bash
   gcloud config set project <project-id>
   ```
5. Update `src/loanwhiz/config.py` to reflect your project ID:
   ```python
   GCP_PROJECT = "<project-id>"
   GCP_LOCATION = "us-central1"
   ```

**Models used:**

| Role | Model |
|------|-------|
| Orchestration and planning | Gemini 2.5 Flash (`gemini-2.5-flash`) |
| Prospectus extraction | Gemini 2.5 Pro (`gemini-2.5-pro`) |

---

## How to Run Against a New Deal

The framework is **data-driven by design**: adding a deal is *data*, not code. The canonical deal registry (`DEAL_REGISTRY` in `src/loanwhiz/config.py`) starts from the in-code Green Lion default and merges in any extra deals from the optional data file `src/loanwhiz/data/deals.json` at import time — so you add a deal **without editing any Python**. The API sources its `DEALS` from this registry, and the `/deal/{deal_id}/...` routes are keyed by the `deal_id` you choose, and the waterfall interpreter executes the deal's *extracted* model rather than hardcoded logic.

> **Scope of validation.** The pipeline is data-driven, but it has been
> end-to-end **validated on exactly one deal so far — Green Lion 2026-1**.
> Real multi-deal validation (seasoned Green Lion 2023-1 / 2024-1 against
> their own published Notes & Cash and investor reports) is **in progress**
> (epic #206), not complete. Treat "add any RMBS, it just works" as the
> design intent demonstrated on one deal — not a proven claim across
> arbitrary deals today.

Create `src/loanwhiz/data/deals.json` as a JSON object mapping each `deal_id` to a deal-context dict (same shape as the in-code `GREEN_LION`):

```json
{
  "my-deal-2026-1": {
    "deal_name": "My Deal 2026-1",
    "prospectus_url": "https://...",
    "tape_urls": [
      {"date": "2026-02-28", "url": "https://..."},
      {"date": "2026-03-31", "url": "https://..."}
    ],
    "investor_report_urls": [
      {"period": "February 2026", "url": "https://..."},
      {"period": "March 2026",    "url": "https://..."}
    ]
  }
}
```

A missing or malformed `deals.json` is tolerated — the in-code Green Lion default still loads, so a bad data file can never take the API down (it is logged and skipped). An entry that reuses an existing `deal_id` overrides the default.

Each `tape_urls[].url` may point at a **CSV or parquet** tape — the `esma_tape_normaliser` primitive is format-agnostic and dispatches on the URL suffix (`.parquet`/`.pq` → parquet, anything else → CSV), and can additionally slice a single reporting period out of a **combined multi-month parquet** (one file holding many `reporting_date`s). Tapes are referenced by their direct URLs (HuggingFace or a local `file://` path); the normaliser loads each and auto-detects its ESMA Annex schema.

Pass this dict to the agent service or the extraction pipeline directly. The framework fetches the documents, runs Docling extraction to build the deal model JSON, and caches the result locally so extraction runs only once per deal.

### Optional deal-context keys

Beyond the four required keys above, a deal-context dict may carry these **optional** keys that the API resolves from the deal, falling back to Green Lion defaults when absent — so a new deal works out of the box and only overrides what differs:

| Key | Shape | Resolved by | Default when absent |
|---|---|---|---|
| `capital_structure` | object — `class_a_balance`, `class_a_rate_pct`, `class_b_balance`, `class_c_balance` | `GET /deal/{id}/waterfall` (the per-tranche cascade) | Green Lion 2026-1 capital structure |
| `original_pool_balance` | float (EUR) | `GET /deal/{id}/compliance` (clean-up-call proximity + loss-rate denominator) | Green Lion closing balance |
| `projection_base` | object — `current_pool_balance` + capital-structure / reserve-account figures | `GET /deal/{id}/project` (forward projection base case) | Green Lion projection base |

Covenant **triggers** are not a deal-context key: `/deal/{id}/compliance` uses the deal model's *extracted* `covenants.triggers` (from the cached deal model the extraction pipeline builds), falling back to the covenant monitor's defaults when no extracted triggers are present.

Green Lion itself carries none of these optional keys and uses the defaults unchanged.

See `GREEN_LION` and `DEAL_REGISTRY` in `config.py` for a fully worked example using the publicly available Green Lion 2024–2026 dataset.

---

## How to Contribute a New Primitive

1. **Subclass `Primitive`** from `src/loanwhiz/primitives/base.py`:
   ```python
   from loanwhiz.primitives.base import Primitive, register_primitive

   @register_primitive
   class MyPrimitive(Primitive):
       name = "my_primitive"
       version = "0.1.0"

       def execute(self, inputs: dict) -> dict:
           ...
   ```

2. **Implement `execute()`** — accept a typed input dict, return a typed output dict. Include a `confidence` float and a `citations` list in the output.

3. **Decorate with `@register_primitive`** — this makes the primitive discoverable by the agent service and the primitive catalogue.

4. **Add tests** under `tests/` covering at least the happy path, one edge case, and the citation/confidence output structure.

5. Look at the existing primitives in `src/loanwhiz/primitives/` as worked examples.

---

## Primitives

**Reachability** marks how each primitive is reached, and is surfaced verbatim by `GET /primitives`. **Live** = called by a REST endpoint and/or exposed as a LangGraph agent tool; **library-only** = registered (so it appears in the catalogue) and importable as library code, but not yet reached by an endpoint or agent tool. Nothing is advertised as live that a judge can't reach.

| Name | Description | Version | Reachability |
|------|-------------|---------|--------------|
| `esma_tape_normaliser` | Normalises ESMA Annex 2–8 loan tapes; computes pool analytics (WAL, arrears breakdown, EPC/geo/rate distributions) | 0.1.0 | Live |
| `collections_aggregator` | Aggregates monthly collections (interest, principal, prepayments, recoveries) from ESMA tapes into waterfall-ready inputs | 0.1.0 | Live |
| `waterfall_runner` | Executes the model-driven waterfall against a period's tape collections; returns computed distributions per tranche with full audit trace | 0.1.0 | Live |
| `covenant_monitor` | Checks tape metrics against extracted trigger thresholds; tracks breach proximity over time | 0.1.0 | Live |
| `audit_logger` | Wraps every primitive call with provenance: input hash, output, confidence score, citations, timestamp, model version, human review flag | 0.1.0 | Live |
| `report_verifier` | Compares waterfall-computed distributions against investor-report actuals; flags discrepancies | 0.1.0 | Library-only |
| `cashflow_projector` | Iterates the waterfall runner forward under base/stress scenarios (a future dedicated projector; the live `/project` route uses the waterfall runner as a single-period stress sensitivity) | 0.1.0 | Library-only |

---

## Data

**Green Lion 2026-1** — a complete, publicly available structured finance deal package built around a synthetic Dutch RMBS. It spans **27 months of loan-tape history** across two HuggingFace datasets:

- **`Algoritmica/green-lion-2024-2025`** — 24 monthly ESMA Annex 2 loan tapes, one per month from January 2024 through December 2025.
- **`Algoritmica/green-lion-2026`** — the 2026 deal package: 3 monthly ESMA Annex 2 loan tapes (February, March, April 2026), the prospectus PDF (Green Lion 2026-1 B.V.), and 3 monthly investor reports (February, March, April 2026).

That is **27 monthly tapes in total** (24 + 3). All tapes share the same ESMA Annex 2 schema. January 2026 (`202601`) exists in neither dataset and is an intentional gap in the chronology — the framework simply skips it. `src/loanwhiz/config.py` builds the full chronological `tape_urls` list programmatically (`_historical_tape_entries()` for 2024–2025, plus the three 2026 entries).

These 27 tapes are **synthetic period snapshots, re-sampled each period** — loan identifiers do not persist across months, so the series is a sequence of point-in-time pool snapshots rather than a true longitudinal loan-level panel. Period-to-period collections and losses are therefore derived by net reconciliation to pool movement, not by tracking individual loans over time. The history is real *in count and schema* and drives genuine multi-period views; it is not a tracked-cohort performance record. The three 2026 reporting periods (Feb–Apr) are the ones accompanied by real investor reports.

This is the primary test and demo dataset for the hackathon submission. All loan-level data is synthetic and was released by Algoritmica.ai specifically for this hackathon. See [docs/data-card.md](docs/data-card.md) for the full data card, including the synthetic-vs-real breakdown.

Tape ingestion is format-agnostic: loan tapes may be supplied as **CSV or parquet**, including a single combined multi-month parquet from which the loader selects a reporting period. Green Lion ships as per-period CSV tapes.

---

## Built On

| Component | Source | License |
|-----------|--------|---------|
| deeploans | Algoritmica.ai | Apache 2.0 |
| FINOS AI Governance Framework | FINOS | Apache 2.0 |
| Docling | IBM | Apache 2.0 |
| LangGraph | LangChain | MIT |
| Vertex AI Gemini | Google Cloud | Commercial |

---

## License

Apache 2.0. See [LICENSE](LICENSE).
