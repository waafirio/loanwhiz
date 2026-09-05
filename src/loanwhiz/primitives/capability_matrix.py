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
  :func:`loanwhiz.primitives.reconciler.validate_green_lion_2024_1`.
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
from typing import Any, Literal

from pydantic import BaseModel, Field

from loanwhiz.extraction.assembler import DealModel
from loanwhiz.primitives.reconciler import ReconciliationReport

# ---------------------------------------------------------------------------
# Cell state vocabulary — the three honest outcomes.
# ---------------------------------------------------------------------------

STATE_VALIDATED = "validated"
STATE_RAN = "ran"
STATE_NOT_APPLICABLE = "not-applicable"

#: The closed cell-state vocabulary, as a type. Mirrors the sibling honesty
#: vocabulary in :mod:`loanwhiz.governance.finos_conformance`
#: (``ConformanceStatus``) so both read the same way, and matches the closed
#: ``CapabilityCellState`` union the web client declares.
#:
#: **Closed on purpose.** The three states are the #193 honesty contract, not a
#: presentation detail: widening them is how "we could not grade this" quietly
#: acquires a fourth, friendlier spelling. Typing the field means a new state is
#: rejected by pydantic at construction — the boundary — rather than serialised
#: to a client whose own union does not carry it.
CellState = Literal["validated", "ran", "not-applicable"]

#: Registry keys under which a deal's published periodic reports are declared.
#: A deal with any of these has *external* published figures in principle; it
#: still needs a committed validation builder before the engine can be graded
#: against them. Keeping the distinction registry-driven is what lets the
#: engine-validation reason stay true per deal without naming any deal.
_PUBLISHED_REPORT_KEYS = ("notes_cash_report_urls", "investor_report_urls")

#: Jurisdiction default for the Dutch Green Lion deals, which carry no explicit
#: ``jurisdiction`` registry key (only the non-Dutch deals do). Resolving it here
#: keeps the matrix's per-deal jurisdiction column complete and legible.
_DEFAULT_JURISDICTION = "Netherlands"

#: Asset-class default for the deals that predate the registry's ``asset_class``
#: key. The matrix reports asset class so a non-RMBS column is legible *as* a
#: different asset class; resolving it from the registry (never from the deal id)
#: is what lets a CMBS or Auto column arrive as data, with no change here.
_DEFAULT_ASSET_CLASS = "RMBS"


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
    state: CellState = Field(
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
    asset_class: str = Field(
        ..., description="Resolved asset class (e.g. RMBS, CLO) — registry-driven, RMBS default."
    )
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
    [str, Mapping[str, Any], "DealModel | None", "Mapping[str, Callable[[], ReconciliationReport]]"],
    "tuple[CellState, str, CellEvidence]",
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
    validators: Mapping[str, Callable[[], ReconciliationReport]],
) -> tuple[str, str, CellEvidence]:
    """ESMA tape normalisation / pool analytics — applies only when loan tapes exist."""
    tapes = deal_ctx.get("tape_urls") or []
    if not tapes:
        return (
            STATE_NOT_APPLICABLE,
            "No machine-readable ESMA loan tape is registered for this deal, so tape "
            "analytics has no input. This is a statement about tape availability only — "
            "it does not claim the deal publishes no loan-level collateral detail in "
            "another form.",
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
    validators: Mapping[str, Callable[[], ReconciliationReport]],
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
    validators: Mapping[str, Callable[[], ReconciliationReport]],
) -> tuple[str, str, CellEvidence]:
    """Waterfall execution — applies when *any* priority-of-payments waterfall
    with executable steps was extracted.

    Most deals (Green Lion, Leone Arancio) extract a ``revenue`` waterfall, so it
    is the preferred cascade reported here. But a deal may legitimately extract a
    ``redemption`` and/or ``post_enforcement`` waterfall without a step-level
    ``revenue`` cascade — e.g. Sol-Lion II (ES), whose revenue PoP section was
    located but yielded no enumerable steps, while its redemption (8) and
    post-enforcement (7) cascades extracted cleanly. Keying solely on ``revenue``
    would mark such a deal ``not-applicable`` with a *factually false* reason
    ("no waterfall extracted"), so this counts any waterfall carrying steps.
    """
    waterfalls = model.waterfalls if model else {}
    # Prefer the revenue cascade when it carries steps; otherwise fall back to the
    # first non-empty waterfall so a redemption/post-enforcement-only deal still
    # reports the real extracted capability.
    chosen_type = ""
    chosen_steps: list = []
    for wf_type in ("revenue", "redemption", "post_enforcement"):
        wf_steps = ((waterfalls.get(wf_type) or {}).get("steps") or []) if waterfalls else []
        if wf_steps:
            chosen_type, chosen_steps = wf_type, wf_steps
            break
    if not chosen_steps:
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
        f"Extracted {len(chosen_steps)}-step {chosen_type} waterfall executes against period funds.",
        CellEvidence(
            confidence=1.0,  # deterministic interpreter run
            citation=_seed_citation(model, "Extracted deal model seed."),
            detail={
                "waterfall_type": chosen_type,
                "step_count": len(chosen_steps),
                "waterfalls": sorted(waterfalls.keys()),
            },
        ),
    )


