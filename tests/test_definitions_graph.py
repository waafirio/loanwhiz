"""Tests for loanwhiz.extraction.definitions_graph.

Two test layers:

1. Unit tests — synthetic data, no network, no LLM.  These validate
   ``DefinedTerm``, ``DefinitionsGraph``, and ``resolve`` / ``resolve_all``
   behaviour using an in-memory graph built directly from fixture data.
   They run in plain ``pytest`` with no external dependencies.

2. Integration tests (``@pytest.mark.integration``) — download (or load
   from cache) the Green Lion 2026-1 prospectus, run Docling, call Gemini
   2.5 Pro to extract definitions, and assert the real graph properties.
   These tests are automatically skipped when:
   - The cache file is absent AND network is unavailable.
   - ``httpx`` or ``docling`` are not importable.
   - The Gemini call fails (e.g. missing GCP credentials).

   They are safe for CI environments without internet access: decorate
   the test class or individual tests with ``@pytest.mark.integration``
   and run CI with ``pytest -m 'not integration'``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loanwhiz.extraction.definitions_graph import (
    DefinedTerm,
    DefinitionsGraph,
    _graph_from_json,
    _graph_to_json,
    load_or_extract,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Cache dir relocated to data/extraction_cache in #152; derive from the module
# under test rather than hardcoding the old /tmp literal.
from loanwhiz.extraction.definitions_graph import _CACHE_DIR

_CACHE_PATH = _CACHE_DIR / "definitions_green_lion_2026_1_prospectus.json"
_PROSPECTUS_URL = (
    "https://huggingface.co/datasets/Algoritmica/green-lion-2026"
    "/resolve/main/Hackathon_Data/green-lion-2026-1-prospectus.pdf"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_graph(*terms: tuple[str, str, str]) -> DefinitionsGraph:
    """Build a DefinitionsGraph from (term, definition, page_or_section) tuples."""
    graph = DefinitionsGraph()
    for term, definition, page_or_section in terms:
        graph.terms[term] = DefinedTerm(
            term=term,
            definition=definition,
            page_or_section=page_or_section,
            excerpt=definition[:200],
        )
    return graph


def _get_or_build_graph() -> DefinitionsGraph | None:
    """Return a real DefinitionsGraph for the Green Lion prospectus.

    Loads from the disk cache if available; otherwise calls
    ``load_or_extract`` which will download the PDF, run Docling, and
    invoke Gemini. Returns ``None`` on any failure so callers can skip.
    """
    try:
        return load_or_extract(_PROSPECTUS_URL, cache_path=str(_CACHE_PATH))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Unit tests — no network, no LLM
# ---------------------------------------------------------------------------


class TestDefinedTerm:
    """DefinedTerm is a plain dataclass — basic smoke tests."""

    def test_fields(self) -> None:
        dt = DefinedTerm(
            term="Available Revenue Funds",
            definition="Available Revenue Funds means the aggregate of ...",
            page_or_section="Section 9.1",
            excerpt="Available Revenue Funds means the aggregate of ..."[:200],
        )
        assert dt.term == "Available Revenue Funds"
        assert dt.page_or_section == "Section 9.1"
        assert len(dt.excerpt) <= 200

    def test_excerpt_truncation(self) -> None:
        long_def = "X" * 500
        dt = DefinedTerm(
            term="Foo",
            definition=long_def,
            page_or_section="Section 9.1",
            excerpt=long_def[:200],
        )
        assert len(dt.excerpt) == 200


class TestDefinitionsGraph:
    """DefinitionsGraph unit tests — purely synthetic data."""

    def _graph(self) -> DefinitionsGraph:
        return _make_graph(
            (
                "Available Revenue Funds",
                "Available Revenue Funds means the aggregate of interest and principal receipts.",
                "Section 9.1",
            ),
            (
                "Notes Payment Date",
                "Notes Payment Date means the 25th calendar day of each month.",
                "Section 9.1",
            ),
            (
                "Sequential Pay Trigger",
                "Sequential Pay Trigger means an event that switches the waterfall to sequential.",
                "Section 9.1",
            ),
            (
                "Class A Notes",
                "Class A Notes means the most senior class of notes.",
                "Section 9.1",
            ),
        )

    def test_len(self) -> None:
        graph = self._graph()
        assert len(graph) == 4

    def test_len_empty(self) -> None:
        assert len(DefinitionsGraph()) == 0

    # --- resolve exact match ---

    def test_resolve_exact(self) -> None:
        graph = self._graph()
        result = graph.resolve("Available Revenue Funds")
        assert result is not None
        assert result.term == "Available Revenue Funds"

    def test_resolve_case_insensitive_lower(self) -> None:
        graph = self._graph()
        result = graph.resolve("available revenue funds")
        assert result is not None
        assert result.term == "Available Revenue Funds"

    def test_resolve_case_insensitive_upper(self) -> None:
        graph = self._graph()
        result = graph.resolve("AVAILABLE REVENUE FUNDS")
        assert result is not None
        assert result.term == "Available Revenue Funds"

    def test_resolve_case_insensitive_mixed(self) -> None:
        graph = self._graph()
        result = graph.resolve("Notes PAYMENT date")
        assert result is not None
        assert result.term == "Notes Payment Date"

    # --- resolve "the " prefix stripping ---

    def test_resolve_strips_the_lowercase(self) -> None:
        graph = self._graph()
        result = graph.resolve("the Available Revenue Funds")
        assert result is not None
        assert result.term == "Available Revenue Funds"

    def test_resolve_strips_The_uppercase(self) -> None:
        graph = self._graph()
        result = graph.resolve("The Notes Payment Date")
        assert result is not None
        assert result.term == "Notes Payment Date"

    def test_resolve_strips_the_case_insensitive(self) -> None:
        graph = self._graph()
        result = graph.resolve("the sequential pay trigger")
        assert result is not None
        assert result.term == "Sequential Pay Trigger"

    # --- resolve partial match ---

    def test_resolve_partial_key_in_query(self) -> None:
        """Query contains the key as substring — should match."""
        graph = self._graph()
        # "Class A Notes outstanding" contains "Class A Notes"
        result = graph.resolve("Class A Notes outstanding")
        assert result is not None
        assert result.term == "Class A Notes"

    def test_resolve_partial_query_in_key(self) -> None:
        """Key contains the query as substring — should match."""
        graph = self._graph()
        # "Revenue Funds" is a substring of "Available Revenue Funds"
        result = graph.resolve("Revenue Funds")
        assert result is not None

    # --- resolve unknown ---

    def test_resolve_unknown_returns_none(self) -> None:
        graph = self._graph()
        assert graph.resolve("Nonexistent Term XYZ") is None

    def test_resolve_empty_string_returns_none(self) -> None:
        graph = self._graph()
        assert graph.resolve("") is None

    # --- resolve_all ---

    def test_resolve_all_finds_terms(self) -> None:
        graph = self._graph()
        text = (
            "The Available Revenue Funds shall be applied on each Notes Payment Date "
            "in the following order. If a Sequential Pay Trigger has occurred, "
            "principal is applied sequentially."
        )
        found = graph.resolve_all(text)
        assert "Available Revenue Funds" in found
        assert "Notes Payment Date" in found
        assert "Sequential Pay Trigger" in found

    def test_resolve_all_empty_text(self) -> None:
        graph = self._graph()
        assert graph.resolve_all("") == {}

    def test_resolve_all_no_match(self) -> None:
        graph = self._graph()
        assert graph.resolve_all("The quick brown fox jumps over the lazy dog.") == {}

    def test_resolve_all_returns_definedterm_objects(self) -> None:
        graph = self._graph()
        found = graph.resolve_all("Available Revenue Funds were applied.")
        assert isinstance(found["Available Revenue Funds"], DefinedTerm)

    # --- definition contains term ---

    def test_resolve_definition_contains_term(self) -> None:
        """A resolved definition should mention the term (or at least be non-empty)."""
        graph = self._graph()
        result = graph.resolve("Notes Payment Date")
        assert result is not None
        assert result.definition  # non-empty
        # In our synthetic fixture, definition starts with the term
        assert "Notes Payment Date" in result.definition


class TestDefinitionsGraphLink:
    """DefinitionsGraph.link — the ordered, de-duplicated linking surface (#395).

    ``link`` is the list-returning sibling of ``resolve_all``: the assembler and
    interpreter call it to turn a raw step condition / trigger metric into the
    structured defined-term links that let conditional waterfall prose resolve
    against its trigger instead of silently no-op'ing.
    """

    def _graph(self) -> DefinitionsGraph:
        return _make_graph(
            (
                "Available Revenue Funds",
                "Available Revenue Funds means interest and principal receipts.",
                "Section 9.1",
            ),
            (
                "Notes Payment Date",
                "Notes Payment Date means the 25th calendar day of each month.",
                "Section 9.1",
            ),
            (
                "Sequential Pay Trigger",
                "Sequential Pay Trigger switches the waterfall to sequential.",
                "Section 9.1",
            ),
        )

    def test_link_returns_referenced_terms(self) -> None:
        graph = self._graph()
        linked = graph.link("if a Sequential Pay Trigger has occurred")
        assert [t.term for t in linked] == ["Sequential Pay Trigger"]

    def test_link_returns_definedterm_objects(self) -> None:
        graph = self._graph()
        linked = graph.link("Available Revenue Funds were applied.")
        assert linked and isinstance(linked[0], DefinedTerm)

    def test_link_is_ordered_by_first_appearance(self) -> None:
        """Terms come back in document order of first appearance, not graph order."""
        graph = self._graph()
        # Notes Payment Date appears in the text BEFORE Sequential Pay Trigger,
        # even though the graph inserts Sequential Pay Trigger last.
        text = (
            "On each Notes Payment Date, if a Sequential Pay Trigger has "
            "occurred, the Available Revenue Funds are applied sequentially."
        )
        linked = graph.link(text)
        assert [t.term for t in linked] == [
            "Notes Payment Date",
            "Sequential Pay Trigger",
            "Available Revenue Funds",
        ]

    def test_link_deduplicates(self) -> None:
        graph = self._graph()
        text = "Sequential Pay Trigger ... and again the Sequential Pay Trigger."
        linked = graph.link(text)
        assert [t.term for t in linked] == ["Sequential Pay Trigger"]

    def test_link_case_insensitive(self) -> None:
        graph = self._graph()
        linked = graph.link("if a SEQUENTIAL PAY TRIGGER has occurred")
        assert [t.term for t in linked] == ["Sequential Pay Trigger"]

    def test_link_empty_text_returns_empty_list(self) -> None:
        graph = self._graph()
        assert graph.link("") == []
        assert graph.link("   ") == []

    def test_link_no_match_returns_empty_list(self) -> None:
        graph = self._graph()
        assert graph.link("The quick brown fox.") == []

    def test_link_on_empty_graph(self) -> None:
        assert DefinitionsGraph().link("Sequential Pay Trigger") == []


class TestJsonRoundTrip:
    """_graph_to_json / _graph_from_json round-trip."""

    def test_round_trip(self) -> None:
        original = _make_graph(
            ("Foo Term", "Foo means bar.", "Section 9.1"),
            ("Bar Term", "Bar means baz.", "Section 9.1"),
        )
        serialised = _graph_to_json(original)
        data = json.loads(serialised)
        restored = _graph_from_json(data)

        assert len(restored) == len(original)
        assert "Foo Term" in restored.terms
        assert restored.terms["Foo Term"].definition == "Foo means bar."

    def test_empty_graph_round_trip(self) -> None:
        graph = DefinitionsGraph()
        data = json.loads(_graph_to_json(graph))
        restored = _graph_from_json(data)
        assert len(restored) == 0

    def test_excerpt_preserved(self) -> None:
        long_def = "A" * 300
        original = _make_graph(("Long", long_def, "Section 9.1"))
        # Excerpt is truncated at construction time in extract_definitions;
        # in this test we simulate by setting it explicitly
        original.terms["Long"].excerpt = long_def[:200]
        data = json.loads(_graph_to_json(original))
        restored = _graph_from_json(data)
        assert len(restored.terms["Long"].excerpt) == 200


# ---------------------------------------------------------------------------
# Integration tests — require network + Docling + Gemini
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGreenLionDefinitionsGraph:
    """Integration tests against the real Green Lion 2026-1 prospectus.

    These tests:
    - Load the cached definitions if available at ``_CACHE_PATH``.
    - Extract fresh from the prospectus (download + Docling + Gemini) if not.
    - Skip automatically if anything goes wrong (network, credentials, etc.).

    Run: ``pytest -m integration tests/test_definitions_graph.py``
    Skip in CI: ``pytest -m 'not integration'``
    """

    @pytest.fixture(scope="class")
    def graph(self) -> DefinitionsGraph:
        g = _get_or_build_graph()
        if g is None:
            pytest.skip(
                "Green Lion definitions graph unavailable "
                "(no cache, no network, or Gemini credentials absent)"
            )
        return g

    def test_definitions_graph_length(self, graph: DefinitionsGraph) -> None:
        """Green Lion 2026-1 has a rich Definitions section — expect > 50 terms."""
        assert len(graph) > 50, (
            f"Expected > 50 defined terms, got {len(graph)}. "
            "Check that the full Definitions section was sent to Gemini."
        )

    def test_resolve_available_revenue_funds(self, graph: DefinitionsGraph) -> None:
        """'Available Revenue Funds' is a core waterfall term — must resolve."""
        result = graph.resolve("Available Revenue Funds")
        assert result is not None, "Expected 'Available Revenue Funds' to resolve"
        assert result.definition, "Definition must be non-empty"

    def test_resolve_notes_payment_date(self, graph: DefinitionsGraph) -> None:
        """'Notes Payment Date' is used throughout payment mechanics — must resolve."""
        result = graph.resolve("Notes Payment Date")
        assert result is not None, "Expected 'Notes Payment Date' to resolve"
        assert result.definition, "Definition must be non-empty"

    def test_resolve_sequential_pay_trigger(self, graph: DefinitionsGraph) -> None:
        """'Sequential Pay Trigger' is a key covenant term — must resolve."""
        result = graph.resolve("Sequential Pay Trigger")
        assert result is not None, "Expected 'Sequential Pay Trigger' to resolve"
        assert result.definition, "Definition must be non-empty"

    def test_resolve_case_insensitive_real(self, graph: DefinitionsGraph) -> None:
        """Case-insensitive lookup works on the real graph."""
        lower = graph.resolve("available revenue funds")
        upper = graph.resolve("Available Revenue Funds")
        assert lower is not None
        assert upper is not None
        assert lower.term == upper.term

    def test_resolve_the_prefix_real(self, graph: DefinitionsGraph) -> None:
        """'the Available Revenue Funds' resolves to the same term."""
        with_the = graph.resolve("the Available Revenue Funds")
        without = graph.resolve("Available Revenue Funds")
        assert with_the is not None
        assert without is not None
        assert with_the.term == without.term

    def test_resolve_unknown_returns_none_real(self, graph: DefinitionsGraph) -> None:
        """Nonsense term resolves to None on the real graph."""
        assert graph.resolve("ZZZNONSENSETERMXYZ999") is None

    def test_definitions_have_page_or_section(self, graph: DefinitionsGraph) -> None:
        """Every extracted term should have a page_or_section reference."""
        for key, dt in graph.terms.items():
            assert dt.page_or_section, (
                f"Term '{key}' has an empty page_or_section"
            )

    def test_excerpts_at_most_200_chars(self, graph: DefinitionsGraph) -> None:
        """Excerpts must not exceed 200 characters."""
        for key, dt in graph.terms.items():
            assert len(dt.excerpt) <= 200, (
                f"Term '{key}' has excerpt of {len(dt.excerpt)} chars (limit 200)"
            )

    def test_resolve_all_finds_waterfall_terms(self, graph: DefinitionsGraph) -> None:
        """resolve_all should find known waterfall terms in a sample text."""
        sample = (
            "The Available Revenue Funds shall be applied on each Notes Payment Date "
            "in the following priority: firstly to pay senior expenses, secondly to "
            "pay interest on the Class A Notes. If a Sequential Pay Trigger is "
            "outstanding, principal is distributed sequentially."
        )
        found = graph.resolve_all(sample)
        # At least two of our three key terms should appear
        key_terms = {"Available Revenue Funds", "Notes Payment Date", "Sequential Pay Trigger"}
        matched = key_terms.intersection(found.keys())
        assert len(matched) >= 2, (
            f"Expected at least 2 key terms in sample text; found: {matched}"
        )

    def test_cache_file_is_created(self, graph: DefinitionsGraph) -> None:
        """After extraction, the cache file should exist on disk."""
        assert _CACHE_PATH.exists(), (
            f"Cache file not found at {_CACHE_PATH}. "
            "load_or_extract should have written it after extraction."
        )

    def test_cache_reload_matches_graph(self, graph: DefinitionsGraph) -> None:
        """Loading from cache should reproduce the same graph."""
        if not _CACHE_PATH.exists():
            pytest.skip("Cache not available")
        reloaded = load_or_extract(_PROSPECTUS_URL, cache_path=str(_CACHE_PATH))
        assert len(reloaded) == len(graph)
        for key in graph.terms:
            assert key in reloaded.terms
