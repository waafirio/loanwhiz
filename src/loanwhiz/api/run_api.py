"""Uvicorn entrypoint for the LoanWhiz REST API.

Run with::

    python -m loanwhiz.api.run_api

or, for autoreload during development::

    uvicorn loanwhiz.api.main:app --reload
"""

from __future__ import annotations

import os

import uvicorn

from loanwhiz.api.main import app


def main() -> None:
    """Launch the API with uvicorn, honouring ``HOST`` / ``PORT`` env vars."""
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
