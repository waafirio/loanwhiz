"""Pool-level full-pipeline harness — the V5 coarse end-to-end check (#211).

This is the **seasoned-deal** counterpart to V1's exact ``reconciliation_harness``
(#187) and V4's exact engine-vs-published-PoP reconciliation (#210). The real ING
Green Lion **2023-1 / 2024-1** deals publish two report families but **no
loan-level tapes** (those live in a private repository), so the validation here
is deliberately **coarser** than the demo deal's loan-level proof:

The pipeline this harness runs, end to end
------------------------------------------
1. **Reconstruct collateral at POOL level** from the monthly collateral reports
   (``extraction.collateral_ledger.CollateralLedger``). With no loan tape, a
   period's collections are derived by **net-reconciliation to pool movement**
   (``pool_balance_begin − pool_balance_end``), not by loan-level cashflow
   tracking — see :func:`collections_from_collateral_period`. The collateral
   report carries no revenue figures, so interest is *approximated* from the
   reported weighted-average coupon, and recovery is left at 0 — these are
   named, surfaced coarseness, not hidden assumptions.
2. **Seed the liability side from the prospectus** (the deal's extracted
   capital structure — tranche balances, reserve target, original pool), exactly
   as the spine's ``DealState.seed_from_prospectus`` prescribes (liabilities seed
   from the prospectus, never from reports — spike S0 / #180).
3. **Run the waterfall end to end** through the existing reconstruction engine
   (``period_state_machine.reconstruct_period_series``) — this harness does NOT
   re-implement the waterfall, the trigger logic, or the state transition; it
   composes the engine.
4. **Compare the reconstructed closing state to the Notes & Cash actuals**
   (V3's ``notes_cash_parser.NotesCashReport``) on the overlapping reporting
   dates: per-class tranche balances, total PDL, reserve balance, and the
   aggregate revenue/redemption distributed totals.

Why this is a *characterisation*, not a cent-level PASS gate
------------------------------------------------------------
V1/V4 gate "to the cent" because they have an exact ground truth on the side
they reconcile. V5 does not: the collateral reports are **monthly** while the
Notes & Cash reports are **quarterly**, so the join is on the few overlapping
dates; pool-level aggregation loses the loan-level cashflow split; and (per the
``loanwhiz-modeling-gaps`` finding) the reconstruction engine is partly
decorative — the sequential-pay branch and several conditional gates are
approximate. A tight tie is therefore **not** expected. This harness reports
per-line deltas and an honest ``match_quality`` narrative with an explicit
``caveats`` list naming the precision-loss sources, instead of manufacturing a
PASS/FAIL the data cannot support. The ``within_coarse_band`` flag uses a
generous *relative* band (default 5%) purely to characterise — it is not a
correctness gate.

Pure & deterministic — no LLM, no network. The Green-Lion convenience builder
(:func:`run_pool_pipeline_green_lion`) touches the durable extraction caches /
Gemini / ``pypdf`` to *obtain* the ledger + report, so that single function is
integration-gated; the pipeline core (:func:`run_pool_pipeline`) is fully
offline and unit-tested.

Each deal is validated only against its OWN data — never spliced (epic #206).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from loanwhiz.extraction.collateral_ledger import CollateralLedger, CollateralPeriod
from loanwhiz.primitives.deal_state import DealState, PeriodCollections
from loanwhiz.primitives.notes_cash_parser import NotesCashPeriod, NotesCashReport
from loanwhiz.primitives.period_state_machine import (
    DealStateSeries,
    PeriodInput,
    reconstruct_period_series,
)

# Granularity tag — stamped on every report so the consumer never mistakes this
# coarse pool-level characterisation for V1/V4's exact cent-level reconciliation.
GRANULARITY = "pool-level (coarse) — seasoned-deal collateral→waterfall→notes-cash"

# Default *characterisation* band: a line is "within band" when its relative
# deviation is within this fraction. This is NOT a correctness gate (see module
# docstring) — it only buckets per-line deltas for the human-readable summary.
DEFAULT_COARSE_BAND_PCT = 5.0

# The standing caveat list — the precision-loss sources that make this a coarse
# check by construction. Surfaced on every report so the divergence is honestly
# attributed rather than read as engine error.
STANDING_CAVEATS: tuple[str, ...] = (
    "Pool-level, not loan-level: the seasoned deals publish no loan tape, so "
    "collections are net-reconciled to the reported pool movement "
    "(pool_balance_begin − pool_balance_end), losing the loan-level cashflow "
    "split that V1's demo-deal proof relies on.",
    "Monthly↔quarterly join: collateral (Portfolio & Performance) reports are "
    "monthly; Notes & Cash (liability) reports are quarterly. Liability actuals "
    "exist only on the quarter-end dates, so the comparison runs on the few "
    "overlapping reporting dates, not every collateral period.",
    "Revenue side approximated: the collateral report carries no interest/fee "
    "figures, so available revenue is approximated from the reported "
    "weighted-average coupon and recovery is taken as 0 — the revenue waterfall "
    "is therefore indicative.",
    "Engine is partly indicative (modeling-gaps audit, 2026-06-05): the "
    "sequential-pay pro-rata↔sequential branch and several conditional gates in "
    "the reconstruction engine are approximate, so reconstructed tranche "
    "amortisation/PDL/reserve are not expected to tie to the cent.",
)


# ===========================================================================
# Step 1 — collections from the collateral pool roll-forward
# ===========================================================================


def collections_from_collateral_period(
    period: CollateralPeriod,
    *,
    days_in_period: int = 30,
) -> PeriodCollections:
    """Derive a period's :class:`PeriodCollections` from a collateral report period.

    With no loan tape, principal is **net-reconciled to pool movement**: the
    report's own ``repayments`` / ``prepayments`` roll-forward lines map directly
    to ``scheduled_principal`` / ``prepayment``. Realized loss is the report's
    ``default_amount`` where present. Interest is *approximated* from the reported
    weighted-average coupon applied to the opening pool balance over the period
    (Act/360) — the collateral report carries no interest figure, so this is an
    explicit indicative estimate (see module caveats), not a measured cashflow.
    Recovery is left at 0 (the collateral report does not break it out).

    Parameters
    ----------
    period:
        One collateral-ledger period (the monthly Portfolio & Performance
        roll-forward).
    days_in_period:
        Day count for the Act/360 interest approximation.

    Returns
    -------
    PeriodCollections
        The asset-side input the reconstruction engine consumes for this period.
    """
    # Interest approximation: wtd-avg coupon on the opening pool, Act/360. The
    # collateral report has no interest line, so this is indicative only.
    interest = 0.0
    if period.wtd_avg_coupon_pct is not None and period.pool_balance_begin > 0.0:
        interest = (
            period.pool_balance_begin
            * (period.wtd_avg_coupon_pct / 100.0)
            * (days_in_period / 360.0)
        )

    return PeriodCollections(
        interest=max(0.0, interest),
        scheduled_principal=period.repayments,
        prepayment=period.prepayments,
        recovery=0.0,
        realized_loss=max(0.0, period.default_amount or 0.0),
    )


def build_period_inputs(
    ledger: CollateralLedger,
    *,
    days_in_period: int = 30,
) -> list[PeriodInput]:
    """Build the ordered engine :class:`PeriodInput` list from a collateral ledger.

    One ``PeriodInput`` per collateral period, in reporting-date order, keyed by
    the period's ISO reporting date. The collections for each period are derived
    by :func:`collections_from_collateral_period` (pool-movement net-reconciliation).
    """
    return [
        PeriodInput(
            reporting_date=period.reporting_date,
            collections=collections_from_collateral_period(
                period, days_in_period=days_in_period
            ),
            days_in_period=days_in_period,
        )
        for period in ledger.periods
    ]


# ===========================================================================
# Step 2 — prospectus capital structure from the extracted deal model
# ===========================================================================

_TRANCHE_KEY_BY_SENIORITY = ("class_a_balance", "class_b_balance", "class_c_balance")


def capital_structure_from_deal_model(deal_model: dict[str, Any]) -> dict[str, float]:
    """Read the seed prospectus capital structure into the engine's shape.

    Maps the extracted deal model's ``tranche_structure`` (a list of
    ``{name, size_eur, rating, rate, seniority}`` ordered senior→junior) onto the
    ``class_{a,b,c}_balance`` keys ``DealState.seed_from_prospectus`` expects.
    Tranches are ordered by their ``seniority`` field (ascending = most senior
    first); the first three map to Class A / B / C. A numeric ``rate`` is carried
    through as ``class_x_rate_pct`` for the interest needs where parseable.

    Parameters
    ----------
    deal_model:
        The extracted deal model dict (e.g. a committed seed under
        ``data/deals/seed/{slug}.json``), carrying ``tranche_structure``.

    Returns
    -------
    dict[str, float]
        ``{class_a_balance, class_b_balance, class_c_balance}`` plus any parseable
        ``class_x_rate_pct``.

    Raises
    ------
    ValueError
        If the deal model carries no usable tranche balances.
    """
    tranches = list(deal_model.get("tranche_structure") or [])
    tranches.sort(key=lambda t: (t.get("seniority") if t.get("seniority") is not None else 99))

    cap: dict[str, float] = {}
    for idx, key in enumerate(_TRANCHE_KEY_BY_SENIORITY):
        if idx >= len(tranches):
            break
        size = tranches[idx].get("size_eur")
        if size is None:
            continue
        cap[key] = float(size)
        rate = _parse_rate(tranches[idx].get("rate"))
        if rate is not None:
            cap[key.replace("_balance", "_rate_pct")] = rate

    if not any(k in cap for k in _TRANCHE_KEY_BY_SENIORITY):
        raise ValueError(
            "deal model has no usable tranche_structure size_eur figures — "
            "cannot seed the liability side from the prospectus"
        )
    # seed_from_prospectus requires all three balances; default a missing tranche
    # to 0 (a 2-tranche deal still seeds cleanly).
    for key in _TRANCHE_KEY_BY_SENIORITY:
        cap.setdefault(key, 0.0)
    return cap


def _parse_rate(raw: Any) -> float | None:
    """Best-effort parse of a tranche ``rate`` into a percent float, or None.

    Tranche rates in the seed are free text like ``"3m EURIBOR + 0.45"`` or a
    bare number. Only a cleanly numeric leading value is taken (the spread/margin
    is not a usable absolute coupon); a floating-rate description with no fixed
    coupon yields ``None`` (no interest need modelled for that tranche, matching
    the engine's missing-rate default).
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip()
    if not text:
        return None
    # A bare numeric string ("3.62") is a usable coupon; anything with letters
    # (a EURIBOR description) is not a fixed coupon we can apply.
    try:
        return float(text)
    except ValueError:
        return None


# ===========================================================================
# Output models
# ===========================================================================


class LiabilityLineCheck(BaseModel):
    """One reconstructed-vs-reported liability line, with its delta.

    Unlike V1's ``CollateralLineCheck`` (which gates a cent-level ``match``), this
    is a *characterisation*: it carries the delta and a coarse-band flag, never a
    correctness PASS/FAIL.

    Attributes
    ----------
    line_item:
        Canonical name, e.g. ``"class_a_balance"`` or ``"reserve_balance"``.
    reconstructed_value:
        The value from the engine-reconstructed ``DealState``.
    reported_value:
        The value from the Notes & Cash report (the liability actual).
    delta:
        ``reconstructed_value − reported_value`` (signed, EUR).
    abs_delta:
        ``abs(delta)`` (EUR).
    delta_pct:
        ``delta / reported_value * 100`` for display; ``0.0`` when both are 0,
        ``None`` when ``reported_value`` is 0 but reconstructed is not.
    coarse_band_pct:
        The relative band used to bucket this line as within/outside band.
    within_coarse_band:
        ``True`` iff the relative deviation is within ``coarse_band_pct`` (a
        generous characterisation band — NOT a correctness gate).
    """

    line_item: str
    reconstructed_value: float
    reported_value: float
    delta: float
    abs_delta: float
    delta_pct: float | None
    coarse_band_pct: float
    within_coarse_band: bool


class PeriodLiabilityComparison(BaseModel):
    """The pool-level liability comparison for one overlapping reporting period.

    Attributes
    ----------
    reporting_date:
        ISO period-end date — the join key (collateral period == notes-cash period).
    period_label:
        Human-readable label from the notes-cash period.
    line_checks:
        Per-line reconstructed-vs-reported comparisons (tranche balances, total
        PDL, reserve, distributed totals).
    lines_within_band / lines_total:
        Count of lines inside the coarse band, and the total compared.
    """

    reporting_date: str
    period_label: str
    line_checks: list[LiabilityLineCheck]
    lines_within_band: int
    lines_total: int


class PoolPipelineReport(BaseModel):
    """The coarse pool-level end-to-end characterisation across overlapping periods.

    Deliberately NOT a cent-level PASS/FAIL — see :data:`GRANULARITY` and
    :attr:`caveats`. It characterises how the prospectus-seeded, pool-level
    reconstruction tracks the Notes & Cash liability actuals.

    Attributes
    ----------
    deal_name:
        The deal this report covers.
    granularity:
        The standing granularity label (:data:`GRANULARITY`) — pool-level/coarse.
    coarse_band_pct:
        The relative characterisation band applied to every line.
    periods:
        One :class:`PeriodLiabilityComparison` per reporting date present on BOTH
        the reconstructed series and the Notes & Cash report.
    unmatched_notes_cash_dates:
        Notes & Cash reporting dates with no reconstructed state (reported but not
        reconstructed) — surfaced, never silently dropped.
    unmatched_reconstructed_dates:
        Reconstructed dates with no Notes & Cash period (e.g. the monthly
        collateral periods that fall between quarter-ends) — expected, surfaced.
    periods_compared:
        Number of overlapping periods compared.
    lines_within_band / lines_total:
        Aggregate line counts across all compared periods.
    within_band_pct:
        ``lines_within_band / lines_total * 100`` — the headline characterisation
        figure (``None`` when nothing was compared).
    match_quality:
        One-paragraph honest narrative of how well the coarse reconstruction
        tracks the actuals, and why a tight tie is not expected.
    caveats:
        The precision-loss sources (:data:`STANDING_CAVEATS`) that make this a
        coarse check by construction.
    summary:
        One-line human-readable characterisation.
    """

    deal_name: str
    granularity: str = GRANULARITY
    coarse_band_pct: float
    periods: list[PeriodLiabilityComparison]
    unmatched_notes_cash_dates: list[str]
    unmatched_reconstructed_dates: list[str]
    periods_compared: int
    lines_within_band: int
    lines_total: int
    within_band_pct: float | None
    match_quality: str
    caveats: list[str] = Field(default_factory=lambda: list(STANDING_CAVEATS))
    summary: str


# ===========================================================================
# Internal comparison helpers
# ===========================================================================


def _build_line_check(
    line_item: str,
    reconstructed_value: float,
    reported_value: float,
    band_pct: float,
) -> LiabilityLineCheck:
    """Construct a :class:`LiabilityLineCheck` with delta + coarse-band flag."""
    delta = reconstructed_value - reported_value
    abs_delta = abs(delta)
    if reported_value != 0.0:
        delta_pct: float | None = delta / reported_value * 100.0
        within = abs(delta_pct) <= band_pct
    elif reconstructed_value == 0.0:
        delta_pct = 0.0
        within = True
    else:
        # Reported 0 but reconstructed non-zero — unbounded pct; outside band.
        delta_pct = None
        within = False
    return LiabilityLineCheck(
        line_item=line_item,
        reconstructed_value=reconstructed_value,
        reported_value=reported_value,
        delta=delta,
        abs_delta=abs_delta,
        delta_pct=delta_pct,
        coarse_band_pct=band_pct,
        within_coarse_band=within,
    )


def _compare_period(
    state: DealState,
    notes_cash: NotesCashPeriod,
    band_pct: float,
) -> PeriodLiabilityComparison:
    """Compare one reconstructed state against one Notes & Cash period.

    Compares only the liability lines the Notes & Cash report actually publishes
    (a line whose reported figure is genuinely absent is skipped, not compared
    against a fabricated 0): per-class tranche balances, total PDL, reserve
    balance, and the aggregate revenue/redemption distributed totals.
    """
    checks: list[LiabilityLineCheck] = []

    # Per-class note balances (Bond Report → DealState tranche balances + PDL).
    reconstructed_balances = {
        "class_a": state.class_a_balance,
        "class_b": state.class_b_balance,
        "class_c": state.class_c_balance,
    }
    for cls_key, recon_bal in reconstructed_balances.items():
        reported = notes_cash.note_balance(cls_key)
        if reported is None or reported.principal_balance_after_payment is None:
            continue
        checks.append(
            _build_line_check(
                f"{cls_key}_balance",
                recon_bal,
                reported.principal_balance_after_payment,
                band_pct,
            )
        )

    # Total PDL — the report publishes per-class PDL; compare the aggregate.
    if any(b.pdl_balance_after_payment is not None for b in notes_cash.note_balances):
        checks.append(
            _build_line_check("total_pdl", state.total_pdl, notes_cash.total_pdl, band_pct)
        )

    # Reserve account balance.
    reserve_reported = notes_cash.reserve_balance
    if reserve_reported is not None:
        checks.append(
            _build_line_check(
                "reserve_balance", state.reserve_balance, reserve_reported, band_pct
            )
        )

    # Aggregate distributed totals — the engine's per-period collections vs the
    # report's published PoP totals. Revenue distributed ≈ interest collected
    # (what the revenue waterfall had to distribute); redemption distributed ≈
    # principal collected (what the redemption waterfall had to distribute).
    if notes_cash.revenue_pop:
        recon_revenue = state.collections.interest if state.collections else 0.0
        checks.append(
            _build_line_check(
                "revenue_distributed_total",
                recon_revenue,
                notes_cash.revenue_distributed_total(),
                band_pct,
            )
        )
    if notes_cash.redemption_pop:
        recon_redemption = (
            state.collections.total_principal if state.collections else 0.0
        )
        checks.append(
            _build_line_check(
                "redemption_distributed_total",
                recon_redemption,
                notes_cash.redemption_distributed_total(),
                band_pct,
            )
        )

    within = sum(1 for c in checks if c.within_coarse_band)
    return PeriodLiabilityComparison(
        reporting_date=notes_cash.reporting_date,
        period_label=notes_cash.period_label,
        line_checks=checks,
        lines_within_band=within,
        lines_total=len(checks),
    )


def _build_match_quality(
    periods_compared: int,
    within_band_pct: float | None,
    band_pct: float,
) -> str:
    """Honest one-paragraph narrative of how the coarse reconstruction tracks."""
    if periods_compared == 0:
        return (
            "No overlapping reporting dates between the pool-level reconstruction "
            "and the Notes & Cash liability actuals — the monthly collateral "
            "periods and the quarterly liability periods did not coincide in the "
            "data provided, so no coarse comparison could be characterised. This "
            "is an expected consequence of the monthly↔quarterly cadence "
            "mismatch, not a reconstruction failure."
        )
    pct = within_band_pct if within_band_pct is not None else 0.0
    return (
        f"Pool-level (coarse) characterisation across {periods_compared} "
        f"overlapping period(s): {pct:.0f}% of compared liability lines fall "
        f"within a generous {band_pct:.0f}% relative band of the Notes & Cash "
        "actuals. This is a CHARACTERISATION, not a cent-level proof — a tight "
        "tie is not expected: the liabilities are seeded from the prospectus and "
        "amortised by a partly-indicative engine over collections net-reconciled "
        "to pool movement (no loan tape), and the revenue side is approximated "
        "from the reported coupon. The exact engine-vs-published-PoP "
        "reconciliation is V4's (#210) job against the same Notes & Cash data; "
        "this pipeline shows the prospectus-seeded reconstruction tracks the "
        "reported liability trajectory at pool granularity. See `caveats`."
    )


# ===========================================================================
# The pipeline core (pure, offline)
# ===========================================================================


def run_pool_pipeline(
    ledger: CollateralLedger,
    notes_cash: NotesCashReport,
    capital_structure: dict[str, float],
    *,
    reserve_target: float,
    original_pool_balance: float,
    seed_reporting_date: str,
    opening_pool_balance: float | None = None,
    coarse_band_pct: float = DEFAULT_COARSE_BAND_PCT,
    days_in_period: int = 30,
) -> PoolPipelineReport:
    """Run the pool-level full pipeline and characterise it against the actuals.

    The coarse end-to-end check for the seasoned deals: reconstruct collateral at
    pool level from the collateral ledger, seed the liability side from the
    prospectus capital structure, run the waterfall via the real
    ``reconstruct_period_series`` engine, and compare the reconstructed closing
    states to the Notes & Cash liability actuals on the overlapping reporting
    dates.

    This does NOT re-implement the engine — it composes
    ``period_state_machine.reconstruct_period_series``. It does NOT gate a
    cent-level PASS — it characterises (per-line deltas + a coarse band + an
    honest narrative + caveats), because pool-level aggregation, the
    monthly↔quarterly join, and the partly-indicative engine make a tight tie
    impossible by construction (see module docstring / :data:`STANDING_CAVEATS`).

    Parameters
    ----------
    ledger:
        The deal's collateral ground-truth ledger (monthly Portfolio &
        Performance reports), keyed by reporting date.
    notes_cash:
        The deal's Notes & Cash (liability) report set, keyed by reporting date.
    capital_structure:
        Prospectus capital structure — ``class_{a,b,c}_balance`` (+ optional
        ``class_x_rate_pct``). Build it from the seed deal model via
        :func:`capital_structure_from_deal_model`.
    reserve_target:
        The reserve account target (the reserve opens funded at this amount).
    original_pool_balance:
        Pool balance at deal closing — the factor / loss-rate denominator.
    seed_reporting_date:
        ISO reporting date for the prospectus-seeded period-0 opening state.
    opening_pool_balance:
        Pool balance at the start of period 0 (defaults to the first ledger
        period's opening balance when available, else ``original_pool_balance``).
    coarse_band_pct:
        The relative characterisation band (default 5%) — NOT a correctness gate.
    days_in_period:
        Day count for the Act/360 interest approximation in each period.

    Returns
    -------
    PoolPipelineReport
        The coarse pool-level characterisation across the overlapping periods.
    """
    period_inputs = build_period_inputs(ledger, days_in_period=days_in_period)

    if opening_pool_balance is None and ledger.periods:
        opening_pool_balance = ledger.periods[0].pool_balance_begin

    series: DealStateSeries = reconstruct_period_series(
        capital_structure=capital_structure,
        reserve_target=reserve_target,
        original_pool_balance=original_pool_balance,
        opening_pool_balance=opening_pool_balance,
        seed_reporting_date=seed_reporting_date,
        periods=period_inputs,
    )

    # Index reconstructed states by reporting date. Prefer a state that actually
    # recorded collections (the closing state for that period) over the seed.
    states_by_date: dict[str, DealState] = {}
    for state in series.states:
        existing = states_by_date.get(state.reporting_date)
        if existing is None or (
            existing.collections is None and state.collections is not None
        ):
            states_by_date[state.reporting_date] = state

    notes_cash_by_date = notes_cash.by_date

    matched_dates = sorted(set(states_by_date) & set(notes_cash_by_date))
    unmatched_notes_cash = sorted(set(notes_cash_by_date) - set(states_by_date))
    unmatched_reconstructed = sorted(set(states_by_date) - set(notes_cash_by_date))

    periods = [
        _compare_period(states_by_date[d], notes_cash_by_date[d], coarse_band_pct)
        for d in matched_dates
    ]

    periods_compared = len(periods)
    lines_within_band = sum(p.lines_within_band for p in periods)
    lines_total = sum(p.lines_total for p in periods)
    within_band_pct = (
        lines_within_band / lines_total * 100.0 if lines_total > 0 else None
    )

    match_quality = _build_match_quality(periods_compared, within_band_pct, coarse_band_pct)
    summary = _build_summary(
        notes_cash.deal_name,
        periods_compared,
        lines_within_band,
        lines_total,
        coarse_band_pct,
        unmatched_notes_cash,
    )

    return PoolPipelineReport(
        deal_name=notes_cash.deal_name,
        coarse_band_pct=coarse_band_pct,
        periods=periods,
        unmatched_notes_cash_dates=unmatched_notes_cash,
        unmatched_reconstructed_dates=unmatched_reconstructed,
        periods_compared=periods_compared,
        lines_within_band=lines_within_band,
        lines_total=lines_total,
        within_band_pct=within_band_pct,
        match_quality=match_quality,
        summary=summary,
    )


def _build_summary(
    deal_name: str,
    periods_compared: int,
    lines_within_band: int,
    lines_total: int,
    band_pct: float,
    unmatched_notes_cash: list[str],
) -> str:
    """One-line human-readable characterisation summary (never a PASS/FAIL gate)."""
    if periods_compared == 0:
        base = (
            f"COARSE (pool-level): no overlapping periods between {deal_name}'s "
            "collateral reconstruction and its Notes & Cash actuals to characterise"
        )
    else:
        base = (
            f"COARSE (pool-level): {lines_within_band}/{lines_total} liability "
            f"lines across {periods_compared} period(s) track {deal_name}'s Notes "
            f"& Cash actuals within {band_pct:.0f}%"
        )
    if unmatched_notes_cash:
        base += (
            f"; {len(unmatched_notes_cash)} reported quarter(s) had no "
            f"reconstructed state: {', '.join(unmatched_notes_cash)}"
        )
    return base + "."


# ===========================================================================
# Green-Lion convenience builder (live legs — integration-gated)
# ===========================================================================


def run_pool_pipeline_green_lion(
    deal_context: dict[str, Any],
    deal_model: dict[str, Any],
    *,
    reserve_target: float | None = None,
    original_pool_balance: float | None = None,
    seed_reporting_date: str | None = None,
    coarse_band_pct: float = DEFAULT_COARSE_BAND_PCT,
) -> PoolPipelineReport:  # pragma: no cover - obtains ledger/report via cache/extraction
    """Run the pool pipeline for a seasoned Green-Lion deal end to end.

    Convenience wrapper that *obtains* the collateral ledger
    (:func:`extraction.collateral_ledger.extract_collateral_ledger`) and the Notes
    & Cash report (:func:`notes_cash_parser.parse_notes_cash_report`) from the
    durable caches / extraction seams, reads the prospectus capital structure from
    the extracted ``deal_model`` (:func:`capital_structure_from_deal_model`), and
    runs the pure :func:`run_pool_pipeline` core. Because the ledger/report legs
    can touch the extraction cache / network, this function is integration-gated;
    the pure core it delegates to is unit-tested directly.

    Parameters
    ----------
    deal_context:
        A deal-context dict (a ``DEAL_REGISTRY`` entry) with ``deal_name``,
        ``investor_report_urls`` and ``notes_cash_report_urls``.
    deal_model:
        The deal's extracted prospectus model dict (carrying ``tranche_structure``).
    reserve_target / original_pool_balance / seed_reporting_date:
        Optional overrides. ``original_pool_balance`` defaults to the first
        collateral period's opening balance; ``reserve_target`` to 0 (no reserve
        ground truth in the prospectus model); ``seed_reporting_date`` to the day
        before the first collateral period.
    coarse_band_pct:
        The characterisation band passed through to the core.
    """
    from loanwhiz.extraction.collateral_ledger import extract_collateral_ledger
    from loanwhiz.primitives.notes_cash_parser import parse_notes_cash_report

    ledger = extract_collateral_ledger(deal_context)
    notes_cash = parse_notes_cash_report(deal_context)
    capital_structure = capital_structure_from_deal_model(deal_model)

    if original_pool_balance is None:
        original_pool_balance = (
            ledger.periods[0].pool_balance_begin if ledger.periods else 1.0
        )
    if seed_reporting_date is None:
        seed_reporting_date = (
            ledger.periods[0].period_start or ledger.periods[0].reporting_date
            if ledger.periods
            else "1970-01-01"
        )

    return run_pool_pipeline(
        ledger,
        notes_cash,
        capital_structure,
        reserve_target=reserve_target or 0.0,
        original_pool_balance=original_pool_balance,
        seed_reporting_date=seed_reporting_date,
        coarse_band_pct=coarse_band_pct,
    )
