"use client";

import { useEffect, useMemo, useState } from "react";
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

import {
  ApiError,
  getCompliance,
  type ComplianceResult,
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
import { humanize } from "@/lib/format";

const LINE_COLORS = ["#2563eb", "#16a34a", "#d97706", "#9333ea", "#dc2626"];

export default function PoolPage() {
  const [data, setData] = useState<ComplianceResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCompliance()
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
        description="Per-period pool metrics derived from the covenant monitor across the reported ESMA tapes."
      />
      {error ? (
        <ErrorState title="Could not load pool analytics" message={error} />
      ) : !data ? (
        <LoadingState />
      ) : (
        <PoolContent data={data} />
      )}
    </div>
  );
}

function PoolContent({ data }: { data: ComplianceResult }) {
  // Pivot trigger_statuses into one row per period, one column per metric.
  const { periods, metrics, chartData } = useMemo(() => {
    const periodSet = new Set<string>();
    const metricSet = new Set<string>();
    const byPeriod = new Map<string, Record<string, number>>();

    for (const s of data.trigger_statuses) {
      periodSet.add(s.period);
      metricSet.add(s.trigger_name);
      const row = byPeriod.get(s.period) ?? {};
      row[s.trigger_name] = s.metric_value;
      byPeriod.set(s.period, row);
    }

    const periods = [...periodSet].sort();
    const metrics = [...metricSet];
    const chartData: Array<Record<string, string | number>> = periods.map(
      (p) => ({ period: p, ...byPeriod.get(p) }),
    );
    return { periods, metrics, chartData };
  }, [data]);

  if (periods.length === 0) {
    return <EmptyState message="No per-period pool analytics available." />;
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Metric trend across periods</CardTitle>
        </CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={320}>
            <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="period" fontSize={12} />
              <YAxis fontSize={12} />
              <Tooltip />
              <Legend />
              {metrics.map((m, i) => (
                <Line
                  key={m}
                  type="monotone"
                  dataKey={m}
                  name={humanize(m)}
                  stroke={LINE_COLORS[i % LINE_COLORS.length]}
                  dot={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>

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
                  <TableHead key={p} className="text-right">
                    {p}
                  </TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {metrics.map((m) => (
                <TableRow key={m}>
                  <TableCell className="font-medium">{humanize(m)}</TableCell>
                  {periods.map((p) => {
                    const row = chartData.find((r) => r.period === p);
                    const v = row?.[m];
                    return (
                      <TableCell key={p} className="text-right tabular-nums">
                        {typeof v === "number" ? v.toLocaleString() : "—"}
                      </TableCell>
                    );
                  })}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
