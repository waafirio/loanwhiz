"use client";

import { useEffect, useState } from "react";

import {
  ApiError,
  getDueDiligence,
  type DueDiligenceRecord,
} from "@/lib/api";
import { useSelectedDeal } from "@/lib/deal-context";
import { DueDiligenceBody } from "@/components/evidence-pack-sheet";
import { ErrorState, LoadingState, PageHeader } from "@/components/page-states";
import { Card, CardContent } from "@/components/ui/card";

/**
 * Investor due-diligence record (#568, epic #561) — the evidence file a
 * compliance reader can take away.
 *
 * **Beside governance, not beside compliance.** `/compliance` answers whether
 * the *deal* is inside its covenants; this answers whether the *holder's*
 * UK-SR risk-retention verification is documented. A deal can pass every
 * covenant with its retention unestablished, and the reverse — so this sits in
 * the sidebar's "Platform & Governance" group and shares no vocabulary with
 * the covenant screen.
 *
 * The rendering itself lives in `DueDiligenceBody`, alongside the evidence-pack
 * components it reuses, and is guarded by `tests/test_due_diligence_surface.py`.
 * This page is the fetch and the frame.
 */
export default function DueDiligencePage() {
  const { dealId } = useSelectedDeal();

  // Keyed by the deal the response is FOR, so a slow response for a
  // previously-selected deal cannot paint under the current deal's heading —
  // a record attributed to the wrong deal is the one error this screen must
  // not make. Same shape as the Compliance and Overview pages.
  const [state, setState] = useState<{
    dealId: string;
    data: DueDiligenceRecord | null;
    error: string | null;
  }>({ dealId, data: null, error: null });

  useEffect(() => {
    let cancelled = false;
    getDueDiligence(dealId)
      .then((d) => !cancelled && setState({ dealId, data: d, error: null }))
      .catch(
        (e) =>
          !cancelled &&
          setState({
            dealId,
            data: null,
            error:
              e instanceof ApiError
                ? e.message
                : "Failed to load the due-diligence record.",
          }),
      );
    return () => {
      cancelled = true;
    };
  }, [dealId]);

  const current = state.dealId === dealId ? state : null;
  const data = current?.data ?? null;
  const error = current?.error ?? null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Due diligence"
        description="Risk-retention verification per deal — what was established from which document, and what was not."
      />
      {error ? (
        <ErrorState
          title="Could not load the due-diligence record"
          message={error}
        />
      ) : !data ? (
        <LoadingState />
      ) : (
        <Card>
          <CardContent className="pt-6">
            <DueDiligenceBody record={data} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
