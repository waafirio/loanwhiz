"""Payment Date schedule — the stated calendar an accrual period is measured on (#528).

Why this module exists
----------------------
:class:`~loanwhiz.primitives.waterfall_interpreter.WaterfallFunds` accrues note
interest Act/360 over ``days_in_period``, and the report path
(:mod:`loanwhiz.primitives.report_adapter`) had no source for that number, so it
asserted a flat ``90`` — a quarterly approximation. For Cairn CLO XVII that
assumption was the *entire* residual on the epic's first two engine-computed
lines (#511): Class A and Class C each reproduced their published interest only
at a **95**-day period.

That 95 was reachable one way — dividing the published interest by the engine's
own figure — which is precisely the circularity epic #510 exists to delete. #521
stood down rather than commit it, after establishing that Cairn's Note Valuation
Report states no accrual period at all: it prints point-in-time dates
(``Calculation Date``, ``Next Payment Date``) and no period range.

This module takes the non-circular route. The deal's own *Accrual Period*
definition is payment-date to payment-date, so the accrual period is derivable
from the **Payment Date schedule**, which the Listing Particulars state outright::

    "Payment Date" means: ...
    (b) 18 January, 18 April, 18 October and 18 July at all other times,
    in each case, in each year commencing on 18 April 2024 ...
    provided that if any Payment Date would otherwise fall on a day which is
    not a Business Day, it shall be postponed to the next day that is a
    Business Day (unless it would thereby fall in the following month, in
    which case it shall be brought forward to the immediately preceding
    Business Day).

Nothing here reads a published interest figure. The day count is the distance
between two dates the prospectus names.

Why a regex parser rather than the LLM extractor
------------------------------------------------
:mod:`loanwhiz.extraction.definitions_graph` pastes the Definitions section into
one Gemini prompt truncated at ``max_chars=40_000`` (``definitions_graph.py:195``,
sliced at ``:240``). A glossary is alphabetical, so the truncation is not a random
sample — it drops C–Z wholesale, and Cairn's committed seed stops at
``"Bankruptcy Exchange Test"``. ``Payment Date`` and ``Business Day`` both fall in
the lost range.

Raising that budget would need Vertex credentials, re-run the whole extraction and
non-deterministically rewrite every other section of the seed. #480 hit the same
wall for the coverage-test required levels and answered it the same way: recover
the specific lost fact with a deterministic parser over the document's own text.
``pypdf`` reads the 420-page Listing Particulars' text layer in seconds — no
Docling OCR, no LLM, no credentials, and the same bytes every run.

The calendar
------------
Resolving a scheduled date to an actual Payment Date needs the deal's Business Day
definition, which the same glossary states: T2 open, **and** commercial banks
settling in **London, Dublin and New York**. :data:`BUSINESS_CENTRE_HOLIDAYS`
carries those centres' published holidays for the years it declares in
:data:`COVERED_YEARS`; outside them :func:`adjust_to_business_day` raises rather
than guessing, because a silently-wrong calendar produces a plausible wrong day
count and no error anywhere.

The table is checkable against the documents rather than trusted: the reports
state their own ``Next Payment Date``, and
``tests/test_payment_schedule_parser.py`` asserts that every date this module
derives equals the one the committed report prints. That cross-check is what
would catch a mis-entered holiday.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta

__all__ = [
    "BUSINESS_CENTRE_HOLIDAYS",
    "COVERED_YEARS",
    "PaymentDateSchedule",
    "UnresolvableBusinessDay",
    "accrual_period_days",
    "adjust_to_business_day",
    "is_business_day",
    "parse_business_day_centres",
    "parse_payment_date_schedule",
    "payment_date_for",
    "payment_date_on_or_after",
    "previous_payment_date",
    "scheduled_dates_in_year",
]


# ===========================================================================
# Errors
# ===========================================================================


class UnresolvableBusinessDay(ValueError):
    """A date could not be resolved against the committed holiday calendar.

    Raised rather than defaulted. A deal whose schedule *is* modelled but whose
    dates fall outside :data:`COVERED_YEARS` must not silently fall back to the
    90-day approximation — that would reintroduce, invisibly, the assumption this
    module exists to replace.
    """


# ===========================================================================
# The calendar
# ===========================================================================

#: Years :data:`BUSINESS_CENTRE_HOLIDAYS` covers. Cairn CLO XVII's first Payment
#: Date is 18 April 2024 and the committed reports run to March 2025; the range is
#: deliberately narrow, because an unstated year must raise rather than be guessed.
COVERED_YEARS: frozenset[int] = frozenset({2024, 2025, 2026})

#: Published non-settlement days for the centres Cairn's ``"Business Day"``
#: definition names — T2 plus London, Dublin and New York.
#:
#: Provenance, per centre:
#:
#: - **T2** (the Eurosystem RTGS successor to TARGET2) closes on 1 January, Good
#:   Friday, Easter Monday, 1 May, 25 December and 26 December — the ECB's fixed
#:   closing-day list, which has no weekend substitution.
#: - **London** — England & Wales bank holidays (GOV.UK), which substitute the
#:   following weekday when a fixed date falls on a weekend.
#: - **Dublin** — Ireland's public holidays (Citizens Information), including
#:   St Brigid's Day (first Monday in February) from 2023.
#: - **New York** — US federal holidays (OPM), observed-date convention.
#:
#: Easter anchors the movable feasts: Easter Sunday is 31 March 2024,
#: 20 April 2025 and 5 April 2026.
#:
#: Only whole dates matter here; a centre's half-days are irrelevant because the
#: definition asks whether banks *settle payments*, not for how long.
BUSINESS_CENTRE_HOLIDAYS: dict[str, frozenset[date]] = {
    "T2": frozenset(
        {
            date(2024, 1, 1),
            date(2024, 3, 29),  # Good Friday
            date(2024, 4, 1),  # Easter Monday
            date(2024, 5, 1),
            date(2024, 12, 25),
            date(2024, 12, 26),
            date(2025, 1, 1),
            date(2025, 4, 18),  # Good Friday
            date(2025, 4, 21),  # Easter Monday
            date(2025, 5, 1),
            date(2025, 12, 25),
            date(2025, 12, 26),
            date(2026, 1, 1),
            date(2026, 4, 3),  # Good Friday
            date(2026, 4, 6),  # Easter Monday
            date(2026, 5, 1),
            date(2026, 12, 25),
        }
    ),
    "London": frozenset(
        {
            date(2024, 1, 1),
            date(2024, 3, 29),
            date(2024, 4, 1),
            date(2024, 5, 6),
            date(2024, 5, 27),
            date(2024, 8, 26),
            date(2024, 12, 25),
            date(2024, 12, 26),
            date(2025, 1, 1),
            date(2025, 4, 18),
            date(2025, 4, 21),
            date(2025, 5, 5),
            date(2025, 5, 26),
            date(2025, 8, 25),
            date(2025, 12, 25),
            date(2025, 12, 26),
            date(2026, 1, 1),
            date(2026, 4, 3),
            date(2026, 4, 6),
            date(2026, 5, 4),
            date(2026, 5, 25),
            date(2026, 8, 31),
            date(2026, 12, 25),
            date(2026, 12, 28),  # Boxing Day falls on a Saturday; substitute Monday
        }
    ),
    "Dublin": frozenset(
        {
            date(2024, 1, 1),
            date(2024, 2, 5),  # St Brigid's Day (first Monday in February)
            date(2024, 3, 18),  # St Patrick's Day falls on a Sunday; substitute Monday
            date(2024, 4, 1),
            date(2024, 5, 6),
            date(2024, 6, 3),
            date(2024, 8, 5),
            date(2024, 10, 28),
            date(2024, 12, 25),
            date(2024, 12, 26),
            date(2025, 1, 1),
            date(2025, 2, 3),
            date(2025, 3, 17),
            date(2025, 4, 21),
            date(2025, 5, 5),
            date(2025, 6, 2),
            date(2025, 8, 4),
            date(2025, 10, 27),
            date(2025, 12, 25),
            date(2025, 12, 26),
            date(2026, 1, 1),
            date(2026, 2, 2),
            date(2026, 3, 17),
            date(2026, 4, 6),
            date(2026, 5, 4),
            date(2026, 6, 1),
            date(2026, 8, 3),
            date(2026, 10, 26),
            date(2026, 12, 25),
            date(2026, 12, 28),  # St Stephen's Day falls on a Saturday; substitute
        }
    ),
    "New York": frozenset(
        {
            date(2024, 1, 1),
            date(2024, 1, 15),  # Martin Luther King Jr. Day
            date(2024, 2, 19),  # Washington's Birthday
            date(2024, 5, 27),  # Memorial Day
            date(2024, 6, 19),  # Juneteenth
            date(2024, 7, 4),
            date(2024, 9, 2),  # Labor Day
            date(2024, 10, 14),  # Columbus Day
            date(2024, 11, 11),  # Veterans Day
            date(2024, 11, 28),  # Thanksgiving
            date(2024, 12, 25),
            date(2025, 1, 1),
            date(2025, 1, 20),  # Martin Luther King Jr. Day
            date(2025, 2, 17),
            date(2025, 5, 26),
            date(2025, 6, 19),
            date(2025, 7, 4),
            date(2025, 9, 1),
            date(2025, 10, 13),
            date(2025, 11, 11),
            date(2025, 11, 27),
            date(2025, 12, 25),
            date(2026, 1, 1),
            date(2026, 1, 19),
            date(2026, 2, 16),
            date(2026, 5, 25),
            date(2026, 6, 19),
            date(2026, 7, 3),  # Independence Day falls on a Saturday; observed Friday
            date(2026, 9, 7),
            date(2026, 10, 12),
            date(2026, 11, 11),
            date(2026, 11, 26),
            date(2026, 12, 25),
        }
    ),
}


# ===========================================================================
# The schedule
# ===========================================================================

#: Month names as the Listing Particulars spell them, in the order a date parser
#: needs them.
_MONTHS: dict[str, int] = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


@dataclass(frozen=True)
class PaymentDateSchedule:
    """The Payment Date schedule a prospectus states, as structured data.

    Attributes:
        day_of_month: The scheduled day, unadjusted (Cairn: ``18``).
        months:       Scheduled months, ascending (Cairn: January, April, July,
                      October).
        commencing:   First Scheduled Payment Date (Cairn: 18 April 2024). Dates
                      before it are not Payment Dates.
        convention:   Business-day convention; ``"modified_following"`` is the
                      "postponed … unless it would thereby fall in the following
                      month" proviso.
        business_centres: Centres whose settlement the ``"Business Day"``
                      definition requires, keys of :data:`BUSINESS_CENTRE_HOLIDAYS`.
        source:       Where in the document this was read from, for the data card.
    """

    day_of_month: int
    months: tuple[int, ...]
    commencing: date
    convention: str = "modified_following"
    business_centres: tuple[str, ...] = ("T2", "London", "Dublin", "New York")
    source: str = ""

    def to_dict(self) -> dict[str, object]:
        """Serialise for the deal-model seed JSON."""
        return {
            "day_of_month": self.day_of_month,
            "months": list(self.months),
            "commencing": self.commencing.isoformat(),
            "convention": self.convention,
            "business_centres": list(self.business_centres),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "PaymentDateSchedule":
        """Rebuild from the seed JSON written by :meth:`to_dict`."""
        return cls(
            day_of_month=int(raw["day_of_month"]),  # type: ignore[arg-type]
            months=tuple(int(m) for m in raw["months"]),  # type: ignore[union-attr]
            commencing=date.fromisoformat(str(raw["commencing"])),
            convention=str(raw.get("convention", "modified_following")),
            business_centres=tuple(
                str(c) for c in raw.get("business_centres", ())  # type: ignore[union-attr]
            ),
            source=str(raw.get("source", "")),
        )


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

#: The definition block, from its opening quote to the start of the **next
#: definition**. pypdf's text layer wraps mid-sentence and leaves stray spaces
#: inside words ("b e brought forward"), so every downstream pattern runs over
#: whitespace-collapsed text rather than the raw lines.
#:
#: The terminator requires the following quoted term to be the subject of its own
#: ``means``. Stopping at any quoted capitalised phrase looks equivalent and is
#: not: the Payment Date definition *labels* one inline (``(each a "Scheduled
#: Payment Date")``), which truncates the block before its business-day proviso
#: and silently downgrades the convention to plain "following".
_NEXT_DEFINITION = r'(?="[A-Z][^"]{2,80}"\s*means)'
_PAYMENT_DATE_BLOCK_RE = re.compile(
    r'"Payment Date"\s+means:\s*(?P<body>.*?)' + _NEXT_DEFINITION, re.DOTALL
)
_BUSINESS_DAY_BLOCK_RE = re.compile(
    r'"Business Day"\s+means\s*(?P<body>.*?)' + _NEXT_DEFINITION, re.DOTALL
)

#: ``(b) 18 January, 18 April, 18 October and 18 July at all other times,`` — the
#: standing schedule. Paragraph (a) is the post-Frequency-Switch-Event semi-annual
#: fallback and is deliberately NOT read: it applies only after an event the
#: reports would state, and reading it would silently halve the schedule.
_STANDING_SCHEDULE_RE = re.compile(
    r"\(b\)\s+(?P<dates>\d{1,2}\s+[A-Z][a-z]+(?:\s*,\s*\d{1,2}\s+[A-Z][a-z]+)*"
    r"(?:\s+and\s+\d{1,2}\s+[A-Z][a-z]+)?)\s+at all other times"
)

_DAY_MONTH_RE = re.compile(r"(?P<day>\d{1,2})\s+(?P<month>[A-Z][a-z]+)")

#: ``in each year commencing on 18 April 2024``
_COMMENCING_RE = re.compile(
    r"commencing on\s+(?P<day>\d{1,2})\s+(?P<month>[A-Z][a-z]+)\s+(?P<year>\d{4})"
)

#: The business-day proviso. The "unless it would thereby fall in the following
#: month" tail is what makes it *modified* following rather than plain following.
_POSTPONED_RE = re.compile(r"postponed to the next day that is a Business Day")
_MODIFIED_RE = re.compile(r"fall in the following month")

#: ``(b) on which commercial banks ... settle payments in London, Dublin and New
#: York``. T2 is stated in its own limb ``(a)`` and added separately.
#:
#: Deliberately delimiter-terminated rather than shaped to Cairn's wording. An
#: earlier version required a ``(`` immediately after the list — true of this
#: document's parenthetical and of nothing in general — and a prospectus phrasing
#: the limb without it parsed to ``["T2"]`` alone, silently, yielding a 94-day
#: January period instead of 95. Every centre dropped moves dates.
_CENTRES_RE = re.compile(r"settle payments in\s+(?P<centres>[^;.()]+)")

#: Limb ``(c)`` says "settle payments in **that place**" — a back-reference, not a
#: centre. It matches the pattern above and must not become a business centre.
_NOT_A_CENTRE = frozenset({"that place", "that jurisdiction", "each such place"})
_T2_RE = re.compile(r"\bT2\b\s+is open for settlement")


def _collapse(text: str) -> str:
    """Collapse pypdf's line wrapping into one whitespace-normalised string.

    The text layer breaks lines mid-word and mid-sentence, so patterns that would
    be trivial on the printed page fail on the raw extract. Collapsing first is
    what lets every pattern in this module stay readable.
    """
    return re.sub(r"\s+", " ", text)


def parse_payment_date_schedule(text: str) -> PaymentDateSchedule | None:
    """Parse the ``"Payment Date"`` definition out of prospectus text.

    Args:
        text: Prospectus text as ``pypdf`` extracts it (line wrapping and all).

    Returns:
        The stated schedule, or ``None`` when the definition is absent — a
        document that does not define Payment Date has no schedule to read, which
        is a finding, not an error.

    Raises:
        ValueError: If the definition is present but its standing schedule or
            commencement date cannot be read. A half-parsed schedule is more
            dangerous than none, because it still yields a number.
    """
    block = _PAYMENT_DATE_BLOCK_RE.search(text)
    if block is None:
        return None
    body = _collapse(block.group("body"))

    standing = _STANDING_SCHEDULE_RE.search(body)
    if standing is None:
        raise ValueError(
            '"Payment Date" is defined but its standing schedule — the '
            '"(b) … at all other times" limb — could not be read; refusing to '
            "guess a payment frequency"
        )

    days: set[int] = set()
    months: set[int] = set()
    for match in _DAY_MONTH_RE.finditer(standing.group("dates")):
        month = _MONTHS.get(match.group("month").lower())
        if month is None:
            continue
        days.add(int(match.group("day")))
        months.add(month)

    if len(days) != 1 or not months:
        raise ValueError(
            f"Payment Date schedule is not a single day-of-month across its "
            f"months (days={sorted(days)}, months={sorted(months)})"
        )

    commencing = _COMMENCING_RE.search(body)
    if commencing is None:
        raise ValueError(
            '"Payment Date" is defined but states no commencement date; the '
            "first Scheduled Payment Date is required to bound the schedule"
        )
    commencing_month = _MONTHS.get(commencing.group("month").lower())
    if commencing_month is None:
        raise ValueError(
            f"unrecognised commencement month {commencing.group('month')!r}"
        )

    if not _POSTPONED_RE.search(body):
        raise ValueError(
            '"Payment Date" is defined but states no business-day convention; '
            "a convention decides which day a period ends on, so it is read or "
            "the schedule is refused"
        )
    convention = "modified_following" if _MODIFIED_RE.search(body) else "following"

    return PaymentDateSchedule(
        day_of_month=next(iter(days)),
        months=tuple(sorted(months)),
        commencing=date(
            int(commencing.group("year")),
            commencing_month,
            int(commencing.group("day")),
        ),
        convention=convention,
        business_centres=tuple(parse_business_day_centres(text)),
        source='Listing Particulars, Definitions — "Payment Date"',
    )


def parse_business_day_centres(text: str) -> list[str]:
    """Parse the settlement centres the ``"Business Day"`` definition requires.

    Returns them in the order they appear, T2 first when the definition's Euro
    settlement limb is present. An empty list means the definition was **not
    found** at all — the caller decides whether that is fatal.

    A definition that *is* found but yields no recognisable centre raises instead.
    That asymmetry is the point: dropping centres does not fail, it shifts dates,
    and the shift is small enough to look like a real answer. Missing New York
    alone moves Cairn's January 2025 Payment Date from the 21st to the 20th and
    its accrual period from 95 days to 94.

    Raises:
        ValueError: If the definition is present but no centre could be read.
    """
    block = _BUSINESS_DAY_BLOCK_RE.search(text)
    if block is None:
        return []
    body = _collapse(block.group("body"))

    centres: list[str] = []
    if _T2_RE.search(body):
        centres.append("T2")

    for named in _CENTRES_RE.finditer(body):
        raw = named.group("centres").replace(" and ", ", ")
        for part in raw.split(","):
            centre = " ".join(part.split())
            if not centre or centre.lower() in _NOT_A_CENTRE:
                continue
            if not centre[0].isupper():
                continue
            if centre not in centres:
                centres.append(centre)

    if not centres:
        raise ValueError(
            '"Business Day" is defined but names no settlement centres this '
            "parser recognises; refusing to resolve payment dates against an "
            "empty calendar"
        )
    return centres


# ---------------------------------------------------------------------------
# Resolving scheduled dates to actual Payment Dates
# ---------------------------------------------------------------------------


def is_business_day(day: date, centres: tuple[str, ...] | list[str]) -> bool:
    """Is ``day`` a Business Day in **every** named centre?

    The definition is conjunctive — a day on which T2 is open *and* banks settle
    in London, Dublin and New York — so one centre's holiday is enough to
    disqualify it.

    Raises:
        UnresolvableBusinessDay: If ``day``'s year is outside :data:`COVERED_YEARS`
            or a centre has no committed holiday list.
    """
    if day.year not in COVERED_YEARS:
        raise UnresolvableBusinessDay(
            f"{day.isoformat()} is outside the committed holiday calendar "
            f"(covered years: {sorted(COVERED_YEARS)}) — refusing to assume it is "
            "a business day"
        )
    if day.weekday() >= 5:
        return False
    for centre in centres:
        holidays = BUSINESS_CENTRE_HOLIDAYS.get(centre)
        if holidays is None:
            raise UnresolvableBusinessDay(
                f"no committed holiday list for business centre {centre!r}"
            )
        if day in holidays:
            return False
    return True


def adjust_to_business_day(
    day: date, centres: tuple[str, ...] | list[str], convention: str
) -> date:
    """Apply the prospectus's business-day convention to a scheduled date.

    ``following`` rolls forward to the next Business Day. ``modified_following``
    does the same but rolls *backward* instead when rolling forward would cross
    into the next month — Cairn's "unless it would thereby fall in the following
    month, in which case it shall be brought forward" proviso.

    Raises:
        UnresolvableBusinessDay: If no Business Day is found within a month, or a
            date falls outside the committed calendar.
    """
    if convention not in {"following", "modified_following"}:
        raise ValueError(f"unsupported business-day convention {convention!r}")

    forward = day
    for _ in range(31):
        if is_business_day(forward, centres):
            break
        forward += timedelta(days=1)
    else:  # pragma: no cover - a month of consecutive holidays is not reachable
        raise UnresolvableBusinessDay(
            f"no business day found within a month of {day.isoformat()}"
        )

    if convention == "following" or forward.month == day.month:
        return forward

    backward = day
    for _ in range(31):
        if is_business_day(backward, centres):
            return backward
        backward -= timedelta(days=1)
    raise UnresolvableBusinessDay(  # pragma: no cover - unreachable in practice
        f"no business day found within a month before {day.isoformat()}"
    )


def scheduled_dates_in_year(schedule: PaymentDateSchedule, year: int) -> list[date]:
    """The schedule's unadjusted dates in ``year``, ascending.

    Dates before :attr:`PaymentDateSchedule.commencing` are excluded — they are
    not Payment Dates, so an accrual period must never start on one.
    """
    dates: list[date] = []
    for month in schedule.months:
        try:
            candidate = date(year, month, schedule.day_of_month)
        except ValueError as exc:  # e.g. a schedule stated on the 31st, in February
            raise UnresolvableBusinessDay(
                f"the stated schedule's day-of-month ({schedule.day_of_month}) "
                f"does not exist in month {month} of {year}; the document states "
                "no convention for that case, so it must not be assumed"
            ) from exc
        if candidate >= schedule.commencing:
            dates.append(candidate)
    return dates


def payment_date_for(schedule: PaymentDateSchedule, scheduled: date) -> date:
    """Resolve one unadjusted Scheduled Payment Date to its actual Payment Date."""
    return adjust_to_business_day(
        scheduled, schedule.business_centres, schedule.convention
    )


def previous_payment_date(schedule: PaymentDateSchedule, payment: date) -> date:
    """The actual Payment Date immediately preceding ``payment``.

    ``payment`` is an *actual* (adjusted) Payment Date — the one a report states
    as its ``Next Payment Date``. The predecessor is found by walking the
    schedule's unadjusted dates backwards from it and adjusting each, so a
    predecessor that was itself moved by a holiday is returned as it actually
    fell.

    Raises:
        UnresolvableBusinessDay: If the predecessor precedes the schedule's
            commencement — the first Accrual Period runs from the Issue Date, which
            no schedule states, so it must not be guessed.
    """
    candidates = [
        resolved
        for year in (payment.year - 1, payment.year)
        for scheduled in scheduled_dates_in_year(schedule, year)
        if (resolved := payment_date_for(schedule, scheduled)) < payment
    ]
    if not candidates:
        raise UnresolvableBusinessDay(
            f"no Payment Date precedes {payment.isoformat()} in the stated "
            f"schedule commencing {schedule.commencing.isoformat()} — the first "
            "Accrual Period runs from the Issue Date, which the schedule does not "
            "state"
        )
    return max(candidates)


def payment_date_on_or_after(schedule: PaymentDateSchedule, day: date) -> date:
    """The first actual Payment Date falling on or after ``day``.

    A report is drawn up on a Calculation Date and pays on the Payment Date that
    follows it, so this maps a reporting date onto the Payment Date its period
    ends on. Comparison is against *adjusted* dates, so a reporting date landing
    between a scheduled date and the business day it rolled to still resolves to
    that same Payment Date.

    Only **Scheduled** Payment Dates are considered. The definition also admits
    Unscheduled Payment Dates — a Redemption Date, the Final Distribution Date —
    which no schedule can predict; a period ending on one is shorter than this
    returns. Callers that hold the report's own stated payment date should prefer
    it, and :func:`accrual_period_days` takes the payment date as an argument so
    they can.

    Raises:
        UnresolvableBusinessDay: If no Payment Date falls within the covered
            calendar on or after ``day``.
    """
    for year in (day.year, day.year + 1):
        for scheduled in scheduled_dates_in_year(schedule, year):
            resolved = payment_date_for(schedule, scheduled)
            if resolved >= day:
                return resolved
    raise UnresolvableBusinessDay(
        f"no Payment Date falls on or after {day.isoformat()} within the "
        f"committed calendar (covered years: {sorted(COVERED_YEARS)})"
    )


def accrual_period_days(schedule: PaymentDateSchedule, payment: date) -> int:
    """Days in the Accrual Period ending on ``payment``.

    The deal's *Accrual Period* is "each successive period from and including each
    Payment Date to, but excluding, the following Payment Date", so the count is
    the plain difference between two actual Payment Dates — both of them stated
    dates, neither derived from any published amount.

    Args:
        schedule: The stated Payment Date schedule.
        payment:  The actual Payment Date the period ends on.

    Returns:
        The Act/360 day count for that period.
    """
    return (payment - previous_payment_date(schedule, payment)).days
