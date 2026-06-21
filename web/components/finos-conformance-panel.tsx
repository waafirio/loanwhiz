"use client";

import { Scale } from "lucide-react";

import type {
  FinosConformanceStatus,
  FinosConformanceSummary,
} from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/**
 * FINOS framework conformance panel (#278) — renders the mapped FINOS AI
 * Governance Framework control catalogue served by
 * `GET /governance/finos-conformance`. This is the surface that makes the
 * Governance view's FINOS claim real: each of the framework's mitigation
 * controls is shown with LoanWhiz's honest status (satisfied / partial /
 * not applicable). The same summary is what `finos_compliant` on every
 * evidence pack reflects.
 */
export function FinosConformancePanel({
  conformance,
  error,
}: {
  conformance: FinosConformanceSummary | null;
  error: string | null;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Scale className="size-4" />
          FINOS control catalogue conformance
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {error ? (
          <div className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            {error}
          </div>
        ) : !conformance ? (
          <ConformanceSkeleton />
        ) : (
          <ConformanceBody conformance={conformance} />
        )}
      </CardContent>
    </Card>
  );
}

function ConformanceSkeleton() {
  return (
    <div className="space-y-3">
      <Skeleton className="h-6 w-1/2" />
      <Skeleton className="h-40 w-full" />
    </div>
  );
}

function ConformanceBody({
  conformance,
}: {
  conformance: FinosConformanceSummary;
}) {
  const { counts, total_controls, is_conformant, controls, reference } =
    conformance;
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-1.5 text-sm">
        {is_conformant ? (
          <Badge variant="secondary" className="font-normal">
            Framework conformant
          </Badge>
        ) : (
          <Badge variant="destructive" className="font-normal">
            Not conformant
          </Badge>
        )}
        <Badge variant="outline" className="font-normal">
          {total_controls} controls mapped
        </Badge>
        <Badge variant="outline" className="font-normal">
          {counts.satisfied} satisfied
        </Badge>
        <Badge variant="outline" className="font-normal">
          {counts.partial} partial
        </Badge>
        <Badge variant="outline" className="font-normal">
          {counts.not_applicable} n/a
        </Badge>
      </div>

      <p className="text-xs text-muted-foreground">
        An honest first-party self-assessment against the published FINOS AI
        Governance Framework catalogue. <code>partial</code> and{" "}
        <code>not applicable</code> are reasoned, bounded states (often deferring
        a deployment-edge concern to the calling application), not gaps hidden
        behind a blanket claim.
      </p>

      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="sticky left-0 z-20 w-[120px] min-w-[120px] bg-card">
                Control
              </TableHead>
              <TableHead className="min-w-[280px]">Title</TableHead>
              <TableHead className="w-[120px] min-w-[120px]">Status</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {controls.map((c) => (
              <TableRow key={c.control_id}>
                <TableCell className="sticky left-0 z-10 w-[120px] min-w-[120px] bg-card align-top font-mono text-xs">
                  {c.control_id}
                </TableCell>
                <TableCell className="max-w-[640px] whitespace-normal break-words align-top text-sm">
                  {c.title}
                  <span className="block text-xs text-muted-foreground">
                    {c.rationale}
                  </span>
                </TableCell>
                <TableCell className="w-[120px] min-w-[120px] align-top">
                  <StatusBadge status={c.status} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {reference ? (
        <p className="text-xs text-muted-foreground">
          Reference:{" "}
          <a
            href={reference}
            target="_blank"
            rel="noreferrer"
            className="underline underline-offset-2"
          >
            {reference}
          </a>
        </p>
      ) : null}
    </div>
  );
}

function StatusBadge({ status }: { status: FinosConformanceStatus }) {
  if (status === "satisfied") {
    return (
      <Badge variant="secondary" className="font-normal">
        Satisfied
      </Badge>
    );
  }
  if (status === "partial") {
    return (
      <Badge variant="outline" className="font-normal">
        Partial
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="font-normal text-muted-foreground">
      Not applicable
    </Badge>
  );
}
