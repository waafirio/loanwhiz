# Published figures & doc drift

<!-- Agent-appended entries below. Append only; never reorder or rewrite an
     existing entry (`merge=union` would duplicate it rather than replace it). -->

## 2026-09-02 · pitfall · #441

Re-derive every published figure from the running system before quoting it —
never from a prior doc, a sibling PR body, or a committed artifact. Three drift
paths bit at once: a tally moved because *data* changed with no code diff
(refreshed seeds flipped 3 capability cells, 1/9/15 → 1/12/12); a stored metric
kept its pre-change value (a seed's `completeness_score` 0.75 was the superseded
section-header ratio, current code scores it 0.925); and two PR bodies
disagreed with the output they themselves committed (0.93 vs 0.925). Hit the
endpoint or recompute — authoritative once is not true now.

Refs: #441

## 2026-09-06 · pattern · #469

Reconcile a parsed tape against the source document's OWN stated aggregates
before anything downstream trusts it, and refuse rather than return on a
divergence — counts and per-bucket distributions, not just the grand total,
which two swapped rows would still satisfy. A trustee report is self-describing:
its concentration tables enumerate the very vocabularies its detail pages use.
When a summary table contradicts the same page's stated total, the DOCUMENT is
wrong rather than the parse — drop that table from the oracle, keep its counts,
and record the discrepancy as a defect instead of absorbing it.

Refs: #469

## 2026-09-08 · pitfall · #494

Assert a section **parsed something** as its own named check, separately from
any arithmetic over it. A reconciliation that sums rows and chains balances
passes *vacuously* on zero rows, and it does so most convincingly where the
document's own stated total is `0.00` — the CLO Principal waterfall. So a
section title that misses reads as a clean reconciliation rather than as a
missing section. Presence and correctness are two different questions; a check
that only answers the second cannot tell "nothing is wrong" from "I saw
nothing".

Refs: #494
## 2026-09-08 · pitfall · #480

A datum recorded as unobtainable is a claim about the document someone looked
in, never about the deal — re-ask it of every document already in the registry
before repeating it. #456 called a CLO's coverage-test thresholds uncapturable
because the offering circular's alphabetical glossary truncated at 40k, losing
every test defined under C–F: true of the circular, false of the deal, whose
already-parsed trustee reports state each required level twice. Name the
document inside the sentence recording the limitation.

Refs: #480

## 2026-09-08 · pattern · #479

Before deriving a figure an operator has been hand-writing, reconcile the
derivation against their value: agreement is the evidence you derived the same
quantity, disagreement that you built a second, parallel notion of it. Green
Lion's declared `projection_base.current_pool_balance` proved to be its newest
tape's summed `pool_balance_eur`, rounded — which is what licensed deriving it
for every deal. Keep serving the declared value where one exists (a four-cent
"improvement" moves a published number for nothing) and pin the agreement as a
test.

Refs: #479
