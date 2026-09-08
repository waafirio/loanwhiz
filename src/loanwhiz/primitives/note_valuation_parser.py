"""Note Valuation Report parser — the *liability* ground truth for a CLO.

This module parses U.S. Bank's **Note Valuation Report** — the quarterly CLO
report that publishes, on facing sections, an **Interest Priority of Payments**
and a **Principal Priority of Payments** — into the *existing*
:class:`~loanwhiz.primitives.notes_cash_parser.NotesCashPeriod` shape.

Why it emits somebody else's model (epic #491)
----------------------------------------------
``notes_cash_parser`` already produces the liability ground truth for the
seasoned RMBS deals, and ``reconciler.reconcile_series`` already consumes it.
The CLO report is a **different document carrying the same facts**, so this is a
sibling reader, not a second format: the Interest PoP lands in ``revenue_pop``,
the Principal PoP in ``redemption_pop``, and the Distribution Summary in
``note_balances``. Everything downstream joins to a CLO exactly as it joins to
Green Lion 2024-1.

The seam it sits on
-------------------
Extraction and page/section splitting are ``collateral_schedule_parser``'s
(#469/#480): :func:`~loanwhiz.primitives.collateral_schedule_parser.extract_report_lines`
rebuilds visual lines from ``pypdf``'s text-run matrix, and sections are located
by the **page's own title text**, never by page number. Nothing here re-imports
``pypdf``; the live seam is that module's, already covered by
``tests/test_live_seam_dependencies.py``.

The acceptance oracle — why this parse can be trusted
-----------------------------------------------------
Each waterfall row prints two figures: the **amount** paid at that step and the
**available-for-disbursements balance after it**. That running balance is a
chain, and a chain notices what a regex cannot: a dropped row, a misread amount,
a column that is a price rather than an amount. :func:`reconcile_note_valuation`
walks it, ties the step sum to the report's own stated available funds, ties the
per-class figures to the Distribution Summary's own totals row, and cross-checks
each class's applied rate against the Executive Summary's separate printing of
it. A divergence **refuses** (#469) rather than returning a period a consumer
would trust.

This is not theoretical. The first draft of this parser filtered page furniture
by prefix before looking for a row's money tail, and the prefix
``U.S. Bank Global Corporate Trust`` — the page footer — also opens the payee
row ``U.S. Bank Global Corporate Trust Limited 15,818.69 …``. One step lost
EUR 15,818.69 and every downstream figure still looked plausible. The chain
caught it on the first run; the section total alone would have caught it too,
but only because that particular waterfall had a non-zero total.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from pydantic import BaseModel, Field

from loanwhiz.primitives.base import (
    AuditEntry,
    BaseInput,
    Citation,
    PrimitiveResult,
)
from loanwhiz.primitives.collateral_schedule_parser import (
    MONEY,
    fetch_report_text,
)
from loanwhiz.primitives.notes_cash_parser import (
    NoteClassBalance,
    NotesCashPeriod,
    NotesCashReport,
    PoPStep,
)

logger = logging.getLogger(__name__)

_PRIMITIVE_NAME = "note_valuation_parser"
_PRIMITIVE_VERSION = "0.1.0"
_DETERMINISTIC_CONFIDENCE = 1.0


# ===========================================================================
# Section vocabulary — the report's own page titles
# ===========================================================================

#: The report prints its section title on every page of the section, so a
#: section that spans four pages is found four times and never by page number.
SECTION_EXECUTIVE = "Executive Summary"
SECTION_DISTRIBUTION = "Distribution Summary"
SECTION_INTEREST_POP = "Interest Priority of Payments"
SECTION_PRINCIPAL_POP = "Principal Priority of Payments"

#: Every section this parser reads. **A title missing from this tuple silently
#: drops its rows** — the #480 lesson — which is why
#: :func:`reconcile_note_valuation` asserts each PoP section parsed at least one
#: step rather than trusting a clean-looking empty result.
_SECTION_TITLES: tuple[str, ...] = (
    SECTION_EXECUTIVE,
    SECTION_DISTRIBUTION,
    SECTION_INTEREST_POP,
    SECTION_PRINCIPAL_POP,
)

#: The two waterfalls, paired with the ``NotesCashPeriod`` field each fills.
#: ``revenue``/``redemption`` are the RMBS names for the same two roles.
_WATERFALLS: tuple[tuple[str, str], ...] = (
    (SECTION_INTEREST_POP, "revenue"),
    (SECTION_PRINCIPAL_POP, "redemption"),
)


# ---------------------------------------------------------------------------
# Row-shape regexes
# ---------------------------------------------------------------------------

#: A waterfall data row ends in exactly two money cells: the amount paid at the
#: step and the available balance remaining after it. Anchored at end-of-line,
#: so a description containing a figure with any other shape (``50.0 per
#: cent.``, ``20 per cent.``) cannot be mistaken for the tail.
_ROW_TAIL_RE = re.compile(rf"^(?P<description>.*?)\s+(?P<amount>{MONEY})\s+(?P<balance>{MONEY})\s*$")

#: A priority label as the report prints it: one or more parenthesised groups.
#: The Interest waterfall runs ``(A)``…``(CC)`` with sub-steps ``(a)``/``(i)``;
#: the Principal waterfall prints compound labels (``(A)(A)(i)``, ``(B)(J)``,
#: ``(S)(2)(III)``) because its steps are cross-referenced to the Interest
#: waterfall's. Both are just "one or more parenthesised short tokens".
_LABEL_RE = re.compile(r"^(?:\((?:[A-Za-z]{1,4}|\d{1,2})\))+")

#: The line that states each waterfall's own available funds, e.g.
#: ``Interest Priority Of Interest Proceeds (Waterfall) 7,255,062.35``.
_WATERFALL_TOTAL_RE = re.compile(
    rf"^(?:Interest|Principal) Priority Of (?:Interest|Principal) Proceeds "
    rf"\(Waterfall\)\s+(?P<total>{MONEY})\s*$"
)

#: A Distribution Summary row: the class name, six money columns (original face,
#: opening balance, principal payment, closing balance, accrued interest due,
#: total interest payable) and the five-decimal applied rate.
_DISTRIBUTION_ROW_RE = re.compile(
    rf"^(?P<name>Class\s+\S.*?)\s+(?P<original_face>{MONEY})\s+(?P<opening>{MONEY})\s+"
    rf"(?P<principal_payment>{MONEY})\s+(?P<closing>{MONEY})\s+(?P<accrued>{MONEY})\s+"
    rf"(?P<payable>{MONEY})\s+(?P<rate>\d+\.\d{{5}})\s*$"
)

#: The Distribution Summary's unlabelled totals row — the same six money columns
#: with no class name and no rate. This is the oracle the per-class rows tie to.
_DISTRIBUTION_TOTALS_RE = re.compile(
    rf"^(?P<original_face>{MONEY})\s+(?P<opening>{MONEY})\s+(?P<principal_payment>{MONEY})\s+"
    rf"(?P<closing>{MONEY})\s+(?P<accrued>{MONEY})\s+(?P<payable>{MONEY})\s*$"
)

#: An Executive Summary note row, which prints the same applied coupon a second
#: time — and prints ``N/A`` where a class has no coupon at all. That ``N/A`` is
#: the document's own word for "unresolved", and the reason this parser records
#: ``None`` rather than the Distribution Summary's ``0.00000`` for such a class:
#: a consumer that read the zero as a rate would accrue no interest and call it
#: an answer (#493).
_EXECUTIVE_ROW_RE = re.compile(
    rf"^Class\s+(?P<designation>[A-Za-z0-9-]+)\s+Notes\s+(?P<balance>{MONEY})\s+"
    rf"(?P<coupon>\d+\.\d{{5}}|N/A)\s+(?P<interest>{MONEY}|N/A)\s+\S+\s+\S+\s*$"
)

#: ``Class A Senior Secured FLR Notes`` → ``A``; ``Class B-1  Senior Secured
#: FLR`` → ``B-1``; ``Class Subordinated Notes`` → ``Subordinated``. The
#: designation is whatever follows ``Class``, before the structure words.
_CLASS_DESIGNATION_RE = re.compile(r"^Class\s+(?P<designation>[A-Za-z0-9-]+)")

#: The Interest waterfall's standard wording for a note's own interest step.
#: Matching it is what lets :func:`reconcile_note_valuation` check a PoP amount
#: against the Distribution Summary's independently stated interest payable —
#: the check that catches a column that is a price rather than an amount (#470).
_INTEREST_STEP_RE = re.compile(
    r"Interest Amounts due and payable on the Class\s+(?P<designation>[A-Za-z0-9-]+)\s+Notes"
)

#: ``As of:  08/01/2025`` — day/month/year, as the trustee prints it. The
#: ``Next Payment: 21/01/2025`` on the same header pins the ordering: 21 is not
#: a month.
_AS_OF_RE = re.compile(r"^As of\s*:?\s+(?P<day>\d{2})/(?P<month>\d{2})/(?P<year>\d{4})\s*$")

_PAGE_MARKER_RE = re.compile(r"^--- page (\d+) ---$")

#: Page furniture: banners, footers and the column headers the report repeats on
#: every page of a section. **Checked only after a line has failed to match a
#: data row** — see :func:`_is_furniture`.
_FURNITURE_PREFIXES: tuple[str, ...] = (
    "www.usbank.com",
    "U.S. Bank Global Corporate Trust",
    "Page ",
    "As of",
    "Next Payment",
    "Available",
    "Document Report Payment for",
    "Reference Reference Amount Disbursements",
    "Payments (EUR)",
    "Original Face Opening Principal of Original Interest Amount Rate",
    "Issue Name Value Balance Payment Balance Due Payable Current",
    "Closing Balance Accrued Total Interest",
)


# ===========================================================================
# Models
# ===========================================================================


class StatedTotals(BaseModel):
    """The figures the report states **about itself** — the oracle's inputs.

    Deliberately separate from the parsed period: these are the document's own
    published aggregates, and reconciling one against the other is the whole
    point. Mixing them into :class:`NotesCashPeriod` would let a parse validate
    itself.
    """

    interest_available: float | None = Field(
        default=None, description="Interest waterfall's stated available funds (EUR)."
    )
    principal_available: float | None = Field(
        default=None, description="Principal waterfall's stated available funds (EUR)."
    )
    distribution_totals: dict[str, float] = Field(
        default_factory=dict,
        description="The Distribution Summary's own totals row, by column name.",
    )
    executive_coupons: dict[str, float | None] = Field(
        default_factory=dict,
        description="Executive Summary coupon per class key; None where it prints N/A.",
    )


class ReconciliationCheck(BaseModel):
    """One comparison of the parsed period against a report-stated figure."""

    name: str
    expected: str
    actual: str
    ok: bool


class NoteValuationReconciliation(BaseModel):
    """The result of tying a parsed period back to the report's own figures."""

    checks: list[ReconciliationCheck] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True only when every check passed."""
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> list[ReconciliationCheck]:
        """The checks that did not tie out."""
        return [c for c in self.checks if not c.ok]


class NoteValuationReconciliationError(ValueError):
    """Raised when a parsed period does not tie out to the report's own figures.

    The enforced boundary, mirroring
    :class:`~loanwhiz.primitives.collateral_schedule_parser.ScheduleReconciliationError`:
    the parser refuses rather than returning liability actuals that a validation
    harness would treat as ground truth.
    """

    def __init__(self, reconciliation: NoteValuationReconciliation):
        self.reconciliation = reconciliation
        detail = " | ".join(
            f"{c.name}: expected {c.expected}, got {c.actual}" for c in reconciliation.failures
        )
        super().__init__(
            "Note Valuation Report does not reconcile to its own stated figures "
            "— refusing to return it. " + detail
        )


class NoteValuationParseInput(BaseInput):
    """Governance input record for the envelope wrapper (#277)."""

    period_label: str
    text: str


# ===========================================================================
# Page/section splitting — the seam collateral_schedule_parser established
# ===========================================================================


def _split_pages(text: str) -> list[list[str]]:
    """Split extracted report text into per-page line lists."""
    pages: list[list[str]] = []
    current: list[str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if _PAGE_MARKER_RE.match(line):
            current = []
            pages.append(current)
            continue
        if current is None:
            continue
        if line:
            current.append(line)
    return pages


def _page_section(lines: list[str]) -> str | None:
    """The section title a page belongs to, located by its own header text.

    The table of contents names every section on one page with dot leaders;
    excluding leadered lines keeps it from being classified as whichever section
    it happens to list first.
    """
    head = " ".join(line for line in lines[:12] if ". . ." not in line)
    for title in _SECTION_TITLES:
        if title in head:
            return title
    return None


def _pages_for(pages: list[list[str]], section: str) -> list[list[str]]:
    """Every page belonging to one section, in document order."""
    return [lines for lines in pages if _page_section(lines) == section]


def _is_furniture(line: str) -> bool:
    """True for repeated page furniture — **asked only of non-data lines**.

    Order matters and is the reason this function is not called first: the page
    footer starts ``U.S. Bank Global Corporate Trust``, and so does the payee
    row ``U.S. Bank Global Corporate Trust Limited 15,818.69 7,222,548.16``.
    A furniture-first filter eats the payee and under-reports its step.
    """
    return line.startswith(_FURNITURE_PREFIXES) or line in _SECTION_TITLES


# ===========================================================================
# Numeric coercion
# ===========================================================================


def _money(token: str) -> float:
    """Parse one printed money token (comma-grouped, two decimals) to float."""
    return float(token.replace(",", ""))


def _iso_date(pages: list[list[str]]) -> str | None:
    """The report's ``As of:`` date as ISO, read from the section page headers.

    The cover page prints ``As of 08 January, 2025``; every section page prints
    ``As of:  08/01/2025``. Only the numeric form is read, and it is day/month
    /year — the ``Next Payment: 21/01/2025`` beside it settles the ordering.
    """
    for lines in pages:
        for line in lines:
            match = _AS_OF_RE.match(line)
            if match:
                return f"{match['year']}-{match['month']}-{match['day']}"
    return None


def _class_key(name: str) -> str | None:
    """Canonical class key from a printed class name, or ``None``.

    >>> _class_key("Class A Senior Secured FLR Notes")
    'class_a'
    >>> _class_key("Class B-1  Senior Secured  FLR")
    'class_b_1'
    >>> _class_key("Class Subordinated Notes")
    'class_subordinated'

    The designation is kept as printed rather than folded into the RMBS
    ``class_a``/``class_b``/``class_c`` trio: a CLO's B-1 and B-2 are different
    notes with different coupons, and collapsing them would lose the
    distinction the report exists to publish.
    """
    match = _CLASS_DESIGNATION_RE.match(name.strip())
    if not match:
        return None
    return "class_" + match["designation"].lower().replace("-", "_")


# ===========================================================================
# Waterfall parsing
# ===========================================================================


def _parse_waterfall(pages: list[list[str]], section: str) -> tuple[list[PoPStep], float | None]:
    """Parse one Priority of Payments section into ordered steps.

    Four line shapes, resolved in this order — data row first, always:

    1. the section's **available funds** line, which states the waterfall's own
       total before any step;
    2. a **data row** — a description ending in ``<amount> <balance>``. Its
       priority is its own printed label, or the enclosing header's when it
       prints none (the payee rows under an Administrative Expenses step);
    3. a **header row** — a labelled line with no money tail, which opens a
       step whose amounts are broken out over the payee rows beneath it;
    4. anything else is a **continuation**: the wrapped remainder of the
       previous row's description, appended to it.

    Returns the steps in document order and the stated available funds.
    """
    steps: list[PoPStep] = []
    available: float | None = None
    parent: str | None = None
    #: Where a wrapped continuation line belongs — the step it extends, or
    #: ``None`` when the previous line was a header (whose description is
    #: context, not an emitted field).
    continues: PoPStep | None = None

    for lines in _pages_for(pages, section):
        for line in lines:
            total = _WATERFALL_TOTAL_RE.match(line)
            if total:
                available = _money(total["total"])
                continues = None
                continue

            row = _ROW_TAIL_RE.match(line)
            if row:
                description = row["description"].strip()
                label = _LABEL_RE.match(description)
                if label:
                    # The label goes in ``priority``; keeping it on the front of
                    # ``recipient`` too would make the two shapes of row (labelled
                    # step, unlabelled payee) read differently for no reason.
                    priority, description = label.group(0), description[label.end() :].strip()
                else:
                    priority = parent
                if priority is None:
                    # A data row before any label at all is a shape this report
                    # does not produce; refusing to guess keeps the failure
                    # visible rather than filing it under the wrong step.
                    logger.warning("%s: data row with no priority label: %r", section, line)
                    continue
                step = PoPStep(
                    priority=priority,
                    recipient=description,
                    amount=_money(row["amount"]),
                    balance_after=_money(row["balance"]),
                )
                steps.append(step)
                continues = step
                continue

            if _is_furniture(line):
                continues = None
                continue

            if _LABEL_RE.match(line):
                parent = _LABEL_RE.match(line).group(0)  # type: ignore[union-attr]
                continues = None
                continue

            if continues is not None:
                continues.recipient = f"{continues.recipient} {line}"

    return steps, available


# ===========================================================================
# Distribution / Executive Summary parsing
# ===========================================================================


def _parse_executive_coupons(pages: list[list[str]]) -> dict[str, float | None]:
    """Per-class applied coupon as the Executive Summary prints it.

    ``None`` where the report prints ``N/A`` — a class with no coupon at all.
    This is a second, independent printing of the Distribution Summary's rate,
    which is what makes it worth reading: the two must agree.
    """
    coupons: dict[str, float | None] = {}
    for lines in _pages_for(pages, SECTION_EXECUTIVE):
        for line in lines:
            match = _EXECUTIVE_ROW_RE.match(line)
            if not match:
                continue
            key = "class_" + match["designation"].lower().replace("-", "_")
            coupon = match["coupon"]
            coupons[key] = None if coupon == "N/A" else float(coupon)
    return coupons


def _parse_distribution(
    pages: list[list[str]], coupons: dict[str, float | None]
) -> tuple[list[NoteClassBalance], dict[str, float]]:
    """Per-class balances from the Distribution Summary, plus its totals row.

    ``interest_rate_applied`` takes the Distribution Summary's ``Rate Current``
    — but only where the Executive Summary agrees the class *has* a coupon.
    Where that section prints ``N/A``, the rate is recorded as ``None``: the
    Distribution Summary writes ``0.00000`` in the same cell, and a consumer
    reading that as a resolved rate would accrue zero interest and call it an
    answer rather than refusing (#493).
    """
    balances: list[NoteClassBalance] = []
    totals: dict[str, float] = {}

    for lines in _pages_for(pages, SECTION_DISTRIBUTION):
        for line in lines:
            row = _DISTRIBUTION_ROW_RE.match(line)
            if row:
                key = _class_key(row["name"])
                if key is None:  # pragma: no cover - the regex anchors on "Class"
                    continue
                original_face = _money(row["original_face"])
                closing = _money(row["closing"])
                rate = float(row["rate"])
                balances.append(
                    NoteClassBalance(
                        note_class=key,
                        principal_balance_after_payment=closing,
                        total_principal_payments=_money(row["principal_payment"]),
                        factor_after_payment=(closing / original_face if original_face else None),
                        total_interest_payments=_money(row["payable"]),
                        interest_rate_applied=(None if coupons.get(key, rate) is None else rate),
                    )
                )
                continue

            if balances and not totals:
                stated = _DISTRIBUTION_TOTALS_RE.match(line)
                if stated:
                    totals = {name: _money(value) for name, value in stated.groupdict().items()}

    return balances, totals


# ===========================================================================
# The contract — reconcile the period to the report's own stated figures
# ===========================================================================


def _check(name: str, expected: Any, actual: Any) -> ReconciliationCheck:
    return ReconciliationCheck(
        name=name, expected=str(expected), actual=str(actual), ok=expected == actual
    )


def _cents(value: float | None) -> int | None:
    """A EUR figure as whole cents, so comparisons are exact.

    Every figure in this report is printed to two decimals, so cents are its
    native precision; comparing floats directly would fail on representation
    alone.
    """
    return None if value is None else round(value * 100)


def _reconcile_waterfall(
    checks: list[ReconciliationCheck], role: str, steps: list[PoPStep], available: float | None
) -> None:
    """Tie one waterfall to its own stated total and its printed balance chain.

    Three checks, and the first is the one that matters most: a section whose
    title moved parses to **zero** steps, and a zero-step waterfall satisfies
    both a sum check and a chain check vacuously. "Nothing to find" and "I
    cannot see" must not be the same output (#480), so presence is asserted
    first and explicitly.
    """
    checks.append(_check(f"{role}_pop_present", True, bool(steps)))
    checks.append(_check(f"{role}_pop_available_funds_stated", True, available is not None))
    if not steps or available is None:
        return

    checks.append(
        _check(
            f"{role}_pop_step_total",
            _cents(available),
            sum(_cents(step.amount) or 0 for step in steps),
        )
    )

    # The chain: each row's printed balance is the previous one less this step's
    # amount. A dropped row breaks it at the row after the gap.
    running = _cents(available) or 0
    breaks: list[str] = []
    for step in steps:
        running -= _cents(step.amount) or 0
        printed = _cents(step.balance_after)
        if printed is None or printed != running:
            breaks.append(f"{step.priority} {step.recipient[:40]!r}")
            running = printed if printed is not None else running
    checks.append(_check(f"{role}_pop_balance_chain", [], breaks))


def reconcile_note_valuation(
    period: NotesCashPeriod, stated: StatedTotals
) -> NoteValuationReconciliation:
    """Tie a parsed period back to the figures the report states about itself.

    This is the acceptance oracle, and the reason the parse can be trusted by a
    validation harness. It checks:

    - each waterfall parsed at least one step and stated its available funds;
    - each waterfall's step amounts sum to those stated available funds;
    - each waterfall's printed running balance chains without a break;
    - the per-class Distribution Summary figures sum to its own totals row;
    - each class's applied rate agrees with the Executive Summary's separate
      printing of the same number;
    - each note's Interest PoP step equals the interest the Distribution Summary
      independently says is payable to it — the check that catches a column
      which is a price rather than an amount (#470).
    """
    checks: list[ReconciliationCheck] = []

    _reconcile_waterfall(checks, "interest", period.revenue_pop, stated.interest_available)
    _reconcile_waterfall(checks, "principal", period.redemption_pop, stated.principal_available)

    checks.append(_check("distribution_summary_present", True, bool(period.note_balances)))
    checks.append(_check("distribution_totals_stated", True, bool(stated.distribution_totals)))

    if period.note_balances and stated.distribution_totals:
        for column, attribute in (
            ("closing", "principal_balance_after_payment"),
            ("principal_payment", "total_principal_payments"),
            ("payable", "total_interest_payments"),
        ):
            checks.append(
                _check(
                    f"distribution_{column}_total",
                    _cents(stated.distribution_totals[column]),
                    sum(_cents(getattr(b, attribute)) or 0 for b in period.note_balances),
                )
            )

    for balance in period.note_balances:
        if balance.note_class in stated.executive_coupons:
            checks.append(
                _check(
                    f"applied_rate_agrees_{balance.note_class}",
                    stated.executive_coupons[balance.note_class],
                    balance.interest_rate_applied,
                )
            )

    for step in period.revenue_pop:
        match = _INTEREST_STEP_RE.search(step.recipient)
        if not match:
            continue
        key = "class_" + match["designation"].lower().replace("-", "_")
        balance = next((b for b in period.note_balances if b.note_class == key), None)
        if balance is None:
            continue
        checks.append(
            _check(
                f"interest_step_matches_payable_{key}",
                _cents(balance.total_interest_payments),
                _cents(step.amount),
            )
        )

    return NoteValuationReconciliation(checks=checks)


# ===========================================================================
# Pure parse path (the unit-tested seam)
# ===========================================================================


def parse_stated_totals(text: str) -> StatedTotals:
    """The figures the report states about itself, without parsing the detail.

    Exposed so a caller can reconcile a period it parsed with ``strict=False``,
    which is the only way to *inspect* a failing parse rather than be refused.
    """
    pages = _split_pages(text)
    coupons = _parse_executive_coupons(pages)
    _, totals = _parse_distribution(pages, coupons)
    _, interest_available = _parse_waterfall(pages, SECTION_INTEREST_POP)
    _, principal_available = _parse_waterfall(pages, SECTION_PRINCIPAL_POP)
    return StatedTotals(
        interest_available=interest_available,
        principal_available=principal_available,
        distribution_totals=totals,
        executive_coupons=coupons,
    )


def parse_note_valuation_text(
    text: str,
    *,
    period_label: str,
    reporting_date: str | None = None,
    strict: bool = True,
) -> NotesCashPeriod:
    """Parse extracted Note Valuation Report text into a :class:`NotesCashPeriod`.

    Pure and deterministic — no network, no LLM. Sections are located by their
    own page titles, never by page number.

    With ``strict`` (the default) the period is reconciled against the report's
    own stated figures and a divergence raises
    :class:`NoteValuationReconciliationError`. That refusal is the contract.
    Pass ``strict=False`` only to inspect a failing parse; pair it with
    :func:`parse_stated_totals` and :func:`reconcile_note_valuation` to see
    which checks failed.

    Parameters
    ----------
    text:
        Page-delimited extracted report text, as
        :func:`~loanwhiz.primitives.collateral_schedule_parser.extract_report_lines`
        produces it.
    period_label:
        Human-readable period label, e.g. ``"January 2025"``.
    reporting_date:
        ISO reporting date override. Falls back to the header's ``As of:`` date.

    Raises
    ------
    ValueError
        If no reporting date can be determined (neither argument nor header).
    NoteValuationReconciliationError
        Under ``strict``, if the parse does not tie out to the report's own
        stated figures.
    """
    pages = _split_pages(text)
    if not pages:
        raise ValueError("no pages found — text is not extracted Note Valuation Report output")

    iso = reporting_date or _iso_date(pages)
    if not iso:
        raise ValueError(
            f"Note Valuation Report for {period_label!r} has no reporting date "
            "(none in header and none passed) — cannot key the period by date"
        )

    coupons = _parse_executive_coupons(pages)
    note_balances, totals = _parse_distribution(pages, coupons)
    revenue_pop, interest_available = _parse_waterfall(pages, SECTION_INTEREST_POP)
    redemption_pop, principal_available = _parse_waterfall(pages, SECTION_PRINCIPAL_POP)

    period = NotesCashPeriod(
        reporting_date=iso,
        period_label=period_label,
        deal_name=pages[0][0] if pages[0] else None,
        reporting_period=None,
        note_balances=note_balances,
        revenue_pop=revenue_pop,
        redemption_pop=redemption_pop,
        available_revenue_funds=interest_available,
        available_principal_funds=principal_available,
    )

    if strict:
        stated = StatedTotals(
            interest_available=interest_available,
            principal_available=principal_available,
            distribution_totals=totals,
            executive_coupons=coupons,
        )
        reconciliation = reconcile_note_valuation(period, stated)
        if not reconciliation.ok:
            raise NoteValuationReconciliationError(reconciliation)

    return period


# ===========================================================================
# Live seam — fetch + parse (integration only)
# ===========================================================================


def parse_note_valuation_report(deal_context: dict[str, Any]) -> NotesCashReport:
    """Fetch and parse every registered Note Valuation Report for a deal.

    ``deal_context`` is a ``DEAL_REGISTRY`` entry carrying ``deal_name`` and
    ``notes_cash_report_urls`` (``[{"period": str, "url": str}, ...]``) — the
    same routing key ``notes_cash_parser`` reads, because the CLO report fills
    the same role. Registering that key for a CLO is #495's job, not this
    module's: the key is a promise that an answer key exists, and it is earned
    at registration.

    No durable cache here. ``notes_cash_parser`` caches into
    ``data/extraction_cache/`` because it re-reads a dozen RMBS periods on every
    demo run; this reader has one registered period and no demo path, so a cache
    would be a second source of truth for nothing.
    """
    deal_name = deal_context["deal_name"]
    periods = [
        parse_note_valuation_text(fetch_report_text(entry["url"]), period_label=entry["period"])
        for entry in deal_context["notes_cash_report_urls"]
    ]
    return NotesCashReport(deal_name=deal_name, periods=periods)


# ===========================================================================
# Governed (PrimitiveResult-returning) surface — the #277 envelope wrapper
# ===========================================================================


def parse_note_valuation_text_result(
    text: str,
    *,
    period_label: str,
) -> PrimitiveResult[NotesCashPeriod]:
    """Envelope-returning wrapper over :func:`parse_note_valuation_text`.

    The parse is deterministic (``pypdf``/regex, no LLM) *and* reconciled to the
    report's own stated figures before it is returned, so the envelope
    confidence is ``1.0`` — the framework's rule-based convention. One
    :class:`Citation` per section grounds the period in the pages it was read
    from.
    """
    started = time.perf_counter()
    parse_input = NoteValuationParseInput(period_label=period_label, text=text)
    period = parse_note_valuation_text(text, period_label=period_label)
    duration_ms = (time.perf_counter() - started) * 1000.0

    document = (
        f"{period.deal_name} — Note Valuation Report ({period_label})"
        if period.deal_name
        else f"Note Valuation Report ({period_label})"
    )
    citations = [
        Citation(
            document=document,
            page_or_row=section,
            excerpt=(
                "Liability actuals parsed deterministically from the extracted "
                "Note Valuation Report text and reconciled against the report's "
                "own stated available funds, running balances and totals."
            ),
        )
        for section in (SECTION_DISTRIBUTION, SECTION_INTEREST_POP, SECTION_PRINCIPAL_POP)
    ]
    audit = AuditEntry.now(
        primitive_name=_PRIMITIVE_NAME,
        version=_PRIMITIVE_VERSION,
        input_hash=parse_input.input_hash(),
        duration_ms=duration_ms,
    )
    return PrimitiveResult[NotesCashPeriod](
        output=period,
        confidence=_DETERMINISTIC_CONFIDENCE,
        citations=citations,
        audit_entry=audit,
    )
