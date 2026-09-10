"""What ``GET /cross-deal-concentration`` serves, and what it refuses (#565).

#564 built look-through concentration as types whose validators do the refusing.
This module grades the seam between those types and the wire: a serialiser is
exactly where a residual gets dropped, because dropping one produces a smaller,
tidier payload that still parses.

So the assertions here are not "the endpoint returns 200". They are the four
properties #564 encoded, checked on the response rather than on the model:

* every bucket carries its proven/candidate/unresolved split, and the three
  reconcile to the bucket's own balance;
* nothing is netted — the buckets plus the unattributed record account for the
  whole book, and no bucket names a residual;
* every contribution carries its own deal's stated date, and ``dates_align``
  says the two committed reports disagree;
* an industry axis that names no taxonomy is refused with the type's own
  sentence, not defaulted to Fitch.

Offline: the handler reads the committed schedule fixtures, so no test here
touches the network.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from loanwhiz.api.main import COMMITTED_SCHEDULE_FIXTURES, _portfolio_for, app

CAIRN = "cairn-clo-xvii"
CONTEGO = "contego-clo-xi"

#: The two reports' own stated dates. Committed fixtures, so these are stable;
#: they are the whole point of the date residual and are pinned rather than
#: read off the response they are meant to check.
STATED_DATES = {CAIRN: "18/03/2025", CONTEGO: "30/08/2024"}

#: Names whose identity across the two books #562 could not prove — the
#: unresolved tier plus the candidate-proposed one — out of the total. The
#: issue that commissioned this screen said "40 unmatched names"; the committed
#: pair says otherwise, and the screen has to carry the real number.
UNPROVEN_NAMES = 244
TOTAL_NAMES = 294

#: Every axis the endpoint serves, so a property is asserted on all of them
#: rather than on the default one.
SERVED_AXES = ["fitch-industry", "sp-industry", "country", "fitch-rating", "sp-rating"]

#: Labels that would name a residual rather than a thing held. #496/#514: this
#: repo has no "Other" bucket anywhere and gains none by being serialised.
RESIDUAL_LABELS = {
    "other",
    "others",
    "unknown",
    "unclassified",
    "n/a",
    "na",
    "none",
    "misc",
    "miscellaneous",
    "unattributed",
    "not published",
}


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def _figure(client: TestClient, axis: str = "fitch-industry") -> dict:
    response = client.get("/cross-deal-concentration", params={"axis": axis})
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Criterion: the axis names its taxonomy, and one that cannot is refused
# ---------------------------------------------------------------------------


def test_the_industry_figure_names_the_taxonomy_it_is_expressed_on(
    client: TestClient,
) -> None:
    """#563 settled Fitch for cross-deal figures; the response has to say so."""
    figure = _figure(client)
    assert figure["axis"]["taxonomy"] == "fitch"
    assert figure["axis"]["label"] == "Fitch industry"
    assert figure["axis"]["agency"] is None


def test_an_industry_axis_naming_no_taxonomy_is_refused_with_the_types_reason(
    client: TestClient,
) -> None:
    """The refusal reaches the API rather than being defaulted away.

    ``ExposureAxis`` refuses an industry axis with no taxonomy, and the handler
    hands that validator's own sentence back. A default here would be the
    failure #563 describes: the same portfolio concentrates differently on S&P
    than on Fitch, so a figure that picked one silently is not comparable to
    anything.
    """
    response = client.get("/cross-deal-concentration", params={"axis": "industry"})
    assert response.status_code == 400
    assert "must name its taxonomy" in response.json()["detail"]


def test_an_unknown_axis_is_refused_and_says_which_axes_exist(
    client: TestClient,
) -> None:
    response = client.get("/cross-deal-concentration", params={"axis": "moody-industry"})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "moody-industry" in detail
    for axis in SERVED_AXES:
        assert axis in detail


def test_the_sp_industry_axis_is_a_different_figure_from_the_fitch_one(
    client: TestClient,
) -> None:
    """The taxonomy is load-bearing, not decorative: naming it changes buckets."""
    fitch = _figure(client, "fitch-industry")
    sp = _figure(client, "sp-industry")
    assert sp["axis"]["taxonomy"] == "sp"
    fitch_labels = {b["label"] for b in fitch["buckets"]}
    sp_labels = {b["label"] for b in sp["buckets"]}
    assert fitch_labels != sp_labels, (
        "if the two taxonomies produced the same buckets, naming the axis would "
        "be a caption rather than part of the figure"
    )


# ---------------------------------------------------------------------------
# Criterion: the obligor residual reaches the wire
# ---------------------------------------------------------------------------


def test_the_unproven_name_count_is_on_the_response(client: TestClient) -> None:
    """The headline residual — the number my own issue body got wrong."""
    figure = _figure(client)
    assert figure["name_count"] == TOTAL_NAMES
    assert figure["unproven_name_count"] == UNPROVEN_NAMES


