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
## 2026-09-08 · pitfall · #484

When generating a population to match a published distribution, decide which
axis the platform *renders* before choosing how to assign buckets. Labelling an
already-drawn pool can reproduce the published **balance** share or the
published **count** share, never both: the smallest loans land in the smallest
buckets, so a bucket worth 0.15% of balance came back holding 0.51% of loans —
at full confidence, on a credit metric. Draw within each bucket against its own
published count and balance instead, and reconcile both.

Refs: #484
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

## 2026-09-09 · pattern · #528

When an input is reachable only by back-solving it from the answer, the choice is
not "refuse or assume" — read the source document directly first. The extractor's
output is not the document: an LLM pass truncated at a character budget drops a
whole alphabetical range, so a term it never emitted may still be stated on the
page, and `pypdf` reads a 420pp text layer in seconds with no OCR or credentials.
Recover the one lost fact with a deterministic parser (#480) rather than widening
the budget and re-running a non-deterministic extraction over everything else.
Then check the result against a value another document states independently.

Refs: #528
Refs: #539 — same route, one Condition further out: the day-count fraction.


## 2026-09-09 · pitfall · #555

Establish which population a stated count describes before reconciling against
it — the balance beside it in the same table need not describe the same one.
Contego's aggregate tables state 212 against a euro total its asset sections
carry 177 identifiers for: the balance is at asset grain, the count is the
interest-accrual row count, one per rate contract. Find the count's population
by looking for a section whose row count equals it, and reconcile each figure
against its own. A count checked against the wrong population cannot fail —
worse than none, since par alone cannot see a dropped row worth zero (#468).

Refs: #555


## 2026-09-10 · pitfall · #550

Before accepting that a figure lives only in documents your path excludes, look
in the one your path already reads. A CLO's overcollateralisation numerator was
filed as unreachable — stated only in trustee reports #524 routed off the live
series — yet the Note Valuation Report that series folds prints it on its own
Par Value Tests Detail page. A scarcity premise is a claim about the whole
registered document set; check it against that set, not the documents the issue
names. Then read the total the report states and tie it to the components above
it: the adjacent aggregate is the near-miss, one line away and three points high.

Refs: #550
