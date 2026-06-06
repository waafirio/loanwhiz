"""Integration tests for the deeploans client.

Markers & skip conditions
--------------------------
The live-backend tests in this module are tagged ``@pytest.mark.integration``,
so the default suite (``pytest -m "not integration and not slow"``) deselects
them — they never run, and never error, in the standard CI/demo run.

Even under the integration marker they skip cleanly rather than error when the
deeploans backend is not reachable at ``http://localhost:8000``. "Reachable"
here means a *genuine* deeploans backend: ``DeepLoansClient._is_reachable()``
confirms ``/openapi.json`` is a deeploans-shaped OpenAPI document, so an
unrelated process bound to :8000 (a stray dev server, a static file host) is
correctly treated as "not deeploans" → skip, not error.

``TestFallback`` mocks an unreachable client and makes no network calls, so it
is deliberately *not* marked integration and runs in the default suite.

To run against a live backend::

    # Start the deeploans FastAPI backend first (see deeploans README), then:
    pytest tests/test_deeploans_integration.py -v -m integration

The ``DeepLoansClient`` contract confirmed here:
- ``list_asset_classes()`` returns a list of strings.
- ``list_tables(asset_class)`` returns a list of strings.
- ``describe_table(asset_class, table_name)`` returns a dict with "columns" key.
- ``sample_rows(asset_class, table_name, n)`` returns a list of dicts with
  length <= n.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from loanwhiz.data.deeploans_client import DeepLoansClient, parse_deeploans_url

# ---------------------------------------------------------------------------
# Module-level reachability check — evaluated once at collection time.
# All live-backend tests are skipped if a genuine deeploans backend is not up.
#
# ``_is_reachable()`` verifies *identity*, not mere liveness: a foreign process
# answering on :8000 (e.g. a static file server returning 404/HTML) does not
# satisfy the deeploans OpenAPI-shape check, so it reports unreachable and these
# tests skip cleanly rather than erroring against the wrong service.
# ---------------------------------------------------------------------------

_BACKEND_REACHABLE = DeepLoansClient()._is_reachable()

# Combined with @pytest.mark.integration on each live-backend class below.
# (TestFallback carries neither marker — it mocks a dead client, no network.)
_skip_if_unreachable = pytest.mark.skipif(
    not _BACKEND_REACHABLE,
    reason=(
        "deeploans backend not reachable at http://localhost:8000 "
        "(no process, or the process there is not a deeploans API). "
        "Start the deeploans backend to run these integration tests."
    ),
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client() -> DeepLoansClient:
    """Return a shared DeepLoansClient for all tests in this module."""
    return DeepLoansClient()


@pytest.fixture(scope="module")
def asset_class(client: DeepLoansClient) -> str:
    """Return the first available asset class for use in table-level tests.

    Skips (does not error) if the backend yields nothing usable — e.g. the
    process on :8000 turned out not to be deeploans, or a deeploans instance
    with no data. A bare ``assert`` here would surface as a fixture *error*; a
    skip keeps the suite green.
    """
    classes = client.list_asset_classes()
    if not classes:
        pytest.skip(
            "deeploans backend at http://localhost:8000 returned no asset "
            "classes — not a usable deeploans backend; skipping live tests."
        )
    return classes[0]


@pytest.fixture(scope="module")
def table_name(client: DeepLoansClient, asset_class: str) -> str:
    """Return the first available table for the chosen asset class.

    Skips (does not error) when the backend exposes no tables for the chosen
    asset class, for the same reason as the ``asset_class`` fixture.
    """
    tables = client.list_tables(asset_class)
    if not tables:
        pytest.skip(
            f"deeploans backend returned no tables for asset class "
            f"{asset_class!r}; skipping live tests."
        )
    return tables[0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
@_skip_if_unreachable
class TestListAssetClasses:
    def test_returns_list(self, client: DeepLoansClient) -> None:
        result = client.list_asset_classes()
        assert isinstance(result, list), f"Expected list, got {type(result)}"

    def test_list_is_non_empty(self, client: DeepLoansClient) -> None:
        result = client.list_asset_classes()
        assert result is not None and len(result) > 0

    def test_elements_are_strings(self, client: DeepLoansClient) -> None:
        result = client.list_asset_classes()
        assert result is not None
        for item in result:
            assert isinstance(item, str), f"Expected str element, got {type(item)}: {item!r}"

    def test_sme_is_present(self, client: DeepLoansClient) -> None:
        """SME is the primary asset class used with Green Lion data."""
        result = client.list_asset_classes()
        assert result is not None
        assert "sme" in result, f"Expected 'sme' in asset classes, got: {result}"


@pytest.mark.integration
@_skip_if_unreachable
class TestListTables:
    def test_returns_list(self, client: DeepLoansClient, asset_class: str) -> None:
        result = client.list_tables(asset_class)
        assert isinstance(result, list), f"Expected list, got {type(result)}"

    def test_elements_are_strings(self, client: DeepLoansClient, asset_class: str) -> None:
        result = client.list_tables(asset_class)
        assert result is not None
        for item in result:
            assert isinstance(item, str)


@pytest.mark.integration
@_skip_if_unreachable
class TestDescribeTable:
    def test_returns_dict(
        self, client: DeepLoansClient, asset_class: str, table_name: str
    ) -> None:
        result = client.describe_table(asset_class, table_name)
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"

    def test_has_columns_key(
        self, client: DeepLoansClient, asset_class: str, table_name: str
    ) -> None:
        result = client.describe_table(asset_class, table_name)
        assert result is not None
        assert "columns" in result, f"Missing 'columns' key in: {result.keys()}"

    def test_columns_is_list(
        self, client: DeepLoansClient, asset_class: str, table_name: str
    ) -> None:
        result = client.describe_table(asset_class, table_name)
        assert result is not None
        assert isinstance(result["columns"], list)

    def test_reflects_asset_class_and_table(
        self, client: DeepLoansClient, asset_class: str, table_name: str
    ) -> None:
        result = client.describe_table(asset_class, table_name)
        assert result is not None
        assert result.get("asset_class") == asset_class.strip().lower()
        assert result.get("table_name") == table_name.strip().lower()


@pytest.mark.integration
@_skip_if_unreachable
class TestSampleRows:
    def test_returns_list(
        self, client: DeepLoansClient, asset_class: str, table_name: str
    ) -> None:
        result = client.sample_rows(asset_class, table_name, n=3)
        assert isinstance(result, list), f"Expected list, got {type(result)}"

    def test_length_le_n(
        self, client: DeepLoansClient, asset_class: str, table_name: str
    ) -> None:
        n = 3
        result = client.sample_rows(asset_class, table_name, n=n)
        assert result is not None
        assert len(result) <= n, f"Expected <= {n} rows, got {len(result)}"

    def test_rows_are_dicts(
        self, client: DeepLoansClient, asset_class: str, table_name: str
    ) -> None:
        result = client.sample_rows(asset_class, table_name, n=5)
        assert result is not None
        for row in result:
            assert isinstance(row, dict), f"Expected dict row, got {type(row)}: {row!r}"

    def test_n_clamp_upper(
        self, client: DeepLoansClient, asset_class: str, table_name: str
    ) -> None:
        """n is clamped to 100; should not raise even with a large value."""
        result = client.sample_rows(asset_class, table_name, n=999)
        assert isinstance(result, list)


class TestFallback:
    """Verify graceful fallback when pointing at a non-existent backend.

    These tests make no network calls to a *real* deeploans backend — they point
    at a dead port (or mock the reachability probe) — so they run in the default
    suite and never require the backend to be up.
    """

    def test_unreachable_returns_none_for_list_asset_classes(self) -> None:
        dead = DeepLoansClient(base_url="http://localhost:19999", timeout=1.0)
        assert dead.list_asset_classes() is None

    def test_unreachable_returns_none_for_describe_table(self) -> None:
        dead = DeepLoansClient(base_url="http://localhost:19999", timeout=1.0)
        assert dead.describe_table("sme", "loans") is None

    def test_unreachable_returns_none_for_sample_rows(self) -> None:
        dead = DeepLoansClient(base_url="http://localhost:19999", timeout=1.0)
        assert dead.sample_rows("sme", "loans", n=5) is None

    def test_unreachable_returns_none_for_fetch_tape(self) -> None:
        dead = DeepLoansClient(base_url="http://localhost:19999", timeout=1.0)
        assert dead.fetch_tape("sme", "loans") is None


# ---------------------------------------------------------------------------
# parse_deeploans_url — no network; pure URL parsing.
# ---------------------------------------------------------------------------


class TestParseDeeploansUrl:
    """The ``deeploans://{asset_class}/{table_name}`` reference decoder."""

    def test_decodes_asset_class_and_table(self) -> None:
        assert parse_deeploans_url("deeploans://sme/loans") == ("sme", "loans")

    def test_normalises_case_and_whitespace(self) -> None:
        assert parse_deeploans_url("deeploans://SME/Loans") == ("sme", "loans")

    def test_http_url_is_not_a_deeploans_ref(self) -> None:
        assert parse_deeploans_url("https://example.com/tape.csv") is None

    def test_file_url_is_not_a_deeploans_ref(self) -> None:
        assert parse_deeploans_url("file:///tmp/tape.parquet") is None

    def test_missing_table_is_malformed(self) -> None:
        assert parse_deeploans_url("deeploans://sme") is None

    def test_missing_asset_class_is_malformed(self) -> None:
        assert parse_deeploans_url("deeploans:///loans") is None


