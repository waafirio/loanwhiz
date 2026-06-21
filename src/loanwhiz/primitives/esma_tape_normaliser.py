"""ESMA loan-level tape normaliser primitive.

Loads a loan tape from a URL (HuggingFace or local) in either **CSV or
parquet** format — the loader is format-agnostic and dispatches on the URL
extension — auto-detects the ESMA Annex schema (Annex 2 RMBS, Annex 5 Auto,
Annex 8 SME, etc.), and computes a comprehensive set of pool analytics:

- Balance-weighted averages: coupon, LTV, seasoning, remaining term.
- Multi-bucket arrears breakdown: current, <29 days, 180+ days, default.
- Categorical distributions: EPC, rate type, property type, geographic.

Implements the ``Primitive[EsmaTapeInput, EsmaTapeOutput]`` contract so it can
be composed with other LoanWhiz primitives by the LangGraph agent.

Canonical column resolution (ESMA Annex 2 RREL field codes)
-----------------------------------------------------------
Tape column names vary across issuers and vintages, but each maps to a stable
ESMA RTS Annex 2 **RREL field code**. The canonical code → field → column table
lives in :mod:`loanwhiz.domain.esma_annex2`; the normaliser resolves each tape's
columns onto LoanWhiz canonical names through that single source
(:func:`~loanwhiz.domain.esma_annex2.canonical_column_for`), so an issuer that
spells a column ``outstanding_balance`` still resolves to ``current_balance``.
A column already in canonical form resolves to itself, so the validated Green
Lion 2026-1 behaviour is preserved byte-for-byte. The output ``Citation`` is
anchored to the matched RREL codes for governance traceability.

Canonical names the pool analytics key on (each backed by an Annex 2 RREL code
in ``esma_annex2.ANNEX2_RMBS_FIELDS``):

- ``current_balance`` (RREL18), ``current_interest_rate_pct`` (RREL22),
  ``remaining_term_months`` (RREL30), ``seasoning_months`` (RREL31),
  ``cltomv_current`` current LTV (RREL40), ``arrears_bucket`` (RREL64),
  ``default_crr_flag`` (RREL66), ``epc_label`` (RREL17), ``rate_type`` (RREL24),
  ``property_type`` (RREL16), ``province`` region (RREL15),
  ``transaction_name`` (RREL3), ``reporting_date`` (RREL5).
"""

from __future__ import annotations

import time
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from loanwhiz.domain.esma_annex2 import canonical_column_for, code_for_column
from loanwhiz.primitives.base import (
    AuditEntry,
    BaseInput,
    Citation,
    Primitive,
    PrimitiveResult,
)
from loanwhiz.primitives.registry import register_primitive

# ---------------------------------------------------------------------------
# Annex detection constants
# ---------------------------------------------------------------------------

# Sentinel columns used to identify ESMA Annex schemas.
# The sets are ordered from most-specific to least-specific so we test in a
# single pass.
_ANNEX_SIGNATURES: list[tuple[set[str], str]] = [
    ({"epc_label", "property_type"}, "Annex 2 (RMBS)"),
    ({"vehicle_type"}, "Annex 5 (Auto)"),
    ({"company_size"}, "Annex 8 (SME)"),
]

_UNKNOWN_ANNEX = "Unknown ABS"

# Confidence deductions (see module docstring).
_DEDUCT_DATE_OVERRIDE = 0.1
_DEDUCT_MISSING_BALANCE = 0.1
_DEDUCT_UNKNOWN_ANNEX = 0.2

# Minimum fraction of missing balance values that triggers the quality deduction.
_MISSING_BALANCE_THRESHOLD = 0.05

# URL/path suffixes that route the loader to ``pd.read_parquet`` rather than
# ``pd.read_csv``. Matched case-insensitively against the URL path component
# (query string stripped).
_PARQUET_SUFFIXES = (".parquet", ".pq")

# Provenance label for the loaded frame. LoanWhiz's canonical tape ingestion
# path is the **direct read** — a loan tape is loaded straight from its source
# URL (HuggingFace CSV/parquet, local ``file://``) via pandas. ``"direct"`` is
# the only ingestion path, so it is the only provenance value; it is surfaced on
# ``EsmaTapeOutput.data_source`` so the governance view records honestly where
# each tape came from. (The field is retained as the provenance contract even
# though it is currently single-valued; an additional ingestion source would
# extend it here.)
DATA_SOURCE_DIRECT = "direct"


