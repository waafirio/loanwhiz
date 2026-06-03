# Demo UI — tab-plugin contract

This document is the contract the five **tab children** of epic
[#75](https://github.com/waafirio/loanwhiz/issues/75) build against. The shell
([`clients/demo/shell.py`](./shell.py)) defines the shared deal state, the tab
registry, and where chat lives. Read this before building a tab — it tells you
exactly how to plug in.

> **TL;DR for a tab worker:** add `clients/demo/tabs/<your_module>.py` with a
> single `def render(state): ...` function; swap your `TAB_REGISTRY` entry's
> `render` in `shell.py` to import it. Read the loaded deal from `state` (a
> `gr.State` whose value is a `DealState`). Do **not** re-load tapes — they're
> already loaded and shared. Do not reorder the tabs.

---

## 1. The shared deal state — `DealState`

The shell loads the deal **once** and shares it with every tab through a
per-session `gr.State` holding a `DealState` (a pydantic model in `shell.py`).
**Load once, share** — a tab must never re-fetch tapes; it reads them from the
shared state.

```python
class DealState(BaseModel):
    deal_name: str               # human deal name, e.g. "Green Lion 2026-1 B.V."
    tapes: list[dict]            # one normalised ESMA tape per reporting period
    deal_model: DealModel | None # cached extracted deal model, or None
    loaded: bool                 # True once tapes are available
    load_error: str | None       # human-readable note if a load degraded
```

### `tapes` shape

Each element is `EsmaTapeOutput.model_dump()` **plus** a `"period"` label key,
in chronological order. This is the same dict shape the standalone dashboard
and chat clients already consume, so existing formatting helpers transfer
directly. Useful keys per tape:

| Key | Meaning |
|---|---|
| `period` | reporting-period label, e.g. `"2026-02-28"` |
| `pool_balance_eur` | total current balance (float) |
| `loan_count` | number of loans (int) |
| `pool_stats` | balance-weighted averages: `wtd_coupon_pct`, `wtd_ltv`, `wtd_seasoning`, `wtd_remaining_term` |
| `arrears_breakdown` | `current_pct`, `arrears_1_2m_pct`, `arrears_180d_plus_pct`, `default_pct` |
| `epc_breakdown` | `{label: pct}` or `None` |
| `rate_type_breakdown`, `property_type_breakdown`, `geographic_breakdown` | `{label: pct}` or `None` |
| `annex_detected` | e.g. `"Annex 2 (RMBS)"` |

### `deal_model` shape

A `loanwhiz.extraction.assembler.DealModel` when the pre-warmed extraction
cache is present, else `None`. It carries `metadata`, `definitions`,
`waterfalls` (revenue / redemption / post_enforcement), `covenants`,
`tranche_structure`, and `trigger_names`. **Always handle `None`** — see §4.

---

## 2. The `render(state)` convention

Each tab module exposes exactly one function:

```python
import gradio as gr

def render(state: gr.State) -> None:
    """Populate this tab. Called inside an open gr.Tab context."""
    info = gr.Markdown()

    def _show(s):                 # s is a DealState (state.value)
        if not s.loaded:
            return "Load a deal first."
        return f"{s.deal_name}: {len(s.tapes)} periods"

    # Read shared state by wiring it as an INPUT to your handlers.
    btn = gr.Button("Show")
    btn.click(_show, inputs=state, outputs=info)
```

Rules:

- `render` is called by the shell **inside** your tab's Gradio context, so any
  components it creates land in your tab. You do not create the `gr.Tab`
  yourself — the shell does.
- `state` is the shared session `gr.State`; its `.value` is a `DealState`.
  Wire it as an **input** to event handlers to read the loaded deal.
- **Do not mutate `state` in place.** If your tab needs to update shared state,
  return a new `DealState` from a handler and list `state` in the handler's
  `outputs`. Most tabs are read-only and never need this.
- `render` returns `None`; it builds UI as a side effect (standard Blocks
  idiom).
- Compute your tab's view from `state` (call primitives in-process if you need
  derived analytics, e.g. `WaterfallRunner`, `CovenantMonitor`); the shell only
  guarantees raw tapes + the optional cached deal model.

---

## 3. Registering your tab

1. Add `clients/demo/tabs/<your_module>.py` with a `render(state)` function.
2. In `shell.py`, replace your entry's stub `render` in `TAB_REGISTRY` with an
   import of your function:

   ```python
   from clients.demo.tabs.deal_overview import render as deal_overview_render
   ...
   TAB_REGISTRY = [
       TabSpec(title="Deal Overview", render=deal_overview_render),
       ...  # other tabs unchanged
   ]
   ```

The registry **order is the demo's narrative arc and is load-bearing** — do
not reorder it:

| # | Tab title | Issue |
|---|---|---|
| 1 | Deal Overview | #78 |
| 2 | Pool & Performance | #79 |
| 3 | Waterfall | #80 |
| 4 | Compliance & Covenants | #82 |
| 5 | Cashflow Projection | #83 |

Because each tab is its own module file and the only shared edit is a one-line
registry swap, the five tab PRs merge with minimal conflict.

---

## 4. Cache-awareness (important for demo day)

Cold prospectus extraction (Docling + Gemini) takes **>10 minutes** on CPU, so
the live demo must never trigger it. `DealState.load_green_lion(cache_aware=True)`
(the default) loads the deal model **only from the pre-warmed cache** at
`/tmp/loanwhiz_cache/deals/<slug>.json`; on a cache miss it leaves
`deal_model=None` and records the reason in `load_error`. Tapes are always
loaded live (fast CSV reads).

**Tab implications:**

- A tab that needs the deal model **must handle `deal_model is None`** — render
  a "deal model not loaded (pre-warm the extraction cache)" notice instead of
  crashing.
- Tabs that only need tapes (Pool & Performance) work without the deal model.
- Pre-warm the cache before the demo by running the extraction once (e.g. via
  `loanwhiz.extraction.assembler.extract_deal_model`, or the demo script).

---

## 5. The docked chat panel

Chat is **not** a tab. It lives in a single right-hand column laid out beside
the `gr.Tabs()` in one shared `gr.Row`, so it is visible and reachable from
every tab — one chat instance, not one per tab. The shell ships a placeholder
handler (`_chat_stub_respond`) so the app is runnable now.

**Issue #81 owns the chat wiring:** it replaces the stub with a real call to
`loanwhiz.agent.run_query` (or `loanwhiz.agent.execute_query`) and grounds the
answer in the loaded `DealState`. To give chat access to the shared deal, wire
the same `deal_state` `gr.State` as an input to the chat handler in
`_render_chat_panel` (the chat panel is rendered in `build_app` where
`deal_state` is in scope).

---

## 6. Running the shell

```bash
python clients/demo/app.py   # launches on 0.0.0.0:7860
```

Tests live in `tests/test_demo_shell.py` and never launch the UI or hit the
network.
