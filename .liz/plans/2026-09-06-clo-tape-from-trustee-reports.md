---
id: 2026-09-06-clo-tape-from-trustee-reports
title: Derive an Annex 4 loan tape from Cairn's trustee reports, ingested through the normal channel
status: filed
created: 2026-09-06
updated: 2026-09-06
epics: [468]
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

One epic, three children, strictly sequential. Each consumes the previous
child's output, so there is no parallelism to be had here and pretending
otherwise would just produce a worker that stands down.

### Epic: CLO loan tape derived from trustee reports   (umbrella #468)

Turn the collateral schedule inside Cairn's monthly trustee reports into a tape
that enters through the **normal** ingestion channel — resolving on the existing
annex registry onto canonical Annex 4 columns, carrying `CRPL` locators where the
correspondence is real, and stating plainly everywhere that it is **derived from
a trustee report, not filed under Article 7(1)(a)**.

The epic is done when the CLO's tape-driven capability cells report real results
instead of "no loan tape published", and no surface anywhere implies the tape is
a regulatory filing.

- **Parse the trustee-report collateral schedule to structured rows** — Extract
  the per-asset schedule from the committed monthly trustee reports (Current
  Asset Characteristics Parts I/II/III plus the S&P CCC Obligations page),
  joining the parts on facility identifier into one row per asset per reporting
  date. **Reuse:** `notes_cash_parser` is the in-tree precedent for a
  deterministic, offline, pypdf-based report parser — follow its shape rather
  than introducing a new PDF stack. **Contract — this is the acceptance bar:**
  the parsed tape must **reconcile against the report's own stated aggregates**
  (Portfolio Profile Tests, aggregate par, S&P Industry Concentration, S&P Rating
  Stratification), pinned by a test; a parse that does not tie out to the
  document's own totals is not trusted by anything downstream. Row continuations
  are the known hazard — multi-line issuer names and wrapped country values are
  visible in the raw text. **Governance:** parse failures and dropped rows must
  be counted and surfaced, never silently skipped. **Generality:** the three
  monthly reports are three periods; the parser handles a *report*, not a date.
  Sequencing: parallel. Paths: `src/loanwhiz/primitives/**`,
  `tests/fixtures/**`, `tests/**`.
- **Map the parsed schedule onto canonical Annex 4 columns, with honest
  provenance** — Resolve the parsed rows onto the canonical columns declared in
  `esma_annex4_corporate`, so the tape carries `CRPL` locators where the
  correspondence is genuine. **This child owns the honesty problem and it is the
  point of the epic.** Three specific traps, all identified in the plan: the tape
  is *derived*, not an Article 7(1)(a) filing, and every provenance surface must
  say so; S&P/Fitch industry classification is **not** the NACE code `CRPL14`
  declares; and country is **not** the NUTS-3 region `CRPL10` declares. **Reuse
  and contract:** #451 made `AnnexField.code` nullable precisely so a field can
  resolve a column while yielding **no locator** — provenance visibly absent
  rather than fabricated. Follow that precedent; do not invent a second
  mechanism, and do not map an approximate correspondence onto a regulatory code.
  Fields the schedule simply lacks (arrears, default, recoveries, original
  balance, Basel segment) must read as **absent, never zero** — the silent-zero
  bug #451 found in exactly this table is the standing warning. Sequencing:
  sequential. After the parse child. Paths: `src/loanwhiz/domain/**`,
  `src/loanwhiz/primitives/esma_tape_normaliser.py`, `tests/**`.
- **Register the derived tape and ingest it through the normal channel** — Make
  the derived tape reachable the way every other tape is, so annex detection
  resolves it as Annex 4 and the tape-driven capability cells produce real
  results. Decide and justify where a *derived* tape lives given `tape_urls`
  currently holds URLs — a committed artefact and a generated-at-ingest artefact
  have different provenance and reproducibility properties, and the choice should
  be argued in the plan rather than defaulted. **Contract:** a derived tape must
  remain **distinguishable from a filed regulatory tape at every surface that
  reports provenance** — `data_source`, the evidence pack, the capability matrix
  reason, and the data card. If a reader can mistake it for an ESMA filing, this
  child has failed regardless of whether the cells light up. **Governance:**
  update the data and model cards to record what the derived tape is, what it
  covers, and what it omits. **Generality:** "derived from an investor/trustee
  report" should be a recognisable *source kind*, not a Cairn special case — the
  next such deal, or Cairn's real Art 7(1)(a) Loan Reports if access is ever
  obtained, should flow the same way. Sequencing: sequential. After the mapping
  child. Paths: `src/loanwhiz/data/**`, `src/loanwhiz/api/**`,
  `src/loanwhiz/primitives/capability_matrix.py`, `docs/**`, `tests/**`.

## Filed issues

- Epic "CLO loan tape derived from trustee reports" → umbrella **#468**
  - **#469** Parse the trustee-report collateral schedule to structured rows  _(prio 1)_
  - **#470** Map the parsed schedule onto canonical Annex 4 columns, with honest provenance  _(sequential, After #469, prio 2)_
  - **#471** Register the derived tape and ingest it through the normal channel  _(sequential, After #470, prio 3)_

All four labelled `liz:enrolled`. Every child body carries the four standing
constraints verbatim plus its own reuse / contract / governance / generality
notes, so a worker never has to open this plan to know how the work must be done.

Strictly sequential: #470 consumes #469's rows, #471 consumes #470's canonical
tape. With the fleet's server-side concurrency cap of 2 this runs one child at a
time regardless.
