"""The risk-retention undertaking, read from the document that states it (#566).

What these tests are for
------------------------
An institutional investor must verify, before holding a securitisation position,
that a named entity retains a material net economic interest of not less than
5% — and must be able to say **by which Article 6(3) method**. "Retention
verified" without the method is not what the obligation asks for.

The property under test is therefore *where the answer comes from*, not that a
5% appears somewhere. Three separate things are asserted below and they are
deliberately not collapsed:

1. **The undertaking parses** from each CLO's own words.
2. **The regulation's generic description is refused.** Cairn's offering
   circular states "not less than five per cent." four pages before it states
   the deal's undertaking, and those pages name no retaining entity. A parser
   that searched for the figure would find them first and produce a record with
   a level and no method — confidently incomplete, which is the failure mode
   this epic exists to remove.
3. **A section that did not arrive is distinguished from a document that says
   nothing.** Cairn's committed glossary stops at ``Payment Date``, so the
   whole R range is lost to the ``max_chars`` truncation
   #548 landed its coverage check for. Judged from that artefact the honest-
   looking conclusion is "this deal states no retention", and it is false.
   :func:`assess_retention` is what makes that a refusal rather than an absence.

Why the citation is the locator, not the words
----------------------------------------------
The three real documents exercised here write the same citation three ways and
pick two different methods — Cairn cites ``Article 6(3)(d)`` and never names
the method in words, Contego names "first loss tranche" *and* cites it, and
Leone Arancio writes ``option 3 (a) of article 6`` for a different method
entirely. Keying on the words would find one document and refuse two.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from loanwhiz.extraction.assembler import DealModel
from loanwhiz.extraction.retention_parser import (
    RetentionCoverage,
    RiskRetention,
    UnsourcedRetention,
    assess_retention,
    parse_retention_method,
    parse_risk_retention,
)

TESTS_DIR = Path(__file__).resolve().parent
FIXTURES = TESTS_DIR / "fixtures" / "prospectus"
CAIRN_FIXTURE = FIXTURES / "cairn-clo-xvii-retention-excerpt.txt"
CONTEGO_FIXTURE = FIXTURES / "contego-clo-xi-retention-excerpt.txt"

SEED_DIR = TESTS_DIR.parent / "src" / "loanwhiz" / "data" / "deals" / "seed"
CAIRN_SEED = SEED_DIR / "cairn-clo-xvii-dac.json"
LEONE_SEED = SEED_DIR / "leone-arancio-rmbs-2023-1-srl.json"


def _page(fixture: Path, marker: str) -> str:
    """One ``--- page N ---`` block of a verbatim pypdf excerpt.

    The fixtures keep the page markers so a reviewer can find the sentence in
    the source document, and so the generic description and the undertaking can
    be handed to the parser separately — they sit 239 pages apart in the real
    offering circular and must not be tested as one blob.
    """
    text = fixture.read_text(encoding="utf-8")
    assert marker in text, f"{fixture.name} has no {marker!r} block"
    after = text.split(marker, 1)[1]
    return after.split("--- page ", 1)[0]


@pytest.fixture
def cairn_undertaking() -> str:
    """Cairn CLO XVII's own retention undertaking (p.282)."""
    return _page(CAIRN_FIXTURE, "--- page 282 ---")


@pytest.fixture
def cairn_generic() -> str:
    """The *Regulation's* description of the Retention Requirements (p.43).

    Not this deal's undertaking: it states the five-per-cent. floor and names no
    retaining entity, because it is describing what the law requires of
    originators in general.
    """
    return _page(CAIRN_FIXTURE, "--- page 43 ---")


@pytest.fixture
def contego_undertaking() -> str:
    """Contego CLO XI's retention undertaking (p.273, the 29-Jun-2023 document)."""
    return _page(CONTEGO_FIXTURE, "--- page 273 ---")


