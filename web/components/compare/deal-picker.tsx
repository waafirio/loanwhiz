"use client";

import { useId, useMemo, useState } from "react";

import type { DealSummary } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/**
 * Searchable typeahead multi-select deal picker for the comparison view (#344).
 *
 * Replaces the old flat chip-row (one toggle button per registry deal), which
 * was unusable once the EDW universe grows to 200+ RMBS. Instead:
 *  - a search box filters the registry by name / jurisdiction / vintage;
 *  - matches render as a bounded, scrollable result list (never all 200+ rows);
 *  - selection is capped at MAX_SELECTED — a deal cannot be added past the cap;
 *  - selected deals show as removable chips;
 *  - the benchmark lens + per-target sub-picker are unchanged.
 *
 * No external multi-select dependency — plain React + the `Input` primitive
 * over the `GET /deals` registry, matching the rest of `web/components/ui`.
 */

// Selection ceiling (#344: "select 3–5", "cap selection at 5"). The comparison
// floor (≥2 deals) is enforced by the page; this is the upper bound.
const MAX_SELECTED = 5;
// Cap the rendered result rows so a 200+ deal universe never paints a giant
// DOM; the user narrows via search instead.
const MAX_RESULTS = 50;

export function DealPicker({
  registry,
  selected,
  target,
  benchmark,
  onToggleDeal,
  onSetTarget,
  onToggleBenchmark,
}: {
  registry: DealSummary[];
  selected: string[];
  target: string | null;
  benchmark: boolean;
  onToggleDeal: (id: string) => void;
  onSetTarget: (id: string | null) => void;
  onToggleBenchmark: () => void;
}) {
  const [query, setQuery] = useState("");
  const searchId = useId();

  const byId = useMemo(
    () => new Map(registry.map((d) => [d.id, d] as const)),
    [registry],
  );

  const atCap = selected.length >= MAX_SELECTED;

  // Filter by name / jurisdiction / vintage (case-insensitive), then bound the
  // result count. Already-selected deals stay visible in the list (so the user
  // can deselect from the results too), but the bound is computed on matches.
  const { results, truncated } = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matches = registry.filter((d) => {
      if (q === "") return true;
      const vintage = d.vintage != null ? String(d.vintage) : "";
      return (
        d.name.toLowerCase().includes(q) ||
        d.jurisdiction.toLowerCase().includes(q) ||
        vintage.includes(q)
      );
    });
    return {
      results: matches.slice(0, MAX_RESULTS),
      truncated: matches.length > MAX_RESULTS,
    };
  }, [registry, query]);

  return (
    <Card>
      <CardContent className="space-y-4 py-4">
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <label htmlFor={searchId} className="text-sm font-medium">
              Deals to compare
            </label>
            <span className="text-xs text-muted-foreground">
              {selected.length}/{MAX_SELECTED} selected
            </span>
          </div>

          {/* Selected deals as removable chips. */}
          {selected.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {selected.map((id) => {
                const deal = byId.get(id);
                return (
                  <span
                    key={id}
                    className="inline-flex items-center gap-1 rounded-full border border-primary bg-primary px-3 py-1 text-sm text-primary-foreground"
                  >
                    {deal?.name ?? id}
                    <button
                      type="button"
                      onClick={() => onToggleDeal(id)}
                      aria-label={`Remove ${deal?.name ?? id}`}
                      className="-mr-1 ml-0.5 rounded-full px-1 leading-none text-primary-foreground/80 transition-colors hover:bg-primary-foreground/20 hover:text-primary-foreground"
                    >
                      ×
                    </button>
                  </span>
                );
              })}
            </div>
          )}

          <Input
            id={searchId}
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search deals by name, jurisdiction, or vintage…"
            aria-describedby={atCap ? `${searchId}-cap` : undefined}
          />

          {atCap && (
            <p id={`${searchId}-cap`} className="text-xs text-muted-foreground">
              Maximum of {MAX_SELECTED} deals selected — remove one to add
              another.
            </p>
          )}

          {/* Bounded, scrollable result list. */}
          {registry.length === 0 ? (
            <p className="text-sm text-muted-foreground">Loading deals…</p>
          ) : results.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No deals match “{query}”.
            </p>
          ) : (
            <ul className="max-h-64 divide-y overflow-y-auto rounded-md border">
              {results.map((d) => {
                const isSelected = selected.includes(d.id);
                const disabled = atCap && !isSelected;
                const facets = [
                  d.jurisdiction !== "Unknown" ? d.jurisdiction : null,
                  d.vintage != null ? String(d.vintage) : null,
                ]
                  .filter(Boolean)
                  .join(" · ");
                return (
                  <li key={d.id}>
                    <button
                      type="button"
                      onClick={() => onToggleDeal(d.id)}
                      aria-pressed={isSelected}
                      disabled={disabled}
                      title={
                        disabled
                          ? `Maximum of ${MAX_SELECTED} deals selected`
                          : undefined
                      }
                      className={cn(
                        "flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm transition-colors",
                        isSelected
                          ? "bg-primary/10 text-foreground"
                          : "text-foreground hover:bg-accent",
                        disabled &&
                          "cursor-not-allowed opacity-50 hover:bg-transparent",
                      )}
                    >
                      <span className="min-w-0">
                        <span className="block truncate">{d.name}</span>
                        {facets && (
                          <span className="block truncate text-xs text-muted-foreground">
                            {facets}
                          </span>
                        )}
                      </span>
                      <span
                        className={cn(
                          "shrink-0 text-xs",
                          isSelected ? "text-primary" : "text-muted-foreground",
                        )}
                      >
                        {isSelected ? "Selected ✓" : "Add"}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}

          {truncated && (
            <p className="text-xs text-muted-foreground">
              Showing the first {MAX_RESULTS} matches — refine your search to
              narrow the list.
            </p>
          )}

          {selected.length < 2 && (
            <p className="text-xs text-muted-foreground">
              Pick at least two deals.
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-3 border-t pt-4">
          <Button
            type="button"
            variant={benchmark ? "default" : "outline"}
            size="sm"
            onClick={onToggleBenchmark}
            aria-pressed={benchmark}
          >
            Benchmark lens {benchmark ? "on" : "off"}
          </Button>

          {benchmark && (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-muted-foreground">Target:</span>
              {selected.map((id) => {
                const deal = byId.get(id);
                const isTarget = (target ?? selected[0]) === id;
                return (
                  <button
                    key={id}
                    type="button"
                    onClick={() => onSetTarget(id)}
                    aria-pressed={isTarget}
                    className={cn(
                      "rounded-md border px-2 py-0.5 text-xs transition-colors",
                      isTarget
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-input text-muted-foreground hover:bg-accent",
                    )}
                  >
                    {deal?.name ?? id}
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
