"""Contract tests for resolving the trustee schedule onto Annex 4 columns (#470).

The mapping is the point of the epic, and its failure mode is silent: every
number stays correct while a value acquires a field code that does not belong to
it, or an unpublished field reads as a measured zero. So these tests assert the
negatives — what the tape must **not** claim — against the real committed
fixtures rather than a hand-built row.

Four contracts are pinned here:

1. **The import-time guard refuses a bad correspondence, both directions.**
2. **The three traps stay shut.** S&P/Fitch industry is not NACE, a country is
   not a NUTS-3 region, a price per 100 of par is not a market value, and an
   obligor name is not an obligor identifier — none of them can yield a ``CRPL``
   locator by any route.
3. **Absent is not zero.** A column the source does not publish emits no key.
4. **Nothing describes this tape as filed.**
"""

from __future__ import annotations

import re
from decimal import Decimal

# Import a ``primitives`` module before ``loanwhiz.domain`` so the package-init
# import cycle resolves. Same convention as ``tests/test_esma_annex_registry.py``.
from loanwhiz.primitives.collateral_schedule_parser import (
    CollateralSchedule,
    parse_schedule_text,
    reconcile_schedule,
)

import pytest
from pydantic import ValidationError

from loanwhiz.domain.esma_annex4_corporate import ANNEX4_CORPORATE
from loanwhiz.domain.esma_annex_registry import ANNEX_REGISTRY
from loanwhiz.domain.tape_provenance import (
    AbsentColumn,
    ApproximateCorrespondenceError,
    Correspondence,
    TapeSourceKind,
)
from loanwhiz.primitives import collateral_tape_mapping
from loanwhiz.primitives.collateral_tape_mapping import (
    ABSENT_COLUMNS,
    SCHEDULE_FIELD_MAP,
    MappedCollateralTape,
    ScheduleFieldMapping,
    SourceRef,
    _validate_mapping,
    map_schedule,
    map_schedule_result,
)

from pathlib import Path

_FIXTURES = Path(__file__).parent / "fixtures" / "collateral_schedule"

#: The three committed periods, so a contract is checked against every rendering
#: the trustee ships rather than the one that happened to be convenient.
_PERIODS: tuple[tuple[str, str], ...] = (
    ("2024-12", "cairn-clo-xvii-december-2024.txt"),
    ("2025-02", "cairn-clo-xvii-february-2025.txt"),
    ("2025-03", "cairn-clo-xvii-march-2025.txt"),
)

_DOCUMENT = "Cairn CLO XVII DAC monthly trustee report"


def _schedule(filename: str, period_label: str) -> CollateralSchedule:
    text = (_FIXTURES / filename).read_text()
    return parse_schedule_text(text, period_label=period_label)


@pytest.fixture(scope="module")
def march() -> CollateralSchedule:
    """The March 2025 schedule — the period #469 reconciles to 196 assets."""
    return _schedule("cairn-clo-xvii-march-2025.txt", "2025-03")


@pytest.fixture(scope="module")
def march_tape(march: CollateralSchedule) -> MappedCollateralTape:
    return map_schedule(march, source_document=_DOCUMENT)


# ===========================================================================
# 1. The import-time guard
# ===========================================================================


def test_the_shipped_mapping_table_validates() -> None:
    """The guard that ran at import is re-run here, so it is a test, not a side effect."""
    _validate_mapping()


def test_the_guard_is_invoked_at_module_scope() -> None:
    """The "cannot be imported" half of the claim, which the tests above do not reach.

    Every other guard test calls :func:`_validate_mapping` directly, so all of
    them still pass if the module-level invocation is deleted — and the contract
    would silently weaken from "a bad row cannot be loaded" to "a bad row fails
    only if someone remembers to check". This asserts the call site itself.

    It pins the call, not the raise: that a violating row genuinely aborts the
    import is what the direct-call tests above establish.
    """
    source = Path(collateral_tape_mapping.__file__).read_text()
    assert re.search(r"^_validate_mapping\(\)$", source, re.MULTILINE), (
        "_validate_mapping() is no longer called at module scope, so the mapping "
        "table is validated on demand rather than at import."
    )


