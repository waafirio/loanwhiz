"""Trigger early-warning / proximity-trend monitor (analyst-facing primitive).

Where :class:`~loanwhiz.primitives.covenant_monitor.CovenantMonitor` evaluates
each covenant trigger *per period* — producing a ``proximity_pct`` (0–100+,
100 = at threshold, >100 = breached) and a last-vs-prior ``direction`` — this
primitive looks at the **whole reporting series** for a trigger and answers the
analyst's forward question:

    "Given how this trigger's proximity-to-breach has trended across the
     reporting periods, when (if ever) does it cross the threshold, and which
     trigger breaches soonest?"

It does this by fitting an ordinary-least-squares (OLS) line to each trigger's
``proximity_pct`` over the (chronological) evaluable period series, projecting
periods-to-breach from the slope, and ranking the triggers by time-to-breach so
the most-urgent covenant surfaces first.

Design notes
------------
- **Builds on, does not change, the covenant monitor.** The input is the
  covenant monitor's existing per-trigger / per-period ``TriggerStatus`` series
  (``CovenantOutput.trigger_statuses``). This keeps evaluation and projection
  decoupled — the monitor owns "is it breached / how close", this primitive owns
  "where is it heading" — and keeps the shared registry edit purely additive.
- **Honest not-evaluable handling.** Mirroring the covenant monitor, a
  not-evaluable period contributes *no* point to the fit (it is not treated as
  0). A trigger with fewer than two evaluable points cannot be trended, so it
  yields an honest ``insufficient-data`` projection rather than a fake slope.
- **Linear is the transparent default.** "Current trajectory" is modelled as the
  OLS slope of proximity over the period ordinal — auditable arithmetic, no LLM.
  A deteriorating slope (> 0, not yet breached) projects a finite
  periods-to-breach; a flat / improving slope projects no breach.
- **Deterministic.** Confidence is always 1.0; citations are carried through
  from the covenant series (the prospectus trigger definitions).
"""

from __future__ import annotations

import math
import time

from pydantic import BaseModel, Field

from loanwhiz.primitives.base import (
    AuditEntry,
    BaseInput,
    Citation,
    Primitive,
    PrimitiveResult,
)
from loanwhiz.primitives.covenant_monitor import (
    CovenantOutput,
    TriggerStatus,
)
from loanwhiz.primitives.registry import register_primitive

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# A trigger is "at breach" when its proximity reaches this value (the covenant
# monitor's convention: 100 = exactly at threshold, > 100 = breached).
_BREACH_PROXIMITY = 100.0

# Below this many evaluable points a trend line is meaningless — a single point
# has no slope. The projection is then ``insufficient-data``.
_MIN_POINTS_FOR_TREND = 2

# Slope magnitudes below this (proximity-points per period) are treated as flat.
# Two reasons for a non-infinitesimal floor rather than raw fp-epsilon:
#   1. floating-point noise around a genuinely stable series should not project
#      a breach at all; and
#   2. a slope marginally above raw epsilon (say 1e-9 proximity-points/period)
#      is *practically* flat but would otherwise project an absurd breach tens
#      of billions of periods out — noise dressed up as a finite forecast.
# At 1e-4 proximity-points/period, even a 100-point gap to breach is >1e6
# periods away (centuries of monthly reporting) — below any actionable horizon,
# so we report it as "no projected breach". ``_trend_label`` and
# ``_periods_to_breach`` share this single threshold so the trend label and the
# projection never disagree (a "deteriorating" label with no projected breach).
_FLAT_SLOPE_EPS = 1e-4


# ---------------------------------------------------------------------------
# I/O models
# ---------------------------------------------------------------------------


class ProximityTrendInput(BaseInput):
    """Input to the proximity-trend monitor.

    The monitor analyses an already-evaluated covenant series — i.e. the output
    of :class:`~loanwhiz.primitives.covenant_monitor.CovenantMonitor`. Supply
    either the whole :class:`CovenantOutput` (preferred) **or** just its
    ``trigger_statuses`` list.

    Attributes:
        trigger_statuses: One :class:`TriggerStatus` per trigger per period, in
            chronological order (oldest first), exactly as produced by the
            covenant monitor. When a :class:`CovenantOutput` is supplied via
            :meth:`from_covenant_output`, this is its ``trigger_statuses``.
    """

    trigger_statuses: list[TriggerStatus] = Field(default_factory=list)

    @classmethod
    def from_covenant_output(cls, output: CovenantOutput) -> "ProximityTrendInput":
        """Build an input from a :class:`CovenantOutput` (the common path)."""
        return cls(trigger_statuses=list(output.trigger_statuses))


