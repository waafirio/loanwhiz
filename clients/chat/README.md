# LoanWhiz Chat Interface

Gradio-based natural language Q&A UI for the Green Lion 2026-1 RMBS deal.
Calls LoanWhiz primitives directly (no REST API required) and uses Gemini 2.5
Flash on Vertex AI to answer questions grounded in the loaded ESMA loan tapes.

## Quickstart

```bash
# 1. Install dependencies (from the repo root):
pip install -e ".[dev]"
# or just the chat extras:
pip install -r clients/chat/requirements.txt

# 2. Authenticate with Vertex AI (GCP project: loanwhiz, region: us-central1):
gcloud auth application-default login

# 3. Run the app:
python clients/chat/app.py
```

Open <http://localhost:7860> in your browser. The interface loads the three
monthly Green Lion tapes on first query (network access required). Subsequent
queries use the in-process cache.
