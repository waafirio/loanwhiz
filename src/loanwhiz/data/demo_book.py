"""The committed illustrative demo book, and the builder that produces it (#571).

#484 committed its synthetic pools **beside the fit spec they were drawn from**,
so the derivation is auditable rather than asserted. A book of holdings needs
the same treatment for the same reason: a list of positions with no visible
construction is indistinguishable from a claim that somebody holds them.

So this module is both halves. :data:`BOOK_SPEC` declares the book — which deal,
which class, how much, as at when — and :func:`build_book` resolves that
declaration against the **real** registry and the deals' **real** capital
structures, refusing anything it cannot place. The committed
``books/demo-book.json`` is exactly :func:`render`'s output for that spec, and
``tests/test_demo_book.py`` asserts it regenerates byte-identically, so the file
can never quietly drift from the code that claims to produce it.

Every position here is :attr:`PositionProvenance.ILLUSTRATIVE`. The deals and
the tranches are real and each one resolves against the deal's actual stack —
but nobody holds any of it, and that claim rides on each record rather than on
this docstring.

Why a module rather than a ``scripts/`` file
--------------------------------------------
#484's generators are offline authoring scripts, and this could have been one.
It is an importable module instead so the byte-identity test can call
:func:`render` directly rather than shelling out — the property worth pinning is
"the committed bytes are this function's output", and a test that re-runs a
subprocess pins the script's *invocation* as much as its result.

Regenerate with::

    python -m loanwhiz.data.demo_book --write
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from loanwhiz.config import DEAL_REGISTRY
from loanwhiz.extraction.assembler import _slug
from loanwhiz.primitives.capital_structure import CapitalStructure
from loanwhiz.primitives.pool_pipeline_harness import capital_structure_from_deal_model
from loanwhiz.domain.position import Book, Position, PositionProvenance, UnplaceablePosition

__all__ = [
    "BOOK_NAME",
    "BOOK_SPEC",
    "DEMO_BOOK_PATH",
    "FORMAT_VERSION",
    "build_book",
    "capital_structures",
    "load_demo_book",
    "render",
]

#: Bumped when the record's shape changes, so a later reader can tell which
#: shape it is holding rather than inferring it from the keys present.
FORMAT_VERSION = 1

_SEED_DIR = Path(__file__).resolve().parent / "deals" / "seed"

#: The committed book. Written by ``--write``; read by :func:`load_demo_book`.
DEMO_BOOK_PATH = Path(__file__).resolve().parent / "books" / "demo-book.json"

BOOK_NAME = "LoanWhiz illustrative demo book"

#: The book, declared. Each entry is resolved against the real registry and the
#: deal's real capital structure by :func:`build_book`, so a typo in a deal id
#: or a class this stack does not carry fails the build loudly rather than
#: producing a plausible-looking book.
#:
#: The classes are chosen to exercise every shape the #538 grammar has to
#: handle, because a demo book that only ever names whole classes would not
#: demonstrate that resolution happens at all:
#:
#: * ``cairn-clo-xvii`` / ``class_b`` — sold in two strips (B-1, B-2);
#: * ``sol-lion-ii`` / ``class_a`` — sold in six (A1…A6), joined spelling;
#: * ``leone-arancio-2023-1`` / ``class_a`` — sold in two, joined spelling;
#: * ``green-lion-2023-1`` / ``class_b`` — sold whole, so it resolves to itself;
#: * ``green-lion-2024-1`` / ``class_a`` — sold whole, the senior class.
#: Every entry states its own ``provenance``. It would be shorter to apply
#: ILLUSTRATIVE to the whole book in :func:`build_book`, and that is exactly the
#: indirection ``tests/test_position_provenance_bypass.py`` refuses: a declared
#: holding that does not say what it is relies on something downstream to say it
#: for them, which is the habit #484's un-badged pools came from. Omit the key
#: and the build raises rather than defaulting.
BOOK_SPEC: tuple[Mapping[str, Any], ...] = (
    {
        "deal_id": "cairn-clo-xvii",
        "tranche": "class_b",
        "size": 5_000_000.0,
        "provenance": PositionProvenance.ILLUSTRATIVE,
    },
    {
        "deal_id": "sol-lion-ii",
        "tranche": "class_a",
        "size": 12_500_000.0,
        "provenance": PositionProvenance.ILLUSTRATIVE,
    },
    {
        "deal_id": "leone-arancio-2023-1",
        "tranche": "class_a",
        "size": 7_250_000.0,
        "provenance": PositionProvenance.ILLUSTRATIVE,
    },
    {
        "deal_id": "green-lion-2023-1",
        "tranche": "class_b",
        "size": 3_000_000.0,
        "provenance": PositionProvenance.ILLUSTRATIVE,
    },
    {
        "deal_id": "green-lion-2024-1",
        "tranche": "class_a",
        "size": 20_000_000.0,
        "provenance": PositionProvenance.ILLUSTRATIVE,
    },
)

#: The date every position in the book is stated as at. One date for the whole
#: book: positions on different dates are not comparable, and a book whose rows
#: silently mix as-of dates is the kind of thing that reads fine and totals
#: wrong.
AS_OF = date(2026, 4, 30)


def capital_structures(
    registry: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, CapitalStructure]:
    """Every registered deal's capital structure, keyed by registry id.

    This is the **placement universe** :meth:`Position.place` resolves against:
    a deal absent from the returned mapping is one the registry does not carry,
    and a position naming it is refused.

    A deal is joined to its committed seed model the way the API already does it
    — ``_slug(deal_name)`` — and the stack is read through the one shared
    builder (:func:`capital_structure_from_deal_model`, #478) rather than by
    re-collapsing ``tranche_structure`` here. A registered deal whose seed is
    missing is omitted rather than defaulted: an empty stack would place no
    class and report every holding in it as unplaceable for the wrong reason.
    """
    source = DEAL_REGISTRY if registry is None else registry
    structures: dict[str, CapitalStructure] = {}
    for deal_id, deal in source.items():
        deal_name = deal.get("deal_name")
        if not deal_name:
            continue
        seed_path = _SEED_DIR / f"{_slug(deal_name)}.json"
        if not seed_path.exists():
            continue
        seed = json.loads(seed_path.read_text(encoding="utf-8"))
        structures[deal_id] = CapitalStructure.from_engine_mapping(
            capital_structure_from_deal_model(seed)
        )
    return structures


def build_book(
    registry: Mapping[str, Mapping[str, Any]] | None = None,
) -> Book:
    """Resolve :data:`BOOK_SPEC` into a :class:`Book`, or refuse.

    Raises:
        UnplaceablePosition: when a spec entry names a deal the registry does
            not carry, or a class the deal's stack cannot place. The book is
            never built partially — a demo book quietly missing a row is worse
            than one that fails to build.
    """
    source = DEAL_REGISTRY if registry is None else registry
    structures = capital_structures(registry)
    for entry in BOOK_SPEC:
        deal_id = entry["deal_id"]
        if deal_id in source and deal_id not in structures:
            # Registered, but no committed seed resolved to a structure. Named
            # separately so the build reports the cause that is actually the
            # deal's, rather than letting `place` report the only one visible
            # to it ("no capital structure available") for both (#549/#457).
            raise UnplaceablePosition(
                f"deal {deal_id!r} IS registered but no committed seed model "
                "resolves to a capital structure for it, so its holdings cannot "
                "be placed. Seed the deal rather than dropping the position."
            )
    positions = tuple(
        Position.place(
            deal_id=entry["deal_id"],
            tranche=entry["tranche"],
            size=entry["size"],
            as_of=AS_OF,
            provenance=entry["provenance"],
            structures=structures,
        )
        for entry in BOOK_SPEC
    )
    return Book(name=BOOK_NAME, positions=positions)


def render(book: Book) -> str:
    """The exact bytes of the committed book file.

    Deterministic by construction — no timestamp, no host detail, no dict
    ordering left to chance — because the committed file's only guarantee is
    that it is this function's output for :data:`BOOK_SPEC`.
    """
    record = {"format_version": FORMAT_VERSION, **book.to_record()}
    return json.dumps(record, indent=2, ensure_ascii=False) + "\n"


def load_demo_book() -> dict[str, Any]:
    """Read the committed book record from disk.

    Returns the published artefact as committed — including every position's
    ``provenance`` and its ``disclosure``, which :meth:`Position.to_record`
    always emits. Callers render the disclosure; they do not compose their own.
    """
    return json.loads(DEMO_BOOK_PATH.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    """Write the committed book. ``--check`` verifies it instead of writing."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write the committed book")
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the committed book differs from a fresh build",
    )
    args = parser.parse_args(argv)

    rendered = render(build_book())
    if args.check:
        current = DEMO_BOOK_PATH.read_text(encoding="utf-8") if DEMO_BOOK_PATH.exists() else ""
        if current != rendered:
            print(f"{DEMO_BOOK_PATH} differs from a fresh build; run --write")
            return 1
        print(f"{DEMO_BOOK_PATH} is up to date")
        return 0

    if args.write:
        DEMO_BOOK_PATH.parent.mkdir(parents=True, exist_ok=True)
        DEMO_BOOK_PATH.write_text(rendered, encoding="utf-8")
        print(f"wrote {DEMO_BOOK_PATH}")
        return 0

    print(rendered, end="")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
