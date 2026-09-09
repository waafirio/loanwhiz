"""Tests for loanwhiz.extraction.truncation — the shared clipping record.

``clip`` is the single slicing path for both extractors that send a prospectus
section to Gemini under a character budget. Before #548 each owned its own
``text[:max_chars]``; one warned, the other said nothing, and the divergence is
exactly what let a waterfall cascade be cut mid-flow in silence.

These are unit tests on a pure function — no network, no LLM, no filesystem.
"""

from __future__ import annotations

from loanwhiz.extraction.truncation import Truncation, clip


class TestClipUnderBudget:
    """The common case: the budget does not bite and nothing is reported."""

    def test_returns_text_unchanged_and_no_record(self) -> None:
        text = "a definitions section well inside the budget"
        kept, truncation = clip("Definitions", text, 10_000)
        assert kept == text
        assert truncation is None

    def test_exactly_at_budget_is_not_truncation(self) -> None:
        """A section the same length as the budget lost nothing.

        Off-by-one here would report a truncation that discarded zero
        characters, which is precisely the false alarm that trains a reader to
        ignore the field.
        """
        text = "x" * 100
        kept, truncation = clip("Definitions", text, 100)
        assert kept == text
        assert truncation is None

    def test_no_budget_means_no_clipping(self) -> None:
        """``None`` / non-positive budgets mean "send it whole"."""
        text = "x" * 5_000
        for budget in (None, 0, -1):
            kept, truncation = clip("Definitions", text, budget)
            assert kept == text
            assert truncation is None

    def test_empty_text(self) -> None:
        kept, truncation = clip("Definitions", "", 100)
        assert kept == ""
        assert truncation is None


class TestClipOverBudget:
    """What the record says when the budget does bite."""

    def test_records_sizes_and_section(self) -> None:
        kept, truncation = clip("1. Definitions", "y" * 250, 100)
        assert len(kept) == 100
        assert truncation is not None
        assert truncation.section_title == "1. Definitions"
        assert truncation.original_chars == 250
        assert truncation.kept_chars == 100

    def test_derived_quantities(self) -> None:
        _, truncation = clip("Definitions", "y" * 200, 50)
        assert truncation is not None
        assert truncation.discarded_chars == 150
        assert truncation.discarded_fraction == 0.75

    def test_last_line_names_where_it_stopped(self) -> None:
        """The stopping point is the field that turns a count into a prefix.

        For an alphabetical glossary this is what tells a caller which terms
        cannot be present, rather than leaving a plausible-looking total. The
        cut lands mid-line far more often than not, so the honest report is the
        partial line the budget stopped inside — the kept text always ends with
        exactly what this field claims.
        """
        body = "\n".join(f'"Term {i}" means something.' for i in range(400))
        kept, truncation = clip("Definitions", body, 300)
        assert truncation is not None
        assert kept.endswith(truncation.last_line)
        assert '"Term 11"' in truncation.last_line
        # A tail line, not the whole kept span.
        assert len(truncation.last_line) <= 120

    def test_last_line_is_the_final_line_when_the_cut_is_clean(self) -> None:
        """On a boundary cut the field names the last whole line seen."""
        body = "alpha\nbravo\ncharlie\n"
        _, truncation = clip("Definitions", body, len("alpha\nbravo\n"))
        assert truncation is not None
        assert truncation.last_line == "bravo"

    def test_discarded_fraction_on_empty_original(self) -> None:
        """Constructed directly rather than via ``clip`` — a cache could hold it.

        A data card rendering this should not have to guard against a division
        by zero it can do nothing about.
        """
        truncation = Truncation(
            section_title="Definitions",
            original_chars=0,
            kept_chars=0,
            last_line="",
        )
        assert truncation.discarded_fraction == 0.0
        assert truncation.discarded_chars == 0


class TestSerialisation:
    """The record has to survive the extractors' on-disk caches."""

    def test_round_trip(self) -> None:
        _, truncation = clip("1. Definitions", "z" * 500, 120)
        assert truncation is not None
        assert Truncation.from_dict(truncation.to_dict()) == truncation

    def test_to_dict_carries_only_stored_fields(self) -> None:
        """Derived values are recomputed on read, so the cache cannot disagree."""
        _, truncation = clip("Definitions", "z" * 500, 120)
        assert truncation is not None
        assert set(truncation.to_dict()) == {
            "section_title",
            "original_chars",
            "kept_chars",
            "last_line",
        }

    def test_absent_key_reads_as_no_record(self) -> None:
        """A cache written before #548 has no ``truncation`` key at all.

        ``.get`` hands ``None`` straight through, and the warm cache must keep
        loading rather than raising.
        """
        assert Truncation.from_dict(None) is None

    def test_unexpected_shape_reads_as_no_record(self) -> None:
        for payload in ("a string", [1, 2, 3], 7):
            assert Truncation.from_dict(payload) is None

    def test_partial_payload_fills_defaults(self) -> None:
        truncation = Truncation.from_dict({"original_chars": 10})
        assert truncation is not None
        assert truncation.original_chars == 10
        assert truncation.kept_chars == 0
        assert truncation.section_title == ""
