"""DAG executor — validates primitive outputs, gates on confidence, routes low-confidence to review.

The planner (#20) already runs a LangGraph ReAct loop and returns a
:class:`~loanwhiz.governance.evidence_pack.GovernanceEvidencePack` recording
every primitive (tool) call with a per-call confidence score. This module
layers a *validation + confidence-gating + human-review-routing* step on top
of that execution, producing a fully auditable :class:`ExecutionResult`.

It does **not** re-execute primitives itself; it consumes the planner's
evidence pack and:

1. Runs the query through the planner (:func:`loanwhiz.agent.planner.run_query`).
2. Validates each tool call's confidence against a threshold, classifying it
   ``PASSED`` / ``LOW_CONFIDENCE`` / ``NEEDS_REVIEW``.
3. Computes an aggregate confidence as the ``min`` of all step confidences —
   the most conservative choice, mirroring
   :meth:`GovernanceEvidencePack.create`'s own aggregate.
4. Derives an overall status and decides whether human review is required.
5. Emits a human-readable reasoning trace and returns the auditable result.

Retry hook
----------
``retry_threshold`` and ``max_retries`` define when a step is *eligible* for
re-invocation. Live re-invocation of the planner is intentionally left as a
documented hook for the hackathon (see :meth:`DAGExecutor._retry_eligible` and
the ``reasoning_trace`` note it produces) — the gating, audit trail, and
human-review routing are the load-bearing behaviours and are implemented in
full here.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from loanwhiz.agent.planner import run_query
from loanwhiz.governance.evidence_pack import GovernanceEvidencePack, ToolCallRecord

__all__ = [
    "ValidationStatus",
    "StepValidation",
    "ExecutionResult",
    "DAGExecutor",
    "execute_query",
]


# ---------------------------------------------------------------------------
# Validation status
# ---------------------------------------------------------------------------


class ValidationStatus(str, Enum):
    """Outcome of validating a single step or the overall execution.

    - ``PASSED``         — confidence at or above ``confidence_threshold``.
    - ``LOW_CONFIDENCE`` — below ``confidence_threshold`` but at or above
                           ``retry_threshold`` (retry-eligible, not review).
    - ``NEEDS_REVIEW``   — below ``retry_threshold`` (routed to human review).
    - ``FAILED``         — reserved for hard failures (e.g. a tool errored);
                           the planner currently surfaces such cases as low
                           confidence rather than raising, so this is a
                           forward-compatible status, not yet emitted by the
                           per-step gate.
    """

    PASSED = "passed"
    LOW_CONFIDENCE = "low_confidence"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Per-step validation record
# ---------------------------------------------------------------------------


class StepValidation(BaseModel):
    """Validation outcome for a single tool call in the executed DAG.

    Attributes:
        tool_name:  Name of the primitive/tool that produced this step.
        confidence: The step's confidence score [0.0, 1.0].
        status:     The :class:`ValidationStatus` assigned by the gate.
        note:       Human-readable note explaining the classification.
    """

    tool_name: str
    confidence: float
    status: ValidationStatus
    note: str


# ---------------------------------------------------------------------------
# Auditable execution result
# ---------------------------------------------------------------------------


class ExecutionResult(BaseModel):
    """Fully auditable result of executing a query through the planner + gate.

    Attributes:
        question:              The user's natural language question.
        answer:                The planner's final answer.
        overall_status:        Aggregate :class:`ValidationStatus` for the run.
        step_validations:      Per-step validation records, in call order.
        aggregate_confidence:  ``min`` of all step confidences. ``0.0`` when no
                               tool calls were made — an answer with no
                               primitive evidence behind it is *ungrounded*,
                               not maximally confident.
        human_review_required: ``True`` when the answer must be routed to a
                               human — fired by **either** trigger: the
                               aggregate confidence falling below
                               ``confidence_threshold`` (always ``True`` for an
                               ungrounded answer), **or** a grounded answer
                               (≥1 tool call) carrying zero citations (an
                               unauditable answer, flagged even at high
                               confidence). Equivalent to ``bool(review_reasons)``.
        review_reasons:        Machine-readable cause(s) the answer was gated for
                               human review, one human-readable string per fired
                               trigger. **Empty** exactly when
                               ``human_review_required`` is ``False`` — this is
                               the structured signal a downstream consumer
                               branches on instead of parsing the prose trace.
        evidence_pack_id:      ``pack_id`` of the governance evidence pack that
                               backs this result (links to the full audit log).
        reasoning_trace:       Human-readable, step-by-step trace of the run.
    """

    question: str
    answer: str
    overall_status: ValidationStatus
    step_validations: list[StepValidation]
    aggregate_confidence: float
    human_review_required: bool
    review_reasons: list[str] = []
    evidence_pack_id: str
    reasoning_trace: list[str]


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class DAGExecutor:
    """Executes a query through the planner with validation and confidence gating.

    Parameters
    ----------
    confidence_threshold:
        A step at or above this confidence ``PASSED``; the run requires human
        review when the aggregate (min) confidence falls below it.
    retry_threshold:
        A step below this confidence is routed to human review
        (``NEEDS_REVIEW``); a step between ``retry_threshold`` (inclusive) and
        ``confidence_threshold`` (exclusive) is ``LOW_CONFIDENCE`` and
        retry-eligible (see the documented retry hook).
    max_retries:
        Maximum re-invocations a retry-eligible step would be granted. The
        live re-invocation is a documented hook for the hackathon; the value
        is recorded and surfaced in the reasoning trace.
    """

    def __init__(
        self,
        confidence_threshold: float = 0.7,
        retry_threshold: float = 0.5,
        max_retries: int = 1,
    ) -> None:
        self.confidence_threshold = confidence_threshold
        self.retry_threshold = retry_threshold
        self.max_retries = max_retries

    # ------------------------------------------------------------------
    # Per-step classification
    # ------------------------------------------------------------------

    def _classify(self, confidence: float) -> ValidationStatus:
        """Classify a single step's confidence against the two thresholds."""
        if confidence >= self.confidence_threshold:
            return ValidationStatus.PASSED
        if confidence >= self.retry_threshold:
            return ValidationStatus.LOW_CONFIDENCE
        return ValidationStatus.NEEDS_REVIEW

    def _retry_eligible(self, status: ValidationStatus) -> bool:
        """Whether a step would be re-invoked by the (documented) retry hook.

        Retry hook (not wired live for the hackathon): a ``LOW_CONFIDENCE``
        step sits below ``confidence_threshold`` but above ``retry_threshold``,
        so re-invoking the planner up to ``max_retries`` times and keeping the
        highest-confidence result is the natural recovery. ``NEEDS_REVIEW``
        steps are too low to trust a retry — they go straight to a human.
        """
        return status == ValidationStatus.LOW_CONFIDENCE and self.max_retries > 0

    # ------------------------------------------------------------------
    # Overall status derivation
    # ------------------------------------------------------------------

    @staticmethod
    def _overall_status(step_validations: list[StepValidation]) -> ValidationStatus:
        """Derive the run-level status from the per-step statuses.

        - ``PASSED`` when every step passed (or there are no steps).
        - ``NEEDS_REVIEW`` when any step needs review (most conservative wins).
        - ``LOW_CONFIDENCE`` otherwise (at least one low-confidence, none
          needing review).
        """
        statuses = {sv.status for sv in step_validations}
        if ValidationStatus.NEEDS_REVIEW in statuses:
            return ValidationStatus.NEEDS_REVIEW
        if statuses <= {ValidationStatus.PASSED}:
            # Empty set (no steps) or only PASSED steps.
            return ValidationStatus.PASSED
        return ValidationStatus.LOW_CONFIDENCE

    # ------------------------------------------------------------------
    # Reasoning trace
    # ------------------------------------------------------------------

    def _build_trace(
        self,
        pack: GovernanceEvidencePack,
        step_validations: list[StepValidation],
        overall_status: ValidationStatus,
        human_review_required: bool,
        aggregate_confidence: float,
        review_reasons: list[str],
    ) -> list[str]:
        """Build a human-readable, step-by-step reasoning trace."""
        marks = {
            ValidationStatus.PASSED: "✓",          # check mark
            ValidationStatus.LOW_CONFIDENCE: "⚠",  # warning sign
            ValidationStatus.NEEDS_REVIEW: "⚑",    # flag
            ValidationStatus.FAILED: "✗",          # cross mark
        }

        trace: list[str] = []
        for tc, sv in zip(pack.tool_calls, step_validations):
            mark = marks.get(sv.status, "")
            detail = tc.input_summary or "(no input recorded)"
            line = (
                f"Called {sv.tool_name} on {detail} "
                f"→ confidence {sv.confidence:.2f} {mark}".rstrip()
            )
            if self._retry_eligible(sv.status):
                line += (
                    f" (below confidence threshold {self.confidence_threshold}; "
                    f"retry hook available, up to {self.max_retries} retr"
                    f"{'y' if self.max_retries == 1 else 'ies'})"
                )
            elif sv.status == ValidationStatus.NEEDS_REVIEW:
                line += (
                    f" (below retry threshold {self.retry_threshold}; "
                    "routed to human review)"
                )
            trace.append(line)

        n = len(pack.tool_calls)
        if n == 0:
            trace.append(
                "Answer synthesised from 0 tool calls — UNGROUNDED "
                "(no primitive evidence backs this answer)"
            )
        else:
            trace.append(
                f"Answer synthesised from {n} tool call{'' if n == 1 else 's'}"
            )
        trace.append(
            f"Aggregate confidence {aggregate_confidence:.2f} "
            f"({'min of step confidences' if n else 'ungrounded → pinned to 0.00'}) "
            f"vs threshold {self.confidence_threshold} "
            f"→ overall status: {overall_status.value}"
        )
        if human_review_required:
            trace.append("Routed to human review queue:")
            for reason in review_reasons:
                trace.append(f"  - {reason}")
        else:
            trace.append("No human review required")
        return trace

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def execute(self, question: str) -> ExecutionResult:
        """Run *question* through the planner, validate, gate, and audit.

        Parameters
        ----------
        question:
            Natural language question about the Green Lion 2026-1 deal.

        Returns
        -------
        ExecutionResult
            A fully auditable result with per-step validations, aggregate
            confidence, human-review routing, and a reasoning trace.
        """
        # 1. Run the query through the planner.
        response = run_query(question, save_evidence=True)
        pack: GovernanceEvidencePack = response["evidence_pack"]

        # 2. Validate each tool call's confidence.
        step_validations: list[StepValidation] = []
        for tc in pack.tool_calls:
            status = self._classify(tc.confidence)
            step_validations.append(
                StepValidation(
                    tool_name=tc.tool_name,
                    confidence=tc.confidence,
                    status=status,
                    note=(
                        f"confidence {tc.confidence:.2f} vs threshold "
                        f"{self.confidence_threshold}"
                    ),
                )
            )

        # 3. Aggregate confidence = min of all step confidences (most
        #    conservative). An answer produced from ZERO tool calls is
        #    *ungrounded* — no primitive evidence backs it — so it must NOT
        #    score 1.0/passed. Otherwise an LLM-only refusal or claim (e.g. the
        #    synthesise-fallback firing on a malformed tool turn) sails through
        #    the gate at max confidence. Ungrounded answers are pinned to 0.0.
        grounded = bool(step_validations)
        if grounded:
            aggregate_confidence = min(sv.confidence for sv in step_validations)
        else:
            aggregate_confidence = 0.0

        # 4. Determine overall status and human-review routing. An ungrounded
        #    answer is forced to NEEDS_REVIEW regardless of the (empty) step set.
        overall_status = (
            self._overall_status(step_validations)
            if grounded
            else ValidationStatus.NEEDS_REVIEW
        )

        # 4b. Build the structured review reasons — the machine-readable cause(s)
        #     that gate this answer for a human. The gate is *acted on* here, not
        #     merely observed: two independent triggers fire it, and
        #     ``human_review_required`` is exactly ``bool(review_reasons)`` so a
        #     downstream consumer can branch on the reasons instead of re-deriving
        #     the rule.
        #       (1) aggregate confidence below the threshold — including the
        #           ungrounded → 0.0 case (distinguished for the auditor); and
        #       (2) a *grounded* answer (≥1 tool call) whose evidence pack carries
        #           ZERO citations — primitive evidence with no traceable source is
        #           unauditable and must route to a human even at high confidence.
        review_reasons: list[str] = []
        if aggregate_confidence < self.confidence_threshold:
            if grounded:
                review_reasons.append(
                    f"aggregate confidence {aggregate_confidence:.2f} is below the "
                    f"review threshold {self.confidence_threshold}"
                )
            else:
                review_reasons.append(
                    "ungrounded answer — no tool calls / primitive evidence backs "
                    "this answer"
                )
        if grounded and not pack.all_citations:
            review_reasons.append(
                "grounded answer carries zero citations — no traceable source to "
                "audit the answer against"
            )
        human_review_required = bool(review_reasons)

        # 5. Build the human-readable reasoning trace.
        reasoning_trace = self._build_trace(
            pack,
            step_validations,
            overall_status,
            human_review_required,
            aggregate_confidence,
            review_reasons,
        )

        return ExecutionResult(
            question=question,
            answer=response["answer"],
            overall_status=overall_status,
            step_validations=step_validations,
            aggregate_confidence=aggregate_confidence,
            human_review_required=human_review_required,
            review_reasons=review_reasons,
            evidence_pack_id=pack.pack_id,
            reasoning_trace=reasoning_trace,
        )


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------


def execute_query(question: str, confidence_threshold: float = 0.7) -> ExecutionResult:
    """Execute a query with default validation settings.

    Convenience wrapper around :class:`DAGExecutor` for callers that only need
    to vary the confidence threshold.
    """
    return DAGExecutor(confidence_threshold=confidence_threshold).execute(question)
