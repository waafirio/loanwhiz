"""What a *holding* is, as distinct from what a deal is (#571).

LoanWhiz can describe a deal in detail and could not, until this module, say
that anyone **holds** part of one. A :class:`Position` is that missing concept
and deliberately nothing more — a deal, a tranche, a size and an as-of date.
No P&L, no valuation, no projection: those are separate questions that need
this one answered first.

Two things about it are load-bearing.

A tranche reference resolves, it is not matched
-----------------------------------------------
A position names a **class**; an issuer sells that class in one or more
**strips**. Green Lion sells Class B whole; Cairn sells it as ``class_b_1``
and ``class_b_2``. A position naming ``class_b`` on Cairn means both strips,
and a string comparison against the deal's tranche names would find neither.
So placement runs the class through
:func:`~loanwhiz.primitives.capital_structure.resolve_strips` — the one
class-to-strips grammar (#538), shared with the waterfall interpreter rather
than copied — against the deal's own :class:`CapitalStructure` (#478).

A class the stack cannot place is an **error, not a zero**. The #452 lesson is
that a partial answer here errs in the flattering direction: a position sized
against a class the deal does not have would report an exposure of nil, which
reads as "holds nothing" rather than "we could not place this". Both refusals
below raise :class:`UnplaceablePosition` naming what was not found.

The illustrative qualifier is on the record
-------------------------------------------
Following the precedent :mod:`loanwhiz.domain.tape_provenance` set for tapes
(#483/#484): the claim rides on the record itself, so every consumer inherits
it rather than each surface remembering to add a caveat. #484's own known gap
is the warning — its synthetic pools are correctly labelled in the data and
the Pool and Waterfall pages still render no badge, because a label a surface
has to *remember* is one it can forget.

Three things make the qualifier hard to drop, and none of them is a
convention:

1. :attr:`Position.provenance` is a **required** field with no default, so a
   position that will not say what it is cannot be constructed at all.
2. :class:`PositionProvenance` is a closed enum whose facts table is guarded
   for total coverage at import (#453), so a member added later cannot become
   well-formed by omission.
3. :meth:`Position.to_record` is the one serialisation, and it always emits
   both the kind and its :attr:`~PositionProvenance.disclosure`. A surface
   that builds its own dict from the fields instead is what
   ``tests/test_position_provenance_bypass.py`` walks the AST to catch (#483's
   ``test_tape_seam_bypass`` shape).

Rendering the qualifier on screen is #573's work, not this module's. What this
module owes #573 is a qualifier that cannot arrive missing.

Import note
-----------
``loanwhiz.domain``'s package ``__init__`` participates in an import cycle with
``loanwhiz.primitives``. Import a ``loanwhiz.primitives`` module before this
one, as :mod:`loanwhiz.domain.tape_provenance` and
:mod:`loanwhiz.domain.esma_annex_registry` already document for their callers.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from loanwhiz.primitives.capital_structure import CapitalStructure

__all__ = [
    "Book",
    "Position",
    "PositionProvenance",
    "UnplaceablePosition",
]


class UnplaceablePosition(ValueError):
    """A position naming a deal or a class the registry cannot place.

    Raised rather than returning a zero-sized or partially-placed holding. The
    message names *what* could not be placed and *what was available*, so the
    caller reports the real cause instead of a bare "no exposure" (#452).
    """


class PositionProvenance(str, Enum):
    """What a holding **is** — a closed vocabulary with no default member.

    There is deliberately no default, for the reason
    :class:`~loanwhiz.domain.tape_provenance.TapeSourceKind` has none: this is
    the most consequential claim the record makes, and a default would let it
    be made by omission — which is precisely how an illustrative book comes to
    be read as somebody's actual exposure.
    """

    #: Constructed by LoanWhiz to demonstrate the platform. The deal and the
    #: tranche are real and the position resolves against the real capital
    #: structure — but nobody holds it, so no figure computed from it is
    #: evidence of anyone's exposure.
    ILLUSTRATIVE = "illustrative"

    #: A holding as stated by the party that holds it. The size is somebody's
    #: actual position, not a demonstration of one.
    CLIENT_STATED = "client_stated"

    @property
    def describes_a_real_holding(self) -> bool:
        """Whether somebody actually holds this.

        The predicate a surface branches on before treating a size as
        exposure. Distinct from whether the *deal* is real: an illustrative
        position names a real deal and a real tranche, and is still nobody's
        holding, which is exactly why the deal being real cannot be the test.
        """
        return _FACTS[self].describes_a_real_holding

    @property
    def disclosure(self) -> str:
        """One sentence stating what this position is, for any operator surface.

        Written to be quoted verbatim. A surface rendering a position's status
        should render this rather than compose its own wording, so the claim
        cannot drift between views — the drift #484 shipped.
        """
        return _FACTS[self].disclosure


class _ProvenanceFacts(BaseModel):
    """The facts each :class:`PositionProvenance` member carries."""

    model_config = ConfigDict(frozen=True)

    describes_a_real_holding: bool
    disclosure: str = Field(min_length=40)


#: Facts per member, guarded for **total** coverage at import (below). A
#: registration-style table that silently missed a member would make a newly
#: added kind well-formed by accident, which is how a closed enum stops being
#: closed (#453). There is no ``.get(..., default)`` anywhere in this module.
_FACTS: dict[PositionProvenance, _ProvenanceFacts] = {
    PositionProvenance.ILLUSTRATIVE: _ProvenanceFacts(
        describes_a_real_holding=False,
        disclosure=(
            "Illustrative position constructed by LoanWhiz to demonstrate the "
            "platform. The deal and tranche are real and the size resolves "
            "against the real capital structure, but nobody holds it: no "
            "figure computed from this position is evidence of exposure."
        ),
    ),
    PositionProvenance.CLIENT_STATED: _ProvenanceFacts(
        describes_a_real_holding=True,
        disclosure=(
            "Holding as stated by the party that holds it, resolved against "
            "the deal's capital structure. The size is a real position and "
            "not a demonstration of one."
        ),
    ),
}

_missing = sorted(kind.value for kind in PositionProvenance if kind not in _FACTS)
if _missing:  # pragma: no cover - import-time guard, asserted by test
    raise RuntimeError(
        f"PositionProvenance members {_missing} carry no facts. Every member "
        "must declare describes_a_real_holding / disclosure: a member with no "
        "entry would answer the is-this-real question by accident."
    )
del _missing


class Position(BaseModel):
    """One holding: a size in one class of one deal, as at one date.

    Construct through :meth:`place` rather than directly — the constructor
    cannot check a class against a stack it was not given, and an unplaced
    position is the thing this module exists to prevent.
    """

    model_config = ConfigDict(frozen=True)

    deal_id: str = Field(..., min_length=1, description="Registry key of the deal held.")
    tranche: str = Field(
        ...,
        min_length=1,
        description="The class as referenced, e.g. ``class_b`` — not necessarily a tranche name.",
    )
    strips: tuple[str, ...] = Field(
        ...,
        min_length=1,
        description="The stack's own names this class resolved to, senior → junior.",
    )
    size: float = Field(..., gt=0.0, description="Notional held, in the deal's currency.")
    as_of: date = Field(..., description="The date this holding is stated as at.")
    provenance: PositionProvenance = Field(
        ...,
        description="What this holding IS. Required: a position that will not say cannot be built.",
    )

    @classmethod
    def place(
        cls,
        *,
        deal_id: str,
        tranche: str,
        size: float,
        as_of: date,
        provenance: PositionProvenance,
        structures: Mapping[str, CapitalStructure],
    ) -> "Position":
        """Resolve a holding against the registry, or refuse it.

        Args:
            deal_id: Registry key of the deal. Absent from *structures* means
                the registry does not carry it.
            tranche: The class named, resolved through the #538 grammar — so
                ``class_b`` on a deal whose strips are ``class_b_1`` and
                ``class_b_2`` places as both.
            size: Notional held, in the deal's currency.
            as_of: The date the holding is stated as at.
            provenance: What this holding is. No default, by design.
            structures: The placement universe — every deal the registry
                carries, with the capital structure to resolve against.

        Raises:
            UnplaceablePosition: when the registry carries no such deal, or
                when the deal's stack places no such class. Never returns a
                zero-sized or partially-placed holding (#452/#493).
        """
        structure = structures.get(deal_id)
        if structure is None:
            known = ", ".join(sorted(structures)) or "no deals"
            # Deliberately does **not** say "not in the registry". This function
            # sees only the placement universe it was handed, and a deal can be
            # absent from it for more than one reason — unregistered, or
            # registered with no resolvable capital structure. Naming the first
            # cause for both is the #549 failure: a correct refusal reporting a
            # cause that was not the deal's. The caller that knows the registry
            # distinguishes them; this message states only what is observable
            # from here.
            raise UnplaceablePosition(
                f"no capital structure is available for deal {deal_id!r}, so a "
                f"position in it cannot be placed. Placeable: {known}."
            )

        strips = [spec.name for spec in structure.strips_for(tranche)]
        if not strips:
            raise UnplaceablePosition(
                f"deal {deal_id!r} places no class {tranche!r}: its stack is "
                f"{', '.join(structure.names)}. A class the structure cannot "
                "place is an error, not a position of zero."
            )

        return cls(
            deal_id=deal_id,
            tranche=tranche,
            strips=tuple(strips),
            size=size,
            as_of=as_of,
            provenance=provenance,
        )

    def to_record(self) -> dict[str, Any]:
        """The one serialisation of a position, disclosure included.

        Every consumer that writes a position out goes through here. The
        ``provenance`` and ``disclosure`` keys are not optional and not
        conditional: a record of a holding always says what the holding is.
        Building a dict from the fields by hand instead is the bypass
        ``tests/test_position_provenance_bypass.py`` exists to catch.
        """
        return {
            "deal_id": self.deal_id,
            "tranche": self.tranche,
            "strips": list(self.strips),
            "size": self.size,
            "as_of": self.as_of.isoformat(),
            "provenance": self.provenance.value,
            "disclosure": self.provenance.disclosure,
        }


class Book(BaseModel):
    """A set of positions held together, and what the set as a whole is.

    A book's own status is decided by its weakest member, not by a flag set
    beside it: one illustrative position makes the totals illustrative, because
    a number summed across the book inherits every input's status.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., min_length=1, description="What this book is called.")
    positions: tuple[Position, ...] = Field(
        ..., min_length=1, description="The holdings, in declaration order."
    )

    @property
    def describes_a_real_holding(self) -> bool:
        """Whether **every** position in the book is somebody's actual holding.

        ``False`` when any member is illustrative — a total is only as real as
        its least real input, so this cannot be an any() or a stored flag.
        """
        return all(p.provenance.describes_a_real_holding for p in self.positions)

    @property
    def disclosures(self) -> tuple[str, ...]:
        """Every distinct disclosure the book's positions carry, in stack order.

        A surface rendering the book renders these; it does not compose its own
        wording, and it does not render only the first.
        """
        seen: list[str] = []
        for position in self.positions:
            text = position.provenance.disclosure
            if text not in seen:
                seen.append(text)
        return tuple(seen)

    def to_record(self) -> dict[str, Any]:
        """The one serialisation of a book — positions through their own record."""
        return {
            "name": self.name,
            "describes_a_real_holding": self.describes_a_real_holding,
            "disclosures": list(self.disclosures),
            "positions": [p.to_record() for p in self.positions],
        }
