---
id: 2026-09-08-clo-run-and-validate
title: Run and validate the CLO — N-class structural config, trustee-report facts, a CLO answer key, and labelled synthetic pools
status: draft
created: 2026-09-08
updated: 2026-09-08
epics: []
---

# Run and validate the CLO — N-class structural config, trustee-report facts, a CLO answer key, and labelled synthetic pools

## Context & intent

This plan came out of walking the demo UI on 2026-09-08, deal by deal, after epic
#468 landed the derived CLO loan tape. Three screens told us something was still
wrong, and investigating each one produced a different — and in two cases
better — answer than the symptom suggested.

### What the screens actually said

**Waterfall and Compliance refuse for the CLO**, with:

> missing required config `capital_structure`… **Refusing to fall back to Green
> Lion 2026-1's numbers**

**Projection refuses too**, with the same shape for `projection_base`.

**The comparison charts are meaningless** for four of six deals, because only
Green Lion 2026-1 and the CLO have loan tapes at all.

### The first diagnosis was wrong, and the correction matters

The obvious reading of the refusal — and the one first given to the operator —
was that the coupon strings are the blocker: the extracted tranches carry
`'3 month EURIBOR + 1.80%'` rather than a number, and `_numeric_rate_pct`
deliberately refuses to coerce a reference rate. The proposed fix was "teach it
to parse INDEX + margin".

Reading the code disproved that on three counts:

1. **The coupon is not the first blocker.** `_extracted_capital_structure` maps
   tranches by seniority and looks for exactly `0, 1, 2`. Cairn's seniorities are
   `0, 101, 102, 200, 300, 400, 500, 2600`, so `_balance(1)` and `_balance(2)`
   return `None` and the function bails **before** `_numeric_rate_pct` is
   consulted. Fixing coupon parsing alone changes nothing.
2. **The shape is the real constraint.** `capital_structure` is a four-field,
   three-class dict — `class_a_balance`, `class_a_rate_pct`, `class_b_balance`,
   `class_c_balance`. Cairn has **eight** classes. There is no honest way to
   express A / B-1 / B-2 / C / D / E / F / Subordinated in it; hand-writing one
   into `deals.json` would mean choosing five classes to discard. Meanwhile
   `DealState` already carries `tranches: list[TrancheState]` — #363 generalised
   the engine onto an N-class list. So this is a **legacy RMBS-shaped config
   adapter sitting in front of an already-general engine**.
3. **Parsing INDEX + margin was the wrong fix entirely.** A margin is not a rate;
   resolving one needs the index fixing for the period, and inventing that is
   exactly what the current refusal exists to prevent.

`projection_base` is the same illness one step further along:
`_resolve_projection_base` reads `deal.get("projection_base")` and raises. It has
**no extracted-model path at all**.

### The trustee report already answers the questions we were about to guess at

Rather than parse a margin and invent an index, the deal's own monthly trustee
report states the resolved facts outright. From the March 2025 report's Executive
Summary:

| class | balance | current coupon | periodic interest |
|---|---|---|---|
| A | 248,000,000.00 | **4.544%** | 2,066,005.33 |
| B-1 | 24,600,000.00 | 5.494% | 247,779.40 |
| B-2 | 15,000,000.00 | 6.870% | 200,375.00 |
| C | 23,100,000.00 | 6.344% | 268,668.40 |
| D | 26,500,000.00 | 8.044% | 390,804.33 |
| E | 17,200,000.00 | 10.204% | 321,766.13 |

Totalling 404,100,000.00 — matching the extracted capital structure exactly.

**And it carries the coverage-test thresholds** that #456 recorded as
uncapturable. That finding was correct about the offering circular — its glossary
is alphabetical and the definitions extractor truncated at 40k, so every test
defined under C–F was lost — but it was looking in the wrong document. The
trustee report states them plainly, alongside the tests' own computed results:

