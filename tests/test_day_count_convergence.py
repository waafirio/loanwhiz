"""One day-count implementation on the API path, not two that disagree (#601).

What this file pins
-------------------
Two functions used to answer "days in period" and nothing checked they agreed.
``loanwhiz.api.main._days_between`` took two ISO strings, hardcoded Act/360, and
returned a plausible ``30`` both when a date would not parse and when the period
did not run forwards.
:func:`loanwhiz.extraction.day_count_parser.accrual_days` is basis-aware, routes
30/360 through :func:`~loanwhiz.extraction.day_count_parser.thirty_360_days`, and
refuses with ``UnsourcedDayCount`` where the document does not decide.

The convergence removed the private one. ``_tape_period_days`` is a *boundary* —
it parses the registry's ISO strings and delegates every count to
``accrual_days`` — so there is one day-count contract rather than two, and no
third.

Why the 30/360 case is the one that proves anything
---------------------------------------------------
#513, #514 and #538 each shipped a conformance check that could not fail: an
``all()`` over an empty list, a 0.00-vs-0.00 tie-out, and a residual sweep that
absorbed an overshoot so the totals matched while two steps were wrong by equal
and opposite amounts. A test asserting the two implementations agree on Act/360
inputs is the same shape — they always did agree there, so it passes on the
unfixed code too. Every assertion below that could pass before the change is
paired with one that could not: the 30/360 period, and the two refusals.

The refusals are asserted in **both** directions, per #493: a test that only
says "this input refused" keeps passing with the fix reverted if some *other*
layer refuses first. Each refusal here is paired with the same call on a
well-formed input, changing nothing else, and asserts it produces the real
count.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi import HTTPException

from loanwhiz.api.main import _tape_period_days
from loanwhiz.extraction import day_count_parser
from loanwhiz.extraction.day_count_parser import accrual_days

#: A period whose two bases genuinely disagree. 2024-11-18 -> 2025-02-18 is 92
#: actual days (30 + 31 + 31) and 90 on a year of twelve 30-day months. Both
#: endpoints fall on the 18th, so the 30/360 variants agree and ``thirty_360_days``
#: does not refuse — the divergence measured here is between the two *bases*, not
#: between the members of one family.
DIVERGENT_START = "2024-11-18"
DIVERGENT_END = "2025-02-18"
DIVERGENT_ACT_360 = 92
DIVERGENT_THIRTY_360 = 90


# ---------------------------------------------------------------------------
# The conformance the old implementation could not satisfy
# ---------------------------------------------------------------------------


def test_the_two_bases_genuinely_differ_over_this_period():
    """The fixture is a real divergence, not a period where both agree.

    Stated first and separately because it is the premise every assertion below
    rests on: if these two numbers were equal, the 30/360 test would be another
    check that cannot fail.
    """
    start, end = date(2024, 11, 18), date(2025, 2, 18)
    assert accrual_days("act/360", start, end) == DIVERGENT_ACT_360
    assert accrual_days("30/360", start, end) == DIVERGENT_THIRTY_360
    assert DIVERGENT_ACT_360 != DIVERGENT_THIRTY_360


def test_the_api_boundary_can_be_asked_for_thirty_360():
    """The API path answers 90 where Act/360 answers 92.

    This is the assertion the old code could not pass in any form: ``_days_between``
    took no basis, so 92 was the only answer it could give for this period. #538
    measured Cairn's Class B as two strips under two conventions; an entrypoint
    that cannot be asked for the second cannot express that deal at all.
    """
    assert (
        _tape_period_days("30/360", DIVERGENT_START, DIVERGENT_END)
        == DIVERGENT_THIRTY_360
    )
    assert (
        _tape_period_days("act/360", DIVERGENT_START, DIVERGENT_END)
        == DIVERGENT_ACT_360
    )


@pytest.mark.parametrize("basis", ["act/360", "30/360"])
@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2024-11-18", "2025-02-18"),  # the divergent period
        ("2025-01-18", "2025-04-18"),  # a period where the bases agree
        ("2024-12-16", "2025-02-18"),  # Cairn's registered tape cadence
        ("2024-02-01", "2024-03-01"),  # a leap-year February
        ("2023-06-15", "2026-06-15"),  # a multi-year span
    ],
)
def test_every_count_comes_from_the_one_contract(basis, start, end):
    """The boundary's answer is ``accrual_days``' answer, on both bases.

    Asserted over a grid rather than one period so a re-implementation that
    happened to agree on the divergent fixture would still be caught.
    """
    assert _tape_period_days(basis, start, end) == accrual_days(
        basis, date.fromisoformat(start), date.fromisoformat(end)
    )


def test_no_second_implementation_survives_on_the_api_path():
    """``_days_between`` is gone, and the name the API path counts through is
    the contract's own function object rather than a local copy of it."""
    from loanwhiz.api import main

    assert not hasattr(main, "_days_between")
    assert main.accrual_days is day_count_parser.accrual_days


