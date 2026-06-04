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

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from loanwhiz.agent.executor import execute_query
from loanwhiz.config import DEAL_REGISTRY
from loanwhiz.extraction.assembler import (
    DEFAULT_DEAL_CACHE_DIR,
    DealModel,
    _slug,
)
from loanwhiz.governance import EvidencePackLogger
from loanwhiz.primitives.collections_aggregator import (
    CollectionsAggregator,
    CollectionsInput,
)
from loanwhiz.primitives.base import Citation
from loanwhiz.primitives.covenant_monitor import (
    CovenantInput,
    CovenantMonitor,
    TriggerDefinition,
)
from loanwhiz.primitives.esma_tape_normaliser import (
    EsmaTapeInput,
    EsmaTapeNormaliser,
)
from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY
from loanwhiz.primitives.waterfall_runner import WaterfallInput, WaterfallRunner

# Import every primitive module so its @register_primitive decorator runs and the
# PRIMITIVE_REGISTRY is fully populated for GET /primitives. Primitives register
# on import; the four imported above (collections_aggregator, covenant_monitor,
# esma_tape_normaliser, waterfall_runner) are already covered, so this pulls in
# the rest (audit_logger, cashflow_projector, report_verifier, waterfall_state).
# Imported for the registration side effect only — hence the noqa.
from loanwhiz.primitives import (  # noqa: F401  (registration side effects)
    audit_logger,
    cashflow_projector,
    report_verifier,
    waterfall_state,
)

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

# Registry of known deals, sourced from the config-driven DEAL_REGISTRY. The
# key is the canonical deal id clients use in the /deal/{deal_id}/... routes.
# Adding a deal is data (config / data/deals.json), not code here — see
# loanwhiz.config.DEAL_REGISTRY. Green Lion is the first registered deal.
DEALS: dict[str, dict] = DEAL_REGISTRY

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


# Directory where ``loanwhiz.extraction.assembler.extract_deal_model`` caches
# extracted deal models. The ``/deal/{id}/model`` endpoint reads from here but
# never triggers a cold extraction (that path is ~10min via Docling) — it only
# serves a cache hit and otherwise degrades gracefully. Sourced from the
# assembler's durable default (#132 moved it from /tmp to the committed
# data/deals/) so the writer and this reader never diverge.
DEAL_MODEL_CACHE_DIR = str(DEFAULT_DEAL_CACHE_DIR)


def _load_cached_deal_model(deal: dict) -> DealModel | None:
    """Read the cached extracted :class:`DealModel` for a deal, or ``None``.

    Reads the assembler's on-disk cache at
    ``{DEAL_MODEL_CACHE_DIR}/{slug(deal_name)}.json`` and validates it into a
    :class:`DealModel`. **Never triggers a cold extraction** — a cache miss
    (no file) returns ``None`` rather than invoking the ~10min Docling
    pipeline. Shared by ``/deal/{id}/model`` (serves it to the frontend) and
    ``/deal/{id}/compliance`` (feeds the deal's own triggers to the monitor)
    so both read the cache identically.
    """
    cache_path = Path(DEAL_MODEL_CACHE_DIR) / f"{_slug(deal['deal_name'])}.json"
    if not cache_path.exists():
        return None
    return DealModel.model_validate_json(cache_path.read_text(encoding="utf-8"))


class DealModelResponse(BaseModel):
    """Response body for ``GET /deal/{deal_id}/model``.

    Carries the deal *config* (name + document URLs the frontend already uses)
    alongside the *extracted* :class:`~loanwhiz.extraction.assembler.DealModel`
    when it has been cached. The extracted model (tranches, triggers,
    waterfalls, completeness, metadata) is the full ``DealModel.model_dump()``
    nested under ``deal_model`` — kept as a free-form dict so the assembler's
    schema is the single source of truth and need not be restated here.

    When the cache is cold/missing the endpoint does **not** block on a cold
    extraction: it returns the config with ``deal_model=None`` and
    ``extraction_status="not_cached"`` so the frontend can render what it has
    and surface the extraction state.
    """

    # Deal config (unchanged from the GREEN_LION context — what the frontend
    # already consumes; kept so nothing breaks).
    deal_name: str
    prospectus_url: str
    tape_urls: list[dict]
    investor_report_urls: list[dict]

    # Extraction state + the cached extracted model (if any).
    extraction_status: str          # "cached" | "not_cached"
    completeness_score: float | None = None
    trigger_names: list[str] | None = None
    deal_model: dict | None = None   # full DealModel.model_dump() on cache hit


