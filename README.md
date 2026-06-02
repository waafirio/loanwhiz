# LoanWhiz

Structured finance agent framework — SF-native primitives, deal model extraction, waterfall execution, and LangGraph orchestration. Built for the Barcelona AI Tinkerers Structured Finance Hackathon 2026 (demo day: 10 June).

---

## Architecture

```
CLIENTS
  Chat / Q&A  |  Dashboard  |  Compliance  |  REST API
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
      HuggingFace: Algoritmica/green-lion-2026
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

### Run against the Green Lion demo

> **Note:** `demo/run_green_lion.py` is in progress and not yet runnable. The primitives it depends on are being implemented; see the Primitives table below. This section documents the intended invocation once the demo is complete.

```bash
python demo/run_green_lion.py
```

This will load the Green Lion 2026-1 deal package (prospectus, three monthly ESMA tapes, three monthly investor reports), run the full extraction and execution pipeline, and print a structured summary.

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

Deal configuration lives in `src/loanwhiz/config.py`. Each deal is a Python dict with three keys:

```python
deal = {
    "deal_name": "My Deal 2026-1",
    "prospectus_url": "https://...",          # URL to the prospectus PDF
    "tape_urls": [
        {"date": "2026-02-28", "url": "https://..."},   # ESMA Annex 2 CSV per period
        {"date": "2026-03-31", "url": "https://..."},
    ],
    "investor_report_urls": [
        {"period": "February 2026", "url": "https://..."},  # investor report PDF per period
        {"period": "March 2026",    "url": "https://..."},
    ],
}
```

Pass this dict to the agent service or the extraction pipeline directly. The framework fetches the documents, runs Docling extraction to build the deal model JSON, and caches the result locally so extraction runs only once per deal.

See `GREEN_LION` in `config.py` for a fully worked example using the publicly available Green Lion 2026-1 dataset.

---

## How to Contribute a New Primitive

1. **Subclass `Primitive`** from `src/loanwhiz/primitives/base.py` (in progress — tracked in issue #4):
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

5. Look at existing primitives in `src/loanwhiz/primitives/` as worked examples once they are implemented.

---

## Primitives

| Name | Description | Version | Status |
|------|-------------|---------|--------|
| `esma_tape_normaliser` | Normalises ESMA Annex 2–8 loan tapes; computes pool analytics (WAL, arrears breakdown, EPC/geo/rate distributions) | 0.1.0 | In progress |
| `waterfall_runner` | Executes the extracted waterfall against monthly tape collections; returns computed distributions per tranche with full audit trace | 0.1.0 | In progress |
| `covenant_monitor` | Checks tape metrics against extracted trigger thresholds; tracks breach proximity over time | 0.1.0 | In progress |
| `report_verifier` | Compares waterfall-computed distributions against investor report actuals; flags discrepancies | 0.1.0 | In progress |
| `cashflow_projector` | Projects forward cashflows under base and stress scenarios using the waterfall runner | 0.1.0 | In progress |
| `audit_logger` | Wraps every primitive call with provenance: input hash, output, confidence score, citations, timestamp, model version, human review flag | 0.1.0 | In progress |
| `collections_aggregator` | Aggregates monthly collections (interest, principal, prepayments, recoveries) from ESMA tapes into waterfall-ready inputs | 0.1.0 | In progress |

---

## Data

**Green Lion 2026-1** (`Algoritmica/green-lion-2026` on HuggingFace)

A complete, publicly available structured finance deal package built around a synthetic Dutch RMBS:

- 3 monthly ESMA Annex 2 loan tapes (February, March, April 2026)
- Prospectus PDF (Green Lion 2026-1 B.V.)
- 3 monthly investor reports (February, March, April 2026)

This is the primary test and demo dataset for the hackathon submission. All data is synthetic and was released by Algoritmica.ai specifically for this hackathon.

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
