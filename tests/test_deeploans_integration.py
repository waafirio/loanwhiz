"""Integration tests for the deeploans client.

Skip condition
--------------
All tests in this module are skipped automatically when the deeploans backend
is not reachable at ``http://localhost:8000``. This is the expected state in CI
and in the demo environment — the tests are opt-in for local development when a
deeploans backend is running.

To run against a live backend::

    # Start the deeploans FastAPI backend first (see deeploans README), then:
    pytest tests/test_deeploans_integration.py -v

The ``DeepLoansClient`` contract confirmed here:
- ``list_asset_classes()`` returns a list of strings.
- ``list_tables(asset_class)`` returns a list of strings.
- ``describe_table(asset_class, table_name)`` returns a dict with "columns" key.
- ``sample_rows(asset_class, table_name, n)`` returns a list of dicts with
  length <= n.
"""

from __future__ import annotations

import pytest

from loanwhiz.data.deeploans_client import DeepLoansClient

# ---------------------------------------------------------------------------
# Module-level reachability check — evaluated once at collection time.
# All tests are skipped if the backend is not up.
# ---------------------------------------------------------------------------

_client = DeepLoansClient()
_BACKEND_REACHABLE = _client._is_reachable()

# Applied to all test *classes* below except TestFallback, which always runs.
_skip_if_unreachable = pytest.mark.skipif(
    not _BACKEND_REACHABLE,
    reason=(
        "deeploans backend not reachable at http://localhost:8000. "
        "Start the backend to run these integration tests."
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
    """Return the first available asset class for use in table-level tests."""
    classes = client.list_asset_classes()
    assert classes, "list_asset_classes() returned empty list despite reachable backend"
    return classes[0]


@pytest.fixture(scope="module")
def table_name(client: DeepLoansClient, asset_class: str) -> str:
    """Return the first available table for the chosen asset class."""
    tables = client.list_tables(asset_class)
    assert tables, f"list_tables({asset_class!r}) returned empty list"
    return tables[0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


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
    """Verify graceful fallback when pointing at a non-existent backend."""

    def test_unreachable_returns_none_for_list_asset_classes(self) -> None:
        dead = DeepLoansClient(base_url="http://localhost:19999", timeout=1.0)
        assert dead.list_asset_classes() is None

    def test_unreachable_returns_none_for_describe_table(self) -> None:
        dead = DeepLoansClient(base_url="http://localhost:19999", timeout=1.0)
        assert dead.describe_table("sme", "loans") is None

    def test_unreachable_returns_none_for_sample_rows(self) -> None:
        dead = DeepLoansClient(base_url="http://localhost:19999", timeout=1.0)
        assert dead.sample_rows("sme", "loans", n=5) is None
