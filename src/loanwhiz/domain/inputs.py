"""``PeriodInputs`` — uniform per-period exogenous inputs for the engine.

Produced by **any** adapter — a loan tape, an investor report, or a scenario
generator — and consumed by ``fold(run_period)``. This supersedes the tape-only
``PeriodCollections``: the engine no longer cares whether a period's numbers came
from a tape or a report, because both fill the same type.

Two locked design decisions shape this module
(``docs/superpowers/specs/2026-06-20-canonical-domain-schema-design.md``):

- **Always store aggregate available funds; the finer ``legs`` are optional**
  (decision 4). The aggregate (``available_revenue`` / ``available_principal``)
  is the common denominator both a tape and a report can supply; the per-leg
  breakdown is the tape's bonus and is ``None`` on the report path.
- **ESMA Annex 2 anchors as citation *locators*, not as new fields**
  (decision 5). The RTS field codes for :class:`RiskSignals` /
  :class:`CollectionLegs` live in ``Citation.page_or_row`` on those fields'
  provenance entries — the mechanism is fixed here; the full code→field mapping
  table is a Phase-4 detail.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from loanwhiz.domain.provenance import ProvenanceMap

# ---------------------------------------------------------------------------
# CollectionLegs — finer, tape-only breakdown; legs sum to the aggregates.
# ---------------------------------------------------------------------------


class CollectionLegs(BaseModel):
    """The per-leg breakdown of a period's collections (tape path only).

    Present only on the tape path; the legs sum to the aggregate
    ``available_revenue`` / ``available_principal`` on :class:`PeriodInputs`.

    Attributes:
        interest:            Interest collected.
        scheduled_principal: Scheduled (contractual) principal.
        prepayment:          Unscheduled principal prepayments.
        recovery:            Recoveries on defaulted loans.
        realized_loss:       Losses crystallised this period.
    """

    interest: float = Field(..., description="Interest collected.")
    scheduled_principal: float = Field(..., description="Scheduled principal.")
    prepayment: float = Field(..., description="Unscheduled prepayments.")
    recovery: float = Field(..., description="Recoveries on defaulted loans.")
    realized_loss: float = Field(..., description="Losses crystallised this period.")


# ---------------------------------------------------------------------------
# RiskSignals — tape-only; ESMA Annex 2-anchored via provenance.
# ---------------------------------------------------------------------------


class RiskSignals(BaseModel):
    """Pool-level risk signals derived from the loan tape (tape path only).

    These are future B7 inputs; their provenance entries carry the ESMA RTS
    Annex 2 field code in ``Citation.page_or_row`` so each value is traceable to
    the regulatory field it came from.

    Attributes:
        arrears_90d:  Balance ≥90 days in arrears.
        arrears_180d: Balance ≥180 days in arrears.
        wa_ltv:       Weighted-average loan-to-value of the pool.
        default_pct:  Defaulted balance as a fraction of the pool.
        pool_balance: Outstanding pool balance.
    """

    arrears_90d: float = Field(..., description="Balance >=90 days in arrears.")
    arrears_180d: float = Field(..., description="Balance >=180 days in arrears.")
    wa_ltv: float = Field(..., description="Weighted-average loan-to-value.")
    default_pct: float = Field(..., description="Defaulted balance fraction of pool.")
    pool_balance: float = Field(..., description="Outstanding pool balance.")


# ---------------------------------------------------------------------------
# PeriodInputs — the per-period exogenous input contract.
# ---------------------------------------------------------------------------


class PeriodInputs(BaseModel):
    """The exogenous inputs to one period of the engine, from any adapter.

    Attributes:
        reporting_date:     The period's reporting date (ISO string).
        days_in_period:     Day count for interest accrual.
        available_revenue:  Aggregate funds for the revenue waterfall (the common
                            denominator a report gives directly).
        available_principal: Aggregate funds for the redemption waterfall.
        realized_loss:      Losses crystallised this period.
        legs:               The finer breakdown, present on the tape path only;
                            ``None`` on the report path.
        step_overrides:     ``priority_label -> reported amount`` for steps the
                            engine cannot compute (``basis == "report_supplied"``).
        step_sources:       ``priority_label -> "engine" | "reported" | "residual"``,
                            recording how each step's amount was determined.
        risk_signals:       Tape-only pool risk signals; ``None`` otherwise.
        source:             Which adapter produced these inputs.
        provenance:         Sidecar provenance, keyed by dotted field path.
    """

    reporting_date: str = Field(..., description="Reporting date (ISO string).")
    days_in_period: int = Field(..., description="Day count for interest accrual.")
    available_revenue: float = Field(
        ..., description="Aggregate funds for the revenue waterfall."
    )
    available_principal: float = Field(
        ..., description="Aggregate funds for the redemption waterfall."
    )
    realized_loss: float = Field(..., description="Losses crystallised this period.")
    legs: CollectionLegs | None = Field(
        default=None, description="Finer breakdown; present on the tape path only."
    )
    step_overrides: dict[str, float] = Field(
        default_factory=dict,
        description="priority_label -> reported amount (report path).",
    )
    step_sources: dict[str, Literal["engine", "reported", "residual"]] = Field(
        default_factory=dict,
        description="priority_label -> how the step amount was determined.",
    )
    risk_signals: RiskSignals | None = Field(
        default=None, description="Tape-only pool risk signals."
    )
    source: Literal["tape", "report", "scenario"] = Field(
        ..., description="Which adapter produced these inputs."
    )
    provenance: ProvenanceMap = Field(
        default_factory=dict, description="Sidecar provenance, keyed by dotted path."
    )
