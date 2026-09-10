"""Tests for the trustee-report collateral schedule parser.

The bar these pin is **reconciliation, not "it parses"**: every period's parsed
tape must tie out to the aggregates the source report states about itself, and a
tape that does not must be refused rather than returned. Children of the epic
build directly on this output, so a parse that silently drifts is the failure
mode worth the most test weight.

Everything here is offline and deterministic — the committed text fixtures are
the extracted output of the live ``pypdf`` seam, so no test touches the network
or needs ``pypdf`` installed.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from loanwhiz.primitives.base import PrimitiveResult
from loanwhiz.domain.trustee_report_registry import CountGrain
from loanwhiz.primitives.collateral_schedule_parser import (
    BALANCE_IN_TEXT,
    PART_III_FLAGS,
    CollateralSchedule,
    LiabilitySummaryReconciliationError,
    ReportLiabilitySummary,
    ScheduleReconciliationError,
    liability_provenance,
    parse_liability_summary_text,
    parse_par_value_numerator_text,
    parse_liability_summary_text_result,
    parse_schedule_text,
    parse_schedule_text_result,
    reconcile_liability_summary,
    reconcile_schedule,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "collateral_schedule"

#: Each committed report with the figures the report itself states. These are
#: the document's own numbers (Country Concentration / Rating Stratification
#: totals), not this parser's output — that is the whole point of the check.
PERIODS: list[tuple[str, str, int, str, str]] = [
    ("December 2024", "cairn-clo-xvii-december-2024.txt", 193, "407181748.22", "16/12/2024"),
    ("February 2025", "cairn-clo-xvii-february-2025.txt", 191, "401342140.14", "18/02/2025"),
    ("March 2025", "cairn-clo-xvii-march-2025.txt", 196, "411342140.14", "18/03/2025"),
]


def _text(filename: str) -> str:
    return (FIXTURE_DIR / filename).read_text(encoding="utf-8")


def _schedule(period: str, filename: str) -> CollateralSchedule:
    return parse_schedule_text(_text(filename), period_label=period)


@pytest.fixture(scope="module")
def march() -> CollateralSchedule:
    return _schedule("March 2025", "cairn-clo-xvii-march-2025.txt")


# ---------------------------------------------------------------------------
# The contract: every period ties out to the report's own stated totals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("period", "filename", "assets", "par", "as_of"), PERIODS)
def test_every_period_reconciles_to_the_reports_own_totals(
    period: str, filename: str, assets: int, par: str, as_of: str
) -> None:
    """A parse is only trustworthy if it ties out — so pin all three periods.

    Three snapshots, not one: the parser handles a *report*, and the deal's
    asset count genuinely moves between them (193 → 191 → 196).
    """
    schedule = parse_schedule_text(_text(filename), period_label=period)

    # The reporting date is read out of the report, not assumed from the label:
    # this parses a *report*, and the three are a time series.
    assert schedule.reporting_date == as_of
    assert schedule.deal_name == "Cairn CLO XVII DAC"
    assert len(schedule.assets) == assets
    assert schedule.total_principal_balance == Decimal(par)
    # The counts above are the report's own, read back out of the document.
    assert schedule.aggregates.asset_count == assets
    assert schedule.aggregates.aggregate_principal_balance == Decimal(par)

    reconciliation = reconcile_schedule(schedule)
    assert reconciliation.ok, [c.name for c in reconciliation.failures]
    assert schedule.defects.blocking == 0, schedule.defects.notes
    # Identifiers are unique: one row per asset per reporting date.
    assert len({a.identifier for a in schedule.assets}) == assets


def test_a_tape_that_lost_one_row_is_refused_not_returned() -> None:
    """Dropping a single asset must fail reconciliation loudly.

    This is the contract's reason for existing: #470/#471 consume this output,
    and a tape quietly missing twelve assets is worse than no tape at all.
    """
    text = _text("cairn-clo-xvii-march-2025.txt")
    # Remove one asset from every detail section, exactly as a silent parse bug
    # would: the row simply is not there, and nothing else changes.
    without = "\n".join(
        line for line in text.splitlines() if not line.startswith("LX171119")
    )

    with pytest.raises(ScheduleReconciliationError) as raised:
        parse_schedule_text(without, period_label="March 2025")

    message = str(raised.value)
    assert "asset count" in message
    assert "does not reconcile" in message
    failures = {c.name for c in raised.value.reconciliation.failures}
    assert "asset count" in failures
    assert "aggregate principal balance" in failures


def test_reconciliation_compares_distributions_not_just_the_total(
    march: CollateralSchedule,
) -> None:
    """Totals alone would pass with two assets' countries swapped."""
    names = {c.name for c in reconcile_schedule(march).checks}
    assert "country balance · United Kingdom" in names
    assert "country count · United Kingdom" in names
    assert "S&P industry balance · Pharmaceuticals" in names
    assert "S&P CCC bucket total" in names


# ---------------------------------------------------------------------------
# Row continuations — the issue's named correctness risk
# ---------------------------------------------------------------------------


def test_wrapped_cells_rejoin_to_the_row_they_belong_to(
    march: CollateralSchedule,
) -> None:
    """Multi-line issuer, facility and country cells must not mis-associate.

    Each assertion below is a cell the PDF split across two lines. A naive
    line-per-row parse silently attaches the remainder to the wrong row or the
    wrong column, which no total would catch.
    """
    assets = march.by_identifier()

    # Issuer fits on one line; the facility name wraps twice.
    sirona = assets["LX226715"]
    assert sirona.issuer_name == "AI Sirona (Luxembourg) Acquisition"
    assert sirona.facility_name == "AI Sirona T/L B3 (Zentiva) (3/24)"

    # The country wraps, and the space at the line break is not emitted:
    # "United" / "Kingdom" must rejoin as "United Kingdom", not "UnitedKingdom".
    assert assets["LX259930"].country == "United Kingdom"

    # This row wraps its facility name *and* its Fitch industry, so its
    # continuation line reads "<name remainder><industry remainder>" — the two
    # must be spliced back into their own columns, in column order.
    asmodee = assets["XS2954189234"]
    assert asmodee.facility_name == "ASMODEE GROUP AB FLOATING 12/15/2029"
    assert asmodee.fitch_industry == "Gaming and leisure and entertainment"
    assert asmodee.issuer_name == "Asmodee Group AB"
    assert asmodee.country == "France"


