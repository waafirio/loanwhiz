"""Portfolio / multi-deal monitoring dashboard primitive (analyst-facing).

Every analyst primitive shipped so far answers a **single-deal** question — the
proximity-trend monitor projects one deal's covenant headroom, the tranche
explorer walks one deal's amortisation, the relative-value screener ranks
tranches *across* deals by structural value. None of them give an analyst
watching a **book of deals** the one thing a portfolio desk needs: a single
cross-deal **watchlist** that, per deal, rolls up where its covenant triggers
sit, how fast they are heading to breach, and which deal needs attention first.

This primitive is that watchlist. It is an **aggregator, not a new analytic**:

    "Across my whole deal set, which deals are breaching or about to breach a
     covenant trigger, ranked most-urgent first?"

Design notes
------------
- **Driven by the capability matrix's deal set.** The input is the same
  ``{deal_id: deal-context}`` registry that :func:`build_capability_matrix` and
  :func:`build_relative_value_scorecard` iterate (``loanwhiz.config.DEAL_REGISTRY``).
  Monitoring the registry *is* "monitoring the portfolio".
- **Composes the merged foundations, never reimplements them.** The forward /
  trigger-headroom signal per deal is exactly the
  :class:`~loanwhiz.primitives.proximity_trend_monitor.ProximityTrendOutput`
  produced by the early-warning monitor (#322) over that deal's covenant
  series. This primitive only *rolls* those per-deal projections up to the
  deal level and ranks the deals.
- **Dependency-injection keeps it offline and deterministic** — mirroring the
  relative-value screener (#324). The per-deal early-warning projection is
  obtained through an injected ``proximity_loader(deal_ctx) -> ProximityTrendOutput
  | None``. In tests the loader is a synthetic stub (no engine, no network); at
  the tool layer it is the real covenant→proximity chain. A deal the loader
  cannot evaluate offline yields an **honest** ``evaluable=False`` row with a
  real reason — never a fabricated status.
- **Deal-level urgency ranking mirrors the trigger-level one.** The proximity
  monitor ranks *triggers* breached < projected < no-breach < insufficient-data;
  this primitive lifts the same buckets to the *deal* level so the watchlist's
  most-urgent deal surfaces first.
- **Honest confidence.** Confidence is the fraction of deals the loader could
  evaluate — 1.0 only when every deal in the registry was evaluable.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable

from pydantic import BaseModel, Field

from loanwhiz.primitives.base import (
    AuditEntry,
    BaseInput,
    Citation,
    Primitive,
    PrimitiveResult,
)
from loanwhiz.primitives.proximity_trend_monitor import ProximityTrendOutput
from loanwhiz.primitives.registry import register_primitive

# ---------------------------------------------------------------------------
# Deal-level watch-status vocabulary + ranking
# ---------------------------------------------------------------------------

# A deal's watch status, derived from its trigger projections. Ordered by
# urgency (lower bucket = more urgent), mirroring the proximity monitor's
# trigger-level status buckets lifted to the deal level.
WATCH_BREACHED = "breached"  # at least one trigger already at/over threshold
WATCH_PROJECTED = "projected"  # no breach yet, but >=1 trigger projected to breach
WATCH_CLEAR = "clear"  # evaluable, nothing breached or projected
WATCH_UNAVAILABLE = "unavailable"  # could not be evaluated offline (honest gap)

_STATUS_BUCKET: dict[str, int] = {
    WATCH_BREACHED: 0,
    WATCH_PROJECTED: 1,
    WATCH_CLEAR: 2,
    WATCH_UNAVAILABLE: 3,
}


# ---------------------------------------------------------------------------
# I/O models
# ---------------------------------------------------------------------------


class PortfolioMonitorInput(BaseInput):
    """Input to the portfolio monitor: the deal set to watch across.

    Attributes:
        deals: The ``{deal_id: deal-context dict}`` registry to monitor — the
            same shape :func:`build_capability_matrix` /
            :func:`build_relative_value_scorecard` consume
            (``loanwhiz.config.DEAL_REGISTRY``). The per-deal early-warning
            projection is obtained through the injected ``proximity_loader`` (see
            :class:`PortfolioMonitor`), not from this hashable input, so the
            input stays JSON-serialisable for the audit trail.
    """

    deals: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description="Deal registry to monitor across."
    )


class DealWatchRow(BaseModel):
    """One deal's roll-up row in the cross-deal watchlist.

    Attributes:
        deal_id:                Canonical deal identifier (registry key).
        deal_name:              Human-readable deal name.
        jurisdiction:           Deal jurisdiction when present in the context.
        evaluable:              ``True`` when the early-warning projection could
                                be computed offline; ``False`` for an honest gap.
        reason:                 Always non-empty — why the deal is/ isn't
                                evaluable, in one phrase.
        watch_status:           ``"breached" | "projected" | "clear" |
                                "unavailable"`` — the deal-level urgency state.
        worst_trigger_proximity_pct: Highest ``latest_proximity_pct`` across the
                                deal's triggers (100 = at threshold); ``None`` when
                                no trigger had an evaluable point.
        most_urgent_trigger:    Name of the deal's most-urgent trigger (the
                                proximity monitor's ``most_urgent``); ``None`` when
                                nothing is breached or projected.
        periods_to_breach:      Soonest projected whole periods-to-breach across
                                the deal's triggers (``0`` if already breached);
                                ``None`` when no breach is projected / not
                                evaluable.
        projected_breach_period: ``"now"`` / ``"+N periods"`` label for the
                                soonest breach; ``None`` otherwise.
        n_triggers_breached:    Count of the deal's already-breached triggers.
        n_triggers_projected:   Count of the deal's projected-to-breach triggers.
        rank:                   1-based cross-deal urgency rank (1 = most urgent);
                                ``None`` only before ranking.
    """

    deal_id: str
    deal_name: str
    jurisdiction: str | None
    evaluable: bool
    reason: str
    watch_status: str
    worst_trigger_proximity_pct: float | None
    most_urgent_trigger: str | None
    periods_to_breach: int | None
    projected_breach_period: str | None
    n_triggers_breached: int
    n_triggers_projected: int
    rank: int | None


class PortfolioWatchlist(BaseModel):
    """The cross-deal watchlist roll-up.

    Attributes:
        rows:            One :class:`DealWatchRow` per deal, ranked most-urgent
                         first (breached → soonest projected → clear →
                         unavailable).
        tally:           Per-status deal counts plus ``deals_total`` /
                         ``deals_evaluable``.
        most_urgent_deal: ``deal_id`` of the top-ranked deal when something is
                         breached or projected; ``None`` when no deal is.
        summary:         Plain-English portfolio early-warning summary.
        note:            Standing honesty disclosure about offline evaluation.
    """

    rows: list[DealWatchRow]
    tally: dict[str, int]
    most_urgent_deal: str | None
    summary: str
    note: str


class PortfolioMonitorOutput(BaseModel):
    """Output wrapper carrying the watchlist."""

    watchlist: PortfolioWatchlist


# Type of the injected per-deal early-warning loader. Returns the deal's
# proximity-trend projection, or ``None`` when the deal cannot be evaluated
# offline (no cached model / no tapes in this environment).
ProximityLoader = Callable[[dict[str, Any]], ProximityTrendOutput | None]


_NOTE = (
    "Offline portfolio watch: per-deal covenant early-warning is computed from "
    "committed/cached deal artifacts only. A deal whose model or tapes are not "
    "available in this environment is reported watch_status='unavailable' with a "
    "reason — never a fabricated status."
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _deal_jurisdiction(deal_ctx: dict[str, Any]) -> str | None:
    """Best-effort jurisdiction off a deal context (registry entries vary)."""
    val = deal_ctx.get("jurisdiction")
    return str(val) if val else None


def _row_from_projection(
    deal_id: str,
    deal_ctx: dict[str, Any],
    projection: ProximityTrendOutput | None,
) -> DealWatchRow:
    """Roll one deal's trigger-level projection up to a deal-level watch row.

    ``projection`` is the deal's :class:`ProximityTrendOutput` (one entry per
    trigger, already ranked most-urgent first by the proximity monitor), or
    ``None`` when the deal could not be evaluated offline.
    """
    deal_name = str(deal_ctx.get("deal_name", deal_id))
    jurisdiction = _deal_jurisdiction(deal_ctx)

    if projection is None:
        return DealWatchRow(
            deal_id=deal_id,
            deal_name=deal_name,
            jurisdiction=jurisdiction,
            evaluable=False,
            reason=(
                "no offline covenant series available (model/tapes not cached in "
                "this environment)"
            ),
            watch_status=WATCH_UNAVAILABLE,
            worst_trigger_proximity_pct=None,
            most_urgent_trigger=None,
            periods_to_breach=None,
            projected_breach_period=None,
            n_triggers_breached=0,
            n_triggers_projected=0,
            rank=None,
        )

    projections = projection.projections
    breached = [p for p in projections if p.status == "breached"]
    projected = [p for p in projections if p.status == "projected"]

    # Worst (closest-to-breach) proximity across the deal's evaluable triggers.
    proxes = [
        p.latest_proximity_pct
        for p in projections
        if p.latest_proximity_pct is not None
    ]
    worst_prox = max(proxes) if proxes else None

    if breached:
        watch_status = WATCH_BREACHED
        ptb: int | None = 0
        # The soonest projected-period label is moot once breached.
        breach_label: str | None = "now"
        reason = f"{len(breached)} trigger(s) breached"
    elif projected:
        watch_status = WATCH_PROJECTED
        # Projections are already ranked most-urgent first; the soonest projected
        # breach is the first projected entry (the monitor sorts by ptb asc).
        soonest = min(
            (p for p in projected if p.periods_to_breach is not None),
            key=lambda p: p.periods_to_breach,  # type: ignore[arg-type,return-value]
            default=None,
        )
        ptb = soonest.periods_to_breach if soonest is not None else None
        breach_label = soonest.projected_breach_period if soonest is not None else None
        reason = (
            f"{len(projected)} trigger(s) projected to breach"
            + (f"; soonest in {ptb} period(s)" if ptb is not None else "")
        )
    else:
        watch_status = WATCH_CLEAR
        ptb = None
        breach_label = None
        # Evaluable but nothing breached/projected — distinguish "trended clear"
        # from "couldn't trend any trigger" for an honest reason.
        any_trended = any(p.status != "insufficient-data" for p in projections)
        if not projections:
            reason = "no covenant triggers in series"
        elif any_trended:
            reason = "no trigger breached or projected to breach under current trend"
        else:
            reason = "covenant series present but no trigger had enough points to trend"

    return DealWatchRow(
        deal_id=deal_id,
        deal_name=deal_name,
        jurisdiction=jurisdiction,
        evaluable=True,
        reason=reason,
        watch_status=watch_status,
        worst_trigger_proximity_pct=(
            round(worst_prox, 4) if worst_prox is not None else None
        ),
        most_urgent_trigger=projection.most_urgent,
        periods_to_breach=ptb,
        projected_breach_period=breach_label,
        n_triggers_breached=len(breached),
        n_triggers_projected=len(projected),
        rank=None,
    )


def _rank_key(row: DealWatchRow) -> tuple[int, float, float]:
    """Sort key ranking deals by urgency (most urgent first).

    Ordering:
      1. status bucket — breached < projected < clear < unavailable;
      2. within ``projected``, fewer periods_to_breach first (sooner = sooner);
      3. tie-break on higher worst proximity (closer to a line) first, by
         sorting on its negation.
    """
    bucket = _STATUS_BUCKET.get(row.watch_status, 99)
    ptb = row.periods_to_breach
    ptb_key = float(ptb) if ptb is not None else math.inf
    worst = row.worst_trigger_proximity_pct
    worst_key = -worst if worst is not None else math.inf
    return (bucket, ptb_key, worst_key)


def _build_summary(rows: list[DealWatchRow], most_urgent_deal: str | None) -> str:
    """Plain-English portfolio early-warning summary over the ranked rows."""
    n = len(rows)
    breached = [r for r in rows if r.watch_status == WATCH_BREACHED]
    projected = [r for r in rows if r.watch_status == WATCH_PROJECTED]
    unavailable = [r for r in rows if r.watch_status == WATCH_UNAVAILABLE]

    if not breached and not projected:
        clause = ""
        if unavailable:
            joined = ", ".join(f"'{r.deal_id}'" for r in unavailable)
            clause = f" Not evaluable offline: {joined}."
        return (
            f"No deal is breaching or projected to breach a covenant trigger "
            f"under the current trend across {n} deal(s).{clause}"
        )

    parts: list[str] = []
    if breached:
        joined = ", ".join(f"'{r.deal_id}'" for r in breached)
        parts.append(f"BREACHED: {joined}")
    if projected:
        joined = ", ".join(
            f"'{r.deal_id}' in {r.periods_to_breach}"
            if r.periods_to_breach is not None
            else f"'{r.deal_id}'"
            for r in projected
        )
        parts.append(f"PROJECTED breach (periods-to-breach): {joined}")
    head = "; ".join(parts)
    urgent = (
        f" Most urgent: '{most_urgent_deal}'." if most_urgent_deal is not None else ""
    )
    return f"Portfolio early-warning across {n} deal(s) — {head}.{urgent}"


def build_portfolio_watchlist(
    deals: dict[str, dict[str, Any]],
    *,
    proximity_loader: ProximityLoader,
) -> PortfolioWatchlist:
    """Roll per-deal covenant early-warning into one ranked cross-deal watchlist.

    The clean functional entry point (mirrors the relative-value screener's
    :func:`build_relative_value_scorecard` seam). For each deal in ``deals`` it
    obtains the deal's :class:`ProximityTrendOutput` via the injected
    ``proximity_loader`` (``None`` for a deal that cannot be evaluated offline),
    rolls it up to a :class:`DealWatchRow`, ranks the rows most-urgent first, and
    returns the assembled :class:`PortfolioWatchlist`.

    Args:
        deals: ``{deal_id: deal-context dict}`` registry to monitor across.
        proximity_loader: Injected per-deal loader returning the deal's
            proximity-trend projection, or ``None`` when not evaluable offline.

    Returns:
        A :class:`PortfolioWatchlist` with one ranked row per deal, the tally,
        the most-urgent deal, a plain-English summary, and the honesty note.
    """
    rows: list[DealWatchRow] = []
    for deal_id, deal_ctx in deals.items():
        try:
            projection = proximity_loader(deal_ctx)
        except Exception:
            # A loader failure for one deal must not sink the whole watchlist —
            # surface it as an honest unavailable row, not a portfolio crash.
            projection = None
        rows.append(_row_from_projection(deal_id, deal_ctx, projection))

    rows.sort(key=_rank_key)
    for i, row in enumerate(rows, start=1):
        row.rank = i

    most_urgent_deal: str | None = None
    for row in rows:  # already ranked; first breached/projected wins
        if row.watch_status in (WATCH_BREACHED, WATCH_PROJECTED):
            most_urgent_deal = row.deal_id
            break

    tally: dict[str, int] = {
        "deals_total": len(rows),
        "deals_evaluable": sum(1 for r in rows if r.evaluable),
        WATCH_BREACHED: sum(1 for r in rows if r.watch_status == WATCH_BREACHED),
        WATCH_PROJECTED: sum(1 for r in rows if r.watch_status == WATCH_PROJECTED),
        WATCH_CLEAR: sum(1 for r in rows if r.watch_status == WATCH_CLEAR),
        WATCH_UNAVAILABLE: sum(
            1 for r in rows if r.watch_status == WATCH_UNAVAILABLE
        ),
    }

    summary = _build_summary(rows, most_urgent_deal)
    return PortfolioWatchlist(
        rows=rows,
        tally=tally,
        most_urgent_deal=most_urgent_deal,
        summary=summary,
        note=_NOTE,
    )


# ---------------------------------------------------------------------------
# Registered primitive wrapper — additive registration, per #326 dispatch note.
# ---------------------------------------------------------------------------


@register_primitive(
    name="portfolio_monitor",
    version="0.1.0",
    description=(
        "Roll per-deal covenant early-warning into one cross-deal watchlist, "
        "ranked by which deal is breaching or about to breach first."
    ),
    tags=["portfolio", "monitoring", "cross-deal", "watchlist", "early-warning"],
)
class PortfolioMonitor(
    Primitive[PortfolioMonitorInput, PortfolioMonitorOutput]
):
    """Cross-deal covenant watchlist, packaged as a governed primitive.

    The roll-up logic lives in :func:`build_portfolio_watchlist`; this wrapper
    adapts it to the :class:`Primitive` envelope (confidence, citations, audit)
    so it appears in the registry alongside the other SF primitives. The
    ``proximity_loader`` is injected at construction and defaults to a no-op
    loader so a bare ``PortfolioMonitor()`` is still constructible — it just
    reports every deal as ``unavailable`` (an honest empty watch).
    """

    name = "portfolio_monitor"
    version = "0.1.0"
    description = (
        "Roll per-deal covenant early-warning into one cross-deal watchlist, "
        "ranked by which deal is breaching or about to breach first."
    )

    def __init__(self, proximity_loader: ProximityLoader | None = None) -> None:
        self._proximity_loader: ProximityLoader = proximity_loader or (
            lambda _ctx: None
        )

    def execute(  # type: ignore[override]
        self, input: PortfolioMonitorInput
    ) -> PrimitiveResult[PortfolioMonitorOutput]:
        """Build the cross-deal watchlist from the injected per-deal loader.

        Args:
            input: Validated ``PortfolioMonitorInput`` carrying the deal registry.

        Returns:
            ``PrimitiveResult[PortfolioMonitorOutput]`` with the ranked
            watchlist. ``confidence`` is the fraction of deals that were
            evaluable offline (1.0 only when every deal was evaluable; 0.0 for
            an empty registry).
        """
        start = time.perf_counter()
        watchlist = build_portfolio_watchlist(
            input.deals, proximity_loader=self._proximity_loader
        )
        duration_ms = (time.perf_counter() - start) * 1000.0

        total = watchlist.tally.get("deals_total", 0)
        evaluable = watchlist.tally.get("deals_evaluable", 0)
        confidence = (evaluable / total) if total else 0.0

        citations = [
            Citation(
                document="Per-deal covenant trigger proximity series",
                page_or_row=None,
                excerpt=(
                    f"Watchlist over {total} deal(s) "
                    f"({evaluable} evaluable offline); per-deal early-warning "
                    "rolled from the proximity-trend monitor's projections."
                ),
            )
        ]
        return PrimitiveResult[PortfolioMonitorOutput](
            output=PortfolioMonitorOutput(watchlist=watchlist),
            confidence=round(confidence, 4),
            citations=citations,
            audit_entry=AuditEntry.now(
                primitive_name=self.name,
                version=self.version,
                input_hash=input.input_hash(),
                duration_ms=duration_ms,
            ),
        )
