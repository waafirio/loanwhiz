# LoanWhiz Demo-Day Runbook

Operator runbook for the unified LoanWhiz demo app (`clients/demo/`). Follow it
top to bottom on demo day. The single most important step is the **cache
pre-warm** — do it well before you go live.

---

## 1. Prerequisites

Confirm all of these **before** the demo, on the machine you will present from:

- **GCP Application Default Credentials active** — the agent and report verifier
  call Vertex AI / Gemini under your ADC:
  ```bash
  gcloud auth application-default login
  ```
- **Project set** — the deal config defaults to project `loanwhiz`:
  ```bash
  export GOOGLE_CLOUD_PROJECT=loanwhiz
  ```
  (`demo/run_green_lion.py` also `setdefault`s this from `config.GCP_PROJECT`,
  but set it explicitly so every entrypoint agrees.)
- **Package installed (editable)** from the repo root:
  ```bash
  pip install -e .
  ```
- **Network reachable** to HuggingFace (`Algoritmica/green-lion-2026`) for the
  ESMA tapes and prospectus, and to Vertex AI for Gemini.

---

## 2. CRITICAL — Pre-warm the extraction cache

The **Deal Overview** tab shows the extracted deal model (tranche structure,
definitions, waterfall, covenants). That model comes from a Docling + Gemini
extraction of the prospectus PDF, which is **>10 minutes on CPU**. The app is
deliberately **cache-aware and never triggers a cold extraction** — on a cache
miss the Deal Overview tab degrades to "no cached deal model" instead of hanging.

So you MUST pre-warm the cache before the demo, or the headline tab is empty.

**Cache location:** `/tmp/loanwhiz_cache/deals/` — the deal model is written to
`/tmp/loanwhiz_cache/deals/<slug(deal_name)>.json` (Green Lion →
`green-lion-2026-1-bv.json`). This is also where the live app and the CLI read
from. Note: `/tmp` is cleared on reboot — re-warm after any restart.

Run **either** of the following once (each populates the same cache):

**Option A — extract Green Lion directly (recommended, smallest scope):**

```bash
GOOGLE_CLOUD_PROJECT=loanwhiz PYTHONPATH=src python3 -c "
from loanwhiz.config import GREEN_LION
from loanwhiz.extraction.assembler import extract_deal_model
extract_deal_model(
    prospectus_url=GREEN_LION['prospectus_url'],
    deal_name=GREEN_LION['deal_name'],
)
print('deal model cached')
"
```

**Option B — run the full CLI demo once (also pre-warms, plus smoke-tests the
whole pipeline):**

```bash
GOOGLE_CLOUD_PROJECT=loanwhiz python demo/run_green_lion.py
```

(Run it **without** `--fast` — full mode performs the live extraction and writes
the cache. `--fast` *skips* extraction and only works if the cache already
exists.)

**Verify the cache is warm before you go live:**

```bash
ls -lh /tmp/loanwhiz_cache/deals/
```

You should see `green-lion-2026-1-bv.json`. After this, `DealState.load_green_lion`
reads it instantly and the Deal Overview tab is fully populated.

---

## 3. Launch

```bash
GOOGLE_CLOUD_PROJECT=loanwhiz python clients/demo/app.py
```

Open **http://localhost:7860**, then click **📂 Load Green Lion 2026-1 Deal**.
The status line should read "Loaded Green Lion 2026-1 B.V. (3 tape period(s);
deal model from cache)." If it says *no cached deal model*, stop and re-run the
pre-warm (section 2) — the tapes load live but the deal model only comes from the
cache.

---

## 4. The 5-tab narrative walkthrough (click-by-click)

Tabs are ordered as the demo's narrative arc; present them left to right. The
deal is loaded once and shared across every tab.

1. **Deal Overview** — open with the extracted deal model. Show that the tranche
   structure, definitions, waterfall, and covenants were pulled straight from
   the prospectus PDF (this is what the pre-warm produced).
2. **Pool & Performance** — show the **3-period pool trends** (Feb / Mar / Apr
   2026) and the **EPC** distribution alongside geo / rate breakdowns. The story:
   real ESMA tapes, normalised live.
3. **Waterfall** — **run the waterfall live** and walk the audience down the
   distribution **cascade**, tranche by tranche, from the extracted structure.
4. **Compliance & Covenants** — show **report verification** (computed vs
   investor-report actuals), the **covenant grid** (breach / near-miss / OK), and
   the **actual-vs-projected-stress chart**.
5. **Cashflow Projection** — show **base vs stress** projections and the
   resulting **WAL**.

**At any point:** use the **docked chat panel** (right of every tab) for ad-hoc
Q&A — e.g. "What happens to Class B if defaults double?" — grounded in the loaded
deal model and tapes. It stays visible no matter which tab is active.

---

## 5. Fallbacks

- **Vertex / Gemini is slow** → the **report verification** on the Compliance tab
  degrades gracefully: when the verifier can't reach Vertex it returns an
  "unavailable" note instead of blocking. The covenant grid and the rest of the
  tab still render from tape data, so keep narrating; don't wait on it.
- **A HuggingFace fetch is slow** (tapes / prospectus) → the first load fetches
  over the network; **tapes cache after the first load**, so a slow first click
  is a one-time cost. If a tape load fails, the status line shows a degraded note
  and the other tabs still work from whatever loaded.
- **The UI misbehaves entirely** → fall back to the **CLI demo** as a backup:
  ```bash
  GOOGLE_CLOUD_PROJECT=loanwhiz python demo/run_green_lion.py --fast
  ```
  `--fast` (~20–30s) skips the live extraction and report-verifier Gemini calls
  and runs off the pre-warmed cache, printing a structured end-to-end summary —
  enough to tell the whole story without the UI.
