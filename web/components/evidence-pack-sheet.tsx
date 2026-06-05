"use client";

import { useEffect, useState } from "react";
import { FileText, ShieldCheck } from "lucide-react";

import {
  ApiError,
  getGovernance,
  type Citation,
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

function PackSkeleton() {
  return (
    <div className="space-y-3">
      <Skeleton className="h-6 w-2/3" />
      <Skeleton className="h-20 w-full" />
      <Skeleton className="h-32 w-full" />
    </div>
  );
}

function PackBody({ pack }: { pack: GovernanceEvidencePack }) {
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
            // The compliance flag is now derived from a real consistency check
            // over the evidence (issue #194), so a false value is a genuine
            // signal — surface it explicitly rather than silently hiding it.
            <Badge variant="destructive" className="font-normal">
              FINOS check failed
            </Badge>
          )}
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
          <dt>Model</dt>
          <dd className="text-foreground">{pack.model_used}</dd>
          <dt>Framework</dt>
          <dd className="text-foreground">{pack.framework_version}</dd>
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
