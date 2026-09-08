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
  **real reason** attached (e.g. "no ESMA loan tape is registered", "waterfall
  not extracted from this prospectus"). Never a silent blank, and never a claim
  wider than the registry fact behind it: "no tape is registered" is a statement
  about this repo, whereas "no loan tapes published" — the wording #457
  retracted, kept here only as the counter-example — is a claim about the world.

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
from loanwhiz.primitives.capital_structure import (
    CapitalStructure,
    senior_tranche_name,
)
from loanwhiz.primitives.derived_tape import source_kind_for
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
            "primitive code is shown running across every registered deal, whatever "
            "its jurisdiction or asset class — each column names both. "
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


#: ``deals.json`` keys the tape-driven period reconstruction needs before it can
#: fold a registered tape into a period series. Mirrors what
#: ``loanwhiz.api.main._resolve_structural_config`` demands; the two are pinned
#: to each other by ``test_capability_matrix`` rather than left to drift, because
#: a matrix cell claiming a reconstruction the endpoint refuses is exactly the
#: flattering half-truth these reasons exist to prevent.
ENGINE_STRUCTURAL_CONFIG_KEYS = (
    "capital_structure",
    "reserve_account_target",
    "original_pool_balance",
)

#: The one deal whose registry context legitimately omits the keys above: its
#: structural config lives in the ``_GREEN_LION_*`` constants the resolver falls
#: back to. Kept in sync with ``loanwhiz.api.main._GREEN_LION_DEAL_ID``.
_GREEN_LION_DEAL_ID = "green-lion-2026-1"


def _missing_structural_config(
    deal_id: str, deal_ctx: Mapping[str, Any], model: DealModel | None
) -> tuple[str, ...]:
    """Structural-config keys this deal cannot resolve, in the resolver's order.

    Empty means the tape-driven reconstruction has the configuration it needs.
    ``capital_structure`` also resolves from the deal's extracted tranche stack
    (the resolver's tier 2), so a deal is only short of it when the seed cannot
    supply a placeable stack either.

    The senior **coupon** is reported as its own key (#478). Balances and coupons
    resolve as separate tiers up in the resolver, because a deal routinely states
    its whole stack while quoting the notes as ``INDEX + margin`` — so "we know
    the classes but not the rate" is a distinct state. Naming it here is what
    keeps the cell's reason pointing at the thing the endpoint actually refuses
    on: Cairn resolves eight classes and is short only its coupon, and a matrix
    that answered "missing capital_structure" would send the reader to write a
    structure the seed already carries.
    """
    if deal_id == _GREEN_LION_DEAL_ID:
        return ()
    missing: list[str] = []
    for key in ENGINE_STRUCTURAL_CONFIG_KEYS:
        declared = deal_ctx.get(key)
        if key == "capital_structure":
            structure = declared if declared is not None else _extracted_structure(model)
            if structure is None:
                missing.append(key)
                continue
            coupon_key = _missing_senior_coupon_key(structure)
            if coupon_key is not None:
                missing.append(coupon_key)
            continue
        if declared is None:
            missing.append(key)
    return tuple(missing)


def _extracted_structure(model: DealModel | None) -> dict[str, float] | None:
    """The engine-shaped capital structure the seed can supply, or ``None``.

    Mirrors ``loanwhiz.api.main._extracted_capital_structure`` — both go through
    the one shared builder, so the mirror is now a *delegation* rather than a
    hand-copy that can drift. ``test_capability_matrix`` still pins the two
    against every registered deal.
    """
    if model is None:
        return None
    try:
        return CapitalStructure.from_tranche_structure(
            model.tranche_structure
        ).to_engine_mapping()
    except Exception:  # noqa: BLE001 — an unplaceable stack is "no value here"
        return None


def _missing_senior_coupon_key(structure: Mapping[str, Any]) -> str | None:
    """The senior ``<name>_rate_pct`` key this structure lacks, or ``None``.

    Mirrors ``loanwhiz.api.main._with_senior_coupon``, and shares its answer to
    "which class is senior" rather than re-deriving it — a second hand-rolled
    copy is what this whole change exists to stop. A structure naming no class
    at all is reported against ``capital_structure`` itself, matching the
    resolver.
    """
    senior = senior_tranche_name(structure)
    if senior is None:
        return "capital_structure"
    return None if structure.get(f"{senior}_rate_pct") is not None else f"{senior}_rate_pct"


def _tape_source_kinds(tapes: list) -> dict[str, int]:
    """Count registered tapes by declared source kind.

    The key is the :class:`~loanwhiz.domain.tape_provenance.TapeSourceKind`
    value, or ``"undeclared"`` for an ordinary published-file tape. ``undeclared``
    is deliberately not folded into "filed": this repo holds no evidence about
    whether those files are their originator's Article 7(1)(a) disclosure, and
    the matrix must not be the surface that invents one.
    """
    counts: dict[str, int] = {}
    for tape in tapes:
        kind = source_kind_for(tape.get("url", ""))
        key = kind.value if kind is not None else "undeclared"
        counts[key] = counts.get(key, 0) + 1
    return counts


def _provenance_qualifier(tapes: list) -> str:
    """A sentence introducing the disclosures of any tape that declares one.

    Quotes ``TapeSourceKind.disclosure`` verbatim rather than paraphrasing it, so
    the claim cannot drift between this cell, the tape citation and the data
    card. Silence when no tape declares a kind — an undeclared tape gets no
    sentence at all rather than a reassuring one.

    The lead-in says only that a declaration exists, which is exactly what the
    identifier encodes. It used to read "N of them are not published tape
    files", true of a derived tape and **false** of a synthetic one, which *is*
    a published file — #457's lesson that one literal covering a whole branch
    will be false for some member of it, reproduced the moment the branch grew a
    second member. Any sentence characterising the kinds belongs in the
    disclosures below, where each kind states its own.
    """
    declared = [t for t in tapes if source_kind_for(t.get("url", "")) is not None]
    if not declared:
        return ""
    kinds = sorted({source_kind_for(t["url"]) for t in declared}, key=lambda k: k.value)
    disclosures = " ".join(k.disclosure for k in kinds)
    return f" {len(declared)} of them declare their provenance: {disclosures}"


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
    kinds = _tape_source_kinds(tapes)
    # "loan tape(s) registered", not "ESMA tape URL(s)": a derived tape resolves
    # onto ESMA annex columns but is not an ESMA tape anyone published, and the
    # positive branch is where that distinction is easiest to lose. #457's lesson
    # was that replacing one overclaim with its mirror image still leaves a false
    # reason — so this states the registry fact and lets the disclosure say what
    # the tapes are.
    return (
        STATE_RAN,
        f"{len(tapes)} loan tape(s) registered; pool analytics normalise per "
        f"period.{_provenance_qualifier(tapes)}",
        CellEvidence(
            confidence=1.0,  # deterministic normalisation
            citation=(
                f"Deal registry context: {len(tapes)} tape(s) registered — "
                + ", ".join(f"{n} {kind}" for kind, n in sorted(kinds.items()))
                + "."
            ),
            detail={"tape_count": len(tapes), "tape_source_kinds": kinds},
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


#: Registry keys naming a period source the engine can cold-start a deal from.
#: Unlike a claim about what a deal publishes, this is fully determined by the
#: registry: it is a statement about what *this repo* can ingest today.
_INGESTIBLE_SOURCE_KEYS = ("tape_urls", "notes_cash_report_urls")


def _has_ingestible_source(deal_ctx: Mapping[str, Any]) -> bool:
    """Whether the per-deal endpoints can reconstruct a period series for this deal."""
    return any(deal_ctx.get(key) for key in _INGESTIBLE_SOURCE_KEYS)


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
    ingestible = _has_ingestible_source(deal_ctx)
    missing_config = _missing_structural_config(deal_id, deal_ctx, model)
    # ``ran`` is a claim about the *engine*, which executes these steps against
    # period funds whatever the deal. It is not a claim that the per-deal
    # endpoints will serve this deal: those need a period source to cold-start
    # from AND the structural config to fold it through, and a deal short of
    # either gets a labelled 422 instead. Saying only the first left the matrix
    # and the endpoint flatly contradicting each other, so the cell carries both
    # halves rather than the flattering one.
    #
    # The qualifier stays **one-directional**: it fires only where the registry
    # proves the endpoint refuses, and stays silent otherwise rather than
    # claiming the endpoint works (serving also depends on a cached report, which
    # the registry does not determine). Registering a derived tape for a deal
    # with no structural config moves it from the first refusal to the second —
    # so the reason names the second, instead of falling silent and reading as
    # "solved".
    if not ingestible:
        qualifier = (
            " The engine executes these steps; the per-deal endpoints cannot yet "
            "serve this deal, which has no registered tape or Notes & Cash report "
            "to cold-start a period series from."
        )
    elif missing_config:
        qualifier = (
            " The engine executes these steps; the per-deal endpoints cannot yet "
            "serve this deal, whose registered period source cannot be folded into "
            "a series without the structural configuration it does not register: "
            f"{', '.join(missing_config)}."
        )
    else:
        qualifier = ""
    return (
        STATE_RAN,
        f"Extracted {len(chosen_steps)}-step {chosen_type} waterfall executes "
        f"against period funds.{qualifier}",
        CellEvidence(
            confidence=1.0,  # deterministic interpreter run
            citation=_seed_citation(model, "Extracted deal model seed."),
            detail={
                "waterfall_type": chosen_type,
                "step_count": len(chosen_steps),
                "waterfalls": sorted(waterfalls.keys()),
                "has_ingestible_source": ingestible,
                "missing_structural_config": list(missing_config),
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
    # A registered tape is necessary but not sufficient. The reconstruction folds
    # each period through the waterfall engine, which needs the deal's capital
    # structure, reserve target and original pool balance; without them the
    # endpoint answers a labelled 422. Reporting ``ran`` off tape count alone
    # would have this cell claim a reconstruction the endpoint refuses — the
    # mirror-image overclaim #457 warns about, arriving the moment a tape is
    # registered for a deal whose structural config is not.
    missing = _missing_structural_config(deal_id, deal_ctx, model)
    if missing:
        return (
            STATE_NOT_APPLICABLE,
            f"{len(tapes)} loan tape(s) are registered, but reconstructing a "
            f"per-period pool state also needs structural configuration this deal "
            f"does not register: {', '.join(missing)}. That is a statement about "
            f"registered configuration, not about the tape — which is present and "
            f"does normalise (see the tape-analytics cell).",
            CellEvidence(
                confidence=None,
                citation=(
                    f"Deal registry context: {len(tapes)} tape(s) registered; "
                    f"missing {', '.join(missing)}."
                ),
                detail={
                    "tape_count": len(tapes),
                    "missing_structural_config": list(missing),
                },
            ),
        )
    return (
        STATE_RAN,
        f"Pool state reconstructed across {len(tapes)} tape period(s) by "
        f"net-reconciliation.{_provenance_qualifier(tapes)}",
        CellEvidence(
            confidence=1.0,
            citation=(
                f"Deal registry context: {len(tapes)} tape(s) registered, with the "
                f"structural config the reconstruction needs."
            ),
            detail={
                "tape_count": len(tapes),
                "tape_source_kinds": _tape_source_kinds(tapes),
                "missing_structural_config": [],
            },
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
        # State only what was actually checked. The tempting richer reason —
        # "reports are published, but nobody authored a key" — is an inference
        # this function is not entitled to make, and the registry cannot support
        # it: `investor_report_urls` counts periodic reports, which are not
        # Priority-of-Payments reports, and a deal whose PoP report exists may
        # deliberately not register it (`notes_cash_report_urls` is a routing
        # promise, not a URL slot — see docs/data-card.md). Guessing in either
        # direction produces a confidently false sentence: "nothing is published"
        # wrongly says a deal *cannot* be validated, and "no key has been
        # authored" is false of a deal that already has one committed.
        return (
            STATE_NOT_APPLICABLE,
            "No offline validation builder is committed for this deal, so there is "
            "nothing here for the engine to be reconciled against. This is a "
            "statement about what is committed, not about what the deal publishes — "
            "docs/data-card.md records what is obtainable, per deal.",
            CellEvidence(
                confidence=None,
                citation="No committed engine-validation builder for this deal.",
                detail={"has_validation_builder": False},
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
