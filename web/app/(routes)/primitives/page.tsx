"use client";

import { useEffect, useState } from "react";

import {
  ApiError,
  getPrimitives,
  type JsonSchema,
  type PrimitiveCatalogueEntry,
} from "@/lib/api";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "@/components/page-states";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

export default function PrimitivesPage() {
  const [data, setData] = useState<PrimitiveCatalogueEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getPrimitives()
      .then(setData)
      .catch((e) =>
        setError(
          e instanceof ApiError
            ? e.message
            : "Failed to load the primitive catalogue",
        ),
      );
  }, []);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Framework"
        description="The SF primitive registry — every registered primitive with its typed input/output contract, version, and tags. This is the framework the challenge judges."
      />
      {error ? (
        <ErrorState title="Could not load the primitive catalogue" message={error} />
      ) : !data ? (
        <LoadingState />
      ) : data.length === 0 ? (
        <EmptyState message="No primitives are registered." />
      ) : (
        <div className="space-y-4">
          {data.map((p) => (
            <PrimitiveCard key={p.name} primitive={p} />
          ))}
        </div>
      )}
    </div>
  );
}

function PrimitiveCard({ primitive }: { primitive: PrimitiveCatalogueEntry }) {
  return (
    <Card>
      <CardHeader className="space-y-2">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <CardTitle className="font-mono text-base">{primitive.name}</CardTitle>
          <span className="text-xs text-muted-foreground">
            v{primitive.version} · {primitive.author}
          </span>
        </div>
        <p className="text-sm text-muted-foreground">{primitive.description}</p>
        <div className="flex flex-wrap gap-1.5">
          {primitive.tags.map((tag) => (
            <Badge key={tag} variant="secondary">
              {tag}
            </Badge>
          ))}
        </div>
        <p className="font-mono text-xs text-muted-foreground">
          {primitive.class_name}
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 md:grid-cols-2">
          <SchemaPanel label="Input" schema={primitive.input_schema} />
          <SchemaPanel label="Output" schema={primitive.output_schema} />
        </div>
        <p className="text-xs text-muted-foreground">{primitive.confidence}</p>
      </CardContent>
    </Card>
  );
}

/** A compact field-name → type listing for one side of the I/O contract. */
function SchemaPanel({ label, schema }: { label: string; schema: JsonSchema }) {
  const fields = schemaFields(schema);
  return (
    <div className="space-y-2">
      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      {fields.length === 0 ? (
        <p className="text-sm text-muted-foreground">No fields.</p>
      ) : (
        <ul className="space-y-1">
          {fields.map((f) => (
            <li
              key={f.name}
              className="flex items-baseline justify-between gap-3 border-b border-dashed py-1 last:border-0"
            >
              <span className="font-mono text-sm">
                {f.name}
                {f.required ? null : (
                  <span className="text-muted-foreground">?</span>
                )}
              </span>
              <span className="font-mono text-xs text-muted-foreground">
                {f.type}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

interface SchemaField {
  name: string;
  type: string;
  required: boolean;
}

/** Flatten a Pydantic object schema's top-level properties to name/type rows. */
function schemaFields(schema: JsonSchema): SchemaField[] {
  const props = schema.properties;
  if (!props) return [];
  const required = new Set(schema.required ?? []);
  return Object.entries(props).map(([name, prop]) => ({
    name,
    type: typeLabel(prop, schema),
    required: required.has(name),
  }));
}

/** A short, human-readable type label for one property schema. */
function typeLabel(prop: JsonSchema, root: JsonSchema): string {
  if (prop.$ref) return refName(prop.$ref, root);

  if (prop.anyOf) {
    // Pydantic renders `Optional[X]` / unions as anyOf; drop the null arm and
    // collapse to the underlying type(s).
    const parts = prop.anyOf
      .filter((s) => s.type !== "null")
      .map((s) => typeLabel(s, root));
    const label = Array.from(new Set(parts)).join(" | ") || "any";
    const nullable = prop.anyOf.some((s) => s.type === "null");
    return nullable ? `${label} | null` : label;
  }

  if (prop.enum) return "enum";

  if (prop.type === "array") {
    const item = prop.items ? typeLabel(prop.items, root) : "any";
    return `${item}[]`;
  }

  return prop.type ?? "object";
}

/** Resolve a `$ref` like "#/$defs/WaterfallStep" to its model title/name. */
function refName(ref: string, root: JsonSchema): string {
  const key = ref.split("/").pop();
  if (!key) return "object";
  const def = root.$defs?.[key];
  return def?.title ?? key;
}
