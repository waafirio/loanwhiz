import { Building2 } from "lucide-react";

import { DEAL_LABEL } from "@/lib/nav";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";

/**
 * Sticky top bar: sidebar toggle, app title, and the deal selector.
 *
 * The deal selector is a static label — the backend serves a single deal
 * (Green Lion 2026-1). It is shaped as a selector so a future multi-deal
 * backend can swap it for a dropdown without touching the layout.
 */
export function TopBar() {
  return (
    <header className="sticky top-0 z-10 flex h-14 shrink-0 items-center gap-2 border-b bg-background px-4">
      <SidebarTrigger className="-ml-1" />
      <Separator orientation="vertical" className="mr-1 h-4" />
      <span className="text-sm font-semibold tracking-tight">LoanWhiz</span>
      <div className="ml-auto flex items-center gap-2">
        <span className="hidden text-xs text-muted-foreground sm:inline">
          Deal
        </span>
        <Badge variant="secondary" className="gap-1.5 font-normal">
          <Building2 className="size-3.5" />
          {DEAL_LABEL}
        </Badge>
      </div>
    </header>
  );
}
