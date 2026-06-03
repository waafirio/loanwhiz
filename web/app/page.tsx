"use client";

import { useEffect, useState } from "react";

import {
  ApiError,
  getDealModel,
  type DealModel,
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/** Shape of a tranche if the model ever exposes one (forward-compatible). */
interface Tranche {
  name?: string;
  class?: string;
  balance?: number;
  rate_pct?: number;
}

export default function OverviewPage() {
  const [data, setData] = useState<DealModel | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getDealModel()
      .then(setData)
      .catch((e) =>
        setError(e instanceof ApiError ? e.message : "Failed to load deal model"),
      );
  }, []);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Overview"
        description="Deal model and headline figures for Green Lion 2026-1."
      />
      {error ? (
        <ErrorState title="Could not load deal model" message={error} />
      ) : !data ? (
        <LoadingState />
      ) : (
        <OverviewContent data={data} />
      )}
    </div>
  );
}

function OverviewContent({ data }: { data: DealModel }) {
  // The model endpoint may not yet surface extracted structure (known backend
  // gap: tranches=0). Read these defensively and render an empty state when
  // absent rather than crashing.
  const tranches = (data.tranches as Tranche[] | undefined) ?? [];
  const triggers = (data.triggers as unknown[] | undefined) ?? [];
  const completeness =
    typeof data.completeness_score === "number"
      ? data.completeness_score
      : null;

  return (
    <div className="space-y-6">
      {/* Headline cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Deal
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-lg font-semibold">{data.deal_name}</div>
            <a
              href={data.prospectus_url}
              target="_blank"
              rel="noreferrer"
              className="text-sm text-primary underline-offset-4 hover:underline"
            >
              Prospectus
            </a>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Reporting periods
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-semibold">{data.tape_urls.length}</div>
            <div className="text-sm text-muted-foreground">ESMA loan tapes</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Completeness
            </CardTitle>
          </CardHeader>
          <CardContent>
            {completeness === null ? (
              <div className="text-sm text-muted-foreground">
                Not reported
              </div>
            ) : (
              <div className="text-2xl font-semibold">
                {Math.round(completeness * 100)}%
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Capital structure / tranches */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Capital structure</CardTitle>
        </CardHeader>
        <CardContent>
          {tranches.length === 0 ? (
            <EmptyState message="No tranches extracted yet — the deal-model extraction does not expose a tranche structure for this deal." />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Tranche</TableHead>
                  <TableHead className="text-right">Balance</TableHead>
                  <TableHead className="text-right">Rate</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tranches.map((t, i) => (
                  <TableRow key={t.name ?? t.class ?? i}>
                    <TableCell>{t.name ?? t.class ?? `Tranche ${i + 1}`}</TableCell>
                    <TableCell className="text-right">
                      {t.balance != null ? t.balance.toLocaleString() : "—"}
                    </TableCell>
                    <TableCell className="text-right">
                      {t.rate_pct != null ? `${t.rate_pct}%` : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Triggers (names only — full status lives on Compliance) */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Triggers</CardTitle>
        </CardHeader>
        <CardContent>
          {triggers.length === 0 ? (
            <EmptyState message="No triggers in the deal model — see the Compliance page for the live covenant monitor." />
          ) : (
            <div className="flex flex-wrap gap-2">
              {triggers.map((t, i) => (
                <Badge key={i} variant="secondary">
                  {String((t as { name?: string }).name ?? t)}
                </Badge>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Source documents */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Loan tapes</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Date</TableHead>
                <TableHead>Source</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.tape_urls.map((t) => (
                <TableRow key={t.url}>
                  <TableCell>{t.date}</TableCell>
                  <TableCell>
                    <a
                      href={t.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-primary underline-offset-4 hover:underline"
                    >
                      Tape CSV
                    </a>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
