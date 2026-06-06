---
id: 2026-06-06-cross-deal-primitives
title: cross-deal-primitives
status: decomposed   # draft → decomposed → filed
created: 2026-06-06
updated: 2026-06-06
epics: []            # umbrella issue numbers, filled in phase 4
---

# cross-deal-primitives

## Context & intent

### The problem (a re-orientation back to the challenge)

The Barcelona AI Tinkerers Structured Finance Hackathon asks for **reusable,
SF-native, *governed* AI primitives** — composable building blocks for structured
finance analysis, with auditability/trust as the differentiator. Over the prior
epics we built exactly those primitives, made them **provably correct** (the spine,
#179), **de-potemkin'd the surface** (#193), and landed a **gold-standard external
proof** — our waterfall engine reproduces a real ING deal's (Green Lion 2024-1)
published Notes & Cash Priority of Payments to the cent (#206).

But measured against what the challenge actually rewards, the **generality and
reusability are buried**:
- The product is **2026-1-centric** — a judge sees one deal, not reusable infra.
- We are **mono-jurisdiction** (Dutch RMBS only) — nothing demonstrates the
  primitives aren't hardcoded to one deal shape.
- The primitives are **not packaged for reuse** (no library/MCP a third party
  could consume).
- We credit **FINOS** governance but not **deeploans** — Algoritmica's (the
  organiser's) own open-source ESMA ETL — which is integrated in code
  (`src/loanwhiz/data/deeploans_client.py`) but **not on the live ingestion path**
  (`esma_tape_normaliser` reads tapes via plain `pandas`, not through deeploans).

So further polishing the single-deal demo has diminishing returns. This epic
shifts focus to **making the reusability/generality/governance undeniable** — the
actual deliverable.

### Why this shape

Four moves, each mapping to something the challenge explicitly values:

1. **Cross-jurisdiction breadth.** The ING investor portal publishes, as *separate
   real deals*: Green Lion 2023-1/2024-1/2026-1 (Dutch RMBS, already loaded),
   **Leone Arancio RMBS** (Italian, with a full 2022–2025 report history), and
   **Sol-Lion II RMBS** (Spanish). Running the *unmodified* extraction pipeline +
   primitives across Dutch **and** Italian **and** Spanish RMBS is the strongest
   possible "broadly applicable, not hardcoded" proof.
2. **Visible reusability.** A "primitives × deals" capability matrix + showcase
   surfaces the same 8 primitives applied across all 5 deals — reusability you can
   *see*, not assert. This becomes the refreshed demo.
3. **Packaged reusability.** Exposing the primitives as a **governed MCP server**
   (mirroring the operator's deck-mcp, consumable on the waafir platform) is the
   literal "reusable infrastructure" deliverable.
4. **Trust, credited correctly.** Elevate the FINOS evidence/audit story *and*
   genuinely wire ESMA ingestion through **deeploans** so crediting it is honest
   (not the over-claim the de-potemkin pass exists to prevent) — and it's smart to
   credit the organiser's ecosystem.

### Honesty constraints (carried from #193 / the de-potemkin discipline)

- Every cross-jurisdiction claim must be **backed by a real extracted model** —
  the Italian/Spanish deals may have **no public loan tapes** (like the Green Lion
  seasoned deals), so primitives that need loan-level data degrade honestly;
  prospectus-derived extraction + report-based validation are what generalise.
- **deeploans** is credited only once it's genuinely on the live path (C6 wires it
  or scopes the claim precisely); we do not re-introduce decorative architecture.
- The capability matrix states what each primitive *actually* did per deal
  (ran / not-applicable-no-tapes / validated), never a blanket green.

### Alternatives considered and rejected

- **Polish the 2026-1 demo further** — rejected; diminishing returns, and it
  doesn't move the generality/reusability axis the challenge scores on.
- **Add more Green Lion (Dutch) deals only** — rejected as the headline; it stays
  mono-jurisdiction. (We already have the 3 Dutch deals; cross-jurisdiction is the
  differentiator.)

### Cross-child ordering rationale

The deal-loading chain is sequential: **C1 register → C2 extract models (the
long-pole offline Docling run on the new-jurisdiction prospectuses) → C3 cross-deal
runner + matrix → C4 showcase UI.** Two deliverables run in parallel alongside it
because they depend only on the *existing* primitives, not the new deals:
**C5 (reusable MCP)** and **C6 (governance + deeploans)**. C2's two-jurisdiction
extraction is driven offline by the orchestrator (as the seasoned-deal V2 was).

## Decomposition

### Epic: Reusable governed primitives across deals & jurisdictions   (umbrella #<N>)

Shift from single-deal polish to proving the primitives are reusable, governed, and
broadly applicable — across 5 real deals in 3 jurisdictions (Dutch / Italian /
Spanish), packaged as consumable infra, with the trust story (FINOS + deeploans)
credited honestly. Honesty discipline from #193 carries: real extractions only,
honest degradation for tape-less deals, deeploans credited only once truly wired.

- **C1 — Register the Italian + Spanish deals** — add Leone Arancio RMBS (Italian)
  and Sol-Lion II RMBS (Spanish) to the deal registry with their ING-hosted URLs
  (prospectus + investor reports); no loan tapes expected. Sequencing: sequential.
  Paths: `src/loanwhiz/data/deals.json`, `src/loanwhiz/config.py`.
- **C2 — Extract the Italian + Spanish deal models** — run the *unmodified*
  extraction pipeline on both prospectuses → committed deal-model seeds, proving the
  pipeline isn't Dutch-RMBS-specific (the cross-jurisdiction proof; long-pole offline
  Docling). Sequencing: sequential. After C1. Paths: `src/loanwhiz/extraction/**`,
  `src/loanwhiz/data/deals/seed/**`.
- **C3 — Cross-deal primitives runner + capability matrix** — a harness/endpoint
  that runs each applicable primitive across all loaded deals and returns a
  "primitives × deals" capability matrix (ran / not-applicable / validated, with
  governance evidence). Sequencing: sequential. After C2. Paths:
  `src/loanwhiz/primitives/**`, `src/loanwhiz/api/main.py`, `tests/**`.
- **C4 — Cross-deal showcase UI** — a view rendering the capability matrix + the
  cross-jurisdiction generality story + the per-deal validation results; becomes the
  refreshed demo. Sequencing: sequential. After C3. Paths: `web/**`,
  `src/loanwhiz/api/main.py`.
- **C5 — Reusable primitives MCP server** — package the 8 SF primitives as a
  governed MCP server (typed I/O + evidence pack), consumable on the waafir platform,
  mirroring the deck-mcp shape. Sequencing: parallel. Paths: `mcp/**`,
  `src/loanwhiz/primitives/**`, `docs/**`.
- **C6 — Governance + deeploans surface** — elevate the FINOS evidence-pack /
  audit-trail / model-risk story in the product, AND genuinely route ESMA tape
  ingestion through `deeploans_client` (or scope the claim precisely) so deeploans is
  credited honestly alongside FINOS. Sequencing: parallel. Paths:
  `src/loanwhiz/data/deeploans_client.py`, `src/loanwhiz/primitives/esma_tape_normaliser.py`,
  `src/loanwhiz/governance/**`, `web/**`, `docs/**`.

## Filed issues

<Filled in phase 4 — the artifact↔issue link.>
