"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { NAV_GROUPS } from "@/lib/nav";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar";

/**
 * Left navigation rail. Renders the labelled nav sections from NAV_GROUPS
 * ("Deal Analytics" — the per-deal analyst views; "Portfolio" — the holder's
 * own surfaces, grouped by who is asking rather than by what they are;
 * "Platform & Governance" — the reusable-framework / trust / demo-headline
 * views) and highlights the active route. Collapsible to icons on desktop,
 * off-canvas sheet on mobile (both behaviours come from shadcn's Sidebar
 * defaults).
 *
 * Every section named above is checked against NAV_GROUPS by
 * tests/test_published_nav_sections.py, so this comment cannot outlive a
 * regrouping the way the two-section version it replaces did (#617).
 */
export function AppSidebar() {
  const pathname = usePathname();

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader>
        <div className="flex items-center gap-2 px-2 py-1.5">
          <div className="flex aspect-square size-8 items-center justify-center rounded-md bg-primary text-primary-foreground text-sm font-semibold">
            LW
          </div>
          <span className="text-base font-semibold group-data-[collapsible=icon]:hidden">
            LoanWhiz
          </span>
        </div>
      </SidebarHeader>
      <SidebarContent>
        {NAV_GROUPS.map((group) => (
          <SidebarGroup key={group.label}>
            <SidebarGroupLabel>{group.label}</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {group.items.map((item) => {
                  const isActive =
                    item.href === "/"
                      ? pathname === "/"
                      : pathname.startsWith(item.href);
                  return (
                    <SidebarMenuItem key={item.href}>
                      <SidebarMenuButton
                        isActive={isActive}
                        tooltip={item.title}
                        render={
                          <Link href={item.href}>
                            <item.icon />
                            <span>{item.title}</span>
                          </Link>
                        }
                      />
                    </SidebarMenuItem>
                  );
                })}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        ))}
      </SidebarContent>
      <SidebarRail />
    </Sidebar>
  );
}