class TriggerProjection(BaseModel):
    """Forward-looking projection for a single covenant trigger.

    Attributes:
        trigger_name:        Name of the trigger (matches the covenant monitor).
        evaluable_points:    Number of evaluable proximity points used in the fit.
        latest_proximity_pct: Most-recent evaluable ``proximity_pct`` (``None``
                             when no evaluable point exists).
        slope_per_period:    OLS slope of proximity per period (proximity-points
                             per reporting period). Positive = deteriorating
                             (moving toward breach). ``None`` when the trend
                             could not be fit (< 2 evaluable points).
        trend:               ``"deteriorating"`` | ``"improving"`` | ``"stable"``
                             | ``"n/a"`` (insufficient data) — the sign of the
                             fitted slope.
        periods_to_breach:   Whole reporting periods until ``proximity_pct``
                             reaches 100 under the current trajectory.
                             ``0`` when already breached, a positive integer for
                             a projected future breach, ``None`` when no breach
                             is projected (flat/improving slope) or the trend
                             could not be fit.
        projected_breach_period: ``"period +N"`` label for a projected future
                             breach (e.g. ``"+3 periods"``); ``"now"`` when
                             already breached; ``None`` otherwise.
        status:              ``"breached"`` (already at/over threshold),
                             ``"projected"`` (finite future breach),
                             ``"no-projected-breach"`` (flat/improving), or
                             ``"insufficient-data"`` (< 2 evaluable points).
    """

    trigger_name: str
    evaluable_points: int
    latest_proximity_pct: float | None
    slope_per_period: float | None
    trend: str
    periods_to_breach: int | None
    projected_breach_period: str | None
    status: str


class ProximityTrendOutput(BaseModel):
    """Output of the proximity-trend monitor.

    Attributes:
        projections:  One :class:`TriggerProjection` per trigger, ranked by
                      time-to-breach (most urgent first — see :func:`_rank_key`).
        most_urgent:  Name of the top-ranked trigger when something is breached
                      or projected to breach; ``None`` when no trigger is
                      breached or projected (nothing to warn about).
        summary:      Plain-English early-warning summary.
    """

    projections: list[TriggerProjection]
    most_urgent: str | None
    summary: str


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _linear_slope(points: list[tuple[int, float]]) -> float | None:
    """Ordinary-least-squares slope of ``y`` over ``x`` for ``points``.

    ``points`` are ``(period_ordinal, proximity_pct)`` pairs for the *evaluable*
    periods only. Returns the slope (proximity-points per period), or ``None``
    when there are fewer than two points or every ``x`` is identical (a zero
    variance denominator — undefined slope).
    """
    if len(points) < _MIN_POINTS_FOR_TREND:
        return None
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in points)
    den = sum((x - mean_x) ** 2 for x, _ in points)
    if den == 0.0:
        return None
    return num / den


def _trend_label(slope: float | None) -> str:
    """Map a fitted slope onto a trend label (proximity rises toward breach)."""
    if slope is None:
        return "n/a"
    # ``<=`` (not ``<``) so the exact-threshold boundary lands on "stable",
    # matching ``_periods_to_breach``'s ``slope <= _FLAT_SLOPE_EPS → None``: a
    # slope at the flat threshold is never labelled "deteriorating" while
    # projecting no breach.
    if abs(slope) <= _FLAT_SLOPE_EPS:
        return "stable"
    return "deteriorating" if slope > 0 else "improving"


def _periods_to_breach(
    latest_proximity: float,
    slope: float | None,
) -> int | None:
    """Whole periods until proximity reaches 100 under the current trajectory.

    - Already at/over threshold (``latest_proximity >= 100``) → ``0``.
    - Deteriorating (``slope`` finite and > 0) → ``ceil((100 - latest) / slope)``
      (at least 1, since we are not yet breached).
    - Flat / improving / un-fit slope → ``None`` (no breach projected).
    """
    if latest_proximity >= _BREACH_PROXIMITY:
        return 0
    if slope is None or slope <= _FLAT_SLOPE_EPS:
        return None
    remaining = _BREACH_PROXIMITY - latest_proximity
    return max(1, math.ceil(remaining / slope))


