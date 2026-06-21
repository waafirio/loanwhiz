"use client";

import { useEffect, useMemo, useState } from "react";

import {
  ApiError,
  getCompare,
  type CompareResponse,
} from "@/lib/api";
import { useSelectedDeal } from "@/lib/deal-context";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
} from "@/components/page-states";
import { DealPicker } from "@/components/compare/deal-picker";
import { RiskSummaryRow } from "@/components/compare/risk-summary-row";
import { StructuralDiff } from "@/components/compare/structural-diff";
import { PerformancePanel } from "@/components/compare/performance-panel";
import { Card, CardContent } from "@/components/ui/card";

/**
 * Deal-comparison tool (#283, Epic 7 — analyst-facing tools).
 *
 * One unified N-way comparison view over the validated engine outputs:
 *
 *  - a multi-select picker (2..N deals; mark one as the benchmark target),
 *  - Panel 1 — structural diff (DealRules) aligned by canonical taxonomy,
 *  - Panel 2 — performance / risk (DealStateSeries) overlaid per deal, with a
 *    latest-period covenant-proximity risk-summary row,
 *  - a benchmark-lens toggle that shades each metric by its deviation from the
 *    comp-set median (the other selected deals).
 *
 * Follows the dashboard's page contract (web/CONTRACT.md): a Client Component
 * that calls one `lib/api.ts` wrapper inside `useEffect`, holds the result in
 * `useState`, and renders loading / error / empty states. Reads nothing at
 * build time.
 */
export default function ComparePage() {
  const { deals: registry } = useSelectedDeal();

  // `null` = the user hasn't touched the picker yet; we derive the default
  // selection (first two registry deals) during render rather than seeding it
  // with a setState-in-effect (react-hooks/set-state-in-effect).
  const [chosen, setChosen] = useState<string[] | null>(null);
  const [target, setTarget] = useState<string | null>(null);
  const [benchmark, setBenchmark] = useState(false);
  const [state, setState] = useState<{
    key: string;
    data: CompareResponse | null;
    error: string | null;
  }>({ key: "", data: null, error: null });

  const selected = useMemo(
    () => chosen ?? registry.slice(0, Math.min(2, registry.length)).map((d) => d.id),
    [chosen, registry],
  );

  // Selection ceiling for the comparison (#344): pick 2..MAX_SELECTED deals.
  // The picker enforces this in its UI; mirror it here so the cap holds even
  // if the picker is bypassed, and so stale state can't push >5 deals into a
  // /compare request.
  const MAX_SELECTED = 5;

  function toggleDeal(id: string) {
    setChosen((prev) => {
      const base = prev ?? selected;
      if (base.includes(id)) return base.filter((d) => d !== id);
      if (base.length >= MAX_SELECTED) return base; // at cap — no-op
      return [...base, id];
    });
  }

  // When the benchmark lens is on but no target is set, default the target to
  // the first selected deal so the lens has a reference.
  const effectiveTarget = useMemo(
    () => (benchmark ? (target ?? selected[0] ?? null) : null),
    [benchmark, target, selected],
  );

  // Stable request key so a re-render with the same inputs doesn't refetch.
  const requestKey = useMemo(
    () => `${[...selected].sort().join(",")}|${effectiveTarget ?? ""}`,
    [selected, effectiveTarget],
  );

  useEffect(() => {
    if (selected.length < 2) return;
    let cancelled = false;
    getCompare(selected, effectiveTarget ?? undefined)
      .then(
        (d) =>
          !cancelled &&
          setState({ key: requestKey, data: d, error: null }),
      )
      .catch(
        (e) =>
          !cancelled &&
          setState({
            key: requestKey,
            data: null,
            error: e instanceof ApiError ? e.message : "Failed to load comparison",
          }),
      );
    return () => {
      cancelled = true;
    };
  }, [requestKey, selected, effectiveTarget]);

  const current = state.key === requestKey ? state : null;
  const data = current?.data ?? null;
  const error = current?.error ?? null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Deal Comparison"
        description="Compare terms, triggers, and performance across deals — risk screening + structural diff over the validated engine."
      />

      <DealPicker
        registry={registry}
        selected={selected}
        target={target}
        benchmark={benchmark}
        onToggleDeal={toggleDeal}
        onSetTarget={setTarget}
        onToggleBenchmark={() => setBenchmark((b) => !b)}
      />

      {selected.length < 2 ? (
        <EmptyState message="Select at least two deals to compare." />
      ) : error ? (
        <ErrorState title="Could not load comparison" message={error} />
      ) : !data ? (
        <LoadingState />
      ) : (
        <ComparisonContent data={data} benchmark={benchmark} />
      )}
    </div>
  );
}

function ComparisonContent({
  data,
  benchmark,
}: {
  data: CompareResponse;
  benchmark: boolean;
}) {
  return (
    <div className="space-y-6">
      {data.notes.length > 0 && (
        <Card className="border-amber-300/60 bg-amber-50/60 dark:border-amber-900/40 dark:bg-amber-950/20">
          <CardContent className="space-y-1 py-4 text-sm text-amber-800 dark:text-amber-300">
            <p className="font-medium">Coverage notes</p>
            <ul className="list-inside list-disc">
              {data.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      <RiskSummaryRow
        deals={data.deals}
        risk={data.risk_summary}
        benchmark={benchmark}
      />

      <PerformancePanel
        deals={data.deals}
        series={data.performance_series}
        commonPeriods={data.common_periods}
      />

      <StructuralDiff
        deals={data.deals}
        rows={data.structural_rows}
        benchmark={benchmark}
      />

      {benchmark && data.comp_suggestions.length > 0 && (
        <Card>
          <CardContent className="py-4 text-sm text-muted-foreground">
            <span className="font-medium text-foreground">Suggested comps</span>{" "}
            (same jurisdiction / vintage, not yet selected):{" "}
            {data.comp_suggestions.join(", ")}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
