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

/**
 * The five backend views. Each `href` is a route under `app/`.
 * Issue #99 fills the page bodies; this is the canonical nav order.
 */
export const NAV_ITEMS: NavItem[] = [
  // Showcase — the refreshed demo's headline/landing (#242, C4, epic #236):
  // the same governed primitives rendered across Dutch / Italian / Spanish RMBS
  // as the primitives × deals capability matrix (validated / ran / not-applicable,
  // with the honest reason behind every cell). Placed first as the demo lead.
  { title: "Showcase", href: "/showcase", icon: Grid3x3 },
  { title: "Overview", href: "/", icon: LayoutDashboard },
  { title: "Pool & Performance", href: "/pool", icon: Layers },
  { title: "Waterfall", href: "/waterfall", icon: Waves },
  { title: "Compliance", href: "/compliance", icon: ShieldCheck },
  { title: "Projection", href: "/projection", icon: TrendingUp },
  // Validation — the headline seasoned-deal proof (#212, epic #206): our
  // waterfall engine reproduced against a real ING deal's own published Notes
  // & Cash Priority of Payments, to the cent.
  { title: "Validation", href: "/validation", icon: BadgeCheck },
  // Framework — the primitive registry catalogue (#137): surfaces the typed
  // primitive contracts that make up the framework the challenge judges.
  { title: "Framework", href: "/primitives", icon: Boxes },
  // Governance — the FINOS evidence-pack / audit-trail / confidence /
  // model-risk surface (#239), the challenge's trust differentiator: per agent
  // query, the auditable reasoning trace, confidence, citations, finos_compliant,
  // and data provenance (deeploans vs direct).
  { title: "Governance", href: "/governance", icon: Scale },
];

/** The single deal the demo serves, shown in the top-bar deal selector. */
export const DEAL_LABEL = "Green Lion 2026-1";
