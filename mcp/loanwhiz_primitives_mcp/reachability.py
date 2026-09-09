"""Primitive reachability for the MCP server — re-exported, never re-stated.

This module used to hold a hand-copied mirror of the API's reachability map,
kept equal by a test. That was a second mechanism for one fact, and it drifted:
``mcp/README.md``'s table, written from it, marked a ``live`` primitive
``library-only`` and listed two primitives that had been deleted.

Since #574 the decision lives once, in
:mod:`loanwhiz.primitives.reachability`, and this module re-exports it. The
reason the mirror existed — that importing ``loanwhiz.api.main`` would drag
FastAPI and the whole REST app into the MCP server's startup for one dict — is
answered by *where* the decision now lives rather than by copying it: the
``loanwhiz.primitives`` package is the primitives themselves, which this server
already imports, and it pulls in no web framework.

The names below are the MCP package's public reachability surface. Import them
from here; the module they come from is an implementation detail of where the
one decision is kept.
"""

from __future__ import annotations

from loanwhiz.primitives.reachability import (
    LIBRARY_ONLY,
    LIVE,
    PRIMITIVE_REACHABILITY,
    is_exposed_as_tool,
    live_primitive_names,
    reachability_of,
)

__all__ = [
    "LIBRARY_ONLY",
    "LIVE",
    "PRIMITIVE_REACHABILITY",
    "is_exposed_as_tool",
    "live_primitive_names",
    "reachability_of",
]
