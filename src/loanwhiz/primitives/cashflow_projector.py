"""Cashflow projector primitive — 12-month forward scenario projections.

Projects monthly cashflows for the Green Lion 2026-1 B.V. deal under multiple
scenarios (base case and stress) by iterating the waterfall runner over a
12-month horizon.

Scenarios are parameterised by:
- ``default_rate_multiplier``: scales the base annual CDR (0.03%).
- ``prepayment_rate_pct``: annual CPR applied monthly as
  ``1 - (1 - CPR)^(1/12)``.
- ``interest_rate_shift_bps``: EURIBOR shift added to the Class A coupon.
- ``recovery_rate_pct``: fraction of defaulted balance recovered (not lost).

For each scenario the projector iterates over 12 monthly periods:
  1. Compute monthly CDR and CPR factors.
  2. Compute scheduled principal (1% of pool balance as a simple amortisation
     proxy — a real model would use the loan-level amortisation schedule).
  3. Apply CPR (prepayments) and CDR (defaults, net of recoveries).
  4. Compute pool interest at the shifted Class A rate.
  5. Build a WaterfallInput and invoke WaterfallRunner.
  6. Update tranche balances from the waterfall output.
  7. Track cumulative losses and reserve fund balance.

Confidence is 0.7 — projections are inherently uncertain; the model captures
the structural mechanics but not macro-economic randomness.
"""

from __future__ import annotations

import time
from typing import List

from pydantic import BaseModel, Field

from loanwhiz.primitives.base import (
    AuditEntry,
    BaseInput,
    Citation,
    Primitive,
    PrimitiveResult,
)
from loanwhiz.primitives.registry import register_primitive
from loanwhiz.primitives.waterfall_runner import WaterfallInput, WaterfallRunner

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Base annual CDR for Green Lion 2026-1 (from latest ESMA tape, ≈ 0.03%).
_BASE_ANNUAL_CDR_PCT: float = 0.03

# Fixed confidence: forward projections carry inherent model uncertainty.
_PROJECTION_CONFIDENCE: float = 0.7

# Waterfall operational constants (Green Lion 2026-1 structural parameters).
_SENIOR_FEES_MONTHLY: float = 16_667.0  # ≈ €200k / 12
_SWAP_PAYMENT: float = 0.0               # No active swap in base model
_RESERVE_ACCOUNT_TARGET: float = 5_000_000.0  # €5M reserve target

# Simple amortisation rate: 1% of pool balance per month (proxy for scheduled
# principal in the absence of a loan-level amortisation schedule).
_SCHEDULED_AMORT_RATE: float = 0.01


# ---------------------------------------------------------------------------
# Scenario assumptions model
# ---------------------------------------------------------------------------


class ScenarioAssumptions(BaseModel):
    """Assumptions for one projection scenario.

    Attributes:
        name:                       Scenario label, e.g. ``"base"`` or
                                    ``"stress_2x_default"``.
        description:                Human-readable description for the summary.
        default_rate_multiplier:    Multiplier applied to the base annual CDR
                                    (0.03%). 1.0 = base; 2.0 = 2× defaults.
        prepayment_rate_pct:        Annual CPR (Conditional Prepayment Rate)
                                    as a percentage. Defaults to 15.0%.
        interest_rate_shift_bps:    Additive EURIBOR shift in basis points.
                                    Defaults to 0 (no shift).
        recovery_rate_pct:          Percentage of the defaulted balance
                                    recovered (not lost permanently). Defaults
                                    to 70%.
    """

    name: str
    description: str
    default_rate_multiplier: float = 1.0
    prepayment_rate_pct: float = 15.0
    interest_rate_shift_bps: float = 0.0
    recovery_rate_pct: float = 70.0


# ---------------------------------------------------------------------------
# Period-level projection output
# ---------------------------------------------------------------------------


class PeriodProjection(BaseModel):
    """Projected cashflows and balances for one monthly period.

    Attributes:
        period:                 Period index, 1 through 12.
        pool_balance_eur:       Remaining pool balance at period end (EUR).
        class_a_distribution:  Total distributed to Class A (interest +
                                principal) in this period (EUR).
        class_b_distribution:  Total distributed to Class B in this period.
        class_c_distribution:  Total distributed to Class C in this period.
        cumulative_losses:      Accumulated pool losses (EUR) from period 1
                                through this period.
        reserve_fund_balance:   Reserve fund balance at period end (EUR).
    """

    period: int
    pool_balance_eur: float
    class_a_distribution: float
    class_b_distribution: float
    class_c_distribution: float
    cumulative_losses: float
    reserve_fund_balance: float