def test_a_facility_name_ending_in_a_digit_does_not_eat_the_balance(
    march: CollateralSchedule,
) -> None:
    """The name/balance boundary is undelimited, and one asset really tests it.

    ``Motor Fuel Group 6/24 (EUR) TLB4`` is immediately followed by
    ``4,500,000.00``. A greedy read takes the balance as ``44,500,000.00`` —
    ten times too big, and over the deal's own single-obligor limit.
    """
    asset = march.by_identifier()["LX235674"]
    assert asset.facility_name == "Motor Fuel Group 6/24 (EUR) TLB4"
    assert asset.principal_balance == Decimal("4500000.00")
    assert asset.issuer_name == "CD&R Firefly Bidco Limited"


# ---------------------------------------------------------------------------
# Sections are located by header, never by page number
# ---------------------------------------------------------------------------


def test_sections_are_located_by_header_not_page_number(
    march: CollateralSchedule,
) -> None:
    """Renumbering every page must not change the parse.

    The issue body's own page numbers were off by one, which is exactly why
    nothing here may depend on them.
    """
    text = _text("cairn-clo-xvii-march-2025.txt")
    renumbered = re.sub(
        r"^--- page (\d+) ---$",
        lambda m: f"--- page {int(m.group(1)) + 500} ---",
        text,
        flags=re.MULTILINE,
    )

    shifted = parse_schedule_text(renumbered, period_label="March 2025")
    assert shifted.assets == march.assets
    assert shifted.total_principal_balance == march.total_principal_balance


def test_both_report_renderings_parse_through_one_path() -> None:
    """The same trustee ships two layouts; neither may need its own parser.

    December's tables are drawn as ordinary space-separated text; March's are
    drawn rotated, so its cells arrive concatenated with no separator at all.
    """
    december = _text("cairn-clo-xvii-december-2024.txt")
    march = _text("cairn-clo-xvii-march-2025.txt")
    assert "LX226715 AI Sirona T/L B3 (Zentiva) (3/24) - - - - - - - -" in december
    assert "LX226715AI Sirona T/L B3 (Zentiva) (3/24)--------" in march

    for period, filename, count, _par, _as_of in PERIODS:
        assert len(_schedule(period, filename).assets) == count


# ---------------------------------------------------------------------------
# Part III flags — proven by the report's own Profile Tests, not assumed
# ---------------------------------------------------------------------------


def test_part_iii_flags_are_proven_by_the_reports_profile_tests(
    march: CollateralSchedule,
) -> None:
    """An all-``False`` flag parse must not be able to reconcile.

    Portfolio Profile Test (h) states the par of Current Pay Obligations and
    (i) the par of Revolving or Delayed Drawdown Collateral. Tying the flagged
    assets' par to those numerators is what makes the flag columns *checked*
    rather than merely populated.
    """
    assets = march.by_identifier()

    current_pay = [a for a in march.assets if a.flag("current_pay")]
    assert [a.identifier for a in current_pay] == ["LX210660"]
    test_h = march.aggregates.profile_test("(h)")
    assert test_h is not None
    assert sum(a.principal_balance for a in current_pay) == test_h.numerator

    drawdown = [
        a for a in march.assets if a.flag("revolving") or a.flag("delayed_drawdown")
    ]
    assert [a.identifier for a in drawdown] == ["LX236761"]
    test_i = march.aggregates.profile_test("(i)")
    assert test_i is not None
    assert sum(a.principal_balance for a in drawdown) == test_i.numerator

    # Every asset carries the full flag vocabulary, so an absent flag is a
    # recorded False rather than a missing key.
    assert set(assets["LX226715"].flags) == set(PART_III_FLAGS)
    assert not any(assets["LX226715"].flags.values())
    assert assets["LX236134"].flag("cov_lite")


def test_ccc_bucket_carries_seniority_and_rating(march: CollateralSchedule) -> None:
    """The CCC page is the only section publishing rating and seniority."""
    rated = [a for a in march.assets if a.sp_rating is not None]
    assert len(rated) == 5
    assert sum(a.principal_balance for a in rated) == march.aggregates.ccc_total

    altice = march.by_identifier()["LX217474"]
    assert altice.sp_rating == "CCC+"
    assert altice.seniority == "Senior Secured Loan"
    # An asset outside the CCC bucket has no rating rather than a defaulted one.
    assert march.by_identifier()["LX226715"].sp_rating is None


# ---------------------------------------------------------------------------
# Honest degradation — absent cells and a self-contradicting source
# ---------------------------------------------------------------------------


def test_a_fixed_rate_bond_reports_absent_cells_as_none(
    march: CollateralSchedule,
) -> None:
    """A fixed-rate asset has no index floor and no index — not a zero floor."""
    bond = march.by_identifier()["XS2294186965"]
    assert bond.asset_type == "Bond"
    assert bond.coupon_type == "Fixed"
    assert bond.current_coupon == Decimal("3.38")
    assert bond.index_floor is None
    assert bond.index_type is None

    loan = march.by_identifier()["LX226715"]
    assert loan.coupon_type == "Floating"
    assert loan.index_type == "EurIBOR"
    assert loan.index_floor == Decimal("0.00")
    assert loan.current_spread == Decimal("3.50")


def test_a_self_contradicting_source_table_is_surfaced_not_absorbed() -> None:
    """December's own industry table disagrees with December's own detail pages.

    The report states the Banks bucket as EUR 200,000 over one asset, while the
    detail pages put that asset's balance at EUR 1,000,000 — and the table's
    rows fall exactly EUR 800,000 short of the aggregate printed on the same
    page. The *document* is inconsistent, not the parse, and the country table
    (which is internally consistent) agrees with the detail pages. So the
    contradiction is reported and that table's balances are dropped from the
    oracle; its counts still have to tie.
    """
    schedule = _schedule("December 2024", "cairn-clo-xvii-december-2024.txt")

    assert "sp_industry" in schedule.aggregates.inconsistent_tables
    assert schedule.defects.source_aggregate_inconsistent == 3
    assert any("does not" in n or "but the same page states" in n for n in schedule.defects.notes)
    # It is surfaced, not fatal: no row was lost or mis-read.
    assert schedule.defects.blocking == 0

    banks = [a for a in schedule.assets if a.sp_industry == "Banks"]
    assert len(banks) == 1
    assert banks[0].principal_balance == Decimal("1000000.00")

    names = {c.name for c in reconcile_schedule(schedule).checks}
    assert "S&P industry count · Banks" in names
    assert "S&P industry balance · Banks" not in names
    # The country table is consistent, so its balances stay in the oracle.
    assert "country balance · France" in names


