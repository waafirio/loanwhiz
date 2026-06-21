"""Pool stratification & concentration primitive.

Extends the single-dimension :mod:`loanwhiz.primitives.esma_tape_normaliser`
pool analytics into a **multi-dimensional stratification report** for the
analyst-facing tooling (issue #325, epic #262):

- **Multi-dimensional strata** — the pool sliced simultaneously across
  **LTV × seasoning × region × rate-type**. Each cell carries its loan count,
  balance, and count/balance share of the pool.
- **Concentration limits vs eligibility** — caller-supplied concentration
  rules (``{dimension, bucket, max_pct, basis}``) evaluated against the
  per-dimension marginal share and classified ``within`` / ``near`` /
  ``breach``. The deal seed models carry no eligibility block today, so the
  limits are an input (the analyst sets the limits they are testing against),
  not a deal-model read.
- **Strata migration across periods** — when a second reporting cut-off
  (``period_compare``) is supplied, the per-dimension marginal shares are
  diffed period-A vs period-B so the analyst sees how the pool composition
  drifted.

Design notes
------------
This primitive **reuses** the tape loader and canonical Green-Lion field
vocabulary from ``esma_tape_normaliser`` (``_load_tape``) rather than
duplicating it — the normaliser's own ``execute`` is left untouched. Numeric
dimensions (LTV ``cltomv_current``, seasoning ``seasoning_months``) are binned
into ordered half-open buckets with configurable edges; categorical dimensions
(region ``province``, rate-type ``rate_type``) pass through as-is. A missing
dimension column degrades honestly to a single ``"unavailable"`` bucket (and
dents confidence) rather than crashing — mirroring the normaliser's
``_optional_breakdown`` returning ``None`` semantics.

Implements the ``Primitive[PoolStratificationInput, PoolStratificationOutput]``
contract so it composes with the other LoanWhiz primitives and is governed
(typed I/O, confidence, citation, audit entry) like every sibling.
"""

from __future__ import annotations

import time
from typing import Literal

import pandas as pd
from pydantic import BaseModel, Field

from loanwhiz.primitives.base import (
    AuditEntry,
    BaseInput,
    Citation,
    Primitive,
    PrimitiveResult,
)
from loanwhiz.primitives.esma_tape_normaliser import _load_tape
from loanwhiz.primitives.registry import register_primitive

# ---------------------------------------------------------------------------
# Dimension vocabulary
# ---------------------------------------------------------------------------

# The four stratification dimensions and the (lower-cased) tape column each
# reads from. LTV and seasoning are numeric (binned); region and rate-type are
# categorical (pass-through). Keyed by the dimension *name* the input uses.
Dimension = Literal["ltv", "seasoning", "region", "rate_type"]

_DIMENSION_COLUMN: dict[str, str] = {
    "ltv": "cltomv_current",
    "seasoning": "seasoning_months",
    "region": "province",
    "rate_type": "rate_type",
}

# Numeric dimensions get binned against edges; categorical ones pass through.
_NUMERIC_DIMENSIONS: frozenset[str] = frozenset({"ltv", "seasoning"})

# Default ordering of all four dimensions when the input does not restrict it.
_DEFAULT_DIMENSIONS: tuple[str, ...] = ("ltv", "seasoning", "region", "rate_type")

# Sentinel bucket label used when a dimension's source column is absent from
# the tape — the loan still appears in the strata, honestly flagged.
UNAVAILABLE_BUCKET = "unavailable"

# Default half-open bin edges (interior boundaries) for the numeric dimensions.
# Buckets are ``[-inf, e0)``, ``[e0, e1)``, …, ``[e_last, +inf)`` — labelled
# ``"<e0"`` / ``"e0-e1"`` / ``"e_last+"``. LTV is a percentage (cltomv_current);
# seasoning is in months.
_DEFAULT_LTV_EDGES: tuple[float, ...] = (50.0, 60.0, 70.0, 80.0, 90.0, 100.0)
_DEFAULT_SEASONING_EDGES: tuple[float, ...] = (12.0, 24.0, 36.0, 60.0, 120.0)

