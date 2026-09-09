"""Contego CLO XI DAC parses through BNY Mellon's row geometry, and ties out.

#533 registered BNY Mellon and stopped at one question rather than guessing it:
every one of Contego's five aggregate tables states **212** against EUR
373,537,007.35, while Asset Information I, II and III each carry **177**
identifiers at that identical balance. Par ties to the euro and the count does
not, and #468's correction is that par alone cannot see a missed row — a row
you drop can be worth zero. So an oracle built on a guessed count is an oracle
that cannot fail.

The answer, and what these tests pin: **212 is neither an asset count nor a
purchase-lot count.** It is the number of rows in the report's own ``Interest
Accrual Detail`` section — one record per asset per rate contract — so an asset
accruing under two contracts is two rows there and two in every ``# of Assets``
column. #533's own lead, purchase lots, is falsified here as well as answered:
the lot table holds 256 rows, and per bucket it is further out still.

The reconciliation is therefore two counts over two populations, never one
against the other, and every per-bucket count is weighted at the accrual grain
while that same bucket's balance is summed at asset grain — because that is
what the document does, in adjacent columns of one row (#484).
"""

from __future__ import annotations

import loanwhiz.primitives.base  # noqa: F401  (import-order guard)

import json
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from loanwhiz.domain.tape_provenance import TapeScheme
from loanwhiz.primitives.derived_tape import (
    derive_tape,
    derived_tape_uri,
    is_derived_uri,
    source_document_for,
)
from loanwhiz.domain.trustee_report_registry import (
    SECTION_ACCRUAL_DETAIL,
    SECTION_ASSET_PART_IV,
    CountGrain,
)
from loanwhiz.primitives.collateral_schedule_parser import (
    _pages_for,
    _reflowed_line,
    _reflowed_rows,
    _resolve_layout,
    _split_pages,
    _BNY_PART_IV_TAIL,
    parse_schedule_text,
    reconcile_schedule,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "collateral_schedule"

#: The two committed periods, with what each report states about itself: the
#: aggregate tables' count, the aggregate balance, and the number of assets its
#: three detail sections each enumerate.
PERIODS = [
    ("august", "August 2024", 212, Decimal("373537007.35"), 177),
    ("september", "September 2024", 215, Decimal("380139061.10"), 179),
]
PERIOD_IDS = [period[0] for period in PERIODS]


def _text(period: str) -> str:
    return (FIXTURE_DIR / f"contego-clo-xi-{period}-2024.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("period", "label", "stated_count", "stated_par", "assets"), PERIODS, ids=PERIOD_IDS
)
def test_the_stated_count_reconciles_against_accrual_records_not_assets(
    period: str, label: str, stated_count: int, stated_par: Decimal, assets: int
) -> None:
    """The issue's question, as an assertion.

    Strict parsing already refuses on a failed check, so reaching this line at
    all means the schedule tied out. The assertions below say *which* figure
    tied to which population — the part a passing parse cannot show on its own,
    and the part that would still have looked fine had the count been guessed.
    """
    schedule = parse_schedule_text(_text(period), period_label=label, strict=True)

    assert len(schedule.assets) == assets
    assert schedule.total_principal_balance == stated_par
    assert schedule.aggregates.asset_count == stated_count
    assert schedule.aggregates.accrual_record_count == stated_count
    assert schedule.count_grain is CountGrain.ACCRUAL_RECORD

    # The two populations are genuinely different, which is the whole point:
    # were they equal the check above would pass for the wrong reason.
    assert stated_count > assets


@pytest.mark.parametrize(("period", "label"), [(p[0], p[1]) for p in PERIODS], ids=PERIOD_IDS)
def test_the_oracle_names_both_counts_and_checks_every_bucket(period: str, label: str) -> None:
    """Both counts are checked, and neither is checked against the other."""
    schedule = parse_schedule_text(_text(period), period_label=label, strict=True)
    checks = {check.name: check for check in reconcile_schedule(schedule).checks}

    assert checks["accrual record count"].ok
    assert checks["accrual record count"].expected == str(
        schedule.aggregates.accrual_record_count
    )
    # The asset count has its own oracle, since no aggregate table states one.
    for section in ("Asset Information I", "Asset Information II", "Asset Information III"):
        assert checks[f"asset count · {section}"].ok
        assert checks[f"asset count · {section}"].actual == str(len(schedule.assets))

    # A stated count is only worth checking per bucket as well as in total: two
    # rows swapped between buckets leave every total intact (#469).
    bucket_counts = [name for name in checks if name.startswith("S&P industry count · ")]
    assert len(bucket_counts) > 20
    assert all(checks[name].ok for name in bucket_counts)


def test_purchase_lots_are_a_third_population_and_are_not_what_212_counts() -> None:
    """#533's lead, falsified rather than left as an open question.

    Lots were the plausible answer and are the wrong one, and saying so
    concretely is what stops the next reader re-opening it: the lot table holds
    more rows than the aggregate tables state, so no reading of "212 counts
    lots" survives even before the per-bucket distribution is looked at.
    """
    schedule = parse_schedule_text(_text("august"), period_label="August 2024", strict=True)

    lots = sum(asset.purchase_lots or 0 for asset in schedule.assets)
    accruals = sum(asset.accrual_records or 0 for asset in schedule.assets)

    assert lots == 256
    assert accruals == 212 == schedule.aggregates.asset_count
    assert lots != schedule.aggregates.asset_count, (
        "the lot count is a real population of this document and is not the one "
        "its aggregate tables count"
    )
    # And the three grains really are three, not two of them coinciding.
    assert len({len(schedule.assets), accruals, lots}) == 3


def test_an_asset_accruing_under_two_contracts_counts_twice() -> None:
    """The mechanism, on a named row, rather than only in the totals.

    Ammega Group BV holds one facility, bought in one lot, accruing under two
    rate contracts at 1,682,998.00 and 280,069.00. It is one asset, one lot and
    **two** of the 212 — which is the whole of why the count column and the
    balance column of one table describe different populations.
    """
    schedule = parse_schedule_text(_text("august"), period_label="August 2024", strict=True)
    ammega = schedule.by_identifier()["LX213528"]

    assert ammega.issuer_name == "Ammega Group BV"
    assert ammega.accrual_records == 2
    assert ammega.purchase_lots == 1


@pytest.mark.parametrize(("period", "label"), [(p[0], p[1]) for p in PERIODS], ids=PERIOD_IDS)
def test_the_accrual_section_mixes_both_row_geometries_on_one_document(
    period: str, label: str
) -> None:
    """Geometry is declared per family and must be detected per page.

    The family declares ``REFLOWED_ROWS`` and most pages are, but this section
    carries at least one page that extracts as one row per line. A parser that
    read only each page's row-major line would skip those pages entirely — and
    since the skipped rows are accrual records, the count would come in short
    against a stated figure while par never moved. That is #468's shape, which
    is precisely the failure the count half of the oracle exists to catch.
    """
    pages = _split_pages(_text(period))
    layout = _resolve_layout(pages)
    section = _pages_for(pages, layout, SECTION_ACCRUAL_DETAIL)

    geometries = {_reflowed_line(page) is not None for page in section}
    assert geometries == {True, False}, (
        "this section is the fixture for per-page geometry detection; if it "
        "ever becomes uniform the detection is no longer exercised here"
    )


def test_a_lot_page_is_routed_by_its_row_major_line_not_its_first_lines() -> None:
    """Asset Information IV opens with a stack of lot numbers, not its title.

    Routing a page on its first twelve lines alone leaves this section matching
    nothing, so 256 lot rows read as zero — and zero rows is exactly what a
    vacuous reconciliation looks like from the outside (#494). The row-major
    line carries the table's own title, so that is what routes it.
    """
    pages = _split_pages(_text("august"))
    layout = _resolve_layout(pages)
    section = _pages_for(pages, layout, SECTION_ASSET_PART_IV)

    assert section, "the lot section must route to pages"
    first_lines = " ".join(section[0][:12])
    assert "Asset Information IV" not in first_lines, (
        "this page is the fixture for title-in-the-row-major-line routing; it "
        "must not start carrying its title in its first lines"
    )
    assert len(_reflowed_rows(section, layout, _BNY_PART_IV_TAIL)) == 256


def test_market_price_is_a_price_and_never_reaches_a_value_column() -> None:
    """#470, checked against the document rather than against our arithmetic.

    ``CCC Obligations`` is the one BNY table printing a market price and a
    market value for the same asset, so the units are checkable from the report
    itself: the value must be the par at that price. A parse that filed the
    price as a value would be wrong by two orders of magnitude here and would
    feed the OC ratio.
    """
    schedule = parse_schedule_text(_text("august"), period_label="August 2024", strict=True)

    assert schedule.defects.market_value_units == 0
    priced = [asset for asset in schedule.assets if asset.market_price_pct is not None]
    assert len(priced) == len(schedule.assets)
    assert all(Decimal("50") < asset.market_price_pct < Decimal("150") for asset in priced)

    valued = [asset for asset in schedule.assets if asset.market_value is not None]
    assert valued, "the CCC bucket states market values"
    for asset in valued:
        implied = (asset.principal_balance * asset.market_price_pct / Decimal(100)).quantize(
            Decimal("0.01")
        )
        assert abs(implied - asset.market_value) <= Decimal("0.02")


def test_an_unrated_asset_reports_no_rating_rather_than_a_rating_shaped_token() -> None:
    """``***`` is what this administrator prints for "no rating from this agency".

    Carrying it through as a string would give every consumer a token that
    sorts, groups and compares like a rating band while meaning its absence.
    """
    schedule = parse_schedule_text(_text("august"), period_label="August 2024", strict=True)

    ratings = {asset.sp_rating for asset in schedule.assets} | {
        asset.fitch_rating for asset in schedule.assets
    }
    assert "***" not in ratings
    assert None in ratings, "this report does carry assets one agency does not rate"
    assert {"B", "B-", "B+"} <= ratings


def test_contegos_tape_is_derived_over_the_existing_channel_with_derived_provenance() -> None:
    """#471's channel carries a second administrator with no new scheme.

    The derivation is exercised against the committed fixture over ``file://``
    rather than the live S3 document, so this is an offline test of the real
    code path: same URI grammar, same deriver, same mapping onto the canonical
    Annex 4 columns. What it proves is that registering a family was enough —
    that reaching a second CLO through this channel took a family table and a
    row geometry, not a second channel.
    """
    source = (FIXTURE_DIR / "contego-clo-xi-august-2024.txt").resolve().as_uri()
    uri = derived_tape_uri(source, "August 2024", scheme=TapeScheme.TRUSTEE_REPORT)

    assert is_derived_uri(uri)
    assert source_document_for(uri) == source

    with tempfile.TemporaryDirectory() as cache_dir:
        tape = derive_tape(uri, cache_dir=cache_dir)

    assert tape.annex_id == "annex_4"
    assert len(tape.rows) == 177
    assert tape.source_document == source
    # Contego, like Cairn, is a private securitisation with no ESMA loan-level
    # filing: the collateral schedule is the substitute for the article, so the
    # provenance is derived and must not claim to be a direct disclosure.
    assert all(field.source == "report" for field in tape.provenance.values())


def test_the_registry_points_contego_at_the_derived_channel() -> None:
    """The registration is the same shape as Cairn's, against the live reports.

    Pinned separately from the derivation because they fail differently: a URI
    that does not parse is a registration bug, and it would otherwise only
    surface on a machine with network access.
    """
    deal = json.loads(
        (Path(__file__).parents[1] / "src" / "loanwhiz" / "data" / "deals.json").read_text(
            encoding="utf-8"
        )
    )["contego-clo-xi"]

    tapes = deal["tape_urls"]
    assert [entry["date"] for entry in tapes] == ["2024-08-30", "2024-09-30"]
    reports = {entry["period"]: entry["url"] for entry in deal["investor_report_urls"]}
    for entry, period in zip(tapes, ("August 2024", "September 2024"), strict=True):
        assert is_derived_uri(entry["url"]), "no new scheme was introduced (#471)"
        assert source_document_for(entry["url"]) == reports[period], (
            "the tape must be derived from the very report the deal registers, "
            "not from a similarly named document"
        )
