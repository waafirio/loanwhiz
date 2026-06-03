# LoanWhiz Dashboard — Green Lion 2026-1

A standalone Gradio dashboard that visualises the Green Lion 2026-1 Dutch RMBS deal, calling LoanWhiz primitives directly (no REST API dependency).

## What it shows

| Tab | Content |
|-----|---------|
| Pool Performance | Period-over-period pool metrics: balance, loan count, arrears, default rate, WTD LTV — Feb to Apr 2026 |
| EPC Distribution | Energy Performance Certificate breakdown by balance (latest period) |
| Covenant Monitor | Green/amber/red status per trigger per period with proximity-to-breach % |

## Running the dashboard

### Prerequisites

```bash
# Install dashboard dependencies (from this directory):
pip install -r requirements.txt

# Install loanwhiz from the repo root:
pip install -e ../../
```

### Launch

```bash
python app.py
```

The dashboard starts at [http://localhost:7861](http://localhost:7861).

Click **Load / Refresh Deal Data** to fetch the three Green Lion loan tapes from HuggingFace (`Algoritmica/green-lion-2026`). Data loading takes ~30 seconds on first run due to network I/O.

## Architecture

```
app.py
  ├── load_all_tapes()          → EsmaTapeNormaliser × 3 (Feb/Mar/Apr 2026)
  ├── build_pool_trend_table()  → tabular pool metrics per period
  ├── build_epc_table()         → EPC breakdown for the latest period
  └── build_covenant_table()    → CovenantMonitor over all 3 periods
```

All data is fetched live from HuggingFace on each "Refresh" click. No local cache is used; the dashboard is stateless.

## Deal context

- **Issuer:** Green Lion 2026-1 B.V.
- **Originator / Servicer:** ING Bank N.V.
- **Asset class:** Dutch prime residential mortgages (RMBS)
- **Data source:** [Algoritmica/green-lion-2026](https://huggingface.co/datasets/Algoritmica/green-lion-2026) (Apache 2.0)
