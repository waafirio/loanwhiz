"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

import { ApiError, getCapabilityMatrix, type CapabilityMatrix } from "@/lib/api";
import {
  ErrorState,
  LoadingState,
  PageHeader,
} from "@/components/page-states";
import { CapabilityMatrixGrid } from "@/components/capability-matrix-grid";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/**
 * Showcase — the refreshed demo's headline/landing (#242, C4, epic #236).
 *
 * Makes the epic's thesis *visible*: the same governed structured-finance
 * primitives, applied across Dutch + Italian + Spanish RMBS. Renders the
 * `primitives × deals` capability matrix from `GET /capability-matrix` as a clean
 * grid, colour-coded by honest state (`validated` / `ran` / `not-applicable`),
 * with every cell's real reason surfaced — `not-applicable` shown as a feature
 * (honest scope), never hidden, never faked green.
 *
 * Frames the cross-jurisdiction generality story (flag/label per deal column),
 * links each `validated` cell to its Validation view, and carries the
 * governance/provenance surface. How many such cells there are is read from
 * the tally this page renders — never named here, because a count written into
 * a file goes stale the next time a deal or an answer key lands (#602).
 *
 * Follows web/CONTRACT.md: Client Component, useEffect/useState, three render
 * states (loading skeleton / error card / data), shadcn light theme, no new deps.
 */
export default function ShowcasePage() {
  const [data, setData] = useState<CapabilityMatrix | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getCapabilityMatrix()
      .then((d) => !cancelled && setData(d))
      .catch(
        (e) =>
          !cancelled &&
          setError(
            e instanceof ApiError
              ? e.message
              : "Failed to load the capability matrix",
          ),
      );
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Showcase — one governed primitive set, three jurisdictions"
        description="The same structured-finance primitives, applied across Dutch, Italian and Spanish RMBS. Every cell shows what actually ran — and the honest reason where it didn't."
      />
      {error ? (
        <ErrorState title="Could not load the capability matrix" message={error} />
      ) : !data ? (
        <LoadingState />
      ) : (
        <ShowcaseContent matrix={data} />
      )}
    </div>
  );
}

function ShowcaseContent({ matrix }: { matrix: CapabilityMatrix }) {
  const validated = matrix.tally.validated ?? 0;
  const ran = matrix.tally.ran ?? 0;
  const notApplicable = matrix.tally["not-applicable"] ?? 0;

  // Distinct jurisdictions, in column order, for the generality headline.
  const jurisdictions = Array.from(
    new Set(matrix.deals.map((d) => d.jurisdiction)),
  );

  return (
    <div className="space-y-6">
      {/* Headline tally — the honest cross-jurisdiction story at a glance. */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <TallyCard
          label="Validated"
          value={validated}
          hint="Ran and reconciled to external truth"
          dot="bg-emerald-500"
        />
        <TallyCard
          label="Ran"
          value={ran}
          hint="Executed; no external truth to check against"
          dot="bg-sky-500"
        />
        <TallyCard
          label="Not applicable"
          value={notApplicable}
          hint="Inputs absent — with the real reason"
          dot="bg-muted-foreground/50"
        />
        <TallyCard
          label="Jurisdictions"
          value={jurisdictions.length}
          hint={jurisdictions.join(" · ")}
          dot="bg-foreground/60"
        />
      </div>

      {/* The matrix grid — the heart of the showcase. */}
      <CapabilityMatrixGrid matrix={matrix} />

      {/* Standing honesty disclosure, straight from the backend. */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">How to read this matrix</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm leading-relaxed text-muted-foreground">
          <p>{matrix.note}</p>
          <p>
            Hover any cell for the honest reason behind its state. The{" "}
            {validated}{" "}
            <span className="font-medium text-emerald-700">validated</span>{" "}
            {validated === 1 ? "cell links" : "cells link"} through to their
            proof — our waterfall engine reconciled against those deals&apos;
            own published Notes &amp; Cash Priorities of Payments, to the cent.
            That count is read from the tally rendered above rather than written
            into this file; a transcribed one had gone stale in three documents
            at once (#602). The auditable trail behind every agent answer lives
            on the{" "}
            <Link
              href="/governance"
              className="text-primary underline-offset-4 hover:underline"
            >
              Governance
            </Link>{" "}
            view.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

function TallyCard({
  label,
  value,
  hint,
  dot,
}: {
  label: string;
  value: number;
  hint: string;
  dot: string;
}) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
          <span className={`size-2 rounded-full ${dot}`} aria-hidden />
          {label}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-semibold tabular-nums">{value}</div>
        <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>
      </CardContent>
    </Card>
  );
}
