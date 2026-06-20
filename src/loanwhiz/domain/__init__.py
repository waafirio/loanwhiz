"""LoanWhiz canonical domain schema — the deal contract.

One canonical, Pydantic-validated domain model that every extractor *fills* and
the ``fold(run_period)`` engine *consumes directly* — no boundary-mapping glue,
because there is nothing to map *to*. This retires the duplicate typed shapes the
same concept used to take across extraction / interpreter / parser / runner (see
``docs/superpowers/specs/2026-06-20-canonical-domain-schema-design.md``).

Import from here, not from the submodules directly::

    from loanwhiz.domain import (
        DealRules, PeriodInputs, DealState,
        RecipientType, MetricType,
        FieldProvenance, ProvenanceMap,
    )

The three aggregates:

- :class:`DealRules` — the period-invariant *program* (prospectus-sourced):
  capital structure, waterfalls, triggers, reserve.
- :class:`PeriodInputs` — uniform per-period exogenous inputs from any adapter
  (tape / report / scenario).
- :class:`DealState` — the evolving per-period structural state the engine folds.

Provenance is a sidecar (:data:`ProvenanceMap`), not per-field wrappers, so the
engine's hot path stays clean while every value remains traceable.
"""

from loanwhiz.domain.inputs import (
    CollectionLegs,
    PeriodInputs,
    RiskSignals,
)
from loanwhiz.domain.provenance import (
    FieldProvenance,
    ProvenanceMap,
)
from loanwhiz.domain.rules import (
    AmountRule,
    ConditionRef,
    DealRules,
    MetricType,
    RateRule,
    RecipientType,
    ReserveRule,
    StepRule,
    TrancheRule,
    TriggerRule,
    WaterfallKind,
)
from loanwhiz.domain.state import (
    DealState,
    TrancheState,
)

__all__ = [
    # Provenance sidecar
    "FieldProvenance",
    "ProvenanceMap",
    # DealRules (the program) + taxonomies & sub-rules
    "DealRules",
    "RecipientType",
    "MetricType",
    "AmountRule",
    "ConditionRef",
    "StepRule",
    "TriggerRule",
    "RateRule",
    "TrancheRule",
    "ReserveRule",
    "WaterfallKind",
    # PeriodInputs (per-period exogenous inputs)
    "PeriodInputs",
    "CollectionLegs",
    "RiskSignals",
    # DealState (evolving structural state)
    "DealState",
    "TrancheState",
]
