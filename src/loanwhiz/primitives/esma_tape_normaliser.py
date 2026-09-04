"""ESMA loan-level tape normaliser primitive.

Loads a loan tape from a URL (HuggingFace or local) in either **CSV or
parquet** format — the loader is format-agnostic and dispatches on the URL
extension — auto-detects the ESMA Annex schema (Annex 2 RMBS, Annex 4 Corporate,
Annex 5 Auto, etc.), and computes a comprehensive set of pool analytics:

- Balance-weighted averages: coupon, LTV, seasoning, remaining term.
- Multi-bucket arrears breakdown: current, <29 days, 180+ days, default.
- Categorical distributions: EPC, rate type, property type, geographic.

Implements the ``Primitive[EsmaTapeInput, EsmaTapeOutput]`` contract so it can
be composed with other LoanWhiz primitives by the LangGraph agent.

Canonical column resolution (scoped to the detected annex)
-----------------------------------------------------------
Tape column names vary across issuers and vintages, but each maps to a stable
ESMA RTS field code — and *which* code depends on the annex the tape was
published under: ``current_balance`` is ``RREL18`` on an Annex 2 (RMBS) tape but
``CRPL39`` on an Annex 4 (Corporate) one. The annex tables and the registry that
holds them live in :mod:`loanwhiz.domain.esma_annexes`.

**This module owns no annex list.** It detects the tape's annex through the
registry, then resolves that tape's columns through *only* the matched
:class:`~loanwhiz.domain.esma_annex_registry.AnnexSpec`. An issuer spelling a
column ``outstanding_balance`` still resolves to ``current_balance``, and a
column already in canonical form resolves to itself, so the validated Green Lion
2026-1 behaviour is preserved byte-for-byte. The output ``Citation`` is anchored
to the matched field codes *of that annex*, so provenance never attributes a
value to another asset class's template.

A tape matching **no** registered signature resolves nothing and cites no field
codes: it degrades honestly (with the unknown-annex confidence deduction) rather
than being resolved through an arbitrary table. Adding an asset class is a new
table module plus one import in ``esma_annexes`` — no edit here.

Canonical names the pool analytics key on (Annex 2 codes shown; the equivalent
column on another annex carries that annex's own code):

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

from loanwhiz.domain.esma_annexes import ANNEX_REGISTRY, AnnexSpec
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

# The annex list itself lives in ``loanwhiz.domain.esma_annexes``: each
# registered ``AnnexSpec`` carries its detection signature, its labels and its
# field table on one record. This module deliberately holds no annex list of its
# own — a second list here is exactly what let detection and resolution drift
# apart, so that an Auto or Corporate tape was detected correctly and then
# resolved (and cited) through the RMBS table.

_UNKNOWN_ANNEX = "Unknown ABS"

# Asset-class label for a tape whose columns match no registered signature.
_UNKNOWN_ASSET_CLASS = "ABS"

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


def _detect_annex(columns: set[str]) -> AnnexSpec | None:
    """Return the registered :class:`AnnexSpec` this tape's columns match.

    Detection is delegated to the annex registry, which walks its specs in
    registration order and asks each whether *its own* signature is satisfied —
    resolving signature columns through that spec's own synonyms, so an issuer
    spelling EPC ``epc_rating`` is still detected as Annex 2 without any other
    annex's vocabulary leaking into the match.

    Parameters
    ----------
    columns:
        Column names present in the tape (matched case-insensitively).

    Returns
    -------
    AnnexSpec | None
        The matched annex, or ``None`` when no registered signature matches.
        ``None`` is the honest answer, and the caller must then resolve nothing
        rather than falling back to an arbitrary table.
    """
    return ANNEX_REGISTRY.detect(columns)


def _pct_distribution(series: pd.Series) -> dict[str, float]:
    """Return a dict mapping each unique value to its percentage (0–100)."""
    total = len(series.dropna())
    if total == 0:
        return {}
    counts = series.value_counts(dropna=True)
    return {str(k): round(float(v) / total * 100, 4) for k, v in counts.items()}


#: ESMA account-status code for a defaulted exposure. ``CRPL79`` (Annex 4) and
#: ``AUTL70`` (Annex 5) share one status enum, of which ``DFLT`` is default.
_ACCOUNT_STATUS_DEFAULTED = "DFLT"


def _default_mask(df: pd.DataFrame) -> pd.Series:
    """Boolean mask of defaulted loans.

    Two annexes express default differently, and both are read here so the mask
    is not silently empty on a tape that states default in the other form:

    - **Annex 2 (RMBS)** carries an explicit ``default_crr_flag`` (``RREL66``).
    - **Annex 4 (Corporate) / Annex 5 (Auto)** carry no default flag at all;
      default is a value of ``account_status`` (``CRPL79`` / ``AUTL70``).

    Reading only the RMBS flag would report ``default_pct = 0.0`` for a corporate
    tape whose obligors are marked ``DFLT`` — a silent zero indistinguishable
    from a clean pool. Falls back to an all-``False`` mask only when the tape
    states default in neither form. Expects lower-cased column names (the
    ``df_lower`` frame).
    """
    mask = pd.Series([False] * len(df), index=df.index)
    if "default_crr_flag" in df.columns:
        mask = mask | (df["default_crr_flag"].astype(str).str.upper() == "Y")
    if "account_status" in df.columns:
        mask = mask | (
            df["account_status"].astype(str).str.upper() == _ACCOUNT_STATUS_DEFAULTED
        )
    return mask


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


def _resolve_columns(columns: list[str], spec: AnnexSpec | None) -> dict[str, str]:
    """Build a ``canonical-column → original-column`` map for a tape.

    Each tape column name is looked up in **the detected annex's** field table.
    When a column is a known canonical name *or* a registered issuer/vintage
    synonym of that annex, its canonical name maps to the original column header
    — so the normaliser's downstream lookups (which use canonical names like
    ``"current_balance"`` / ``"cltomv_current"``) still find the data even when
    the tape spells the column differently across issuers.

    A column already in canonical form maps to itself (the historical Green-Lion
    behaviour is preserved byte-for-byte: every Green-Lion column is its own
    canonical name, so this map is the identity on that tape). The first
    occurrence of a canonical target wins, so a synonym never overrides a column
    that is already present under its canonical name.

    Parameters
    ----------
    columns:
        The tape's original column headers.
    spec:
        The detected annex, or ``None`` when the tape matched no signature.
        **``None`` resolves nothing.** Resolving an unidentified tape through
        some default table is the guessing this seam exists to prevent: it would
        silently rename columns and cite them with a field code from an asset
        class the tape may not belong to.

    Returns a dict keyed by **lower-cased canonical column name**; columns not in
    that annex's table are simply absent (callers keep their existing fallbacks).
    """
    if spec is None:
        return {}
    resolved: dict[str, str] = {}
    for orig in columns:
        canonical = spec.canonical_column_for(orig)
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
        # Annex detection — before resolution, because resolution is scoped to
        # whichever annex is detected. Each spec resolves its own signature
        # through its own synonyms, so detection needs no pre-resolved columns.
        # -----------------------------------------------------------------
        annex_spec = _detect_annex(cols)
        annex_certain = annex_spec is not None
        annex_detected = annex_spec.label if annex_spec else _UNKNOWN_ANNEX

        cols_lower = {c.lower() for c in df.columns}
        # Map original -> lower for column lookup
        col_map = {c.lower(): c for c in df.columns}
        # Overlay the detected annex's canonical-name resolution so issuer/
        # vintage column-name variants resolve onto the canonical names the
        # lookups below expect (e.g. ``outstanding_balance`` → ``current_balance``).
        # ``setdefault`` keeps a column already present under its canonical name
        # authoritative — so Green-Lion tapes (already canonical) are unchanged.
        # An unidentified tape resolves nothing (``_resolve_columns`` returns an
        # empty map), so its columns are only lower-cased.
        for canonical, orig in _resolve_columns(list(df.columns), annex_spec).items():
            col_map.setdefault(canonical, orig)
            cols_lower.add(canonical)

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
        # Lower-case all columns, then rename any synonym columns of the detected
        # annex onto their canonical names so the arrears / categorical
        # extractors (which key on canonical names like ``arrears_bucket`` /
        # ``default_crr_flag`` / ``epc_label``) resolve issuer-variant spellings.
        # ``setdefault`` semantics in ``_resolve_columns`` guarantee a column
        # already present under its canonical name is never overwritten, so a
        # Green-Lion tape is renamed to itself (identity).
        lower_rename = {c: c.lower() for c in df.columns}
        for canonical, orig in _resolve_columns(list(df.columns), annex_spec).items():
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
        asset_class = annex_spec.asset_class if annex_spec else _UNKNOWN_ASSET_CLASS

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
        # Citation — anchored to the DETECTED annex's field codes
        # -----------------------------------------------------------------
        # Surface which regulatory fields the loaded tape's columns map to, so
        # the governance view can trace pool analytics back to the codes they
        # came from (the locator mechanism fixed in the schema design, decision
        # D8). Codes come from the detected annex only: citing an Annex 4
        # corporate balance as ``RREL18`` would attribute the value to a
        # residential-mortgage template it does not belong to. An unidentified
        # tape yields no codes at all rather than borrowed ones.
        matched_codes = (
            sorted(
                {
                    code
                    for c in df.columns
                    if (code := annex_spec.code_for_column(c)) is not None
                }
            )
            if annex_spec is not None
            else []
        )
        annex_anchor = (
            f" ESMA {annex_detected} fields: {', '.join(matched_codes)}."
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
                f"{annex_anchor}"
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


# The annex → asset-class map that used to live here is gone: each registered
# ``AnnexSpec`` carries its own ``asset_class``, so a new asset class is a table
# in ``loanwhiz.domain.esma_annexes`` rather than an edit to this module. (The
# old map was also where ``"Annex 8 (SME)"`` was mislabelled — Annex VIII is
# leasing; the corporate template, which covers SMEs, is Annex IV.)
