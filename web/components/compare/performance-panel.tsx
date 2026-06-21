"use client";

import { useMemo } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import type { CompareDealRef, PerformanceSeries } from "@/lib/api";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/page-states";

const LINE_COLORS = ["#2563eb", "#16a34a", "#d97706", "#9333ea", "#dc2626"];

/** The Panel-2 metrics, one overlaid chart each (one line per deal). */
const METRICS: {
  key: keyof PerformanceSeries["points"][number];
  title: string;
  unit?: string;
  fixed?: number;
}[] = [
  { key: "pool_factor", title: "Pool factor", fixed: 3 },
  { key: "cumulative_loss_rate_pct", title: "Cumulative loss rate", unit: "%", fixed: 2 },
  { key: "total_pdl", title: "Total PDL (EUR)" },
  { key: "reserve_balance", title: "Reserve balance (EUR)" },
];

/**
 * Panel 2 — performance / risk (#283): one overlaid time-series chart per
 * metric, a line per deal on a shared reporting-date axis. Deals without a
 * reconstructable series are omitted from the overlay (the risk-summary row and
 * the coverage notes already flag them).
 */
export function PerformancePanel({
  deals,
  series,
  commonPeriods,
}: {
  deals: CompareDealRef[];
  series: PerformanceSeries[];
  commonPeriods: string[];
}) {
  const dealName = useMemo(
    () => new Map(deals.map((d) => [d.deal_id, d.deal_name])),
    [deals],
  );

  // Build per-metric chart data: one row per reporting date, one column per deal.
  const chartsByMetric = useMemo(() => {
    const allDates = Array.from(
      new Set(series.flatMap((s) => s.points.map((p) => p.reporting_date))),
    ).sort();
    return METRICS.map((m) => {
      const rows = allDates.map((date) => {
        const row: Record<string, number | string | null> = { period: date };
        for (const s of series) {
          const pt = s.points.find((p) => p.reporting_date === date);
          row[s.deal_id] = pt ? (pt[m.key] as number) : null;
        }
        return row;
      });
      return { metric: m, rows };
    });
  }, [series]);

  if (series.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Performance / risk</CardTitle>
        </CardHeader>
        <CardContent>
          <EmptyState message="No reconstructable performance series for the selected deals." />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Performance / risk (overlaid)</CardTitle>
      </CardHeader>
      <CardContent className="space-y-8">
        {commonPeriods.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Shared period axis where every series has data:{" "}
            {commonPeriods[0]} → {commonPeriods[commonPeriods.length - 1]} (
            {commonPeriods.length} periods).
          </p>
        )}
        <div className="grid gap-8 lg:grid-cols-2">
          {chartsByMetric.map(({ metric, rows }) => (
            <div key={String(metric.key)} className="space-y-2">
              <p className="text-sm font-medium">{metric.title}</p>
              <ResponsiveContainer width="100%" height={260}>
                <LineChart
                  data={rows}
                  margin={{ top: 8, right: 16, bottom: 8, left: 8 }}
                >
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis
                    dataKey="period"
                    fontSize={12}
                    minTickGap={24}
                    tickMargin={8}
                  />
                  <YAxis fontSize={12} unit={metric.unit} width={64} />
                  <Tooltip
                    formatter={(v) =>
                      typeof v === "number"
                        ? v.toLocaleString(undefined, {
                            maximumFractionDigits: metric.fixed ?? 0,
                          })
                        : "—"
                    }
                  />
                  <Legend />
                  {series.map((s, i) => (
                    <Line
                      key={s.deal_id}
                      type="monotone"
                      dataKey={s.deal_id}
                      name={dealName.get(s.deal_id) ?? s.deal_id}
                      stroke={LINE_COLORS[i % LINE_COLORS.length]}
                      connectNulls
                      dot={false}
                    />
                  ))}
                </LineChart>
              </ResponsiveContainer>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}
