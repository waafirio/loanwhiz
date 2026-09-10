"use client";

import { useEffect, useState } from "react";
import { Database, FileText, ShieldCheck } from "lucide-react";

import {
  ApiError,
  citationDataSource,
  getGovernance,
  type BookResponse,
  type Citation,
  type DataSource,
  type GovernanceEvidencePack,
  type PositionField,
  type PositionProvenance,
  type ToolCallRecord,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { formatCurrency, formatPct } from "@/lib/format";

/**
 * Slide-over that renders one query's `GovernanceEvidencePack` — the
 * auditable trail behind a chat answer (see web/CONTRACT.md, issue #138).
 *
 * Controlled (`open` / `onOpenChange`) and lazy: it fetches
 * `GET /governance/{packId}` only while open, so opening a turn's evidence
 * doesn't run until the user asks. Surfaces the agent's tool-call trace,
 * per-tool and aggregate confidence, the human-review flag, and the citation
 * trail — the "auditable agents" story the challenge emphasises.
 */
export function EvidencePackSheet({
  packId,
  open,
  onOpenChange,
}: {
  packId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [pack, setPack] = useState<GovernanceEvidencePack | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    // Fetch the pack while the sheet is open. The parent keys this component
    // by pack id, so a fresh instance (with null state) mounts per answer —
    // no stale pack from a previously-viewed turn can flash here, and we
    // never have to reset state synchronously inside the effect.
    let cancelled = false;
    getGovernance(packId)
      .then((p) => {
        if (!cancelled) setPack(p);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(
          e instanceof ApiError ? e.message : "Failed to load evidence pack.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [open, packId]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="flex w-full flex-col gap-0 p-0 sm:max-w-lg"
      >
        <SheetHeader className="border-b">
          <SheetTitle className="flex items-center gap-2">
            <ShieldCheck className="size-4" />
            Governance evidence
          </SheetTitle>
          <SheetDescription>
            The auditable trail behind this answer — reasoning trace,
            confidence, and citations.
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
          {error ? (
            <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
              {error}
            </div>
          ) : !pack ? (
            <PackSkeleton />
          ) : (
            <PackBody pack={pack} />
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

export function PackSkeleton() {
  return (
    <div className="space-y-3">
      <Skeleton className="h-6 w-2/3" />
      <Skeleton className="h-20 w-full" />
      <Skeleton className="h-32 w-full" />
    </div>
  );
}

/**
 * Map a data-source label to a human-readable provenance string.
 *
 * A total `Record`, not a conditional: the previous binary form said "direct
 * URL (HuggingFace / file)" for every non-deeploans channel, so a derived tape
 * — and, once it existed, a synthetic one — was labelled as a direct read of a
 * published file. Widening the union is now a compile error here until this
 * table answers for the new member, which is the point.
 */
const DATA_SOURCE_LABELS: Record<DataSource, string> = {
  deeploans: "deeploans ETL backend",
  direct: "direct URL (HuggingFace / file)",
  derived: "derived from a source document (not a published tape)",
  synthetic: "SYNTHETIC — generated, describes no real obligor",
};

function dataSourceLabel(source: DataSource): string {
  return DATA_SOURCE_LABELS[source];
}

/**
 * Pack-level data-provenance summary: which ingestion paths fed the tapes this
 * answer relied on, derived honestly from the tool-call citations (no field is
 * invented — the ESMA normaliser records provenance on each tape citation).
 * Returns the distinct set of sources seen, in deeploans-first order.
 */
function packDataSources(pack: GovernanceEvidencePack): DataSource[] {
  const seen = new Set<DataSource>();
  for (const tc of pack.tool_calls) {
    for (const c of tc.citations) {
      const src = citationDataSource(c);
      if (src) seen.add(src);
    }
  }
  // Every member of the union, in a fixed display order. The list used to be
  // ["deeploans", "direct"], which silently dropped a derived tape from the
  // pack-level provenance summary — the one place a reader looks to see what
  // fed the answer. Omitting a channel here is indistinguishable from that
  // channel not having been used.
  return (
    ["deeploans", "direct", "derived", "synthetic"] as DataSource[]
  ).filter((s) => seen.has(s));
}

/**
 * Compact "FINOS conformance" line for the pack metadata: the framework verdict
 * + the satisfied/partial/n-a control counts, read from the pack's
 * `finos_conformance` summary. Returns null for packs round-tripped from JSONL
 * before the field existed (the summary object is empty / absent).
 */
function finosConformanceLabel(pack: GovernanceEvidencePack): string | null {
  const conf = pack.finos_conformance;
  // The field is `FinosConformanceSummary | {}` (empty for legacy JSONL packs).
  // Narrow to the populated summary before reading its counts.
  if (!conf || !("total_controls" in conf)) return null;
  const verdict = conf.is_conformant ? "conformant" : "not conformant";
  return `${verdict} — ${conf.counts.satisfied}/${conf.total_controls} satisfied, ${conf.counts.partial} partial`;
}

export function PackBody({ pack }: { pack: GovernanceEvidencePack }) {
  const dataSources = packDataSources(pack);
  return (
    <div className="space-y-5">
      {/* Aggregate governance summary */}
      <section className="space-y-2">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge variant="outline" className="font-normal">
            {formatPct(pack.aggregate_confidence * 100)} aggregate confidence
          </Badge>
          {pack.human_review_required ? (
            <Badge variant="destructive" className="font-normal">
              Human review required
            </Badge>
          ) : (
            <Badge variant="secondary" className="font-normal">
              Within confidence threshold
            </Badge>
          )}
          {pack.finos_compliant ? (
            <Badge variant="secondary" className="font-normal">
              FINOS compliant
            </Badge>
          ) : (
            // `finos_compliant` MEANS framework conformance (issue #278): the
            // conjunction of this pack's consistency check and LoanWhiz
            // conforming to the FINOS control catalogue. A false value is a
            // genuine signal — surface it, never hide it.
            <Badge variant="destructive" className="font-normal">
              FINOS check failed
            </Badge>
          )}
          {dataSources.map((src) => (
            <Badge key={src} variant="outline" className="font-normal">
              <Database className="mr-1 size-3" />
              {src === "deeploans" ? "deeploans" : "direct"} ingestion
            </Badge>
          ))}
        </div>
        {dataSources.length > 0 ? (
          <p className="text-xs text-muted-foreground">
            Data provenance:{" "}
            {dataSources.map((s) => dataSourceLabel(s)).join(", ")}.
          </p>
        ) : null}
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
          <dt>Model</dt>
          <dd className="text-foreground">{pack.model_used}</dd>
          <dt>Framework</dt>
          <dd className="text-foreground">{pack.framework_version}</dd>
          {finosConformanceLabel(pack) ? (
            <>
              <dt>FINOS conformance</dt>
              <dd className="text-foreground">{finosConformanceLabel(pack)}</dd>
            </>
          ) : null}
          <dt>Recorded</dt>
          <dd className="text-foreground">{formatTimestamp(pack.timestamp)}</dd>
          <dt>Pack ID</dt>
          <dd className="font-mono break-all text-foreground">
            {pack.pack_id}
          </dd>
        </dl>
      </section>

      <Separator />

      {/* Tool-call trace */}
      <section className="space-y-2">
        <h3 className="text-sm font-medium">
          Reasoning trace{" "}
          <span className="text-muted-foreground">
            ({pack.tool_calls.length}{" "}
            {pack.tool_calls.length === 1 ? "tool call" : "tool calls"})
          </span>
        </h3>
        {pack.tool_calls.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No tools were called for this answer.
          </p>
        ) : (
          <ol className="space-y-3">
            {pack.tool_calls.map((call) => (
              <ToolCall key={call.call_index} call={call} />
            ))}
          </ol>
        )}
      </section>

      <Separator />

      {/* Deduplicated citation trail */}
      <section className="space-y-2">
        <h3 className="text-sm font-medium">
          Citations{" "}
          <span className="text-muted-foreground">
            ({pack.all_citations.length})
          </span>
        </h3>
        {pack.all_citations.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No source documents were cited.
          </p>
        ) : (
          <ul className="space-y-2">
            {pack.all_citations.map((c, i) => (
              <CitationItem key={i} citation={c} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function ToolCall({ call }: { call: ToolCallRecord }) {
  return (
    <li className="rounded-lg border bg-background px-3 py-2">
      <div className="flex items-start justify-between gap-2">
        <p className="font-mono text-sm">
          <span className="text-muted-foreground">{call.call_index + 1}.</span>{" "}
          {call.tool_name}
        </p>
        <Badge variant="outline" className="shrink-0 font-normal">
          {formatPct(call.confidence * 100)}
        </Badge>
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">In:</span>{" "}
        {call.input_summary}
      </p>
      <p className="mt-0.5 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">Out:</span>{" "}
        {call.output_summary}
      </p>
      <p className="mt-1 text-[11px] text-muted-foreground">
        {Math.round(call.duration_ms)} ms
        {call.citations.length > 0
          ? ` · ${call.citations.length} ${call.citations.length === 1 ? "citation" : "citations"}`
          : ""}
      </p>
    </li>
  );
}

function CitationItem({ citation }: { citation: Citation }) {
  const { document, page_or_row, excerpt } = citation;
  const source = citationDataSource(citation);
  return (
    <li className="rounded-lg border bg-background px-3 py-2">
      <p className="flex items-start gap-1.5 text-xs font-medium">
        <FileText className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
        <span>
          {document ?? "Source"}
          {page_or_row ? (
            <span className="font-normal text-muted-foreground">
              {" "}
              — {page_or_row}
            </span>
          ) : null}
        </span>
        {source ? (
          <Badge variant="outline" className="ml-auto shrink-0 font-normal">
            <Database className="mr-1 size-3" />
            {source}
          </Badge>
        ) : null}
      </p>
      {excerpt ? (
        <p className="mt-1 pl-5 text-xs text-muted-foreground italic">
          &ldquo;{excerpt}&rdquo;
        </p>
      ) : null}
    </li>
  );
}

function formatTimestamp(ts: string): string {
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? ts : d.toLocaleString();
}

// ---------------------------------------------------------------------------
// Holdings book (#573, epic #569) — what a position IS, and what the platform
// could not resolve about it.
//
// This lives beside DATA_SOURCE_LABELS on purpose. That table is this file's
// existing provenance vocabulary: a *total* Record mapping a provenance kind to
// one human-readable sentence, in a voice that names the consequence rather
// than the mechanism ("SYNTHETIC — generated, describes no real obligor").
// A holding's provenance is a different union — it answers "does anybody hold
// this", not "where was this tape read from" — so it gets its own total Record
// rather than new members in that one, but the same shape and the same voice.
// Building a second vocabulary somewhere else is what this region refuses.
//
// #484 is the failure being designed against: its synthetic pools are
// correctly labelled in the *data*, and the Pool and Waterfall pages render no
// badge, so a viewer sees generated collateral presented exactly like real
// collateral. A qualifier a surface has to remember is one it can forget.
// ---------------------------------------------------------------------------

/**
 * What a holding IS, per provenance kind — a total `Record`, like
 * DATA_SOURCE_LABELS above and for the same reason: widening
 * `PositionProvenance` is a compile error here until this table answers for the
 * new member. A conditional would answer for it by accident, which is how the
 * "direct ingestion" label above came to speak for a derived tape.
 */
const POSITION_PROVENANCE_LABELS: Record<PositionProvenance, string> = {
  illustrative: "ILLUSTRATIVE — generated, nobody holds this",
  client_stated: "Client-stated holding",
};

/**
 * The provenance badge for one position.
 *
 * Rendered per position rather than once per screen. A book-level caveat is
 * true of the book and says nothing about the row a reader is looking at, and a
 * mixed book — one client-stated position among illustrative ones — would then
 * carry a qualifier that is wrong for every row it does not apply to.
 *
 * `destructive` for a position nobody holds: the same weight the sheet already
 * gives "Human review required", because "this is not anybody's exposure" is
 * the same class of claim.
 */
export function PositionProvenanceBadge({
  provenance,
  describesARealHolding,
}: {
  provenance: PositionProvenance;
  describesARealHolding: boolean;
}) {
  return (
    <Badge
      variant={describesARealHolding ? "secondary" : "destructive"}
      className="font-normal"
    >
      <ShieldCheck className="mr-1 size-3" />
      {POSITION_PROVENANCE_LABELS[provenance]}
    </Badge>
  );
}

/**
 * The book's own status, rendered above the positions it qualifies.
 *
 * The disclosure sentences are the backend's, quoted verbatim — the same
 * treatment `/showcase` gives `matrix.note`. `domain/position.py` is explicit
 * that a surface must render `PositionProvenance.disclosure` rather than
 * compose its own wording, so the claim cannot drift between views; composing
 * a friendlier sentence here is exactly that drift. Every distinct disclosure
 * is rendered, not the first: a book with two kinds of holding in it makes two
 * different claims.
 */
export function BookDisclosure({ book }: { book: BookResponse }) {
  return (
    <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-4 py-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge
          variant={book.describes_a_real_holding ? "secondary" : "destructive"}
          className="font-normal"
        >
          {book.describes_a_real_holding
            ? "Every position is a stated holding"
            : "ILLUSTRATIVE BOOK — nobody holds these positions"}
        </Badge>
      </div>
      {book.disclosures.map((disclosure) => (
        <p key={disclosure} className="mt-2 text-xs text-muted-foreground">
          {disclosure}
        </p>
      ))}
    </div>
  );
}

/** Render one resolved figure in the units its field is quoted in. */
function formatFactValue(field: string, value: number): string {
  if (field === "balance") return formatCurrency(value);
  if (field === "coupon") return formatPct(value, 2);
  return `${value}`;
}

/**
 * One fact about one position: the figure, or the refusal — at the same weight.
 *
 * #549's rule is that a refusal that keeps the value is not a refusal, and the
 * corollary on a screen is that a refusal rendered as an empty cell is not one
 * either: a reader takes a blank in a numeric column for zero. So a cell that
 * could not resolve says so in a Badge, in the slot the figure would have
 * occupied, and prints the backend's stated cause beneath it — the same two
 * lines a resolved cell gets.
 *
 * The reason is body text, not a tooltip. `capability-matrix-grid.tsx` puts its
 * cell reasons behind a hover, which suits a dense grid of states; here the
 * reason IS the content of the cell, and a cause a reader has to discover by
 * hovering has already lost to the figure beside it.
 *
 * `fact.value` is only reachable inside the `ran` branch — `PositionField` is a
 * discriminated union, so the refusing half has `value: null` and there is
 * nothing to coalesce.
 */
export function PositionFactCell({ fact }: { fact: PositionField }) {
  if (fact.state === "ran") {
    return (
      <div className="space-y-1">
        <span className="text-sm font-medium tabular-nums text-foreground">
          {formatFactValue(fact.field, fact.value)}
        </span>
        <p className="text-xs leading-snug text-muted-foreground">
          {fact.reason}
        </p>
      </div>
    );
  }
  return (
    <div className="space-y-1">
      <Badge variant="destructive" className="font-normal">
        Not resolved
      </Badge>
      <p className="text-xs leading-snug text-muted-foreground">
        {fact.reason}
      </p>
    </div>
  );
}