def test_guard_refuses_an_approximate_row_carrying_a_regulatory_code() -> None:
    """The laundering case, at the table level.

    This is the edit a future contributor makes when an S&P industry name
    "obviously" belongs in the industry column. It must not import.
    """
    bad = (
        ScheduleFieldMapping(
            source=SourceRef.ASSET_ATTR,
            source_name="sp_industry",
            canonical_column="industry_code",
            correspondence=Correspondence.APPROXIMATE,
            note="close enough to NACE",
        ),
    )
    with pytest.raises(ApproximateCorrespondenceError, match="CRPL14"):
        _validate_mapping(bad, ())


def test_guard_refuses_an_exact_row_onto_a_codeless_column() -> None:
    """The half that fails silently without a guard (#453)."""
    bad = (
        ScheduleFieldMapping(
            source=SourceRef.ASSET_ATTR,
            source_name="sp_industry",
            canonical_column="sp_industry",
            correspondence=Correspondence.EXACT,
            note="claims a code the annex does not define",
        ),
    )
    with pytest.raises(ApproximateCorrespondenceError, match="extension field"):
        _validate_mapping(bad, ())


def test_guard_refuses_two_rows_resolving_to_one_column() -> None:
    row = ScheduleFieldMapping(
        source=SourceRef.ASSET_ATTR,
        source_name="sp_industry",
        canonical_column="sp_industry",
        correspondence=Correspondence.APPROXIMATE,
        note="a duplicate makes the emitted value order-dependent",
    )
    with pytest.raises(ValueError, match="duplicate canonical column"):
        _validate_mapping((row, row), ())


def test_guard_refuses_a_column_declared_both_emitted_and_absent() -> None:
    row = ScheduleFieldMapping(
        source=SourceRef.ASSET_ATTR,
        source_name="market_value",
        canonical_column="market_price_pct",
        correspondence=Correspondence.APPROXIMATE,
        note="the stated price",
    )
    absent = (
        AbsentColumn(
            canonical_column="market_price_pct",
            rts_code=None,
            reason="contradicts the emitted row above, which is the point.",
        ),
    )
    with pytest.raises(ValueError, match="both emitted and"):
        _validate_mapping((row,), absent)


def test_guard_refuses_an_absent_column_misstating_its_rts_code() -> None:
    """A transcription error in the very record a reader would trust."""
    absent = (
        AbsentColumn(
            canonical_column="market_value",
            rts_code="CRPL39",
            reason="the wrong code, in the record naming what is missing.",
        ),
    )
    with pytest.raises(ValueError, match="declares RTS code"):
        _validate_mapping((), absent)


def test_guard_refuses_an_absence_declared_for_a_column_the_annex_lacks() -> None:
    absent = (
        AbsentColumn(
            canonical_column="not_a_real_column",
            rts_code=None,
            reason="declaring a non-existent column absent asserts nothing.",
        ),
    )
    with pytest.raises(ValueError, match="not in the Annex 4 table"):
        _validate_mapping((), absent)


# ===========================================================================
# 2. The traps
# ===========================================================================


def test_industry_columns_can_yield_no_nace_locator(
    march_tape: MappedCollateralTape,
) -> None:
    """CRPL14 is a NACE code; an S&P or Fitch industry name is a different scheme."""
    for column in ("sp_industry", "fitch_industry"):
        assert ANNEX4_CORPORATE.code_for_column(column) is None
        assert column not in march_tape.locators()
        assert march_tape.provenance[column].citation.page_or_row is None


