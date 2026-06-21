"""Breadth end-to-end harness — run the deal-facing primitives across the full
cross-jurisdiction / vintage deal set (#282, the last child of epic #261).

The capability matrix (``capability_matrix.py``) makes the cross-jurisdiction
*story* auditable by **classifying** each (deal × capability) cell from the
deal's real inputs. This harness is the complementary half: for every deal in
the registry it **actually executes** the live deal-facing primitives that the
deal's committed seed + the prereqs on this branch (#279 direct-read, #280
tape-native Annex-2 covenants, #281 loan-level amortisation) make runnable —
offline, deterministically, with no network or LLM — and records, per deal:

  * which extracted-covenant set ran through the live :class:`CovenantMonitor`
    (proving NL + IT extracted triggers evaluate, not merely classify);
  * that the #280 tape-native (B7) arrears / default / LTV triggers resolve and
    evaluate through the same live primitive against a synthetic Annex-2 period;
  * that the #281 loan-level amortisation schedule runs on a synthetic tape.

The harness is **deal-generic** — it reads only committed seeds + registry
context via the same ``_extracted_triggers_to_definitions`` / seed loader the
API uses, so it tracks the deals/seeds rather than hardcoding any one
jurisdiction. Where a deal's inputs make a primitive genuinely not-applicable
(IT/ES carry no tapes; Sol-Lion's minimal seed carries no triggers), the
harness records an honest ``not-applicable`` with a reason instead of forcing a
green — the same #193 honesty discipline the matrix encodes. The accompanying
test (``test_breadth_cross_jurisdiction.py``) cross-checks every per-deal
applicability decision here against the live capability matrix so "ran
end-to-end" and "the matrix says ran" can never silently diverge.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from loanwhiz.api.main import (
    _extracted_triggers_to_definitions,
    _load_cached_deal_model,
)
from loanwhiz.primitives.capability_matrix import (
    STATE_NOT_APPLICABLE,
    STATE_RAN,
)
from loanwhiz.primitives.covenant_monitor import (
    CovenantInput,
    CovenantMonitor,
)
from loanwhiz.primitives.loan_level_amortisation import (
    pool_scheduled_principal_schedule,
)

#: A deterministic synthetic ESMA Annex-2 reporting period for the tape-native
#: (B7, #280) triggers. The metrics live exactly where ``_extract_metric``
#: looks for them: ``pool_stats.wtd_ltv`` and the ``arrears_breakdown`` buckets.
#: Chosen above every B7 threshold so each trigger fires (proving the metric
#: resolves AND the comparison runs), not as a claim about any real pool.
SYNTHETIC_TAPE_PERIOD: dict[str, Any] = {
    "reporting_date": "2026-04-30",
    "pool_stats": {"wtd_ltv": 85.0},  # > 80.0 wa_ltv threshold
    "arrears_breakdown": {
        "arrears_180d_plus_pct": 6.0,  # > 5.0 severe-arrears threshold
        "default_pct": 4.0,  # > 3.0 tape-default threshold
    },
}


@dataclass
class CapabilityRun:
    """One (deal × capability) run record produced by the harness.

    ``state`` is the harness's *own* applicability decision, derived by actually
    attempting the run (or honestly skipping it). It uses the same vocabulary as
    the capability matrix (``ran`` / ``not-applicable``) so the test can compare
    the two directly.
    """

    deal_id: str
    capability_key: str
    state: str
    reason: str
    #: Free-form, JSON-serialisable evidence from the live run (trigger count,
    #: evaluated statuses, schedule length, …). Empty for a not-applicable cell.
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class DealRun:
    """All capability runs for one deal, plus its resolved jurisdiction/vintage."""

    deal_id: str
    deal_name: str
    jurisdiction: str
    vintage: int | None
    runs: list[CapabilityRun] = field(default_factory=list)

    def run(self, capability_key: str) -> CapabilityRun:
        for r in self.runs:
            if r.capability_key == capability_key:
                return r
        raise KeyError(f"no run for ({self.deal_id}, {capability_key})")


_DEFAULT_JURISDICTION = "Netherlands"


def _resolve_jurisdiction(deal_ctx: Mapping[str, Any]) -> str:
    """Jurisdiction from the registry context, defaulting to Netherlands.

    Mirrors ``capability_matrix``'s resolution: only the non-Dutch deals carry
    an explicit ``jurisdiction`` key; the Green Lion deals default.
    """
    return str(deal_ctx.get("jurisdiction") or _DEFAULT_JURISDICTION)


def _resolve_vintage(deal_id: str, deal_ctx: Mapping[str, Any]) -> int | None:
    """The deal's vintage *year* — the cross-deal 'vintage' dimension.

    Prefers an explicit registry ``vintage``; otherwise parses the first 19xx/20xx
    four-digit year out of the deal id (e.g. ``green-lion-2024-1`` → 2024,
    ``sol-lion-ii`` → ``None``). Deal-generic; no per-deal table.
    """
    explicit = deal_ctx.get("vintage")
    if isinstance(explicit, int):
        return explicit
    import re

    m = re.search(r"(19|20)\d{2}", deal_id)
    return int(m.group(0)) if m else None


def _run_extracted_covenants(deal_id: str, deal_ctx: Mapping[str, Any]) -> CapabilityRun:
    """Run the deal's *extracted* triggers through the live CovenantMonitor.

    Applicable when the deal's committed seed carries extracted covenant
    triggers (NL Green Lion + IT Leone Arancio today; ES Sol-Lion's minimal seed
    carries none → honest not-applicable). Drives the real primitive — not a
    classifier — against a deterministic synthetic period so the run is offline.
    """
    triggers = _extracted_triggers_to_definitions(dict(deal_ctx))
    if not triggers:
        return CapabilityRun(
            deal_id=deal_id,
            capability_key="covenant_monitoring",
            state=STATE_NOT_APPLICABLE,
            reason="No covenant triggers extracted from this deal's seed model.",
        )
    covenant_input = CovenantInput(periods=[SYNTHETIC_TAPE_PERIOD], triggers=triggers)
    result = CovenantMonitor().execute(covenant_input)
    out = result.output
    return CapabilityRun(
        deal_id=deal_id,
        capability_key="covenant_monitoring",
        state=STATE_RAN,
        reason=f"{len(triggers)} extracted trigger(s) evaluated through CovenantMonitor.",
        detail={
            "trigger_count": len(triggers),
            "status_count": len(out.trigger_statuses),
            "confidence": result.confidence,
            "citation_count": len(result.citations),
        },
    )


def run_tape_native_b7() -> CapabilityRun:
    """Run the #280 tape-native (B7) triggers through the live CovenantMonitor.

    Deal-generic framework capability (the B7 triggers key on Annex-2 tape
    metrics, not on any one deal's extracted prospectus). Proves the
    RREL-resolved arrears / default / LTV metrics resolve out of a synthetic
    Annex-2 period AND fire through the live primitive. Reported under the
    synthetic deal id ``"_framework"`` so it sits outside the per-deal grid.
    """
    triggers = CovenantMonitor.TAPE_NATIVE_TRIGGERS
    covenant_input = CovenantInput(periods=[SYNTHETIC_TAPE_PERIOD], triggers=triggers)
    result = CovenantMonitor().execute(covenant_input)
    out = result.output
    return CapabilityRun(
        deal_id="_framework",
        capability_key="tape_native_covenants",
        state=STATE_RAN,
        reason=f"{len(triggers)} tape-native (B7) trigger(s) resolved + evaluated.",
        detail={
            "trigger_count": len(triggers),
            "active_triggers": list(out.active_triggers),
            "evaluable_count": sum(1 for s in out.trigger_statuses if s.evaluable),
        },
    )


def run_loan_level_amortisation() -> CapabilityRun:
    """Run the #281 loan-level amortisation schedule on a synthetic tape.

    Deal-generic framework capability: amortises a small synthetic performing
    loan set forward and proves a non-trivial pool scheduled-principal schedule
    comes back. Reported under ``"_framework"`` like the B7 run.
    """
    import pandas as pd

    df = pd.DataFrame(
        {
            "current_balance": [200_000.0, 150_000.0, 100_000.0],
            "rate": [3.5, 4.0, 2.5],
            "remaining_term_months": [240, 180, 300],
        }
    )
    horizon = 12
    schedule = pool_scheduled_principal_schedule(df, horizon)
    return CapabilityRun(
        deal_id="_framework",
        capability_key="loan_level_amortisation",
        state=STATE_RAN,
        reason=f"Pool scheduled-principal schedule computed over {horizon} period(s).",
        detail={
            "horizon": horizon,
            "schedule_length": len(schedule),
            "total_scheduled_principal": round(sum(schedule), 2),
            "all_non_negative": all(p >= 0.0 for p in schedule),
        },
    )


def run_breadth(
    deal_registry: Mapping[str, Mapping[str, Any]],
    seed_loader: Callable[[Mapping[str, Any]], Any] | None = None,
) -> list[DealRun]:
    """Run the breadth set end-to-end across every registered deal.

    For each deal, resolves its jurisdiction + vintage and runs the
    seed-driven, offline deal-facing primitives (extracted covenants today; the
    deal-generic #280 / #281 framework legs are produced once by
    :func:`framework_runs`). Returns one :class:`DealRun` per deal.

    ``seed_loader`` is accepted for symmetry with ``build_capability_matrix`` and
    defaults to the API's committed-seed loader; the harness uses it only to
    decide jurisdiction/vintage today, but threading it keeps the harness
    deal-generic and testable with injected seeds.
    """
    loader = seed_loader or _load_cached_deal_model
    deal_runs: list[DealRun] = []
    for deal_id, deal_ctx in deal_registry.items():
        model = loader(dict(deal_ctx))
        deal_name = (
            (model.metadata.deal_name if model is not None else None)
            or deal_ctx.get("deal_name")
            or deal_id
        )
        deal_runs.append(
            DealRun(
                deal_id=deal_id,
                deal_name=str(deal_name),
                jurisdiction=_resolve_jurisdiction(deal_ctx),
                vintage=_resolve_vintage(deal_id, deal_ctx),
                runs=[_run_extracted_covenants(deal_id, deal_ctx)],
            )
        )
    return deal_runs


def framework_runs() -> list[CapabilityRun]:
    """The deal-generic framework legs (#280 B7 covenants, #281 amortisation).

    These are framework capabilities, not per-deal cells — they prove the
    prereq paths run, exercised once, independent of any single deal's tapes
    (which the breadth deals don't carry offline)."""
    return [run_tape_native_b7(), run_loan_level_amortisation()]
