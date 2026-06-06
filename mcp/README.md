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

Consistent with the host app's `GET /primitives` reachability map, only the
**`live`** (endpoint-reachable) primitives are exposed as **callable tools**.
The **`library-only`** primitives are registered and importable but reached by
no endpoint or agent tool in the host app — they are surfaced honestly in the
catalogue resource (with their schemas and reachability) but **not** advertised
as callable tools. Nothing is shown as reachable that a consumer can't actually
reach.

| Primitive | Reachability | MCP tool? |
|---|---|---|
| `esma_tape_normaliser` | `live` | ✅ |
| `collections_aggregator` | `live` | ✅ |
| `covenant_monitor` | `live` | ✅ |
| `waterfall_runner` | `live` | ✅ |
| `audit_logger` | `live` | ✅ |
| `cashflow_projector` | `library-only` | catalogue only |
| `report_verifier` | `library-only` | catalogue only |
| `multi_period_waterfall_runner` | `library-only` | catalogue only |

> The reachability map lives in `loanwhiz_primitives_mcp/reachability.py`, a
> small mirror of `loanwhiz.api.main._PRIMITIVE_REACHABILITY`. A test
> (`tests/test_server_smoke.py::test_reachability_map_matches_api`) asserts the
> two stay equal, so the catalogue can never silently lie about reachability.

## MCP surface

- **Tools** — one per `live` primitive. Each advertises the primitive's typed
  Pydantic input JSON schema (`inputSchema`); calling it validates the
  arguments, runs `execute()`, and returns the serialised `PrimitiveResult`.
- **Resource** — `primitives://catalogue`: the full JSON catalogue of *all 8*
  registered primitives (live + library-only) with name/version/description/
  author/tags, reachability, and input/output JSON schemas. Lets a consumer
  introspect the whole framework, not just the callable tools.

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

The smoke tests assert: the server lists exactly the `live` primitives as tools,
each with a valid typed input schema; a tool call returns a `PrimitiveResult`
carrying the governance evidence; the catalogue resource lists all 8 primitives
with honest reachability; and the reachability mirror matches the host API's map.
```
