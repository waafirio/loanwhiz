"""Cross-deal relative-value / spread screener (#324).

Ranks **tranches across deals** by *structural relative value* and folds the
result into a single comparable scorecard. The four relative-value dimensions
the issue names are:

- **subordination / credit enhancement (CE)** — how much junior capital sits
  below a tranche, as a fraction of the deal;
- **WAL** (weighted-average life);
- **trigger headroom** — protective-trigger coverage / distance-to-breach;
- **pool quality**.

Design — mirrors the capability-matrix pattern
-----------------------------------------------
This is the quantitative sibling of the qualitative deal-comparison tool
(#283). Like :func:`loanwhiz.primitives.capability_matrix.build_capability_matrix`
it is a **dependency-injected, offline, deterministic** cross-deal builder:
it iterates the deal registry, loads each deal's *committed seed*
:class:`~loanwhiz.extraction.assembler.DealModel` via an injected
``seed_loader`` (which never triggers a cold extraction), and emits typed,
JSON-serialisable rows. No loan tape is fetched and no engine is run in the
request path, so the screener is fast and unit-testable.

Honesty over a wall of green
----------------------------
The committed seed model carries *structural* data only — ``tranche_structure``
(size / rating / seniority) and ``covenants.triggers`` (often with qualitative,
``None`` thresholds). It does **not** carry live per-period pool analytics or a
current state series; those require fetching ESMA tapes / investor reports.
So each relative-value dimension reports an honest :class:`RvFactor` that is
either:

- **available** — computed from real structural inputs (e.g. subordination/CE
  from tranche sizes), or
- **unavailable** — the live numeric version (true WAL from amortisation, live
  trigger headroom = current metric vs threshold, true pool quality from the
  tape) is surfaced with ``available=False`` and a real ``reason`` rather than
  fabricated.

A composite score blends only the *available* dimensions (re-normalising their
weights), and tranches are ranked cross-deal by that composite. This mirrors
the matrix's "validated / ran / not-applicable" honesty contract.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field

from loanwhiz.extraction.assembler import DealModel
from loanwhiz.primitives.base import (
    AuditEntry,
    BaseInput,
    Citation,
    Primitive,
    PrimitiveResult,
)
from loanwhiz.primitives.registry import register_primitive

# ---------------------------------------------------------------------------
# Dimension vocabulary.
# ---------------------------------------------------------------------------

DIM_SUBORDINATION_CE = "subordination_ce"
DIM_WAL = "wal"
DIM_TRIGGER_HEADROOM = "trigger_headroom"
DIM_POOL_QUALITY = "pool_quality"

#: The four relative-value dimensions, in scorecard order.
DIMENSIONS: tuple[str, ...] = (
    DIM_SUBORDINATION_CE,
    DIM_WAL,
    DIM_TRIGGER_HEADROOM,
    DIM_POOL_QUALITY,
)

#: Default composite weights (sum to 1.0). Subordination/CE dominates because it
#: is the load-bearing structural protection and is always available offline;
#: the others are lighter signals. The composite re-normalises over whichever
#: dimensions are actually available for a given cohort, so these are relative.
DEFAULT_WEIGHTS: dict[str, float] = {
    DIM_SUBORDINATION_CE: 0.45,
    DIM_WAL: 0.15,
    DIM_TRIGGER_HEADROOM: 0.25,
    DIM_POOL_QUALITY: 0.15,
}

#: Provenance bases for a factor — whether its value came from structural
#: (offline seed) data or requires live period data.
BASIS_STRUCTURAL = "structural"
BASIS_LIVE_REQUIRED = "live-required"


# ---------------------------------------------------------------------------
# Typed result models.
# ---------------------------------------------------------------------------


class RvFactor(BaseModel):
    """One relative-value dimension for one tranche.

    ``available`` is the honesty gate: when ``False`` the dimension's live
    numeric form could not be computed offline (``value``/``score`` are
    ``None``) and ``reason`` explains why — it is never fabricated. When
    ``True``, ``value`` is the raw structural measurement and ``score`` is the
    cross-cohort-normalised sub-score in ``[0, 100]`` (higher = better relative
    value), set by the builder after it sees the whole cohort.
    """

    dimension: str = Field(..., description="One of DIMENSIONS.")
    available: bool = Field(..., description="True when computed; False when live-only / absent.")
    value: float | None = Field(
        default=None, description="Raw structural measurement, or None when unavailable."
    )
    score: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Cross-cohort-normalised sub-score in [0,100], or None when unavailable.",
    )
    basis: str = Field(
        ..., description=f"Provenance: {BASIS_STRUCTURAL!r} or {BASIS_LIVE_REQUIRED!r}."
    )
    reason: str = Field(..., description="Why available / why not — always non-empty.")


class TrancheScore(BaseModel):
    """One (deal, tranche) row of the cross-deal scorecard."""

    deal_id: str = Field(..., description="Canonical deal id.")
    deal_name: str = Field(..., description="Human deal name.")
    tranche_name: str = Field(..., description='Note class, e.g. "Class A".')
    seniority: int | None = Field(default=None, description="0 = most senior; None if unknown.")
    rating: str | None = Field(default=None, description="Credit rating, if rated.")
    size_eur: float | None = Field(default=None, description="Issued balance at closing, if known.")
    factors: dict[str, RvFactor] = Field(
        ..., description="Per-dimension RvFactor, keyed by dimension name."
    )
    composite_score: float | None = Field(
        default=None,
        description="Weighted blend over AVAILABLE dimensions in [0,100], or None if none available.",
    )
    rank: int | None = Field(
        default=None, description="1-based cross-deal rank by composite (1 = best relative value)."
    )


class RelativeValueScorecard(BaseModel):
    """The full cross-deal relative-value scorecard — structured data for the UI."""

    dimensions: list[str] = Field(..., description="The relative-value dimensions, in order.")
    weights: dict[str, float] = Field(..., description="Composite weights used (sum 1.0).")
    tranches: list[TrancheScore] = Field(
        ..., description="One row per (deal, tranche), ranked best→worst by composite."
    )
    tally: dict[str, int] = Field(
        default_factory=dict,
        description="Counts: deals_screened, tranches_scored, per-dimension available counts.",
    )
    note: str = Field(
        default=(
            "Cross-deal relative-value screener. Each tranche is scored from its deal's "
            "committed extracted model (structural data only — tranche sizes, ratings, "
            "triggers). Dimensions that need live period data (true WAL, live trigger "
            "headroom, tape-derived pool quality) are reported available=false with a "
            "real reason rather than fabricated. Sub-scores are min-max normalised across "
            "the screened cohort into [0,100]; the composite blends only the available "
            "dimensions, re-normalising their weights. Honesty over a wall of green."
        ),
        description="Standing honesty disclosure for the scorecard.",
    )


# ---------------------------------------------------------------------------
# Tranche helpers — read the seed model's structural tranche table.
# ---------------------------------------------------------------------------


def _deal_tranches(model: DealModel | None) -> list[dict[str, Any]]:
    """Return the structural tranche list from a seed model, senior→junior.

    Tolerates a missing model or empty structure (low-completeness deals) by
    returning ``[]`` — the builder then emits no rows for that deal rather than
    raising.
    """
    if model is None:
        return []
    tranches = list(getattr(model, "tranche_structure", None) or [])
    # Order senior→junior by seniority when present; stable for ties / missing.
    return sorted(
        tranches,
        key=lambda t: (t.get("seniority") if isinstance(t.get("seniority"), int) else 1_000),
    )


def _total_deal_size(tranches: Sequence[Mapping[str, Any]]) -> float:
    """Sum of tranche ``size_eur`` (the issued capital structure), skipping missing."""
    return float(sum(float(t["size_eur"]) for t in tranches if _is_number(t.get("size_eur"))))


def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


# ---------------------------------------------------------------------------
# Dimension functions — each returns an RvFactor (score filled later by builder).
# ---------------------------------------------------------------------------


def factor_subordination_ce(
    tranche: Mapping[str, Any], all_tranches: Sequence[Mapping[str, Any]]
) -> RvFactor:
    """Credit enhancement = capital strictly junior to this tranche / total deal size.

    Purely structural: derived from the tranche table's ``seniority`` ordering
    and ``size_eur``. Always available when the deal carries a non-trivial sized
    capital structure. A senior tranche has more subordination below it (higher
    CE → better relative protection).
    """
    total = _total_deal_size(all_tranches)
    if total <= 0.0:
        return RvFactor(
            dimension=DIM_SUBORDINATION_CE,
            available=False,
            basis=BASIS_STRUCTURAL,
            reason="No sized tranche structure extracted — cannot compute credit enhancement.",
        )
    sen = tranche.get("seniority")
    if not isinstance(sen, int):
        return RvFactor(
            dimension=DIM_SUBORDINATION_CE,
            available=False,
            basis=BASIS_STRUCTURAL,
            reason="Tranche has no seniority rank — cannot place it in the capital structure.",
        )
    junior_balance = float(
        sum(
            float(t["size_eur"])
            for t in all_tranches
            if _is_number(t.get("size_eur"))
            and isinstance(t.get("seniority"), int)
            and int(t["seniority"]) > sen
        )
    )
    ce_fraction = junior_balance / total
    return RvFactor(
        dimension=DIM_SUBORDINATION_CE,
        available=True,
        value=ce_fraction,
        basis=BASIS_STRUCTURAL,
        reason=(
            f"{ce_fraction:.1%} of the deal sits junior to this tranche "
            "(structural credit enhancement)."
        ),
    )


def factor_wal(tranche: Mapping[str, Any], all_tranches: Sequence[Mapping[str, Any]]) -> RvFactor:
    """Weighted-average life.

    True WAL needs the amortisation profile / per-period principal — live data
    not present in the offline seed. Offline we expose a *structural ordering
    proxy*: under sequential redemption the most-senior tranche amortises first,
    so a lower seniority rank implies a shorter expected life. We surface this
    ordinal as the value and flag the dimension's true-WAL form unavailable.
    """
    sen = tranche.get("seniority")
    if not isinstance(sen, int):
        return RvFactor(
            dimension=DIM_WAL,
            available=False,
            basis=BASIS_LIVE_REQUIRED,
            reason=(
                "True WAL needs the amortisation profile (live period data); no seniority "
                "rank either, so even the structural ordering proxy is unavailable."
            ),
        )
    # Structural proxy: shorter expected life (senior pays down first) = better
    # relative value here. value is the seniority rank (lower = shorter life);
    # the builder inverts during normalisation so that shorter→higher score.
    return RvFactor(
        dimension=DIM_WAL,
        available=True,
        value=float(sen),
        basis=BASIS_STRUCTURAL,
        reason=(
            "Structural WAL proxy from seniority (senior amortises first under sequential "
            "pay). True WAL needs the amortisation profile (live period data)."
        ),
    )


def factor_trigger_headroom(
    tranche: Mapping[str, Any], model: DealModel | None
) -> RvFactor:
    """Trigger headroom.

    Live headroom is the distance between each protective trigger's *current*
    metric value and its threshold — that needs live tape / investor-report
    data. Offline we expose a structural *coverage* proxy: the count of
    extracted triggers and the fraction that are quantified
    (``threshold is not None``), as a signal of how much protective machinery
    guards the structure. More quantified triggers → richer protection.
    """
    triggers = _model_triggers(model)
    if not triggers:
        return RvFactor(
            dimension=DIM_TRIGGER_HEADROOM,
            available=False,
            basis=BASIS_LIVE_REQUIRED,
            reason=(
                "No triggers extracted for this deal — neither live headroom nor a "
                "structural coverage proxy is computable."
            ),
        )
    quantified = sum(1 for t in triggers if _is_number(t.get("threshold")))
    coverage = quantified / len(triggers)
    return RvFactor(
        dimension=DIM_TRIGGER_HEADROOM,
        available=True,
        value=coverage,
        basis=BASIS_STRUCTURAL,
        reason=(
            f"{quantified}/{len(triggers)} extracted triggers are quantified "
            "(structural protection-coverage proxy). Live numeric headroom needs current "
            "metric values vs thresholds (live tape/report)."
        ),
    )


def factor_pool_quality(model: DealModel | None) -> RvFactor:
    """Pool quality.

    True pool quality (WA-LTV, arrears, seasoning, geography) comes from the
    ESMA loan tape — live-only, not in the offline seed. Offline we expose a
    structural proxy from the extracted model's ``completeness_score`` (how much
    of the deal we could model), blended with trigger coverage. This is a proxy
    for *model confidence*, not collateral quality, and is flagged as such.
    """
    if model is None:
        return RvFactor(
            dimension=DIM_POOL_QUALITY,
            available=False,
            basis=BASIS_LIVE_REQUIRED,
            reason="No extracted model for this deal — pool quality needs the ESMA tape (live-only).",
        )
    completeness = getattr(model.metadata, "completeness_score", None)
    if not _is_number(completeness):
        return RvFactor(
            dimension=DIM_POOL_QUALITY,
            available=False,
            basis=BASIS_LIVE_REQUIRED,
            reason="Model carries no completeness score; true pool quality needs the ESMA tape.",
        )
    return RvFactor(
        dimension=DIM_POOL_QUALITY,
        available=True,
        value=float(completeness),
        basis=BASIS_STRUCTURAL,
        reason=(
            f"Structural proxy from model completeness ({float(completeness):.2f}). True pool "
            "quality (WA-LTV, arrears, seasoning) needs the ESMA tape (live-only)."
        ),
    )


def _model_triggers(model: DealModel | None) -> list[dict[str, Any]]:
    """Extract the trigger list from a seed model's covenants block, tolerantly."""
    if model is None:
        return []
    covenants = getattr(model, "covenants", None) or {}
    if not isinstance(covenants, Mapping):
        return []
    return [t for t in (covenants.get("triggers") or []) if isinstance(t, Mapping)]


