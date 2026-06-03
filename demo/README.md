# LoanWhiz Demo — Green Lion 2026-1 B.V.

End-to-end demo script for the Green Lion 2026-1 B.V. RMBS deal. Every section
runs live against the merged LoanWhiz primitives — no stubs.

## Run

### Full mode (everything live)

```bash
python demo/run_green_lion.py
```

Runs all 8 sections live, including the slow paths:

- **Section 3** runs the full Docling + Gemini prospectus extraction
  (`extract_deal_model`). Results are cached to `/tmp/loanwhiz_cache/deals/`, so
  once the cache is warm subsequent runs load from disk instantly. The cold
  run is dominated by Docling's PDF parse + OCR: ~1–2 minutes on a GPU, but
  several minutes on CPU-only machines (RapidOCR on CPU is the bottleneck).
- **Section 5** calls Gemini 2.5 Flash to extract figures from the April
  investor-report PDF and diffs them against the computed waterfall.

Expect the full run to take a few minutes on a cold cache (mostly Docling).
For a quick end-to-end demo, use `--fast` below.

### Fast mode (for a live demo, ~20–30s)

```bash
python demo/run_green_lion.py --fast
# (--skip-extraction is an accepted alias)
```

Fast mode still runs every section, but skips the two slow Gemini/Docling
paths so the whole demo completes in well under 30 seconds:

- **Section 3** loads a cached deal model if one is present, otherwise prints
  the known prospectus section map instead of running Docling + Gemini.
- **Section 5** skips the investor-report Gemini extraction and prints an
  explanatory line; the computed waterfall from Section 4 is unaffected.

Sections 2, 4, 6, 7, and the Section 8 Q&A still run live in fast mode (the
waterfall, covenant, and projection primitives are pure computation, and the
ESMA tape fetch is fast).

## Prerequisites

```bash
pip install -e .
```

The script requires:
- Network access to `huggingface.co` (ESMA loan tapes, ~3 × 6 MB CSVs;
  prospectus + investor-report PDFs in full mode)
- Vertex AI credentials for Gemini 2.5 Flash / Pro
  - Set `GOOGLE_APPLICATION_CREDENTIALS` or run inside GCP with a service account
  - Set `GOOGLE_CLOUD_PROJECT=loanwhiz` if it is not already in your environment
  - The GCP project and region are configured in `src/loanwhiz/config.py`
  - The script sets `GOOGLE_GENAI_USE_VERTEXAI=true` (plus project/location) by
    default so the in-process `genai` clients route to Vertex AI. To use the
    Gemini Developer API instead, export `GEMINI_API_KEY` and
    `GOOGLE_GENAI_USE_VERTEXAI=false` before running — the script never
    overrides an env var you have already set.

If Vertex AI is unavailable the script degrades gracefully: extraction
(Section 3) and report verification (Section 5) print an explanatory line, the
Section 8 Q&A prints a data-derived fallback answer, and the run completes.

## What the demo shows

| Section | What runs |
|---------|-----------|
| 1. Deal context | Static deal metadata from config |
| 2. ESMA tape analytics | Live HuggingFace fetch; real pool metrics for Feb/Mar/Apr 2026 |
| 3. Prospectus extraction | `extract_deal_model` (Docling + Gemini); tranches, waterfall step count, triggers, completeness. Cached/skipped in `--fast` |
| 4. Waterfall execution | `CollectionsAggregator` → `WaterfallRunner`; 11 revenue steps + per-tranche distributions |
| 5. Investor report verification | `ReportVerifier` diffs the computed waterfall vs the April investor report (Gemini). Skipped in `--fast` |
| 6. Covenant monitor | `EsmaTapeNormaliser` ×3 → `CovenantMonitor`; per-period trigger status (🟢/🟡/🔴) |
| 7. Cashflow projection | `CashflowProjector`; base vs stress 12-month projection + Class A WAL |
| 8. Natural language Q&A | Live Gemini 2.5 Flash call using tape data as context |
