"""Collections aggregator primitive.

Derives the **per-period collections & loss breakdown** the deal-state
transition (S1's ``PeriodCollections``) and the waterfall runner consume,
properly separated into the five asset-side legs:

- **interest** — interest receipts, accrued on the **performing** balance only
  (defaulted / 180+d-arrears loans don't pay; see ``performing_mask``).
- **scheduled principal** — contractual amortisation (the principal portion of
  the scheduled instalment), derived per-loan against the prior period's tape.
- **prepayment** (unscheduled principal) — per-loan principal repaid *beyond*
  the scheduled amount, plus the full balance of performing loans that exited
  the pool (redeemed in full).
- **recovery** — cash collected on loans that were non-performing in the prior
  period (their balance reduction).
- **realized loss** — the un-recovered prior balance of defaulted loans that
  *left* the pool (written off).

Two derivation regimes
-----------------------
1. **Per-loan join (highest fidelity).** When ``prev_tape_file_url`` is given,
   the current tape is joined to the prior tape on ``loan_id`` and each loan's
   balance movement is decomposed into the legs above. This is the only regime
   that can separate scheduled principal, prepayment, recovery and loss.
2. **Pool-delta fallback (legacy).** When only the scalar ``prev_pool_balance``
   is given (or neither), scheduled principal is the pool balance delta (the
   historical behaviour) and prepayment / recovery / loss are 0 — the tape
   carries no way to split them without the prior per-loan balances.

The two pool-level aggregates the waterfall runner needs are still exposed:

- **Available Revenue Funds (ARF):** interest collected + swap receipts.
- **Available Principal Funds (APF):** scheduled + unscheduled principal +
  recoveries.

The primitive also computes the Class A coupon due so the waterfall runner has
all the senior-payment inputs in one place, and exposes
``CollectionsOutput.to_period_collections()`` — the typed hand-off into S1's
``DealState`` transition.

Confidence
----------
- **0.9** — ``prev_tape_file_url`` supplied; the legs are derived per-loan.
- **0.8** — only the scalar ``prev_pool_balance`` is supplied; principal is the
  pool balance delta (reliable total, but not separated).
- **0.6** — neither supplied; principal is estimated from the tape's
  ``scheduled_monthly_payment`` column (least certain).

Green Lion 2026-1 field mapping
--------------------------------
- ``loan_id``                   — stable per-loan identifier (the join key)
- ``current_balance``           — loan outstanding balance (EUR)
- ``current_interest_rate_pct`` — per-loan coupon (%)
- ``scheduled_monthly_payment`` — contractual monthly instalment (EUR)
- ``arrears_bucket``            — "Performing" | "<29d" | "180+d"
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
from loanwhiz.primitives.deal_state import PeriodCollections
from loanwhiz.primitives.esma_tape_normaliser import (
    non_performing_mask,
    performing_mask,
)
from loanwhiz.primitives.registry import register_primitive

# ---------------------------------------------------------------------------
# Confidence knobs
# ---------------------------------------------------------------------------

_CONFIDENCE_WITH_PREV_TAPE = 0.9
_CONFIDENCE_WITH_PREV = 0.8
_CONFIDENCE_WITHOUT_PREV = 0.6

# Day-count denominator (simple interest, Actual/360 approximation)
_DAY_COUNT_BASIS = 360.0


def _clamp_non_negative(value: float) -> float:
    """Return ``value`` floored at 0.0 (small negatives from fp noise → 0)."""
    return value if value > 0.0 else 0.0


# ---------------------------------------------------------------------------
# I/O models
# ---------------------------------------------------------------------------


class CollectionsInput(BaseInput):
    """Input schema for the collections aggregator.

    Attributes:
        tape_file_url:       Direct URL (or local path) to the ESMA tape CSV for
                             the current period.
        reporting_period:    Human-readable period label, e.g. "April 2026".
        prev_tape_file_url:  Direct URL (or local path) to the **prior period's**
                             ESMA tape. When supplied, the collections legs
                             (scheduled principal, prepayment, recovery, realized
                             loss) are derived **per-loan** by joining the two
                             tapes on ``loan_id`` — the highest-fidelity regime.
        prev_pool_balance:   Outstanding pool balance at the *prior* period
                             end (EUR). Used only when ``prev_tape_file_url`` is
                             absent: scheduled principal is then the pool balance
                             delta (the legacy behaviour); prepayment / recovery
                             / loss stay 0 (not separable from a scalar). When
                             both are given, ``prev_tape_file_url`` wins.
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
    prev_tape_file_url: str | None = Field(
        default=None,
        description=(
            "Prior period's ESMA tape URL/path. When supplied, the collections "
            "legs are derived per-loan by joining on loan_id (highest fidelity)."
        ),
    )
    prev_pool_balance: float | None = Field(
        default=None,
        description=(
            "Pool balance at the prior period end (EUR).  Used only when "
            "prev_tape_file_url is absent: scheduled_principal = "
            "prev_pool_balance − current_pool_balance (legacy pool-delta path)."
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

    The five separated legs (``interest_collected``, ``scheduled_principal``,
    ``unscheduled_principal``, ``recoveries``, ``realized_losses``) map directly
    onto S1's ``PeriodCollections`` — see ``to_period_collections``.

    Attributes:
        reporting_period:        Period label from the input.
        interest_collected:      Interest receipts from the **performing** pool
                                 (balance-weighted coupon × days/360 over loans
                                 that are not defaulted / 180+d in arrears).
        swap_receipts:           Swap agreement receipts (zero for plain deals).
        available_revenue_funds: ARF = interest_collected + swap_receipts.
        scheduled_principal:     Contractual amortisation. Per-loan scheduled
                                 amount when a prior tape is supplied; the pool
                                 balance delta in the legacy pool-delta regime.
        unscheduled_principal:   Prepayments — per-loan principal repaid beyond
                                 schedule plus the balance of performing loans
                                 that fully redeemed. Zero in the legacy regime.
        recoveries:              Recoveries on loans that were non-performing in
                                 the prior period. Zero in the legacy regime.
        realized_losses:         Un-recovered prior balance of defaulted loans
                                 that left the pool (written off). Zero in the
                                 legacy regime.
        available_principal_funds: APF = scheduled + unscheduled + recoveries.
        pool_balance_eur:        Current pool balance (sum of current_balance).
        loan_count:              Number of loans in the tape.
        derivation:              ``"per-loan"`` | ``"pool-delta"`` | ``"estimate"``
                                 — which regime produced the principal legs.
        class_a_interest_due:    Class A coupon due this period
                                 (= class_a_balance × rate/100 × days/360).
        senior_fees:             Senior-fees estimate passed through from input.
        summary:                 Plain-English summary for operators.
    """

    reporting_period: str
    # Revenue funds
    interest_collected: float = Field(..., description="Interest receipts, performing pool (EUR).")
    swap_receipts: float = Field(
        default=0.0, description="Swap receipts (EUR); zero for plain deals."
    )
    available_revenue_funds: float = Field(
        ..., description="ARF = interest_collected + swap_receipts (EUR)."
    )
    # Principal funds — separated legs
    scheduled_principal: float = Field(
        ..., description="Scheduled (contractual) principal collected (EUR)."
    )
    unscheduled_principal: float = Field(
        default=0.0, description="Prepayments / unscheduled principal (EUR)."
    )
    recoveries: float = Field(
        default=0.0, description="Recoveries on previously non-performing loans (EUR)."
    )
    realized_losses: float = Field(
        default=0.0, description="Realized principal losses written off this period (EUR)."
    )
    available_principal_funds: float = Field(
        ..., description="APF = scheduled + unscheduled + recoveries (EUR)."
    )
    # Pool-level
    pool_balance_eur: float = Field(..., description="Current pool balance (EUR).")
    loan_count: int = Field(..., description="Number of loans in the tape.")
    derivation: str = Field(
        default="estimate",
        description="Principal-leg regime: per-loan | pool-delta | estimate.",
    )
    # Waterfall-ready
    class_a_interest_due: float = Field(
        ..., description="Class A coupon due this period (EUR)."
    )
    senior_fees: float = Field(..., description="Senior fees estimate (EUR).")
    summary: str = Field(..., description="Plain-English operator summary.")

    def to_period_collections(self) -> PeriodCollections:
        """Adapt this output into S1's ``PeriodCollections`` (the DealState input).

        Maps the five separated legs onto the canonical period-collections
        shape the ``DealState.apply_collections`` / ``apply_losses`` transition
        consumes. All legs are floored at 0 to satisfy ``PeriodCollections``'s
        non-negativity constraints (fp noise from per-loan deltas can produce
        tiny negatives).

        Returns
        -------
        PeriodCollections
            ``interest`` ← interest_collected, ``scheduled_principal`` ←
            scheduled_principal, ``prepayment`` ← unscheduled_principal,
            ``recovery`` ← recoveries, ``realized_loss`` ← realized_losses.
        """
        return PeriodCollections(
            interest=_clamp_non_negative(self.interest_collected),
            scheduled_principal=_clamp_non_negative(self.scheduled_principal),
            prepayment=_clamp_non_negative(self.unscheduled_principal),
            recovery=_clamp_non_negative(self.recoveries),
            realized_loss=_clamp_non_negative(self.realized_losses),
        )


# ---------------------------------------------------------------------------
# Derivation helpers
# ---------------------------------------------------------------------------


def _load_tape(file_url: str) -> pd.DataFrame:
    """Load a tape CSV and lower-case its column names."""
    df = pd.read_csv(file_url, low_memory=False)
    df.columns = [c.lower() for c in df.columns]
    return df


def _scheduled_principal_portion(
    payment: pd.Series, balance: pd.Series, rate_pct: pd.Series, days: int
) -> pd.Series:
    """Per-loan scheduled *principal* portion of the contractual instalment.

    The scheduled instalment (``scheduled_monthly_payment``) covers both
    interest and principal. The principal portion is the instalment minus the
    period's interest accrual on the loan balance:

        principal_portion = scheduled_payment_for_period − interest_accrual

    where ``scheduled_payment_for_period = monthly_payment × days / 30`` and
    ``interest_accrual = balance × rate/100 × days / 360``. Floored at 0 (an
    instalment smaller than its interest — e.g. interest-only — contributes no
    scheduled principal). NaNs are treated as 0.
    """
    payment = pd.to_numeric(payment, errors="coerce").fillna(0.0)
    balance = pd.to_numeric(balance, errors="coerce").fillna(0.0)
    rate_pct = pd.to_numeric(rate_pct, errors="coerce").fillna(0.0)

    payment_for_period = payment * (days / 30.0)
    interest_accrual = balance * rate_pct / 100.0 * days / _DAY_COUNT_BASIS
    principal_portion = payment_for_period - interest_accrual
    return principal_portion.clip(lower=0.0)


def _derive_legs_per_loan(
    cur: pd.DataFrame, prev: pd.DataFrame, days_in_period: int
) -> dict[str, float]:
    """Decompose the period's principal movement into the four separated legs.

    Joins the current tape (*cur*) to the prior tape (*prev*) on ``loan_id`` and
    classifies each loan by its **categorical event** (exit, prior-default
    state), then allocates the *net* balance movement of the surviving
    performing pool between scheduled amortisation and prepayment.

    The decomposition is built to **reconcile to the actual pool movement**, not
    to sum noisy per-loan deltas. Synthetic tapes (and, to a lesser degree, real
    ones via re-indexation) carry large offsetting per-loan balance swings that
    net out at the pool level; summing gross per-loan reductions would massively
    over-state prepayment. So:

    - **Exited, was performing:** full prior balance is a **prepayment** (the
      loan redeemed in full) — a categorical event, robust to per-loan noise.
    - **Exited, was non-performing:** full prior balance is a **realized loss**
      (written off, un-recovered) — categorical, robust.
    - **Survived, was non-performing:** the loan's *net* balance reduction is a
      **recovery** (cash collected on a distressed loan).
    - **Survived, was performing:** the cohort's **net** balance reduction
      (Σ prior − Σ current over this cohort, floored at 0) is split into
      scheduled amortisation (the cohort's contractual principal portion, capped
      at the net reduction) and prepayment (the residual). Using the cohort net
      rather than per-loan gross is what makes the legs reconcile to the pool.

    Returns a dict with ``scheduled_principal``, ``unscheduled_principal``,
    ``recoveries`` and ``realized_losses`` (all EUR, non-negative).
    """
    prev_np = non_performing_mask(prev)
    prev = prev.assign(_prev_non_performing=prev_np.to_numpy())

    cur_small = cur[["loan_id", "current_balance"]].copy()
    cur_small["current_balance"] = pd.to_numeric(
        cur_small["current_balance"], errors="coerce"
    ).fillna(0.0)

    prev_cols = ["loan_id", "current_balance", "_prev_non_performing"]
    has_payment = "scheduled_monthly_payment" in prev.columns
    if has_payment:
        prev_cols.append("scheduled_monthly_payment")
    if "current_interest_rate_pct" in prev.columns:
        prev_cols.append("current_interest_rate_pct")
    prev_small = prev[prev_cols].copy()
    prev_small["current_balance"] = pd.to_numeric(
        prev_small["current_balance"], errors="coerce"
    ).fillna(0.0)

    # Left join from prev: prev-only rows (current_balance_cur is NaN) are exits;
    # both-present rows are survivors. (cur-only rows are new loans — they add no
    # principal *flow* this period, so they don't enter the principal legs.)
    merged = prev_small.merge(
        cur_small, on="loan_id", how="left", suffixes=("_prev", "_cur")
    )

    exited = merged["current_balance_cur"].isna()
    prev_bal = merged["current_balance_prev"]
    cur_bal = merged["current_balance_cur"].fillna(0.0)
    was_np = merged["_prev_non_performing"].astype(bool)

    survived = ~exited
    perf_surv = survived & ~was_np

    # --- Categorical events (robust to per-loan balance noise) ---------------
    prepay_from_exits = float(prev_bal[exited & ~was_np].sum())
    realized_losses = float(prev_bal[exited & was_np].sum())

    # Recovery: net balance reduction of surviving non-performing loans.
    np_surv = survived & was_np
    recoveries = _clamp_non_negative(
        float(prev_bal[np_surv].sum() - cur_bal[np_surv].sum())
    )

    # --- Performing survivors: net cohort reduction, split sched vs prepay ----
    net_reduction = _clamp_non_negative(
        float(prev_bal[perf_surv].sum() - cur_bal[perf_surv].sum())
    )

    if has_payment and net_reduction > 0.0:
        sched_portion = _scheduled_principal_portion(
            merged["scheduled_monthly_payment"],
            prev_bal,
            merged["current_interest_rate_pct"]
            if "current_interest_rate_pct" in merged.columns
            else pd.Series(0.0, index=merged.index),
            days_in_period,
        )
        cohort_scheduled = float(sched_portion[perf_surv].sum())
    else:
        # No payment column → can't separate; treat the whole net reduction as
        # scheduled (the conservative classification — no spurious prepayment).
        cohort_scheduled = net_reduction

    # Scheduled is capped at the cohort's net reduction; prepayment is the rest.
    scheduled_principal = min(cohort_scheduled, net_reduction)
    prepay_from_survivors = net_reduction - scheduled_principal

    return {
        "scheduled_principal": _clamp_non_negative(scheduled_principal),
        "unscheduled_principal": _clamp_non_negative(
            prepay_from_survivors + prepay_from_exits
        ),
        "recoveries": recoveries,
        "realized_losses": _clamp_non_negative(realized_losses),
    }


# ---------------------------------------------------------------------------
# Primitive
# ---------------------------------------------------------------------------


@register_primitive(
    name="collections_aggregator",
    version="0.2.0",
    description="Derive separated per-period collections & losses from the ESMA tape",
    tags=["collections", "aggregation", "waterfall", "computation"],
)
class CollectionsAggregator(Primitive[CollectionsInput, CollectionsOutput]):
    """Derive the separated per-period collections & loss legs from an ESMA tape.

    Computes interest (performing pool only), scheduled principal, prepayment,
    recovery and realized loss — per-loan against the prior tape when supplied —
    plus the pool-level Available Revenue / Principal Funds and the Class A
    coupon due. Produces S1's ``PeriodCollections`` via
    ``CollectionsOutput.to_period_collections``.
    """

    name = "collections_aggregator"
    version = "0.2.0"
    description = "Derive separated per-period collections & losses from the ESMA tape"

    def execute(self, input: CollectionsInput) -> PrimitiveResult[CollectionsOutput]:  # type: ignore[override]
        """Aggregate the tape at ``input.tape_file_url`` into separated legs.

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
        # 1. Load current tape
        # -----------------------------------------------------------------
        df = _load_tape(input.tape_file_url)
        loan_count = len(df)

        # -----------------------------------------------------------------
        # 2. Pool balance
        # -----------------------------------------------------------------
        if "current_balance" in df.columns:
            balance_series = pd.to_numeric(df["current_balance"], errors="coerce")
            pool_balance_eur = float(balance_series.sum(skipna=True))
        else:
            balance_series = pd.Series(dtype=float)
            pool_balance_eur = 0.0

        # -----------------------------------------------------------------
        # 3. Interest collected — accrued on the PERFORMING balance only.
        #    Defaulted / 180+d-arrears loans don't pay, so they are excluded
        #    from both the rate-weighting and the balance base.
        # -----------------------------------------------------------------
        interest_collected = self._performing_interest(df, balance_series, input)

        # -----------------------------------------------------------------
        # 4. Swap receipts — zero for plain (non-swapped) deals
        # -----------------------------------------------------------------
        swap_receipts = 0.0
        available_revenue_funds = interest_collected + swap_receipts

        # -----------------------------------------------------------------
        # 5. Principal legs — three regimes (most → least precise)
        # -----------------------------------------------------------------
        scheduled_principal = 0.0
        unscheduled_principal = 0.0
        recoveries = 0.0
        realized_losses = 0.0

        if input.prev_tape_file_url is not None and "loan_id" in df.columns:
            # Regime 1: per-loan join — separates every leg.
            prev_df = _load_tape(input.prev_tape_file_url)
            if "loan_id" in prev_df.columns:
                legs = _derive_legs_per_loan(df, prev_df, input.days_in_period)
                scheduled_principal = legs["scheduled_principal"]
                unscheduled_principal = legs["unscheduled_principal"]
                recoveries = legs["recoveries"]
                realized_losses = legs["realized_losses"]
                derivation = "per-loan"
                confidence = _CONFIDENCE_WITH_PREV_TAPE
            else:
                # Prior tape lacks loan_id — fall back to pool-delta if we can.
                scheduled_principal, derivation, confidence = self._pool_delta(
                    input, pool_balance_eur, df
                )
        elif input.prev_pool_balance is not None:
            # Regime 2: pool-delta — reliable total, not separated.
            scheduled_principal = _clamp_non_negative(
                float(input.prev_pool_balance) - pool_balance_eur
            )
            derivation = "pool-delta"
            confidence = _CONFIDENCE_WITH_PREV
        else:
            # Regime 3: estimate from the scheduled-payment column.
            scheduled_principal, derivation, confidence = self._estimate_principal(
                df, input
            )

        available_principal_funds = (
            scheduled_principal + unscheduled_principal + recoveries
        )

        # -----------------------------------------------------------------
        # 6. Class A interest due (analytic, from input parameters)
        # -----------------------------------------------------------------
        class_a_interest_due = (
            input.class_a_balance
            * input.class_a_rate_pct
            / 100.0
            * input.days_in_period
            / _DAY_COUNT_BASIS
        )

        # -----------------------------------------------------------------
        # 7. Summary
        # -----------------------------------------------------------------
        def _m(v: float) -> str:
            return f"€{v / 1_000_000:.2f}m"

        summary = (
            f"{_m(available_revenue_funds)} revenue, "
            f"{_m(available_principal_funds)} principal "
            f"(sched {_m(scheduled_principal)}, prepay {_m(unscheduled_principal)}, "
            f"recov {_m(recoveries)}, loss {_m(realized_losses)}) "
            f"[{derivation}] ({input.reporting_period})"
        )

        # -----------------------------------------------------------------
        # 8. Citation
        # -----------------------------------------------------------------
        citation = Citation(
            document=input.tape_file_url,
            page_or_row=f"rows 1-{loan_count}",
            excerpt=(
                f"ESMA tape: {loan_count} loans, pool balance "
                f"{_m(pool_balance_eur)}; legs derived via {derivation}"
            ),
        )

        # -----------------------------------------------------------------
        # 9. Audit entry
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
            realized_losses=realized_losses,
            available_principal_funds=available_principal_funds,
            pool_balance_eur=pool_balance_eur,
            loan_count=loan_count,
            derivation=derivation,
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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _performing_interest(
        df: pd.DataFrame, balance_series: pd.Series, input: CollectionsInput
    ) -> float:
        """Interest accrued on the **performing** balance only (arrears-aware).

        Excludes defaulted / 180+d-arrears loans from both the balance base and
        the balance-weighted coupon, so distressed loans contribute no interest.
        """
        if "current_balance" not in df.columns or balance_series.empty:
            return 0.0

        perf = performing_mask(df)
        perf_balance = balance_series.where(perf, other=0.0)
        perf_pool = float(perf_balance.sum(skipna=True))
        if perf_pool <= 0.0:
            return 0.0

        if "current_interest_rate_pct" in df.columns:
            rate_series = pd.to_numeric(df["current_interest_rate_pct"], errors="coerce")
            sub = pd.DataFrame({"b": perf_balance, "r": rate_series, "p": perf})
            sub = sub[sub["p"]].dropna(subset=["b", "r"])
            if not sub.empty and sub["b"].sum() > 0:
                wtd_coupon_pct = float((sub["b"] * sub["r"]).sum() / sub["b"].sum())
            else:
                wtd_coupon_pct = 0.0
        else:
            wtd_coupon_pct = 0.0

        return (
            perf_pool
            * wtd_coupon_pct
            / 100.0
            * input.days_in_period
            / _DAY_COUNT_BASIS
        )

    @staticmethod
    def _pool_delta(
        input: CollectionsInput, pool_balance_eur: float, df: pd.DataFrame
    ) -> tuple[float, str, float]:
        """Pool-delta scheduled-principal fallback (when per-loan join is N/A).

        Returns ``(scheduled_principal, derivation, confidence)``.
        """
        if input.prev_pool_balance is not None:
            scheduled = _clamp_non_negative(
                float(input.prev_pool_balance) - pool_balance_eur
            )
            return scheduled, "pool-delta", _CONFIDENCE_WITH_PREV
        return CollectionsAggregator._estimate_principal(df, input)

    @staticmethod
    def _estimate_principal(
        df: pd.DataFrame, input: CollectionsInput
    ) -> tuple[float, str, float]:
        """Estimate scheduled principal from the scheduled-payment column.

        The weakest regime: ``scheduled_monthly_payment`` summed and scaled to
        the period length. Returns ``(scheduled_principal, "estimate", conf)``.
        """
        if "scheduled_monthly_payment" in df.columns:
            payment_series = pd.to_numeric(
                df["scheduled_monthly_payment"], errors="coerce"
            )
            monthly_total = float(payment_series.sum(skipna=True))
            scheduled = monthly_total * (input.days_in_period / 30.0)
        else:
            scheduled = 0.0
        return scheduled, "estimate", _CONFIDENCE_WITHOUT_PREV
