---
id: 2026-09-09-contego-onboarding
title: "Onboard Contego CLO XI: a second CLO, and a second trustee-report family"
status: decomposed
created: 2026-09-09
updated: 2026-09-09
epics: []
---

# Onboard Contego CLO XI: a second CLO, and a second trustee-report family

## Context & intent

The platform has one CLO. Every CLO-shaped thing it can do — the derived Annex 4
tape (#471), the collateral-schedule parser (#469), the Note Valuation Report
parser (#494), the coverage-test answer key (#481) — was built against Cairn CLO
XVII, whose reports are **U.S. Bank**. A single specimen cannot tell a general
capability from a fitted one, and the comparison screen with one CLO in it is a
column next to three RMBS deals.

**Contego CLO XI DAC** (Five Arrows, issuer 29975 on Euronext Dublin) is the
right second deal, and its documents are verified downloadable — I fetched three
of the URLs directly and each returns `HTTP 206 application/pdf`, no login:

| document | date | pages |
|---|---|---|
| Listing Particulars | 29-Jun-2023 | 415 |
| Listing Particulars (**reset**) | 19-Nov-2024 | 421 |
| COMPLIANCE REPORT | 30-Aug-2024 | 74 |
| COMPLIANCE REPORT | 30-Sep-2024 | 74 |
| NOTE VALUATION REPORT (pmt) | 20-Aug-2024 | 86 |

Reinvestment ends 22-Nov-2027, adjusted collateral EUR 375.7m, Class A 5.41%.

### Why this deal and not the alternatives

**It changes exactly one variable: the collateral administrator.** Cairn is U.S.
Bank; Contego is **BNY Mellon**, whose reports open with a Client Service Manager
block and disclaimer rather than the `Global Corporate Trust www.usbank.com/clo`
header. At 74–86pp they are the same order as Cairn's 68–90pp, so when a parser
assumption breaks, the break is diagnosable rather than buried in a 250pp
document. It also has a **Note Valuation Report** — the PoP-bearing document —
which is what makes a second *validated* CLO conceivable later.

Rejected, with reasons: **Palmer Square European CLO 2023-1** (Citi/Virtus) has
250pp+ monthlies and a EUR 80.97m Class A *Loan* pari passu with the notes — a
structure the platform has never modelled, so it changes two variables at once.
**Contego CLO V** shares XI's administrator, so it exercises nothing new.
**Nassau Euro CLO II** qualifies on documents but its Listing Particulars is the
February 2025 reset stack while both payment reports predate the reset —
registering them together would silently compare a 2039 capital structure to
reports describing the old one. **Carlyle Euro CLO 2013-1** is the only candidate
exercising the post-reinvestment waterfall (reinvestment ended 05-Apr-2021), but
no offering document has been located, so it is not registrable yet.

### The trap this epic must not walk into

Contego XI carries the **same reset hazard as Nassau, in a milder form**, and it
is the single most likely way to get this deal silently wrong. There are two
Listing Particulars: the original **29-Jun-2023** and a **19-Nov-2024 reset**.
All three available reports are from **August and September 2024** — *before* the
reset. So the **2023** document is the one that describes the capital structure
those reports report on. Registering the newer, more obvious-looking document
would produce a seed whose tranches, coupons and tests belong to a different
stack than the reports being parsed against it, and every downstream figure would
be confidently wrong rather than visibly broken.

Whichever is registered, the seed and the reports must be from the same side of
the reset, and the reason must be written down where the next registrant meets it.

### Why the parser work is a generalisation, not a second parser

This is the load-bearing design decision, and it is the standing constraint
applied: *creating, exposing and enforcing clear contracts and maximising code
re-use is key to not eventually having to play whack-a-mole.*

`collateral_schedule_parser` and `note_valuation_parser` were written against one
document family and carry U.S. Bank's section titles and layout assumptions.
Copying them into BNY-shaped twins would ship a working second deal and guarantee
a third fork for the third administrator. `.claude/euronext-doc-api.md` already
records four distinct families — U.S. Bank, BNY Mellon, Deutsche Bank, Citi +
Virtus — so the third and fourth are not hypothetical.

The right shape is a **dispatch on report family**: a detector that identifies the
family from the document's own header, and per-family section-title and layout
tables behind one interface, exactly as `_ANNEX_SIGNATURES` dispatches tape
annexes today. Adding an administrator should then be a registration, not a fork.
That child comes first, and Cairn must stay byte-identical through it — a
generalisation that changes the existing deal's output is a rewrite wearing a
generalisation's clothes.

### Cross-epic dependency

The comparison verification depends on **epic #522** (make the live deal path
serve the CLO). Until #523 lands, any CLO 422s on `/deal/{id}/compliance` and
`/deal/{id}/waterfall` and appears in `/compare` as a column of blanks — so
onboarding a second CLO before that would produce two blank columns instead of
one. This epic can be built in parallel; only its final child needs #522 landed.

### What this epic does not promise

**That Contego will be validated, or even graded.** It onboards the deal: seed,
tape, and an answer key from the published coverage tests by #481's route. Whether
its engine reconciles to its Note Valuation Report is the same question #510 is
still answering for Cairn, and deliberately out of scope here — attempting both at
once would repeat the mistake of learning what "validated" costs while also
learning what a new report family costs.

The success condition is narrower and honest: a second CLO that ingests, extracts
and appears in the comparison with real figures, whose parser path is shared with
the first rather than forked from it.

## Decomposition

### Epic: Onboard Contego CLO XI   (umbrella #<N>)

- **Dispatch the trustee-report parsers on report family** — Generalise
  `collateral_schedule_parser` and `note_valuation_parser` off their U.S. Bank
  layout assumptions onto a family-detected dispatch, so a new administrator is a
  registration rather than a fork, with Cairn byte-identical. Sequencing: parallel.
  Paths: `src/loanwhiz/primitives/collateral_schedule_parser.py`,
  `src/loanwhiz/primitives/note_valuation_parser.py`, `tests/**`.
- **Register Contego CLO XI and extract its Listing Particulars** — Add the deal
  to the registry against the **29-Jun-2023** document (pre-reset, matching the
  Aug/Sep 2024 reports) and commit its extracted seed model. Sequencing: parallel.
  Paths: `src/loanwhiz/data/deals/**`, `tests/**`.
- **Parse Contego's BNY reports and derive its Annex 4 tape** — Read the two
  COMPLIANCE REPORTs and the NOTE VALUATION REPORT through the family dispatch,
  and register the derived tape through the existing `derived+trustee-report:`
  channel. Sequencing: sequential. After both of the above.
  Paths: `src/loanwhiz/primitives/**`, `src/loanwhiz/data/deals/**`, `tests/**`.
- **Author Contego's answer key from its published coverage tests** — Commit a key
  by #481's `from_trustee_liability_summaries` route, excluding any test the report
  states as `N/A` rather than coercing it. Sequencing: sequential. After the child
  above. Paths: `src/loanwhiz/data/deals/answer_keys/**`, `tests/**`.
- **Verify two CLOs compare** — Confirm `/compare` renders both CLOs with real
  figures rather than blanks, and record in `docs/data-card.md` what the second
  deal demonstrates about generality that the first could not. Sequencing:
  sequential. After the child above, and after epic #522.
  Paths: `tests/**`, `docs/**`.

## Filed issues

<Filled after filing.>
