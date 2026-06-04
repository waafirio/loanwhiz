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

Green Lion 2026-1 field mapping (validated against Algoritmica/green-lion-2026)
-------------------------------------------------------------------------------
- ``current_balance``         — loan outstanding balance (EUR)
- ``current_interest_rate_pct`` — coupon rate (%)
- ``remaining_term_months``   — months to contractual maturity
- ``seasoning_months``        — months since origination
- ``cltomv_current``          — current loan-to-value ratio (%)
- ``arrears_bucket``          — "Performing" | "<29d" | "180+d"
- ``default_crr_flag``        — "Y" | "N"
- ``epc_label``               — "A" | "A+" | "B" | etc.
- ``rate_type``               — "Fixed" | "Floating"
- ``property_type``           — "House" | "Apartment" | etc.
- ``province``                — NUTS-2 / regional identifier
- ``transaction_name``        — deal name from the tape
- ``reporting_date``          — reporting cut-off date (YYYY-MM-DD)
"""

from __future__ import annotations

import time
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

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


def _load_tape(file_url: str, period: str | None) -> pd.DataFrame:
    """Load a loan tape from *file_url* as a DataFrame, format-agnostically.

    Detects the format from the URL/path extension: a ``.parquet``/``.pq``
    suffix is read via :func:`pandas.read_parquet`; anything else falls back to
    :func:`pandas.read_csv` (the historical default, with ``low_memory=False``).

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
    pandas.DataFrame
        The loaded tape, sliced to *period* when requested.

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

    return df


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
    has_default_col = "default_crr_flag" in df.columns

    # Priority 1: defaulted (highest)
    default_mask = (
        df["default_crr_flag"].str.upper() == "Y"
        if has_default_col
        else pd.Series([False] * n, index=df.index)
    )
    # Priority 2: 180+ days arrears (not also flagged as default)
    arrears_180d_mask = (
        (df["arrears_bucket"] == "180+d") & ~default_mask
        if has_arrears_col
        else pd.Series([False] * n, index=df.index)
    )
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
        df = _load_tape(input.file_url, input.period)
        cols: set[str] = set(df.columns)

        # -----------------------------------------------------------------
        # Annex detection
        # -----------------------------------------------------------------
        cols_lower = {c.lower() for c in df.columns}
        # Map original -> lower for column lookup
        col_map = {c.lower(): c for c in df.columns}

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
        df_lower = df.rename(columns={c: c.lower() for c in df.columns})
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
        # Citation
        # -----------------------------------------------------------------
        citation = Citation(
            document=input.file_url,
            page_or_row=f"rows 1-{loan_count}",
            excerpt=f"ESMA {annex_detected} tape with {loan_count} loans",
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
