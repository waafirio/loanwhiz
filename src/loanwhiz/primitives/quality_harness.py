"""Cross-deal quality harness — grade each deal against its ground-truth answer key (#428, epic #425).

The *graded* sibling of :mod:`loanwhiz.primitives.capability_matrix`. Where the
capability matrix answers "did this primitive **run** for this deal?" (``ran`` /
``validated`` / ``not-applicable``), the quality harness answers the harder
question the epic is built around: "did the primitive's output **reconcile to the
deal's published ground truth, to tolerance**?" — emitting ``passed`` / ``failed``
/ ``not-applicable`` with a numeric score, governance evidence, and an honest
reason for every skip.

It enumerates the whole ``DEAL_REGISTRY`` and, per deal, runs
extraction→execution→reconciliation and grades each ``(deal × primitive × check)``
against the deal's committed :class:`~loanwhiz.primitives.reconciliation_answer_key.DealAnswerKey`
(#427) to that key's EUR tolerance. The grading mechanism for the
Priority-of-Payments checks is #427's
:func:`~loanwhiz.primitives.reconciliation_answer_key.reconcile_against_answer_key`
— the same to-the-cent reconciler the live engine-validation endpoint uses, folded
through ``run_period`` so the grade reflects the real engine, not a parallel one.

Graded checks (derived from the answer key's three typed sections, #427):

- ``revenue_pop`` / ``redemption_pop`` (``waterfall_runner``) — the engine's
  folded revenue / redemption distribution vs. the published PoP, to the cent.
- ``covenants`` (``covenant_monitor``) — the engine's per-period trigger
  evaluation vs. the published covenant pass/fail outcomes.
- ``pool_stats`` (``collections_aggregator``) — the engine's reconstructed
  end-of-period pool balance / principal collected vs. the published statistics.

Honesty discipline (#193 — no wall of green). This harness is the *opposite* of a
green-painting exercise:

- A deal with **no committed answer key** grades every check ``not-applicable``
  with the real reason — never a fabricated pass. The backfill (#429) committed
  Green Lion 2024-1's answer key (authored from its published Notes & Cash
  report), so over the live registry the honest verdict is now mixed: GL-2024-1's
  revenue + redemption PoP grade ``passed`` to the cent, while deals with no
  committed published ground truth stay honestly ``not-applicable``. The grading
  machinery is also proven against injected/synthetic keys.
- A check whose answer-key section is empty, or whose engine series could not be
  folded, is ``not-applicable`` with that reason — not silently passed.
- A genuine miss is ``failed`` with the delta, surfaced, never hidden.

Design (mirrors :mod:`capability_matrix`):

- **Dependency-injected loaders.** :func:`build_quality_matrix` takes the deal
  registry, a seed-model loader, an answer-key loader, and a *series provider*
  (the deal's offline-folded engine :class:`DealStateSeries`), so it is
  unit-testable offline and deal-generic. The series provider + extracted-trigger
  seams default to deferred imports of the offline builders (the same pattern
  :mod:`reconciler` / :mod:`reconciliation_gate` use) so this reader does not pull
  the API surface at module load. The API wires it to the live ``DEAL_REGISTRY`` /
  ``_load_cached_deal_model`` / ``load_answer_key``.
- **The series provider, not the answer key, supplies the engine fold.** An answer
  key carries the deal's published *ground truth* (PoP / covenants / pool stats),
  not the liability-section opening balances the report-path fold needs to seed
  period-0 — so the engine series is folded from the deal's committed offline
  source (the :mod:`reconciler` fixtures path, mirroring the per-deal
  ``_VALIDATION_BUILDERS`` precedent), and #427's
  :func:`reconcile_against_answer_key` grades that folded series against the key.
  A deal with an answer key but no committed offline series grades the execution
  checks ``not-applicable`` with that real reason.
- **Per-cell defensive degradation.** A grader that hits an unexpected error on
  one deal records an honest ``not-applicable`` cell carrying the error rather
  than sinking the whole matrix — the same per-cell honesty the breadth harness
  and capability matrix encode.
- **Offline & deterministic.** The fold reads only committed seed + answer-key
  data; no loan tape is fetched and no LLM is called in the grading path.

The result is JSON-serialisable structured data — the graded extension of
``/capability-matrix`` the API surfaces at ``/quality-matrix``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, Field

from loanwhiz.extraction.assembler import DealModel
from loanwhiz.primitives.period_state_machine import DealStateSeries
from loanwhiz.primitives.reconciliation_answer_key import DealAnswerKey
from loanwhiz.primitives.reconciler import ReconciliationReport

# ---------------------------------------------------------------------------
# Grade vocabulary — the three honest graded outcomes.
# ---------------------------------------------------------------------------

GRADE_PASSED = "passed"
GRADE_FAILED = "failed"
GRADE_NOT_APPLICABLE = "not-applicable"

#: Jurisdiction default for the Dutch Green Lion deals (mirrors capability_matrix).
_DEFAULT_JURISDICTION = "Netherlands"


# ---------------------------------------------------------------------------
# Typed result models.
# ---------------------------------------------------------------------------


class GradedCell(BaseModel):
    """One (check × deal) graded cell of the quality matrix.

    ``reason`` is **mandatory and non-empty** for a ``not-applicable`` cell — the
    honesty contract is that every skip carries its real reason. For
    ``passed`` / ``failed`` it is a short positive/negative note. ``score`` is the
    fraction of sub-checks (steps / stats / covenants) that reconciled, in
    ``[0, 1]``, or ``None`` when the cell did not grade (``not-applicable``).
    ``tolerance_eur`` is the EUR tolerance applied where the check is numeric.
    """

    check_key: str = Field(..., description="Stable check identifier.")
    deal_id: str = Field(..., description="Canonical deal id.")
    grade: str = Field(
        ..., description=f"One of {GRADE_PASSED!r}, {GRADE_FAILED!r}, {GRADE_NOT_APPLICABLE!r}."
    )
    score: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Fraction of sub-checks passed in [0,1], or None."
    )
    tolerance_eur: float | None = Field(
        default=None, description="EUR tolerance applied to this check, when numeric."
    )
    reason: str = Field(..., description="Human reason — REQUIRED and non-empty for not-applicable.")
    evidence: dict[str, Any] = Field(
        default_factory=dict, description="Structured, JSON-serialisable grading evidence."
    )


class QualityCheckRow(BaseModel):
    """A graded check (one row of the matrix) and its declared metadata."""

    key: str = Field(..., description="Stable check identifier.")
    primitive_name: str = Field(..., description="Underlying registered primitive name.")
    label: str = Field(..., description="Human-readable check label for the UI.")
    description: str = Field(..., description="One-line description of what the check grades.")
    category: str = Field(..., description="Answer-key section the check grades against.")


class QualityDealColumn(BaseModel):
    """A deal (one column of the matrix) and its declared metadata."""

    deal_id: str = Field(..., description="Canonical deal id.")
    deal_name: str = Field(..., description="Human deal name.")
    jurisdiction: str = Field(..., description="Resolved jurisdiction (Netherlands default).")
    has_seed_model: bool = Field(..., description="Whether a committed extracted model was loaded.")
    has_answer_key: bool = Field(..., description="Whether a committed answer key was loaded.")


class QualityMatrix(BaseModel):
    """The full cross-deal quality matrix — the graded extension of the capability matrix.

    ``cells`` is the flat list of every (check × deal) graded cell. ``tally`` is a
    per-grade count across all cells, so the UI can show the honest headline
    ("N passed / N failed / N not-applicable") without re-deriving it.
    """

    checks: list[QualityCheckRow]
    deals: list[QualityDealColumn]
    cells: list[GradedCell]
    tally: dict[str, int] = Field(
        default_factory=dict, description="Per-grade cell counts across the whole matrix."
    )
    note: str = Field(
        default=(
            "Each cell GRADES a deal's engine output against its committed ground-truth "
            "answer key (#427), to the key's EUR tolerance: 'passed' = reconciled to "
            "tolerance; 'failed' = a real miss (with the delta); 'not-applicable' = no "
            "answer key / empty section / unfoldable series, with the real reason. "
            "Green Lion 2024-1's answer key is committed (#429, from its published Notes "
            "& Cash report), so its revenue + redemption PoP grade to the cent; deals "
            "with no committed published ground truth stay honestly not-applicable. "
            "Honesty over a wall of green."
        ),
        description="Standing honesty disclosure for the quality matrix.",
    )


# ---------------------------------------------------------------------------
# Per-deal precomputed grading context.
# ---------------------------------------------------------------------------


class _DealGrading:
    """Everything the graders for one deal share — computed once per deal.

    Holds the loaded seed model + answer key and (when both are present and the
    answer key carries PoP) the folded engine series + the to-the-cent
    reconciliation report. ``fold_error`` carries the reason the fold could not
    run, so the PoP / pool-stat graders degrade to an honest not-applicable.
    """

    def __init__(
        self,
        *,
        deal_id: str,
        deal_ctx: Mapping[str, Any],
        model: DealModel | None,
        answer_key: DealAnswerKey | None,
        series: DealStateSeries | None,
        recon: ReconciliationReport | None,
        fold_error: str | None,
    ) -> None:
        self.deal_id = deal_id
        self.deal_ctx = deal_ctx
        self.model = model
        self.answer_key = answer_key
        self.series = series
        self.recon = recon
        self.fold_error = fold_error


# ---------------------------------------------------------------------------
# Grader catalogue — each grades one (deal × check) cell.
# ---------------------------------------------------------------------------
#
# A grader returns a GradedCell. It derives applicability from the deal's real
# inputs (does an answer key exist? does it carry this section? did the engine
# series fold?) — never a hardcoded per-deal table — and every skip carries a
# real reason. Each grader is wrapped so an unexpected error becomes an honest
# not-applicable cell rather than sinking the matrix.

#: Signature of a cell grader.
Grader = Callable[[str, "_DealGrading"], GradedCell]


def _na(
    check_key: str, deal_id: str, reason: str, *, evidence: dict[str, Any] | None = None
) -> GradedCell:
    """A not-applicable cell with a guaranteed non-empty reason."""
    return GradedCell(
        check_key=check_key,
        deal_id=deal_id,
        grade=GRADE_NOT_APPLICABLE,
        score=None,
        tolerance_eur=None,
        reason=reason or "Not applicable for this deal (inputs absent).",
        evidence=evidence or {},
    )


def _grade_pop_side(check_key: str, ctx: _DealGrading, *, side: str) -> GradedCell:
    """Grade one PoP side (``revenue`` / ``redemption``) against the answer key.

    Reads the folded :class:`ReconciliationReport` produced by #427's
    ``reconcile_against_answer_key`` and grades the chosen waterfall side per
    period, to the answer key's EUR tolerance.
    """
    deal_id = ctx.deal_id
    key = ctx.answer_key
    if key is None:
        return _na(check_key, deal_id, "No committed answer key for this deal.")
    pop_attr = "revenue_pop" if side == "revenue" else "redemption_pop"
    if not any(getattr(p, pop_attr) for p in key.periods):
        return _na(
            check_key,
            deal_id,
            f"Answer key carries no {side} Priority-of-Payments line items.",
        )
    if ctx.recon is None:
        return _na(
            check_key,
            deal_id,
            f"Could not reconcile the engine series: {ctx.fold_error or 'unknown error'}.",
        )

    sides = [getattr(p, side) for p in ctx.recon.periods]
    if not sides:
        return _na(check_key, deal_id, "Reconciliation produced no periods to grade.")

    total_steps = sum(len(s.steps) for s in sides)
    passed_steps = sum(s.steps_passed for s in sides)
    periods_passed = sum(1 for s in sides if s.passed)
    max_abs_delta = max(
        (abs(step.delta) for s in sides for step in s.steps), default=0.0
    )
    all_passed = all(s.passed for s in sides)
    score = (passed_steps / total_steps) if total_steps else None
    evidence = {
        "periods_checked": len(sides),
        "periods_passed": periods_passed,
        "steps_total": total_steps,
        "steps_passed": passed_steps,
        "max_abs_delta_eur": round(max_abs_delta, 4),
    }
    if all_passed:
        return GradedCell(
            check_key=check_key,
            deal_id=deal_id,
            grade=GRADE_PASSED,
            score=score,
            tolerance_eur=key.tolerance_eur,
            reason=(
                f"Engine {side} distribution reconciled to the published PoP across "
                f"{len(sides)} period(s), to EUR {key.tolerance_eur:.2f}."
            ),
            evidence=evidence,
        )
    return GradedCell(
        check_key=check_key,
        deal_id=deal_id,
        grade=GRADE_FAILED,
        score=score,
        tolerance_eur=key.tolerance_eur,
        reason=(
            f"Engine {side} distribution did not reconcile in "
            f"{len(sides) - periods_passed}/{len(sides)} period(s) "
            f"(worst delta EUR {max_abs_delta:.2f})."
        ),
        evidence=evidence,
    )


def _grade_revenue_pop(check_key: str, ctx: _DealGrading) -> GradedCell:
    """Revenue PoP reconciliation — engine revenue cascade vs. published revenue PoP."""
    return _grade_pop_side(check_key, ctx, side="revenue")


def _grade_redemption_pop(check_key: str, ctx: _DealGrading) -> GradedCell:
    """Redemption PoP reconciliation — engine redemption cascade vs. published redemption PoP."""
    return _grade_pop_side(check_key, ctx, side="redemption")


#: The two pool-statistic keys the harness can ground against the folded series.
#: ``pool_balance_end`` ↔ the closing ``DealState.pool_balance``;
#: ``principal_collected`` ↔ that state's ``collections.total_principal`` (the
#: net pool reduction the engine recorded for the period).
_POOL_STAT_KEYS = ("pool_balance_end", "principal_collected")


def _grade_pool_stats(check_key: str, ctx: _DealGrading) -> GradedCell:
    """Grade published pool statistics against the folded engine series.

    Compares the answer key's ``pool_balance_end`` / ``principal_collected`` for
    each period to the matching closing :class:`~loanwhiz.primitives.deal_state.DealState`
    in the folded series, to the answer key's EUR tolerance. Statistics with no
    series-grounded analogue are surfaced honestly (``ungraded_stat_keys``), never
    faked.
    """
    deal_id = ctx.deal_id
    key = ctx.answer_key
    if key is None:
        return _na(check_key, deal_id, "No committed answer key for this deal.")
    if not any(p.pool_stats for p in key.periods):
        return _na(check_key, deal_id, "Answer key carries no published pool statistics.")
    if ctx.series is None:
        return _na(
            check_key,
            deal_id,
            f"No offline engine series to ground pool statistics: {ctx.fold_error or 'unknown'}.",
        )

    # Index closing states by reporting date — prefer the state that recorded
    # collections (the period's closing state, not the seed) on a date collision.
    states_by_date: dict[str, Any] = {}
    for state in ctx.series.states:
        existing = states_by_date.get(state.reporting_date)
        if existing is None or (
            getattr(existing, "collections", None) is None
            and getattr(state, "collections", None) is not None
        ):
            states_by_date[state.reporting_date] = state

    graded = 0
    matched = 0
    ungraded_stat_keys: set[str] = set()
    unmatched_dates: list[str] = []
    max_abs_delta = 0.0
    for period in key.periods:
        if not period.pool_stats:
            continue
        for stat_key, expected in period.pool_stats.items():
            if stat_key not in _POOL_STAT_KEYS:
                ungraded_stat_keys.add(stat_key)
                continue
            state = states_by_date.get(period.reporting_date)
            if state is None:
                unmatched_dates.append(period.reporting_date)
                continue
            if stat_key == "pool_balance_end":
                actual = float(state.pool_balance)
            else:  # principal_collected
                collections = getattr(state, "collections", None)
                if collections is None:
                    unmatched_dates.append(period.reporting_date)
                    continue
                actual = float(collections.total_principal)
            graded += 1
            delta = abs(actual - float(expected))
            max_abs_delta = max(max_abs_delta, delta)
            if delta <= key.tolerance_eur:
                matched += 1

    evidence = {
        "stats_graded": graded,
        "stats_matched": matched,
        "max_abs_delta_eur": round(max_abs_delta, 4),
        "ungraded_stat_keys": sorted(ungraded_stat_keys),
        "unmatched_dates": sorted(set(unmatched_dates)),
    }
    if graded == 0:
        return _na(
            check_key,
            deal_id,
            (
                "No published pool statistic could be grounded against the folded "
                "series (no series-mappable keys, or no matching reconstructed period)."
            ),
            evidence=evidence,
        )
    score = matched / graded
    if matched == graded:
        return GradedCell(
            check_key=check_key,
            deal_id=deal_id,
            grade=GRADE_PASSED,
            score=score,
            tolerance_eur=key.tolerance_eur,
            reason=(
                f"All {graded} grounded pool statistic(s) reconciled to the folded "
                f"engine series, to EUR {key.tolerance_eur:.2f}."
            ),
            evidence=evidence,
        )
    return GradedCell(
        check_key=check_key,
        deal_id=deal_id,
        grade=GRADE_FAILED,
        score=score,
        tolerance_eur=key.tolerance_eur,
        reason=(
            f"{graded - matched}/{graded} pool statistic(s) missed the engine series "
            f"(worst delta EUR {max_abs_delta:.2f})."
        ),
        evidence=evidence,
    )


def _grade_covenants(
    check_key: str,
    ctx: _DealGrading,
    *,
    triggers_loader: Callable[[Mapping[str, Any]], list[Any]],
) -> GradedCell:
    """Grade published covenant outcomes against the engine's CovenantMonitor.

    Runs the deal's extracted triggers (falling back to the monitor's defaults)
    over per-period inputs assembled from the answer key, then name-matches each
    published :class:`~loanwhiz.primitives.reconciliation_answer_key.CovenantResult`
    to the engine's evaluated :class:`~loanwhiz.primitives.covenant_monitor.TriggerStatus`
    for that period and compares published pass/fail to the engine's
    breached/OK. Only evaluable, name-matched covenants are graded — unmatched or
    not-evaluable published covenants are surfaced honestly, never faked.
    """
    from loanwhiz.primitives.covenant_monitor import CovenantInput, CovenantMonitor

    deal_id = ctx.deal_id
    key = ctx.answer_key
    if key is None:
        return _na(check_key, deal_id, "No committed answer key for this deal.")
    if not any(p.covenants for p in key.periods):
        return _na(check_key, deal_id, "Answer key carries no published covenant results.")

    triggers = list(triggers_loader(ctx.deal_ctx)) or list(CovenantMonitor.DEFAULT_TRIGGERS)
    # Per-period inputs: the metric resolver reads the period dict directly and
    # nested under ``pool_stats`` / ``arrears_breakdown``, so surface both.
    periods = [
        {"reporting_date": p.reporting_date, "pool_stats": dict(p.pool_stats), **p.pool_stats}
        for p in key.periods
    ]
    result = CovenantMonitor().execute(CovenantInput(periods=periods, triggers=triggers))
    status_by = {(s.trigger_name, s.period): s for s in result.output.trigger_statuses}

    graded = 0
    matched = 0
    unmatched_names: set[str] = set()
    not_evaluable: set[str] = set()
    for period in key.periods:
        for cov in period.covenants:
            status = status_by.get((cov.name, period.reporting_date))
            if status is None:
                unmatched_names.add(cov.name)
                continue
            if not status.evaluable:
                not_evaluable.add(cov.name)
                continue
            graded += 1
            engine_passed = not status.is_triggered
            if engine_passed == cov.passed:
                matched += 1

    evidence = {
        "covenants_graded": graded,
        "covenants_matched": matched,
        "unmatched_covenant_names": sorted(unmatched_names),
        "not_evaluable_covenant_names": sorted(not_evaluable),
        "trigger_count": len(triggers),
    }
    if graded == 0:
        return _na(
            check_key,
            deal_id,
            (
                "No published covenant matched an offline-evaluable engine trigger "
                "(name mismatch or metric not resolvable from the answer key)."
            ),
            evidence=evidence,
        )
    score = matched / graded
    if matched == graded:
        return GradedCell(
            check_key=check_key,
            deal_id=deal_id,
            grade=GRADE_PASSED,
            score=score,
            tolerance_eur=None,
            reason=(
                f"All {graded} evaluable covenant(s) matched the engine's per-period "
                "trigger evaluation."
            ),
            evidence=evidence,
        )
    return GradedCell(
        check_key=check_key,
        deal_id=deal_id,
        grade=GRADE_FAILED,
        score=score,
        tolerance_eur=None,
        reason=(
            f"{graded - matched}/{graded} evaluable covenant(s) disagreed with the "
            "engine's per-period trigger evaluation."
        ),
        evidence=evidence,
    )


#: The declared, ordered catalogue of graded checks (matrix rows). Each entry
#: pairs the row metadata with its grader. The PoP and pool-stat graders share
#: the per-deal folded series; the covenant grader needs the extracted-trigger
#: loader, bound in :func:`build_quality_matrix`.
def _check_catalogue(
    triggers_loader: Callable[[Mapping[str, Any]], list[Any]],
) -> list[tuple[QualityCheckRow, Grader]]:
    return [
        (
            QualityCheckRow(
                key="revenue_pop",
                primitive_name="waterfall_runner",
                label="Revenue waterfall reconciliation",
                description="Grade the engine's revenue distribution against the published revenue PoP, to the cent.",
                category="revenue_pop",
            ),
            _grade_revenue_pop,
        ),
        (
            QualityCheckRow(
                key="redemption_pop",
                primitive_name="waterfall_runner",
                label="Redemption waterfall reconciliation",
                description="Grade the engine's redemption distribution against the published redemption PoP, to the cent.",
                category="redemption_pop",
            ),
            _grade_redemption_pop,
        ),
        (
            QualityCheckRow(
                key="covenants",
                primitive_name="covenant_monitor",
                label="Covenant outcome grading",
                description="Grade the engine's per-period trigger evaluation against the published covenant results.",
                category="covenants",
            ),
            lambda ck, ctx: _grade_covenants(ck, ctx, triggers_loader=triggers_loader),
        ),
        (
            QualityCheckRow(
                key="pool_stats",
                primitive_name="collections_aggregator",
                label="Pool-statistics reconciliation",
                description="Grade the folded engine series' pool balance / principal collected against the published statistics.",
                category="pool_stats",
            ),
            _grade_pool_stats,
        ),
    ]


def quality_check_rows() -> list[QualityCheckRow]:
    """Return the declared graded-check catalogue (matrix rows), in order."""
    return [row for row, _ in _check_catalogue(lambda _ctx: [])]


def _resolve_jurisdiction(deal_ctx: Mapping[str, Any]) -> str:
    """Resolve a deal's jurisdiction — explicit registry key, else Netherlands default."""
    return deal_ctx.get("jurisdiction") or _DEFAULT_JURISDICTION


