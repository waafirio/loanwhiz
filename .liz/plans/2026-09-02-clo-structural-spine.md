---
id: 2026-09-02-clo-structural-spine
title: CLO structural spine — generalise the engine off RMBS
status: filed
created: 2026-09-02
updated: 2026-09-05
epics: [450, 454]
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

**There was no CLO data when this plan was written.** No prospectus, no trustee
reports, no loan-level tape. Sourcing was therefore made part of the work rather
than a precondition.

> ### ⚠ CORRECTION — 2026-09-05
>
> **The paragraph that stood here asserted that no to-the-cent CLO validation
> was possible. That assertion was wrong.** It is preserved below because a plan
> is a record of what was believed and why, and quietly rewriting it would hide
> the mistake rather than fix it.
>
> The original claim was:
>
> > this plan promises no to-the-cent validation. The project's credibility rests
> > on "reproduced a real deal to the cent," and that claim requires published
> > actuals to reconcile against — for a CLO that means trustee reports, which
> > are *typically vendor or investor-portal material rather than free EDW
> > downloads*. Every cell this plan produces is an honest `ran`, never a
> > `validated`.
>
> The emphasised clause was an **assumption, never verified**. Issue #455 checked
> it by going and looking: **six Euronext Dublin URLs return `200
> application/pdf` with no authentication**, and their text was read rather than
> trusted — three U.S. Bank monthly trustee reports and an **83-page Note
> Valuation Report carrying both an Interest Priority of Payments and a Principal
> Priority of Payments**. That is precisely the Notes & Cash analogue that let
> Green Lion 2024-1 and 2023-1 reconcile to the cent.
>
> **A validated CLO cell is therefore feasible.** Nothing in the data forecloses
> it. Issues #454 and #457 carried the same false claim and were corrected on the
> same date.

**What remains true, for a narrower reason.** This plan still does not pursue a
validated cell and still authors no answer key — because that is a **deferred
scope decision**, and the sensible moment to take it is after #456 has shown the
extractor can read a CLO offering circular at all. Until a key is committed,
`/quality-matrix` does not grade a CLO and its cells read `ran` or
`not-applicable` — **because none has been authored yet, not because none is
possible.** The #193 honesty discipline is untouched: no answer key may be
invented. "Feasible" means the source documents exist and can be read, not that a
key may be assumed.

**The lesson worth keeping.** The premise was stated confidently, written into
two issue bodies and this plan, and survived until a worker was instructed to
verify rather than assume. It is the same failure this plan's own §"calibration"
section warns about, committed while writing that section.

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

### Standing constraints (operator-set, 2026-09-02) — every child obeys these

Four rules govern *how* each child is built, not what it delivers. They are
repeated on every filed issue because they are the difference between a CLO
bolt-on and a platform step.

**1. Reuse and adapt before adding.** LoanWhiz already carries a lot of working
machinery, and the default is to extend it rather than stand up something
parallel. Concretely: coverage tests are **triggers**, so they go through the
existing `TriggerRule` / `covenant_monitor` path rather than a new coverage
subsystem; a new recipient is a `@register_need` decorated function on the
existing open registry, not a new execution branch; a new loan-level template is
another table in the existing `Annex2Field` record shape; obligor and industry
concentration limits reuse `pool_stratification`'s existing
`{dimension, bucket, max_pct, basis}` limit shape. A child that proposes a new
parallel subsystem where an existing seam would serve should say why in its plan
and expect that to be challenged. The engine collapse of #276 — three duplicate
execution paths deleted down to one `run_period` fold — is the standing example
of what this rule exists to prevent recurring.

