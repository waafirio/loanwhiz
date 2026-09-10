"""Primitive reachability — the one place that decides what is reachable (#574).

Not every registered primitive is reachable in the live path, and the platform
says so rather than advertising a uniform surface it cannot back. This module
owns that judgement for **every** consumer of it:

- ``GET /primitives`` renders it as each catalogue entry's ``reachability``.
- ``GET /mcp/surface`` renders it as whether a primitive is callable as an MCP
  tool.
- The MCP server itself dispatches on it: ``loanwhiz_primitives_mcp`` re-exports
  the names below, so the tool list a client sees and the page describing that
  list are the same decision rather than two that agree by maintenance.

The last point is why this module is in ``loanwhiz.primitives`` and not in
``loanwhiz.api.main``, where the map used to live. The MCP package must not
import FastAPI to learn one dict, so before #574 it kept a hand-copied mirror
guarded by an equality test — a second mechanism for one fact, and the shape
this codebase has removed repeatedly (#503, #520, #531, #538). Both consumers
can import ``loanwhiz.primitives``, so putting the decision here removes the
mirror instead of policing it.

The two classes
---------------
``live``
    Called by a REST endpoint and/or exposed as a LangGraph agent tool in the
    host app. These, and only these, are exposed as callable MCP tools.
``library-only``
    Registered (so it appears in the catalogue) and importable as library code,
    but reached by no endpoint or agent tool. Surfaced honestly in the
    catalogue and on the MCP surface, never advertised as callable.

Reachability is a property of the **registered primitive**, not of its module.
``report_extractor`` is ``library-only`` even though two endpoints import
``resolve_parsed_report`` from its module: nothing instantiates the primitive
or calls its ``execute()``, so a client cannot reach *the primitive* through
any endpoint. Classifying it ``live`` would advertise a tool that answers
nothing.

Unknown / future primitives default to ``library-only`` — the conservative,
honest default: a primitive becomes callable by being wired up and named here,
never by being forgotten.
"""

from __future__ import annotations

LIVE = "live"
LIBRARY_ONLY = "library-only"

#: Reachability by primitive name. A name absent from this map is
#: ``library-only`` (see :func:`reachability_of`), so adding a primitive to the
#: registry never silently exposes it as a tool.
PRIMITIVE_REACHABILITY: dict[str, str] = {
    # Each of the four data primitives is called by a REST endpoint AND exposed
    # as a LangGraph agent tool (``loanwhiz.agent.tools``).
    "esma_tape_normaliser": LIVE,
    "collections_aggregator": LIVE,
    "covenant_monitor": LIVE,
    "waterfall_runner": LIVE,
    # Reached by the deal endpoints, which record audit entries through it.
    "audit_logger": LIVE,
    # #320: reached by ``GET /deal/{id}/report-verification`` and the
    # ``verify_report`` agent tool.
    "report_verifier": LIVE,
}


def reachability_of(name: str) -> str:
    """Return ``LIVE`` or ``LIBRARY_ONLY`` for the primitive called *name*.

    An unrecognised name is ``library-only``: reachability is earned by being
    wired up and declared, never assumed.
    """
    return PRIMITIVE_REACHABILITY.get(name, LIBRARY_ONLY)


def is_exposed_as_tool(name: str) -> bool:
    """Return whether the primitive called *name* is exposed as an MCP tool.

    **This predicate is the MCP surface.** The server builds its tool list from
    it, and ``GET /mcp/surface`` describes the surface by asking it — so the
    documented surface cannot drift from the dispatched one. Exposure is
    exactly live-reachability today; keeping it a named predicate rather than
    an inlined ``== LIVE`` comparison means a future exposure rule (an opt-out,
    a tool that needs credentials) changes the surface and its description
    together, in one edit.
    """
    return reachability_of(name) == LIVE


def live_primitive_names() -> list[str]:
    """Return the names mapped to ``live``, in declaration order.

    Note this reads the *map*, so it names primitives declared live whether or
    not they are registered. Callers describing the actual MCP tool surface
    should filter the registry through :func:`is_exposed_as_tool` instead — the
    intersection of "registered" and "exposed" is what a client can call.
    """
    return [name for name, reach in PRIMITIVE_REACHABILITY.items() if reach == LIVE]