@pytest.fixture
def leone_retention_covenant() -> str:
    """Leone Arancio's retention covenant, already committed as extracted prose.

    Reached through ``issuer_covenants`` rather than a new fixture because it is
    a real committed artefact of the existing pipeline — a free-string list
    nothing reads. It is the only committed text in this repo citing a *different*
    Article 6(3) method, and it writes the citation in a third form.
    """
    seed = json.loads(LEONE_SEED.read_text(encoding="utf-8"))
    return seed["covenants"]["issuer_covenants"][0]


# ---------------------------------------------------------------------------
# 1. The undertaking, read from each document's own words
# ---------------------------------------------------------------------------


def test_cairn_states_a_first_loss_tranche_retention(cairn_undertaking: str) -> None:
    """Every limb the obligation asks for, from Cairn's own sentence."""
    retention = parse_risk_retention(cairn_undertaking)

    assert retention.retainer == "The Investment Manager"
    assert retention.retainer_capacity == "originator"
    assert retention.method_letter == "d"
    assert retention.method == "first-loss tranche"
    assert retention.article == "Article 6(3)(d)"
    assert retention.level_pct == 5.0
    assert retention.level_basis == "the Aggregate Collateral Balance"
    assert retention.instrument == "Subordinated Notes"


def test_contego_states_the_same_method_in_different_words(
    contego_undertaking: str,
) -> None:
    """Same method, different entity, and a level measured on a different base.

    Contego names the method in words *and* cites it; Cairn only cites it. Both
    must yield ``d`` — which is the whole reason the citation is the locator.
    """
    retention = parse_risk_retention(contego_undertaking)

    assert retention.retainer == "Five Arrows Global Loan Investments II PLC"
    assert retention.retainer_capacity == "originator"
    assert retention.method_letter == "d"
    assert retention.level_pct == 5.0
    assert retention.instrument == "Subordinated Notes"
    # pypdf splits words in this document ("securiti sed"), so the base is
    # compared without whitespace rather than papered over with a rejoining
    # heuristic that would be free to invent tokens the page does not carry.
    assert (
        re.sub(r"\s+", "", retention.level_basis)
        == "thenominalvalueofthesecuritisedexposures"
    )


def test_the_method_is_read_from_the_citation_not_the_words(
    cairn_undertaking: str,
) -> None:
    """Cairn never writes "first loss tranche" — and still resolves to it.

    The anti-vacuity guard for the test above: if the parser were secretly
    keying on the words, this document would refuse.
    """
    assert "first loss tranche" not in re.sub(r"\s+", " ", cairn_undertaking).lower()
    assert parse_risk_retention(cairn_undertaking).method == "first-loss tranche"


def test_the_two_clos_name_different_retainers(
    cairn_undertaking: str, contego_undertaking: str
) -> None:
    """A retainer parsed from the wrong span would collide across documents."""
    assert (
        parse_risk_retention(cairn_undertaking).retainer
        != parse_risk_retention(contego_undertaking).retainer
    )


# ---------------------------------------------------------------------------
# 2. The method map: a closed set, three citation forms
# ---------------------------------------------------------------------------


def test_the_inverted_citation_form_is_read(leone_retention_covenant: str) -> None:
    """``option 3 (a) of article 6`` is the same citation, written backwards.

    A third surface form, a third document, and the *only* committed text in
    this repo naming a method other than the first-loss tranche.
    """
    assert parse_retention_method(leone_retention_covenant) == "a"


@pytest.mark.parametrize(
    ("letter", "method"),
    [
        ("a", "vertical slice"),
        ("b", "seller's share"),
        ("c", "randomly selected exposures"),
        ("d", "first-loss tranche"),
        ("e", "first-loss exposure"),
    ],
)
def test_every_article_6_3_sub_paragraph_resolves_to_its_method(
    letter: str, method: str
) -> None:
    """All five methods the Regulation permits, none of them a default.

    A citation is either one of these or it is refused; there is no nearest
    neighbour and no fall-through (#453).
    """
    text = (
        "X Capital LLP shall act as Retention Holder. It qualifies as an "
        '"originator" and will retain a material net economic interest of not '
        f"less than five per cent. of the Aggregate Collateral Balance in "
        f"accordance with Article 6(3)({letter}) of the Securitisation "
        "Regulations."
    )
    assert parse_retention_method(text) == letter
    assert parse_risk_retention(text).method == method


