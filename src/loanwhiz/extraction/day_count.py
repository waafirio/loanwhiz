"""Counting days on a stated day-count basis — the one primitive, at the bottom (#607).

Why this module exists
----------------------
This repo has had three implementations of "days in this period" and no check
that they agreed. #601 removed the first: ``loanwhiz.api.main._days_between``
took two ISO strings, hardcoded Act/360, and returned a plausible ``30`` when it
could not answer, and it became ``_tape_period_days(basis, …)`` — a boundary that
parses and delegates rather than counting anything itself.

The second was ``payment_schedule_parser.accrual_period_days``, and it could not
be converged the same way. :mod:`loanwhiz.extraction.day_count_parser` — where
``accrual_days`` lived — **imports** :mod:`loanwhiz.extraction.payment_schedule_parser`
for ``PaymentDateSchedule`` and ``previous_payment_date``, so having
``accrual_period_days`` call ``accrual_days`` closes an import cycle. A local
import inside the function would have hidden the cycle rather than removed it,
and this programme's one deliberate cycle (``domain`` ↔ ``primitives``, #562/#563/#571)
is documented as a cycle *named and lived with*, not as licence to add another.

So the dependency is inverted by extracting **downward**. The primitive does not
depend on the Conditions text it is read from, nor on the calendar it is measured
over; only the two parsers above it do. Moving it here leaves:

.. code-block:: text

    day_count.py                 (stdlib only — no loanwhiz imports)
      ^                     ^
      |                     |
    payment_schedule_parser  day_count_parser  ---> payment_schedule_parser

— an acyclic graph in which both paths reach one implementation.
:mod:`loanwhiz.extraction.day_count_parser` re-exports every name below, so
``day_count_parser.accrual_days`` remains the same function object and importers
that already named it there did not have to change.

What is here and what is not
----------------------------
Here: counting days between two dates on a basis. Not here: deciding *which*
basis applies — that is read from the deal's own Conditions by
:func:`loanwhiz.extraction.day_count_parser.parse_interest_day_counts`, and
:func:`~loanwhiz.extraction.day_count_parser.class_accrual_days` decides which
*pair of dates* a class measures between. The split is the section divider that
was already inside ``day_count_parser``; this only makes it a module boundary.

The refusal, unchanged
----------------------
:class:`UnsourcedDayCount` moved with the arithmetic because it is part of the
arithmetic's contract: "12 months of 30 days each" names a *family* — 30/360 US,
30E/360 and 30E/360 ISDA — whose members differ only when an endpoint falls on
the 31st or the last day of February. At such a date the document genuinely has
not said which member is meant, so :func:`thirty_360_days` refuses instead of
picking one. #493's rule (``rate_pct`` is ``float | None``, never a defaulted
0.0) and #549's (a refusal that keeps its value is not a refusal) both apply: a
caller cannot tell a guessed day count from a sourced one, so there is no honest
number to return.
"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Literal

__all__ = [
    "DayCountBasis",
    "UnsourcedDayCount",
    "accrual_days",
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

    The one day-count implementation in this repository. Every entrypoint that
    answers "days in this period" — the API path's ``_tape_period_days``, the
    report path's ``accrual_period_days``, and the per-class ``class_accrual_days``
    — parses its own inputs and then calls this; none of them counts days itself.

    Args:
        basis: The stated day-count basis.
        start: First day of the Accrual Period (included).
        end:   Payment Date the period runs to (excluded).

    Returns:
        The day count that basis produces.

    Raises:
        UnsourcedDayCount: From :func:`thirty_360_days`, where a 30/360 endpoint
            falls on a date the variants disagree about.
    """
    if basis == "act/360":
        return (end - start).days
    return thirty_360_days(start, end)
