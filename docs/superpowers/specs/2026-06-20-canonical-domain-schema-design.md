# Canonical domain schema — the LoanWhiz deal contract

**Date:** 2026-06-20
**Status:** Design (decisions locked in brainstorm; pending implementation)
**Phase:** 1 (foundation) — every other phase is downstream of this.

---

## Context & purpose

LoanWhiz today has no single canonical schema. The same domain concept exists in
several incompatible typed shapes — a waterfall step appears as `WaterfallStep`
(extraction), `StepSpec` (interpreter), `PoPStep` (Notes & Cash parser), and
builtin step-dicts (old runner); a note class appears four ways; a trigger three.
Every boundary between them carries hand-written mapping code, and a whole class
of the `MODELING-GAPS` bugs are *boundary-mapping* bugs (A4: extractor metric
names matched none of the monitor's sentinels → silent 0.0; C8: `threshold_unit`
dropped on mapping → 100× error).

This document defines **one canonical domain model** — `DealRules`,
`PeriodInputs`, `DealState` — that:

- every **extractor fills** (prospectus → `DealRules`; report → `PeriodInputs` +
  seed `DealState`), under schema validation, and
- the **engine consumes directly** — no mapping glue, because there is nothing
  to map *to*.

The schema is the contract between the ingestion adapters and the
`fold(run_period)` engine. It is also what turns extraction from trial-and-error
into a measurable target: an LLM extractor doing **structured output against
these types** is filling a fixed, validated form, and "completeness" becomes
"which required fields are filled."

> **The tape side already has a canonical schema — ESMA Annex 2** (the regulatory
> loan-level template, normalised by deeploans). The prospectus and report sides
> are prose with *no* external machine schema, which is exactly why we must
> define one here.

This schema is validated end-to-end in **Phase 2a**: `DealRules` + report-sourced
`PeriodInputs` → `fold` → `DealStateSeries`, reconciled to Green Lion 2024-1's
published Notes & Cash **to the cent**. That reconciliation is what *locks* the
contract before the expensive Phase-3 extraction work is built against it.

---

## Locked design decisions

1. **Provenance is a sidecar map, not per-field wrappers.** The engine reads
   plain typed values; the governance layer and human-review gate read a parallel
   `ProvenanceMap`. Keeps the hot path clean.
2. **The recipient and metric taxonomies are closed enums with an explicit
   `unmapped` escape.** A deal's exotic step degrades honestly to
   "report-supplied / not-evaluable" instead of silently mis-mapping. Open
   strings would reintroduce the boundary-mapping bug class.
3. **A step's amount is a bound calculator-key, never a free-form formula
   string.** Executable and safe; the prose is retained only as `raw_text` for
   audit. Free formulas = unbounded eval = a trap.
4. **`PeriodInputs` always stores aggregate available funds; the finer `legs`
   are optional.** Aggregate is the common denominator both a tape and a report
   can supply; legs are the tape's bonus.
5. **ESMA Annex 2 anchors as citation *locators*, not as new fields.** RTS field
   codes live in `Citation.page_or_row` on the provenance of `RiskSignals` /
   `CollectionLegs`. The *mechanism* is defined now; the full Annex-2 field-code
   mapping table is a **Phase 4** detail (it lands with tape ingestion).

---

## Provenance model

```python
class FieldProvenance(BaseModel):
    source:     Literal["prospectus", "report", "tape", "config", "engine", "reconciled"]
    method:     Literal["deterministic", "ocr+llm", "llm", "computed"]
    confidence: float                       # 0.0–1.0
    citation:   Citation | None             # reuse base.Citation{document, page_or_row, excerpt}
    reconciled: bool = False                # a cross-check confirmed it (report path)

# Keyed by dotted field path, e.g. "tranches.class_a.original_balance".
ProvenanceMap = dict[str, FieldProvenance]
```

- Engine-computed / deterministic values carry `confidence = 1.0` (or are simply
  absent from the map — absence means "not extracted, derived").
- `reconciled = True` is the **strong correctness signal** the Reconciler sets
  when an engine-recomputed line matches a report-stated line to the cent. The
  human-review gate routes only **unreconciled, low-confidence** fields to a
  person — this is the report path's advantage over the prospectus path (you can
  recompute distributions; you cannot recompute rules).

---

## 1. `DealRules` — the program (extracted from the prospectus)

### Canonical recipient taxonomy

