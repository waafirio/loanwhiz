---
id: 2026-06-04-deal-generality-and-27-period-data
title: deal-generality-and-27-period-data
status: decomposed   # draft → decomposed → filed
created: 2026-06-04
updated: 2026-06-04
epics: []            # umbrella issue numbers, filled in phase 4
---

# deal-generality-and-27-period-data

## Context & intent

### Trigger

The Green Lion deal team published a new HuggingFace dataset,
`Algoritmica/green-lion-2024-2025` (uploaded 2026-06-04), containing **24
monthly ESMA loan tapes spanning Jan 2024 → Dec 2025** plus a combined
`Overall_2024_2025_all_months.parquet`. Combined with the existing
`Algoritmica/green-lion-2026` repo (Feb/Mar/Apr 2026), Green Lion now has a
**~27-month loan-level history** (one gap: Jan 2026 / `202601` is absent
between the two repos). The new tapes use the **exact same 71-column ESMA
Annex-2 schema** as the 2026 tapes (verified header-for-header), so they are
drop-in compatible with `esma_tape_normaliser`.

The deal team framed it as: *"The framework is data-agnostic by design, so if
you have access to a public or anonymised historical deal you can plug it
straight in. The Green Lion 2026-1 synthetic data (2024–2026) should also give
enough temporal coverage for compliance tracking."* They also ship the data as
**parquet** (the combined file), so we must be **prepared to read parquet**, not
only CSV.

### The intent — two things at once

The operator's directive was explicit: **wire in the richer data AND make sure
the framework stays generalisable enough to accept *other* deals.** Those are
the two epics. The data wiring is the easy, visible win (a 27-period compliance
time-series for the demo); the generality hardening is the durable one (the
Challenge-1 selling point — a reusable agent framework, not a Green-Lion
one-off).

### Why this shape (audit-driven)

A read-only audit of the codebase found the framework is **already
period-count-agnostic**: every endpoint (`/tape-analytics`, `/compliance`,
`/waterfall`) iterates `deal["tape_urls"]` dynamically, tape loading is
URL-keyed and durably cached (27 monthly CSVs map 1:1 to our per-period model),
and nothing zips tapes to investor reports (27 tapes + 3 reports is safe). So
**Epic B (data wiring) needs no new period-count machinery** — it is mostly
config plus an end-to-end verification that 27 periods behave.

But the audit also found a handful of places that **silently assume Green
Lion** — harmless for Green Lion, wrong for any *second* deal:

- `original_pool_balance = 1_063_600_000` hardcoded in `waterfall_state.py` and
  `agent/tools.py` → drives the clean-up-call trigger and cumulative-loss-rate;
  a different deal would compute these against the wrong denominator.
- `/deal/{id}/project` always uses `_GREEN_LION_PROJECTION_BASE`
  (`api/main.py`) → projections ignore the selected deal's own capital
  structure.
- `/deal/{id}/compliance` falls back to `covenant_monitor`'s hardcoded
  Green-Lion `DEFAULT_TRIGGERS` rather than the deal model's **extracted**
  triggers — so a deal with different triggers would be monitored against Green
  Lion's.

These are Epic A. The fix pattern is uniform and already proven by the #151
`/waterfall` fix (resolve from deal context, default to Green Lion): move each
hardcoded value behind `deal.get(<key>, <green-lion-default>)`, then **document
the optional deal-context keys** so "plug in any deal" is real and written down.

### Parquet (operator steer)

Parquet "may not be needed" for our per-period architecture (the 24 monthly
CSVs map cleanly to periods; the combined parquet would have to be split back
out by `reporting_date`). But the deal team is *handing us* parquet, so
data-agnostic must mean **format-agnostic**: `esma_tape_normaliser` should
accept a `.parquet` URL as readily as `.csv` (single-period parquet → read
directly; combined multi-month parquet → split by `reporting_date` into
periods). This is a generality child under Epic A, not a blocker for the CSV
wiring.

### Alternatives weighed and rejected

- **Use the combined parquet as the primary tape source** — rejected: our
  contract is one-tape-per-period (`tape_urls: [{date,url}]`), which the 24
  monthly CSVs satisfy 1:1 with zero new splitting logic. Parquet support is
  added for format-agnosticism, not made the primary path.
- **A second `deals.json` entry for the history** — rejected: it is the *same*
  Green Lion deal with more history, so we extend Green Lion's `tape_urls`
  rather than fork a near-duplicate deal.
- **Hand-type 27 URL dicts in `config.py`** — rejected as a maintainability
  smell; build the historical block programmatically (regular monthly naming),
  keeping the explicit `{date,url}` contract.
- **Defer generality hardening until a real second deal exists** — rejected by
  the operator: the whole point is the framework's *generalisability*, and the
  fixes are small, well-understood, and de-risk the demo's "data-agnostic"
  claim now.

### Cross-epic ordering

Epics A and B are **independent** and run in parallel. They touch mostly
disjoint files (A: `waterfall_state.py`, `agent/tools.py`, `api/main.py` project
path, `covenant_monitor` wiring, `esma_tape_normaliser`; B: `config.py` + a
verification pass). The only soft coupling: B's 27-period verification is a
better test once A's parquet/триggers work lands, but it does not block — B can
verify on CSV with the existing trigger path and pick up A's improvements on
merge. No hard `After` across epics.

## Decomposition

### Epic A: Deal-generality hardening — keep the framework plug-in-able   (umbrella #<N>)

