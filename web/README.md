# LoanWhiz — Demo UI (Next.js)

A clean, light, professional dashboard over the LoanWhiz FastAPI service.
Built with **Next.js (App Router) + TypeScript + Tailwind + shadcn/ui**, plain
`fetch` + React hooks, and `recharts` for charts. No state library, no auth —
deliberately lean (see [`CONTRACT.md`](./CONTRACT.md)).

## What's here

This is the **app scaffold** (issue #97): the layout shell (sidebar + top
bar), the typed API client, and placeholder pages for the five backend views.
The page bodies (issue #99) and the chat panel (issue #100) build on top.

- `app/layout.tsx` — the shell every page renders into.
- `lib/api.ts` — typed `fetch` client for the FastAPI endpoints.
- `lib/nav.ts` — the five-view navigation definition.
- `app/page.tsx` + `app/(routes)/*/page.tsx` — Overview + Pool / Waterfall /
  Compliance / Projection (currently "coming soon" placeholders).

## Run it

Requires Node 24 (or any current LTS) and npm.

```bash
cd web
npm install
npm run dev
```

Open http://localhost:3000. The shell + placeholder pages render with **no
backend running**.

To talk to the API, run the FastAPI service separately (default
`http://localhost:8000`) — e.g. from the repo root:

```bash
uvicorn loanwhiz.api.main:app --reload
```

Point the UI elsewhere by setting `NEXT_PUBLIC_API_BASE` (see
[`.env.example`](./.env.example)):

```bash
cp .env.example .env.local   # then edit NEXT_PUBLIC_API_BASE
```

## Verify

```bash
npm run build   # Next production build — succeeds with no backend
npm run lint    # ESLint — clean
```

## Adding a page

See [`CONTRACT.md`](./CONTRACT.md) — it documents the route structure, how a
page fetches via `lib/api.ts`, where shared components live, and the
shadcn-defaults / light-theme rule.