The recipient enum is what makes an extracted step *executable*: each value binds
to one engine need-calculator. Ordered roughly senior→junior.

```python
class RecipientType(str, Enum):
    senior_expenses        = "senior_expenses"        # issuer costs, admin, trustee
    servicing_fee          = "servicing_fee"
    swap_payment           = "swap_payment"
    class_a_interest       = "class_a_interest"
    class_b_interest       = "class_b_interest"
    class_c_interest       = "class_c_interest"
    class_a_pdl_cure       = "class_a_pdl_cure"       # PDL replenishment, senior
    class_b_pdl_cure       = "class_b_pdl_cure"
    reserve_replenishment  = "reserve_replenishment"
    class_a_principal      = "class_a_principal"
    class_b_principal      = "class_b_principal"
    class_c_principal      = "class_c_principal"
    subordinated_amounts   = "subordinated_amounts"   # subordinated swap, deferred fees
    residual_certificate   = "residual_certificate"   # deferred purchase price / residual
    unmapped               = "unmapped"               # explicit escape → report-supplied / not-evaluable
```

### Amount, condition, step

```python
class AmountRule(BaseModel):
    calculator: RecipientType                  # binds to the engine's need-calculator
    basis: Literal[
        "interest_accrual",      # balance × rate × days / basis
        "pdl_balance",           # cure up to outstanding PDL
        "target_shortfall",      # reserve: max(0, target − balance)
        "principal_due",         # amortisation / sequential / pro-rata
        "report_supplied",       # no engine formula — amount comes from PeriodInputs.step_overrides
        "residual",              # whatever remains (terminal step)
    ]
    raw_text: str                              # verbatim prose, for audit

class ConditionRef(BaseModel):
    trigger_name: str                          # references a TriggerRule by name
    when: Literal["breached", "not_breached"]  # gate direction

class StepRule(BaseModel):
    order: int                                 # absolute order within the waterfall
    priority_label: str                        # "(a)", "5.2(a)"
    recipient: RecipientType
    amount: AmountRule
    condition: ConditionRef | None = None      # None = unconditional
    pari_passu_group: str | None = None        # equal-ranking parties share a group id
```

### Canonical metric taxonomy (triggers / covenants)

```python
class MetricType(str, Enum):
    cumulative_loss_rate = "cumulative_loss_rate"
    class_a_pdl          = "class_a_pdl"
    class_b_pdl          = "class_b_pdl"
    reserve_fund_ratio   = "reserve_fund_ratio"
    pool_factor          = "pool_factor"
    arrears_90d_ratio    = "arrears_90d_ratio"
    arrears_180d_ratio   = "arrears_180d_ratio"
    wa_ltv               = "wa_ltv"
    unmapped             = "unmapped"

class TriggerRule(BaseModel):
    name: str
    metric: MetricType
    operator: Literal["<", "<=", ">", ">=", "=="]
    threshold: float | None                    # None = qualitative / not quantified
    threshold_unit: Literal["percent", "fraction", "bps", "eur"]   # normalised ONCE, here
    consequence: str                           # e.g. "switch to sequential pay"
```

### Tranches, rate, reserve

```python
class RateRule(BaseModel):
    kind: Literal["fixed", "floating"]
    fixed_pct: float | None = None
    index: str | None = None                   # "EURIBOR_3M"
    margin_bps: float | None = None

class TrancheRule(BaseModel):
    name: str                                  # "Class A"
    seniority: int                             # 0 = most senior
    original_balance: float
    rate: RateRule
    rating: str | None = None

class ReserveRule(BaseModel):
    floor: float = 0.0
    pct_of_note_balance: float | None = None   # target = max(floor, pct · note_balance)
```

### The aggregate

```python
class DealRules(BaseModel):
    deal_id: str
    deal_name: str
    jurisdiction: str
    currency: str = "EUR"
    tranches: list[TrancheRule]
    waterfalls: dict[Literal["revenue", "redemption", "post_enforcement"], list[StepRule]]
    triggers: list[TriggerRule]
    reserve: ReserveRule
    provenance: ProvenanceMap = {}
    completeness: float                        # required canonical fields filled (see below)
```

---

## 2. `PeriodInputs` — uniform per-period exogenous inputs

Produced by **any** adapter (tape, report, or scenario generator). Supersedes the
tape-only `PeriodCollections`.

