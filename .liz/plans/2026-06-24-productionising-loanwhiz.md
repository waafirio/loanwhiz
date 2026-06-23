---
id: 2026-06-24-productionising-loanwhiz
title: Productionising LoanWhiz — extraction generality, self-service ingestion, analytical depth, governed seams
status: filed
created: 2026-06-24
updated: 2026-06-24
epics: [390, 391, 392, 393]
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

Four epics, 13 children. Epics A, B, C, D all file in parallel; cross-epic
dependencies are narrative (C's data quality benefits from A/B; none are hard
blockers). Within Epic B the two children are sequential.

### Epic A: Extraction generality   (umbrella #390)

Raise the realistic extraction success rate for an arbitrary EDW prospectus
(unseen issuer/language/layout) from ~40–50% toward broad coverage, on the
existing sound, honestly-degrading base. All children are independent.

- **Broaden the canonical recipient/metric taxonomy** — expand the closed
  `RecipientType`/`MetricType` enums + alias table + LLM-classify coverage
  beyond Green-Lion vocabulary for the global ABS universe; add missing
  metrics (e.g. `pool_balance_fraction`, extracted but not in the enum → an
  unfireable trigger). Sequencing: parallel. Paths: `src/loanwhiz/domain/rules.py`, `src/loanwhiz/extraction/taxonomy.py`, `src/loanwhiz/primitives/covenant_monitor.py`.
- **Fix and link the definitions graph** — `definitions_graph.py` is wired but
  always emits `{}`; populate term→definition and link defined terms into step
  conditions + trigger metrics so conditional waterfall prose resolves.
  Sequencing: parallel. Paths: `src/loanwhiz/extraction/definitions_graph.py`, `src/loanwhiz/extraction/assembler.py`, `src/loanwhiz/primitives/waterfall_interpreter.py`.
- **Harden non-standard waterfall section routing** — fix the nested-layout
  failure (Sol-Lion ES revenue PoP = 0 steps: real steps under a generic
  parent section); improve LLM routing / payment-list detection /
  descendant-text handling. Sequencing: parallel. Paths: `src/loanwhiz/extraction/section_router.py`, `src/loanwhiz/extraction/assembler.py`.
- **Structure-agnostic tranche parsing** — handle exotic stacks (A1–A6
  multi-series, single-class, non-A/B/C naming) that currently mis-parse
  (Sol-Lion Class O = 42 EUR artifact). Sequencing: parallel. Paths: `src/loanwhiz/extraction/waterfall_extractor.py`, `src/loanwhiz/extraction/**`.

### Epic B: Self-service ingestion   (umbrella #391)

Make onboarding a deal's full data set a product action, not per-deal dev work.

- **General report ingestion** — replace the hand-written per-deal
  `_REPORT_LOADERS` requirement (a new deal's report path 422s without a
  committed parser) with a general Notes & Cash report extractor → canonical
  `ParsedReport`, reconciliation-gated, so a new deal cold-starts the report
  path zero-touch. Sequencing: sequential. Paths: `src/loanwhiz/primitives/report_extractor.py`, `src/loanwhiz/primitives/report_adapter.py`, `src/loanwhiz/api/main.py`.
- **Tape & report ingest API** — runtime endpoints to register + ingest a
  tape or report for a deal (today registration is a `deals.json` edit and
  `/extract` does only the prospectus model), materialising into the same
  registry/cache the cold-start reads. Sequencing: sequential. After the
  general report ingestion child. Paths: `src/loanwhiz/api/main.py`, `src/loanwhiz/api/extraction_jobs.py`, `src/loanwhiz/config.py`.

### Epic C: Analytical depth — comparison + chat   (umbrella #392)

Turn comparison from display into analysis, and give chat real cross-source /
cross-deal reach. All children are independent.

- **Wire scoring + reasoning into `/compare`** — integrate the existing
  relative-value scorecard (`relative_value_screener`) into `/compare` and add
  a reasoned comparative narrative (credit enhancement, trigger headroom, WAL,
  loss performance) with honest available/unavailable flags — the "why A is
  better" the endpoint lacks today. Sequencing: parallel. Paths: `src/loanwhiz/api/compare.py`, `src/loanwhiz/primitives/relative_value_screener.py`, `src/loanwhiz/api/main.py`.
- **Expose deal comparison to chat** — add a `compare_deals` agent tool
  (the `/compare` endpoint exists but isn't wired to the agent) and remove the
  hardcoded single-deal default so chat can reason cross-deal. Sequencing:
  parallel. Paths: `src/loanwhiz/agent/tools.py`, `src/loanwhiz/agent/planner.py`.
- **General investor-report reader tool for chat** — today only
  `verify_report` (diff) reads reports; add a tool to read/explore
  investor-report contents so the agent can answer "what does the report say
  about X?". Sequencing: parallel. Paths: `src/loanwhiz/agent/tools.py`, `src/loanwhiz/api/main.py`.
- **Cross-source synthesis in chat** — add system-prompt guidance (and/or a
  synthesis tool) so the agent deliberately combines prospectus + report + tape
  in one grounded answer instead of single-tool routing (which risks
  hallucinated synthesis). Sequencing: parallel. Paths: `src/loanwhiz/agent/planner.py`, `src/loanwhiz/agent/tools.py`.

### Epic D: Governance at the seams   (umbrella #393)

Close governance exactly where it is thinnest — the extraction pipeline, the
newest `/extract` endpoint, and review enforcement. All children independent.

- **Govern the on-demand `/extract` endpoint** — wrap the `extract_deal_model`
  call in `audit_result()` + a `PrimitiveResult`-style envelope; surface
  confidence + a failure audit trail, not just metadata counts (it is the
  least-governed, newest, user-facing seam). Sequencing: parallel. Paths: `src/loanwhiz/api/extraction_jobs.py`, `src/loanwhiz/primitives/audit_logger.py`.
- **Surface per-field extraction confidence** — `waterfall_extractor`/
  `covenant_extractor` compute `extraction_confidence` per step/trigger but it
  is not threaded into the emitted `DealModel` (only aggregate completeness);
  surface per-step/per-trigger confidence so users can tell reliable
  extractions from noise. Sequencing: parallel. Paths: `src/loanwhiz/extraction/waterfall_extractor.py`, `src/loanwhiz/extraction/covenant_extractor.py`, `src/loanwhiz/extraction/assembler.py`.
- **Enforce human-review-required** — the flag is computed in evidence packs /
  executor but not acted on (no gating/routing); make a low-confidence answer
  actually gate or route, or formalise + document the API contract. Sequencing:
  parallel. Paths: `src/loanwhiz/agent/executor.py`, `src/loanwhiz/governance/evidence_pack.py`, `src/loanwhiz/api/main.py`.

## Filed issues

- Epic "Extraction generality" → umbrella #390
  - #394 Broaden the canonical recipient/metric taxonomy
  - #395 Fix and link the definitions graph
  - #396 Harden non-standard waterfall section routing
  - #397 Structure-agnostic tranche parsing
- Epic "Self-service ingestion" → umbrella #391
  - #398 General report ingestion (kill per-deal _REPORT_LOADERS)
  - #399 Tape & report ingest API  [After #398]
- Epic "Analytical depth — comparison + chat" → umbrella #392
  - #400 Wire scoring + reasoning into /compare
  - #401 Expose deal comparison to chat
  - #402 General investor-report reader tool for chat
  - #403 Cross-source synthesis in chat
- Epic "Governance at the seams" → umbrella #393
  - #404 Govern the on-demand /extract endpoint
  - #405 Surface per-field extraction confidence
  - #406 Enforce human-review-required