def _default_triggers_loader() -> Callable[[Mapping[str, Any]], list[Any]]:
    """Deferred-import the API layer's extracted-trigger resolver.

    Kept out of module scope (mirrors :mod:`reconciler` / :mod:`reconciliation_gate`)
    so this reader does not pull the API surface in at import time.
    """
    from loanwhiz.api.main import _extracted_triggers_to_definitions

    return lambda deal_ctx: _extracted_triggers_to_definitions(dict(deal_ctx))


#: Signature of a series provider: ``(deal_id, deal_ctx, model) -> series | None``.
SeriesProvider = Callable[[str, Mapping[str, Any], "DealModel | None"], "DealStateSeries | None"]


def _default_series_provider() -> SeriesProvider:
    """The default per-deal offline engine-series provider.

    Mirrors the API's per-deal ``_VALIDATION_BUILDERS`` precedent: a small map of
    committed offline folds keyed by deal id. Today only Green Lion 2024-1 has a
    committed offline series (its Notes & Cash fixtures + seed model, folded
    through ``run_period`` by :func:`reconciler.fold_green_lion_2024_1`); a deal
    absent from the map has no offline series, so its execution checks grade
    ``not-applicable`` with that honest reason. The fold is deferred-imported so
    this reader stays offline-importable.
    """
    builders: dict[str, Callable[[], DealStateSeries]] = {}

    def _green_lion_2024_1() -> DealStateSeries:
        from loanwhiz.primitives.reconciler import fold_green_lion_2024_1

        series, _ = fold_green_lion_2024_1()
        return series

    builders["green-lion-2024-1"] = _green_lion_2024_1

    def provider(
        deal_id: str, deal_ctx: Mapping[str, Any], model: DealModel | None
    ) -> DealStateSeries | None:
        builder = builders.get(deal_id)
        return builder() if builder is not None else None

    return provider


