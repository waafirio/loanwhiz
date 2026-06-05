# LoanWhiz — Modeling Gap Audit (2026-06-05)

A deep pass across extraction, waterfall execution, covenant monitoring, the
tape↔structural-state linkage, and projection/reconciliation. Every claim is
grounded in `file:line`. Reference deal: Green Lion 2026-1 (Dutch RMBS).

> **Context note.** Two code paths exist depending on whether a cached deal
> model is present. On the clean checkout `data/deals/` is empty → the API falls
> back to `DEFAULT_TRIGGERS` + hardcoded constants. The **running demo**
> (`/var/tmp/loanwhiz-main`) *has* a cached model → it exercises the extracted-
> trigger path (the 3 PDL/reserve triggers, `threshold=null`, the empty chart).
> The gaps below are in the code and hold on both paths.

---

## The one root theme

**The extracted deal model is presentational, and there is no period-to-period
state machine wired in.** Two structural facts cause ~80% of the findings:

1. **The model never drives the computation.** `WaterfallRunner` hardcodes Green
   Lion's 11-step revenue + 4-step redemption sequence in Python
   (`waterfall_runner.py:252-395`) and never reads `DealModel.waterfalls`
   (`extraction/waterfall_extractor.py:57-64`). Conditions, amounts, and
   pari-passu are stored as un-evaluable prose. → It can't model any other deal,
   and even for Green Lion the conditional logic (sequential-pay switch) is lost.