def test_country_can_yield_no_nuts3_locator(march_tape: MappedCollateralTape) -> None:
    """CRPL10 is a NUTS-3 sub-national region; the report states a country."""
    assert ANNEX4_CORPORATE.code_for_column("obligor_country") is None
    assert "obligor_country" not in march_tape.locators()


def test_a_column_spelled_country_no_longer_resolves_to_crpl10() -> None:
    """The live trap this PR closes, asserted at the annex table.

    Before #470 a tape column named ``country`` resolved through CRPL10's
    synonyms onto ``province`` and was cited as a NUTS-3 region. It now resolves
    onto a code-less column: still usable, no fabricated locator.
    """
    assert ANNEX4_CORPORATE.canonical_column_for("country") == "obligor_country"
    assert ANNEX4_CORPORATE.code_for_column("country") is None


def test_a_column_spelled_industry_no_longer_resolves_to_crpl14() -> None:
    assert ANNEX4_CORPORATE.canonical_column_for("industry") == "industry_classification"
    assert ANNEX4_CORPORATE.code_for_column("industry") is None
    assert ANNEX4_CORPORATE.code_for_column("industry_classification") is None


def test_removing_the_trap_synonyms_did_not_orphan_the_real_fields() -> None:
    """The genuine spellings still resolve — the fix narrowed, it did not delete."""
    assert ANNEX4_CORPORATE.code_for_column("nace") == "CRPL14"
    assert ANNEX4_CORPORATE.code_for_column("nace_code") == "CRPL14"
    assert ANNEX4_CORPORATE.code_for_column("province") == "CRPL10"
    assert ANNEX4_CORPORATE.code_for_column("nuts3") == "CRPL10"


def test_market_value_is_absent_and_the_stated_price_carries_no_locator(
    march_tape: MappedCollateralTape,
) -> None:
    """CRPL41 is a value amount; the report states a price per 100 of par.

    Emitting ``99.72`` under ``market_value`` would be wrong by four orders of
    magnitude quite apart from the provenance question.
    """
    assert "market_value" not in march_tape.columns
    assert "market_price_pct" not in march_tape.locators()
    assert all("market_value" not in row for row in march_tape.rows)

    entry = next(c for c in ABSENT_COLUMNS if c.canonical_column == "market_value")
    assert entry.rts_code == "CRPL41"
    # The reason must name the derivation, so a consumer that needs the amount
    # knows how to get it rather than assuming nobody thought about it.
    assert "market_price_pct" in entry.reason


def test_the_stated_price_is_carried_verbatim(march_tape: MappedCollateralTape) -> None:
    """``LX226715`` prints ``99.72`` on the March Part I page against par 3,000,000.00."""
    row = next(r for r in march_tape.rows if r["loan_identifier"] == "LX226715")
    assert row["market_price_pct"] == Decimal("99.72")
    assert row["current_balance"] == Decimal("3000000.00")


def test_obligor_name_is_not_an_obligor_identifier(
    march_tape: MappedCollateralTape,
) -> None:
    """CRPL4 is an identifier, and the RTS identifier is not the obligor's real name."""
    assert "obligor_name" not in march_tape.locators()
    assert "obligor_identifier" not in march_tape.columns
    assert any(c.rts_code == "CRPL4" for c in ABSENT_COLUMNS)


def test_every_approximate_row_states_why_it_has_no_locator(
    march_tape: MappedCollateralTape,
) -> None:
    """An absent locator with no reason beside it reads as an oversight."""
    for mapping in SCHEDULE_FIELD_MAP:
        if mapping.correspondence is not Correspondence.APPROXIMATE:
            continue
        citation = march_tape.provenance[mapping.canonical_column].citation
        assert citation.page_or_row is None
        assert "No ESMA RTS locator" in citation.excerpt


def test_every_exact_row_carries_a_real_locator(
    march_tape: MappedCollateralTape,
) -> None:
    for mapping in SCHEDULE_FIELD_MAP:
        if mapping.correspondence is not Correspondence.EXACT:
            continue
        locator = march_tape.locators()[mapping.canonical_column]
        assert locator.startswith(("CRPL", "CRPC"))