class ScenarioWal(BaseModel):
    """Class A weighted-average life (WAL) for one projected scenario.

    Surfaced per scenario in the ``POST /deal/{deal_id}/project`` response
    (additively — the existing waterfall projection fields are left intact).

    Attributes:
        wal_class_a_months:  Class A weighted-average life in months, computed as
                             ``sum(t × principal_t) / sum(principal_t)`` over the
                             projection horizon. ``0.0`` when no Class A principal
                             is returned.
        wal_class_a_years:   ``wal_class_a_months / 12`` for convenience.
    """

    wal_class_a_months: float
    wal_class_a_years: float


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


# --- deal registry listing (#131) --------------------------------------------
# Self-contained block (response model + handler) for the deal-registry listing.
# Lets the frontend deal selector (#134) populate from the config-driven
# registry. Kept contiguous to minimise conflicts with the sibling issues
# (#130 / #135 / #136) editing this same module in parallel.


class DealSummary(BaseModel):
    """One available deal — id + display name — for ``GET /deals``."""

    id: str
    name: str


@app.get("/deals", response_model=list[DealSummary])
def list_deals() -> list[DealSummary]:
    """List the available deals (id + name) from the config-driven registry.

    Sourced from :data:`DEALS` (``loanwhiz.config.DEAL_REGISTRY``), so a deal
    added as data — not code — surfaces here automatically. The frontend deal
    selector uses this to populate; ``id`` is the value to pass to the
    ``/deal/{deal_id}/...`` routes.
    """
    return [
        DealSummary(id=deal_id, name=deal["deal_name"])
        for deal_id, deal in DEALS.items()
    ]


# --- end deal registry listing (#131) ----------------------------------------


@app.get("/deal/{deal_id}/model", response_model=DealModelResponse)
def deal_model(deal_id: str) -> DealModelResponse:
    """Return the deal config plus the cached extracted DealModel.

    Serves the extracted model (tranches, triggers, waterfalls, completeness,
    metadata) from the assembler's on-disk cache when present. **Never triggers
    a cold extraction** — that runs Docling (~10min) — so on a cache miss the
    endpoint returns the config with ``deal_model=None`` and
    ``extraction_status="not_cached"`` rather than blocking the request.
    """
    deal = _require_deal(deal_id)

    base = DealModelResponse(
        deal_name=deal["deal_name"],
        prospectus_url=deal["prospectus_url"],
        tape_urls=deal["tape_urls"],
        investor_report_urls=deal["investor_report_urls"],
        extraction_status="not_cached",
    )

    # Read the cache directly (do NOT call extract_deal_model — a cache miss
    # there would synchronously run the ~10min Docling pipeline).
    model = _load_cached_deal_model(deal)
    if model is None:
        return base

    base.extraction_status = "cached"
    base.completeness_score = model.metadata.completeness_score
    base.trigger_names = model.trigger_names
    base.deal_model = model.model_dump()
    return base


