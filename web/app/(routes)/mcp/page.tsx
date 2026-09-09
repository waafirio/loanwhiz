"use client";

import { useEffect, useState } from "react";

import {
  ApiError,
  getMcpSurface,
  type JsonSchema,
  type McpGovernanceField,
  type McpSurfaceEntry,
} from "@/lib/api";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "@/components/page-states";
import { McpAuthSketch } from "@/components/mcp-auth-sketch";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/**
 * The MCP page.
 *
 * It leads with the **governed result** rather than the tool list: what a
 * caller gets back from a tool — a confidence score, source citations and a
 * structured audit entry — is the argument for serving these primitives over
 * MCP at all. The catalogue below it is context for that, not the headline.
 *
 * Every field name and description on this page is read from
 * `GET /mcp/surface`, which derives them from the server's own dispatch
 * predicate and from the `PrimitiveResult` models. Nothing is transcribed:
 * two transcribed tallies went stale in silence in this repo before (#484,
 * #492), so any count here is computed from the rows actually rendered.
 */
export default function McpPage() {
  const [data, setData] = useState<McpSurfaceEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getMcpSurface()
      .then(setData)
      .catch((e) =>
        setError(
          e instanceof ApiError ? e.message : "Failed to load the MCP surface",
        ),
      );
  }, []);

  return (
    <div className="space-y-6">
      <PageHeader
        title="MCP"
        description="The primitives, served as a governed MCP server. Every callable tool returns its answer wrapped in the evidence that grounds it — a confidence score, the citations behind it, and an audit entry recording exactly what ran. Exposure is decided by the server's own is_exposed_as_tool(), so this page cannot describe a surface different from the one it dispatches."
      />

      {error ? (
        <ErrorState title="Could not load the MCP surface" message={error} />
      ) : !data ? (
        <LoadingState />
      ) : data.length === 0 ? (
        <EmptyState message="No primitives are registered." />
      ) : (
        <>
          <GovernedResult entries={data} />
          <ToolCatalogue entries={data} />
        </>
      )}

      <McpAuthSketch />
    </div>
  );
}

/**
 * The lead section: one exposed tool's call, and the evidence its result
 * carries.
 *
 * The request is that tool's real `input_schema`; the response is the real
 * `result_governance` list. **No value shown is a recorded run** — each is
 * written as its own type or as the endpoint's own description of the field,
 * and the section says so. Inventing a plausible `confidence` figure on the
 * page that argues for honest evidence would be the same lie it warns about.
 */
