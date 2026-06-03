"use client";

import { useEffect, useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  ApiError,
  getCompliance,
  type ComplianceResult,
  type TriggerStatus,
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
import { formatPct, humanize } from "@/lib/format";

const LINE_COLORS = ["#2563eb", "#16a34a", "#d97706", "#9333ea", "#dc2626"];

/** Map a trigger's state to a red/amber/green badge. */
function statusBadge(s: TriggerStatus) {
  if (s.is_triggered) {
    return <Badge variant="destructive">Breached</Badge>;
  }
  if (s.proximity_pct >= 80) {
    return (
      <Badge className="bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
        Near miss
      </Badge>
    );
  }
  return (
    <Badge className="bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400">
      OK
    </Badge>
  );
}

export default function CompliancePage() {
  const [data, setData] = useState<ComplianceResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCompliance()
      .then(setData)
      .catch((e) =>
        setError(e instanceof ApiError ? e.message : "Failed to load compliance"),
      );
  }, []);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Compliance"
        description="Covenant monitor output — per-trigger status and proximity to threshold."
      />
      {error ? (
        <ErrorState title="Could not load compliance" message={error} />
      ) : !data ? (
        <LoadingState />
      ) : (
        <ComplianceContent data={data} />
      )}
    </div>
  );
}

function ComplianceContent({ data }: { data: ComplianceResult }) {
  const { latest, triggers, chartData } = useMemo(() => {
    const periods = [...new Set(data.trigger_statuses.map((s) => s.period))].sort();
    const triggers = [...new Set(data.trigger_statuses.map((s) => s.trigger_name))];
    const latestPeriod = periods[periods.length - 1];

    // Latest-period status, one row per trigger.
    const latest = triggers
      .map((name) =>
        data.trigger_statuses.find(
          (s) => s.trigger_name === name && s.period === latestPeriod,
        ),
      )
      .filter((s): s is TriggerStatus => s != null);

    // Proximity-vs-threshold series, one point per period.
    const byPeriod = new Map<string, Record<string, number>>();
    for (const s of data.trigger_statuses) {
      const row = byPeriod.get(s.period) ?? {};
      row[s.trigger_name] = s.proximity_pct;
      byPeriod.set(s.period, row);
    }
    const chartData = periods.map((p) => ({ period: p, ...byPeriod.get(p) }));

    return { latest, triggers, chartData };
  }, [data]);

  if (data.trigger_statuses.length === 0) {
    return <EmptyState message="No covenant trigger data available." />;
  }

  return (
    <div className="space-y-6">
      {/* Summary + headline counts */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Summary</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p className="text-muted-foreground">{data.summary}</p>
          <div className="flex flex-wrap gap-2">
            <Badge variant="destructive">
              {data.active_triggers.length} active
            </Badge>
            <Badge className="bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
              {data.near_miss_triggers.length} near miss
            </Badge>
          </div>
        </CardContent>
      </Card>

      {/* Latest-period status grid */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Trigger status (latest period)</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Trigger</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Metric</TableHead>
                <TableHead className="text-right">Threshold</TableHead>
                <TableHead className="text-right">Proximity</TableHead>
                <TableHead>Direction</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {latest.map((s) => (
                <TableRow key={s.trigger_name}>
                  <TableCell className="font-medium">
                    {humanize(s.trigger_name)}
                  </TableCell>
                  <TableCell>{statusBadge(s)}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {s.metric_value.toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {s.threshold != null ? s.threshold.toLocaleString() : "—"}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatPct(s.proximity_pct)}
                  </TableCell>
                  <TableCell className="capitalize text-muted-foreground">
                    {s.direction}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Proximity-vs-threshold trend */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            Proximity to threshold across periods
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ResponsiveContainer width="100%" height={320}>
            <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" />
              <XAxis dataKey="period" fontSize={12} />
              <YAxis fontSize={12} unit="%" />
              <Tooltip formatter={(v) => formatPct(Number(v))} />
              <Legend />
              <ReferenceLine
                y={100}
                stroke="#dc2626"
                strokeDasharray="4 4"
                label={{ value: "threshold", fontSize: 11, fill: "#dc2626" }}
              />
              {triggers.map((t, i) => (
                <Line
                  key={t}
                  type="monotone"
                  dataKey={t}
                  name={humanize(t)}
                  stroke={LINE_COLORS[i % LINE_COLORS.length]}
                  dot={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>
    </div>
  );
}
