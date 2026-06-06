# LoanWhiz

Structured finance agent framework — SF-native primitives, deal model extraction, waterfall execution, and LangGraph orchestration. Built for the Barcelona AI Tinkerers Structured Finance Hackathon 2026 (demo day: 10 June).

The same governed primitives run **end-to-end across 5 deals in 3 jurisdictions** — Dutch (Green Lion 2023-1 / 2024-1 / 2026-1), Italian (Leone Arancio RMBS 2023-1), and Spanish (Sol-Lion II RMBS) — and the model-driven waterfall engine has been **validated to the cent against a real published deal** (Green Lion 2024-1's own Notes & Cash Priority of Payments). What is *validated* vs merely *ran* vs *not-applicable* is tracked honestly in a per-cell capability matrix (`GET /capability-matrix` and the **Showcase** view) — the source of truth is **1 validated / 9 ran / 15 not-applicable**, never a blanket "validated everywhere". Extraction on the non-English prospectuses is honestly **partial** (see the data/model cards). The 8 primitives are also packaged as a governed **MCP server** (`mcp/`) for third-party consumption.

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
      deeploans ETL (ESMA tape ingestion, multi-annex; live deeploans:// path + direct fallback)
      Docling extraction pipeline (prospectus -> deal model JSON)
      HuggingFace: 5 deals across 3 jurisdictions (NL / IT / ES RMBS)

CROSS-DEAL / FRAMEWORK SURFACE
      capability matrix  (primitives x 5 deals, validated/ran/not-applicable; /capability-matrix + Showcase)
      engine validation  (Green Lion 2024-1 Notes & Cash, to the cent; /validation)
      governed MCP server  (mcp/ — the 8 primitives as MCP tools, evidence pack travels with each call)
      governance surface  (FINOS evidence pack + deeploans-vs-direct provenance; /governance)
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

Then open http://localhost:3000. The sidebar groups the views into two sections (`NAV_GROUPS` in `web/lib/nav.ts`), plus a docked chat panel.

**Deal Analytics** — the per-deal views an analyst works in (one loaded deal at a time):

1. **Overview** — the extracted deal model (tranche structure, trigger names, completeness).
2. **Pool & Performance** — 3-period pool analytics and arrears / EPC / geographic distributions.
3. **Waterfall** — the revenue priority cascade and per-tranche distributions for the latest period.
4. **Compliance** — the live covenant monitor across reporting periods.
5. **Projection** — a single-period **stress sensitivity**: the waterfall re-run on the base-case capital structure under base vs stressed collection factors, with Class A WAL. (This is a sensitivity, not a multi-month CPR/CDR projection — the dedicated forward `cashflow_projector` is not yet wired.)

**Platform & Governance** — the reusable-framework / trust / cross-deal layer:

6. **Showcase** — the primitives × 5 deals **capability matrix** (Dutch / Italian / Spanish RMBS), each cell `validated` / `ran` / `not-applicable` with the honest reason behind it (tally **1 validated / 9 ran / 15 not-applicable**).
7. **Validation** — the seasoned-deal proof: the waterfall engine reproduced against **Green Lion 2024-1's own published Notes & Cash Priority of Payments, to the cent** (revenue 11/11, redemption 4/4; Class A interest engine-computed).
8. **Framework** — the typed primitive-registry catalogue.
9. **Governance** — the FINOS evidence pack (audit trail, confidence, citations, `finos_compliant`) plus per-tape `deeploans`-vs-`direct` data provenance.

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

> **Scope of validation.** The pipeline is data-driven, and the *unmodified*
> primitives now run end-to-end across **5 deals in 3 jurisdictions** — Dutch
> (Green Lion 2023-1 / 2024-1 / 2026-1), Italian (Leone Arancio RMBS 2023-1),
> and Spanish (Sol-Lion II RMBS). But "ran" is not "validated", and the two
> are tracked separately and honestly:
>
> - **Validated to the cent on one real deal.** The model-driven waterfall
>   engine reproduces **Green Lion 2024-1's own published Notes & Cash Priority
>   of Payments to the cent** (revenue 11/11, redemption 4/4; Class A interest
>   engine-computed from the capital structure, not the report). See the
>   **Validation** view / `engine_validation_harness.py`. Green Lion 2023-1 is
>   registered but has no Notes & Cash fixture yet, so its validation reports
>   `available=false` rather than a false pass.
> - **Extraction is honestly partial on the non-English prospectuses.** Real
>   *cited* triggers and issuer covenants extract from the Italian deal
>   (completeness ≈ 0.38, no waterfall); the Spanish deal is minimal
>   (≈ 0.30). These are not clean extractions and are not presented as such.
> - **The capability matrix is the source of truth.** `GET /capability-matrix`
>   and the **Showcase** view tally every primitive × deal cell as
>   `validated` / `ran` / `not-applicable` — currently **1 validated / 9 ran /
>   15 not-applicable** — each with a real reason. Never read this as
>   "validated across all deals": exactly one cell is validated.

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

The same primitives are also packaged as a governed **MCP server** under [`mcp/`](mcp/README.md): each `live` primitive is exposed as an MCP tool whose input is the primitive's own typed Pydantic schema, and every call returns the full `PrimitiveResult` envelope (output **plus** the governance evidence pack — confidence, citations, audit entry). A `primitives://catalogue` resource lists all 8 registered primitives (live + library-only) with honest reachability, so a third party (e.g. the waafir platform, Claude Desktop) can consume the framework without rewriting any primitive.

---

## Data

**Green Lion 2026-1** — the primary demo deal: a publicly available structured finance deal package built around a synthetic Dutch RMBS (~EUR 1bn pool). The deal reports **three monthly ESMA Annex 2 loan tapes** from `Algoritmica/green-lion-2026`: February, March, and April 2026, accompanied by the prospectus PDF (Green Lion 2026-1 B.V.) and 3 monthly investor reports. January 2026 (`202601`) is an intentional gap in the chronology.

**The full deal set — 5 deals across 3 jurisdictions.** Alongside 2026-1, the registry (`src/loanwhiz/data/deals.json`) carries four more deals the *same* primitives run on end-to-end: the seasoned Dutch deals **Green Lion 2023-1** and **2024-1** (the latter is the engine's to-the-cent validation target), the Italian **Leone Arancio RMBS 2023-1 S.r.l.**, and the Spanish **Sol-Lion II RMBS Fondo de Titulización**. Extraction completeness is honest per deal — clean on the Dutch prospectuses, **partial on the Italian (≈ 0.38, cited triggers but no waterfall) and minimal on the Spanish (≈ 0.30)**. The capability matrix (`/capability-matrix`, Showcase view) is the per-deal source of truth. See [docs/data-card.md](docs/data-card.md) and [docs/model-card.md](docs/model-card.md) for the full per-deal breakdown.

> **A note on the other Green Lion datasets.** `Algoritmica/green-lion-2024-2025` (and the real ING `green-lion-2023-1` / `green-lion-2024-1` deals) are **separate deals**, not Green Lion 2026-1's pre-history — different deals' loan tapes are not interchangeable (the 2024-2025 dataset is a ~EUR 139bn pool, ~130× this deal). They are therefore not chained into 2026-1's `tape_urls`. Validating the engine against the real seasoned deals' published Notes & Cash reports is tracked separately.

The three 2026 tapes are **synthetic period snapshots** — loan identifiers do not persist across months, so period-to-period collections and losses are derived by net reconciliation to pool movement rather than by tracking individual loans. All three 2026 reporting periods are accompanied by real investor reports.

This is the primary test and demo dataset for the hackathon submission. All loan-level data is synthetic and was released by Algoritmica.ai specifically for this hackathon. See [docs/data-card.md](docs/data-card.md) for the full data card, including the synthetic-vs-real breakdown.

Tape ingestion is format-agnostic: loan tapes may be supplied as **CSV or parquet**, including a single combined multi-month parquet from which the loader selects a reporting period. Green Lion ships as per-period CSV tapes.

---

## Data provenance & governance

LoanWhiz is built on two open frameworks that together carry the **trust story**: [deeploans](https://github.com/Algoritmica-ai/deeploans) (Algoritmica's open-source ESMA loan-level ETL) for data, and the [FINOS AI Governance Framework](https://github.com/finos/ai-governance-framework) for auditability.

**Honest data provenance — deeploans is on the live path.** ESMA tape ingestion is routed through the deeploans backend when one is available, with a clean fallback to a direct-URL pandas read otherwise:

- A tape referenced as `deeploans://{asset_class}/{table_name}` (e.g. `deeploans://sme/loans`) is fetched through the running deeploans ETL backend (`loanwhiz.data.deeploans_client.DeepLoansClient.fetch_tape`). This is a genuinely reachable ingestion path, not a decorative dependency.
- Any other tape URL (the Green Lion HuggingFace CSVs, a local `file://` path) takes the direct pandas path. The demo runs with **or without** a deeploans backend — the 10 June demo environment has none, and the direct path keeps everything working.

Either way, the `esma_tape_normaliser` records which path produced the data as a `data_source` field (`"deeploans"` or `"direct"`) on its output. That provenance flows through `/deal/{id}/tape-analytics` and the agent's `load_esma_tape` tool into the governance evidence pack — so the trust story extends all the way down to where the data came from.

**Governance surface.** Every governed agent query emits one FINOS-aligned evidence pack (audit trail, conservative aggregate confidence, deduplicated citations, human-review flag, and a real `finos_compliant` consistency check). The demo UI's **Governance** view (and the chat panel's evidence slide-over) surface this per answer, including the per-tape `deeploans`-vs-`direct` provenance. See [docs/governance.md](docs/governance.md).

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