def _reconcile_deal(
    deal_id: str,
    deal_ctx: Mapping[str, Any],
    model: DealModel | None,
    answer_key: DealAnswerKey | None,
    series_provider: SeriesProvider,
) -> tuple[DealStateSeries | None, ReconciliationReport | None, str | None]:
    """Obtain a deal's offline engine series and reconcile it against its key, once.

    The engine series comes from the injected ``series_provider`` (NOT the answer
    key — the key carries ground truth, not the opening balances the fold seeds
    from). When both a series and an answer key with PoP exist, grades them via
    #427's :func:`reconcile_against_answer_key`. Returns ``(series, recon, error)``
    — ``error`` is the one-line reason the execution checks degrade to a honest
    not-applicable (no key / no series / reconcile mismatch). ``series`` is still
    returned when only the reconcile failed, so pool-stat grading can use it.
    """
    if answer_key is None:
        return None, None, "no committed answer key"
    try:
        series = series_provider(deal_id, deal_ctx, model)
    except Exception as exc:  # noqa: BLE001 — per-deal defensive degradation (honest n/a)
        return None, None, f"series provider error ({type(exc).__name__}): {exc}"
    if series is None:
        return None, None, "no committed offline engine series for this deal"
    if not any(p.revenue_pop or p.redemption_pop for p in answer_key.periods):
        # No PoP ground truth to reconcile; the series is still usable for pool-stat
        # grading, so return it with a reason the PoP graders surface.
        return series, None, "answer key carries no Priority-of-Payments to reconcile"
    try:
        from loanwhiz.primitives.reconciliation_answer_key import (
            reconcile_against_answer_key,
        )

        recon = reconcile_against_answer_key(series, answer_key)
        return series, recon, None
    except Exception as exc:  # noqa: BLE001 — per-deal defensive degradation (honest n/a)
        return series, None, f"reconcile error ({type(exc).__name__}): {exc}"


