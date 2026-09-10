"""``GET /book`` — a holder's positions, with per-field facts or refusals (#572).

The endpoint's whole contract is that a fact the platform cannot resolve
refuses **in place**: the position, and the rest of the book, are served
anyway. So the tests below are paired on purpose — a refusal asserted on its
own proves nothing, because whichever layer refuses first keeps the assertion
green with the fix reverted (#493). Every refusal here has a case beside it
that resolves.
"""

from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from loanwhiz.api import app
from loanwhiz.api import main as api_main
from loanwhiz.data.demo_book import capital_structures
from loanwhiz.domain.position import (
    Book,
    Position,
    PositionProvenance,
    UnplaceablePosition,
)

client = TestClient(app)


def _get_book() -> dict:
    resp = client.get("/book")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _positions_by_key(body: dict) -> dict[tuple[str, str], dict]:
    return {(p["deal_id"], p["tranche"]): p for p in body["positions"]}


def _cell(position: dict, field: str) -> dict:
    matches = [f for f in position["facts"] if f["field"] == field]
    assert len(matches) == 1, f"expected exactly one {field!r} cell, got {matches}"
    return matches[0]


def _book_of(*positions: Position) -> Book:
    return Book(name="test book", positions=tuple(positions))


def _place(deal_id: str, tranche: str, size: float = 1_000_000.0) -> Position:
    return Position.place(
        deal_id=deal_id,
        tranche=tranche,
        size=size,
        as_of=date(2026, 4, 30),
        provenance=PositionProvenance.ILLUSTRATIVE,
        structures=capital_structures(),
    )


# --- every position survives -------------------------------------------------


def test_every_committed_position_is_served():
    """The book's positions arrive whole — none dropped, none collapsed.

    Asserted against the committed book rather than a count literal, so the
    check tracks the book instead of going stale beside it. Presence is its own
    question, separate from any arithmetic over the positions (#494).
    """
    from loanwhiz.data.demo_book import build_book

    expected = {(p.deal_id, p.tranche): p for p in build_book().positions}
    served = _positions_by_key(_get_book())

    assert set(served) == set(expected)
    for key, position in expected.items():
        assert served[key]["strips"] == list(position.strips)
        assert served[key]["size"] == position.size


def test_one_unresolvable_coupon_does_not_deny_the_rest_of_the_book():
    """The issue's central requirement: per-field refusal, not per-request failure.

    Several committed positions can resolve no coupon at all. The book is still
    200 and still complete, and every one of those positions still reports the
    facts the platform *does* have.
    """
    body = _get_book()
    refused = [
        p for p in body["positions"] if _cell(p, "coupon")["state"] == "not-applicable"
    ]
    assert refused, "no coupon refusal in the book — this endpoint's case is untested"

    for position in refused:
        assert _cell(position, "balance")["state"] == "ran"
        assert _cell(position, "seniority")["state"] == "ran"


def test_no_refused_cell_carries_a_value():
    """A refusal that keeps its value is not a refusal (#549)."""
    for position in _get_book()["positions"]:
        for cell in position["facts"]:
            if cell["state"] == "not-applicable":
                assert cell["value"] is None, cell
                assert cell["reason"].strip(), cell


def test_every_position_reports_every_field():
    """A field is never dropped: absent and refused must not render alike (#494)."""
    for position in _get_book()["positions"]:
        assert [f["field"] for f in position["facts"]] == list(
            api_main.BOOK_POSITION_FIELDS
        )


# --- the coupon, refused and resolved ---------------------------------------


def test_a_class_whose_strips_state_no_rate_refuses_naming_the_stack():
    """The reason asserts only what the input encodes (#457).

    "The capital structure states no numeric rate" is checkable against the
    seed. "The deal pays no coupon" would be a claim about the world that the
    input does not support — assert that wording is absent, since a test that
    checks only the state passes while the prose lies (#471).
    """
    position = _positions_by_key(_get_book())[("green-lion-2024-1", "class_a")]
    coupon = _cell(position, "coupon")

    assert coupon["state"] == "not-applicable"
    assert coupon["value"] is None
    assert "capital structure states no numeric rate" in coupon["reason"]
    assert "class_a" in coupon["reason"]
    assert "pays no coupon" not in coupon["reason"]
    assert "no interest" not in coupon["reason"]


def test_a_resolved_coupon_is_reported_as_ran():
    """The paired positive branch — without it every refusal above is free (#493).

    ``sol-lion-ii``'s ``class_a1`` is a single strip that states 0.25%. Supply
    that one input, change nothing else, and the same cell flips to resolved.
    """
    book = _book_of(_place("sol-lion-ii", "class_a1"))
    with patch.object(api_main, "_load_book", return_value=book):
        body = _get_book()

    coupon = _cell(body["positions"][0], "coupon")
    assert coupon["state"] == "ran"
    assert coupon["value"] == pytest.approx(0.25)


def test_a_class_issued_at_differing_rates_refuses_rather_than_averaging():
    """Six strips, six rates — the class has no single coupon, and no average.

    Averaging would invent a figure the deal does not have; the refusal names
    the rates so the caller can see what it declined to collapse (#538/#549).
    """
    position = _positions_by_key(_get_book())[("sol-lion-ii", "class_a")]
    coupon = _cell(position, "coupon")

    assert coupon["state"] == "not-applicable"
    assert coupon["value"] is None
    assert "differing rates" in coupon["reason"]
    assert "0.25%" in coupon["reason"] and "0.75%" in coupon["reason"]


