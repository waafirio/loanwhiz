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
import {
  ProvenanceBadge,
  ProvenanceBadges,
  distinctDataSources,
} from "@/components/provenance-badge";
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
import { cn } from "@/lib/utils";
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
      {/*
        The backend serves tape-analytics from a committed seed (offline-capable
        for the flagship deal) and degrades a per-tape failure to a partial 200
        rather than a 500 (#347), so a seeded-offline deal lands on the data
        branches below, not ErrorState. ErrorState now only fires on a genuine
        API error (e.g. the deal is unknown / the API is down); an empty 200
        ([] periods) renders the EmptyState inside PoolContent.
      */}
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

/** The distributions this page renders, in display order. */
type BreakdownKey =
  | "arrears"
  | "rate_type"
  | "epc"
  | "property_type"
  | "geographic";

/**
 * What a section shows. Three states, all of them visible.
 *
 * A distribution can be missing for two different reasons, and collapsing them
 * is what made this page uninformative: a corporate loan pool HAS no energy
 * rating, while it may well have a geography its tape simply does not carry.
 * The first is a refusal with a reason; the second is a gap. Rendering neither
 * — the prior behaviour for geography — teaches a reader nothing at all.
 */
type BreakdownState =
  | { kind: "present"; rows: Array<{ name: string; pct: number }> }
  | { kind: "not-applicable"; reason: string }
  | { kind: "absent"; reason: string };

/**
 * Which distributions the asset class does not have, and why.
 *
 * Keyed on the tape's own `asset_class` — the annex the normaliser detected,
 * not a guess read off an empty map. That distinction is the whole point: an
 * empty breakdown never proves inapplicability on its own, so a reason claimed
 * from emptiness would be false for the first tape that merely omits a column
 * it does have. A not-applicable reason asserts something about the world, so
 * it may say only what the input actually encodes.
 *
 * Deliberately narrow. Only the two genuinely mortgage-shaped concepts are
 * here: arrears and geography apply perfectly well to a corporate loan pool,
 * so when they are empty they read as absent, never as inapplicable.
 */
const NOT_APPLICABLE: Record<
  string,
  Partial<Record<BreakdownKey, (annex: string) => string>>
> = {
  Corporate: {
    epc: (annex) =>
      `Not applicable — this is a corporate loan pool. An EPC rating describes ` +
      `the energy performance of a mortgaged property, and ${annex} reports no ` +
      `such field for a corporate obligor.`,
    property_type: (annex) =>
      `Not applicable — this is a corporate loan pool. Property type describes ` +
      `mortgage collateral, and ${annex} reports no property field for a ` +
      `corporate obligor.`,
  },
};

/**
 * Resolve one distribution to the state the card renders.
 *
 * The absent wording is a statement about the tape LoanWhiz normalised, never
 * about what the issuer filed — a report can publish a figure in a section this
 * platform does not yet parse, and claiming the issuer reported none would then
 * be a fabricated refusal.
 */
function breakdownState(
  key: BreakdownKey,
  breakdown: Record<string, number> | null,
  assetClass: string,
  annex: string,
): BreakdownState {
  const rows = breakdownRows(breakdown);
  if (rows.length > 0) return { kind: "present", rows };
  const reason = NOT_APPLICABLE[assetClass]?.[key];
  if (reason) return { kind: "not-applicable", reason: reason(annex) };
  return {
    kind: "absent",
    reason:
      `Not in the normalised tape for this period (${annex}). That is a ` +
      `statement about the columns LoanWhiz parsed — it does not claim the ` +
      `issuer publishes no such figure in another form.`,
  };
}

/**
 * Every distribution, resolved from one list.
 *
 * One loop, so a sixth distribution cannot be added without giving it a state —
 * the failure this replaces was two sections hand-written with an empty message
 * and two more that the page fetched and never rendered at all.
 */
