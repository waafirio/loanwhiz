"""What a loan tape *is*, as distinct from what it says.

A loan-level tape can reach LoanWhiz two ways, and they are not interchangeable:

- **Filed** under Article 7(1)(a) of the Securitisation Regulation — the
  originator/sponsor/SSPE's own disclosure, in the ESMA RTS annex template, with
  the field codes and the vocabularies the template mandates.
- **Derived** from an investor or trustee report — reconstructed by LoanWhiz
  from a document published for a different purpose, resolved onto the same
  canonical columns so downstream analytics work, but *not* a regulatory filing
  and not stated in the RTS's coded vocabularies.

The two are indistinguishable once the numbers are in a DataFrame, and that is
exactly the problem. A derived tape presented as a filed one is **provenance
laundering**: every consumer downstream — a coverage ratio, a concentration
test, a governance view citing field codes — inherits an authority the data
never had. So the distinction is carried as a *required* value on the tape
itself rather than as prose in a docstring somebody may not read.

Why a source *kind* and not a deal flag
---------------------------------------
"Derived from an investor/trustee report" is a property of the **channel**, not
of any one deal. European and US CLOs are typically private transactions for
Securitisation-Regulation purposes and list on exchange-regulated markets, so
their asset-level reports go to Competent Authorities, Noteholders and
prospective investors rather than to a securitisation repository. Any deal in
that position is reachable only through the derived channel. The enum below
therefore names channels; nothing here names an issuer.

What this module does **not** do
--------------------------------
It defines no second provenance record. Per-field provenance already has a home
in :mod:`loanwhiz.domain.provenance` — :class:`FieldProvenance` keyed into a
:data:`ProvenanceMap`, whose ``citation.page_or_row`` is where a regulatory
locator belongs and whose ``citation`` may carry **no** locator at all. A tape
mapper populates that map; it does not invent a parallel one.

Import note
-----------
``loanwhiz.domain``'s package ``__init__`` participates in an import cycle with
``loanwhiz.primitives``. Import a ``loanwhiz.primitives`` module before this one,
as :mod:`loanwhiz.domain.esma_annex_registry`'s existing callers already do.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from loanwhiz.domain.esma_annex_registry import AnnexSpec

__all__ = [
    "TapeSourceKind",
    "Correspondence",
    "AbsentColumn",
    "ApproximateCorrespondenceError",
    "locator_for_correspondence",
]


# ---------------------------------------------------------------------------
# The channel a tape arrived through
# ---------------------------------------------------------------------------


class TapeSourceKind(str, Enum):
    """How a loan tape reached LoanWhiz — a closed vocabulary.

    There is deliberately **no default member**. A tape's kind is a fact about
    where it came from, and a default would let the most consequential claim in
    the system be made by omission.
    """

    #: Disclosed by the originator/sponsor/SSPE under Securitisation Regulation
    #: Art. 7(1)(a), in the ESMA RTS annex template.
    FILED_ARTICLE_7_1_A = "filed_article_7_1_a"

    #: Reconstructed by LoanWhiz from an investor or trustee report. Resolved
    #: onto canonical annex columns for analysis; **not** a regulatory filing.
    DERIVED_FROM_INVESTOR_REPORT = "derived_from_investor_report"

    @property
    def is_regulatory_filing(self) -> bool:
        """Whether a tape of this kind *is* a regulatory disclosure.

        The single predicate every provenance surface should branch on, so no
        surface has to re-derive the distinction from a label string.
        """
        return _FACTS[self].is_regulatory_filing

    @property
    def rts_coded_values(self) -> bool:
        """Whether values are stated in the RTS's own coded vocabularies.

        A filed tape carries RTS codes (``DFLT``, ``SNDB``, a NACE code). A
        derived tape carries the *source document's* words, because translating
        them into RTS codes would be a second mapping with no source behind it.
        Consumers comparing against an RTS code must check this first.
        """
        return _FACTS[self].rts_coded_values

    @property
    def disclosure(self) -> str:
        """One sentence stating what this tape is, for any operator-facing surface.

        Written to be quoted verbatim. A surface that renders a tape's origin
        should render this rather than compose its own wording, so the claim
        cannot drift between views.
        """
        return _FACTS[self].disclosure


class _SourceKindFacts(BaseModel):
    """The facts each :class:`TapeSourceKind` member carries."""

    model_config = {"frozen": True}

    is_regulatory_filing: bool
    rts_coded_values: bool
    disclosure: str = Field(min_length=40)


#: Facts per member. Guarded for **total** coverage at import (below): a
#: registration-style table that silently misses a member would make a newly
#: added kind well-formed by accident, which is how a closed enum stops being
#: closed. There is no ``.get(..., default)`` anywhere in this module.
_FACTS: dict[TapeSourceKind, _SourceKindFacts] = {
    TapeSourceKind.FILED_ARTICLE_7_1_A: _SourceKindFacts(
        is_regulatory_filing=True,
        rts_coded_values=True,
        disclosure=(
            "Filed by the originator, sponsor or SSPE under Article 7(1)(a) of "
            "the Securitisation Regulation, in the ESMA RTS annex template."
        ),
    ),
    TapeSourceKind.DERIVED_FROM_INVESTOR_REPORT: _SourceKindFacts(
        is_regulatory_filing=False,
        rts_coded_values=False,
        disclosure=(
            "Derived by LoanWhiz from an investor/trustee report; NOT filed "
            "under Article 7(1)(a) and not a regulatory disclosure. Values are "
            "the source document's own, not ESMA RTS codes."
        ),
    ),
}

_missing = sorted(kind.value for kind in TapeSourceKind if kind not in _FACTS)
if _missing:  # pragma: no cover - import-time guard, asserted by test
    raise RuntimeError(
        f"TapeSourceKind members {_missing} carry no facts. Every member must "
        "declare is_regulatory_filing / rts_coded_values / disclosure: a member "
        "with no entry would answer the derived-vs-filed question by accident."
    )
del _missing


# ---------------------------------------------------------------------------
# How closely a source column corresponds to an annex field
# ---------------------------------------------------------------------------


class Correspondence(str, Enum):
    """How a source column relates to the annex field it resolves onto."""

    #: The source column *is* the annex field's datum. A regulatory locator is
    #: genuine and must be emitted.
    EXACT = "exact"

    #: The source column is near the annex field but not the same datum — a
    #: different classification scheme, a different geography, a different unit.
    #: It must resolve onto a **code-less** column, so no locator can be built.
    APPROXIMATE = "approximate"


class ApproximateCorrespondenceError(ValueError):
    """Raised when a declared correspondence contradicts the annex table.

    Both directions are errors, and the second is the one that ships (#453):

    - ``APPROXIMATE`` onto a column the annex *does* code — the laundering this
      module exists to prevent, caught before a fabricated locator can be built.
    - ``EXACT`` onto a column the annex does *not* code — a silently
      locator-less field that reads as a deliberate absence but is really a
      missing table row.
    """


def locator_for_correspondence(
    spec: AnnexSpec,
    canonical_column: str,
    correspondence: Correspondence,
) -> str | None:
    """Return the regulatory locator a mapping row is entitled to.

    This is the single decision point for "does this value get a field code?".
    It refuses rather than resolving by convention, so an approximate mapping
    onto a regulatory code is not a thing anyone can write and have work.

    Args:
        spec: The annex the tape resolves through.
        canonical_column: The canonical column the source field maps onto.
        correspondence: What the mapping author is claiming about the fit.

    Returns:
        The ``"<code> · <description>"`` locator for an ``EXACT`` mapping, or
        ``None`` for an ``APPROXIMATE`` one — provenance visibly absent.

    Raises:
        ApproximateCorrespondenceError: when the claim and the annex table
            disagree in either direction.
        KeyError: when *canonical_column* is not in this annex's table at all.
    """
    record = spec.field_for_column(canonical_column)
    if record is None:
        raise KeyError(
            f"{spec.annex_id}: no field resolves column {canonical_column!r}. "
            "Add a row to the annex table (an extension field with code=None if "
            "the RTS defines none) rather than mapping onto a column it lacks."
        )

    if correspondence is Correspondence.EXACT:
        if record.code is None:
            raise ApproximateCorrespondenceError(
                f"{spec.annex_id}: column {canonical_column!r} is an extension "
                "field with no RTS code, so an EXACT correspondence cannot yield "
                "a locator. Either the annex table is missing the real field, or "
                "this mapping is APPROXIMATE and should say so."
            )
        return spec.locator_for(record.field_name)

    if record.code is not None:
        raise ApproximateCorrespondenceError(
            f"{spec.annex_id}: column {canonical_column!r} carries RTS code "
            f"{record.code!r}, so an APPROXIMATE mapping onto it would attach a "
            "regulatory locator to a value that is not that field's datum. Map "
            "it onto a code-less extension column instead."
        )
    return None


# ---------------------------------------------------------------------------
# Columns a source cannot supply
# ---------------------------------------------------------------------------


class AbsentColumn(BaseModel):
    """A canonical column the annex defines and this source does not supply.

    Absence is a *fact worth stating*, not an empty slot. The failure this
    record exists to prevent is the one #451 found in this very annex: Annex 4
    carries no default flag (default is an ``account_status`` value), so a
    corporate tape whose obligors are defaulted reported ``default_pct: 0.0`` —
    a silent zero indistinguishable from a clean pool.

    A mapper therefore emits **no key at all** for these columns, and declares
    them here so a consumer can tell "the source does not publish this" from
    "the source publishes this and it is zero".
    """

    model_config = {"frozen": True}

    canonical_column: str = Field(min_length=1)
    rts_code: str | None = Field(
        default=None,
        description="The RTS field code this column would carry, when it has one.",
    )
    reason: str = Field(
        min_length=20,
        description=(
            "Why this source cannot supply the column. An assertion about the "
            "world: say only what the source encodes, never that the datum does "
            "not exist."
        ),
    )
