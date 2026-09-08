"""Tests for the CLO Note Valuation Report parser.

The bar these pin is **reconciliation, not "it parses"**. Every figure asserted
below is one the source report states about itself somewhere else in the same
document — the waterfall's own available-funds line, the running balance printed
beside each row, the Distribution Summary's totals row, the Executive Summary's
second printing of each coupon. A parse that agrees with all of them is hard to
be wrong about; a parse that merely returns rows is not.

Everything here is offline and deterministic. The committed fixture is the
extracted output of the live ``pypdf`` seam
(``collateral_schedule_parser.write_fixture``), so no test touches the network.

On the falsifiers
-----------------
Two edits are worth naming because they are the ones this file exists to catch:

- **Filtering page furniture before looking for a row's money tail.** The page
  footer starts ``U.S. Bank Global Corporate Trust`` and so does the payee row
  ``U.S. Bank Global Corporate Trust Limited 15,818.69 …``.
  ``test_a_payee_row_that_starts_with_the_page_footer_banner_survives`` reds on
  it *first*; the strict-parse tests then red too, because losing a row is
  exactly what the oracle refuses. It cannot red alone, and that is the design.
- **A section title that misses.** A section the router cannot find parses to
  zero steps, and a zero-step waterfall satisfies a sum check and a chain check
  vacuously. ``test_a_missing_section_is_refused_not_reconciled_vacuously``
  makes the empty result flag instead of reading as clean.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from loanwhiz.primitives.base import PrimitiveResult
from loanwhiz.primitives.note_valuation_parser import (
    SECTION_PRINCIPAL_POP,
    NoteValuationReconciliationError,
    parse_note_valuation_text,
    parse_note_valuation_text_result,
    parse_stated_totals,
    reconcile_note_valuation,
)
from loanwhiz.primitives.notes_cash_parser import NotesCashPeriod, NotesCashReport

FIXTURE = Path(__file__).parent / "fixtures" / "note_valuation" / "cairn-clo-xvii-january-2025.txt"

PERIOD_LABEL = "January 2025"

#: Figures the *report* states, transcribed from the document, not from this
#: parser's output — that separation is the whole point of the checks below.
#: Source: Cairn CLO XVII DAC Note Valuation Report as of 08/01/2025, pages 3-13.
STATED_INTEREST_AVAILABLE = 7_255_062.35
STATED_PRINCIPAL_AVAILABLE = 0.0
STATED_TOTAL_CLOSING_BALANCE = 404_100_000.00
STATED_TOTAL_INTEREST_PAYABLE = 6_561_906.67

#: The Distribution Summary's ``Rate Current`` column, per class. ``None`` for
#: the Subordinated Notes because the Executive Summary prints ``N/A`` there —
#: the report's own word for a class with no coupon.
STATED_RATES: dict[str, float | None] = {
    "class_a": 5.008,
    "class_b_1": 5.958,
    "class_b_2": 6.870,
    "class_c": 6.808,
    "class_d": 8.508,
    "class_e": 10.668,
    "class_f": 12.848,
    "class_subordinated": None,
}

#: Each rated class's Interest PoP step, as the Distribution Summary
#: independently states the interest payable to it.
STATED_INTEREST_PAYABLE: dict[str, float] = {
    "class_a": 3_277_457.78,
    "class_b_1": 386_773.50,
    "class_b_2": 257_625.00,
    "class_c": 415_004.33,
    "class_d": 594_969.17,
    "class_e": 484_208.67,
    "class_f": 495_004.89,
    "class_subordinated": 650_863.33,
}

#: One payee row per waterfall whose text opens with the page-footer banner.
FOOTER_SHAPED_PAYEE = "U.S. Bank Global Corporate Trust Limited"
FOOTER_SHAPED_PAYEE_AMOUNT = 15_818.69

#: The verbatim line the falsifier tests delete or corrupt.
PAYEE_LINE = "U.S. Bank Global Corporate Trust Limited 15,818.69 7,222,548.16\n"
CLASS_A_STEP_LINE = (
    "(G) Interest Amounts due and payable on the Class A Notes 3,277,457.78 3,647,184.05\n"
)


def _text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def period() -> NotesCashPeriod:
    return parse_note_valuation_text(_text(), period_label=PERIOD_LABEL)


# ---------------------------------------------------------------------------
# The contract: the period ties out to everything the report states about itself
# ---------------------------------------------------------------------------


def test_the_period_reconciles_to_every_figure_the_report_states(
    period: NotesCashPeriod,
) -> None:
    """The acceptance oracle passes, and it is not vacuous — it makes real checks."""
    reconciliation = reconcile_note_valuation(period, parse_stated_totals(_text()))
    assert reconciliation.failures == []
    # A reconciliation with nothing in it would also report `ok`. Assert the
    # named checks are present, not just that none failed.
    names = {check.name for check in reconciliation.checks}
    assert {
        "interest_pop_present",
        "interest_pop_step_total",
        "interest_pop_balance_chain",
        "principal_pop_present",
        "principal_pop_balance_chain",
        "distribution_closing_total",
        "interest_step_matches_payable_class_a",
    } <= names


@pytest.mark.parametrize(
    ("attribute", "stated"),
    [
        ("available_revenue_funds", STATED_INTEREST_AVAILABLE),
        ("available_principal_funds", STATED_PRINCIPAL_AVAILABLE),
    ],
)
def test_each_waterfall_carries_the_available_funds_the_report_states(
    period: NotesCashPeriod, attribute: str, stated: float
) -> None:
    assert getattr(period, attribute) == pytest.approx(stated)


@pytest.mark.parametrize(
    ("steps_attribute", "stated"),
    [
        ("revenue_pop", STATED_INTEREST_AVAILABLE),
        ("redemption_pop", STATED_PRINCIPAL_AVAILABLE),
    ],
)
def test_each_waterfall_sums_to_its_stated_available_funds(
    period: NotesCashPeriod, steps_attribute: str, stated: float
) -> None:
    """Every euro the report says was available reaches exactly one step."""
    steps = getattr(period, steps_attribute)
    assert steps, "waterfall parsed no steps at all"
    assert sum(step.amount for step in steps) == pytest.approx(stated, abs=0.005)


@pytest.mark.parametrize("steps_attribute", ["revenue_pop", "redemption_pop"])
def test_each_waterfall_ends_at_its_stated_final_balance(
    period: NotesCashPeriod, steps_attribute: str
) -> None:
    """The last row's printed balance is zero — the waterfall distributes in full."""
    assert getattr(period, steps_attribute)[-1].balance_after == pytest.approx(0.0)


