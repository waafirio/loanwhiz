# Quickstart

This guide walks through setting up LoanWhiz from scratch, verifying your environment, and running the Green Lion demo end-to-end.

---

## Prerequisites

### Python 3.12

LoanWhiz requires Python 3.12. Check your version:

```bash
python3 --version
```

If you need to install 3.12, the recommended approach is via [pyenv](https://github.com/pyenv/pyenv):

```bash
pyenv install 3.12
pyenv local 3.12
```

### GCP project with Vertex AI enabled

You need a Google Cloud project with the Vertex AI API enabled. LoanWhiz uses:

- **Gemini 2.5 Flash** (`gemini-2.5-flash`) for orchestration and planning (1M token context window, fast)
- **Gemini 2.5 Pro** (`gemini-2.5-pro`) for prospectus extraction (highest quality structured output)

If you do not have a GCP project, create one at [console.cloud.google.com](https://console.cloud.google.com/). GCP free-tier credits and hackathon credits both work.

Enable the Vertex AI API:

```bash
gcloud services enable aiplatform.googleapis.com
```

### Google Cloud CLI and ADC

Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) if you do not have it. Then configure Application Default Credentials:

```bash
gcloud auth application-default login
```

This opens a browser window for OAuth. The credentials are cached locally and used automatically by the `google-genai` SDK.

Set your active project:

```bash
gcloud config set project <your-project-id>
```

---

## Installation

Clone the repo and install in editable mode:

```bash
git clone https://github.com/waafirio/loanwhiz.git
cd loanwhiz
pip install -e .
```

Editable mode (`-e`) means changes to `src/loanwhiz/` are immediately visible without reinstalling. All runtime dependencies (LangGraph, google-genai, Docling, pandas, FastAPI, etc.) are declared in `pyproject.toml` and installed automatically.

To verify the package is importable:

```bash
python3 -c "import loanwhiz; print(loanwhiz.__version__)"
```

Expected output: `0.1.0`

---

## Run the Smoke Test

The smoke test sends a minimal request to Vertex AI / Gemini 2.5 Flash to confirm your credentials and project are correctly configured:

```bash
pytest tests/test_smoke.py -v
```

Expected output:

```
tests/test_smoke.py::test_gemini_reachable PASSED
```

If the test fails, check:

1. `GCP_PROJECT` in `src/loanwhiz/config.py` matches the project ID you enabled Vertex AI on.
2. `gcloud auth application-default login` has been run and credentials are not expired.
3. The Vertex AI API is enabled: `gcloud services list --enabled | grep aiplatform`

---

## Configure Your Project ID

Open `src/loanwhiz/config.py` and set your GCP project ID:

```python
GCP_PROJECT = "<your-project-id>"
GCP_LOCATION = "us-central1"       # Vertex AI region; us-central1 is recommended
```

The `GCP_LOCATION` should be a region where Gemini 2.5 Flash and Pro are available. `us-central1` is the broadest support region as of June 2026.

---

## Run Against the Green Lion Demo

The Green Lion 2026-1 deal is a publicly available Dutch synthetic RMBS package (~EUR 1bn pool). It reports **three monthly ESMA Annex 2 loan tapes** — February, March, and April 2026 (`Algoritmica/green-lion-2026`) — accompanied by the prospectus PDF and three monthly investor reports. January 2026 is an intentional gap. (The separate `Algoritmica/green-lion-2024-2025` dataset is a *different* deal, not this deal's history, and is not loaded here.) It is the primary demo dataset for this hackathon.

Run the end-to-end demo:

```bash
python demo/run_green_lion.py
```

What this does, in order:

1. **Fetches the deal package** from HuggingFace (`Algoritmica/green-lion-2026` — the prospectus, 3 monthly tapes, and 3 investor reports). Documents are downloaded on first run and cached locally under `data/deals/green-lion-2026-1/`.

2. **Extracts the deal model** using Docling + Gemini 2.5 Pro. The prospectus PDF is parsed into structured sections, and key deal components are extracted:
   - Revenue Priority of Payments (waterfall, 11 ordered steps from section 5.2)
   - Defined terms and cross-references
   - Trigger thresholds and covenant definitions
   - Tranche structure and note conditions

   The extracted deal model is cached as `data/deals/green-lion-2026-1/deal_model.json`. Subsequent runs skip extraction.

3. **Loads the ESMA tapes** — the deal's three 2026 monthly tapes (February, March, April) via the `esma_tape_normaliser` primitive, which reads each tape as **CSV or parquet** (suffix-detected, including a combined multi-month parquet sliced by `reporting_date`). Computes pool analytics per period: WAL, arrears breakdown by bucket, EPC distribution, geographic distribution, rate type distribution.

4. **Runs the waterfall** using the `waterfall_runner` primitive against each month's tape collections. Outputs computed distributions per tranche per period with a full audit trace.

5. **Reconciles against the investor reports.** Collateral (pool balance, collections, arrears) reconciles to the published monthly investor reports to the cent; liabilities (tranche/PDL/reserve) are reconstructed from the prospectus and invariant-validated, since 2026-1 has no in-window note-level actuals report (see [docs/data-card.md](data-card.md)).

6. **Monitors covenants** using the `covenant_monitor` primitive. Checks each extracted trigger threshold (Class A/B PDL, reserve-fund shortfall) per period and reports proximity to breach.

7. **Runs a forward stress sensitivity.** The waterfall is re-run on the base-case capital structure under base vs stressed collection factors (a single-period sensitivity, exposed via `POST /deal/{id}/project`). This is a stress sensitivity, not a multi-month CPR/CDR projection — the dedicated `cashflow_projector` is implemented as a library primitive but not yet wired into the route.

8. **Prints a structured summary** of each stage to stdout, with citations back to the prospectus for extracted values.

---

## Running Against a New Deal

The framework is **data-driven by design** — adding a deal is *data*, not code (the waterfall interpreter executes each deal's extracted model rather than hardcoded logic). Drop a `src/loanwhiz/data/deals.json` file next to `config.py`; it is a JSON object mapping each `deal_id` to a deal-context dict and is merged over the in-code Green Lion default at import time (no Python edit required). End-to-end validation so far covers exactly one deal — Green Lion 2026-1; multi-deal validation is in progress (epic #206):

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

A missing or malformed `deals.json` is tolerated — the Green Lion default still loads. A deal may also carry optional keys that the API resolves from the deal (falling back to Green Lion defaults when absent): `capital_structure` (Class A/B/C balances + Class A rate, used by `GET /deal/{id}/waterfall`), `original_pool_balance` (the closing-balance denominator used by `GET /deal/{id}/compliance`), and `projection_base` (the base case for `GET /deal/{id}/project`). Tape URLs may point at CSV or parquet. See the README's "How to Run Against a New Deal" for the full deal-context shape.

All three document types are optional for a partial run — for example, you can extract the deal model from the prospectus alone without supplying tape or investor report URLs. The framework will skip primitives that require missing inputs and log which steps were skipped.

---

## Running the REST API

Start the FastAPI service:

```bash
uvicorn loanwhiz.api.main:app --reload
```

Endpoints:

- `POST /query` — natural language query against a loaded deal (returns a governance evidence pack)
- `GET /deal/{id}/model` — retrieve the extracted deal model JSON
- `GET /deal/{id}/compliance` — covenant monitor results across periods
- `POST /deal/{id}/project` — single-period waterfall stress sensitivity under base/stress scenarios
- `GET /primitives` — the primitive catalogue with per-primitive reachability (`live` / `library-only`)
- `GET /capability-matrix` — the primitives × 5 deals capability matrix: each cell `validated` / `ran` / `not-applicable` with a real reason, plus the tally (**1 validated / 9 ran / 15 not-applicable**)
- `GET /deal/{id}/validation` — the engine-validation report for a deal; returns `available=false` with an honest note for a deal without a Notes & Cash fixture (e.g. Green Lion 2023-1), `available=true` with the to-the-cent reconciliation for Green Lion 2024-1

See `src/loanwhiz/api/README.md` for the full endpoint reference and curl examples.

### Demo UI views

The Next.js dashboard (`./scripts/run-demo-v2.sh`, UI on :3000) groups its views into two sidebar sections (`NAV_GROUPS` in `web/lib/nav.ts`):

- **Deal Analytics** — Overview, Pool & Performance, Waterfall, Compliance, Projection (the per-deal analyst views, one loaded deal at a time).
- **Platform & Governance** — Showcase (the primitives × 5 deals capability matrix across Dutch / Italian / Spanish RMBS), Validation (the Green Lion 2024-1 engine-vs-Notes-&-Cash proof, to the cent), Framework (the primitive-registry catalogue), and Governance (the FINOS evidence pack + `deeploans`-vs-`direct` data provenance).

The capability matrix is the honest source of truth for what is validated vs ran vs not-applicable across the deal set — never read the cross-jurisdiction coverage as "validated everywhere".

### MCP server

The 8 primitives are also consumable as a governed MCP server (`mcp/`) — each `live` primitive becomes an MCP tool that returns the full `PrimitiveResult` evidence pack. See [mcp/README.md](../mcp/README.md) for wiring it into an MCP client.

---

## Next Steps

- Read [Contributing a New Primitive](../README.md#how-to-contribute-a-new-primitive) in the README.
- Explore the primitives in `src/loanwhiz/primitives/`.
- Check the `tests/` directory for usage examples of each primitive.
- File issues or PRs at [github.com/waafirio/loanwhiz](https://github.com/waafirio/loanwhiz).