def test_the_unproven_count_is_every_tier_except_the_proven_one(
    client: TestClient,
) -> None:
    """Derived from the tiers, so it cannot drift from the split beside it."""
    figure = _figure(client)
    by_tier = {t["tier"]: t for t in figure["tiers"]}
    assert set(by_tier) == {"proven_shared", "candidate_proposed", "unresolved"}
    assert (
        figure["unproven_name_count"]
        == by_tier["candidate_proposed"]["name_count"]
        + by_tier["unresolved"]["name_count"]
    )
    assert (
        figure["name_count"]
        == figure["unproven_name_count"] + by_tier["proven_shared"]["name_count"]
    )


def test_a_candidate_name_counts_as_unproven_not_as_identified(
    client: TestClient,
) -> None:
    """A proposal is not a match. Counting one as proven is the whole error.

    #562 reports candidates as proposals and never merges them into a row,
    because applying one makes the distinct-obligor count a point estimate. The
    serialiser must take the same side: candidate names sit on the unproven
    side of the headline count.
    """
    figure = _figure(client)
    candidates = next(t for t in figure["tiers"] if t["tier"] == "candidate_proposed")
    assert candidates["name_count"] > 0, "the committed pair does carry candidates"
    assert figure["proposal_count"] > 0
    assert figure["unproven_name_count"] >= candidates["name_count"]


def test_the_distinct_obligor_count_is_a_range_not_a_point(
    client: TestClient,
) -> None:
    """No accessor collapses the bounds — a point estimate assumes what was not proved."""
    figure = _figure(client)
    bounds = figure["obligor_bounds"]
    assert bounds["lower"] < bounds["upper"]
    assert bounds["is_exact"] is False
    assert bounds["upper"] == figure["name_count"]


@pytest.mark.parametrize("axis", SERVED_AXES)
def test_the_tier_balances_account_for_the_whole_book(
    client: TestClient, axis: str
) -> None:
    """The three tiers partition the balance; none of them is a leftover."""
    figure = _figure(client, axis)
    total = sum(t["balance"] for t in figure["tiers"])
    assert total == pytest.approx(figure["total_balance"], abs=0.01)


def test_the_unproven_share_is_two_thirds_of_this_book(client: TestClient) -> None:
    """A property of the committed pair, asserted so the screen cannot flatter it."""
    figure = _figure(client)
    assert figure["not_proven_share_pct"] > 50.0
    by_tier = {t["tier"]: t for t in figure["tiers"]}
    assert figure["not_proven_share_pct"] == pytest.approx(
        by_tier["candidate_proposed"]["share_pct"] + by_tier["unresolved"]["share_pct"],
        abs=0.02,
    )


# ---------------------------------------------------------------------------
# Criterion: every bucket carries its split, and nothing is netted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("axis", SERVED_AXES)
def test_every_bucket_carries_its_resolution_split(
    client: TestClient, axis: str
) -> None:
    """The split is the qualifier that makes a share readable — never optional."""
    figure = _figure(client, axis)
    assert figure["buckets"], f"the {axis} axis places at least one asset"
    for bucket in figure["buckets"]:
        split = bucket["split"]
        assert set(split) == {"proven_shared", "candidate_proposed", "unresolved"}
        assert sum(split.values()) == pytest.approx(bucket["balance"], abs=0.01), (
            f"{bucket['label']} on {axis}: the split must reconcile to the "
            f"bucket balance it qualifies"
        )


@pytest.mark.parametrize("axis", SERVED_AXES)
def test_no_bucket_names_a_residual(client: TestClient, axis: str) -> None:
    """There is no "Other" row for an unresolved name to be netted into."""
    figure = _figure(client, axis)
    for bucket in figure["buckets"]:
        assert bucket["label"].strip().lower() not in RESIDUAL_LABELS


@pytest.mark.parametrize("axis", SERVED_AXES)
def test_the_buckets_and_the_unattributed_record_account_for_every_asset(
    client: TestClient, axis: str
) -> None:
    """Nothing is lost between the figure and its serialisation.

    An asset the axis cannot place becomes ``unattributed`` — a different kind,
    so no loop over ``buckets`` picks it up as though it were a concentration —
    but it is still accounted for. A serialiser that dropped it would leave a
    payload whose shares silently summed to less than the book.
    """
    figure = _figure(client, axis)
    placed = sum(b["balance"] for b in figure["buckets"])
    assert placed + figure["unattributed"]["balance"] == pytest.approx(
        figure["total_balance"], abs=0.01
    )
    assert (
        sum(b["asset_count"] for b in figure["buckets"])
        + figure["unattributed"]["asset_count"]
        == figure["asset_count"]
    )


def test_the_fitch_rating_axis_reports_its_large_unplaced_share(
    client: TestClient,
) -> None:
    """The residual is real on this book, so the assertion above can fail.

    Cairn publishes no Fitch rating at all. If ``unattributed`` were dropped or
    quietly folded into a bucket, this axis is where it would show, and the
    figure would read as a fully-rated book.
    """
    figure = _figure(client, "fitch-rating")
    unplaced = figure["unattributed"]
    assert unplaced["asset_count"] > 0
    assert unplaced["share_pct"] > 50.0
    assert unplaced["reason"] == "not_published"
    assert {c["deal"] for c in unplaced["per_deal"]} == {CAIRN, CONTEGO}


