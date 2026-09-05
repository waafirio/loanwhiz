# LoanWhiz — System Status & Known Limitations (2026-09-02)

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
  `tests/test_breadth_cross_jurisdiction.py`. A sixth deal — the Irish CLO
  Cairn CLO XVII DAC — is **registered as data only** and executes nowhere yet;
  see `tests/test_clo_deal_registration.py`, which pins what is absent for it
  as hard as what is present.
- **Multi-period projection.** `POST /deal/{deal_id}/project`
  (`src/loanwhiz/api/main.py:3571`) takes a `months` horizon
  (`ProjectRequest`, `main.py:374`), generates a synthetic CPR / CDR /
  recovery / rate-shift `PeriodInputs` stream per scenario, and folds it
  through `run_period` one period at a time (`main.py:3646-3651`), returning a
  per-period series and a Class A WAL. `POST /deal/{deal_id}/stress-matrix`
  (`main.py:3917`) runs the same fold across a CPR × CDR × rate-shift grid,
  capped at `_MAX_MATRIX_CELLS = 64` (`main.py:3697`).
- **Comparison that renders a judgement.** `GET /compare` (`main.py:1226`)
  returns a `RelativeValueScorecard` and a `ComparativeVerdict` with a ranking
  (`src/loanwhiz/api/compare.py:727`, `:801`); the screener is also directly
  reachable at `GET /relative-value-screener` (`main.py:3097`).
- **Live governance threading.** Agent tools thread each primitive's *real*
  `confidence` / `citations` / audit entry into the FINOS evidence pack
  (`src/loanwhiz/governance/evidence_pack.py`); `finos_compliant` is derived in
  `create()`, not a hardcoded constant.
- **Report verification and on-demand extraction** are wired live
  (`/report-verification`, `/extract` + `/extract/status`).

---

## Retired since 2026-06-24 — limitations this doc used to carry

Recorded rather than silently deleted, so a reader who knew the old list can
see what moved and check it.

- **"Projection is a single-period stress sensitivity."** False since the
  `months` horizon and the per-period fold above. What remains true is
  narrower and is stated as limitation 4 below: the projection is driven by
  *supplied* CPR/CDR assumptions, not by speeds estimated from the deal's own
  tape history.
- **"`/compare` does not rank deals or emit a relative-value verdict; the
  screener is registered but reached by no endpoint."** False since #400 wired
  `relative_value_screener` into `api/compare.py`. Both halves are now wrong —
  the verdict is returned, and the screener has its own endpoint.
- **"IT/ES extraction is thin; Sol-Lion II's revenue waterfall is empty."**
  False since #438/#439 re-extracted both seeds. Superseded by limitation 1,
  which states what those re-extractions did and — more importantly — did not
  establish.

---

## Known limitations (the real boundaries)

Accurate as of 2026-09-02 and verified against current code. None is a "TODO
that's actually done"; each is a genuine present boundary.

### 1. Extraction coverage is now high on every deal — coverage is not correctness, and only one deal is externally validated
The committed IT/ES seeds were re-extracted in #438/#439 and are no longer the
pre-fix artifacts the old item 3 described. Measured from
`src/loanwhiz/data/deals/seed/`:

| Deal | completeness | revenue / redemption / post-enf. steps | triggers | tranches |
|---|---|---|---|---|
| Green Lion 2023-1 (NL) | 1.0 | 11 / 4 / 8 | 4 | 3 |
| Green Lion 2024-1 (NL) | 0.925 | 11 / 4 / 8 | 3 | 3 |
| Green Lion 2026-1 (NL) | 0.75 *(see limitation 5)* | 11 / 4 / 8 | 3 | 3 |
| Leone Arancio 2023-1 (IT) | 0.925 | 23 / 23 / 12 | 3 | 3 |
| Sol-Lion II (ES) | 0.925 | 20 / 15 / 12 | 3 | 8 |

