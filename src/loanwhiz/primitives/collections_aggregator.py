"""Collections aggregator primitive.

Aggregates per-loan ESMA tape rows into the two pool-level inputs the
waterfall runner requires:

- **Available Revenue Funds (ARF):** interest collected + swap receipts.
  Interest is estimated as balance-weighted coupon × days_in_period / 360.
- **Available Principal Funds (APF):** scheduled principal + unscheduled
  principal + recoveries.
  Scheduled principal is the pool balance reduction since the prior period
  when ``prev_pool_balance`` is provided; otherwise estimated from the tape's
  ``scheduled_monthly_payment`` column.

The primitive also computes the Class A coupon due so the waterfall runner has
all the senior-payment inputs in one place.

Confidence
----------
- **0.8** — ``prev_pool_balance`` is provided; principal is computed from the
  actual balance delta (reliable).
- **0.6** — ``prev_pool_balance`` is absent; principal is estimated from the
  tape's ``scheduled_monthly_payment`` column (less certain).

Green Lion 2026-1 field mapping
--------------------------------
- ``current_balance``           — loan outstanding balance (EUR)
- ``current_interest_rate_pct`` — per-loan coupon (%)
- ``scheduled_monthly_payment`` — contractual monthly instalment (EUR)
- ``default_crr_flag``          — "Y" = defaulted loan
"""

from __future__ import annotations

import time

import pandas as pd
from pydantic import BaseModel, Field

from loanwhiz.primitives.base import (
    AuditEntry,
    BaseInput,
    Citation,
    Primitive,
    PrimitiveResult,
)
from loanwhiz.primitives.registry import register_primitive

# ---------------------------------------------------------------------------
# Confidence knobs
# ---------------------------------------------------------------------------

_CONFIDENCE_WITH_PREV = 0.8
_CONFIDENCE_WITHOUT_PREV = 0.6

# Day-count denominator (simple interest, Actual/360 approximation)
_DAY_COUNT_BASIS = 360.0


# ---------------------------------------------------------------------------
# I/O models
# ---------------------------------------------------------------------------


class CollectionsInput(BaseInput):
    """Input schema for the collections aggregator.

    Attributes:
        tape_file_url:       Direct URL (or local path) to the ESMA tape CSV.
        reporting_period:    Human-readable period label, e.g. "April 2026".
        prev_pool_balance:   Outstanding pool balance at the *prior* period
                             end (EUR). When supplied, scheduled principal is
                             computed as the actual balance delta; when absent,
                             it is estimated from the tape's
                             ``scheduled_monthly_payment`` column.
        class_a_rate_pct:    Current Class A coupon rate in percent
                             (EURIBOR + spread).  Defaults to 3.62 %.
        class_a_balance:     Class A note balance (EUR).
        class_b_balance:     Class B note balance (EUR).
        class_c_balance:     Class C note balance (EUR).
        senior_fees_estimate: Monthly estimate of servicer + admin + trustee
                             fees (EUR).
        days_in_period:      Actual or assumed number of days in the
                             collection period.  Used for interest accrual
                             (``interest = balance × rate / 100 × days / 360``).
    """

    tape_file_url: str = Field(..., description="ESMA tape CSV URL or local path.")
    reporting_period: str = Field(
        ..., description="Human-readable period label, e.g. 'April 2026'."
    )
    prev_pool_balance: float | None = Field(
        default=None,
        description=(
            "Pool balance at the prior period end (EUR).  When supplied, "
            "scheduled_principal = prev_pool_balance − current_pool_balance."
        ),
    )
    # Green Lion specific parameters
    class_a_rate_pct: float = Field(
        default=3.62, description="Class A coupon rate in percent."
    )
    class_a_balance: float = Field(
        default=1_000_000_000.0, description="Class A note outstanding balance (EUR)."
    )
    class_b_balance: float = Field(
        default=53_100_000.0, description="Class B note outstanding balance (EUR)."
    )
    class_c_balance: float = Field(
        default=10_500_000.0, description="Class C note outstanding balance (EUR)."
    )
    senior_fees_estimate: float = Field(
        default=50_000.0,
        description="Servicer + admin + trustee monthly estimate (EUR).",
    )
    days_in_period: int = Field(
        default=90, description="Days in the collection period (for interest accrual)."
    )


