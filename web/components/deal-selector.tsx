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
 * the chosen deal.
 *
 * Renders for 1..N deals (issue #198):
 *  - While the `/deals` fetch is in flight: a skeleton.
 *  - With a single deal (the common case today, and the no-backend fallback):
 *    a clean, non-interactive label — no dropdown chevron, nothing to click —
 *    so the bar reads "this is the deal" rather than presenting a dead
 *    one-option dropdown.
 *  - With two or more deals (forward-compatible with the seasoned deals
 *    landing later): the working dropdown that switches the selected deal.
 */
export function DealSelector() {
  const { dealId, setDealId, deals, loading } = useSelectedDeal();

  if (loading) {
    return <Skeleton className="h-8 w-40" />;
  }

  // Always have at least the current id so the selector renders even with no
  // backend (the provider keeps the default deal id in that case).
  const options =
    deals.length > 0 ? deals : [{ id: dealId, name: DEAL_LABEL }];

  // Single deal: present it as a static label, not a dropdown. A one-option
  // dropdown is a dead control — opening it reveals nothing. The label is
  // styled to sit cleanly in the top bar alongside the icon.
  if (options.length <= 1) {
    const only = options[0];
    const label = only?.name ?? DEAL_LABEL;
    return (
      <div
        className="flex h-7 w-fit items-center gap-1.5 rounded-[min(var(--radius-md),10px)] px-2.5 text-sm whitespace-nowrap text-foreground"
        aria-label={`Active deal: ${label}`}
      >
        <Building2 className="size-3.5 text-muted-foreground" />
        <span className="line-clamp-1">{label}</span>
      </div>
    );
  }

  // Two or more deals: the working dropdown.
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
