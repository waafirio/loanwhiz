"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  ApiError,
  postProjection,
  type ProjectionResult,
} from "@/lib/api";
import { useSelectedDeal } from "@/lib/deal-context";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  NoTapesNotice,
  PageHeader,
  useDealHasTapes,
} from "@/components/page-states";
import { TablePagination } from "@/components/table-pagination";
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
import { usePagination } from "@/lib/use-pagination";

/**
 * Projection horizon, in months. The deal runs ~4 years monthly, so the demo
 * projects over a 48-month horizon (the endpoint clamps to what it can model).
 */
const HORIZON_MONTHS = 48;

export default function ProjectionPage() {
  const { dealId } = useSelectedDeal();
  // The forward projection runs off the deal's reported tape state — a seasoned
  // deal has no published tapes, so degrade to NoTapesNotice rather than project
  // a tape-less deal off another deal's base case.
  const hasTapes = useDealHasTapes(dealId);
  // Tag the result with its deal so a deal switch falls back to the loading
  // state without a synchronous setState in the effect (see Overview page).
  const [state, setState] = useState<{
    dealId: string;
    data: ProjectionResult | null;
    error: string | null;
  }>({ dealId, data: null, error: null });

  useEffect(() => {
    if (hasTapes === false) return;
    let cancelled = false;
    postProjection(
      { scenarios: ["base", "stress"], months: HORIZON_MONTHS },
      dealId,
    )
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
              e instanceof ApiError ? e.message : "Failed to load projection",
          }),
      );
    return () => {
      cancelled = true;
    };
  }, [dealId, hasTapes]);

  const current = state.dealId === dealId ? state : null;
  const data = current?.data ?? null;
  const error = current?.error ?? null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Projection"
        description={`Forward payment-waterfall projection — base vs stress over a ${HORIZON_MONTHS}-month horizon.`}
      />
      {hasTapes === false ? (
        <NoTapesNotice what="the forward projection" />
      ) : error ? (
        <ErrorState title="Could not load projection" message={error} />
      ) : !data ? (
        <LoadingState />
      ) : (
        <ProjectionContent result={data} />
      )}
    </div>
  );
}

function ProjectionContent({ result }: { result: ProjectionResult }) {
  const scenarios = useMemo(
    () => result.scenarios.filter((s) => result.projections[s]),
    [result],
  );

  // Final remaining pool vs cumulative losses per scenario — the engine-derived
  // outcome surface the #319 `/project` response actually carries (the old
  // `total_distributed` / `shortfall` waterfall fields were dropped). Guard
  // every read with `?? 0` so a partial response renders zero, never NaN/throw.
  const chartData = useMemo(
    () =>
      scenarios.map((s) => {
        const p = result.projections[s];
        return {
          scenario: humanize(s),
          poolRemaining: p.final_pool_balance_eur ?? 0,
          losses: p.cumulative_losses ?? 0,
        };
      }),
    [scenarios, result],
  );

  // Per-period tranche-principal rows are scenarios × periods; paginate so a
  // many-scenario / long-horizon response stays bounded in the DOM. `periods`
  // is guarded with `?? []` so a no-periods scenario contributes no rows
  // instead of throwing on `.map`.
  const periodRows = useMemo(
    () =>
      scenarios.flatMap((s) =>
        (result.projections[s].periods ?? []).map((p) => ({
          scenario: s,
          period: p.period,
          reporting_date: p.reporting_date,
          class_a_principal_eur: p.class_a_principal_eur ?? 0,
          class_b_principal_eur: p.class_b_principal_eur ?? 0,
          class_c_principal_eur: p.class_c_principal_eur ?? 0,
        })),
      ),
    [scenarios, result],
  );
  const periodPagination = usePagination(periodRows, 12);

  if (scenarios.length === 0) {
    return <EmptyState message="No projection scenarios returned." />;
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            Final pool vs cumulative losses ({result.months}-month horizon)
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={320}>
            <BarChart data={chartData} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="scenario" fontSize={12} />
              <YAxis fontSize={12} width={80} />
              <Tooltip formatter={(v) => formatCurrency(Number(v))} />
              <Legend />
              <Bar dataKey="poolRemaining" name="Final pool balance" fill="#2563eb" />
              <Bar dataKey="losses" name="Cumulative losses" fill="#dc2626" />
            </BarChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Scenario summary</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Scenario</TableHead>
                <TableHead className="text-right">Final pool balance</TableHead>
                <TableHead className="text-right">Final Class A balance</TableHead>
                <TableHead className="text-right">Cumulative losses</TableHead>
                <TableHead className="text-right">Class A WAL</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {scenarios.map((s) => {
                const p = result.projections[s];
                // WAL is surfaced both on the per-scenario projection and in the
                // top-level `wal` map; prefer the projection, fall back to the map.
                const walYears =
                  p.wal_class_a_years ?? result.wal?.[s]?.wal_class_a_years ?? null;
                const walMonths =
                  p.wal_class_a_months ?? result.wal?.[s]?.wal_class_a_months ?? null;
                return (
                  <TableRow key={s}>
                    <TableCell className="font-medium">{humanize(s)}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatCurrency(p.final_pool_balance_eur ?? 0)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatCurrency(p.final_class_a_balance ?? 0)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatCurrency(p.cumulative_losses ?? 0)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {walYears != null && walMonths != null
                        ? `${walYears.toFixed(2)} yr (${walMonths.toFixed(1)} mo)`
                        : "—"}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            Per-period tranche principal by scenario
          </CardTitle>
        </CardHeader>
        <CardContent>
          {periodRows.length === 0 ? (
            <EmptyState message="No per-period projection data returned." />
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Scenario</TableHead>
                    <TableHead className="text-right">Period</TableHead>
                    <TableHead className="text-right">Class A principal</TableHead>
                    <TableHead className="text-right">Class B principal</TableHead>
                    <TableHead className="text-right">Class C principal</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {periodPagination.pageItems.map((r) => (
                    <TableRow key={`${r.scenario}-${r.period}`}>
                      <TableCell className="font-medium">
                        {humanize(r.scenario)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {r.period}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatCurrency(r.class_a_principal_eur)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatCurrency(r.class_b_principal_eur)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatCurrency(r.class_c_principal_eur)}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <TablePagination pagination={periodPagination} noun="rows" />
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
