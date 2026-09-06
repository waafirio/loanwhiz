"""Resolve a parsed trustee-report collateral schedule onto canonical annex columns.

:mod:`loanwhiz.primitives.collateral_schedule_parser` turns a monthly CLO trustee
report into typed :class:`CollateralAsset` rows that reconcile to the report's
own stated totals. This module is the next step and the harder one: it decides,
for each of those fields, **which canonical Annex 4 (Corporate) column it is**
and **whether it is entitled to a regulatory field code**.

The distinction the whole module turns on
-----------------------------------------
**A locator asserts field identity, not value conformance.** ``CRPL24`` is "type
of debt instrument"; the report's ``Loan``/``Bond`` column is that datum, so the
code is genuine even though ``Loan`` is not one of the RTS's own enum values.
``CRPL14`` is not "industry" — it is *a NACE code* (Reg. (EC) No 1893/2006), and
the scheme is part of the field's definition, so an S&P industry name is a
different datum however closely the two read.

Applying that line to this source moves three of the correspondences a reader
would assume, and each is a trap worth naming:

- **``CRPL14`` is NACE, ``CRPL10`` is NUTS-3.** The report gives S&P/Fitch
  industry names and a country. Both resolve onto code-less extension columns.
- **``CRPL41`` is a market *value*; the report states a *price*.** Its "Market
  Value" column reads ``99.72`` against a par balance of ``3,000,000.00`` — a
  price per 100 of par, as the same report's Assets Sold page confirms by
  printing Par ``500,000.00`` / Price ``100.00`` / Cost ``500,000.00``. Putting
  ``99.72`` in a column named ``market_value`` is wrong by four orders of
  magnitude before provenance is even considered, so the price lands on
  ``market_price_pct`` and ``market_value`` is declared **absent**, naming the
  derivation (``current_balance x price / 100``) nobody here applies.
- **``CRPL4`` is an obligor *identifier*; the report gives a *name*.** A name is
  a different datum, and the RTS identifier is explicitly not the obligor's real
  name.

Values are never translated
---------------------------
Where a code *is* genuine, the value still crosses over in the source document's
own words — ``Senior Secured Loan``, not ``SNDB``. Translating into RTS
vocabularies would be a second mapping table with no source behind it, and a
wrong one is undetectable downstream. :attr:`TapeSourceKind.rts_coded_values` is
how a consumer learns this before comparing against an RTS code.

Absent is not zero
------------------
Columns the schedule simply does not carry — arrears, default, recoveries,
original balance, Basel segment — emit **no key at all**, and are declared in
:data:`ABSENT_COLUMNS` with a reason. #451 found precisely this bug in precisely
this annex: Annex 4 has no default flag (default is an ``account_status``
value), so a corporate tape with defaulted obligors reported ``default_pct:
0.0``. A missing key degrades honestly where a zero lies.

The contract, and where it is enforced
--------------------------------------
:func:`_validate_mapping` runs at **import**, so a mapping row whose declared
correspondence contradicts the annex table cannot be loaded, let alone shipped.
It guards both directions (#453): an ``APPROXIMATE`` row onto a coded column is
the laundering, and an ``EXACT`` row onto a code-less one is the missing table
entry that fails silently.

Generality
----------
Nothing here names a deal. The mapping keys off the US Bank trustee-report row
shape that :mod:`~loanwhiz.primitives.collateral_schedule_parser` produces, and
``DERIVED_FROM_INVESTOR_REPORT`` is a *channel*, so the next CLO whose tape is
reachable only through its trustee report is a registration, not a new mapper.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from loanwhiz.primitives.base import (
    AuditEntry,
    BaseInput,
    Citation,
    PrimitiveResult,
)
from loanwhiz.primitives.collateral_schedule_parser import (
    CollateralAsset,
    CollateralSchedule,
)

# ``loanwhiz.domain``'s package __init__ cycles with ``loanwhiz.primitives``;
# the imports above resolve it, which is why these come second. Same convention
# as ``tests/test_esma_annex_registry.py``.
from loanwhiz.domain.esma_annex4_corporate import ANNEX4_CORPORATE
from loanwhiz.domain.provenance import FieldProvenance, ProvenanceMap
from loanwhiz.domain.tape_provenance import (
    AbsentColumn,
    Correspondence,
    TapeSourceKind,
    locator_for_correspondence,
)

__all__ = [
    "SCHEDULE_FIELD_MAP",
    "ABSENT_COLUMNS",
    "ScheduleFieldMapping",
    "SourceRef",
    "MappedCollateralTape",
    "map_schedule",
    "map_schedule_result",
]

_PRIMITIVE_NAME = "collateral_tape_mapping"
_PRIMITIVE_VERSION = "0.1.0"
_DETERMINISTIC_CONFIDENCE = 1.0

#: The date format every date in a US Bank trustee report is printed in.
_SOURCE_DATE_FORMAT = "%d/%m/%Y"


# ===========================================================================
# The mapping table
# ===========================================================================


class SourceRef(Enum):
    """Where a mapping row reads its value from."""

    #: A named attribute of one :class:`CollateralAsset`.
    ASSET_ATTR = "asset_attr"

    #: A key of one :class:`CollateralAsset`'s Part III ``flags`` dict.
    ASSET_FLAG = "asset_flag"

    #: A named attribute of the :class:`CollateralSchedule` — the same value on
    #: every row.
    SCHEDULE_ATTR = "schedule_attr"

    #: A fact carried by the document's identity rather than any column. Used
    #: only where the fact is true of the whole schedule by construction, and
    #: the provenance excerpt says so rather than implying a source column.
    DOCUMENT_FACT = "document_fact"


@dataclass(frozen=True)
class ScheduleFieldMapping:
    """One source field's resolution onto a canonical annex column.

    Attributes:
        source: Where the value is read from.
        source_name: The attribute/flag name, or a short label for a
            ``DOCUMENT_FACT``.
        canonical_column: The canonical column this resolves onto. Must exist in
            the annex table — :func:`_validate_mapping` refuses otherwise.
        correspondence: What is being claimed about the fit. ``EXACT`` earns a
            regulatory locator; ``APPROXIMATE`` must land on a code-less column
            and earns none.
        note: Why this correspondence, in one sentence. For an ``APPROXIMATE``
            row this is the *reason no locator applies*, and it is surfaced on
            the provenance entry — an absent locator with no reason beside it is
            indistinguishable from an oversight.
        iso_date: Normalise the source's ``DD/MM/YYYY`` into ISO 8601. A format
            change, not a vocabulary translation.
        constant: The value for a ``DOCUMENT_FACT`` row.
    """

    source: SourceRef
    source_name: str
    canonical_column: str
    correspondence: Correspondence
    note: str
    iso_date: bool = False
    constant: Any = None


#: The correspondence table. One row per emitted column; the *only* place the
#: source-to-annex relation is stated. Adding a column means adding a row here,
#: and the import-time guard decides whether the claim is admissible.
SCHEDULE_FIELD_MAP: tuple[ScheduleFieldMapping, ...] = (
    # --- Exact: the source column is the annex field's datum ----------------
    ScheduleFieldMapping(
        source=SourceRef.ASSET_ATTR,
        source_name="identifier",
        canonical_column="loan_identifier",
        correspondence=Correspondence.EXACT,
        note="The report's Issue/Facility Identifier is the exposure identifier.",
    ),
    ScheduleFieldMapping(
        source=SourceRef.SCHEDULE_ATTR,
        source_name="reporting_date",
        canonical_column="reporting_date",
        correspondence=Correspondence.EXACT,
        note="The report's stated 'As of' data cut-off date.",
        iso_date=True,
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_ATTR,
        source_name="principal_balance",
        canonical_column="current_balance",
        correspondence=Correspondence.EXACT,
        note="The report's Principal Balance is the current outstanding principal.",
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_ATTR,
        source_name="asset_type",
        canonical_column="debt_type",
        correspondence=Correspondence.EXACT,
        note=(
            "Loan/Bond is the type of debt instrument. The value stays the "
            "report's own word rather than an RTS enum member."
        ),
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_ATTR,
        source_name="coupon_type",
        canonical_column="rate_type",
        correspondence=Correspondence.EXACT,
        note="Floating/Fixed is the interest-rate type.",
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_ATTR,
        source_name="current_spread",
        canonical_column="current_interest_rate_margin",
        correspondence=Correspondence.EXACT,
        note="The report's Current Spread is the margin over the reference index.",
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_ATTR,
        source_name="current_coupon",
        canonical_column="current_interest_rate_pct",
        correspondence=Correspondence.EXACT,
        note="The report's Current Coupon is the total gross current interest rate.",
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_ATTR,
        source_name="index_type",
        canonical_column="interest_rate_index",
        correspondence=Correspondence.EXACT,
        note="The report's Index Type is the reference index the rate floats over.",
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_ATTR,
        source_name="maturity_date",
        canonical_column="maturity_date",
        correspondence=Correspondence.EXACT,
        note="The report's Maturity Date, normalised from DD/MM/YYYY to ISO 8601.",
        iso_date=True,
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_ATTR,
        source_name="currency",
        canonical_column="currency",
        correspondence=Correspondence.EXACT,
        note="The report's Currency is the exposure's denomination.",
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_FLAG,
        source_name="pik",
        canonical_column="pik_flag",
        correspondence=Correspondence.EXACT,
        note="Part III's PIK Security flag is the payment-in-kind field.",
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_ATTR,
        source_name="seniority",
        canonical_column="seniority",
        correspondence=Correspondence.EXACT,
        note=(
            "Seniority as the S&P CCC Obligations page states it; null for "
            "assets outside that bucket, which is the only section publishing "
            "it. The value stays the report's own words."
        ),
    ),
    ScheduleFieldMapping(
        source=SourceRef.DOCUMENT_FACT,
        source_name="collateral schedule of a CLO trustee report",
        canonical_column="managed_by_clo",
        correspondence=Correspondence.EXACT,
        note=(
            "True for every asset by construction: this is the CLO's own "
            "collateral schedule, so the fact comes from the document's "
            "identity rather than from a column."
        ),
        constant=True,
    ),
    # --- Approximate: near the annex field, but not the same datum ----------
    ScheduleFieldMapping(
        source=SourceRef.ASSET_ATTR,
        source_name="issuer_name",
        canonical_column="obligor_name",
        correspondence=Correspondence.APPROXIMATE,
        note=(
            "The report states the obligor's name. CRPL4 is an obligor "
            "*identifier*, and the RTS identifier is expressly not the real "
            "name — a different datum, so no locator."
        ),
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_ATTR,
        source_name="facility_name",
        canonical_column="facility_name",
        correspondence=Correspondence.APPROXIMATE,
        note="Annex IV defines no facility-name field; CRPL2 is an identifier.",
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_ATTR,
        source_name="sp_industry",
        canonical_column="sp_industry",
        correspondence=Correspondence.APPROXIMATE,
        note=(
            "S&P's industry classification is a different scheme from NACE. "
            "CRPL14 is specifically a NACE code, so citing it would assert a "
            "regulatory conformance this source does not carry."
        ),
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_ATTR,
        source_name="fitch_industry",
        canonical_column="fitch_industry",
        correspondence=Correspondence.APPROXIMATE,
        note="Fitch's industry classification is likewise not NACE (CRPL14).",
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_ATTR,
        source_name="country",
        canonical_column="obligor_country",
        correspondence=Correspondence.APPROXIMATE,
        note=(
            "The report states a country. CRPL10 is a NUTS-3 sub-national "
            "region, a finer geography than the source has, so no locator."
        ),
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_ATTR,
        source_name="market_value",
        canonical_column="market_price_pct",
        correspondence=Correspondence.APPROXIMATE,
        note=(
            "The report's Market Value column is a price per 100 of par, not "
            "an amount; CRPL41 is a market value. See ABSENT_COLUMNS for the "
            "derivation this module deliberately does not perform."
        ),
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_ATTR,
        source_name="index_floor",
        canonical_column="index_floor",
        correspondence=Correspondence.APPROXIMATE,
        note="Annex IV defines no index-floor field.",
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_ATTR,
        source_name="sp_rating",
        canonical_column="sp_rating",
        correspondence=Correspondence.APPROXIMATE,
        note=(
            "Annex IV defines no rating, score, PD or LGD field anywhere. Null "
            "outside the S&P CCC bucket, the only section publishing it."
        ),
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_FLAG,
        source_name="cov_lite",
        canonical_column="cov_lite_flag",
        correspondence=Correspondence.APPROXIMATE,
        note="The string 'covenant' does not occur in Annex IV; no field exists.",
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_FLAG,
        source_name="dip",
        canonical_column="dip_flag",
        correspondence=Correspondence.APPROXIMATE,
        note="Annex IV defines no debtor-in-possession field.",
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_FLAG,
        source_name="deferring",
        canonical_column="deferring_flag",
        correspondence=Correspondence.APPROXIMATE,
        note=(
            "Annex IV defines no deferring-security field. Distinct from "
            "CRPL31 payment-in-kind, which this report states separately."
        ),
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_FLAG,
        source_name="current_pay",
        canonical_column="current_pay_flag",
        correspondence=Correspondence.APPROXIMATE,
        note="Annex IV defines no current-pay-obligation field.",
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_FLAG,
        source_name="revolving",
        canonical_column="revolving_flag",
        correspondence=Correspondence.APPROXIMATE,
        note=(
            "A separate boolean from CRPL24 debt type, which this report states "
            "independently as Loan/Bond."
        ),
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_FLAG,
        source_name="delayed_drawdown",
        canonical_column="delayed_drawdown_flag",
        correspondence=Correspondence.APPROXIMATE,
        note="Annex IV defines no delayed-drawdown field.",
    ),
    ScheduleFieldMapping(
        source=SourceRef.ASSET_FLAG,
        source_name="bridge",
        canonical_column="bridge_flag",
        correspondence=Correspondence.APPROXIMATE,
        note="Annex IV defines no bridge-loan field.",
    ),
)


#: Canonical columns Annex 4 defines that this source does not supply. Every one
#: emits **no key** on every row, so a consumer reading a missing column knows
#: the source is silent rather than reading a zero as a measurement.
ABSENT_COLUMNS: tuple[AbsentColumn, ...] = (
    AbsentColumn(
        canonical_column="unique_identifier",
        rts_code="CRPL1",
        reason=(
            "The trustee report carries no securitisation-level RTS identifier; "
            "it identifies the deal by name only."
        ),
    ),
    AbsentColumn(
        canonical_column="obligor_identifier",
        rts_code="CRPL4",
        reason=(
            "The report states obligor names, not identifiers. The name is "
            "emitted on the code-less obligor_name column instead."
        ),
    ),
    AbsentColumn(
        canonical_column="province",
        rts_code="CRPL10",
        reason=(
            "The report states a country, not a NUTS-3 region. The country is "
            "emitted on the code-less obligor_country column instead."
        ),
    ),
    AbsentColumn(
        canonical_column="industry_code",
        rts_code="CRPL14",
        reason=(
            "The report states S&P and Fitch industry classifications, neither "
            "of which is a NACE code. Both are emitted on code-less columns."
        ),
    ),
    AbsentColumn(
        canonical_column="basel_segment",
        rts_code="CRPL15",
        reason="The collateral schedule states no obligor Basel III segment.",
    ),
    AbsentColumn(
        canonical_column="enterprise_size",
        rts_code="CRPL16",
        reason=(
            "The collateral schedule states no enterprise size. This is also "
            "Annex 4's whole detection signature, so a tape built from this "
            "source cannot be auto-detected and must be resolved against a "
            "stated annex."
        ),
    ),
    AbsentColumn(
        canonical_column="original_balance",
        rts_code="CRPL38",
        reason=(
            "The schedule states current principal balance only; it publishes "
            "no balance at origination."
        ),
    ),
    AbsentColumn(
        canonical_column="market_value",
        rts_code="CRPL41",
        reason=(
            "The report states a market price per 100 of par, not a value "
            "amount. The amount is derivable as current_balance x "
            "market_price_pct / 100, but this module emits only what the source "
            "states and does not compute it."
        ),
    ),
    AbsentColumn(
        canonical_column="arrears_balance",
        rts_code="CRPL77",
        reason="The collateral schedule states no arrears balance for any asset.",
    ),
    AbsentColumn(
        canonical_column="days_in_arrears",
        rts_code="CRPL78",
        reason="The collateral schedule states no arrears ageing for any asset.",
    ),
    AbsentColumn(
        canonical_column="account_status",
        rts_code="CRPL79",
        reason=(
            "The schedule publishes no performance status. Annex 4 expresses "
            "default as an account_status value, so its absence means default "
            "is unstated by this source — not that no asset is defaulted."
        ),
    ),
    AbsentColumn(
        canonical_column="default_amount",
        rts_code="CRPL81",
        reason="The collateral schedule states no default amount for any asset.",
    ),
    AbsentColumn(
        canonical_column="cumulative_recoveries",
        rts_code="CRPL84",
        reason="The collateral schedule states no recoveries for any asset.",
    ),
)


# ===========================================================================
# The import-time guard
# ===========================================================================


def _validate_mapping(
    mappings: tuple[ScheduleFieldMapping, ...] = SCHEDULE_FIELD_MAP,
    absent: tuple[AbsentColumn, ...] = ABSENT_COLUMNS,
) -> None:
    """Refuse a mapping table whose claims the annex table contradicts.

    Runs at import, so a bad row cannot be loaded — the boundary a violation
    should fail at, rather than surfacing as a wrong field code in a governance
    view three layers away. Every check below is a mistake that would otherwise
    be silent:

    - a duplicate canonical column would make emission order-dependent;
    - a correspondence the annex table disagrees with is delegated to
      :func:`locator_for_correspondence`, which guards **both** directions;
    - a column declared both emitted and absent contradicts itself;
    - an absent column whose stated RTS code is not the annex's code for it is a
      transcription error in the very record a reader would trust.
    """
    seen: set[str] = set()
    for row in mappings:
        if row.canonical_column in seen:
            raise ValueError(
                f"duplicate canonical column {row.canonical_column!r} in "
                "SCHEDULE_FIELD_MAP — two source fields resolving to one column "
                "make the emitted value order-dependent."
            )
        seen.add(row.canonical_column)
        # Raises when the declared correspondence and the annex table disagree.
        locator_for_correspondence(
            ANNEX4_CORPORATE, row.canonical_column, row.correspondence
        )
        if row.source is SourceRef.DOCUMENT_FACT and row.constant is None:
            raise ValueError(
                f"{row.canonical_column!r} is a DOCUMENT_FACT with no constant; "
                "a document fact with no value states nothing."
            )

    for entry in absent:
        if entry.canonical_column in seen:
            raise ValueError(
                f"{entry.canonical_column!r} is declared both emitted and "
                "absent — a column cannot be both."
            )
        record = ANNEX4_CORPORATE.field_for_column(entry.canonical_column)
        if record is None:
            raise ValueError(
                f"absent column {entry.canonical_column!r} is not in the Annex 4 "
                "table; declaring the absence of a column that does not exist "
                "asserts nothing."
            )
        if record.code != entry.rts_code:
            raise ValueError(
                f"absent column {entry.canonical_column!r} declares RTS code "
                f"{entry.rts_code!r} but the annex table says {record.code!r}."
            )


_validate_mapping()


# ===========================================================================
# Output
# ===========================================================================


class MappedCollateralTape(BaseModel):
    """A collateral schedule resolved onto canonical Annex 4 columns.

    Attributes:
        source_kind: What this tape **is**. Required with no default: a tape
            whose origin was never stated is the failure this whole module
            exists to prevent.
        source_document: The document the rows were derived from.
        annex_id / annex_label: The annex the columns resolve through, stated
            rather than sniffed — see the ``enterprise_size`` entry in
            :data:`ABSENT_COLUMNS` for why sniffing cannot work here.
        columns: Emitted canonical columns, in mapping-table order.
        rows: One dict per asset. A key is **absent** when the source publishes
            no such column (see :attr:`absent_columns`); a key is present and
            ``None`` when the source publishes the column but not for this row.
        provenance: Per-column :class:`FieldProvenance`. A column's
            ``citation.page_or_row`` is its regulatory locator, or ``None``
            where none genuinely applies — with the reason in the excerpt.
        absent_columns: Canonical columns this source cannot supply, each with
            a reason. Absent, never zero.
        unmapped_values: Values the mapper could not normalise, counted rather
            than dropped, so a degraded map is visible.
    """

    source_kind: TapeSourceKind = Field(
        ..., description="How this tape reached LoanWhiz. No default, by design."
    )
    source_document: str = Field(..., min_length=1)
    annex_id: str
    annex_label: str
    deal_name: str | None = None
    period_label: str
    reporting_date: str | None = None
    columns: tuple[str, ...] = ()
    rows: list[dict[str, Any]] = Field(default_factory=list)
    provenance: ProvenanceMap = Field(default_factory=dict)
    absent_columns: tuple[AbsentColumn, ...] = ()
    unmapped_values: tuple[str, ...] = ()

    @property
    def disclosure(self) -> str:
        """What this tape is, in one sentence, for any operator-facing surface."""
        return self.source_kind.disclosure

    @property
    def is_regulatory_filing(self) -> bool:
        """Whether this tape is a regulatory disclosure. It is not, when derived."""
        return self.source_kind.is_regulatory_filing

    def locators(self) -> dict[str, str]:
        """Emitted columns that carry a regulatory locator, keyed by column.

        Columns resolving to an extension field are absent from this map — the
        honest answer, and the one that makes a fabricated locator visible.
        """
        return {
            column: entry.citation.page_or_row
            for column, entry in self.provenance.items()
            if entry.citation is not None
            and isinstance(entry.citation.page_or_row, str)
        }


class TapeMappingInput(BaseInput):
    """Governance input record for the envelope wrapper."""

    period_label: str
    source_document: str
    source_kind: TapeSourceKind
    asset_count: int


# ===========================================================================
# Mapping
# ===========================================================================


def _iso_date(raw: str) -> str | None:
    """Normalise a ``DD/MM/YYYY`` source date to ISO 8601, or ``None``.

    A format change, not a vocabulary translation — the datum is unchanged. An
    unparseable date returns ``None`` and is counted by the caller rather than
    passed through: emitting a non-ISO string into a date column would push the
    failure onto whoever parses it next.
    """
    try:
        return datetime.strptime(raw.strip(), _SOURCE_DATE_FORMAT).date().isoformat()
    except (ValueError, AttributeError):
        return None


def _read(
    row: ScheduleFieldMapping,
    asset: CollateralAsset,
    schedule: CollateralSchedule,
) -> Any:
    """Read one mapping row's raw value off the asset, schedule or document."""
    if row.source is SourceRef.ASSET_ATTR:
        return getattr(asset, row.source_name)
    if row.source is SourceRef.ASSET_FLAG:
        # ``flags`` is populated wholesale from Part III or not at all, so a
        # missing key means the row was never read — not a False flag.
        return asset.flags.get(row.source_name)
    if row.source is SourceRef.SCHEDULE_ATTR:
        return getattr(schedule, row.source_name)
    return row.constant


