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

import re
from decimal import Decimal
from pathlib import Path

import pytest

from loanwhiz.primitives.base import PrimitiveResult
from loanwhiz.primitives.collateral_schedule_parser import (
    PART_III_FLAGS,
    CollateralSchedule,
    ScheduleReconciliationError,
    parse_schedule_text,
    parse_schedule_text_result,
    reconcile_schedule,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "collateral_schedule"

#: Each committed report with the figures the report itself states. These are
#: the document's own numbers (Country Concentration / Rating Stratification
#: totals), not this parser's output — that is the whole point of the check.
PERIODS: list[tuple[str, str, int, str]] = [
    ("December 2024", "cairn-clo-xvii-december-2024.txt", 193, "407181748.22"),
    ("February 2025", "cairn-clo-xvii-february-2025.txt", 191, "401342140.14"),
    ("March 2025", "cairn-clo-xvii-march-2025.txt", 196, "411342140.14"),
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


@pytest.mark.parametrize(("period", "filename", "assets", "par"), PERIODS)
def test_every_period_reconciles_to_the_reports_own_totals(
    period: str, filename: str, assets: int, par: str
) -> None:
    """A parse is only trustworthy if it ties out — so pin all three periods.

    Three snapshots, not one: the parser handles a *report*, and the deal's
    asset count genuinely moves between them (193 → 191 → 196).
    """
    schedule = parse_schedule_text(_text(filename), period_label=period)

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

    for period, filename, count, _par in PERIODS:
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


def test_fixture_lines_survive_any_read_mode() -> None:
    """A fixture must read back identically in text mode and in binary.

    The reports carry stray CRLFs in their prose pages. Left in, a text-mode
    read translates them into extra line breaks, so the text the parser sees
    depends on how the file was opened — and an extra break inside a row is
    exactly what splits an asset in two.
    """
    for _period, filename, _assets, _par in PERIODS:
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
