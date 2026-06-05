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
    "EsmaTapeInput",
    "EsmaTapeNormaliser",
    "EsmaTapeOutput",
    "Primitive",
    "PrimitiveMetadata",
    "PrimitiveResult",
    "PRIMITIVE_REGISTRY",
    "register_primitive",
]