@pytest.mark.parametrize("steps_attribute", ["revenue_pop", "redemption_pop"])
def test_the_printed_running_balance_chains_without_a_break(
    period: NotesCashPeriod, steps_attribute: str
) -> None:
    """Each row's printed balance is the previous one less this step's amount.

    This is the property that makes a silently dropped row impossible, so it is
    asserted directly here as well as through the parser's own refusal.
    """
    steps = getattr(period, steps_attribute)
    running = round(
        (
            period.available_revenue_funds
            if steps_attribute == "revenue_pop"
            else period.available_principal_funds
        )
        * 100
    )
    for step in steps:
        running -= round(step.amount * 100)
        assert round(step.balance_after * 100) == running, f"chain breaks at {step.priority}"


# ---------------------------------------------------------------------------
# The falsifiers — what the oracle is for
# ---------------------------------------------------------------------------


def test_a_dropped_waterfall_row_is_refused_not_absorbed() -> None:
    """Delete one row from the source text; the parse must refuse, not shrug.

    The chain names the row *after* the gap, because that is where the printed
    balance stops matching the running one.
    """
    with pytest.raises(NoteValuationReconciliationError) as excinfo:
        parse_note_valuation_text(_text().replace(PAYEE_LINE, ""), period_label=PERIOD_LABEL)

    failures = {check.name: check.actual for check in excinfo.value.reconciliation.failures}
    assert "interest_pop_balance_chain" in failures
    assert "interest_pop_step_total" in failures
    assert "Fitch Ratings Ltd" in failures["interest_pop_balance_chain"]


def test_a_payee_row_that_starts_with_the_page_footer_banner_survives(
    period: NotesCashPeriod,
) -> None:
    """The row whose text opens with the page-footer banner is a payment, not furniture.

    A furniture filter applied before the money-tail check eats this row and
    under-reports its step by EUR 15,818.69 — the defect that motivated the
    ordering in ``_parse_waterfall``.
    """
    matches = [
        step for step in period.revenue_pop if step.recipient.startswith(FOOTER_SHAPED_PAYEE)
    ]
    assert len(matches) == 2, "the payee appears once under (C) and once under (Z)"
    assert matches[0].amount == pytest.approx(FOOTER_SHAPED_PAYEE_AMOUNT)