def _load_tape(file_url: str, period: str | None) -> tuple[pd.DataFrame, str]:
    """Load a loan tape from *file_url* as a DataFrame, with its provenance.

    Ingestion
    ~~~~~~~~~
    The tape is read **directly** from *file_url* — the canonical LoanWhiz tape
    ingestion path — and tagged ``data_source="direct"``. The format is detected
    from the URL/path extension: a ``.parquet``/``.pq`` suffix is read via
    :func:`pandas.read_parquet`; anything else via :func:`pandas.read_csv` with
    ``low_memory=False``. This covers HuggingFace CSV/parquet tapes and local
    ``file://`` paths — the sources every LoanWhiz deal actually uses.

    Combined multi-month tapes (e.g. ``Overall_2024_2025_all_months.parquet``)
    carry many ``reporting_date`` values in one file. Since the LoanWhiz model
    is one-tape-per-period, *period* selects a single reporting cut-off: when
    set and a ``reporting_date`` column is present, the frame is filtered to
    rows whose ``reporting_date`` (string-cast) equals *period*. Selecting a
    period absent from the file is an error.

    Parameters
    ----------
    file_url:
        URL or path to the tape (CSV or parquet).
    period:
        Optional reporting-date selector for combined multi-month tapes. When
        ``None`` the whole frame is returned unchanged (the historical path).

    Returns
    -------
    (pandas.DataFrame, str)
        The loaded tape (sliced to *period* when requested) and its provenance
        label — always :data:`DATA_SOURCE_DIRECT`.

    Raises
    ------
    ValueError
        When *period* is set but matches no rows in the tape.
    """
    # Strip any query string before matching the extension so signed URLs
    # (``...parquet?token=...``) still route to the parquet reader.
    path = file_url.split("?", 1)[0]
    if path.lower().endswith(_PARQUET_SUFFIXES):
        df = pd.read_parquet(file_url)
    else:
        df = pd.read_csv(file_url, low_memory=False)
    data_source = DATA_SOURCE_DIRECT

    if period is not None:
        col_map = {c.lower(): c for c in df.columns}
        if "reporting_date" in col_map:
            rd_col = col_map["reporting_date"]
            mask = df[rd_col].astype(str) == period
            df = df[mask]
            if df.empty:
                raise ValueError(
                    f"period={period!r} matched no rows in tape {file_url!r}; "
                    "no such reporting_date in the (combined) file."
                )

    return df, data_source


# ---------------------------------------------------------------------------
# I/O models
# ---------------------------------------------------------------------------


class EsmaTapeInput(BaseInput):
    """Input schema for the ESMA tape normaliser.

    Attributes:
        file_url:        Direct URL to the ESMA loan tape, in CSV or parquet
                         format (HuggingFace or local ``file://`` path). The
                         loader dispatches on the URL extension.
        reporting_date:  Override for the reporting date (ISO 8601, e.g.
                         ``"2026-04-30"``). Only needed when the tape does
                         not carry a ``reporting_date`` column, or when you
                         want to pin a different cut-off. This is a *label*
                         override — it does NOT filter rows; use ``period``
                         to slice a combined file.
        period:          Reporting-date selector for a **combined multi-month**
                         tape (e.g. ``Overall_2024_2025_all_months.parquet``).
                         When set, the loaded frame is filtered to rows whose
                         ``reporting_date`` equals this value, and the output
                         reporting_date is pinned to it — yielding the
                         per-period slice the one-tape-per-period model
                         expects. ``None`` (default) leaves the frame whole.
    """

    file_url: str = Field(
        ..., description="URL or path to the ESMA loan tape (CSV or parquet)."
    )
    reporting_date: str | None = Field(
        default=None,
        description=(
            "Reporting date label override (ISO 8601). If None, the value is "
            "read from the tape's ``reporting_date`` column (first non-null "
            "value). Does not filter rows — use ``period`` for that."
        ),
    )
    period: str | None = Field(
        default=None,
        description=(
            "Reporting-date selector for a combined multi-month tape. When "
            "set, rows are filtered to ``reporting_date == period`` and the "
            "output reporting_date is pinned to it. None reads the whole file."
        ),
    )