def test_the_emitted_locators_are_exactly_the_verified_set(
    march_tape: MappedCollateralTape,
) -> None:
    """Pins the mapping a reviewer checked, so a later addition is a deliberate act."""
    assert {locator.split(" · ")[0] for locator in march_tape.locators().values()} == {
        "CRPL2",
        "CRPL6",
        "CRPL24",
        "CRPL27",
        "CRPL30",
        "CRPL31",
        "CRPL34",
        "CRPL37",
        "CRPL39",
        "CRPL52",
        "CRPL53",
        "CRPL54",
        "CRPL56",
    }


def test_values_are_not_translated_into_rts_vocabularies(
    march_tape: MappedCollateralTape,
) -> None:
    """A locator asserts field identity, not value conformance.

    Translating ``Senior Secured Loan`` into ``SNDB`` would be a second mapping
    with no source behind it, so values cross over in the report's own words —
    and ``rts_coded_values`` is how a consumer learns that before comparing.
    """
    assert march_tape.source_kind.rts_coded_values is False
    assert {row["debt_type"] for row in march_tape.rows} <= {"Loan", "Bond"}
    seniorities = {
        row["seniority"] for row in march_tape.rows if row["seniority"] is not None
    }
    assert seniorities
    assert all(value not in {"SNDB", "MZZD", "JUND", "SBOD"} for value in seniorities)


# ===========================================================================
# 3. Absent is not zero
# ===========================================================================


def test_no_declared_absent_column_appears_on_any_row(
    march_tape: MappedCollateralTape,
) -> None:
    """The #451 bug, generalised: a missing key, never a zero.

    A present-but-zero ``default_amount`` is indistinguishable from a clean
    pool; a missing key is not.
    """
    absent = {entry.canonical_column for entry in ABSENT_COLUMNS}
    assert absent.isdisjoint(march_tape.columns)
    for row in march_tape.rows:
        assert absent.isdisjoint(row)


@pytest.mark.parametrize(
    "column",
    [
        "arrears_balance",
        "days_in_arrears",
        "account_status",
        "default_amount",
        "cumulative_recoveries",
        "original_balance",
        "basel_segment",
    ],
)
def test_the_named_unpublished_fields_are_declared_absent(column: str) -> None:
    """Every field the issue named must be *declared* absent, not merely missing."""
    entry = next(
        (c for c in ABSENT_COLUMNS if c.canonical_column == column), None
    )
    assert entry is not None, f"{column} is unpublished but not declared absent"
    assert len(entry.reason) >= 20


def test_the_account_status_reason_does_not_overclaim() -> None:
    """A not-applicable reason is an assertion about the world (#457).

    "The source states no performance status" is true. "No asset is defaulted"
    would be a claim about the pool that this source cannot support.
    """
    entry = next(c for c in ABSENT_COLUMNS if c.canonical_column == "account_status")
    assert "not that no asset is defaulted" in entry.reason


def test_a_column_the_source_publishes_but_a_row_lacks_is_null_not_missing(
    march_tape: MappedCollateralTape,
) -> None:
    """Two different absences, kept distinct.

    ``seniority`` is published only on the S&P CCC page, so a non-CCC asset has
    a present key with ``None``. That is not the same as ``account_status``,
    which the source never publishes for anyone.
    """
    assert "seniority" in march_tape.columns
    assert all("seniority" in row for row in march_tape.rows)
    assert any(row["seniority"] is None for row in march_tape.rows)
    assert any(row["seniority"] is not None for row in march_tape.rows)


# ===========================================================================
# 4. Nothing describes this tape as filed
# ===========================================================================


def test_source_kind_is_required_with_no_default() -> None:
    """A tape whose origin was never stated is the failure this module prevents."""
    with pytest.raises(ValidationError):
        MappedCollateralTape(
            source_document=_DOCUMENT,
            annex_id="annex_4",
            annex_label="Annex 4 (Corporate)",
            period_label="2025-03",
        )


