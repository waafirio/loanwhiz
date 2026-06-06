"""LoanWhiz primitives MCP server — a governed Model Context Protocol server.

This package wraps the LoanWhiz SF primitive registry
(:data:`loanwhiz.primitives.registry.PRIMITIVE_REGISTRY`) and exposes each
*endpoint-reachable* (``live``) primitive as an MCP tool. The primitives are
**not** rewritten here — the server only introspects the registry and the
``Primitive`` / ``PrimitiveResult`` contracts defined in
``loanwhiz.primitives.base``.

The trust story travels with every tool: a tool call validates its arguments
against the primitive's typed Pydantic input schema, runs the primitive's
``execute()``, and returns the full :class:`~loanwhiz.primitives.base.PrimitiveResult`
— output **plus** the governance evidence (``confidence``, ``citations`` and the
structured ``audit_entry``).

Public surface:
    build_catalogue()   — JSON-serialisable catalogue of all registered
                          primitives (incl. reachability + I/O schemas).
    live_tool_names()   — names of the primitives exposed as callable tools.
    build_server()      — construct the low-level ``mcp.server.Server``.
    main()              — stdio entrypoint (the console script).
"""

from loanwhiz_primitives_mcp.catalogue import build_catalogue, live_tool_names
from loanwhiz_primitives_mcp.reachability import (
    LIBRARY_ONLY,
    LIVE,
    PRIMITIVE_REACHABILITY,
    live_primitive_names,
    reachability_of,
)
from loanwhiz_primitives_mcp.server import build_server, main

__all__ = [
    "LIBRARY_ONLY",
    "LIVE",
    "PRIMITIVE_REACHABILITY",
    "build_catalogue",
    "build_server",
    "live_primitive_names",
    "live_tool_names",
    "main",
    "reachability_of",
]