# Confidence deductions.
_DEDUCT_UNAVAILABLE_DIMENSION = 0.1  # per dimension whose column is absent
_DEDUCT_MISSING_BALANCE = 0.1  # current_balance column absent (count-only)

# Near-miss margin: an observed share within this fraction *below* the limit is
# flagged ``near`` rather than ``within`` (e.g. 0.9 → within 10% of the limit).
# Mirrors the covenant monitor's near-miss idea, kept self-contained here.
_NEAR_MISS_RATIO = 0.9


# ---------------------------------------------------------------------------
# I/O models
# ---------------------------------------------------------------------------


class ConcentrationLimit(BaseModel):
    """A single caller-supplied concentration / eligibility rule.

    Attributes:
        dimension: Which stratification dimension the limit applies to
                   (``ltv`` | ``seasoning`` | ``region`` | ``rate_type``).
        bucket:    The bucket label within that dimension's marginal the limit
                   caps (e.g. ``"90-100"`` for LTV, ``"Floating"`` for
                   rate-type, ``"ES30"`` for region).
        max_pct:   Maximum allowed share (0–100) for that bucket.
        basis:     Whether the share is measured by ``balance`` (default) or
                   ``count``.
    """

    dimension: Dimension = Field(..., description="Dimension the limit applies to.")
    bucket: str = Field(..., description="Bucket label within the dimension marginal.")
    max_pct: float = Field(..., ge=0.0, le=100.0, description="Max allowed share (%).")
    basis: Literal["balance", "count"] = Field(
        default="balance", description="Share basis: balance-weighted or loan-count."
    )


class PoolStratificationInput(BaseInput):
    """Input schema for the pool stratification & concentration tool.

    Attributes:
        file_url:         URL/path to the ESMA loan tape (CSV/parquet) or a
                          ``deeploans://`` reference — same loader as the
                          normaliser.
        period:           Reporting-date selector for a combined multi-month
                          tape (filters ``reporting_date == period``). ``None``
                          reads the whole file.
        period_compare:   Optional second reporting cut-off. When set, the same
                          tape is loaded for this period too and a per-dimension
                          marginal **migration** (period → period_compare) is
                          computed. ``None`` skips migration.
        dimensions:       Subset/order of ``ltv|seasoning|region|rate_type`` to
                          stratify on. Defaults to all four.
        ltv_edges:        Interior bin edges for the LTV dimension. ``None``
                          uses the RMBS defaults.
        seasoning_edges:  Interior bin edges for the seasoning dimension (months).
                          ``None`` uses the defaults.
        concentration_limits: Concentration / eligibility rules to evaluate.
                          Empty (default) → no checks emitted.
    """

    file_url: str = Field(
        ..., description="URL or path to the ESMA loan tape (CSV or parquet)."
    )
    period: str | None = Field(
        default=None,
        description="Reporting-date selector for a combined multi-month tape.",
    )
    period_compare: str | None = Field(
        default=None,
        description="Second reporting cut-off for strata migration; None skips it.",
    )
    dimensions: list[Dimension] = Field(
        default_factory=lambda: list(_DEFAULT_DIMENSIONS),
        description="Dimensions to stratify on (subset/order). Default: all four.",
    )
    ltv_edges: list[float] | None = Field(
        default=None, description="Interior LTV bin edges (%); None uses defaults."
    )
    seasoning_edges: list[float] | None = Field(
        default=None,
        description="Interior seasoning bin edges (months); None uses defaults.",
    )
    concentration_limits: list[ConcentrationLimit] = Field(
        default_factory=list,
        description="Concentration / eligibility rules to evaluate.",
    )


class StratumCell(BaseModel):
    """One cell of the multi-dimensional stratification.

    Attributes:
        key:         Ordered ``{dimension: bucket}`` mapping identifying the
                     cell (one entry per active dimension, in input order).
        loan_count:  Number of loans in the cell.
        balance_eur: Sum of ``current_balance`` in the cell (0.0 when the
                     balance column is absent).
        count_pct:   Cell loan count as a percentage of the pool (0–100).
        balance_pct: Cell balance as a percentage of pool balance (0–100; 0.0
                     when the balance column is absent).
    """

    key: dict[str, str]
    loan_count: int
    balance_eur: float
    count_pct: float
    balance_pct: float


