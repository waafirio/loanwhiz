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