function GovernedResult({ entries }: { entries: McpSurfaceEntry[] }) {
  const example = entries.find((e) => e.exposed_as_tool);
  if (!example) return null;

  return (
    <Card>
      <CardHeader className="space-y-2">
        <CardTitle className="text-base">
          What a call returns — the governed result
        </CardTitle>
        <p className="text-sm text-muted-foreground">
          A tool call does not return a bare answer. It returns the answer plus
          the evidence a reviewer needs to decide whether to believe it. Below
          is the shape of one call against{" "}
          <span className="font-mono">{example.name}</span>, read from the
          endpoint.
        </p>
        <p className="text-xs text-muted-foreground">
          Every field and description here comes from{" "}
          <span className="font-mono">GET /mcp/surface</span>. The values are
          written as their own types, not as figures from a run — this is the
          contract, not a recorded result.
        </p>
      </CardHeader>
      <CardContent className="space-y-5">
        <div className="space-y-2">
          <SectionLabel>Request</SectionLabel>
          <p className="font-mono text-sm">
            {example.name}
            <span className="text-muted-foreground">
              {" "}
              · v{example.version}
            </span>
          </p>
          <SchemaFieldList schema={example.input_schema} />
        </div>

        <div className="space-y-2">
          <SectionLabel>Response — the answer</SectionLabel>
          <FieldRow
            name="output"
            type="the tool's typed output"
            description="The answer itself. Everything below is evidence about it."
          />
        </div>

        <div className="space-y-2">
          <SectionLabel>Response — the evidence it travels with</SectionLabel>
          {example.result_governance.map((field) => (
            <GovernanceFieldRows key={field.name} field={field} />
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

/** One governance field, plus its sub-fields where the envelope has them. */
function GovernanceFieldRows({ field }: { field: McpGovernanceField }) {
  const subFields = Object.entries(field.fields);
  return (
    <div className="space-y-1.5 rounded-md border p-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="font-mono text-sm font-semibold">{field.name}</span>
      </div>
      <p className="text-sm text-muted-foreground">{field.description}</p>
      {subFields.length > 0 ? (
        <ul className="space-y-1 pt-1">
          {subFields.map(([name, description]) => (
            <li
              key={name}
              className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 border-b border-dashed py-1 last:border-0"
            >
              <span className="font-mono text-xs">{name}</span>
              <span className="text-xs text-muted-foreground">
                {description}
              </span>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * The catalogue: every registered primitive, and whether the server exposes it
 * as a callable tool.
 *
 * The count in the header is derived from the rows rendered — `/mcp/surface`
 * ships no tally, and one written into this file would be the transcription
 * this endpoint exists to remove.
 */
function ToolCatalogue({ entries }: { entries: McpSurfaceEntry[] }) {
  const exposed = entries.filter((e) => e.exposed_as_tool);

  return (
    <Card>
      <CardHeader className="space-y-2">
        <CardTitle className="text-base">Tool catalogue</CardTitle>
        <p className="text-sm text-muted-foreground">
          Every registered primitive, exposed or not. Of the{" "}
          {entries.length} listed here, {exposed.length} are callable as MCP
          tools; the rest keep their typed contract and state why they are not
          advertised as callable.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        {entries.map((entry) => (
          <ToolRow key={entry.name} entry={entry} />
        ))}
      </CardContent>
    </Card>
  );
}

function ToolRow({ entry }: { entry: McpSurfaceEntry }) {
  return (
    <div className="space-y-2 rounded-md border p-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-sm font-semibold">{entry.name}</span>
          <Badge
            variant={entry.exposed_as_tool ? "default" : "outline"}
            title={
              entry.exposed_as_tool
                ? "Exposed by the MCP server as a callable tool."
                : "Registered and importable, but not advertised as callable."
            }
          >
            {entry.exposed_as_tool ? "callable tool" : "not a tool"}
          </Badge>
        </div>
        <span className="text-xs text-muted-foreground">v{entry.version}</span>
      </div>
      <p className="text-sm text-muted-foreground">{entry.description}</p>
      {entry.not_exposed_reason ? (
        <p className="text-xs text-muted-foreground">
          {entry.not_exposed_reason}
        </p>
      ) : null}
      <details>
        <summary className="cursor-pointer text-xs text-muted-foreground">
          Input schema
        </summary>
        <div className="pt-2">
          <SchemaFieldList schema={entry.input_schema} />
        </div>
      </details>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
      {children}
    </p>
  );
}

function FieldRow({
  name,
  type,
  description,
}: {
  name: string;
  type: string;
  description?: string;
}) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5 border-b border-dashed py-1 last:border-0">
      <span className="font-mono text-sm">{name}</span>
      <span className="font-mono text-xs text-muted-foreground">{type}</span>
      {description ? (
        <span className="text-xs text-muted-foreground">{description}</span>
      ) : null}
    </div>
  );
}

/** The top-level properties of a Pydantic object schema, as name/type rows. */
function SchemaFieldList({ schema }: { schema: JsonSchema }) {
  const props = schema.properties;
  if (!props) return <p className="text-sm text-muted-foreground">No fields.</p>;
  const required = new Set(schema.required ?? []);
  const rows = Object.entries(props);
  if (rows.length === 0)
    return <p className="text-sm text-muted-foreground">No fields.</p>;

  return (
    <div>
      {rows.map(([name, prop]) => (
        <FieldRow
          key={name}
          name={required.has(name) ? name : `${name}?`}
          type={typeLabel(prop, schema)}
        />
      ))}
    </div>
  );
}

/** A short, human-readable type label for one property schema. */
function typeLabel(prop: JsonSchema, root: JsonSchema): string {
  if (prop.$ref) return refName(prop.$ref, root);

  if (prop.anyOf) {
    const parts = prop.anyOf
      .filter((s) => s.type !== "null")
      .map((s) => typeLabel(s, root));
    const label = Array.from(new Set(parts)).join(" | ") || "any";
    return prop.anyOf.some((s) => s.type === "null") ? `${label} | null` : label;
  }

  if (prop.enum) return "enum";

  if (prop.type === "array") {
    return `${prop.items ? typeLabel(prop.items, root) : "any"}[]`;
  }

  return prop.type ?? "object";
}

/** Resolve a `$ref` like "#/$defs/WaterfallStep" to its model title/name. */
function refName(ref: string, root: JsonSchema): string {
  const key = ref.split("/").pop();
  if (!key) return "object";
  return root.$defs?.[key]?.title ?? key;
}
