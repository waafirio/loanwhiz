"""deeploans integration client for LoanWhiz.

Integration contract
--------------------
deeploans (https://github.com/Algoritmica-ai/deeploans) is an open-source
ESMA loan-level ETL framework built by Algoritmica.ai. It ships a FastAPI
backend that serves normalised loan tape data across multiple asset classes
(SME, residential mortgages, consumer lending, auto loans) via a REST API, and
an MCP server that adds analyst-friendly introspection tools on top of that API.

How LoanWhiz uses deeploans
~~~~~~~~~~~~~~~~~~~~~~~~~~~
LoanWhiz treats the deeploans backend as a **canonical data source** for
querying ESMA loan tapes that have already been ingested and normalised by the
deeploans ETL pipelines. The three integration points are:

- ``list_asset_classes()`` — discover available credit types (e.g. "sme").
- ``describe_table(asset_class, table_name)`` — get column metadata for a
  table from deeploans' local schema (no network call required).
- ``sample_rows(asset_class, table_name, n)`` — fetch preview rows from the
  live API endpoint ``GET /api/v1/{credit_type}/{table_name}?limit=N``.

Fallback strategy
~~~~~~~~~~~~~~~~~
The deeploans backend is a locally-running FastAPI server (default port 8000).
It will not be available in all environments — notably the demo environment on
10 June 2026. When the backend is unreachable, every method returns ``None``
and logs a clear message directing the caller to use ``green_lion.py`` instead,
which loads Green Lion 2026-1 data directly from HuggingFace.

API path reference (from deeploans/mcp-server/deeploans_mcp/server.py)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
- Health probe:  ``GET /openapi.json`` (presence test; fast, unauthenticated)
- Sample rows:   ``GET /api/v1/{credit_type}/{table_name}?limit={n}&offset=0``
- Table listing: parse ``paths`` from ``GET /openapi.json``
- Table schema:  served from local ``tables_with_filters.json`` (not via API)
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Asset classes supported by the deeploans ETL platform.
# Sourced from deeploans/mcp-server/deeploans_mcp/server.py → get_asset_classes().
DEEPLOANS_ASSET_CLASSES: list[str] = [
    "sme",
    "residential_mortgages",
    "consumer_lending",
    "auto_loans",
]

_FALLBACK_MSG = (
    "deeploans backend not reachable at {base_url!r}. "
    "Use loanwhiz.data.green_lion to load Green Lion 2026-1 data from HuggingFace."
)


def _normalize(value: str) -> str:
    """Lower-case and strip whitespace — mirrors deeploans' _normalize_identifier."""
    return value.strip().lower()


