"use client";

import { useEffect, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  ApiError,
  getWaterfall,
  type WaterfallResult,
} from "@/lib/api";
import { useSelectedDeal } from "@/lib/deal-context";
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

export default function WaterfallPage() {
  const { dealId } = useSelectedDeal();
  // Tag the result with its deal so a deal switch falls back to the loading
  // state without a synchronous setState in the effect (see Overview page).
  const [state, setState] = useState<{
    dealId: string;
    data: WaterfallResult | null;
    error: string | null;
  }>({ dealId, data: null, error: null });

  useEffect(() => {
    let cancelled = false;
    getWaterfall(dealId)
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
              e instanceof ApiError ? e.message : "Failed to load waterfall",
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
        title="Waterfall"
        description="Revenue priority cascade and per-tranche distributions for the latest reported period."
      />
      {error ? (
        <ErrorState title="Could not load waterfall" message={error} />
      ) : !data ? (
        <LoadingState />
      ) : (
        <WaterfallContent result={data} />
      )}
    </div>
  );
}

function WaterfallContent({ result }: { result: WaterfallResult }) {
  const cascade = result.revenue_waterfall ?? [];
  const chartData = cascade.map((step) => ({
    name: `${step.priority} ${step.recipient}`,
    distributed: step.amount_distributed,
  }));

  return (
    <div className="space-y-6">
      {/* Headline cards */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Reporting period
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-lg font-semibold">{result.reporting_period}</div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Available revenue
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-lg font-semibold tabular-nums">
              {formatCurrency(result.available_revenue_funds)}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Total distributed
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-lg font-semibold tabular-nums">
              {formatCurrency(result.total_distributed)}
            </div>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-muted-foreground">
              Shortfall
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="text-lg font-semibold tabular-nums">
              {formatCurrency(result.shortfall)}
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            Revenue cascade — distribution per step
          </CardTitle>
        </CardHeader>
        <CardContent>
          {chartData.length === 0 ? (
            <EmptyState message="No revenue waterfall steps returned." />
          ) : (
            <ResponsiveContainer width="100%" height={340}>
              <BarChart
                data={chartData}
                margin={{ top: 8, right: 16, bottom: 64, left: 8 }}
              >
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis
                  dataKey="name"
                  fontSize={11}
                  angle={-35}
                  textAnchor="end"
                  interval={0}
                  height={72}
                />
                <YAxis fontSize={12} width={80} />
                <Tooltip formatter={(v) => formatCurrency(Number(v))} />
                <Bar dataKey="distributed" fill="#2563eb" />
              </BarChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Revenue waterfall steps</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Priority</TableHead>
                <TableHead>Recipient</TableHead>
                <TableHead className="text-right">Available</TableHead>
                <TableHead className="text-right">Distributed</TableHead>
                <TableHead className="text-right">Shortfall</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {cascade.map((step, i) => (
                <TableRow key={`${step.priority}-${i}`}>
                  <TableCell className="font-medium">{step.priority}</TableCell>
                  <TableCell>{step.recipient}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCurrency(step.amount_available)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCurrency(step.amount_distributed)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCurrency(step.shortfall)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Tranche distributions</CardTitle>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Tranche</TableHead>
                <TableHead className="text-right">Interest</TableHead>
                <TableHead className="text-right">Principal</TableHead>
                <TableHead className="text-right">Total</TableHead>
                <TableHead className="text-right">Closing balance</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {result.tranche_distributions.map((t) => (
                <TableRow key={t.tranche}>
                  <TableCell className="font-medium">
                    {humanize(t.tranche)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCurrency(t.interest_received)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCurrency(t.principal_received)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCurrency(t.total_received)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatCurrency(t.closing_balance)}
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