def _map_extracted_trigger(raw: dict) -> TriggerDefinition:
    """Map one extracted-trigger dict onto a covenant_monitor ``TriggerDefinition``.

    ``raw`` is an ``ExtractedTrigger.model_dump()`` (from the cached deal
    model's ``covenants.triggers``) carrying ``name, display_name, description,
    metric, threshold, threshold_unit, direction, consequence,
    section_reference, citation``.

    Schema bridge:
    - ``name / metric / threshold / description / consequence`` pass through.
    - ``direction``: the extractor's ``"non_zero"`` (any positive debit balance
      fires, e.g. a PDL) maps to ``direction="above"`` with ``threshold=None`` —
      the convention ``covenant_monitor`` already uses (``threshold is None`` →
      any positive value triggers). ``"above"`` / ``"below"`` pass through.
    - ``threshold_unit`` has no slot on ``TriggerDefinition`` (the monitor
      reasons numerically from metric + threshold + direction only); it is
      intentionally not forwarded.
    - ``citation`` is a free-form dict in the extracted schema; rebuild a
      :class:`Citation`, falling back to the trigger's ``section_reference`` /
      ``display_name`` when individual keys are absent.
    """
    direction = raw.get("direction", "above")
    threshold = raw.get("threshold")
    if direction == "non_zero":
        direction = "above"
        threshold = None  # any positive (debit) balance fires the trigger

    citation_raw = raw.get("citation") or {}
    citation = Citation(
        document=citation_raw.get("document") or "prospectus",
        page_or_row=citation_raw.get("page_or_row") or raw.get("section_reference"),
        excerpt=citation_raw.get("excerpt") or raw.get("display_name") or raw["name"],
    )

    return TriggerDefinition(
        name=raw["name"],
        description=raw.get("description") or raw.get("display_name") or raw["name"],
        metric=raw["metric"],
        threshold=threshold,
        direction=direction,
        consequence=raw.get("consequence", ""),
        citation=citation,
    )


def _extracted_triggers_to_definitions(deal: dict) -> list[TriggerDefinition]:
    """Return the deal's extracted triggers as ``TriggerDefinition`` objects.

    Reads the cached deal model (never a live extraction) and maps each
    ``covenants.triggers`` entry onto a ``TriggerDefinition``. Returns an empty
    list when the deal has no cached model or its model carries no triggers —
    the caller then falls back to ``CovenantMonitor.DEFAULT_TRIGGERS``.
    """
    model = _load_cached_deal_model(deal)
    if model is None:
        return []
    raw_triggers = model.covenants.get("triggers") or []
    return [_map_extracted_trigger(raw) for raw in raw_triggers]


@app.get("/deal/{deal_id}/compliance")
def deal_compliance(deal_id: str) -> dict:
    """Run covenant compliance across all reporting periods for the deal.

    Normalises every ESMA tape the deal references, then runs the covenant
    monitor over the per-period pool analytics. The trigger set comes from the
    deal model's *extracted* triggers (the cached ``covenants.triggers``,
    mapped onto the monitor's schema) so each deal is checked against its own
    thresholds and directions; it falls back to the monitor's hardcoded
    ``DEFAULT_TRIGGERS`` only when the deal has no cached model or no extracted
    triggers (Green Lion's extracted triggers match the defaults, so its
    behaviour is unchanged). Reading the cache never triggers a live
    extraction.
    """
    deal = _require_deal(deal_id)
    normaliser = EsmaTapeNormaliser()
    periods = [
        normaliser.execute(EsmaTapeInput(file_url=tape["url"])).output.model_dump()
        for tape in deal["tape_urls"]
    ]
    triggers = _extracted_triggers_to_definitions(deal) or CovenantMonitor.DEFAULT_TRIGGERS
    monitor = CovenantMonitor()
    result = monitor.execute(CovenantInput(periods=periods, triggers=triggers))
    return result.output.model_dump()


# ---------------------------------------------------------------------------
# Waterfall endpoint (CollectionsAggregator -> WaterfallRunner, latest period)
#
# Self-contained block: the response models + handler for
# GET /deal/{deal_id}/waterfall live together here so the route can be reviewed
# (and merged) as one unit. It mirrors the demo runner (demo/run_green_lion.py):
# aggregate the latest reported tape into Available Revenue / Principal Funds,
# then run the Green Lion Revenue + Redemption Priority of Payments.
# Deterministic (no LLM), but it fetches the tape CSV.
# ---------------------------------------------------------------------------

# Green Lion 2026-1 capital structure (prospectus section 5; also the
# primitives' own defaults). Restated here so the endpoint is explicit about
# the structure it runs the waterfall against.
_GREEN_LION_CLASS_A_BALANCE = 1_000_000_000.0
_GREEN_LION_CLASS_A_RATE_PCT = 3.62
_GREEN_LION_CLASS_B_BALANCE = 53_100_000.0
_GREEN_LION_CLASS_C_BALANCE = 10_500_000.0

