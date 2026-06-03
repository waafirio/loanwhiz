import {
  LayoutDashboard,
  Layers,
  Waves,
  ShieldCheck,
  TrendingUp,
  Boxes,
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
  { title: "Overview", href: "/", icon: LayoutDashboard },
  { title: "Pool & Performance", href: "/pool", icon: Layers },
  { title: "Waterfall", href: "/waterfall", icon: Waves },
  { title: "Compliance", href: "/compliance", icon: ShieldCheck },
  { title: "Projection", href: "/projection", icon: TrendingUp },
  // Framework — the primitive registry catalogue (#137): surfaces the typed
  // primitive contracts that make up the framework the challenge judges.
  { title: "Framework", href: "/primitives", icon: Boxes },
];

/** The single deal the demo serves, shown in the top-bar deal selector. */
export const DEAL_LABEL = "Green Lion 2026-1";
