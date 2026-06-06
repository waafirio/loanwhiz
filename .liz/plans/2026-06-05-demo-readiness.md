---
id: 2026-06-05-demo-readiness
title: demo-readiness
status: filed        # draft → decomposed → filed
created: 2026-06-05
updated: 2026-06-05
epics: [193]         # umbrella issue numbers, filled in phase 4
---

# demo-readiness

## Context & intent

### What this is

A system-wide "potemkin" audit (2026-06-05, 5 parallel agents, captured in
`DEMO-RISKS.md`) swept the whole stack for anything hardcoded / stubbed / mocked /
dead / over-claimed that a sharp structured-finance judge would catch at the
hackathon demo (10 June). The reassuring finding: the *read-only analytics*
surface is genuinely real over real 27-period data (Pool, the Waterfall cascade,
the Compliance trend, tape analytics, the Primitives catalogue). The exposure is
concentrated in four areas + doc over-claims:

1. **Governance / evidence-pack + confidence** — the "auditable agents" centrepiece
   is theater: the planner hardcodes every tool-call's `confidence=0.9`,
   `citations=[]`, `duration_ms=0`, `output_summary=""` (`agent/planner.py:166-175`),
   discarding the *real* values the primitives return; `human_review` can never fire;
   `finos_compliant=True` is a literal.
2. **Chat reachability** — a 4-tool ReAct agent with only 3 hardcoded 2026 tape URLs,
   no prospectus/deal-model tool, no period selection, blind to the 24 historical tapes.
3. **The cold-cache Overview *landing page*** — blank on a clean host (no committed
   deal-model cache).
4. **Dead primitives advertised as live** + **docs/deck over-claims** the system can't back.

### Why a separate epic from the spine (#179)

The **spine** (#179) fixes the *modeling math* — the waterfall/compliance/projection
*correctness* (real PDL/reserve/amortization, the flat covenant chart, the
report-verifier wiring). This epic fixes the *surface* — governance provenance, the
chat agent, the UI cold-cache, the framework catalogue, and the honesty of every
claim. Different concerns, mostly disjoint files, parallelizable, and on a tighter
demo clock. Several spine items will *also* improve the demo (real compliance curve,
amortizing balances) — those are tracked there, not duplicated here.

### The governing principle: every claim is wired or softened

The point is not to paper over gaps — it's that **by demo day, every claim the
product makes is either genuinely backed or honestly scoped.** Where we can wire the
real thing in time (governance provenance, chat grounding, the cold-cache fix), we
wire it. Where we can't before 10 June (e.g. a fully generic second deal), we
*soften the claim to match reality* rather than risk a live contradiction. The
docs/deck honesty pass (D6) is therefore the LAST child — it reflects whatever state
the other fixes (and the spine) actually delivered, so the narration is true on the day.

### Scope boundaries

- **In:** governance/evidence provenance, chat grounding, Overview cold-cache,
  catalogue honesty, deal selector, docs/deck honesty pass, demo hygiene.
- **Out (tracked elsewhere):** the modeling correctness (spine #179) and forward
  projection realism (its own fast-follow epic). The quick compliance metric-alias
  stopgap noted in `DEMO-RISKS.md` is a spine concern, not here.

### Ordering rationale

D1–D5 + D7 are independent (disjoint surfaces) and run in parallel, ranked by
demo-exposure (governance / chat / cold-cache are the 🔴 top-three). D6 (docs/deck
honesty) is sequential and LAST — it can only tell the truth once it knows what the
other fixes and the spine actually landed.

## Decomposition

### Epic: Demo-readiness — de-potemkin the surface   (umbrella #<N>)

Make every demo-facing claim either genuinely backed or honestly scoped by 10 June.
Targets the four exposure areas + over-claims from `DEMO-RISKS.md` that the spine
(#179) and the projection fast-follow don't cover. D1–D5 + D7 are disjoint surfaces
run in parallel (ranked by demo-exposure); D6 (docs/deck honesty) is sequential and
LAST so it reflects what actually landed.

- **D1 — Real governance/evidence pack** — thread the true per-tool confidence /
  citations / duration the primitives already return into the evidence pack so the
  "auditable agents" sheet stops showing constant 0.9 / 0 ms / no citations, and
  `human_review_required` can fire; derive or relabel `finos_compliant`.
  Sequencing: parallel. Paths: `src/loanwhiz/agent/planner.py`,
  `src/loanwhiz/agent/executor.py`, `src/loanwhiz/governance/evidence_pack.py`,
  `web/components/evidence-pack-sheet.tsx`.
- **D2 — Chat grounding** — give the agent a deal-model tool (prospectus-derived
  terms/triggers/waterfall), access to all 27 tapes, and deal/period selection, so
  it can answer the obvious questions instead of working off 3 hardcoded URLs. (The
  reconstructed-ledger tool is a follow-on once the spine lands.) Sequencing: parallel.
  Paths: `src/loanwhiz/agent/tools.py`, `src/loanwhiz/agent/planner.py`.
- **D3 — Overview cold-cache** — ship/seed the cached deal model (or make the cold
  path graceful) so the landing page isn't blank on a clean host. Sequencing: parallel.
  Paths: `src/loanwhiz/data/deals/**`, `src/loanwhiz/api/main.py`, `scripts/**`.
- **D4 — Catalogue honesty** — wire `audit_logger` into the REST primitive path (or
  soften the claim), and mark which catalogued primitives are actually endpoint/agent
  reachable. Sequencing: parallel. Paths: `src/loanwhiz/api/main.py`,
  `src/loanwhiz/primitives/audit_logger.py`, `web/app/(routes)/primitives/**`.
- **D5 — Deal selector robustness** — make the selector correctly handle 1..N deals
  (no single-option dead control); forward-compatible with the seasoned deals landing
  later. Sequencing: parallel. Paths: `web/components/deal-selector.tsx`,
  `web/lib/deal-context.tsx`.
- **D7 — Demo hygiene** — fix `run_green_lion.py`'s 27-tape mislabelling (assumes 3)
  and delete the dead `clients/` bytecode. Sequencing: parallel. Paths:
  `demo/run_green_lion.py`, `clients/**`.
- **D6 — Docs/deck honesty pass** — align README, `docs/*.md`,
  `presentation/build_deck.py`, `governance.md`, the model card with what actually
  runs (the over-claim list in `DEMO-RISKS.md`): soften where unwired, assert where
  fixed. Runs LAST. Sequencing: sequential. After D1, D2, D3, D4, D5, D7. Paths:
  `README.md`, `docs/**`, `presentation/**`.

## Filed issues

- Epic "Demo-readiness — de-potemkin the surface" → umbrella **#193**
  - #194 D1 — Real governance/evidence pack
  - #195 D2 — Chat grounding
  - #196 D3 — Overview cold-cache
  - #197 D4 — Catalogue honesty
  - #198 D5 — Deal selector robustness
  - #199 D7 — Demo hygiene
  - #200 D6 — Docs/deck honesty pass (after #194–#199)