# ---------------------------------------------------------------------------
# Cross-cohort normalisation.
# ---------------------------------------------------------------------------

#: Dimensions where a *lower* raw value means *better* relative value (so the
#: min-max normalisation is inverted). WAL proxy: lower seniority → shorter life.
_LOWER_IS_BETTER: frozenset[str] = frozenset({DIM_WAL})


def _normalise_dimension(values: list[float], invert: bool) -> list[float]:
    """Min-max normalise *values* into [0,100]; invert so lower→higher when asked.

    A degenerate cohort (one value, or all equal) maps every member to 50.0 —
    a neutral midpoint — because there is no spread to rank on. This is what
    keeps single-tranche / single-deal cohorts from dividing by zero.
    """
    lo, hi = min(values), max(values)
    if hi == lo:
        return [50.0 for _ in values]
    out: list[float] = []
    for v in values:
        frac = (v - lo) / (hi - lo)
        if invert:
            frac = 1.0 - frac
        out.append(round(frac * 100.0, 2))
    return out


def _composite(factors: Mapping[str, RvFactor], weights: Mapping[str, float]) -> float | None:
    """Weighted blend over available dimensions, re-normalising their weights.

    Returns ``None`` when no dimension is available (nothing to score).
    """
    avail = [
        (dim, f.score, weights.get(dim, 0.0))
        for dim, f in factors.items()
        if f.available and f.score is not None
    ]
    total_w = sum(w for _, _, w in avail)
    if not avail or total_w <= 0.0:
        return None
    blended = sum(score * w for _, score, w in avail) / total_w
    return round(blended, 2)


