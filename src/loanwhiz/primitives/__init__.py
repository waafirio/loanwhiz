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
from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY, register_primitive

__all__ = [
    "AuditEntry",
    "BaseInput",
    "Citation",
    "Primitive",
    "PrimitiveMetadata",
    "PrimitiveResult",
    "PRIMITIVE_REGISTRY",
    "register_primitive",
]