def _provenance_for(row: ScheduleFieldMapping, source_document: str) -> FieldProvenance:
    """Build one column's provenance entry.

    The locator lives in ``citation.page_or_row`` and is ``None`` for an
    approximate correspondence — provenance visibly absent rather than
    fabricated. The excerpt always carries the reason, so an absent locator can
    never be mistaken for an oversight.
    """
    locator = locator_for_correspondence(
        ANNEX4_CORPORATE, row.canonical_column, row.correspondence
    )
    prefix = (
        f"Source: {row.source_name}."
        if row.source is not SourceRef.DOCUMENT_FACT
        else f"Source: {row.source_name} (document-level fact, not a column)."
    )
    excerpt = f"{prefix} {row.note}"
    if locator is None:
        excerpt = (
            f"{excerpt} No ESMA RTS locator: the correspondence is approximate, "
            "so none genuinely applies."
        )
    return FieldProvenance(
        source="report",
        method="deterministic",
        confidence=_DETERMINISTIC_CONFIDENCE,
        citation=Citation(
            document=source_document,
            page_or_row=locator,
            excerpt=excerpt,
        ),
    )


def map_schedule(
    schedule: CollateralSchedule,
    *,
    source_document: str,
    source_kind: TapeSourceKind = TapeSourceKind.DERIVED_FROM_INVESTOR_REPORT,
) -> MappedCollateralTape:
    """Resolve *schedule* onto canonical Annex 4 (Corporate) columns.

    Args:
        schedule: A parsed collateral schedule, already reconciled to its own
            report's stated totals by
            :func:`~loanwhiz.primitives.collateral_schedule_parser.reconcile_schedule`.
        source_document: The trustee report the rows were derived from. Named on
            every citation, so a reader always sees what grounded the value.
        source_kind: What the resulting tape is. Defaults to
            ``DERIVED_FROM_INVESTOR_REPORT`` because a
            :class:`CollateralSchedule` can only have come from one — a filed
            Article 7(1)(a) tape does not arrive through this parser. The value
            is still required and explicit on the output.

    Returns:
        A :class:`MappedCollateralTape`. Its ``rows`` omit a key entirely for
        any column in :data:`ABSENT_COLUMNS`; absence there means the source is
        silent, never that the value is zero.
    """
    columns = tuple(row.canonical_column for row in SCHEDULE_FIELD_MAP)
    provenance: ProvenanceMap = {
        row.canonical_column: _provenance_for(row, source_document)
        for row in SCHEDULE_FIELD_MAP
    }

    rows: list[dict[str, Any]] = []
    unmapped: list[str] = []
    for asset in schedule.assets:
        emitted: dict[str, Any] = {}
        for mapping in SCHEDULE_FIELD_MAP:
            value = _read(mapping, asset, schedule)
            if value is not None and mapping.iso_date:
                normalised = _iso_date(str(value))
                if normalised is None and len(unmapped) < 25:
                    unmapped.append(
                        f"{mapping.canonical_column}: unparseable date "
                        f"{value!r} for {asset.identifier}"
                    )
                value = normalised
            emitted[mapping.canonical_column] = value
        rows.append(emitted)

    reporting_date = _iso_date(schedule.reporting_date) if schedule.reporting_date else None

    return MappedCollateralTape(
        source_kind=source_kind,
        source_document=source_document,
        annex_id=ANNEX4_CORPORATE.annex_id,
        annex_label=ANNEX4_CORPORATE.label,
        deal_name=schedule.deal_name,
        period_label=schedule.period_label,
        reporting_date=reporting_date,
        columns=columns,
        rows=rows,
        provenance=provenance,
        absent_columns=ABSENT_COLUMNS,
        unmapped_values=tuple(unmapped),
    )


