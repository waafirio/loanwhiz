"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { Pagination } from "@/lib/use-pagination";

/**
 * Pagination footer for the long client-side-paginated tables (per-period
 * pool metrics, per-scenario tranche rows). Renders a "showing X–Y of N" hint
 * plus prev/next controls. Pairs with `usePagination` from
 * `@/lib/use-pagination`. Renders nothing when there is only one page.
 */
export function TablePagination<T>({
  pagination,
  noun = "rows",
}: {
  pagination: Pagination<T>;
  /** Plural label for the counted items, e.g. "periods", "rows". */
  noun?: string;
}) {
  const { from, to, total, page, pageCount, canPrev, canNext, prev, next } =
    pagination;

  if (pageCount <= 1) return null;

  return (
    <div className="mt-3 flex items-center justify-between gap-2 text-sm text-muted-foreground">
      <span className="tabular-nums">
        Showing {from}–{to} of {total} {noun}
      </span>
      <div className="flex items-center gap-2">
        <span className="tabular-nums">
          Page {page} / {pageCount}
        </span>
        <Button
          variant="outline"
          size="sm"
          onClick={prev}
          disabled={!canPrev}
          aria-label="Previous page"
        >
          <ChevronLeft />
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={next}
          disabled={!canNext}
          aria-label="Next page"
        >
          <ChevronRight />
        </Button>
      </div>
    </div>
  );
}