def test_defect_census_counts_a_row_it_cannot_resolve() -> None:
    """The census must fire on a real defect, not merely report zero.

    A counter that only ever prints ``0`` cannot distinguish "nothing is wrong"
    from "nothing was checked", so drive a row into failure and require both the
    count and the refusal.
    """
    text = _text("cairn-clo-xvii-march-2025.txt")
    # Corrupt one Part II row's balance so the row cannot be resolved.
    broken = text.replace(
        "LX171119Alloheim T/L B1,250,000.00Healthcare Providers & Services",
        "LX171119Alloheim T/L B____Healthcare Providers & Services",
        1,
    )
    assert broken != text

    with pytest.raises(ScheduleReconciliationError) as raised:
        parse_schedule_text(broken, period_label="March 2025")

    defects = raised.value.defects
    assert defects.blocking > 0
    assert defects.unrecognised_rows == 1
    assert any("LX171119" in note for note in defects.notes)

    # Non-strict still returns, so a failing parse can be inspected — and it
    # still reports the defect rather than presenting a clean schedule.
    lenient = parse_schedule_text(broken, period_label="March 2025", strict=False)
    assert lenient.defects.unrecognised_rows == 1
    assert len(lenient.assets) == 195


def test_a_truncated_part_iii_name_cannot_pass_silently() -> None:
    """Part III anchors every other section, so a short read there must be caught.

    Part III's facility name is the key that splits Part II's name/balance and
    Part I's issuer/name boundaries. If a continuation were ever dropped — the
    one shape this parser cannot rule out structurally, since a row's
    continuation is looked for only on its own page — the name would come back
    short. Part II independently consumes that whole name from its own
    rendering, so a short name leaves a fragment where the balance should be
    and the row is counted rather than quietly accepted.
    """
    text = _text("cairn-clo-xvii-march-2025.txt")
    broken = text.replace(
        "LX237014Vodafone Spain 7/24 (EUR) Cov-Lite T/L--------",
        "LX237014Vodafone Spain 7/24 (EUR) Cov-Lite--------",
        1,
    )
    assert broken != text

    with pytest.raises(ScheduleReconciliationError) as raised:
        parse_schedule_text(broken, period_label="March 2025")
    assert raised.value.defects.blocking > 0
    assert any("LX237014" in note for note in raised.value.defects.notes)


def test_fixture_lines_survive_any_read_mode() -> None:
    """A fixture must read back identically in text mode and in binary.

    The reports carry stray CRLFs in their prose pages. Left in, a text-mode
    read translates them into extra line breaks, so the text the parser sees
    depends on how the file was opened — and an extra break inside a row is
    exactly what splits an asset in two.
    """
    for _period, filename, _assets, _par, _as_of in PERIODS:
        path = FIXTURE_DIR / filename
        raw = path.read_bytes().decode("utf-8")
        assert path.read_text(encoding="utf-8") == raw, filename
        # Only "\n" may act as a break, so line counts agree both ways.
        assert len(raw.splitlines()) == raw.count("\n"), filename


# ---------------------------------------------------------------------------
# Governance envelope
# ---------------------------------------------------------------------------


def test_result_wrapper_returns_the_governance_envelope() -> None:
    """The reader participates in the #277 envelope contract."""
    result = parse_schedule_text_result(
        _text("cairn-clo-xvii-march-2025.txt"), period_label="March 2025"
    )

    assert isinstance(result, PrimitiveResult)
    assert result.confidence == 1.0
    assert len(result.citations) == 4
    assert all("Cairn CLO XVII DAC" in c.document for c in result.citations)
    assert {c.page_or_row for c in result.citations} == {
        "Current Asset Characteristics - Part I",
        "Current Asset Characteristics - Part II",
        "Current Asset Characteristics - Part III",
        "S&P CCC Obligations",
    }
    assert result.audit_entry is not None
    assert result.audit_entry.primitive_name == "collateral_schedule_parser"
    assert len(result.output.assets) == 196


# ===========================================================================
# The liability side — the notes, their coupons, and the coverage thresholds
# ===========================================================================
#
# The bar is the same as above and for the same reason: these figures exist to
# be consumed by the engine and graded against, so a plausible-but-wrong one is
# worse than none. What differs is the oracle. The collateral schedule ties to
# concentration tables; the liability summary ties to two stated totals over
# different quantities, and to the report's own second rendering of the same
# coverage tests in the opposite column order.


#: Every figure the March 2025 Executive Summary states, transcribed from the
#: document. Eight classes, not the six an eye skimming the table sees: Class F
#: and the Subordinated notes are what make the balances reach the stated
#: 404,100,000.00 rather than the 354,400,000.00 the report separately calls
#: "Total for E".
MARCH_NOTE_CLASSES: list[tuple[str, str, str | None, str | None]] = [
    ("A", "248000000.00", "4.54400", "2066005.33"),
    ("B-1", "24600000.00", "5.49400", "247779.40"),
    ("B-2", "15000000.00", "6.87000", "200375.00"),
    ("C", "23100000.00", "6.34400", "268668.40"),
    ("D", "26500000.00", "8.04400", "390804.33"),
    ("E", "17200000.00", "10.20400", "321766.13"),
    ("F", "14600000.00", "12.38400", "331478.40"),
    ("Subordinated", "35100000.00", None, None),
]

#: The required levels, which are deal terms rather than period figures — so
#: they must be identical in all three reports, and a parse that read them off
#: the wrong column would not be.
REQUIRED_LEVELS: dict[str, str] = {
    "class_a_b_par_value_test": "130.08",
    "class_c_par_value_test": "121.74",
    "class_d_par_value_test": "112.62",
    "class_e_par_value_test": "107.87",
    "class_f_par_value_test": "103.90",
    "reinvestment_overcollateralisation_test": "104.40",
    "class_a_b_interest_coverage_test": "120.00",
    "class_c_interest_coverage_test": "110.00",
    "class_d_interest_coverage_test": "105.00",
}


def _summary(period: str, filename: str) -> ReportLiabilitySummary:
    return parse_liability_summary_text(_text(filename), period_label=period)


@pytest.fixture(scope="module")
def march_liabilities() -> ReportLiabilitySummary:
    return _summary("March 2025", "cairn-clo-xvii-march-2025.txt")


