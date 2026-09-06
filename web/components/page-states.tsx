import { useEffect, useState } from "react";

import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { getDealModel } from "@/lib/api";

/**
 * The two shared non-data states every backend page renders (see
 * web/CONTRACT.md §3): a loading skeleton and an error card. Kept as plain
 * presentational components — no abstraction over the fetch itself, each page
 * still owns its own useEffect/useState.
 */

/** A simple page header: title + one-line description. */
export function PageHeader({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="space-y-1">
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <p className="text-sm text-muted-foreground">{description}</p>
    </div>
  );
}

/** Loading skeleton shown while the API call is pending. */
export function LoadingState() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-28 w-full" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

/** Error card shown when the API call fails. */
export function ErrorState({ title, message }: { title: string; message: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{title}</CardTitle>
      </CardHeader>
      <CardContent className="text-sm text-muted-foreground">
        {message}
      </CardContent>
    </Card>
  );
}

/** Empty-data card — e.g. Overview's "no tranches extracted yet". */
export function EmptyState({ message }: { message: string }) {
  return (
    <div className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
      {message}
    </div>
  );
}

/**
 * Graceful-degrade notice for the tape-based views (Pool / Waterfall /
 * Compliance / Projection) when the selected deal has no loan tape registered
 * in LoanWhiz (e.g. the real ING Green Lion 2023-1 / 2024-1, whose loan-level
 * tapes live in a private repository — #212, epic #206). These views are
 * loan-tape-driven, so rather than a generic "no data" empty card (which reads
 * like a bug), this says what is missing and points the user at where the
 * deal's validation *does* live: the Validation view.
 *
 * The wording states what LoanWhiz has, never what an issuer discloses — the
 * distinction #457 drew for the capability matrix's cell reasons, which this
 * card is the user-facing counterpart of. "No tape is registered" is a fact
 * about this system; "no loan tapes are published" was a claim about the world,
 * and a false one for a deal whose loan-level detail is published in another
 * form (a CLO trustee report, say).
 */
export function NoTapesNotice({ what }: { what: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">
          No loan tape is registered for this deal
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm text-muted-foreground">
        <p>
          LoanWhiz has no loan-level tape registered for this deal, so {what}{" "}
          does not apply here. That is a statement about what this system holds
          — the deal may publish loan-level detail in a form no tape covers.
        </p>
        <p>
          See the{" "}
          <a
            href="/validation"
            className="text-primary underline-offset-4 hover:underline"
          >
            Validation
          </a>{" "}
          view, where our waterfall engine is reconciled against this deal&apos;s
          own published Notes &amp; Cash Priority of Payments.
        </p>
      </CardContent>
    </Card>
  );
}

/**
 * Whether the selected deal has any published loan tapes — the "seasoned deal"
 * signal the tape-based views (Pool / Waterfall / Compliance / Projection) use
 * to render `NoTapesNotice` instead of a generic empty/error card. Returns
 * `null` while the deal-model fetch is in flight (the page shows its own loading
 * state until then) and on fetch failure (so the page falls through to its own
 * data fetch + error handling rather than silently hiding it).
 *
 * Colocated with `NoTapesNotice` so the degrade behaviour lives in one place;
 * each tape page reads `useDealHasTapes(dealId)` and branches on it.
 */
export function useDealHasTapes(dealId: string): boolean | null {
  const [state, setState] = useState<{ dealId: string; hasTapes: boolean | null }>(
    { dealId, hasTapes: null },
  );

  useEffect(() => {
    let cancelled = false;
    getDealModel(dealId)
      .then(
        (d) =>
          !cancelled &&
          setState({ dealId, hasTapes: d.tape_urls.length > 0 }),
      )
      .catch(
        // On failure, leave `hasTapes` null so the page falls through to its
        // own data fetch (which renders the real error). Don't mask it.
        () => !cancelled && setState({ dealId, hasTapes: null }),
      );
    return () => {
      cancelled = true;
    };
  }, [dealId]);

  // Until the model for the *current* deal resolves, report "unknown" (null).
  return state.dealId === dealId ? state.hasTapes : null;
}