def test_a_missing_section_is_refused_not_reconciled_vacuously(monkeypatch) -> None:
    """A section the router cannot find must flag, not read as clean.

    The Principal waterfall's stated available funds are ``0.00``, so a
    zero-step parse of it sums to zero and chains trivially — every arithmetic
    check passes on nothing at all. Presence is therefore asserted separately,
    and this is the test that proves the assertion fires.
    """
    import loanwhiz.primitives.note_valuation_parser as module

    monkeypatch.setattr(
        module,
        "_SECTION_TITLES",
        tuple(title for title in module._SECTION_TITLES if title != SECTION_PRINCIPAL_POP),
    )

    with pytest.raises(NoteValuationReconciliationError) as excinfo:
        parse_note_valuation_text(_text(), period_label=PERIOD_LABEL)

    failed = {check.name for check in excinfo.value.reconciliation.failures}
    assert "principal_pop_present" in failed


def test_a_price_where_an_amount_belongs_is_refused() -> None:
    """A two-decimal column can be a price rather than an amount (#470).

    Corrupt Class A's Interest PoP step to a price-scale figure — four orders of
    magnitude out — and both the balance chain and the independent tie to the
    Distribution Summary's stated interest payable must catch it.
    """
    corrupted = _text().replace(
        CLASS_A_STEP_LINE,
        CLASS_A_STEP_LINE.replace("3,277,457.78", "99.72", 1),
    )
    assert corrupted != _text()

    with pytest.raises(NoteValuationReconciliationError) as excinfo:
        parse_note_valuation_text(corrupted, period_label=PERIOD_LABEL)

    failed = {check.name for check in excinfo.value.reconciliation.failures}
    assert "interest_step_matches_payable_class_a" in failed
    assert "interest_pop_balance_chain" in failed


# ---------------------------------------------------------------------------
# The Distribution Summary — per-class liability actuals
# ---------------------------------------------------------------------------


def test_per_class_balances_tie_to_the_distribution_summary_totals_row(
    period: NotesCashPeriod,
) -> None:
    """The classes parsed sum to the totals row the same section prints."""
    assert sum(b.principal_balance_after_payment for b in period.note_balances) == pytest.approx(
        STATED_TOTAL_CLOSING_BALANCE, abs=0.005
    )
    assert sum(b.total_interest_payments for b in period.note_balances) == pytest.approx(
        STATED_TOTAL_INTEREST_PAYABLE, abs=0.005
    )


def test_every_class_the_report_publishes_reaches_the_period(period: NotesCashPeriod) -> None:
    """Including both B tranches, kept apart rather than folded into one ``class_b``."""
    assert [b.note_class for b in period.note_balances] == list(STATED_RATES)


@pytest.mark.parametrize(("note_class", "stated"), sorted(STATED_RATES.items()))
def test_each_class_carries_the_rate_the_report_says_was_applied(
    period: NotesCashPeriod, note_class: str, stated: float | None
) -> None:
    """The applied rate is transcribed, never derived from coupon minus margin.

    The report publishes no separable index fixing anywhere in its 83 pages; it
    publishes the all-in rate applied to each class, twice — in the Distribution
    Summary and again in the Executive Summary — and the parser refuses unless
    the two agree.
    """
    balance = period.note_balance(note_class)
    assert balance is not None
    if stated is None:
        assert balance.interest_rate_applied is None
    else:
        assert balance.interest_rate_applied == pytest.approx(stated)


def test_a_class_the_report_marks_not_applicable_carries_no_rate(
    period: NotesCashPeriod,
) -> None:
    """No rate is ``None``, never ``0.0`` — the distinction #493 depends on.

    The Distribution Summary prints ``0.00000`` in the Subordinated Notes' rate
    cell while the Executive Summary prints ``N/A`` for the same number. A
    consumer that read the zero as a resolved rate would accrue no interest and
    call it an answer; ``None`` makes it refuse instead.
    """
    subordinated = period.note_balance("class_subordinated")
    assert subordinated is not None
    assert subordinated.interest_rate_applied is None
    # It is not a class with no figures — the report pays it EUR 650,863.33.
    assert subordinated.total_interest_payments == pytest.approx(
        STATED_INTEREST_PAYABLE["class_subordinated"]
    )


