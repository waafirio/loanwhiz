"""Comprehensiveness invariants over the S6 ``DealStateSeries`` (S8, #188).

This module is the **"comprehensive" half of the spine's proof** (#179). S6
(:mod:`loanwhiz.primitives.period_state_machine`) reconstructs a deal's full
per-period ``DealState`` series and, per transition, the two S4
``WaterfallExecution`` audit traces plus the S5 ``TriggerEvaluation``. S8 asserts
a suite of structural invariants *over that reconstructed series + its
provenance* and surfaces every violation as a **structured finding** — a typed
:class:`InvariantFinding`, not a bare ``assert`` — so downstream callers (S7
reconciliation, S9 endpoints) and operators get a machine-readable proof
artifact rather than a stack trace.

The invariants
--------------
Given the series ``S`` and its per-period results ``R`` (``len(R) == len(S)-1``):

(a) **Every executed waterfall step is accounted for.** For each period, the
    revenue / redemption execution trace must be 1:1 (recipient-for-recipient,
    in order) with the input ``StepSpec`` list the loop ran. A step that
    distributed 0 because it was *gated* or *not-evaluable* is present and
    accounted for; a step *missing from the trace* is the violation (a silently
    skipped step). ``error``.

(b) **Conservation of funds.** Per period, per pot (revenue, principal):
    ``total_distributed + remaining == available`` where ``available`` is
    re-derived independently from the period's collections (interest for the
    revenue pot; scheduled + prepayment + recovery for the principal pot). A
    mismatch beyond tolerance is an ``error``. Separately, any non-zero
    ``total_shortfall`` is surfaced as a ``warning`` — the honest report of an
    economic shortfall (e.g. the S6 ``reserve_draw=0`` finding: the loop never
    auto-draws the reserve to cover a senior revenue shortfall, so a stressed
    deal genuinely under-pays; S8 reports that fact rather than hiding it).

(c) **Non-negativity.** Every tranche balance, PDL ledger, reserve balance and
    cumulative-loss figure on every state is ``>= 0``. ``DealState`` already
    enforces this at construction (``ge=0.0``); the invariant re-asserts it over
    the *series* as defense-in-depth, so a future contract change that drops a
    clamp is caught here. ``error``.

(d) **Chaining ``closing[N] == opening[N+1]``.** S6 feeds each closing state
    forward as the next opening, so ``states[N+1]`` must equal
    ``period_results[N].closing_state`` on every structural field. A mismatch
    means the chain was broken. ``error``.

(e) **Cumulative-loss monotonicity.** ``states[N].cumulative_losses <=
    states[N+1].cumulative_losses`` — realized losses only accumulate. ``error``.

Pure & deterministic — no LLM, no network. Mirrors the immutable typed-pydantic
conventions of the surrounding primitives and the typed-finding shape of
:mod:`loanwhiz.primitives.report_verifier`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from loanwhiz.primitives.period_state_machine import (
    DealStateSeries,
    PeriodResult,
)
from loanwhiz.primitives.waterfall_interpreter import (
    StepSpec,
    WaterfallExecution,
)

# Default tolerance for the floating-point conservation / chaining comparisons.
# Monetary figures are EUR; 1e-6 is well below a cent, so a violation beyond it
# is a real accounting break, not fp noise.
_DEFAULT_TOLERANCE = 1e-6

# The structural ``DealState`` fields the chaining invariant compares across the
# closing[N] == opening[N+1] seam. Metadata that legitimately changes across the
# seam (``reporting_date``, ``period_index``, ``revolving``, ``collections``) is
# excluded — the seam asserts the *balances* carry forward unchanged, not the
# period stamp.
_CHAIN_FIELDS: tuple[str, ...] = (
    "class_a_balance",
    "class_b_balance",
    "class_c_balance",
    "class_a_pdl",
    "class_b_pdl",
    "class_c_pdl",
    "reserve_balance",
    "reserve_target",
    "cumulative_losses",
    "pool_balance",
    "original_pool_balance",
)

# The non-negative numeric fields the non-negativity invariant checks on every
# state.
_NON_NEGATIVE_FIELDS: tuple[str, ...] = (
    "class_a_balance",
    "class_b_balance",
    "class_c_balance",
    "class_a_pdl",
    "class_b_pdl",
    "class_c_pdl",
    "reserve_balance",
    "reserve_target",
    "cumulative_losses",
    "pool_balance",
)


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------


class InvariantFinding(BaseModel):
    """One invariant observation over the reconstructed series.

    A finding is emitted whenever an invariant is *not* satisfied (an ``error``)
    or surfaces a notable-but-not-incorrect fact (a ``warning`` — e.g. an honest
    economic shortfall). A clean series produces zero findings.

    Attributes
    ----------
    invariant:
        Stable machine name of the invariant — one of ``step_coverage``,
        ``conservation``, ``shortfall``, ``non_negative``, ``chaining``,
        ``loss_monotonicity``.
    severity:
        ``"error"`` for a true invariant violation; ``"warning"`` for a surfaced
        fact that does not make the model incorrect (the shortfall report).
    period_index:
        The ``period_index`` of the state / transition the finding concerns, or
        ``None`` when the finding is series-global.
    recipient:
        The waterfall recipient / field name the finding concerns, or ``None``.
    message:
        Human-readable one-line description.
    observed / expected:
        The numeric values that disagreed (for conservation / chaining / loss
        findings), or ``None`` when not applicable.
    """

    invariant: str
    severity: Literal["error", "warning"]
    period_index: int | None = None
    recipient: str | None = None
    message: str
    observed: float | None = None
    expected: float | None = None


class InvariantReport(BaseModel):
    """The full result of checking the comprehensiveness invariants over a series.

    Attributes
    ----------
    periods_checked:
        Number of transitions checked (``len(series.states) - 1``).
    findings:
        Every :class:`InvariantFinding` emitted, in check order.
    ok:
        ``True`` when no ``error``-severity finding was emitted (warnings do not
        flip ``ok`` — a surfaced shortfall is honest reporting, not a failure).
    summary:
        Human-readable one-line roll-up.
    """

    periods_checked: int
    findings: list[InvariantFinding] = Field(default_factory=list)
    ok: bool
    summary: str

    @property
    def errors(self) -> list[InvariantFinding]:
        """Only the ``error``-severity findings (true invariant violations)."""
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[InvariantFinding]:
        """Only the ``warning``-severity findings (surfaced facts, e.g. shortfall)."""
        return [f for f in self.findings if f.severity == "warning"]

    def by_invariant(self, name: str) -> list[InvariantFinding]:
        """All findings for a given invariant name."""
        return [f for f in self.findings if f.invariant == name]


# ---------------------------------------------------------------------------
# Individual invariant checks
# ---------------------------------------------------------------------------


def _check_step_coverage(
    result: PeriodResult,
    period_index: int,
    revenue_steps: list[StepSpec],
    redemption_steps: list[StepSpec],
) -> list[InvariantFinding]:
    """Invariant (a): every input step appears in the execution trace, in order.

    A step missing from the trace was silently skipped — the violation. A step
    present but ``gated`` / ``not_evaluable`` (distributing 0) is accounted for
    and is *not* a violation.
    """
    findings: list[InvariantFinding] = []
    for label, steps, execution in (
        ("revenue", revenue_steps, result.revenue_execution),
        ("redemption", redemption_steps, result.redemption_execution),
    ):
        expected = [s.recipient for s in steps]
        actual = [s.recipient for s in execution.steps]
        if actual == expected:
            continue
        # Length / order mismatch — report the specific recipients that went
        # missing (in the input but not the trace) and any extras.
        missing = [r for r in expected if r not in actual]
        for recipient in missing:
            findings.append(
                InvariantFinding(
                    invariant="step_coverage",
                    severity="error",
                    period_index=period_index,
                    recipient=recipient,
                    message=(
                        f"{label} waterfall: extracted step '{recipient}' was not "
                        f"executed (missing from the execution trace)."
                    ),
                    observed=float(len(actual)),
                    expected=float(len(expected)),
                )
            )
        if not missing:
            # Same set but wrong order/count (e.g. duplicated/reordered) — still
            # a coverage break worth one finding.
            findings.append(
                InvariantFinding(
                    invariant="step_coverage",
                    severity="error",
                    period_index=period_index,
                    recipient=None,
                    message=(
                        f"{label} waterfall: execution trace does not match the "
                        f"extracted step order/count "
                        f"(expected {expected}, got {actual})."
                    ),
                    observed=float(len(actual)),
                    expected=float(len(expected)),
                )
            )
    return findings


def _check_conservation(
    result: PeriodResult,
    period_index: int,
    tolerance: float,
) -> list[InvariantFinding]:
    """Invariant (b): per-pot conservation + honest shortfall reporting.

    ``available`` is re-derived independently from the period's recorded
    collections (so this is a genuine cross-check, not a tautology against the
    execution's own ``total_distributed + remaining``). Any non-zero shortfall
    is surfaced as a ``warning``.
    """
    findings: list[InvariantFinding] = []
    collections = result.closing_state.collections
    if collections is None:  # pragma: no cover - transition always sets it
        findings.append(
            InvariantFinding(
                invariant="conservation",
                severity="error",
                period_index=period_index,
                message="closing state has no recorded collections; cannot verify conservation.",
            )
        )
        return findings

    available_revenue = collections.interest
    available_principal = (
        collections.scheduled_principal
        + collections.prepayment
        + collections.recovery
    )

    for label, available, execution in (
        ("revenue", available_revenue, result.revenue_execution),
        ("principal", available_principal, result.redemption_execution),
    ):
        accounted = execution.total_distributed + execution.remaining
        if abs(accounted - available) > tolerance:
            findings.append(
                InvariantFinding(
                    invariant="conservation",
                    severity="error",
                    period_index=period_index,
                    recipient=label,
                    message=(
                        f"{label} pot not conserved: distributed + remaining "
                        f"({accounted:.6f}) != available ({available:.6f})."
                    ),
                    observed=accounted,
                    expected=available,
                )
            )
        # Honest shortfall report (e.g. reserve_draw=0 senior-interest gap).
        if execution.total_shortfall > tolerance:
            findings.append(
                InvariantFinding(
                    invariant="shortfall",
                    severity="warning",
                    period_index=period_index,
                    recipient=label,
                    message=(
                        f"{label} waterfall has an unmet shortfall of "
                        f"{execution.total_shortfall:.2f} this period "
                        f"(funds insufficient to meet all needs; no reserve draw applied)."
                    ),
                    observed=execution.total_shortfall,
                    expected=0.0,
                )
            )
    return findings


def _check_non_negative(
    series: DealStateSeries,
    tolerance: float,
) -> list[InvariantFinding]:
    """Invariant (c): all balances / ledgers / reserve / losses are non-negative."""
    findings: list[InvariantFinding] = []
    for state in series.states:
        for field in _NON_NEGATIVE_FIELDS:
            value = getattr(state, field)
            if value < -tolerance:
                findings.append(
                    InvariantFinding(
                        invariant="non_negative",
                        severity="error",
                        period_index=state.period_index,
                        recipient=field,
                        message=f"{field} is negative ({value:.6f}).",
                        observed=value,
                        expected=0.0,
                    )
                )
    return findings


def _check_chaining(
    series: DealStateSeries,
    tolerance: float,
) -> list[InvariantFinding]:
    """Invariant (d): ``states[N+1] == period_results[N].closing_state`` field-wise.

    S6 carries each closing state forward as the next opening, so the structural
    balance fields of ``states[N+1]`` must equal the closing state the period-N
    transition produced.
    """
    findings: list[InvariantFinding] = []
    for n, result in enumerate(series.period_results):
        closing = result.closing_state
        next_state = series.states[n + 1]
        for field in _CHAIN_FIELDS:
            produced = getattr(closing, field)
            carried = getattr(next_state, field)
            if abs(produced - carried) > tolerance:
                findings.append(
                    InvariantFinding(
                        invariant="chaining",
                        severity="error",
                        period_index=next_state.period_index,
                        recipient=field,
                        message=(
                            f"chaining broken at transition {n}: closing[{n}].{field} "
                            f"({produced:.6f}) != opening[{n + 1}].{field} ({carried:.6f})."
                        ),
                        observed=carried,
                        expected=produced,
                    )
                )
    return findings


def _check_loss_monotonicity(
    series: DealStateSeries,
    tolerance: float,
) -> list[InvariantFinding]:
    """Invariant (e): cumulative realized losses never decrease across the series."""
    findings: list[InvariantFinding] = []
    states = series.states
    for n in range(len(states) - 1):
        prev = states[n].cumulative_losses
        nxt = states[n + 1].cumulative_losses
        if nxt < prev - tolerance:
            findings.append(
                InvariantFinding(
                    invariant="loss_monotonicity",
                    severity="error",
                    period_index=states[n + 1].period_index,
                    recipient="cumulative_losses",
                    message=(
                        f"cumulative losses decreased from {prev:.6f} to {nxt:.6f} "
                        f"across transition {n}."
                    ),
                    observed=nxt,
                    expected=prev,
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_series(
    series: DealStateSeries,
    *,
    revenue_steps: list[StepSpec],
    redemption_steps: list[StepSpec],
    tolerance: float = _DEFAULT_TOLERANCE,
) -> InvariantReport:
    """Check the comprehensiveness invariants over a reconstructed series.

    Runs all five invariants ((a) step coverage, (b) conservation + shortfall,
    (c) non-negativity, (d) chaining, (e) loss monotonicity) and returns a typed
    :class:`InvariantReport` of structured findings. ``report.ok`` is ``True``
    iff no ``error``-severity finding was emitted; surfaced ``warning`` findings
    (an honest shortfall) do not flip ``ok``.

    Parameters
    ----------
    series:
        The S6 :class:`DealStateSeries` to validate.
    revenue_steps / redemption_steps:
        The ordered ``StepSpec`` lists the S6 loop executed — the ground truth
        the step-coverage invariant checks the execution traces against. Pass the
        same lists you passed to ``reconstruct_period_series`` (defaulting to the
        Green-Lion builtin lists if you used those).
    tolerance:
        Absolute tolerance for the fp conservation / chaining comparisons.

    Returns
    -------
    InvariantReport
        The structured findings + roll-up.
    """
    findings: list[InvariantFinding] = []

    # Series-global invariants.
    findings.extend(_check_non_negative(series, tolerance))
    findings.extend(_check_chaining(series, tolerance))
    findings.extend(_check_loss_monotonicity(series, tolerance))

    # Per-transition invariants.
    for n, result in enumerate(series.period_results):
        period_index = result.closing_state.period_index
        findings.extend(
            _check_step_coverage(result, period_index, revenue_steps, redemption_steps)
        )
        findings.extend(_check_conservation(result, period_index, tolerance))

    periods_checked = max(0, len(series.states) - 1)
    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    ok = not errors

    if ok and not warnings:
        summary = (
            f"All comprehensiveness invariants hold over {periods_checked} "
            f"transition(s); no findings."
        )
    elif ok:
        summary = (
            f"All comprehensiveness invariants hold over {periods_checked} "
            f"transition(s); {len(warnings)} warning(s) surfaced "
            f"(e.g. shortfall reporting)."
        )
    else:
        summary = (
            f"{len(errors)} invariant violation(s) over {periods_checked} "
            f"transition(s); {len(warnings)} warning(s) surfaced."
        )

    return InvariantReport(
        periods_checked=periods_checked,
        findings=findings,
        ok=ok,
        summary=summary,
    )


class InvariantViolation(AssertionError):
    """Raised by :func:`assert_series_invariants` when an ``error`` finding exists.

    Carries the :class:`InvariantReport` so a caller catching it can inspect the
    structured findings rather than re-parsing the message.
    """

    def __init__(self, report: InvariantReport) -> None:
        self.report = report
        lines = "\n".join(f"  - {f.message}" for f in report.errors)
        super().__init__(f"{report.summary}\n{lines}")


def assert_series_invariants(
    series: DealStateSeries,
    *,
    revenue_steps: list[StepSpec],
    redemption_steps: list[StepSpec],
    tolerance: float = _DEFAULT_TOLERANCE,
) -> InvariantReport:
    """Hard-gate wrapper: raise :class:`InvariantViolation` on any ``error`` finding.

    Runs :func:`check_series` and raises if any ``error``-severity finding was
    emitted (warnings are tolerated — they are honest surfaced facts, not
    violations). Returns the :class:`InvariantReport` when the series is clean so
    callers can still inspect any warnings.
    """
    report = check_series(
        series,
        revenue_steps=revenue_steps,
        redemption_steps=redemption_steps,
        tolerance=tolerance,
    )
    if not report.ok:
        raise InvariantViolation(report)
    return report
