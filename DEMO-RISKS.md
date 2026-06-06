# LoanWhiz — Demo-Exposure ("Potemkin") Audit (2026-06-05)

A system-wide sweep (5 parallel agents) for anything hardcoded / stubbed / mocked /
dead / over-claimed that a sharp structured-finance judge would catch on demo day.
Complements `MODELING-GAPS.md` (the backend math); this is the **demo-facing** view.
Every claim is `file:line`-grounded.

> **Important framing:** the *read-only analytics* surface is genuinely real and
> live over the real 27-period data — **Pool & Performance, the Waterfall cascade,
> the Compliance trend, the Primitives catalogue, the per-period tape analytics all
> compute from actual tapes.** The potemkin is concentrated in four areas, plus doc
> over-claims:
> 1. **Governance / evidence-pack + confidence** (the "auditable agents" centerpiece)
> 2. **Anything labelled "Projection"**
> 3. **The cold-cache Overview *landing page***
> 4. **Chat reachability**

Risk legend: 🔴 breaks/looks-fake on a normal click-through · 🟠 exposed by a pointed
question · 🟡 only if deliberately probed.

---

## Demo-exposure TOP 10 (ranked by likelihood of getting caught)

1. **🔴 Overview landing page is blank out-of-the-box.** Capital Structure + Triggers +
   Completeness all show "not extracted" empty-states unless the deal-model cache is
   pre-warmed (`data/deals/` is `.gitkeep`-only; `/model` returns `not_cached` rather
   than running the ~30-min cold extraction). First screen the audience sees.
   `web/app/page.tsx:142-220`, `api/main.py:304-333`. **→ ship/seed the cached model.**
2. **🔴 "Projection" doesn't project.** `/deal/{id}/project` runs a *single* waterfall
   per scenario; `months` linearly rescales one collections number; "stress" = ×0.7;
   **WAL is pinned to the horizon** so base == stress == "4.00 yr (48.0 mo)".
   `api/main.py:844-924` (esp. `:869-870,900-909`). The real `CashflowProjector` is
   imported for side-effects only.
3. **🔴 Governance evidence pack is hollow.** The planner hardcodes every tool-call's
   `confidence=0.9`, `citations=[]`, `output_summary=""`, `duration_ms=0`
   (`agent/planner.py:166-175`) — discarding the *real* confidence/citations the
   primitives return (`tools.py:100-103`). So every "View evidence" sheet shows blank
   outputs, 0 ms, 0 citations, a constant 90%, and "human review" can **never** fire;
   `finos_compliant=True` is a hardcoded field (`governance/evidence_pack.py:99-101`).
   The "auditable agents" headline collapses on the first click.
4. **🔴 Chat can't answer the obvious questions.** 4-tool ReAct agent (Gemini Flash)
   with **only 3 hardcoded 2026 tape URLs in the system prompt** (`planner.py:37-40`),
   **no prospectus/deal-model tool**, no deal/period selection, no access to the 24
   historical tapes. "What's the reserve target?", "show me Jan 2025", "did anything
   breach in 2024?", "what coupon does the prospectus set?" all fail or hallucinate.
5. **🔴 Compliance: PDL & reserve triggers can never fire.** `/compliance` builds
   `CovenantInput` without the PDL/reserve scalars (`api/main.py:433-439`) → default
   0/0; reserve ratio → `100.0` "fully funded"; extracted metric names match no
   monitor sentinel → `0.0` (`covenant_monitor.py:233-260`). A risk monitor whose
   scariest triggers are structurally dead, and the flat proximity chart. *(Also a
   spine item — S5/S9.)*
6. **🔴 Demo-runner mislabels 27 tapes as Feb/Mar/Apr.** `demo/run_green_lion.py:162-217`
   loops all 27 tapes but `dates[0/1/2]` + the print table assume 3 — run live as a
   fallback, "Feb 2026" shows Jan 2024 and it downloads 27 CSVs.
7. **🟠 Waterfall reserve/swap/PDL steps show €0.** `reserve=0/0`, `swap=0`,
   `pdl=0/0` hardcoded at the call site (`api/main.py:590-596`); 6 junior steps +
   Class B interest are hardcoded 0 inside the runner. The cascade renders but several
   lines are pinned at zero; Class A never amortizes. *(Spine — S6/S9.)*
