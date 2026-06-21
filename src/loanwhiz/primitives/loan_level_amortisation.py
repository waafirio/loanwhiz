"""Loan-level amortisation schedule from an ESMA tape (#281).

The forward-projection engine (:class:`~loanwhiz.primitives.scenario_generator.ScenarioGenerator`)
historically amortised the pool with a **flat pool-level proxy** — a constant
1%/month fraction of the opening balance — because it had no loan-level
amortisation schedule to draw on. This module supplies that schedule: it turns
a loan tape's per-loan rows into a per-period **pool scheduled-principal
schedule** (a list of monthly scheduled-principal amounts), which the generator
then consumes in place of the flat proxy.

Why loan-level
--------------
A real pool amortises on a curve, not a flat line. Early periods are
interest-heavy and repay little principal; later periods are principal-heavy.
Each loan also has its own rate and remaining term. Flattening all of that to a
single 1%/month constant is exactly the proxy this module replaces. The tape
already carries the fields needed to do it properly per loan
(:mod:`loanwhiz.primitives.esma_tape_normaliser` documents the Green Lion field
mapping):

- ``current_balance``           — loan outstanding balance (EUR)
- ``current_interest_rate_pct`` — per-loan coupon (%)
- ``remaining_term_months``     — months to contractual maturity
- ``scheduled_monthly_payment`` — contractual monthly instalment (EUR, optional)

Method
------
For each **performing** loan (non-performing loans don't make scheduled
payments — their defaults are the scenario CDR leg's job, not scheduled
amortisation), the loan is amortised forward month by month:

1. ``interest = balance * monthly_rate`` (Act/360, 30-day month — the same
   day-count convention :class:`ScenarioGenerator` uses for pool interest).
2. ``instalment`` is the tape's ``scheduled_monthly_payment`` when present and
   positive (highest fidelity — the contractual instalment the tape states, the
   same column :mod:`loanwhiz.primitives.collections_aggregator` trusts),
   otherwise the standard **level-payment annuity** instalment computed from the
   balance, rate and remaining term: ``P * i / (1 - (1 + i)^-n)``.
3. ``scheduled_principal = min(instalment - interest, balance)`` (never repays
   more than the outstanding balance; floored at zero).
4. Decrement the balance and roll to the next period.

Scheduled principal is summed across loans per period to yield the pool
schedule. The result is deterministic and side-effect free — pure analytics
over the tape frame.

Degenerate cases are handled explicitly so a messy tape never poisons the
schedule with ``NaN``:

- Zero interest rate → straight-line ``balance / remaining_term`` instalment.
- Zero / blank / non-positive ``remaining_term_months`` → the loan repays its
  full balance in the first projected period (a balloon, not a divide-by-zero).
- Missing ``current_balance`` / non-numeric values → that loan contributes zero.
- Missing optional columns (rate, term, instalment) → handled per loan via the
  degenerate paths above; the function never requires a column beyond
  ``current_balance``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Day count per monthly period for the Act/360 interest accrual — mirrors
# ``ScenarioGenerator._DAYS_PER_MONTH`` so the loan-level schedule accrues
# interest on the same convention the pool projection uses.
_DAYS_PER_MONTH: int = 30
_DAY_COUNT_BASIS: float = 360.0


def _monthly_rate(annual_rate_pct: float) -> float:
    """Per-period interest factor for an annual coupon (%), Act/360, 30-day month.

    Matches the generator's pool-interest convention: ``rate_pct / 100 / 360 *
    30``. Returns ``0.0`` for a non-positive or non-finite rate.
    """
    if not np.isfinite(annual_rate_pct) or annual_rate_pct <= 0.0:
        return 0.0
    return (annual_rate_pct / 100.0) / _DAY_COUNT_BASIS * _DAYS_PER_MONTH


def _level_payment(balance: float, monthly_rate: float, term_months: int) -> float:
    """Standard level-payment (annuity) instalment for one loan.

    ``P * i / (1 - (1 + i)^-n)`` for a positive rate; the straight-line
    ``P / n`` when the rate is zero. ``term_months <= 0`` means "no schedule" —
    the caller treats that as a full-balance balloon, so this returns the whole
    balance as the instalment.
    """
    if term_months <= 0:
        return balance
    if monthly_rate <= 0.0:
        return balance / term_months
    factor = 1.0 - (1.0 + monthly_rate) ** (-term_months)
    if factor <= 0.0:  # numerically degenerate; fall back to straight-line
        return balance / term_months
    return balance * monthly_rate / factor


def _amortise_one_loan(
    balance: float,
    annual_rate_pct: float,
    term_months: int,
    stated_instalment: float | None,
    months: int,
) -> list[float]:
    """Per-period scheduled principal for a single loan over ``months`` periods.

    Returns a list of length ``months`` (zero-padded once the loan is fully
    repaid). The instalment is the tape's ``stated_instalment`` when present and
    positive, otherwise the computed level payment. Each period's scheduled
    principal is ``instalment - interest`` capped at the outstanding balance.
    """
    out = [0.0] * months
    if not np.isfinite(balance) or balance <= 0.0:
        return out

    monthly_rate = _monthly_rate(annual_rate_pct)
    term = term_months if (np.isfinite(term_months) and term_months > 0) else 0

    # No remaining term and no contractual instalment to follow: the loan has
    # no forward schedule, so treat it as a balloon — its full balance is
    # scheduled principal in the first projected period.
    has_stated = (
        stated_instalment is not None
        and np.isfinite(stated_instalment)
        and stated_instalment > 0.0
    )
    if term == 0 and not has_stated:
        out[0] = balance
        return out

    instalment = float(stated_instalment) if has_stated else _level_payment(
        balance, monthly_rate, term
    )

    remaining = balance
    for k in range(months):
        if remaining <= 0.0:
            break
        interest = remaining * monthly_rate
        principal = instalment - interest
        # A loan whose instalment doesn't even cover interest (negative
        # amortisation) repays no scheduled principal this period.
        if principal <= 0.0:
            principal = 0.0
        principal = min(principal, remaining)
        out[k] = principal
        remaining -= principal
    return out


def _col(df: pd.DataFrame, name: str) -> pd.Series | None:
    """Case-insensitive column lookup (mirrors the normaliser's ``col_map``)."""
    lower = {c.lower(): c for c in df.columns}
    orig = lower.get(name.lower())
    return df[orig] if orig is not None else None


def pool_scheduled_principal_schedule(df: pd.DataFrame, months: int) -> list[float]:
    """Per-period **pool** scheduled-principal schedule from a loan tape.

    Amortises each performing loan in ``df`` forward ``months`` periods (level
    payment, or the tape's stated instalment when present) and sums scheduled
    principal across loans per period. The returned list is the drop-in
    replacement for the flat ``pool_balance * scheduled_amort_rate`` proxy in
    :class:`~loanwhiz.primitives.scenario_generator.ScenarioGenerator`.

    Parameters
    ----------
    df:
        Loan tape as a DataFrame, one row per loan. Column names are matched
        case-insensitively. Only ``current_balance`` is required; ``rate`` /
        ``remaining_term_months`` / ``scheduled_monthly_payment`` are used when
        present and degrade gracefully when absent. Non-performing loans
        (defaulted or 180+ days in arrears, per the shared tape masks) are
        excluded — their principal is the scenario default leg's concern, not
        scheduled amortisation.
    months:
        Projection horizon in monthly periods (``>= 0``).

    Returns
    -------
    list[float]
        Exactly ``months`` pool scheduled-principal amounts (EUR), one per
        projected period. All zeros when ``months == 0``, the tape is empty, or
        no balance column is present.
    """
    if months < 0:
        raise ValueError("months must be non-negative")
    if months == 0 or df is None or len(df) == 0:
        return [0.0] * months

    # Exclude non-performing loans using the shared lower-cased tape masks, so
    # the loan-level schedule and the engine's interest base agree on who pays.
    # Imported lazily to avoid the primitives import-cycle at module load.
    from loanwhiz.primitives.esma_tape_normaliser import performing_mask

    df_lower = df.rename(columns={c: c.lower() for c in df.columns})
    mask = performing_mask(df_lower)
    perf = df[mask.to_numpy()]
    if len(perf) == 0:
        return [0.0] * months

    balance_col = _col(perf, "current_balance")
    if balance_col is None:
        return [0.0] * months

    n_loans = len(perf)

    def _numeric_array(name: str, default: float) -> np.ndarray:
        """Per-loan numeric array for *name*, or a constant *default* when absent."""
        col = _col(perf, name)
        if col is None:
            return np.full(n_loans, default, dtype=float)
        return pd.to_numeric(col, errors="coerce").to_numpy(dtype=float)

    balance = pd.to_numeric(balance_col, errors="coerce").to_numpy(dtype=float)
    rate = _numeric_array("current_interest_rate_pct", 0.0)
    term = _numeric_array("remaining_term_months", 0.0)
    inst_col = _col(perf, "scheduled_monthly_payment")
    instalment = (
        pd.to_numeric(inst_col, errors="coerce").to_numpy(dtype=float)
        if inst_col is not None
        else None
    )

    schedule = [0.0] * months
    for i in range(n_loans):
        bal = float(balance[i]) if np.isfinite(balance[i]) else 0.0
        r = float(rate[i]) if np.isfinite(rate[i]) else 0.0
        t_val = term[i]
        t = int(t_val) if np.isfinite(t_val) and t_val > 0 else 0
        stated = None
        if instalment is not None and np.isfinite(instalment[i]):
            stated = float(instalment[i])
        loan_sched = _amortise_one_loan(bal, r, t, stated, months)
        for k in range(months):
            schedule[k] += loan_sched[k]
    return schedule