class EsmaTapeOutput(BaseModel):
    """Normalised pool analytics derived from an ESMA loan tape.

    Attributes:
        reporting_date:        Cut-off date for the tape (ISO 8601 string).
        asset_class:           Inferred asset class — mirrors ``annex_detected``.
        transaction_name:      Deal name extracted from the tape's
                               ``transaction_name`` column, or ``None``.
        loan_count:            Number of loans in the tape.
        pool_balance_eur:      Sum of ``current_balance`` across all loans.
        pool_stats:            Balance-weighted pool averages:
                               ``wtd_coupon_pct``, ``wtd_ltv``,
                               ``wtd_seasoning``, ``wtd_remaining_term``.
        arrears_breakdown:     Percentage of loans in each arrears bucket:
                               ``current_pct``, ``arrears_1_2m_pct``,
                               ``arrears_180d_plus_pct``, ``default_pct``.
        epc_breakdown:         Percentage distribution by EPC label, or
                               ``None`` when the field is absent.
        rate_type_breakdown:   Percentage distribution by rate type (Fixed /
                               Floating), or ``None``.
        property_type_breakdown: Percentage distribution by property type, or
                               ``None``.
        geographic_breakdown:  Percentage distribution by region/province, or
                               ``None``.
        annex_detected:        Human-readable Annex label, e.g.
                               ``"Annex 2 (RMBS)"``.
        data_source:           Ingestion provenance — always ``"direct"``: the
                               tape was read directly from its source URL
                               (HuggingFace CSV/parquet, local file), LoanWhiz's
                               canonical tape ingestion path. Surfaced so the
                               governance view can show honest data provenance.
    """

    reporting_date: str
    asset_class: str
    transaction_name: str | None
    loan_count: int
    pool_balance_eur: float
    pool_stats: dict[str, float]
    arrears_breakdown: dict[str, float]
    epc_breakdown: dict[str, float] | None
    rate_type_breakdown: dict[str, float] | None
    property_type_breakdown: dict[str, float] | None
    geographic_breakdown: dict[str, float] | None
    annex_detected: str
    data_source: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _detect_annex(columns: set[str]) -> tuple[str, bool]:
    """Return ``(annex_label, certain)`` for the given column set.

    Parameters
    ----------
    columns:
        Lower-cased column names present in the CSV.

    Returns
    -------
    (annex_label, certain)
        ``certain`` is ``True`` when a signature matched, ``False`` for the
        unknown-ABS fallback.
    """
    for required_cols, label in _ANNEX_SIGNATURES:
        if required_cols.issubset(columns):
            return label, True
    return _UNKNOWN_ANNEX, False


def _pct_distribution(series: pd.Series) -> dict[str, float]:
    """Return a dict mapping each unique value to its percentage (0–100)."""
    total = len(series.dropna())
    if total == 0:
        return {}
    counts = series.value_counts(dropna=True)
    return {str(k): round(float(v) / total * 100, 4) for k, v in counts.items()}


def _default_mask(df: pd.DataFrame) -> pd.Series:
    """Boolean mask of defaulted loans (``default_crr_flag == "Y"``).

    Falls back to an all-``False`` mask when the ``default_crr_flag`` column is
    absent. Expects lower-cased column names (the ``df_lower`` frame).
    """
    if "default_crr_flag" in df.columns:
        return df["default_crr_flag"].astype(str).str.upper() == "Y"
    return pd.Series([False] * len(df), index=df.index)


def _arrears_180d_mask(df: pd.DataFrame) -> pd.Series:
    """Boolean mask of loans 180+ days in arrears (``arrears_bucket == "180+d"``).

    Falls back to an all-``False`` mask when the ``arrears_bucket`` column is
    absent. Expects lower-cased column names.
    """
    if "arrears_bucket" in df.columns:
        return df["arrears_bucket"] == "180+d"
    return pd.Series([False] * len(df), index=df.index)


def non_performing_mask(df: pd.DataFrame) -> pd.Series:
    """Boolean mask of **non-performing** loans for a lower-cased tape frame.

    A loan is non-performing when it is in default (``default_crr_flag == "Y"``)
    **or** 180+ days in arrears (``arrears_bucket == "180+d"``) — the loans that
    do not pay interest in the period. This is the single shared definition the
    arrears breakdown and the collections engine's arrears-aware interest base
    both read from, so the two never drift.

    Parameters
    ----------
    df:
        Tape DataFrame with **lower-cased** column names. Missing arrears/default
        columns degrade to "all performing" (an empty non-performing set).

    Returns
    -------
    pandas.Series
        Boolean mask aligned to ``df.index``; ``True`` = non-performing.
    """
    return _default_mask(df) | _arrears_180d_mask(df)


def performing_mask(df: pd.DataFrame) -> pd.Series:
    """Boolean mask of **performing** loans — the complement of
    :func:`non_performing_mask`. Expects lower-cased column names."""
    return ~non_performing_mask(df)