8. **🟠 Confidence gating is theater.** `executor.py:288-311` classifies on
   `tc.confidence` which is always 0.9 → every step PASSED, aggregate always 0.9 >
   0.7, `human_review_required` never true; the retry hook is "not wired live".
9. **🟠 Dead primitives advertised as live.** `cashflow_projector`, `report_verifier`,
   `audit_logger`, `multi_period_waterfall_runner` appear in the Framework catalogue
   but no endpoint/agent reaches them (`api/main.py:66-71` side-effect imports). The
   "reconcile against investor reports" primitive is reachable only via the offline CLI.
10. **🟡 Single-deal selector + divergent hardcoded constants.** The deal dropdown has
    one option (`deals.json` is `{}`); reserve balance is **5,000,000** in the demo
    runner vs **10,636,000** in the API projection base; pool base frozen at
    `1_033_412_063`. Cross-checkable inconsistencies.

---

## Over-claims a structured-finance judge will challenge (docs/deck vs reality)

- **"Data-agnostic — add any RMBS, no code change"** (deck 9, README:124). Registry
  plumbing is real but ships one deal; a 2nd deal renders a blank Overview and the
  runner hardcodes Green Lion's steps (ignores `DealModel.waterfalls`). 🔴
- **"Executes the deal's waterfall / reconciles against the investor report"**
  (deck 3/6, README:201-203). Runner doesn't interpret the extracted PoP; sequential-
  pay trigger neither extracted nor implemented; `ReportVerifier` is dead code. 🔴
- **"FINOS confidence scoring (40% coverage / 30% cross-ref / 30% LLM) + citations on
  every call + human review < 0.7"** (deck 7, governance.md §2/§5, model-card). The
  `compute_confidence` formula exists in **no code**; the evidence pack hardcodes
  0.9/empty; `completeness_score` is just "section headers found / 4". 🔴
- **"definitions{}"** in the architecture/deck — the shipped model has **0** definitions. 🟠
- **Covenant prompt claims "5 known triggers"** — only 3 extracted (Sequential-Pay &
  Clean-Up Call missing — the headline RMBS mechanic). 🔴 (`covenant_extractor.py:115`)
- **"27 months of history / proven"** — genuine synthetic tapes, but 24 are
  *pre-closing* snapshots with no investor-report ground truth; phrasing reads as real
  post-issuance seasoning. 🟠 (data-card is candid; deck/README blur it.)

**Positive (real, keep showing):** trigger citations are genuine verbatim prospectus
excerpts with section refs; Pool/tape analytics are real; the registry/I-O schemas are real.

---

## What fixes what (so we don't double-track)

**Already covered by the spine epic #179** (correct modeling): the flat compliance
chart (S5/S9), waterfall zeros + no amortization + reserve/PDL (S6/S9), report-verifier
wiring (S7), multi-period state (S6), the fake `completeness_score` (S8), endpoint
inconsistency (S9).

**Separate fast-follow (projection epic):** `/project` realism + WAL.

**NOT covered — the new "demo-readiness / de-potemkin" epic** (this audit's net-new):
1. **Real governance/evidence pack** — thread true confidence/citations/duration from
   tool results into `ToolCallRecord`; make `human_review_required` fire; derive (or
   relabel) `finos_compliant`. (`planner.py:166-175`, `executor.py:288-311`)
2. **Chat grounding** — give the agent a deal-model tool + a reconstructed-ledger tool +
   all 27 tapes + deal/period selection (+ projector/verifier tools). (`agent/`)
3. **Overview cold-cache** — ship/seed the cached deal model so the landing isn't blank
   on a clean host. (`data/deals/`, `api/main.py:304`)
4. **Catalogue honesty** — wire `audit_logger` into the REST primitive path (or soften);
   mark non-reachable primitives in `/primitives`. (`api/main.py:66-71`)
5. **Deal selector** — restyle as a static deal label, or add a real 2nd deal.
6. **Docs/deck honesty pass** — align README, `docs/*.md`, `presentation/build_deck.py`,
   governance.md, model-card with what actually runs (soften the verbs in the list above).
7. **Demo hygiene** — fix `run_green_lion.py` 27-tape labels; delete dead `clients/` bytecode.
