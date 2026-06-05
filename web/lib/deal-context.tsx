"use client";

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { DEFAULT_DEAL_ID, getDeals, type DealSummary } from "@/lib/api";

/**
 * Selected-deal state for the whole app.
 *
 * Lean by design (see web/CONTRACT.md): a single React context — no state
 * library. The provider fetches `GET /deals` once on mount to populate the
 * selector and holds the currently-selected deal id. Every data page reads
 * `useSelectedDeal()` and threads `dealId` into its `lib/api.ts` call, so
 * switching the deal re-fetches every page against the chosen deal.
 *
 * The selector defaults to `green-lion-2026-1` (DEFAULT_DEAL_ID). When the
 * `/deals` list comes back and does NOT contain that id, we fall back to the
 * first available deal so the UI never points at a deal the backend doesn't
 * serve. With no backend reachable the context still works — `dealId` stays
 * at the default and the selector simply shows that single entry.
 *
 * This provider is deal-count-agnostic: it exposes whatever `/deals` returns
 * (1..N) and the top-bar `DealSelector` decides how to render it — a static
 * label for a single deal, a dropdown for two or more (see #198 and
 * web/components/deal-selector.tsx). Nothing here changes when the seasoned
 * deals land; the list simply grows.
 */

interface DealContextValue {
  /** The currently-selected deal id — feeds every `/deal/{id}/...` call. */
  dealId: string;
  /** Switch the selected deal. */
  setDealId: (id: string) => void;
  /** Available deals from `GET /deals` (empty until the fetch resolves). */
  deals: DealSummary[];
  /** True while the initial `/deals` fetch is in flight. */
  loading: boolean;
}

const DealContext = createContext<DealContextValue | null>(null);

export function DealProvider({ children }: { children: React.ReactNode }) {
  const [dealId, setDealId] = useState<string>(DEFAULT_DEAL_ID);
  const [deals, setDeals] = useState<DealSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    getDeals()
      .then((list) => {
        if (cancelled) return;
        setDeals(list);
        // Keep the default if it's served; otherwise fall back to the first
        // available deal so we never query a deal the backend doesn't have.
        setDealId((current) =>
          list.some((d) => d.id === current)
            ? current
            : (list[0]?.id ?? current),
        );
      })
      .catch(() => {
        // No backend / fetch failed: keep the default deal id. Pages render
        // their own error states for the actual data calls.
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const value = useMemo<DealContextValue>(
    () => ({ dealId, setDealId, deals, loading }),
    [dealId, deals, loading],
  );

  return <DealContext.Provider value={value}>{children}</DealContext.Provider>;
}

/** Read the selected deal id + setter + available deals. */
export function useSelectedDeal(): DealContextValue {
  const ctx = useContext(DealContext);
  if (!ctx) {
    throw new Error("useSelectedDeal must be used within a <DealProvider>");
  }
  return ctx;
}
