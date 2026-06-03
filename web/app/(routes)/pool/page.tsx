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
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "@/components/page-states";
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

const BAR_COLORS = ["#2563eb", "#16a34a", "#d97706", "#9333ea", "#dc2626", "#0891b2"];

export default function PoolPage() {
  const [data, setData] = useState<TapeAnalyticsPeriod[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getTapeAnalytics()
      .then(setData)
      .catch((e) =>
        setError(
          e instanceof ApiError ? e.message : "Failed to load pool analytics",
        ),
      );
  }, []);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Pool & Performance"
        description="Per-period pool analytics across the reported ESMA tapes — balance, loan count, arrears, weighted LTV, and distributions."
      />
      {error ? (
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

  const latest = periods[periods.length - 1];

  if (periods.length === 0) {
    return <EmptyState message="No per-period pool analytics available." />;
  }

  const epcRows = breakdownRows(latest.epc_breakdown);
  const arrearsRows = breakdownRows(latest.arrears_breakdown);
  const geoRows = breakdownRows(latest.geographic_breakdown);

  return (
    <div className="space-y-6">
      {/* Pool balance trend */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Pool balance across periods</CardTitle>
        </CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart
              data={chartData}
              margin={{ top: 8, right: 16, bottom: 8, left: 8 }}
            >
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="period" fontSize={12} />
              <YAxis fontSize={12} width={88} />
              <Tooltip formatter={(v) => formatCurrency(Number(v))} />
              <Legend />
              <Line
                type="monotone"
                dataKey="pool_balance_eur"
                name="Pool balance"
                stroke="#2563eb"
                dot
              />
            </LineChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>

      {/* Per-period headline metrics */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Per-period metrics</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Metric</TableHead>
                {periods.map((p) => (
                  <TableHead key={periodLabel(p)} className="text-right">
                    {periodLabel(p)}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow>
                <TableCell className="font-medium">Pool balance</TableCell>
                {periods.map((p) => (
                  <TableCell
                    key={periodLabel(p)}
                    className="text-right tabular-nums"
                  >
                    {formatCurrency(p.pool_balance_eur)}
                  </TableCell>
                ))}
              </TableRow>
              <TableRow>
                <TableCell className="font-medium">Loan count</TableCell>
                {periods.map((p) => (
                  <TableCell
                    key={periodLabel(p)}
                    className="text-right tabular-nums"
                  >
                    {p.loan_count.toLocaleString()}
                  </TableCell>
                ))}
              </TableRow>
              <TableRow>
                <TableCell className="font-medium">Weighted LTV</TableCell>
                {periods.map((p) => (
                  <TableCell
                    key={periodLabel(p)}
                    className="text-right tabular-nums"
                  >
                    {p.pool_stats.wtd_ltv != null
                      ? formatPct(p.pool_stats.wtd_ltv)
                      : "—"}
                  </TableCell>
                ))}
              </TableRow>
              <TableRow>
                <TableCell className="font-medium">Weighted coupon</TableCell>
                {periods.map((p) => (
                  <TableCell
                    key={periodLabel(p)}
                    className="text-right tabular-nums"
                  >
                    {p.pool_stats.wtd_coupon_pct != null
                      ? formatPct(p.pool_stats.wtd_coupon_pct)
                      : "—"}
                  </TableCell>
                ))}
              </TableRow>
              <TableRow>
                <TableCell className="font-medium">Weighted seasoning (mo)</TableCell>
                {periods.map((p) => (
                  <TableCell
                    key={periodLabel(p)}
                    className="text-right tabular-nums"
                  >
                    {p.pool_stats.wtd_seasoning != null
                      ? p.pool_stats.wtd_seasoning.toFixed(1)
                      : "—"}
                  </TableCell>
                ))}
              </TableRow>
            </TableBody>
          </Table>
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
