"use client";

import { useMemo, useState } from "react";

/**
 * Minimal client-side pagination over an in-memory array.
 *
 * Lean by design (see web/CONTRACT.md): no table/data library — long
 * per-period / per-loan tables paginate client-side so a ~48-period (or
 * longer) response renders a bounded number of rows at a time instead of
 * one giant DOM table. The data is already fully fetched; this only slices it.
 */
export interface Pagination<T> {
  /** The rows for the current page. */
  pageItems: T[];
  /** 1-based current page. */
  page: number;
  /** Total number of pages (at least 1). */
  pageCount: number;
  /** Total number of items across all pages. */
  total: number;
  /** Rows per page. */
  pageSize: number;
  /** 1-based index of the first row shown (0 when empty). */
  from: number;
  /** 1-based index of the last row shown (0 when empty). */
  to: number;
  canPrev: boolean;
  canNext: boolean;
  next: () => void;
  prev: () => void;
  setPage: (page: number) => void;
}

export function usePagination<T>(items: T[], pageSize = 12): Pagination<T> {
  const total = items.length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const [page, setPageState] = useState(1);

  // Clamp at render time (no effect needed): if the underlying data shrinks —
  // e.g. after a deal switch — `safePage` falls back into range immediately,
  // and the setters below also clamp so stored state can't drift out of bounds.
  const safePage = Math.min(Math.max(1, page), pageCount);
  const start = (safePage - 1) * pageSize;

  const pageItems = useMemo(
    () => items.slice(start, start + pageSize),
    [items, start, pageSize],
  );

  return {
    pageItems,
    page: safePage,
    pageCount,
    total,
    pageSize,
    from: total === 0 ? 0 : start + 1,
    to: Math.min(start + pageSize, total),
    canPrev: safePage > 1,
    canNext: safePage < pageCount,
    next: () => setPageState((p) => Math.min(p + 1, pageCount)),
    prev: () => setPageState((p) => Math.max(p - 1, 1)),
    setPage: (p: number) => setPageState(Math.min(Math.max(1, p), pageCount)),
  };
}