def test_the_mapped_tape_says_it_is_derived(march_tape: MappedCollateralTape) -> None:
    assert march_tape.source_kind is TapeSourceKind.DERIVED_FROM_INVESTOR_REPORT
    assert march_tape.is_regulatory_filing is False
    assert "NOT filed" in march_tape.disclosure


def test_no_envelope_surface_implies_a_regulatory_filing(
    march: CollateralSchedule,
) -> None:
    """A reader of only the governance envelope must not mistake this for a filing.

    Asserts the retracted wording is absent rather than merely that the correct
    wording appears somewhere (#457).
    """
    result = map_schedule_result(march, source_document=_DOCUMENT)
    assert result.confidence == 1.0
    assert result.audit_entry.primitive_name == "collateral_tape_mapping"

    lead = result.citations[0].excerpt
    assert "NOT filed" in lead
    assert "Article 7(1)(a)" in lead

    # Every mention of filing anywhere in the envelope must be a *denial* of one.
    for citation in result.citations:
        for sentence in citation.excerpt.split("."):
            if "filed" in sentence.lower():
                assert "not filed" in sentence.lower(), sentence

    # And no per-field provenance excerpt may assert the tape was filed either.
    for entry in result.output.provenance.values():
        assert entry.citation is not None
        assert "filed" not in entry.citation.excerpt.lower()


# ===========================================================================
# The tape as a whole, across every committed period
# ===========================================================================


@pytest.mark.parametrize(("period_label", "filename"), _PERIODS)
def test_every_period_maps_to_the_same_honest_column_set(
    period_label: str, filename: str
) -> None:
    """All three renderings the trustee ships must produce one stable tape shape."""
    schedule = _schedule(filename, period_label)
    assert reconcile_schedule(schedule).ok

    tape = map_schedule(schedule, source_document=_DOCUMENT)
    assert tape.columns == tuple(m.canonical_column for m in SCHEDULE_FIELD_MAP)
    assert len(tape.rows) == len(schedule.assets)
    assert tape.unmapped_values == ()
    assert tape.reporting_date is not None
    # ISO 8601, normalised from the report's DD/MM/YYYY.
    assert tape.reporting_date[4] == "-" and len(tape.reporting_date) == 10
    assert tape.is_regulatory_filing is False


def test_the_mapped_tape_cannot_be_auto_detected_as_annex_4(
    march_tape: MappedCollateralTape,
) -> None:
    """A named decision for #471, not a surprise it discovers during ingestion.

    Annex 4's entire detection signature is ``enterprise_size``, which no
    trustee report publishes. So resolution must be against a *stated* annex —
    which is why the tape carries ``annex_id`` rather than expecting a sniff.
    If detection is ever widened, this test is where that choice surfaces.
    """
    assert ANNEX_REGISTRY.detect(set(march_tape.columns)) is None
    assert march_tape.annex_id == "annex_4"

    entry = next(c for c in ABSENT_COLUMNS if c.canonical_column == "enterprise_size")
    assert "detection signature" in entry.reason


def test_march_maps_every_reconciled_asset(march_tape: MappedCollateralTape) -> None:
    """196 assets / EUR 411,342,140.14 — #469's corrected March baseline."""
    assert len(march_tape.rows) == 196
    total = sum(row["current_balance"] for row in march_tape.rows)
    assert total == Decimal("411342140.14")


def test_managed_by_clo_is_sourced_from_the_document_not_a_column(
    march_tape: MappedCollateralTape,
) -> None:
    """True by construction, and its provenance says so rather than implying a column."""
    assert all(row["managed_by_clo"] is True for row in march_tape.rows)
    excerpt = march_tape.provenance["managed_by_clo"].citation.excerpt
    assert "document-level fact" in excerpt
