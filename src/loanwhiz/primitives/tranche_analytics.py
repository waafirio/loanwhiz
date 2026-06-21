"""Per-tranche cashflow & WAL/duration explorer (analyst-facing, #321).

This primitive turns the engine's canonical :class:`DealStateSeries` — the
opening→closing period chain produced by ``period_state_machine`` — into
**per-tranche analytics** an analyst can read directly:

- an **amortization schedule** per tranche (Class A / B / C): opening balance,
  principal paid, and closing balance for every period;
- **WAL** (weighted average life), in years, per tranche — the principal-weighted
  average time to repayment (``Σ tᵢ·Pᵢ / ΣPᵢ``);
- the **principal window** — the first and last period (and reporting date) in
  which the tranche receives principal;
- the **pro-rata vs sequential switch state per period** — whether principal was
  distributed sequentially (senior-first) or pro-rata that period, read from the
  same trigger evaluation the engine used to drive the branch.

Design notes
------------
- **Reads only public model fields** on :class:`DealStateSeries` /
  :class:`DealState` / :class:`PeriodResult`. It adds no coupling and edits no
  shared module — registration is via the ``@register_primitive`` decorator, so
  this file is the *only* file the feature touches in ``primitives/`` (keeps it
  conflict-free against parallel siblings on the epic branch).
- **Deal-agnostic**: tranche balances and per-period principal come from the
  input series, never from hardcoded deal constants. The default tranche set
  (``class_a/b/c``) mirrors the engine's three-tranche structure but is an
  overridable input.
- **Amortization figures are balance deltas.** Per-period principal for a tranche
  is ``opening_balance − closing_balance`` across consecutive states (floored at
  0). This is the canonical amortization figure and is always present; the
  redemption-waterfall trace (``redemption_execution.distributed_to(...)``) is a
  corroborating source, surfaced separately as ``principal_distributed`` so an
  analyst can spot reserve-draw / rounding divergence without it changing the
  schedule.
- **Switch state never contradicts the engine.** ``sequential_pay_active`` is read
  from ``PeriodResult.trigger_evaluation`` for the matching transition. When the
  trigger is not evaluable for a period, we adopt the engine's own
  senior-protective default (sequential active) rather than guessing — and lower
  the result confidence to flag the fallback.
"""

from __future__ import annotations

import time
from datetime import date

from pydantic import BaseModel, Field

from loanwhiz.primitives.base import (
    AuditEntry,
    BaseInput,
    Citation,
    Primitive,
    PrimitiveResult,
)
from loanwhiz.primitives.period_state_machine import DealStateSeries, PeriodResult
from loanwhiz.primitives.registry import register_primitive

# The trigger whose breach flips principal from pro-rata to sequential. This is
# the engine's default (``period_state_machine._SEQUENTIAL_PAY_TRIGGER``); kept
# as a local constant so this primitive never imports a private name. Overridable
# per call via the input so a deal with a differently-named sequential-pay
# trigger can be analysed without code change.
_DEFAULT_SEQUENTIAL_PAY_TRIGGER = "cumulative_loss_trigger"

#: The engine's three-tranche structure, senior→junior.
_DEFAULT_TRANCHES: tuple[str, ...] = ("class_a", "class_b", "class_c")

#: Maps a tranche key to its ``DealState`` outstanding-balance field and the
#: redemption-waterfall recipient kind for its principal.
_BALANCE_FIELD = {
    "class_a": "class_a_balance",
    "class_b": "class_b_balance",
    "class_c": "class_c_balance",
}
_PRINCIPAL_RECIPIENT = {
    "class_a": "class_a_principal",
    "class_b": "class_b_principal",
    "class_c": "class_c_principal",
}

_DAYS_PER_YEAR = 365.25


# ---------------------------------------------------------------------------
# Input / output schemas
# ---------------------------------------------------------------------------


class TrancheAnalyticsInput(BaseInput):
    """Inputs to the per-tranche analytics primitive.

    Attributes
    ----------
    series:
        The reconstructed :class:`DealStateSeries` to analyse. ``states[0]`` is
        the opening state; each subsequent state is a period's closing state.
    tranches:
        Tranche keys to analyse, senior→junior. Defaults to the engine's
        ``class_a/b/c``. Any key must be one of the recognised tranche fields.
    sequential_pay_trigger:
        Name of the trigger whose breach drives sequential (vs pro-rata)
        principal allocation. Defaults to the engine's ``cumulative_loss_trigger``.
    """

    series: DealStateSeries
    tranches: list[str] = Field(default_factory=lambda: list(_DEFAULT_TRANCHES))
    sequential_pay_trigger: str = _DEFAULT_SEQUENTIAL_PAY_TRIGGER