def test_words_contradicting_the_citation_are_refused() -> None:
    """Two readings of one document disagree, so neither is reported.

    Silently preferring either would make a record that says "verified" out of
    a document that contradicts itself.
    """
    text = (
        "X Capital LLP shall act as Retention Holder. It qualifies as an "
        '"originator" and will retain a material net economic interest in the '
        "first loss tranche of not less than five per cent. of the Aggregate "
        "Collateral Balance in accordance with Article 6(3)(a) of the "
        "Securitisation Regulations."
    )
    with pytest.raises(UnsourcedRetention, match="disagree"):
        parse_retention_method(text)


# ---------------------------------------------------------------------------
# 2b. The span: every limb belongs to *this* undertaking
# ---------------------------------------------------------------------------
#
# The four tests below are the gap the first self-review found. Until they
# existed every input was either a hand-sliced page or a short synthetic
# string, so nothing exercised what happens when the parser is handed more
# text than the undertaking — which is what a caller with a document actually
# has.


def test_the_undertaking_is_found_beside_the_generic_description() -> None:
    """Both pages at once: the deal's commitment wins over the Regulation's prose.

    Page 43 states the same five-per-cent. floor and names no retainer; page 282
    states the undertaking. Handed both, the parser must return the second — a
    reader that took the first match would report a level with no method.
    """
    both = CAIRN_FIXTURE.read_text(encoding="utf-8")
    retention = parse_risk_retention(both)

    assert retention.retainer == "The Investment Manager"
    assert retention.method_letter == "d"
    assert retention.level_basis == "the Aggregate Collateral Balance"


def test_the_capacity_must_belong_to_the_retainer() -> None:
    """A capacity stated of somebody else is not this retainer's capacity.

    The capacity is what makes the retention *bind*, so taking it from an
    unrelated sentence is the one inference this module must not make.
    """
    text = (
        "The Issuer is an originator for some other purpose. Acme Holdings LLP "
        "shall act as Retention Holder. It will retain a material net economic "
        "interest of not less than five per cent. of the Aggregate Collateral "
        "Balance in accordance with Article 6(3)(d)."
    )
    with pytest.raises(UnsourcedRetention, match="states no capacity"):
        parse_risk_retention(text)


def test_a_distant_method_word_does_not_contradict_the_citation() -> None:
    """The Regulation naming a method elsewhere is a different subject.

    Treating it as a contradiction refuses an unambiguous document, which is
    the failure direction this surface is least allowed.
    """
    text = (
        "The Regulation permits retention of the first loss tranche. "
        + "Filler sentence. " * 200
        + "Acme LLP shall act as Retention Holder. It qualifies as an "
        "originator and will retain not less than five per cent. of the "
        "Aggregate Collateral Balance in accordance with Article 6(3)(a)."
    )
    assert parse_risk_retention(text).method == "vertical slice"


def test_a_citation_in_the_generic_passage_does_not_shadow_the_undertaking() -> None:
    """A document may cite a sub-paragraph while merely *describing* the rule.

    Refusing on the first citation when a later one states the undertaking in
    full is a false refusal — the failure direction this surface is least
    allowed — so each citation is tried and the first complete one wins.
    """
    text = (
        "An originator may retain under Article 6(3)(d) of the Securitisation "
        "Regulations; the Issuer makes no such commitment in this section. "
        + "Filler sentence. " * 50
        + "Acme LLP shall act as Retention Holder. It qualifies as an "
        '"originator" and will retain not less than five per cent. of the '
        "Aggregate Collateral Balance in accordance with Article 6(3)(d)."
    )
    retention = parse_risk_retention(text)

    assert retention.retainer == "Acme LLP"
    assert retention.method_letter == "d"


