"use client";

import type { CompareDealRef, RiskSummary } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { formatPct, humanize } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * The at-a-glance triage row above Panel 2 (#283): one card per deal showing
 * the latest-period covenant proximity-to-breach, active/near-miss counts, and
 * the pool factor + loss rate. With the benchmark lens on, the target's
 * proximity is shaded by its deviation from the comp-set median.
 */
export function RiskSummaryRow({
  deals,
  risk,
  benchmark,
}: {
  deals: CompareDealRef[];
  risk: RiskSummary[];
  benchmark: boolean;
}) {
  const byId = new Map(risk.map((r) => [r.deal_id, r]));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Risk summary (latest period)</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {deals.map((d) => {
            const r = byId.get(d.deal_id);
            return (
              <div
                key={d.deal_id}
                className={cn(
                  "rounded-lg border p-3",
                  d.is_target && benchmark && "border-primary ring-1 ring-primary/30",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="truncate text-sm font-medium" title={d.deal_name}>
                    {d.deal_name}
                  </p>
                  {d.is_target && benchmark && (
                    <Badge className="bg-primary/10 text-primary">target</Badge>
                  )}
                </div>

                {!d.has_performance || !r || r.latest_period == null ? (
                  <p className="mt-2 text-xs text-muted-foreground">
                    No performance series — risk unavailable.
                  </p>
                ) : (
                  <dl className="mt-2 space-y-1 text-sm">
                    <Stat
                      label="Tightest covenant"
                      value={
                        r.tightest_trigger
                          ? `${humanize(r.tightest_trigger)} · ${formatPct(
                              r.tightest_proximity_pct ?? 0,
                            )}`
                          : "—"
                      }
                      emphasise={
                        r.tightest_proximity_pct != null &&
                        r.tightest_proximity_pct >= 80
                      }
                    />
                    {benchmark &&
                      r.proximity_deviation != null &&
                      r.comp_median_proximity_pct != null && (
                        <Stat
                          label="Δ vs comp median"
                          value={`${r.proximity_deviation >= 0 ? "+" : ""}${formatPct(
                            r.proximity_deviation,
                          )}`}
                          emphasise={r.proximity_deviation > 0}
                        />
                      )}
                    <Stat
                      label="Pool factor"
                      value={
                        r.latest_pool_factor != null
                          ? r.latest_pool_factor.toFixed(3)
                          : "—"
                      }
                    />
                    <Stat
                      label="Cum. loss rate"
                      value={
                        r.latest_cumulative_loss_rate_pct != null
                          ? formatPct(r.latest_cumulative_loss_rate_pct, 2)
                          : "—"
                      }
                    />
                    <div className="flex gap-2 pt-1">
                      {r.active_triggers.length > 0 && (
                        <Badge variant="destructive">
                          {r.active_triggers.length} active
                        </Badge>
                      )}
                      {r.near_miss_triggers.length > 0 && (
                        <Badge className="bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400">
                          {r.near_miss_triggers.length} near miss
                        </Badge>
                      )}
                    </div>
                  </dl>
                )}
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

function Stat({
  label,
  value,
  emphasise = false,
}: {
  label: string;
  value: string;
  emphasise?: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd
        className={cn(
          "tabular-nums",
          emphasise && "font-medium text-amber-700 dark:text-amber-400",
        )}
      >
        {value}
      </dd>
    </div>
  );
}
