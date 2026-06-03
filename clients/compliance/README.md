# LoanWhiz Compliance View — Green Lion 2026-1

A standalone Gradio compliance report for the Green Lion 2026-1 Dutch RMBS deal,
calling LoanWhiz primitives directly (no REST API dependency). It surfaces two
compliance questions in tabular form: *"is the deal within its covenant
triggers?"* and *"did the servicer apply the waterfall correctly?"*

## What it shows

| Tab | Content |
|-----|---------|
| Covenant Compliance Over Time | `CovenantMonitor` run across all three reporting periods (Feb/Mar/Apr 2026). One row per trigger per period: metric value, threshold, proximity-to-breach %, and 🔴 BREACH / 🟡 near-miss / 🟢 OK status. |
| Report Verification | `ReportVerifier` comparing the April waterfall-computed distributions against the investor report actuals. One row per line item: computed value, reported value, delta, delta %, and 🟢 MATCH / 🔴 MISMATCH status. |

## Running the view

### Prerequisites

```bash
# Install compliance-view dependencies (from this directory):
pip install -r requirements.txt

# Install loanwhiz from the repo root:
pip install -e ../../
```

### Launch

```bash
python app.py
```

The view starts at [http://localhost:7862](http://localhost:7862).

- Click **Run Covenant Check (Feb–Apr 2026)** to fetch the three Green Lion
  loan tapes from HuggingFace (`Algoritmica/green-lion-2026`) and evaluate the
  covenant triggers. Loading takes ~30 seconds on first run due to network I/O.
- Click **Verify April Investor Report** to run the report verifier.

### Report verification requires Vertex AI

The report verifier extracts figures from the investor report PDF using Gemini
2.5 Flash, which needs Google Cloud / Vertex AI credentials. When those are not
available (e.g. CI or a laptop without `gcloud` auth), the Report Verification
tab degrades gracefully: it shows a single explanatory row noting that Vertex
AI access is required, rather than failing. The Covenant tab is fully
functional without any Gemini access.

## Architecture

```
app.py
  ├── run_covenant_compliance()  → EsmaTapeNormaliser × 3 → CovenantMonitor
  │     └── build_covenant_rows()  (pure formatting; 6-column rows)
  ├── run_report_verification()  → WaterfallRunner → ReportVerifier (graceful)
  │     └── build_report_rows()    (pure formatting; 6-column rows)
  └── create_compliance_view()   → gr.Blocks with two tabs
```

Each button binds a single callable returning a `(rows, summary)` tuple, so the
underlying primitive runs exactly once per click. All tape data is fetched live
from HuggingFace; the view is stateless.

## Deal context

- **Issuer:** Green Lion 2026-1 B.V.
- **Originator / Servicer:** ING Bank N.V.
- **Asset class:** Dutch prime residential mortgages (RMBS)
- **Data source:** [Algoritmica/green-lion-2026](https://huggingface.co/datasets/Algoritmica/green-lion-2026) (Apache 2.0)
