"""Loads every trustee-report family table into the registry.

Import this module (rather than :mod:`loanwhiz.domain.trustee_report_registry`
directly) whenever you need a *populated* :data:`FAMILY_REGISTRY`. Each family
table module registers its own
:class:`~loanwhiz.domain.trustee_report_registry.TrusteeReportFamily` at import;
this module is the one place that imports them all, so registration is explicit
and its **order is the detection order**.

Detection returns the first family whose header signature the report carries.
:meth:`TrusteeReportFamilyRegistry.register` refuses a signature comparable to
an already-registered one, so no ordering can silently shadow a family — but the
order is still declared here rather than left to import side effects elsewhere.

**Adding a collateral administrator is a table, not a code change.** Write the
family module with its ``TrusteeReportFamily``, then add one import line below.
Neither ``collateral_schedule_parser`` nor ``note_valuation_parser`` changes:
they own no layout, resolving every report through whichever family the registry
detects, and refusing outright when none matches.

This module is deliberately a *leaf* — it imports only the family tables and the
registry, never ``loanwhiz.domain``'s package ``__init__`` or anything under
``loanwhiz.primitives``, so it cannot widen the domain↔primitives import cycle
that :mod:`loanwhiz.extraction.taxonomy` documents.
"""

from __future__ import annotations

from loanwhiz.domain.trustee_family_bny_mellon import BNY_MELLON
from loanwhiz.domain.trustee_family_us_bank import US_BANK
from loanwhiz.domain.trustee_report_registry import (
    FAMILY_REGISTRY,
    ColumnOrder,
    DocumentKind,
    DocumentLayout,
    FurnitureOrder,
    IdentifierPosition,
    RowGeometry,
    TrusteeReportFamily,
    TrusteeReportFamilyRegistry,
    UnknownReportFamilyError,
)

__all__ = [
    "BNY_MELLON",
    "FAMILY_REGISTRY",
    "US_BANK",
    "ColumnOrder",
    "DocumentKind",
    "DocumentLayout",
    "FurnitureOrder",
    "IdentifierPosition",
    "RowGeometry",
    "TrusteeReportFamily",
    "TrusteeReportFamilyRegistry",
    "UnknownReportFamilyError",
]