class CollectionsOutput(BaseModel):
    """Waterfall-ready collections summary derived from an ESMA tape.

    Attributes:
        reporting_period:        Period label from the input.
        interest_collected:      Estimated interest receipts from the pool
                                 (balance-weighted coupon × days/360).
        swap_receipts:           Swap agreement receipts (zero for plain deals).
        available_revenue_funds: ARF = interest_collected + swap_receipts.
        scheduled_principal:     Pool balance reduction from prior period
                                 (or estimated from scheduled_monthly_payment).
        unscheduled_principal:   Prepayments beyond scheduled (tape does not
                                 carry an explicit prepayment column — set to
                                 zero; captured in confidence penalty).
        recoveries:              Recoveries from defaulted loans (not in
                                 standard ESMA tape — set to zero).
        available_principal_funds: APF = scheduled + unscheduled + recoveries.
        pool_balance_eur:        Current pool balance (sum of current_balance).
        loan_count:              Number of loans in the tape.
        class_a_interest_due:    Class A coupon due this period
                                 (= class_a_balance × rate/100 × days/360).
        senior_fees:             Senior-fees estimate passed through from input.
        summary:                 Plain-English summary for operators.
    """

    reporting_period: str
    # Revenue funds
    interest_collected: float = Field(..., description="Estimated interest receipts (EUR).")
    swap_receipts: float = Field(
        default=0.0, description="Swap receipts (EUR); zero for plain deals."
    )
    available_revenue_funds: float = Field(
        ..., description="ARF = interest_collected + swap_receipts (EUR)."
    )
    # Principal funds
    scheduled_principal: float = Field(
        ..., description="Scheduled principal collected (EUR)."
    )
    unscheduled_principal: float = Field(
        default=0.0, description="Prepayments (EUR); zero when not derivable from tape."
    )
    recoveries: float = Field(
        default=0.0, description="Recoveries (EUR); zero when not in tape."
    )
    available_principal_funds: float = Field(
        ..., description="APF = scheduled + unscheduled + recoveries (EUR)."
    )
    # Pool-level
    pool_balance_eur: float = Field(..., description="Current pool balance (EUR).")
    loan_count: int = Field(..., description="Number of loans in the tape.")
    # Waterfall-ready
    class_a_interest_due: float = Field(
        ..., description="Class A coupon due this period (EUR)."
    )
    senior_fees: float = Field(..., description="Senior fees estimate (EUR).")
    summary: str = Field(..., description="Plain-English operator summary.")


# ---------------------------------------------------------------------------
# Primitive
# ---------------------------------------------------------------------------


