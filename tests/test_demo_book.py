"""The committed demo book is traceable to the builder that produced it (#571)."""

from __future__ import annotations

import json

import pytest

from loanwhiz.primitives.capital_structure import CapitalStructure
from loanwhiz.data import demo_book
from loanwhiz.domain.position import PositionProvenance, UnplaceablePosition


class TestTheBookRegenerates:
    def test_book_regenerates(self) -> None:
        """The committed bytes are exactly ``render(build_book())``.

        This is what makes the book *evidence* rather than an assertion: a row
        edited by hand, or a builder changed without regenerating, reds here.
        #484 committed its fit spec beside its pools for the same reason.
        """
        assert demo_book.DEMO_BOOK_PATH.exists(), (
            f"{demo_book.DEMO_BOOK_PATH} is missing; regenerate it with "
            "`python -m loanwhiz.data.demo_book --write`"
        )
        committed = demo_book.DEMO_BOOK_PATH.read_text(encoding="utf-8")
        assert committed == demo_book.render(demo_book.build_book()), (
            "the committed demo book differs from a fresh build. Regenerate it "
            "with `python -m loanwhiz.data.demo_book --write` — do not hand-edit "
            "the JSON, which is what makes it untraceable to its construction."
        )

    def test_the_build_is_deterministic(self) -> None:
        """Two builds agree — no timestamp, no ordering left to chance."""
        assert demo_book.render(demo_book.build_book()) == demo_book.render(
            demo_book.build_book()
        )

    def test_the_committed_record_is_valid_json_at_the_declared_version(self) -> None:
        record = demo_book.load_demo_book()
        assert record["format_version"] == demo_book.FORMAT_VERSION


class TestTheBookIsLabelledOnEveryRow:
    def test_the_book_is_not_a_real_holding(self) -> None:
        assert demo_book.build_book().describes_a_real_holding is False
        assert demo_book.load_demo_book()["describes_a_real_holding"] is False

    def test_every_committed_position_states_its_kind_and_disclosure(self) -> None:
        """Not one row relies on the book-level flag to say what it is."""
        for position in demo_book.load_demo_book()["positions"]:
            assert position["provenance"] == PositionProvenance.ILLUSTRATIVE.value
            assert position["disclosure"] == PositionProvenance.ILLUSTRATIVE.disclosure

    def test_every_spec_entry_declares_its_own_provenance(self) -> None:
        """Omitting it must raise, never default to illustrative."""
        for entry in demo_book.BOOK_SPEC:
            assert "provenance" in entry


class TestTheBookExercisesTheGrammar:
    def test_a_split_class_resolves_to_every_strip(self) -> None:
        """The headline case: ``class_b`` on Cairn is B-1 and B-2, not a string miss."""
        positions = {p.deal_id: p for p in demo_book.build_book().positions}
        assert positions["cairn-clo-xvii"].strips == ("class_b_1", "class_b_2")

    def test_a_whole_class_resolves_to_itself(self) -> None:
        positions = {p.deal_id: p for p in demo_book.build_book().positions}
        assert positions["green-lion-2023-1"].strips == ("class_b",)

    def test_every_position_resolves_to_names_its_deal_actually_carries(self) -> None:
        """No strip is invented: each resolves to a name in that deal's own stack."""
        structures = demo_book.capital_structures()
        for position in demo_book.build_book().positions:
            stack = set(structures[position.deal_id].names)
            assert set(position.strips) <= stack
            assert position.strips, "a placed position always names at least one strip"


class TestTheBuilderRefuses:
    def test_a_spec_naming_an_unregistered_deal_fails_the_build(self) -> None:
        """A demo book quietly missing a row is worse than one that fails to build."""
        with pytest.raises(UnplaceablePosition, match="no capital structure is available"):
            demo_book.build_book(registry={})

    def test_a_registered_deal_with_no_seed_is_refused_by_its_own_cause(self) -> None:
        """Registered-but-unseeded must not be reported as unregistered (#549).

        Both end up absent from the placement universe, so `place` cannot tell
        them apart. `build_book` knows the registry and must say which it is.
        """
        with pytest.raises(UnplaceablePosition, match="IS registered but no committed seed"):
            demo_book.build_book(
                registry={
                    e["deal_id"]: {"deal_name": "No Such Deal That Was Ever Seeded"}
                    for e in demo_book.BOOK_SPEC
                }
            )

    def test_the_placement_universe_is_the_registry(self) -> None:
        """Every deal placed against comes from the registry, not a local list."""
        structures = demo_book.capital_structures()
        assert structures, "no registered deal resolved to a capital structure"
        for deal_id, structure in structures.items():
            assert isinstance(structure, CapitalStructure)
            assert structure.names, f"{deal_id} resolved to an empty stack"

    def test_a_deal_with_no_seed_is_omitted_rather_than_emptied(self) -> None:
        """An empty stack would refuse every holding for the wrong reason (#457)."""
        structures = demo_book.capital_structures(
            registry={"ghost": {"deal_name": "No Such Deal That Was Ever Seeded"}}
        )
        assert structures == {}


class TestTheCommittedFileMatchesTheBuiltBook:
    def test_the_records_agree_position_for_position(self) -> None:
        built = demo_book.build_book().to_record()["positions"]
        committed = demo_book.load_demo_book()["positions"]
        assert committed == built

    def test_the_committed_file_ends_with_a_newline(self) -> None:
        """A trailing newline, so the file is a well-formed text file in git."""
        assert demo_book.DEMO_BOOK_PATH.read_text(encoding="utf-8").endswith("\n")

    def test_the_committed_file_parses_as_the_shape_it_declares(self) -> None:
        record = json.loads(demo_book.DEMO_BOOK_PATH.read_text(encoding="utf-8"))
        assert record["name"] == demo_book.BOOK_NAME
        assert len(record["positions"]) == len(demo_book.BOOK_SPEC)