@pytest.mark.parametrize(("period", "filename", "_assets", "_par", "as_of"), PERIODS)
def test_every_period_reconciles_to_the_reports_own_liability_totals(
    period: str, filename: str, _assets: int, _par: str, as_of: str
) -> None:
    """All three periods tie out, through the one path, in both renderings.

    December is the space-separated rendering and February/March the rotated
    one where cells arrive with no delimiter at all. Parametrising over all
    three is what proves a single parse path handles both — a fork would show up
    here as one period failing.
    """
    summary = parse_liability_summary_text(_text(filename), period_label=period)

    assert summary.reporting_date == as_of
    assert summary.deal_name == "Cairn CLO XVII DAC"
    assert len(summary.note_classes) == 8
    assert len(summary.coverage_tests) == 9

    # The capital structure does not amortise across these three periods, so the
    # stated total is the same 404,100,000.00 every month — and the parsed
    # classes must sum to it.
    assert summary.stated_total_balance == Decimal("404100000.00")
    assert summary.total_note_balance == summary.stated_total_balance
    assert summary.total_periodic_interest == summary.stated_total_periodic_interest

    reconciliation = reconcile_liability_summary(summary)
    assert reconciliation.ok, [c.name for c in reconciliation.failures]


def test_march_matches_every_figure_the_report_states(
    march_liabilities: ReportLiabilitySummary,
) -> None:
    """Transcribed figures, class by class — the point of the whole exercise.

    Class A's ``4.54400`` is the number the prospectus could not give: there it
    is ``3 month EURIBOR + 1.80%``, and resolving that needs the period's
    fixing.
    """
    parsed = [
        (
            c.note_class,
            str(c.principal_balance),
            None if c.coupon_pct is None else str(c.coupon_pct),
            None if c.periodic_interest is None else str(c.periodic_interest),
        )
        for c in march_liabilities.note_classes
    ]
    assert parsed == MARCH_NOTE_CLASSES

    # The two stated totals, both of which the classes above must sum to.
    assert march_liabilities.stated_total_balance == Decimal("404100000.00")
    assert march_liabilities.stated_total_periodic_interest == Decimal("3826876.99")


def test_a_class_with_no_stated_coupon_reads_none_not_zero(
    march_liabilities: ReportLiabilitySummary,
) -> None:
    """``N/A`` is an absent fact, not a zero.

    The subordinated notes are the first-loss piece: they take residual cash,
    not a coupon, and the report prints ``N/A`` for both rate cells. A note
    paying a 0% coupon and a note whose coupon the report does not state are
    different claims about the world, and only one of them is true here.
    """
    subordinated = march_liabilities.note_class("Subordinated")
    assert subordinated is not None
    assert subordinated.principal_balance == Decimal("35100000.00")
    assert subordinated.coupon_pct is None
    assert subordinated.periodic_interest is None
    assert subordinated.coupon_pct != Decimal("0")

    # And the stated interest total is over the classes that state one, so the
    # absent cells do not silently drag the reconciliation to a false pass.
    assert march_liabilities.total_periodic_interest == Decimal("3826876.99")


def test_a_lost_note_class_is_refused_not_returned() -> None:
    """Dropping one class must fail the balance oracle loudly."""
    text = _text("cairn-clo-xvii-march-2025.txt")
    without = text.replace("Class F Notes14,600,000.0012.38400331,478.40B-B-", "")

    with pytest.raises(LiabilitySummaryReconciliationError) as raised:
        parse_liability_summary_text(without, period_label="March 2025")

    failures = {c.name for c in raised.value.reconciliation.failures}
    assert "stated total note balance" in failures
    assert "does not reconcile" in str(raised.value)


def test_a_transposition_is_invisible_to_both_totals() -> None:
    """States what the oracles do NOT catch, so nobody over-trusts them.

    Swapping two classes' periodic interest leaves every balance untouched and
    the interest *sum* unchanged, so both stated totals still tie perfectly.
    A sum is invariant under transposition — which is exactly why the per-class
    figures are pinned by transcription in
    :func:`test_march_matches_every_figure_the_report_states` rather than by the
    reconciliation alone. Recorded here as a known limit of the oracle, not as
    a defect: no total over these rows can distinguish this case.
    """
    text = _text("cairn-clo-xvii-march-2025.txt")
    # Class C's interest attributed to Class D and vice versa. The sum is
    # unchanged, so this specifically defeats a totals-only check.
    mutated = text.replace(
        "Class C Notes23,100,000.006.34400268,668.40AAClass D Notes26,500,000.008.04400390,804.33BBB-BBB-",
        "Class C Notes23,100,000.006.34400390,804.33AAClass D Notes26,500,000.008.04400268,668.40BBB-BBB-",
    )
    assert mutated != text, "fixture text changed — the mutation no longer applies"

    summary = parse_liability_summary_text(mutated, period_label="March 2025", strict=False)
    # The balance oracle is satisfied, and the interest oracle's *total* is too:
    # a transposition is invisible to both, which is why the per-class figures
    # are pinned by transcription above rather than by a sum alone.
    assert summary.total_note_balance == summary.stated_total_balance
    assert summary.total_periodic_interest == summary.stated_total_periodic_interest
    assert summary.note_class("C").periodic_interest == Decimal("390804.33")


def test_dropping_a_stated_interest_figure_fails_the_second_oracle() -> None:
    """The interest total is a real check, not decoration."""
    text = _text("cairn-clo-xvii-march-2025.txt")
    # Same balance, no stated interest: the balance oracle still ties.
    mutated = text.replace(
        "Class E Notes17,200,000.0010.20400321,766.13BB-BB-",
        "Class E Notes17,200,000.00N/AN/ABB-BB-",
    )
    assert mutated != text, "fixture text changed — the mutation no longer applies"

    with pytest.raises(LiabilitySummaryReconciliationError) as raised:
        parse_liability_summary_text(mutated, period_label="March 2025")

    failures = {c.name for c in raised.value.reconciliation.failures}
    assert "stated total periodic interest" in failures
    assert "stated total note balance" not in failures


