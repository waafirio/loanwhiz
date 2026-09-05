---
id: 2026-09-06-clo-tape-from-trustee-reports
title: Derive an Annex 4 loan tape from Cairn's trustee reports, ingested through the normal channel
status: draft
created: 2026-09-06
updated: 2026-09-06
epics: []
---

# Derive an Annex 4 loan tape from Cairn's trustee reports, ingested through the normal channel

## Context & intent

The CLO structural spine shipped (epics #450 and #454, both on `main`). Cairn
CLO XVII DAC extracts to 8 classes and both Priorities of Payments, and folds
through the same `run_period` kernel as every RMBS deal. But when the demo UI was
brought up, most per-deal screens for the CLO read **"no loan tape published for
this deal"** — because `tape_urls` is empty and every tape-driven capability cell
is honestly `not-applicable`.

The operator's question was the obvious one: can we find a real CLO *with* loan
tapes? Answering it properly is what produced this plan, and the route matters as
much as the destination.

### What we ruled out, and why

**A different, "public" CLO whose loan tape is filed to a securitisation
repository.** This is the ideal answer — the tape would arrive through exactly
the annex registry #451 built, with real `CRPL` field codes and real regulatory
provenance. We searched for one and could not find it, and the reason is
structural rather than a gap in the search:

- Only **public** securitisations must report to a securitisation repository.
- European and US CLOs are *"usually structured in a way that exempts them from
  the requirement to produce a EU Prospectus Regulation compliant prospectus,
  which makes them private transactions for the purposes of the EU SR"*, and the
  market expectation is that *"going forward CLOs will be structured as private
  securitisations, meaning they will list on secondary, exchange-regulated
  markets, not on primary regulated markets."*
- Cairn is exactly that shape: listed on Euronext Dublin's **Global Exchange
  Market**, an exchange-regulated market. Its 420-page offering circular mentions
  "securitisation repository" **zero times**.
- Annex 4 also carries **200+ fields at asset/obligor level**, described in
  industry commentary as containing esoteric fields not readily available to
  secondary-market participants — a compliance cost that itself argues for
  staying private.

So "find a public CLO" is closer to "find a deal that chose an expensive
structure for no benefit" than to a search problem. We are not claiming none
exists; we found no evidence of one and the incentives run hard against it.

**Scraping the trustee report into a bespoke CSV.** This was the first thing
proposed, and the operator correctly rejected it as ad hoc. Three reasons it is:
it would be a parser for *one trustee's* layout (a deal with a different trustee
means a second parser — failing the "would the next one be cheaper?" test); PDF
column extraction is a fragile foundation for something we call governed; and
most importantly it would **bypass the contract #451 built**, entering as a tape
with no field codes, no `locator_for` provenance, and weaker traceability than
every other tape in the system.

### What this plan does instead, and why it is not the same thing

Cairn **does** file a real regulatory loan tape. Its offering circular commits to
*"quarterly asset-level reports in accordance with **Article 7(1)(a)** of the
Securitisation Regulations and the Article 7 Technical Standards"* — the "Loan
Reports", in ESMA Annex 4 format. They are simply distributed *"to, amongst
others, Competent Authorities, Noteholders and prospective investors"* rather
than to a repository. The data exists in the right shape; we lack the access
channel.

Meanwhile the **monthly trustee reports we already hold** carry a full collateral
schedule, in three parts joined on facility identifier:

| section | pages | fields |
|---|---|---|
| Current Asset Characteristics — Part I | 18–28 | issuer, facility, par balance, asset type, coupon type, spread, floor, current coupon, index, maturity, **market value** |
| Part II | 29–36 | par, S&P industry, Fitch industry, currency, country |
| Part III | 37–47 | cov-lite, DIP, **PIK**, deferring, current-pay, revolving, delayed-drawdown, bridge flags |
| S&P CCC Obligations | 13 | the CCC bucket with seniority, rating, **market value** |
| Portfolio Profile Tests / S&P Industry Concentration / S&P Rating Stratification | 4, 64, 67 | report-stated aggregates |

A naive regex over Part I found **162 distinct facilities, ~EUR 354m par**, and
we hold **three monthly snapshots**, so it is a time series rather than one cut.

So the difference between this plan and the rejected version is not the source —
it is the **target and the provenance**. We are not producing "a CSV". We are
producing a tape that **resolves through the existing annex registry onto
canonical Annex 4 columns**, carrying `CRPL` locators where the correspondence is
real, and carrying an explicit, visible statement of where it came from.

### The honesty problem is the heart of this plan

A derived tape that is indistinguishable from a filed regulatory tape is
**provenance laundering**, and it is precisely the failure this project's #193
discipline exists to prevent. Three specific traps, all identified before
planning:

1. **The tape is derived from a trustee report, not filed under Article
   7(1)(a).** Every surface that reports its provenance must say so. A reader
   must never be able to mistake this for an ESMA filing.
2. **S&P/Fitch industry classification is not NACE.** `CRPL14` is explicitly a
   NACE code. Mapping "Pharmaceuticals" onto it asserts a regulatory conformance
   we do not have.
3. **Country is not NUTS-3.** `CRPL10` is a NUTS-3 geographic region; the report
   gives "Luxembourg".

The precedent for handling this already exists in-tree: #451 made
`AnnexField.code` nullable precisely so `vehicle_type` could resolve a column
while yielding **no locator** — provenance visibly absent rather than fabricated.
That is the pattern to follow, not to reinvent.

### Field correspondence (established, not assumed)

Roughly 15 of the 34 declared Annex 4 fields map cleanly from the schedule:
facility id → `CRPL2`, issuer → `CRPL4`, par → `CRPL39`, asset type → `CRPL24`,
coupon type → `CRPL52`, spread → `CRPL56`, current coupon → `CRPL53`, index →
`CRPL54`, maturity → `CRPL34`, market value → `CRPL41`, currency → `CRPL37`, PIK
→ `CRPL31`, seniority → `CRPL27`, `managed_by_clo` → `CRPL30`. Industry and
country map only *approximately* (see traps 2 and 3). Arrears, default,
recoveries, original balance and Basel segment are simply **absent** — and must
read as absent, never as zero. The silent-zero bug #451 found (a corporate tape
with defaulted obligors reporting `default_pct: 0.0`) is the standing warning.

### What this unlocks, and what it does not

Unlocks: the tape-driven capability cells for the CLO stop being blank; **market
value plus the CCC bucket are exactly the inputs the OC ratio needs to stop
erring falsely high** (the known limitation #452 documented); industry
classification makes concentration tests possible; ratings make WARF/diversity
possible; and production ingestion stops refusing the deal for want of a
registered tape.

Does **not** unlock: a `validated` cell. That still needs a committed answer key,
which remains a deferred operator decision. Nothing in this plan authors one.

### Ordering

One epic, three children, strictly sequential — each consumes the previous one's
output. The parse must reconcile against the report's *own* stated aggregates
before anything downstream trusts it; that check is the contract that makes the
rest safe.

## Decomposition

_(Filled in phase 2.)_

## Filed issues

_(Filled in phase 4.)_
