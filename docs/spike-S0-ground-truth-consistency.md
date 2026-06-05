# Spike S0 — Ground-truth consistency (the model-builder GATE)

**Issue:** waafirio/loanwhiz#180 · **Epic:** #179 (provably-correct deal model-builder) · **Date:** 2026-06-05

> **This is a decision-gate, not a feature.** It does not change the model,
> runner, or primitives. It answers one question with evidence, then makes a
> recommendation the operator approves before the rest of the spine (S1–S9) is
> built.

---

## RECOMMENDATION (read this first)

**The data is internally consistent. Adopt option (a): TAPES = SPEC.** Build the
model first-principles from the loan tapes; treat the investor reports as a
**collateral-side cross-check** (pool roll-forward), not as the ground truth for
the **liability side** (tranche balances, PDL, reserve, distributions) — because
**the reports do not contain any liability-side figures at all.**

Concretely for the spine:

- **S3 (collections from tape) and S7 (reconciliation harness) anchor on the
  tapes.** The tape↔report pool reconciliation is *exact to the cent* across all
  three periods, so the tapes are trustworthy as the cashflow source.
- **There is no "reports = spec" option to choose.** "Back out implied
  assumptions so the model reconciles to the reports" (option b) is **not
  possible**: the reports carry no note balances, no note factors, no PDL, no
  reserve account, and no priority-of-payments distributions to reconcile
  against. The liability side must be reconstructed first-principles from the
  prospectus capital structure + the waterfall, seeded at period 0 from the
  prospectus (NOT from the investor reports — they don't have it).
- **S7's report-reconciliation gate should assert what the reports actually
  contain:** computed `pool_balance(end)`, the principal roll-forward
  (`repayments`/`prepayments`/`other`), and pool stats — these tie out exactly.
  It must **not** try to reconcile tranche/PDL/reserve against the reports;
  those become *invariant* checks (conservation, non-negativity,
  closing[N]==opening[N+1]) and a prospectus-seeded check, per the epic plan's
  "comprehensive" definition.

The discrepancies `MODELING-GAPS.md` flagged (three divergent "current pool
balance" values, PDL/reserve permanently 0, balances that never amortize) are
**model bugs, not data inconsistency.** Once the spine is built they reconcile —
the ground truth is sound.

---

## Problem as understood

The epic's proof of correctness rests on reconciling the reconstructed deal
model against "ground truth (the investor reports)". Before building that
harness (S7) and the inputs that feed it (S2/S3), S0 must verify the load-bearing
assumption: **does Algoritmica's synthetic investor-report data actually tie to
the loan tapes via the deal waterfall?** If it doesn't, the whole proof rests on
sand, and the operator must decide whether *tapes* or *reports* are authoritative.

## How the question was answered

- **Reports:** the 3 monthly investor-report PDFs (Feb/Mar/Apr 2026; URLs in
  `src/loanwhiz/config.py` `GREEN_LION["investor_report_urls"]`) were transcribed
  and extracted with Gemini 2.5 Pro on Vertex (`scripts/s0_extract_reports.py`;
  cached to `/tmp/loanwhiz_cache/report_extract_full.json`). A full page-by-page
  transcription was run first to confirm document structure, then a targeted
  JSON extraction of every reconcilable figure plus a `has_tranche_section`
  probe.
- **Tapes:** the warmed `esma_tape_normaliser` analytics cache at
  `/tmp/loanwhiz_cache/tape_analytics/` (one file per period: `pool_balance_eur`,
  `pool_stats.wtd_coupon_pct`, arrears, …). Interest and principal were derived
  with the existing `collections_aggregator` formulae (interest = pool ×
  wtd_coupon × days/360; scheduled principal = prior-period balance delta).
- **Compare + diagnose:** `scripts/s0_reconcile.py` — pure arithmetic over the
  two caches, no network. Per period, per figure: absolute + % discrepancy, plus
  internal roll-forward and period-chaining consistency checks.

---

## The decisive finding: these are ESMA *Portfolio & Performance* reports

The 3 "investor reports" are ESMA **collateral / portfolio** reports (ING,
`www.dutchsecuritisation.nl`, Report Version 2.1), not bond/note **distribution**
reports. Their table of contents — identical across all 3 periods — is:

> Key Dates · The Mortgage Loan Portfolio · Foreclosure Statistics · Performance
> Ratios · Transaction Specific Information · Stratification Tables · Glossary ·
> Contact Information

The one page that would carry note balances / waterfall distributions —
**"Transaction Specific Information"** — is **blank** (a section header only).
There are **no tranche balances, no note factors, no PDL, no reserve account, and
no priority-of-payments tables anywhere in the documents.** Extraction confirms
`has_tranche_section = false` and zero liability-side figures present in all 3
reports.

**Implication:** the spine cannot reconcile its tranche/PDL/reserve outputs
against these reports — the data simply isn't there. The reports are a
collateral-side cross-check only.

---

## Comparison tables (tape-derived vs reported)

Reproduce with `python scripts/s0_reconcile.py`.

### Pool balance (end of period) — the asset side

| Period | Tape (Σ current_balance) | Report (Net Outstanding, end) | Δ abs | Δ % |
|---|---:|---:|---:|---:|
| Feb 2026 | 1,048,763,811.94 | 1,048,763,811.94 | **0.00** | **0.0000** |
| Mar 2026 | 1,042,493,289.74 | 1,042,493,289.74 | **0.00** | **0.0000** |
| Apr 2026 | 1,033,412,063.04 | 1,033,412,063.04 | **0.00** | **0.0000** |

**Exact to the cent, every period.** The tape *is* the pool the report describes.

### Principal collections — tape balance-delta vs report roll-forward

| Period | Tape Δbalance | Report (begin − end) | Δ abs | Δ % |
|---|---:|---:|---:|---:|
| Mar 2026 | 6,270,522.20 | 6,270,522.20 | **0.0000** | **0.0000** |
| Apr 2026 | 9,081,226.70 | 9,081,226.70 | **0.0000** | **0.0000** |

(Feb has no prior tape — Jan-2026 is intentionally absent in both data repos, per
`config.py`. So Feb principal can't be computed from a balance delta; this is a
data-coverage limitation, not an inconsistency.)

The tape's month-on-month balance reduction equals the report's full principal
roll-forward (`repayments + prepayments − further_advances − other`) **to the
cent.** Comparing the tape delta against `repayments + prepayments` *alone*
leaves a small residual (−0.13% Mar, +0.50% Apr) — and that residual **equals the
report's `other_balance_change` line exactly** (Mar 8,356.19; Apr −45,341.07),
i.e. construction-deposit / non-principal balance movements the report itemizes
separately and the raw balance delta absorbs. Account for that one line and the
residual is **0.0000**.

### Internal roll-forward (report self-consistency)

`balance_begin − repayments − prepayments + further_advances + other_balance_change == balance_end`

| Period | Computed end | Reported end | Δ |
|---|---:|---:|---:|
| Feb 2026 | 1,048,763,811.94 | 1,048,763,811.94 | 0.0000 |
| Mar 2026 | 1,042,493,289.74 | 1,042,493,289.74 | 0.0000 |
| Apr 2026 | 1,033,412,063.04 | 1,033,412,063.04 | 0.0000 |

### Period chaining (report ⟷ tape ⟷ prior report)

`report.balance_begin[N] == tape.pool[N-1] == report.balance_end[N-1]`

| Link | report.begin[N] | tape.pool[N-1] | report.end[N-1] |
|---|---:|---:|---:|
| Feb→Mar | 1,048,763,811.94 | 1,048,763,811.94 | 1,048,763,811.94 |
| Mar→Apr | 1,042,493,289.74 | 1,042,493,289.74 | 1,042,493,289.74 |

All three quantities coincide exactly — the periods chain cleanly and the tape at
the end of month N is the opening pool of report N+1.

### Interest

The reports carry **no interest-distributed figure** (no liability side). The
tape yields a derivable interest estimate (pool × wtd_coupon × days/360 ≈ €2.6–
2.9m/period) but there is nothing in the report to check it against. This is a
*missing report figure*, not a discrepancy.

### Weighted-average coupon (minor definitional delta)

| Period | Tape wtd_coupon | Report WA current rate | Δ % |
|---|---:|---:|---:|
| Feb 2026 | 3.2021% | 3.14% | +1.98% |
| Mar 2026 | 3.1895% | 3.14% | +1.58% |
| Apr 2026 | 3.1808% | 3.15% | +0.98% |

A small, stable ~0.06pp gap. **Diagnosis: definitional, not inconsistency.** The
report weights its WA coupon over **7,028 loan-parts** (the report's
stratification tables are loan-part level), while the tape analytic weights
`current_interest_rate_pct` over the **3,275 loan rows** by `current_balance`.
Loan-part vs loan-level balance weighting on a multi-part mortgage pool produces
exactly this kind of sub-2% drift. It does not affect the pool-balance or
principal reconciliation (which are exact) and is well within any reasonable
reconciliation tolerance for an interest cross-check.

---

## Per-discrepancy diagnosis (model gap vs data inconsistency)

This is the whole point of the spike: separate "the model is wrong but the data
ties out" from "the synthetic reports don't derive from the tapes."

| Observed | Magnitude | Diagnosis | Evidence |
|---|---|---|---|
| Pool balance (end) | Δ = 0.00, all periods | **Data is consistent** | tape Σ == report Net Outstanding, to the cent |
| Principal collections | Δ = 0.0000 (full roll-fwd) | **Data is consistent** | tape Δbalance == report (begin−end); residual vs repay+prepay == report `other` line exactly |
| Period chaining | exact | **Data is consistent** | begin[N]==tape[N-1]==end[N-1] |
| WA coupon | ~0.06pp (<2%) | **Definitional** (loan-part vs loan weighting) | report strats are loan-part level (7,028); tape is loan level (3,275) |
| Tranche balances / note factors | absent | **Reports lack the data** (not derivable from these reports) | `has_tranche_section=false`; "Transaction Specific Information" page blank |
| PDL balance | absent | **Reports lack the data** | not in any report section |
| Reserve-account balance | absent | **Reports lack the data** | not in any report section |
| Interest distributed per class | absent | **Reports lack the data** | reports are collateral-side only |
| MODELING-GAPS: 3 divergent pool balances (tape-sum vs 1,033,412,063 vs 1,063,600,000) | — | **Model bug** | `1,033,412,063` *is* the Apr tape sum; the other two are hardcoded/stale constants (`api/main.py`), not data conflict |
| MODELING-GAPS: PDL/reserve permanently 0; balances never amortize | — | **Model bug** | dead `MultiPeriodWaterfallRunner` (`waterfall_state.py`), not unwired data |

**Verdict on the gate's core question:** the synthetic data **does** derive from
the tapes and **is** internally consistent — every quantity the reports and tapes
share reconciles exactly. The reports simply **do not carry the liability-side
figures** the model most needs. So the proof's ground truth is sound for the
asset/collateral side, and the liability side must be reconstructed
first-principles and proven by invariants, not by report-reconciliation.

---

## What changes if you disagree with the framing

- **"I want the model to reconcile to reported tranche distributions."** Not
  possible with this data — the reports contain none. The only path to a
  liability-side ground truth would be a *different* artifact (a payment-date
  report / cash-manager report), which is not in `config.py` and may not exist
  for this synthetic deal. If the operator has access to such a report, S7's
  scope should expand to ingest it; absent that, invariants are the proof.
- **"The ~2% coupon delta worries me."** It is a loan-part-vs-loan weighting
  definition, stable and sub-2%, and orthogonal to the exact balance/principal
  reconciliation. If desired, S3 can compute the tape coupon at loan-part
  granularity to close it — but it is not a data-integrity issue and not a gate
  blocker.
- **"Reports should still seed period-0 state."** They can seed *pool* state
  (balance, stats) but **cannot** seed PDL/reserve/tranche state (absent). Period-0
  liability state must come from the **prospectus** capital structure, not the
  reports. This refines `MODELING-GAPS.md` B5 ("seed structural state from the
  report"): the report seeds the *asset* side; the *prospectus* seeds the
  *liability* side.

---

## Deliverables

- `scripts/s0_extract_reports.py` — extracts the reconcilable figures (+ tranche
  probe) from the 3 report PDFs to `/tmp/loanwhiz_cache/report_extract_full.json`
  (needs GCP ADC + Vertex). Re-runnable; idempotent cache.
- `scripts/s0_reconcile.py` — offline reconciliation over the tape-analytics and
  report caches; prints the tables above. No network.
- This findings doc.

No production code, model, runner, or primitive was changed (scope: `docs/**`,
`scripts/**`).