def test_an_inverted_citation_is_tried_even_when_a_direct_one_appears_first() -> None:
    """The two citation forms are one ordered list, not two fallbacks.

    A describing passage may use the direct form while the undertaking uses the
    inverted one. Trying direct citations first and inverted ones only when
    none exist would never reach the real commitment here.
    """
    text = (
        "An originator may retain under Article 6(3)(d); nothing is committed "
        "in this paragraph. "
        + "Filler sentence. " * 50
        + "Acme LLP shall act as Retention Holder. It qualifies as an "
        '"originator" and will retain not less than five per cent. of the '
        "Aggregate Collateral Balance in accordance with option 3 (a) of "
        "article 6 of the Securitisation Regulation."
    )
    retention = parse_risk_retention(text)

    assert retention.retainer == "Acme LLP"
    assert retention.method == "vertical slice"


def test_a_document_sized_span_is_read_without_backtracking(
    cairn_undertaking: str,
) -> None:
    """The undertaking is reachable inside a document, not only a slice of one.

    Before the span was bound, the retainer search's greedy prefix backtracked
    over everything ahead of it: on the real 420-page text this did not return
    at all. The generous bound below separates "fast" from "hung" without
    pinning a performance number.
    """
    padding = "The Retention Holder is discussed at length. " * 6000
    started = time.monotonic()

    retention = parse_risk_retention(padding + cairn_undertaking)

    assert time.monotonic() - started < 15
    assert retention.retainer == "The Investment Manager"
    assert retention.method_letter == "d"


# ---------------------------------------------------------------------------
# 3. Refusals — each naming the limb that is missing
# ---------------------------------------------------------------------------


def test_the_regulations_generic_description_is_refused(cairn_generic: str) -> None:
    """The five-per-cent. floor, stated by the law rather than by this deal.

    This page is what a search for the *figure* finds first, 239 pages before
    the undertaking. It must not become a retention record.
    """
    assert "five per cent" in re.sub(r"\s+", " ", cairn_generic)
    with pytest.raises(UnsourcedRetention, match="no sub-paragraph of Article 6"):
        parse_risk_retention(cairn_generic)


def test_a_covenant_naming_no_retainer_is_refused(
    leone_retention_covenant: str,
) -> None:
    """The method is stated; "who retains" is not — so the record is refused.

    The covenant limb elides its subject, and the entity is only recoverable
    from a sibling covenant. Reporting a retention whose holder was inferred
    from an adjacent sentence is exactly the inference this module will not
    make: the refusal names the missing limb instead.
    """
    with pytest.raises(UnsourcedRetention, match="names no retaining entity"):
        parse_risk_retention(leone_retention_covenant)


def test_a_named_retainer_without_a_capacity_is_refused() -> None:
    """A designation is not a capacity — the retention binds through the latter."""
    text = (
        "X Capital LLP shall act as Retention Holder. It will retain a material "
        "net economic interest of not less than five per cent. of the Aggregate "
        "Collateral Balance in accordance with Article 6(3)(d)."
    )
    with pytest.raises(UnsourcedRetention, match="states no capacity"):
        parse_risk_retention(text)


def test_a_level_without_a_base_is_refused() -> None:
    """5% of *what* is half the fact (#539); a bare percentage is not a level."""
    text = (
        "X Capital LLP shall act as Retention Holder. It qualifies as an "
        '"originator" and will retain a material net economic interest of not '
        "less than five per cent. in accordance with Article 6(3)(d)."
    )
    with pytest.raises(UnsourcedRetention, match="no retained level with a base"):
        parse_risk_retention(text)


