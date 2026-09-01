---
id: 2026-09-02-re-baseline-before-clo
title: Re-baseline LoanWhiz before the CLO breadth bet
status: draft
created: 2026-09-02
updated: 2026-09-02
epics: []
---

# Re-baseline LoanWhiz before the CLO breadth bet

## Context & intent

This plan came out of a 2026-09-02 question — *"do we handle CLOs, and what
would it take to add them?"* — and the backlog check the operator asked for
before committing to that work. The answer to the first question is no (RMBS
only, three jurisdictions; `docs/data-card.md` and `docs/model-card.md` say so
plainly). The answer to the second turned out to depend on facts the
conversation could not establish until the repo was actually re-read, and the
re-read is what produced this plan.

### What the backlog check found

**The GitHub backlog is genuinely empty.** 211 issues, all 211 closed. Zero
open PRs. Zero `TODO`/`FIXME`/`HACK` markers in `src/`, `web/`, or `mcp/`. The
fleet drained the whole productionising plan (#390–#393) and the ground-truth
quality engine (#425) through to promotion; `main` sits at `98311ab`
(2026-07-15).

So the remaining work is not tracked as issues. It lives in three places, and
all three point at the same shape of problem: **shipped code that never reached
the committed data, and honesty docs that have drifted behind the code.**

**1. The IT/ES seeds are pre-fix artifacts.** Epic A of the productionising
plan fixed exactly two named defects — `#396` (non-standard waterfall section
routing: "Sol-Lion ES revenue PoP = 0 steps") and `#397` (structure-agnostic
tranche parsing: "Sol-Lion Class O = 42 EUR artifact"). Both shipped on
2026-06-24. But a seed is *materialised extraction output*, and neither seed
was re-extracted afterwards, so both still carry the artifacts verbatim:

| seed | `tranche_structure` | revenue steps |
|---|---|---|
| `green-lion-2023-1-bv` | 3 tranches, sized + rated | 11 |
| `green-lion-2024-1-bv` | 3 tranches, sized + rated | 11 |
| `green-lion-2026-1-bv` | 3 tranches, sized + rated | 11 |
| `leone-arancio-rmbs-2023-1-srl` | **1** — `Class A`, `size_eur: null` | 23 |
| `sol-lion-ii-rmbs-fondo-de-titulización` | **1** — `Class O`, `size_eur: 42.0`, `seniority: 14` | **0** |

This is the cheapest possible correctness win in the repo: the fix is already
written, tested and merged. It just has to be *run*, and the output committed.
Until it is, every downstream surface — the capability matrix, `/compare`, the
quality matrix, the Showcase view — reports the IT/ES deals against a capital
structure that does not exist.

**2. The ground-truth quality engine is grading 1 deal out of 5.** Epic #425
exists precisely so that quality grading *auto-scales* — `quality_harness.py`
enumerates the whole `DEAL_REGISTRY` and grades each deal against its own
published figures, replacing the hand-built `_VALIDATION_BUILDERS` one-off.
Child #429 was scoped to "backfill answer keys for existing deals with
published reports (Green Lion vintages at minimum)". It committed exactly one:
`green-lion-2024-1-bv.json`. Green Lion 2023-1 has **three published quarterly
Notes & Cash reports** already sitting in `deals.json` and no answer key
against them. It is the only remaining deal that can get one — Leone Arancio
and Sol-Lion II carry investor reports but **zero** Notes & Cash reports, so
`DealAnswerKey.from_notes_cash_report(...)` has nothing to read for them, and
the #193 honesty discipline correctly forbids inventing a key. So the backfill
has exactly one deal left in it, and it doubles the graded coverage.

**3. `SYSTEM-STATUS.md` is three weeks stale and one of its six limitations is
already false.** The doc is dated 2026-06-24; epics #425 and #392 promoted on
06-28 and 07-15. Its item 2 states that `/compare` "does not rank deals or emit
a relative-value verdict" and that the screener "is registered but reached by no
endpoint" — but #400 wired `relative_value_screener` into `api/compare.py`,
which now returns a `RelativeValueScorecard` and a `ComparativeVerdict`. A
document whose entire purpose is to be the honest boundary statement cannot
carry a stale limitation; that is the one failure mode it must not have.

### Why this shape, and why before CLO rather than after

The CLO question is a **breadth** bet: a sixth deal in a new asset class, needing
an Annex 4 / corporate loan-level schema, OC/IC coverage tests with their
cash-diversion semantics, collateral quality tests (WARF, diversity score,
obligor/industry concentration), a real reinvestment period, and an incentive
fee on an equity IRR hurdle. That is substantial, and the engine is in better
shape to receive it than expected — `#394` already broadened `RecipientType` /
`MetricType` for the global ABS universe (the `class_d_interest` member is
commented `# deeper-stack interest (auto/consumer/CLO)`), `#397` generalised
tranche parsing, and `#426` made the waterfall path consume each deal's own
extracted steps.

But breadth is only worth buying if the platform can *measure* it. Right now it
cannot: the quality matrix grades one deal, and two of the five existing deals
are being graded against extraction artifacts rather than their real capital
structures. Adding a CLO into that state means `/quality-matrix` gets a sixth
column it cannot honestly grade, and the project's core credibility claim —
"reproduced a real deal to the cent" — gets diluted rather than extended.

The alternative shape we rejected was **CLO first, re-baseline alongside**. It
is tempting because the CLO work is the interesting work and the re-baseline is
mostly re-running existing pipelines. We rejected it because the three items
here are unusually cheap relative to their leverage: two are re-running code
that is already merged, one is authoring a JSON file from reports that are
already in the registry. Deferring them does not buy meaningful time, and it
means the CLO work would be validated against a baseline that is known-wrong in
two of five columns. Land the baseline, then spend real effort on breadth.

This plan therefore covers **only the re-baseline**. The CLO scoping stays in
the conversation and gets filed as its own plan once this lands — it is the
explicit next leg, not part of this one.

### Deliberately out of scope

- **CLO / new asset class support.** The next plan, gated on this one.
- **CI.** There is no `.github/` directory at all — the repo has zero CI, and
  the ground-truth quality plan says so outright ("no CI workflows exist, so
  the test suite is the only gate"). There is also no `--cov-fail-under`. This
  is a real gap and a real finding, but it is infrastructure work the operator
  has not scoped, and folding it in unasked would widen this plan past what was
  agreed. Raised here so it is not lost.
- **The remaining true `SYSTEM-STATUS.md` limitations** — single-period
  `/project`, the in-process `/extract` job store, no inline cold-extract on
  first view, non-evaluable PDL/reserve proximity. These stay documented as
  boundaries; this plan corrects the doc, it does not close the boundaries.

### Cross-epic ordering

One epic, four children. The two seed re-extractions and the answer-key
backfill are mutually independent and run in parallel. The `SYSTEM-STATUS.md`
refresh runs last, because it is the doc that has to describe the end state —
re-dating it before the seeds and the answer key land would just create a
second stale doc.

Both re-extractions are LLM-dependent (Docling + Vertex, ~20–37 min per deal,
needs GCP ADC) and so are the slow leg; the answer-key backfill is offline and
deterministic and should finish well ahead of them.

## Decomposition

_(Filled in phase 2.)_

## Filed issues

_(Filled in phase 4.)_