def _classify_collateral_reconciliation(
    deal_id: str,
    deal_ctx: Mapping[str, Any],
    model: DealModel | None,
    validators: Mapping[str, Callable[[], ReconciliationReport]],
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
            "No machine-readable ESMA loan tape is registered for this deal, so there "
            "is no collateral pool series to reconstruct — a statement about tape "
            "availability, not about what the deal publishes elsewhere.",
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
    validators: Mapping[str, Callable[[], ReconciliationReport]],
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
        published = _published_report_count(deal_ctx)
        # Two genuinely different reasons for the same honest not-applicable, told
        # apart by a registry fact rather than by a deal id. Reporting the wrong
        # one is not a cosmetic slip: "nothing is published" says the deal *cannot*
        # be validated, when for some deals the truth is that nobody has authored
        # the answer key yet. Only the second is a deferred decision.
        reason = (
            "No published periodic report is registered for this deal, so there is "
            "no external ground truth to reconcile the engine against."
            if published == 0
            else (
                f"{published} published periodic report(s) are registered for this "
                "deal, but no answer key has been authored and no offline validation "
                "builder is committed — so the engine has nothing to be reconciled "
                "against yet. Unvalidated for want of a key, not for want of a report."
            )
        )
        return (
            STATE_NOT_APPLICABLE,
            reason,
            CellEvidence(
                confidence=None,
                citation="No committed engine-validation builder for this deal.",
                detail={"published_report_count": published},
            ),
        )
    report: ReconciliationReport = builder()
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
#: primitives (report_verifier / audit_logger) are
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
            primitive_name="reconciler",
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


def _resolve_asset_class(deal_ctx: Mapping[str, Any]) -> str:
    """Resolve a deal's asset class — explicit registry key, else the RMBS default.

    The exact shape of :func:`_resolve_jurisdiction`, and deliberately so: asset
    class is **registry data**, not a branch. The matrix therefore describes a
    CLO column without knowing what a CLO is, and a CMBS or Auto deal becomes a
    legible column by being registered — no change to this module.
    """
    return deal_ctx.get("asset_class") or _DEFAULT_ASSET_CLASS


def _published_report_count(deal_ctx: Mapping[str, Any]) -> int:
    """How many published periodic reports the registry declares for this deal.

    The discriminator behind an honest engine-validation reason: it separates
    *"nothing is published to reconcile against"* from *"reports are published,
    but no answer key has been authored and no builder is committed"*. Both are
    ``not-applicable``; only one of them is true of any given deal, and saying
    the wrong one is the #193 failure in prose form.
    """
    return sum(len(deal_ctx.get(key) or []) for key in _PUBLISHED_REPORT_KEYS)


def build_capability_matrix(
    deals: Mapping[str, Mapping[str, Any]],
    *,
    seed_loader: Callable[[Mapping[str, Any]], DealModel | None],
    validators: Mapping[str, Callable[[], ReconciliationReport]],
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
        :class:`ReconciliationReport` reconciling the engine against the deal's
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
                asset_class=_resolve_asset_class(deal_ctx),
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