**2. Create, expose and *enforce* clear contracts — the alternative is
whack-a-mole.** Reuse alone is not enough; the reason to keep one path is that
one path can carry one enforced contract. So for each seam a child touches:
define the contract in exactly one place, expose it as a typed, discoverable
thing rather than a convention, and **enforce it at the boundary** so a
violation fails loudly there instead of surfacing as a wrong number three layers
away. This codebase already has the good examples to copy: the closed
`RecipientType` / `MetricType` enums with an explicit `unmapped` escape (a
contract that makes an unknown *representable* rather than silently
mis-mapped); the `Primitive[Input, Output]` typed envelope; the runtime
`threshold_unit` guard at the covenant seam (#372) and the `to_canonical_threshold`
scale discipline, which exist precisely because a percent misread as a fraction
is a 100× error nobody sees. And it has the counter-example: before the metric
alias map, an extracted trigger whose `metric` matched no sentinel fell through
to a silent `0.0` and never fired.

Concretely, a child is not done when the code works — it is done when the
contract it introduced or extended is **pinned by a test that fails if someone
violates it later**. A new annex table needs a test asserting every declared
field resolves and that an unknown column stays unresolved rather than guessing;
a new metric needs a test that an unmappable string lands `unmapped` rather than
a default; a new need-calculator needs a test that an unregistered recipient
yields `not_evaluable` rather than 0. Prefer making an invalid state
unrepresentable over documenting that it is invalid. Where a contract cannot be
enforced in types, enforce it with a runtime guard at the seam plus a regression
test — and say in the plan which of the three you chose and why.

**3. Governance, auditability and visibility are first-class, not follow-ups.**
Anything new must thread the same evidence the rest of the platform does: the
`PrimitiveResult` envelope with real `confidence` and `citations` (never a
hardcoded constant), an `audit_result` entry, per-field provenance via
`ProvenanceMap`, and regulatory locators where they exist — the way
`esma_annex2.locator_for` anchors a value to its RREL field code. Honest
degradation is part of this: an unmappable recipient or metric lands `unmapped`
/ `not_evaluable` with a real reason, and never a silent `0.0` — the failure
class the canonical taxonomy exists to kill. Visibility means the result reaches
a surface a human reads: the capability matrix, `/governance`, the evidence
pack. A child whose output cannot be traced back to a cited source is not done.

**4. Build the seam, not the special case — this is a multi-asset-class
platform.** The goal is a platform that handles many securitisation types
seamlessly, so CLO is the *first* non-RMBS member, not the subject. Prefer the
generalising change: a multi-annex registry over a hardcoded corporate branch; a
per-class coverage-test metric over an OC/IC pair pinned to one deal's
attachment points; an asset-class-neutral stratification dimension over a CLO
one. The test to apply is *"would adding CMBS or Auto next be cheaper because of
this change?"* — if the answer is no, the shape is probably wrong. Two existing
facts make this concrete and cheap to honour: `esma_annex2.py`'s own docstring
already says its record shape "admits other annexes (Auto/SME) later without
breaking callers", and `esma_tape_normaliser.py` already *detects* Annex 5 and
Annex 8 while having no field table for either — so the registry this plan
builds should close those two gaps as a side effect, not just serve CLO.

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

### Epic A: CLO engine foundations   (umbrella #450)

Everything the engine needs to *represent* a CLO, built and testable without a
single CLO document. Each child is additive to a closed vocabulary or an open
registry, in the style those modules already establish — none of this is
architectural. The epic is done when the canonical schema can express a CLO's
capital stack, its fee waterfall recipients, and its coverage tests, with
per-recipient need actually computable for a stack deeper than three classes.

- **Multi-annex loan-level schema registry, with the corporate template as its
  first new member** — Generalise the single hardcoded `esma_annex2` table into
  a registry that resolves a tape's columns through *whichever* annex applies,
  and add the corporate / leveraged-loan template (obligor, industry, facility
  rating, lien seniority, spread, defaulted / PIK / cov-lite) as its first
  additional member; close the standing gap where `_ANNEX_SIGNATURES` detects
  Annex 5 (Auto) and Annex 8 (SME) but no field table exists for either, and
  verify the `"Annex 8 (SME)"` label against the actual RTS numbering (the
  corporate template is believed to be Annex 4 / `CRPL`, with Annex 8 leasing),
  correcting it if wrong. **Reuse:** the existing `Annex2Field` record shape and
  `canonical_column_for` / `code_for_column` resolution, whose docstring already
  anticipates this. **Contract:** one registry as the single resolution point;
  an unknown column stays unresolved rather than guessing, pinned by test.
  **Governance:** every resolved value keeps its regulatory locator
  (`locator_for`) so provenance still cites a field code. **Generality:** adding
  CMBS or Consumer next must be a table, not a code change. Sequencing:
  parallel. Paths: `src/loanwhiz/domain/**`,
  `src/loanwhiz/primitives/esma_tape_normaliser.py`, `tests/**`.
- **Coverage tests as first-class, per-class covenant metrics** — Add
  overcollateralisation and interest-coverage ratios to `MetricType` with the
  alias-table and classifier coverage an extracted trigger needs, and make them
  resolvable to a real value from deal state, per attachment point rather than
  as a single OC/IC pair. Explicitly excludes the cash-diversion mechanic — a
  failing test is *observed* here, not yet acted on. **Reuse:** a coverage test
  is a trigger, so it rides the existing `TriggerRule` / `covenant_monitor` /
  `_extract_metric` path — do not stand up a coverage subsystem. **Contract:**
  respect the existing canonical-scale discipline (`to_canonical_threshold`, the
  `threshold_unit` runtime guard from #372) so a ratio can never be misread
  against a percent; an unresolvable coverage metric must land `not_evaluable`
  with a reason, never `0.0`, pinned by test. **Governance:** the monitor's
  existing evaluable / proximity reporting must carry these honestly.
  **Generality:** the metric shape should suit any deal that tests coverage at
  an attachment point, not only CLOs. Sequencing: parallel. Paths:
  `src/loanwhiz/domain/rules.py`, `src/loanwhiz/extraction/taxonomy.py`,
  `src/loanwhiz/primitives/covenant_monitor.py`, `tests/**`.
- **Close the recipient-enum vs need-calculator contract gap** — `RecipientType`
  carries 23 members while `NEED_CALCULATORS` registers 9, so deeper-stack
  recipients (`class_d/e/f_*`) map correctly and then compute need 0; register
  the missing calculators and add the senior / subordinated management-fee
  recipients a CLO waterfall pays. Excludes the incentive fee, which needs an
  equity IRR that is out of scope. **Reuse:** `@register_need` on the existing
  open registry — no new execution branch. **Contract:** this child is mostly a
  contract fix, so close the class of bug rather than the instances — make the
  enum↔calculator relationship checkable (e.g. a test that every non-`unmapped`
  `RecipientType` either has a calculator or is explicitly declared
  report-supplied), so the next vocabulary broadening cannot silently reopen the
  gap. **Governance:** an unregistered recipient stays `not_evaluable` in the
  step trace with a real reason. Sequencing: parallel. Paths:
  `src/loanwhiz/primitives/waterfall_interpreter.py`,
  `src/loanwhiz/domain/rules.py`, `src/loanwhiz/extraction/taxonomy.py`,
  `tests/**`.

### Epic B: CLO deal onboarding   (umbrella #454)

Take one real CLO from documents to an executable, honestly-graded deal model.
This epic is where the data gap bites and where the #437 calibration applies:
the extraction child is expected to be hard, and a plan that concludes the
extractor needs work before the deal can be modelled is the right outcome, not a
failure.

- **Source and register a CLO deal document set** — Identify a European CLO with
  a publicly obtainable prospectus / offering memorandum, register it in
  `deals.json` in the existing deal-context shape, and record honestly in the
  data card which documents are and are not available (in particular whether any
  trustee reports could be obtained, since their availability is what decides
  whether to-the-cent validation is reachable — they turned out to be freely
  obtainable; see the 2026-09-05 correction above). No extraction. **Reuse:** the existing deal-context
  shape and registry — adding a deal is *data*, not code, and must stay that
  way. **Governance:** the data card records what is and is not available, so
  the absence of trustee reports is visible now rather than discovered at
  grading time. Sequencing: parallel. Paths:
  `src/loanwhiz/data/deals.json`, `docs/data-card.md`, `src/loanwhiz/config.py`.
- **Extract the CLO prospectus to canonical `DealRules`** — Run the extraction
  pipeline against the sourced document and get an honest deal model out: the
  full capital stack, both the interest and principal waterfalls, and the
  coverage-test definitions as extracted triggers; fix the extractor where a CLO
  document defeats it rather than hand-editing a seed, exactly as #438/#439
  established. **Reuse:** the existing section router, taxonomy classifier and
  assembler — a CLO document is a harder input to the same pipeline, not a
  reason for a second one. **Contract:** where a CLO defeats the extractor, fix
  the general rule and pin it with a test against the shape that broke it (the
  #438/#439 pattern), rather than special-casing this deal. **Governance:**
  per-step and per-trigger extraction confidence and citations must be threaded,
  and anything unmappable stays `unmapped` with the prose retained for audit —
  never executed. Sequencing: sequential. After the sourcing child. Paths:
  `src/loanwhiz/extraction/**`, `src/loanwhiz/data/deals/seed/**`, `tests/**`.
- **Execute and grade the CLO deal honestly** — Run the extracted model through
  the engine, evaluate its coverage tests as covenant metrics, and let the
  capability matrix report the result with real per-cell reasons — `ran` where
  it ran, `not-applicable` with a genuine reason where it did not, and never
  `validated`, since no answer key has been authored for this deal (the trustee
  reports themselves are obtainable — see the 2026-09-05 correction above). **Reuse:**
  the existing `run_period` fold, covenant monitor and capability matrix — this
  child wires a deal through machinery that already exists and should add no new
  execution path. **Contract:** the matrix's three-state vocabulary is not
  widened for CLO; a cell that cannot be graded is `not-applicable` with a real
  reason. **Governance:** the run must be traceable end to end — evidence pack,
  audit entries, and a data/model-card update saying plainly that this deal is
  unvalidated and why. **Generality:** whatever the matrix needs to describe a
  non-RMBS deal should be asset-class-neutral, so a CMBS column later needs no
  further change. Sequencing: sequential. After the extraction child. Paths:
  `src/loanwhiz/primitives/capability_matrix.py`, `src/loanwhiz/api/main.py`,
  `docs/**`, `tests/**`.

## Filed issues

- Epic A "CLO engine foundations" → umbrella **#450**
  - **#451** Multi-annex loan-level schema registry, with the corporate template as its first new member  _(parallel, prio 1)_
  - **#452** Coverage tests as first-class, per-class covenant metrics  _(parallel, prio 1)_
  - **#453** Close the recipient-enum vs need-calculator contract gap  _(parallel, prio 1)_
- Epic B "CLO deal onboarding" → umbrella **#454**
  - **#455** Source and register a CLO deal document set  _(parallel, prio 1 — the long pole, start first)_
  - **#456** Extract the CLO prospectus to canonical DealRules  _(sequential, After #455; also needs epic #450)_
  - **#457** Execute and grade the CLO deal honestly  _(sequential, After #456)_

All eight labelled `liz:enrolled`. Every child body carries the four standing
constraints verbatim, plus its own Reuse / Contract / Governance / Generality
notes, so a worker never has to open this plan to know how the work must be done.

**Epic B's umbrella deliberately carries no `After #450`.** A hard cross-epic
marker there would block #455, the sourcing child — which is the long pole and
is independent of Epic A. The real dependency is per-child: #456 and #457 need
#450 landed, and #456's body says so.
