"use client";

import { Building2 } from "lucide-react";

import { DEAL_LABEL } from "@/lib/nav";
import { useSelectedDeal } from "@/lib/deal-context";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";

/**
 * Top-bar deal selector. Reads the registry-backed deal list and the selected
 * deal id from `DealProvider`; changing it re-points every page's API calls at
 * the chosen deal. While the `/deals` fetch is in flight it shows a skeleton;
 * if the fetch failed (no backend) it falls back to the single default deal so
 * the bar still reads cleanly.
 */
export function DealSelector() {
  const { dealId, setDealId, deals, loading } = useSelectedDeal();

  if (loading) {
    return <Skeleton className="h-8 w-40" />;
  }

  // Always show at least the current id so the selector renders even with no
  // backend (the provider keeps the default deal id in that case).
  const options =
    deals.length > 0 ? deals : [{ id: dealId, name: DEAL_LABEL }];

  return (
    <Select
      value={dealId}
      onValueChange={(value) => {
        if (value) setDealId(value);
      }}
    >
      <SelectTrigger size="sm" aria-label="Select deal" className="gap-1.5">
        <Building2 className="size-3.5 text-muted-foreground" />
        <SelectValue placeholder="Select deal" />
      </SelectTrigger>
      <SelectContent>
        {options.map((d) => (
          <SelectItem key={d.id} value={d.id}>
            {d.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
