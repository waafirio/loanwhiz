---
id: 2026-09-10-clo-investor-surfaces
title: "Two surfaces a CLO buyer needs: look-through concentration, and an investor due-diligence record"
status: decomposed
created: 2026-09-10
updated: 2026-09-10
epics: []
---

# Two surfaces a CLO buyer needs: look-through concentration, and an investor due-diligence record

## Context & intent

The platform now carries **two** CLOs with obligor-level collateral schedules
parsed from their trustee reports — Cairn CLO XVII (580 assets) and Contego CLO
XI (177/179 assets). Each `CollateralAsset` carries `identifier`,
`issuer_name`, `facility_name`, `principal_balance`, `sp_industry`,
`fitch_industry`, `country`, `sp_rating`, `market_price_pct`, `maturity_date`
and `current_spread`.

Everything built so far answers questions about **a deal**. These two epics
answer questions about **a holder** — which is the audience actually
considering a CLO allocation.

### Why these two, and why now

**Look-through concentration is the analytic a CLO investor cannot do by hand.**
Every CLO is marketed as diversified, and the diversification is drawn from
substantially the same pool of European leveraged loans. Hold several deals and
you likely hold the same obligor several times over without knowing it — no
deal's own concentration table can tell you, because each reports only itself.
Two deals with parsed schedules is enough to demonstrate it, and the analytic
needs **no new document, no new extraction, and none of the open engine work**.
It runs on facts already parsed.

**The due-diligence record turns the platform's distinguishing property into a
deliverable.** UK Securitisation Regulation obliges an institutional investor to
verify risk retention before holding a position and to monitor it thereafter —
a supervised obligation with an evidence burden. This platform's unusual
property is that it **refuses rather than fabricates**: provenance kinds,
citations, and a named reason for every gap. For a compliance reader that is not
a caveat, it is the product. Every other tool shows green.

### Why the due-diligence record is not part of `/compliance`

`/deal/{id}/compliance` runs the covenant monitor over a deal's extracted
triggers: *is this deal within its own structural tests?* Risk retention asks
something categorically different: *have I, the holder, discharged my regulatory
duty?* Different subject, different reader, different failure mode — a deal can
pass every covenant while an investor's retention verification is undocumented,
and vice versa.

Folding them together would be the same category error this codebase has
repeatedly had to unpick: `validated` conflated with `ran` (#241), a graded
covenants row read as a reconciliation (#481), a refusal that kept its value
(#549). The two surfaces share **infrastructure** — provenance, citations, the
honest-absence discipline — and should share none of their vocabulary.

### The hard part of concentration, stated up front

**Obligor identity across deals is not given.** Cairn and Contego identify
assets by their own conventions (`LX` loan identifiers and ISINs), and the same
borrower may appear under different facility names, different identifiers, or a
different legal entity in the same group. A naive join on `identifier` will
under-count overlap and quietly report a book as more diversified than it is —
which is the exact direction of error that matters, and the direction a buyer
would be harmed by.

So the first child is identity, not aggregation, and its honest outcome may be
"these two deals share N obligors we can prove and M we cannot resolve" — with
the unresolved set named. A concentration figure that silently drops
unmatchable names is worse than no figure.

**The two industry taxonomies are a second instance of the same problem.** Each
asset carries both `sp_industry` and `fitch_industry`, and the schedules do not
agree on granularity. Aggregating across deals requires choosing one, or
mapping between them — and saying which was chosen.

### What these epics do not promise

**Not portfolio management.** Neither epic models a *position* — how much of
which tranche, bought when, at what price. Both are deal-level analytics a
holder can read. A position model is the natural next step and is deliberately
out of scope here; see the note at the end of the decomposition.

**Not regulatory capital.** No SEC-SA or SEC-ERBA risk weighting. Getting a
capital formula wrong in front of a bank is unrecoverable, and it wants a
specialist review this platform has not had.

**Not a claim that retention is verified.** The due-diligence epic produces a
*record of what was checked*, including — especially — what could not be. If a
deal's offering document does not state its retention holder in extractable
form, the record says so.

## Decomposition

### Epic: Look-through concentration across CLO holdings   (umbrella #<N>)

- **Resolve obligor identity across deals** — Establish which obligors in two
  deals' schedules are the same borrower, and name the ones that cannot be
  resolved rather than dropping them. Sequencing: parallel.
  Paths: `src/loanwhiz/primitives/**`, `tests/**`.
- **Reconcile the two industry taxonomies** — Decide and record how
  `sp_industry` and `fitch_industry` aggregate across deals, so a concentration
  figure states which taxonomy it is in. Sequencing: parallel.
  Paths: `src/loanwhiz/primitives/**`, `docs/**`, `tests/**`.
- **Compute cross-deal overlap and aggregate exposure** — Given a set of deals,
  report shared obligors, aggregate look-through exposure by name, industry,
  country and rating, and the unresolved residual. Sequencing: sequential.
  After both of the above. Paths: `src/loanwhiz/primitives/**`, `tests/**`.
- **Surface it** — An endpoint and a screen that show a holder their true
  single-name and sector concentration across the deals they hold, with the
  unresolved set visible rather than netted away. Sequencing: sequential.
  After the child above. Paths: `src/loanwhiz/api/main.py`, `web/**`, `tests/**`.

### Epic: Investor due-diligence record   (umbrella #<N>)

- **Extract the risk-retention statement** — Read the offering document's
  retention undertaking: who retains, by which Article 6(3) method, and at what
  level; refuse rather than infer when the document does not state it in
  extractable form. Sequencing: parallel.
  Paths: `src/loanwhiz/extraction/**`, `src/loanwhiz/data/deals/**`, `tests/**`.
- **Assemble the per-deal due-diligence record** — A typed record of what was
  verified, from which document, as of which date, with a citation — and a
  named reason for everything that could not be. Sequencing: sequential. After
  the child above. Paths: `src/loanwhiz/primitives/**`, `tests/**`.
- **Surface it beside governance, not beside compliance** — An endpoint and a
  screen presenting the record as an evidence file a compliance reader can take
  away, with refusals shown as prominently as verifications. Sequencing:
  sequential. After the child above.
  Paths: `src/loanwhiz/api/main.py`, `web/**`, `docs/**`, `tests/**`.

### A note on portfolio management, deliberately not an epic here

The instinct is right and the scope is wrong. "Portfolio management" for a CLO
book means position keeping, P&L, and cashflow projection — the first two are
plumbing a bank already owns, and the third depends on the engine-computed core,
which today covers 3 of 29 lines on the one deal that reconciles.

What is missing and worth building is narrower: **a position model** — a holding
of a named tranche, in a size, from a date. The platform has no concept of one;
everything is deal-level. Once positions exist, both epics above become
genuinely portfolio-grade rather than deal-grade: concentration becomes
*exposure-weighted by what you actually own*, and the due-diligence record
attaches per position, which is how the obligation attaches in law.

So the recommendation is to let the position model earn its way in as the thing
these two surfaces need, rather than to build a portfolio module and look for
uses. It is not filed here because neither epic requires it to be useful.

## Filed issues

<Filled after filing.>