class DeepLoansClient:
    """Thin HTTP client wrapping the deeploans FastAPI backend.

    Parameters
    ----------
    base_url:
        Root URL of the running deeploans backend. Defaults to the standard
        local development address.
    timeout:
        Connection timeout in seconds. Kept short so an offline backend fails
        fast rather than blocking the caller.
    api_key:
        Optional Algoritmica API key forwarded as the ``x-algoritmica-api-key``
        header. Not required for local / development instances.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        timeout: float = 5.0,
        api_key: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._headers: dict[str, str] = {"Accept": "application/json"}
        if api_key:
            self._headers["x-algoritmica-api-key"] = api_key

    # ------------------------------------------------------------------
    # Reachability probe
    # ------------------------------------------------------------------

    def _is_reachable(self) -> bool:
        """Return True if the deeploans backend responds to a quick health probe.

        Uses ``GET /openapi.json`` as the probe endpoint — it is unauthenticated
        and always present on a running deeploans FastAPI instance.
        """
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(
                    f"{self.base_url}/openapi.json",
                    headers=self._headers,
                )
                return response.status_code < 500
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError):
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_asset_classes(self) -> list[str] | None:
        """Return the asset classes supported by deeploans.

        Returns the static list from deeploans' platform metadata without
        making an API call (the list is defined in deeploans source and does
        not change at runtime). Validates that the backend is reachable first
        so callers get a clear signal when it is not.

        Returns
        -------
        list[str] | None
            Asset class identifiers (e.g. ``["sme", "residential_mortgages"]``),
            or ``None`` when the backend is not reachable.
        """
        if not self._is_reachable():
            logger.warning(_FALLBACK_MSG.format(base_url=self.base_url))
            return None
        return list(DEEPLOANS_ASSET_CLASSES)

    def list_tables(self, asset_class: str) -> list[str] | None:
        """List tables available for an asset class by parsing the OpenAPI spec.

        Parameters
        ----------
        asset_class:
            Credit type to enumerate (e.g. ``"sme"``).

        Returns
        -------
        list[str] | None
            Sorted table names, or ``None`` when the backend is not reachable.
        """
        normalized = _normalize(asset_class)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(
                    f"{self.base_url}/openapi.json",
                    headers=self._headers,
                )
                response.raise_for_status()
                payload: dict[str, Any] = response.json()
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError):
            logger.warning(_FALLBACK_MSG.format(base_url=self.base_url))
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning("deeploans returned HTTP %s: %s", exc.response.status_code, exc)
            return None

        tables = sorted(
            {
                parts[3]
                for path in payload.get("paths", {})
                if len(parts := path.strip("/").split("/")) >= 4
                and parts[0] == "api"
                and parts[1] == "v1"
                and parts[2] == normalized
            }
        )
        return tables if tables else []

    def describe_table(
        self, asset_class: str, table_name: str
    ) -> dict[str, Any] | None:
        """Fetch column and filterability metadata for a table.

        The deeploans MCP server's ``describe_table`` reads from a local
        ``tables_with_filters.json`` schema file — it does not make a live API
        call. This client therefore proxies the request through the backend's
        OpenAPI spec to confirm reachability, then delegates to the deeploans
        MCP endpoint if one is exposed, or returns a minimal descriptor from
        the live endpoint's response headers.

        In practice the deeploans backend does not expose a standalone
        ``/describe`` REST endpoint; metadata lives in the local MCP server
        process. This method therefore returns the column names inferred from
        a zero-row sample request, with an explicit note that full schema
        metadata requires the MCP server.

        Parameters
        ----------
        asset_class:
            Credit type (e.g. ``"sme"``).
        table_name:
            Table identifier (e.g. ``"loans"``).

        Returns
        -------
        dict[str, Any] | None
            ``{"asset_class": ..., "table_name": ..., "columns": [...],
            "note": "..."}`` or ``None`` when the backend is unreachable.
        """
        normalized_ac = _normalize(asset_class)
        normalized_tn = _normalize(table_name)
        url = (
            f"{self.base_url}/api/v1/{normalized_ac}/{normalized_tn}"
            "?limit=1&offset=0"
        )
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, headers=self._headers)
                response.raise_for_status()
                payload = response.json()
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError):
            logger.warning(_FALLBACK_MSG.format(base_url=self.base_url))
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning("deeploans returned HTTP %s: %s", exc.response.status_code, exc)
            return None

        rows: list[dict[str, Any]] = (
            payload if isinstance(payload, list) else payload.get("data", [])
        )
        columns: list[str] = list(rows[0].keys()) if rows else []
        return {
            "asset_class": normalized_ac,
            "table_name": normalized_tn,
            "columns": columns,
            "column_count": len(columns),
            "note": (
                "Column list inferred from a live 1-row sample. For full schema "
                "metadata (types, filterability) use the deeploans MCP server's "
                "describe_table tool, which reads tables_with_filters.json locally."
            ),
        }

    def sample_rows(
        self,
        asset_class: str,
        table_name: str,
        n: int = 10,
    ) -> list[dict[str, Any]] | None:
        """Fetch up to *n* sample rows from a deeploans table.

        Maps directly to the deeploans API endpoint::

            GET /api/v1/{credit_type}/{table_name}?limit={n}&offset=0

        Parameters
        ----------
        asset_class:
            Credit type (e.g. ``"sme"``).
        table_name:
            Table identifier (e.g. ``"loans"``).
        n:
            Maximum number of rows to return. Clamped to [1, 100].

        Returns
        -------
        list[dict[str, Any]] | None
            List of row dicts, or ``None`` when the backend is unreachable.
        """
        normalized_ac = _normalize(asset_class)
        normalized_tn = _normalize(table_name)
        row_limit = max(1, min(int(n), 100))
        url = (
            f"{self.base_url}/api/v1/{normalized_ac}/{normalized_tn}"
            f"?limit={row_limit}&offset=0"
        )
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(url, headers=self._headers)
                response.raise_for_status()
                payload = response.json()
        except (httpx.ConnectError, httpx.TimeoutException, httpx.TransportError):
            logger.warning(_FALLBACK_MSG.format(base_url=self.base_url))
            return None
        except httpx.HTTPStatusError as exc:
            logger.warning("deeploans returned HTTP %s: %s", exc.response.status_code, exc)
            return None

        rows: list[dict[str, Any]] = (
            payload if isinstance(payload, list) else payload.get("data", [])
        )
        return rows if isinstance(rows, list) else []
