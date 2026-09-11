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
  SeriesBasisRow,
  hasQualifiedBasis,
  seriesLegendName,
} from "@/components/compare/series-basis";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/page-states";

const LINE_COLORS = ["#2563eb", "#16a34a", "#d97706", "#9333ea", "#dc2626"];

// ---------------------------------------------------------------------------
// Y-axis bounds that keep a flat series off the chart frame (#629)
// ---------------------------------------------------------------------------

/**
 * Fraction of the plotted extent added above the data as headroom, and the
 * pixel inset held at both ends of the axis range.
 *
 * recharts' default y-domain is `[0, 'auto']`, which ends the axis *exactly*
 * at the data maximum. A near-constant series is then drawn on the boundary
 * and is indistinguishable from the frame: measured on `/compare`, a pool
 * factor of 0.986 drew at y=8 on a plot spanning y=8-202 (the top frame), and
 * a 0.00% cumulative loss rate drew at y=202 (the bottom axis). Both charts
 * read as empty while they were plotting real data.
 *
 * Two ends, two mechanisms, because they fail for different reasons:
 *
 * - `HEADROOM` widens the *top* of the domain, which is what pins a non-zero
 *   constant to the top frame. The zero anchor is kept, so the height of a
 *   line still means what it meant before and two deals stay comparable.
 * - `RANGE_INSET` insets the axis *range* in pixels, which is the only thing
 *   that lifts an all-zero series off the bottom. Padding the domain cannot:
 *   the floor is a true zero we will not draw below, so the value sits on it.
 *
 * Neither invents variation. A flat series is still drawn as a flat line and
 * still reads flat against its axis labels - it just no longer hides inside
 * the frame.
 */
const HEADROOM = 0.12;
const RANGE_INSET = 10;

/**
 * The padded `[min, max]` y-domain for one metric's rows.
 *
 * `rows` is the chart's row-per-date shape, `dealIds` the plotted columns.
 * A missing point is `null` and is skipped - a gap is not a zero.
 */
export function paddedDomain(
  rows: Record<string, number | string | null>[],
  dealIds: string[],
): [number, number] {
  let lo = 0;
  let hi = 0;
  let seen = false;
  for (const row of rows) {
    for (const id of dealIds) {
      const v = row[id];
      if (typeof v !== "number" || !Number.isFinite(v)) continue;
      if (!seen) {
        // Anchor at zero unless the data itself goes below it.
        lo = Math.min(0, v);
        hi = v;
        seen = true;
      } else {
        lo = Math.min(lo, v);
        hi = Math.max(hi, v);
      }
    }
  }
  // Nothing plottable: a trivial axis, not a fabricated one.
  if (!seen) return [0, 1];
  const extent = hi - lo;
  // An all-equal series has no extent to take a fraction of. Give the axis one
  // unit of range so the line has somewhere to sit that is not the frame.
  const pad =
    extent > 0 ? extent * HEADROOM : Math.max(Math.abs(hi) * HEADROOM, 1);
  return [lo < 0 ? lo - pad : 0, hi + pad];
}

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
  // Every legend name is marked from the total tables in `series-basis`, never
  // from a condition here: a literal `=== "projected"` names one member and
  // silently mislabels the rest, which is how this panel came to call a
  // synthetic-rate series "reported" by omission (#614).
  const dealName = useMemo(
    () => new Map(deals.map((d) => [d.deal_id, seriesLegendName(d)])),
    [deals],
  );

  // Deals whose series is anything other than reported-on-a-stated-rate, so the
  // panel can state each one's basis above the overlay (#345 for the projected
  // half, #614 for the rate half).
  const qualified = useMemo(() => deals.filter(hasQualifiedBasis), [deals]);

  // Build per-metric chart data: one row per reporting date, one column per deal.
  const dealIds = useMemo(() => series.map((s) => s.deal_id), [series]);
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
      return { metric: m, rows, domain: paddedDomain(rows, dealIds) };
    });
  }, [series, dealIds]);

  if (series.length === 0) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Performance / risk</CardTitle>
        </CardHeader>
        <CardContent>
          <EmptyState message="No reported or projected performance series for the selected deals." />
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
        {qualified.length > 0 && (
          <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900">
            <p className="mb-2 font-medium">
              Not every series below is reported performance. What each one rests
              on:
            </p>
            {/* One row per deal, rendered from inside the loop that marks them
                (#575): a marking written once beside a list survives only until
                someone adds an item. */}
            <ul className="flex flex-col gap-2">
              {qualified.map((d) => (
                <SeriesBasisRow key={d.deal_id} deal={d} />
              ))}
            </ul>
          </div>
        )}
        {commonPeriods.length > 0 && (
          <p className="text-xs text-muted-foreground">
            Shared period axis where every series has data:{" "}
            {commonPeriods[0]} → {commonPeriods[commonPeriods.length - 1]} (
            {commonPeriods.length} periods).
          </p>
        )}
        <div className="grid gap-8 lg:grid-cols-2">
          {chartsByMetric.map(({ metric, rows, domain }) => (
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
                  <YAxis
                    fontSize={12}
                    unit={metric.unit}
                    width={64}
                    domain={domain}
                    padding={{ top: RANGE_INSET, bottom: RANGE_INSET }}
                  />
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
