"""``ScenarioGenerator`` — synthetic forward ``PeriodInputs`` for the fold (#275).

Projection, history, and reconciliation are *the same fold* over different input
streams (see ``docs/superpowers/specs/2026-06-20-cold-start-edw-deal-engine-design.md``,
"one engine + adapters"). History feeds the fold from a tape/report adapter;
**projection feeds it from this generator**. The generator is a pure,
deterministic, pool-level adapter: given an opening pool balance + the deal's
structural rates + a :class:`ScenarioAssumptions` (CPR / CDR / recovery /
rate-shift), it yields an ordered list of canonical
:class:`~loanwhiz.domain.inputs.PeriodInputs` (``source="scenario"``) for an
N-month horizon. ``POST /deal/{id}/project`` then folds that stream through the
*same* ``period_state_machine.run_period`` the live history path uses — there is
no second engine.

The C5 fix
----------
The legacy ``CashflowProjector`` (the dead duplicate engine this generator
absorbs) decomposed the annual CDR to monthly **linearly** (``annual_cdr / 12``)
while decomposing the annual CPR to monthly SMM with the **survival/geometric**
``1 - (1 - CPR)^(1/12)`` — two different annual→monthly conventions applied to
the two pool decrements, internally inconsistent (issue #275, C5). Here a single
shared helper, :func:`_annual_to_monthly_survival`, decomposes **both** the CDR
and the CPR with the same survival convention. That one shared helper *is* the
C5 fix: defaults and prepayments now peel a consistent monthly fraction of the
surviving balance, and over 12 months each reproduces its own annual rate.

Scheduled amortisation: loan-level when available, proxy otherwise
------------------------------------------------------------------
Prepayments and defaults are scenario-driven (CPR / CDR) and peel off the
surviving *pool* balance — that is inherently a pool-level treatment and stays
so. **Scheduled** principal, however, is now loan-level when a tape is
available: :meth:`ScenarioGenerator.generate` accepts an optional
``scheduled_principal_schedule`` (a per-period pool scheduled-principal series
derived from the tape by
:func:`loanwhiz.primitives.loan_level_amortisation.pool_scheduled_principal_schedule`),
and uses it in place of the flat ``scheduled_amort_rate`` proxy (#281). When no
schedule is supplied — a deal with no loan tape (e.g. the report-driven
cold-start deals) — the generator falls back to the constant-rate proxy, so its
behaviour is byte-identical to before. The fold kernel, the ``PeriodInputs``
contract, and the C5 CDR↔SMM decomposition are untouched either way.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from loanwhiz.domain.inputs import CollectionLegs, PeriodInputs
from loanwhiz.primitives.deal_state import DealState

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Months a year decomposes into (the survival-decomposition root).
_MONTHS_PER_YEAR: int = 12

#: Scheduled amortisation proxy: fraction of the pool balance repaid on schedule
#: each month, used in the absence of a loan-level amortisation schedule. Matches
#: the legacy projector's 1%/month proxy so projection behaviour is comparable.
_DEFAULT_SCHEDULED_AMORT_RATE: float = 0.01

#: Day count per monthly period for the Act/360 interest accrual.
_DAYS_PER_MONTH: int = 30


# ---------------------------------------------------------------------------
# Scenario assumptions
# ---------------------------------------------------------------------------


class ScenarioAssumptions(BaseModel):
    """Pool-level assumptions for one forward projection scenario.

    All rates are **annual** and expressed in percent; the generator decomposes
    them to monthly with a single consistent survival convention (the C5 fix).

    Attributes:
        name:                    Scenario label, e.g. ``"base"`` / ``"stress"``.
        cpr_pct:                 Annual Conditional Prepayment Rate (%). Drives the
                                 monthly SMM applied to the surviving pool balance.
        cdr_pct:                 Annual Conditional Default Rate (%). Drives the
                                 monthly MDR applied to the surviving pool balance.
        recovery_pct:            Fraction of defaulted balance recovered (%). The
                                 complement is the realized loss.
        rate_shift_bps:          Additive shift to the pool/coupon rate in basis
                                 points (e.g. a EURIBOR stress). Affects projected
                                 pool interest only.
        scheduled_amort_rate:    Monthly scheduled-principal fraction of the pool
                                 (constant-rate amortisation proxy).
    """

    name: str = Field(..., description="Scenario label.")
    cpr_pct: float = Field(
        default=15.0, ge=0.0, le=100.0, description="Annual CPR (%)."
    )
    cdr_pct: float = Field(
        default=0.03, ge=0.0, le=100.0, description="Annual CDR (%)."
    )
    recovery_pct: float = Field(
        default=70.0, ge=0.0, le=100.0, description="Recovery on defaults (%)."
    )
    rate_shift_bps: float = Field(
        default=0.0, description="Additive rate shift (bps)."
    )
    scheduled_amort_rate: float = Field(
        default=_DEFAULT_SCHEDULED_AMORT_RATE,
        ge=0.0,
        le=1.0,
        description="Monthly scheduled-principal fraction of the pool.",
    )


# ---------------------------------------------------------------------------
# Decomposition — the single, consistent annual→monthly convention (C5 fix)
# ---------------------------------------------------------------------------


def _annual_to_monthly_survival(annual_rate: float, n: int = _MONTHS_PER_YEAR) -> float:
    """Decompose an annual rate to its per-period equivalent (survival convention).

    Returns the monthly rate ``m`` such that compounding ``1 - m`` over ``n``
    periods reproduces the annual survival ``1 - annual_rate``:

        ``m = 1 - (1 - annual_rate)^(1/n)``

    This is the standard SMM-from-CPR formula, applied **uniformly** to both the
    default rate (annual CDR → monthly MDR) and the prepayment rate (annual CPR →
    monthly SMM). Using the *same* helper for both is the C5 fix: the legacy
    projector decomposed CDR linearly (``/12``) and CPR geometrically, so the two
    pool decrements used inconsistent conventions.

    Parameters
    ----------
    annual_rate:
        Annual rate as a fraction in ``[0, 1]`` (e.g. ``0.15`` for 15% CPR).
    n:
        Periods per year the rate decomposes into (12 for monthly).

    Returns
    -------
    float
        The per-period rate, in ``[0, 1]``.
    """
    if annual_rate <= 0.0:
        return 0.0
    if annual_rate >= 1.0:
        return 1.0
    return 1.0 - (1.0 - annual_rate) ** (1.0 / n)


# ---------------------------------------------------------------------------
# The generator
# ---------------------------------------------------------------------------


class ScenarioGenerator:
    """Produce a synthetic forward ``PeriodInputs[]`` stream for the fold.

    Stateless and deterministic: :meth:`generate` is a pure function of its
    arguments. The emitted stream carries ``source="scenario"`` and empty
    ``step_overrides`` so the engine computes every waterfall line itself (a
    projection is a model output, not a report of post-resolution actuals).
    """

    def generate(
        self,
        seed: DealState,
        *,
        assumptions: ScenarioAssumptions,
        rate_pct: float,
        months: int,
        start_date: str | None = None,
        scheduled_principal_schedule: list[float] | None = None,
    ) -> list[PeriodInputs]:
        """Roll the pool forward ``months`` periods into synthetic ``PeriodInputs``.

        Each period peels, off the *surviving* pool balance and in order:
        scheduled principal, then prepayments (monthly SMM) and defaults (monthly
        MDR) — the latter two decomposed with the **same** survival convention
        (C5). Scheduled principal is the loan-level
        ``scheduled_principal_schedule[k]`` when supplied (capped at the opening
        pool balance), otherwise the flat ``scheduled_amort_rate`` proxy (#281).
        The realized loss is the defaulted balance net of recoveries; the
        recovered principal joins the available principal funds. Pool interest
        accrues on the period's opening pool balance at the rate-shifted coupon
        (Act/360, 30-day months).

        Parameters
        ----------
        seed:
            The period-0 opening :class:`DealState`. Only its ``pool_balance``
            and ``reporting_date`` are read here; the full state is threaded by
            ``run_period`` in the fold.
        assumptions:
            The scenario's CPR / CDR / recovery / rate-shift assumptions.
        rate_pct:
            The base annual pool/coupon rate (%), before the scenario's
            ``rate_shift_bps`` is applied.
        months:
            Projection horizon, in monthly periods (``>= 0``).
        start_date:
            ISO reporting date the first projected period closes on. Defaults to
            ``seed.reporting_date`` (the dates are advisory labels — the engine
            keys off the order, not the calendar).
        scheduled_principal_schedule:
            Optional per-period pool scheduled-principal series (EUR), derived
            from the deal's loan tape by
            :func:`loanwhiz.primitives.loan_level_amortisation.pool_scheduled_principal_schedule`.
            When supplied, period *k* uses ``schedule[k]`` (capped at that
            period's opening pool balance) as scheduled principal instead of the
            flat ``scheduled_amort_rate`` proxy (#281). A shorter list than
            ``months`` is zero-padded (the loans have fully amortised). When
            ``None`` (no tape), the constant-rate proxy is used and behaviour is
            unchanged.

        Returns
        -------
        list[PeriodInputs]
            Exactly ``months`` canonical ``PeriodInputs`` (``source="scenario"``),
            one per projected period, ready to fold through ``run_period``.
        """
        if months < 0:
            raise ValueError("months must be non-negative")

        monthly_smm = _annual_to_monthly_survival(assumptions.cpr_pct / 100.0)
        monthly_mdr = _annual_to_monthly_survival(assumptions.cdr_pct / 100.0)
        recovery_rate = assumptions.recovery_pct / 100.0
        effective_rate_pct = rate_pct + assumptions.rate_shift_bps / 100.0
        label_date = start_date if start_date is not None else seed.reporting_date

        pool_balance = seed.pool_balance
        periods: list[PeriodInputs] = []

        for period_idx in range(months):
            # Scheduled principal: the loan-level tape-derived schedule when
            # supplied (capped at the opening balance so it can't repay more
            # than is outstanding), else the constant-rate pool proxy (#281).
            if scheduled_principal_schedule is not None:
                scheduled_from_tape = (
                    scheduled_principal_schedule[period_idx]
                    if period_idx < len(scheduled_principal_schedule)
                    else 0.0
                )
                scheduled_principal = min(max(0.0, scheduled_from_tape), pool_balance)
            else:
                scheduled_principal = pool_balance * assumptions.scheduled_amort_rate
            balance_after_scheduled = max(0.0, pool_balance - scheduled_principal)

            # Prepayments and defaults peel off the SAME surviving balance with
            # the SAME survival decomposition (the C5 fix).
            prepayment = balance_after_scheduled * monthly_smm
            default_principal = balance_after_scheduled * monthly_mdr

            # Net loss vs. recovered principal split of the defaulted balance.
            realized_loss = default_principal * (1.0 - recovery_rate)
            recovered_principal = default_principal * recovery_rate

            # Pool interest on the opening balance (Act/360, 30-day month).
            pool_interest = (
                pool_balance * (effective_rate_pct / 100.0) / 360.0 * _DAYS_PER_MONTH
            )

            available_principal = scheduled_principal + prepayment + recovered_principal

            periods.append(
                PeriodInputs(
                    reporting_date=label_date,
                    days_in_period=_DAYS_PER_MONTH,
                    available_revenue=pool_interest,
                    available_principal=available_principal,
                    realized_loss=realized_loss,
                    legs=CollectionLegs(
                        interest=pool_interest,
                        scheduled_principal=scheduled_principal,
                        prepayment=prepayment,
                        recovery=recovered_principal,
                        realized_loss=realized_loss,
                    ),
                    source="scenario",
                )
            )

            # Advance the pool: scheduled + prepayment + the full defaulted
            # balance (recovered or lost, it leaves the performing pool).
            pool_balance = max(
                0.0,
                pool_balance
                - scheduled_principal
                - prepayment
                - default_principal,
            )

        return periods
