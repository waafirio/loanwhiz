---
id: 2026-06-24-productionising-loanwhiz
title: Productionising LoanWhiz — extraction generality, self-service ingestion, analytical depth, governed seams
status: draft
created: 2026-06-24
updated: 2026-06-24
epics: []
---

# Productionising LoanWhiz — extraction generality, self-service ingestion, analytical depth, governed seams

## Context & intent

This plan is the durable capture of the next leg chosen after the
**2026-06-24 five-question system audit** of LoanWhiz (current `main`, after
the tape-path-canonicalisation plan + the on-demand `/extract` endpoint all
shipped). The audit asked, for the platform's headline promises, "is this
actually true today?" — and answered honestly:

| Question | Verdict |
|---|---|
| Load **any EDW prospectus** → reasonable output? | 🟡 PARTIAL (~40–50% full extraction on unseen English/ESMA; less for non-English/non-ESMA) |
| Incrementally add **reports + tapes** → more detail? | 🟡 PARTIAL (tapes incremental; reports gated on a per-deal loader) |
| Compare deals with **"why one is better" analysis**? | 🔴 NO (aligned display + median deviation, no judgement) |
| Chat insights pulling from **all three sources**? | 🟡 PARTIAL (baked-in dual-source tools; no general report reader / cross-deal / cross-source synthesis) |
| **Governance** throughout? | 🟡 PARTIAL (strong in agent/REST/MCP; thin at extraction + `/extract`; review-flag not enforced) |

**The core is real and honest** — a deterministic, tranche-general engine on
a canonical schema, with honest degradation (`unmapped` escape, valid JSON
always) and a genuine governance framework. LoanWhiz is a credible
decision-support platform, not a Green-Lion demo. What remains is the gap
between "structurally general and honest" and "productionised across the EDW
breadth with real analytical output and governance at every seam." The audit's
findings cluster cleanly into **four independent themes**, each an epic.

### The four themes (and why this shape)

- **Epic A — Extraction generality.** The engine is deal-agnostic, but
  *extraction* is the long pole (the EDW plan said so), and the audit
  quantified it: an arbitrary EDW prospectus extracts fully only ~40–50% of
  the time (English/ESMA), less otherwise. The blockers are concrete and
  fixable: a sparse closed taxonomy (recipients/metrics tuned to Green Lion),
  a definitions graph that is wired but always returns `{}` (a real bug),
  section routing that still drops nested waterfall layouts (Sol-Lion's empty
  revenue PoP), and tranche parsing that mis-handles exotic stacks. None of
  these is architectural — they are coverage/robustness work on a sound base.

- **Epic B — Self-service ingestion.** Tapes are genuinely incremental
  (add to the registry, the fold grows), but the *report* path is gated on a
  hand-written per-deal `_REPORT_LOADERS` parser (a new deal 422s without it),
  and there is no runtime ingest API — registration is a `deals.json` edit and
  `/extract` only does the prospectus model. Onboarding a deal's full data set
  must become a product action, not dev work.

- **Epic C — Analytical depth (comparison + chat).** This is where the
  product is thinnest vs. its promise. `/compare` is *aligned display* — it
  presents canonical-taxonomy-aligned structure, overlaid performance, and a
  median-deviation lens, but renders **no judgement of why one deal is
  better**; the relative-value scorecard exists but isn't wired in, and
  price/spread RV is deferred (#307). Chat synthesises only *inside*
  pre-built dual-source tools — there is no general investor-report reader, no
  cross-deal comparison tool, and the system prompt routes one-question→
  one-tool with no cross-source synthesis guidance (a hallucination risk).
  This epic turns "display" into "analysis" and gives chat real reach.

- **Epic D — Governance at the seams.** Governance is strong where it's
  wired (agent/REST/MCP thread real confidence+citations, `audit_result`
  wraps calls, `finos_compliant` is derived from a real 23-control mapping) —
  but **thin exactly where it should be strongest**: the extraction pipeline
  surfaces per-step citations but not per-step *confidence*, the newest
  user-facing `/extract` endpoint is an ungoverned seam (metadata counts, no
  `audit_result`, no envelope), and `human_review_required` is computed but
  never *enforced*. Close the seams and make the review gate real.

### Cross-epic ordering rationale

```
A (extraction generality) ─┐
B (self-service ingestion) ─┤── breadth foundation (A,B parallel)
C (analytical depth) ───────┤── builds on richer A data + B report ingestion,
                            │   but its compare/chat wiring is mostly independent
D (governed seams) ─────────┘── fully independent; runs alongside everything
```

- **A and B are the breadth foundation** and run in parallel — more deals
  extract well (A) and more of their data ingests self-service (B).
- **C builds on A/B** for richer comparison/chat inputs, but most of its work
  (wiring the scorecard into `/compare`, adding chat tools) is independent, so
  it is filed parallel and only its data-quality *benefits* from A/B.
- **D is fully independent** — governance plumbing touches the seams directly
  and can proceed at any time.
- Recommend filing **all four epics enrolled now**. They are independent of
  the in-flight **#389** (docs + test hardening), which proceeds separately.

This plan does not re-open the design — it materialises the four themes the
audit surfaced and the operator confirmed ("all 4 are important and relevant").

## Decomposition

<filled in Phase 2>

## Filed issues

<filled in Phase 4>
