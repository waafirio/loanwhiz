"""A holding resolves against the deal's real structure, or is refused (#571)."""

from __future__ import annotations

from datetime import date

import pytest

from loanwhiz.primitives.capital_structure import CapitalStructure, resolve_strips
from loanwhiz.domain.position import (
    Book,
    Position,
    PositionProvenance,
    UnplaceablePosition,
)

AS_OF = date(2026, 4, 30)


def _structure(*names: str) -> CapitalStructure:
    return CapitalStructure.from_engine_mapping({f"{n}_balance": 100.0 for n in names})


#: Cairn's real shape — Class B sold in two strips.
CAIRN = _structure("class_a", "class_b_1", "class_b_2", "class_c")
#: Green Lion's real shape — Class B sold whole.
GREEN_LION = _structure("class_a", "class_b", "class_c")
#: Leone Arancio's real shape — the joined ``A1``/``A2`` spelling (#456).
LEONE = _structure("class_a1", "class_a2", "class_j")

UNIVERSE = {
    "cairn-clo-xvii": CAIRN,
    "green-lion-2023-1": GREEN_LION,
    "leone-arancio-2023-1": LEONE,
}


def _place(deal_id: str, tranche: str, **kw) -> Position:
    return Position.place(
        deal_id=deal_id,
        tranche=tranche,
        size=kw.pop("size", 1_000_000.0),
        as_of=kw.pop("as_of", AS_OF),
        provenance=kw.pop("provenance", PositionProvenance.ILLUSTRATIVE),
        structures=kw.pop("structures", UNIVERSE),
    )


class TestTheOneGrammar:
    """`resolve_strips` is the single class-to-strips resolution (#538/#549)."""

    def test_the_waterfall_interpreter_resolves_through_it(self) -> None:
        """`tranche_strips` delegates rather than carrying its own regex.

        If it grew a second copy this would still pass on the day it was
        written — so the assertion is identity of *result* across the shapes
        that distinguish the two implementations, checked below.
        """
        from loanwhiz.primitives.waterfall_interpreter import WaterfallFunds

        funds = WaterfallFunds(
            tranches=[
                {"name": "class_b_1", "balance": 10.0},
                {"name": "class_b_2", "balance": 5.0},
            ]
        )
        assert [t.name for t in funds.tranche_strips("class_b")] == resolve_strips(
            "class_b", ["class_b_1", "class_b_2"]
        )

    def test_a_class_sold_in_two_strips_resolves_to_both(self) -> None:
        assert _place("cairn-clo-xvii", "class_b").strips == ("class_b_1", "class_b_2")

    def test_a_class_sold_whole_resolves_to_itself(self) -> None:
        assert _place("green-lion-2023-1", "class_b").strips == ("class_b",)

    def test_the_joined_spelling_resolves_too(self) -> None:
        """``Class A1`` slugs to ``class_a1``; matching only ``class_a_1`` would drop it (#456)."""
        assert _place("leone-arancio-2023-1", "class_a").strips == ("class_a1", "class_a2")

    def test_an_exact_class_match_wins_over_its_own_strips(self) -> None:
        """An aggregate row is the class; the series scan is skipped (#538)."""
        both = _structure("class_a", "class_a_1", "class_a_2")
        assert resolve_strips("class_a", both.names) == ["class_a"]

    def test_a_refinanced_strip_is_not_a_series_of_the_class_it_replaces(self) -> None:
        """``class_a_r`` is a replacement, not a second strip — so ``class_a`` places nothing."""
        assert resolve_strips("class_a", ("class_a_r", "class_b")) == []

    def test_a_sibling_classs_strips_are_not_swept_in(self) -> None:
        assert resolve_strips("class_b", CAIRN.names) == ["class_b_1", "class_b_2"]
        assert "class_a" not in resolve_strips("class_b", CAIRN.names)