def _extract_arrears(df: pd.DataFrame) -> dict[str, float]:
    """Compute multi-bucket arrears breakdown as percentages.

    Buckets are mutually exclusive; priority order is:

    1. ``default_pct``          — ``default_crr_flag == "Y"`` (highest priority)
    2. ``arrears_180d_plus_pct`` — ``arrears_bucket == "180+d"`` AND not in default
    3. ``arrears_1_2m_pct``     — ``arrears_bucket == "<29d"`` AND not in default
    4. ``current_pct``          — all remaining loans

    All as a percentage of total loan count; the four buckets sum to 100.
    """
    n = len(df)
    if n == 0:
        return {
            "current_pct": 0.0,
            "arrears_1_2m_pct": 0.0,
            "arrears_180d_plus_pct": 0.0,
            "default_pct": 0.0,
        }

    has_arrears_col = "arrears_bucket" in df.columns

    # Priority 1: defaulted (highest) — shared with the collections engine.
    default_mask = _default_mask(df)
    # Priority 2: 180+ days arrears (not also flagged as default)
    arrears_180d_mask = _arrears_180d_mask(df) & ~default_mask
    # Priority 3: <29 days arrears (not also flagged as default or 180+d)
    arrears_1_2m_mask = (
        (df["arrears_bucket"] == "<29d") & ~default_mask
        if has_arrears_col
        else pd.Series([False] * n, index=df.index)
    )
    # Priority 4: current (everything else)
    current_mask = ~default_mask & ~arrears_1_2m_mask & ~arrears_180d_mask

    def pct(mask: pd.Series) -> float:
        return round(float(mask.sum()) / n * 100, 4)

    return {
        "current_pct": pct(current_mask),
        "arrears_1_2m_pct": pct(arrears_1_2m_mask),
        "arrears_180d_plus_pct": pct(arrears_180d_mask),
        "default_pct": pct(default_mask),
    }


def _optional_breakdown(df: pd.DataFrame, col: str) -> dict[str, float] | None:
    """Return percentage distribution for *col*, or ``None`` when absent."""
    if col not in df.columns:
        return None
    return _pct_distribution(df[col])


def _resolve_columns(columns: list[str]) -> dict[str, str]:
    """Build a ``canonical-column → original-column`` map for a tape.

    Each tape column name is looked up in the canonical **ESMA Annex 2**
    field-code table (:func:`loanwhiz.domain.esma_annex2.canonical_column_for`).
    When a column is a known canonical name *or* a registered issuer/vintage
    synonym, its canonical name maps to the original column header — so the
    normaliser's downstream lookups (which use canonical names like
    ``"current_balance"`` / ``"cltomv_current"``) still find the data even when
    the tape spells the column differently across issuers.

    A column already in canonical form maps to itself (the historical
    Green-Lion behaviour is preserved byte-for-byte: every Green-Lion column is
    its own canonical name, so this map is the identity on that tape). The first
    occurrence of a canonical target wins, so a synonym never overrides a column
    that is already present under its canonical name.

    Returns a dict keyed by **lower-cased canonical column name**; columns not
    in the Annex 2 table are simply absent (callers keep their existing
    fallbacks).
    """
    resolved: dict[str, str] = {}
    for orig in columns:
        canonical = canonical_column_for(orig)
        if canonical is None:
            continue
        # A column present under its own canonical name always wins; a synonym
        # only fills a canonical slot that nothing else has claimed.
        if canonical == orig.lower():
            resolved[canonical] = orig
        else:
            resolved.setdefault(canonical, orig)
    return resolved


# ---------------------------------------------------------------------------
# Primitive
# ---------------------------------------------------------------------------