class ConcentrationCheck(BaseModel):
    """Result of evaluating one concentration limit against the strata.

    Attributes:
        dimension:    Dimension the rule applied to.
        bucket:       Bucket the rule capped.
        basis:        ``balance`` or ``count``.
        observed_pct: The bucket's observed marginal share (0–100). 0.0 when
                      the bucket is absent from the pool.
        limit_pct:    The configured maximum share.
        status:       ``within`` (comfortably under), ``near`` (within the
                      near-miss margin of the limit), or ``breach`` (over).
    """

    dimension: str
    bucket: str
    basis: str
    observed_pct: float
    limit_pct: float
    status: Literal["within", "near", "breach"]


class MigrationCell(BaseModel):
    """Per-bucket migration of a single dimension marginal between two periods.

    Attributes:
        dimension:       The dimension whose marginal migrated.
        bucket:          Bucket label.
        count_a:         Loan count in the bucket at the base period.
        count_b:         Loan count in the bucket at the compare period.
        balance_pct_a:   Bucket balance share (0–100) at the base period.
        balance_pct_b:   Bucket balance share (0–100) at the compare period.
        balance_pct_delta: ``balance_pct_b - balance_pct_a`` (percentage points).
    """

    dimension: str
    bucket: str
    count_a: int
    count_b: int
    balance_pct_a: float
    balance_pct_b: float
    balance_pct_delta: float


class PoolStratificationOutput(BaseModel):
    """Multi-dimensional stratification + concentration + migration report.

    Attributes:
        reporting_date:      Base-period cut-off the strata were computed for.
        dimensions:          The active dimensions, in order.
        total_loans:         Loan count of the (base-period) pool.
        total_balance_eur:   Pool balance of the base period.
        strata:              Multi-dimensional cells (one per occupied combo).
        concentration_checks: Results of the supplied concentration limits.
        migration:           Per-dimension marginal migration cells, or ``None``
                             when ``period_compare`` was not supplied.
        unavailable_dimensions: Dimensions whose source column was absent (each
                             collapsed to the ``"unavailable"`` bucket).
    """

    reporting_date: str
    dimensions: list[str]
    total_loans: int
    total_balance_eur: float
    strata: list[StratumCell]
    concentration_checks: list[ConcentrationCheck]
    migration: list[MigrationCell] | None
    unavailable_dimensions: list[str]


# ---------------------------------------------------------------------------
# Bucketing helpers (pure)
# ---------------------------------------------------------------------------


def _edges_for(dimension: str, input: PoolStratificationInput) -> tuple[float, ...]:
    """Return the interior bin edges for a numeric *dimension*."""
    if dimension == "ltv":
        return tuple(input.ltv_edges) if input.ltv_edges else _DEFAULT_LTV_EDGES
    if dimension == "seasoning":
        return (
            tuple(input.seasoning_edges)
            if input.seasoning_edges
            else _DEFAULT_SEASONING_EDGES
        )
    raise ValueError(f"{dimension!r} is not a numeric dimension")  # pragma: no cover


def _bucket_labels(edges: tuple[float, ...]) -> list[str]:
    """Ordered human-readable labels for the half-open bins defined by *edges*.

    For edges ``(e0, e1, ..., en)`` the labels are
    ``["<e0", "e0-e1", ..., "en+"]`` — a label per ``len(edges)+1`` bins.
    """
    if not edges:
        return ["all"]
    labels = [f"<{_fmt(edges[0])}"]
    for lo, hi in zip(edges, edges[1:]):
        labels.append(f"{_fmt(lo)}-{_fmt(hi)}")
    labels.append(f"{_fmt(edges[-1])}+")
    return labels


def _fmt(x: float) -> str:
    """Format a bin edge without a trailing ``.0`` for whole numbers."""
    return str(int(x)) if float(x).is_integer() else str(x)


