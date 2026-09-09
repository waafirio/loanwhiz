# LoanWhiz primitives MCP server

A **governed [Model Context Protocol](https://modelcontextprotocol.io) server**
that packages the LoanWhiz structured-finance (SF) primitives as reusable tools
a third party (e.g. the waafir platform) can consume — **without rewriting any
primitive**.

The server introspects the live primitive registry
(`loanwhiz.primitives.registry.PRIMITIVE_REGISTRY`) and exposes each
*endpoint-reachable* primitive as an MCP tool whose input is the primitive's own
typed Pydantic schema. Every tool call runs the primitive's `execute()` and
returns the full `PrimitiveResult` — the typed output **plus the governance
evidence**: a `confidence` score, source `citations`, and a structured
`audit_entry` (input hash, timestamp, duration). The trust story travels with
the tool.

This package owns `mcp/**` only; it *reads* `src/loanwhiz/primitives/**` and
does not modify the primitives or the REST API.

## Why an MCP server (and why Python)

The primitives are Python classes implementing `loanwhiz.primitives.base.Primitive`,
each with a typed Pydantic input/output and a `describe()` that yields JSON
schemas. A Python MCP server (the official `mcp` SDK) wrapping the registry is
the natural shape: it reuses the existing typed contracts and the `PrimitiveResult`
evidence pack verbatim, so the governance story a consumer gets over MCP is
exactly the one the host application already produces.

## Governance: the evidence pack travels with every tool call

Every primitive returns a `PrimitiveResult` envelope:

| Field | Meaning |
|---|---|
| `output` | The primitive's typed output (its own JSON schema). |
| `confidence` | `[0.0, 1.0]` — `1.0` for deterministic/rule-based computation, lower under model or data-quality uncertainty. |
| `citations` | Source references (document + locator + excerpt) grounding the output. |
| `audit_entry` | `primitive_name`, `version`, SHA-256 `input_hash`, ISO-8601 `executed_at`, `duration_ms`. |

The MCP tool returns this whole envelope as its result content — so a consuming
agent receives not just the answer but the evidence to trust (or escalate) it.

## What's exposed: `live` vs `library-only`

Only the **`live`** (endpoint-reachable) primitives are exposed as **callable
tools**. The **`library-only`** primitives are registered and importable but
reached by no endpoint or agent tool in the host app — they are surfaced
honestly in the catalogue resource, with their schemas and reachability, but
**not** advertised as callable. Nothing is shown as reachable that a consumer
can't actually reach.

**For the current surface, read `GET /mcp/surface`** — which primitives are
exposed as tools, each tool's typed input schema, and the governance fields its
result carries. This README used to print that as a table; it drifted, and
badly. It marked `report_verifier` `library-only` after #320 made it live, and
listed `cashflow_projector` and `multi_period_waterfall_runner`, both deleted
in #276. A reader trusting it would have believed the server exposed fewer
tools than it does, and that primitives which no longer exist were still
catalogued. It is not replaced with a corrected table here, because a corrected
table is the same mechanism with a later timestamp (#574).

> The decision lives once, in `loanwhiz.primitives.reachability`:
> `is_exposed_as_tool()` is what `server.py` filters its registrations through,
> what `catalogue.live_tool_names()` answers from, and what `GET /mcp/surface`
> asks. `loanwhiz_primitives_mcp/reachability.py` re-exports it rather than
> mirroring it, so the tool list and every description of it are one fact.

## MCP surface

- **Tools** — one per `live` primitive. Each advertises the primitive's typed
  Pydantic input JSON schema (`inputSchema`); calling it validates the
  arguments, runs `execute()`, and returns the serialised `PrimitiveResult`.
- **Resource** — `primitives://catalogue`: the full JSON catalogue of every
  registered primitive (live + library-only) with name/version/description/
  author/tags, reachability, and input/output JSON schemas. Lets a consumer
  introspect the whole framework, not just the callable tools. The registry is
  completed by walking the primitives package, so a primitive is catalogued
  because it exists rather than because a list names it (#574).

## Running the server

The server speaks MCP over **stdio** (the standard transport). It needs the
`loanwhiz` package importable. From the repo root:

```bash
# Option A — run in place via PYTHONPATH (no install):
PYTHONPATH=src:mcp python3 -m loanwhiz_primitives_mcp.server

# Option B — install both packages, then use the console script:
pip install -e .            # the loanwhiz package (repo root)
pip install -e mcp          # this MCP package
loanwhiz-primitives-mcp     # the entrypoint declared in mcp/pyproject.toml
```

### Wiring into an MCP client

A client (e.g. the waafir platform, or Claude Desktop) launches the server as a
stdio subprocess:

```json
{
  "mcpServers": {
    "loanwhiz-primitives": {
      "command": "python3",
      "args": ["-m", "loanwhiz_primitives_mcp.server"],
      "env": { "PYTHONPATH": "/abs/path/to/loanwhiz/src:/abs/path/to/loanwhiz/mcp" }
    }
  }
}
```

(With both packages `pip install`-ed, use `"command": "loanwhiz-primitives-mcp"`
and drop the `PYTHONPATH`.)

## Sample tool call

After `initialize`, a client lists tools (`tools/list`) and gets, for example,
the `audit_logger` tool with this input schema:

```json
{
  "name": "audit_logger",
  "inputSchema": {
    "type": "object",
    "title": "AuditLoggerInput",
    "properties": {
      "log_dir": { "type": "string", "default": "/tmp/loanwhiz_audit" },
      "auto_flag_threshold": { "type": "number", "minimum": 0.0, "maximum": 1.0, "default": 0.7 }
    }
  }
}
```

Calling it (`tools/call`) with:

```json
{ "name": "audit_logger", "arguments": { "log_dir": "/tmp/loanwhiz_audit", "auto_flag_threshold": 0.7 } }
```

returns the full `PrimitiveResult` as the tool result — output **and** the
governance evidence:

```json
{
  "output": {
    "log_path": "/tmp/loanwhiz_audit/esma_tape_normaliser/2026-06-06.jsonl",
    "entries_written": 61,
    "flagged_for_review": 0
  },
  "confidence": 1.0,
  "citations": [],
  "audit_entry": {
    "primitive_name": "audit_logger",
    "version": "0.1.0",
    "input_hash": "71fa80434992dc8d217bf670433fdaac50dd3f8bbf9ef98e3e3cf8ce62b9cbbc",
    "executed_at": "2026-06-06T10:13:49.866964+00:00",
    "duration_ms": 24.73
  }
}
```

## Tests

```bash
PYTHONPATH=src python3 -m pytest mcp/tests -m "not slow and not integration" -q
```

The smoke tests assert: the server lists exactly the exposed primitives as
tools, each with a valid typed input schema; a tool call returns a
`PrimitiveResult` carrying the governance evidence; and the catalogue resource
lists every registered primitive with honest reachability.

> **These tests do not currently run in CI**, and had never executed: the `mcp`
> SDK is not installed in the host app's environment and the root suite's
> `testpaths` excludes `mcp/tests`. Issue #577 wires them up. The surface's
> agreement with the endpoint is meanwhile covered by `tests/test_mcp_surface.py`
> in the root suite, which imports this package without the SDK.
```