def _project_one(trigger_name: str, statuses: list[TriggerStatus]) -> TriggerProjection:
    """Build the :class:`TriggerProjection` for one trigger's period series.

    ``statuses`` are this trigger's :class:`TriggerStatus` rows in chronological
    order (one per period). Only the evaluable rows with a non-``None``
    ``proximity_pct`` enter the trend fit; their position in the *full* series
    (the original period ordinal) is the ``x`` so gaps from not-evaluable periods
    are honoured rather than collapsed.
    """
    evaluable: list[tuple[int, float]] = [
        (idx, s.proximity_pct)
        for idx, s in enumerate(statuses)
        if s.evaluable and s.proximity_pct is not None
    ]
    n_eval = len(evaluable)

    if n_eval == 0:
        return TriggerProjection(
            trigger_name=trigger_name,
            evaluable_points=0,
            latest_proximity_pct=None,
            slope_per_period=None,
            trend="n/a",
            periods_to_breach=None,
            projected_breach_period=None,
            status="insufficient-data",
        )

    latest_proximity = evaluable[-1][1]

    if n_eval < _MIN_POINTS_FOR_TREND:
        # One evaluable point: we know where it sits but cannot trend it. An
        # already-breached single point is still reported as breached (the
        # forward projection is moot but the breach is a fact).
        already = latest_proximity >= _BREACH_PROXIMITY
        return TriggerProjection(
            trigger_name=trigger_name,
            evaluable_points=n_eval,
            latest_proximity_pct=latest_proximity,
            slope_per_period=None,
            trend="n/a",
            periods_to_breach=0 if already else None,
            projected_breach_period="now" if already else None,
            status="breached" if already else "insufficient-data",
        )

    slope = _linear_slope(evaluable)
    ptb = _periods_to_breach(latest_proximity, slope)

    if latest_proximity >= _BREACH_PROXIMITY:
        status = "breached"
        projected_label: str | None = "now"
    elif ptb is not None:
        status = "projected"
        projected_label = f"+{ptb} period" + ("s" if ptb != 1 else "")
    else:
        status = "no-projected-breach"
        projected_label = None

    return TriggerProjection(
        trigger_name=trigger_name,
        evaluable_points=n_eval,
        latest_proximity_pct=round(latest_proximity, 4),
        slope_per_period=round(slope, 6) if slope is not None else None,
        trend=_trend_label(slope),
        periods_to_breach=ptb,
        projected_breach_period=projected_label,
        status=status,
    )


# Rank-bucket ordering: breached deals are most urgent, then projected future
# breaches, then triggers with no projected breach, then those we couldn't
# trend. Within "projected", sooner is more urgent.
_STATUS_BUCKET = {
    "breached": 0,
    "projected": 1,
    "no-projected-breach": 2,
    "insufficient-data": 3,
}


def _rank_key(projection: TriggerProjection) -> tuple[int, float, float]:
    """Sort key ranking projections by time-to-breach (most urgent first).

    Ordering:
      1. status bucket — breached < projected < no-breach < insufficient-data;
      2. within ``projected``, fewer periods_to_breach first (sooner = sooner);
      3. tie-break on higher latest proximity (closer to the line) first, by
         sorting on its negation.
    """
    bucket = _STATUS_BUCKET.get(projection.status, 99)
    ptb = projection.periods_to_breach
    ptb_key = float(ptb) if ptb is not None else math.inf
    latest = projection.latest_proximity_pct
    latest_key = -latest if latest is not None else math.inf
    return (bucket, ptb_key, latest_key)


def _build_summary(projections: list[TriggerProjection], most_urgent: str | None) -> str:
    """Plain-English early-warning summary over the ranked projections."""
    breached = [p.trigger_name for p in projections if p.status == "breached"]
    projected = [p for p in projections if p.status == "projected"]
    n = len(projections)

    if not breached and not projected:
        unfit = [p.trigger_name for p in projections if p.status == "insufficient-data"]
        clause = ""
        if unfit:
            joined = ", ".join(f"'{t}'" for t in unfit)
            clause = f" Insufficient data to trend: {joined}."
        return (
            f"No covenant trigger is breached or projected to breach under the "
            f"current trajectory across {n} trigger(s).{clause}"
        )

    parts: list[str] = []
    if breached:
        joined = ", ".join(f"'{t}'" for t in breached)
        parts.append(f"BREACHED: {joined}")
    if projected:
        soonest = projected[0]  # already ranked
        joined = ", ".join(
            f"'{p.trigger_name}' in {p.periods_to_breach}" for p in projected
        )
        parts.append(f"PROJECTED breach (periods-to-breach): {joined}")
    head = "; ".join(parts)
    urgent_clause = (
        f" Most urgent: '{most_urgent}'." if most_urgent is not None else ""
    )
    return f"Early-warning across {n} trigger(s) — {head}.{urgent_clause}"