def test_the_industry_axis_places_everything_so_the_two_axes_differ(
    client: TestClient,
) -> None:
    """The paired assertion: an empty residual is measured, not assumed."""
    figure = _figure(client)
    assert figure["unattributed"]["asset_count"] == 0
    assert figure["unattributed"]["share_pct"] == 0.0


# ---------------------------------------------------------------------------
# Criterion: each deal's own stated date travels with its contribution
# ---------------------------------------------------------------------------


def test_both_deals_state_their_own_reporting_date(client: TestClient) -> None:
    figure = _figure(client)
    stated = {entry["deal"]: entry["stated"] for entry in figure["as_of"]}
    assert stated == STATED_DATES


def test_the_figure_says_its_two_dates_do_not_align(client: TestClient) -> None:
    """The mixing is on the face of the figure, not in a caveat elsewhere."""
    figure = _figure(client)
    assert figure["dates_align"] is False


@pytest.mark.parametrize("axis", SERVED_AXES)
def test_every_contribution_carries_its_own_deals_date(
    client: TestClient, axis: str
) -> None:
    """Not one aggregated as-of: the date is only true per deal."""
    figure = _figure(client, axis)
    contributions = [
        c
        for bucket in figure["buckets"]
        for c in bucket["per_deal"]
    ] + figure["unattributed"]["per_deal"]
    assert contributions
    for contribution in contributions:
        assert contribution["as_of"] == STATED_DATES[contribution["deal"]]


def test_the_disclosure_sentences_name_both_dates(client: TestClient) -> None:
    """The prose the screen renders is the primitive's, not a paraphrase."""
    figure = _figure(client)
    for sentence in (figure["disclosure"], figure["obligor_disclosure"]):
        for date in STATED_DATES.values():
            assert date in sentence
    assert "unproven" in figure["disclosure"]


def test_every_bucket_sentence_names_the_axis_and_the_split(
    client: TestClient,
) -> None:
    figure = _figure(client)
    for bucket in figure["buckets"]:
        assert "Fitch industry" in bucket["disclosure"]
        assert "unresolved" in bucket["disclosure"]


# ---------------------------------------------------------------------------
# Criterion: the endpoint is offline, deterministic, and refuses rather than
# serving a partial book
# ---------------------------------------------------------------------------


def test_the_committed_pair_is_what_the_endpoint_serves(client: TestClient) -> None:
    figure = _figure(client)
    assert figure["deals"] == sorted(COMMITTED_SCHEDULE_FIXTURES)
    assert figure["currency"] == "EUR"


def test_two_calls_return_the_same_figure(client: TestClient) -> None:
    """Deterministic: no network, no LLM, no clock in the request path."""
    assert _figure(client) == _figure(client)


def test_the_schedules_are_parsed_once_rather_than_per_request(
    client: TestClient,
) -> None:
    """The memo is wired into the handler, not merely available beside it.

    Parsing both reports and re-resolving every obligor is a quarter-second of
    CPU whose answer cannot differ between two requests on committed files.
    Asserting the hit count rather than the elapsed time keeps this a statement
    about wiring — "nothing ran the parser twice" and "the parser is not
    reached at all" are different, and only the cache counters tell them apart.
    """
    _portfolio_for.cache_clear()
    _figure(client)
    first = _portfolio_for.cache_info()
    assert first.misses == 1, "the first request must actually parse"
    _figure(client, "country")
    second = _portfolio_for.cache_info()
    assert second.misses == 1, "a second axis must not re-parse the schedules"
    assert second.hits == first.hits + 1


def test_the_memo_is_keyed_on_the_fixtures_rather_than_on_nothing() -> None:
    """A memo keyed on nothing would serve March's book for December's request."""
    contego = (CONTEGO, "contego-clo-xi-august-2024.txt", "2024-08")
    march = _portfolio_for(((CAIRN, "cairn-clo-xvii-march-2025.txt", "2025-03"), contego))
    december = _portfolio_for(
        ((CAIRN, "cairn-clo-xvii-december-2024.txt", "2024-12"), contego)
    )
    assert march.as_of_for(CAIRN).stated == STATED_DATES[CAIRN]
    assert december.as_of_for(CAIRN).stated != STATED_DATES[CAIRN]


def test_a_deal_with_no_committed_schedule_refuses_the_whole_figure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A subset is a different answer, not a smaller one.

    Serving the deals that happen to have parsed would understate every
    concentration in the result while looking exactly like a complete one, so
    the endpoint refuses and names the file it wanted.
    """
    monkeypatch.setitem(
        COMMITTED_SCHEDULE_FIXTURES, "absent-clo-i", ("absent-clo-i-2025.txt", "2025-01")
    )
    response = client.get("/cross-deal-concentration")
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert "absent-clo-i" in detail
    assert "absent-clo-i-2025.txt" in detail
