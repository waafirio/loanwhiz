---
id: 2026-09-02-clo-structural-spine
title: CLO structural spine — generalise the engine off RMBS
status: decomposed
created: 2026-09-02
updated: 2026-09-02
epics: []
---

# CLO structural spine — generalise the engine off RMBS

## Context & intent

This plan answers a question asked on 2026-09-02: *"do we handle CLOs?"* The
answer was no — LoanWhiz is RMBS-only across three jurisdictions, and
`docs/data-card.md` says so plainly ("CLOs, CMBS, US RMBS, ABS, and other asset
classes are not represented"). This is the first deliberate step off that
boundary.

It is filed **after** the re-baseline epic (#437, promoted 2026-09-02) and is
gated on it by design: adding a sixth deal in a new asset class was not worth
doing while `/quality-matrix` graded one deal out of five and two of the five
were being graded against extraction artifacts. That is now fixed.

### What the engine already gives us, and what it does not

The good news is that the execution spine is genuinely asset-class agnostic and
carries over intact:

- `primitives/waterfall_interpreter.py` walks an ordered `StepSpec` list, paying
  each recipient from a running pot, resolving need through an **open registry**
  (`NEED_CALCULATORS`, extended by a `@register_need` decorator). Adding a
  recipient is a decorated function, not a core edit.
- The condition→predicate seam already gates steps on prose conditions and
  drives the sequential↔pro-rata branch. Coverage-test gating plugs in here.
- `extraction/taxonomy.py` maps free-string recipients onto the closed enum with
  a deterministic alias table, an LLM fallback, and an explicit `unmapped`
  escape, so unknown CLO vocabulary degrades to `not_evaluable` rather than
  mis-mapping.
- `#394` already broadened `RecipientType` for the global ABS universe — the
  `class_d_interest` member is commented `# deeper-stack interest
  (auto/consumer/CLO)`.
- `#397` generalised tranche parsing for exotic stacks, and `#426` made the
  waterfall path consume each deal's own extracted steps.

The gaps, verified against `main` @ `78d2c53` rather than assumed:

- **`domain/` carries exactly one annex table** — `esma_annex2.py`, RMBS only.
  `esma_tape_normaliser.py:63-66` can *detect* Annex 5 (Auto) and Annex 8 (SME)
  by sentinel column, but no field table exists for either, and nothing covers
  the corporate/leveraged-loan template a CLO's collateral reports on. That
  detection list is also worth a second look: under the ESMA securitisation
  disclosure RTS the corporate template (covering SME and leveraged loans) is
  generally understood to be **Annex 4**, with `CRPL` field codes, while Annex 8
  is leasing — so the existing `"Annex 8 (SME)"` label may be wrong, and it sits
  exactly where CLO work lands.
- **`MetricType` has 9 members and none of them is a coverage test.** No
  overcollateralisation ratio, no interest coverage ratio.
- **`RecipientType` has 23 members but `NEED_CALCULATORS` has only 9 registered
  recipients.** #394 broadened the vocabulary without adding calculators, so a
  deep stack maps `class_d_interest` correctly and then computes need 0. This is
  latent today because no committed deal has a stack that deep; a CLO would hit
  it immediately.
- **Zero occurrences** of `obligor`, `WARF`, `diversity score`, `coverage test`,
  or `reinvestment period` anywhere in `src/`, `docs/` or `data/`.
- `pool_stratification.py:63` hardcodes `Dimension = Literal["ltv", "seasoning",
  "region", "rate_type"]` — all RMBS attributes.
- `DealState` carries reserve balance/target, cumulative losses and pool factor.
  It has no par value, no coverage-test state, and `revolving` is a bare bool.

### Why "structural spine only", and why nothing is promised to the cent

Two operator decisions shape this plan, and both narrow it deliberately.

**There is no CLO data yet.** No prospectus, no trustee reports, no loan-level
tape. Sourcing is therefore part of the work rather than a precondition. The
consequence is load-bearing and must not be quietly forgotten later: **this plan
promises no to-the-cent validation.** The project's credibility rests on
"reproduced a real deal to the cent," and that claim requires published actuals
to reconcile against — for a CLO that means trustee reports, which are typically
vendor or investor-portal material rather than free EDW downloads. Every cell
this plan produces is an honest `ran`, never a `validated`, and
`/quality-matrix` will not grade a CLO until an answer key exists. The #193
honesty discipline forbids inventing one.

**Scope stops at the structural spine.** Capital stack, both waterfalls, the
coverage-test *definitions*, and the corporate loan-level schema — enough to
prove the engine generalises off RMBS. Explicitly **not** in this plan:

- **Coverage-test cash diversion.** In a real CLO, OC/IC failure does not merely
  record a breach — it *redirects* interest and principal proceeds to redeem
  notes sequentially until the test cures. That is a solve-to-target need
  calculator and a cross-waterfall interlock, unlike every existing calculator
  which is closed-form (`balance × rate × days/360`). It is the heart of a CLO
  and the single hardest piece, and it is the natural **next** increment once
  the spine holds. Deliberately deferred so it can be scoped against a real
  extracted deal rather than in the abstract.
- **Reinvestment period, trading, par build/loss.** `DealState.revolving` is a
  bool; a real reinvestment period needs trade events, and collections are
  currently the only way the pool mutates.
- **Collateral quality tests** — WARF, weighted-average spread/recovery,
  Moody's diversity score, obligor/industry concentration limits. The
  concentration-limit shape in `pool_stratification.py`
  (`{dimension, bucket, max_pct, basis}`) fits industry/obligor directly, but
  WARF and diversity are weighted-average and combinatorial scores that do not
  fit the marginal-share model at all.
- **Incentive management fee on an equity IRR hurdle**, which needs a running
  equity cashflow IRR in `DealState`.
- **CCC-excess haircuts and defaulted-asset carrying values**, which need
  asset-level ratings and market prices the schema will not yet hold.

The rejected alternative was **full CLO support in one plan**. It is a coherent
end state, but it front-loads the hardest mechanic (cash diversion) and the
most data-hungry work (quality tests, haircuts) before anything has proved the
engine even ingests a CLO document. Landing the spine first makes the diversion
work scopeable against a real extracted deal, and makes the data gap visible
early rather than three epics deep.

### One calibration carried forward from epic #437

The re-baseline epic is the reason to distrust an optimistic estimate here. Its
plan assumed #396/#397 had already fixed IT/ES extraction and the seeds merely
needed re-running. A real cold re-extraction proved that false on **both** deals:
Docling emitted every heading of the Spanish prospectus at one markdown level,
making #396's widening a no-op by construction; the Italian prospectus stated its
capital structure in prose rather than a table; #397's `A–G` letter bound
excluded a real Class J; and a latent `_seniority_for` bug had been reading the
"C" in "Class". Two of two deals needed extractor fixes, not re-runs.

So **extraction generality is weaker than the merge status of the June epics
implied**, and a CLO offering memorandum — longer, differently structured, and
with a capital stack far deeper than three classes — is a harder target than any
RMBS prospectus tried so far. Epic B's extraction child should be estimated with
that in mind, and a plan that comes back saying "the extractor needs work first"
is a success of this discipline, not a failure of the child.

### Cross-epic ordering

Two epics, six children.

**Epic A (engine foundations) has no data dependency** and can run to completion
before a single CLO document exists. That separation is the point: if sourcing
stalls — and it may, since CLO documents are the scarce input — Epic A still
lands and still promotes.

**Epic B (deal onboarding) needs both.** Its sourcing child is independent of
Epic A and is the **long pole**, so it should start immediately and in parallel;
its extraction and evaluation children need both the sourced documents and Epic
A's schema and vocabulary.

Nothing here touches the promotion gate, the honesty discipline, or the
capability matrix's three-state vocabulary. A CLO column appears in the matrix
only with real, honest per-cell reasons.

## Decomposition

Two epics, six children. Epic A's three children are mutually independent and
need no CLO data. Epic B's sourcing child is also independent and should start
immediately — it is the long pole — while its extraction and evaluation children
wait on both the documents and Epic A.

### Epic A: CLO engine foundations   (umbrella #<N>)

Everything the engine needs to *represent* a CLO, built and testable without a
single CLO document. Each child is additive to a closed vocabulary or an open
registry, in the style those modules already establish — none of this is
architectural. The epic is done when the canonical schema can express a CLO's
capital stack, its fee waterfall recipients, and its coverage tests, with
per-recipient need actually computable for a stack deeper than three classes.

- **Corporate / leveraged-loan loan-level field table** — Add the corporate
  underlying-exposure field table alongside `esma_annex2.py` in the same
  `Annex2Field` record shape (which its own docstring says "admits other annexes
  later without breaking callers"), covering the obligor, industry, facility
  rating, seniority, spread, and defaulted/PIK/cov-lite attributes CLO
  collateral reports carry; wire annex detection to it and verify the existing
  `"Annex 8 (SME)"` label against the actual RTS annex numbering, correcting it
  if wrong. Sequencing: parallel. Paths: `src/loanwhiz/domain/**`,
  `src/loanwhiz/primitives/esma_tape_normaliser.py`, `tests/**`.
- **Coverage-test metrics in the canonical taxonomy** — Add
  overcollateralisation and interest-coverage ratio metrics to `MetricType` with
  the alias coverage the taxonomy classifier needs, and make them computable
  from deal state so an extracted OC/IC trigger resolves to a real value instead
  of `unmapped`; per-class, since a CLO tests coverage at several attachment
  points. Explicitly excludes the cash-diversion mechanic. Sequencing: parallel.
  Paths: `src/loanwhiz/domain/rules.py`, `src/loanwhiz/extraction/taxonomy.py`,
  `src/loanwhiz/primitives/covenant_monitor.py`, `tests/**`.
- **Close the recipient-enum vs need-calculator gap** — `RecipientType` carries
  23 members while `NEED_CALCULATORS` registers 9, so deeper-stack recipients
  (`class_d/e/f_*`) and the CLO fee recipients map correctly and then compute
  need 0; register the missing calculators and add the senior / subordinated
  management-fee recipients a CLO waterfall pays. Excludes the incentive fee,
  which needs an equity IRR that is out of scope. Sequencing: parallel. Paths:
  `src/loanwhiz/primitives/waterfall_interpreter.py`,
  `src/loanwhiz/domain/rules.py`, `src/loanwhiz/extraction/taxonomy.py`,
  `tests/**`.

### Epic B: CLO deal onboarding   (umbrella #<N>)

Take one real CLO from documents to an executable, honestly-graded deal model.
This epic is where the data gap bites and where the #437 calibration applies:
the extraction child is expected to be hard, and a plan that concludes the
extractor needs work before the deal can be modelled is the right outcome, not a
failure.

- **Source and register a CLO deal document set** — Identify a European CLO with
  a publicly obtainable prospectus / offering memorandum, register it in
  `deals.json` in the existing deal-context shape, and record honestly in the
  data card which documents are and are not available (in particular whether any
  trustee reports could be obtained, since their absence is what forecloses
  to-the-cent validation). No extraction. Sequencing: parallel. Paths:
  `src/loanwhiz/data/deals.json`, `docs/data-card.md`, `src/loanwhiz/config.py`.
- **Extract the CLO prospectus to canonical `DealRules`** — Run the extraction
  pipeline against the sourced document and get an honest deal model out: the
  full capital stack, both the interest and principal waterfalls, and the
  coverage-test definitions as extracted triggers; fix the extractor where a CLO
  document defeats it rather than hand-editing a seed, exactly as #438/#439
  established. Sequencing: sequential. After the sourcing child. Paths:
  `src/loanwhiz/extraction/**`, `src/loanwhiz/data/deals/seed/**`, `tests/**`.
- **Execute and grade the CLO deal honestly** — Run the extracted model through
  the engine, evaluate its coverage tests as covenant metrics, and let the
  capability matrix report the result with real per-cell reasons — `ran` where
  it ran, `not-applicable` with a genuine reason where it did not, and never
  `validated`, since no answer key exists without trustee reports. Sequencing:
  sequential. After the extraction child. Paths:
  `src/loanwhiz/primitives/capability_matrix.py`, `src/loanwhiz/api/main.py`,
  `docs/**`, `tests/**`.

## Filed issues

_(Filled in phase 4.)_
