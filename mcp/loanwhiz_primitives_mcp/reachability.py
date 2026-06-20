"""Primitive reachability map for the MCP server (catalogue honesty).

Not every registered primitive is reachable in the live path. This map mirrors
``loanwhiz.api.main._PRIMITIVE_REACHABILITY`` so the MCP catalogue advertises
reachability exactly as the repo's ``GET /primitives`` endpoint does — nothing
is shown as ``live`` (callable) that a consumer can't actually reach, and the
``library-only`` primitives are surfaced honestly in the catalogue resource
without being exposed as callable tools.

Why a mirror rather than an import: this package owns ``mcp/**`` and only
*reads* ``src/loanwhiz/primitives/**`` (issue #238 scope); importing the API
module would pull FastAPI and the whole REST app's import graph into the MCP
server's startup just to reuse one dict. The mirror is small and is guarded by
``test_server_smoke.py``, which asserts it stays equal to the API's map so the
two can never silently drift.

- ``live``         — called by a REST endpoint and/or exposed as a LangGraph
                     agent tool in the host app; exposed here as a callable MCP
                     tool.
- ``library-only`` — registered (so it appears in the catalogue) and importable
                     as library code, but reached by no endpoint or agent tool;
                     surfaced in the catalogue resource, not as a tool.

Unknown / future primitives default to ``library-only`` (the conservative,
honest default), matching the API's ``.get(name, _REACHABILITY_LIBRARY_ONLY)``.
"""

from __future__ import annotations

LIVE = "live"
LIBRARY_ONLY = "library-only"

# Mirror of loanwhiz.api.main._PRIMITIVE_REACHABILITY. Kept in sync by
# test_server_smoke.py::test_reachability_map_matches_api.
PRIMITIVE_REACHABILITY: dict[str, str] = {
    "esma_tape_normaliser": LIVE,
    "collections_aggregator": LIVE,
    "covenant_monitor": LIVE,
    "waterfall_runner": LIVE,
    "audit_logger": LIVE,
    "report_verifier": LIBRARY_ONLY,
}


def reachability_of(name: str) -> str:
    """Return the reachability of *name*, defaulting to ``library-only``.

    The conservative default mirrors the API: an unknown / future primitive is
    treated as ``library-only`` (not advertised as a callable tool) until it is
    explicitly wired up.
    """
    return PRIMITIVE_REACHABILITY.get(name, LIBRARY_ONLY)


def live_primitive_names() -> list[str]:
    """Return the names mapped to ``live``, in declaration order."""
    return [name for name, r in PRIMITIVE_REACHABILITY.items() if r == LIVE]
