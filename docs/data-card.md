# Data Card: LoanWhiz deal set (Green Lion 2026-1 + cross-jurisdiction deals + the first CLO)

> Governance artefact following FINOS AI Governance Framework templates.
> See also: [docs/model-card.md](model-card.md) · [docs/governance.md](governance.md)

The primary demo and validation subject is **Green Lion 2026-1** (documented in
full below). The deal registry additionally carries **four more RMBS deals across
two further jurisdictions** that the *same* primitives run on end-to-end, plus a
sixth deal — the Irish CLO **Cairn CLO XVII DAC** — which is **extracted and
partly graded, but not validated**: its documents are sourced and recorded here,
the pipeline reads its Listing Particulars to a canonical deal model (#456), and
since #481 its trustee reports' published coverage-test results are committed as
an answer key, so `GET /quality-matrix` grades that one row against figures the
engine did not compute. That is a graded row, not a validated cell — see
[the CLO's own section](#cairn-clo-xvii-dac--what-is-and-is-not-obtainable). See [The full deal set](#the-full-deal-set--6-registered-deals-5-that-run)
for the honest per-deal breakdown. "Runs on" is not "validated against": the only deal validated
to the cent against external published actuals is **Green Lion 2024-1** (a
second, **Green Lion 2023-1**, is graded to the cent against a committed answer
key), and while extraction *coverage* on the non-English prospectuses is now
high, neither of those deals publishes a report to validate against. The
capability matrix
(`GET /capability-matrix`, Showcase view) is the source of truth. Read the
tally from the endpoint rather than from here: it has moved with the data
several times, and a number transcribed into prose goes stale in silence — as
two different stale tallies in this repo's own docs did before #484.

---

## Dataset Identity

| Field | Value |
|---|---|
| **Dataset name** | Green Lion 2026-1 B.V. |
| **HuggingFace identifier** | `Algoritmica/green-lion-2026` (prospectus, 3 monthly tapes, 3 investor reports) |
| **Provider** | Algoritmica.ai |
| **Version** | As of 2026-06-03 (no version tag; use commit hash for reproducibility) |
| **License** | Available on HuggingFace; see dataset repository for terms |
| **URL** | https://huggingface.co/datasets/Algoritmica/green-lion-2026 |

### Reporting periods (3 monthly tapes)

Green Lion 2026-1 (~EUR 1bn pool) reports **3 monthly ESMA Annex 2 tapes** from `Algoritmica/green-lion-2026` — **February, March, and April 2026** — each with a matching real investor report. **January 2026 (`202601`) is an intentional gap** in the chronology.

> **Separate deals are not interchangeable.** `Algoritmica/green-lion-2024-2025` (~EUR 139bn pool, ~130× this deal) and the real ING `green-lion-2023-1` / `green-lion-2024-1` deals are **different deals**, not Green Lion 2026-1's pre-history. Their loan tapes are **not** chained into this deal's `tape_urls` — doing so would splice unrelated pools. Green Lion 2023-1 and 2024-1 are registered as their own deals (see [The full deal set](#the-full-deal-set--6-registered-deals-5-that-run)); 2024-1 is the engine's to-the-cent validation target against its own published Notes & Cash report.

> **These are period snapshots, not a longitudinal panel.** The three tapes are
> **re-sampled each period** — loan identifiers do not persist across months
> (the gross balance falls in one period and a similar gross balance rises in
> the next, netting to a small movement). So the series is a sequence of
> point-in-time pool snapshots, not a tracked-cohort loan-level time series.
> Per-period collections and losses are derived by **net reconciliation to
> pool movement**, not by following individual loans.

---

## The full deal set — 6 registered deals, 5 that run

Green Lion 2026-1 is the headline demo deal, but the deal registry
(`src/loanwhiz/data/deals.json`, merged over the in-code Green Lion default)
carries **six deals across four jurisdictions and two asset classes**. **Five of
them the *unmodified* pipeline runs on end-to-end; the sixth — Cairn CLO XVII DAC
— is registered only.** That distinction is load-bearing and is kept everywhere
in this card: a registered deal has its documents sourced and its availability
recorded; a deal that *runs* has been through the pipeline.
This demonstrates the primitives are deal-agnostic — but
**"the pipeline ran" is reported separately from "the output was validated"**,
and extraction completeness is stated honestly per deal. High completeness is a
*coverage* measure over what the extractor populated; it is not a claim that the
extracted numbers are correct. The capability matrix (`GET /capability-matrix`, Showcase view) is
the per-cell source of truth; it reports the current tally itself.

| Deal | Jurisdiction | Documents | Extraction completeness | What extracted | Validation |
|---|---|---|---|---|---|
| **Green Lion 2026-1 B.V.** | Netherlands | Prospectus (real) + 3 synthetic Annex 2 tapes + 3 investor reports (real) | **0.75** | Full waterfall (revenue/redemption/post-enforcement), 3 triggers, 0 definitions | Collateral reconciled to investor reports to the cent; liabilities prospectus-derived & invariant-checked (no in-window Notes & Cash) |
| **Green Lion 2024-1 B.V.** | Netherlands | Prospectus (real) + investor reports + **quarterly Notes & Cash (real)** | **0.925** | Full waterfall, 3 triggers | **Validated to the cent** — engine reproduces the published Notes & Cash Priority of Payments (revenue 11/11, redemption 4/4; Class A interest engine-computed). One of the two `validated` cells, both Dutch. |
| **Green Lion 2023-1 B.V.** | Netherlands | Prospectus (real) + investor reports + **quarterly Notes & Cash (real)** | **1.0** | Full waterfall, 4 triggers | **Validated to the cent** — graded by `GET /quality-matrix` against a committed answer key (#440) across all three published periods, and since #492 `validated` on the capability matrix too, because that cell is now derived from the committed key rather than from a hand-built builder. The `/deal/{id}/validation` endpoint still returns `available=false`: no validation *builder* is registered, so that one endpoint continues to understate what is graded. |
| **Leone Arancio RMBS 2023-1 S.r.l.** | Italy | Prospectus (real, Italian) + investor reports | **0.925** | Full waterfall (23/23/12 steps), 3 triggers, 3 note classes — A1 480m / A2 6,600m / J 920m | Pipeline ran; tranche sizes reconcile to the curated `deals.json` registry, but **no** Notes & Cash report is published, so no external validation is possible |
| **Sol-Lion II RMBS Fondo de Titulización** | Spain | Prospectus (real, Spanish) + investor reports | **0.925** | Full waterfall (20/15/12 steps), 3 triggers, 8 note classes — A1–A6, B, C | Pipeline ran; tranche sizes reconcile to the curated `deals.json` registry, but **no** Notes & Cash report is published, so no external validation is possible |
| **Cairn CLO XVII DAC** *(CLO — extracted, covenants graded, PoP ground truth committed, graded; the Interest cascade reconciles)* | Ireland | Listing Particulars (real, 420pp) + 3 monthly trustee reports (real) + Note Valuation Report (real, 83pp) | **1.0** | Full 8-class stack (A, B-1, B-2, C, D, E, F + Subordinated, EUR 404.1m), both Priorities of Payments as distinct cascades (29-step Interest / 23-step Principal / 26-step Post-Acceleration), 10 triggers of which 8 are per-class coverage tests, 25 definitions | **Executes and is graded on one row; not validated.** The seed folds through the shared `run_period` kernel with the deal's own cascades (#457). Since #481 an answer key **is** committed — authored from the trustee reports' stated coverage-test results, so `GET /quality-matrix` grades the `covenants` row `passed`. It is still **not** `validated`: since #492 that cell is earned by committed *data* — an answer key carrying a Priority-of-Payments section plus an offline engine series — rather than by a bespoke validation builder. #494 **parses** the Note Valuation Report's Interest and Principal Priorities of Payments and #495 committed them as the key's January 2025 period, so the first artifact exists; the cell refuses on the second, the offline engine series. **#496 ran that grade anyway and it did not reconcile — the finding, not a deferral.** Folded against the very document the key's PoP period was authored from, the deal's 29-step Interest cascade distributed EUR 5,434,911.93 of the report's stated EUR 7,255,062.35 available revenue and left EUR 1,820,150.42 undistributed. The pot was the report's own figure and no step was starved — `total_shortfall` was EUR 0.00 — so that was not an under-funded cascade but an incomplete one: the money had no step to go to. No step disagreed: the report prints its 62 rows against the cascade's 29 labels, 20 of them were joined by no step at all, and the eight of those that carry money (`(A)(i)`, `(A)(ii)`, `(H)(i)`, `(H)(ii)`, `(CC)(1)(a)`, two it re-letters bare `(a)`) accounted for the shortfall to the cent — so that failure was the tie-out, not a delta. Three of the 29 lines are genuinely engine-computed, and the rest are not: #511 resolved the recipient spelling so Class A and Class C interest are derived rather than handed the report's figure, #512 supplied their published applied rates and #528 the accrual period, so each is computed from tranche size x rate x a day count measured between two stated Payment Dates and reproduces its published figure (EUR 3,277,457.78 and EUR 415,004.33) with no report input on the engine's side. Every other step's amount is still taken from the report and compared to itself, and the Principal cascade reconciles on EUR 0.00 of available principal funds, which an engine that never paid anything would reproduce exactly. **The Interest cascade's own gaps are now closed, and each turned out to be a different thing.** #514 joined the 20 unmatched rows so every published cent reaches a step; #538 resolved Class B's recipient onto the two strips the class was issued in — its stack is spelled `class_b_1`/`class_b_2` while the report path seeded a canonical `class_b`, so no tranche had attached; and #539 sourced each strip's day-count fraction from Conditions 6(e)(ii)/(iii), which is what makes the sum right, since B-1 accrues over 95 actual days and B-2 over 90 on 30/360. All 29 steps now agree at the key's EUR 0.01 tolerance and `engine_computed_passed` reads 3. **The aggregate is not what earns that**: for one period the cascade's total tied perfectly while two steps were wrong by equal and opposite amounts, because the pot is fixed and the `(CC)` residual sweep absorbs any senior step's over-draw — so `steps_passed` and the per-step deltas are the signal, and the tie-out only corroborates them. Since #513 the grade is reachable through the committed key itself: the key unions four periods and only the one authored from the Note Valuation Report is foldable, so `reconcile_against_answer_key` grades that period and reports the other three not-applicable — the document behind them publishes no Priority of Payments — reaching the same figures through the key that #496 reached by bypassing it. A period skipped that way is not a period passed: it is excluded from the verdict and from both period counts, so this key cannot report three-quarters green for periods nothing compared. `tests/test_clo_pop_grading.py` holds every figure above; no answer key or engine module was changed to produce them. `covenant_monitoring`, `waterfall_execution` and — since #471 registered the derived tape — `tape_analytics` are `ran`; collateral reconciliation and engine validation stay `not-applicable`. **One of those reasons is no longer true of this deal (#525).** The `waterfall_execution` cell still reads "the per-deal endpoints cannot yet serve this deal, whose registered period source cannot be folded into a series without the structural configuration it does not register"; both halves are now false — the endpoints serve and the series folds by the report path, which needs none of that configuration. It is recorded here rather than corrected: the fix is in `capability_matrix.py`, outside the issue that measured it. The `collateral_reconciliation` reason is unaffected and stays accurate, because pool-state reconstruction from the tape genuinely does need those fields. Collateral reconciliation stays refused because the deal registers no structural config, not because it has no tape. The coverage tests' required levels are extracted from the trustee reports (#480) and are **still not wired onto the deal model's triggers**, which carry `threshold: null` — so on the deal's own state the monitor still reports them not-evaluable. #481 did not change that; it supplies the published level *and* the published ratio from the answer key on the grading path only. A second reason the deal's own cascades did not evaluate has since been closed: every extracted step recipient now resolves to a canonical `RecipientType` (#503, pinned by `tests/test_clo_recipient_vocabulary.py`), which is what made the grade above runnable at all. **#525 graded what the three live screens render, and the split is the finding rather than the serving.** `GET /deal/cairn-clo-xvii/compliance` and `/waterfall` both answer 200 where they answered a labelled 422, and the CLO now appears in `/compare`'s `performance_series` with a real `latest_period` of 2025-01-08. The waterfall screen renders the Note Valuation Report's January 2025 period: a 29-step Interest cascade over the report's stated EUR 7,255,062.35 of available revenue, and **every one of those 29 steps agrees with the document's own published rows to the cent**, with no published row left off the screen. That number is worth much less than it looks. Measured at the live path's own input seam — `PeriodInputs.revenue_step_sources` and the override map the report adapter builds — **26 of the 29 steps arrive carrying the report's figure as an override and hand it back**, so their agreement is true by construction and is the document reconciling with itself. **3 arrive with no override**: Class A `(G)`, Class B `(H)` and Class C `(J)` interest, computed from each class's size, its published applied rate and a day count measured between two stated Payment Dates, reproducing EUR 3,277,457.78, EUR 644,398.50 and EUR 415,004.33 with no report input on the engine's side. Those three are the only non-circular comparisons either screen supports. The Principal cascade still renders an EUR 0.00 pot, which an engine that paid nothing would reproduce exactly. **The compliance screen serves a refusal, not a measurement.** It renders 10 triggers across 3 trustee-report periods and **evaluates none of them** — every status is `evaluable: false` with a stated cause, and no metric or threshold is rendered — so the 200 conveys no compliance information for this deal. That cause is #549's. **The two screens also describe different points in the deal's life**: the waterfall folds the Note Valuation Report's single period, compliance runs over the trustee reports' periods, and the two sets are disjoint — which is also why `/compare` returns an empty `common_periods` for this deal against any comp, and why its performance series is flat across the one date it has. **The comparison panel's blank cells were not a CLO defect.** #522 counted 46 of 55 structural rows blank for this deal and expected the coupon work to fill them; it did not, because there was nothing there to fill. The CLO holds a populated cell on the large majority of those rows — more of them than Green Lion does, 8 tranches to 3 — and what the count was measuring is a null `value`, the per-deal comparable scalar, which is null on every waterfall and qualitative-trigger row **for every deal including both externally validated Dutch RMBS**. Those rows render the step's priority letter and its basis, and per-period amounts live on the waterfall screen instead. `tests/test_clo_live_screens.py` holds every figure in this paragraph, including the 3/26 split and the absence of an override on the three; no engine, seed, answer key or endpoint was changed to produce them. See the limitation below |

### Cairn CLO XVII DAC — what is and is not obtainable

The registry's first non-RMBS deal, added by #455 to generalise the platform off
RMBS and extracted by #456. **The Listing Particulars have been extracted; the
three monthly trustee reports have been parsed on both sides — collateral detail
(#469) and the liability-side figures (#480) — the Note Valuation Report has
not, no ground truth has been authored, and no capability cell is `validated`.**
This subsection is the availability record the epic asked for, negatives
included.

Deal identity: an Irish *designated activity company*, trustee **U.S. Bank Global
Corporate Trust**, Class A ISIN `XS2650750537` (page 395 of the Listing
Particulars), listed on **Euronext Dublin's Global Exchange Market**.

**Obtainable — free, unauthenticated, no portal account.** All five documents are
plain objects on Euronext Dublin's public document store; each was fetched with an
ordinary `GET` (verified `200 application/pdf`, no redirect, no cookies) and its
identity confirmed by reading its text:

| Document | Pages | As-of | Registry key |
|---|---|---|---|
| Listing Particulars (the offering circular) | 420 | dated 19 Sep 2023 | `prospectus_url` |
| U.S. Bank monthly trustee report | 74 | 16 Dec 2024 | `investor_report_urls` |
| U.S. Bank monthly trustee report | 74 | 18 Feb 2025 | `investor_report_urls` |
| U.S. Bank monthly trustee report | 74 | 18 Mar 2025 | `investor_report_urls` |
| **Note Valuation Report** | 83 | 08 Jan 2025 | `notes_cash_report_urls` |

The Note Valuation Report was **obtainable but deliberately not registered** under
`notes_cash_report_urls` until #495. That key is a *routing promise*, not a URL
slot: `_reconstruct_series` dispatches on it, and
`test_answer_keys_exist_exactly_where_published_ground_truth_does` treats its
presence as an assertion that a **PoP-bearing** answer key exists for the deal.
Setting it with no parser for the CLO report format and no PoP ground truth would
have asserted a promise this deal could not keep. #481 committed a key by the
*other* route (published coverage-test results, no Priority of Payments), which is
why that invariant distinguishes the two: a key alone does not imply a PoP
reconciliation.

**Both halves of the promise are now kept.** #494 parses the report's two
Priorities of Payments, refusing any parse that does not tie out to the report's
own running balances and stated totals; #495 commits the resulting PoP section
into the deal's answer key and registers the document:

```
https://ise-prodnr-eu-west-1-data-integration.s3-eu-west-1.amazonaws.com/202502/12423666-a060-4e34-b3e8-f5510297ac6f.pdf
```

Registering it changed no routing at the time — `_reconstruct_series` matched
`tape_urls` first and the derived tapes had been registered since #471 — so the
key was a *claim about published ground truth*, which is all it was meant to be.
**#524 changed that**: this report is now the source the deal's live series
folds. The rule, and what it does to the three periods it does not cover, is
below.

#### Which source a deal's live series folds (the precedence contract, #484/#524)

**Read this before registering a second source against a deal.** A deal may
register both `tape_urls` and `notes_cash_report_urls`, and `_reconstruct_series`
folds exactly one of them into the ledger `/waterfall`, `/compliance` and
`/reconciliation` read. Which one is a stated rule, not dispatch order. The ranks,
senior first:

1. a **first-hand** tape — the originator's own loan-level statement, filed under
   Article 7(1)(a). Nothing published stands closer to the pool, so it keeps the
   tape path. **An undeclared tape identifier counts as first-hand**: it names a
   published file and this repo holds no evidence it is anything less, so
   registering an ordinary tape URL never silently demotes a deal.
2. the deal's **published report** — the document itself.
3. a **derived** or **synthetic** tape — LoanWhiz's rendering of a document the
   deal already publishes, or rows that describe nobody.

A deal yields to its reports when no registered tape is first-hand **and** a
report is registered to yield to. A deal whose only pool data is generated and
which publishes no report (Green Lion 2026-1) keeps its tape path: yielding there
would leave it not-modelable, which is a regression rather than honesty.

Rank 2-over-3 is #484's, written to stop a synthetic Annex 2 pool displacing the
Notes & Cash reports the Green Lion vintages' answer keys grade against. Rank
1-over-2-over-3 is #524's, and it is what moves *this* deal: the reading of a
document does not outrank the document. Cairn's report is its only
Priority-of-Payments-bearing source, the only one a `validated` cell can be
earned on, and the only path already fitted to its eight-class split-B stack —
`ReportAdapter` takes the tranche list from the deal (#527), while the tape
path's `_collections_tranche_args` is still shaped for `class_a`/`class_b`/
`class_c` and refuses this stack outright.

**What happens to the periods the preferred source does not cover.** They are
**set aside, and named** — never dropped in silence. `_set_aside_tape_periods`
returns the reporting date of every tape a yield displaced, and the deal's 422
quotes them. This matters most here, because Cairn's two sources overlap on **no
period at all**: the derived tapes are reconstructed from the December 2024,
February 2025 and March 2025 trustee reports, while the Note Valuation Report is
a January 2025 cut. A rule that narrowed the series to one period without saying
so would trade a blank screen for a misleadingly short one, which is worse —
a short series looks like data.

Set aside is not lost, and not unpublished. Those three periods remain the source
of the deal's collateral time series — the pool analytics read the tapes
directly, not the folded series — and of the committed answer key's covenant
rows, which `quality_harness._grade_covenants` grades from `key.periods` with no
series at all. What they stop being is the *ledger* the waterfall and compliance
screens fold.

**A defect this rule exposes rather than causes, recorded here because it bites
the next registrant too.** `/compliance` builds its period list from
`deal["tape_urls"]` unconditionally and then pairs it positionally against the
folded series' states. For a deal that has yielded, those are different sources:
Green Lion 2024-1 today labels its compliance screen with its synthetic tape's
`2026-04-30` while the states it evaluates are the report's `2025-10-23` →
`2026-04-23`, and reports "across 1 reporting period" for a four-state ledger.
#524 did not fix it — the naive fix (drop the tape periods) strips the pool
analytics the tape-sourced triggers need, turning evaluable triggers unevaluable,
so getting per-period pool analytics onto a report-driven deal is real work with
its own issue. It is named here so it is not rediscovered as a surprise.

**The absence it used to record was a *not-yet*, not a *never*, and that
distinction outlives it.** Leone Arancio and Sol-Lion II still carry no
`notes_cash_report_urls` because no such report is published at all — never the
same absence as Cairn's, which was a published report nobody had read yet.
`tests/test_clo_deal_registration.py` pins both states so they cannot flatten
into "another deal with no report".

**The epic's decisive question — do trustee reports exist for a CLO? — is
answered YES.** Epic #454 was written on the premise that CLO trustee reports are
portal/vendor material and that therefore "this epic promises no validated cell".
That premise is **wrong for this deal**: not only are the monthly trustee reports
free, the **Note Valuation Report carries both an *Interest Priority of Payments*
and a *Principal Priority of Payments*** — the CLO analogue of the Notes & Cash
report that makes Green Lion 2024-1's to-the-cent validation possible. A validated
CLO cell is therefore *feasible* in a way it never was for the Italian and Spanish
deals. **Feasible is still not done, and nothing here is presented as
validation.** #481 authored a key from the *trustee* reports, not this one, so it
grades published coverage-test outcomes and no Priority of Payments; no
validation builder is committed and no cell reads `validated`. Whether this
platform pursues the PoP route — parsing the Note Valuation Report and
registering it — remains a scope decision for the operator.

**Not obtainable — the negatives, recorded because an absent document set is a
finding, not a blank.**

- **No *filed* loan tape — but a derived one is now registered (#471).** Cairn's
  own Article 7(1)(a) Loan Reports are distributed to Competent Authorities,
  Noteholders and prospective investors rather than to a securitisation
  repository, and LoanWhiz does not have them. What it does have is the trustee
  reports' loan-level collateral detail — "Current Asset Characteristics" Parts
  I–III, "Defaulted Collateral Obligation Detail", "Deferring Collateral
  Obligation Detail", "Assets Purchased" and "Assets Sold". That detail is now
  parsed, reconciled against each report's own stated aggregates and resolved
  onto canonical Annex 4 columns, so `tape_urls` carries three **derived** tape
  entries (see [Derived tape](#the-derived-tape--what-it-is-covers-and-omits)
  below and [`tape-ingestion.md`](tape-ingestion.md)).

  The earlier entry here said registering a tape URL "would be a claim the pool
  analytics would then act on". That remains exactly right, and is why the
  derivation is identified by a `derived+trustee-report:` URI rather than a
  plain URL: the claim the analytics act on is now *derived, not filed*, at
  every surface, and no reader can mistake one for the other.
- **The full monthly series is login-walled.** U.S. Bank's CLO investor-reporting
  portal (`pivot.usbank.com`) requires an account (verified: login / password /
  registration). Only what the exchange filed is public, which is why the three
  registered trustee reports are **not contiguous** — Euronext's listing for this
  issuer carries exactly six records and January 2025's monthly report is not
  among them. Nothing is interpolated to make the series look complete.
- **No open loan-level corpus found.** No free, bulk, machine-readable source of
  CLO collateral data surfaced for this deal. European DataWarehouse, the
  ESMA-registered securitisation repository, is the obvious candidate; its data
  portal is account-gated and its terms were **not** established here, so treat
  this bullet as "not found by open search", not as a determination about EDW.

**Redistribution caveat (Reg S / Rule 144A).** The notes were offered under
Regulation S and Rule 144A and listed on a professional-investor exchange market,
not under a retail prospectus regime. The registry stores **URLs only** — LoanWhiz
mirrors no bytes and redistributes none of these documents, and anyone following
the links is subject to the offering documents' own selling and transfer
restrictions.

**What the extraction gives you, and the one thing it does not (#456).** The
pipeline reads the Listing Particulars end to end with no CLO-specific branch —
the same section router, taxonomy and assembler the RMBS deals use. It produces
the full 8-class capital stack with sizes reconciling to the cover page's
EUR 404.1m, both Priorities of Payments bound to *different* sections (a
distinction the router prompt explicitly permits collapsing), and 10 triggers of
which 8 are the per-class coverage tests, each resolving onto a real
per-attachment-point OC/IC metric — including the senior-most, the combined
Class A/B Par Value Test, which the document defines over Class A + Class B
outstanding and which therefore resolves at the Class B point.

**The coverage tests carry no thresholds *from the Listing Particulars*, and
this is a real limitation rather than an absence in the document.** Read that
scope literally — #480 below found the same levels stated outright in a document
this subsection already lists. The levels are stated ("the Class A/B Par
Value Ratio is at least equal to 130.08 per cent") but they live in the
definitions glossary, which runs past the extractor's 40,000-character budget.
A glossary is alphabetical, so truncation loses a *range*, not a sample: the
extraction captured 25 terms spanning "Acceleration Notice" to "Bankruptcy
Exchange Test", and every coverage test is defined under C–F. Since #548 the
truncation is a **structural fact the extraction result carries** — how much was
discarded and the last line seen — reaching a caller through
`metadata.truncations` rather than only a log line, so the shortfall is visible
rather than inferred from a healthy-looking term count. Downstream this degrades
honestly — a coverage test with no quantified threshold is reported
`not_evaluable` with that reason, never as a passing test. Capturing the levels
is the obvious next increment and is **not** done here.

**Corrected by #480: the levels are obtainable, from a document already in this
registry.** The finding above was right about the *offering circular* and wrong
about the *document set*. Every monthly trustee report states each coverage
test's required level twice: once in the Executive Summary
(`Test Description · Threshold · Current · Result`) and once on the Par Value
Tests Detail and Interest Coverage Tests Detail pages
(`… TEST · RATIO · REQUIRED LEVEL · CALCULATION · RESULT`) — in **opposite**
column order, which makes the pair a cross-check rather than a transcription
risk. All nine are now extracted by `collateral_schedule_parser`'s
`parse_liability_summary_text`, from the same reports and the same seam the
collateral schedule is read from, reconciled against both renderings, and
identical across all three reporting dates as a deal term must be:

| Test | Required level | March 2025 ratio |
|---|---|---|
| Class A/B Par Value | 130.08% | 139.43% |
| Class C Par Value | 121.74% | 129.07% |
| Class D Par Value | 112.62% | 118.92% |
| Class E Par Value | 107.87% | 113.15% |
| Class F Par Value | 103.90% | 108.68% |
| Reinvestment Overcollateralisation | 104.40% | 108.68% |
| Class A/B Interest Coverage | 120.00% | 184.35% |
| Class C Interest Coverage | 110.00% | 166.55% |
| Class D Interest Coverage | 105.00% | 146.04% |

The same parse also takes each class's **resolved current coupon** — Class A's
`4.54400` for March 2025, where the circular can only say
`3 month EURIBOR + 1.80%` — and its periodic interest.

**#528: the accrual period is obtainable too, and from the circular itself.**
The same 40,000-character truncation that lost the coverage-test levels also lost
`"Payment Date"` and `"Business Day"` — both fall in the dropped C–Z range — and
with them the deal's *Accrual Period*, which the seed does state and which is
defined payment-date to payment-date. Without it the engine accrued Act/360 over
a hardcoded 90-day quarterly approximation, and that assumption was the entire
residual on the two interest lines it computes for itself.

The remedy is #480's, applied to a different lost fact: a deterministic parser
over the document's own text rather than a wider LLM budget.

**#548: the same fact was lost again on a second deal, by a different
mechanism — which is why the check is on the output, not the cause.** Contego
CLO XI's glossary never reached the 40,000-character budget at all: its
definitions section arrived at **3,255 characters**, so nothing was truncated
and the extraction succeeded quietly on four terms. The cause sits one stage
earlier, in `route_sections`. Docling renders many of this prospectus's defined
terms as markdown *headings* — `' Payment Date ' means:`, `' Measurement Date '
means:` — and a section ends at the next heading, so `1. Definitions` stopped at
the first of them and the rest of the glossary became sibling sections nobody
sent. The contiguous span from `1. Definitions` to `2. Form and Denomination`
measures **246,245 characters carrying 445 defined-term entries**; 1.3% of it
was extracted.

So the same fact — `"Payment Date"` — was lost on Cairn by truncation and on
Contego by orphaning. Widening the budget would have fixed neither this document
nor the next one. Three things follow, and they are what #548 built:

- **Truncation is structural at both sites.** `waterfall_extractor` was the more
  dangerous one: it had no warning at all, so a Priority of Payments section over
  its 20,000-character budget was cut *mid-cascade* in silence, and a truncated
  cascade does not look broken — it looks like a shorter deal.
- **The definitions stage judges its own output.** `metadata.glossary_coverage`
  reports the term count, the alphabetical span and an `implausible` verdict,
  and it reaches that verdict **without reference to which mechanism caused
  it** — a truncated section, a section too small to be a glossary at all, or
  too few terms from a healthy one. A repair aimed at either individual cause
  would not have caught the other.
- **The routing itself is repaired, under a guard.** A numbered definitions
  heading whose entries Docling promoted to siblings now widens to the next
  *numbered* sibling. It is a strict no-op when the routed section already
  carries a real glossary, which is the Cairn and Green Lion case — so no
  existing deal's extraction moves.

**What was priced and not built.** Widening the span makes the 40,000-character
budget bite where it previously did not, so the remaining question is a larger
constant versus chunking. Chunking wins, and not on cost: 445 definitions
emitted as full text need roughly 60,000 output tokens against Gemini 2.5 Pro's
65,536-token ceiling, so a single widened call would truncate the *function
call* — a partial tool payload, which fails less visibly than a truncated input.
It is deferred because it would move Cairn's extraction, which feeds a committed
answer key and a graded reconciliation. The truncation record is what makes that
follow-on decidable rather than speculative.
`extraction/payment_schedule_parser.py` reads the schedule from the Listing
Particulars' text layer via `pypdf` — no OCR, no model call, the same bytes every
run — and the seed now carries it, plus both definitions verbatim:

| Stated term | Value | Source |
|---|---|---|
| Scheduled Payment Dates | 18 January, 18 April, 18 July, 18 October | Listing Particulars, Definitions — "Payment Date" |
| First Payment Date | 18 April 2024 | same |
| Business-day convention | Modified following | same |
| Business Day centres | T2, London, Dublin **and New York** | Listing Particulars, Definitions — "Business Day" |

**The January 2025 Accrual Period is therefore 95 days** — from the 18 October
2024 Payment Date to the 21 January 2025 one. Both endpoints are stated dates,
not fitted ones, and the number is measured between them rather than read off any
published amount. That distinction is the whole point: 95 was previously
reachable only by dividing the published Class A interest by what the engine
computed, and #521 stood down rather than commit a figure back-solved from the
answer it was meant to check. The derivation agreeing with that figure is its
result, not its method.

**The holiday table is a hand-authored input, and it is cross-checked rather than
trusted.** Resolving a scheduled date needs published holidays for four centres,
so `BUSINESS_CENTRE_HOLIDAYS` carries them for 2024–2026 with per-centre
provenance (ECB fixed closing days; GOV.UK; Citizens Information; US OPM), and
raises rather than guessing outside those years. The control on it is that the
reports state the Payment Dates they actually paid on: 18 January 2025 is a
Saturday and the Monday is Martin Luther King Jr. Day, giving the 21st the
December and January reports both print; 18 April 2025 is Good Friday and the
21st Easter Monday, giving the 22nd the February report prints. A mis-entered
holiday breaks that match, which `tests/test_payment_schedule_parser.py` asserts.

Two bounds. The derivation reads **Scheduled** Payment Dates, while the
definition also admits unscheduled ones — the March 2025 report states a
28/03/2025 redemption date on no scheduled month — so a period ending on one is
shorter than the schedule predicts; the graded fold uses only the January Note
Valuation Report, and the case is pinned rather than silently handled. And the
90-day default itself is unchanged: it remains a fair approximation where a deal
states nothing better, so every deal without a committed schedule — Green Lion
included, which stays byte-identical — keeps the numbers it had.

**#539: the day-count *fraction* is per class, and the Conditions state it.**
#528 sourced the accrual period; it did not source the convention that period is
counted on, and the engine applied one — Act/360 — to every class. #538 measured
that this deal does not have one: Class B is issued in two strips under two
conventions, so no deal-level setting can express it. The fraction sits in the
*Conditions*, a section the definitions extractor never emits at all, so the same
`pypdf` route recovers it: `extraction/day_count_parser.py` reads Condition 6(e)
off the text layer, and the seed carries the result per class.

| Class | Basis | Accrual Period measured over | Source |
|---|---|---|---|
| A, B-1, C, D, E, F | Actual days / 360 | Adjusted Payment Dates | Listing Particulars, Conditions — 6(e)(ii) |
| B-2 | 360-day year of twelve 30-day months | **Unadjusted** Payment Dates | Listing Particulars, Conditions — 6(e)(iii) |

Condition 6(e)(iii) states the fixed basis outright — *"Interest is calculated on
the basis of a 360-day year consisting of 12 months of 30 days each"* — and the
*Accrual Period* proviso states which Condition's dates go unadjusted; the parser
reads which limb that proviso names rather than assuming it is the fixed one.
**Nothing here was chosen because it made a published figure tie**: the residual
#538 measured was used as neither a target, a check nor a bound, and had the
Condition stated something else, that would have been the finding.

**Two independent documents agree on which class is fixed.** The prospectus states
a fixed day-count basis for Class B-2 alone; the Note Valuation Report separately
prints Class B-2 as `FXR` where every other class is `FLR`, and
`note_valuation_parser.stated_rate_types` captures that marking — previously
discarded as furniture — so the two can be compared. They agree. A disagreement
would be reported as a finding rather than reconciled, since choosing between two
documents is not a parser's judgment to make.

**Where the convention is not obtainable, it is refused rather than defaulted.**
"12 months of 30 days each" names a *family* — 30/360 US, 30E/360 and 30E/360
ISDA — whose members differ only when an endpoint is the 31st or the last day of
February. Every Payment Date this deal states is the 18th, so all three agree
everywhere the schedule reaches and no unstated pick is needed; an endpoint where
they would diverge raises instead, because there the document genuinely has not
decided. The same holds for a class stating no basis at all, and for an
Unscheduled Payment Date, which has no unadjusted counterpart to measure between.

**A deal stating no per-class convention is untouched.** Green Lion states none,
so its seed carries no `note_day_counts`, every tranche keeps the deal-wide count,
and its graded output stays byte-identical — asserted per seed rather than
assumed.

**Composed with #538, this closes the Interest cascade.** #538 resolved the
recipient-to-tranche seam so `class_b_interest` reaches both strips and sums them;
#539 gives each strip its own basis, which is what makes the sum right — B-1 over
95 actual days, B-2 over 90 on 30/360, reproducing EUR 386,773.50 and
EUR 257,625.00 against the published pair. Every step of the 29-step Interest
cascade now agrees and `engine_computed_passed` reads 3.

**That the figures tie is the result, not the method.** The fractions were read
out of Conditions 6(e)(ii) and 6(e)(iii) and the dates off the stated schedule; no
published amount was divided by anything to obtain either, and the residual #538
measured was used as neither target, check nor bound. Had the Condition stated a
different fraction, the line would be red and that would be the finding.

**What this does *not* change: on the deal's own state the monitor still reports
every coverage test `not_evaluable`, and both of the reasons above still hold.**
These figures are extracted and reconciled, not wired: nothing writes them onto
the deal model's triggers, which still carry `threshold: null` (#478/#479 own the
config shape). And per #457 below, the threshold gap is not even the refusal that
fires first. A reader taking this paragraph as "the coverage tests now evaluate"
would be making exactly the inversion #457 warns about.

**#481 grades these outcomes without closing that gap, and the distinction is
the whole of what it claims.** The published results are committed as an answer
key and `GET /quality-matrix` grades the `covenants` row against them — but the
level *and* the ratio it compares are both supplied from that key, because the
deal model states no threshold and the engine cannot yet compute the ratio for
this stack. So the graded row exercises the monitor's threshold comparison,
direction and pass/fail polarity over published figures; it does **not** show
the engine reproducing a coverage ratio from the collateral. `/compliance` still
refuses this deal. See [What the graded covenants row does and does not
prove](#what-the-graded-covenants-row-does-and-does-not-prove).

#### What the graded covenants row does and does not prove

**What it proves.** For every coverage test the three trustee reports decide,
across all three reporting dates, the engine's verdict matches the trustee's.
Removing the published threshold makes the row read `failed` rather than
`passed` — the Reinvestment Overcollateralisation Test's metric resolves to no
coverage sentinel, so it escapes the "no quantified threshold" guard and falls
through to the PDL convention where any positive value fires, reporting a
healthy ratio as breached. Inverting the comparison direction also reds the row.
Both were applied and observed, not asserted.

**What it cannot prove, stated because the shape of the data bounds it.** Every
outcome these reports decide is `Passed`. An engine that reported *nothing* as
breached would therefore match all of them and the row would still read
`passed`. This cell can catch a wrong direction, a dropped or disagreeing
threshold, an unresolvable metric and a unit error; it cannot catch a
permanently non-firing monitor, and no amount of care in the key changes that —
only a period in which this deal actually failed a test would.

**And one test is excluded rather than coerced.** The Class F Par Value Test is
stated `N/A` in every period. `CovenantResult.passed` is a boolean and cannot
express "did not apply", so writing `true` there would publish a pass the
trustee never stated. It is absent from the key, and
`tests/test_reconciliation_answer_key.py` asserts both that it is absent and
that its pass/fail siblings are present, so the exclusion cannot silently become
a dropped section.

Two further limits, stated because they bound what the figures are: they are
**report-derived facts, not prospectus terms** — one month's stated figure, not
the contractual definition — so provenance records them as `source="report"` and
the parser offers no way to say otherwise; and the Collateral Quality Tests on
the same page (`Weighted Average Life Test`, the two Fitch tests) are *not*
captured, because they carry no `%` terminator and their two figures cannot be
separated unambiguously.

**Refined by #457: the missing threshold is real, but it is not the refusal that
fires first.** Running the deal revealed that on the actual eight-class stack
every coverage test refuses one layer earlier, in the structural metric itself:
the Subordinated Notes carry no class letter, so #452 cannot place the tranche in
the capital structure and correctly refuses the whole metric rather than trusting
a denominator it cannot verify. The threshold reason is only reached once that
tranche is set aside. Both refusals are honest and both are separately fixable,
which is why the distinction is recorded rather than smoothed over — a reader
told only about thresholds would fix thresholds and find the tests still refusing.

Worth noting for whoever takes that on: the equity tranche is junior to every
attachment point, so it enters no coverage denominator and placing it would leave
every ratio unchanged. With it set aside the engine produces a correctly-ordered
OC ladder that falls as the attachment point deepens, then refuses again for the
documented threshold reason. Changing the placement rule is a change to #452's
deliberate conservatism in `covenant_monitor.py`, so #457 surfaced it rather than
making that call unilaterally.

**Two reporting reasons were inaccurate for this deal; #457 corrected both.**
They lived in `capability_matrix.py` and are recorded here because the wording of
a refusal is load-bearing — "nothing is published" says a deal *cannot* be
validated, which is a different and stronger claim than "nobody has authored the
key yet".

- `engine_validation` said "No published Notes & Cash Priority-of-Payments report
  to reconcile the engine against for this deal." For Cairn that was **factually
  false** — the Note Valuation Report publishes both Priorities of Payments. The
  reason now states only what the classifier actually verified, explicitly noting
  that this says nothing about what the deal publishes. The richer wording
  ("reports exist, but nobody authored a key") was tried and rejected — the
  registry cannot support it, since `investor_report_urls` counts periodic reports
  rather than PoP reports and this deal deliberately leaves
  `notes_cash_report_urls` unset. It would have been false of Green Lion 2023-1,
  which has a committed answer key. For Cairn the substantive answer stands and is
  recorded here rather than inferred in code: unvalidated for want of an authored
  key, not for want of an obtainable report.

  **#492 changed what is verified, not that discipline.** The classifier now reads
  the answer-key registry rather than the hand-built `_VALIDATION_BUILDERS` map,
  so the single refusal became three, each naming the precondition that is
  genuinely missing: no committed answer key, a committed key carrying no
  Priority-of-Payments section (Cairn's shape between #481 and #495), or a
  PoP-bearing key with no committed offline engine series — which is Cairn's
  shape now, and the reason its `engine_validation` cell still refuses. That
  reason is a fact about this repo's registry and stayed accurate through #496,
  which measured what such a fold produces without registering one: the grade
  fails. #513 changed what registering a series would now produce — a real
  failing grade rather than a join-error string — by teaching the grader to
  grade the key's PoP-bearing period and report the other three not-applicable;
  it did not register one, so the cell has not moved and the refusal above is
  still the accurate one. Each still ends with the same explicit disclaimer,
  because the registry still cannot see what an issuer publishes.
  Splitting the reason is what #471 asks for — "no key is committed" and "the
  committed key carries no PoP" are different findings, and one sentence covering
  both is false of one of them.
- `tape_analytics` and `collateral_reconciliation` said "No loan tapes published
  for this deal", which was true of ESMA tapes but read as the stronger claim that
  no loan-level data exists. Both now say only what `tape_urls` encodes.

**#471 moved the same risk into the positive branch, and it needed the same
discipline.** Registering a derived tape flips these cells off `not-applicable`,
and the reason that replaces a false negative can be a false positive:

- `tape_analytics` is now `ran`. Its old positive wording called every tape an
  "ESMA tape URL", which would have described a reconstruction as a published
  regulatory tape. It now counts tapes by declared source kind and quotes
  `TapeSourceKind.disclosure` verbatim, so the cell states that these three are
  **not** filed under Article 7(1)(a).
- `collateral_reconciliation` is **still `not-applicable`**, and this is the
  substantive point rather than a technicality. A registered tape is necessary
  but not sufficient: the per-period pool-state reconstruction folds each period
  through the engine, which needs `capital_structure`,
  `reserve_account_target` and `original_pool_balance` — none of which this deal
  registers. Reporting `ran` off tape count alone would have swapped one false
  reason for another. The cell names the missing configuration instead, and
  explicitly says the tape is present. **This bullet used to end "so `GET
  /deal/cairn-clo-xvii/waterfall` answers a labelled 422", and #525 measured
  that clause false** — the endpoint serves 200 by folding the published Note
  Valuation Report, a path that needs none of those three fields. The rest of
  the bullet stands: the *tape-driven* pool-state reconstruction still needs
  them, which is what this cell is actually about.
- The `waterfall_execution` qualifier moved with it. It previously fired only
  where the registry proved there was no period source at all; registering the
  tape would otherwise have silenced it and left the cell reading as though the
  endpoints now serve this deal. It names the missing structural configuration
  instead — **and that qualifier has since inverted into the error it was
  written to prevent (#525).** The endpoints *do* now serve this deal, so a
  reason still saying they "cannot yet" is a false negative of exactly the kind
  this section exists to catch: the guard against overclaiming became an
  underclaim the moment the refusal beneath it was fixed. It lives in
  `capability_matrix.py` and is recorded rather than corrected here.

### The derived tape — what it is, covers and omits

**What it is.** Three per-period tapes reconstructed by LoanWhiz from Cairn's
monthly trustee reports and resolved onto canonical ESMA Annex 4 (Corporate)
columns. **It is not a regulatory filing** and is not Cairn's Article 7(1)(a)
Loan Report. Each tape's `TapeSourceKind` is `derived_from_investor_report`, and
every provenance surface — `data_source`, the tape citation in the evidence
pack, the capability-matrix cell reason, this card — renders that kind's own
disclosure sentence rather than a local paraphrase.

**What it covers.**

| Period | Reporting date | Assets | Aggregate par (EUR) |
|---|---|---|---|
| December 2024 | 2024-12-16 | 193 | 407,181,748.22 |
| February 2025 | 2025-02-18 | 191 | 401,342,140.14 |
| March 2025 | 2025-03-18 | 196 | 411,342,140.14 |

Every figure is reconciled at parse time against the source report's own stated
asset count, aggregate balance and per-bucket distributions; a schedule that
does not tie back is refused rather than returned (#469). The asset count
genuinely moves between periods — this is a time series, not one cut repeated.

*(These figures correct an earlier estimate of "162 distinct facilities,
~EUR 354m par" that appeared in the planning issues: it came from an `LX`-only
identifier regex that silently skipped 34 ISIN-identified assets.)*

**What it omits.** Twenty-eight canonical columns are emitted with 13 genuine
`CRPL` locators. Thirteen Annex 4 fields are declared **absent** — meaning the
source is silent, never that the value is zero, and the key is omitted from the
row entirely rather than filled with a `0`:

- **Credit performance is not published at all**: arrears balance (`CRPL77`),
  days in arrears (`CRPL78`), account status (`CRPL79`), default amount
  (`CRPL81`), cumulative recoveries (`CRPL84`). Because the tape states arrears
  in no form, the pool analytics emit **no** arrears breakdown for it rather
  than the `current_pct: 100.0 / default_pct: 0.0` a missing-column fallback
  would otherwise produce — a clean pool and an unreported one must not look
  alike.
- **Original balance** (`CRPL38`) and **Basel segment** (`CRPL15`) are absent.
- **`enterprise_size`** (`CRPL16`) is absent, which is why the annex is stated
  rather than detected: it is Annex 4's entire detection signature.
- **`market_value`** (`CRPL41`) is absent. The schedule's "Market Value" column
  is a **price per 100 of par** (e.g. `99.72`), not an amount — the report's own
  Assets Sold page proves it — so it lands on a code-less `market_price_pct`.
  A market value is derivable as `par × price / 100`, but LoanWhiz does not
  perform that derivation, and no column here silently stands in for it.
- **Industry is not NACE.** The report gives S&P and Fitch industry names; they
  resolve onto code-less columns, **not** `CRPL14`, which is defined as a NACE
  code.
- **Country is not NUTS-3.** The report gives a country ("Luxembourg"); it
  resolves onto a code-less column, **not** `CRPL10`, which is a NUTS-3 region.
- **Obligor identity** (`CRPL1`, `CRPL4`) is absent: the report names obligors
  in prose, and a name is not an identifier.

Values are the source document's own words (`Senior Secured Loan`), **not** ESMA
RTS coded vocabularies (`SNDB`) — `TapeSourceKind.rts_coded_values` is `False`
for this tape, and any consumer comparing against an RTS code must check it.

**What it still does not unlock.** No `validated` cell. Since #492 that state is
earned by two committed artifacts: an answer key carrying a Priority-of-Payments
section, and an offline engine series to reconcile it against. #495 supplied the
first — the key's January 2025 period carries both waterfalls from the Note
Valuation Report — so the cell refuses on the second. #496 ran the reconciliation
without one and reported that it failed: the Interest cascade was short EUR
1,820,150.42 of the report's stated available revenue, the published rows that
joined no cascade step at all. #514 closed the join and #538/#539 gave Class B a
computable, correctly-counted need, so all 29 Interest steps now agree and three
are independently computed (#511/#512/#528/#538/#539). The cell would not have
turned green either way: it is earned by an offline engine series, which this
deal still does not have.

**And if it did, `validated` would mean something narrower here than it means
for Green Lion — so read the count, not the word.** Across the two cascades this
deal publishes, 52 steps are compared and **3 are engine-computed; 49 are
report-supplied**. Every one of the 3 is a note-interest line — Class A, Class B
and Class C — derived from the seed's tranche balance, the report's published
applied rate and a day count read off the prospectus Payment Date schedule and
Conditions 6(e)(ii)/(iii). The remaining 49 have their amount taken from the
report and compared to itself, so they demonstrate that this repo *routes* a
published figure to the right step in the right order, never that it *computes*
the figure. That split is not a defect to be engineered away: a CLO waterfall
carries steps no deal model can derive — management fees, capped administrative
expenses, coverage-test and par-value-test cures, hedge and swap payments —
whose amounts come from the manager's and trustee's own books rather than from
any formula in the Listing Particulars. On this period 26 of the 49 sit in the
Interest cascade and 23 in the Principal one; 40 of the 52 compare EUR 0.00 with
EUR 0.00 and could not have distinguished a correct engine from a silent one.
So the reconciliation's whole independent signal is 3 lines carrying EUR
4,336,860.61 of the Interest cascade's EUR 7,255,062.35 pot.

**Why 3 and not 7 is a fact about this repo, not about the document.** The
engine computes a note-interest need only for recipients named in
`primitives/step_source_classifier.ENGINE_COMPUTED_RECIPIENTS`, which stops at
`class_c_interest` — a set authored for a three-tranche RMBS stack. Cairn's
Classes D, E and F reach the fold with everything those three have: a seeded
balance, an applied rate in the same published `Rate Current` column, and a
day-count basis parsed from Condition 6(e)(ii). Each reproduces its published
interest exactly — EUR 594,969.17, EUR 484,208.67 and EUR 495,004.89 — so the
figure 3 bounds a declaration this repo authored, not the data the deal
publishes. Widening it is a change to the engine and was deliberately not made
while measuring the engine.

**The engine executes this deal (#457).** The committed seed folds through the
existing `run_period` kernel — the same one the RMBS deals use — over the full
eight-class stack, running the deal's own Interest and Principal cascades rather
than the built-in RMBS defaults. No new execution path was added: `run_period`
already accepts extracted steps and triggers as parameters, so wiring a CLO
through it is parameterisation. `tests/test_clo_engine_execution.py` pins this.

Two things that proof deliberately does **not** claim. It exercises the engine
*kernel*, not the ingestion path: the production `/deal/{id}` reconstruction
still refuses this deal with a labelled 422. **The reason has changed and the
old one is no longer true** — the deal registers both a derived tape (#471) and a
Notes & Cash report (#495), and since #524 it routes to the report path, where it
refuses because that report resolves offline for no committed fixture and no
durable cache. Supplying that carrier is what remains. And it validates nothing — no cell reads `validated`, because
no validation builder is committed. #481's answer key does not change either
statement: the graded `covenants` row is reached through `/quality-matrix`, not
through the deal's reconstruction, which still 422s on `class_a_rate_pct`.

A third, narrower gap: `RegisterDealRequest` (`api/main.py`) is a fixed whitelist
of registry keys and does not list `asset_class`, so a deal registered at runtime
through `POST /deals` would have that key **silently dropped** (Pydantic's default
`extra='ignore'`). The committed registry is unaffected; #457 owns the fix.

**Honesty note on the non-English deals.** The Italian and Spanish figures above
are the **post-#438/#439 re-extractions**; this card previously reported them as
"≈ 0.38 / ≈ 0.30, no waterfall", which described seeds extracted before those
fixes landed. Their coverage is now comparable to the Dutch deals — **but
coverage is not validation.** Their capital structures reconcile independently
to the curated `deals.json` registry, which no part of the extractor reads; that
is a real check, and it is internal. Neither deal publishes a Notes & Cash
report, so neither can be graded against published actuals, and no answer key is
invented for them. They remain `ran` (not `validated`) cells in the capability
matrix. Nothing about the cross-jurisdiction coverage should be read as
"validated across all deals" — exactly one deal (Green Lion 2024-1) is validated
against external published actuals, one more (Green Lion 2023-1) is graded to
the cent against a committed answer key, and the CLO has one row graded against
published coverage-test outcomes.

The four non-2026 deals carry a `jurisdiction` field in the registry where they
are non-Dutch (`"Italy"`, `"Spain"`, `"Ireland"`), and every entry in the shipped
`deals.json` now also declares an `asset_class` (`"RMBS"` × 4, `"CLO"` × 1) so the
CLO is the first member of a dimension rather than the registry's special case.
Both keys are **additive and optional**: the in-code Green Lion 2026-1 default
carries neither, and a reader resolves a default for an absent key exactly as
`capability_matrix._resolve_jurisdiction` already does. Their loan tapes follow the same
ESMA-format ingestion path; the same synthetic-vs-real and snapshot caveats below
apply to whichever tapes are synthetic.

---

## IMPORTANT: Synthetic vs Real Data

> **The loan-level data (loan tapes) in this dataset is SYNTHETIC.**

This is the most important disclosure in this data card. Specifically:

| Component | Nature | Notes |
|---|---|---|
| **Prospectus** | **REAL** | The offering document for the Green Lion 2026-1 deal |
| **Investor reports** | **REAL** | Monthly investor reports for February, March, and April 2026 |
| **Loan tapes (ESMA Annex 2)** | **SYNTHETIC** | Loan-level data is synthetically generated to approximate a realistic Dutch RMBS pool; it does not represent real borrower or loan data |

The synthetic loan tapes are identified in the HuggingFace dataset by the `_synthetic_loan_tape` suffix in their filenames. They were generated by Algoritmica.ai to provide a realistic ESMA-format loan-level dataset for research, testing, and framework demonstration purposes in the absence of publicly available real loan-level data.

**This card is no longer the only place that says so (#483).** A filename suffix and a document are read by people; they are not read by the evidence pack, the capability matrix or the tape citation. Until #483 these tapes therefore reported `data_source: "direct"` — the same ingestion provenance a filed Article 7(1)(a) regulatory tape reports — so the platform could not distinguish a synthetic pool from a real one *at the point where it makes claims about the deal*.

Each tape is now registered under a `synthetic:` identifier, giving it
`TapeSourceKind.SYNTHETIC_GENERATED`, `data_source: "synthetic"` and
`describes_real_assets: False`. Every provenance surface renders that kind's own
disclosure sentence, so a reader who never opens this card is still told. The
prefix strips off before the file is fetched, so the pool figures are unchanged
— re-running the normaliser across the re-identified tapes moved `data_source`
and nothing else. See [`tape-ingestion.md`](tape-ingestion.md).

### The four fitted pools (#484)

Green Lion 2026-1's tapes above were supplied ready-made. The other four deals
publish **no loan tape in any form**, so LoanWhiz generates one for each — and
because a synthetic pool that contradicts its own deal's investor reports would
be worse than no pool, each is **fitted to figures that deal itself publishes**.

| deal | fitted from | fitted on | the source states no |
|---|---|---|---|
| Green Lion 2023-1 | its own monthly *Portfolio and Performance Report* | Key Characteristics + Delinquencies, Interest Payment Type, Property Description, EPC, province | — |
| Green Lion 2024-1 | same | same | — |
| Leone Arancio 2023-1 | its own *Monthly Investor Report* | Summary + Arrears, Interest Type, Geography Region | energy label, property type |
| Sol-Lion II | same | same | energy label, property type |

Each pool's committed **fit spec** — `src/loanwhiz/data/pool_fits/<deal>.json` —
is the authority for what was fitted, and every figure in it names the report
section it was read from. `scripts/investor_report_pool_fit.py` re-derives the
spec from a committed text extract of the published report;
`scripts/generate_synthetic_tapes.py` builds the pool from the spec and
**refuses to write it** unless it reproduces every fitted aggregate. Both are
deterministic and offline, so anyone can re-run them and diff.

**What is fitted, and what is only shape.** Fitted: the loan count, pool
balance, balance-weighted coupon, seasoning, remaining term and current LTV,
and the balance share of every distribution above. Arrears is additionally
fitted on the published loan *count*, because that is how the platform renders
the breakdown. Everything else — the spread of balances within a bucket, the
correlation between one distribution and another — is **generated**, and no
figure computed from it is evidence about the real pool.

**Three consequences worth stating plainly.**

- **Neither Iberian report publishes an energy label or a property type**, so
  those columns are *absent* from those two tapes rather than invented. Annex 2's
  detection signature is exactly those two fields, so both tapes read as
  `Unknown ABS` at reduced confidence — the honest outcome, not a defect.
- **The tape's arrears vocabulary is coarser than the reports'.** It has three
  states and a default flag, so the reports' finer day-count buckets are mapped
  onto it; the mapping is recorded in each fit spec, the report bucket's own
  lower bound is preserved in `days_in_arrears`, and a bucket that maps to
  nothing raises rather than falling through to "performing". Leone Arancio
  publishes a *Payment Holiday* bucket outside its days-past-due ladder: those
  loans are written as performing, since the report does not state them as in
  arrears, and the fit spec names the share that was moved.
- **These pools now drive more than the pool charts.** Leone Arancio and
  Sol-Lion II previously refused to model at all; with a pool registered they
  fold a full ledger, so their waterfall, compliance and reconciliation views
  serve numbers built on generated collections. Every capability cell for them
  carries the synthetic disclosure verbatim, and none of them is `validated`.

**What a synthetic pool still cannot reach.** Green Lion 2023-1 and 2024-1 also
publish quarterly Notes & Cash reports, and those — not their pools — are what
their committed answer keys grade against. `_reconstruct_series` keeps a deal on
its report path whenever every registered tape is synthetic and a real report
path exists, so registering these pools did not move either deal off the ground
truth it is graded on. Green Lion 2024-1's engine reconciliation remains the
repo's only `validated` cell.

### The illustrative demo book (#571)

The pools above say what a deal's collateral is. The **demo book** says who
holds part of one — and nobody does. `src/loanwhiz/data/books/demo-book.json`
is a set of positions committed **beside the builder that produced it**
(`src/loanwhiz/data/demo_book.py`), the same arrangement the fit specs have
with their generator, and for the same reason: a book of holdings with no
visible construction is indistinguishable from a claim that someone holds them.

**The deals and the tranches are real; the holdings are not.** Each position
names a registered deal and resolves its class against that deal's actual
capital structure — so a position in Cairn's `class_b` resolves to the two
strips Cairn really sold it in, `class_b_1` and `class_b_2`, and a class the
deal does not carry is **refused rather than sized at zero**. What is invented
is only the size, and the fact that anybody holds it at all.

**Every row says so itself.** Each position carries
`PositionProvenance.ILLUSTRATIVE` and that kind's own disclosure sentence, in
the record, so a consumer inherits the claim instead of remembering to add it.
This is deliberately not a flag on the book: the lesson from the synthetic
pools directly above is that a qualifier a surface has to *remember* is one it
can forget — those pools are correctly labelled in the data and the Pool and
Waterfall pages still render no badge. Rendering it is tracked separately;
what the data guarantees is that the qualifier cannot arrive missing.

Regenerate with `python -m loanwhiz.data.demo_book --write`; the suite asserts
the committed file is byte-identical to a fresh build, so it cannot drift from
the code that claims to produce it.

**Consequence:** The loan tapes do not represent real borrower behaviour, real loan performance, or real default history. Any analysis of loan-level metrics (arrears rates, default rates, prepayment rates, LTV distributions) reflects the synthetic generation process, not observed market behaviour. These metrics must not be used to draw conclusions about Dutch RMBS performance, ING Bank's mortgage book, or the Green Lion 2026-1 deal's actual credit performance.

---

## Deal Structure

### Overview

| Field | Value |
|---|---|
| **Deal name** | Green Lion 2026-1 B.V. |
| **Asset class** | Dutch RMBS (Residential Mortgage-Backed Securities) |
| **Annex format** | ESMA Annex 2 (Residential Loans) |
| **Originator** | ING Bank N.V. |
| **Issuer** | Green Lion 2026-1 B.V. |
| **Jurisdiction** | Netherlands (Dutch law governed) |
| **Currency** | EUR |

### Pool

| Field | Value |
|---|---|
| **Approximate pool size** | ~3,275 residential mortgage loans |
| **Approximate outstanding balance** | ~€1.05 billion |
| **Loan type** | Dutch residential mortgages |
| **Property type** | Owner-occupied residential |

> Pool statistics are approximate, derived from the synthetic loan tapes and the investor reports. They reflect the synthetic dataset, not necessarily the actual Green Lion 2026-1 deal parameters.

---

## Time Period

Green Lion 2026-1 provides **3 monthly loan-tape snapshots** — February, March, and April 2026 (January 2026 absent):

| Period | Source dataset | Type |
|---|---|---|
| February 2026 (2026-02-28) | `Algoritmica/green-lion-2026` | Loan tape (SYNTHETIC) + Investor report (REAL) |
| March 2026 (2026-03-31) | `Algoritmica/green-lion-2026` | Loan tape (SYNTHETIC) + Investor report (REAL) |
| April 2026 (2026-04-30) | `Algoritmica/green-lion-2026` | Loan tape (SYNTHETIC) + Investor report (REAL) |

That is **3 monthly tapes** for Green Lion 2026-1, each with a matching real investor report. **January 2026 is an intentional gap** — no tape exists for it in the dataset.

---

## Documents

### Prospectus (Real)

- `green-lion-2026-1-prospectus.pdf` — The full offering prospectus for Green Lion 2026-1 B.V. Contains the deal structure, Priority of Payments (waterfall), Definitions, Covenant and Trigger conditions, Conditions of the Notes, and Eligibility Criteria.

The prospectus is the primary input to the LoanWhiz Extraction Pipeline. Key sections validated during LoanWhiz development:
- Section 5.2 (Revenue Priority of Payments) — 11 steps extracted correctly
- Definitions section — extracted; cross-reference resolution requires review

### Investor Reports (Real)

Monthly investor reports for February, March, and April 2026. For a Dutch RMBS these are **collateral-side** reports (Portfolio & Performance): pool balance, collections, arrears, and stratifications. The deal's separate quarterly Notes & Cash report — which would carry note-level actuals (per-step waterfall distributions, note balances, reserve/PDL) — does **not** exist for 2026-1 within the Feb–Apr window (it is quarterly, and 2026-1's first such period falls after the demo window). This shapes the reconciliation model below.

#### Reconciliation split (what is reconciled vs reconstructed)

- **Collateral** (pool balance, collections, arrears) is reconstructed from the tapes and **reconciles to the published monthly investor reports to the cent**.
- **Liabilities** (tranche balances, PDL, reserve account) are **reconstructed from the prospectus and invariant-validated** (conservation, non-negativity, chaining) — *not* reconciled against a report, because no note-level actuals report exists for 2026-1 in-window.

This split is deliberate and honest: liability figures are prospectus-derived and consistency-checked, not matched against an external actuals report. (The seasoned Green Lion deals targeted by epic #206 *do* publish Notes & Cash reports, which is what makes external liability validation possible there.)

### Loan Tapes (SYNTHETIC)

ESMA Annex 2 format CSV files, one per monthly reporting period. Green Lion 2026-1's three tapes (in `Algoritmica/green-lion-2026`) are:
- `green_lion_202602_1_synthetic_loan_tape.csv` (February 2026)
- `green_lion_202603_1_synthetic_loan_tape.csv` (March 2026)
- `green_lion_2026_1_synthetic_loan_tape.csv` (April 2026)

All three tapes contain loan-level fields per ESMA's Annex 2 specification: loan identifiers, outstanding balance, original balance, interest rate, rate type, remaining term, LTV, geographic region, EPC rating, arrears status, and other regulatory disclosure fields.

**Ingestion is format-agnostic.** The `esma_tape_normaliser` primitive routes each tape by its URL/path suffix — `.parquet`/`.pq` via `pandas.read_parquet`, anything else as CSV — so a tape published in either format works unchanged. The loader can also slice a single reporting period out of a combined multi-month parquet by `reporting_date`.

---

## Intended Use

This dataset is used by LoanWhiz for:

1. **Framework testing** — validating that the extraction pipeline correctly processes a complete Dutch RMBS prospectus
2. **Primitive development** — developing and testing the waterfall runner, covenant monitor, report verifier, and cashflow projector against a realistic (if synthetic) dataset
3. **Demonstration** — demonstrating the LoanWhiz framework's end-to-end capabilities in a reproducible, publicly shareable way

The dataset is **not intended** for:

- Conclusions about actual Dutch RMBS performance or ING Bank's mortgage book
- Research into real borrower behaviour or loan-level credit performance
- Production analytics on the actual Green Lion 2026-1 deal without access to the real (non-synthetic) loan tape data
- Regulatory reporting

---

## Limitations

| Limitation | Description |
|---|---|
| **Two validated deals of six** | The pipeline *runs* on 5 of the 6 registered deals; **Green Lion 2024-1** and **Green Lion 2023-1** are validated to the cent against their own published Notes & Cash reports — the only `validated` capability cells, and both Dutch RMBS. Since #492 that cell is earned by committed data (an answer key carrying a Priority-of-Payments section, plus an offline engine series) rather than by bespoke Python, which is what made 2023-1's long-standing to-the-cent grade legible as validation. **Cairn CLO XVII** is additionally graded by `GET /quality-matrix` on its published coverage-test outcomes (#481) — a different check, against a different kind of document, and not a to-the-cent reconciliation. Its to-the-cent reconciliation was run separately (#496) and **fails**, which is why the count of validated deals is two rather than three. Every other cell is `ran` or `not-applicable` — outputs there are unvalidated and do not generalise without re-validation, and no non-Dutch and no non-RMBS deal is validated at all. |
| **Coverage without external truth on the non-English deals** | Extraction on the Italian (Leone Arancio) and Spanish (Sol-Lion II) prospectuses now reaches 0.925 completeness with a full waterfall on both — this card's earlier "≈ 0.38 / ≈ 0.30, no waterfall" described pre-#438/#439 seeds. Neither deal publishes a Notes & Cash report, so neither can ever be graded against published actuals without inventing ground truth. High coverage on these two is not evidence that their numbers are right. |
| **Ungraded PDL / reserve proximity** | Principal-deficiency-ledger and reserve-account proximity are computed and surfaced, but the Green Lion keys carry empty `covenants` and `pool_stats` for every period and the CLO key carries coverage tests only, so those checks grade `not-applicable` for every deal and no PDL or reserve check key exists. A flat or zero proximity there means "not evaluable from current inputs", not "healthy". |
| **Two asset classes are extracted; only one is validated** | The pipeline now reads both RMBS (Dutch, Italian, Spanish) and a CLO (Cairn CLO XVII DAC). Extraction is not validation, and neither is grading one row. The CLO's committed key now carries a published-report Priority of Payments too (#495), and #496 reconciled the engine against it: it did **not** tie out — the Interest cascade was short EUR 1,820,150.42 of the report's stated available revenue, the published rows no cascade step joined. That gap is now closed (#514/#538/#539) and all 29 Interest steps agree, three of them from the deal model alone. **This still is not validation of the deal.** One cascade of one period of one document reconciles; the Principal cascade ties on EUR 0.00 and proves nothing, 26 of the 29 agreeing lines are report figures compared against themselves, and the per-deal endpoints still refuse this deal. CMBS, US RMBS, ABS and other asset classes are not represented at all. |
| **Synthetic loan performance** | No real default history in the synthetic tapes. Arrears rates, default rates, and prepayment rates reflect synthetic generation assumptions, not observed market behaviour. |
| **Three jurisdictions run, a fourth only registered** | The deals the pipeline runs on span Dutch, Italian, and Spanish RMBS only — three legal regimes, three EPC/market conventions. Ireland is present in the registry (the CLO) but nothing has been run against it. Coverage of other European or non-European markets is untested. |
| **Synthetic time series (snapshots, not a panel)** | The deal's three 2026 monthly tapes enable time-series views and multi-period waterfall runs. The tapes are **re-sampled each period** — loan IDs do not persist — so the series is a sequence of point-in-time snapshots, not a tracked-cohort longitudinal panel. It is synthetically generated, so prepayment/default speeds estimated from it reflect the generation process, not observed market behaviour. |
| **No amendments or supplements** | The prospectus is the original offering document. Any amendments, supplements, or side letters issued after closing are not included. |

---

## Privacy and Data Protection

The loan tapes are **synthetic** — they do not contain real borrower data. There are no personally identifiable individuals represented in the loan-level data.

The prospectus and investor reports are public documents, published in connection with a public securitisation transaction in the European Union.

---

## FINOS AI Governance Framework Reference

This data card follows [FINOS AI Governance Framework](https://github.com/finos/ai-governance-framework) templates for dataset documentation. It is one component of the LoanWhiz governance artefact set:

- [docs/model-card.md](model-card.md) — Model card for the LoanWhiz Extraction Pipeline
- [docs/governance.md](governance.md) — Governance pattern document

**Reference:** [https://github.com/finos/ai-governance-framework](https://github.com/finos/ai-governance-framework)
