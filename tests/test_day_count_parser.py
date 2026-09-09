"""Each Class's day-count basis, read from the Condition that states it (#539).

What these tests are for
------------------------
Cairn issues Class B in two strips on two different conventions: B-1 floats and
accrues on the actual day count, B-2 is fixed and accrues on 30/360. #538 measured
that difference and stood down rather than name the fixed convention, because the
only thing pointing at 30/360 from inside the engine was that it made the published
figure tie — and an input back-solved from the answer is exactly what epic #510
exists to remove.

So the property under test here is **not** "B-2's day count is 90", and no test in
this file reads a published interest figure or asserts a total. What is under test
is *where the basis comes from*: Condition 6(e)(iii) states a 360-day year of
twelve 30-day months for the Class B-2 Notes, Condition 6(e)(ii) states the actual
number of days for the six floating classes, and the *Accrual Period* proviso says
which of those measures over unadjusted Payment Dates. Every one of those is a
sentence in the Listing Particulars, and the tests below assert the parser reads
them rather than that it produces a number someone wanted.

Two things worth knowing about the failure modes
------------------------------------------------
A mis-parse here yields a *plausible* basis rather than an error, so
:func:`test_the_fixed_limb_does_not_capture_the_classes_the_next_limb_names`
guards the one boundary that would silently spread the fixed basis across the whole
deal: Condition 6(e)(iv) enumerates Classes A through F for an unrelated purpose,
immediately after the limb that names only B-2.

And "12 months of 30 days each" names a *family* — 30/360 US, 30E/360 and
30E/360 ISDA — whose members disagree only on the 31st and the last day of
February. Every Payment Date this deal states falls on the 18th, so they agree
everywhere the schedule can reach; :func:`test_a_variant_sensitive_endpoint_is_refused`
asserts the parser refuses rather than silently picking a member where they don't.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from loanwhiz.extraction.day_count_parser import (
    ClassDayCount,
    UnsourcedDayCount,
    accrual_days,
    class_accrual_days,
    parse_interest_day_counts,
    parse_unadjusted_condition,
    thirty_360_days,
)
from loanwhiz.extraction.payment_schedule_parser import (
    PaymentDateSchedule,
    parse_payment_date_schedule,
)

TESTS_DIR = Path(__file__).parent
PROSPECTUS_FIXTURE = (
    TESTS_DIR / "fixtures" / "prospectus" / "cairn-clo-xvii-definitions-excerpt.txt"
)

#: The six classes Condition 6(e)(ii) names, and the one Condition 6(e)(iii) does.
#: Written out rather than derived from the parser's own output, so a parser that
#: attributed a limb's basis to the wrong classes reds instead of agreeing with
#: itself.
FLOATING_CLASSES = ("A", "B-1", "C", "D", "E", "F")
FIXED_CLASSES = ("B-2",)


@pytest.fixture(scope="module")
def prospectus_text() -> str:
    """The committed excerpt of the Listing Particulars.

    A verbatim slice of what ``pypdf`` extracts from the 420-page PDF — wrapped
    lines, stray intra-word spaces, hyphens split from their words and all — so
    the parser is exercised against the text it really sees rather than a tidied
    paraphrase.
    """
    return PROSPECTUS_FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def day_counts(prospectus_text: str) -> dict[str, ClassDayCount]:
    return parse_interest_day_counts(prospectus_text)


@pytest.fixture(scope="module")
def schedule(prospectus_text: str) -> PaymentDateSchedule:
    return parse_payment_date_schedule(prospectus_text)


# ---------------------------------------------------------------------------
# 1. What the Conditions say
# ---------------------------------------------------------------------------


def test_the_floating_limb_states_the_actual_day_count(
    day_counts: dict[str, ClassDayCount],
) -> None:
    """Condition 6(e)(ii) puts its six classes on the actual number of days."""
    for class_key in FLOATING_CLASSES:
        parsed = day_counts[class_key]
        assert parsed.basis == "act/360"
        assert parsed.condition == "6(e)(ii)"


def test_the_fixed_limb_states_a_year_of_twelve_thirty_day_months(
    day_counts: dict[str, ClassDayCount],
) -> None:
    """Condition 6(e)(iii) puts the Class B-2 Notes on 30/360.

    The sentence is ``"Interest is calculated on the basis of a 360-day year
    consisting of 12 months of 30 days each"``. Nothing about the published
    interest is consulted to reach it.
    """
    for class_key in FIXED_CLASSES:
        parsed = day_counts[class_key]
        assert parsed.basis == "30/360"
        assert parsed.condition == "6(e)(iii)"


def test_every_class_the_conditions_name_is_accounted_for(
    day_counts: dict[str, ClassDayCount],
) -> None:
    """No class is left without a basis, and none is invented."""
    assert set(day_counts) == set(FLOATING_CLASSES) | set(FIXED_CLASSES)


def test_the_accrual_period_proviso_names_the_fixed_limb(
    prospectus_text: str,
) -> None:
    """The unadjusted rule attaches to the limb the *document* names.

    The parser reads which Condition the *Accrual Period* proviso points at
    rather than assuming it is the fixed one, so this asserts the reading, not
    the assumption.
    """
    assert parse_unadjusted_condition(prospectus_text) == "6(e)(iii)"


def test_only_the_limb_the_proviso_names_measures_unadjusted(
    day_counts: dict[str, ClassDayCount],
) -> None:
    for class_key in FIXED_CLASSES:
        assert day_counts[class_key].unadjusted_payment_dates is True
    for class_key in FLOATING_CLASSES:
        assert day_counts[class_key].unadjusted_payment_dates is False


def test_the_implied_rate_type_matches_the_basis(
    day_counts: dict[str, ClassDayCount],
) -> None:
    """A fixed basis implies ``FXR``, a floating one ``FLR``.

    This is the value the Note Valuation Report prints independently; the
    cross-check that compares the two lives in
    ``tests/test_note_valuation_parser.py``.
    """
    assert {k: v.rate_type for k, v in day_counts.items()} == {
        **{k: "FLR" for k in FLOATING_CLASSES},
        **{k: "FXR" for k in FIXED_CLASSES},
    }


# ---------------------------------------------------------------------------
# 2. Refusals — a basis that is not stated is not known
# ---------------------------------------------------------------------------


FIXED_LIMB = (
    "(iii) Calculation of Fixed Interest Amounts "
    'The Class B-2 Notes bear interest at the rate of 6.87 per cent. per annum (such rate, the "Class B-2 Fixed Rate of Interest "). '
    "Interest is calculated on the basis of a 360 -day year consisting of 12 months of 30 days each, "
)

FLOATING_LIMB = (
    "(ii) Determination of Floating Rate of Interest and Calculation of Interest Amount "
    "determine the Class A Floating Rate of Interest, to an amount equal to the Principal Amount "
    "Outstanding in respect of the relevant Class of Notes, multiplying the product by the actual "
    "number of days in the Accrual Period concerned, divided by 360. "
)


def test_a_limb_stating_no_basis_is_refused() -> None:
    """Dropping the stated fraction leaves the convention unsourced.

    This is the ``red-when`` for the parser: with ``30 days each`` gone the
    Condition no longer says what B-2 accrues on, and the honest answer is a
    refusal naming the Condition — never "the other classes use act/360, so".
    """
    stripped = FIXED_LIMB.replace("12 months of 30 days each", "12 months")
    with pytest.raises(UnsourcedDayCount, match=r"6\(e\)\(iii\)"):
        parse_interest_day_counts(stripped)


def test_a_limb_stating_both_bases_is_refused() -> None:
    """An ambiguous Condition is refused rather than resolved by precedence."""
    both = FIXED_LIMB + (
        "multiplying the product by the actual number of days in the Accrual "
        "Period concerned, divided by 360. "
    )
    with pytest.raises(UnsourcedDayCount, match="both"):
        parse_interest_day_counts(both)


def test_a_limb_naming_no_class_is_refused() -> None:
    """A basis with nothing to attribute it to is not a result."""
    classless = FIXED_LIMB.replace("Class B-2", "relevant Class of")
    with pytest.raises(UnsourcedDayCount, match="names no Class"):
        parse_interest_day_counts(classless)


def test_text_with_no_conditions_yields_nothing_rather_than_refusing(
    schedule: PaymentDateSchedule,
) -> None:
    """A deal whose document states no 6(e) limb simply has no per-class basis.

    That is the Green Lion case, and it must be an empty result rather than an
    error: those deals keep the behaviour they have.
    """
    assert parse_interest_day_counts("A prospectus that says nothing relevant.") == {}


def test_the_fixed_limb_does_not_capture_the_classes_the_next_limb_names() -> None:
    """Limb (iv) enumerates every class; the fixed basis must not reach them.

    Condition 6(e)(iv) opens ``"so long as any Class A Notes, Class B Note,
    Class C Note ... remains Outstanding"``. A block that ran to the end of the
    text would hand all of them the 30/360 basis and still look like a clean
    parse, so this is the boundary that catches it.
    """
    with_next_limb = FIXED_LIMB + (
        "(iv) Reference Banks and Calculation Agent The Issuer will procure that, "
        "so long as any Class A Notes, Class B Note, Class C Note, Class D Note, "
        "Class E Note or Class F Note remains Outstanding: "
    )
    parsed = parse_interest_day_counts(with_next_limb)
    assert set(parsed) == {"B-2"}


# ---------------------------------------------------------------------------
# 3. Counting days on a basis
# ---------------------------------------------------------------------------


def test_thirty_360_counts_whole_months_between_scheduled_dates() -> None:
    """Two dates a quarter apart on the same day-of-month are three 30-day months."""
    assert thirty_360_days(date(2024, 10, 18), date(2025, 1, 18)) == 90
    assert thirty_360_days(date(2024, 4, 18), date(2024, 7, 18)) == 90


def test_act_360_counts_calendar_days() -> None:
    assert accrual_days("act/360", date(2024, 10, 18), date(2025, 1, 21)) == 95


@pytest.mark.parametrize(
    "endpoint",
    [
        pytest.param(date(2025, 1, 31), id="the-31st"),
        pytest.param(date(2025, 2, 28), id="last-day-of-february"),
        pytest.param(date(2024, 2, 29), id="last-day-of-a-leap-february"),
    ],
)
def test_a_variant_sensitive_endpoint_is_refused(endpoint: date) -> None:
    """Where 30/360's variants disagree, the Condition has not decided.

    It names the family — "12 months of 30 days each" — and not the member, so a
    period ending on one of these dates has no sourceable day count. Picking a
    member to get an answer is the guess this module exists to refuse.
    """
    with pytest.raises(UnsourcedDayCount, match="disagree"):
        thirty_360_days(date(2024, 10, 18), endpoint)


# ---------------------------------------------------------------------------
# 4. The two bases measure between different pairs of dates
# ---------------------------------------------------------------------------


def test_a_floating_class_measures_between_adjusted_payment_dates(
    schedule: PaymentDateSchedule, day_counts: dict[str, ClassDayCount]
) -> None:
    """The January 2025 period, for a class that adjusts.

    18 January 2025 is a Saturday and 20 January is a US holiday, so the Payment
    Date rolls to Tuesday 21 January; the period runs from Friday 18 October
    2024, which needs no adjustment.
    """
    assert class_accrual_days(schedule, day_counts["B-1"], date(2025, 1, 21)) == 95


def test_the_fixed_class_measures_between_scheduled_payment_dates(
    schedule: PaymentDateSchedule, day_counts: dict[str, ClassDayCount]
) -> None:
    """The same period for B-2, under the *Accrual Period* proviso.

    The proviso says the Payment Date "shall not be adjusted" for Condition
    6(e)(iii) purposes, so the period runs between the two *scheduled* dates —
    18 October 2024 and 18 January 2025 — on the 30/360 basis its Condition
    states. The result follows from those two stated dates and that stated
    fraction; it is not checked against any published figure.
    """
    assert class_accrual_days(schedule, day_counts["B-2"], date(2025, 1, 21)) == 90


def test_the_two_strips_of_class_b_disagree(
    schedule: PaymentDateSchedule, day_counts: dict[str, ClassDayCount]
) -> None:
    """The property that makes this per-class rather than per-deal.

    B-1 and B-2 are one class in the waterfall and two conventions in the
    Conditions, so any deal-level day-count setting is unable to express this
    deal at all.
    """
    payment = date(2025, 1, 21)
    assert class_accrual_days(
        schedule, day_counts["B-1"], payment
    ) != class_accrual_days(schedule, day_counts["B-2"], payment)


def test_an_unscheduled_payment_date_has_no_unadjusted_counterpart(
    schedule: PaymentDateSchedule, day_counts: dict[str, ClassDayCount]
) -> None:
    """A Redemption Date is a Payment Date the schedule does not state.

    The fixed basis needs the *scheduled* date behind an actual one, and an
    Unscheduled Payment Date has none — so it refuses rather than measuring
    between whatever dates happen to be nearby.
    """
    with pytest.raises(UnsourcedDayCount, match="Scheduled Payment Date"):
        class_accrual_days(schedule, day_counts["B-2"], date(2025, 3, 5))
