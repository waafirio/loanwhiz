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
]


def __getattr__(name: str) -> object:
    """Lazily expose ``ReportAdapter`` on the package surface (PEP 562).

    ``report_adapter`` imports the canonical ``loanwhiz.domain`` schema, which in
    turn imports ``loanwhiz.primitives.base`` — eagerly importing the adapter at
    module top level would close that cycle and break ``import loanwhiz.domain``.
    The same reason ``period_state_machine`` (which also imports ``domain``) is
    not eagerly imported here. A lazy ``__getattr__`` keeps
    ``from loanwhiz.primitives import ReportAdapter`` working without the cycle.
    """
    if name == "ReportAdapter":
        from loanwhiz.primitives.report_adapter import ReportAdapter

        return ReportAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
