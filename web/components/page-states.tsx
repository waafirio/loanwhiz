import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

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