class PeriodAmortRow(BaseModel):
    """One period's amortization row for one tranche."""

    period_index: int = Field(..., description="0-based closing-period ordinal.")
    reporting_date: str = Field(..., description="ISO period-end date of the closing state.")
    opening_balance: float = Field(..., ge=0.0, description="Tranche balance at period open (EUR).")
    principal_paid: float = Field(
        ..., ge=0.0, description="Principal amortized this period = opening − closing (EUR)."
    )
    closing_balance: float = Field(..., ge=0.0, description="Tranche balance at period close (EUR).")
    principal_distributed: float | None = Field(
        default=None,
        description=(
            "Principal the redemption waterfall trace attributes to this tranche "
            "this period (corroborating figure; None when no PeriodResult exists)."
        ),
    )
    sequential_pay_active: bool = Field(
        ..., description="True when sequential (senior-first) principal was in effect this period."
    )
    pro_rata_active: bool = Field(
        ..., description="Negation of sequential_pay_active — pro-rata principal in effect."
    )
    switch_state_evaluable: bool = Field(
        ...,
        description=(
            "False when the sequential-pay trigger could not be measured this "
            "period and the senior-protective default was assumed."
        ),
    )


class TrancheSchedule(BaseModel):
    """The full amortization + WAL/window analytics for one tranche."""

    tranche: str = Field(..., description="Tranche key (e.g. 'class_a').")
    rows: list[PeriodAmortRow] = Field(..., description="Per-period amortization rows.")
    wal_years: float | None = Field(
        default=None,
        description="Weighted average life in years (Σ tᵢ·Pᵢ/ΣPᵢ); None if no principal repaid.",
    )
    total_principal_repaid: float = Field(
        ..., ge=0.0, description="Sum of principal_paid across all periods (EUR)."
    )
    final_balance: float = Field(..., ge=0.0, description="Tranche closing balance at series end (EUR).")
    principal_window_start_period: int | None = Field(
        default=None, description="Period index of the first principal payment (None if none)."
    )
    principal_window_start_date: str | None = Field(
        default=None, description="Reporting date of the first principal payment."
    )
    principal_window_end_period: int | None = Field(
        default=None, description="Period index of the last principal payment (None if none)."
    )
    principal_window_end_date: str | None = Field(
        default=None, description="Reporting date of the last principal payment."
    )

    @property
    def fully_repaid(self) -> bool:
        """True when the tranche's closing balance has reached zero."""
        return self.final_balance <= 0.0


class TrancheAnalyticsOutput(BaseModel):
    """Per-tranche analytics over a deal-state series."""

    schedules: list[TrancheSchedule] = Field(..., description="One schedule per analysed tranche.")
    periods_analysed: int = Field(..., ge=0, description="Number of period transitions analysed.")
    series_start_date: str | None = Field(
        default=None, description="Reporting date of the opening (period-0) state."
    )
    series_end_date: str | None = Field(
        default=None, description="Reporting date of the final state."
    )
    summary: str = Field(..., description="One-line human-readable summary of the analytics.")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _parse_iso(value: str) -> date | None:
    """Parse an ISO date string, returning ``None`` if unparseable."""
    try:
        return date.fromisoformat(value.strip()[:10])
    except (ValueError, AttributeError):
        return None


def _years_from(period0_date: date | None, target_date: date | None, period_index: int) -> float:
    """Elapsed years from the series opening to a period.

    Uses actual/365.25 from real reporting dates when both are parseable;
    otherwise falls back to the period index as a year-spacing proxy (so WAL is
    still monotone and finite when dates are missing or malformed).
    """
    if period0_date is not None and target_date is not None:
        return max((target_date - period0_date).days, 0) / _DAYS_PER_YEAR
    return float(period_index)


def _wal_years(rows: list[PeriodAmortRow], period0_date: date | None) -> float | None:
    """Principal-weighted average life in years: ``Σ tᵢ·Pᵢ / ΣPᵢ``.

    ``tᵢ`` is the elapsed time from the series opening to the period in which
    principal ``Pᵢ`` was repaid. Returns ``None`` when the tranche repays no
    principal (WAL is undefined with zero weight).
    """
    total_principal = sum(r.principal_paid for r in rows)
    if total_principal <= 0.0:
        return None
    weighted = 0.0
    for r in rows:
        if r.principal_paid <= 0.0:
            continue
        t = _years_from(period0_date, _parse_iso(r.reporting_date), r.period_index)
        weighted += t * r.principal_paid
    return weighted / total_principal


def _clamp_non_negative(value: float) -> float:
    return value if value > 0.0 else 0.0


# ---------------------------------------------------------------------------
# Primitive
# ---------------------------------------------------------------------------


