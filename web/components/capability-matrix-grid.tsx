"use client";

import Link from "next/link";

import type {
  CapabilityCell,
  CapabilityCellState,
  CapabilityMatrix,
  DealColumn,
} from "@/lib/api";
import { useSelectedDeal } from "@/lib/deal-context";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/**
 * The cross-deal capability grid — the heart of the C4 showcase (#242, epic #236).
 *
 * Renders the `primitives × deals` capability matrix from `GET /capability-matrix`
 * as a clean light-theme grid: one row per primitive capability, one column per
 * deal, each cell colour-coded by its honest state (`validated` / `ran` /
 * `not-applicable`) with the cell's REAL reason surfaced in a tooltip.
 *
 * Honesty discipline (epic #193): the real states + reasons are rendered as-is.
 * `not-applicable` is shown as a feature (honest scope), never hidden, never
 * faked green. The single `validated` cell links to its proof (the Validation
 * view for that deal).
 */

/** The one capability that can reach `validated` — its cell links to the proof. */
const ENGINE_VALIDATION_KEY = "engine_validation";

/** Per-state visual treatment — light theme, honest. */
const STATE_STYLES: Record<
  CapabilityCellState,
  { cell: string; dot: string; label: string }
> = {
  validated: {
    cell: "bg-emerald-50 text-emerald-900 ring-1 ring-inset ring-emerald-200",
    dot: "bg-emerald-500",
    label: "Validated",
  },
  ran: {
    cell: "bg-sky-50 text-sky-900 ring-1 ring-inset ring-sky-200",
    dot: "bg-sky-500",
    label: "Ran",
  },
  "not-applicable": {
    cell: "bg-muted/60 text-muted-foreground ring-1 ring-inset ring-border",
    dot: "bg-muted-foreground/50",
    label: "N/A",
  },
};

/** Lightweight per-jurisdiction flag emoji + label for the deal column header. */
function jurisdictionFlag(jurisdiction: string): string {
  switch (jurisdiction.toLowerCase()) {
    case "netherlands":
      return "🇳🇱";
    case "italy":
      return "🇮🇹";
    case "spain":
      return "🇪🇸";
    default:
      return "🏳️";
  }
}

export function CapabilityMatrixGrid({ matrix }: { matrix: CapabilityMatrix }) {
  // Index cells by `${capability_key}::${deal_id}` for O(1) lookup per grid slot.
  const cellByKey = new Map<string, CapabilityCell>();
  for (const c of matrix.cells) {
    cellByKey.set(`${c.capability_key}::${c.deal_id}`, c);
  }

  return (
    <TooltipProvider>
      <div className="overflow-x-auto rounded-xl bg-card ring-1 ring-foreground/10">
        <table className="w-full border-separate border-spacing-0 text-sm">
          <thead>
            <tr>
              <th
                scope="col"
                className="sticky left-0 z-10 min-w-56 bg-card p-4 text-left align-bottom font-medium text-muted-foreground"
              >
                Primitive capability
              </th>
              {matrix.deals.map((deal) => (
                <DealHeaderCell key={deal.deal_id} deal={deal} />
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.capabilities.map((row) => (
              <tr key={row.key} className="group">
                <th
                  scope="row"
                  className="sticky left-0 z-10 border-t border-border bg-card p-4 text-left align-top font-normal"
                >
                  <div className="font-medium text-foreground">{row.label}</div>
                  <div className="mt-0.5 max-w-56 text-xs text-muted-foreground">
                    {row.description}
                  </div>
                </th>
                {matrix.deals.map((deal) => {
                  const cell = cellByKey.get(`${row.key}::${deal.deal_id}`);
                  return (
                    <td
                      key={deal.deal_id}
                      className="border-t border-border p-2 align-top"
                    >
                      {cell ? <MatrixCell cell={cell} /> : <span>—</span>}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </TooltipProvider>
  );
}

function DealHeaderCell({ deal }: { deal: DealColumn }) {
  return (
    <th
      scope="col"
      className="min-w-40 border-b border-border p-4 text-left align-bottom font-medium"
    >
      <div className="flex items-center gap-1.5 text-foreground">
        <span aria-hidden>{jurisdictionFlag(deal.jurisdiction)}</span>
        <span className="line-clamp-2">{deal.deal_name}</span>
      </div>
      <div className="mt-1 text-xs font-normal text-muted-foreground">
        {deal.jurisdiction}
        {deal.completeness_score != null ? (
          <span className="tabular-nums">
            {" · "}
            {Math.round(deal.completeness_score * 100)}% extracted
          </span>
        ) : null}
      </div>
    </th>
  );
}

function MatrixCell({ cell }: { cell: CapabilityCell }) {
  const { setDealId } = useSelectedDeal();
  const styles = STATE_STYLES[cell.state];
  const isProof =
    cell.state === "validated" && cell.capability_key === ENGINE_VALIDATION_KEY;

  const pillClass = cn(
    "flex w-full items-center gap-1.5 rounded-md px-2.5 py-2 text-left text-xs font-medium focus-visible:ring-2 focus-visible:ring-foreground/30 focus-visible:outline-none",
    styles.cell,
  );

  // The cell's interactive surface, passed to `TooltipTrigger` via base-ui's
  // `render` prop (same pattern as SidebarMenuButton's `render={<Link/>}`):
  // base-ui merges the trigger's hover/focus + a11y props onto this element, so
  // the tooltip works on every cell. The `validated` proof cell is a Link that
  // sets the global selected deal then routes to the Validation view; every
  // other cell is a plain, non-navigating pill.
  const trigger = isProof ? (
    <Link
      href="/validation"
      onClick={() => setDealId(cell.deal_id)}
      className={cn(pillClass, "font-semibold underline-offset-2 hover:underline")}
      aria-label={`${styles.label}: ${cell.reason}. Open the validation proof for ${cell.deal_id}.`}
    >
      <span className={cn("size-1.5 shrink-0 rounded-full", styles.dot)} aria-hidden />
      <span>{styles.label} →</span>
    </Link>
  ) : (
    <div
      className={pillClass}
      aria-label={`${cell.capability_key} for ${cell.deal_id}: ${styles.label}. ${cell.reason}`}
    >
      <span className={cn("size-1.5 shrink-0 rounded-full", styles.dot)} aria-hidden />
      <span>{styles.label}</span>
    </div>
  );

  return (
    <Tooltip>
      <TooltipTrigger render={trigger} />
      <TooltipContent side="top" className="max-w-xs text-left">
        <div className="space-y-1">
          <div className="font-medium">{styles.label}</div>
          <div>{cell.reason}</div>
          <div className="text-background/70">{cell.evidence.citation}</div>
          {isProof ? (
            <div className="text-background/70">Click to open the proof →</div>
          ) : null}
        </div>
      </TooltipContent>
    </Tooltip>
  );
}