@register_primitive(
    name="esma_tape_normaliser",
    version="0.1.0",
    description="Normalise an ESMA loan-level tape CSV into pool analytics with Annex auto-detection.",
    tags=["data", "esma", "tape"],
)
class EsmaTapeNormaliser(Primitive[EsmaTapeInput, EsmaTapeOutput]):
    """Normalise ESMA loan-level tape CSV into pool analytics.

    Accepts a CSV URL, detects the ESMA Annex schema, computes balance-
    weighted averages and categorical distributions, and returns a typed
    ``PrimitiveResult[EsmaTapeOutput]`` with a confidence score and source
    citation.
    """

    name = "esma_tape_normaliser"
    version = "0.1.0"
    description = (
        "Normalise an ESMA loan-level tape CSV into pool analytics with Annex auto-detection."
    )

    def execute(self, input: EsmaTapeInput) -> PrimitiveResult[EsmaTapeOutput]:  # type: ignore[override]
        """Run pool analytics on the ESMA loan tape at ``input.file_url``.

        Parameters
        ----------
        input:
            Validated ``EsmaTapeInput`` with ``file_url`` and optional
            ``reporting_date`` override.

        Returns
        -------
        PrimitiveResult[EsmaTapeOutput]
            Typed output with confidence score, one citation, and an audit
            entry.
        """
        t0 = time.perf_counter()
        input_hash = input.input_hash()

        # -----------------------------------------------------------------
        # Load tape
        # -----------------------------------------------------------------
        df, data_source = _load_tape(input.file_url, input.period)
        cols: set[str] = set(df.columns)

        # -----------------------------------------------------------------
        # Annex detection
        # -----------------------------------------------------------------
        cols_lower = {c.lower() for c in df.columns}
        # Map original -> lower for column lookup
        col_map = {c.lower(): c for c in df.columns}
        # Overlay ESMA Annex 2 canonical-name resolution so issuer/vintage
        # column-name variants resolve onto the canonical names the lookups
        # below expect (e.g. ``outstanding_balance`` → ``current_balance``).
        # ``setdefault`` keeps a column already present under its canonical name
        # authoritative — so Green-Lion tapes (already canonical) are unchanged.
        for canonical, orig in _resolve_columns(list(df.columns)).items():
            col_map.setdefault(canonical, orig)
            cols_lower.add(canonical)

        annex_detected, annex_certain = _detect_annex(cols_lower)

        # -----------------------------------------------------------------
        # Reporting date
        # -----------------------------------------------------------------
        date_overridden = False
        reporting_date: str

        if input.reporting_date is not None:
            reporting_date = input.reporting_date
            date_overridden = True
        elif input.period is not None:
            # The frame was sliced to exactly this reporting period by
            # ``_load_tape``; pin it as the output cut-off (not a low-confidence
            # override — it is the authoritative period of this slice).
            reporting_date = input.period
        elif "reporting_date" in cols_lower:
            orig_col = col_map["reporting_date"]
            non_null = df[orig_col].dropna()
            if not non_null.empty:
                reporting_date = str(non_null.iloc[0])
            else:
                reporting_date = "unknown"
                date_overridden = True  # effectively overridden to sentinel
        else:
            reporting_date = "unknown"
            date_overridden = True

        # -----------------------------------------------------------------
        # Loan count and pool balance
        # -----------------------------------------------------------------
        loan_count = len(df)

        balance_col: str | None = col_map.get("current_balance")
        if balance_col is not None:
            balance_series = pd.to_numeric(df[balance_col], errors="coerce")
            missing_balance_frac = balance_series.isna().mean()
            pool_balance_eur = float(balance_series.sum(skipna=True))
        else:
            balance_series = pd.Series(dtype=float)
            missing_balance_frac = 1.0
            pool_balance_eur = 0.0

        # -----------------------------------------------------------------
        # Transaction name
        # -----------------------------------------------------------------
        transaction_name: str | None = None
        if "transaction_name" in cols_lower:
            orig_tn = col_map["transaction_name"]
            non_null_tn = df[orig_tn].dropna()
            if not non_null_tn.empty:
                transaction_name = str(non_null_tn.iloc[0])

        # -----------------------------------------------------------------
        # Balance-weighted pool stats
        # -----------------------------------------------------------------
        pool_stats: dict[str, float] = {}

        def _wa(value_col_lower: str) -> float | None:
            if value_col_lower not in cols_lower or balance_col is None:
                return None
            orig_vc = col_map[value_col_lower]
            num = pd.to_numeric(df[orig_vc], errors="coerce")
            sub_df = pd.DataFrame({"v": num, "w": balance_series}).dropna()
            if sub_df.empty or sub_df["w"].sum() == 0:
                return None
            return float((sub_df["v"] * sub_df["w"]).sum() / sub_df["w"].sum())

        for stat_key, col_lower in [
            ("wtd_coupon_pct", "current_interest_rate_pct"),
            ("wtd_ltv", "cltomv_current"),
            ("wtd_seasoning", "seasoning_months"),
            ("wtd_remaining_term", "remaining_term_months"),
        ]:
            val = _wa(col_lower)
            if val is not None:
                pool_stats[stat_key] = round(val, 4)

        # -----------------------------------------------------------------
        # Arrears breakdown — build normalised df with lower-case cols
        # -----------------------------------------------------------------
        # Lower-case all columns, then rename any ESMA Annex 2 synonym columns
        # onto their canonical names so the arrears / categorical extractors
        # (which key on canonical names like ``arrears_bucket`` /
        # ``default_crr_flag`` / ``epc_label``) resolve issuer-variant spellings.
        # ``setdefault`` semantics in ``_resolve_columns`` guarantee a column
        # already present under its canonical name is never overwritten, so a
        # Green-Lion tape is renamed to itself (identity).
        lower_rename = {c: c.lower() for c in df.columns}
        for canonical, orig in _resolve_columns(list(df.columns)).items():
            lower_rename[orig] = canonical
        df_lower = df.rename(columns=lower_rename)
        arrears_breakdown = _extract_arrears(df_lower)

        # -----------------------------------------------------------------
        # Categorical distributions
        # -----------------------------------------------------------------
        epc_breakdown = _optional_breakdown(df_lower, "epc_label")
        rate_type_breakdown = _optional_breakdown(df_lower, "rate_type")
        property_type_breakdown = _optional_breakdown(df_lower, "property_type")
        geographic_breakdown = _optional_breakdown(df_lower, "province")

        # -----------------------------------------------------------------
        # Asset class label
        # -----------------------------------------------------------------
        asset_class = _annex_to_asset_class(annex_detected)

        # -----------------------------------------------------------------
        # Confidence scoring
        # -----------------------------------------------------------------
        confidence = 1.0
        if date_overridden:
            confidence -= _DEDUCT_DATE_OVERRIDE
        if missing_balance_frac > _MISSING_BALANCE_THRESHOLD:
            confidence -= _DEDUCT_MISSING_BALANCE
        if not annex_certain:
            confidence -= _DEDUCT_UNKNOWN_ANNEX
        confidence = max(0.0, round(confidence, 4))

        # -----------------------------------------------------------------
        # Citation — anchored to ESMA RTS Annex 2 RREL field codes
        # -----------------------------------------------------------------
        # Surface which regulatory Annex 2 fields the loaded tape's columns map
        # to, so the governance view can trace pool analytics back to the RREL
        # codes they came from (the locator mechanism fixed in the schema design,
        # decision D8). Codes are de-duplicated and sorted for a stable string.
        matched_codes = sorted(
            {
                code
                for c in df.columns
                if (code := code_for_column(c)) is not None
            }
        )
        annex2_anchor = (
            f" ESMA Annex 2 fields: {', '.join(matched_codes)}."
            if matched_codes
            else ""
        )
        citation = Citation(
            document=input.file_url,
            page_or_row=(
                f"rows 1-{loan_count}"
                + (f" · {', '.join(matched_codes)}" if matched_codes else "")
            ),
            excerpt=(
                f"ESMA {annex_detected} tape with {loan_count} loans "
                f"(ingested via {data_source})."
                f"{annex2_anchor}"
            ),
        )

        # -----------------------------------------------------------------
        # Audit entry
        # -----------------------------------------------------------------
        duration_ms = (time.perf_counter() - t0) * 1000.0
        audit = AuditEntry.now(
            primitive_name=self.name,
            version=self.version,
            input_hash=input_hash,
            duration_ms=duration_ms,
        )

        output = EsmaTapeOutput(
            reporting_date=reporting_date,
            asset_class=asset_class,
            transaction_name=transaction_name,
            loan_count=loan_count,
            pool_balance_eur=pool_balance_eur,
            pool_stats=pool_stats,
            arrears_breakdown=arrears_breakdown,
            epc_breakdown=epc_breakdown,
            rate_type_breakdown=rate_type_breakdown,
            property_type_breakdown=property_type_breakdown,
            geographic_breakdown=geographic_breakdown,
            annex_detected=annex_detected,
            data_source=data_source,
        )

        return PrimitiveResult[EsmaTapeOutput](
            output=output,
            confidence=confidence,
            citations=[citation],
            audit_entry=audit,
        )


# ---------------------------------------------------------------------------
# Annex → asset class label
# ---------------------------------------------------------------------------

_ANNEX_TO_ASSET_CLASS: dict[str, str] = {
    "Annex 2 (RMBS)": "RMBS",
    "Annex 5 (Auto)": "Auto",
    "Annex 8 (SME)": "SME",
}


def _annex_to_asset_class(annex: str) -> str:
    """Map annex label to a short asset class string."""
    return _ANNEX_TO_ASSET_CLASS.get(annex, "ABS")
