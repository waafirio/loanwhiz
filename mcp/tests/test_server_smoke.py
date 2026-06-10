"""Smoke tests for the LoanWhiz primitives MCP server.

Offline (no network, no LLM, no PDF fetch). These assert the governed-MCP
contract: the server lists exactly the ``live`` primitives as tools with valid
typed input schemas, a tool call returns the full PrimitiveResult including
governance evidence, the catalogue resource is honest about reachability, and
the local reachability map cannot silently drift from the host API's.

Run from the repo root with:

    PYTHONPATH=src python3 -m pytest mcp/tests -m "not slow and not integration" -q
"""

from __future__ import annotations

import json

import mcp.types as types
import pytest

from loanwhiz_primitives_mcp.catalogue import build_catalogue, live_tool_names
from loanwhiz_primitives_mcp.reachability import (
    LIBRARY_ONLY,
    LIVE,
    PRIMITIVE_REACHABILITY,
)
from loanwhiz_primitives_mcp.server import CATALOGUE_URI, build_server

# The primitives expected to be exposed as callable MCP tools — the ``live``
# ones, mirroring the host app's GET /primitives reachability.
EXPECTED_LIVE_TOOLS = {
    "esma_tape_normaliser",
    "collections_aggregator",
    "covenant_monitor",
    "waterfall_runner",
    "audit_logger",
}
EXPECTED_LIBRARY_ONLY = {
    "cashflow_projector",
    "report_verifier",
    "multi_period_waterfall_runner",
    "prospectus_extractor",
}


async def _list_tools(server) -> list[types.Tool]:
    req = types.ListToolsRequest(method="tools/list")
    res = await server.request_handlers[types.ListToolsRequest](req)
    return res.root.tools


async def _call_tool(server, name: str, arguments: dict) -> types.CallToolResult:
    req = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    res = await server.request_handlers[types.CallToolRequest](req)
    return res.root


async def _read_catalogue(server) -> list[dict]:
    req = types.ReadResourceRequest(
        method="resources/read",
        params=types.ReadResourceRequestParams(uri=CATALOGUE_URI),
    )
    res = await server.request_handlers[types.ReadResourceRequest](req)
    return json.loads(res.root.contents[0].text)


async def test_lists_exactly_the_live_tools():
    """list_tools exposes exactly the live primitives — no more, no less."""
    server = build_server()
    tools = await _list_tools(server)
    assert {t.name for t in tools} == EXPECTED_LIVE_TOOLS
    # The server's own helper and the catalogue agree on the live set.
    assert set(live_tool_names()) == EXPECTED_LIVE_TOOLS


async def test_every_tool_has_a_valid_object_input_schema():
    """Each tool advertises a non-empty JSON-Schema object input."""
    server = build_server()
    tools = await _list_tools(server)
    assert tools, "expected at least one tool"
    for tool in tools:
        schema = tool.inputSchema
        assert isinstance(schema, dict) and schema, f"{tool.name} has empty schema"
        assert schema.get("type") == "object", f"{tool.name} input schema is not an object"
        # describe()-sourced schemas always carry a properties block.
        assert "properties" in schema, f"{tool.name} schema missing properties"
        assert tool.description, f"{tool.name} missing description"


async def test_tool_call_returns_primitive_result_with_governance_evidence():
    """A tool call returns the full PrimitiveResult — output + evidence pack.

    Uses audit_logger because its input is self-contained config (no tape /
    report file dependency), so the call is fully offline and deterministic.
    """
    server = build_server()
    result = await _call_tool(
        server,
        "audit_logger",
        {"log_dir": "/tmp/loanwhiz_mcp_smoke", "auto_flag_threshold": 0.7},
    )
    assert result.isError is False
    payload = json.loads(result.content[0].text)
    # The trust story travels with the tool: output + the three evidence fields.
    assert set(payload) >= {"output", "confidence", "citations", "audit_entry"}
    assert 0.0 <= payload["confidence"] <= 1.0
    assert isinstance(payload["citations"], list)
    audit = payload["audit_entry"]
    assert audit["primitive_name"] == "audit_logger"
    assert len(audit["input_hash"]) == 64  # SHA-256 hex digest


async def test_unknown_tool_is_rejected():
    """Calling a non-live / unknown primitive name is an error, not a crash."""
    server = build_server()
    result = await _call_tool(server, "report_verifier", {})
    assert result.isError is True  # library-only — not exposed as a tool


async def test_catalogue_resource_lists_all_primitives_with_honest_reachability():
    """The catalogue resource surfaces all 9 primitives, marked honestly."""
    server = build_server()
    catalogue = await _read_catalogue(server)
    by_name = {e["name"]: e for e in catalogue}
    assert set(by_name) == EXPECTED_LIVE_TOOLS | EXPECTED_LIBRARY_ONLY
    for name in EXPECTED_LIVE_TOOLS:
        assert by_name[name]["reachability"] == LIVE
    for name in EXPECTED_LIBRARY_ONLY:
        assert by_name[name]["reachability"] == LIBRARY_ONLY
    # Every entry carries the typed I/O contract and governance semantics.
    for entry in catalogue:
        assert entry["input_schema"].get("type") == "object"
        assert entry["output_schema"].get("type") == "object"
        assert "confidence" in entry and "audit_entry" in entry["confidence"]


def test_build_catalogue_is_json_serialisable():
    """The catalogue is plain JSON — safe to ship over the wire / as a resource."""
    catalogue = build_catalogue()
    assert len(catalogue) == 9
    json.dumps(catalogue)  # must not raise


def test_reachability_map_matches_api():
    """The local reachability mirror must not drift from the host API's map.

    This is the guard that lets the MCP package keep a small local copy of
    ``_PRIMITIVE_REACHABILITY`` instead of importing FastAPI: if anyone changes
    the API's map without updating this mirror (or vice-versa), this fails.
    """
    from loanwhiz.api.main import _PRIMITIVE_REACHABILITY

    assert PRIMITIVE_REACHABILITY == _PRIMITIVE_REACHABILITY
