"use client";

import { Fragment, useMemo } from "react";

import type { CompareDealRef, StructuralCell, StructuralRow } from "@/lib/api";
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
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const SECTION_LABELS: Record<string, string> = {
  tranche: "Tranche stack",
  "waterfall:revenue": "Revenue waterfall",
  "waterfall:redemption": "Redemption waterfall",
  trigger: "Triggers / covenants",
  reserve: "Reserve mechanics",
};

const SECTION_ORDER = [
  "tranche",
  "waterfall:revenue",
  "waterfall:redemption",
  "trigger",
  "reserve",
];

/**
 * Panel 1 — structural diff (#283): a column-per-deal table whose rows align by
 * the canonical RecipientType / MetricType taxonomy, so a waterfall step or
 * covenant lines up across deals even when each issuer labels it differently.
 * Rows where the deals differ are diff-highlighted; an `unmapped` step/trigger
 * is shown honestly as "not comparable". With the benchmark lens on, the
 * target's numeric cells carry their deviation from the comp-set median.
 */
export function StructuralDiff({
  deals,
  rows,
  benchmark,
}: {
  deals: CompareDealRef[];
  rows: StructuralRow[];
  benchmark: boolean;
}) {
  const bySection = useMemo(() => {
    const map = new Map<string, StructuralRow[]>();
    for (const row of rows) {
      const list = map.get(row.section) ?? [];
      list.push(row);
      map.set(row.section, list);
    }
    return map;
  }, [rows]);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Structural diff</CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-56">Row</TableHead>
              {deals.map((d) => (
                <TableHead key={d.deal_id}>
                  <span className="flex items-center gap-1">
                    <span className="truncate" title={d.deal_name}>
                      {d.deal_name}
                    </span>
                    {d.is_target && benchmark && (
                      <Badge className="bg-primary/10 text-primary">target</Badge>
                    )}
                  </span>
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {SECTION_ORDER.filter((s) => bySection.has(s)).map((section) => (
              <Fragment key={section}>
                <TableRow className="bg-muted/40 hover:bg-muted/40">
                  <TableCell
                    colSpan={deals.length + 1}
                    className="text-xs font-semibold uppercase tracking-wide text-muted-foreground"
                  >
                    {SECTION_LABELS[section] ?? section}
                  </TableCell>
                </TableRow>
                {(bySection.get(section) ?? []).map((row) => (
                  <TableRow
                    key={row.key}
                    className={cn(row.differs && "bg-amber-50/50 dark:bg-amber-950/20")}
                  >
                    <TableCell className="font-medium">
                      {row.label}
                      {row.differs && (
                        <span
                          className="ml-1 text-amber-600 dark:text-amber-400"
                          title="Deals differ on this row"
                        >
                          ●
                        </span>
                      )}
                    </TableCell>
                    {row.cells.map((cell) => (
                      <TableCell key={cell.deal_id}>
                        <Cell cell={cell} benchmark={benchmark} />
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </Fragment>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function Cell({
  cell,
  benchmark,
}: {
  cell: StructuralCell;
  benchmark: boolean;
}) {
  if (!cell.present) {
    return <span className="text-muted-foreground">—</span>;
  }
  if (!cell.comparable) {
    return (
      <span className="text-xs text-muted-foreground">
        {cell.label ? `${cell.label} · ` : ""}not comparable
      </span>
    );
  }
  return (
    <div className="space-y-0.5">
      {cell.detail && <div className="text-sm">{cell.detail}</div>}
      {cell.label && (
        <div className="text-xs text-muted-foreground">{cell.label}</div>
      )}
      {benchmark && cell.deviation != null && (
        <div
          className={cn(
            "text-xs font-medium",
            cell.deviation >= 0
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-rose-600 dark:text-rose-400",
          )}
          title="Deviation from comp-set median"
        >
          {cell.deviation >= 0 ? "+" : ""}
          {cell.deviation.toLocaleString(undefined, { maximumFractionDigits: 2 })} vs
          median
        </div>
      )}
    </div>
  );
}
