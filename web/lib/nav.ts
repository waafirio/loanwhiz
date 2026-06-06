import {
  LayoutDashboard,
  Layers,
  Waves,
  ShieldCheck,
  TrendingUp,
  Boxes,
  BadgeCheck,
  Scale,
  Grid3x3,
  type LucideIcon,
} from "lucide-react";

/** A single sidebar navigation entry. */
export interface NavItem {
  title: string;
  href: string;
  icon: LucideIcon;
}

/** A labelled section of the sidebar. */
export interface NavGroup {
  label: string;
  items: NavItem[];
}

/**
 * The sidebar is split into two sections:
 *
 *  - "Deal Analytics" — the generally-useful structured-finance product: the
 *    per-deal views an analyst actually works in (overview, pool, waterfall,
 *    compliance, projection).
 *  - "Platform & Governance" — the reusable-framework / trust / cross-deal
 *    layer built to headline the hackathon: the cross-jurisdiction showcase,
 *    the engine-validation proof, the primitive-registry catalogue, and the
 *    FINOS + deeploans governance surface.
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Deal Analytics",
    items: [
      { title: "Overview", href: "/", icon: LayoutDashboard },
      { title: "Pool & Performance", href: "/pool", icon: Layers },
      { title: "Waterfall", href: "/waterfall", icon: Waves },
      { title: "Compliance", href: "/compliance", icon: ShieldCheck },
      { title: "Projection", href: "/projection", icon: TrendingUp },
    ],
  },
  {
    label: "Platform & Governance",
    items: [
      // Showcase — same governed primitives across Dutch / Italian / Spanish
      // RMBS as the primitives × deals capability matrix (#242, epic #236).
      { title: "Showcase", href: "/showcase", icon: Grid3x3 },
      // Validation — the engine reproduced against a real ING deal's own
      // published Notes & Cash Priority of Payments, to the cent (#212, #206).
      { title: "Validation", href: "/validation", icon: BadgeCheck },
      // Framework — the typed primitive-registry catalogue (#137).
      { title: "Framework", href: "/primitives", icon: Boxes },
      // Governance — the FINOS evidence-pack / audit-trail / confidence /
      // model-risk + data-provenance (deeploans vs direct) surface (#239).
      { title: "Governance", href: "/governance", icon: Scale },
    ],
  },
];

/** Flattened view of every nav entry (canonical order), for any consumer that
 * wants the items without their section grouping. */
export const NAV_ITEMS: NavItem[] = NAV_GROUPS.flatMap((g) => g.items);

/** The single deal the demo serves, shown in the top-bar deal selector. */
export const DEAL_LABEL = "Green Lion 2026-1";
