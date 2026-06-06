"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  ApiError,
  getTapeAnalytics,
  type TapeAnalyticsPeriod,
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
import { formatCurrency, formatPct, humanize } from "@/lib/format";
import { usePagination } from "@/lib/use-pagination";

const BAR_COLORS = ["#2563eb", "#16a34a", "#d97706", "#9333ea", "#dc2626", "#0891b2"];

export default function PoolPage() {
  const { dealId } = useSelectedDeal();
  // Seasoned deals have no published loan tapes — pool analytics is tape-driven,
  // so we degrade to NoTapesNotice rather than render an empty trend.
  const hasTapes = useDealHasTapes(dealId);
  // Tag the result with its deal so a deal switch falls back to the loading
  // state without a synchronous setState in the effect (see Overview page).
  const [state, setState] = useState<{
    dealId: string;
    data: TapeAnalyticsPeriod[] | null;
    error: string | null;
  }>({ dealId, data: null, error: null });

  useEffect(() => {
    if (hasTapes === false) return;
    let cancelled = false;
    getTapeAnalytics(dealId)
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
              e instanceof ApiError
                ? e.message
                : "Failed to load pool analytics",
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
        title="Pool & Performance"
        description="Per-period pool analytics across the reported ESMA tapes — balance, loan count, arrears, weighted LTV, and distributions."
      />
      {hasTapes === false ? (
        <NoTapesNotice what="per-period pool analytics" />
      ) : error ? (
        <ErrorState title="Could not load pool analytics" message={error} />
      ) : !data ? (
        <LoadingState />
      ) : (
        <PoolContent periods={data} />
      )}
    </div>
  );
}

/** A label for the period — prefer the registered tape date. */
function periodLabel(p: TapeAnalyticsPeriod): string {
  return p.tape_date || p.reporting_date;
}

/** Turn a breakdown map (label → %) into the latest period's bar-chart rows. */
function breakdownRows(
  breakdown: Record<string, number> | null,
): Array<{ name: string; pct: number }> {
  if (!breakdown) return [];
  return Object.entries(breakdown)
    .map(([name, pct]) => ({ name: humanize(name), pct }))
    .sort((a, b) => b.pct - a.pct);
}

function PoolContent({ periods }: { periods: TapeAnalyticsPeriod[] }) {
  // One point per reporting period — the x-axis is a real time axis (period
  // date), so a ~48-period response reads as a trend line rather than 48
  // categorical bars. `minTickGap` lets recharts thin the date ticks so they
  // stay legible at high period counts.
  const chartData = useMemo(
    () =>
      periods.map((p) => ({
        period: periodLabel(p),
        pool_balance_eur: p.pool_balance_eur,
        loan_count: p.loan_count,
        wtd_ltv: p.pool_stats.wtd_ltv ?? null,
        wtd_coupon_pct: p.pool_stats.wtd_coupon_pct ?? null,
      })),
    [periods],
  );

  // The per-period metrics table reads down by period (one row per period),
  // not across (one column per period) — so 48 periods is a long paginated
  // list, not 48 unreadable columns.
  const tableRows = useMemo(() => [...periods].reverse(), [periods]); // newest first
  const pagination = usePagination(tableRows, 12);

  const latest = periods[periods.length - 1];

  if (periods.length === 0) {
    return <EmptyState message="No per-period pool analytics available." />;
  }

  const epcRows = breakdownRows(latest.epc_breakdown);
  const arrearsRows = breakdownRows(latest.arrears_breakdown);
  const geoRows = breakdownRows(latest.geographic_breakdown);

  return (
    <div className="space-y-6">
      {/* Pool balance over time */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            Pool balance over time ({periods.length} periods)
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart
              data={chartData}
              margin={{ top: 8, right: 16, bottom: 8, left: 8 }}
            >
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis
                dataKey="period"
                fontSize={12}
                minTickGap={24}
                tickMargin={8}
              />
              <YAxis fontSize={12} width={88} />
              <Tooltip formatter={(v) => formatCurrency(Number(v))} />
              <Legend />
              <Line
                type="monotone"
                dataKey="pool_balance_eur"
                name="Pool balance"
                stroke="#2563eb"
                dot={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>

      {/* Weighted LTV & coupon over time */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            Weighted LTV &amp; coupon over time
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={280}>
            <LineChart
              data={chartData}
              margin={{ top: 8, right: 16, bottom: 8, left: 8 }}
            >
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis
                dataKey="period"
                fontSize={12}
                minTickGap={24}
                tickMargin={8}
              />
              <YAxis fontSize={12} unit="%" width={56} />
              <Tooltip formatter={(v) => formatPct(Number(v))} />
              <Legend />
              <Line
                type="monotone"
                dataKey="wtd_ltv"
                name="Weighted LTV"
                stroke="#16a34a"
                dot={false}
                connectNulls
              />
              <Line
                type="monotone"
                dataKey="wtd_coupon_pct"
                name="Weighted coupon"
                stroke="#d97706"
                dot={false}
                connectNulls
              />
            </LineChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>

      {/* Per-period headline metrics — one row per period, paginated */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Per-period metrics</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Period</TableHead>
                <TableHead className="text-right">Pool balance</TableHead>
                <TableHead className="text-right">Loan count</TableHead>
                <TableHead className="text-right">Weighted LTV</TableHead>
                <TableHead className="text-right">Weighted coupon</TableHead>
                <TableHead className="text-right">Seasoning (mo)</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {pagination.pageItems.map((p) => (
                <TableRow key={periodLabel(p)}>
                  <TableCell className="font-medium">{periodLabel(p)}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCurrency(p.pool_balance_eur)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {p.loan_count.toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {p.pool_stats.wtd_ltv != null
                      ? formatPct(p.pool_stats.wtd_ltv)
                      : "—"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {p.pool_stats.wtd_coupon_pct != null
                      ? formatPct(p.pool_stats.wtd_coupon_pct)
                      : "—"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {p.pool_stats.wtd_seasoning != null
                      ? p.pool_stats.wtd_seasoning.toFixed(1)
                      : "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <TablePagination pagination={pagination} noun="periods" />
        </CardContent>
      </Card>

      {/* Distribution breakdowns (latest period) */}
      <div className="grid gap-6 lg:grid-cols-2">
        <BreakdownCard
          title={`Arrears breakdown (${periodLabel(latest)})`}
          rows={arrearsRows}
          emptyMessage="No arrears breakdown in this tape."
        />
        <BreakdownCard
          title={`EPC distribution (${periodLabel(latest)})`}
          rows={epcRows}
          emptyMessage="No EPC breakdown in this tape."
        />
      </div>

      {geoRows.length > 0 ? (
        <BreakdownCard
          title={`Geographic distribution (${periodLabel(latest)})`}
          rows={geoRows}
          emptyMessage="No geographic breakdown in this tape."
        />
      ) : null}
    </div>
  );
}

function BreakdownCard({
  title,
  rows,
  emptyMessage,
}: {
  title: string;
  rows: Array<{ name: string; pct: number }>;
  emptyMessage: string;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <EmptyState message={emptyMessage} />
        ) : (
          <ResponsiveContainer width="100%" height={Math.max(220, rows.length * 36)}>
            <BarChart
              data={rows}
              layout="vertical"
              margin={{ top: 8, right: 24, bottom: 8, left: 8 }}
            >
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis type="number" fontSize={12} unit="%" />
              <YAxis
                type="category"
                dataKey="name"
                fontSize={12}
                width={120}
              />
              <Tooltip formatter={(v) => formatPct(Number(v))} />
              <Bar dataKey="pct" name="Share">
                {rows.map((r, i) => (
                  <Cell key={r.name} fill={BAR_COLORS[i % BAR_COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}
