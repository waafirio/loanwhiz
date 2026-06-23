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

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

if TYPE_CHECKING:
    from loanwhiz.domain.state import TrancheState


def _tranche_state_cls() -> type["TrancheState"]:
    """Lazily resolve the canonical :class:`~loanwhiz.domain.state.TrancheState`.

    ``deal_state`` is imported very early in the ``primitives`` package cascade
    (``primitives.base`` is pulled in by ``domain.provenance``). Importing
    ``domain.state`` at *module load* would close a pre-existing import cycle
    (``domain`` → ``domain.inputs`` → ``domain.provenance`` →
    ``primitives.base`` → ``primitives`` → ``deal_state`` → ``domain.state`` →
    ``domain.provenance`` mid-init). Deferring the import to first use — by which
    point the cascade has settled — keeps ``TrancheState`` canonical (no
    duplicate type) while letting ``import loanwhiz.domain`` and
    ``import loanwhiz.primitives`` both succeed regardless of order.
    """
    from loanwhiz.domain.state import TrancheState as _TrancheState

    return _TrancheState

# Allocation order for principal losses across the PDL ledgers. Losses are
# borne junior-first: Class C absorbs first, then Class B, then Class A. This
# is the standard sequential loss-allocation order for a senior/mezz/junior
# RMBS capital structure; it is data about the *structure*, not a per-deal
# branch (the canonical A/B/C deals modelled here carry their tranches in
# seniority order). A deal with a different tranche set passes its own
# junior→senior ``allocation`` order to ``apply_losses``.
_DEFAULT_LOSS_ALLOCATION: tuple[str, ...] = ("class_c", "class_b", "class_a")

# Canonical A/B/C tranche names, in seniority (senior→junior) order. This is
# the order ``seed_from_prospectus`` lays the tranche list out in, and the order
# the backward-compatible ``class_{a,b,c}_*`` accessors map onto. It is *not* a
# hard constraint on the engine — the tranche list may carry any names in any
# order — it only fixes the layout of the canonical Green-Lion structure so the
# accessors and serialisation stay stable.
_CANONICAL_TRANCHE_NAMES: tuple[str, ...] = ("class_a", "class_b", "class_c")


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


class TranchePayment(BaseModel):
    """The per-tranche distribution outcome for one period, keyed by name.

    The generalised, deal-agnostic carrier of a tranche's two waterfall outcomes
    — principal repaid and PDL cured — replacing the hardcoded ``class_{a,b,c}_*``
    scalar pairs. ``WaterfallResult`` stores a list of these by tranche ``name``;
    the ``class_{a,b,c}_*`` accessors read the matching entry so existing callers
    are unbroken.

    Attributes
    ----------
    name:
        Tranche name, matching the :class:`~loanwhiz.domain.state.TrancheState`
        and the ``DealState`` tranche it lands on.
    principal:
        Principal repaid to this tranche this period (reduces its outstanding
        balance, floored at 0).
    pdl_replenishment:
        Amount the revenue waterfall applied to cure this tranche's PDL debit.
        Capped on apply at the outstanding PDL balance — you cannot replenish
        more than is owed.
    """

    name: str = Field(..., description="Tranche name.")
    principal: float = Field(default=0.0, ge=0.0)
    pdl_replenishment: float = Field(default=0.0, ge=0.0)