# Default capital structure for a deal whose registry context does not carry its
# own. The deal-context dict (loanwhiz.config.DEAL_REGISTRY entries) may include
# an optional ``capital_structure`` key with these four fields; ``deal_waterfall``
# resolves it from the deal and falls back to this Green Lion default so the
# route is deal-generic without a registry-schema migration. Green Lion (no
# ``capital_structure`` key) is unchanged.
_GREEN_LION_CAPITAL_STRUCTURE = {
    "class_a_balance": _GREEN_LION_CLASS_A_BALANCE,
    "class_a_rate_pct": _GREEN_LION_CLASS_A_RATE_PCT,
    "class_b_balance": _GREEN_LION_CLASS_B_BALANCE,
    "class_c_balance": _GREEN_LION_CLASS_C_BALANCE,
}


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
    path, then runs the Revenue + Redemption Priority of Payments
    (``WaterfallRunner``) on the deal's capital structure. Both the tapes and
    the capital structure are resolved from the deal context (mirroring
    ``/compliance`` and ``/tape-analytics``): a deal may carry its own
    ``capital_structure`` in the registry, otherwise the Green Lion default
    applies. Returns the 11-step revenue cascade and the per-tranche
    distributions.
    """
    deal = _require_deal(deal_id)

    tapes = deal["tape_urls"]
    latest = tapes[-1]
    period = latest["date"]

    # Capital structure from the deal context, defaulting to Green Lion's when
    # the deal carries none — keeps the route deal-generic without a registry
    # schema migration.
    cap = deal.get("capital_structure", _GREEN_LION_CAPITAL_STRUCTURE)

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
                class_a_rate_pct=cap["class_a_rate_pct"],
                class_a_balance=cap["class_a_balance"],
                class_b_balance=cap["class_b_balance"],
                class_c_balance=cap["class_c_balance"],
            )
        ).output
        prev_pool_balance = prev.pool_balance_eur

    collections = aggregator.execute(
        CollectionsInput(
            tape_file_url=latest["url"],
            reporting_period=period,
            prev_pool_balance=prev_pool_balance,
            class_a_rate_pct=cap["class_a_rate_pct"],
            class_a_balance=cap["class_a_balance"],
            class_b_balance=cap["class_b_balance"],
            class_c_balance=cap["class_c_balance"],
        )
    ).output

    waterfall = WaterfallRunner().execute(
        WaterfallInput(
            reporting_period=period,
            available_revenue_funds=collections.available_revenue_funds,
            available_principal_funds=collections.available_principal_funds,
            senior_fees=collections.senior_fees,
            swap_payment=0.0,
            class_a_balance=cap["class_a_balance"],
            class_a_rate_pct=cap["class_a_rate_pct"],
            class_b_balance=cap["class_b_balance"],
            class_c_balance=cap["class_c_balance"],
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


# --- tape-analytics (#110) ---------------------------------------------------
# Self-contained block (response model + handler) for the per-period pool
# analytics endpoint. Kept contiguous to minimise conflicts with the sibling
# issues (#109/#111/#112) editing this same module in parallel.
#
# Caching (#130, Part of #128 — scale-readiness)
# ----------------------------------------------
# Normalising every ESMA tape on every request fetches + parses each CSV anew;
# with ~48 monthly tapes a single ``/tape-analytics`` call would take 30-90s
# and pay that cost on every refresh. Each tape's normalised analytics is
# deterministic and keyed by its (content-stable) URL, so we cache it in two
# layers:
#
#   1. In-process memo (``_TAPE_ANALYTICS_MEMO``) — instant repeat reads within
#      a running process.
#   2. On-disk JSON under ``TAPE_ANALYTICS_CACHE_DIR`` — survives restarts.
#
# Both are keyed by the tape URL (a new reporting period is a new URL, never a
# mutated file, so URL-keying never serves stale data). The on-disk dir is
# deliberately distinct from the assembler's extraction cache
# (``DEAL_MODEL_CACHE_DIR`` / ``/tmp/loanwhiz_cache/deals``, reworked under
# #132) so the two cache stories don't collide.
#
# Invalidation: delete ``TAPE_ANALYTICS_CACHE_DIR`` (and restart, or clear
# ``_TAPE_ANALYTICS_MEMO``) — there is no per-tape expiry because the keyed
# URLs are immutable.

# On-disk cache directory for normalised tape analytics. Distinct from
# DEAL_MODEL_CACHE_DIR so this cache never collides with the extraction
# artifact cache (#132). Module-level so tests can patch it at a tmp_path.
TAPE_ANALYTICS_CACHE_DIR = "/tmp/loanwhiz_cache/tape_analytics"

# In-process memo: tape URL -> EsmaTapeOutput.model_dump() dict. Module-level
# so it persists across requests within a process; tests clear it for
# determinism.
_TAPE_ANALYTICS_MEMO: dict[str, dict] = {}


def _tape_cache_path(url: str) -> Path:
    """On-disk cache path for a tape URL.

    The URL is the cache key; we hash it to a filesystem-safe filename rather
    than embedding the raw URL.
    """
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return Path(TAPE_ANALYTICS_CACHE_DIR) / f"{digest}.json"


def _normalised_tape_output(url: str) -> dict:
    """Return the normalised ``EsmaTapeOutput`` dict for a tape URL, cached.

    Checks the in-process memo, then the on-disk JSON cache, and only on a miss
    runs the (network-fetching, CPU-heavy) :class:`EsmaTapeNormaliser`. A miss
    populates both layers so the analytics for any given tape is computed at
    most once. The returned dict is the unchanged ``EsmaTapeOutput.model_dump()``
    shape — callers spread it into ``TapeAnalyticsPeriod`` as before.
    """
    memo_hit = _TAPE_ANALYTICS_MEMO.get(url)
    if memo_hit is not None:
        return memo_hit

    cache_path = _tape_cache_path(url)
    if cache_path.exists():
        output = json.loads(cache_path.read_text(encoding="utf-8"))
        _TAPE_ANALYTICS_MEMO[url] = output
        return output

    output = EsmaTapeNormaliser().execute(EsmaTapeInput(file_url=url)).output.model_dump()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(output), encoding="utf-8")
    _TAPE_ANALYTICS_MEMO[url] = output
    return output


class TapeAnalyticsPeriod(BaseModel):
    """Per-period pool analytics for one ESMA tape, returned by tape-analytics.

    Mirrors :class:`~loanwhiz.primitives.esma_tape_normaliser.EsmaTapeOutput`
    (balance, loan count, weighted pool stats, arrears, and the EPC /
    geographic / property-type breakdowns), with the deal's reporting-period
    date the tape was registered under for chronological context.
    """

    tape_date: str
    reporting_date: str
    asset_class: str
    transaction_name: str | None
    loan_count: int
    pool_balance_eur: float
    pool_stats: dict[str, float]
    arrears_breakdown: dict[str, float]
    epc_breakdown: dict[str, float] | None
    rate_type_breakdown: dict[str, float] | None
    property_type_breakdown: dict[str, float] | None
    geographic_breakdown: dict[str, float] | None
    annex_detected: str


@app.get("/deal/{deal_id}/tape-analytics", response_model=list[TapeAnalyticsPeriod])
def deal_tape_analytics(deal_id: str) -> list[TapeAnalyticsPeriod]:
    """Return per-period pool analytics across the deal's ESMA tapes.

    Normalises every ESMA tape the deal references (deterministic, no LLM) and
    returns one analytics object per reporting period in chronological order —
    pool balance, loan count, arrears, weighted LTV, and the EPC / geographic /
    property-type breakdowns.

    Per-tape analytics is served from a keyed cache (in-process memo + on-disk
    JSON, keyed by tape URL) so a given tape is normalised at most once; see the
    caching note at the top of this block.
    """
    deal = _require_deal(deal_id)
    return [
        TapeAnalyticsPeriod(
            tape_date=tape["date"],
            **_normalised_tape_output(tape["url"]),
        )
        for tape in deal["tape_urls"]
    ]


# --- end tape-analytics (#110) -----------------------------------------------


# --- primitive registry catalogue (#135) -------------------------------------
# Self-contained block (response model + handler) for GET /primitives — the
# framework's primitive registry catalogue, so the UI (#137) can render every
# registered primitive: name, version, description, tags, author, and the typed
# input/output JSON schemas. Kept contiguous to minimise conflicts with the
# sibling issues editing this module in parallel (#136).
#
# Sourcing: PRIMITIVE_REGISTRY.describe() yields the registry metadata
# (name/version/description/author/tags/class_name); the primitive class's own
# describe() classmethod yields the Pydantic input/output JSON schemas. The two
# are merged per entry. All primitive modules are imported at the top of this
# file so the registry is fully populated (primitives register on import).


class PrimitiveCatalogueEntry(BaseModel):
    """One primitive in the registry catalogue returned by ``GET /primitives``.

    Combines the registry metadata (name, version, description, author, tags,
    implementing class name) with the primitive's typed I/O contract — the
    Pydantic JSON schemas for its input and output models — plus a note on the
    framework's confidence semantics (every primitive returns a
    ``PrimitiveResult`` with a ``confidence`` score in ``[0.0, 1.0]``: ``1.0``
    for deterministic/rule-based primitives, lower when model or data-quality
    uncertainty applies).
    """

    name: str = Field(..., description="Unique snake_case primitive identifier.")
    version: str = Field(..., description="Semver version string.")
    description: str = Field(..., description="One-line human-readable description.")
    author: str = Field(..., description="Author/team identifier.")
    tags: list[str] = Field(
        default_factory=list, description="Tags for grouping/filtering."
    )
    class_name: str = Field(..., description="Qualified name of the implementing class.")
    input_schema: dict[str, Any] = Field(
        default_factory=dict, description="JSON Schema for the primitive's input model."
    )
    output_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for the primitive's output model.",
    )
    confidence: str = Field(
        default=(
            "Every primitive returns a PrimitiveResult with a confidence score in "
            "[0.0, 1.0]: 1.0 for deterministic/rule-based computation, lower when "
            "model or data-quality uncertainty applies."
        ),
        description="Framework confidence semantics for the primitive's result.",
    )


@app.get("/primitives", response_model=list[PrimitiveCatalogueEntry])
def primitives() -> list[PrimitiveCatalogueEntry]:
    """Return the primitive registry catalogue.

    Lists every registered SF primitive with its registry metadata
    (name/version/description/author/tags/class_name) and its typed input/output
    JSON schemas, so the UI can render the framework's primitives. All primitive
    modules are imported at module load so the registry is complete.
    """
    catalogue = PRIMITIVE_REGISTRY.describe()
    entries: list[PrimitiveCatalogueEntry] = []
    for name, meta in catalogue.items():
        registration = PRIMITIVE_REGISTRY.get(name)
        input_schema: dict[str, Any] = {}
        output_schema: dict[str, Any] = {}
        if registration is not None:
            described = registration.primitive_class.describe()
            input_schema = described.input_schema
            output_schema = described.output_schema
        entries.append(
            PrimitiveCatalogueEntry(
                name=meta["name"],
                version=meta["version"],
                description=meta["description"],
                author=meta["author"],
                tags=meta["tags"],
                class_name=meta["class_name"],
                input_schema=input_schema,
                output_schema=output_schema,
            )
        )
    return entries


# --- end primitive registry catalogue (#135) ---------------------------------


@app.post("/deal/{deal_id}/project")
def deal_project(deal_id: str, req: ProjectRequest) -> dict:
    """Project forward payment waterfalls under the requested scenarios.

    For each scenario, runs the deal's payment waterfall on the base-case
    capital structure with the scenario's collection stress factor applied,
    returning the per-tranche distributions and any shortfall.

    The projection base (pool balance + capital structure) is resolved from
    the deal context — a deal may carry its own ``projection_base`` in the
    registry, otherwise the Green Lion default applies. This mirrors the
    deal-context resolution of ``/waterfall`` (#151) so projections track the
    *selected* deal rather than always Green Lion; Green Lion (no
    ``projection_base`` key) is unchanged.
    """
    deal = _require_deal(deal_id)
    runner = WaterfallRunner()

    # Projection base from the deal context, defaulting to Green Lion's when
    # the deal carries none — keeps the route deal-generic without a registry
    # schema migration (mirrors the /waterfall capital-structure resolution).
    base = deal.get("projection_base", _GREEN_LION_PROJECTION_BASE)
    # Assume collections roughly track the pool balance over the horizon; this
    # is the base-case revenue/principal split a dedicated projector would
    # refine per period.
    base_revenue = base["current_pool_balance"] * 0.04 * (req.months / 12.0)
    base_principal = base["current_pool_balance"] * 0.10 * (req.months / 12.0)

    projections = {}
    wal = {}
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
        projection = result.output.model_dump()

        # Class A weighted-average life for the scenario. WAL is
        # sum(t × principal_t) / sum(principal_t); over this single-period
        # horizon the one Class A principal distribution lands at month
        # ``req.months``, so a positive Class A principal yields a WAL of the
        # full horizon and zero principal yields 0.0 (avoids divide-by-zero).
        class_a_principal = sum(
            dist.get("principal_received", 0.0)
            for dist in projection.get("tranche_distributions", [])
            if dist.get("tranche") == "class_a"
        )
        wal_months = float(req.months) if class_a_principal > 0.0 else 0.0
        scenario_wal = ScenarioWal(
            wal_class_a_months=wal_months,
            wal_class_a_years=wal_months / 12.0,
        )

        # Surface WAL additively on the per-scenario projection (existing
        # waterfall fields are untouched) and in a top-level per-scenario map.
        projection["wal_class_a_months"] = scenario_wal.wal_class_a_months
        projection["wal_class_a_years"] = scenario_wal.wal_class_a_years
        projections[scenario] = projection
        wal[scenario] = scenario_wal.model_dump()

    return {
        "deal_id": deal_id,
        "months": req.months,
        "scenarios": req.scenarios,
        "projections": projections,
        "wal": wal,
    }


# ---------------------------------------------------------------------------
# Governance evidence pack (#136)
#
# Self-contained block (response models + handler) for the auditable-agents
# surface: GET /governance/{pack_id} returns a stored GovernanceEvidencePack —
# the agent's tool-call trace, per-tool/aggregate confidence, deduplicated
# citation trail, and human-review flag — so the UI (#138) can render the
# evidence behind a query. Read-only over the EvidencePackLogger the planner
# already writes to via run_query(..., save_evidence=True). Kept contiguous to
# minimise additive merge conflicts with siblings editing this module.
# ---------------------------------------------------------------------------

# Root directory the EvidencePackLogger persists packs under. Mirrors the
# logger's own default log_dir (loanwhiz.governance.evidence_pack), so this
# endpoint reads from the same JSONL store the planner writes to. Patchable for
# tests (same pattern as DEAL_MODEL_CACHE_DIR) so the suite can seed a pack into
# a temp dir without running an agent query.
GOVERNANCE_LOG_DIR = "/tmp/loanwhiz_governance"


class ToolCallRecordModel(BaseModel):
    """One agent tool call within a query (mirrors ``ToolCallRecord``)."""

    call_index: int
    tool_name: str
    input_summary: str
    output_summary: str
    confidence: float
    citations: list[dict]
    duration_ms: float
    timestamp: str


class GovernanceEvidencePackResponse(BaseModel):
    """Response body for ``GET /governance/{pack_id}``.

    Mirrors the serialisable shape of
    :class:`~loanwhiz.governance.evidence_pack.GovernanceEvidencePack` — the
    audit trail (query/answer/timestamp + ordered tool-call records), the
    per-tool and aggregate confidence, the deduplicated citation trail, the
    human-review flag, and the governance metadata.
    """

    pack_id: str
    query: str
    answer: str
    timestamp: str

    tool_calls: list[ToolCallRecordModel]
    aggregate_confidence: float
    all_citations: list[dict]
    human_review_required: bool

    model_used: str
    framework_version: str
    finos_compliant: bool


@app.get("/governance/{pack_id}", response_model=GovernanceEvidencePackResponse)
def governance_pack(pack_id: str) -> GovernanceEvidencePackResponse:
    """Return the stored governance evidence pack for ``pack_id``.

    Loads the pack from the on-disk ``EvidencePackLogger`` store (the same
    store the planner writes to when a query runs with ``save_evidence=True``)
    and returns its full serialisable shape. Raises 404 when no pack with that
    id exists.
    """
    logger = EvidencePackLogger(log_dir=GOVERNANCE_LOG_DIR)
    pack = logger.load(pack_id)
    if pack is None:
        raise HTTPException(
            status_code=404, detail=f"Evidence pack {pack_id} not found"
        )
    return GovernanceEvidencePackResponse(**pack.model_dump())
