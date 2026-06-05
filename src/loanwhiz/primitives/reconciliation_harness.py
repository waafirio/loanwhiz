"""Reconciliation harness — the S7 proof of correctness (#187).

This is the spine's (#179) *proof*: it reconciles the reconstructed deal model
(S6's per-period ``DealState`` series, ``period_state_machine``) against the
published investor-report actuals (S2's ``CollateralLedger``,
``extraction.collateral_ledger``) and emits a PASS/FAIL discrepancy report — to
the cent.

The split proof (spike S0, #180)
--------------------------------
The Green Lion 2026-1 investor reports are ESMA **Portfolio & Performance**
(collateral-side) reports. They carry the pool-balance roll-forward (begin/end,
repayments, prepayments, further advances, other) — which reconciles to the cent
across every reported 2026 period — but **no liability-side data at all**: no
tranche balances, no PDL, no reserve account, no priority-of-payments
distributions (``has_liability_section=False`` on every period).

So this harness reconciles **only the collateral side** against the reports:

- ``pool_balance`` (reconstructed end-of-period) ↔ ``pool_balance_end`` (report),
- ``principal_collected`` (reconstructed ``collections.total_principal``) ↔ the
  report's ``repayments + prepayments``,
- and asserts the report's *own* pool roll-forward is internally self-consistent
  (``roll_forward_residual`` ≈ 0).

It deliberately does **not** reconcile tranche / PDL / reserve against the
reports — that ground truth does not exist. Those are asserted by *invariants*
instead, and the general invariant suite is owned by **S8 (#188)**. This harness
surfaces the liability side as a clearly-labelled note (see
``ReconciliationReport.liability_note``) rather than fabricating a comparison,
and explicitly carries S6's ``reserve_draw=0`` caveat (the engine does not
auto-draw the reserve on a revenue shortfall) so it is visible, not papered over.

Pure & deterministic — no LLM, no network. The Green-Lion convenience builder
(:func:`reconcile_green_lion`) does touch the durable extraction cache / Gemini
to *obtain* the ledger, so that single function is integration-gated; the
reconciliation core (:func:`reconcile_collateral`) is fully offline and
unit-tested.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from loanwhiz.extraction.collateral_ledger import CollateralLedger, CollateralPeriod
from loanwhiz.primitives.deal_state import DealState
from loanwhiz.primitives.period_state_machine import DealStateSeries

# Default reconciliation tolerance. The proof is "to the cent", so the gate is an
# ABSOLUTE EUR tolerance (one cent) — not a percentage. A percentage gate would
# let a multi-thousand-EUR discrepancy on a billion-EUR pool slip through.
DEFAULT_TOLERANCE_EUR = 0.01

# The standing liability-side disclosure (spike S0): the reports carry no
# liability ground truth, so the liability side is proven by invariants (S8 #188),
# not by report reconciliation.
LIABILITY_NOTE = (
    "Liability side (tranche balances, PDL, reserve, distributions) has NO "
    "external ground truth: the ESMA Portfolio & Performance investor reports "
    "carry no liability-side figures (spike S0 / #180). It is therefore NOT "
    "reconciled against the reports here — it is validated by invariants "
    "(conservation, non-negativity, closing[N]==opening[N+1], prospectus seed), "
    "owned by S8 (#188). CAVEAT: S6 reconstructs reserve_draw=0 — the engine does "
    "not auto-draw the reserve on a revenue shortfall; this is surfaced, not "
    "corrected, by S7."
)


# ---------------------------------------------------------------------------
# Output models
# ---------------------------------------------------------------------------


class CollateralLineCheck(BaseModel):
    """One reconciled collateral line item: reconstructed vs reported.

    The gate is :attr:`abs_delta` against an absolute EUR tolerance (the
    "to the cent" proof). :attr:`delta_pct` is carried for human display only —
    it is *not* the pass/fail gate.

    Attributes
    ----------
    line_item:
        Canonical name, e.g. ``"pool_balance_end"`` or ``"principal_collected"``.
    reconstructed_value:
        The value from the S6-reconstructed ``DealState``.
    reported_value:
        The value from the S2 collateral ledger (the report ground truth).
    delta:
        ``reconstructed_value - reported_value`` (signed, EUR).
    abs_delta:
        ``abs(delta)`` — the figure the EUR tolerance gates on.
    delta_pct:
        ``delta / reported_value * 100`` for display; ``0.0`` when both are 0,
        ``None`` when ``reported_value`` is 0 but ``reconstructed`` is not
        (unbounded — the EUR gate still catches it).
    tolerance_eur:
        Absolute EUR tolerance used for the match decision.
    match:
        ``True`` iff ``abs_delta <= tolerance_eur``.
    """

    line_item: str
    reconstructed_value: float
    reported_value: float
    delta: float
    abs_delta: float
    delta_pct: float | None
    tolerance_eur: float
    match: bool


class PeriodReconciliation(BaseModel):
    """Collateral reconciliation for one reporting period.

    Attributes
    ----------
    reporting_date:
        ISO period-end date — the join key between the reconstructed state and
        the report period.
    period_label:
        Human-readable label from the ledger (e.g. ``"March 2026"``).
    line_checks:
        Per-line collateral comparisons (pool balance, principal collected).
    roll_forward_residual:
        The report's own pool-roll-forward self-consistency residual
        (``begin − repayments − prepayments + further_advances + other − end``).
        Should be ≈ 0 for a self-consistent report.
    roll_forward_consistent:
        ``True`` iff ``abs(roll_forward_residual) <= tolerance_eur``.
    period_pass:
        ``True`` iff every line check matches AND the report roll-forward is
        self-consistent.
    """

    reporting_date: str
    period_label: str
    line_checks: list[CollateralLineCheck]
    roll_forward_residual: float
    roll_forward_consistent: bool
    period_pass: bool


class ReconciliationReport(BaseModel):
    """The full PASS/FAIL collateral reconciliation across reported periods.

    Attributes
    ----------
    deal_name:
        The deal this report covers.
    tolerance_eur:
        The absolute EUR tolerance applied to every line check.
    periods:
        One :class:`PeriodReconciliation` per period present on *both* sides
        (reconstructed series ∩ ledger), keyed by reporting date.
    unmatched_report_dates:
        Ledger reporting dates with no reconstructed state (reported but not
        reconstructed) — surfaced, never silently dropped.
    unmatched_reconstructed_dates:
        Reconstructed reporting dates with no ledger period (reconstructed but
        not reported — e.g. the prospectus-seeded period-0 state, or periods
        beyond the published reports).
    periods_checked / periods_passed / periods_failed:
        Aggregate counts over :attr:`periods`.
    overall_pass:
        ``True`` iff every checked period passed AND there are no unmatched
        *report* dates (a reported period we failed to reconstruct is a proof
        gap). Reconstructed-only dates do NOT fail the proof — they are expected
        (period-0 seed, forward periods).
    liability_note:
        The standing liability-side disclosure (see :data:`LIABILITY_NOTE`) —
        the reports carry no liability ground truth, so it is invariant-validated
        (S8) rather than reconciled here; includes the ``reserve_draw=0`` caveat.
    summary:
        One-line human-readable PASS/FAIL summary.
    """

    deal_name: str
    tolerance_eur: float
    periods: list[PeriodReconciliation]
    unmatched_report_dates: list[str]
    unmatched_reconstructed_dates: list[str]
    periods_checked: int
    periods_passed: int
    periods_failed: int
    overall_pass: bool
    liability_note: str = LIABILITY_NOTE
    summary: str


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_line_check(
    line_item: str,
    reconstructed_value: float,
    reported_value: float,
    tolerance_eur: float,
) -> CollateralLineCheck:
    """Construct a :class:`CollateralLineCheck` with delta + match status."""
    delta = reconstructed_value - reported_value
    abs_delta = abs(delta)
    if reported_value != 0.0:
        delta_pct: float | None = delta / reported_value * 100.0
    elif reconstructed_value == 0.0:
        delta_pct = 0.0
    else:
        # reported is 0 but reconstructed is not — unbounded pct; the EUR gate
        # still catches it via abs_delta.
        delta_pct = None
    return CollateralLineCheck(
        line_item=line_item,
        reconstructed_value=reconstructed_value,
        reported_value=reported_value,
        delta=delta,
        abs_delta=abs_delta,
        delta_pct=delta_pct,
        tolerance_eur=tolerance_eur,
        match=abs_delta <= tolerance_eur,
    )


def _reconcile_period(
    state: DealState,
    period: CollateralPeriod,
    tolerance_eur: float,
) -> PeriodReconciliation:
    """Reconcile one reconstructed state against one report period."""
    line_checks: list[CollateralLineCheck] = [
        _build_line_check(
            "pool_balance_end",
            state.pool_balance,
            period.pool_balance_end,
            tolerance_eur,
        )
    ]

    # Principal collected: the reconstructed state's collections
    # (``total_principal`` = scheduled + prepayment, set by ``apply_collections``
    # during the transition that produced this closing state) vs the report's
    # **full pool reduction** (``pool_balance_begin − pool_balance_end``).
    #
    # The report figure is the FULL net reduction, NOT ``repayments +
    # prepayments`` alone: spike S0 (#180) proved the tape's month-on-month
    # balance delta (which is exactly what advances ``DealState.pool_balance``
    # via ``apply_collections``) ties to the report's full roll-forward to the
    # cent, while ``repayments + prepayments`` alone leaves a residual equal to
    # the report's ``other_balance_change`` line (construction-deposit / other
    # non-principal movements the report itemises separately). Comparing against
    # ``repayments + prepayments`` would therefore manufacture a false mismatch
    # of exactly ``other_balance_change`` every period. A state with no
    # collections recorded (e.g. the period-0 seed) contributes 0.0; such a state
    # normally has no matching ledger period anyway.
    reconstructed_principal = (
        state.collections.total_principal if state.collections is not None else 0.0
    )
    reported_pool_reduction = period.pool_balance_begin - period.pool_balance_end
    line_checks.append(
        _build_line_check(
            "principal_collected",
            reconstructed_principal,
            reported_pool_reduction,
            tolerance_eur,
        )
    )

    residual = period.roll_forward_residual
    roll_forward_consistent = abs(residual) <= tolerance_eur

    period_pass = roll_forward_consistent and all(c.match for c in line_checks)

    return PeriodReconciliation(
        reporting_date=period.reporting_date,
        period_label=period.period_label,
        line_checks=line_checks,
        roll_forward_residual=residual,
        roll_forward_consistent=roll_forward_consistent,
        period_pass=period_pass,
    )


def _build_summary(
    deal_name: str,
    periods_checked: int,
    periods_passed: int,
    unmatched_report_dates: list[str],
    tolerance_eur: float,
    overall_pass: bool,
) -> str:
    """Build the one-line PASS/FAIL summary."""
    verdict = "PASS" if overall_pass else "FAIL"
    tol = f"€{tolerance_eur:.2f}"
    base = (
        f"{verdict}: {periods_passed}/{periods_checked} collateral periods "
        f"reconcile to {deal_name}'s investor reports within {tol}"
    )
    if unmatched_report_dates:
        base += (
            f"; {len(unmatched_report_dates)} reported period(s) had no "
            f"reconstructed state: {', '.join(unmatched_report_dates)}"
        )
    return base + "."


# ---------------------------------------------------------------------------
# The reconciliation core (pure, offline)
# ---------------------------------------------------------------------------


def reconcile_collateral(
    series: DealStateSeries,
    ledger: CollateralLedger,
    *,
    tolerance_eur: float = DEFAULT_TOLERANCE_EUR,
) -> ReconciliationReport:
    """Reconcile a reconstructed ``DealState`` series against the report ledger.

    The proof of correctness for the collateral side: for every reporting period
    present on **both** the reconstructed series and the report ledger (joined by
    ISO ``reporting_date``), assert that the reconstructed end-of-period pool
    balance and principal collected tie to the report's figures within
    ``tolerance_eur`` (default one cent), and that the report's own pool
    roll-forward is internally self-consistent.

    Periods present on only one side are surfaced (``unmatched_report_dates`` /
    ``unmatched_reconstructed_dates``), never silently dropped. A *reported*
    period with no reconstructed state fails the overall proof (it is a gap in
    what we can prove); a *reconstructed* period with no report (the period-0
    prospectus seed, or forward periods beyond the published reports) does not —
    those are expected.

    Parameters
    ----------
    series:
        S6's reconstructed series (``reconstruct_period_series`` output). Its
        ``states`` carry the per-period ``pool_balance``, ``reporting_date`` and
        ``collections`` this harness reads.
    ledger:
        S2's collateral ground-truth ledger (the report actuals), keyed by
        reporting date.
    tolerance_eur:
        Absolute EUR match tolerance (default one cent — the "to the cent" gate).

    Returns
    -------
    ReconciliationReport
        The PASS/FAIL discrepancy report.
    """
    # Index reconstructed states by reporting date. A series should not carry two
    # states for the same date, but if it does (e.g. seed date == first report
    # date), prefer the one that actually recorded collections — that is the
    # closing state for the period, the one the report describes.
    states_by_date: dict[str, DealState] = {}
    for state in series.states:
        existing = states_by_date.get(state.reporting_date)
        if existing is None or (
            existing.collections is None and state.collections is not None
        ):
            states_by_date[state.reporting_date] = state

    ledger_by_date = ledger.by_date

    matched_dates = sorted(set(states_by_date) & set(ledger_by_date))
    unmatched_report_dates = sorted(set(ledger_by_date) - set(states_by_date))
    unmatched_reconstructed_dates = sorted(set(states_by_date) - set(ledger_by_date))

    periods: list[PeriodReconciliation] = [
        _reconcile_period(states_by_date[d], ledger_by_date[d], tolerance_eur)
        for d in matched_dates
    ]

    periods_checked = len(periods)
    periods_passed = sum(1 for p in periods if p.period_pass)
    periods_failed = periods_checked - periods_passed

    # A reported period we couldn't reconstruct is a proof gap → overall FAIL.
    overall_pass = (
        periods_checked > 0
        and periods_failed == 0
        and not unmatched_report_dates
    )

    summary = _build_summary(
        deal_name=ledger.deal_name,
        periods_checked=periods_checked,
        periods_passed=periods_passed,
        unmatched_report_dates=unmatched_report_dates,
        tolerance_eur=tolerance_eur,
        overall_pass=overall_pass,
    )

    return ReconciliationReport(
        deal_name=ledger.deal_name,
        tolerance_eur=tolerance_eur,
        periods=periods,
        unmatched_report_dates=unmatched_report_dates,
        unmatched_reconstructed_dates=unmatched_reconstructed_dates,
        periods_checked=periods_checked,
        periods_passed=periods_passed,
        periods_failed=periods_failed,
        overall_pass=overall_pass,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Green-Lion convenience builder (live ledger leg — integration-gated)
# ---------------------------------------------------------------------------


def reconcile_green_lion(
    series: DealStateSeries,
    *,
    deal_context: dict[str, Any] | None = None,
    tolerance_eur: float = DEFAULT_TOLERANCE_EUR,
) -> ReconciliationReport:  # pragma: no cover - obtains ledger via cache/Gemini
    """Reconcile a reconstructed Green-Lion series against its report ledger.

    Convenience wrapper that *obtains* the collateral ledger via
    :func:`loanwhiz.extraction.collateral_ledger.extract_collateral_ledger`
    (durable cache → warm-start → Gemini) and then runs the pure
    :func:`reconcile_collateral` core. Because the ledger leg can touch the
    extraction cache / Gemini, this function is integration-gated; the pure core
    it delegates to is unit-tested directly.

    Parameters
    ----------
    series:
        The reconstructed ``DealState`` series to prove.
    deal_context:
        A deal-context dict (defaults to ``config.GREEN_LION``).
    tolerance_eur:
        Absolute EUR match tolerance.
    """
    from loanwhiz.config import GREEN_LION
    from loanwhiz.extraction.collateral_ledger import extract_collateral_ledger

    context = deal_context if deal_context is not None else GREEN_LION
    ledger = extract_collateral_ledger(context)
    return reconcile_collateral(series, ledger, tolerance_eur=tolerance_eur)
