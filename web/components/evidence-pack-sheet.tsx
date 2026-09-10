"use client";

import { Fragment, useEffect, useState } from "react";
import { Database, FileText, ShieldCheck } from "lucide-react";

import {
  ApiError,
  citationDataSource,
  getGovernance,
  type BookResponse,
  type Citation,
  type ConcentrationBucket,
  type ConcentrationSplit,
  type CrossDealConcentration,
  type DataSource,
  type DueDiligenceCheck,
  type DueDiligenceRecord,
  type DueDiligenceSource,
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

/* -------------------------------------------------------------------------
 * Look-through concentration disclosure (#565, epic #560)
 *
 * The provenance surface above answers "where did this number come from".
 * This region answers the question #564 made answerable: "what could still
 * move it". It lives here rather than in a parallel component because a
 * reader who has learned to look for a disclosure block should find every
 * kind of disclosure in the same shape.
 *
 * Four properties #564 encoded as types, which this must not undo:
 *
 *   1. Every bucket carries its proven/candidate/unresolved split, and the
 *      three are rendered as three figures. They are never added together
 *      into an "identified" total and never divided into a score: a grade
 *      built out of two kinds is the number a reader treats as the
 *      measurement (#549), and summing two kinds needs no division, so a ban
 *      that only looks for a ratio does not catch it.
 *   2. An unresolved name is never netted anywhere, including "Other". There
 *      is no residual row in this file, and assets the axis cannot place
 *      render as their own kind (`unattributed`) with their own label.
 *   3. Every deal's own stated reporting date is shown. When they disagree —
 *      they do, on the committed pair — the disagreement is a rendered line,
 *      not a tooltip.
 *   4. Every industry figure names its taxonomy, because the same book
 *      concentrates differently on S&P than on Fitch (#563).
 *
 * NOTE for anyone extending this: `PackBody` above renders a provenance badge
 * reading "direct ingestion" for a `derived` or `synthetic` source. That is a
 * known defect belonging to the provenance epic, not copied here — the
 * disclosure below reads its labels off the figure rather than off a binary.
 * ------------------------------------------------------------------------- */

/** How each obligor-resolution tier names itself, and what it means. */
const TIER_LABELS: Record<string, string> = {
  proven_shared: "Proven shared",
  candidate_proposed: "Candidate (proposed, not applied)",
  unresolved: "Unresolved",
};

function tierLabel(tier: string): string {
  return TIER_LABELS[tier] ?? tier;
}

/**
 * The headline residual: how much of the book's obligor identity is unproven,
 * on the same surface as the concentration figures it qualifies.
 *
 * A buyer reading "3.2% exposure to Chemicals" needs to see, without clicking
 * anything, that the names behind it may not be distinct. So the unproven-name
 * count is a figure of the same weight as the shares, not a caption.
 */
export function ConcentrationDisclosure({
  figure,
}: {
  figure: CrossDealConcentration;
}) {
  return (
    <section className="space-y-3 rounded-lg border border-amber-500/40 bg-amber-500/5 px-4 py-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant="outline" className="font-normal">
          <ShieldCheck className="mr-1 size-3" />
          {figure.axis.label} axis
        </Badge>
        <Badge variant="destructive" className="font-normal">
          {figure.unproven_name_count} of {figure.name_count} names unproven
        </Badge>
        <Badge variant="destructive" className="font-normal">
          {formatPct(figure.not_proven_share_pct, 2)} of balance
        </Badge>
        <Badge variant="outline" className="font-normal">
          Distinct obligors {figure.obligor_bounds.lower}&ndash;
          {figure.obligor_bounds.upper}
        </Badge>
      </div>

      <p className="text-sm">
        Obligor identity could not be proven for{" "}
        <strong>{figure.unproven_name_count}</strong> of the{" "}
        {figure.name_count} names these deals hold between them. Every share
        below is exact as a balance; the number of distinct borrowers behind it
        is not. {figure.proposal_count} candidate link
        {figure.proposal_count === 1 ? " is" : "s are"} proposed and none is
        applied, which is why the obligor count is a range rather than a
        figure.
      </p>

      {/* Each tier as its own figure. Never a sum, never a ratio: "1 of 7
          verified" is a grade with no division in it, and it is still a
          grade. */}
      <dl className="grid grid-cols-[1fr_auto_auto] gap-x-4 gap-y-1 text-xs">
        {figure.tiers.map((t) => (
          <Fragment key={t.tier}>
            <dt className="text-muted-foreground">{tierLabel(t.tier)}</dt>
            <dd className="text-right tabular-nums">
              {t.name_count} name{t.name_count === 1 ? "" : "s"}
            </dd>
            <dd className="text-right tabular-nums">
              {formatCurrency(t.balance)} · {formatPct(t.share_pct, 2)}
            </dd>
          </Fragment>
        ))}
      </dl>

      <ReportingDates figure={figure} />

      {figure.unattributed.asset_count > 0 ? (
        <p className="text-xs text-muted-foreground">
          <strong className="text-foreground">
            Unplaced by this axis: {formatCurrency(figure.unattributed.balance)}{" "}
            ({formatPct(figure.unattributed.share_pct, 2)},{" "}
            {figure.unattributed.asset_count} assets)
          </strong>{" "}
          — the report published no {figure.axis.label} value for these. They
          are excluded from every bucket rather than collected into one, so no
          share below is diluted by them.
        </p>
      ) : null}

      <p className="text-xs text-muted-foreground italic">
        {figure.disclosure}
      </p>
      <p className="text-xs text-muted-foreground italic">
        {figure.obligor_disclosure}
      </p>
    </section>
  );
}

/**
 * Both deals' stated reporting dates, and — when they disagree — the fact
 * that they do. An aggregate implying a single as-of is a figure nobody can
 * reconcile back to either source, so the two dates render separately and the
 * mismatch is stated in words.
 */
export function ReportingDates({ figure }: { figure: CrossDealConcentration }) {
  return (
    <div className="space-y-1">
      <dl className="grid grid-cols-[1fr_auto] gap-x-4 text-xs">
        {figure.as_of.map((d) => (
          <Fragment key={d.deal}>
            <dt className="text-muted-foreground">
              {d.deal_name ?? d.deal} reported as of
            </dt>
            <dd className="text-right font-medium tabular-nums">{d.stated}</dd>
          </Fragment>
        ))}
      </dl>
      {figure.dates_align ? null : (
        <p className="text-xs font-medium text-amber-700 dark:text-amber-400">
          These reports are as of different dates. This figure adds them
          together as published; it reconciles to neither source on its own.
        </p>
      )}
    </div>
  );
}

/**
 * One bucket's tier split, for a row in the concentration table.
 *
 * Three figures, in tier order, each labelled. Deliberately not a single
 * "proven %" — that would be a score over two kinds of thing, and the reader
 * would treat it as the measurement.
 */
export function BucketSplit({ split }: { split: ConcentrationSplit }) {
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-2 text-[11px] text-muted-foreground">
      <dt>Proven</dt>
      <dd className="text-right tabular-nums text-foreground">
        {formatCurrency(split.proven_shared)}
      </dd>
      <dt>Candidate</dt>
      <dd className="text-right tabular-nums text-foreground">
        {formatCurrency(split.candidate_proposed)}
      </dd>
      <dt>Unresolved</dt>
      <dd className="text-right tabular-nums text-foreground">
        {formatCurrency(split.unresolved)}
      </dd>
    </dl>
  );
}

/**
 * One bucket's per-deal contributions, each stamped with its own deal's date.
 * The date travels with the contribution because that is the only level at
 * which it is true.
 */
export function BucketContributions({ bucket }: { bucket: ConcentrationBucket }) {
  return (
    <ul className="space-y-0.5 text-[11px] text-muted-foreground">
      {bucket.per_deal.map((c) => (
        <li key={c.deal} className="tabular-nums">
          {c.deal} — {formatCurrency(c.balance)} ({c.asset_count} assets) as of{" "}
          {c.as_of}
        </li>
      ))}
      {bucket.reached_by_one_deal ? (
        <li className="italic">Held by one deal only.</li>
      ) : null}
    </ul>
  );
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