**Read this as coverage, not as validation** (#193 per-cell honesty). Three
distinct things are being counted, and only the last is external:

- **Completeness** is a weighted coverage ratio over the extracted artifact
  (`assembler.py:240`). A high score means the extractor found and populated
  the expected sections — it says nothing about whether the numbers are right.
- **Cross-checked against the curated registry.** Both refreshed seeds
  reconcile to `src/loanwhiz/data/deals.json`, which no part of the extractor
  reads: Leone Arancio's Class A1 480m + A2 6,600m matches its 7.08bn
  `class_a_balance` and Class J 920m its `class_b_balance`; Sol-Lion II's
  Class A1–A6 sum to EUR 12,036,900,000, Class B 1,643,800,000, Class C
  375,800,000. This is a real independent check on the **capital structure**,
  and it is *internal* — a curated registry is not a published source.
- **Externally validated** means reconciled to a deal's own published Notes &
  Cash report. That is still **one deal**: Green Lion 2024-1 is the only
  `validated` cell in `GET /capability-matrix` (live tally: 1 validated /
  14 ran / 15 not-applicable, over 6 deal columns). Green Lion 2023-1 now has a committed
  ground-truth answer key (#440), so `GET /quality-matrix` **grades two
  deals** — both reconcile their revenue and redemption Priority of Payments
  to the cent across all three published periods.

Leone Arancio and Sol-Lion II publish **no** Notes & Cash report, so no answer
key can be authored for them without inventing one, and none is. The CLO is the
exception that proves the rule: Cairn CLO XVII DAC's Note Valuation Report *does*
publish both an Interest and a Principal Priority of Payments, freely and
unauthenticated, so an answer key is **feasible** there — but none is authored,
no validation builder is committed, and every CLO cell reads `not-applicable`.
Whether to pursue a validated CLO cell is an open operator decision, not a
promise this repo has made. Coverage is
also uneven *within* a seed: Sol-Lion II carries ratings and coupons (A1–A6
AAA, 0.25%–0.75%; B 1.00%, C 1.50%), while **Leone Arancio's are all `null`** —
that prospectus states them far from the class labels, and inferring them by
proximity is how a plausible wrong number gets committed. A refreshed seed is
not evidence about the world.

### 2. The on-demand `/extract` job store is in-process and single-instance
`src/loanwhiz/api/extraction_jobs.py:225` holds jobs in a module-level
`_JOBS: dict[str, ExtractionJob]` (and `_REPORT_JOBS` at `:396`) guarded by a
lock, with a `ThreadPoolExecutor(max_workers=1)` (`:227`). It is
**process-local**: it resets on restart and does not coordinate across
multiple API instances. The *durable* output is the materialised deal-model
cache — the job store is only the live status of an in-flight extraction, not
a persistent queue. Fine for the single-worker demo deployment; not a
multi-instance job system. The audit JSONL also defaults under `/tmp`
(`AUDIT_LOG_DIR`, `:66`).

### 3. A brand-new deal still needs a seed or a long extraction run before its Overview is populated
`GET /deal/{id}/model` (`main.py:649`) reads the cache read-only and returns
`not_cached` on a miss rather than blocking the request for the ~20–37 min
Docling + Vertex extraction; `_load_cached_deal_model` (`main.py:419`) never
triggers a cold extraction. A new deal becomes cold-startable only after a
committed seed ships for it (`src/loanwhiz/data/deals/seed/*.json`) or a
`POST /deal/{id}/extract` run completes.

**The on-demand extraction endpoint does not change this.** `POST
/deal/{id}/extract` (`main.py:715`, 202 Accepted) plus `GET
/deal/{id}/extract/status` (`main.py:757`) is an **explicit,
operator-initiated, asynchronous** action: a client must deliberately POST and
then poll. No read or view path calls it — the web client never requests it,
and the Overview renders an honest empty state instead. There is still **no
inline cold-extract on first view**.

### 4. Projection assumptions are supplied, not estimated
The multi-period projection above is a real forward fold, but its CPR / CDR /
recovery / rate-shift inputs come from named presets or a caller-supplied
`assumptions` override — they are **not** estimated from the deal's own tape
history. The 2026 tapes are re-sampled per period (loan IDs do not persist),
so speeds cannot be estimated from them anyway; see `docs/data-card.md`. Read
the Projection panel as "what the structure does *under these assumptions*",
never as a forecast. `months` also carries no upper bound.

### 5. Committed completeness scores are point-in-time artifacts, and the router is not reproducible from a clean checkout
`completeness_score` is stored in each seed's metadata at extraction time; it
is not recomputed on read. **Green Lion 2026-1's committed `0.75` predates the
scoring change** — it is the old section-header ratio (3 of 4), where the
current `_completeness_score` (`assembler.py:240`) scores that same seed
`0.925`. Every other seed's stored score matches the current formula. Treat a
stored score as "what that extraction run reported", not as a current
measurement of the seed.

Separately, the scores are **not reproducible from a clean checkout**. #445
cached the LLM section router — the last uncached LLM step — so repeated runs
*on one machine* now replay the same answer. But that cache lives in
`data/extraction_cache/`, which is gitignored (`.gitignore:16-17`), so a fresh
clone starts cold: the router re-asks the model and may resolve a different
`sections_found`, and hence a different completeness, for the same document.
Per-machine reproducibility is not third-party reproducibility — a reader still
cannot independently re-derive the published figures, and doing so would need
GCP credentials and a multi-hour run regardless.

### 6. Covenant proximity / metric caveats that still genuinely hold
The covenant monitor enforces a runtime `threshold_unit` guard at the seam
(#372) and tracks tape-native (B7) arrears/LTV/default triggers, so the empty-
chart class of problems the old audit flagged is largely addressed for
tape-derivable metrics. PDL/reserve-style triggers whose period-over-period
scalars are not sourced from a reconstructed state still cannot show meaningful
proximity: `_compute_proximity` (`covenant_monitor.py:431`) returns `None` —
`proximity_pct` is documented `None = not evaluable` (`:317`) — when no target
is resolvable. Treat a flat/zero proximity on those as "not evaluable from
current inputs", not "healthy".

**They are also ungraded.** The quality harness carries four checks —
`revenue_pop`, `redemption_pop`, `covenants`, `pool_stats` — and
`_grade_covenants` (`quality_harness.py:441`) short-circuits to
not-applicable when the answer key carries no published covenant results
(`:464`). Both committed answer keys have empty `covenants` and `pool_stats`
in every period, so those cells are not-applicable for **every** deal, and no
PDL-balance or reserve-proximity check key exists at all. The published PDL
and Reserve Account figures *are* present in the committed report fixtures and
are parsed, but nothing reconciles the **engine's** computed PDL against them.

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

**There is no CI.** The repo has no `.github/` directory and no workflows, so
nothing runs the suite automatically on a push or a pull request. The local
offline run above is the **only** gate this project has, and every claim that
"the suite passes" is a claim about someone's machine. This is a real
infrastructure gap, recorded here because a reader is entitled to know that a
green PR was verified by hand.

> **Running the suite on a GCP-credentialed host mutates its own inputs.** The
> run materialises a Green Lion model into the gitignored runtime cache
> `data/deals/*.json`, and `_load_cached_deal_model` prefers that cache over
> the committed seed. Its trigger set differs from the seed's, so a *second*
> run fails `tests/test_api.py::test_stress_matrix_first_breach_discriminates_stress`
> and `::test_deal_compliance_proximity_series_is_non_flat` for reasons
> unrelated to any diff. `rm -f data/deals/*.json` restores green.
