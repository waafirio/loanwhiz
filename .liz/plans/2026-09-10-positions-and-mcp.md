---
id: 2026-09-10-positions-and-mcp
title: "A position model, and an honest MCP catalogue page"
status: filed
created: 2026-09-10
updated: 2026-09-10
epics: [569, 570]
---

# A position model, and an honest MCP catalogue page

## Context & intent

Two pieces of infrastructure the demo audience — a UK bank considering a CLO
allocation — will ask for, and which the platform cannot express today.

### The position model, and why it is infrastructure rather than a feature

Everything the platform models is **deal-level**. There is no concept of a
*holding*: which tranche, in what size, from what date. That absence is what
keeps two already-filed epics deal-grade rather than portfolio-grade —
look-through concentration (#560) can only report a deal's exposure rather than
*yours*, and the investor due-diligence record (#561) attaches per deal when the
regulatory obligation attaches **per position**.

So the position model earns its way in as the thing those surfaces need. It is
deliberately **not** "portfolio management": no P&L, no mark-to-market, no
cashflow projection. The third of those depends on the engine-computed core,
which today covers 3 of 29 lines on the one deal that reconciles, and building a
projection on it would invite a question the platform cannot yet answer.

### The honesty problem a demo book creates

A demo needs positions, and the platform holds none. **Inventing holdings and
presenting them as data is fabrication** — the failure this codebase has removed
repeatedly (#493 zero coupons, #513 empty-period passes, #514 vacuous steps,
#538 netted tie-outs, #549 a refusal that kept its value).

There is a precedent for doing it honestly. #483 added `synthetic` as a tape
provenance kind and #484 committed generated pools *with the fit spec beside
them*, so the derivation is auditable; the evidence-pack component labels such a
source **"SYNTHETIC — generated, describes no real obligor"**. A demo book must
carry the same class of qualifier: illustrative positions, marked as such at
every surface that renders them, never mixed indistinguishably with a real one.

### The MCP page, and the thing it must not imply

The MCP server is real and better than a page would suggest. It introspects
`PRIMITIVE_REGISTRY` and exposes each endpoint-reachable primitive as a tool
whose input is the primitive's own typed Pydantic schema; every call returns the
full `PrimitiveResult` — the typed output **plus** `confidence`, source
`citations`, and a structured `audit_entry`. For a bank weighing whether to let
a model touch its analytics, that governance evidence travelling with the tool
call is the whole argument, and `GET /primitives` already serves the catalogue
with per-primitive `reachability` (currently 6 `live`, 5 `library-only`).

**But the server has no authentication of any kind** — no token, no bearer,
nothing. The operator's decision is to leave it that way for now, because this
will eventually run on top of waafir-platform, which owns that concern.

That decision is fine and the page can still be built. What the page must not do
is **imply a lock that is not there.** A PAT setup flow rendered like a working
one, in front of a bank, is a false claim about a security property — the most
expensive kind of confident-wrong this platform could ship, and the exact
inverse of what makes it credible. The illustrative section must be
unmistakably illustrative, and say where the real thing will live.

## Decomposition

### Epic: A position model   (umbrella #569)

- **Model a position, and commit an illustrative book** — A typed holding
  (deal, tranche, size, as-of date) with a provenance qualifier marking an
  illustrative position as illustrative, plus a committed demo book that no
  surface can render as real. Sequencing: parallel.
  Paths: `src/loanwhiz/domain/**`, `src/loanwhiz/data/**`, `tests/**`.
- **Serve a book view** — An endpoint returning a holder's positions with the
  deal facts already available per tranche (seniority, balance, coupon where
  resolved, and an honest refusal where not). Sequencing: sequential. After the
  child above. Paths: `src/loanwhiz/api/main.py`, `tests/**`.
- **Surface the book** — A screen showing the positions, with illustrative ones
  visibly marked. Sequencing: sequential. After the child above.
  Paths: `web/**`, `docs/**`, `tests/**`.

### Epic: An MCP catalogue page   (umbrella #570)

- **Serve the MCP catalogue truthfully** — An endpoint stating which primitives
  the MCP server actually exposes as tools versus which are library-only, read
  live from the registry rather than transcribed, with each tool's typed input
  schema and the governance fields its result carries. Sequencing: parallel.
  Paths: `src/loanwhiz/api/main.py`, `mcp/**`, `tests/**`.
- **Build the page, with authentication marked as illustrative** — A screen
  showing the tool catalogue, an example call and its governed result, and a
  setup section that is unmistakably a *sketch of a future flow* rather than a
  working one, naming waafir-platform as where authentication will live.
  Sequencing: sequential. After the child above.
  Paths: `web/**`, `docs/**`, `tests/**`.

## Filed issues

- Epic "A position model" -> umbrella **#569**
  - #571 Model a position, and commit an illustrative book - parallel
  - #572 Serve a book view - after #571
  - #573 Surface the book - after #572
- Epic "An MCP catalogue page" -> umbrella **#570**
  - #574 Serve the MCP catalogue truthfully - parallel
  - #575 Build the MCP page, with authentication marked as illustrative - after #574