@register_primitive(
    name="collections_aggregator",
    version="0.1.0",
    description="Aggregate ESMA loan tape into waterfall-ready collection amounts",
    tags=["collections", "aggregation", "waterfall", "computation"],
)
class CollectionsAggregator(Primitive[CollectionsInput, CollectionsOutput]):
    """Aggregate ESMA per-loan tape rows into waterfall-ready pool-level amounts.

    Computes Available Revenue Funds (ARF) and Available Principal Funds (APF)
    from the tape's balance and coupon columns, and derives the Class A coupon
    due analytically from the input parameters.
    """

    name = "collections_aggregator"
    version = "0.1.0"
    description = "Aggregate ESMA loan tape into waterfall-ready collection amounts"

    def execute(self, input: CollectionsInput) -> PrimitiveResult[CollectionsOutput]:  # type: ignore[override]
        """Aggregate the tape at ``input.tape_file_url`` into waterfall inputs.

        Parameters
        ----------
        input:
            Validated ``CollectionsInput``.

        Returns
        -------
        PrimitiveResult[CollectionsOutput]
            Typed output with confidence score, one citation, and audit entry.
        """
        t0 = time.perf_counter()
        input_hash = input.input_hash()

        # -----------------------------------------------------------------
        # 1. Load tape
        # -----------------------------------------------------------------
        df = pd.read_csv(input.tape_file_url, low_memory=False)

        # Normalise to lower-case column names for uniform field access.
        df.columns = [c.lower() for c in df.columns]

        loan_count = len(df)

        # -----------------------------------------------------------------
        # 2. Pool balance
        # -----------------------------------------------------------------
        balance_col = "current_balance"
        if balance_col in df.columns:
            balance_series = pd.to_numeric(df[balance_col], errors="coerce")
            pool_balance_eur = float(balance_series.sum(skipna=True))
        else:
            balance_series = pd.Series(dtype=float)
            pool_balance_eur = 0.0

        # -----------------------------------------------------------------
        # 3. Interest collected
        #    Estimate: balance-weighted coupon × days_in_period / 360
        # -----------------------------------------------------------------
        rate_col = "current_interest_rate_pct"
        if rate_col in df.columns and not balance_series.empty:
            rate_series = pd.to_numeric(df[rate_col], errors="coerce")
            sub = pd.DataFrame({"b": balance_series, "r": rate_series}).dropna()
            if not sub.empty and sub["b"].sum() > 0:
                wtd_coupon_pct = float(
                    (sub["b"] * sub["r"]).sum() / sub["b"].sum()
                )
            else:
                wtd_coupon_pct = 0.0
        else:
            wtd_coupon_pct = 0.0

        interest_collected = (
            pool_balance_eur * wtd_coupon_pct / 100.0 * input.days_in_period / _DAY_COUNT_BASIS
        )

        # -----------------------------------------------------------------
        # 4. Swap receipts — zero for plain (non-swapped) deals
        # -----------------------------------------------------------------
        swap_receipts = 0.0

        # -----------------------------------------------------------------
        # 5. Available Revenue Funds
        # -----------------------------------------------------------------
        available_revenue_funds = interest_collected + swap_receipts

        # -----------------------------------------------------------------
        # 6. Scheduled principal
        #    If prev_pool_balance is known: balance delta (reliable).
        #    Otherwise: sum of scaled scheduled_monthly_payment (estimated).
        # -----------------------------------------------------------------
        prev_balance_known = input.prev_pool_balance is not None

        if prev_balance_known:
            scheduled_principal = max(
                0.0, float(input.prev_pool_balance) - pool_balance_eur  # type: ignore[arg-type]
            )
        else:
            # Estimate from tape: scheduled_monthly_payment * (days / 30)
            # approximates the period's scheduled amortisation.
            payment_col = "scheduled_monthly_payment"
            if payment_col in df.columns:
                payment_series = pd.to_numeric(df[payment_col], errors="coerce")
                monthly_total = float(payment_series.sum(skipna=True))
                # Scale by fraction of month covered by the period.
                scheduled_principal = monthly_total * (input.days_in_period / 30.0)
            else:
                scheduled_principal = 0.0

        # -----------------------------------------------------------------
        # 7. Unscheduled principal (prepayments)
        #    The ESMA tape does not carry an explicit prepayment column.
        #    With prev_balance known, the total balance delta minus scheduled
        #    amortisation would give unscheduled — but scheduled IS already
        #    the full delta (we set it to prev - current), so there is no
        #    residual to split without a prepayment column.
        #    We set unscheduled_principal = 0; the confidence penalty already
        #    signals the estimation gap.
        # -----------------------------------------------------------------
        unscheduled_principal = 0.0

        # -----------------------------------------------------------------
        # 8. Recoveries — not present in standard ESMA tape
        # -----------------------------------------------------------------
        recoveries = 0.0

        # -----------------------------------------------------------------
        # 9. Available Principal Funds
        # -----------------------------------------------------------------
        available_principal_funds = (
            scheduled_principal + unscheduled_principal + recoveries
        )

        # -----------------------------------------------------------------
        # 10. Class A interest due (analytic, from input parameters)
        # -----------------------------------------------------------------
        class_a_interest_due = (
            input.class_a_balance
            * input.class_a_rate_pct
            / 100.0
            * input.days_in_period
            / _DAY_COUNT_BASIS
        )

        # -----------------------------------------------------------------
        # 11. Summary
        # -----------------------------------------------------------------
        def _m(v: float) -> str:
            return f"€{v / 1_000_000:.2f}m"

        summary = (
            f"{_m(available_revenue_funds)} revenue, "
            f"{_m(available_principal_funds)} principal collected "
            f"({input.reporting_period})"
        )

        # -----------------------------------------------------------------
        # 12. Confidence
        # -----------------------------------------------------------------
        confidence = _CONFIDENCE_WITH_PREV if prev_balance_known else _CONFIDENCE_WITHOUT_PREV

        # -----------------------------------------------------------------
        # 13. Citation
        # -----------------------------------------------------------------
        citation = Citation(
            document=input.tape_file_url,
            page_or_row=f"rows 1-{loan_count}",
            excerpt=(
                f"ESMA tape: {loan_count} loans, pool balance "
                f"{_m(pool_balance_eur)}, wtd coupon {wtd_coupon_pct:.4f}%"
            ),
        )

        # -----------------------------------------------------------------
        # 14. Audit entry
        # -----------------------------------------------------------------
        duration_ms = (time.perf_counter() - t0) * 1000.0
        audit = AuditEntry.now(
            primitive_name=self.name,
            version=self.version,
            input_hash=input_hash,
            duration_ms=duration_ms,
        )

        output = CollectionsOutput(
            reporting_period=input.reporting_period,
            interest_collected=interest_collected,
            swap_receipts=swap_receipts,
            available_revenue_funds=available_revenue_funds,
            scheduled_principal=scheduled_principal,
            unscheduled_principal=unscheduled_principal,
            recoveries=recoveries,
            available_principal_funds=available_principal_funds,
            pool_balance_eur=pool_balance_eur,
            loan_count=loan_count,
            class_a_interest_due=class_a_interest_due,
            senior_fees=input.senior_fees_estimate,
            summary=summary,
        )

        return PrimitiveResult[CollectionsOutput](
            output=output,
            confidence=confidence,
            citations=[citation],
            audit_entry=audit,
        )