class TestPlacementRefuses:
    """An unplaceable holding is an error, not a zero (#452/#493)."""

    def test_a_deal_the_registry_does_not_carry_is_refused(self) -> None:
        with pytest.raises(UnplaceablePosition, match="not in the registry"):
            _place("no-such-deal", "class_a")

    def test_a_class_the_structure_cannot_place_is_refused(self) -> None:
        with pytest.raises(UnplaceablePosition, match="places no class"):
            _place("cairn-clo-xvii", "class_z")

    def test_a_refinanced_class_is_refused_rather_than_sized_at_zero(self) -> None:
        """The #452 direction: a nil size would read as 'holds nothing', not 'cannot place'."""
        universe = {"refi": _structure("class_a_r", "class_b")}
        with pytest.raises(UnplaceablePosition, match="places no class"):
            _place("refi", "class_a", structures=universe)

    def test_supplying_the_missing_deal_flips_the_same_call_to_placed(self) -> None:
        """The paired direction (#493) — without it the refusal proves nothing.

        A test asserting only 'this refused' passes for whichever layer refuses
        first and keeps passing with the fix reverted. So: change nothing but
        the one missing input, and assert the same call now succeeds.
        """
        with pytest.raises(UnplaceablePosition):
            _place("late-deal", "class_b", structures=UNIVERSE)

        widened = {**UNIVERSE, "late-deal": CAIRN}
        assert _place("late-deal", "class_b", structures=widened).strips == (
            "class_b_1",
            "class_b_2",
        )

    def test_supplying_the_missing_class_flips_the_same_call_to_placed(self) -> None:
        """The other refusal layer, paired the same way."""
        thin = {"d": _structure("class_a")}
        with pytest.raises(UnplaceablePosition):
            _place("d", "class_b", structures=thin)

        assert _place("d", "class_b", structures={"d": GREEN_LION}).strips == ("class_b",)


class TestTheQualifierIsLoadBearing:
    def test_provenance_is_required(self) -> None:
        """A position that will not say what it is cannot be constructed."""
        with pytest.raises(Exception) as excinfo:
            Position(
                deal_id="d",
                tranche="class_a",
                strips=("class_a",),
                size=1.0,
                as_of=AS_OF,
            )
        assert "provenance" in str(excinfo.value)

    def test_every_member_carries_facts(self) -> None:
        """Total coverage, derived from the enum — never a fixed list (#453/#478)."""
        for kind in PositionProvenance:
            assert isinstance(kind.describes_a_real_holding, bool)
            assert len(kind.disclosure) >= 40

    def test_an_illustrative_position_is_not_a_real_holding(self) -> None:
        assert PositionProvenance.ILLUSTRATIVE.describes_a_real_holding is False
        assert PositionProvenance.CLIENT_STATED.describes_a_real_holding is True

    def test_the_disclosures_are_distinct_per_kind(self) -> None:
        """Two kinds sharing one sentence would make the badge meaningless."""
        texts = {k.disclosure for k in PositionProvenance}
        assert len(texts) == len(list(PositionProvenance))

    def test_every_record_carries_the_kind_and_its_disclosure(self) -> None:
        record = _place("cairn-clo-xvii", "class_b").to_record()
        assert record["provenance"] == "illustrative"
        assert record["disclosure"] == PositionProvenance.ILLUSTRATIVE.disclosure

    def test_a_position_is_frozen(self) -> None:
        position = _place("cairn-clo-xvii", "class_b")
        with pytest.raises(Exception):
            position.provenance = PositionProvenance.CLIENT_STATED  # type: ignore[misc]


class TestBook:
    def test_one_illustrative_position_makes_the_book_illustrative(self) -> None:
        """A total is only as real as its least real input."""
        real = _place("green-lion-2023-1", "class_b", provenance=PositionProvenance.CLIENT_STATED)
        illustrative = _place("cairn-clo-xvii", "class_b")
        assert Book(name="mixed", positions=(real, illustrative)).describes_a_real_holding is False

    def test_a_wholly_client_stated_book_is_a_real_holding(self) -> None:
        real = _place("green-lion-2023-1", "class_b", provenance=PositionProvenance.CLIENT_STATED)
        assert Book(name="real", positions=(real,)).describes_a_real_holding is True

    def test_the_book_surfaces_every_distinct_disclosure(self) -> None:
        real = _place("green-lion-2023-1", "class_b", provenance=PositionProvenance.CLIENT_STATED)
        illustrative = _place("cairn-clo-xvii", "class_b")
        book = Book(name="mixed", positions=(real, illustrative))
        assert set(book.disclosures) == {
            PositionProvenance.CLIENT_STATED.disclosure,
            PositionProvenance.ILLUSTRATIVE.disclosure,
        }

    def test_an_empty_book_is_not_constructible(self) -> None:
        """A book with no positions has nothing to inherit a qualifier from."""
        with pytest.raises(Exception):
            Book(name="empty", positions=())