# ---------------------------------------------------------------------------
# The builder — dependency-injected, offline, deterministic.
# ---------------------------------------------------------------------------


def build_relative_value_scorecard(
    deals: Mapping[str, Mapping[str, Any]],
    *,
    seed_loader: Callable[[Mapping[str, Any]], DealModel | None],
    weights: Mapping[str, float] | None = None,
) -> RelativeValueScorecard:
    """Build the cross-deal relative-value scorecard.

    Parameters
    ----------
    deals:
        The deal registry — ``{deal_id: deal-context dict}`` (the live
        ``DEAL_REGISTRY`` shape). Each context carries at least ``deal_name``.
    seed_loader:
        Loads a deal's committed extracted :class:`DealModel` from its context,
        or returns ``None`` on a miss (never triggers a cold extraction). The API
        passes ``_load_cached_deal_model``; tests pass a fake.
    weights:
        Optional override of the composite weights; defaults to
        :data:`DEFAULT_WEIGHTS`.

    Returns
    -------
    RelativeValueScorecard
        One row per (deal, tranche) with honest per-dimension factors, a
        composite over the available dimensions, and a cross-deal rank.
    """
    used_weights = dict(weights) if weights is not None else dict(DEFAULT_WEIGHTS)

    rows: list[TrancheScore] = []
    deals_screened = 0
    for deal_id, deal_ctx in deals.items():
        model = seed_loader(deal_ctx)
        tranches = _deal_tranches(model)
        if not tranches:
            continue
        deals_screened += 1
        deal_name = str(deal_ctx.get("deal_name", deal_id))
        for tr in tranches:
            factors = {
                DIM_SUBORDINATION_CE: factor_subordination_ce(tr, tranches),
                DIM_WAL: factor_wal(tr, tranches),
                DIM_TRIGGER_HEADROOM: factor_trigger_headroom(tr, model),
                DIM_POOL_QUALITY: factor_pool_quality(model),
            }
            rows.append(
                TrancheScore(
                    deal_id=deal_id,
                    deal_name=deal_name,
                    tranche_name=str(tr.get("name", "?")),
                    seniority=tr.get("seniority") if isinstance(tr.get("seniority"), int) else None,
                    rating=tr.get("rating"),
                    size_eur=float(tr["size_eur"]) if _is_number(tr.get("size_eur")) else None,
                    factors=factors,
                )
            )

    # Cross-cohort normalisation: for each dimension, gather the available raw
    # values across ALL rows, min-max them, and write the sub-scores back.
    for dim in DIMENSIONS:
        available_rows = [r for r in rows if r.factors[dim].available and r.factors[dim].value is not None]
        if not available_rows:
            continue
        vals = [r.factors[dim].value for r in available_rows]  # type: ignore[misc]
        scores = _normalise_dimension(vals, invert=dim in _LOWER_IS_BETTER)
        for r, s in zip(available_rows, scores):
            r.factors[dim].score = s

    # Composite + cross-deal rank.
    for r in rows:
        r.composite_score = _composite(r.factors, used_weights)

    # Deterministic stable sort: composite desc (None last), then deal_id,
    # then seniority — so ties and unscored rows are ordered reproducibly.
    rows.sort(
        key=lambda r: (
            -(r.composite_score if r.composite_score is not None else -1.0),
            r.deal_id,
            r.seniority if r.seniority is not None else 1_000,
        )
    )
    for i, r in enumerate(rows, start=1):
        r.rank = i if r.composite_score is not None else None

    tally: dict[str, int] = {
        "deals_screened": deals_screened,
        "tranches_scored": len(rows),
    }
    for dim in DIMENSIONS:
        tally[f"available_{dim}"] = sum(1 for r in rows if r.factors[dim].available)

    return RelativeValueScorecard(
        dimensions=list(DIMENSIONS),
        weights=used_weights,
        tranches=rows,
        tally=tally,
    )