def test_a_partly_resolvable_class_still_refuses_but_keeps_its_other_facts():
    """Cairn's ``class_b``: ``class_b_1`` states no rate, ``class_b_2`` states 6.87%.

    A class is held whole, so one unresolved strip denies the class a coupon —
    but only the coupon. Balance and seniority are unaffected, which is what
    "per-field" means.
    """
    position = _positions_by_key(_get_book())[("cairn-clo-xvii", "class_b")]

    coupon = _cell(position, "coupon")
    assert coupon["state"] == "not-applicable"
    assert "class_b_1" in coupon["reason"]
    assert "class_b_2" not in coupon["reason"]

    assert _cell(position, "balance")["state"] == "ran"
    assert _cell(position, "seniority")["state"] == "ran"


def test_validated_is_never_claimed():
    """Nothing here is checked against an answer key, so that member never fires."""
    for position in _get_book()["positions"]:
        for cell in position["facts"]:
            assert cell["state"] in {"ran", "not-applicable"}, cell


# --- balance sums the strips -------------------------------------------------


def test_balance_sums_every_strip_of_the_class():
    """Two strips under one class: the holding is their sum, not either one (#571).

    A class re-keyed by name collapses same-named strips and the figure halves;
    a smaller number reads as health, so it is asserted against the stack's own
    balances rather than a literal.
    """
    structure = capital_structures()["cairn-clo-xvii"]
    specs = structure.strips_for("class_b")
    assert len(specs) == 2, "fixture drift: cairn class_b is no longer two strips"

    position = _positions_by_key(_get_book())[("cairn-clo-xvii", "class_b")]
    balance = _cell(position, "balance")

    assert balance["state"] == "ran"
    assert balance["value"] == pytest.approx(sum(s.balance for s in specs))
    assert balance["value"] > max(s.balance for s in specs)


def test_seniority_ranks_the_class_within_its_own_stack():
    position = _positions_by_key(_get_book())[("cairn-clo-xvii", "class_b")]
    seniority = _cell(position, "seniority")

    names = [t.name for t in capital_structures()["cairn-clo-xvii"].tranches]
    assert seniority["state"] == "ran"
    assert seniority["value"] == pytest.approx(names.index("class_b_1") + 1)


# --- the strip-conservation control ------------------------------------------


def test_a_strip_the_stack_does_not_carry_refuses_and_names_it():
    """The control that must fire, or a short book reads as a complete one.

    ``_strips_present`` exists to notice a strip the stack cannot resolve. Its
    silent failure mode is dropping it — the sum then quietly under-states and
    nothing anywhere says so. Feed it a strip the stack lacks and require it to
    flag, since a checker that finds nothing and one that cannot see are
    otherwise the same output (#494).
    """
    position = Position(
        deal_id="cairn-clo-xvii",
        tranche="class_b",
        strips=("class_b_1", "class_b_absent"),
        size=5_000_000.0,
        as_of=date(2026, 4, 30),
        provenance=PositionProvenance.ILLUSTRATIVE,
    )
    with patch.object(api_main, "_load_book", return_value=_book_of(position)):
        body = _get_book()

    served = body["positions"][0]
    balance = _cell(served, "balance")
    assert balance["state"] == "not-applicable"
    assert balance["value"] is None
    assert "class_b_absent" in balance["reason"]


def test_strips_present_returns_the_missing_name_rather_than_dropping_it():
    """The same control at the unit boundary, where the drop would happen."""
    structure = capital_structures()["cairn-clo-xvii"]
    position = Position(
        deal_id="cairn-clo-xvii",
        tranche="class_b",
        strips=("class_b_1", "class_b_absent"),
        size=1.0,
        as_of=date(2026, 4, 30),
        provenance=PositionProvenance.ILLUSTRATIVE,
    )

    specs, missing = api_main._strips_present(position, structure)

    assert missing == ["class_b_absent"]
    assert [s.name for s in specs] == ["class_b_1"]


# --- the book is marked as what it is ---------------------------------------


def test_the_illustrative_qualifier_is_rendered_at_this_surface():
    """#484's gap was labelled-in-the-data, not-on-the-screen. This is a surface."""
    body = _get_book()

    assert body["describes_a_real_holding"] is False
    assert body["disclosures"] and all(d.strip() for d in body["disclosures"])
    for position in body["positions"]:
        assert position["describes_a_real_holding"] is False
        assert position["provenance"] == "illustrative"
        assert position["disclosure"].strip()


# --- the one honest per-request refusal --------------------------------------


def test_an_unplaceable_book_is_a_labelled_422_not_a_partial_book():
    """A book that cannot be built has no honest partial rendering (#452)."""
    with patch.object(
        api_main,
        "_load_book",
        side_effect=UnplaceablePosition("no such class 'class_z' in cairn-clo-xvii"),
    ):
        resp = client.get("/book")

    assert resp.status_code == 422
    assert "class_z" in resp.json()["detail"]


def test_a_strip_the_stack_cannot_supply_twice_refuses_rather_than_counting_it_once():
    """Membership is not multiplicity — the #571 case, from the serving side.

    Two strips issued under one name is precisely what #571 measured. A control
    that asks "does the stack have *a* strip by that name" answers yes and the
    balance quietly counts one, which is the under-statement that reads as
    health. It must compare how many.
    """
    structure = capital_structures()["cairn-clo-xvii"]
    position = Position(
        deal_id="cairn-clo-xvii",
        tranche="class_b",
        strips=("class_b_1", "class_b_1"),
        size=1.0,
        as_of=date(2026, 4, 30),
        provenance=PositionProvenance.ILLUSTRATIVE,
    )

    _, missing = api_main._strips_present(position, structure)
    assert missing == ["class_b_1"]

    with patch.object(api_main, "_load_book", return_value=_book_of(position)):
        body = _get_book()

    balance = _cell(body["positions"][0], "balance")
    assert balance["state"] == "not-applicable"
    assert balance["value"] is None
