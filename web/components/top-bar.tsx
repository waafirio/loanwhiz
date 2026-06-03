import { DealSelector } from "@/components/deal-selector";
import { Separator } from "@/components/ui/separator";
import { SidebarTrigger } from "@/components/ui/sidebar";

/**
 * Sticky top bar: sidebar toggle, app title, and the registry-driven deal
 * selector. The selector (a shadcn `Select`) reads/writes the selected deal id
 * from `DealProvider`; every data page threads that id into its API calls, so
 * switching the deal here re-fetches the whole UI against the chosen deal.
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
        <DealSelector />
      </div>
    </header>
  );
}
