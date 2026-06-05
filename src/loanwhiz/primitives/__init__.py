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
from loanwhiz.primitives.covenant_monitor import (
    CovenantInput,
    CovenantMonitor,
    CovenantOutput,
    TriggerDefinition,
    TriggerEvaluation,
    TriggerStatus,
    evaluate_triggers,
)
from loanwhiz.primitives.deal_state import DealState
from loanwhiz.primitives.esma_tape_normaliser import (
    EsmaTapeInput,
    EsmaTapeNormaliser,
    EsmaTapeOutput,
)
from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY, register_primitive

__all__ = [
    "AuditEntry",
    "BaseInput",
    "Citation",
    "CovenantInput",
    "CovenantMonitor",
    "CovenantOutput",
    "DealState",
    "EsmaTapeInput",
    "EsmaTapeNormaliser",
    "EsmaTapeOutput",
    "Primitive",
    "PrimitiveMetadata",
    "PrimitiveResult",
    "PRIMITIVE_REGISTRY",
    "TriggerDefinition",
    "TriggerEvaluation",
    "TriggerStatus",
    "evaluate_triggers",
    "register_primitive",
]
