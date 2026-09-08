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

from loanwhiz.config import DEAL_REGISTRY
from loanwhiz.domain.esma_annex4_corporate import ANNEX4_CORPORATE
from loanwhiz.domain.tape_provenance import (
    AbsentColumn,
    ApproximateCorrespondenceError,
    Correspondence,
    TapeChannel,
    TapeScheme,
    TapeSourceKind,
    channel_for,
    kind_for_scheme,
    locator_for_correspondence,
    scheme_for,
    source_kind_for,
    underlying_url,
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
        assert isinstance(kind.describes_real_assets, bool)
        assert isinstance(kind.channel, TapeChannel)
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


# ---------------------------------------------------------------------------
# Synthetic provenance (#483, epic #482)
# ---------------------------------------------------------------------------
#
# The failure these pin shipped, and left every number correct: Green Lion
# 2026-1's tapes are generated, said so in their filename, and reported
# `data_source='direct'` — the ingestion channel of a filed regulatory tape.
# Nothing broke. The pool simply inherited an authority it never had, in the
# evidence pack, the capability matrix and the citation excerpt alike.
#
# So the assertions below are again about what must not be *possible*: a kind
# whose rows describe nobody reporting the channel of a published filing, a
# scheme with no declared kind, or a synthetic tape registered without saying so.


def test_synthetic_is_neither_a_filing_nor_a_record_of_real_assets() -> None:
    kind = TapeSourceKind.SYNTHETIC_GENERATED
    assert kind.is_regulatory_filing is False
    assert kind.rts_coded_values is False
    assert kind.describes_real_assets is False
    assert kind.channel is TapeChannel.SYNTHETIC


def test_describes_real_assets_separates_synthetic_from_derived() -> None:
    """The two predicates answer different questions and must not collapse.

    A derived tape is not a filing but does describe real loans; a synthetic one
    is not a filing and describes none. Folding "not a filing" into "not real"
    would let a trustee-report tape read as fabricated, which is its own
    dishonesty.
    """
    derived = TapeSourceKind.DERIVED_FROM_INVESTOR_REPORT
    synthetic = TapeSourceKind.SYNTHETIC_GENERATED
    assert derived.is_regulatory_filing == synthetic.is_regulatory_filing
    assert derived.describes_real_assets is not synthetic.describes_real_assets


def test_the_synthetic_disclosure_says_it_is_not_evidence() -> None:
    """The sentence every surface quotes must carry the claim that matters.

    A disclosure naming only "generated" leaves a reader to infer the
    consequence. State it: no figure computed from the tape is evidence about a
    real pool.
    """
    disclosure = TapeSourceKind.SYNTHETIC_GENERATED.disclosure.lower()
    assert "not" in disclosure
    assert "real" in disclosure
    assert "evidence" in disclosure


def test_a_facts_entry_omitting_the_channel_is_refused() -> None:
    """The channel is a *required* fact, not one that can default to `direct`.

    This is the guard that makes the impossibility contract hold for kinds that
    do not exist yet: a member added without a channel cannot silently inherit
    the one a published filing reports, because there is nothing to inherit —
    the table refuses to construct at import.
    """
    from loanwhiz.domain.tape_provenance import _SourceKindFacts

    with pytest.raises(ValidationError):
        _SourceKindFacts(
            is_regulatory_filing=False,
            rts_coded_values=False,
            describes_real_assets=False,
            disclosure=(
                "A kind whose facts entry omits the ingestion channel it is "
                "reported under, which must not be constructible."
            ),
        )


def test_every_scheme_declares_a_source_kind() -> None:
    """Totality in both directions, as for the facts table.

    A scheme with no kind would make the most consequential claim about a tape
    by falling off the end of a lookup, which is the exact defect #470's guard
    exists to prevent — restated here because the registry grew a member.
    """
    for scheme in TapeScheme:
        assert isinstance(kind_for_scheme(scheme), TapeSourceKind)


def test_a_kind_describing_no_real_assets_can_never_report_direct() -> None:
    """The impossibility contract, over every scheme rather than one example.

    `direct` is what a published regulatory filing reports. This asserts no
    identifier whose rows describe nobody can resolve to it — not for the
    schemes that exist today, but for any scheme the enum ever carries.
    """
    for scheme in TapeScheme:
        if kind_for_scheme(scheme).describes_real_assets:
            continue
        url = f"{scheme.value}:https://example.invalid/pool.csv"
        assert channel_for(url) is not TapeChannel.DIRECT


def test_an_undeclared_identifier_is_direct_but_claims_no_kind() -> None:
    """`None` means "not declared" — deliberately not "filed".

    The channel still has to be *something* for an ordinary published URL, and
    `direct` is what that has always meant. But answering the channel must not
    smuggle in an answer to the different question of what the tape is: this
    repo holds no evidence that its published tape files are their originator's
    Article 7(1)(a) disclosure.
    """
    plain = "https://example.invalid/pool.csv"
    assert channel_for(plain) is TapeChannel.DIRECT
    assert source_kind_for(plain) is None
    assert scheme_for(plain) is None


def test_underlying_url_round_trips() -> None:
    """Declaring a tape synthetic changes its identifier, not the file it names.

    The round-trip is what makes the re-identification of an existing tape safe:
    strip the scheme and the byte-identical URL comes back, so the same bytes
    are fetched and every pool figure is unchanged.
    """
    file_url = "https://example.invalid/a_synthetic_loan_tape.csv"
    declared = f"{TapeScheme.SYNTHETIC.value}:{file_url}"
    assert underlying_url(declared) == file_url
    assert underlying_url(file_url) == file_url
    assert channel_for(declared) is TapeChannel.SYNTHETIC


def test_a_synthetic_scheme_survives_a_query_string_and_a_fragment() -> None:
    """Stripping is by scheme prefix, so the rest of the URL is untouched."""
    file_url = "https://example.invalid/pool.parquet?token=abc#frag"
    assert underlying_url(f"synthetic:{file_url}") == file_url


def test_registered_synthetic_tapes_declare_the_scheme() -> None:
    """Census over the real registry: a tape naming itself synthetic must say so.

    This is the checker for the mistake that produced #483 — a generated tape
    registered as a plain URL, where the only trace of "synthetic" is a filename
    nothing reads. It passes by finding nothing, so the failure it must be able
    to report is an *added* offender; the assertion names them rather than
    counting, so a red run says which.

    A filename is evidence of a registration mistake, never of provenance: this
    refuses such a tape rather than classifying it, because inferring what a
    tape is from a substring is the very thing the scheme exists to replace.
    """
    offenders = []
    for deal_id, deal in DEAL_REGISTRY.items():
        for tape in deal.get("tape_urls") or []:
            url = tape.get("url", "")
            filename = underlying_url(url).rsplit("/", 1)[-1].lower()
            if "synthetic" not in filename:
                continue
            if channel_for(url) is not TapeChannel.SYNTHETIC:
                offenders.append(f"{deal_id}: {url}")
    assert not offenders, (
        "these registered tapes name themselves synthetic but do not declare it "
        "in their identifier, so they report the provenance of a filed "
        f"regulatory tape: {offenders}. Prefix the URL with "
        f"'{TapeScheme.SYNTHETIC.value}:'."
    )