def _bin_numeric(series: pd.Series, edges: tuple[float, ...]) -> pd.Series:
    """Assign each value in *series* to a half-open bin label.

    Bins are ``(-inf, e0)``, ``[e0, e1)``, …, ``[e_last, +inf)``. Non-numeric /
    missing values map to :data:`UNAVAILABLE_BUCKET`.
    """
    labels = _bucket_labels(edges)
    numeric = pd.to_numeric(series, errors="coerce")
    full_edges = [float("-inf"), *edges, float("inf")]
    # ``right=False`` → half-open ``[lo, hi)`` bins matching the labels above.
    binned = pd.cut(
        numeric, bins=full_edges, labels=labels, right=False, include_lowest=True
    )
    out = binned.astype(object)
    out[numeric.isna()] = UNAVAILABLE_BUCKET
    return out


def _bucket_series(
    df_lower: pd.DataFrame, dimension: str, input: PoolStratificationInput
) -> tuple[pd.Series, bool]:
    """Return ``(bucket_labels, available)`` for *dimension* over the frame.

    *available* is ``False`` when the dimension's source column is absent — the
    returned series is then a constant :data:`UNAVAILABLE_BUCKET` so the loans
    still stratify (honestly flagged) instead of crashing.
    """
    col = _DIMENSION_COLUMN[dimension]
    if col not in df_lower.columns:
        const = pd.Series(
            [UNAVAILABLE_BUCKET] * len(df_lower), index=df_lower.index, dtype=object
        )
        return const, False

    if dimension in _NUMERIC_DIMENSIONS:
        return _bin_numeric(df_lower[col], _edges_for(dimension, input)), True

    # Categorical: pass through as string; NaN → unavailable bucket.
    cat = df_lower[col].astype(object)
    cat = cat.where(df_lower[col].notna(), UNAVAILABLE_BUCKET)
    return cat.astype(str), True


# ---------------------------------------------------------------------------
# Stratification core
# ---------------------------------------------------------------------------


def _balance_series(df_lower: pd.DataFrame) -> tuple[pd.Series, bool]:
    """Return ``(balance, available)`` — numeric ``current_balance`` or zeros."""
    if "current_balance" in df_lower.columns:
        bal = pd.to_numeric(df_lower["current_balance"], errors="coerce").fillna(0.0)
        return bal, True
    return pd.Series([0.0] * len(df_lower), index=df_lower.index), False


def _stratify(
    df_lower: pd.DataFrame,
    dimensions: list[str],
    input: PoolStratificationInput,
) -> tuple[list[StratumCell], list[str]]:
    """Build the multi-dimensional strata over *dimensions*.

    Returns ``(cells, unavailable_dimensions)``. Cells are sorted by their key
    for deterministic output. ``count_pct`` / ``balance_pct`` are pool shares.
    """
    n = len(df_lower)
    balance, _bal_ok = _balance_series(df_lower)
    total_balance = float(balance.sum())

    work = pd.DataFrame({"_balance": balance.to_numpy()}, index=df_lower.index)
    unavailable: list[str] = []
    for dim in dimensions:
        labels, available = _bucket_series(df_lower, dim, input)
        work[dim] = labels.to_numpy()
        if not available:
            unavailable.append(dim)

    cells: list[StratumCell] = []
    if n == 0:
        return cells, unavailable

    grouped = work.groupby(dimensions, dropna=False, observed=True)
    for key_vals, sub in grouped:
        # groupby returns a scalar for a single key, a tuple for several.
        key_tuple = key_vals if isinstance(key_vals, tuple) else (key_vals,)
        key = {dim: str(val) for dim, val in zip(dimensions, key_tuple)}
        count = int(len(sub))
        bal = float(sub["_balance"].sum())
        cells.append(
            StratumCell(
                key=key,
                loan_count=count,
                balance_eur=round(bal, 2),
                count_pct=round(count / n * 100, 4),
                balance_pct=round(bal / total_balance * 100, 4)
                if total_balance > 0
                else 0.0,
            )
        )

    cells.sort(key=lambda c: tuple(c.key[d] for d in dimensions))
    return cells, unavailable