class WaterfallResult(BaseModel):
    """The distribution outcome of one period's priority-of-payments.

    This is the **contract** between the waterfall engine (S4) and the state
    transition. S4 computes how the period's available funds are applied;
    ``DealState.apply_waterfall_result`` folds that outcome back into the
    canonical state (reduce tranche balances by principal repaid, replenish
    PDLs, top up / draw the reserve). **This module does not compute it** — it
    only defines its shape and how it lands on the state.

    Generalised storage (deal-agnostic)
    -----------------------------------
    The per-tranche outcomes are stored as a ``tranches: list[TranchePayment]``
    keyed by tranche *name* — so a deal that isn't exactly A/B/C round-trips
    without hardcoded fields. The ``class_{a,b,c}_principal`` /
    ``class_{a,b,c}_pdl_replenishment`` names remain available as **accessors**
    over that list (and may be passed at construction as kwargs), so every
    existing caller and the byte-stable serialisation are unchanged.

    All amounts are in the deal currency and non-negative.

    Attributes
    ----------
    tranches:
        Per-tranche principal + PDL-replenishment outcomes, keyed by name.
    class_a_principal / class_b_principal / class_c_principal:
        Principal repaid to each tranche this period (accessor over ``tranches``;
        reduces that tranche's outstanding balance, floored at 0).
    class_a_pdl_replenishment / class_b_pdl_replenishment /
    class_c_pdl_replenishment:
        Amounts the revenue waterfall applied to cure each class's PDL debit
        (accessor over ``tranches``). Capped on apply at the outstanding PDL
        balance — you cannot replenish more than is owed.
    reserve_payment:
        Amount distributed *into* the reserve account (revenue waterfall
        top-up step). Capped on apply at the reserve target.
    reserve_draw:
        Amount drawn *from* the reserve to cover a senior shortfall (floored at
        0 on apply — you cannot draw the reserve negative).
    """

    tranches: list[TranchePayment] = Field(default_factory=list)
    reserve_payment: float = Field(default=0.0, ge=0.0)
    reserve_draw: float = Field(default=0.0, ge=0.0)

    @model_validator(mode="before")
    @classmethod
    def _coerce_class_kwargs(cls, data: object) -> object:
        """Accept legacy ``class_{a,b,c}_*`` kwargs, fold them into ``tranches``.

        Keeps the old construction surface (``WaterfallResult(class_a_principal=…,
        class_b_pdl_replenishment=…)``) working: when ``tranches`` is not supplied
        explicitly, any ``class_<x>_principal`` / ``class_<x>_pdl_replenishment``
        kwargs are gathered into a per-name :class:`TranchePayment` list in
        canonical A/B/C order. A tranche named in either kwarg gets an entry; the
        other half defaults to 0.
        """
        if not isinstance(data, dict):
            return data
        if data.get("tranches"):
            return data
        principals: dict[str, float] = {}
        replenishments: dict[str, float] = {}
        for key in list(data.keys()):
            if key.endswith("_principal"):
                principals[key[: -len("_principal")]] = data.pop(key)
            elif key.endswith("_pdl_replenishment"):
                replenishments[key[: -len("_pdl_replenishment")]] = data.pop(key)
        if not principals and not replenishments:
            return data
        # Preserve canonical A/B/C order first, then any other names encountered.
        names = list(_CANONICAL_TRANCHE_NAMES) + [
            n
            for n in list(principals) + list(replenishments)
            if n not in _CANONICAL_TRANCHE_NAMES
        ]
        seen: set[str] = set()
        tranches: list[dict[str, float | str]] = []
        for name in names:
            if name in seen or (name not in principals and name not in replenishments):
                continue
            seen.add(name)
            tranches.append(
                {
                    "name": name,
                    "principal": principals.get(name, 0.0),
                    "pdl_replenishment": replenishments.get(name, 0.0),
                }
            )
        data["tranches"] = tranches
        return data

    def _payment(self, name: str) -> TranchePayment | None:
        for t in self.tranches:
            if t.name == name:
                return t
        return None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_a_principal(self) -> float:
        p = self._payment("class_a")
        return p.principal if p is not None else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_b_principal(self) -> float:
        p = self._payment("class_b")
        return p.principal if p is not None else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_c_principal(self) -> float:
        p = self._payment("class_c")
        return p.principal if p is not None else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_a_pdl_replenishment(self) -> float:
        p = self._payment("class_a")
        return p.pdl_replenishment if p is not None else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_b_pdl_replenishment(self) -> float:
        p = self._payment("class_b")
        return p.pdl_replenishment if p is not None else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_c_pdl_replenishment(self) -> float:
        p = self._payment("class_c")
        return p.pdl_replenishment if p is not None else 0.0


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
    ``tranches``               canonical per-tranche liability store
                               (``list[TrancheState]`` — ``name`` / ``balance`` /
                               ``pdl_balance``), so the engine is not hardcoded to
                               three classes.
    ``class_{a,b,c}_balance``  outstanding tranche balances (accessors over
                               ``tranches``; also accepted as construction kwargs).
    ``class_{a,b,c}_pdl``      per-class PDL debit balances (accessors; 0 = no
                               deficiency).
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
    # Canonical per-tranche liability store. Each ``TrancheState`` carries the
    # tranche's ``name``, outstanding ``balance``, and ``pdl_balance`` (PDL debit;
    # 0 = no deficiency). The legacy ``class_{a,b,c}_balance|_pdl`` scalar fields
    # are kept as backward-compatible accessors (and accepted as construction
    # kwargs) over this list — see ``_coerce_class_kwargs`` and the computed
    # fields below — so the engine is no longer hardcoded to three classes while
    # every existing caller and the serialised shape are unchanged.
    tranches: list[TrancheState] = Field(
        default_factory=list, description="Per-tranche outstanding balance + PDL debit."
    )
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

    @field_validator("tranches")
    @classmethod
    def _non_negative_tranches(cls, v: list[TrancheState]) -> list[TrancheState]:
        """Enforce non-negative tranche balances / PDLs (the old per-field ``ge``).

        ``TrancheState`` (the canonical domain type) carries no ``ge`` bound, so
        the non-negativity the old ``class_{a,b,c}_balance|_pdl`` fields enforced
        is re-asserted here at the ``DealState`` boundary."""
        for t in v:
            if t.balance < 0.0:
                raise ValueError(f"tranche {t.name!r} balance must be >= 0")
            if t.pdl_balance < 0.0:
                raise ValueError(f"tranche {t.name!r} pdl_balance must be >= 0")
        return v

    @model_validator(mode="before")
    @classmethod
    def _coerce_class_kwargs(cls, data: object) -> object:
        """Accept legacy ``class_{a,b,c}_balance|_pdl`` kwargs as ``tranches``.

        Keeps the old construction surface working: a caller (or test) that
        builds ``DealState(class_a_balance=…, class_b_balance=…, class_a_pdl=…)``
        gets those folded into a canonical A/B/C-ordered ``tranches`` list when
        ``tranches`` is not supplied explicitly. A tranche named in any of the
        balance/pdl kwargs gets an entry; a missing balance defaults to 0.0, a
        missing PDL to 0.0. Callers may instead pass ``tranches`` directly (the
        canonical path) — then the ``class_*`` kwargs are ignored.
        """
        if not isinstance(data, dict):
            return data
        if data.get("tranches"):
            # Explicit tranche list wins; drop any stray class_* kwargs so they
            # don't trip the "unexpected field" guard.
            for key in list(data.keys()):
                if key.endswith("_balance") and key.startswith("class_"):
                    data.pop(key, None)
                elif key.endswith("_pdl") and key.startswith("class_"):
                    data.pop(key, None)
            return data
        balances: dict[str, float] = {}
        pdls: dict[str, float] = {}
        for key in list(data.keys()):
            if key.startswith("class_") and key.endswith("_balance"):
                balances[key[: -len("_balance")]] = data.pop(key)
            elif key.startswith("class_") and key.endswith("_pdl"):
                pdls[key[: -len("_pdl")]] = data.pop(key)
        if not balances and not pdls:
            return data
        names = list(_CANONICAL_TRANCHE_NAMES) + [
            n
            for n in list(balances) + list(pdls)
            if n not in _CANONICAL_TRANCHE_NAMES
        ]
        seen: set[str] = set()
        tranches: list[dict[str, float | str]] = []
        for name in names:
            if name in seen or (name not in balances and name not in pdls):
                continue
            seen.add(name)
            tranches.append(
                {
                    "name": name,
                    "balance": balances.get(name, 0.0),
                    "pdl_balance": pdls.get(name, 0.0),
                }
            )
        data["tranches"] = tranches
        return data

    # ------------------------------------------------------------------
    # Tranche access (the canonical liability surface) + legacy accessors
    # ------------------------------------------------------------------

    def _tranche(self, name: str) -> TrancheState | None:
        """The :class:`TrancheState` named ``name``, or ``None`` if absent."""
        for t in self.tranches:
            if t.name == name:
                return t
        return None

    def _with_tranche_updates(
        self,
        balance_deltas: dict[str, float] | None = None,
        pdl_values: dict[str, float] | None = None,
        *,
        extra: dict[str, object] | None = None,
    ) -> "DealState":
        """Return a new state with named tranches' balances / PDLs replaced.

        The single name-keyed mutation primitive the transition methods use in
        place of the old ``model_copy(update={"class_a_pdl": …})`` (which would
        not work now that ``class_*`` are computed accessors). ``balance_deltas``
        maps tranche name → new balance, ``pdl_values`` maps name → new PDL; only
        the named tranches change, the rest carry forward. ``extra`` is merged
        into the ``model_copy`` update for non-tranche fields (e.g. the reserve).
        """
        balance_deltas = balance_deltas or {}
        pdl_values = pdl_values or {}
        new_tranches: list[TrancheState] = []
        for t in self.tranches:
            upd: dict[str, float] = {}
            if t.name in balance_deltas:
                upd["balance"] = balance_deltas[t.name]
            if t.name in pdl_values:
                upd["pdl_balance"] = pdl_values[t.name]
            new_tranches.append(t.model_copy(update=upd) if upd else t)
        update: dict[str, object] = {"tranches": new_tranches}
        if extra:
            update.update(extra)
        return self.model_copy(update=update)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_a_balance(self) -> float:
        """Class A outstanding balance (accessor over ``tranches``)."""
        t = self._tranche("class_a")
        return t.balance if t is not None else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_b_balance(self) -> float:
        """Class B outstanding balance (accessor over ``tranches``)."""
        t = self._tranche("class_b")
        return t.balance if t is not None else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_c_balance(self) -> float:
        """Class C outstanding balance (accessor over ``tranches``)."""
        t = self._tranche("class_c")
        return t.balance if t is not None else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_a_pdl(self) -> float:
        """Class A PDL debit (accessor over ``tranches``)."""
        t = self._tranche("class_a")
        return t.pdl_balance if t is not None else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_b_pdl(self) -> float:
        """Class B PDL debit (accessor over ``tranches``)."""
        t = self._tranche("class_b")
        return t.pdl_balance if t is not None else 0.0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def class_c_pdl(self) -> float:
        """Class C PDL debit (accessor over ``tranches``)."""
        t = self._tranche("class_c")
        return t.pdl_balance if t is not None else 0.0

    # ------------------------------------------------------------------
    # Derived quantities
    # ------------------------------------------------------------------

    @property
    def total_pdl(self) -> float:
        """Sum of every tranche's PDL debit balance."""
        return sum(t.pdl_balance for t in self.tranches)

    @property
    def total_liabilities(self) -> float:
        """Sum of every tranche's outstanding balance."""
        return sum(t.balance for t in self.tranches)

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
            Mapping of ``<tranche>_balance`` closing balances (e.g.
            ``class_a_balance``, ``class_b_balance``, ``class_c_balance``). Extra
            keys (e.g. ``class_a_rate_pct``) are ignored. The tranche list is
            built from every ``<tranche>_balance`` key found, ordered
            canonical-A/B/C-first then any other names in insertion order — so a
            non-A/B/C structure seeds straight from its own balances. Sourced
            from the deal model / registry — never hardcoded here — so the seed
            is deal-agnostic. (A ``class_a/b/c_balance``-only structure seeds the
            three canonical tranches exactly as before.)
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
        # Build the tranche list from every ``<tranche>_balance`` key, ordered
        # canonical-A/B/C-first then any further names in insertion order. PDLs
        # all open at 0 (per spike S0 — the prospectus seeds balances, not PDLs).
        _ensure_deal_state_built()
        tranche_state = _tranche_state_cls()
        balance_names = [
            k[: -len("_balance")] for k in capital_structure if k.endswith("_balance")
        ]
        non_canonical = [n for n in balance_names if n not in _CANONICAL_TRANCHE_NAMES]
        if not non_canonical:
            # Purely-canonical structure: keep the prior strict A/B/C contract —
            # all three balances are required (a ``KeyError`` on the missing one,
            # matching the pre-refactor behaviour).
            ordered_names = list(_CANONICAL_TRANCHE_NAMES)
        else:
            # Custom structure: seed exactly the tranches present (canonical names
            # first, then the others in insertion order). Deal-agnostic — no
            # hardcoded A/B/C requirement.
            ordered_names = [
                n for n in _CANONICAL_TRANCHE_NAMES if n in balance_names
            ] + non_canonical
        tranches = [
            tranche_state(
                name=name,
                balance=float(capital_structure[f"{name}_balance"]),
                pdl_balance=0.0,
            )
            for name in ordered_names
        ]
        return cls(
            reporting_date=reporting_date,
            period_index=period_index,
            tranches=tranches,
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

        # Absorb the loss junior-first along ``allocation``; any tranche in the
        # state that ``allocation`` does not name is appended in reverse list
        # order (most-junior-last by convention) so a non-A/B/C structure still
        # absorbs across all its tranches without the caller having to restate
        # the default order.
        order = list(allocation) + [
            t.name for t in reversed(self.tranches) if t.name not in allocation
        ]
        pdl_values: dict[str, float] = {}
        remaining = loss
        for name in order:
            if remaining <= 0.0:
                break
            tranche = self._tranche(name)
            if tranche is None:
                continue
            headroom = _clamp_non_negative(tranche.balance - tranche.pdl_balance)
            debit = min(remaining, headroom)
            if debit > 0.0:
                pdl_values[name] = tranche.pdl_balance + debit
                remaining -= debit

        return self._with_tranche_updates(
            pdl_values=pdl_values,
            extra={"cumulative_losses": self.cumulative_losses + loss},
        )

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
        # Per-tranche principal redemption (floored at 0) and PDL replenishment
        # (capped at the outstanding debit), keyed by tranche name so the result
        # lands on whatever structure the state carries.
        balance_deltas: dict[str, float] = {}
        pdl_values: dict[str, float] = {}
        for payment in result.tranches:
            tranche = self._tranche(payment.name)
            if tranche is None:
                continue
            balance_deltas[payment.name] = _clamp_non_negative(
                tranche.balance - payment.principal
            )
            pdl_values[payment.name] = _clamp_non_negative(
                tranche.pdl_balance
                - min(payment.pdl_replenishment, tranche.pdl_balance)
            )

        # Reserve: top up (the *payment* is capped so it doesn't carry the
        # balance above target — but an already-over-target balance is never
        # clawed back), then draw (floored at 0).
        payment_amt = _clamp_non_negative(result.reserve_payment)
        if self.reserve_target > 0.0:
            headroom = _clamp_non_negative(self.reserve_target - self.reserve_balance)
            payment_amt = min(payment_amt, headroom)
        topped = self.reserve_balance + payment_amt
        drawn = _clamp_non_negative(topped - _clamp_non_negative(result.reserve_draw))

        return self._with_tranche_updates(
            balance_deltas=balance_deltas,
            pdl_values=pdl_values,
            extra={"reserve_balance": drawn},
        )

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


# ---------------------------------------------------------------------------
# Deferred forward-ref resolution
# ---------------------------------------------------------------------------
#
# ``DealState.tranches`` is annotated ``list[TrancheState]`` with ``TrancheState``
# imported only under ``TYPE_CHECKING`` (see ``_tranche_state_cls`` for why — it
# breaks a pre-existing ``domain`` ↔ ``primitives`` import cycle). The Pydantic
# core schema therefore cannot be built at class-definition time; it is rebuilt
# the first time the model is actually used. ``model_rebuild`` is idempotent and
# cheap after the first successful call.

_deal_state_built = False


def _ensure_deal_state_built() -> None:
    """Resolve ``DealState``'s deferred ``TrancheState`` ref (idempotent).

    Called at the start of every ``DealState`` construction path in this module
    (the ``seed_from_prospectus`` classmethod) and re-exported for the engine
    entry points. By first-use the ``primitives`` ↔ ``domain`` import cascade has
    settled, so ``domain.state`` imports cleanly.
    """
    global _deal_state_built
    if _deal_state_built:
        return
    DealState.model_rebuild(
        _types_namespace={"TrancheState": _tranche_state_cls()}
    )
    _deal_state_built = True


try:  # Best-effort eager rebuild — succeeds when this module is imported after
    # the cascade has settled (the common case: anything importing a primitives
    # symbol first). When it runs mid-cascade (domain imported first) it raises an
    # ImportError, which is swallowed; ``_ensure_deal_state_built`` then performs
    # the rebuild lazily on first construction.
    _ensure_deal_state_built()
except ImportError:
    pass
