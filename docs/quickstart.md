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

The Green Lion 2026-1 deal is a complete, publicly available Dutch synthetic RMBS package. It spans **27 months of ESMA Annex 2 loan tapes** — 24 monthly historical tapes for January 2024 through December 2025 (`Algoritmica/green-lion-2024-2025`) plus 3 for February, March, and April 2026 (`Algoritmica/green-lion-2026`), the latter accompanied by the prospectus PDF and three monthly investor reports. January 2026 is an intentional gap. It is the primary demo dataset for this hackathon.

> **Note:** `demo/run_green_lion.py` is in progress and not yet runnable — the primitives it orchestrates are still being implemented. This section documents the intended behaviour of the demo once complete.

Run the end-to-end demo:

```bash
python demo/run_green_lion.py
```

What this does, in order:

1. **Fetches the deal package** from HuggingFace (`Algoritmica/green-lion-2026` for the 2026 deal documents and `Algoritmica/green-lion-2024-2025` for the 24-month historical tapes). Documents are downloaded on first run and cached locally under `data/deals/green-lion-2026-1/`.

2. **Extracts the deal model** using Docling + Gemini 2.5 Pro. The prospectus PDF is parsed into structured sections, and key deal components are extracted:
   - Revenue Priority of Payments (waterfall, 11 ordered steps from section 5.2)
   - Defined terms and cross-references
   - Trigger thresholds and covenant definitions
   - Tranche structure and note conditions

   The extracted deal model is cached as `data/deals/green-lion-2026-1/deal_model.json`. Subsequent runs skip extraction.

3. **Loads the ESMA tapes** — the full 27-month chronology (24 monthly tapes for 2024–2025 plus February, March, and April 2026) via the `esma_tape_normaliser` primitive, which reads each tape as **CSV or parquet** (suffix-detected, including a combined multi-month parquet sliced by `reporting_date`). Computes pool analytics per period: WAL, arrears breakdown by bucket, EPC distribution, geographic distribution, rate type distribution.

4. **Runs the waterfall** using the `waterfall_runner` primitive against each month's tape collections. Outputs computed distributions per tranche per period with a full audit trace.

5. **Verifies against investor reports** using the `report_verifier` primitive. Compares computed distributions to the reported figures from each monthly investor report. Flags line items where computed and reported values diverge.

6. **Monitors covenants** using the `covenant_monitor` primitive. Checks each trigger threshold (e.g. cumulative loss rate, PDL balance) per period and reports proximity to breach.

7. **Projects forward cashflows** using the `cashflow_projector` primitive under base and stress (2x default rate) scenarios for 12 months.

8. **Prints a structured summary** of each stage to stdout, with citations back to the prospectus for extracted values.

---

## Running Against a New Deal

The framework is **data-agnostic by design** — adding a deal is *data*, not code. Drop a `src/loanwhiz/data/deals.json` file next to `config.py`; it is a JSON object mapping each `deal_id` to a deal-context dict and is merged over the in-code Green Lion default at import time (no Python edit required):

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

Start the FastAPI service (in progress — tracked in issue #22):

```bash
uvicorn loanwhiz.api:app --reload
```

Endpoints (planned):

- `POST /query` — natural language query against a loaded deal
- `GET /deal/{id}/model` — retrieve the extracted deal model JSON
- `GET /deal/{id}/compliance` — covenant monitor results
- `POST /deal/{id}/project` — cashflow projection under a scenario

---

## Next Steps

- Read [Contributing a New Primitive](../README.md#how-to-contribute-a-new-primitive) in the README.
- Explore the primitives in `src/loanwhiz/primitives/` once they are implemented.
- Check the `tests/` directory for usage examples of each primitive.
- File issues or PRs at [github.com/waafirio/loanwhiz](https://github.com/waafirio/loanwhiz).