2. **The state loop exists but is dead.** `MultiPeriodWaterfallRunner` +
   `WaterfallState` (`waterfall_state.py:289-423`) is fully implemented and
   tested but **never imported by the API**. `record_loss`,
   `revolving_period_active`, and reserve `withdrawal` have **zero callers** →
   PDLs and cumulative losses are permanently 0. `/waterfall`, `/compliance`,
   `/project` each compute disconnected one-off snapshots from three different
   structural sources, so they disagree on basic facts (three different "current
   pool balance" values: tape-sum vs `1_033_412_063` vs `1_063_600_000`).

Everything in Tier 1 is a facet of these two.

---

## Tier 1 — Architectural (the spine)

### A1. Runner ignores the extracted Priority of Payments (hardcoded Green Lion)
- `waterfall_runner.py:252-322` (revenue), `:350-395` (redemption); bypassed model `waterfall_extractor.py:57-64`.
- Extraction layer is decorative for cashflow. Won't generalize; extracted 8-step **Post-Enforcement** waterfall has *no executor at all*.
- **Direction:** make the runner *interpret* `DealModel.waterfalls[*].steps` (priority order, recipient→input binding, condition gating).

### A2. No reconstructed structural state across periods (the dead state machine)
- `MultiPeriodWaterfallRunner`/`WaterfallState` unwired (`waterfall_state.py:289-423`); `record_loss`/`revolving_period_active`/reserve-`withdrawal` have no callers (`:97,88,208`). `/waterfall` resets PDL/reserve to 0 every request (`api/main.py:593-596`); `/project` likewise (`:889-890`).
- Tranche balances never amortize: runner computes `closing_balance` (`waterfall_runner.py:433`) then **discards** it; Class A opening stays €1bn for all 27 months.
- **Direction:** wire `/waterfall` (or a `/waterfall/series`) through `MultiPeriodWaterfallRunner` over all periods, threading ending→opening state.

### A3. Sequential Pay Trigger (pro-rata ↔ sequential) not implemented
- `waterfall_runner.py:362-390`: Class A principal need = *all* available principal (`:368`); Class B always gets ~0 (`:382`). Steps mislabeled `condition="pari passu"` (`:375,388`) but math is pure sequential.
- Tranche WALs/timing wrong in the **normal (non-stressed)** state; subordinate cashflows never emerge.
- **Direction:** evaluate the trigger from deal state; branch pro-rata-by-balance vs sequential.

### A4. Compliance metric mismatch + structural inputs never plumbed (the empty chart)
- Extractor metric names (`pdl_debit_balance`, `reserve_fund_balance`, `cumulative_loss_rate_pct`, `pool_balance_fraction`) match **none** of the monitor's sentinels (`covenant_monitor.py:233-260`) → silent `0.0` (`:260`). `_map_extracted_trigger` forwards `raw["metric"]` verbatim (`api/main.py:371`).
- `deal_compliance` builds `CovenantInput` with only periods/triggers/original_pool_balance (`api/main.py:433-439`) → PDL/reserve scalars default 0/0; `reserve_fund_ratio`→`100.0` ("fully funded"), the most dangerous default for a risk monitor.
- `_compute_proximity` returns `0.0` whenever threshold is null/0 (`:174-175`), conflating healthy / no-threshold / unevaluable. `non_zero` collapses to `above`+`threshold=None` (`api/main.py:358-362`) → PDL triggers can never show proximity.
- **Direction (minimal for a real curve):** add a metric-alias map in `_map_extracted_trigger` (`pool_balance_fraction→pool_balance_pct`, `cumulative_loss_rate_pct→default_pct`) — these need only the already-passed `original_pool_balance` and immediately yield a non-zero 27-period curve. Mark PDL/reserve `not_evaluable` instead of emitting 0.

### A5. `/project` doesn't project; the real projector + verifier are dead code
- `POST /deal/{id}/project` makes a **single** `WaterfallRunner` call per scenario (`api/main.py:876`); "12 months" faked by scaling collections (`:869-870`); WAL mechanically pinned to the horizon (`:900-909`).
- `CashflowProjector` (period-iterating, SMM/CDR/PDL) is imported for side-effects only (`api/main.py:66-71`) — wired to no endpoint/agent tool. `ReportVerifier` likewise unreachable (`report_verifier.py:406-560`, only `api/main.py:69`).
- **Direction:** drive `/project` through `CashflowProjector`; add `/deal/{id}/verify-report` running the waterfall→`ReportVerifier`.

---

## Tier 2 — High (correctness within a snapshot)

### B1. Six revenue steps + redemption (a) distribute hardcoded zeros
- `waterfall_runner.py:259-266` (operating fees), `:286-288` (expense acct), `:297-304` (subordinated swap), `:310-317` (Class C principal), `:353-360` (new receivables). Operating fees are real senior cash leakage → **overstates** funds to junior steps, understates shortfall.

### B2. Only Class A interest modeled; Class B interest absent, Class C int/prin conflated
- `waterfall_runner.py:271-272`, `:418` (B interest = 0), `:422-425` (C "interest" = principal). Understates senior revenue uses; semantically wrong tranche distribution.

### B3. Collections: prepayments & recoveries always 0; interest is synthetic
- `collections_aggregator.py:302` (unscheduled=0), `:307` (recoveries=0), `:276-279` (principal = full balance delta, lumping scheduled+prepay); interest = balance × wtd-coupon × days/360 (`:255-257`), ignoring arrears/defaults. Recoveries=0 means PDL-cure-via-recovery can never happen.

### B4. Reserve target hardcoded to 0 → step (f) inert; reserve covenant always "funded"
- `api/main.py:593` (`reserve_*=0.0`); `waterfall_runner.py:221-224,279-282`. Reserve fund is a core credit enhancement modeled as absent; inflates residual to Deferred Purchase Price (step k).

### B5. Investor reports parsed but never ingested to seed structural state
- 3 monthly report PDFs carry exactly the missing figures (`report_verifier.py:61-67`: reserve, pool, class_a principal/interest) but are used only for (unwired) comparison, never to *source* opening PDL/reserve/tranche state (`config.py:73-77`, no endpoint invokes the verifier). This is *why* Tier-1 falls back to constants.

### B6. Endpoints source structural state three divergent ways → inconsistent
- `/waterfall` (`api/main.py:549,593`) vs `/compliance` (`:419-439`) vs `/project` (`:865-891`). Single reconstructed per-period state object should back all three.

### B7. No tape-derivable covenants tracked
- The tape carries period-varying signals (arrears 1-2m/180d+, default %, WA-LTV, pool balance) — `esma_tape_normaliser.py:181-220` — but only `default_pct` + `pool_balance_eur` are wired (and only under DEFAULT_TRIGGERS). The live data that would make the chart move is unused.

### B8. `report_verifier` can't reconcile pool_balance / reserve_fund_balance
- `report_verifier.py:289-345`: both default to `0.0` → any reported value trips the `999.0` false-mismatch sentinel (`:358-363`); `total_collections` proxied by `total_distributed` (`:323`) — different quantities.

---

## Tier 3 — Modeling quality / fidelity

- **C1.** `completeness_score` = fraction of 4 section headers found (`assembler.py:126-131`) — can be 1.0 with **zero** waterfall steps. `extraction_confidence` = recipients-non-empty check (`waterfall_extractor.py:516-521`). Both are false confidence signals.
- **C2.** Tranche closing balance ignores PDL/loss write-downs (`waterfall_runner.py:433-451`) — junior notes can only fall via cash, never loss allocation.
- **C3.** `cumulative_loss_trigger` uses point-in-time `default_pct`, not a cumulative realised-loss series (`covenant_monitor.py:301-322`) — non-monotonic, ignores loans that left the pool. `original_pool_balance` is passed but unused for this.
- **C4.** Reserve-account target modeled as a scalar; should be a per-period formula `max(floor, %·note balance)` (`waterfall_runner.py:221-224`).
- **C5.** Projector amortization = flat 1%/month (`cashflow_projector.py:61,381`); monthly CDR linearized `/12` (`:339`) inconsistent with the geometric SMM (`:344`).
- **C6.** Scenario coverage = one dimension (base + 0.7 haircut). No CPR±/CDR-spike/severity/rate grid.
- **C7.** Definitions stored flat, never linked into steps/triggers; covenant extractor ignores the graph; no term→term edges despite "graph" name (`assembler.py:248-254`, `definitions_graph.py:94-104`).
- **C8.** `threshold_unit` captured then dropped on mapping → fraction(0.10) vs percent(10.0) 100× risk (`covenant_extractor.py:51`, `api/main.py`).
- **C9.** `is_pari_passu` is a bare bool with no group id/weights (`waterfall_extractor.py:53`) → can't split a shortfall pro-rata among equal-ranking parties.
- **C10.** Hardcoded constants that should be deal/period-dynamic: original pool balance, coupon, day-count (90), swap=0, capital structure (`waterfall_state.py:91`, `collections_aggregator.py:99-117`, `api/main.py:457-482,588`).
- **C11.** Fixed confidence 0.7 on projector; no confidence on the `/project` response.

---

## Suggested order of attack (tomorrow)

The spine first; quality fixes fall out of it.

1. **Reconstruct per-period state** (A2): wire `MultiPeriodWaterfallRunner` into the API, thread tranche balances + PDL + reserve, seed period-0 from the **investor report** (B5). This single change unblocks A4/B4/B6/B7/C2.
2. **Drive execution from the extracted model** (A1) + implement the **sequential-pay** branch (A3) + real step amounts (B1/B2).
3. **Real collections** from the tape (B3) feeding the waterfall.
4. **Compliance:** metric-alias map + plumb PDL/reserve from #1; add tape-native covenants (A4/B7) — *fastest visible demo win is the metric-alias for the proximity chart.*
5. **Projection:** route `/project` through `CashflowProjector`; wire `ReportVerifier` (A5/B8).
6. Honest **scoring** (C1) and the remaining Tier-3 fidelity items.

**Quickest demo-visible win** (if you just want the Compliance chart real before the deep work): the A4 metric-alias map (`pool_balance_fraction→pool_balance_pct`, `cumulative_loss_rate_pct→default_pct`) — pure `api/main.py` change, needs no new state.
</content>
