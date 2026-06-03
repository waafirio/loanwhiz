# LoanWhiz Demo UI (v2)

A Next.js browser frontend for the LoanWhiz FastAPI service. The UI runs on
`:3000` and calls the API on `:8000`; CORS on the API allows the local dev
origin so the browser can talk to it directly.

> The Next.js app itself lives in this `web/` directory (built under issue
> #97). This README covers running the full v2 demo — backend + frontend —
> together.

## Prerequisites

1. **Google Application Default Credentials (ADC)** — the backend uses Vertex
   AI. Authenticate once:

   ```bash
   gcloud auth application-default login
   ```

2. **GCP project** — the backend expects `GOOGLE_CLOUD_PROJECT=loanwhiz`
   (the run script sets this for you; export it yourself if you start the
   backend manually).

3. **Backend installed** — from the repo root:

   ```bash
   pip install -e .
   ```

4. **Frontend dependencies** — from this directory:

   ```bash
   cd web && npm install
   ```

## Run both with one command

From the repo root:

```bash
./scripts/run-demo-v2.sh
```

This starts the FastAPI backend in the background and the Next.js dev server
in the foreground. Press **Ctrl-C** to stop both.

The two URLs:

- **API** — http://localhost:8000 (interactive docs at http://localhost:8000/docs)
- **UI** — http://localhost:3000

## Note on ADC tokens

ADC tokens expire. If the backend starts returning auth errors, re-run
`gcloud auth application-default login` before the demo — token expiry is the
most common cause of a backend that worked yesterday but fails today.
