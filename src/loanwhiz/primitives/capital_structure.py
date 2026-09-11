"""The deal's capital structure, as one typed N-class contract (#478).

Why this module exists
----------------------
``capital_structure`` used to be an **untyped four-key dict** —
``{class_a_balance, class_a_rate_pct, class_b_balance, class_c_balance}`` —
and three separate places independently collapsed a deal's extracted
``tranche_structure`` onto it. They disagreed with each other:

* ``api.main._extracted_capital_structure`` looked up seniority ``0/1/2`` and
  returned ``None`` unless all four fields filled;
* ``pool_pipeline_harness.capital_structure_from_deal_model`` sorted by
  seniority and took the **first three positionally**, zero-filling the rest —
  so an 8-class CLO silently became a 3-class deal with five tranches dropped;
* ``capability_matrix._extracted_tranche_balances`` was a third hand-copy.

Three collapses of one idea is how a wrong number reaches a report: the
narrowest of them *refuses*, the widest *invents*. Meanwhile the engine below
was never three-class — #363 moved ``DealState`` onto a canonical
``tranches`` list, and :meth:`DealState.seed_from_prospectus` already accepts
``{<name>_balance}`` for arbitrary tranche names. The four-key restriction was
imposed entirely upstream.

So this module is the **one** builder, and the shape it produces is the shape
``seed_from_prospectus`` already consumes. Adding a deeper deal needs no code
change here.

The contract, and how it is enforced
------------------------------------
Three layers, deliberately, because no one of them covers the failure:

1. **The type** (:class:`CapitalStructure`) makes the N-class list the only
   representable shape — there is no "third tranche" slot to overflow.
2. **A runtime guard** (:meth:`CapitalStructure.from_tranche_structure`)
   refuses a stack it cannot place *in full*. A type cannot express "you
   mapped every input row", because dropping a row yields a perfectly valid
   value of the type — which is exactly the bug that shipped. So the builder
   checks that the tranche count and the balance total it emits account for
   every input row, and raises :class:`UnresolvableCapitalStructure` naming
   the offending row rather than returning a shorter stack.
3. **Regression tests** (``tests/test_capital_structure.py``) pin both.

Refusing rather than truncating matters in one specific direction: the total
is a **denominator** for the coverage and attachment-point metrics, so a
silently short stack reports *more* subordination than the deal has — it reads
as health, never as a bug (the #452 lesson).

Coupons are separate from balances
----------------------------------
A tranche's balance is structure; its coupon is a term of that tranche, and
the two are **not** available together. Cairn's notes pay
``"3 month EURIBOR + 1.80%"`` — a margin, not a rate: resolving it needs the
period's index fixing, and inventing one is precisely what the engine's
refusal exists to prevent. So :attr:`TrancheSpec.rate_pct` is ``None`` unless
the document states a genuinely numeric coupon, and a caller that *needs* a
rate must refuse for want of it, loudly and by name — never by defaulting to
zero, which would silently model an interest-free note.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CapitalStructure",
    "TrancheSpec",
    "UnresolvableCapitalStructure",
    "classes_from_strips",
    "engine_tranche_name",
    "numeric_rate_pct",
    "resolve_strips",
    "senior_tranche_name",
]


class UnresolvableCapitalStructure(ValueError):
    """A tranche stack that cannot be placed in full.

    Raised rather than returning a partial structure. Carries the reason in its
    message so the caller can name *why* a deal's structure is unusable instead
    of reporting a bare "missing config".
    """


def engine_tranche_name(document_name: str) -> str:
    """``"Class B-1"`` → ``"class_b_1"`` — the engine's tranche-name spelling.

    The same normalisation ``DealState.seed_from_prospectus`` /
    ``waterfall_interpreter`` key their tranche lookups on, so a name built
    here addresses the same tranche the engine folds.
    """
    return re.sub(r"[^a-z0-9]+", "_", document_name.lower()).strip("_")


def resolve_strips(class_name: str, names: Sequence[str]) -> list[str]:
    """The names in *names* that make up the class ``class_name``, in stack order.

    **The one class-to-strips grammar (#538).** A recipient, a covenant or a
    position names a **class**; an issuer sells that class in one or more
    **strips**. Green Lion sells Class B whole, so ``class_b`` is one name and
    this returns it alone. Cairn sells Class B in two — ``class_b_1`` floating
    and ``class_b_2`` fixed — so ``class_b`` names nothing directly and the
    class is exactly those two.

    It lives here, on the module that already owns the engine's tranche-name
    spelling (:func:`engine_tranche_name`), because two callers need it against
    two different collections: :meth:`WaterfallFunds.tranche_strips` resolves it
    over a period's funds, and :meth:`CapitalStructure.strips_for` over a deal's
    stack. Both delegate here rather than carrying a regex apiece — a second
    copy agrees on the day it is written and drifts silently afterwards (#549).

    The grammar is the sub-series one :mod:`loanwhiz.extraction.assembler`
    defines on the document side — a class letter followed by an optional series
    **number** — read through the slug :func:`engine_tranche_name` produces.
    Both committed spellings are covered: hyphenated ``"Class B-1"`` slugs to
    ``class_b_1`` and joined ``"Class A1"`` to ``class_a1`` (#456).

    **A lettered suffix is deliberately not a series.** A refinanced
    ``"Class A-R"`` (``class_a_r``) replaces Class A rather than joining it, so
    sweeping it in would double-count where both are present. A deal carrying
    only ``class_a_r`` resolves ``class_a`` to nothing and the caller refuses.

    **An exact match wins outright**, skipping the series scan: a name for the
    class itself *is* the class, so a stack carrying both an aggregate
    ``class_a`` row and its ``class_a_1``/``class_a_2`` components reports the
    aggregate once instead of counting the class roughly twice.

    Returns ``[]`` when the class was issued in no strip. That is the
    **unknown** answer, and every caller must keep it distinct from a value of
    zero (#493) — a position naming such a class is refused, not sized at nil.
    """
    if class_name in names:
        return [class_name]
    pattern = re.compile(rf"^{re.escape(class_name)}_?\d+$")
    return [name for name in names if pattern.match(name)]


def classes_from_strips(names: Sequence[str]) -> list[str]:
    """The classes the strip names in *names* make up, in stack order.

    **The inverse of :func:`resolve_strips`, and the only one.** That function
    answers "which strips make up this class"; a reader that must enumerate a
    deal's classes — a per-class panel, a per-class roll-up — has the strips and
    needs the question the other way round. Cairn sells Class B in two, so
    ``["class_a", "class_b_1", "class_b_2", "class_c"]`` names three classes,
    not four, and ``class_b`` is one of them even though no strip is spelled
    that way.

    It is written here, beside :func:`resolve_strips`, and **decides nothing on
    its own**: a candidate class is proposed by stripping a trailing series
    number, then accepted only if ``resolve_strips`` agrees the strip belongs to
    it. So there is still exactly one grammar (#538) and the two directions
    cannot drift (#549) — every rule ``resolve_strips`` states holds here by
    construction rather than by a second copy of the regex agreeing today:

    - **A lettered suffix is not a series.** ``class_a_r`` proposes itself (no
      trailing number to strip) and stays its own class, so a refinanced class
      is never swept into the one it replaced.
    - **An exact match wins outright.** A stack carrying both an aggregate
      ``class_a`` and its ``class_a_1`` components resolves ``class_a`` to the
      aggregate alone, so ``class_a_1`` does not round-trip and is reported as
      its own class rather than folded into a total that already counts it.

    Order is the stack's own, senior → junior, and a class appears once — at the
    position of its most senior strip.
    """
    classes: list[str] = []
    for name in names:
        candidate = re.sub(r"_?\d+$", "", name)
        if candidate and candidate != name and name in resolve_strips(candidate, names):
            resolved = candidate
        else:
            resolved = name
        if resolved not in classes:
            classes.append(resolved)
    return classes


def numeric_rate_pct(raw: Any) -> float | None:
    """A tranche coupon as a numeric percent, or ``None`` when it is not one.

    Accepts a number, a numeric string, and a numeric string with a trailing
    ``%`` (``"6.87%"`` → ``6.87``). Deliberately returns ``None`` for a
    reference-rate description like ``"3 month EURIBOR + 1.80%"``: a margin is
    not a coupon, and coercing one — by taking the leading number, the trailing
    number, or any other reading — fabricates a rate the document does not
    state.

    This is the single definition. It previously existed twice, as
    ``api.main._numeric_rate_pct`` and ``pool_pipeline_harness._parse_rate``,
    which disagreed: the latter rejected ``"6.87%"``, so Cairn's one genuinely
    fixed-rate tranche parsed on one path and not the other.
    """
    if isinstance(raw, bool):  # bool is an int subclass; a flag is not a rate
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        cleaned = raw.strip().rstrip("%").strip()
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


class TrancheSpec(BaseModel):
    """One class of notes in a deal's capital structure."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., description="Engine tranche name, e.g. ``class_b_1``.")
    balance: float = Field(..., description="Closing balance at issue (deal currency).")
    rate_pct: float | None = Field(
        default=None,
        description=(
            "Annual coupon in percent, or None when the document states a "
            "reference rate rather than a resolved coupon."
        ),
    )
    document_name: str | None = Field(
        default=None, description="The document's own label, e.g. ``Class B-1``."
    )
    seniority: int | None = Field(
        default=None, description="0 = most senior; the source's own ordinal."
    )


class CapitalStructure(BaseModel):
    """A deal's full tranche stack, ordered senior → junior.

    The engine consumes this as a ``{<name>_balance: float}`` mapping
    (:meth:`to_engine_mapping`); this model is the typed, discoverable form of
    that mapping and the only sanctioned way to build one from an extracted
    ``tranche_structure``.
    """

    model_config = ConfigDict(frozen=True)

    tranches: tuple[TrancheSpec, ...] = Field(
        ..., description="Every class in the deal, ordered senior → junior."
    )

    # -- construction --------------------------------------------------------

    @classmethod
    def from_tranche_structure(
        cls, rows: Iterable[Mapping[str, Any]] | None
    ) -> "CapitalStructure":
        """Build from an extracted model's ``tranche_structure``.

        ``rows`` are the extractor's ``{name, size_eur, rating, rate,
        seniority}`` dicts. Every row must carry a usable name and a numeric
        ``size_eur``; the result is ordered by ``seniority`` ascending (0 = most
        senior), which is the convention every consumer in the tree uses.

        Raises
        ------
        UnresolvableCapitalStructure
            If there are no rows, if any row lacks a name or a numeric size, if
            two rows normalise to the same engine name, or if the built stack
            fails to account for every input row. **Refusing is the point** — a
            stack short by one tranche is a smaller denominator, which reports
            subordination the deal does not have.
        """
        rows = list(rows or [])
        if not rows:
            raise UnresolvableCapitalStructure(
                "no tranche_structure rows — the deal model states no capital structure"
            )

        specs: list[TrancheSpec] = []
        for position, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise UnresolvableCapitalStructure(
                    f"tranche at position {position} is not a mapping"
                )
            document_name = row.get("name")
            if not isinstance(document_name, str) or not document_name.strip():
                raise UnresolvableCapitalStructure(
                    f"tranche at position {position} states no name"
                )
            name = engine_tranche_name(document_name)
            if not name:
                raise UnresolvableCapitalStructure(
                    f"tranche {document_name!r} normalises to an empty engine name"
                )
            size = row.get("size_eur")
            if isinstance(size, bool) or not isinstance(size, (int, float)):
                raise UnresolvableCapitalStructure(
                    f"tranche {document_name!r} states no numeric size_eur "
                    f"(got {size!r}) — refusing to place a stack with an unsized class"
                )
            seniority = row.get("seniority")
            specs.append(
                TrancheSpec(
                    name=name,
                    balance=float(size),
                    rate_pct=numeric_rate_pct(row.get("rate")),
                    document_name=document_name,
                    seniority=seniority if isinstance(seniority, int) else None,
                )
            )

        names = [s.name for s in specs]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            raise UnresolvableCapitalStructure(
                f"two tranches share the engine name(s) {duplicates} — "
                "one would overwrite the other"
            )

        # Senior → junior. A row with no stated seniority sorts after every row
        # that has one, keeping its document order among its peers.
        ordered = sorted(
            specs,
            key=lambda s: (s.seniority is None, s.seniority or 0),
        )
        structure = cls(tranches=tuple(ordered))

        # -- the guard the type cannot express -------------------------------
        # Every input row must survive into the stack, and the balances must
        # still sum to what the document states. A type is satisfied by a
        # shorter list; this is what refuses one.
        if len(structure.tranches) != len(rows):
            raise UnresolvableCapitalStructure(
                f"built {len(structure.tranches)} tranches from {len(rows)} rows — "
                "refusing a stack that drops a class"
            )
        stated_total = sum(float(r["size_eur"]) for r in rows)
        if abs(structure.total_balance - stated_total) > 0.005:
            raise UnresolvableCapitalStructure(
                f"stack totals {structure.total_balance} but the rows state "
                f"{stated_total} — refusing a structure that does not tie out"
            )
        return structure

    @classmethod
    def from_engine_mapping(cls, mapping: Mapping[str, Any]) -> "CapitalStructure":
        """Build from an operator-declared ``{<name>_balance}`` config mapping.

        This is the ``deals.json`` ``capital_structure`` shape — including the
        legacy four-key ``{class_a_balance, class_a_rate_pct, class_b_balance,
        class_c_balance}`` form, which is simply a three-tranche instance of it
        and keeps working unchanged.

        Order is the mapping's own insertion order; a declared config states no
        seniority, and inventing one from the key names would be a guess.
        """
        specs: list[TrancheSpec] = []
        for key, value in mapping.items():
            if not key.endswith("_balance"):
                continue
            name = key[: -len("_balance")]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise UnresolvableCapitalStructure(
                    f"declared {key!r} is not numeric (got {value!r})"
                )
            specs.append(
                TrancheSpec(
                    name=name,
                    balance=float(value),
                    rate_pct=numeric_rate_pct(mapping.get(f"{name}_rate_pct")),
                )
            )
        if not specs:
            raise UnresolvableCapitalStructure(
                "declared capital_structure carries no ``<name>_balance`` key"
            )
        return cls(tranches=tuple(specs))

    # -- projection ----------------------------------------------------------

    def to_engine_mapping(self) -> dict[str, float]:
        """The ``{<name>_balance, <name>_rate_pct}`` mapping the engine consumes.

        A tranche whose coupon is unresolved contributes **no** ``_rate_pct``
        key at all. That is deliberate and load-bearing: emitting ``0.0`` would
        model an interest-free note and quietly understate the revenue
        waterfall's need, where an absent key makes the caller decide what to do
        about a rate it does not have (the #471 "no key, not zero" rule).
        """
        mapping: dict[str, float] = {}
        for tranche in self.tranches:
            mapping[f"{tranche.name}_balance"] = tranche.balance
            if tranche.rate_pct is not None:
                mapping[f"{tranche.name}_rate_pct"] = tranche.rate_pct
        return mapping

    # -- accessors -----------------------------------------------------------

    @property
    def total_balance(self) -> float:
        """Sum of every tranche balance — the stack's own total."""
        return sum(t.balance for t in self.tranches)

    @property
    def names(self) -> tuple[str, ...]:
        """Engine tranche names, senior → junior."""
        return tuple(t.name for t in self.tranches)

    @property
    def senior(self) -> TrancheSpec:
        """The most senior tranche. The stack is never empty by construction."""
        return self.tranches[0]

    def tranche(self, name: str) -> TrancheSpec | None:
        """The tranche named ``name``, or ``None`` when the stack has no such class."""
        for tranche in self.tranches:
            if tranche.name == name:
                return tranche
        return None

    def strips_for(self, class_name: str) -> list[TrancheSpec]:
        """Every strip of the class ``class_name`` in this stack, senior → junior.

        The stack-side half of :func:`resolve_strips`; see it for the grammar.
        ``[]`` means the stack places no such class — the **unknown** answer a
        caller must refuse on, never read as a zero-sized holding (#452/#493).
        """
        by_name = {tranche.name: tranche for tranche in self.tranches}
        return [by_name[name] for name in resolve_strips(class_name, self.names)]


def senior_tranche_name(capital_structure: Mapping[str, Any]) -> str | None:
    """The most senior class's engine name in a config mapping, or ``None``.

    The single answer to "which class is senior here", used by both the API
    resolver and the capability matrix's mirror of it. Written once on purpose:
    two hand-rolled copies held together by a comment is the exact failure this
    module was introduced to remove, and it would have been reintroduced at half
    the size.

    Both config sources are ordered senior → junior — the builder sorts by
    seniority, and a declared ``deals.json`` mapping is read in declaration order
    — so the senior class is the first one the mapping names. ``None`` means the
    mapping names no class at all, which the callers report against
    ``capital_structure`` itself.
    """
    if not isinstance(capital_structure, Mapping):
        # A ``deals.json`` value is operator-authored and only checked to be
        # JSON; a list or string here must reach the caller's labelled 422 for
        # a misconfigured deal, not an AttributeError 500 from inside the type.
        return None
    try:
        return CapitalStructure.from_engine_mapping(capital_structure).senior.name
    except UnresolvableCapitalStructure:
        return None
