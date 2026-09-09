---
id: 2026-09-09-clo-live-deal-path
title: "Make the live deal path serve the CLO: compliance, waterfall and comparison"
status: filed
created: 2026-09-09
updated: 2026-09-09
epics: [522]
---

# Make the live deal path serve the CLO: compliance, waterfall and comparison

## Context & intent

Epic #491 established that the platform ingests, executes and grades a real
Irish CLO. Epic #510 is closing the gaps between that and a defensible
reconciliation verdict. **Neither touches the screens.** Measured against the
running API on `main`:

```
/deal/cairn-clo-xvii/compliance       422  missing required config 'class_a_rate_pct'
/deal/cairn-clo-xvii/waterfall        422  missing required config 'class_a_rate_pct'
/deal/cairn-clo-xvii/tape-analytics   200
/compare?deals=cairn-clo-xvii,green-lion-2024-1
    performance_series deals: ['green-lion-2024-1']       <- the CLO is absent
    risk_summary cairn: latest=None tightest_trigger=None
    notes: "cairn-clo-xvii: No reconstructable series —
            performance/risk unavailable for this deal."
```

So an operator opening the demo sees the CLO 422 on two screens and appear as a
column of blanks on the third. That is honest — the note says exactly why — but
it is not the platform this work has actually built.

### One root cause, three symptoms

The comparison's blanks looked like three separate problems and are not:

| section | rows | blank for Cairn |
|---|---|---|
| tranche | 8 | **0** |
| waterfall:revenue | 29 | 29 |
| waterfall:redemption | 5 | 5 |
| trigger | 11 | 11 |
| reserve | 2 | 1 |

The tranche rows are **full** — the 8-class stack renders correctly, which is
#478's N-class work showing through. And the blank rows are *Cairn's own*
labels: "Class D Interest", "Senior Management Fee", "Senior Expenses
Uncapped". The comparison already knows the deal's 29-step revenue cascade and
its 11 triggers. It has no per-period values to put in them, because there is
no reconstructable series, because of the same 422. Fix the coupon resolution
and 46 of those 55 rows fill in alongside the two screens.

### Why the 422 happens, precisely

`_reconstruct_series` dispatches on `if deal.get("tape_urls")` first. Cairn
registers three `derived+trustee-report:` tapes (#471) and one
`notes_cash_report_urls` (#495). #484's precedence contract yields to the
report path only when **every** registered tape is *synthetic* and a real
report path exists — Cairn's are `derived`, not synthetic, so it stays on the
tape path. That path calls `_resolve_structural_config`, whose three tiers are:

1. the deal's explicit `deals.json` context key — Cairn declares none;
2. the deal's extracted model, which yields `capital_structure` but **no
   coupon**, because every Cairn class quotes `INDEX + margin`;
3. a Green-Lion-only last resort, which correctly refuses a non-GL deal.

So it raises, naming `class_a_rate_pct` rather than the structure it does have
— which is #478's deliberate wording, and right.

**The number exists.** #512 wired the published all-in applied rates
(`applied_rate_class_a: 5.008`, and siblings) into the fold's rate map from
`NoteClassBalance.interest_rate_applied`. The live path simply has no tier that
reads them. Same missing number, two resolution paths, and epic #510 only
touches one.

### Why this is its own epic and not more children on #510

#510 has already gone from four children to seven, and every addition was a
gap found while closing another. This is a genuinely different surface: the
grading path reconciles a committed report against the engine offline, while
this is the live request path that serves `/deal/{id}/*` and `/compare`. They
share a missing input and nothing else. Folding it into #510 would also delay
that epic's verdict, which is the thing #491 set up.

It is also, for a demo, the higher-value work: three screens going from 422 to
populated is more visible than a reconciliation verdict that lives in a test
and a data card.

### The discipline this must not break

**A resolved coupon must never become a fabricated one.** `numeric_rate_pct`
refuses `"3 month EURIBOR + 1.80%"` deliberately, and #493 made an unresolved
coupon report `not_evaluable` rather than accrue zero. The fix is a new
*source* for a genuinely published number — the applied rate the trustee report
states for that period — not a loosening of the refusal. A period with no
published rate must still refuse, and the screens must show that refusal rather
than a plausible zero.

**A live screen must not silently prefer the weaker source.** Cairn now has
both a derived tape and a PoP-bearing report. #484's precedence contract knows
`synthetic` vs real but has no rule for `derived` vs report, so the current
choice is an accident of ordering rather than a decision. Which source a CLO's
waterfall and compliance screens should fold is a contract question, and this
epic should answer it deliberately and write it down.

### What this epic does not promise

That the CLO's numbers will be *right*. It makes the live path produce a
series and populate the screens; whether that series reconciles is #510's
question, and the two are deliberately independent. A populated screen carrying
a wrong number would be worse than a 422, so the verification child grades what
it renders against the published report and says plainly which figures are
engine-computed and which are report-supplied.

## Decomposition

### Epic: Make the live deal path serve the CLO   (umbrella #522)

- **Resolve a published applied coupon on the live structural path** — Give
  `_resolve_structural_config` a tier that reads the period's published all-in
  applied rate for a class whose prospectus coupon is a floating margin, so the
  live path resolves a real number instead of raising, and still refuses when
  no rate is published. Sequencing: parallel.
  Paths: `src/loanwhiz/api/main.py`, `tests/**`.
- **Decide and record which source a CLO's live series folds** — Extend the
  precedence contract to rank a `derived` tape against a registered
  PoP-bearing report, so the choice is a stated rule rather than dispatch
  order, and record it in `docs/data-card.md`. Sequencing: sequential.
  After the child above. Paths: `src/loanwhiz/api/main.py`, `docs/**`,
  `tests/**`.
- **Verify the three screens against the published report** — Confirm
  `/deal/{id}/compliance`, `/deal/{id}/waterfall` and `/compare` serve the CLO,
  grade the rendered figures against the trustee report, and state which are
  engine-computed and which report-supplied. Sequencing: sequential. After the
  two above. Paths: `tests/**`, `docs/**`.

## Filed issues

- Epic "Make the live deal path serve the CLO" → umbrella **#522**
  - #523 Resolve a published applied coupon on the live structural path — parallel, `liz:priority:1`
  - #524 Decide and record which source a CLO's live series folds — sequential, after #523
  - #525 Verify the three CLO screens against the published report — sequential, after #524