# ---------------------------------------------------------------------------
# Scenario-level projection output
# ---------------------------------------------------------------------------


class ScenarioProjection(BaseModel):
    """Full 12-month projection for one scenario.

    Attributes:
        scenario:           The assumptions that drove this projection.
        periods:            Monthly projections, one entry per period.
        total_class_a:      Sum of Class A distributions across all periods.
        total_class_b:      Sum of Class B distributions across all periods.
        wal_class_a_months: Weighted-average life of Class A, in months.
                            Computed as sum(t × principal_t) / sum(principal_t)
                            where t is the period number and principal_t is the
                            Class A principal received in period t.
    """

    scenario: ScenarioAssumptions
    periods: List[PeriodProjection]
    total_class_a: float
    total_class_b: float
    wal_class_a_months: float


# ---------------------------------------------------------------------------
# Input / Output models for the primitive
# ---------------------------------------------------------------------------


class CashflowProjectorInput(BaseInput):
    """Input schema for the cashflow projector.

    All monetary amounts in EUR. Rates in percent.

    Attributes:
        current_pool_balance:     Current aggregate pool balance from the
                                  latest tape (EUR).
        current_class_a_balance:  Class A outstanding note balance (EUR).
        current_class_b_balance:  Class B outstanding note balance (EUR).
        current_class_c_balance:  Class C outstanding note balance (EUR).
        class_a_rate_pct:         Current Class A coupon rate in percent
                                  (e.g. 3.62 for EURIBOR 3.19 + 0.43 margin).
        reserve_fund_balance:     Current reserve fund balance (EUR).
        scenarios:                Scenarios to project. Defaults to a base
                                  case and a 2× default / +100bps stress case.
        projection_months:        Number of monthly periods to project.
                                  Defaults to 12.
    """

    current_pool_balance: float
    current_class_a_balance: float
    current_class_b_balance: float
    current_class_c_balance: float
    class_a_rate_pct: float
    reserve_fund_balance: float
    scenarios: List[ScenarioAssumptions] = Field(
        default_factory=lambda: [
            ScenarioAssumptions(
                name="base",
                description="Base case: historical CPR, zero defaults",
            ),
            ScenarioAssumptions(
                name="stress",
                description="Stress: 2× default rate, +100bps rates",
                default_rate_multiplier=2.0,
                interest_rate_shift_bps=100,
            ),
        ]
    )
    projection_months: int = 12


class CashflowProjectorOutput(BaseModel):
    """Output of the cashflow projector for all scenarios.

    Attributes:
        scenario_projections: One entry per scenario in
                              ``CashflowProjectorInput.scenarios``.
        summary:              Human-readable comparison of scenarios, e.g.
                              "Base case: Class A WAL 3.2yr, fully repaid by
                              month 8; Stress: WAL 4.1yr …".
    """

    scenario_projections: List[ScenarioProjection]
    summary: str


# ---------------------------------------------------------------------------
# Primitive
# ---------------------------------------------------------------------------