def project_proximity_trends(
    trigger_statuses: list[TriggerStatus],
) -> ProximityTrendOutput:
    """Compute ranked breach projections from a covenant trigger-status series.

    The clean functional entry point (mirrors the covenant monitor's
    :func:`evaluate_triggers` seam). Groups the ``trigger_statuses`` by trigger
    name — preserving chronological order within each trigger — fits the trend,
    projects periods-to-breach, and returns the projections ranked most-urgent
    first.
    """
    by_trigger: dict[str, list[TriggerStatus]] = {}
    for status in trigger_statuses:
        by_trigger.setdefault(status.trigger_name, []).append(status)

    projections = [
        _project_one(name, rows) for name, rows in by_trigger.items()
    ]
    projections.sort(key=_rank_key)

    most_urgent: str | None = None
    for proj in projections:  # already ranked; first breached/projected wins
        if proj.status in ("breached", "projected"):
            most_urgent = proj.trigger_name
            break

    summary = _build_summary(projections, most_urgent)
    return ProximityTrendOutput(
        projections=projections,
        most_urgent=most_urgent,
        summary=summary,
    )


def project_from_covenant_output(output: CovenantOutput) -> ProximityTrendOutput:
    """Convenience entry: project trends straight from a :class:`CovenantOutput`."""
    return project_proximity_trends(output.trigger_statuses)


# ---------------------------------------------------------------------------
# Primitive
# ---------------------------------------------------------------------------


@register_primitive(
    name="proximity_trend_monitor",
    version="0.1.0",
    description=(
        "Project covenant-trigger periods-to-breach from the proximity trend "
        "across the reporting series, ranked by time-to-breach"
    ),
    tags=["covenant", "trigger", "early-warning", "computation"],
)
class ProximityTrendMonitor(Primitive[ProximityTrendInput, ProximityTrendOutput]):
    """Project periods-to-breach per covenant trigger from its proximity trend.

    Consumes the covenant monitor's per-trigger / per-period ``TriggerStatus``
    series, fits an OLS proximity trend per trigger, projects periods-to-breach
    under the current trajectory, and ranks the triggers by time-to-breach.
    Deterministic — confidence is always 1.0.
    """

    name = "proximity_trend_monitor"
    version = "0.1.0"
    description = (
        "Project covenant-trigger periods-to-breach from the proximity trend "
        "across the reporting series, ranked by time-to-breach"
    )

    def execute(  # type: ignore[override]
        self, input: ProximityTrendInput
    ) -> PrimitiveResult[ProximityTrendOutput]:
        """Fit each trigger's proximity trend and rank the breach projections.

        Args:
            input: Validated ``ProximityTrendInput`` carrying the covenant
                   monitor's ``trigger_statuses`` series.

        Returns:
            ``PrimitiveResult[ProximityTrendOutput]`` with one ranked
            ``TriggerProjection`` per trigger, the most-urgent trigger name, and
            a plain-English early-warning summary. ``confidence`` is 1.0.
        """
        t0 = time.perf_counter()
        input_hash = input.input_hash()

        output = project_proximity_trends(input.trigger_statuses)

        duration_ms = (time.perf_counter() - t0) * 1000.0
        audit = AuditEntry.now(
            primitive_name=self.name,
            version=self.version,
            input_hash=input_hash,
            duration_ms=duration_ms,
        )

        # The trend is grounded in the same prospectus trigger definitions the
        # covenant monitor cited; surface one citation back to that derivation so
        # the projection is auditable rather than free-floating.
        citations = [
            Citation(
                document="Covenant trigger proximity series",
                page_or_row=None,
                excerpt=(
                    "Periods-to-breach projected from the OLS proximity trend "
                    "over the covenant monitor's per-period trigger statuses."
                ),
            )
        ]

        return PrimitiveResult[ProximityTrendOutput](
            output=output,
            confidence=1.0,
            citations=citations,
            audit_entry=audit,
        )
