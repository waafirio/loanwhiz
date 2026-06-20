# Deal-comparison tool — design

**Date:** 2026-06-20
**Status:** Design (forks locked: unified N-way view + benchmark lens; drill-down chatbot). Spec for #283 (Epic 7).
**Phase:** 5 (analyst-facing tools). The original roadmap **item 2**.

Reads the canonical `DealRules` + `DealStateSeries` (both on `main`); lives in the Next.js dashboard (`web/`).

---

## Context & purpose

The first analyst-facing product on top of the validated engine. **v1 serves three jobs** (operator-chosen):

1. **Credit / risk screening** — which of these deals is riskier, and why (arrears, covenant proximity, PDL/reserve, loss rate).
2. **Structural diligence** — how the deals differ in *terms* (waterfall, triggers, tranches, reserve).
3. **New-deal benchmarking** — is a target deal in line with seasoned comps (jurisdiction/vintage).

**Relative value / pricing is explicitly deferred** (it needs external spread/price inputs; tracked separately as #307). So v1 runs entirely on data the platform already produces — no external feed.

---

## Locked decisions

- **One unified N-way comparison view + a benchmark lens** (not separate views). Risk-screening and structural-diligence are the same N deals seen two ways; benchmarking is that same view with one deal marked as *target* and metrics shaded by deviation from the comp-set median.
- **The chatbot is a drill-down companion** to the visual (not chatbot-primary). The visual is the persistent at-a-glance comparison; the chat answers ad-hoc "why/which" questions over the same data, grounded with the governance evidence pack.

---

## Architecture

```
deal picker (multi-select 2..N; benchmark: mark 1 target, auto-suggest comps by jurisdiction/vintage)
        ▼
GET /compare?deals=a,b,c[&target=a]   ── backend assembles + ALIGNS DealRules + DealStateSeries for N deals
        ▼
┌─────────────────────────── cross-deal view (web/) ───────────────────────────┐
│ Panel 1 — Structural diff (DealRules)     Panel 2 — Performance/risk (DealStateSeries) │
│   column per deal; rows aligned by          overlaid time-series, one line per deal:     │
│   RecipientType / MetricType; diff-          pool factor · arrears buckets · reserve ·   │
│   highlighted where deals differ            PDL · covenant proximity-to-breach           │
│                                             + a risk-summary row                          │
│   ── Benchmark lens (toggle): mark target, shade each metric by Δ vs comp-set median ──   │
└───────────────────────────────────────────────────────────────────────────────────────┘
        ▼ docked chatbot — NL drill-down over the same data, answers carry the governance evidence pack
```

### Panel 1 — Structural diff (jobs 3 + 4, from `DealRules`)
Column-per-deal comparison table. **Rows align by the canonical `RecipientType` / `MetricType`** — so a waterfall step or trigger lines up across deals even when each issuer labels it differently (this is exactly what the canonical taxonomy buys us). Rows: tranche stack (size/rating/coupon), waterfall steps in priority order, trigger metrics + normalized thresholds, reserve mechanics. Cells diff-highlighted where deals differ; `unmapped` steps shown honestly as "not comparable."

### Panel 2 — Performance / risk (jobs 1 + 4, from `DealStateSeries`)
Overlaid time-series, one line per deal, on a shared period axis: pool factor, arrears buckets (where tape-derived), reserve balance/target, PDL, and **covenant proximity-to-breach**. Plus a top risk-summary row (latest-period snapshot + trend arrows) for at-a-glance triage.

### Benchmark lens (job 4)
A toggle on the unified view: designate one deal as **target**; each metric cell/series is shaded by its deviation from the **comp-set median** (the other selected deals). Auto-suggest the comp set by jurisdiction + vintage from the deal registry, operator-overridable.

### Chatbot (drill-down companion)
Docked chat (the dashboard's existing pattern) answering NL questions over the *currently-selected* deals: "which has the tightest covenant headroom?", "diff the reserve mechanics", "is the target's arrears in line with comps?". Answers route through the agent and **carry the governance evidence pack** (citations + confidence) that already ships with every query — so a claim in chat is traceable to the source figure. The chat operates on the same assembled comparison payload as the visual; it explains, it doesn't replace.

---

## Backend

A new `GET /compare` (or `POST /compare`) endpoint that, for N deal ids:
- assembles each deal's `DealRules` + `DealStateSeries` (the per-deal data already exists — `/deal/{id}/model`, the reconstructed series),
- **aligns** waterfall steps + triggers across deals by `RecipientType`/`MetricType`,
- computes the comp-set median + per-target deviations when `target` is set,
- returns one comparison payload the view + chat both consume.

No new modeling — pure assembly/alignment over existing per-deal outputs.

---

## Validation

- N-way structural + performance comparison renders for ≥3 of the 5-deal set across jurisdictions.
- Benchmark lens: target vs comps shades deviations correctly against a hand-checked median.
- Chatbot answers a comparison question with a correct figure + a citation from the right deal.

---

## Relation to #284's accepted split

#284's split created a **relative-value/spread screener (#307, held)** — that is the *quantitative RV scorecard*, the deferred job (2). This tool (#283) is the *qualitative/visual* comparison for jobs 1/3/4. When RV is picked up, #307 plugs a "relative value" panel/tab into this same view rather than a separate surface.

---

## Open / deferred

- **Relative value / pricing** (job 2) — needs external spread/price inputs; #307.
- Comparing deals modeled from *different* document packages (tape-driven vs report-driven) — the series are comparable, but flag provenance/coverage differences in the UI so a thinner deal isn't read as equivalent.