# ---------------------------------------------------------------------------
# Registered primitive wrapper — makes the screener discoverable in the
# PRIMITIVE_REGISTRY without editing registry.py (additive, per #324).
# ---------------------------------------------------------------------------


class RelativeValueScreenerInput(BaseInput):
    """Input for the screener primitive: the deal registry to screen across.

    ``deals`` is the ``{deal_id: deal-context dict}`` registry. The seed_loader
    is injected on the instance (not part of the hashable input) so the input
    stays JSON-serialisable for the audit trail.
    """

    deals: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="Deal registry to screen across."
    )


class RelativeValueScreenerOutput(BaseModel):
    """Output wrapper carrying the scorecard."""

    scorecard: RelativeValueScorecard


@register_primitive(
    name="relative_value_screener",
    version="1.0.0",
    description="Rank tranches across deals by structural relative value into a comparable scorecard.",
    tags=["screener", "cross-deal", "relative-value"],
)
class RelativeValueScreener(
    Primitive[RelativeValueScreenerInput, RelativeValueScreenerOutput]
):
    """Cross-deal relative-value screener, packaged as a governed primitive.

    The screening logic lives in :func:`build_relative_value_scorecard`; this
    wrapper adapts it to the :class:`Primitive` envelope (confidence, citations,
    audit) so it appears in the registry alongside the other SF primitives. The
    ``seed_loader`` is injected at construction; it defaults to a no-op loader
    so a bare ``RelativeValueScreener()`` is still constructible (it just
    returns an empty scorecard).
    """

    name = "relative_value_screener"
    version = "1.0.0"
    description = (
        "Rank tranches across deals by structural relative value into a comparable scorecard."
    )

    def __init__(
        self,
        seed_loader: Callable[[Mapping[str, Any]], DealModel | None] | None = None,
        weights: Mapping[str, float] | None = None,
    ) -> None:
        self._seed_loader = seed_loader or (lambda _ctx: None)
        self._weights = weights

    def execute(
        self, input: RelativeValueScreenerInput
    ) -> PrimitiveResult[RelativeValueScreenerOutput]:
        start = time.perf_counter()
        scorecard = build_relative_value_scorecard(
            input.deals, seed_loader=self._seed_loader, weights=self._weights
        )
        duration_ms = (time.perf_counter() - start) * 1000.0

        # Confidence reflects how much of the screener is structurally grounded:
        # the fraction of (tranche × dimension) cells that were available.
        cells = len(scorecard.tranches) * len(scorecard.dimensions)
        available = sum(
            1 for r in scorecard.tranches for f in r.factors.values() if f.available
        )
        confidence = (available / cells) if cells else 0.0

        citations = [
            Citation(
                document="committed extracted deal models (seed)",
                excerpt=(
                    f"Screened {scorecard.tally.get('deals_screened', 0)} deal(s), "
                    f"{scorecard.tally.get('tranches_scored', 0)} tranche(s)."
                ),
            )
        ]
        return PrimitiveResult(
            output=RelativeValueScreenerOutput(scorecard=scorecard),
            confidence=round(confidence, 4),
            citations=citations,
            audit_entry=AuditEntry.now(
                primitive_name=self.name,
                version=self.version,
                input_hash=input.input_hash(),
                duration_ms=duration_ms,
            ),
        )
