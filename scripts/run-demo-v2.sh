#!/usr/bin/env bash
#
# run-demo-v2.sh — one-command launcher for the LoanWhiz demo v2.
#
# Starts the FastAPI backend (uvicorn, :8000) in the background and the
# Next.js dev server (web/, :3000) in the foreground. Ctrl-C (or any exit)
# tears both down together via the trap below.
#
#   API:  http://localhost:8000   (docs at /docs)
#   UI:   http://localhost:3000
#
# The web/ frontend is built by issue #97; if it isn't present yet this
# script prints a clear message and exits without starting anything.

set -euo pipefail

# Resolve the repo root from this script's location, so the script works
# regardless of the caller's working directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WEB_DIR="$REPO_ROOT/web"

# The browser frontend lives in web/, created by sibling issue #97. Until that
# lands there is nothing to serve on :3000 — fail fast with a clear message
# rather than starting a backend that has no UI to talk to.
if [ ! -d "$WEB_DIR" ]; then
  echo "error: web/ not found at $WEB_DIR" >&2
  echo "       The Next.js frontend (issue #97) is not present on this branch yet." >&2
  echo "       Once web/ exists, run:  cd web && npm install  then re-run this script." >&2
  exit 1
fi

# Vertex/ADC configuration the backend needs (matches the API run convention).
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-loanwhiz}"
export GOOGLE_GENAI_USE_VERTEXAI="${GOOGLE_GENAI_USE_VERTEXAI:-true}"
export PYTHONPATH="${PYTHONPATH:-$REPO_ROOT/src}"

API_PID=""
cleanup() {
  if [ -n "$API_PID" ] && kill -0 "$API_PID" 2>/dev/null; then
    echo
    echo "Stopping API (pid $API_PID)..."
    kill "$API_PID" 2>/dev/null || true
    wait "$API_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "Starting FastAPI on http://localhost:8000 ..."
uvicorn loanwhiz.api.main:app --port 8000 &
API_PID=$!

echo "Starting Next.js dev server on http://localhost:3000 ..."
echo "(Ctrl-C stops both.)"
cd "$WEB_DIR"
# Foreground; when it exits (or Ctrl-C), the EXIT trap stops the API.
npm run dev
