---
id: 2026-06-06-cross-deal-primitives
title: cross-deal-primitives
status: draft        # draft → decomposed → filed
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

<Filled in phase 2.>

## Filed issues

<Filled in phase 4 — the artifact↔issue link.>
