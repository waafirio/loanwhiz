# LoanWhiz Demo — Green Lion 2026-1 B.V.

End-to-end demo script for the Green Lion 2026-1 B.V. RMBS deal.

## Run

```bash
python demo/run_green_lion.py
```

No arguments required. The script fetches all data from HuggingFace and calls
Vertex AI in-process.

## Prerequisites

```bash
pip install -e .
```

The script requires:
- Network access to `huggingface.co` (ESMA loan tapes, ~3 × 6 MB CSVs)
- Vertex AI credentials for Gemini 2.5 Flash (Section 8 Q&A)
  - Set `GOOGLE_APPLICATION_CREDENTIALS` or run inside GCP with a service account
  - The GCP project and region are configured in `src/loanwhiz/config.py`

If Vertex AI is unavailable the script degrades gracefully: Section 8 prints
a data-derived fallback answer and continues to completion.

## What the demo shows

| Section | What runs |
|---------|-----------|
| 1. Deal context | Static deal metadata from config |
| 2. ESMA tape analytics | Live HuggingFace fetch; real pool metrics for Feb/Mar/Apr 2026 |
| 3. Prospectus extraction | Stub — Docling pipeline status + section map |
| 4. Waterfall execution | Stub — 11 Revenue Priority of Payments steps |
| 5. Investor report verification | Stub — what the verifier checks |
| 6. Covenant monitor | Stub — trigger thresholds from prospectus |
| 7. Cashflow projection | Stub — base / stress / fast-prepay scenarios |
| 8. Natural language Q&A | Live Gemini 2.5 Flash call using tape data as context |

Sections 3–7 will become fully live once the parallel primitive issues
(#7, #8, #9, #13, #14, #15, #16) land on `liz/epic/28`.
