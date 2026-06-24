# LoanWhiz — System Status & Known Limitations (2026-06-24)

The current, honest picture of what the system does and where its real
boundaries are. Every claim is grounded in `file:line` against the code in
this repo. This doc supersedes the retired `MODELING-GAPS.md` and
`DEMO-RISKS.md` (both dated 2026-06-05), which described a pre-refactor
system: most of the architectural gaps they listed have since shipped (the
single `run_period` kernel, the generalised canonical `DealRules` assembly,
the live `/report-verification` / `/compare` / `/extract` surfaces,
cross-jurisdiction execution), and they cited modules — `waterfall_state.py`,
`cashflow_projector.py`, `MultiPeriodWaterfallRunner` — that were **deleted**
in the #276 engine collapse.

> Read this alongside `README.md` (capability overview + the per-deal
> capability matrix), `docs/model-card.md`, `docs/data-card.md`, and
> `docs/governance.md`. Where those are already honest about a boundary, this
> doc does not repeat them — it collects the cross-cutting limitations a
> reviewer should know before drawing conclusions.

---

## What the system genuinely does now

- **One waterfall execution kernel.** `run_period()` in
  `src/loanwhiz/primitives/period_state_machine.py` is the single executor —
  the legacy `WaterfallRunner` and `MultiPeriodWaterfallRunner` duplicate
  engines were deleted (#276; see `src/loanwhiz/primitives/waterfall_runner.py`
  docstring, now a thin MCP-tool-surface wrapper onto `run_period`). The kernel
  carries canonical `DEFAULT_REVENUE_STEPS` / `DEFAULT_REDEMPTION_STEPS` (the
  modelled Green Lion 2026-1 Priority of Payments) and also interprets an
  extracted deal's own steps through `waterfall_interpreter.py`.
- **Generalised, model-driven assembly.** `build_deal_rules()`
  (`src/loanwhiz/extraction/assembler.py`) maps an extracted `DealModel` onto
  canonical `DealRules` (`src/loanwhiz/domain/rules.py`) — typed recipients,
  amount bases, conditions, tranches and triggers, with per-field provenance.
  Steps the taxonomy can't map degrade **honestly** to `unmapped` /
  `report_supplied` (prose retained, never executed) rather than being faked.
- **Cross-jurisdiction execution.** The same primitives run end-to-end across
  5 deals in 3 jurisdictions (Dutch / Italian / Spanish RMBS); see
  `tests/test_cross_jurisdiction_cold_start.py` and
  `tests/test_breadth_cross_jurisdiction.py`.
- **Live governance threading.** Agent tools thread each primitive's *real*
  `confidence` / `citations` / audit entry into the FINOS evidence pack
  (`src/loanwhiz/governance/evidence_pack.py`); `finos_compliant` is derived in
  `create()`, not a hardcoded constant.
- **Report verification, comparison, and on-demand extraction** are wired live
  (`/report-verification`, `/compare`, `/extract` + `/extract/status`).

---

## Known limitations (the real boundaries)

These are accurate as of 2026-06-24 and verified against current code. None of
them is a "TODO that's actually done"; each is a genuine present boundary.

### 1. "Projection" is a single-period stress sensitivity, not a forward CPR/CDR projection
`GET /deal/{id}/project` re-runs the waterfall on the base-case capital
structure under base-vs-stressed collection factors and reports Class A WAL. It
is a sensitivity, **not** a multi-month amortisation projection — there is no
dedicated forward `cashflow_projector` wired to an endpoint (the standalone
projector engine was removed in #276). README "Projection" view and the
primitives table already mark this; do not read the Projection panel as a term
structure.

### 2. Deal comparison is aligned-display + signed median-deviation, NOT an automated "which deal is better" judgement
`GET /compare` (`src/loanwhiz/api/compare.py`) aligns deals row-by-row by
canonical `RecipientType` / `MetricType`, overlays performance series, and
computes each deal's signed deviation from the comp-set **median**. It presents
the numbers aligned; it does **not** rank deals or emit a relative-value
verdict. The cross-deal **relative-value / spread screener**
(`src/loanwhiz/primitives/relative_value_screener.py`) that *would* produce a
"rich/cheap" judgement is **registered but reached by no endpoint** — it is the
separate deferred relative-value screener (#307). Comparison shows; it does not
decide.

### 3. IT/ES extraction is real but thin; Sol-Lion II's revenue waterfall is empty
Extraction runs on all 5 deals, but completeness is honest per deal and
**partial** on the non-English prospectuses: Leone Arancio RMBS 2023-1 (IT)
extracts ≈0.38 — cited triggers but **no waterfall**; Sol-Lion II RMBS (ES)
extracts ≈0.30 — minimal, with an **empty revenue Priority of Payments** (0
steps in the committed seed). The engine generalises; the foreign-language
extraction does not yet fill these in. The per-deal capability matrix
(`GET /capability-matrix`, Showcase view) and `docs/data-card.md` /
`docs/model-card.md` are the source of truth — never read a blanket "works on
all 5".

### 4. The on-demand `/extract` job store is in-process and single-instance
`src/loanwhiz/api/extraction_jobs.py` holds jobs in a module-level
`_JOBS: dict[str, ExtractionJob]` guarded by a lock, with a
`ThreadPoolExecutor(max_workers=1)`. It is **process-local**: it resets on
restart and does not coordinate across multiple API instances. The *durable*
output is the materialised deal-model cache (one source of truth) — the job
store is just the live status of an in-flight extraction, not a persistent
queue. Fine for the single-worker demo deployment; not a multi-instance job
system.

### 5. A brand-new deal still needs a seed or a long extraction run before its Overview is populated
Adding a deal is *data*, but the data has to exist. `GET /deal/{id}/model`
reads the cache read-only and returns `not_cached` on a miss rather than
blocking the request for the ~20–37 min Docling + Vertex extraction. A new deal
becomes cold-startable only after either a committed seed ships for it
(`src/loanwhiz/data/deals/seed/*.json`) or a `POST /extract` run completes and
materialises the cache. Until then its Overview shows empty-states. There is no
inline cold-extract on first view.

### 6. Covenant proximity / metric caveats that still genuinely hold
The covenant monitor enforces a runtime `threshold_unit` guard at the seam
(#372) and tracks tape-native (B7) arrears/LTV/default triggers, so the empty-
chart class of problems the old audit flagged is largely addressed for
tape-derivable metrics. PDL/reserve-style triggers whose period-over-period
scalars are not sourced from a reconstructed state still cannot show meaningful
proximity — treat a flat/zero proximity on those as "not evaluable from current
inputs", not "healthy".

---

## Coverage baseline

The suite is measured with `pytest-cov` (configured in `pyproject.toml`).
Measure the offline suite locally with:

```bash
pytest --cov=loanwhiz --cov-report=term-missing -m "not integration and not slow"
```

There is intentionally **no** `--cov-fail-under` gate wired into the default
run: the baseline is established and reported, not enforced as a hard CI floor
(a failing threshold would red an otherwise-green suite and isn't meaningful
until a baseline exists). Raise the floor deliberately once the baseline is
known.
