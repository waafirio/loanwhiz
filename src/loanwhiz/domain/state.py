"""``DealState`` — the canonical *evolving structural state* of a deal.

Where :class:`loanwhiz.domain.rules.DealRules` is the period-invariant program,
``DealState`` is the per-period structural snapshot the engine rolls forward:

    DealStateSeries = fold(run_period, seed, inputs[])

The **seed** state carries provenance — it was *extracted* from a prospectus or
report (the B5 period-0 seed). Every rolled state is engine-computed and needs
none, so ``DealState.provenance`` is ``None`` on rolled states (spec §3).

Note this is the canonical ``domain`` ``DealState``; it is intentionally distinct
from the older ``loanwhiz.primitives.deal_state.DealState``, which this schema's
consolidation map lists as eventually superseded. Migrating those call sites is a
downstream epic phase — this module only *defines* the canonical type.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from loanwhiz.domain.provenance import ProvenanceMap

# ---------------------------------------------------------------------------
# TrancheState — one note class's evolving balances.
# ---------------------------------------------------------------------------


class TrancheState(BaseModel):
    """One note class's evolving balances at a point in time.

    Attributes:
        name:        Class name, matching the :class:`TrancheRule` it tracks.
        balance:     Outstanding note balance.
        pdl_balance: Outstanding Principal Deficiency Ledger balance.
    """

    name: str = Field(..., description="Class name.")
    balance: float = Field(..., description="Outstanding note balance.")
    pdl_balance: float = Field(..., description="Outstanding PDL balance.")


# ---------------------------------------------------------------------------
# DealState — the per-period structural snapshot.
# ---------------------------------------------------------------------------


class DealState(BaseModel):
    """The structural state of a deal at one reporting date.

    Attributes:
        reporting_date:        The snapshot's reporting date (ISO string).
        tranches:              Per-class evolving balances.
        reserve_balance:       Current reserve account balance.
        reserve_target:        Current reserve target.
        pool_balance:          Current outstanding pool balance.
        original_pool_balance: Pool balance at closing (for pool-factor).
        cumulative_losses:     Cumulative realised losses to date.
        sequential_pay_active: Whether sequential-pay has been triggered.
        provenance:            Sidecar provenance — set only on the period-0
                               seed (it was extracted); ``None`` on every rolled
                               (engine-computed) state.
    """

    reporting_date: str = Field(..., description="Reporting date (ISO string).")
    tranches: list[TrancheState] = Field(..., description="Per-class balances.")
    reserve_balance: float = Field(..., description="Current reserve balance.")
    reserve_target: float = Field(..., description="Current reserve target.")
    pool_balance: float = Field(..., description="Current outstanding pool balance.")
    original_pool_balance: float = Field(
        ..., description="Pool balance at closing (for pool-factor)."
    )
    cumulative_losses: float = Field(..., description="Cumulative realised losses.")
    sequential_pay_active: bool = Field(
        ..., description="Whether sequential-pay is active."
    )
    provenance: ProvenanceMap | None = Field(
        default=None,
        description="Set only on the period-0 seed; None on rolled states.",
    )