- Par Value: A/B **139.43%** vs required **130.08%**; C 129.07% / 121.74%;
  D 118.92% / 112.62%; E 113.15% / 107.87%; F 108.68% / 103.90%; Reinvestment OC
  108.68% / 104.40%
- Interest Coverage: A/B **184.35%** vs **120.00%**; C 166.55% / 110.00%;
  D 146.04% / 105.00%

### Which makes a validated CLO cell reachable, not just feasible

Epic #454 shipped with every CLO cell honestly `not-applicable`, and its umbrella
was corrected on 2026-09-05 to say that a validated cell is *feasible* — the
documents exist — while none was pursued. This plan pursues it, and the format
needs no new shape:

`DealAnswerKey` already carries `revenue_pop`, `redemption_pop` **and**
`covenants: list[CovenantResult]` for "published covenant / trigger test
results", and `quality_harness` already grades `covenants_matched` /
`covenants_graded`. `from_notes_cash_report` is one **constructor**, not the
schema. So the CLO needs a sibling constructor, not a new format — and there are
**two independent routes to ground truth**: the Note Valuation Report's Interest
and Principal Priorities of Payments, and the trustee report's stated test
results with thresholds.

### On synthesising pool data — the concern, and why it is nevertheless sound

The operator asked for synthetic data on the deals lacking loan tapes, so the
comparison charts stop being meaningless. On its face this collides with the
#193 honesty discipline that this whole codebase is built around.

It does not, for one reason: **synthetic tapes are already established, labelled
practice here.** Green Lion 2026-1's three tapes are synthetic — the filenames
say so, and `docs/data-card.md` carries a bold *"The loan-level data (loan tapes)
in this dataset is SYNTHETIC"* section. The request is to extend an existing
practice to four deals that have no pool data at all, not to start fabricating.

**But there is a real gap, and it must be closed first.** Running the existing
synthetic Green Lion 2026-1 tape through the normaliser today returns:

```
loan_count : 3237
data_source: 'direct'
```

`direct` — the same provenance a real regulatory tape reports. The word
"synthetic" lives in the filename and in a document; it does **not** live in the
layer the evidence pack, the governance surface and the capability matrix
actually read. So the platform currently cannot tell a synthetic pool from a
filed one at the point where it makes claims.

Adding four more synthetic tapes on top of that would multiply the gap fivefold.
Closing it first is cheap, because #470 already built the mechanism: a derived
tape is identified by a URI scheme (`derived+trustee-report:`) and `TapeSourceKind`
is a required, defaulted-nowhere type. `synthetic` slots in beside `derived`.

Two safeguards are therefore non-negotiable in this plan:

1. **Synthetic provenance must be structural**, visible wherever `data_source`
   is — not a filename convention and not a doc footnote.
2. **Synthetic pool data must never reach a `validated` cell.** Green Lion
   2024-1 is the one validated deal in the repo; its validation is
   Notes & Cash PoP reconciliation, not pool statistics, so the two are
   separable — but that separation must be *pinned by a test*, not assumed.

### Rejected alternatives

**Hand-write a `capital_structure` into `deals.json` for the CLO.** Fastest, and
wrong: it forces a choice of which five of eight classes to discard, and leaves
the next deep-stack deal in exactly the same place. It fixes the instance and not
the class.

**Parse `INDEX + margin` and source an index fixing.** Real work with real
guessing in it, when the trustee report states the resolved coupon directly.

**Skip the provenance fix and just add synthetic tapes.** Would make the charts
render and quietly make the platform's central claim — that it never presents
fabricated data as real — false in five places instead of one.

### Ordering

**Epic A is one sequential chain** rather than two epics, deliberately. Epics
#450 → #454 taught that a hard cross-epic dependency stalls the downstream epic
at the upstream one's *promotion gate*; keeping the config work and the
validation work in one epic avoids reproducing that.

**Epic B is fully independent** of Epic A and can run alongside it — it touches
the tape provenance layer and the RMBS deals, not the CLO's structural config.

## Decomposition

_(Filled in phase 2.)_

## Filed issues

_(Filled in phase 4.)_
