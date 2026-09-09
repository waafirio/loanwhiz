"""Per-class day-count basis — the fraction each Class's interest accrues on (#539).

Why this module exists
----------------------
:func:`~loanwhiz.primitives.waterfall_interpreter._accrued_interest` accrues every
tranche ``balance × rate/100 / 360 × days``, and until now ``days`` was one scalar
on :class:`~loanwhiz.primitives.waterfall_interpreter.WaterfallFunds` — the whole
deal on one convention. #528 made that scalar a *derived* number (the distance
between two stated Payment Dates) instead of a flat ``90``, which closed Cairn's
Class A and Class C.

It did not close Class B, and #538 measured why rather than assuming: Cairn issues
Class B in two strips under **two different conventions**. B-1 is floating and
accrues on the actual day count; B-2 is the deal's one genuinely fixed strip — the
Note Valuation Report prints it ``FXR`` where every other class is ``FLR`` — and
accrues on a 30/360 basis over *unadjusted* Payment Dates. One scalar cannot say
both, and a deal-level setting cannot either: the difference is *within* one class.

#538 stopped at exactly the right place. The residual it measured could have been
closed by picking 30/360 because that made the figure tie, and that back-solving is
what #511 and #528 spent this epic removing. The convention has to come from the
document or not at all.

Where the convention actually lives
-----------------------------------
It is stated outright, per class, in the Listing Particulars' Conditions — and the
seed does not carry it for the same reason it did not carry the Payment Date
schedule: :mod:`loanwhiz.extraction.definitions_graph` truncates the Definitions
section at ``max_chars=40_000`` (``definitions_graph.py:195``), an alphabetical
glossary truncates to A–B, and the *Conditions* are a different section entirely
that the definitions pass never emits. So this is #528's situation one Condition
further out, and it takes #528's answer: ``pypdf`` reads the 420-page text layer
in seconds — no Docling, no LLM, no credentials, same bytes every run — so the
lost fact is recovered by a deterministic parser over committed document text.

Condition 6(e)(ii) (*Determination of Floating Rate of Interest and Calculation of
Interest Amount*) names Classes A, B-1, C, D, E and F, and calculates by::

    multiplying the product by the actual number of days in the Accrual
    Period concerned, divided by 360

Condition 6(e)(iii) (*Calculation of Fixed Interest Amounts*) names Class B-2, and::

    Interest is calculated on the basis of a 360-day year consisting of
    12 months of 30 days each

    ... multiplying the product by the number of days in the Accrual Period
    concerned (the number of days to be calculated on the basis of a year of
    360 days with 12 months of 30 days each), divided by 360

Both divide by 360. **Only the numerator differs** — the day count, not the
formula — which is why the engine change this feeds is a per-tranche day count
rather than a per-tranche formula.

Nothing here reads a published interest figure, and no figure was used as a target,
a check or a bound. The basis of each class is whichever phrase that class's own
Condition states; if a Condition states neither phrase, or both, this module
refuses rather than choosing.

Why the fixed basis measures over *unadjusted* dates
-----------------------------------------------------
The *Accrual Period* definition — which the seed does carry — closes with a proviso::

    provided that, for the purposes of calculating interest payable in
    accordance with Condition 6(e)(iii) (Calculation of Fixed Interest
    Amounts), the Payment Date (for the purposes of determining the Accrual
    Period) shall not be adjusted if the relevant Payment Date would have
    fallen other than on a Business Day but for the proviso in the definition
    of Payment Date.

:func:`parse_unadjusted_condition` reads *which Condition limb* that proviso names
rather than assuming it is the fixed one, so a deal whose proviso pointed elsewhere
would be read correctly instead of being forced into this deal's shape.

The 30/360 variant question, and why it does not arise
-------------------------------------------------------
"12 months of 30 days each" names a family, not a member: 30/360 US (Bond),
30E/360 (Eurobond) and 30E/360 ISDA differ, but **only** when an endpoint falls on
the 31st or on the last day of February. Every Payment Date this deal states falls
on the 18th, so all three agree on every period the schedule can produce and no
unstated pick is needed. :func:`thirty_360_days` enforces that rather than trusting
it: an endpoint where the variants would diverge raises
:class:`UnsourcedDayCount`, because at that date the document genuinely does not
say which member is meant, and guessing one would be the same back-solving this
module exists to avoid.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date
from typing import Literal

from loanwhiz.extraction.payment_schedule_parser import (
    PaymentDateSchedule,
    payment_date_for,
    previous_payment_date,
    scheduled_dates_in_year,
)

__all__ = [
    "ClassDayCount",
    "DayCountBasis",
    "UnsourcedDayCount",
    "accrual_days",
    "class_accrual_days",
    "parse_interest_day_counts",
    "parse_unadjusted_condition",
    "thirty_360_days",
]


#: The two bases Condition 6(e) states. Both divide by 360; they differ in the
#: numerator only — ``act/360`` counts calendar days, ``30/360`` counts on a year
#: of twelve 30-day months.
DayCountBasis = Literal["act/360", "30/360"]


class UnsourcedDayCount(ValueError):
    """A class's day-count basis could not be read from the document.

    Raised rather than defaulted, and deliberately never rescued by "the other
    classes use X, so this one probably does too". A basis that is not stated is
    not known, and #493's rule applies: the need reports ``not_evaluable`` naming
    the unsourced convention rather than accruing on a plausible one. The whole
    point of #539 is that the convention comes from the document; a fallback here
    would quietly reintroduce the assumption it removes.
    """


@dataclass(frozen=True)
class ClassDayCount:
    """The day-count basis one Class of Notes accrues on, and where it was stated.

    Attributes
    ----------
    class_key:
        Class identifier as the Conditions write it — ``"A"``, ``"B-1"``,
        ``"B-2"``, ``"C"``. Matches :mod:`loanwhiz.primitives.note_valuation_parser`'s
        ``_class_key`` output so the report's per-class rate type can be
        cross-checked against this without a second naming scheme.
    basis:
        ``act/360`` or ``30/360`` — read from the Condition's own words.
    unadjusted_payment_dates:
        Whether this class's Accrual Period is measured between *scheduled*
        Payment Dates rather than the business-day-adjusted ones, per the
        *Accrual Period* proviso.
    condition:
        The Condition limb this came from, e.g. ``"6(e)(iii)"`` — carried so a
        refusal and the data card can name the source rather than gesture at it.
    rate_type:
        ``"FXR"`` or ``"FLR"`` — the rate type this basis *implies*, for the
        cross-check against what the Note Valuation Report independently prints.
        A fixed day-count basis is stated in the Condition that fixes the rate.
    """

    class_key: str
    basis: DayCountBasis
    unadjusted_payment_dates: bool
    condition: str
    rate_type: Literal["FXR", "FLR"]


# ===========================================================================
# Text normalisation
# ===========================================================================

#: pypdf's text layer wraps lines mid-sentence and sprinkles stray spaces inside
#: words and around hyphens — ``"Class  B -2"``, ``"360 -day"``, ``"inte rest"``.
#: Collapsing whitespace fixes the first two classes of artefact; the third is
#: left alone because no pattern here depends on a word pypdf split.
_HYPHEN_SPACES = re.compile(r"(?<=[A-Za-z0-9])\s*-\s*(?=[A-Za-z0-9])")


def _collapse(text: str) -> str:
    """Collapse pypdf's line wrapping into one whitespace-normalised string.

    Whitespace runs (including the newlines pypdf inserts mid-sentence) become a
    single space, and spaces straddling a hyphen between two alphanumerics are
    removed so ``"Class B -2"`` and ``"360 -day"`` read as written.

    Args:
        text: Prospectus text as ``pypdf`` extracts it, line wrapping and all.

    Returns:
        The same text on one line, with the two artefacts above removed.
    """
    return _HYPHEN_SPACES.sub("-", re.sub(r"\s+", " ", text)).strip()


# ===========================================================================
# The Condition patterns
# ===========================================================================

#: Condition 6(e) limb headings. The Conditions number their limbs with lowercase
#: roman numerals, and each heading names what the limb calculates.
_LIMB_HEADINGS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "6(e)(ii)",
        re.compile(
            r"\(ii\)\s*Determination of Floating Rate of Interest and "
            r"Calculation of Interest Amount",
            re.I,
        ),
    ),
    (
        "6(e)(iii)",
        re.compile(r"\(iii\)\s*Calculation of Fixed Interest Amounts", re.I),
    ),
)

#: "the actual number of days in the Accrual Period concerned, divided by 360".
_ACT_360 = re.compile(
    r"the actual number of days in the Accrual Period concerned,?\s*divided by 360",
    re.I,
)

#: The 30/360 family, in either of the two forms Condition 6(e)(iii) uses: the
#: summary sentence ("a 360-day year consisting of 12 months of 30 days each")
#: and the operative parenthetical ("a year of 360 days with 12 months of 30
#: days each"). Either alone is sufficient; the Condition happens to state both.
_THIRTY_360 = re.compile(
    r"(?:360-day year consisting of|year of 360 days with)\s*"
    r"12 months of 30 days each",
    re.I,
)

#: A Class named in a Condition: ``Class A``, ``Class B-1``, ``Class B-2``. The
#: trailing boundary stops ``"Class of Notes"`` and ``"Class Subordinated"`` from
#: matching, and the strip suffix is optional because most classes have none.
_CLASS = re.compile(r"\bClass\s+([A-F](?:-\d)?)\b")

#: The *Accrual Period* proviso naming the Condition limb whose Payment Dates are
#: not business-day adjusted. The limb is captured rather than assumed.
_UNADJUSTED = re.compile(
    r"Condition\s*6\(e\)\((?P<limb>i{1,3})\)[^.]*?shall not be adjusted",
    re.I,
)


#: Any lowercase-roman limb heading — ``"(iv) Reference Banks and Calculation
#: Agent"``. Used only to find where a limb *ends*, which matters because the limb
#: after the fixed-rate one enumerates Classes A through F for an unrelated
#: purpose: a block that ran to the end of the text would attribute the 30/360
#: basis to every class in the deal. Case-sensitive, and requiring a capital after
#: the bracket, so the enumerations *inside* a limb (``"(i) for each Accrual
#: Period"``) and its uppercase sub-limbs (``"(D) Where:"``) are not boundaries.
_ANY_LIMB = re.compile(r"\((?:i{1,3}|iv|vi{0,3}|ix|xi{0,3}|x)\)\s+[A-Z]")


def _limb_blocks(collapsed: str) -> dict[str, str]:
    """Split the collapsed Conditions text into ``limb → its own text``.

    Each limb runs from its heading to the next limb heading of any number, or to
    the end of the text. Slicing this way is what keeps the two bases independent:
    a limb's basis is read from *its* words, never inherited from the section
    around it, and never spread onto the Classes a later limb happens to name.
    """
    blocks: dict[str, str] = {}
    for limb, pattern in _LIMB_HEADINGS:
        match = pattern.search(collapsed)
        if match is None:
            continue
        following = _ANY_LIMB.search(collapsed, match.end())
        end = following.start() if following is not None else len(collapsed)
        blocks[limb] = collapsed[match.start() : end]
    return blocks


def parse_unadjusted_condition(text: str) -> str | None:
    """The Condition limb whose Accrual Period uses unadjusted Payment Dates.

    Reads the *Accrual Period* proviso and returns the limb it names — e.g.
    ``"6(e)(iii)"`` — or ``None`` when the text states no such proviso, which is
    the ordinary case for a deal where every class adjusts.

    Args:
        text: Prospectus text containing the ``"Accrual Period"`` definition.

    Returns:
        The limb identifier, or ``None`` if no proviso is stated.
    """
    match = _UNADJUSTED.search(_collapse(text))
    if match is None:
        return None
    return f"6(e)({match.group('limb').lower()})"


def parse_interest_day_counts(text: str) -> dict[str, ClassDayCount]:
    """Parse Condition 6(e) into each Class's stated day-count basis.

    Reads every 6(e) limb the text contains, takes each limb's basis from its own
    words, and attributes it to every Class that limb names. The *Accrual Period*
    proviso (:func:`parse_unadjusted_condition`) decides which limb measures over
    unadjusted Payment Dates.

    Args:
        text: Prospectus text as ``pypdf`` extracts it, covering the Conditions
            and — for the unadjusted proviso — the ``"Accrual Period"`` definition.

    Returns:
        ``class_key → ClassDayCount``, empty when the text contains no 6(e) limb.

    Raises:
        UnsourcedDayCount: If a limb states no basis, states both, or names no
            Class. A partially-read Condition is refused rather than half-applied
            — the same discipline ``payment_schedule_parser`` applies to a
            partially-read *Business Day* definition.
    """
    collapsed = _collapse(text)
    unadjusted_limb = parse_unadjusted_condition(collapsed)

    day_counts: dict[str, ClassDayCount] = {}
    for limb, block in _limb_blocks(collapsed).items():
        is_act = _ACT_360.search(block) is not None
        is_thirty = _THIRTY_360.search(block) is not None
        if is_act == is_thirty:
            raise UnsourcedDayCount(
                f"Condition {limb} states "
                f"{'both an actual/360 and a 30/360' if is_act else 'no'} "
                "day-count basis — the convention cannot be sourced from it, and "
                "inferring one from the other limbs would be the assumption #539 "
                "exists to remove"
            )

        basis: DayCountBasis = "act/360" if is_act else "30/360"
        classes = dict.fromkeys(_CLASS.findall(block))
        if not classes:
            raise UnsourcedDayCount(
                f"Condition {limb} states a {basis} basis but names no Class of "
                "Notes, so there is nothing to attribute it to"
            )

        for class_key in classes:
            day_counts[class_key] = ClassDayCount(
                class_key=class_key,
                basis=basis,
                unadjusted_payment_dates=limb == unadjusted_limb,
                condition=limb,
                rate_type="FXR" if basis == "30/360" else "FLR",
            )
    return day_counts


# ===========================================================================
# Counting days on a basis
# ===========================================================================


def _variant_sensitive(day: date) -> bool:
    """Whether the 30/360 variants disagree about this endpoint.

    30/360 US, 30E/360 and 30E/360 ISDA treat the 31st and the last day of
    February differently; on every other date they agree exactly. A Condition
    saying only "12 months of 30 days each" names the family, so a date where the
    members diverge is a date the document does not decide.
    """
    if day.day == 31:
        return True
    return day.month == 2 and day.day == calendar.monthrange(day.year, 2)[1]


def thirty_360_days(start: date, end: date) -> int:
    """Days from ``start`` to ``end`` on a year of twelve 30-day months.

    Args:
        start: First day of the period (included).
        end:   Day the period runs to (excluded).

    Returns:
        The 30/360 day count.

    Raises:
        UnsourcedDayCount: If either endpoint is one where the 30/360 variants
            disagree — the 31st, or the last day of February. The Condition names
            the family and not the member, so the answer is genuinely unstated
            there and picking a member would be a guess.
    """
    for label, day in (("start", start), ("end", end)):
        if _variant_sensitive(day):
            raise UnsourcedDayCount(
                f"the Accrual Period's {label} date {day.isoformat()} is one where "
                "30/360 US, 30E/360 and 30E/360 ISDA disagree; the Condition states "
                '"12 months of 30 days each" without naming which, so the day '
                "count cannot be sourced for this period"
            )
    return (
        360 * (end.year - start.year)
        + 30 * (end.month - start.month)
        + (end.day - start.day)
    )


def accrual_days(basis: DayCountBasis, start: date, end: date) -> int:
    """Days in the period ``[start, end)`` on ``basis``.

    Args:
        basis: The stated day-count basis.
        start: First day of the Accrual Period (included).
        end:   Payment Date the period runs to (excluded).

    Returns:
        The day count that basis produces.
    """
    if basis == "act/360":
        return (end - start).days
    return thirty_360_days(start, end)


def _scheduled_date_for(schedule: PaymentDateSchedule, payment: date) -> date:
    """The scheduled (unadjusted) date that ``payment`` is the adjusted form of.

    The adjustment can move a date across a month boundary in either direction, so
    the search covers the neighbouring years rather than assuming the scheduled
    date shares the actual one's year.

    Raises:
        UnsourcedDayCount: If no scheduled date resolves to ``payment`` — the
            Payment Date is then an Unscheduled one (a Redemption Date, the Final
            Distribution Date), which no schedule states and which therefore has
            no unadjusted counterpart to measure between.
    """
    for year in (payment.year - 1, payment.year, payment.year + 1):
        for scheduled in scheduled_dates_in_year(schedule, year):
            if payment_date_for(schedule, scheduled) == payment:
                return scheduled
    raise UnsourcedDayCount(
        f"{payment.isoformat()} is not a Scheduled Payment Date in the stated "
        "schedule, so it has no unadjusted counterpart; an Unscheduled Payment "
        "Date's fixed-basis Accrual Period is not derivable from the schedule"
    )


def class_accrual_days(
    schedule: PaymentDateSchedule,
    day_count: ClassDayCount,
    payment: date,
) -> int:
    """Days in the Accrual Period ending on ``payment``, for one Class.

    The two bases measure between different pairs of dates, which is the whole
    reason this is per-class: a floating class's period runs between the two
    *actual* (business-day adjusted) Payment Dates, while a class under the
    *Accrual Period* proviso measures between the two *scheduled* ones.

    Args:
        schedule:  The deal's stated Payment Date schedule.
        day_count: The class's basis, from :func:`parse_interest_day_counts`.
        payment:   The actual Payment Date the period ends on.

    Returns:
        The day count for that class over that period.

    Raises:
        UnsourcedDayCount: If an unadjusted period is asked for a Payment Date the
            schedule does not state, or if the period's endpoints fall where the
            30/360 variants disagree.
    """
    if not day_count.unadjusted_payment_dates:
        return accrual_days(
            day_count.basis, previous_payment_date(schedule, payment), payment
        )

    end = _scheduled_date_for(schedule, payment)
    start = _scheduled_date_for(
        schedule, previous_payment_date(schedule, payment)
    )
    return accrual_days(day_count.basis, start, end)
