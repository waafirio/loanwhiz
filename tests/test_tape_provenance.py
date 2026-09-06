"""Contract tests for the derived-vs-filed distinction and the locator guard (#470).

These pin a claim, not a code path. The failure they exist to prevent leaves
every number correct and every test green: a tape derived from a trustee report
presented as one filed under Article 7(1)(a), or an approximate correspondence
carrying a regulatory field code. Nothing breaks — the data simply inherits an
authority it never had.

So the assertions below are about what must **not** be possible:

- a tape whose origin was never stated (no default source kind);
- a derived tape describing itself as filed, or as a regulatory disclosure;
- an approximate mapping resolving onto a column the RTS codes;
- an exact mapping resolving onto a code-less column and silently yielding no
  locator, which reads as a deliberate absence but is a missing table row.
"""

from __future__ import annotations

# Import a ``primitives`` module before ``loanwhiz.domain`` so the package-init
# import cycle resolves. Same convention as ``tests/test_esma_annex_registry.py``.
import loanwhiz.primitives  # noqa: F401  (import-order side effect)

import pytest
from pydantic import ValidationError

from loanwhiz.domain.esma_annex4_corporate import ANNEX4_CORPORATE
from loanwhiz.domain.tape_provenance import (
    AbsentColumn,
    ApproximateCorrespondenceError,
    Correspondence,
    TapeSourceKind,
    locator_for_correspondence,
)


# ---------------------------------------------------------------------------
# The source kind
# ---------------------------------------------------------------------------


def test_every_source_kind_declares_its_facts() -> None:
    """Coverage in both directions (#453): the guard catches a *missing* member.

    A registration-style table that silently misses a member would let a newly
    added kind answer ``is_regulatory_filing`` by accident. There is no
    ``.get(..., default)`` in the module, so a gap raises rather than defaults —
    this asserts every member is genuinely covered today.
    """
    for kind in TapeSourceKind:
        assert isinstance(kind.is_regulatory_filing, bool)
        assert isinstance(kind.rts_coded_values, bool)
        assert len(kind.disclosure) >= 40


def test_derived_is_not_a_regulatory_filing() -> None:
    kind = TapeSourceKind.DERIVED_FROM_INVESTOR_REPORT
    assert kind.is_regulatory_filing is False
    assert kind.rts_coded_values is False


def test_filed_is_a_regulatory_filing() -> None:
    kind = TapeSourceKind.FILED_ARTICLE_7_1_A
    assert kind.is_regulatory_filing is True
    assert kind.rts_coded_values is True


def test_derived_disclosure_states_it_is_not_filed() -> None:
    """The disclosure must make the distinction, not merely omit the claim.

    A reader who sees only this sentence must not be able to mistake the tape
    for an ESMA filing, so it names Article 7(1)(a) and denies it.
    """
    disclosure = TapeSourceKind.DERIVED_FROM_INVESTOR_REPORT.disclosure
    assert "Article 7(1)(a)" in disclosure
    assert "NOT filed" in disclosure


def test_derived_disclosure_does_not_claim_to_be_a_filing() -> None:
    """Assert the wrong wording is *absent*, not just that the right one is present.

    A test checking only that the correct sentence appears passes while a second
    sentence beside it says the opposite (#457).
    """
    disclosure = TapeSourceKind.DERIVED_FROM_INVESTOR_REPORT.disclosure.lower()
    assert "filed by the originator" not in disclosure
    assert "is a regulatory disclosure" not in disclosure


def test_source_kinds_are_channels_not_deals() -> None:
    """"Derived from an investor report" is a source kind, not a Cairn special case."""
    for kind in TapeSourceKind:
        assert "cairn" not in kind.value.lower()
        assert "cairn" not in kind.disclosure.lower()


# ---------------------------------------------------------------------------
# The locator guard — both directions
# ---------------------------------------------------------------------------


def test_exact_correspondence_onto_a_coded_column_yields_its_locator() -> None:
    locator = locator_for_correspondence(
        ANNEX4_CORPORATE, "current_balance", Correspondence.EXACT
    )
    assert locator is not None
    assert locator.startswith("CRPL39 · ")


def test_approximate_correspondence_onto_a_codeless_column_yields_no_locator() -> None:
    """Provenance visibly absent, which is the whole point of the extension field."""
    assert (
        locator_for_correspondence(
            ANNEX4_CORPORATE, "sp_industry", Correspondence.APPROXIMATE
        )
        is None
    )


def test_approximate_correspondence_onto_a_coded_column_is_refused() -> None:
    """The laundering case. Mapping an S&P industry onto CRPL14 must be impossible.

    ``industry_code`` is the canonical column CRPL14 owns; claiming an
    approximate fit onto it is exactly the "close enough" move this guard exists
    to refuse.
    """
    with pytest.raises(ApproximateCorrespondenceError, match="CRPL14"):
        locator_for_correspondence(
            ANNEX4_CORPORATE, "industry_code", Correspondence.APPROXIMATE
        )


def test_approximate_correspondence_onto_nuts3_is_refused() -> None:
    with pytest.raises(ApproximateCorrespondenceError, match="CRPL10"):
        locator_for_correspondence(
            ANNEX4_CORPORATE, "province", Correspondence.APPROXIMATE
        )


def test_exact_correspondence_onto_a_codeless_column_is_refused() -> None:
    """The direction that ships (#453): a *missing* table row, failing silently.

    Without this half, declaring EXACT on an extension column would quietly
    yield no locator — indistinguishable from a deliberate absence.
    """
    with pytest.raises(ApproximateCorrespondenceError, match="extension field"):
        locator_for_correspondence(
            ANNEX4_CORPORATE, "sp_industry", Correspondence.EXACT
        )


def test_a_column_outside_the_annex_table_is_refused() -> None:
    """Mapping onto a column the annex lacks is a table gap, not a resolution."""
    with pytest.raises(KeyError):
        locator_for_correspondence(
            ANNEX4_CORPORATE, "not_a_real_column", Correspondence.EXACT
        )


# ---------------------------------------------------------------------------
# Declared absence
# ---------------------------------------------------------------------------


def test_absent_column_requires_a_substantive_reason() -> None:
    """An absence with no reason is indistinguishable from an oversight."""
    with pytest.raises(ValidationError):
        AbsentColumn(canonical_column="market_value", rts_code="CRPL41", reason="n/a")


def test_absent_column_accepts_a_real_reason() -> None:
    entry = AbsentColumn(
        canonical_column="market_value",
        rts_code="CRPL41",
        reason="The report states a price per 100 of par, not a value amount.",
    )
    assert entry.rts_code == "CRPL41"
