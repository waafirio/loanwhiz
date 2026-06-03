# LoanWhiz — Demo UI (v2, Next.js)

A clean, light, professional dashboard over the LoanWhiz FastAPI service.
Built with **Next.js (App Router) + TypeScript + Tailwind + shadcn/ui**, plain
`fetch` + React hooks, and `recharts` for charts. No state library, no auth —
deliberately lean (see [`CONTRACT.md`](./CONTRACT.md)).

The UI runs on `:3000` and calls the API on `:8000`; CORS on the API allows the
local dev origin so the browser can talk to it directly.

## What's here

- `app/layout.tsx` — the shell every page renders into (sidebar + top bar).
- `lib/api.ts` — typed `fetch` client for the FastAPI endpoints.
- `lib/nav.ts` — the five-view navigation definition.
- `app/page.tsx` + `app/(routes)/*/page.tsx` — Overview + Pool / Waterfall /
  Compliance / Projection.

## Prerequisites

1. **Google Application Default Credentials (ADC)** — the backend uses Vertex
   AI. Authenticate once: `gcloud auth application-default login`.
   (ADC tokens expire — if the backend starts returning auth errors, re-run
   this. Token expiry is the most common "worked yesterday, fails today" cause.)
2. **GCP project** — the backend expects `GOOGLE_CLOUD_PROJECT=loanwhiz`
   (the run script sets this for you).
3. **Backend installed** — from the repo root: `pip install -e .`
4. **Frontend dependencies** — from this directory: `cd web && npm install`
   (requires Node 24 or any current LTS).

## Run both with one command

From the repo root:

```bash
./scripts/run-demo-v2.sh
```

Starts the FastAPI backend in the background and the Next.js dev server in the
foreground. Press **Ctrl-C** to stop both.

- **API** — http://localhost:8000 (docs at http://localhost:8000/docs)
- **UI** — http://localhost:3000

### Frontend only

```bash
cd web && npm run dev   # shell + pages render with no backend; API calls just error
```

Point the UI elsewhere with `NEXT_PUBLIC_API_BASE` (see `.env.example`):
`cp .env.example .env.local` then edit.

## Verify

```bash
npm run build   # Next production build — succeeds with no backend
npm run lint    # ESLint — clean
```

## Adding a page

See [`CONTRACT.md`](./CONTRACT.md) — route structure, how a page fetches via
`lib/api.ts`, where shared components live, and the shadcn-defaults /
light-theme rule.
