"""One day-count implementation in the repo, not three that disagree (#601, #607).

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
``accrual_days`` — so there is one day-count contract rather than two.

#607 finished the job. ``payment_schedule_parser.accrual_period_days`` was the
third: also basis-less, also hardcoding Act/360, and — unlike the one #601
deleted — **live**, on the path that serves ``GET /deal/{id}/waterfall``. It
could not be converged the same way, because ``day_count_parser`` imports
``payment_schedule_parser`` and delegating would have closed an import cycle, so
the primitive moved down into ``loanwhiz.extraction.day_count``. The sections
below pin the report path's half, and a census pins that a *fourth* cannot
appear unannounced.

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

import ast
import collections
import pathlib
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from loanwhiz.api.main import _tape_period_days
from loanwhiz.extraction import day_count, day_count_parser, payment_schedule_parser
from loanwhiz.extraction.day_count_parser import ClassDayCount, accrual_days
from loanwhiz.extraction.payment_schedule_parser import (
    PaymentDateSchedule,
    accrual_period_days,
    previous_payment_date,
)
from loanwhiz.primitives.report_adapter import ReportAdapter

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


# ===========================================================================
# The report path (#607)
# ===========================================================================
#
# #601 converged the API path and left the report path's own implementation in
# place: ``payment_schedule_parser.accrual_period_days`` hardcoded Act/360 and
# took no basis, and unlike the one #601 deleted it was live — it is what
# ``GET /deal/{id}/waterfall`` counts days with.
#
# Converging it needed the dependency inverted first. ``day_count_parser``
# imports ``payment_schedule_parser``, so delegating there would have closed an
# import cycle; the primitive moved down into ``loanwhiz.extraction.day_count``,
# which imports no loanwhiz module at all, and both parsers now reach it.

#: The one deal whose report path this issue changes.
CLO_DEAL_ID = "cairn-clo-xvii"

#: A schedule paying the 18th of February, May, August and November — chosen so
#: that ``previous_payment_date(.., 2025-02-18)`` is **2024-11-18**, reproducing
#: #601's divergent period on the report path's own entrypoint. Cairn's own
#: schedule pays January/April/July/October, on which no period lands here.
DIVERGENT_SCHEDULE = PaymentDateSchedule(
    day_of_month=18, months=(2, 5, 8, 11), commencing=date(2024, 2, 18)
)

#: A schedule paying the **31st**, where the 30/360 family's members disagree.
#: Every Payment Date Cairn states falls on the 18th, so no committed deal can
#: reach the refusal below — which is itself worth pinning: the refusal exists
#: for the deal this repo does not yet hold, and an untested one is a guess.
VARIANT_SENSITIVE_SCHEDULE = PaymentDateSchedule(
    day_of_month=31, months=(1, 3, 5, 7), commencing=date(2024, 1, 31)
)


def _day_count(basis):
    """One class stating ``basis``, adjusted dates, everything else held equal."""
    return ClassDayCount(
        class_key="B-2",
        basis=basis,
        unadjusted_payment_dates=False,
        condition="6(e)(iii)",
        rate_type="FXR" if basis == "30/360" else "FLR",
    )


def _adapter(schedule, basis):
    return ReportAdapter(
        revenue_steps=[],
        redemption_steps=[],
        payment_schedule=schedule,
        note_day_counts={"B-2": _day_count(basis)},
    )


# ---------------------------------------------------------------------------
# The conformance the report path's old implementation could not satisfy
# ---------------------------------------------------------------------------


def test_the_report_boundary_can_be_asked_for_thirty_360():
    """``accrual_period_days`` answers 90 where Act/360 answers 92.

    The assertion the old code could not pass in any form: it took no basis, so
    92 was the only answer it could give for this period. A test asserting the
    surviving implementations agree on Act/360 would prove nothing — they always
    did agree there, and it passes on the unfixed code too.
    """
    payment = date(2025, 2, 18)
    assert previous_payment_date(DIVERGENT_SCHEDULE, payment) == date(2024, 11, 18)

    assert (
        accrual_period_days("30/360", DIVERGENT_SCHEDULE, payment)
        == DIVERGENT_THIRTY_360
    )
    assert (
        accrual_period_days("act/360", DIVERGENT_SCHEDULE, payment)
        == DIVERGENT_ACT_360
    )


@pytest.mark.parametrize("basis", ["act/360", "30/360"])
@pytest.mark.parametrize(
    "payment",
    [
        date(2025, 2, 18),  # the divergent period
        date(2024, 11, 18),  # a period where the bases agree
        date(2025, 5, 18),  # a period whose payment date rolls off a weekend
    ],
)
def test_the_report_boundary_counts_through_the_one_contract(basis, payment):
    """Its answer is ``accrual_days``' answer, on both bases.

    Over a grid rather than one period, so a re-implementation that happened to
    agree on the divergent fixture would still be caught.
    """
    previous = previous_payment_date(DIVERGENT_SCHEDULE, payment)
    assert accrual_period_days(basis, DIVERGENT_SCHEDULE, payment) == accrual_days(
        basis, previous, payment
    )


def test_the_report_path_reaches_the_same_function_object():
    """One implementation, not two that happen to agree.

    ``is`` rather than an equality of results: a copied body would satisfy every
    numeric assertion above while being the fourth implementation this issue
    exists to prevent.
    """
    assert payment_schedule_parser.accrual_days is day_count.accrual_days
    assert day_count_parser.accrual_days is day_count.accrual_days
    assert day_count_parser.UnsourcedDayCount is day_count.UnsourcedDayCount


def test_the_inverted_dependency_has_no_cycle():
    """``day_count`` imports no loanwhiz module, which is what makes it a leaf.

    The cycle is the whole reason this issue was not a repeat of #601: a
    function-local import inside ``accrual_period_days`` would have made the
    numbers right while leaving the cycle in place, so the property worth
    pinning is structural rather than numeric.
    """
    source = pathlib.Path(day_count.__file__).read_text()
    imported = {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not [m for m in imported if m.startswith("loanwhiz")]


# ---------------------------------------------------------------------------
# The report path's refusals, each paired with the input that makes it
# evaluable (#493)
# ---------------------------------------------------------------------------


def test_an_unsourced_basis_refuses_on_the_report_path():
    """A 30/360 class whose Accrual Period ends on the 31st refuses by name.

    "12 months of 30 days each" names a *family* — 30/360 US, 30E/360 and
    30E/360 ISDA — whose members differ only at the 31st and the last day of
    February. At such a date the document has not said which member is meant, so
    the count is unsourced and is refused rather than picked.

    Paired below with the same adapter, the same schedule and the same dates,
    changing **only** the basis: it returns a real count. Without that pair the
    assertion would keep passing if some unrelated layer refused first (#493).
    """
    period = SimpleNamespace(reporting_date="2025-03-30")

    with pytest.raises(day_count.UnsourcedDayCount) as excinfo:
        _adapter(VARIANT_SENSITIVE_SCHEDULE, "30/360")._tranche_days_in_period(period)

    assert "2025-01-31" in str(excinfo.value)
    assert "30E/360 ISDA" in str(excinfo.value)

    # ...and the same call on the sourceable basis produces the count.
    assert _adapter(VARIANT_SENSITIVE_SCHEDULE, "act/360")._tranche_days_in_period(
        period
    ) == {"class_b_2": 59}


def test_the_refusal_is_not_downgraded_to_the_deal_wide_count():
    """It raises rather than omitting the tranche.

    Omitting it would be the worse failure: an absent entry means "this class
    states no convention", so the class would silently fall back to the deal-wide
    Act/360 count — the exact silent-wrong-convention the per-class basis exists
    to remove. #549's rule: a refusal that keeps its value is not a refusal.
    """
    adapter = _adapter(VARIANT_SENSITIVE_SCHEDULE, "30/360")
    period = SimpleNamespace(reporting_date="2025-03-30")

    with pytest.raises(day_count.UnsourcedDayCount):
        adapter._tranche_days_in_period(period)

    # The deal-wide count for the same period is a real number, so "raise" was a
    # choice and not the only thing the adapter could have done here.
    assert adapter._days_in_period(period) == 59


def test_the_report_fold_turns_an_unsourced_day_count_into_a_named_422(monkeypatch):
    """It reaches the operator as a labelled 422, not an unhandled 500.

    Both halves are real: the exception is the one ``thirty_360_days`` actually
    raises for a variant-sensitive endpoint (constructed by calling it, never
    hand-written), and the conversion is ``_reconstruct_series_from_reports``'s
    own. What is substituted is the *raising site* — every committed deal pays on
    the 18th, so none can reach this refusal through the live fold, and a refusal
    with no test is a guess about what the operator would see.

    Paired below with the same call, unpatched, on the same deal: it folds a real
    series. So the 422 is the day count's doing, not a deal that cannot model.
    """
    from loanwhiz.api import main

    deal = main.DEAL_REGISTRY[CLO_DEAL_ID]

    with pytest.raises(day_count.UnsourcedDayCount) as raised:
        day_count.thirty_360_days(date(2025, 1, 31), date(2025, 3, 31))
    real_refusal = raised.value

    monkeypatch.setattr(main, "_RECONSTRUCTION_MEMO", {})

    def _refusing_fold(model, report, adapter):
        raise real_refusal

    monkeypatch.setattr(main, "fold_report_series", _refusing_fold)

    with pytest.raises(HTTPException) as excinfo:
        main._reconstruct_series_from_reports(CLO_DEAL_ID, deal)

    assert excinfo.value.status_code == 422
    # It carries the refusal's OWN reason rather than a generic message, which is
    # what lets an operator tell an unsourced convention from a missing report.
    assert "2025-01-31" in excinfo.value.detail
    assert CLO_DEAL_ID in excinfo.value.detail
    # ...and it is a distinct refusal from "this deal cannot be modelled at all".
    assert excinfo.value.detail != main._not_modelable_deal(CLO_DEAL_ID, deal).detail


def test_the_same_deal_folds_when_the_day_count_is_sourceable(monkeypatch):
    """The other direction of the pair above: unpatched, the fold succeeds."""
    from loanwhiz.api import main

    monkeypatch.setattr(main, "_RECONSTRUCTION_MEMO", {})
    series = main._reconstruct_series_from_reports(
        CLO_DEAL_ID, main.DEAL_REGISTRY[CLO_DEAL_ID]
    )
    assert series.period_results


# ---------------------------------------------------------------------------
# The census: a check that reds if a FOURTH implementation appears
# ---------------------------------------------------------------------------
#
# The tests above pin that today's entrypoints agree. They say nothing about the
# one next quarter. This walks the source and counts the two shapes a day count
# is written in, so a new one is a failure rather than a discovery.
#
# It is a checker, which means it passes by finding nothing — and "found
# nothing" and "I could not see" are the same output (#562). So the expectations
# are LITERAL, written here rather than derived from the code they describe
# (#598: a guard that computes its expectation from the same constant as the
# code cannot disagree with it), and the mutation table below plants each defect
# it claims to catch and requires it to fire.

#: ``(a - b).days`` — counting calendar days between two dates.
#:
#: ``extraction/day_count.py`` is ``accrual_days``' Act/360 branch: the one
#: implementation. ``primitives/tranche_analytics.py`` is **not** an Accrual
#: Period day count — it is elapsed years on actual/365.25 for a WAL, a
#: different quantity that happens to share the subtraction. It is allowlisted by
#: name and by count, not by pattern, so a real day count added to that file is
#: still caught.
EXPECTED_ACTUAL_DAY_SITES = {
    "extraction/day_count.py": 1,
    "primitives/tranche_analytics.py": 1,
}

#: ``360 * (…) + 30 * (…)`` over date components — a year of twelve 30-day
#: months. Two per ``thirty_360_days``: the years term and the months term of the
#: one expression.
EXPECTED_THIRTY_360_SITES = {
    "extraction/day_count.py": 2,
}

#: The **adjacent** spellings — a day count is not always written ``.days``.
#: ``(end.toordinal() - start.toordinal())`` and ``(end - start).total_seconds()
#: / 86400`` are the same quantity by another route, and the first two maps are
#: structurally blind to both. Expected nowhere, including in ``day_count.py``:
#: the one contract does not use them either, so any appearance is new.
#:
#: #599's lesson is that a guard's blind spot sits *adjacent* to what it sees,
#: not far from it. ``timedelta(days=1)`` is deliberately NOT here — it is how
#: ``payment_schedule_parser`` steps to the next business day, which is a walk
#: and not a count, and banning it would make the census cry wolf.
EXPECTED_ADJACENT_SITES: dict[str, int] = {}


def _census(root: pathlib.Path):
    """Count day-count-shaped expressions per module under ``root``.

    AST rather than grep, because the defect this guards against is a *shape*
    and not a spelling: ``(end - start).days`` written across two lines, or with
    the subtraction parenthesised differently, is the same implementation and a
    text pattern would miss it (#602 — a prose guard that could not see the shape
    the defect actually had).
    """
    files = sorted(root.rglob("*.py"))
    actual: collections.Counter = collections.Counter()
    thirty: collections.Counter = collections.Counter()
    adjacent: collections.Counter = collections.Counter()
    for path in files:
        tree = ast.parse(path.read_text(), filename=str(path))
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "days"
                and isinstance(node.value, ast.BinOp)
                and isinstance(node.value.op, ast.Sub)
            ):
                actual[rel] += 1
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
                for const, other in (
                    (node.left, node.right),
                    (node.right, node.left),
                ):
                    if (
                        isinstance(const, ast.Constant)
                        and const.value in (30, 360)
                        and any(
                            isinstance(n, ast.Attribute)
                            and n.attr in ("year", "month", "day")
                            for n in ast.walk(other)
                        )
                    ):
                        thirty[rel] += 1
                        break
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("toordinal", "total_seconds")
            ):
                adjacent[rel] += 1
    return files, actual, thirty, adjacent


def _violations(actual, thirty, adjacent):
    """Every module whose day-count site count differs from the literal map."""
    found = []
    for label, counted, expected in (
        ("actual-day", actual, EXPECTED_ACTUAL_DAY_SITES),
        ("30/360", thirty, EXPECTED_THIRTY_360_SITES),
        ("adjacent-spelling", adjacent, EXPECTED_ADJACENT_SITES),
    ):
        for module in set(counted) | set(expected):
            if counted.get(module, 0) != expected.get(module, 0):
                found.append(
                    f"{label}: {module} has {counted.get(module, 0)}, "
                    f"expected {expected.get(module, 0)}"
                )
    return sorted(found)


SRC_ROOT = pathlib.Path(day_count.__file__).resolve().parents[1]


def test_census_finds_no_fourth_day_count_implementation():
    """Every day-count site in ``src/loanwhiz`` is one this file names."""
    _, actual, thirty, adjacent = _census(SRC_ROOT)
    assert _violations(actual, thirty, adjacent) == []


def test_census_actually_scanned_the_tree_it_reports_on():
    """The wiring check: "found nothing" must not be able to pass for "clean".

    #562's rule — a guard that passes by finding nothing needs a test that its
    call is still plugged in. A census pointed at the wrong root, or at a tree it
    failed to parse, reports zero violations and reads as a green guard. So this
    asserts it walked many modules AND positively located the known sites, either
    of which fails if the walk came back empty.
    """
    files, actual, thirty, adjacent = _census(SRC_ROOT)
    assert len(files) > 1
    assert actual["extraction/day_count.py"] == 1
    assert thirty["extraction/day_count.py"] == 2


# -- the mutation table: each plants the defect and requires the census to fire

def _plant(tmp_path, rel, body):
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    return tmp_path


#: The census's expected shape, reproduced so a mutant differs from a clean tree
#: by exactly the planted defect.
_CLEAN_DAY_COUNT = (
    "def accrual_days(basis, start, end):\n"
    "    if basis == 'act/360':\n"
    "        return (end - start).days\n"
    "    return (360 * (end.year - start.year)\n"
    "            + 30 * (end.month - start.month)\n"
    "            + (end.day - start.day))\n"
)
_CLEAN_ANALYTICS = (
    "def _years_from(a, b):\n    return max((b - a).days, 0) / 365.25\n"
)


def _clean_tree(tmp_path):
    _plant(tmp_path, "extraction/day_count.py", _CLEAN_DAY_COUNT)
    _plant(tmp_path, "primitives/tranche_analytics.py", _CLEAN_ANALYTICS)
    return tmp_path


def test_census_control_a_clean_tree_reports_nothing(tmp_path):
    """The control. Without it, a census that always fired would 'catch' every
    mutant below while being worthless on the real tree."""
    _, actual, thirty, adjacent = _census(_clean_tree(tmp_path))
    assert _violations(actual, thirty, adjacent) == []


def test_census_fires_on_a_fourth_implementation_in_a_new_module(tmp_path):
    """The defect this exists to catch: someone writes their own day count."""
    _clean_tree(tmp_path)
    _plant(
        tmp_path,
        "primitives/settlement_helper.py",
        "def period_days(start, end):\n    return (end - start).days\n",
    )
    _, actual, thirty, adjacent = _census(tmp_path)
    assert any("settlement_helper" in v for v in _violations(actual, thirty, adjacent))


def test_census_fires_on_a_second_site_inside_an_allowlisted_module(tmp_path):
    """Hiding it where a site is already permitted does not get past.

    The allowlist is keyed by module **and count**, so ``tranche_analytics`` —
    which legitimately holds one subtraction for a WAL — cannot quietly acquire a
    real Accrual Period day count beside it.
    """
    _clean_tree(tmp_path)
    _plant(
        tmp_path,
        "primitives/tranche_analytics.py",
        _CLEAN_ANALYTICS + "\n\ndef period_days(a, b):\n    return (b - a).days\n",
    )
    _, actual, thirty, adjacent = _census(tmp_path)
    assert any("tranche_analytics" in v for v in _violations(actual, thirty, adjacent))


def test_census_fires_on_a_second_thirty_360_formula(tmp_path):
    """The other shape. A 30/360 re-implementation is the one that would move a
    published figure, since that is the basis Cairn's Class B-2 accrues on."""
    _clean_tree(tmp_path)
    _plant(
        tmp_path,
        "primitives/fixed_leg.py",
        "def bond_days(a, b):\n"
        "    return 360 * (b.year - a.year) + 30 * (b.month - a.month)\n",
    )
    _, actual, thirty, adjacent = _census(tmp_path)
    assert any("fixed_leg" in v for v in _violations(actual, thirty, adjacent))


def test_census_fires_when_the_one_implementation_is_deleted(tmp_path):
    """Both directions (#493): it flags a site that appears AND one that leaves.

    A census that only ever counted upward would pass on a tree where the
    contract had been moved out from under it, which is the state in which every
    other test in this file is silently testing nothing.
    """
    _plant(tmp_path, "primitives/tranche_analytics.py", _CLEAN_ANALYTICS)
    _, actual, thirty, adjacent = _census(tmp_path)
    assert any("extraction/day_count.py" in v for v in _violations(actual, thirty, adjacent))


# ---------------------------------------------------------------------------
# The deal-wide basis is passed, not assumed
# ---------------------------------------------------------------------------
#
# Found by mutation, not by design: changing ``_days_in_period`` to ask for
# 30/360 passed all 51 tests of the report path. Cairn's six interest lines each
# state their own basis, so they route through ``_tranche_days_in_period`` and
# never touch the deal-wide count — which left the one call site this issue
# changed pinned by nothing at all.


def test_the_deal_wide_count_is_the_basis_condition_6_e_ii_states():
    """``_days_in_period`` asks for Act/360, on a period where that is a choice.

    Cairn's deal-wide Accrual Period runs 2024-10-18 -> 2025-01-21, which is 95
    actual days and **93** on a year of twelve 30-day months. So this is not a
    period where the bases happen to agree: the number identifies which basis the
    call site passed, which is the only thing that makes the assertion able to
    fail.

    Act/360 is Condition 6(e)(ii)'s own basis — "the actual number of days in the
    Accrual Period concerned, divided by 360" — the limb governing every floating
    class, and this count is the fallback for tranches stating no basis.
    """
    from loanwhiz.api import main
    from loanwhiz.primitives.report_extractor import resolve_parsed_report

    deal = main.DEAL_REGISTRY[CLO_DEAL_ID]
    model = main._load_cached_deal_model(deal)
    report = resolve_parsed_report(
        CLO_DEAL_ID, deal, cache_dir=main.REPORT_EXTRACTION_CACHE_DIR
    ).to_notes_cash_report()
    adapter = ReportAdapter.from_deal_model(model)

    (period,) = report.periods
    payment = payment_schedule_parser.payment_date_on_or_after(
        adapter.payment_schedule, date.fromisoformat(period.reporting_date)
    )
    previous = previous_payment_date(adapter.payment_schedule, payment)

    assert adapter._days_in_period(period) == accrual_days(
        "act/360", previous, payment
    )
    assert adapter._days_in_period(period) == 95
    # The other basis is a different number, so 95 names the convention.
    assert accrual_days("30/360", previous, payment) == 93


def test_a_class_stating_its_own_basis_does_not_take_the_deal_wide_count():
    """Which is why the deal-wide count needed its own test.

    Class B-2 accrues 30/360 over 90 days while the deal-wide count is 95, so the
    per-class route genuinely overrides rather than merely agreeing — and the six
    published interest figures are reproduced through it, not through
    ``_days_in_period``.
    """
    from loanwhiz.api import main
    from loanwhiz.primitives.report_extractor import resolve_parsed_report

    deal = main.DEAL_REGISTRY[CLO_DEAL_ID]
    model = main._load_cached_deal_model(deal)
    report = resolve_parsed_report(
        CLO_DEAL_ID, deal, cache_dir=main.REPORT_EXTRACTION_CACHE_DIR
    ).to_notes_cash_report()
    adapter = ReportAdapter.from_deal_model(model)
    (period,) = report.periods

    per_class = adapter._tranche_days_in_period(period)
    assert per_class["class_b_1"] == 95
    assert per_class["class_b_2"] == 90
    assert adapter._days_in_period(period) == 95


def test_census_fires_on_a_day_count_spelled_without_dot_days(tmp_path):
    """The adjacent spelling. A guard's blind spot sits next to what it sees.

    ``(end.toordinal() - start.toordinal())`` is the same quantity as
    ``(end - start).days`` and the first two shapes are structurally blind to it,
    so a fourth implementation written this way would have walked past a census
    that only knew the two spellings already in the tree (#599).
    """
    _clean_tree(tmp_path)
    _plant(
        tmp_path,
        "primitives/ordinal_days.py",
        "def period_days(start, end):\n"
        "    return end.toordinal() - start.toordinal()\n",
    )
    _, actual, thirty, adjacent = _census(tmp_path)
    assert any("ordinal_days" in v for v in _violations(actual, thirty, adjacent))


def test_census_does_not_fire_on_a_business_day_walk(tmp_path):
    """``timedelta(days=1)`` is a step, not a count — and stays unflagged.

    ``payment_schedule_parser`` walks forward and backward a day at a time to
    find the next Business Day. A census that flagged it would cry wolf on the
    module it most needs to be trusted about, so the exclusion is deliberate and
    pinned here rather than left as an accident of the patterns chosen.
    """
    _clean_tree(tmp_path)
    _plant(
        tmp_path,
        "extraction/business_day_walk.py",
        "from datetime import timedelta\n\n\n"
        "def next_business_day(day, is_holiday):\n"
        "    while is_holiday(day):\n"
        "        day += timedelta(days=1)\n"
        "    return day\n",
    )
    _, actual, thirty, adjacent = _census(tmp_path)
    assert _violations(actual, thirty, adjacent) == []
