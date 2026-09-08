---
id: 2026-09-08-clo-pop-validation
title: "Validate the CLO: parse the Note Valuation Report's Priorities of Payments and reconcile the engine to the cent"
status: decomposed
created: 2026-09-08
updated: 2026-09-08
epics: []
---

# Validate the CLO: parse the Note Valuation Report's Priorities of Payments and reconcile the engine to the cent

## Context & intent

The question that started this: *"so how will we get cairn validated?"*

### What `validated` actually claims, and why Cairn does not reach it

`validated` is not a mood. It is one specific claim, emitted at exactly
one site — `capability_matrix._classify_engine_validation` — and it says:
*the engine reproduced this deal's own published Priority of Payments, to
the cent.* That site returns `STATE_VALIDATED` only when the deal has an
entry in the hand-built `_VALIDATION_BUILDERS` map (`api/main.py:3443`),
which today holds exactly one key: `green-lion-2024-1`.

Epic #477's child #481 gave Cairn a **real answer key**, but by the other
of the two sanctioned routes — `from_trustee_liability_summaries(...)`,
authored from the monthly trustee reports' *stated coverage-test
outcomes*. The committed `cairn-clo-xvii-dac.json` carries 3 periods x 8
covenants and, deliberately, **zero** `revenue_pop`, **zero**
`redemption_pop` and **zero** `pool_stats`. That earns Cairn a graded
`covenants` row on `GET /quality-matrix` (24/24 matched, 0 failed) and it
earns nothing at all on the engine-validation row, which stays
`not-applicable` with a reason that is true of this repo rather than of
the issuer.

So Cairn today is **graded, not validated**, and that distinction is
correct, not a bug. This epic is about closing it honestly.

### The route exists and the document is already located

`docs/data-card.md` already records the answer, from the #468/#477 work:
Cairn's **Note Valuation Report** — 83 pages, dated 08 Jan 2025, free,
with its URL committed at `docs/data-card.md:118` so nothing has to be
re-sourced — carries **both an Interest Priority of Payments and a
Principal Priority of Payments**. That is the CLO analogue of the Notes &
Cash report that makes Green Lion 2024-1's to-the-cent validation
possible. The data card states the consequence in as many words
(`docs/data-card.md:133`): *"A validated CLO cell is therefore feasible in
a way it never was for the Italian and Spanish deals. Feasible is still
not done."*

It also records why `notes_cash_report_urls` is deliberately **unset** on
the Cairn registry entry: that key is a *routing promise*, not a URL slot.
`_reconstruct_series` dispatches on it and
`test_answer_keys_exist_exactly_where_published_ground_truth_does` reads
its presence as an assertion that a **PoP-bearing** key exists.
`tests/test_clo_deal_registration.py` pins the distinction so Cairn's
absence never quietly flattens into "another deal with no report". Setting
that key is therefore not a config tweak — it is a claim, and this epic is
what makes the claim true before making it.

### Why this shape, and not the cheaper ones

**1. Converge the two ground-truth surfaces before extending either.**
There are currently two: the hand-built `_VALIDATION_BUILDERS` dict that
`/capability-matrix` reads, and the data-driven `load_answer_key` /
`reconcile_against_answer_key` pair that `/quality-matrix` reads.
`reconciliation_answer_key.py:3` describes itself, accurately, as *"the
data-driven generalization of the hand-built `_VALIDATION_BUILDERS` map"*
— but the capability matrix never switched over. The result is that
adding a validated deal still means writing bespoke Python, which is
precisely the whack-a-mole the standing constraint rules out. Adding
Cairn as a second entry in that map would be the cheapest possible green
cell and would *confirm the map as the real interface*. So the first
child converges them: `validated` becomes a property of a deal's
committed answer key carrying a PoP section, Cairn reaches it as **data**,
and so does the deal after Cairn — with no new Python.

**2. A zero coupon is worse than a missing one, and today it is silent.**
Verified against the tree during planning:
`waterfall_interpreter.py:203` declares `rate_pct: float = Field(default=0.0)`,
`:516` calls `_accrued_interest(t.balance, t.rate_pct, funds.days_in_period)`,
and `:489` computes `balance * (rate_pct / 100.0) / 360.0 * days`. A
tranche whose coupon the capital structure could not resolve therefore
accrues **exactly nothing**. Meanwhile `period_state_machine._rate_inputs`
(`:181`) already documents the opposite behaviour — *"the need calculator
reports it as unevaluable instead of costing the waterfall nothing"* — so
the intent is recorded and the implementation is not.

This is load-bearing for this deal specifically. **All eight of Cairn's
classes are floating** (`"3 month EURIBOR + 1.80%"` and siblings), and
`capital_structure.numeric_rate_pct` correctly refuses to coerce a margin
into a rate — so every one of the eight arrives with no rate and accrues
zero interest need. A reconciliation run before this is fixed would not
merely fail; it would fail **in the flattering direction**, modelling a
deal that services its entire note stack for free. That is the #452
lesson in a different place: the wrong number reads as health. This child
lands before anything is graded.

**3. Authoring the key and grading against it are separate children,
deliberately.** `data/deals/answer_keys/README.md` already states the
rule: *"An answer key must never be derived from the engine's own output.
That would grade the engine against itself and make every cell vacuously
green — the most damaging failure available to this surface, because it
would look like success."* Splitting authorship from grading enforces
that at the **process** level rather than by intent alone: the worker that
writes the ground truth never sees the engine's answer, and the worker
that grades cannot reach back and adjust the key.