def test_required_levels_come_from_the_header_not_from_position() -> None:
    """The two sections state the same pairs in opposite column order.

    The Executive Summary prints ``Threshold`` then ``Current``; the detail
    pages print ``RATIO`` then ``REQUIRED LEVEL``. A parser that assumed either
    would swap a computed ratio with the level it has to clear — reporting a
    breaching test as passing, which is the worst answer available here. So the
    order is read off each section's own header, and this pins that it is: give
    the detail page the Executive Summary's header and its columns are read the
    other way round.
    """
    text = _text("cairn-clo-xvii-march-2025.txt")
    honest = parse_liability_summary_text(text, period_label="March 2025")
    assert honest.coverage_test("class_a_b_par_value_test").required_pct == Decimal("130.08")
    assert honest.coverage_test("class_a_b_par_value_test").current_pct == Decimal("139.43")

    swapped_header = text.replace(
        "OVERCOLLATERALIZATION TESTRATIOREQUIRED LEVELCALCULATIONRESULT",
        "Test DescriptionThresholdCurrentResult",
    )
    assert swapped_header != text, "fixture text changed — the mutation no longer applies"

    read_back = parse_liability_summary_text(
        swapped_header, period_label="March 2025", strict=False
    )
    par_value = next(
        t for t in read_back.coverage_tests if t.trigger_key == "class_a_b_par_value_test"
    )
    # Read in the order the header now claims: the detail page's first column is
    # taken as the required level rather than the ratio.
    assert par_value.required_pct == Decimal("139.43")
    assert par_value.current_pct == Decimal("130.08")

    # And the cross-check refuses it, because the two renderings now disagree.
    with pytest.raises(LiabilitySummaryReconciliationError):
        parse_liability_summary_text(swapped_header, period_label="March 2025")


def test_an_unrecognised_column_header_is_refused_not_guessed() -> None:
    """Two percentages in an unknown order is a wrong answer, not a degraded one."""
    text = _text("cairn-clo-xvii-march-2025.txt")
    mangled = text.replace(
        "OVERCOLLATERALIZATION TESTRATIOREQUIRED LEVELCALCULATIONRESULT",
        "OVERCOLLATERALIZATION TESTCOLUMN ONECOLUMN TWOCALCULATIONRESULT",
    )
    assert mangled != text, "fixture text changed — the mutation no longer applies"

    with pytest.raises(ValueError, match="no recognised coverage-test column header"):
        parse_liability_summary_text(mangled, period_label="March 2025")


def test_the_two_renderings_must_agree_or_the_parse_is_refused() -> None:
    """A figure stated twice and differently is a parse nobody should trust."""
    text = _text("cairn-clo-xvii-march-2025.txt")
    # Change one required level on the detail page only.
    mutated = text.replace(
        "Class D Par Value Test118.92%112.62%A/DPassed",
        "Class D Par Value Test118.92%111.11%A/DPassed",
    )
    assert mutated != text, "fixture text changed — the mutation no longer applies"

    with pytest.raises(LiabilitySummaryReconciliationError) as raised:
        parse_liability_summary_text(mutated, period_label="March 2025")

    failures = {c.name for c in raised.value.reconciliation.failures}
    assert "required level · class_d_par_value_test" in failures


def test_a_test_missing_from_one_rendering_is_refused() -> None:
    """A row that parsed in one section and not the other must not vanish quietly."""
    text = _text("cairn-clo-xvii-march-2025.txt")
    mutated = text.replace(
        "Class E Par Value Test113.15%107.87%A/EPassed", "", 1
    )
    assert mutated != text, "fixture text changed — the mutation no longer applies"

    with pytest.raises(LiabilitySummaryReconciliationError) as raised:
        parse_liability_summary_text(mutated, period_label="March 2025")

    failures = {c.name for c in raised.value.reconciliation.failures}
    assert "coverage tests named in both renderings" in failures


@pytest.mark.parametrize(("period", "filename", "_assets", "_par", "_as_of"), PERIODS)
def test_required_levels_are_the_same_in_every_report(
    period: str, filename: str, _assets: int, _par: str, _as_of: str
) -> None:
    """A required level is a deal term, so it cannot move month to month.

    The computed ratios do move (139.20% → 139.43%), which is what makes this a
    real check rather than a restatement of the fixture: a parse that read the
    wrong column would produce three *different* sets of "required" levels.
    """
    summary = parse_liability_summary_text(_text(filename), period_label=period)
    levels = {t.trigger_key: str(t.required_pct) for t in summary.coverage_tests}
    assert levels == REQUIRED_LEVELS


def test_coverage_test_keys_match_the_extracted_models_trigger_names() -> None:
    """The keys line up with the deal model's triggers, and #456 is why it matters.

    Those triggers all carry ``threshold: null`` today, because the offering
    circular's alphabetical glossary was truncated at 40,000 characters and
    every coverage test is defined under C-F. This asserts the correspondence
    that makes the gap closable — not that it is closed, which is a wiring
    change this PR deliberately does not make.
    """
    seed = json.loads(
        (
            Path(__file__).parents[1]
            / "src/loanwhiz/data/deals/seed/cairn-clo-xvii-dac.json"
        ).read_text(encoding="utf-8")
    )
    triggers = {t["name"]: t for t in seed["covenants"]["triggers"]}

    summary = _summary("March 2025", "cairn-clo-xvii-march-2025.txt")
    parsed_keys = {t.trigger_key for t in summary.coverage_tests}

    assert parsed_keys <= set(triggers), sorted(parsed_keys - set(triggers))
    assert parsed_keys == set(REQUIRED_LEVELS)
    # Every one of them is a threshold the extracted model still lacks — the
    # limitation this parse makes closable.
    for key in parsed_keys:
        assert triggers[key]["threshold"] is None
        # And the report's name for the test is the model's display name, so the
        # match above is a real correspondence rather than a lucky slug.
        assert triggers[key]["display_name"] == summary.coverage_test(key).name


def test_collateral_quality_tests_are_not_read_as_coverage_tests(
    march_liabilities: ReportLiabilitySummary,
) -> None:
    """The same page prints a second family of tests in an identical shape.

    ``Weighted Average Life Test7.024.27`` has no ``%`` terminator, so its two
    figures cannot be split apart at all; ``Fitch Maximum WA Rating Factor
    Test25.5024.46`` is the same trap with different digits. They are excluded
    by name rather than by position, because December's rendering interleaves
    the two families row by row.
    """
    names = {t.name for t in march_liabilities.coverage_tests}
    names |= {t.name for t in march_liabilities.summary_coverage_tests}
    for quality_test in (
        "S&P CDO Monitor Test",
        "Fitch Maximum WA Rating Factor Test",
        "Fitch Minimum WA Recovery Rate Test",
        "Maximum Obligor Concentration Test",
        "Minimum Weighted Average Spread Test",
        "Weighted Average Life Test",
    ):
        assert quality_test not in names