# ---------------------------------------------------------------------------
# fetch_tape — paging logic exercised with the HTTP boundary mocked, so no
# live deeploans backend is required. Runs in the default suite.
# ---------------------------------------------------------------------------


class _FakeResponse:
    """Minimal httpx.Response stand-in for the mocked client."""

    def __init__(self, payload: list[dict]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:  # pragma: no cover - always 2xx here
        return None

    def json(self) -> list[dict]:
        return self._payload


class _FakeHttpxClient:
    """Stand-in for ``httpx.Client`` that serves canned pages by offset.

    ``pages`` is the full row list; the client slices it by the ``limit``/
    ``offset`` query params so the paging loop sees realistic short final pages.
    """

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def __enter__(self) -> "_FakeHttpxClient":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def get(self, url: str, headers: dict | None = None) -> _FakeResponse:
        # Parse limit/offset out of the query string the client builds.
        from urllib.parse import parse_qs, urlsplit

        qs = parse_qs(urlsplit(url).query)
        limit = int(qs["limit"][0])
        offset = int(qs["offset"][0])
        return _FakeResponse(self._rows[offset : offset + limit])


class TestFetchTape:
    def test_pages_full_table_into_dataframe(self) -> None:
        rows = [{"loan_id": i, "balance": i * 100.0} for i in range(12)]
        client = DeepLoansClient()
        with (
            patch.object(DeepLoansClient, "_is_reachable", return_value=True),
            patch(
                "loanwhiz.data.deeploans_client.httpx.Client",
                return_value=_FakeHttpxClient(rows),
            ),
        ):
            df = client.fetch_tape("sme", "loans", max_rows=100)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 12
        assert df["loan_id"].tolist() == list(range(12))

    def test_respects_max_rows_cap(self) -> None:
        rows = [{"loan_id": i} for i in range(50)]
        client = DeepLoansClient()
        with (
            patch.object(DeepLoansClient, "_is_reachable", return_value=True),
            patch(
                "loanwhiz.data.deeploans_client.httpx.Client",
                return_value=_FakeHttpxClient(rows),
            ),
        ):
            df = client.fetch_tape("sme", "loans", max_rows=10)

        assert df is not None
        assert len(df) == 10

    def test_empty_reachable_table_returns_empty_frame(self) -> None:
        client = DeepLoansClient()
        with (
            patch.object(DeepLoansClient, "_is_reachable", return_value=True),
            patch(
                "loanwhiz.data.deeploans_client.httpx.Client",
                return_value=_FakeHttpxClient([]),
            ),
        ):
            df = client.fetch_tape("sme", "loans")

        assert df is not None
        assert df.empty