# ---------------------------------------------------------------------------
# The refusals, each paired with the input that makes it evaluable (#493)
# ---------------------------------------------------------------------------


def test_unparseable_current_date_refuses_and_names_the_value():
    with pytest.raises(HTTPException) as excinfo:
        _tape_period_days("act/360", DIVERGENT_START, "18-02-2025")

    assert excinfo.value.status_code == 422
    # Naming the offending value is the point: #535's lesson is that a bare 422
    # outlives its cause and tells the operator nothing about which date was bad.
    assert "18-02-2025" in excinfo.value.detail
    assert "current" in excinfo.value.detail

    # Paired: supply a parseable date, change nothing else, and the same call
    # produces the real count. Without this half the test would keep passing if
    # some earlier layer began refusing everything.
    assert (
        _tape_period_days("act/360", DIVERGENT_START, DIVERGENT_END)
        == DIVERGENT_ACT_360
    )


def test_unparseable_previous_date_refuses_and_names_the_value():
    with pytest.raises(HTTPException) as excinfo:
        _tape_period_days("act/360", "not-a-date", DIVERGENT_END)

    assert excinfo.value.status_code == 422
    assert "not-a-date" in excinfo.value.detail
    assert "previous" in excinfo.value.detail

    assert (
        _tape_period_days("act/360", DIVERGENT_START, DIVERGENT_END)
        == DIVERGENT_ACT_360
    )


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2025-02-18", "2025-02-18"),  # a zero-length period
        ("2025-02-18", "2024-11-18"),  # a period running backwards
    ],
)
def test_a_period_that_does_not_run_forwards_refuses(start, end):
    """The second silent fallback: ``_days_between`` returned 30 here too.

    ``return delta if delta > 0 else 30`` answered a plausible month for a pair
    of dates that state no period at all, which is the same defect as the parse
    fallback wearing different clothes.
    """
    with pytest.raises(HTTPException) as excinfo:
        _tape_period_days("act/360", start, end)

    assert excinfo.value.status_code == 422
    assert start in excinfo.value.detail
    assert end in excinfo.value.detail

    # Paired: the same call over a period that does run forwards is evaluable.
    assert (
        _tape_period_days("act/360", DIVERGENT_START, DIVERGENT_END)
        == DIVERGENT_ACT_360
    )


@pytest.mark.parametrize("bad_end", ["18-02-2025", "", "2025-02-30", "not-a-date"])
def test_a_refusal_keeps_no_day_count(bad_end):
    """#549: a refusal that keeps its value is not a refusal.

    The old code's failure mode was not that it refused badly — it was that it
    returned 30, a number no caller could tell from a measured one. Every
    malformed shape here therefore has to leave *no* count behind: a rejected
    date, an empty string, a date-shaped string naming a day that does not
    exist, and prose.
    """
    with pytest.raises(HTTPException):
        _tape_period_days("act/360", DIVERGENT_START, bad_end)


def test_an_undecidable_thirty_360_endpoint_refuses_through_the_same_422():
    """The contract's own refusal is a refusal of this boundary too.

    ``thirty_360_days`` raises ``UnsourcedDayCount`` when an endpoint falls on
    the 31st or the last day of February, because 30/360 US, 30E/360 and
    30E/360 ISDA disagree there and a Condition saying "12 months of 30 days
    each" names the family without naming the member. Re-raised as the same 422
    carrying that reason, so every way this boundary can fail to establish a day
    count fails the same way rather than one of them escaping as a 500.
    """
    with pytest.raises(HTTPException) as excinfo:
        _tape_period_days("30/360", "2025-01-18", "2025-03-31")

    assert excinfo.value.status_code == 422
    assert "2025-03-31" in excinfo.value.detail
    assert "disagree" in excinfo.value.detail

    # Paired (#493): the same basis over an endpoint the variants agree on is
    # evaluable, so this refuses the undecidable date and not 30/360 itself.
    assert _tape_period_days("30/360", "2025-01-18", "2025-03-18") == 60


# ---------------------------------------------------------------------------
# The wiring: the boundary's answer has to arrive where the count is read
# ---------------------------------------------------------------------------