def test_a_report_figure_can_only_present_as_report_derived(
    march_liabilities: ReportLiabilitySummary,
) -> None:
    """Provenance must distinguish a trustee-report fact from a prospectus term.

    A coupon read off one month's trustee report is that month's stated figure;
    a prospectus term is the deal's contractual definition. Presenting the first
    as the second lends it an authority it does not have — so the source is a
    module constant with no parameter, and there is no call that can say
    ``prospectus``.
    """
    provenance = liability_provenance(march_liabilities)

    assert provenance
    assert {p.source for p in provenance.values()} == {"report"}
    assert all(p.method == "deterministic" for p in provenance.values())
    assert all(p.citation is not None for p in provenance.values())
    assert all(
        "Trustee Report" in p.citation.document for p in provenance.values()
    )

    coupon = provenance["tranches.class_a.coupon_pct"]
    assert coupon.confidence == 1.0
    assert coupon.reconciled is True
    assert coupon.citation.page_or_row == "Executive Summary"

    # A required level cites the section that states it beside the ratio.
    required = provenance["covenants.class_a_b_par_value_test.required_pct"]
    assert required.citation.page_or_row == "Par Value Tests Detail"
    assert provenance[
        "covenants.class_a_b_interest_coverage_test.required_pct"
    ].citation.page_or_row == "Interest Coverage Tests Detail"


def test_provenance_cannot_claim_a_reconciliation_that_did_not_run() -> None:
    """``reconciled`` is a recorded fact, not something a caller can assert.

    ``FieldProvenance.reconciled`` is the strong correctness signal the
    human-review gate routes by: it sends *unreconciled, low-confidence* fields
    to a person. A caller able to set it could route a figure that was never
    cross-checked straight past that reviewer — so the parser records whether
    the check ran and passed, and `liability_provenance` takes no argument for
    it at all.
    """
    text = _text("cairn-clo-xvii-march-2025.txt")

    checked = parse_liability_summary_text(text, period_label="March 2025")
    assert checked.reconciled is True
    assert {p.reconciled for p in liability_provenance(checked).values()} == {True}

    # strict=False is the documented inspection path: nothing was verified, and
    # the provenance must say so rather than inherit an optimistic default.
    unchecked = parse_liability_summary_text(
        text, period_label="March 2025", strict=False
    )
    assert unchecked.reconciled is False
    assert {p.reconciled for p in liability_provenance(unchecked).values()} == {False}


def test_an_unstated_figure_gets_no_provenance_key_at_all(
    march_liabilities: ReportLiabilitySummary,
) -> None:
    """Absence is a fact, and a key with a null value is not how to state it.

    The subordinated notes have a balance and no coupon. A map carrying a
    coupon key for them would read as "we have provenance for this coupon",
    which is the shape of claim #451 found reporting ``default_pct: 0.0`` for a
    pool of defaulted obligors.
    """
    provenance = liability_provenance(march_liabilities)

    assert "tranches.class_subordinated.principal_balance" in provenance
    assert "tranches.class_subordinated.coupon_pct" not in provenance
    assert "tranches.class_subordinated.periodic_interest" not in provenance
    # Every other class does state one.
    assert "tranches.class_f.coupon_pct" in provenance


def test_liability_result_wrapper_returns_the_governance_envelope() -> None:
    """The envelope, with citations naming the sections the figures came from."""
    result = parse_liability_summary_text_result(
        _text("cairn-clo-xvii-march-2025.txt"), period_label="March 2025"
    )

    assert isinstance(result, PrimitiveResult)
    assert result.confidence == 1.0
    assert len(result.citations) == 3
    assert {c.page_or_row for c in result.citations} == {
        "Executive Summary",
        "Par Value Tests Detail",
        "Interest Coverage Tests Detail",
    }
    assert all(
        c.document == "Cairn CLO XVII DAC — Monthly Trustee Report (March 2025)"
        for c in result.citations
    )
    assert result.audit_entry.primitive_name == "collateral_schedule_parser"
    assert len(result.output.note_classes) == 8
    assert len(result.output.coverage_tests) == 9


def test_the_liability_sections_are_located_by_header_not_page_number() -> None:
    """The brief's page numbers were off by one; the section titles were not.

    Prepending a page shifts every page number in the document by one. A parse
    keyed on position would break; one keyed on header text does not notice.
    """
    text = _text("cairn-clo-xvii-march-2025.txt")
    shifted = "--- page 0 ---\nCairn CLO XVII DAC\nInserted cover page\n" + text

    summary = parse_liability_summary_text(shifted, period_label="March 2025")
    assert len(summary.note_classes) == 8
    assert len(summary.coverage_tests) == 9
    assert summary.coverage_test("class_e_par_value_test").required_pct == Decimal("107.87")


def test_cairn_carries_none_of_the_fields_added_for_the_second_family() -> None:
    """The generalisation added columns; it must not have added *values* here.

    #555 gave ``CollateralAsset`` four fields the BNY parse populates — a
    quoted price, a purchase-lot count, an accrual-record count and a Fitch
    rating. Adding them moved the Cairn goldens' bytes, because each asset's
    golden digest covers every field of the row, so the goldens were
    regenerated. That is the one thing a golden cannot then prove about
    itself, and this is the assertion that closes the gap: on U.S. Bank's
    documents every one of those fields must still be absent.

    A future change that started populating one of them for Cairn — by
    defaulting a price to zero, say, or by folding the two families' column
    logic together — would move real numbers into a deal whose parse was
    supposed to be untouched, and the regenerated goldens would silently agree
    with it. This test does not.
    """
    schedule = parse_schedule_text(
        (FIXTURE_DIR / "cairn-clo-xvii-december-2024.txt").read_text(),
        period_label="December 2024",
    )

    added_for_bny = ("market_price_pct", "purchase_lots", "accrual_records", "fitch_rating")
    for asset in schedule.assets:
        for field in added_for_bny:
            assert getattr(asset, field) is None, (
                f"{asset.identifier} carries {field}={getattr(asset, field)!r}; "
                "this field is populated only by the BNY parse path"
            )
    assert schedule.count_grain is CountGrain.ASSET
    assert schedule.aggregates.accrual_record_count is None


# ---------------------------------------------------------------------------
# The par value tests' numerator (#550)
# ---------------------------------------------------------------------------

NOTE_VALUATION_DIR = Path(__file__).parent / "fixtures" / "note_valuation"

