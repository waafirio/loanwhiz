"""LoanWhiz primitives — public surface.

Import from here, not from the submodules directly:

    from loanwhiz.primitives import (
        Primitive,
        BaseInput,
        PrimitiveResult,
        Citation,
        AuditEntry,
        PrimitiveMetadata,
        PRIMITIVE_REGISTRY,
        register_primitive,
        EsmaTapeNormaliser,
        EsmaTapeInput,
        EsmaTapeOutput,
    )
"""

from loanwhiz.primitives.base import (
    AuditEntry,
    BaseInput,
    Citation,
    Primitive,
    PrimitiveMetadata,
    PrimitiveResult,
)
from loanwhiz.primitives.capability_matrix import (
    CapabilityCell,
    CapabilityMatrix,
    CapabilityRow,
    CellEvidence,
    DealColumn,
    build_capability_matrix,
    capability_rows,
)
from loanwhiz.primitives.esma_tape_normaliser import (
    EsmaTapeInput,
    EsmaTapeNormaliser,
    EsmaTapeOutput,
)
from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY, register_primitive

__all__ = [
    "AuditEntry",
    "BaseInput",
    "build_capability_matrix",
    "capability_rows",
    "CapabilityCell",
    "CapabilityMatrix",
    "CapabilityRow",
    "CellEvidence",
    "Citation",
    "DealColumn",
    "EsmaTapeInput",
    "EsmaTapeNormaliser",
    "EsmaTapeOutput",
    "Primitive",
    "PrimitiveMetadata",
    "PrimitiveResult",
    "PRIMITIVE_REGISTRY",
    "register_primitive",
    "ReportAdapter",
    # report_extractor (#271) — lazily exposed (see __getattr__)
    "ReportExtractor",
    "ReportExtractInput",
    "ParsedReport",
    "ParsedReportPeriod",
    "NoteBalance",
    "ReportedStep",
    "ReportedTrigger",
    "ReportFormat",
    "FORMAT_REGISTRY",
    "extract_report",
    # reconciliation_gate (#272) — lazily exposed (see __getattr__)
    "reconcile_as_gate",
    "apply_reconciliation",
    "fields_for_human_review",
    "ReconciliationGateResult",
    "ReviewItem",
    "DEFAULT_REVIEW_CONFIDENCE_THRESHOLD",
    # scenario_generator — lazily exposed (see __getattr__)
    "ScenarioAssumptions",
    "ScenarioGenerator",
]

# Names re-exported lazily from modules that import the canonical
# ``loanwhiz.domain`` schema — see __getattr__ for why eager import closes the
# import cycle.
_LAZY_REPORT_EXTRACTOR = {
    "ReportExtractor",
    "ReportExtractInput",
    "ParsedReport",
    "ParsedReportPeriod",
    "NoteBalance",
    "ReportedStep",
    "ReportedTrigger",
    "ReportFormat",
    "FORMAT_REGISTRY",
    "extract_report",
}

# reconciliation_gate (#272) also imports the canonical ``domain`` schema (via
# the extractor + reconciler), so it is exposed lazily for the same import-cycle
# reason as the report extractor above.
_LAZY_RECONCILIATION_GATE = {
    "reconcile_as_gate",
    "apply_reconciliation",
    "fields_for_human_review",
    "ReconciliationGateResult",
    "ReviewItem",
    "DEFAULT_REVIEW_CONFIDENCE_THRESHOLD",
}


def __getattr__(name: str) -> object:
    """Lazily expose ``domain``-importing primitives on the package surface (PEP 562).

    ``report_adapter``, ``report_extractor``, ``reconciliation_gate`` and
    ``scenario_generator`` import the canonical ``loanwhiz.domain`` schema, which
    in turn imports ``loanwhiz.primitives.base`` — eagerly importing any of them
    at module top level would close that cycle and break ``import
    loanwhiz.domain``. The same reason ``period_state_machine`` (which also
    imports ``domain``) is not eagerly imported here. A lazy ``__getattr__`` keeps
    ``from loanwhiz.primitives import ReportAdapter`` / ``ReportExtractor`` /
    ``ScenarioGenerator`` working without the cycle.
    """
    if name == "ReportAdapter":
        from loanwhiz.primitives.report_adapter import ReportAdapter

        return ReportAdapter
    if name in _LAZY_REPORT_EXTRACTOR:
        from loanwhiz.primitives import report_extractor

        return getattr(report_extractor, name)
    if name in _LAZY_RECONCILIATION_GATE:
        from loanwhiz.primitives import reconciliation_gate

        return getattr(reconciliation_gate, name)
    if name in ("ScenarioGenerator", "ScenarioAssumptions"):
        from loanwhiz.primitives.scenario_generator import (
            ScenarioAssumptions,
            ScenarioGenerator,
        )

        return {"ScenarioGenerator": ScenarioGenerator, "ScenarioAssumptions": ScenarioAssumptions}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
