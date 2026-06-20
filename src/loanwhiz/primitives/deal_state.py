"""Canonical per-period ``DealState`` and the opening→closing transition.

This module defines the **single canonical structural state** of a
securitisation deal at one point in time, plus the typed contract for
advancing it one period. It is the spine seam the model-builder epic (#179)
is built on: every downstream engine and endpoint reads ``DealState`` instead
of faking structural figures from scattered constants.

Why this module exists (vs the deleted ``waterfall_state.WaterfallState``)
-------------------------------------------------------------------------
The former ``WaterfallState`` was a thin carry-forward of three PDL/reserve
scalars used by the (now-deleted) ``MultiPeriodWaterfallRunner`` duplicate engine
(removed in #276). ``DealState`` is the *complete* per-period canonical state the
spine needs:

- per-tranche outstanding balances (Class A / B / C),
- per-class Principal Deficiency Ledger (PDL) balances,
- the reserve account balance **and** its target,
- cumulative realized losses,
- the pool balance and pool factor,
- the period's collections breakdown (interest / scheduled principal /
  prepayment / recovery / realized loss),
- a revolving / amortizing flag,
- the reporting date / period index.

The good raw bookkeeping from ``WaterfallState`` — PDL-capped replenishment,
reserve floor, loss accumulation — is **folded in** here (not duplicated) as
``DealState`` methods, generalised to the three-tranche Green Lion structure.

The transition contract (the S6 seam)
--------------------------------------
A single period advances opening → closing through three explicit, immutable
steps, each returning a *new* ``DealState`` (nothing mutates in place):

    opening
      .apply_collections(period_collections)   # record this period's cashflows
      .apply_losses(realized_loss, ...)         # allocate losses to PDLs
      .apply_waterfall_result(waterfall_result) # repay tranches, replenish, reserve
    == closing

``transition(...)`` composes the three into one call. The closing state of
period N is, by construction, a valid opening state for period N+1
(``closing[N] == opening[N+1]``) — that is the invariant S6 (the multi-period
loop) drives. S4 produces the ``WaterfallResult`` this module consumes; S5
(triggers) reads the same state. **This module does not implement the
waterfall or the trigger logic** — it defines the schema, the seed, and the
mechanical bookkeeping the contract owns.

Deal-agnostic by construction
-----------------------------
Nothing here branches on a specific deal. Green Lion's figures enter via
``seed_from_prospectus`` arguments (sourced from the deal model / prospectus
capital structure), never as hardcoded constants in this module.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

# Allocation order for principal losses across the PDL ledgers. Losses are
# borne junior-first: Class C absorbs first, then Class B, then Class A. This
# is the standard sequential loss-allocation order for a senior/mezz/junior
# RMBS capital structure; it is data about the *structure*, not a per-deal
# branch (every deal modelled here has A/B/C in seniority order).
_DEFAULT_LOSS_ALLOCATION: tuple[str, ...] = ("class_c", "class_b", "class_a")

_PDL_FIELD = {
    "class_a": "class_a_pdl",
    "class_b": "class_b_pdl",
    "class_c": "class_c_pdl",
}
_BALANCE_FIELD = {
    "class_a": "class_a_balance",
    "class_b": "class_b_balance",
    "class_c": "class_c_balance",
}


def _clamp_non_negative(value: float) -> float:
    """Return ``value`` floored at 0.0 (small negatives from fp noise → 0)."""
    return value if value > 0.0 else 0.0


# ---------------------------------------------------------------------------
# PeriodCollections — the period's cashflow breakdown (input to the transition)
# ---------------------------------------------------------------------------


class PeriodCollections(BaseModel):
    """The cash a deal collected from its pool over one collection period.

    This is the asset-side input to a period transition. It is produced
    upstream from the loan tape (S2/S3 — ``collections_aggregator`` and
    friends); ``DealState.apply_collections`` records it onto the opening
    state. All amounts are in the deal currency (EUR for Green Lion 2026-1)
    and must be non-negative.

    Attributes
    ----------
    interest:
        Interest receipts from the pool this period (available revenue funds,
        before swap receipts).
    scheduled_principal:
        Contractual / scheduled principal repayments.
    prepayment:
        Unscheduled (voluntary) principal — prepayments beyond schedule.
    recovery:
        Recoveries on previously defaulted / written-down loans.
    realized_loss:
        Principal written off as a realized loss this period. Recorded here
        for completeness of the collections picture; the *allocation* of this
        loss to the PDL ledgers happens in ``DealState.apply_losses`` (which
        may be driven from this field or an explicit argument).
    """

    interest: float = Field(default=0.0, ge=0.0, description="Interest receipts (EUR).")
    scheduled_principal: float = Field(
        default=0.0, ge=0.0, description="Scheduled principal repayments (EUR)."
    )
    prepayment: float = Field(
        default=0.0, ge=0.0, description="Unscheduled principal / prepayments (EUR)."
    )
    recovery: float = Field(
        default=0.0, ge=0.0, description="Recoveries on defaulted loans (EUR)."
    )
    realized_loss: float = Field(
        default=0.0, ge=0.0, description="Principal realized as loss this period (EUR)."
    )

    @property
    def total_principal(self) -> float:
        """Scheduled principal + prepayment — total principal collected."""
        return self.scheduled_principal + self.prepayment


# ---------------------------------------------------------------------------
# WaterfallResult — the typed contract S4 produces (input to the transition)
# ---------------------------------------------------------------------------


class WaterfallResult(BaseModel):
    """The distribution outcome of one period's priority-of-payments.

    This is the **contract** between the waterfall engine (S4) and the state
    transition. S4 computes how the period's available funds are applied;
    ``DealState.apply_waterfall_result`` folds that outcome back into the
    canonical state (reduce tranche balances by principal repaid, replenish
    PDLs, top up / draw the reserve). **This module does not compute it** — it
    only defines its shape and how it lands on the state.

    All amounts are in the deal currency and non-negative. The fields mirror
    the existing ``waterfall_state`` vocabulary (per-class PDL replenishment,
    reserve payment/withdrawal) so S4 can be wired in without a vocabulary
    migration.

    Attributes
    ----------
    class_a_principal / class_b_principal / class_c_principal:
        Principal repaid to each tranche this period (reduces that tranche's
        outstanding balance, floored at 0).
    class_a_pdl_replenishment / class_b_pdl_replenishment /
    class_c_pdl_replenishment:
        Amounts the revenue waterfall applied to cure each class's PDL debit.
        Capped on apply at the outstanding PDL balance — you cannot replenish
        more than is owed.
    reserve_payment:
        Amount distributed *into* the reserve account (revenue waterfall
        top-up step). Capped on apply at the reserve target.
    reserve_draw:
        Amount drawn *from* the reserve to cover a senior shortfall (floored at
        0 on apply — you cannot draw the reserve negative).
    """

    class_a_principal: float = Field(default=0.0, ge=0.0)
    class_b_principal: float = Field(default=0.0, ge=0.0)
    class_c_principal: float = Field(default=0.0, ge=0.0)
    class_a_pdl_replenishment: float = Field(default=0.0, ge=0.0)
    class_b_pdl_replenishment: float = Field(default=0.0, ge=0.0)
    class_c_pdl_replenishment: float = Field(default=0.0, ge=0.0)
    reserve_payment: float = Field(default=0.0, ge=0.0)
    reserve_draw: float = Field(default=0.0, ge=0.0)


# ---------------------------------------------------------------------------
# DealState — the canonical per-period structural state
# ---------------------------------------------------------------------------


class DealState(BaseModel):
    """The complete structural state of a deal at one reporting period.

    Immutable (``frozen=True``): every transition method returns a *new*
    ``DealState`` via ``model_copy``, mirroring the immutable pattern of
    ``WaterfallState``. This makes the period chain a value-stream — S6 holds a
    list of states and never mutates one in place.

    All monetary fields are in the deal currency (EUR for Green Lion 2026-1).

    Liability side (seeded from the PROSPECTUS — see spike S0)
    ---------------------------------------------------------
    ``class_{a,b,c}_balance``  outstanding tranche balances.
    ``class_{a,b,c}_pdl``      per-class PDL debit balances (0 = no deficiency).
    ``reserve_balance``        current reserve account cash.
    ``reserve_target``         the reserve account target (the cap on top-ups).
    ``cumulative_losses``      running total of realized principal losses.

    Asset side (reconciles to the tapes/reports — see spike S0)
    -----------------------------------------------------------
    ``pool_balance``           current outstanding pool balance.
    ``pool_factor``            ``pool_balance / original_pool_balance`` (1.0 at
                               par). Refreshed whenever the pool moves.
    ``original_pool_balance``  pool balance at deal closing — the denominator
                               for both the pool factor and the loss rate.

    Period metadata
    ---------------
    ``reporting_date``         ISO date string of the period end (e.g.
                               ``"2026-02-28"``).
    ``period_index``           0-based ordinal of this period in the deal life
                               (period-0 is the seeded opening state).
    ``revolving``              ``True`` while the deal is revolving (new
                               receivables purchasable); ``False`` once
                               amortizing.
    ``collections``            the ``PeriodCollections`` recorded for this
                               period (``None`` on a freshly-seeded opening
                               state before ``apply_collections`` runs).
    """

    model_config = {"frozen": True}

    # --- period metadata ---
    reporting_date: str = Field(..., description="ISO period-end date (e.g. 2026-02-28).")
    period_index: int = Field(default=0, ge=0, description="0-based period ordinal.")

    # --- liability side (prospectus-seeded) ---
    class_a_balance: float = Field(..., ge=0.0, description="Class A outstanding (EUR).")
    class_b_balance: float = Field(..., ge=0.0, description="Class B outstanding (EUR).")
    class_c_balance: float = Field(..., ge=0.0, description="Class C outstanding (EUR).")
    class_a_pdl: float = Field(default=0.0, ge=0.0, description="Class A PDL debit (EUR).")
    class_b_pdl: float = Field(default=0.0, ge=0.0, description="Class B PDL debit (EUR).")
    class_c_pdl: float = Field(default=0.0, ge=0.0, description="Class C PDL debit (EUR).")
    reserve_balance: float = Field(default=0.0, ge=0.0, description="Reserve cash (EUR).")
    reserve_target: float = Field(default=0.0, ge=0.0, description="Reserve target (EUR).")
    cumulative_losses: float = Field(
        default=0.0, ge=0.0, description="Cumulative realized principal losses (EUR)."
    )

    # --- asset side (tape/report-reconciled) ---
    pool_balance: float = Field(..., ge=0.0, description="Current pool balance (EUR).")
    pool_factor: float = Field(
        default=1.0, ge=0.0, description="pool_balance / original_pool_balance."
    )
    original_pool_balance: float = Field(
        ..., gt=0.0, description="Pool balance at closing — factor/loss denominator (EUR)."
    )

    # --- flags / cashflows ---
    revolving: bool = Field(default=False, description="True while revolving; False if amortizing.")
    collections: PeriodCollections | None = Field(
        default=None, description="The cashflows recorded for this period."
    )

    @field_validator("reporting_date")
    @classmethod
    def _non_empty_date(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("reporting_date must be a non-empty ISO date string")
        return v

    # ------------------------------------------------------------------
    # Derived quantities
    # ------------------------------------------------------------------

    @property
    def total_pdl(self) -> float:
        """Sum of the three per-class PDL debit balances."""
        return self.class_a_pdl + self.class_b_pdl + self.class_c_pdl

    @property
    def total_liabilities(self) -> float:
        """Sum of the three outstanding tranche balances."""
        return self.class_a_balance + self.class_b_balance + self.class_c_balance

    @property
    def cumulative_loss_rate_pct(self) -> float:
        """``cumulative_losses / original_pool_balance * 100``."""
        if self.original_pool_balance <= 0.0:
            return 0.0
        return self.cumulative_losses / self.original_pool_balance * 100.0

    def _recompute_factor(self, pool_balance: float) -> float:
        """Pool factor for a given pool balance against the closing balance."""
        if self.original_pool_balance <= 0.0:
            return 0.0
        return pool_balance / self.original_pool_balance

    # ------------------------------------------------------------------
    # Construction / seed
    # ------------------------------------------------------------------

    @classmethod
    def seed_from_prospectus(
        cls,
        capital_structure: dict[str, float],
        *,
        reserve_target: float,
        original_pool_balance: float,
        opening_pool_balance: float | None = None,
        reporting_date: str,
        revolving: bool = False,
        period_index: int = 0,
    ) -> "DealState":
        """Build the period-0 opening ``DealState`` from prospectus figures.

        Per spike S0's decision, the liability side seeds from the
        **prospectus** capital structure (NOT the investor reports, which carry
        no liability figures): tranche balances are the prospectus closing
        balances, PDLs are all 0, and the reserve account opens fully funded at
        its target.

        Parameters
        ----------
        capital_structure:
            Mapping with at least ``class_a_balance``, ``class_b_balance``,
            ``class_c_balance`` (the prospectus closing balances). Extra keys
            (e.g. ``class_a_rate_pct``) are ignored. Sourced from the deal
            model / registry — never hardcoded here — so the seed is
            deal-agnostic.
        reserve_target:
            The reserve account target. The reserve opens funded at this amount
            (``reserve_balance == reserve_target``).
        original_pool_balance:
            Pool balance at deal closing — the factor/loss-rate denominator.
        opening_pool_balance:
            The pool balance at the start of period 0. Defaults to
            ``original_pool_balance`` (pool factor 1.0 at par).
        reporting_date:
            ISO date string of the period-0 reporting date.
        revolving:
            Whether the deal opens in its revolving period.
        period_index:
            Period ordinal for the seeded state (default 0).

        Returns
        -------
        DealState
            A period-0 opening state: tranche balances from the prospectus,
            PDLs 0, reserve at target, cumulative losses 0, pool factor derived
            from ``opening_pool_balance / original_pool_balance``.

        Raises
        ------
        KeyError
            If ``capital_structure`` is missing a required tranche balance.
        """
        opening_pool = (
            opening_pool_balance
            if opening_pool_balance is not None
            else original_pool_balance
        )
        factor = (
            opening_pool / original_pool_balance if original_pool_balance > 0.0 else 0.0
        )
        return cls(
            reporting_date=reporting_date,
            period_index=period_index,
            class_a_balance=capital_structure["class_a_balance"],
            class_b_balance=capital_structure["class_b_balance"],
            class_c_balance=capital_structure["class_c_balance"],
            class_a_pdl=0.0,
            class_b_pdl=0.0,
            class_c_pdl=0.0,
            reserve_balance=reserve_target,
            reserve_target=reserve_target,
            cumulative_losses=0.0,
            pool_balance=opening_pool,
            pool_factor=factor,
            original_pool_balance=original_pool_balance,
            revolving=revolving,
            collections=None,
        )

    # ------------------------------------------------------------------
    # Transition contract — each step returns a NEW DealState
    # ------------------------------------------------------------------

    def apply_collections(self, collections: PeriodCollections) -> "DealState":
        """Record this period's collections onto the (opening) state.

        Sets ``self.collections`` and advances the pool: principal collected
        (scheduled + prepayment) reduces ``pool_balance`` and refreshes
        ``pool_factor``. The pool is floored at 0 (it cannot go negative).

        This is the asset-side leg of the transition. Interest / recovery do
        not change the pool balance (they are revenue, not principal
        amortization). Realized losses are *also* a principal reduction, but
        they are applied through ``apply_losses`` so the PDL allocation and the
        cumulative-loss accounting stay in one place; pass the same loss to
        ``apply_losses`` (or use ``transition``) to fold it in.

        Returns
        -------
        DealState
            New state with ``collections`` set and the pool advanced.
        """
        new_pool = _clamp_non_negative(self.pool_balance - collections.total_principal)
        return self.model_copy(
            update={
                "collections": collections,
                "pool_balance": new_pool,
                "pool_factor": self._recompute_factor(new_pool),
            }
        )

    def apply_losses(
        self,
        realized_loss: float,
        *,
        allocation: tuple[str, ...] = _DEFAULT_LOSS_ALLOCATION,
    ) -> "DealState":
        """Allocate a realized principal loss to the PDL ledgers.

        Folds in (and generalises to three tranches) ``WaterfallState``'s
        ``record_loss`` logic: the loss increments ``cumulative_losses`` and is
        debited to the PDL ledgers in seniority order — junior tranches absorb
        first (``allocation`` default ``class_c → class_b → class_a``). Each
        class's PDL debit is capped at that class's outstanding *balance* (you
        cannot record a deficiency larger than the principal at risk); any
        residual cascades to the next class. A residual remaining after the
        most-senior class is dropped (the structure has absorbed its full
        capacity) — ``cumulative_losses`` still records the full loss.

        Parameters
        ----------
        realized_loss:
            EUR principal lost this period. Negatives are treated as 0.0.
        allocation:
            Tranche keys in loss-absorption order (junior→senior).

        Returns
        -------
        DealState
            New state with updated per-class PDLs and ``cumulative_losses``.
        """
        loss = _clamp_non_negative(realized_loss)
        if loss == 0.0:
            return self

        updates: dict[str, float] = {}
        remaining = loss
        for cls_key in allocation:
            if remaining <= 0.0:
                break
            pdl_field = _PDL_FIELD[cls_key]
            bal_field = _BALANCE_FIELD[cls_key]
            current_pdl = getattr(self, pdl_field)
            balance = getattr(self, bal_field)
            headroom = _clamp_non_negative(balance - current_pdl)
            debit = min(remaining, headroom)
            if debit > 0.0:
                updates[pdl_field] = current_pdl + debit
                remaining -= debit

        updates["cumulative_losses"] = self.cumulative_losses + loss
        return self.model_copy(update=updates)

    def apply_waterfall_result(self, result: WaterfallResult) -> "DealState":
        """Apply a period's waterfall distribution to the (closing) state.

        Folds in ``WaterfallState``'s ``replenish_pdl`` (capped at the
        outstanding PDL) and ``update_reserve`` (floored at 0, capped at
        target) logic, generalised to three tranches, plus tranche principal
        redemption:

        - **Principal redemption** reduces each tranche's outstanding balance by
          the principal repaid to it (floored at 0).
        - **PDL replenishment** reduces each class's PDL debit by the
          replenishment amount, capped at the outstanding debit (you cannot
          cure more than is owed).
        - **Reserve** is increased by ``reserve_payment`` (capped so the balance
          does not exceed ``reserve_target``) and decreased by ``reserve_draw``
          (floored at 0).

        This is the liability-side leg of the transition. S4 computes the
        ``WaterfallResult``; this method is the only place it lands on state.

        Returns
        -------
        DealState
            New (closing) state with tranche balances, PDLs and reserve updated.
        """
        updates: dict[str, float] = {}

        # Tranche principal redemption (floored at 0).
        updates["class_a_balance"] = _clamp_non_negative(
            self.class_a_balance - result.class_a_principal
        )
        updates["class_b_balance"] = _clamp_non_negative(
            self.class_b_balance - result.class_b_principal
        )
        updates["class_c_balance"] = _clamp_non_negative(
            self.class_c_balance - result.class_c_principal
        )

        # PDL replenishment (capped at outstanding debit).
        updates["class_a_pdl"] = _clamp_non_negative(
            self.class_a_pdl - min(result.class_a_pdl_replenishment, self.class_a_pdl)
        )
        updates["class_b_pdl"] = _clamp_non_negative(
            self.class_b_pdl - min(result.class_b_pdl_replenishment, self.class_b_pdl)
        )
        updates["class_c_pdl"] = _clamp_non_negative(
            self.class_c_pdl - min(result.class_c_pdl_replenishment, self.class_c_pdl)
        )

        # Reserve: top up (the *payment* is capped so it doesn't carry the
        # balance above target — but an already-over-target balance is never
        # clawed back), then draw (floored at 0).
        payment = _clamp_non_negative(result.reserve_payment)
        if self.reserve_target > 0.0:
            headroom = _clamp_non_negative(self.reserve_target - self.reserve_balance)
            payment = min(payment, headroom)
        topped = self.reserve_balance + payment
        drawn = _clamp_non_negative(topped - _clamp_non_negative(result.reserve_draw))
        updates["reserve_balance"] = drawn

        return self.model_copy(update=updates)

    def transition(
        self,
        *,
        collections: PeriodCollections,
        waterfall_result: WaterfallResult,
        realized_loss: float | None = None,
        allocation: tuple[str, ...] = _DEFAULT_LOSS_ALLOCATION,
        next_reporting_date: str | None = None,
        next_revolving: bool | None = None,
    ) -> "DealState":
        """Advance this (opening) state one period to its closing state.

        The single-step transition contract S6 drives: it composes
        ``apply_collections`` → ``apply_losses`` → ``apply_waterfall_result``
        and returns the **closing** ``DealState``. By construction the result is
        itself a valid opening ``DealState`` — ``closing[N] == opening[N+1]`` —
        so S6 can feed it straight into the next period.

        Parameters
        ----------
        collections:
            The period's cashflows (asset side).
        waterfall_result:
            The period's distribution outcome (liability side; produced by S4).
        realized_loss:
            The principal loss to allocate. Defaults to
            ``collections.realized_loss`` so a single source of truth drives
            both the cashflow record and the PDL allocation; pass an explicit
            value to override.
        allocation:
            Loss-absorption order (junior→senior).
        next_reporting_date:
            If given, stamps the closing state with the next period's reporting
            date and increments ``period_index`` — so the returned state is
            ready to use as the next period's opening. If ``None``, the closing
            state keeps this period's metadata (the caller stamps it).
        next_revolving:
            If given, sets the closing state's ``revolving`` flag (e.g. the
            revolving period ended this period). If ``None``, the flag carries
            forward unchanged.

        Returns
        -------
        DealState
            The closing state for this period == opening state for the next.
        """
        loss = (
            realized_loss
            if realized_loss is not None
            else collections.realized_loss
        )
        closing = (
            self.apply_collections(collections)
            .apply_losses(loss, allocation=allocation)
            .apply_waterfall_result(waterfall_result)
        )

        rollover: dict[str, object] = {}
        if next_reporting_date is not None:
            rollover["reporting_date"] = next_reporting_date
            rollover["period_index"] = self.period_index + 1
        if next_revolving is not None:
            rollover["revolving"] = next_revolving
        if rollover:
            closing = closing.model_copy(update=rollover)
        return closing