def map_schedule_result(
    schedule: CollateralSchedule,
    *,
    source_document: str,
    source_kind: TapeSourceKind = TapeSourceKind.DERIVED_FROM_INVESTOR_REPORT,
) -> PrimitiveResult[MappedCollateralTape]:
    """:func:`map_schedule` wrapped in the governance envelope.

    The leading citation states what the tape **is** — not a regulatory filing,
    when derived — so a consumer reading only the envelope's citations cannot
    miss the distinction. The remaining citations are the emitted locators, one
    per column that genuinely has one.
    """
    started = time.perf_counter()
    tape = map_schedule(
        schedule, source_document=source_document, source_kind=source_kind
    )
    duration_ms = (time.perf_counter() - started) * 1000.0

    citations = [
        Citation(
            document=source_document,
            page_or_row=f"{tape.annex_label} · {tape.period_label}",
            excerpt=(
                f"{len(tape.rows)} assets resolved onto {len(tape.columns)} "
                f"canonical columns. {tape.disclosure}"
            ),
        )
    ]
    citations.extend(
        Citation(document=source_document, page_or_row=locator, excerpt=column)
        for column, locator in sorted(tape.locators().items())
    )

    return PrimitiveResult[MappedCollateralTape](
        output=tape,
        confidence=_DETERMINISTIC_CONFIDENCE,
        citations=citations,
        audit_entry=AuditEntry.now(
            primitive_name=_PRIMITIVE_NAME,
            version=_PRIMITIVE_VERSION,
            input_hash=TapeMappingInput(
                period_label=schedule.period_label,
                source_document=source_document,
                source_kind=source_kind,
                asset_count=len(schedule.assets),
            ).input_hash(),
            duration_ms=duration_ms,
        ),
    )