@register_primitive(
    name="cashflow_projector",
    version="0.1.0",
    description="Project 12-month cashflows under base and stress scenarios",
    tags=["projection", "cashflow", "scenario", "computation"],
)
class CashflowProjector(Primitive[CashflowProjectorInput, CashflowProjectorOutput]):
    """Project 12-month cashflows under base and stress scenarios.

    Iterates WaterfallRunner monthly for each scenario.  Confidence is fixed
    at 0.7 — forward projections carry inherent model uncertainty.
    """

    name = "cashflow_projector"
    version = "0.1.0"
    description = "Project 12-month cashflows under base and stress scenarios"

    def execute(
        self, input: CashflowProjectorInput
    ) -> PrimitiveResult[CashflowProjectorOutput]:
        """Project cashflows for all scenarios and return results.

        Parameters
        ----------
        input:
            Validated ``CashflowProjectorInput``.

        Returns
        -------
        PrimitiveResult[CashflowProjectorOutput]
            Scenario projections with ``confidence=0.7``.
        """
        t0 = time.perf_counter()
        input_hash = input.input_hash()

        runner = WaterfallRunner()
        scenario_projections: list[ScenarioProjection] = []

        for scenario in input.scenarios:
            proj = self._project_scenario(input, scenario, runner)
            scenario_projections.append(proj)

        summary = self._build_summary(scenario_projections)

        duration_ms = (time.perf_counter() - t0) * 1000.0
        audit = AuditEntry.now(
            primitive_name=self.name,
            version=self.version,
            input_hash=input_hash,
            duration_ms=duration_ms,
        )

        citations = [
            Citation(
                document="Green Lion 2026-1 B.V. Prospectus",
                page_or_row="section 5.2",
                excerpt=(
                    "Revenue and Redemption Priority of Payments; "
                    "cashflow projections are model outputs, not prospectus data"
                ),
            )
        ]

        output = CashflowProjectorOutput(
            scenario_projections=scenario_projections,
            summary=summary,
        )

        return PrimitiveResult[CashflowProjectorOutput](
            output=output,
            confidence=_PROJECTION_CONFIDENCE,
            citations=citations,
            audit_entry=audit,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _project_scenario(
        self,
        input: CashflowProjectorInput,
        scenario: ScenarioAssumptions,
        runner: WaterfallRunner,
    ) -> ScenarioProjection:
        """Run the 12-month projection for one scenario."""

        # Annual CDR = base_CDR × multiplier (expressed as a fraction, not %).
        annual_cdr = (_BASE_ANNUAL_CDR_PCT / 100.0) * scenario.default_rate_multiplier

        # Monthly CDR (simple 1/12 decomposition from annual).
        monthly_cdr = annual_cdr / 12.0

        # Monthly SMM (Single Monthly Mortality) from annual CPR.
        # SMM = 1 - (1 - CPR)^(1/12)
        annual_cpr = scenario.prepayment_rate_pct / 100.0
        monthly_smm = 1.0 - (1.0 - annual_cpr) ** (1.0 / 12.0)

        # Effective Class A coupon rate with EURIBOR shift.
        effective_rate_pct = (
            input.class_a_rate_pct + scenario.interest_rate_shift_bps / 100.0
        )

        # Mutable state across periods.
        pool_balance = input.current_pool_balance
        class_a_balance = input.current_class_a_balance
        class_b_balance = input.current_class_b_balance
        class_c_balance = input.current_class_c_balance
        reserve_balance = input.reserve_fund_balance
        cumulative_losses = 0.0

        # Principal Deficiency Ledger (PDL) balances.  When a default loss is
        # recorded the PDL is debited; the waterfall's revenue step (e) uses
        # available interest revenue to replenish the PDL before interest
        # passes to Class A.  This is the mechanism by which stress scenarios
        # reduce Class A income per period.
        class_a_pdl = 0.0
        class_b_pdl = 0.0

        periods: list[PeriodProjection] = []
        total_class_a = 0.0
        total_class_b = 0.0

        # WAL numerator/denominator accumulators.
        wal_numerator = 0.0
        wal_denominator = 0.0

        for t in range(1, input.projection_months + 1):
            # ------------------------------------------------------------------
            # 1. Compute period cashflows from the pool.
            # ------------------------------------------------------------------

            # Scheduled principal (simple amortisation proxy).
            scheduled_principal = pool_balance * _SCHEDULED_AMORT_RATE

            # Prepayments (SMM applied to remaining balance after scheduled
            # principal is peeled off).
            balance_after_scheduled = pool_balance - scheduled_principal
            prepayment_principal = balance_after_scheduled * monthly_smm

            # Default principal (CDR applied to remaining balance after
            # scheduled principal).
            default_principal_gross = balance_after_scheduled * monthly_cdr

            # Net loss = defaults × (1 - recovery_rate).
            recovery_rate = scenario.recovery_rate_pct / 100.0
            period_loss = default_principal_gross * (1.0 - recovery_rate)
            recovered_principal = default_principal_gross * recovery_rate

            # Total available principal funds for the waterfall this period.
            available_principal = (
                scheduled_principal + prepayment_principal + recovered_principal
            )

            # Pool interest (at effective coupon, Act/360 with 30 days).
            # Using 30 days per month (Act/360 approximation for monthly).
            pool_interest = pool_balance * (effective_rate_pct / 100.0) / 360.0 * 30.0

            # Pool balance at end of period (before waterfall tranche updates).
            pool_balance_end = max(
                0.0,
                pool_balance
                - scheduled_principal
                - prepayment_principal
                - default_principal_gross,
            )

            # ------------------------------------------------------------------
            # 2. Run the waterfall.
            # ------------------------------------------------------------------

            wf_input = WaterfallInput(
                reporting_period=f"Month {t}",
                available_revenue_funds=pool_interest,
                available_principal_funds=available_principal,
                senior_fees=_SENIOR_FEES_MONTHLY,
                swap_payment=_SWAP_PAYMENT,
                class_a_balance=class_a_balance,
                class_a_rate_pct=effective_rate_pct,
                class_b_balance=class_b_balance,
                class_c_balance=class_c_balance,
                reserve_account_balance=reserve_balance,
                reserve_account_target=_RESERVE_ACCOUNT_TARGET,
                class_a_pdl_balance=class_a_pdl,
                class_b_pdl_balance=class_b_pdl,
                days_in_period=30,
            )
            wf_result = runner.execute(wf_input)
            wf_output = wf_result.output

            # ------------------------------------------------------------------
            # 3. Extract distributions from waterfall output.
            # ------------------------------------------------------------------

            def _tranche_total(tranche_name: str) -> float:
                for dist in wf_output.tranche_distributions:
                    if dist.tranche == tranche_name:
                        return dist.total_received
                return 0.0

            def _tranche_principal(tranche_name: str) -> float:
                for dist in wf_output.tranche_distributions:
                    if dist.tranche == tranche_name:
                        return dist.principal_received
                return 0.0

            class_a_dist = _tranche_total("class_a")
            class_b_dist = _tranche_total("class_b")
            class_c_dist = _tranche_total("class_c")

            class_a_principal = _tranche_principal("class_a")

            # Amount replenished into the PDL this period (revenue step e/h).
            def _rev_step_distributed(recipient: str) -> float:
                for step in wf_output.revenue_waterfall:
                    if step.recipient == recipient:
                        return step.amount_distributed
                return 0.0

            class_a_pdl_replenished = _rev_step_distributed("class_a_pdl_replenishment")
            class_b_pdl_replenished = _rev_step_distributed("class_b_pdl_replenishment")

            # ------------------------------------------------------------------
            # 4. Update state for next period.
            # ------------------------------------------------------------------

            class_a_balance = max(0.0, class_a_balance - class_a_principal)
            class_b_balance = max(0.0, class_b_balance - _tranche_principal("class_b"))
            class_c_balance = max(0.0, class_c_balance - _tranche_principal("class_c"))
            pool_balance = pool_balance_end
            cumulative_losses += period_loss

            # Update PDL: debit with this period's net loss; credit with
            # amounts replenished by the revenue waterfall this period.
            # Losses are allocated first to Class B PDL, then to Class A PDL
            # (junior absorbs first, as per senior/subordinate structure).
            remaining_loss = period_loss
            class_b_pdl_debit = min(remaining_loss, class_b_balance if class_b_balance > 0 else 0.0)
            remaining_loss -= class_b_pdl_debit
            class_a_pdl_debit = remaining_loss

            class_a_pdl = max(0.0, class_a_pdl + class_a_pdl_debit - class_a_pdl_replenished)
            class_b_pdl = max(0.0, class_b_pdl + class_b_pdl_debit - class_b_pdl_replenished)

            # Reserve fund: replenished by the waterfall (step f) if below
            # target, or may draw down if there's a shortfall. For simplicity,
            # clamp to [0, target].
            reserve_balance = min(_RESERVE_ACCOUNT_TARGET, max(0.0, reserve_balance))

            # Accumulate totals.
            total_class_a += class_a_dist
            total_class_b += class_b_dist

            # WAL computation (Class A principal distributions).
            wal_numerator += t * class_a_principal
            wal_denominator += class_a_principal

            periods.append(
                PeriodProjection(
                    period=t,
                    pool_balance_eur=pool_balance,
                    class_a_distribution=class_a_dist,
                    class_b_distribution=class_b_dist,
                    class_c_distribution=class_c_dist,
                    cumulative_losses=cumulative_losses,
                    reserve_fund_balance=reserve_balance,
                )
            )

        # WAL: months (avoid division by zero when no principal is returned).
        wal = wal_numerator / wal_denominator if wal_denominator > 0.0 else 0.0

        return ScenarioProjection(
            scenario=scenario,
            periods=periods,
            total_class_a=total_class_a,
            total_class_b=total_class_b,
            wal_class_a_months=wal,
        )

    @staticmethod
    def _build_summary(projections: list[ScenarioProjection]) -> str:
        """Build a human-readable scenario comparison summary string."""
        parts: list[str] = []
        for sp in projections:
            wal_yr = sp.wal_class_a_months / 12.0
            # Find the first period where the pool balance is near zero.
            repaid_period = None
            for p in sp.periods:
                if p.pool_balance_eur < 1.0:
                    repaid_period = p.period
                    break
            repaid_str = (
                f"fully amortised by month {repaid_period}"
                if repaid_period is not None
                else f"pool balance {sp.periods[-1].pool_balance_eur:,.0f} EUR remaining at month 12"
            )
            parts.append(
                f"{sp.scenario.name.title()}: Class A WAL {wal_yr:.1f}yr, {repaid_str}"
            )
        return "; ".join(parts)
