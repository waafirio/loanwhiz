"""LoanWhiz REST API package.

Exposes the FastAPI application object so clients and the uvicorn entrypoint
can do ``from loanwhiz.api import app``.
"""

from loanwhiz.api.main import app

__all__ = ["app"]
