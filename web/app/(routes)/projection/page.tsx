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
import { formatCurrency, humanize } from "@/lib/format";

export default function ProjectionPage() {
  const [data, setData] = useState<ProjectionResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    postProjection({ scenarios: ["base", "stress"], months: 12 })
      .then(setData)
      .catch((e) =>
        setError(e instanceof ApiError ? e.message : "Failed to load projection"),
      );
  }, []);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Projection"
        description="Forward payment-waterfall projection — base vs stress over 12 months."
      />
      {error ? (
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

  const chartData = useMemo(
    () =>
      scenarios.map((s) => ({
        scenario: humanize(s),
        distributed: result.projections[s].total_distributed,
        shortfall: result.projections[s].shortfall,
      })),
    [scenarios, result],
  );

  if (scenarios.length === 0) {
    return <EmptyState message="No projection scenarios returned." />;
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            Distributed vs shortfall ({result.months}-month horizon)
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
              <Bar dataKey="distributed" name="Distributed" fill="#2563eb" />
              <Bar dataKey="shortfall" name="Shortfall" fill="#dc2626" />
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
                <TableHead>Period</TableHead>
                <TableHead className="text-right">Total distributed</TableHead>
                <TableHead className="text-right">Shortfall</TableHead>
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
                    <TableCell className="text-muted-foreground">
                      {p.reporting_period}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatCurrency(p.total_distributed)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatCurrency(p.shortfall)}
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
            Per-tranche received by scenario
          </CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Scenario</TableHead>
                <TableHead>Tranche</TableHead>
                <TableHead className="text-right">Interest</TableHead>
                <TableHead className="text-right">Principal</TableHead>
                <TableHead className="text-right">Total</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {scenarios.flatMap((s) =>
                result.projections[s].tranche_distributions.map((t) => (
                  <TableRow key={`${s}-${t.tranche}`}>
                    <TableCell className="font-medium">{humanize(s)}</TableCell>
                    <TableCell>{humanize(t.tranche)}</TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatCurrency(t.interest_received)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatCurrency(t.principal_received)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {formatCurrency(t.total_received)}
                    </TableCell>
                  </TableRow>
                )),
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