def _marginal_shares(
    df_lower: pd.DataFrame, dimension: str, input: PoolStratificationInput
) -> dict[str, tuple[int, float]]:
    """Per-bucket ``{bucket: (loan_count, balance_pct)}`` for one *dimension*."""
    n = len(df_lower)
    balance, _ = _balance_series(df_lower)
    total_balance = float(balance.sum())
    labels, _available = _bucket_series(df_lower, dimension, input)

    work = pd.DataFrame(
        {"bucket": labels.to_numpy(), "_balance": balance.to_numpy()},
        index=df_lower.index,
    )
    out: dict[str, tuple[int, float]] = {}
    if n == 0:
        return out
    for bucket, sub in work.groupby("bucket", dropna=False, observed=True):
        bal = float(sub["_balance"].sum())
        share = round(bal / total_balance * 100, 4) if total_balance > 0 else 0.0
        out[str(bucket)] = (int(len(sub)), share)
    return out


def _marginal_count_pct(
    df_lower: pd.DataFrame, dimension: str, input: PoolStratificationInput
) -> dict[str, float]:
    """Per-bucket count-share (0–100) for one *dimension* marginal."""
    n = len(df_lower)
    labels, _available = _bucket_series(df_lower, dimension, input)
    if n == 0:
        return {}
    counts = labels.value_counts()
    return {str(k): round(int(v) / n * 100, 4) for k, v in counts.items()}


# ---------------------------------------------------------------------------
# Concentration checks
# ---------------------------------------------------------------------------


def _classify(observed: float, limit: float) -> Literal["within", "breach", "near"]:
    """Classify an observed share against a limit (with the near-miss margin)."""
    if observed > limit:
        return "breach"
    if limit > 0 and observed >= limit * _NEAR_MISS_RATIO:
        return "near"
    return "within"


def _check_limits(
    df_lower: pd.DataFrame,
    input: PoolStratificationInput,
) -> list[ConcentrationCheck]:
    """Evaluate each concentration limit against the relevant marginal share."""
    checks: list[ConcentrationCheck] = []
    # Cache marginals per (dimension, basis) so repeated rules don't recompute.
    balance_cache: dict[str, dict[str, tuple[int, float]]] = {}
    count_cache: dict[str, dict[str, float]] = {}

    for rule in input.concentration_limits:
        if rule.basis == "balance":
            if rule.dimension not in balance_cache:
                balance_cache[rule.dimension] = _marginal_shares(
                    df_lower, rule.dimension, input
                )
            observed = balance_cache[rule.dimension].get(rule.bucket, (0, 0.0))[1]
        else:
            if rule.dimension not in count_cache:
                count_cache[rule.dimension] = _marginal_count_pct(
                    df_lower, rule.dimension, input
                )
            observed = count_cache[rule.dimension].get(rule.bucket, 0.0)

        checks.append(
            ConcentrationCheck(
                dimension=rule.dimension,
                bucket=rule.bucket,
                basis=rule.basis,
                observed_pct=observed,
                limit_pct=rule.max_pct,
                status=_classify(observed, rule.max_pct),
            )
        )
    return checks


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


def _migrate(
    df_a: pd.DataFrame,
    df_b: pd.DataFrame,
    dimensions: list[str],
    input: PoolStratificationInput,
) -> list[MigrationCell]:
    """Per-dimension marginal migration between two period frames.

    For each active dimension, the union of bucket labels across both periods
    is diffed: count A/B and balance-share A/B, plus the balance-share delta.
    """
    cells: list[MigrationCell] = []
    for dim in dimensions:
        marg_a = _marginal_shares(df_a, dim, input)
        marg_b = _marginal_shares(df_b, dim, input)
        for bucket in sorted(set(marg_a) | set(marg_b)):
            count_a, bpct_a = marg_a.get(bucket, (0, 0.0))
            count_b, bpct_b = marg_b.get(bucket, (0, 0.0))
            cells.append(
                MigrationCell(
                    dimension=dim,
                    bucket=bucket,
                    count_a=count_a,
                    count_b=count_b,
                    balance_pct_a=bpct_a,
                    balance_pct_b=bpct_b,
                    balance_pct_delta=round(bpct_b - bpct_a, 4),
                )
            )
    return cells


# ---------------------------------------------------------------------------
# Primitive
# ---------------------------------------------------------------------------


