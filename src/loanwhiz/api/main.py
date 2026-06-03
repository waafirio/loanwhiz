"""LoanWhiz REST API — FastAPI service over the agent and primitives.

This is the interface every client (CLI, notebook, demo UI) calls. It wraps:

- the agent (:func:`loanwhiz.agent.executor.execute_query`) behind ``POST /query``;
- the deal context (:data:`loanwhiz.config.GREEN_LION`) behind
  ``GET /deal/{id}/model``;
- the covenant monitor (over normalised ESMA tapes) behind
  ``GET /deal/{id}/compliance``;
- a forward payment-waterfall projection (:class:`WaterfallRunner`) behind
  ``POST /deal/{id}/project``.

Projection primitive note
--------------------------
The original design referenced a dedicated ``CashflowProjector`` primitive.
That primitive is not present in this branch's ``loanwhiz.primitives`` package;
the available deterministic forward-projection primitive is
:class:`~loanwhiz.primitives.waterfall_runner.WaterfallRunner`, which runs the
Green Lion Revenue + Redemption waterfalls against per-period collections. The
``POST /deal/{id}/project`` endpoint is built on it and keeps a scenario-shaped
request/response so a later swap to a dedicated projector is a drop-in change.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from loanwhiz.agent.executor import execute_query
from loanwhiz.config import GREEN_LION
from loanwhiz.primitives.collections_aggregator import (
    CollectionsAggregator,
    CollectionsInput,
)
from loanwhiz.primitives.covenant_monitor import CovenantInput, CovenantMonitor
from loanwhiz.primitives.esma_tape_normaliser import (
    EsmaTapeInput,
    EsmaTapeNormaliser,
)
from loanwhiz.primitives.waterfall_runner import WaterfallInput, WaterfallRunner

app = FastAPI(
    title="LoanWhiz API",
    description="Structured finance agent framework — REST interface",
    version="0.1.0",
)

# Allow the local Next.js demo frontend (v2, served on :3000) to call this API
# from the browser. Scoped to the two localhost dev origins — this is a local
# demo allowlist, not a production CORS policy.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registry of known deals. For the hackathon, Green Lion is the one deal; the
# key is the canonical deal id clients use in the /deal/{deal_id}/... routes.
DEALS: dict[str, dict] = {"green-lion-2026-1": GREEN_LION}

# Green Lion 2026-1 capital structure / latest reported figures, used as the
# base case for the forward projection. These mirror the deal's reported tape
# and investor-report values; a future dedicated projector would derive them
# from the latest period rather than hard-coding.
_GREEN_LION_PROJECTION_BASE = {
    "current_pool_balance": 1_033_412_063.0,
    "class_a_balance": 1_000_000_000.0,
    "class_b_balance": 53_100_000.0,
    "class_c_balance": 10_500_000.0,
    "class_a_rate_pct": 3.62,
    "reserve_account_balance": 10_636_000.0,
    "reserve_account_target": 10_636_000.0,
}

# Stress multipliers applied to available collections per scenario. "base" runs
# the deal as reported; "stress" haircuts collections to model a downturn.
_SCENARIO_COLLECTION_FACTORS = {
    "base": 1.0,
    "stress": 0.7,
}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class QueryRequest(BaseModel):
    """Request body for ``POST /query``."""

    question: str
    confidence_threshold: float = 0.7


class QueryResponse(BaseModel):
    """Response body for ``POST /query`` — the agent answer + governance fields."""

    question: str
    answer: str
    overall_status: str
    aggregate_confidence: float
    human_review_required: bool
    reasoning_trace: list[str]
    evidence_pack_id: str


class ProjectRequest(BaseModel):
    """Request body for ``POST /deal/{deal_id}/project``."""

    scenarios: list[str] = Field(default_factory=lambda: ["base", "stress"])
    months: int = 12


# ---------------------------------------------------------------------------
# Service / health
# ---------------------------------------------------------------------------


@app.get("/")
def root() -> dict:
    """Service info — name, version, and the known deal ids."""
    return {"service": "LoanWhiz API", "version": "0.1.0", "deals": list(DEALS)}


@app.get("/health")
def health() -> dict:
    """Liveness probe."""
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Agent query
# ---------------------------------------------------------------------------


@app.post("/query", response_model=QueryResponse)
def query(req: QueryRequest) -> QueryResponse:
    """Answer a natural language question about a structured finance deal."""
    result = execute_query(req.question, confidence_threshold=req.confidence_threshold)
    return QueryResponse(
        question=result.question,
        answer=result.answer,
        overall_status=result.overall_status.value,
        aggregate_confidence=result.aggregate_confidence,
        human_review_required=result.human_review_required,
        reasoning_trace=result.reasoning_trace,
        evidence_pack_id=result.evidence_pack_id,
    )


# ---------------------------------------------------------------------------
# Deal endpoints
# ---------------------------------------------------------------------------


def _require_deal(deal_id: str) -> dict:
    """Return the deal context for ``deal_id`` or raise a 404."""
    deal = DEALS.get(deal_id)
    if deal is None:
        raise HTTPException(status_code=404, detail=f"Deal {deal_id} not found")
    return deal


@app.get("/deal/{deal_id}/model")
def deal_model(deal_id: str) -> dict:
    """Return the deal context/model (document URLs, structure)."""
    return _require_deal(deal_id)


@app.get("/deal/{deal_id}/compliance")
def deal_compliance(deal_id: str) -> dict:
    """Run covenant compliance across all reporting periods for the deal.

    Normalises every ESMA tape the deal references, then runs the covenant
    monitor over the per-period pool analytics using the monitor's default
    trigger set.
    """
    deal = _require_deal(deal_id)
    normaliser = EsmaTapeNormaliser()
    periods = [
        normaliser.execute(EsmaTapeInput(file_url=tape["url"])).output.model_dump()
        for tape in deal["tape_urls"]
    ]
    monitor = CovenantMonitor()
    result = monitor.execute(
        CovenantInput(periods=periods, triggers=CovenantMonitor.DEFAULT_TRIGGERS)
    )
    return result.output.model_dump()


# ---------------------------------------------------------------------------
# Waterfall endpoint (CollectionsAggregator -> WaterfallRunner, latest period)
#
# Self-contained block: the response models + handler for
# GET /deal/{deal_id}/waterfall live together here so the route can be reviewed
# (and merged) as one unit. It mirrors the v1 Gradio chain
# (clients/demo/tabs/waterfall.py) and the demo runner
# (demo/run_green_lion.py): aggregate the latest reported tape into Available
# Revenue / Principal Funds, then run the Green Lion Revenue + Redemption
# Priority of Payments. Deterministic (no LLM), but it fetches the tape CSV.
# ---------------------------------------------------------------------------

# Green Lion 2026-1 capital structure (prospectus section 5; also the
# primitives' own defaults). Restated here so the endpoint is explicit about
# the structure it runs the waterfall against.
_GREEN_LION_CLASS_A_BALANCE = 1_000_000_000.0
_GREEN_LION_CLASS_A_RATE_PCT = 3.62
_GREEN_LION_CLASS_B_BALANCE = 53_100_000.0
_GREEN_LION_CLASS_C_BALANCE = 10_500_000.0


class WaterfallStepModel(BaseModel):
    """One priority step in the revenue cascade (mirrors ``WaterfallStep``)."""

    priority: str
    recipient: str
    amount_available: float
    amount_distributed: float
    shortfall: float
    condition: str | None = None


class TrancheDistributionModel(BaseModel):
    """Per-tranche distribution summary (mirrors ``TrancheDistribution``)."""

    tranche: str
    interest_received: float
    principal_received: float
    total_received: float
    opening_balance: float
    closing_balance: float


class WaterfallResponse(BaseModel):
    """Response body for ``GET /deal/{deal_id}/waterfall``.

    Carries the 11-step Revenue Priority of Payments cascade and the
    per-tranche (Class A / B / C) distributions for the latest reported period,
    plus the Available Revenue / Principal Funds the waterfall ran on.
    """

    deal_id: str
    reporting_period: str
    available_revenue_funds: float
    available_principal_funds: float
    revenue_waterfall: list[WaterfallStepModel]
    tranche_distributions: list[TrancheDistributionModel]
    total_distributed: float
    shortfall: float


@app.get("/deal/{deal_id}/waterfall", response_model=WaterfallResponse)
def deal_waterfall(deal_id: str) -> WaterfallResponse:
    """Run the revenue waterfall for the deal's latest reported period.

    Aggregates the most recent ESMA tape into Available Revenue / Principal
    Funds (``CollectionsAggregator``), deriving ``prev_pool_balance`` from the
    prior period's tape so scheduled principal is the reliable balance-delta
    path, then runs the Green Lion Revenue + Redemption Priority of Payments
    (``WaterfallRunner``) on the deal's capital structure. Returns the 11-step
    revenue cascade and the per-tranche distributions.
    """
    _require_deal(deal_id)

    tapes = GREEN_LION["tape_urls"]
    latest = tapes[-1]
    period = latest["date"]

    aggregator = CollectionsAggregator()

    # prev_pool_balance from the prior period's tape (reliable balance-delta
    # path for scheduled principal). None when this is the only period; derived
    # by aggregating the prior tape's pool balance.
    prev_pool_balance: float | None = None
    if len(tapes) >= 2:
        prev = aggregator.execute(
            CollectionsInput(
                tape_file_url=tapes[-2]["url"],
                reporting_period=tapes[-2]["date"],
                class_a_rate_pct=_GREEN_LION_CLASS_A_RATE_PCT,
                class_a_balance=_GREEN_LION_CLASS_A_BALANCE,
                class_b_balance=_GREEN_LION_CLASS_B_BALANCE,
                class_c_balance=_GREEN_LION_CLASS_C_BALANCE,
            )
        ).output
        prev_pool_balance = prev.pool_balance_eur

    collections = aggregator.execute(
        CollectionsInput(
            tape_file_url=latest["url"],
            reporting_period=period,
            prev_pool_balance=prev_pool_balance,
            class_a_rate_pct=_GREEN_LION_CLASS_A_RATE_PCT,
            class_a_balance=_GREEN_LION_CLASS_A_BALANCE,
            class_b_balance=_GREEN_LION_CLASS_B_BALANCE,
            class_c_balance=_GREEN_LION_CLASS_C_BALANCE,
        )
    ).output

    waterfall = WaterfallRunner().execute(
        WaterfallInput(
            reporting_period=period,
            available_revenue_funds=collections.available_revenue_funds,
            available_principal_funds=collections.available_principal_funds,
            senior_fees=collections.senior_fees,
            swap_payment=0.0,
            class_a_balance=_GREEN_LION_CLASS_A_BALANCE,
            class_a_rate_pct=_GREEN_LION_CLASS_A_RATE_PCT,
            class_b_balance=_GREEN_LION_CLASS_B_BALANCE,
            class_c_balance=_GREEN_LION_CLASS_C_BALANCE,
            reserve_account_balance=0.0,
            reserve_account_target=0.0,
            class_a_pdl_balance=0.0,
            class_b_pdl_balance=0.0,
        )
    ).output

    return WaterfallResponse(
        deal_id=deal_id,
        reporting_period=waterfall.reporting_period,
        available_revenue_funds=collections.available_revenue_funds,
        available_principal_funds=collections.available_principal_funds,
        revenue_waterfall=[
            WaterfallStepModel(
                priority=step.priority,
                recipient=step.recipient,
                amount_available=step.amount_available,
                amount_distributed=step.amount_distributed,
                shortfall=step.shortfall,
                condition=step.condition,
            )
            for step in waterfall.revenue_waterfall
        ],
        tranche_distributions=[
            TrancheDistributionModel(
                tranche=t.tranche,
                interest_received=t.interest_received,
                principal_received=t.principal_received,
                total_received=t.total_received,
                opening_balance=t.opening_balance,
                closing_balance=t.closing_balance,
            )
            for t in waterfall.tranche_distributions
        ],
        total_distributed=waterfall.total_distributed,
        shortfall=waterfall.shortfall,
    )


@app.post("/deal/{deal_id}/project")
def deal_project(deal_id: str, req: ProjectRequest) -> dict:
    """Project forward payment waterfalls under the requested scenarios.

    For each scenario, runs the deal's payment waterfall on the base-case
    capital structure with the scenario's collection stress factor applied,
    returning the per-tranche distributions and any shortfall.
    """
    _require_deal(deal_id)
    runner = WaterfallRunner()

    base = _GREEN_LION_PROJECTION_BASE
    # Assume collections roughly track the pool balance over the horizon; this
    # is the base-case revenue/principal split a dedicated projector would
    # refine per period.
    base_revenue = base["current_pool_balance"] * 0.04 * (req.months / 12.0)
    base_principal = base["current_pool_balance"] * 0.10 * (req.months / 12.0)

    projections = {}
    for scenario in req.scenarios:
        factor = _SCENARIO_COLLECTION_FACTORS.get(scenario, 1.0)
        result = runner.execute(
            WaterfallInput(
                reporting_period=f"projection+{req.months}m ({scenario})",
                available_revenue_funds=base_revenue * factor,
                available_principal_funds=base_principal * factor,
                senior_fees=base["current_pool_balance"] * 0.0005,
                swap_payment=0.0,
                class_a_balance=base["class_a_balance"],
                class_a_rate_pct=base["class_a_rate_pct"],
                class_b_balance=base["class_b_balance"],
                class_c_balance=base["class_c_balance"],
                reserve_account_balance=base["reserve_account_balance"],
                reserve_account_target=base["reserve_account_target"],
                class_a_pdl_balance=0.0,
                class_b_pdl_balance=0.0,
            )
        )
        projections[scenario] = result.output.model_dump()

    return {
        "deal_id": deal_id,
        "months": req.months,
        "scenarios": req.scenarios,
        "projections": projections,
    }
