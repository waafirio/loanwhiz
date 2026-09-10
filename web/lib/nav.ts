import {
  LayoutDashboard,
  Layers,
  Waves,
  ShieldCheck,
  TrendingUp,
  Boxes,
  BadgeCheck,
  Scale,
  FileSearch,
  Grid3x3,
  GitCompareArrows,
  BookOpen,
  Layers3,
  Plug,
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
 * The sidebar is split into three sections. Two of them split on what a
 * surface IS; the third splits on WHO IS ASKING, which is why it exists at
 * all (#613) — three holder-level views were scattered across the other two,
 * each sitting in a group about something else.
 *
 *  - "Deal Analytics" — the generally-useful structured-finance product: the
 *    per-deal views an analyst actually works in (overview, pool, waterfall,
 *    compliance, projection).
 *  - "Portfolio" — one reader's question: what do I hold, and what can this
 *    platform prove about it? Named for that reader rather than for the
 *    mechanism. Ordered so the holding comes before the analyses of it: the
 *    Book, then concentration through it, then the diligence record behind
 *    it. Nothing here is per-deal analytics and nothing here is about the
 *    platform.
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
      // Deal Comparison — N-way risk screening + structural diff across deals
      // (#283, epic #262 analyst-facing tools).
      { title: "Comparison", href: "/compare", icon: GitCompareArrows },
    ],
  },
  {
    label: "Portfolio",
    items: [
      // Book — a holder's positions, each badged with what it IS and carrying
      // the platform's refusals in place of blank cells (#573, epic #569).
      // First in the section: the holding a reader meets before any analysis
      // of it.
      { title: "Book", href: "/book", icon: BookOpen },
      // Look-through concentration — true single-name / sector exposure across
      // the CLOs a holder owns, with the unresolved obligor set rendered
      // rather than netted away (#565, epic #560).
      { title: "Concentration", href: "/concentration", icon: Layers3 },
      // Due diligence — the per-deal UK-SR risk-retention record: what was
      // verified from which document, and what was not (#568, epic #561).
      // Beside the holder's other surfaces on purpose, and still deliberately
      // NOT beside Compliance. "Compliance", in the Deal Analytics group
      // above, answers whether the DEAL is inside its covenants; this answers
      // whether the HOLDER's verification is documented. Two questions, two
      // readers — the separation is the point, so do not merge the entries.
      // Grouping by reader (#613) is what makes that separation legible; it
      // was never an argument for filing a holder's surface under "Platform".
      { title: "Due Diligence", href: "/due-diligence", icon: FileSearch },
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
      // MCP — the primitives as a governed MCP server: which are exposed as
      // callable tools, and the evidence a call's result carries (#575).
      { title: "MCP", href: "/mcp", icon: Plug },
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
