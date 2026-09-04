"""ESMA RTS loan-level annex registry — the single resolution point for tapes.

LoanWhiz reads loan-level tapes published under the **ESMA Securitisation
disclosure RTS** (Commission Delegated Regulation (EU) 2020/1224). That RTS does
not define *one* loan-level template: it defines one **per asset class**, each in
its own annex with its own stable field-code prefix — Annex II residential real
estate (``RREL``), Annex IV corporate (``CRPL``), Annex V automobile (``AUTL``),
and so on. A tape's columns are only meaningful relative to the annex it was
published under.

Why this module exists
----------------------
:mod:`loanwhiz.domain.esma_annex2` shipped the RMBS table first, and its docstring
anticipated this change: its record shape "admits other annexes (Auto/SME) later
without breaking callers". Meanwhile the tape normaliser grew its *own* annex
list — a signature table and an annex→asset-class map — so **detection and
resolution drifted apart**: the normaliser could detect three annexes but resolved
every one of them through the Annex 2 (RMBS) table. A non-RMBS tape spelling a
column ``outstanding_balance`` was therefore resolved *and cited* as ``RREL18``,
attaching a residential-mortgage field code to a datum that is not one.

This module closes that gap by making the **annex the unit of registration and
the scope of resolution**. One :class:`AnnexSpec` carries, in a single record:

- how to *detect* the annex (:attr:`AnnexSpec.signature_columns`), and
- how to *resolve* its columns (:attr:`AnnexSpec.fields`).

Because both live on one record, they cannot drift — and
:meth:`AnnexRegistry.register` refuses a spec whose signature its own field table
cannot resolve, so the exact condition that produced the original bug is not
registrable.

The contract callers depend on
------------------------------
**A column that is not in the applicable annex's table stays unresolved rather
than guessed.** Resolution is always scoped to one spec; there is deliberately no
"resolve against whatever annex happens to know this column" entry point, because
that is the guessing this module exists to prevent. An unidentifiable tape
resolves nothing and cites no field codes — it degrades honestly instead of
borrowing another asset class's provenance.

Adding an asset class is a **table, not a code change**: write a module with an
:class:`AnnexSpec`, call :func:`register_annex`, and import it from
:mod:`loanwhiz.domain.esma_annexes`. Nothing in the tape normaliser changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Record shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnnexField:
    """One ESMA RTS loan-level field-code mapping record.

    This is the record shape :mod:`loanwhiz.domain.esma_annex2` introduced for
    Annex 2, generalised to any annex. ``Annex2Field`` remains an alias of it, so
    existing callers are unaffected.

    Attributes:
        code:
            The ESMA RTS field code, e.g. ``"RREL18"`` (Annex 2) or ``"CRPL39"``
            (Annex 4). Stable across issuers and vintages — the regulatory anchor
            a citation locator is built from.

            ``None`` marks an **extension field**: a column LoanWhiz resolves
            that the RTS annex does not define. These exist because real tapes
            carry issuer columns with no regulatory counterpart — the Annex 5
            ``vehicle_type`` sentinel is one; the RTS automobile template has no
            vehicle-type field at all. An extension field resolves its column but
            yields **no locator**, so provenance degrades visibly rather than
            attaching a fabricated code to the value. Never invent a code to
            avoid a ``None`` here.
        field_name:
            The semantic field name LoanWhiz uses for this datum (snake_case),
            e.g. ``"current_balance"``.
        description:
            One-line human-readable description of what the field carries.
        canonical_column:
            The canonical (lower-cased) tape column name LoanWhiz normalises this
            field to. Often equals ``field_name`` but differs where a historical
            column name is the canonical one (e.g. ``"cltomv_current"``).
        synonyms:
            Alternative (lower-cased) column names seen across issuers/vintages
            that resolve onto the same canonical column.
    """

    code: str | None
    field_name: str
    description: str
    canonical_column: str
    synonyms: tuple[str, ...] = field(default=())


# Backwards-compatible alias. ``esma_annex2`` introduced this name and external
# callers import it; it is the same record, not a parallel shape.
Annex2Field = AnnexField


# ---------------------------------------------------------------------------
# Annex specification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnnexSpec:
    """One ESMA RTS loan-level annex: how to detect it and how to resolve it.

    Attributes:
        annex_id:
            Stable machine identifier, e.g. ``"annex_2"``. Unique in a registry.
        label:
            Human-readable label surfaced on ``EsmaTapeOutput.annex_detected``,
            e.g. ``"Annex 2 (RMBS)"``.
        asset_class:
            Short asset-class string surfaced on ``EsmaTapeOutput.asset_class``,
            e.g. ``"RMBS"``.
        code_prefixes:
            The field-code prefixes this annex's codes must carry. A tuple
            because an annex may have more than one section: Annex 4 has the
            underlying-exposure table (``CRPL``) *and* a collateral-level table
            (``CRPC``).
        signature_columns:
            **Canonical** column names whose joint presence identifies this
            annex. Every one must be resolvable within this spec's own
            ``fields`` — :meth:`AnnexRegistry.register` enforces it, which is
            what stops detection and resolution drifting apart.
        fields:
            The field table. A load-bearing slice of the annex, not the full
            template — add a row here rather than special-casing a column name
            somewhere downstream.
    """

    annex_id: str
    label: str
    asset_class: str
    code_prefixes: tuple[str, ...]
    signature_columns: frozenset[str]
    fields: tuple[AnnexField, ...]

    # Derived lookup indices, built once at construction.
    _by_code: dict[str, AnnexField] = field(
        init=False, repr=False, compare=False, default_factory=dict
    )
    _by_field: dict[str, AnnexField] = field(
        init=False, repr=False, compare=False, default_factory=dict
    )
    _by_column: dict[str, AnnexField] = field(
        init=False, repr=False, compare=False, default_factory=dict
    )

    def __post_init__(self) -> None:
        """Build the code / field-name / column indices.

        The column index maps each record's ``canonical_column`` and every
        ``synonym`` onto that record, all lower-cased, so issuer/vintage column
        drift resolves onto one canonical field. Canonical columns are indexed
        last so a synonym can never shadow a column that is canonical for some
        other row in the same table.
        """
        for rec in self.fields:
            if rec.code is not None:
                self._by_code[rec.code.lower()] = rec
            self._by_field[rec.field_name.lower()] = rec
            for syn in rec.synonyms:
                self._by_column.setdefault(syn.lower(), rec)
        for rec in self.fields:
            self._by_column[rec.canonical_column.lower()] = rec

    # -- resolution -------------------------------------------------------

    def field_for_code(self, code: str) -> AnnexField | None:
        """Return the field for an RTS code, or ``None``. Case-insensitive."""
        return self._by_code.get(code.strip().lower())

    def field_for_name(self, field_name: str) -> AnnexField | None:
        """Return the field for a semantic field name, or ``None``."""
        return self._by_field.get(field_name.strip().lower())

    def field_for_column(self, column: str) -> AnnexField | None:
        """Return the field a tape column resolves to **in this annex**, or ``None``."""
        return self._by_column.get(column.strip().lower())

    def canonical_column_for(self, column: str) -> str | None:
        """Resolve a (possibly issuer-specific) column to its canonical name.

        ``None`` when the column is not in **this annex's** table — the caller
        keeps its existing fallback rather than receiving another annex's guess.
        A column already in canonical form resolves to itself.
        """
        rec = self.field_for_column(column)
        return rec.canonical_column if rec is not None else None

    def code_for_column(self, column: str) -> str | None:
        """Return the RTS field code for a tape column, or ``None``.

        ``None`` covers two distinct cases that are both honest answers: the
        column is not in this annex's table, or it resolves to an **extension
        field** the RTS does not define (:attr:`AnnexField.code`).
        """
        rec = self.field_for_column(column)
        return rec.code if rec is not None else None

    def locator_for(self, field_name: str) -> str | None:
        """Return the citation locator for a semantic field, or ``None``.

        The locator is the ``"<code> · <description>"`` string that belongs in
        ``Citation.page_or_row`` so a tape-sourced value stays traceable to the
        regulatory field it came from. ``None`` when the field is unknown here or
        is an extension field with no regulatory code — provenance is left
        visibly absent rather than fabricated.
        """
        rec = self.field_for_name(field_name)
        if rec is None or rec.code is None:
            return None
        return f"{rec.code} · {rec.description}"

    # -- detection --------------------------------------------------------

    def matches(self, columns: set[str]) -> bool:
        """Whether *columns* satisfy this annex's signature.

        A signature column counts as present when the tape carries it under its
        canonical name **or** under a synonym this annex registers — so an issuer
        spelling EPC ``epc_rating`` is still detected as Annex 2. Resolution is
        scoped to this spec, so another annex's synonyms never leak into the
        match.
        """
        lowered = {c.strip().lower() for c in columns}
        resolved = {
            canonical
            for c in lowered
            if (canonical := self.canonical_column_for(c)) is not None
        }
        return self.signature_columns <= (lowered | resolved)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class AnnexRegistry:
    """Ordered registry of :class:`AnnexSpec`, with validation at registration.

    Detection walks specs in registration order and returns the first match, so
    ordering is deterministic and reviewable rather than dict-hash dependent.
    """

    def __init__(self) -> None:
        self._specs: list[AnnexSpec] = []

    def register(self, spec: AnnexSpec) -> AnnexSpec:
        """Validate and register *spec*; returns it so modules can assign the result.

        Raises:
            ValueError: on any of the conditions below. Each is a mistake that
                would otherwise surface as a silently mis-resolved column much
                later, so it fails loudly here at the boundary instead.
        """
        if any(s.annex_id == spec.annex_id for s in self._specs):
            raise ValueError(f"annex_id {spec.annex_id!r} is already registered")
        if any(s.label == spec.label for s in self._specs):
            raise ValueError(f"annex label {spec.label!r} is already registered")
        if not spec.code_prefixes:
            raise ValueError(f"{spec.annex_id}: at least one code prefix is required")
        if not spec.signature_columns:
            raise ValueError(
                f"{spec.annex_id}: signature_columns is empty — the annex could "
                "never be detected"
            )

        seen_codes: set[str] = set()
        seen_names: set[str] = set()
        for rec in spec.fields:
            name = rec.field_name.lower()
            if name in seen_names:
                raise ValueError(f"{spec.annex_id}: duplicate field_name {name!r}")
            seen_names.add(name)
            if rec.code is None:
                continue
            code = rec.code.lower()
            if code in seen_codes:
                raise ValueError(f"{spec.annex_id}: duplicate field code {rec.code!r}")
            seen_codes.add(code)
            if not rec.code.upper().startswith(spec.code_prefixes):
                raise ValueError(
                    f"{spec.annex_id}: field code {rec.code!r} does not carry one "
                    f"of this annex's prefixes {spec.code_prefixes}"
                )

        # The anti-drift guard. A signature column its own table cannot resolve
        # is exactly how detection and resolution came apart in the first place:
        # the annex would be detected and then resolve nothing.
        unresolvable = sorted(
            col for col in spec.signature_columns if spec.canonical_column_for(col) is None
        )
        if unresolvable:
            raise ValueError(
                f"{spec.annex_id}: signature column(s) {unresolvable} are not "
                "resolvable in this annex's own field table — detection would "
                "succeed and resolution would not. Add a field row (an extension "
                "field with code=None if the RTS defines none)."
            )

        self._specs.append(spec)
        return spec

    def detect(self, columns: set[str]) -> AnnexSpec | None:
        """Return the first registered annex whose signature *columns* satisfy.

        ``None`` when no annex matches — the caller must then resolve nothing,
        rather than falling back to an arbitrary table.
        """
        for spec in self._specs:
            if spec.matches(columns):
                return spec
        return None

    def get(self, annex_id: str) -> AnnexSpec | None:
        """Return the registered spec with this ``annex_id``, or ``None``."""
        for spec in self._specs:
            if spec.annex_id == annex_id:
                return spec
        return None

    def all(self) -> tuple[AnnexSpec, ...]:
        """Every registered spec, in registration (detection) order."""
        return tuple(self._specs)


#: The process-wide annex registry. Populated by importing
#: :mod:`loanwhiz.domain.esma_annexes`, which pulls in each annex table module.
ANNEX_REGISTRY = AnnexRegistry()


def register_annex(spec: AnnexSpec) -> AnnexSpec:
    """Register *spec* in the process-wide :data:`ANNEX_REGISTRY`."""
    return ANNEX_REGISTRY.register(spec)
