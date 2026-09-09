"""The Payment Date schedule, and the accrual period measured on it (#528).

What these tests are for
------------------------
Cairn's accrual period is 95 days, and for most of epic #510's life that number
was reachable exactly one way: dividing the published Class A interest by what
the engine computed for it. #521 stood down rather than commit it, correctly —
an input back-solved from the answer makes every future period tie by
construction, which is the circularity the epic exists to remove.

So the property under test here is **not** "the day count is 95". It is *where
the 95 comes from*: two Payment Dates the Listing Particulars state, resolved
through a Business Day definition the same document states. No test in this file
reads a published interest figure, and none should be made to.

The cross-check is the load-bearing one
---------------------------------------
Parsing the schedule is easy to get subtly wrong, and a wrong schedule yields a
plausible number rather than an error. The reports are the independent check:
each states the Payment Date it actually paid on, and
:func:`test_derived_payment_dates_match_the_dates_the_reports_state` asserts the
schedule plus the committed holiday table reproduces them. A mis-entered holiday
reds there.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from loanwhiz.extraction.assembler import DealModel
from loanwhiz.extraction.payment_schedule_parser import (
    BUSINESS_CENTRE_HOLIDAYS,
    COVERED_YEARS,
    PaymentDateSchedule,
    UnresolvableBusinessDay,
    accrual_period_days,
    adjust_to_business_day,
    is_business_day,
    parse_business_day_centres,
    parse_payment_date_schedule,
    payment_date_for,
    payment_date_on_or_after,
    previous_payment_date,
    scheduled_dates_in_year,
)
from loanwhiz.primitives.note_valuation_parser import stated_next_payment_date
from loanwhiz.primitives.report_adapter import DEFAULT_DAYS_IN_PERIOD, ReportAdapter

TESTS_DIR = Path(__file__).parent
SEED_DIR = (
    TESTS_DIR.parent / "src" / "loanwhiz" / "data" / "deals" / "seed"
)
PROSPECTUS_FIXTURE = (
    TESTS_DIR / "fixtures" / "prospectus" / "cairn-clo-xvii-definitions-excerpt.txt"
)

#: The reports Euronext's listing carries, each with the Payment Date it states.
#: Both document families are included deliberately: they are parsed by different
#: primitives and print the header differently (the collateral schedules run the
#: fields together with no spaces), so covering only one would leave the other's
#: format unasserted.
REPORT_FIXTURES: tuple[tuple[str, str], ...] = (
    ("collateral_schedule/cairn-clo-xvii-december-2024.txt", "December 2024"),
    ("note_valuation/cairn-clo-xvii-january-2025.txt", "January 2025"),
    ("collateral_schedule/cairn-clo-xvii-february-2025.txt", "February 2025"),
    ("collateral_schedule/cairn-clo-xvii-march-2025.txt", "March 2025"),
)


@pytest.fixture(scope="module")
def prospectus_text() -> str:
    """The committed excerpt of the Listing Particulars.

    A verbatim slice of what ``pypdf`` extracts from the 420-page PDF — wrapped
    lines, stray intra-word spaces and all — so the parser is exercised against
    the text it really sees rather than a tidied paraphrase. It carries the two
    definitions this module needs; #539 appended the *Accrual Period* definition
    and Condition 6(e), which the day-count parser reads from the same file.
    """
    return PROSPECTUS_FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def schedule(prospectus_text: str) -> PaymentDateSchedule:
    return parse_payment_date_schedule(prospectus_text)


# ---------------------------------------------------------------------------
# 1. What the document says
# ---------------------------------------------------------------------------


def test_the_schedule_parses_from_the_prospectus_text(
    schedule: PaymentDateSchedule,
) -> None:
    """The stated schedule: the 18th of January, April, July and October.

    Read from limb ``(b)`` — "at all other times" — which is the standing
    schedule. Limb ``(a)`` states a *semi-annual* fallback that applies only after
    a Frequency Switch Event, and reading it instead would silently halve the
    payment frequency and roughly double every accrual period.
    """
    assert schedule.day_of_month == 18
    assert schedule.months == (1, 4, 7, 10)
    assert schedule.commencing == date(2024, 4, 18)
    assert schedule.convention == "modified_following"


def test_the_business_day_definition_spans_four_centres(
    schedule: PaymentDateSchedule, prospectus_text: str
) -> None:
    """T2 plus London, Dublin **and New York** — and New York is load-bearing.

    The definition is conjunctive, so the deal's Payment Dates move on a US
    holiday even though nothing about the deal is American. That is not
    incidental: it is precisely why the January 2025 Payment Date is the 21st and
    not the 20th, and a Business Day defined over European centres alone would
    produce a 94-day accrual period that looks perfectly reasonable.
    """
    assert schedule.business_centres == ("T2", "London", "Dublin", "New York")
    assert parse_business_day_centres(prospectus_text) == [
        "T2",
        "London",
        "Dublin",
        "New York",
    ]
    assert set(schedule.business_centres) <= set(BUSINESS_CENTRE_HOLIDAYS)


def test_the_centre_list_does_not_depend_on_cairns_parenthetical(
    prospectus_text: str,
) -> None:
    """A re-worded Business Day limb still yields all four centres.

    Self-review finding. The pattern originally required a ``(`` immediately after
    the centre list — true of this document ("… and New York (other than a
    Saturday …") and of nothing in general. A prospectus phrasing the limb without
    it parsed to ``["T2"]`` alone and resolved January 2025 to the **20th**, a
    94-day period, with no exception raised anywhere.

    That is the worst available failure for this module: not a crash, but a
    plausible number that is wrong by one holiday. Pinned against the same
    fixture with the parenthetical removed.
    """
    reworded = prospectus_text.replace(
        "New \nYork (other than a Saturday or a Sunday); and", "New \nYork; and"
    )
    assert reworded != prospectus_text, "the mutation did not apply"
    assert parse_business_day_centres(reworded) == [
        "T2",
        "London",
        "Dublin",
        "New York",
    ]


def test_limb_c_back_reference_is_not_read_as_a_centre(prospectus_text: str) -> None:
    """"settle payments in **that place**" is a back-reference, not a city.

    Limb ``(c)`` matches the same "settle payments in …" shape as limb ``(b)``.
    Now that the pattern scans every occurrence rather than only the first, the
    back-reference has to be excluded explicitly or it becomes a business centre
    with no holiday list — which would then raise on every date.
    """
    assert "settle payments in that place" in " ".join(prospectus_text.split())
    assert "that place" not in parse_business_day_centres(prospectus_text)


def test_a_business_day_definition_naming_no_centre_refuses() -> None:
    """Found but unreadable raises — it must not return a usable-looking subset.

    Dropping centres does not fail loudly; it shifts dates by a day or two, which
    is indistinguishable from a correct answer downstream.
    """
    with pytest.raises(ValueError, match="names no settlement centres"):
        parse_business_day_centres(
            '"Business Day" means a day of the week. "Cut-off" means something.'
        )


def test_a_schedule_stating_no_convention_refuses() -> None:
    """A business-day convention is read or the schedule is refused.

    Defaulting it silently is the same shape as the centres finding: the
    convention decides which day a period ends on.
    """
    with pytest.raises(ValueError, match="no business-day convention"):
        parse_payment_date_schedule(
            '"Payment Date" means: (b) 18 January, 18 April, 18 October and '
            "18 July at all other times, in each case, in each year commencing "
            'on 18 April 2024. "Person" means a person.'
        )


def test_a_schedule_whose_day_does_not_exist_in_a_month_refuses() -> None:
    """The 31st of February is not a date, and no convention is stated for it."""
    impossible = PaymentDateSchedule(
        day_of_month=31, months=(1, 2), commencing=date(2024, 1, 31)
    )
    with pytest.raises(UnresolvableBusinessDay, match="does not exist in month"):
        scheduled_dates_in_year(impossible, 2025)


def test_the_convention_survives_the_inline_defined_term(schedule: PaymentDateSchedule) -> None:
    """``modified_following``, read past the ``"Scheduled Payment Date"`` label.

    The Payment Date definition *labels* a term inline — ``(each a "Scheduled
    Payment Date")`` — before stating its business-day proviso. A block terminator
    that stops at any quoted capitalised phrase looks equivalent and truncates
    here, dropping the proviso and silently downgrading the convention to plain
    "following". Pinned because the two conventions agree on this deal's dates and
    the mistake would therefore never surface as a wrong number.
    """
    assert schedule.convention == "modified_following"


def test_a_document_that_defines_no_payment_date_yields_no_schedule() -> None:
    """Absence is a finding, not an exception.

    A deal whose prospectus states no schedule must return ``None`` so the caller
    keeps its documented approximation, rather than raising and taking down a
    fold that was working.
    """
    assert parse_payment_date_schedule("nothing resembling a definitions section") is None


def test_a_half_read_schedule_refuses_rather_than_guessing() -> None:
    """A definition present but unreadable raises — it must not yield a number.

    This is the failure mode worth refusing on: a partial parse still produces a
    day count, and a day count is exactly the kind of thing nobody re-checks.
    """
    with pytest.raises(ValueError, match="standing schedule"):
        parse_payment_date_schedule('"Payment Date" means: something else entirely. "Person" means a person.')


# ---------------------------------------------------------------------------
# 2. The cross-check — derived dates against the dates the reports state
# ---------------------------------------------------------------------------


def test_derived_payment_dates_match_the_dates_the_reports_state(
    schedule: PaymentDateSchedule,
) -> None:
    """The schedule and calendar reproduce the Payment Dates the trustee printed.

    **This is the test that makes the holiday table trustworthy.** Everything else
    here checks that the parser read the document correctly; this checks the
    result against dates the documents state independently of it, and each one is
    a non-trivial adjustment:

    - 18 January 2025 is a Saturday, and the following Monday is Martin Luther
      King Jr. Day — a New York holiday — so the Payment Date is Tuesday the 21st,
      which is what the December and January reports both print.
    - 18 April 2025 is Good Friday and the 21st is Easter Monday, both closed in
      T2, London and Dublin, so the Payment Date is Tuesday the 22nd, which is
      what the February report prints.

    Get a holiday wrong and one of these stops matching. March is excluded and
    handled separately below.
    """
    checked: list[tuple[str, date]] = []
    for fixture, label in REPORT_FIXTURES:
        if label == "March 2025":
            continue
        stated = stated_next_payment_date(
            (TESTS_DIR / "fixtures" / fixture).read_text(encoding="utf-8")
        )
        assert stated is not None, f"{label} states no payment date"
        stated_date = date.fromisoformat(stated)
        derived = payment_date_on_or_after(
            schedule, date(stated_date.year, stated_date.month, 1)
        )
        assert derived == stated_date, (
            f"{label}: schedule derives {derived}, report states {stated_date}"
        )
        checked.append((label, stated_date))

    # The control on the control: a loop over an empty or silently-shrunk fixture
    # list passes while checking nothing, and "the calendar is right" and "I
    # compared no dates" would then be the same green. Both distinct payment dates
    # must have been reached.
    assert [label for label, _ in checked] == [
        "December 2024",
        "January 2025",
        "February 2025",
    ]
    assert {payment for _, payment in checked} == {date(2025, 1, 21), date(2025, 4, 22)}


def test_the_march_report_states_a_payment_date_the_schedule_does_not_predict(
    schedule: PaymentDateSchedule,
) -> None:
    """28 March 2025 is not a Scheduled Payment Date — and that is correct.

    The definition admits Payment Dates the schedule cannot predict: "any
    Redemption Date in connection with a redemption in whole", the Final
    Distribution Date, and after the Rated Notes are repaid any Business Day at
    all. The March 2025 trustee report states one — a Calculation Date of
    18/03/2025 against a Next Payment Date of 28/03/2025, ten days later and on no
    scheduled month.

    Asserted rather than ignored, because it bounds what this module may be used
    for. A period ending on an Unscheduled Payment Date is **shorter** than
    :func:`payment_date_on_or_after` returns, so a caller holding the report's own
    stated payment date should pass it to :func:`accrual_period_days` directly
    rather than re-deriving it. Nothing in the graded fold does — the PoP series
    is built from the January Note Valuation Report alone — but the day this file
    stops being true silently is the day someone folds the March report.
    """
    stated = stated_next_payment_date(
        (TESTS_DIR / "fixtures" / "collateral_schedule" / "cairn-clo-xvii-march-2025.txt")
        .read_text(encoding="utf-8")
    )
    assert stated == "2025-03-28"

    unscheduled = date.fromisoformat(stated)
    assert unscheduled.month not in schedule.months
    assert payment_date_on_or_after(schedule, date(2025, 3, 1)) == date(2025, 4, 22)

    # Handed the stated date, the module still measures the real period.
    assert accrual_period_days(schedule, unscheduled) == 66
    assert previous_payment_date(schedule, unscheduled) == date(2025, 1, 21)


# ---------------------------------------------------------------------------
# 3. The accrual period itself
# ---------------------------------------------------------------------------


def test_the_january_2025_accrual_period_is_measured_between_two_stated_dates(
    schedule: PaymentDateSchedule,
) -> None:
    """95 days, and every input to it is a date the prospectus names.

    The deal's *Accrual Period* is defined "from and including each Payment Date
    to, but excluding, the following Payment Date". The period the January 2025
    report pays for therefore runs from the 18 October 2024 Payment Date to the
    21 January 2025 one.

    Both endpoints are asserted, not just the count, because the count is the part
    that could be reached the wrong way. 18 October 2024 is a Friday and needs no
    adjustment; 21 January 2025 is the adjusted 18th. Neither is read from, or
    checked against, any published amount.
    """
    payment = payment_date_on_or_after(schedule, date(2025, 1, 8))
    previous = previous_payment_date(schedule, payment)

    assert previous == date(2024, 10, 18)
    assert payment == date(2025, 1, 21)
    assert previous.weekday() == 4  # a Friday, so unadjusted
    assert accrual_period_days(schedule, payment) == (payment - previous).days
    assert accrual_period_days(schedule, payment) == 95


def test_the_following_period_is_a_different_length(
    schedule: PaymentDateSchedule,
) -> None:
    """91 days for January → April 2025 — quarterly periods are not equal.

    The point of the whole exercise: a fixed 90 is an approximation of a quantity
    that genuinely varies, and consecutive periods on this deal differ by four
    days. A schedule that returned the same number every period would be a
    hardcoded default wearing a parser.
    """
    april = payment_date_on_or_after(schedule, date(2025, 2, 18))
    assert april == date(2025, 4, 22)
    assert accrual_period_days(schedule, april) == 91


def test_the_first_accrual_period_refuses(schedule: PaymentDateSchedule) -> None:
    """The period ending on the first Payment Date is not derivable, so it raises.

    The Accrual Period definition starts the first period at the *Issue Date*,
    which the Payment Date schedule does not state. Returning a plausible number
    here would be exactly the guess this module exists to avoid.
    """
    first = payment_date_for(schedule, date(2024, 4, 18))
    with pytest.raises(UnresolvableBusinessDay, match="Issue Date"):
        accrual_period_days(schedule, first)


# ---------------------------------------------------------------------------
# 4. The business-day machinery
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "expected", "why"),
    [
        (date(2024, 10, 18), True, "an ordinary Friday"),
        (date(2025, 1, 18), False, "a Saturday"),
        (date(2025, 1, 20), False, "Martin Luther King Jr. Day in New York"),
        (date(2025, 1, 21), True, "the Tuesday the reports state"),
        (date(2025, 4, 18), False, "Good Friday in T2, London and Dublin"),
        (date(2025, 4, 21), False, "Easter Monday"),
        (date(2025, 3, 17), False, "St Patrick's Day in Dublin"),
    ],
)
def test_is_business_day_reads_every_centre(day: date, expected: bool, why: str) -> None:
    """One centre's holiday disqualifies the day for all of them."""
    centres = ("T2", "London", "Dublin", "New York")
    assert is_business_day(day, centres) is expected, why


def test_modified_following_rolls_back_rather_than_across_a_month_boundary() -> None:
    """The "unless it would thereby fall in the following month" limb.

    Cairn's own dates never exercise it — an 18th cannot roll into the next month
    — so it is tested on a constructed month-end date. Left untested it would be
    code nothing runs, and the two conventions would be indistinguishable.
    """
    centres = ("T2",)
    # 31 May 2025 is a Saturday; rolling forward lands on 2 June, a new month.
    assert adjust_to_business_day(date(2025, 5, 31), centres, "following") == date(
        2025, 6, 2
    )
    assert adjust_to_business_day(
        date(2025, 5, 31), centres, "modified_following"
    ) == date(2025, 5, 30)


def test_a_date_outside_the_committed_calendar_refuses(
    schedule: PaymentDateSchedule,
) -> None:
    """Outside :data:`COVERED_YEARS` the calendar raises instead of assuming.

    A deal modelled on stated dates must not quietly fall back to the
    approximation those dates replaced. Silence here would look exactly like
    success and be wrong by however many holidays the unlisted year contains.
    """
    assert 2030 not in COVERED_YEARS
    with pytest.raises(UnresolvableBusinessDay, match="outside the committed"):
        is_business_day(date(2030, 1, 18), schedule.business_centres)


def test_an_unknown_business_centre_refuses() -> None:
    """A centre with no committed holiday list is not silently treated as open."""
    with pytest.raises(UnresolvableBusinessDay, match="no committed holiday list"):
        is_business_day(date(2025, 1, 21), ("Tokyo",))


# ---------------------------------------------------------------------------
# 5. What reaches the engine
# ---------------------------------------------------------------------------


def test_the_committed_seed_carries_the_parsed_schedule(
    schedule: PaymentDateSchedule,
) -> None:
    """The seed's schedule is what the parser produces — not a hand-typed copy.

    Asserted through a round-trip rather than field-by-field so a future
    re-extraction that changed the shape cannot leave the seed quietly stale.
    """
    seed = json.loads((SEED_DIR / "cairn-clo-xvii-dac.json").read_text(encoding="utf-8"))
    assert PaymentDateSchedule.from_dict(seed["payment_schedule"]) == schedule

    # The two definitions the truncated glossary lost are committed verbatim too,
    # so the seed states the rule and not only its parsed result.
    assert "18 January, 18 April, 18 October and 18 July" in (
        seed["definitions"]["Payment Date"]["definition"]
    )
    assert "London, Dublin and New York" in (
        seed["definitions"]["Business Day"]["definition"]
    )


def test_the_report_adapter_derives_cairns_day_count() -> None:
    """``ReportAdapter`` takes the day count from the schedule, not the default."""
    model = DealModel.model_validate_json(
        (SEED_DIR / "cairn-clo-xvii-dac.json").read_text(encoding="utf-8")
    )
    adapter = ReportAdapter.from_deal_model(model)
    assert adapter.payment_schedule is not None
    assert accrual_period_days(
        adapter.payment_schedule,
        payment_date_on_or_after(adapter.payment_schedule, date(2025, 1, 8)),
    ) == 95


#: Every committed seed except the CLO — enumerated from disk rather than listed,
#: so a seed added later is covered the day it lands. A hardcoded list omitted
#: ``sol-lion-ii`` and would have silently exempted any new deal (self-review
#: finding); the point of this guard is that it covers *all* of them.
OTHER_SEEDS: tuple[str, ...] = tuple(
    sorted(
        path.name
        for path in SEED_DIR.glob("*.json")
        if path.name != "cairn-clo-xvii-dac.json"
    )
)


def test_the_byte_identity_guard_covers_every_other_committed_seed() -> None:
    """The guard below is only worth its name if nothing escapes it."""
    assert len(OTHER_SEEDS) == len(list(SEED_DIR.glob("*.json"))) - 1
    assert "green-lion-2024-1-bv.json" in OTHER_SEEDS
    assert any(name.startswith("sol-lion") for name in OTHER_SEEDS)
    assert "cairn-clo-xvii-dac.json" not in OTHER_SEEDS


@pytest.mark.parametrize("seed_name", OTHER_SEEDS)
def test_deals_without_a_stated_schedule_keep_the_ninety_day_default(
    seed_name: str,
) -> None:
    """No schedule, no change — this is the Green Lion byte-identity guard.

    Green Lion is validated to the cent against its own published Priorities of
    Payments, and #528 must not move it. It states no Payment Date schedule, so
    its adapter carries ``None`` and its day count stays
    :data:`DEFAULT_DAYS_IN_PERIOD`.

    #521 separately established that reading Green Lion's *real* periods would
    change no number there anyway: ``api/main._period_coupon_pct`` back-solves its
    Class A rate with a hardcoded 90, so the day count cancels on both sides. That
    is a reason not to bother, not a reason it would be safe — this test is what
    makes it safe.
    """
    model = DealModel.model_validate_json(
        (SEED_DIR / seed_name).read_text(encoding="utf-8")
    )
    assert ReportAdapter.from_deal_model(model).payment_schedule is None
    assert DEFAULT_DAYS_IN_PERIOD == 90
