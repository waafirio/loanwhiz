"""Loads every ESMA RTS loan-level annex table into the registry.

Import this module (rather than :mod:`loanwhiz.domain.esma_annex_registry`
directly) whenever you need a *populated* :data:`ANNEX_REGISTRY`. Each annex
table module registers its own :class:`~loanwhiz.domain.esma_annex_registry.AnnexSpec`
at import; this module is the one place that imports them all, so registration is
explicit and its **order is the detection order**.

Ordering is by annex number, and detection returns the first spec whose signature
matches. The registered signatures are disjoint, so order is not currently
load-bearing for correctness — but it is deterministic and reviewable here rather
than dependent on import side effects elsewhere.

**Adding an asset class is a table, not a code change.** Write the annex module
with its ``AnnexSpec``, then add one import line below. Nothing in
``esma_tape_normaliser`` changes: it owns no annex list, resolving every tape
through whichever spec the registry detects.

This module is deliberately a *leaf* — it imports only the annex tables and the
registry, never ``loanwhiz.domain``'s package ``__init__`` or anything under
``loanwhiz.primitives``, so it cannot widen the domain↔primitives import cycle
that :mod:`loanwhiz.extraction.taxonomy` documents.
"""

from __future__ import annotations

from loanwhiz.domain.esma_annex2 import ANNEX2_RMBS
from loanwhiz.domain.esma_annex4_corporate import ANNEX4_CORPORATE
from loanwhiz.domain.esma_annex5_auto import ANNEX5_AUTO
from loanwhiz.domain.esma_annex_registry import (
    ANNEX_REGISTRY,
    AnnexField,
    AnnexRegistry,
    AnnexSpec,
)

__all__ = [
    "ANNEX2_RMBS",
    "ANNEX4_CORPORATE",
    "ANNEX5_AUTO",
    "ANNEX_REGISTRY",
    "AnnexField",
    "AnnexRegistry",
    "AnnexSpec",
]