def test_from_dict_refuses_a_letter_outside_article_6_3() -> None:
    """A seed cannot smuggle in a method the Regulation does not permit."""
    raw = dict(_CAIRN_EXPECTED, method_letter="z")
    with pytest.raises(UnsourcedRetention, match="not one of the methods"):
        RiskRetention.from_dict(raw)


def test_from_dict_refuses_an_unrecognised_capacity() -> None:
    """"Investment manager" is a role in the deal, not a retention capacity."""
    raw = dict(_CAIRN_EXPECTED, retainer_capacity="investment manager")
    with pytest.raises(UnsourcedRetention, match="not one the Regulation recognises"):
        RiskRetention.from_dict(raw)


def test_the_method_name_is_derived_not_stored() -> None:
    """A seed cannot carry a letter and a name that disagree.

    ``method`` and ``source`` are rendered from ``method_letter`` on the way
    out and ignored on the way in, so the contradiction the parser refuses to
    produce cannot be reintroduced by hand-editing a seed.
    """
    raw = dict(_CAIRN_EXPECTED, method="vertical slice", source="somewhere else")
    rebuilt = RiskRetention.from_dict(raw)

    assert rebuilt.method == "first-loss tranche"
    assert rebuilt.source == "Listing Particulars, Article 6(3)(d)"


# ---------------------------------------------------------------------------
# 4. Coverage — a section that did not arrive is not a document that is silent
# ---------------------------------------------------------------------------


def test_the_truncated_glossary_is_flagged_implausible() -> None:
    """The #548 cliff, on a second fact.

    Cairn's committed glossary is 24k characters that mention retention and
    cite no sub-paragraph, because the terms that would have carried the
    citation fall past the ``max_chars`` cut. The check must call that
    unproven, not empty.
    """
    seed = json.loads(CAIRN_SEED.read_text(encoding="utf-8"))
    glossary = " ".join(entry["definition"] for entry in seed["definitions"].values())

    coverage = assess_retention(glossary)

    assert coverage.implausible
    assert coverage.retention_mentions > 0
    assert not coverage.cites_article_6_3
    assert "Article 6(3)" in coverage.reason


def test_the_real_undertaking_is_not_flagged(cairn_undertaking: str) -> None:
    """The anti-vacuity half: a check that flags everything proves nothing."""
    coverage = assess_retention(cairn_undertaking)

    assert not coverage.implausible
    assert coverage.reason == ""
    assert coverage.cites_article_6_3


def test_a_truncated_section_is_flagged_even_when_it_parses(
    cairn_undertaking: str,
) -> None:
    """Truncation is reported from the record, not inferred from the output.

    A clipped section can still contain a readable undertaking; that it parsed
    does not make what is *missing* from it absent from the document.
    """
    coverage = assess_retention(cairn_undertaking, truncated=True)

    assert coverage.implausible
    assert "truncated" in coverage.reason


def test_coverage_round_trips_and_tolerates_an_older_cache() -> None:
    """Old caches predate this record; they must keep loading (the #548 shape)."""
    coverage = assess_retention("far too short to be a retention section")

    assert RetentionCoverage.from_dict(coverage.to_dict()) == coverage
    assert RetentionCoverage.from_dict(None) is None
    assert RetentionCoverage.from_dict("not a dict") is None


def test_implausible_and_reason_never_disagree(cairn_undertaking: str) -> None:
    """``implausible`` is exactly ``bool(reason)`` — one fact, not two."""
    for text, truncated in (
        (cairn_undertaking, False),
        (cairn_undertaking, True),
        ("too short", False),
    ):
        coverage = assess_retention(text, truncated=truncated)
        assert coverage.implausible == bool(coverage.reason)


# ---------------------------------------------------------------------------
# 5. The documents' own words are committed
# ---------------------------------------------------------------------------