#: The numerator each U.S. Bank document states, and the note-class labels each
#: par value test divides it by (cumulative, senior-most first). These are the
#: report's own figures, read off its own Par Value Tests Detail page — not this
#: parser's output, which is the whole point of checking against them.
_NUMERATORS: list[tuple[str, Path, str]] = [
    ("December 2024", FIXTURE_DIR / "cairn-clo-xvii-december-2024.txt", "400334133.76"),
    ("February 2025", FIXTURE_DIR / "cairn-clo-xvii-february-2025.txt", "401005051.90"),
    ("March 2025", FIXTURE_DIR / "cairn-clo-xvii-march-2025.txt", "401013723.08"),
    (
        "January 2025",
        NOTE_VALUATION_DIR / "cairn-clo-xvii-january-2025.txt",
        "399984890.74",
    ),
]

#: Which classes each par value test's denominator runs through, in the order
#: the Executive Summary prints them.
_DENOMINATORS: dict[str, tuple[str, ...]] = {
    "Class A/B Par Value Test": ("A", "B-1", "B-2"),
    "Class C Par Value Test": ("A", "B-1", "B-2", "C"),
    "Class D Par Value Test": ("A", "B-1", "B-2", "C", "D"),
    "Class E Par Value Test": ("A", "B-1", "B-2", "C", "D", "E"),
    "Class F Par Value Test": ("A", "B-1", "B-2", "C", "D", "E", "F"),
}


@pytest.mark.parametrize(("period", "path", "expected"), _NUMERATORS)
def test_the_stated_numerator_ties_to_the_components_printed_above_it(
    period: str, path: Path, expected: str
) -> None:
    """The block prints its parts and their total; a parse must satisfy both.

    Reading only the total would accept a misread digit, and reading only the
    parts would accept a dropped line. Requiring the two to agree is what makes
    a partial read a refusal rather than an understatement — and the components
    include negatives, so a sign dropped anywhere breaks the tie.
    """
    numerator = parse_par_value_numerator_text(path.read_text(encoding="utf-8"))
    assert numerator is not None, f"{period} states a numerator block"
    assert numerator.stated_total == Decimal(expected)
    assert numerator.components_total == numerator.stated_total
    assert any(c < 0 for c in numerator.components), (
        "principal proceeds are subtracted — a parse reading only magnitudes "
        "would still tie out only if this deal happened to subtract nothing"
    )


@pytest.mark.parametrize(("period", "path", "expected"), _NUMERATORS)
def test_the_numerator_reproduces_every_ratio_the_report_states(
    period: str, path: Path, expected: str
) -> None:
    """The acceptance oracle: the ratio the engine would compute is published.

    Each par value test states its own ratio beside its required level. Dividing
    the numerator read here by the note balances read from the same document has
    to land on that figure, to the cent it is printed at, for every test on the
    page. Nothing here is back-solved: numerator, denominators and the ratio are
    three separate readings of the report, and only their agreement is checked.
    """
    text = path.read_text(encoding="utf-8")
    numerator = parse_par_value_numerator_text(text)
    assert numerator is not None
    summary = parse_liability_summary_text(text, period_label=period)
    balances = {c.note_class: c.principal_balance for c in summary.note_classes}

    checked = 0
    for test in summary.coverage_tests:
        classes = _DENOMINATORS.get(test.name)
        if classes is None:
            continue
        denominator = sum((balances[label] for label in classes), Decimal("0"))
        computed = (numerator.stated_total / denominator * 100).quantize(Decimal("0.01"))
        assert computed == test.current_pct, (
            f"{period} · {test.name}: computed {computed}, report states "
            f"{test.current_pct}"
        )
        checked += 1
    assert checked == len(_DENOMINATORS), "every par value test on the page was checked"


def test_the_numerator_is_not_the_aggregate_principal_balance() -> None:
    """The near-miss this exists to refuse, made explicit.

    ``aggregate_principal_balance`` is on the same document, is asset-side, and
    is the obvious thing to reach for. It is the numerator's *first component*,
    before principal proceeds and the defaulted / discount adjustments — so
    substituting it does not fail loudly, it reports a healthier deal than the
    report does.
    """
    text = (NOTE_VALUATION_DIR / "cairn-clo-xvii-january-2025.txt").read_text(
        encoding="utf-8"
    )
    numerator = parse_par_value_numerator_text(text)
    summary = parse_liability_summary_text(text, period_label="January 2025")
    assert numerator is not None
    aggregate = parse_schedule_text(
        text, period_label="January 2025", strict=False
    ).aggregates.aggregate_principal_balance
    assert aggregate is not None
    assert aggregate != numerator.stated_total

    senior = sum(
        (
            c.principal_balance
            for c in summary.note_classes
            if c.note_class in _DENOMINATORS["Class A/B Par Value Test"]
        ),
        Decimal("0"),
    )
    published = summary.coverage_test("class_a_b_par_value_test")
    assert published is not None
    assert (numerator.stated_total / senior * 100).quantize(Decimal("0.01")) == published.current_pct
    assert (aggregate / senior * 100).quantize(Decimal("0.01")) != published.current_pct


def test_a_family_that_states_no_numerator_block_is_refused_not_guessed() -> None:
    """BNY Mellon prints no such block, so the answer is None, never a guess.

    The refusal is the contract: an unavailable numerator has to reach the
    monitor as absent, because the alternative — reading some other figure into
    its place — is the defect the whole seam exists to prevent.
    """
    text = (FIXTURE_DIR / "contego-clo-xi-august-2024.txt").read_text(encoding="utf-8")
    assert parse_par_value_numerator_text(text) is None


def test_the_interest_coverage_blocks_numerator_is_not_read_as_the_collateral_one() -> None:
    """The same document states a second ``NUMERATOR``, over interest proceeds.

    It would tie out against its own components too, so the components check
    cannot catch a section slip — only scoping the search to the Par Value Tests
    Detail pages can. Pin that the figure read is the par value one.
    """
    text = (NOTE_VALUATION_DIR / "cairn-clo-xvii-january-2025.txt").read_text(
        encoding="utf-8"
    )
    numerator = parse_par_value_numerator_text(text)
    assert numerator is not None
    # The interest block's total is smaller than the collateral one by orders of
    # magnitude; reading it would be silent, not an error.
    assert numerator.stated_total > Decimal("100000000")


def test_the_summary_carries_the_numerator_and_reconciles_it() -> None:
    """A strict parse of a report stating the block returns it, already tied out."""
    summary = parse_liability_summary_text(
        _text("cairn-clo-xvii-february-2025.txt"), period_label="February 2025"
    )
    assert summary.reconciled is True
    assert summary.par_value_numerator is not None
    assert summary.par_value_numerator.stated_total == Decimal("401005051.90")