**4. Reuse over addition.** The only genuinely new code in this epic is
one parser. It is a sibling of `notes_cash_parser`, emits the **existing**
`NotesCashReport` shape, and sits on the **existing** pypdf +
section-title seam that `collateral_schedule_parser` established in
#469/#480. `AnswerKeyPeriod` already carries `revenue_pop` and
`redemption_pop`, so there is **no schema change**. `reconcile_series` and
the to-the-cent core are untouched. This is deliberately the same profile
as epic #468 — parse, map, register, prove — because that shape worked.

### Alternatives weighed and rejected

- **Just add `cairn-clo-xvii` to `_VALIDATION_BUILDERS`.** The cheapest
  green cell available. Rejected: it entrenches the hand-built map as the
  interface, leaves #427's generalization unproven, and means the third
  validated deal needs bespoke Python all over again.
- **Coerce the floating coupons to a fixed equivalent.** Rejected on the
  existing refusal's own terms (`capital_structure.py:101`): a margin is
  not a coupon, and coercing one fabricates a rate the document does not
  state. It is also unnecessary — the Note Valuation Report states the
  period's actual index fixing, so the number can be read rather than
  guessed.
- **Grade the NVR's Priorities of Payments through #481's existing
  coverage-test constructor.** Rejected: it would report a payment
  reconciliation as a covenant outcome, a category error, and would not
  reach `validated` in any case because that row reads the PoP section.
- **Reconcile now and treat the zero-interest gap as a known delta.**
  Rejected: the "delta" is the entire interest cascade. "To the cent"
  would mean nothing.

### What this epic does not promise

**It does not promise that Cairn will reconcile.** The Note Valuation
Report may not parse cleanly; the extracted 29-step Interest / 23-step
Principal cascades may disagree with the published ones; the numbers may
simply not tie. **A failing reconciliation is this epic's finding, not its
failure.** The grading child records whatever the run says — pass, fail or
partial — and updates `docs/data-card.md` and the answer-keys README to
match. Nothing in this plan licenses tuning the key or the engine to
produce a green cell, and the authorship/grading split exists so that no
single worker is in a position to.

Two of the five children (the surface convergence and the coupon refusal)
are worth doing **whether or not Cairn ever validates** — one removes a
scaling trap, the other removes a silent-zero that flatters every deep
capital structure the platform will ever load. They are not scaffolding
for the CLO; the CLO is what surfaced them.

### Ordering

Children 1, 2 and 3 are independent and dispatch in parallel; 1 and 2
carry the lower `liz:priority` because 5's verdict is only meaningful once
both are in the epic branch (2 for correctness, 1 for the cell to be
reachable as data at all). 4 depends on 3 for the parsed report. 5 depends
on 4 for the key, and — by the ordering above, not by a hard marker — sees
1 and 2 already landed.

## Decomposition

### Epic: Validate the CLO — reconcile the engine to Cairn's published Priorities of Payments   (umbrella #<N — filled in phase 4>)

Cairn CLO XVII is graded but not validated: #481 committed an answer key
from the trustee reports' coverage-test outcomes, which carries no
Priority of Payments, and the one site that emits `validated` reads a
hand-built builder map holding a single deal. This epic closes that by
converging engine validation onto the data-driven answer-key registry,
fixing a silent zero-coupon that would make any CLO waterfall run
flatteringly wrong, parsing the Note Valuation Report's two Priorities of
Payments, authoring the PoP-bearing key from them, and grading the engine
against it — reporting whatever the reconciliation actually says.

- **Converge engine validation onto the answer-key registry** — Make
  `capability_matrix._classify_engine_validation` derive `validated` from a
  deal's committed answer key carrying a Priority-of-Payments section,
  rather than from the hand-built `_VALIDATION_BUILDERS` map, with Green
  Lion 2024-1 unchanged as the behaviour-preserving regression.
  Sequencing: parallel.
  Paths: `src/loanwhiz/primitives/capability_matrix.py`, `src/loanwhiz/api/main.py`, `tests/**`.
- **Refuse an unresolved tranche coupon instead of accruing zero interest**
  — Make a tranche whose coupon the capital structure could not resolve
  unevaluable and named in the interest need, rather than defaulting
  `rate_pct` to `0.0` and costing the waterfall nothing, as
  `period_state_machine._rate_inputs`' own docstring already claims it does.
  Sequencing: parallel.
  Paths: `src/loanwhiz/primitives/waterfall_interpreter.py`, `src/loanwhiz/primitives/period_state_machine.py`, `tests/**`.
- **Parse the Note Valuation Report's two Priorities of Payments** — Add a
  Note Valuation Report parser that emits the existing `NotesCashReport`
  shape for both the Interest and the Principal Priority of Payments, over
  the same pypdf/section-title seam `collateral_schedule_parser` uses.
  Sequencing: parallel.
  Paths: `src/loanwhiz/primitives/**`, `tests/**`.
- **Author Cairn's PoP-bearing answer key and register the report** —
  Extend `DealAnswerKey` with the constructor that fills `revenue_pop`,
  `redemption_pop` and the period's published index fixings from the parsed
  Note Valuation Report, commit the enriched `cairn-clo-xvii-dac.json`, and
  set `notes_cash_report_urls` on the registry entry so the routing promise
  becomes true. Sequencing: sequential. After #<child-3>.
  Paths: `src/loanwhiz/primitives/reconciliation_answer_key.py`, `src/loanwhiz/data/deals/**`, `tests/**`.
- **Grade the engine against the key and record the result honestly** — Run
  Cairn's extracted cascades through `reconcile_series` against the
  committed key and report whatever it says — pass, fail or partial —
  updating `docs/data-card.md` and the answer-keys README to match, without
  tuning either side to reach a green cell. Sequencing: sequential.
  After #<child-4>.
  Paths: `docs/**`, `src/loanwhiz/data/deals/answer_keys/README.md`, `tests/**`.

## Filed issues

<Filled in phase 4 — the artifact<->issue link.>
