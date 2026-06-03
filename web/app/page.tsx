"use client";

import { useEffect, useState } from "react";

import {
  ApiError,
  getDealModel,
  type DealModel,
  type Tranche,
} from "@/lib/api";
import { useSelectedDeal } from "@/lib/deal-context";
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
import { formatCurrency, humanize } from "@/lib/format";

export default function OverviewPage() {
  const { dealId } = useSelectedDeal();
  // Tag each result/error with the deal it belongs to. When `dealId` changes
  // the tagged state no longer matches, so the page renders the loading state
  // again without a synchronous reset inside the effect (which the lint rule
  // forbids); the async fetch callbacks then publish the fresh result.
  const [state, setState] = useState<{
    dealId: string;
    data: DealModel | null;
    error: string | null;
  }>({ dealId, data: null, error: null });

  useEffect(() => {
    let cancelled = false;
    getDealModel(dealId)
      .then(
        (d) => !cancelled && setState({ dealId, data: d, error: null }),
      )
      .catch(
        (e) =>
          !cancelled &&
          setState({
            dealId,
            data: null,
            error:
              e instanceof ApiError ? e.message : "Failed to load deal model",
          }),
      );
    return () => {
      cancelled = true;
    };
  }, [dealId]);

  const current = state.dealId === dealId ? state : null;
  const data = current?.data ?? null;
  const error = current?.error ?? null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Overview"
        description="Deal model and headline figures for the selected deal."
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
  const extracted = data.deal_model;
  const cached = data.extraction_status === "cached" && extracted != null;

  // Prefer the extracted model's structure; the top-level fields mirror it on a
  // cache hit and are null otherwise.
  const tranches: Tranche[] = extracted?.tranche_structure ?? [];
  const triggers: string[] = data.trigger_names ?? extracted?.trigger_names ?? [];
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
              <div className="text-sm text-muted-foreground">Not reported</div>
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
          {!cached ? (
            <EmptyState message="Deal model not yet extracted — the extraction cache is cold for this deal. Capital structure appears once extraction has run." />
          ) : tranches.length === 0 ? (
            <EmptyState message="No tranches extracted — the prospectus tranche structure could not be derived for this deal." />
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Tranche</TableHead>
                  <TableHead className="text-right">Size</TableHead>
                  <TableHead>Rating</TableHead>
                  <TableHead>Rate</TableHead>
                  <TableHead className="text-right">Seniority</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tranches.map((t, i) => (
                  <TableRow key={t.name ?? i}>
                    <TableCell className="font-medium">
                      {t.name ? humanize(t.name) : `Tranche ${i + 1}`}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {t.size_eur != null ? formatCurrency(t.size_eur) : "—"}
                    </TableCell>
                    <TableCell>{t.rating ?? "—"}</TableCell>
                    <TableCell>{t.rate ?? "—"}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {t.seniority}
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
            <EmptyState
              message={
                cached
                  ? "No triggers in the extracted deal model — see the Compliance page for the live covenant monitor."
                  : "Deal model not yet extracted — trigger names appear once extraction has run. See the Compliance page for the live covenant monitor."
              }
            />
          ) : (
            <div className="flex flex-wrap gap-2">
              {triggers.map((t, i) => (
                <Badge key={`${t}-${i}`} variant="secondary">
                  {humanize(t)}
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