def test_a_numerator_whose_components_disagree_with_its_total_is_refused() -> None:
    """The reconciliation must be able to fail, or it proves nothing (#493)."""
    summary = parse_liability_summary_text(
        _text("cairn-clo-xvii-february-2025.txt"),
        period_label="February 2025",
        strict=False,
    )
    assert summary.par_value_numerator is not None
    summary.par_value_numerator.stated_total += Decimal("0.01")
    reconciliation = reconcile_liability_summary(summary)
    assert reconciliation.ok is False
    assert any(
        "par value numerator" in check.name and not check.ok
        for check in reconciliation.checks
    )


# ---------------------------------------------------------------------------
# #600 — a section's total must not be welded onto the last obligor's name
# ---------------------------------------------------------------------------
#
# Par ties either way, which is why this survived: the arithmetic reconciles
# against the report's own aggregates while the name is wrong (#468's lesson).
# Every assertion below is therefore on the **name**, never on a total.


_BALANCE_IN_A_NAME = re.compile(BALANCE_IN_TEXT)


@pytest.mark.parametrize(("period", "filename", "_assets", "_par", "_as_of"), PERIODS)
def test_no_issuer_name_carries_a_balance(
    period: str, filename: str, _assets: int, _par: str, _as_of: str
) -> None:
    """No obligor name may carry a thousands-separated amount.

    The shape is the money, not the digit: real borrowers here are named
    ``Emerald 2 Ltd.`` and ``Techem Verwaltungsgesellschaft 675 MBH``, and a
    guard keyed on "has a digit" would discard them (#439).
    """
    schedule = parse_schedule_text(_text(filename), period_label=period)
    carrying = {
        asset.identifier: asset.issuer_name
        for asset in schedule.assets
        if asset.issuer_name and _BALANCE_IN_A_NAME.search(asset.issuer_name)
    }
    assert carrying == {}


@pytest.mark.parametrize(("period", "filename", "_assets", "par", "_as_of"), PERIODS)
def test_the_last_row_of_a_section_is_not_given_that_sections_total(
    period: str, filename: str, _assets: int, par: str, _as_of: str
) -> None:
    """``LX183461`` sorts last in Part I, so the total line follows its row.

    Read as a wrapped remainder it welded the **portfolio aggregate** — the very
    figure this row's period states as its par — onto the obligor. The name is
    pinned exactly, and the amount that used to be glued to it is named here so
    the test says what went wrong, not merely that something did.
    """
    schedule = parse_schedule_text(_text(filename), period_label=period)
    (ziggo,) = [a for a in schedule.assets if a.identifier == "LX183461"]

    assert ziggo.issuer_name == "Ziggo Secured Finance B.V."
    # The figure that used to be appended was this period's stated aggregate,
    # not anything belonging to this asset — whose own balance is a rounding
    # error beside it.
    assert schedule.aggregates.aggregate_principal_balance == Decimal(par)
    assert ziggo.principal_balance == Decimal("1000000.00")


def test_a_genuinely_wrapped_row_is_still_joined() -> None:
    """The guard must refuse a total line without eating a real continuation.

    ``XS2431015655``'s facility name wraps across two rendered lines
    (``VZ Secured`` / ``Financing BV``). If the total-line test were widened
    past "nothing but an amount" this row would lose its remainder, so it is
    the falsifier for an over-broad guard.
    """
    schedule = _schedule("December 2024", "cairn-clo-xvii-december-2024.txt")
    (wrapped,) = [a for a in schedule.assets if a.identifier == "XS2431015655"]

    assert wrapped.facility_name == "VZ Secured Financing BV"


def test_a_name_that_still_carries_a_balance_is_refused_not_returned() -> None:
    """Contaminated by another route, the name is refused — and par still ties.

    The row's amount is spliced into the issuer cell *on the row's own line*, so
    the row-grouper's total-line test cannot see it. That is the point: the
    guard is on the output's plausibility, not on the geometry that was fixed
    (#548), so it still fires on a mis-read nobody has seen yet.

    Note what reds and what does not — the defect count and the name move; the
    reconciliation does not. An arithmetic oracle cannot catch this class.
    """
    clean = _text("cairn-clo-xvii-march-2025.txt")
    contaminated = clean.replace(
        "LX202330CEP V Investment 23 S.a.r.l",
        "LX202330CEP V Investment 999,888,777.66 23 S.a.r.l",
        1,
    )
    assert contaminated != clean, "fixture line moved — update this test's anchor"

    schedule = parse_schedule_text(contaminated, period_label="March 2025", strict=False)
    (row,) = [a for a in schedule.assets if a.identifier == "LX202330"]

    assert row.issuer_name is None, "a name carrying a balance is not a name"
    assert schedule.defects.unresolved_issuer_name == 1
    # The arithmetic is untouched: this is exactly why the defect survived.
    assert schedule.total_principal_balance == Decimal("411342140.14")


def test_a_bny_description_carrying_a_subtotal_reports_no_obligor() -> None:
    """The second family reaches the same contract by its own route.

    Contego's pages are re-cut out of one row-major line, so a group's
    ``Subtotal:`` trailer lands on the front of the next group's first row and
    the ``" - "`` separator is still present — the split alone cannot catch it.
    The obligor is refused and counted; the facility half is readable and is
    still returned, because a guard should withhold only what it cannot read.
    """
    schedule = parse_schedule_text(
        _text("contego-clo-xi-august-2024.txt"), period_label="August 2024", strict=False
    )
    (row,) = [a for a in schedule.assets if a.identifier == "XS2342057143"]

    assert row.issuer_name is None
    assert row.facility_name == "Allied Universal Hold 3.625 01Jun28"
    assert schedule.defects.descriptions_without_an_obligor == 1


def test_a_legitimate_digit_bearing_name_is_not_collateral_damage() -> None:
    """The guard's excluded set, checked against real input rather than assumed.

    #439's rule: write down what the guard excludes and test it. These three are
    real borrowers on this book whose names carry digits, and none carries the
    thousands separator that makes an amount an amount.
    """
    schedule = _schedule("March 2025", "cairn-clo-xvii-march-2025.txt")
    names = {a.issuer_name for a in schedule.assets}

    assert "Emerald 2 Ltd." in names
    assert "Techem Verwaltungsgesellschaft 675 MBH" in names
    assert "Spa Holdings 3 Oy" in names
