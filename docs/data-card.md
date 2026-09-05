# Data Card: LoanWhiz deal set (Green Lion 2026-1 + cross-jurisdiction deals + the first CLO)

> Governance artefact following FINOS AI Governance Framework templates.
> See also: [docs/model-card.md](model-card.md) · [docs/governance.md](governance.md)

The primary demo and validation subject is **Green Lion 2026-1** (documented in
full below). The deal registry additionally carries **four more RMBS deals across
two further jurisdictions** that the *same* primitives run on end-to-end, plus a
sixth deal — the Irish CLO **Cairn CLO XVII DAC** — which is **extracted but not
validated**: its documents are sourced and recorded here, the pipeline now reads
its Listing Particulars to a canonical deal model (#456), and no ground truth is
authored for it. See [The full deal set](#the-full-deal-set--6-registered-deals-5-that-run)
for the honest per-deal breakdown. "Runs on" is not "validated against": the only deal validated
to the cent against external published actuals is **Green Lion 2024-1** (a
second, **Green Lion 2023-1**, is graded to the cent against a committed answer
key), and while extraction *coverage* on the non-English prospectuses is now
high, neither of those deals publishes a report to validate against. The
capability matrix
(`GET /capability-matrix`, Showcase view) is the source of truth, tallying
**1 validated / 14 ran / 15 not-applicable** across its 6 deal columns.

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
the per-cell source of truth: **1 validated / 14 ran / 15 not-applicable**.

| Deal | Jurisdiction | Documents | Extraction completeness | What extracted | Validation |
|---|---|---|---|---|---|
| **Green Lion 2026-1 B.V.** | Netherlands | Prospectus (real) + 3 synthetic Annex 2 tapes + 3 investor reports (real) | **0.75** | Full waterfall (revenue/redemption/post-enforcement), 3 triggers, 0 definitions | Collateral reconciled to investor reports to the cent; liabilities prospectus-derived & invariant-checked (no in-window Notes & Cash) |
| **Green Lion 2024-1 B.V.** | Netherlands | Prospectus (real) + investor reports + **quarterly Notes & Cash (real)** | **0.925** | Full waterfall, 3 triggers | **Validated to the cent** — engine reproduces the published Notes & Cash Priority of Payments (revenue 11/11, redemption 4/4; Class A interest engine-computed). This is the single `validated` cell. |
| **Green Lion 2023-1 B.V.** | Netherlands | Prospectus (real) + investor reports + **quarterly Notes & Cash (real)** | **1.0** | Full waterfall, 4 triggers | **Graded to the cent** by `GET /quality-matrix` against a committed answer key (#440) — revenue + redemption PoP across all three published periods. The `/deal/{id}/validation` endpoint still returns `available=false`: the fixtures and key are committed, but no validation *builder* is registered, so that endpoint understates what is graded. |
| **Leone Arancio RMBS 2023-1 S.r.l.** | Italy | Prospectus (real, Italian) + investor reports | **0.925** | Full waterfall (23/23/12 steps), 3 triggers, 3 note classes — A1 480m / A2 6,600m / J 920m | Pipeline ran; tranche sizes reconcile to the curated `deals.json` registry, but **no** Notes & Cash report is published, so no external validation is possible |
| **Sol-Lion II RMBS Fondo de Titulización** | Spain | Prospectus (real, Spanish) + investor reports | **0.925** | Full waterfall (20/15/12 steps), 3 triggers, 8 note classes — A1–A6, B, C | Pipeline ran; tranche sizes reconcile to the curated `deals.json` registry, but **no** Notes & Cash report is published, so no external validation is possible |
| **Cairn CLO XVII DAC** *(CLO — extracted, unvalidated)* | Ireland | Listing Particulars (real, 420pp) + 3 monthly trustee reports (real) + Note Valuation Report (real, 83pp) | **1.0** | Full 8-class stack (A, B-1, B-2, C, D, E, F + Subordinated, EUR 404.1m), both Priorities of Payments as distinct cascades (29-step Interest / 23-step Principal / 26-step Post-Acceleration), 10 triggers of which 8 are per-class coverage tests, 25 definitions | **Ran, not validated.** No answer key is authored, so no cell is `validated`. `covenant_monitoring` and `waterfall_execution` are `ran`; tape analytics, collateral reconciliation and engine validation stay `not-applicable`. The coverage tests carry no thresholds — see the limitation below — so the monitor reports them not-evaluable rather than passing |

### Cairn CLO XVII DAC — what is and is not obtainable

The registry's first non-RMBS deal, added by #455 to generalise the platform off
RMBS and extracted by #456. **The Listing Particulars have been extracted; the
trustee reports and Note Valuation Report have not, no ground truth has been
authored, and no capability cell is `validated`.** This subsection is the
availability record the epic asked for, negatives included.

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
| **Note Valuation Report** | 83 | 08 Jan 2025 | **none yet — see below** |

The Note Valuation Report is **obtainable but deliberately not registered** under
`notes_cash_report_urls`. That key is a *routing promise*, not a URL slot:
`_reconstruct_series` dispatches on it, and
`test_answer_keys_exist_exactly_where_published_reports_do` treats its presence as
an assertion that a committed answer key exists for the deal. Setting it now — with
nothing extracted, no parser for the CLO report format and no key — would assert a
promise this deal cannot keep, and would trip that contract. The key is earned at
extraction (#456), not at sourcing. So that nothing has to be re-sourced, the
document is:

```
https://ise-prodnr-eu-west-1-data-integration.s3-eu-west-1.amazonaws.com/202502/12423666-a060-4e34-b3e8-f5510297ac6f.pdf
```

**This absence is a *not-yet*, not a *never* — and the difference is the finding.**
Leone Arancio and Sol-Lion II carry no `notes_cash_report_urls` because no such
report is published at all. Cairn carries none for the opposite reason: the report
exists, is free, and carries both Priorities of Payments.
`tests/test_clo_deal_registration.py` pins that distinction so it cannot quietly
flatten into "another deal with no report".

**The epic's decisive question — do trustee reports exist for a CLO? — is
answered YES.** Epic #454 was written on the premise that CLO trustee reports are
portal/vendor material and that therefore "this epic promises no validated cell".
That premise is **wrong for this deal**: not only are the monthly trustee reports
free, the **Note Valuation Report carries both an *Interest Priority of Payments*
and a *Principal Priority of Payments*** — the CLO analogue of the Notes & Cash
report that makes Green Lion 2024-1's to-the-cent validation possible. A validated
CLO cell is therefore *feasible* in a way it never was for the Italian and Spanish
deals. **Feasible is not done, and nothing here is presented as validation**: no
answer key is authored, no builder is committed, and whether this platform pursues
CLO validation is a scope decision for the operator, not something this child took.

**Not obtainable — the negatives, recorded because an absent document set is a
finding, not a blank.**

- **No machine-readable loan tape.** `tape_urls` is empty. Loan-level collateral
  data *does* exist and *is* free — the trustee reports carry "Current Asset
  Characteristics" Parts I–III, "Defaulted Collateral Obligation Detail",
  "Deferring Collateral Obligation Detail", "Assets Purchased" and "Assets Sold" —
  but as **PDF tables, not an ESMA Annex tape**. The `esma_tape_normaliser` cannot
  read them, so registering a tape URL would be a claim the pool analytics would
  then act on. This is the honest shape: the data is obtainable, the *format* is
  not ingestible today.
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

**The coverage tests carry no thresholds, and this is a real limitation rather
than an absence in the document.** The levels are stated ("the Class A/B Par
Value Ratio is at least equal to 130.08 per cent") but they live in the
definitions glossary, which runs past the extractor's 40,000-character budget.
A glossary is alphabetical, so truncation loses a *range*, not a sample: the
extraction captured 25 terms spanning "Acceleration Notice" to "Bankruptcy
Exchange Test", and every coverage test is defined under C–F. The truncation now
logs a warning naming the last term it saw, so the shortfall is visible rather
than inferred from a healthy-looking term count. Downstream this degrades
honestly — a coverage test with no quantified threshold is reported
`not_evaluable` with that reason, never as a passing test. Capturing the levels
is the obvious next increment and is **not** done here.

**Two reporting reasons that are now inaccurate for this deal** (both live in
`capability_matrix.py`, outside this child's scope — routed to #457, which wires
the CLO reader):

- `engine_validation` reports `not-applicable` because "No published Notes & Cash
  Priority-of-Payments report to reconcile the engine against for this deal."
  For Cairn that is **factually false** — the Note Valuation Report publishes both
  Priorities of Payments. What is genuinely missing is a committed validation
  *builder*, not the report.
- `tape_analytics` reports "No loan tapes published for this deal", which is true
  of ESMA tapes but understates that loan-level collateral detail *is* published,
  in an unreadable format.

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
against external published actuals, and one more (Green Lion 2023-1) is graded
against a committed answer key.

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
| **One validated deal, two graded** | The pipeline *runs* on 5 of the 6 registered deals, but only **Green Lion 2024-1** is validated to the cent against external published actuals (its Notes & Cash report) — the single `validated` capability cell. **Green Lion 2023-1** is additionally graded to the cent by `GET /quality-matrix` against a committed answer key. Every other cell is `ran` or `not-applicable` — outputs there are unvalidated and do not generalise without re-validation. |
| **Coverage without external truth on the non-English deals** | Extraction on the Italian (Leone Arancio) and Spanish (Sol-Lion II) prospectuses now reaches 0.925 completeness with a full waterfall on both — this card's earlier "≈ 0.38 / ≈ 0.30, no waterfall" described pre-#438/#439 seeds. Neither deal publishes a Notes & Cash report, so neither can ever be graded against published actuals without inventing ground truth. High coverage on these two is not evidence that their numbers are right. |
| **Ungraded PDL / reserve proximity** | Principal-deficiency-ledger and reserve-account proximity are computed and surfaced, but both committed answer keys carry empty `covenants` and `pool_stats` for every period, so those checks grade `not-applicable` for every deal and no PDL or reserve check key exists. A flat or zero proximity there means "not evaluable from current inputs", not "healthy". |
| **Two asset classes are extracted; only one is validated** | The pipeline now reads both RMBS (Dutch, Italian, Spanish) and a CLO (Cairn CLO XVII DAC). Extraction is not validation: the CLO has **no answer key and no published-report reconciliation**, so nothing about its numbers is externally checked, and it does not yet execute through the engine (#457). CMBS, US RMBS, ABS and other asset classes are not represented at all. |
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
