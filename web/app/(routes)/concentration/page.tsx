"use client";

import { useEffect, useState } from "react";

import {
  ApiError,
  CONCENTRATION_AXES,
  getCrossDealConcentration,
  type ConcentrationAxisKey,
  type CrossDealConcentration,
} from "@/lib/api";
import {
  BucketContributions,
  BucketSplit,
  ConcentrationDisclosure,
} from "@/components/evidence-pack-sheet";
import { ErrorState, LoadingState, PageHeader } from "@/components/page-states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatCurrency, formatPct } from "@/lib/format";

/**
 * Look-through concentration — what a holder of both CLOs is actually exposed
 * to, with the unresolved set visible rather than netted away (#565, epic #560).
 *
 * The figures come from `GET /cross-deal-concentration`, which serves #564's
 * types without re-deriving them. What this page is *for* is the residual: the
 * disclosure block sits above the table, not below it, because a share read
 * without it is read as a measurement of something it does not measure.
 *
 * Nothing here nets an unresolved name into a total, and there is no residual
 * "Other" row for it to be netted into (#496/#514) — assets the axis cannot
 * place render as their own kind, in the disclosure block.
 *
 * Follows web/CONTRACT.md: Client Component, useEffect/useState, three render
 * states (loading skeleton / error card / data), shadcn defaults, no new deps.
 */

/** How each axis key spells itself in the picker. */
const AXIS_LABELS: Record<ConcentrationAxisKey, string> = {
  "fitch-industry": "Fitch industry",
  "sp-industry": "S&P industry",
  country: "Country",
  "fitch-rating": "Fitch rating",
  "sp-rating": "S&P rating",
};

/** One axis's outcome, stamped with the axis it answers for. */
interface AxisResult {
  axis: ConcentrationAxisKey;
  figure: CrossDealConcentration | null;
  error: string | null;
}

export default function ConcentrationPage() {
  const [axis, setAxis] = useState<ConcentrationAxisKey>("fitch-industry");
  const [result, setResult] = useState<AxisResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    getCrossDealConcentration(axis)
      .then((d) => !cancelled && setResult({ axis, figure: d, error: null }))
      .catch(
        (e) =>
          !cancelled &&
          setResult({
            axis,
            figure: null,
            error:
              e instanceof ApiError
                ? e.message
                : "Failed to load look-through concentration",
          }),
      );
    return () => {
      cancelled = true;
    };
  }, [axis]);

  // The result is only current if it answers for the axis now selected — so
  // switching axis shows the loading state rather than the previous axis's
  // shares under the new axis's name, which would be the wrong figure with a
  // plausible label. Derived rather than cleared in the effect body.
  const current = result?.axis === axis ? result : null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Look-through concentration"
        description="Your true single-name and sector exposure across the CLOs you hold — with the names the two reports gave no way to join shown, not netted away."
      />

      <div className="flex flex-wrap gap-1.5">
        {CONCENTRATION_AXES.map((key) => (
          <Button
            key={key}
            size="sm"
            variant={key === axis ? "default" : "outline"}
            onClick={() => setAxis(key)}
          >
            {AXIS_LABELS[key]}
          </Button>
        ))}
      </div>

      {current?.error ? (
        <ErrorState
          title="Could not load look-through concentration"
          message={current.error}
        />
      ) : !current?.figure ? (
        <LoadingState />
      ) : (
        <ConcentrationContent figure={current.figure} />
      )}
    </div>
  );
}

function ConcentrationContent({ figure }: { figure: CrossDealConcentration }) {
  return (
    <div className="space-y-6">
      {/* The residual first. It qualifies every figure below it, so it is not
          reachable only by scrolling past them. */}
      <ConcentrationDisclosure figure={figure} />

      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2">
            <span>Combined exposure by {figure.axis.label}</span>
            <Badge variant="outline" className="font-normal">
              {formatCurrency(figure.total_balance)} over {figure.asset_count}{" "}
              assets
            </Badge>
            <Badge variant="outline" className="font-normal">
              {figure.deals.join(" + ")}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  {/* The axis is named in the column head as well as the card
                      title: a share pasted out of this table without its
                      taxonomy is not comparable to any other book (#563). */}
                  <TableHead>{figure.axis.label}</TableHead>
                  <TableHead className="text-right">Balance</TableHead>
                  <TableHead className="text-right">Share</TableHead>
                  <TableHead>Obligor identity</TableHead>
                  <TableHead>Per deal, as of its own date</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {figure.buckets.map((bucket) => (
                  <TableRow key={bucket.label} className="align-top">
                    <TableCell className="font-medium">
                      {bucket.label}
                      {bucket.published_spellings.length > 1 ? (
                        <span className="block text-[11px] font-normal text-muted-foreground">
                          published as{" "}
                          {bucket.published_spellings.join(", ")}
                        </span>
                      ) : null}
                      <span className="block text-[11px] font-normal text-muted-foreground italic">
                        {bucket.disclosure}
                      </span>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatCurrency(bucket.balance)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatPct(bucket.share_pct, 2)}
                    </TableCell>
                    <TableCell>
                      <BucketSplit split={bucket.split} />
                    </TableCell>
                    <TableCell>
                      <BucketContributions bucket={bucket} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
