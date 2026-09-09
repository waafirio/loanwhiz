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

from typing import Any

from loanwhiz_primitives_mcp.catalogue import build_catalogue, live_tool_names
from loanwhiz_primitives_mcp.reachability import (
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
    "build_catalogue",
    "build_server",
    "is_exposed_as_tool",
    "live_primitive_names",
    "live_tool_names",
    "main",
    "reachability_of",
]

# ``build_server`` and ``main`` are resolved on first access rather than at
# import time, because ``server`` imports the ``mcp`` SDK and the two modules
# above do not (#574). Eagerly importing it here made ``import
# loanwhiz_primitives_mcp.catalogue`` fail with ``ModuleNotFoundError: mcp``
# wherever the SDK is absent — which is every environment that wants to read
# the catalogue or the reachability decision *without* running a server, the
# host app's own test environment included. The SDK is still required to build
# or run a server; it is no longer required to ask what the server exposes.
_LAZY = {"build_server", "main"}


def __getattr__(name: str) -> Any:
    if name in _LAZY:
        from loanwhiz_primitives_mcp import server

        return getattr(server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)
