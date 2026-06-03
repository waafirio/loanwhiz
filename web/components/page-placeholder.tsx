import { Construction } from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/**
 * Standard "coming soon" card for the placeholder routes.
 *
 * Issue #99 replaces each page body with a real fetch-and-render view (see
 * web/CONTRACT.md). Until then every route renders this so the shell is
 * navigable end to end.
 */
export function PagePlaceholder({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        <p className="text-sm text-muted-foreground">{description}</p>
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Construction className="size-4 text-muted-foreground" />
            Coming soon
          </CardTitle>
          <CardDescription>
            This view is part of the LoanWhiz demo UI and will be wired to the
            FastAPI backend in a follow-up. The navigation, layout, and typed
            API client are in place.
          </CardDescription>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">
          See <code className="font-mono">web/CONTRACT.md</code> for how this
          page will fetch and render its data.
        </CardContent>
      </Card>
    </div>
  );
}
