"use client";

import { Badge } from "@/components/ui/badge";
import {
  type CompareDealRef,
  type PerformanceProvenance,
  type RateProvenance,
} from "@/lib/api";

/* ---------------------------------------------------------------------------
 * How a compare Panel-2 series' basis is named on screen (#614, epic #613)
 *
 * #599 gave the tape-channel vocabulary one total table that five surfaces
 * route through, and the comparison panel was not one of them — it was never
 * onboarded, and nobody had counted it. It hand-rolled
 * `d.performance_provenance === "projected"` in two places, which is the exact
 * shape #599's `no-provenance-ternary` rule bans everywhere else: a literal
 * condition names the branch it tests and silently mislabels every other member
 * of the union, so the moment the union grew a case the panel called it
 * "reported" by omission.
 *
 * So this is that table, for this surface's two unions. It is a SEPARATE module
 * from `provenance-badge.tsx` on purpose: that one is total over the tape
 * *ingestion channel* (`deeploans | direct | derived | synthetic`), which is a
 * different question from how a series was built and what rate it rests on.
 * Merging them would put one label table over two unrelated vocabularies and
 * make totality unenforceable for either.
 *
 * Two unions, deliberately not one
 * --------------------------------
 * How a series was BUILT and what the coupon under it CAME FROM vary
 * independently. Cairn CLO XVII is reported-on-a-stated-rate; Contego CLO XI is
 * projected-on-a-generated-one. A single enum would force a false choice
 * between naming the construction and naming the assumption, and whichever it
 * named, the other would go unsaid on the screen where the two deals sit side
 * by side.
 *
 * `Record<Union, string>` is what makes these total: adding a member to either
 * union in `lib/api.ts` fails the build here until every table below names it.
 * That is the property `tests/test_compare_provenance.py` mutates against.
 * ------------------------------------------------------------------------- */

/** What each construction means, in the reader's words. Total over the union. */
export const PERFORMANCE_PROVENANCE_LABELS: Record<
  PerformanceProvenance,
  string
> = {
  reported: "REPORTED — folded from this deal's own published report history",
  projected:
    "PROJECTED — a forward projection from the canonical model, not reported performance",
};

/** What each coupon source means, in the reader's words. Total over the union. */
export const RATE_PROVENANCE_LABELS: Record<RateProvenance, string> = {
  stated: "STATED RATE — the coupon is published or operator-declared",
  synthetic:
    "SYNTHETIC RATE — the coupon rests on a generated index fixing, not a published one",
};

/**
 * The suffix each construction adds to a deal's name in the chart legend.
 *
 * The legend is where the two series are most easily confused, because it is
 * the one place they appear as bare names next to each other. Marking from a
 * table rather than a ternary is #575's lesson: written once beside a list, a
 * marking survives only until someone adds a case.
 */
export const PERFORMANCE_PROVENANCE_SUFFIX: Record<
  PerformanceProvenance,
  string
> = {
  reported: "",
  projected: " (projected)",
};

/** The suffix each coupon source adds to a deal's name in the chart legend. */
export const RATE_PROVENANCE_SUFFIX: Record<RateProvenance, string> = {
  stated: "",
  synthetic: " (synthetic rate)",
};

/**
 * How loudly each coupon source reads. `synthetic` is `destructive` for the
 * same reason #599 made the synthetic tape channel destructive: a generated
 * input that renders at the same weight as a published one has defeated the
 * marking, which is #484's original defect.
 */
export const RATE_PROVENANCE_VARIANTS: Record<
  RateProvenance,
  "outline" | "destructive"
> = {
  stated: "outline",
  synthetic: "destructive",
};

/** The legend name for one deal, marked with whatever its basis actually is. */
export function seriesLegendName(deal: CompareDealRef): string {
  const built =
    deal.performance_provenance === null
      ? ""
      : PERFORMANCE_PROVENANCE_SUFFIX[deal.performance_provenance];
  const rate =
    deal.rate_provenance === null
      ? ""
      : RATE_PROVENANCE_SUFFIX[deal.rate_provenance];
  return `${deal.deal_name}${built}${rate}`;
}

/**
 * Whether this deal's series is anything other than reported-on-a-stated-rate.
 *
 * Used to decide which deals need a basis row. Written as "not the plain case"
 * rather than as a list of loud cases, so a union member added later is flagged
 * by default instead of falling silently into the quiet branch.
 */
export function hasQualifiedBasis(deal: CompareDealRef): boolean {
  if (!deal.has_performance) return false;
  return (
    deal.performance_provenance !== "reported" ||
    deal.rate_provenance !== "stated"
  );
}

/**
 * One deal's basis, stated in full: how the series was built, what rate it
 * rests on, and the API's own disclosure sentence.
 *
 * The disclosure is rendered verbatim from `note` rather than reassembled here,
 * so the tenor, the assumed value and the "no fixing is published" fact cannot
 * drift between the API that knows them and the screen that shows them. The
 * component renders both badges unconditionally when the values are present —
 * there is no branch that can drop one and keep the other.
 */
export function SeriesBasisRow({ deal }: { deal: CompareDealRef }) {
  return (
    <li className="flex flex-col gap-1 border-l-2 border-amber-400 pl-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{deal.deal_name}</span>
        {deal.performance_provenance !== null && (
          <Badge variant="outline">
            {PERFORMANCE_PROVENANCE_LABELS[deal.performance_provenance]}
          </Badge>
        )}
        {deal.rate_provenance !== null && (
          <Badge variant={RATE_PROVENANCE_VARIANTS[deal.rate_provenance]}>
            {RATE_PROVENANCE_LABELS[deal.rate_provenance]}
          </Badge>
        )}
      </div>
      {deal.note && <p className="text-xs leading-relaxed">{deal.note}</p>}
    </li>
  );
}
