"use client";

import { useEffect, useState } from "react";
import { Database } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { DATA_SOURCES, getTapeAnalytics, type DataSource } from "@/lib/api";

/* ---------------------------------------------------------------------------
 * How a tape's ingestion channel is named on screen (#599, epic #597)
 *
 * One vocabulary, one place. This module exists because the same defect was
 * fixed once and then survived a layer up: `DATA_SOURCE_LABELS` replaced a
 * binary that called every non-`deeploans` channel a "direct URL", and the
 * badge above the sentence it fed kept its own binary —
 * `{src === "deeploans" ? "deeploans" : "direct"} ingestion` — so a `derived`
 * or `synthetic` tape was badged "direct ingestion" directly above a sentence
 * correctly reading "SYNTHETIC — generated, describes no real obligor". Two
 * claims about one source, on one screen, disagreeing.
 *
 * The convergence, and the property to preserve: the badge's text IS
 * `dataSourceLabel(source)`, not a second phrasing of it. Badge and sentence
 * are the same string, so they cannot drift apart, and every table below is a
 * total `Record` — widening `DataSource` is a compile error here until each
 * one answers for the new member, which is the point.
 *
 * There is no conditional in this file that decides what a source is called.
 * That is deliberate and is what `tests/test_provenance_badges.py` enforces: a
 * ternary is how the original defect was written, and a total lookup is how it
 * stops being writable. The "we could not tell" answer is a key in the same
 * tables rather than a branch around them.
 *
 * `web/` has no JS test runner, so that guard asserts this file's SOURCE, not
 * what a browser paints — the same trade `tests/test_capability_matrix.py`
 * already makes to reach `page-states.tsx`. A source guard proves the code
 * says a thing; it can never prove a reader saw it.
 * ------------------------------------------------------------------------- */

/**
 * Map a data-source label to a human-readable provenance string.
 *
 * A total `Record`, not a conditional: the previous binary form said "direct
 * URL (HuggingFace / file)" for every non-deeploans channel, so a derived tape
 * — and, once it existed, a synthetic one — was labelled as a direct read of a
 * published file. Widening the union is now a compile error here until this
 * table answers for the new member, which is the point.
 *
 * A label says how the tape arrived, not what it is (`lib/api.ts`'s
 * `DataSource` docstring). Rendering it is not a substitute for the citation
 * excerpt's full disclosure sentence, and no surface should treat it as one.
 */
export const DATA_SOURCE_LABELS: Record<DataSource, string> = {
  deeploans: "deeploans ETL backend",
  direct: "direct URL (HuggingFace / file)",
  derived: "derived from a source document (not a published tape)",
  synthetic: "SYNTHETIC — generated, describes no real obligor",
};

export function dataSourceLabel(source: DataSource): string {
  return DATA_SOURCE_LABELS[source];
}

/**
 * The key a surface uses when it could not resolve a channel at all — the read
 * failed, is still in flight, or the API served a tape with no provenance.
 *
 * It is a *key*, not a branch: rendering nothing when provenance is unknown is
 * indistinguishable, to a reader, from the channel being an ordinary published
 * tape — which is exactly the failure #484 left on the Pool and Waterfall
 * pages. An unresolved source has to say so at the same weight as a resolved
 * one, so it lives in the same tables.
 */
type ProvenanceKey = DataSource | "unresolved";

const PROVENANCE_LABELS: Record<ProvenanceKey, string> = {
  ...DATA_SOURCE_LABELS,
  unresolved: "ingestion channel not reported",
};

/**
 * How loudly each channel renders. `synthetic` is `destructive` because the
 * tape describes no real obligor and a reader skimming a page of outline
 * badges will not read the one that matters; every other channel is a neutral
 * outline. Total for the same reason the label table is.
 */
const PROVENANCE_VARIANTS: Record<ProvenanceKey, "outline" | "destructive"> = {
  deeploans: "outline",
  direct: "outline",
  derived: "outline",
  synthetic: "destructive",
  unresolved: "outline",
};

/**
 * One channel, badged. `source` is nullable because most callers hold an
 * optional API field; `null` resolves to the `unresolved` key above rather
 * than to a default channel, because defaulting an unknown to `direct` is the
 * mislabelling this whole module exists to prevent.
 */
export function ProvenanceBadge({ source }: { source: DataSource | null }) {
  const key: ProvenanceKey = source ?? "unresolved";
  return (
    <Badge variant={PROVENANCE_VARIANTS[key]} className="font-normal">
      <Database className="mr-1 size-3" />
      {PROVENANCE_LABELS[key]}
    </Badge>
  );
}

/**
 * A whole surface's channels, badged — and never nothing.
 *
 * An empty or unresolved list renders the unresolved badge rather than an
 * empty fragment. That is the property worth the component: a caller cannot
 * accidentally render silence on a provenance surface by holding a list that
 * happened to come back empty.
 */
export function ProvenanceBadges({ sources }: { sources: DataSource[] | null }) {
  const resolved = sources ?? [];
  if (resolved.length === 0) {
    return <ProvenanceBadge source={null} />;
  }
  return (
    <>
      {resolved.map((s) => (
        <ProvenanceBadge key={s} source={s} />
      ))}
    </>
  );
}

/**
 * The distinct channels present in *sources*, in the union's display order.
 *
 * Ordered off `DATA_SOURCES` (`lib/api.ts`) rather than a local literal: the
 * pack-level summary used to filter a hardcoded `["deeploans", "direct"]`,
 * which silently dropped a derived tape from the one place a reader looks to
 * see what fed an answer. Omitting a channel there is indistinguishable from
 * that channel not having been used.
 */
export function distinctDataSources(
  sources: readonly (DataSource | null | undefined)[],
): DataSource[] {
  const seen = new Set<DataSource>();
  for (const s of sources) {
    if (s) seen.add(s);
  }
  return DATA_SOURCES.filter((s) => seen.has(s));
}

/**
 * The ingestion channels behind one deal's tapes, for a surface whose own
 * payload carries no provenance (the waterfall cascade is computed from the
 * tapes, but `WaterfallResult` reports nothing about where they came from).
 *
 * Returns `null` until resolved — in flight, failed, or a deal switch not yet
 * caught up. Callers pass that straight to `ProvenanceBadges`, which renders
 * the unresolved badge; a failure here must never quietly become "real".
 */
export function useDealDataSources(dealId: string): DataSource[] | null {
  const [state, setState] = useState<{
    dealId: string;
    sources: DataSource[] | null;
  }>({ dealId, sources: null });

  useEffect(() => {
    let cancelled = false;
    getTapeAnalytics(dealId)
      .then(
        (periods) =>
          !cancelled &&
          setState({
            dealId,
            sources: distinctDataSources(periods.map((p) => p.data_source)),
          }),
      )
      .catch(
        // Leave `sources` null on failure. The caller renders "not reported",
        // which is true; inventing a channel here would not be.
        () => !cancelled && setState({ dealId, sources: null }),
      );
    return () => {
      cancelled = true;
    };
  }, [dealId]);

  // Until the tapes for the *current* deal resolve, report "unknown" (null).
  return state.dealId === dealId ? state.sources : null;
}