const BREAKDOWNS: Array<{
  key: BreakdownKey;
  title: string;
  pick: (p: TapeAnalyticsPeriod) => Record<string, number> | null;
}> = [
  {
    key: "arrears",
    title: "Arrears breakdown",
    pick: (p) => p.arrears_breakdown,
  },
  {
    key: "rate_type",
    title: "Rate type",
    pick: (p) => p.rate_type_breakdown,
  },
  { key: "epc", title: "EPC distribution", pick: (p) => p.epc_breakdown },
  {
    key: "property_type",
    title: "Property type",
    pick: (p) => p.property_type_breakdown,
  },
  {
    key: "geographic",
    title: "Geographic distribution",
    pick: (p) => p.geographic_breakdown,
  },
];

function PoolContent({ periods }: { periods: TapeAnalyticsPeriod[] }) {
  // Provenance for the whole view, derived from the periods already in hand —
  // no second fetch. It renders above the charts because #484's failure was
  // that a reader who never reaches the table below sees generated collateral
  // presented exactly like real collateral.
  const dataSources = useMemo(
    () => distinctDataSources(periods.map((p) => p.data_source)),
    [periods],
  );
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


  return (
    <div className="space-y-6">
      {/* Ingestion provenance for the tapes every figure below is computed
          from. It is a badge row rather than a footnote because a synthetic
          pool describes no real obligor, and nothing further down this page
          says so. */}
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-xs text-muted-foreground">Ingested via</span>
        <ProvenanceBadges sources={dataSources} />
      </div>

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
                  <TableCell className="font-medium">
                    <span className="flex flex-wrap items-center gap-1.5">
                      {periodLabel(p)}
                      {/* Rendered from inside the loop, off this period's own
                          `data_source` — a caveat written once beside the
                          table survives only until someone adds a period. */}
                      <ProvenanceBadge source={p.data_source ?? null} />
                    </span>
                  </TableCell>
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

      {/* Distribution breakdowns (latest period). Every one renders, whatever
          its state — a section that removes itself when empty is a refusal
          that hides itself, and the reason is the part worth reading. */}
      <div className="grid gap-6 lg:grid-cols-2">
        {BREAKDOWNS.map((b) => (
          <BreakdownCard
            key={b.key}
            title={`${b.title} (${periodLabel(latest)})`}
            state={breakdownState(
              b.key,
              b.pick(latest),
              latest.asset_class,
              latest.annex_detected,
            )}
          />
        ))}
      </div>
    </div>
  );
}

/**
 * The stated-absence panel, in the capability matrix's not-applicable idiom.
 *
 * It borrows that surface's muted pill rather than importing it: the styles are
 * module-private to `capability-matrix-grid.tsx` and lifting them into a shared
 * component would pull `/showcase` into this diff. Same weight as a populated
 * section by construction — the minimum height matches the shortest chart, so a
 * refusal cannot read as a smaller thing than an answer.
 */
function BreakdownNotice({
  state,
}: {
  state: Extract<BreakdownState, { kind: "not-applicable" | "absent" }>;
}) {
  const notApplicable = state.kind === "not-applicable";
  return (
    <div className="flex min-h-[220px] flex-col items-start justify-center gap-2.5">
      <span
        className={cn(
          "inline-flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium",
          "bg-muted/60 text-muted-foreground ring-1 ring-inset ring-border",
        )}
      >
        <span
          className={cn(
            "h-1.5 w-1.5 rounded-full",
            notApplicable ? "bg-muted-foreground/50" : "bg-amber-500",
          )}
        />
        {notApplicable ? "Not applicable" : "Not in this tape"}
      </span>
      <p className="max-w-prose text-xs leading-relaxed text-muted-foreground">
        {state.reason}
      </p>
    </div>
  );
}

function BreakdownCard({
  title,
  state,
}: {
  title: string;
  state: BreakdownState;
}) {
  const rows = state.kind === "present" ? state.rows : [];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        {state.kind !== "present" ? (
          <BreakdownNotice state={state} />
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
