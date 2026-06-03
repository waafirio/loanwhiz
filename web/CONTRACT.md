# Page-plugin contract (`web/`)

This is the **foundation** for the LoanWhiz demo UI. It defines the shell,
the typed API client, and the conventions the five backend pages (issue #99)
and the chat panel (issue #100) build against. Read this before adding a page.

The binding constraint is **LEAN**: lean entirely on shadcn/ui defaults
(clean, light, professional out of the box). Do **not** build a custom
theme/token system, do **not** add a state-management library — plain
`fetch` + React hooks only. No auth, no animations.

---

## 1. Where things live

```
web/
  app/
    layout.tsx              # the shell — sidebar + top bar; DON'T duplicate
    globals.css             # shadcn theme tokens (light, neutral). DON'T edit
    page.tsx                # Overview route ("/")
    (routes)/
      pool/page.tsx         # /pool          — Pool & Performance
      waterfall/page.tsx    # /waterfall     — Waterfall
      compliance/page.tsx   # /compliance    — Compliance
      projection/page.tsx   # /projection    — Projection
  components/
    app-sidebar.tsx         # left nav rail (reads lib/nav)
    top-bar.tsx             # title + deal selector
    page-placeholder.tsx    # the "coming soon" card (replace per page)
    ui/                     # shadcn components (button, card, table, ...)
  hooks/                    # shadcn hooks (use-mobile, ...)
  lib/
    api.ts                  # THE typed API client — use it, don't refetch raw
    nav.ts                  # NAV_ITEMS + DEAL_LABEL (single source of nav)
    utils.ts                # shadcn `cn()` helper
```

- **Shared page components** that aren't shadcn primitives go in
  `components/` (e.g. a `MetricCard`, a `WaterfallTable`). One component per
  file, kebab-case filename, named export.
- **Need another shadcn component?** Add it with the CLI, don't hand-write it:
  `npx shadcn@latest add <name>` (e.g. `select`, `tabs`, `chart`). It lands in
  `components/ui/`.
- **Charts:** `recharts` is already a dependency. Keep charts basic (per the
  lean constraint) — a simple `<BarChart>` / `<LineChart>`, not a bespoke viz.

## 2. Routing convention

- Each backend view is its own route under `app/`. Overview is the root `/`;
  the other four live in the `(routes)` route group (a group keeps them
  organised without adding a URL segment).
- The nav is driven by `lib/nav.ts → NAV_ITEMS`. **To add or rename a view,
  edit `NAV_ITEMS`** — the sidebar renders from it and active-state is derived
  from the pathname automatically. The `href` must match the route folder.
- Every page renders **into the shell** (`app/layout.tsx`) automatically —
  you only write the page body, never the sidebar/top-bar.

## 3. How a page fetches and renders

A page that shows backend data is a **Client Component** (`"use client"`) that
calls a typed wrapper from `lib/api.ts` in `useEffect`, holds the result in
`useState`, and handles three states: loading (`<Skeleton/>`), error (a small
card), and data. **No fetching at build time** — pages must render a
loading/placeholder state with no backend running, so `npm run build` never
needs the API.

```tsx
"use client";

import { useEffect, useState } from "react";
import { getCompliance, ApiError, type ComplianceResult } from "@/lib/api";
import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function CompliancePage() {
  const [data, setData] = useState<ComplianceResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getCompliance()
      .then(setData)
      .catch((e) =>
        setError(e instanceof ApiError ? e.message : "Failed to load"),
      );
  }, []);

  if (error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Could not load compliance</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-muted-foreground">{error}</CardContent>
      </Card>
    );
  }
  if (!data) return <Skeleton className="h-64 w-full" />;

  // ...render data with shadcn <Table/>, <Badge/>, <Card/>, recharts...
}
```

### The API client (`lib/api.ts`)

Use the typed wrappers — never `fetch` the backend directly from a page.

| Wrapper | Endpoint | Returns |
|---|---|---|
| `getServiceInfo()` | `GET /` | `ServiceInfo` |
| `getHealth()` | `GET /health` | `HealthStatus` |
| `postQuery({ question, confidence_threshold? })` | `POST /query` | `QueryResponse` |
| `getDealModel(dealId?)` | `GET /deal/{id}/model` | `DealModel` |
| `getCompliance(dealId?)` | `GET /deal/{id}/compliance` | `ComplianceResult` |
| `postProjection({ scenarios?, months? }, dealId?)` | `POST /deal/{id}/project` | `ProjectionResult` |

- `dealId` defaults to `DEFAULT_DEAL_ID` (`"green-lion-2026-1"`) — the only
  deal the backend serves.
- Base URL: `API_BASE` from `NEXT_PUBLIC_API_BASE` (default
  `http://localhost:8000`). Set it in `.env.local` to point elsewhere.
- Non-2xx responses throw `ApiError` (with `.status` and `.detail`). Catch it
  to render the error state.

## 4. Theme rules (non-negotiable)

- **shadcn defaults, light theme.** Don't edit `globals.css` tokens, don't add
  a custom palette, don't add a dark-mode toggle. The neutral light theme and
  Geist font are already configured.
- Use shadcn primitives and Tailwind utility classes only. No CSS-in-JS, no
  styled-components, no extra UI library.
- Keep it boring and clean — that is the whole point.