def _lower_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Return *df* with lower-cased column names (the canonical work frame)."""
    return df.rename(columns={c: c.lower() for c in df.columns})


def _reporting_date(df_lower: pd.DataFrame, fallback: str | None) -> str:
    """Best-effort reporting-date label for the (base) frame."""
    if "reporting_date" in df_lower.columns:
        non_null = df_lower["reporting_date"].dropna()
        if not non_null.empty:
            return str(non_null.iloc[0])
    return fallback or "unknown"


@register_primitive(
    name="pool_stratification",
    version="0.1.0",
    description=(
        "Multi-dimensional pool stratification (LTV x seasoning x region x "
        "rate-type) with concentration-limit checks and cross-period migration."
    ),
    tags=["data", "esma", "tape", "stratification"],
)
class PoolStratification(
    Primitive[PoolStratificationInput, PoolStratificationOutput]
):
    """Stratify an ESMA loan tape across LTV x seasoning x region x rate-type.

    Reuses the normaliser's tape loader, bins numeric dimensions, evaluates
    caller-supplied concentration limits, and (optionally) diffs the
    per-dimension marginals between two reporting periods. Returns a governed
    ``PrimitiveResult`` with confidence, a citation, and an audit entry.
    """

    name = "pool_stratification"
    version = "0.1.0"
    description = (
        "Multi-dimensional pool stratification (LTV x seasoning x region x "
        "rate-type) with concentration-limit checks and cross-period migration."
    )

    def execute(  # type: ignore[override]
        self, input: PoolStratificationInput
    ) -> PrimitiveResult[PoolStratificationOutput]:
        """Run the stratification report for the tape at ``input.file_url``."""
        t0 = time.perf_counter()
        input_hash = input.input_hash()

        dimensions = list(input.dimensions) if input.dimensions else list(
            _DEFAULT_DIMENSIONS
        )

        # -----------------------------------------------------------------
        # Load base period and stratify.
        # -----------------------------------------------------------------
        df_a, data_source = _load_tape(input.file_url, input.period)
        df_a_lower = _lower_frame(df_a)

        _balance, balance_available = _balance_series(df_a_lower)
        strata, unavailable = _stratify(df_a_lower, dimensions, input)
        concentration_checks = _check_limits(df_a_lower, input)

        total_balance = float(_balance.sum())
        reporting_date = _reporting_date(df_a_lower, input.period)

        # -----------------------------------------------------------------
        # Optional cross-period migration.
        # -----------------------------------------------------------------
        migration: list[MigrationCell] | None = None
        if input.period_compare is not None:
            df_b, _ = _load_tape(input.file_url, input.period_compare)
            df_b_lower = _lower_frame(df_b)
            migration = _migrate(df_a_lower, df_b_lower, dimensions, input)

        # -----------------------------------------------------------------
        # Confidence scoring.
        # -----------------------------------------------------------------
        confidence = 1.0
        confidence -= _DEDUCT_UNAVAILABLE_DIMENSION * len(unavailable)
        if not balance_available:
            confidence -= _DEDUCT_MISSING_BALANCE
        confidence = max(0.0, round(confidence, 4))

        output = PoolStratificationOutput(
            reporting_date=reporting_date,
            dimensions=dimensions,
            total_loans=len(df_a_lower),
            total_balance_eur=round(total_balance, 2),
            strata=strata,
            concentration_checks=concentration_checks,
            migration=migration,
            unavailable_dimensions=unavailable,
        )

        citation = Citation(
            document=input.file_url,
            page_or_row=f"rows 1-{len(df_a_lower)}",
            excerpt=(
                f"Pool stratified across {dimensions} into {len(strata)} cells "
                f"(ingested via {data_source})"
            ),
        )

        duration_ms = (time.perf_counter() - t0) * 1000.0
        audit = AuditEntry.now(
            primitive_name=self.name,
            version=self.version,
            input_hash=input_hash,
            duration_ms=duration_ms,
        )

        return PrimitiveResult[PoolStratificationOutput](
            output=output,
            confidence=confidence,
            citations=[citation],
            audit_entry=audit,
        )
