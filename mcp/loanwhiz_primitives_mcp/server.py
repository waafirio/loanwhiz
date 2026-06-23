"""The LoanWhiz primitives MCP server.

A low-level :class:`mcp.server.Server` that introspects ``PRIMITIVE_REGISTRY``
and exposes each ``live`` primitive as an MCP tool. The low-level Server (rather
than the FastMCP convenience layer) is used so each tool can advertise the
primitive's own Pydantic JSON input schema verbatim, and so the full
catalogue — including ``library-only`` primitives — can be served as a resource.

Per tool:
- ``inputSchema`` is the primitive's typed Pydantic input model's JSON schema.
- ``call_tool`` validates arguments into that model, instantiates the primitive,
  runs ``execute()``, and returns the full ``PrimitiveResult`` serialised to JSON
  — output **plus** the governance evidence (``confidence``, ``citations``,
  ``audit_entry``). The trust story travels with the tool result.

The server also exposes ``primitives://catalogue`` as a resource: the full
JSON catalogue of all registered primitives (live + library-only) with their
reachability and I/O schemas, so a consumer can introspect the whole framework.
"""

from __future__ import annotations

import json
from typing import Any

import mcp.types as types
from mcp.server import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents

from loanwhiz.primitives.audit_logger import audit_result
from loanwhiz_primitives_mcp.catalogue import (
    build_catalogue,
    ensure_primitives_registered,
    primitive_input_type,
)
from loanwhiz_primitives_mcp.reachability import LIVE, reachability_of

SERVER_NAME = "loanwhiz-primitives"
CATALOGUE_URI = "primitives://catalogue"

# Audit log dir for primitive calls reached through the MCP server. Mirrors the
# REST API's ``API_AUDIT_LOG_DIR`` and the agent tools' ``AGENT_AUDIT_LOG_DIR``
# (all default to ``/tmp/loanwhiz_audit``) so the MCP path is governed like the
# endpoint + agent paths: every tool call appends one ``AuditLogEntry`` to the
# per-primitive JSONL store. ``call_tool`` already returns the ``audit_entry`` in
# the result envelope; persisting it here makes the trust story durable, not just
# in-flight. Patchable (like the REST/agent constants) so tests can point it at a
# tmp dir and assert an entry was written without polluting ``/tmp``.
MCP_AUDIT_LOG_DIR = "/tmp/loanwhiz_audit"


def _live_registrations() -> list[Any]:
    """Return the ``PrimitiveRegistration`` objects for every ``live`` primitive."""
    ensure_primitives_registered()
    from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY

    return [
        reg
        for reg in PRIMITIVE_REGISTRY.list_all()
        if reachability_of(reg.name) == LIVE
    ]


def build_server() -> Server:
    """Construct and return the configured MCP ``Server``.

    Registers ``list_tools`` / ``call_tool`` for the ``live`` primitives and
    ``list_resources`` / ``read_resource`` for the catalogue resource. Building
    the server eagerly resolves the registry so a misconfigured primitive
    surfaces at startup rather than on first call.
    """
    server: Server = Server(SERVER_NAME)

    # name -> (PrimitiveRegistration, input_model_type). Resolved once at build
    # time; the registry is immutable for the server's lifetime.
    live_tools: dict[str, tuple[Any, type | None]] = {}
    for reg in _live_registrations():
        live_tools[reg.name] = (reg, primitive_input_type(reg.primitive_class))

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        tools: list[types.Tool] = []
        for name, (reg, input_type) in live_tools.items():
            described = reg.primitive_class.describe()
            # NB: we deliberately do NOT set the tool's ``outputSchema`` to the
            # primitive's bare output model. The tool returns the *full*
            # PrimitiveResult envelope (output + confidence + citations +
            # audit_entry), which is a superset of the output model — declaring
            # the narrower output schema would make the SDK reject the richer,
            # evidence-bearing result. The output model still travels with each
            # entry in the catalogue resource for consumers that want it.
            tools.append(
                types.Tool(
                    name=name,
                    title=f"{reg.primitive_class.__qualname__} (v{reg.version})",
                    description=(
                        f"{reg.description} Returns a PrimitiveResult with the "
                        f"typed output plus governance evidence (confidence, "
                        f"citations, audit_entry)."
                    ),
                    inputSchema=described.input_schema or {"type": "object", "properties": {}},
                )
            )
        return tools

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        entry = live_tools.get(name)
        if entry is None:
            raise ValueError(
                f"Unknown or non-live primitive tool: {name!r}. "
                f"Available tools: {sorted(live_tools)}."
            )
        reg, input_type = entry
        if input_type is None:
            raise ValueError(
                f"Primitive {name!r} does not expose a typed input model; "
                "cannot invoke it as a tool."
            )

        # Validate / coerce the arguments into the primitive's typed input model.
        # (The SDK also validates against inputSchema; this builds the real
        # Pydantic instance the primitive's execute() expects.)
        primitive_input = input_type(**arguments)
        primitive = reg.primitive_class()
        result = primitive.execute(primitive_input)

        # Persist one AuditLogEntry to disk — mirroring the REST path's
        # ``_audit(...)`` so a primitive call reached through MCP leaves the same
        # durable provenance record, not just the in-flight ``audit_entry`` in
        # the response envelope. ``audit_result`` is best-effort and
        # failure-isolated (a non-real result or an unwritable dir is swallowed),
        # so the audit side-channel never takes down the tool call.
        audit_result(primitive, primitive_input, result, log_dir=MCP_AUDIT_LOG_DIR)

        # Serialise the full PrimitiveResult — output AND governance evidence
        # (confidence, citations, audit_entry) — so the trust story travels with
        # the tool result.
        payload = result.model_dump(mode="json")
        return [types.TextContent(type="text", text=json.dumps(payload, indent=2))]

    @server.list_resources()
    async def list_resources() -> list[types.Resource]:
        return [
            types.Resource(
                uri=CATALOGUE_URI,  # type: ignore[arg-type]
                name="primitives-catalogue",
                title="LoanWhiz primitives catalogue",
                description=(
                    "Full catalogue of all registered SF primitives (live and "
                    "library-only) with reachability and typed I/O JSON schemas. "
                    "Mirrors the host app's GET /primitives endpoint."
                ),
                mimeType="application/json",
            )
        ]

    @server.read_resource()
    async def read_resource(uri: Any) -> list[ReadResourceContents]:
        if str(uri) != CATALOGUE_URI:
            raise ValueError(f"Unknown resource: {uri!r}. Expected {CATALOGUE_URI!r}.")
        return [
            ReadResourceContents(
                content=json.dumps(build_catalogue(), indent=2),
                mime_type="application/json",
            )
        ]

    return server


async def _run_stdio() -> None:
    """Run the server over a stdio transport (the standard MCP transport)."""
    from mcp.server.stdio import stdio_server

    server = build_server()
    init_options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, init_options)


def main() -> None:
    """Console-script entrypoint: serve the primitives over stdio."""
    import anyio

    anyio.run(_run_stdio)


if __name__ == "__main__":
    main()