@pytest.mark.parametrize(
    ("note_class", "stated"),
    sorted((k, v) for k, v in STATED_INTEREST_PAYABLE.items() if k != "class_subordinated"),
)
def test_a_note_interest_step_equals_its_stated_interest_payable(
    period: NotesCashPeriod, note_class: str, stated: float
) -> None:
    """The waterfall and the Distribution Summary agree, section to section."""
    designation = note_class.removeprefix("class_").upper().replace("_", "-")
    step = next(
        s
        for s in period.revenue_pop
        if f"due and payable on the Class {designation} Notes" in s.recipient
    )
    assert step.amount == pytest.approx(stated)


# ---------------------------------------------------------------------------
# The reuse contract — this is a NotesCashPeriod, not a new report format
# ---------------------------------------------------------------------------


def test_the_parse_emits_the_existing_notes_cash_shape(period: NotesCashPeriod) -> None:
    """A CLO period joins downstream exactly as a Green Lion period does."""
    assert isinstance(period, NotesCashPeriod)
    assert period.reporting_date == "2025-01-08"
    assert period.period_label == PERIOD_LABEL
    assert period.deal_name == "Cairn CLO XVII DAC"
    assert period.revenue_step("(G)") is not None
    assert period.redemption_step("(A)(G)") is not None
    assert period.revenue_distributed_total() == pytest.approx(
        STATED_INTEREST_AVAILABLE, abs=0.005
    )


#: Each note's own interest step, as the Interest waterfall labels it. The
#: reconciliation oracle checks *amounts*; nothing in it checks that a step was
#: filed under the right label, so the labels are pinned here instead.
INTEREST_STEP_LABELS: dict[str, str] = {
    "class_a": "(G)",
    "class_b_1": "(H)(i)",
    "class_b_2": "(H)(ii)",
    "class_c": "(J)",
    "class_d": "(M)",
    "class_e": "(P)",
    "class_f": "(S)",
}


@pytest.mark.parametrize(("note_class", "label"), sorted(INTEREST_STEP_LABELS.items()))
def test_each_note_interest_step_is_filed_under_the_label_the_report_prints(
    period: NotesCashPeriod, note_class: str, label: str
) -> None:
    """The oracle validates amounts, not labels — so assert the labels directly.

    A compound label read one group short (``(H)`` for ``(H)(i)``) or a
    sub-label mistaken for a step would tie out arithmetically and still join
    the engine's step to the wrong published one.
    """
    designation = note_class.removeprefix("class_").upper().replace("_", "-")
    step = next(
        s
        for s in period.revenue_pop
        if f"due and payable on the Class {designation} Notes" in s.recipient
    )
    assert step.priority == label


def test_the_first_and_last_step_carry_the_labels_the_report_opens_and_closes_with(
    period: NotesCashPeriod,
) -> None:
    """Both ends of the Interest waterfall, including its deepest compound label."""
    assert period.revenue_pop[0].priority == "(A)(i)"
    assert period.revenue_pop[0].recipient == "Taxes CSP"
    assert period.revenue_pop[-1].priority == "(CC)(2)(III)(a)"
    assert period.redemption_pop[0].priority == "(A)(A)(i)"
    assert period.redemption_pop[-1].priority == "(S)(2)(III)"


def test_a_priority_label_is_not_unique_and_no_row_is_merged_away(
    period: NotesCashPeriod,
) -> None:
    """The report reuses labels; every row is kept anyway.

    A step whose payments are broken out over named payees prints the label once
    and the payees beneath it, so nine rows share ``(C)``. Keying by label would
    silently merge them — the ordered list is the join surface, and
    ``revenue_step`` returns the first match by design.
    """
    labels = Counter(step.priority for step in period.revenue_pop)
    assert labels["(C)"] == 9
    assert labels["(I)"] == 2, "the Par Value and Interest Coverage tests share a label"
    assert sum(labels.values()) == len(period.revenue_pop)


def test_strict_false_returns_a_failing_parse_for_inspection() -> None:
    """The only way to see *why* a report was refused."""
    broken = _text().replace(PAYEE_LINE, "")
    inspected = parse_note_valuation_text(broken, period_label=PERIOD_LABEL, strict=False)
    reconciliation = reconcile_note_valuation(inspected, parse_stated_totals(broken))
    assert not reconciliation.ok
    assert {c.name for c in reconciliation.failures} == {
        "interest_pop_step_total",
        "interest_pop_balance_chain",
    }


