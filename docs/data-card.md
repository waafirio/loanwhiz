# Data Card: LoanWhiz deal set (Green Lion 2026-1 + cross-jurisdiction deals)

> Governance artefact following FINOS AI Governance Framework templates.
> See also: [docs/model-card.md](model-card.md) · [docs/governance.md](governance.md)

The primary demo and validation subject is **Green Lion 2026-1** (documented in
full below). The deal registry additionally carries **four more deals across two
further jurisdictions** that the *same* primitives run on end-to-end — see
[The full deal set](#the-full-deal-set--5-deals-3-jurisdictions) for the honest
per-deal breakdown. "Runs on" is not "validated against": the only deal validated
to the cent against external published actuals is **Green Lion 2024-1**, and
extraction on the non-English prospectuses is **partial**. The capability matrix
(`GET /capability-matrix`, Showcase view) is the source of truth, tallying
**1 validated / 9 ran / 15 not-applicable**.

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

> **Separate deals are not interchangeable.** `Algoritmica/green-lion-2024-2025` (~EUR 139bn pool, ~130× this deal) and the real ING `green-lion-2023-1` / `green-lion-2024-1` deals are **different deals**, not Green Lion 2026-1's pre-history. Their loan tapes are **not** chained into this deal's `tape_urls` — doing so would splice unrelated pools. Green Lion 2023-1 and 2024-1 are registered as their own deals (see [The full deal set](#the-full-deal-set--5-deals-3-jurisdictions)); 2024-1 is the engine's to-the-cent validation target against its own published Notes & Cash report.

> **These are period snapshots, not a longitudinal panel.** The three tapes are
> **re-sampled each period** — loan identifiers do not persist across months
> (the gross balance falls in one period and a similar gross balance rises in
> the next, netting to a small movement). So the series is a sequence of
> point-in-time pool snapshots, not a tracked-cohort loan-level time series.
> Per-period collections and losses are derived by **net reconciliation to
> pool movement**, not by following individual loans.

---

## The full deal set — 5 deals, 3 jurisdictions

Green Lion 2026-1 is the headline demo deal, but the deal registry
(`src/loanwhiz/data/deals.json`, merged over the in-code Green Lion default)
carries **five deals across three jurisdictions** that the *unmodified* pipeline
runs on end-to-end. This demonstrates the primitives are deal-agnostic — but
**"the pipeline ran" is reported separately from "the output was validated"**,
and extraction completeness is stated honestly per deal (it is *not* clean
everywhere). The capability matrix (`GET /capability-matrix`, Showcase view) is
the per-cell source of truth: **1 validated / 9 ran / 15 not-applicable**.

| Deal | Jurisdiction | Documents | Extraction completeness | What extracted | Validation |
|---|---|---|---|---|---|
| **Green Lion 2026-1 B.V.** | Netherlands | Prospectus (real) + 3 synthetic Annex 2 tapes + 3 investor reports (real) | **0.75** | Full waterfall (revenue/redemption/post-enforcement), 3 triggers, 0 definitions | Collateral reconciled to investor reports to the cent; liabilities prospectus-derived & invariant-checked (no in-window Notes & Cash) |
| **Green Lion 2024-1 B.V.** | Netherlands | Prospectus (real) + investor reports + **quarterly Notes & Cash (real)** | **0.925** | Full waterfall, 3 triggers | **Validated to the cent** — engine reproduces the published Notes & Cash Priority of Payments (revenue 11/11, redemption 4/4; Class A interest engine-computed). This is the single `validated` cell. |
| **Green Lion 2023-1 B.V.** | Netherlands | Prospectus (real) + investor reports + Notes & Cash | **1.0** | Full waterfall, 4 triggers | Registered; **no Notes & Cash fixture committed yet**, so the validation endpoint returns `available=false` with an honest note rather than a false pass |
| **Leone Arancio RMBS 2023-1 S.r.l.** | Italy | Prospectus (real, Italian) + investor reports | **≈ 0.38** | **Partial** — real *cited* triggers (performance trigger, PDL shortfall) and issuer covenants; **no waterfall extracted** | Pipeline ran; outputs not externally validated |
| **Sol-Lion II RMBS Fondo de Titulización** | Spain | Prospectus (real, Spanish) + investor reports | **≈ 0.30** | **Minimal** — no waterfall, no triggers resolved into the model | Pipeline ran; outputs not externally validated |

**Honesty note on the non-English deals.** Extraction on the Italian and Spanish
prospectuses is **partial by design of reality, not by claim**: the Leone Arancio
model carries real, cited triggers but no waterfall, and the Sol-Lion II model is
minimal. These are surfaced as `ran` (not `validated`) cells in the capability
matrix, each with the real reason. Nothing about the cross-jurisdiction coverage
should be read as "validated across all deals" — exactly one deal (Green Lion
2024-1) is validated against external published actuals.

The four non-2026 deals carry a `jurisdiction` field in the registry where they
are non-Dutch (`"Italy"`, `"Spain"`). Their loan tapes follow the same
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
| **One validated deal** | The pipeline *runs* on 5 deals across 3 jurisdictions, but only **Green Lion 2024-1** is validated to the cent against external published actuals (its Notes & Cash report). Every other cell is `ran` or `not-applicable` in the capability matrix — outputs there are unvalidated and do not generalise without re-validation. |
| **Partial non-English extraction** | Extraction on the Italian (Leone Arancio, ≈ 0.38) and Spanish (Sol-Lion II, ≈ 0.30) prospectuses is partial — cited triggers at best on the Italian, minimal on the Spanish, no waterfall on either. These are honest `ran` cells, not clean extractions. |
| **Single asset class** | RMBS only (Dutch, Italian, Spanish). CLOs, CMBS, US RMBS, ABS, and other asset classes are not represented. |
| **Synthetic loan performance** | No real default history in the synthetic tapes. Arrears rates, default rates, and prepayment rates reflect synthetic generation assumptions, not observed market behaviour. |
| **Three jurisdictions, not arbitrary** | The deal set spans Dutch, Italian, and Spanish RMBS only — three legal regimes, three EPC/market conventions. Coverage of other European or non-European RMBS markets is untested. |
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
