"use client";

import { useEffect, useState } from "react";
import { Database, FileText, ShieldCheck } from "lucide-react";

import {
  ApiError,
  citationDataSource,
  getGovernance,
  type Citation,
  type DataSource,
  type DueDiligenceCheck,
  type DueDiligenceRecord,
  type DueDiligenceSource,
  type GovernanceEvidencePack,
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
import { formatPct } from "@/lib/format";

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
// Investor due-diligence record (#568, epic #561)
//
// Extends this file rather than standing beside it: the evidence-pack surface
// already carries `CitationItem` and the badge vocabulary, and a parallel
// component would be a second place for a citation to render differently.
//
// **The known gap this file still has, stated rather than built over.** #484
// committed synthetic pools labelled correctly *in the data*, and the Pool and
// Waterfall pages still render no synthetic badge; `PackBody` above still
// resolves its provenance badge to "direct ingestion" for a `derived` or
// `synthetic` source, because the badge text is a binary on `deeploans`. That
// is not fixed here — it is #484's surface, not this one — and nothing below
// depends on it. It is recorded so the next reader does not mistake this
// component's correctness for the file's.
//
// **Refusals render at the same weight as verifications.** #549's rule is that
// a refusal which keeps the value is not a refusal: `not_evaluable` protects
// the *grade*, not the screen. Applied here, that means the not-established
// section renders FIRST, in the same card, at the same heading level, with its
// full reason — never collapsed, never truncated, never behind a disclosure
// widget, and never reduced to a ratio. An evidence file showing five green
// items and hiding two unestablished ones is worse than one showing nothing,
// because a compliance officer will sign it.
//
// Guarded by `tests/test_due_diligence_surface.py`, which asserts this file's
// SOURCE — `web/` has no JS test runner, so the guard cannot assert rendered
// output. That is the same trade `tests/test_capability_matrix.py` already
// makes to reach `page-states.tsx`, and it is a real limit: a guard over source
// cannot see what a browser paints.
// ---------------------------------------------------------------------------

/**
 * One deal's due-diligence record.
 *
 * Counting is per-kind and never a ratio. "1 of 7 verified" is a grade, and a
 * grade is what invites a reader to treat the remainder as rounding error;
 * "Not established (6)" and "Verified (1)" are two facts, and the first is on
 * screen first.
 */
export function DueDiligenceBody({ record }: { record: DueDiligenceRecord }) {
  return (
    <div className="space-y-5">
      <section className="space-y-1">
        <h2 className="text-sm font-medium">{record.deal_name}</h2>
        <p className="text-xs text-muted-foreground">
          What UK Securitisation Regulation risk-retention verification this
          platform could establish for this deal from the documents it holds,
          and what it could not. This is not the deal&rsquo;s covenant
          compliance, which is a different question for a different reader.
        </p>
      </section>

      {/* Not established FIRST and unconditionally — see the header note. The
          empty case still renders its heading, so "nothing was refused" and
          "the section is missing" cannot look the same. */}
      <section className="space-y-2">
        <h3 className="text-sm font-medium">
          Not established{" "}
          <span className="text-muted-foreground">
            ({record.not_established.length})
          </span>
        </h3>
        {record.not_established.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Every check below was established from a registered document.
          </p>
        ) : (
          <ul className="space-y-2">
            {record.not_established.map((check) => (
              <CheckItem key={check.check} check={check} />
            ))}
          </ul>
        )}
      </section>

      <Separator />

      <section className="space-y-2">
        <h3 className="text-sm font-medium">
          Verified{" "}
          <span className="text-muted-foreground">
            ({record.verified.length})
          </span>
        </h3>
        {record.verified.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No check on this deal established its fact.
          </p>
        ) : (
          <ul className="space-y-2">
            {record.verified.map((check) => (
              <CheckItem key={check.check} check={check} />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/** Human labels for the check keys. Falls back to the raw key — an unlabelled
 *  check must still render, because a check that renders as nothing is
 *  indistinguishable from one that was never asked. */
const CHECK_LABELS: Record<string, string> = {
  risk_retention: "Risk retention (UK SR Article 6)",
};

/**
 * One check, whichever outcome it reached — the SAME component for both, so
 * the two sections cannot drift into different weights. Only the badge differs.
 */
function CheckItem({ check }: { check: DueDiligenceCheck }) {
  const established = check.outcome === "verified";
  return (
    <li className="rounded-lg border bg-background px-3 py-2">
      <div className="flex items-start justify-between gap-2">
        <p className="text-sm font-medium">
          {CHECK_LABELS[check.check] ?? check.check}
        </p>
        <Badge
          variant={established ? "secondary" : "destructive"}
          className="shrink-0 font-normal"
        >
          {established ? "Verified" : "Not established"}
        </Badge>
      </div>
      {/* The reason renders in full on BOTH outcomes. On a refusal it names the
          document that was looked in, which is what makes the limitation
          re-askable of a document this reading did not reach. Clamping it would
          hide exactly that. */}
      <p className="mt-1 text-xs text-muted-foreground">{check.reason}</p>
      {check.source ? <SourceLine source={check.source} /> : null}
      {check.citations.length > 0 ? (
        <ul className="mt-2 space-y-2">
          {check.citations.map((c, i) => (
            <CitationItem key={i} citation={c} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/**
 * The document a check was answered from.
 *
 * `read_at` and `document_date` are two different facts and are labelled as
 * two different facts. `read_at` is LoanWhiz's own clock — the label says so —
 * and it is NEVER rendered in the document-date row: no registry field carries
 * a publication date, so that row shows `document_date_reason` instead. Passing
 * our read time off as the document's date would be confident wrongness of
 * exactly the kind this record exists to avoid.
 */
function SourceLine({ source }: { source: DueDiligenceSource }) {
  return (
    <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
      <dt>Document read</dt>
      <dd className="font-mono break-all text-foreground">{source.url}</dd>
      <dt>Registry field</dt>
      <dd className="font-mono text-foreground">{source.registry_slot}</dd>
      <dt>Read by LoanWhiz</dt>
      <dd className="text-foreground">
        {source.read_at ? formatTimestamp(source.read_at) : "not read"}
      </dd>
      <dt>Document date</dt>
      <dd className="text-foreground">
        {source.document_date ?? (
          <span className="italic">{source.document_date_reason}</span>
        )}
      </dd>
      {source.registry_note ? (
        <>
          <dt>Registry note</dt>
          <dd className="text-foreground">{source.registry_note}</dd>
        </>
      ) : null}
    </dl>
  );
}