def build_quality_matrix(
    deals: Mapping[str, Mapping[str, Any]],
    *,
    seed_loader: Callable[[Mapping[str, Any]], DealModel | None],
    answer_key_loader: Callable[[Mapping[str, Any]], DealAnswerKey | None],
    series_provider: SeriesProvider | None = None,
    triggers_loader: Callable[[Mapping[str, Any]], list[Any]] | None = None,
) -> QualityMatrix:
    """Build the cross-deal graded quality matrix.

    Parameters
    ----------
    deals:
        The deal registry — ``{deal_id: deal-context dict}`` (the live
        ``DEAL_REGISTRY`` shape). Each context carries ``deal_name`` and
        optionally ``jurisdiction``.
    seed_loader:
        Loads a deal's committed extracted :class:`DealModel` from its context, or
        ``None`` on a miss (never a cold extraction). The API passes
        ``_load_cached_deal_model``; tests pass a fake.
    answer_key_loader:
        Loads a deal's committed :class:`DealAnswerKey` from its context, or
        ``None`` on a miss. The API passes ``load_answer_key``; tests inject
        synthetic keys.
    series_provider:
        Yields a deal's offline-folded engine :class:`DealStateSeries` (or
        ``None`` when none is committed). Defaults to the per-deal offline builders
        (today: Green Lion 2024-1's fixtures fold). The answer key is NOT used to
        fold — it carries ground truth, not the opening balances the fold seeds
        from. Tests inject a real or synthetic series.
    triggers_loader:
        Resolves a deal's extracted covenant triggers from its context. Defaults
        to a deferred import of ``api.main._extracted_triggers_to_definitions``.

    Returns
    -------
    QualityMatrix
        Every (check × deal) graded cell with its grade, score, EUR tolerance,
        honest reason and governance evidence, plus per-grade tally and the
        standing disclosure.
    """
    if series_provider is None:
        series_provider = _default_series_provider()
    if triggers_loader is None:
        triggers_loader = _default_triggers_loader()

    catalogue = _check_catalogue(triggers_loader)
    rows = [row for row, _ in catalogue]
    columns: list[QualityDealColumn] = []
    cells: list[GradedCell] = []
    tally: dict[str, int] = {GRADE_PASSED: 0, GRADE_FAILED: 0, GRADE_NOT_APPLICABLE: 0}

    for deal_id, deal_ctx in deals.items():
        model = seed_loader(deal_ctx)
        answer_key = answer_key_loader(deal_ctx)
        series, recon, fold_error = _reconcile_deal(
            deal_id, deal_ctx, model, answer_key, series_provider
        )
        ctx = _DealGrading(
            deal_id=deal_id,
            deal_ctx=deal_ctx,
            model=model,
            answer_key=answer_key,
            series=series,
            recon=recon,
            fold_error=fold_error,
        )
        columns.append(
            QualityDealColumn(
                deal_id=deal_id,
                deal_name=str(deal_ctx.get("deal_name", deal_id)),
                jurisdiction=_resolve_jurisdiction(deal_ctx),
                has_seed_model=model is not None,
                has_answer_key=answer_key is not None,
            )
        )
        for row, grader in catalogue:
            try:
                cell = grader(row.key, ctx)
            except Exception as exc:  # noqa: BLE001 — one bad deal can't sink the matrix
                cell = _na(
                    row.key,
                    deal_id,
                    f"Grader error ({type(exc).__name__}): {exc}.",
                )
            # Honesty contract: a not-applicable cell must carry a real reason.
            if cell.grade == GRADE_NOT_APPLICABLE and not cell.reason.strip():
                cell.reason = "Not applicable for this deal (inputs absent)."
            tally[cell.grade] = tally.get(cell.grade, 0) + 1
            cells.append(cell)

    return QualityMatrix(checks=rows, deals=columns, cells=cells, tally=tally)