Move the remaining hardcoded Green-Lion values behind the deal context so a
second, non-Green-Lion deal produces correct numbers, add parquet ingestion so
the framework is format-agnostic, and document the optional deal-context keys.
Fix pattern mirrors the #151 `/waterfall` fix: `deal.get(<key>, <gl-default>)`,
defaults preserved so Green Lion is unchanged.

- **original_pool_balance → deal context** — resolve `original_pool_balance`
  from the deal context (default to the Green Lion closing balance) instead of
  the hardcoded constant in `waterfall_state.py` and `agent/tools.py`; this
  feeds the clean-up-call trigger and cumulative-loss-rate. Sequencing: parallel.
  Paths: `src/loanwhiz/primitives/waterfall_state.py`, `src/loanwhiz/agent/tools.py`.
- **/project base → deal context** — make `/deal/{id}/project` derive its
  projection base (pool balance + capital structure) from the resolved deal
  (capital_structure / latest tape) with the Green Lion base as default, instead
  of always using `_GREEN_LION_PROJECTION_BASE`. Sequencing: parallel.
  Paths: `src/loanwhiz/api/main.py`.
- **/compliance uses extracted triggers** — feed the deal model's extracted
  triggers into `covenant_monitor` for `/deal/{id}/compliance`, falling back to
  the hardcoded `DEFAULT_TRIGGERS` only when the deal model has none. Sequencing:
  parallel. Paths: `src/loanwhiz/api/main.py`, `src/loanwhiz/primitives/covenant_monitor.py`.
- **Parquet tape ingestion** — `esma_tape_normaliser` accepts a `.parquet` tape
  URL as well as `.csv` (single-period parquet read directly; combined
  multi-month parquet split by `reporting_date` into per-period frames), so the
  framework is format-agnostic to whatever the deal team provides. Sequencing:
  parallel. Paths: `src/loanwhiz/primitives/esma_tape_normaliser.py`.
- **Document optional deal-context keys** — document the deal-context schema and
  its optional keys (`capital_structure`, `original_pool_balance`,
  `projection_base`, `triggers`) and supported tape formats (CSV/parquet) in the
  config docstring + README, so adding a new deal is a written, data-only
  operation. Sequencing: sequential. After the three resolver children land so
  the documented keys match the code. Paths: `src/loanwhiz/config.py`, `README.md`.

### Epic B: Wire Green Lion's 27-month history   (umbrella #<N>)

Extend the Green Lion deal so it runs on the full 2024–2026 monthly history and
verify the multi-period endpoints behave over 27 periods. Same deal, richer
time-series — the organizer's "temporal coverage for compliance tracking".

Data references for the implementer:
- Historical (new repo) base: `https://huggingface.co/datasets/Algoritmica/green-lion-2024-2025/resolve/main/green_lion_<YYYYMM>_1_synthetic_loan_tape.csv` for `<YYYYMM>` in `202401`..`202512` (24 files), month-end dates.
- 2026 (existing repo) base: `https://huggingface.co/datasets/Algoritmica/green-lion-2026/resolve/main/Hackathon_Data/...` — `202602`, `202603`, and `green_lion_2026_1` (= April 2026); already in config.
- Gap: `202601` (Jan 2026) is absent in both repos — expected, not an error.
- Investor reports remain the 3 existing 2026 PDFs (no 2024–2025 reports).

- **Wire the 27-month tape history into config** — extend Green Lion's
  `tape_urls` to the full chronological 2024-01→2026-04 set (24 historical CSVs
  from the new repo + the existing 3 for 2026), built programmatically rather
  than hand-typed, keeping the `{date,url}` contract; leave investor reports as
  the 3 existing 2026 entries. Sequencing: parallel.
  Paths: `src/loanwhiz/config.py`.
- **Verify multi-period behaviour over 27 periods** — end-to-end check that
  `/tape-analytics`, `/compliance`, and the agent covenant tooling produce
  correct, non-degenerate output across all 27 periods (and that the
  `MAX_VERBATIM_PERIODS` summarisation path engages cleanly above 6 periods);
  add/extend a test that exercises a many-period deal. Sequencing: sequential.
  After the config wiring lands. Paths: `tests/**`, `src/loanwhiz/api/main.py`.

### Finalisation: refresh docs & presentation   (standalone issue, after both epics)

After Epics A and B land, bring the human-facing artefacts up to date with the
new reality. This is a single standalone enrolled issue (no children), gated
`After` **both** umbrellas so it runs once the framework and data work is merged.

- **Update docs & regenerate the presentation** — refresh the human-facing
  artefacts to reflect: the 2024–2026 multi-period dataset (cite the
  `Algoritmica/green-lion-2024-2025` HF dataset), CSV **and** parquet
  format-agnostic ingestion, and the deal-generality (the optional deal-context
  keys). Update `README.md` (Data + "run against a new deal" sections),
  `docs/data-card.md`, and `docs/model-card.md` / `docs/governance.md` where
  affected; then **regenerate the committed PowerPoint** by running
  `presentation/build_deck.py` (→ `presentation/LoanWhiz-Presentation.pptx`) so
  the deck reflects data-agnostic-by-design, format support, and 27-period
  temporal coverage. Sequencing: standalone. After #<A-umbrella> and
  #<B-umbrella>. Paths: `README.md`, `docs/**`, `presentation/**`.

## Filed issues

<Filled in phase 4 — the artifact↔issue link.>
