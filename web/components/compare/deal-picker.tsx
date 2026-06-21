"use client";

import type { DealSummary } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/**
 * Multi-select deal picker for the comparison view (2..N deals). Each registry
 * deal is a toggle chip; selected deals can be marked as the benchmark target
 * (one at a time), and the benchmark lens is toggled here. No external
 * multi-select dependency — plain buttons over the `GET /deals` registry.
 */
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
  return (
    <Card>
      <CardContent className="space-y-4 py-4">
        <div className="space-y-2">
          <p className="text-sm font-medium">Deals to compare</p>
          <div className="flex flex-wrap gap-2">
            {registry.length === 0 ? (
              <span className="text-sm text-muted-foreground">
                Loading deals…
              </span>
            ) : (
              registry.map((d) => {
                const isSelected = selected.includes(d.id);
                return (
                  <button
                    key={d.id}
                    type="button"
                    onClick={() => onToggleDeal(d.id)}
                    aria-pressed={isSelected}
                    className={cn(
                      "rounded-full border px-3 py-1 text-sm transition-colors",
                      isSelected
                        ? "border-primary bg-primary text-primary-foreground"
                        : "border-input bg-background text-muted-foreground hover:bg-accent",
                    )}
                  >
                    {d.name}
                  </button>
                );
              })
            )}
          </div>
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
                const deal = registry.find((d) => d.id === id);
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