```python
class CollectionLegs(BaseModel):               # finer, tape-only; legs sum to the aggregates
    interest: float
    scheduled_principal: float
    prepayment: float
    recovery: float
    realized_loss: float

class RiskSignals(BaseModel):                  # tape-only; future B7; ESMA Annex 2-anchored via provenance
    arrears_90d: float
    arrears_180d: float
    wa_ltv: float
    default_pct: float
    pool_balance: float

class PeriodInputs(BaseModel):
    reporting_date: str
    days_in_period: int
    available_revenue: float                   # common denominator (a report gives this directly)
    available_principal: float
    realized_loss: float
    legs: CollectionLegs | None = None         # present on the tape path, None on the report path
    step_overrides: dict[str, float] = {}      # priority_label -> reported amount (report path)
    step_sources: dict[str, Literal["engine", "reported", "residual"]] = {}
    risk_signals: RiskSignals | None = None
    source: Literal["tape", "report", "scenario"]
    provenance: ProvenanceMap = {}
```

---

## 3. `DealState` — evolving structural state

```python
class TrancheState(BaseModel):
    name: str
    balance: float
    pdl_balance: float

class DealState(BaseModel):
    reporting_date: str
    tranches: list[TrancheState]
    reserve_balance: float
    reserve_target: float
    pool_balance: float
    original_pool_balance: float
    cumulative_losses: float
    sequential_pay_active: bool
    provenance: ProvenanceMap | None = None    # set only on the period-0 seed (B5); None on rolled states
```

`DealStateSeries = fold(run_period, seed, inputs[])`. The seed carries provenance
(it was *extracted* from a prospectus or report); every rolled state is
engine-computed and needs none.

---

## Consolidation map — what each canonical type supersedes

| Canonical type | Replaces |
|---|---|
| `StepRule` + `RecipientType` | `WaterfallStep`, `StepSpec`, `PoPStep`, builtin step-dicts |
| `TrancheRule` / `TrancheState` | `tranche_structure` dict, `NoteClassBalance`, `capital_structure` dict, `WaterfallInput.class_*` fields |
| `TriggerRule` + `MetricType` | `Trigger`, `TriggerState`, `DEFAULT_TRIGGERS`, monitor sentinels + the metric-alias map |
| `PeriodInputs` | `PeriodCollections`, ad-hoc `WaterfallFunds` construction, the harness's `need_overrides` |
| `DealState` | today's `DealState` (generalised; seed-provenance added) |

The metric-alias map and `_build_specs` step-classifier become **unnecessary** —
extraction targets the canonical names directly.

---

## Completeness — honest, field-based

`DealRules.completeness` = fraction of **required canonical fields** populated
with non-null, in-taxonomy values. Required set (minimum to drive the engine):

- ≥1 tranche with `original_balance` and `rate`
- a `revenue` waterfall with ≥1 step whose `recipient != unmapped`
- a `redemption` waterfall with ≥1 step
- `reserve` target resolvable
- ≥1 trigger with a non-null `threshold`

This replaces the header-count metric (which read 1.0 on a structurally empty
model). A step with `recipient = unmapped` does **not** count toward completeness.

---

## ESMA Annex 2 anchoring (mechanism now, mapping later)

`RiskSignals` and `CollectionLegs` fields are derived from the loan tape. Their
provenance entries carry the **ESMA RTS Annex 2 field code** in
`Citation.page_or_row` (e.g. `"RREL ... arrears balance"`), so a value is
traceable to the regulatory field it came from. Defining the full code→field
mapping table is **Phase 4** (it ships with deeploans tape ingestion); only the
locator *mechanism* is fixed here.

---

## Placement & validation

- **Home:** a new `src/loanwhiz/domain/` module (separate from `primitives/`),
  imported by both adapters and the engine. (Placement to confirm at
  implementation.)
- **Validation:** the schema is proven by **Phase 2a** — `DealRules` +
  report-sourced `PeriodInputs` fold to a `DealStateSeries` that the Reconciler
  matches to Green Lion 2024-1's Notes & Cash to EUR 0.01. If a required field or
  taxonomy value is missing for that reconciliation, the schema is wrong and is
  fixed *here*, cheaply, before Phase-3 extraction is built against it.

---

## Open / deferred

- Full ESMA Annex 2 field-code mapping table → Phase 4.
- `RecipientType` / `MetricType` may gain values as non-GL deals are onboarded
  (Phase 3); the `unmapped` escape makes that additive, not breaking.
- Multi-currency / cross-currency swap modelling beyond a single `currency` →
  out of scope until a deal demands it (YAGNI).