def test_the_reconstruction_loop_counts_through_the_contract(monkeypatch, tmp_path):
    """``_reconstruct_series_from_tapes`` asks ``accrual_days`` for the count, on
    Act/360, and both consumers receive that number.

    Asserted at the two places the value is *read* rather than only where it is
    computed — #520's lesson, that a correct value can reach nothing with no
    error anywhere. The loop hands it to ``CollectionsInput.days_in_period`` and
    again to ``TapeAdapter.period_inputs``, and the two are separate arrivals.

    The stubs are the network boundary and nothing else: the aggregator fetches
    loan tapes, ``_normalised_tape_output`` resolves published analytics, and
    ``reconstruct_period_series`` folds a capital structure this synthetic deal
    does not have. The day-count call under test is real, and so is the
    ``CollectionsInput`` the loop builds.

    No registered deal reaches this loop today — the four RMBS deals carry one
    tape each and both CLOs yield their derived tapes to their published reports
    (``_tapes_yield_to_reports``) — so a synthetic two-tape deal is the only way
    to exercise it. That is also why the defect was latent rather than visible.
    """
    from loanwhiz.api import main

    calls: list[tuple[str, date, date]] = []
    collections_inputs: list[object] = []
    adapter_days: list[int] = []

    def _recording_accrual_days(basis, start, end):
        calls.append((basis, start, end))
        return accrual_days(basis, start, end)

    class _FakeResult:
        output = object()

    class _FakeAggregator:
        def execute(self, collections_input):
            collections_inputs.append(collections_input)
            return _FakeResult()

    class _FakeTapeAdapter:
        def period_inputs(
            self, collections, tape_output, *, reporting_date, days_in_period
        ):
            adapter_days.append(days_in_period)
            return object()

    class _FakeSeries:
        def model_dump_json(self):
            return "{}"

    def _offline(url):
        raise RuntimeError("no analytics for a synthetic tape")

    monkeypatch.setattr(main, "accrual_days", _recording_accrual_days)
    monkeypatch.setattr(main, "RECONSTRUCTION_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(main, "_RECONSTRUCTION_MEMO", {})
    monkeypatch.setattr(
        main, "_resolve_structural_config", lambda deal_id, deal: ({}, 0.0, 0.0)
    )
    monkeypatch.setattr(main, "_collections_tranche_args", lambda deal_id, cap: {})
    monkeypatch.setattr(main, "_audit", lambda *args, **kwargs: None)
    monkeypatch.setattr(main, "_normalised_tape_output", _offline)
    monkeypatch.setattr(main, "CollectionsAggregator", _FakeAggregator)
    monkeypatch.setattr(main, "TapeAdapter", _FakeTapeAdapter)
    monkeypatch.setattr(main, "_run_period_step_kwargs", lambda deal: {})
    monkeypatch.setattr(main, "reconstruct_period_series", lambda **kwargs: _FakeSeries())

    deal = {
        "tape_urls": [
            {"url": "synthetic://period-0", "date": DIVERGENT_START},
            {"url": "synthetic://period-1", "date": DIVERGENT_END},
        ]
    }
    main._reconstruct_series_from_tapes("synthetic-two-tape-deal", deal)

    # The loop ran — an empty capture here would make every assertion below
    # vacuously true, which is exactly the shape #513's `all()` over an empty
    # list took.
    assert len(calls) == 1

    assert calls == [("act/360", date(2024, 11, 18), date(2025, 2, 18))]
    assert [ci.days_in_period for ci in collections_inputs] == [DIVERGENT_ACT_360]
    assert adapter_days == [DIVERGENT_ACT_360]


def test_a_malformed_registry_date_refuses_the_reconstruction(monkeypatch, tmp_path):
    """The refusal is not swallowed on its way out of the loop.

    Paired with the test above, which is the same call over a well-formed
    registry: one refuses by name, the other reconstructs.
    """
    from loanwhiz.api import main

    monkeypatch.setattr(main, "RECONSTRUCTION_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(main, "_RECONSTRUCTION_MEMO", {})
    monkeypatch.setattr(
        main, "_resolve_structural_config", lambda deal_id, deal: ({}, 0.0, 0.0)
    )

    deal = {
        "tape_urls": [
            {"url": "synthetic://period-0", "date": DIVERGENT_START},
            {"url": "synthetic://period-1", "date": "31st of February"},
        ]
    }
    with pytest.raises(HTTPException) as excinfo:
        main._reconstruct_series_from_tapes("synthetic-malformed-deal", deal)

    assert excinfo.value.status_code == 422
    assert "31st of February" in excinfo.value.detail