def test_a_text_with_no_pages_is_rejected() -> None:
    with pytest.raises(ValueError, match="no pages found"):
        parse_note_valuation_text("not a report", period_label=PERIOD_LABEL)


def test_a_report_with_no_as_of_date_is_rejected() -> None:
    stripped = "\n".join(
        line for line in _text().splitlines() if not line.strip().startswith("As of")
    )
    with pytest.raises(ValueError, match="no reporting date"):
        parse_note_valuation_text(stripped, period_label=PERIOD_LABEL)


# ---------------------------------------------------------------------------
# The governance envelope (#277)
# ---------------------------------------------------------------------------


def test_the_envelope_wrapper_returns_a_grounded_deterministic_result() -> None:
    result = parse_note_valuation_text_result(_text(), period_label=PERIOD_LABEL)
    assert isinstance(result, PrimitiveResult)
    assert result.confidence == 1.0
    assert result.output.reporting_date == "2025-01-08"
    assert [c.page_or_row for c in result.citations] == [
        "Distribution Summary",
        "Interest Priority of Payments",
        "Principal Priority of Payments",
    ]
    assert all("Cairn CLO XVII DAC" in c.document for c in result.citations)
    assert result.audit_entry.primitive_name == "note_valuation_parser"


# ---------------------------------------------------------------------------
# The live seam — wiring only; the fetch itself is stubbed at the network edge
# ---------------------------------------------------------------------------


def test_the_deal_level_reader_returns_the_existing_report_shape(monkeypatch) -> None:
    """A deal's registered reports become a ``NotesCashReport``, as for an RMBS deal.

    Only ``fetch_report_text`` is stubbed — the genuine network boundary. The
    parse, the reconciliation and the report assembly all run for real.
    """
    import loanwhiz.primitives.note_valuation_parser as module

    monkeypatch.setattr(module, "fetch_report_text", lambda url: _text())

    report = module.parse_note_valuation_report(
        {
            "deal_name": "Cairn CLO XVII DAC",
            "notes_cash_report_urls": [{"period": PERIOD_LABEL, "url": "https://example/nvr.pdf"}],
        }
    )

    assert isinstance(report, NotesCashReport)
    assert report.reporting_dates == ["2025-01-08"]
    period = report.period_for("2025-01-08")
    assert period is not None
    assert period.available_revenue_funds == pytest.approx(STATED_INTEREST_AVAILABLE)


def test_a_cross_reference_inside_a_wrapped_sentence_is_not_read_as_a_step() -> None:
    """Step (W)'s text wraps onto a line that opens ``(ii)`` — prose, not a label.

    The report's ``(W)`` reads "... in an amount of the lower of (i) 50.0 per
    cent. of all remaining Interest Proceeds, and / (ii) the extent necessary to
    cause such test to be satisfied". Read as a step header, that second line
    truncates (W)'s description and leaves ``(ii)`` as the parent any following
    unlabelled row would be filed under. Nothing arithmetic catches either — the
    amounts are untouched — so it is asserted directly.
    """
    period = parse_note_valuation_text(_text(), period_label=PERIOD_LABEL)
    step = next(s for s in period.revenue_pop if s.priority == "(W)")

    assert step.recipient.endswith("(ii) the extent necessary to cause such test to be satisfied")
    assert "(i) 50.0 per cent." in step.recipient
    # The only genuine ``(ii)`` in this waterfall is the hedge breakdown row.
    # A prose line promoted to a header would leave a second one, or file the
    # rows after it under ``(ii)`` instead of their own step.
    assert [s.recipient for s in period.revenue_pop if s.priority == "(ii)"] == [
        "Defaulted Currency Hedge Termination Payment"
    ]


def test_a_breakdown_row_still_keeps_its_own_lower_case_label(
    period: NotesCashPeriod,
) -> None:
    """The rule above bites headers only — a breakdown row's own ``(a)`` stands.

    Under the Senior Collateral Management Fee step the report breaks the
    payment out as ``(a) Senior Management Fee`` and ``(a) VAT - Senior
    Management Fee``, each with its own amount. Those keep their printed labels.
    """
    fee = next(s for s in period.revenue_pop if s.recipient == "Senior Management Fee")
    assert fee.priority == "(a)"
    assert fee.amount == pytest.approx(155_457.93)