def test_the_undertakings_are_committed_verbatim(
    cairn_undertaking: str, contego_undertaking: str
) -> None:
    """A reviewer must read the sentence without fetching a 420-page PDF.

    A parser change that started agreeing with the seed for the wrong reason
    still has to agree with *these* words.
    """
    cairn = re.sub(r"\s+", " ", cairn_undertaking)
    contego = re.sub(r"\s+", " ", contego_undertaking)

    assert "in accordance with Article 6(3)(d)" in cairn
    assert "The Investment Manager shall act as Retention Holder" in cairn
    assert "pursuant to Article 6(3)(d)" in contego
    assert "first loss tranche" in contego


def test_the_address_resolving_cairns_designation_is_committed(
    cairn_undertaking: str,
) -> None:
    """Cairn names its retainer by role; the address is what resolves it.

    The offering circular's back cover lists ``Cairn Loan Investments II LLP``
    at this address as Investment Manager, so a reader can close the gap. The
    parser deliberately does not: substituting a legal name for the designation
    the document used is a cross-section inference, and it belongs beside the
    record as evidence rather than inside it as a fact (#567 presents it).
    """
    assert "62 Buckingham Gate" in re.sub(r"\s+", " ", cairn_undertaking)
    assert parse_risk_retention(cairn_undertaking).retainer == "The Investment Manager"


# ---------------------------------------------------------------------------
# 6. The seed states what the parser produces
# ---------------------------------------------------------------------------


#: The Cairn undertaking as the seed carries it — the one place the expected
#: record is written out, so the refusal tests above mutate a *valid* block
#: rather than each inventing their own near-miss.
_CAIRN_EXPECTED: dict[str, object] = {
    "retainer": "The Investment Manager",
    "retainer_capacity": "originator",
    "method_letter": "d",
    "method": "first-loss tranche",
    "level_pct": 5.0,
    "level_basis": "the Aggregate Collateral Balance",
    "instrument": "Subordinated Notes",
    "source": "Listing Particulars, Article 6(3)(d)",
}


def test_the_seed_carries_the_parsed_undertaking(cairn_undertaking: str) -> None:
    """The seed's ``risk_retention`` is what the parser produces, not a copy.

    Asserted through a round-trip rather than field by field, so a re-extraction
    that changed the shape cannot leave the seed quietly stale.
    """
    seed = json.loads(CAIRN_SEED.read_text(encoding="utf-8"))

    assert RiskRetention.from_dict(seed["risk_retention"]) == parse_risk_retention(
        cairn_undertaking
    )
    assert seed["risk_retention"] == _CAIRN_EXPECTED


def test_the_seed_loads_as_a_deal_model() -> None:
    """The block survives the model it is carried in."""
    model = DealModel.model_validate_json(CAIRN_SEED.read_text(encoding="utf-8"))

    assert model.risk_retention is not None
    assert RiskRetention.from_dict(model.risk_retention).method == "first-loss tranche"


#: Every committed seed except the CLO whose undertaking this PR reads,
#: enumerated from disk rather than listed so a seed added later is covered the
#: day it lands.
OTHER_SEEDS: tuple[str, ...] = tuple(
    sorted(
        path.name for path in SEED_DIR.glob("*.json") if path.name != CAIRN_SEED.name
    )
)


def test_the_guard_below_covers_every_other_committed_seed() -> None:
    """The guard is only worth its name if nothing escapes it."""
    assert len(OTHER_SEEDS) == len(list(SEED_DIR.glob("*.json"))) - 1
    assert CAIRN_SEED.name not in OTHER_SEEDS


@pytest.mark.parametrize("seed_name", OTHER_SEEDS)
def test_a_seed_with_no_retention_keeps_the_old_shape(seed_name: str) -> None:
    """No undertaking read, no key — and the model still validates.

    ``risk_retention`` absent means **not read**, never "retains nothing", so a
    deal this PR did not read must gain nothing at all. That is also the inverse
    of the write: delete the key and the seed is exactly what it was.
    """
    path = SEED_DIR / seed_name
    raw = json.loads(path.read_text(encoding="utf-8"))

    assert "risk_retention" not in raw
    assert DealModel.model_validate(raw).risk_retention is None
