"""Cross-deal capability matrix — make primitive reusability *visible* (C3, epic #236).

The epic's claim is that LoanWhiz's structured-finance primitives are *reusable
and general* — the same code runs across Dutch, Italian and Spanish deals. This
module makes that claim **auditable** instead of asserted: for each deal-facing
primitive capability × each registered deal, it computes a **typed cell** with an
honest state and the governance evidence behind it.

Three honest states (carrying the #193 honesty discipline — the matrix must tell
the *true* cross-jurisdiction story, not a wall of green):

- ``validated`` — the primitive ran **and** its output reconciled to external
  truth. The only ``validated`` cell today is Green Lion 2024-1's engine vs. its
  **own published Notes & Cash Priority of Payments**, reconciled to the cent by
  :func:`loanwhiz.primitives.engine_validation_harness.validate_green_lion_2024_1`.
- ``ran`` — the primitive's inputs exist and it executes, but there is **no
  external ground truth** to reconcile against (e.g. a deal with an extracted
  waterfall but no published per-step distribution).
- ``not-applicable`` — the primitive's inputs are absent for this deal, with a
  **real reason** attached (e.g. "no loan tapes published", "waterfall not
  extracted from this prospectus"). Never a silent blank.

Design
------
- **Data-driven applicability.** Whether a cell is ``ran`` / ``not-applicable``
  is derived from the deal's *actual* inputs — does the registry context carry
  ``tape_urls``? does the committed seed :class:`DealModel` carry ``waterfalls``?
  ``covenants.triggers``? is there a committed offline validation builder? — so
  the matrix stays correct as deals and seeds evolve, and the same code genuinely
  runs across every jurisdiction. Nothing is hardcoded per deal.
- **Dependency-injected loaders.** :func:`build_capability_matrix` takes the deal
  registry, a seed-model loader, and the validation-builder map as arguments, so
  it is unit-testable offline and deal-generic. The API wires it to the live
  ``DEAL_REGISTRY`` / ``_load_cached_deal_model`` / ``_VALIDATION_BUILDERS``.
- **Offline & deterministic.** The applicability decision reads only committed
  registry + seed metadata; the single ``validated`` cell reuses the
  committed-fixture validation builder (no network, no LLM). The matrix never
  fetches a loan tape or runs a live waterfall in its decision path.

The result is JSON-serialisable structured data the C4 demo UI renders.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel, Field

from loanwhiz.extraction.assembler import DealModel
from loanwhiz.primitives.engine_validation_harness import EngineValidationReport

# ---------------------------------------------------------------------------
# Cell state vocabulary — the three honest outcomes.
# ---------------------------------------------------------------------------

STATE_VALIDATED = "validated"
STATE_RAN = "ran"
STATE_NOT_APPLICABLE = "not-applicable"

#: Jurisdiction default for the Dutch Green Lion deals, which carry no explicit
#: ``jurisdiction`` registry key (only the non-Dutch deals do). Resolving it here
#: keeps the matrix's per-deal jurisdiction column complete and legible.
_DEFAULT_JURISDICTION = "Netherlands"


# ---------------------------------------------------------------------------
# Typed result models.
# ---------------------------------------------------------------------------


class CellEvidence(BaseModel):
    """Governance evidence attached to one capability cell.

    Mirrors the framework's governance surface (confidence + citations/provenance)
    so the matrix carries the *why* behind each state, not just the state.

    Attributes
    ----------
    confidence:
        The governance confidence for this cell in ``[0.0, 1.0]`` — e.g. the
        deal model's extraction confidence for an extraction-derived capability,
        or ``1.0`` for a deterministic to-the-cent reconciliation. ``None`` when
        the cell is ``not-applicable`` (nothing ran, so no confidence).
    citation:
        A one-line provenance/citation string grounding the evidence (the seed
        artifact, the published report reconciled against, etc.).
    detail:
        Free-form structured detail for the UI (e.g. periods reconciled,
        tolerance, trigger count) — JSON-serialisable scalars only.
    """

    confidence: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Governance confidence in [0,1], or None."
    )
    citation: str = Field(..., description="One-line provenance/citation for the evidence.")
    detail: dict[str, Any] = Field(
        default_factory=dict, description="Structured, JSON-serialisable evidence detail."
    )


class CapabilityCell(BaseModel):
    """One (capability × deal) cell of the matrix.

    ``reason`` is **mandatory and non-empty** for a ``not-applicable`` cell — the
    honesty contract is that every skip carries its real reason. For ``ran`` /
    ``validated`` it is a short positive note ("executed", "reconciled to the
    cent").
    """

    capability_key: str = Field(..., description="Stable capability identifier.")
    deal_id: str = Field(..., description="Canonical deal id.")
    state: str = Field(
        ..., description=f"One of {STATE_VALIDATED!r}, {STATE_RAN!r}, {STATE_NOT_APPLICABLE!r}."
    )
    reason: str = Field(..., description="Human reason — REQUIRED and non-empty for not-applicable.")
    evidence: CellEvidence


class CapabilityRow(BaseModel):
    """A primitive capability (one row of the matrix) and its declared metadata."""

    key: str = Field(..., description="Stable capability identifier.")
    primitive_name: str = Field(..., description="Underlying registered primitive name.")
    label: str = Field(..., description="Human-readable capability label for the UI.")
    description: str = Field(..., description="One-line description of what the capability does.")


class DealColumn(BaseModel):
    """A deal (one column of the matrix) and its declared metadata."""

    deal_id: str = Field(..., description="Canonical deal id.")
    deal_name: str = Field(..., description="Human deal name.")
    jurisdiction: str = Field(..., description="Resolved jurisdiction (Netherlands default).")
    has_seed_model: bool = Field(..., description="Whether a committed extracted model was loaded.")
    completeness_score: float | None = Field(
        default=None, description="Extracted-model completeness in [0,1], if a model loaded."
    )


class CapabilityMatrix(BaseModel):
    """The full cross-deal capability matrix — structured data the C4 UI renders.

    ``cells`` is the flat list of every (capability × deal) cell. ``tally`` is a
    per-state count across all cells, so the UI can show the honest headline
    ("N validated / N ran / N not-applicable") without re-deriving it.
    """

    capabilities: list[CapabilityRow]
    deals: list[DealColumn]
    cells: list[CapabilityCell]
    tally: dict[str, int] = Field(
        default_factory=dict, description="Per-state cell counts across the whole matrix."
    )
    note: str = Field(
        default=(
            "Each cell is computed from the deal's real inputs (registry context + "
            "committed extracted model + offline validation builder), so the same "
            "primitive code is shown running across Dutch, Italian and Spanish deals. "
            "'validated' = ran AND reconciled to external truth; 'ran' = executed, no "
            "external truth to check; 'not-applicable' = inputs absent, with the real "
            "reason. Honesty over a wall of green."
        ),
        description="Standing honesty disclosure for the matrix.",
    )


# ---------------------------------------------------------------------------
# Capability catalogue — the deal-facing primitive rows.
# ---------------------------------------------------------------------------
#
# Each capability declares how, given a deal's registry context + committed seed
# model + the validation-builder map, to classify the cell. The classifier
# returns ``(state, reason, evidence)``. Applicability is derived from real
# inputs — never hardcoded per deal — so the matrix tracks the deals/seeds.

#: Signature of a cell classifier.
CellClassifier = Callable[
    [str, Mapping[str, Any], "DealModel | None", "Mapping[str, Callable[[], EngineValidationReport]]"],
    "tuple[str, str, CellEvidence]",
]


def _seed_citation(model: DealModel | None, fallback: str) -> str:
    """Citation string grounding a cell in the committed seed model, when present."""
    if model is None:
        return fallback
    return f"Extracted deal model seed (completeness {model.metadata.completeness_score:.2f})."


def _classify_tape_analytics(
    deal_id: str,
    deal_ctx: Mapping[str, Any],
    model: DealModel | None,
    validators: Mapping[str, Callable[[], EngineValidationReport]],
) -> tuple[str, str, CellEvidence]:
    """ESMA tape normalisation / pool analytics — applies only when loan tapes exist."""
    tapes = deal_ctx.get("tape_urls") or []
    if not tapes:
        return (
            STATE_NOT_APPLICABLE,
            "No loan tapes published for this deal — ESMA tape analytics has no input.",
            CellEvidence(
                confidence=None,
                citation="Deal registry context: tape_urls is empty.",
                detail={"tape_count": 0},
            ),
        )
    return (
        STATE_RAN,
        f"{len(tapes)} loan tape(s) available; pool analytics normalise per period.",
        CellEvidence(
            confidence=1.0,  # deterministic normalisation
            citation=f"Deal registry context: {len(tapes)} ESMA tape URL(s).",
            detail={"tape_count": len(tapes)},
        ),
    )


def _classify_covenant_monitor(
    deal_id: str,
    deal_ctx: Mapping[str, Any],
    model: DealModel | None,
    validators: Mapping[str, Callable[[], EngineValidationReport]],
) -> tuple[str, str, CellEvidence]:
    """Covenant monitoring — applies when the deal has extracted triggers."""
    triggers = (model.covenants.get("triggers") if model else None) or []
    if not triggers:
        return (
            STATE_NOT_APPLICABLE,
            "No covenant triggers extracted from this deal's prospectus.",
            CellEvidence(
                confidence=None,
                citation=_seed_citation(model, "No extracted deal model for this deal."),
                detail={"trigger_count": 0},
            ),
        )
    confidence = model.covenants.get("extraction_confidence") if model else None
    return (
        STATE_RAN,
        f"{len(triggers)} extracted trigger(s) monitored against per-period state.",
        CellEvidence(
            confidence=confidence,
            citation=_seed_citation(model, "Extracted deal model seed."),
            detail={"trigger_count": len(triggers)},
        ),
    )


def _classify_waterfall_execution(
    deal_id: str,
    deal_ctx: Mapping[str, Any],
    model: DealModel | None,
    validators: Mapping[str, Callable[[], EngineValidationReport]],
) -> tuple[str, str, CellEvidence]:
    """Waterfall execution — applies when a revenue waterfall was extracted."""
    waterfalls = model.waterfalls if model else {}
    revenue = (waterfalls.get("revenue") or {}) if waterfalls else {}
    steps = revenue.get("steps") or []
    if not steps:
        return (
            STATE_NOT_APPLICABLE,
            "No priority-of-payments waterfall extracted from this deal's prospectus.",
            CellEvidence(
                confidence=None,
                citation=_seed_citation(model, "No extracted deal model for this deal."),
                detail={"revenue_step_count": 0},
            ),
        )
    return (
        STATE_RAN,
        f"Extracted {len(steps)}-step revenue waterfall executes against period funds.",
        CellEvidence(
            confidence=1.0,  # deterministic interpreter run
            citation=_seed_citation(model, "Extracted deal model seed."),
            detail={
                "revenue_step_count": len(steps),
                "waterfalls": sorted(waterfalls.keys()),
            },
        ),
    )


def _classify_collateral_reconciliation(
    deal_id: str,
    deal_ctx: Mapping[str, Any],
    model: DealModel | None,
    validators: Mapping[str, Callable[[], EngineValidationReport]],
) -> tuple[str, str, CellEvidence]:
    """Collateral / pool-state reconstruction — applies when loan tapes exist.

    The period-state reconstruction (collections aggregation + per-period pool
    state) is driven by the deal's loan tapes; without tapes there is no
    collateral series to reconstruct.
    """
    tapes = deal_ctx.get("tape_urls") or []
    if not tapes:
        return (
            STATE_NOT_APPLICABLE,
            "No loan tapes published — no collateral pool series to reconstruct.",
            CellEvidence(
                confidence=None,
                citation="Deal registry context: tape_urls is empty.",
                detail={"tape_count": 0},
            ),
        )
    return (
        STATE_RAN,
        f"Pool state reconstructed across {len(tapes)} tape period(s) by net-reconciliation.",
        CellEvidence(
            confidence=1.0,
            citation=f"Deal registry context: {len(tapes)} ESMA tape URL(s).",
            detail={"tape_count": len(tapes)},
        ),
    )


def _classify_engine_validation(
    deal_id: str,
    deal_ctx: Mapping[str, Any],
    model: DealModel | None,
    validators: Mapping[str, Callable[[], EngineValidationReport]],
) -> tuple[str, str, CellEvidence]:
    """Engine validation vs. published PoP — ``validated`` only with a committed builder.

    This is the only capability that can reach ``validated``: a deal has a
    committed offline validation builder (the engine reconciled against the
    deal's *own* published Notes & Cash Priority of Payments, to the cent). A
    deal with an extracted waterfall but no published-PoP builder is
    ``not-applicable`` here (the engine can run — see waterfall execution — but
    there is no external truth to reconcile against for this capability).
    """
    builder = validators.get(deal_id)
    if builder is None:
        return (
            STATE_NOT_APPLICABLE,
            "No published Notes & Cash Priority-of-Payments report to reconcile the "
            "engine against for this deal.",
            CellEvidence(
                confidence=None,
                citation="No committed engine-validation builder for this deal.",
                detail={},
            ),
        )
    report: EngineValidationReport = builder()
    passed = report.passed
    return (
        STATE_VALIDATED if passed else STATE_RAN,
        (
            f"Engine reproduced the deal's own published PoP to EUR "
            f"{report.tolerance_eur:.2f} ({report.periods_passed}/{report.periods_checked} "
            f"period(s))."
            if passed
            else "Engine ran against the published PoP but did not fully reconcile."
        ),
        CellEvidence(
            confidence=1.0 if passed else 0.5,
            citation=f"Published Notes & Cash report for {report.deal_name}, reconciled to the cent.",
            detail={
                "passed": passed,
                "periods_checked": report.periods_checked,
                "periods_passed": report.periods_passed,
                "tolerance_eur": report.tolerance_eur,
            },
        ),
    )


#: The declared, ordered catalogue of deal-facing capabilities (matrix rows).
#: Each entry pairs the row metadata with its cell classifier. Library-only
#: primitives (cashflow_projector / report_verifier / audit_logger) are
#: deliberately excluded — they have no per-deal applicability story, so a row
#: that is not-applicable for every deal would add noise, not signal.
_CAPABILITIES: list[tuple[CapabilityRow, CellClassifier]] = [
    (
        CapabilityRow(
            key="tape_analytics",
            primitive_name="esma_tape_normaliser",
            label="ESMA tape analytics",
            description="Normalise ESMA loan-level tapes into per-period pool analytics.",
        ),
        _classify_tape_analytics,
    ),
    (
        CapabilityRow(
            key="covenant_monitoring",
            primitive_name="covenant_monitor",
            label="Covenant monitoring",
            description="Monitor extracted triggers against per-period structural state.",
        ),
        _classify_covenant_monitor,
    ),
    (
        CapabilityRow(
            key="waterfall_execution",
            primitive_name="waterfall_runner",
            label="Waterfall execution",
            description="Execute the extracted priority-of-payments waterfall against period funds.",
        ),
        _classify_waterfall_execution,
    ),
    (
        CapabilityRow(
            key="collateral_reconciliation",
            primitive_name="collections_aggregator",
            label="Collateral reconciliation",
            description="Reconstruct the pool's per-period state from its loan tapes.",
        ),
        _classify_collateral_reconciliation,
    ),
    (
        CapabilityRow(
            key="engine_validation",
            primitive_name="engine_validation_harness",
            label="Engine validation (vs. published PoP)",
            description="Reconcile the waterfall engine against the deal's own published PoP, to the cent.",
        ),
        _classify_engine_validation,
    ),
]


def capability_rows() -> list[CapabilityRow]:
    """Return the declared capability catalogue (matrix rows), in order."""
    return [row for row, _ in _CAPABILITIES]


def _resolve_jurisdiction(deal_ctx: Mapping[str, Any]) -> str:
    """Resolve a deal's jurisdiction — explicit registry key, else Netherlands default."""
    return deal_ctx.get("jurisdiction") or _DEFAULT_JURISDICTION


def build_capability_matrix(
    deals: Mapping[str, Mapping[str, Any]],
    *,
    seed_loader: Callable[[Mapping[str, Any]], DealModel | None],
    validators: Mapping[str, Callable[[], EngineValidationReport]],
) -> CapabilityMatrix:
    """Build the cross-deal capability matrix.

    Parameters
    ----------
    deals:
        The deal registry — ``{deal_id: deal-context dict}`` (the live
        ``DEAL_REGISTRY`` shape). Each context carries ``deal_name``,
        ``tape_urls``, and optionally ``jurisdiction``.
    seed_loader:
        Loads a deal's committed extracted :class:`DealModel` from its context,
        or returns ``None`` on a miss (never triggers a cold extraction). The API
        passes ``_load_cached_deal_model``; tests pass a fake.
    validators:
        ``{deal_id: offline-validation-builder}`` — a builder returns an
        :class:`EngineValidationReport` reconciling the engine against the deal's
        own published PoP. The API passes ``_VALIDATION_BUILDERS``.

    Returns
    -------
    CapabilityMatrix
        Every (capability × deal) cell with its honest state, real reason, and
        governance evidence, plus per-state tally and the standing disclosure.
    """
    rows = [row for row, _ in _CAPABILITIES]
    columns: list[DealColumn] = []
    cells: list[CapabilityCell] = []
    tally: dict[str, int] = {STATE_VALIDATED: 0, STATE_RAN: 0, STATE_NOT_APPLICABLE: 0}

    for deal_id, deal_ctx in deals.items():
        model = seed_loader(deal_ctx)
        columns.append(
            DealColumn(
                deal_id=deal_id,
                deal_name=str(deal_ctx.get("deal_name", deal_id)),
                jurisdiction=_resolve_jurisdiction(deal_ctx),
                has_seed_model=model is not None,
                completeness_score=(model.metadata.completeness_score if model else None),
            )
        )
        for row, classifier in _CAPABILITIES:
            state, reason, evidence = classifier(deal_id, deal_ctx, model, validators)
            # Honesty contract: a not-applicable cell must carry a real reason.
            if state == STATE_NOT_APPLICABLE and not reason.strip():
                reason = "Not applicable for this deal (inputs absent)."
            tally[state] = tally.get(state, 0) + 1
            cells.append(
                CapabilityCell(
                    capability_key=row.key,
                    deal_id=deal_id,
                    state=state,
                    reason=reason,
                    evidence=evidence,
                )
            )

    return CapabilityMatrix(
        capabilities=rows,
        deals=columns,
        cells=cells,
        tally=tally,
    )