@register_primitive(
    name="tranche_analytics",
    version="0.1.0",
    description=(
        "Per-tranche cashflow & WAL/duration explorer: amortization schedule, "
        "WAL, principal window, and pro-rata/sequential switch state per period."
    ),
    tags=["analytics", "tranche", "cashflow", "wal", "duration"],
)
class TrancheAnalytics(Primitive[TrancheAnalyticsInput, TrancheAnalyticsOutput]):
    """Derive per-tranche analytics from a :class:`DealStateSeries`."""

    name = "tranche_analytics"
    version = "0.1.0"
    description = (
        "Per-tranche cashflow & WAL/duration explorer: amortization schedule, "
        "WAL, principal window, and pro-rata/sequential switch state per period."
    )

    def execute(
        self, input: TrancheAnalyticsInput
    ) -> PrimitiveResult[TrancheAnalyticsOutput]:
        t0 = time.perf_counter()
        input_hash = input.input_hash()

        series = input.series
        states = series.states
        results: list[PeriodResult] = series.period_results

        period0_date = _parse_iso(states[0].reporting_date) if states else None

        schedules: list[TrancheSchedule] = []
        any_fallback = False

        for tranche in input.tranches:
            bal_field = _BALANCE_FIELD.get(tranche)
            if bal_field is None:
                raise ValueError(
                    f"Unknown tranche {tranche!r}; expected one of {sorted(_BALANCE_FIELD)}"
                )
            recipient = _PRINCIPAL_RECIPIENT[tranche]

            rows: list[PeriodAmortRow] = []
            # Walk consecutive states pairwise: states[i] opens period i,
            # states[i+1] closes it. period_results[i] is that transition's trace.
            for i in range(len(states) - 1):
                opening = states[i]
                closing = states[i + 1]
                opening_bal = float(getattr(opening, bal_field))
                closing_bal = float(getattr(closing, bal_field))
                principal_paid = _clamp_non_negative(opening_bal - closing_bal)

                result = results[i] if i < len(results) else None
                principal_distributed: float | None = None
                seq_active = True  # senior-protective default (matches engine)
                evaluable = False
                if result is not None:
                    principal_distributed = result.redemption_execution.distributed_to(
                        recipient
                    )
                    te = result.trigger_evaluation
                    evaluable = te.evaluable(input.sequential_pay_trigger)
                    if evaluable:
                        seq_active = te.is_triggered(input.sequential_pay_trigger)
                    # else: keep senior-protective default
                if not evaluable:
                    any_fallback = True

                rows.append(
                    PeriodAmortRow(
                        period_index=closing.period_index,
                        reporting_date=closing.reporting_date,
                        opening_balance=opening_bal,
                        principal_paid=principal_paid,
                        closing_balance=closing_bal,
                        principal_distributed=principal_distributed,
                        sequential_pay_active=seq_active,
                        pro_rata_active=not seq_active,
                        switch_state_evaluable=evaluable,
                    )
                )

            paying = [r for r in rows if r.principal_paid > 0.0]
            window_start = paying[0] if paying else None
            window_end = paying[-1] if paying else None
            final_bal = (
                float(getattr(states[-1], bal_field)) if states else 0.0
            )

            schedules.append(
                TrancheSchedule(
                    tranche=tranche,
                    rows=rows,
                    wal_years=_wal_years(rows, period0_date),
                    total_principal_repaid=sum(r.principal_paid for r in rows),
                    final_balance=final_bal,
                    principal_window_start_period=(
                        window_start.period_index if window_start else None
                    ),
                    principal_window_start_date=(
                        window_start.reporting_date if window_start else None
                    ),
                    principal_window_end_period=(
                        window_end.period_index if window_end else None
                    ),
                    principal_window_end_date=(
                        window_end.reporting_date if window_end else None
                    ),
                )
            )

        periods_analysed = max(len(states) - 1, 0)
        series_start = states[0].reporting_date if states else None
        series_end = states[-1].reporting_date if states else None

        repaid_tranches = [s.tranche for s in schedules if s.fully_repaid]
        summary = (
            f"Analysed {periods_analysed} period(s) across {len(schedules)} tranche(s); "
            f"{len(repaid_tranches)} fully repaid"
            + (f" ({', '.join(repaid_tranches)})" if repaid_tranches else "")
            + "."
        )

        output = TrancheAnalyticsOutput(
            schedules=schedules,
            periods_analysed=periods_analysed,
            series_start_date=series_start,
            series_end_date=series_end,
            summary=summary,
        )

        # Full confidence when every period's switch state was directly
        # evaluable; reduced when any period fell back to the senior-protective
        # default (the schedule itself is still exact — only the switch-state
        # attribution was assumed).
        confidence = 0.8 if any_fallback else 1.0

        citation = Citation(
            document="DealStateSeries",
            page_or_row=(
                f"periods {series_start or '?'}…{series_end or '?'}"
                f" ({periods_analysed} transitions)"
            ),
            excerpt=(
                "Per-tranche amortization, WAL and switch state derived from the "
                "reconstructed deal-state series (balances + redemption traces + "
                "trigger evaluation)."
            ),
        )

        duration_ms = (time.perf_counter() - t0) * 1000.0
        audit = AuditEntry.now(
            primitive_name=self.name,
            version=self.version,
            input_hash=input_hash,
            duration_ms=duration_ms,
        )

        return PrimitiveResult[TrancheAnalyticsOutput](
            output=output,
            confidence=confidence,
            citations=[citation],
            audit_entry=audit,
        )
