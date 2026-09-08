"""Generate the committed synthetic loan tapes from their fit specs (#484).

Four registered deals publish no loan tape, so every pool-driven chart is empty
for them. This script builds each one an ESMA Annex 2 pool from the fit spec
``investor_report_pool_fit.py`` read out of that deal's own published investor
report, and writes it to ``src/loanwhiz/data/tapes/synthetic/``.

**The rows are generated once and committed**, rather than materialised per
read. A synthetic pool has nothing to defer — it comes from parameters fixed at
authoring time — so committing it puts the exact data behind every comparison
chart in git, and the fit spec beside it makes the derivation auditable.

What is fitted and what is not
------------------------------
Fitted, and **verified before anything is written**: the loan count, the pool
balance, the balance-weighted coupon, seasoning, remaining term and current
LTV, and the balance shares of every distribution the report publishes
(arrears, rate type, region, and — Dutch reports only — property type and
energy label). :func:`reconcile` recomputes each from the generated frame and
**refuses** on divergence, so a pool that contradicts the report it was fitted
to cannot be written.

Not fitted: the dispersion *within* a bucket, and the correlation *between*
dimensions. Those are generated. The tape's identifier says so — every one is
registered under the ``synthetic:`` scheme, so it reports
``data_source: "synthetic"`` and carries ``TapeSourceKind.SYNTHETIC_GENERATED``
with ``describes_real_assets: False`` — and no figure computed from that
dispersion is evidence about the real pool.

Determinism
-----------
The generator is seeded from the deal id alone, so re-running it reproduces the
committed bytes exactly; ``tests/test_synthetic_tape_generation.py`` pins that.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.investor_report_pool_fit import DEALS, fit_path, map_arrears_bucket

REPO_ROOT = Path(__file__).resolve().parents[1]
TAPE_DIR = REPO_ROOT / "src" / "loanwhiz" / "data" / "tapes" / "synthetic"

#: The registry stores this, resolved against the ``loanwhiz`` package root by
#: ``esma_tape_normaliser._resolve_committed_tape``.
REGISTERED_PREFIX = "data/tapes/synthetic"

#: How far a generated aggregate may sit from the figure it was fitted to.
BALANCE_TOLERANCE_EUR = 0.01
WEIGHTED_TOLERANCE = 0.0005
SHARE_TOLERANCE_PCT = 0.05

#: Spread of the generated per-loan balance, as a lognormal sigma. Not fitted —
#: the reports publish an outstanding-balance stratification this pool does not
#: reproduce — so it is kept modest and declared.
_BALANCE_SIGMA = 0.45

#: Bounds each generated continuous field is clipped into before its
#: balance-weighted mean is corrected back onto the fitted figure.
_BOUNDS = {
    "current_interest_rate_pct": (0.01, 12.0),
    "seasoning_months": (1.0, 480.0),
    "remaining_term_months": (1.0, 600.0),
    "cltomv_current": (1.0, 125.0),
}
_DISPERSION = {
    "current_interest_rate_pct": 0.9,
    "seasoning_months": 24.0,
    "remaining_term_months": 60.0,
    "cltomv_current": 18.0,
}


class FitDivergence(RuntimeError):
    """A generated pool does not reproduce an aggregate it was fitted to.

    Raised before the tape is written. A synthetic pool that contradicts the
    deal's own investor report is worse than no pool: it would make the
    comparison charts confidently wrong rather than honestly empty.
    """


def _seed(deal_id: str) -> int:
    return int(hashlib.sha256(deal_id.encode("utf-8")).hexdigest()[:8], 16)


# ---------------------------------------------------------------------------
# Drawing the pool
# ---------------------------------------------------------------------------


def draw_balances(rng: np.random.Generator, count: int, total: float) -> np.ndarray:
    """Draw *count* loan balances summing to *total* to the cent.

    Rescaled and then cent-rounded, with the rounding residual absorbed by the
    largest loan — so the pool balance the report states is reproduced exactly
    rather than approximately.
    """
    raw = rng.lognormal(mean=0.0, sigma=_BALANCE_SIGMA, size=count)
    scaled = raw / raw.sum() * total
    rounded = np.round(scaled, 2)
    rounded[int(np.argmax(rounded))] += round(total - rounded.sum(), 2)
    return rounded


def draw_weighted(
    rng: np.random.Generator,
    balances: np.ndarray,
    target: float,
    *,
    spread: float,
    bounds: tuple[float, float],
    decimals: int,
) -> np.ndarray:
    """Draw a per-loan field whose **balance-weighted** mean is *target*.

    Dispersion is generated; the weighted mean is not — it is the figure the
    report states, so the draw is re-centred onto it. Clipping and rounding
    both pull the mean back off, so all three happen inside the loop and the
    correction is applied to the *rounded* values: correcting first and
    rounding afterwards leaves a residual that ``reconcile`` then refuses.
    """
    weights = balances / balances.sum()
    values = target + rng.normal(0.0, spread, size=len(balances))
    step = 10.0**-decimals
    low, high = bounds

    for _ in range(8):
        values = np.clip(np.round(values, decimals), low, high)
        error = float(np.dot(weights, values)) - target
        if abs(error) <= WEIGHTED_TOLERANCE / 10:
            break
        if abs(error) >= step:
            # Far out: a plain shift is cheapest, and the next pass re-rounds.
            values = values + -error
            continue
        # Within one rounding step of the target, a shift would round away. The
        # only lever left is moving individual loans by exactly one step, so
        # move just enough weight to close the gap — lightest loans first, so
        # the last one overshoots by as little as possible.
        needed_weight = abs(error) / step
        direction = -1.0 if error > 0 else 1.0
        moved = 0.0
        for index in np.argsort(weights):
            if moved >= needed_weight:
                break
            candidate = values[index] + direction * step
            if not low <= candidate <= high:
                continue
            values[index] = candidate
            moved += weights[index]
    return np.clip(np.round(values, decimals), low, high)


def largest_remainder(shares: np.ndarray, total: int) -> np.ndarray:
    """Apportion *total* whole loans across *shares*, losing nothing to rounding."""
    exact = shares / shares.sum() * total
    floors = np.floor(exact).astype(int)
    for index in np.argsort(-(exact - floors))[: total - int(floors.sum())]:
        floors[index] += 1
    return floors


def draw_bucketed_balances(
    rng: np.random.Generator,
    buckets: list[tuple[str, int, float]],
    total: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Draw balances **within** each bucket, so count and balance both hold.

    Returns ``(balances, labels)``. Assigning labels to an already-drawn pool
    can honour the published balance share or the published count share but not
    both — a bucket worth 0.04% of a 2,765-loan pool is about one average loan,
    so the two constraints fight. Drawing each bucket's loans against its own
    stated count and balance satisfies both by construction, which is why the
    arrears distribution is built this way and the rest are not.
    """
    balances: list[np.ndarray] = []
    labels: list[np.ndarray] = []
    for label, count, bucket_balance in buckets:
        if count <= 0:
            continue
        raw = rng.lognormal(mean=0.0, sigma=_BALANCE_SIGMA, size=count)
        scaled = np.round(raw / raw.sum() * bucket_balance, 2)
        scaled[int(np.argmax(scaled))] += round(bucket_balance - scaled.sum(), 2)
        balances.append(scaled)
        labels.append(np.full(count, label, dtype=object))

    pool = np.concatenate(balances)
    pool[int(np.argmax(pool))] += round(total - pool.sum(), 2)
    return pool, np.concatenate(labels)


def assign_by_balance_share(
    balances: np.ndarray, buckets: list[tuple[str, float]]
) -> np.ndarray:
    """Label each loan so every bucket's **balance** share matches the report.

    Largest-deficit allocation: loans are walked largest-first and each is
    given to whichever bucket is currently furthest below its stated balance.
    Filling buckets one at a time instead overshoots each boundary by up to a
    whole loan, which on a 2,765-loan pool is enough to miss the report's own
    shares — the guard in :func:`reconcile` caught exactly that.

    This reproduces each bucket's **balance** share, which is the figure both
    report families state for every stratification. It does not reproduce a
    bucket's loan *count* share: the smallest loans land in the smallest
    buckets, so a tiny bucket ends up holding more loans than the report shows.
    Arrears — the one distribution where that would misstate a credit metric —
    is therefore built the other way round, by :func:`draw_bucketed_balances`.

    Independent per dimension, so the dimensions are uncorrelated: that is
    generated shape, not a published fact, and the fit spec says so.
    """
    labels = np.empty(len(balances), dtype=object)
    names = [name for name, _ in buckets]
    targets = np.array([target for _, target in buckets], dtype=float)
    filled = np.zeros(len(buckets), dtype=float)

    for loan in np.argsort(-balances):
        choice = int(np.argmax(targets - filled))
        labels[loan] = names[choice]
        filled[choice] += float(balances[loan])
    return labels


def _bucket_pairs(distribution: dict) -> list[tuple[str, float]]:
    return [
        (bucket["label"], bucket["balance"])
        for bucket in distribution["buckets"]
        if bucket["balance"] > 0
    ]


def build_tape(spec: dict) -> pd.DataFrame:
    """Build the Annex 2 frame for one fit spec."""
    rng = np.random.default_rng(_seed(spec["deal_id"]))
    fitted = spec["fitted"]
    count = int(fitted["loan_count"]["value"])
    pool_balance = fitted["pool_balance_eur"]["value"]

    # Arrears is the primary dimension: it is the one distribution whose loan
    # *count* share the platform renders (``_extract_arrears`` divides by loan
    # count), so a pool that matched only its balance share would misstate a
    # credit metric. Drawing each bucket's loans against its own published count
    # and balance satisfies both.
    arrears = spec["distributions"]["arrears_bucket"]
    populated = [b for b in arrears["buckets"] if b["balance"] > 0]
    counts = largest_remainder(
        np.array([float(b["count"]) for b in populated]), count
    )
    balances, report_labels = draw_bucketed_balances(
        rng,
        [
            (bucket["label"], int(n), bucket["balance"])
            for bucket, n in zip(populated, counts)
        ],
        pool_balance,
    )

    frame = pd.DataFrame(
        {
            "loan_identifier": [
                f"{spec['deal_id'].upper().replace('-', '')}-{i:07d}" for i in range(count)
            ],
            "transaction_name": spec["deal_name"],
            "reporting_date": spec["reporting_date"],
            "current_balance": balances,
        }
    )

    for column, key in (
        ("current_interest_rate_pct", "wtd_coupon_pct"),
        ("seasoning_months", "wtd_seasoning_months"),
        ("remaining_term_months", "wtd_remaining_term_months"),
        ("cltomv_current", "wtd_ltv_pct"),
    ):
        decimals = 4 if column.endswith("pct") or column == "cltomv_current" else 1
        frame[column] = draw_weighted(
            rng,
            balances,
            fitted[key]["value"],
            spread=_DISPERSION[column],
            bounds=_BOUNDS[column],
            decimals=decimals,
        )

    # The report's buckets collapsed onto the tape's closed vocabulary, with the
    # report bucket's own days floor preserved.
    mapped = [map_arrears_bucket(str(label)) for label in report_labels]
    frame["arrears_bucket"] = [
        "Performing" if bucket == "default" else bucket for bucket, _ in mapped
    ]
    frame["default_crr_flag"] = ["Y" if bucket == "default" else "N" for bucket, _ in mapped]
    frame["days_in_arrears"] = [floor for _, floor in mapped]

    rate_shares = spec["distributions"]["rate_type"]["shares_pct"]
    total_balance = float(balances.sum())
    frame["rate_type"] = assign_by_balance_share(
        balances,
        [(name, pct / 100.0 * total_balance) for name, pct in rate_shares.items() if pct > 0],
    )

    for column in ("province", "property_type", "epc_label"):
        distribution = spec["distributions"].get(column)
        if distribution is None:
            continue  # the report states it in no form; emit no column (#471)
        frame[column] = assign_by_balance_share(balances, _bucket_pairs(distribution))

    return frame


# ---------------------------------------------------------------------------
# The contract: reproduce the fit, or refuse
# ---------------------------------------------------------------------------


def _weighted(frame: pd.DataFrame, column: str) -> float:
    weights = frame["current_balance"]
    return float((frame[column] * weights).sum() / weights.sum())


def reconcile(frame: pd.DataFrame, spec: dict) -> list[str]:
    """Recompute every fitted aggregate from *frame*; raise on any divergence."""
    fitted = spec["fitted"]
    checked: list[str] = []
    problems: list[str] = []

    def check(name: str, got: float, want: float, tolerance: float, unit: str) -> None:
        checked.append(name)
        if abs(got - want) > tolerance:
            problems.append(
                f"{name}: generated {got:,.4f}{unit} against a fitted "
                f"{want:,.4f}{unit} (tolerance {tolerance}{unit})"
            )

    check("loan_count", float(len(frame)), fitted["loan_count"]["value"], 0.0, " loans")
    check(
        "pool_balance_eur",
        float(frame["current_balance"].sum()),
        fitted["pool_balance_eur"]["value"],
        BALANCE_TOLERANCE_EUR,
        " EUR",
    )
    for column, key in (
        ("current_interest_rate_pct", "wtd_coupon_pct"),
        ("seasoning_months", "wtd_seasoning_months"),
        ("remaining_term_months", "wtd_remaining_term_months"),
        ("cltomv_current", "wtd_ltv_pct"),
    ):
        check(key, _weighted(frame, column), fitted[key]["value"], WEIGHTED_TOLERANCE, "")

    total = float(frame["current_balance"].sum())
    for column, distribution in spec["distributions"].items():
        if column == "rate_type":
            wanted = distribution["shares_pct"]
            frame_column = "rate_type"
        elif column == "arrears_bucket":
            continue  # checked below, against the mapped vocabulary
        else:
            wanted = {
                bucket["label"]: bucket["balance"] / total * 100
                for bucket in distribution["buckets"]
                if bucket["balance"] > 0
            }
            frame_column = column
        if frame_column not in frame.columns:
            continue
        got = frame.groupby(frame_column)["current_balance"].sum() / total * 100
        for label, want in wanted.items():
            check(
                f"{column}[{label}]",
                float(got.get(label, 0.0)),
                want,
                SHARE_TOLERANCE_PCT,
                "%",
            )

    # Arrears is checked against the *mapped* shares: the tape's vocabulary is
    # coarser than the report's, so several report buckets collapse into one.
    wanted_arrears: dict[str, float] = {}
    for bucket in spec["distributions"]["arrears_bucket"]["buckets"]:
        tape_bucket, _ = map_arrears_bucket(bucket["label"])
        wanted_arrears[tape_bucket] = wanted_arrears.get(tape_bucket, 0.0) + bucket["balance"]
    got_default = float(frame.loc[frame["default_crr_flag"] == "Y", "current_balance"].sum())
    got_buckets = frame.groupby("arrears_bucket")["current_balance"].sum()
    for tape_bucket, want_balance in wanted_arrears.items():
        got = (
            got_default
            if tape_bucket == "default"
            else float(got_buckets.get(tape_bucket, 0.0))
        )
        if tape_bucket == "Performing":
            got -= got_default  # defaulted loans keep a Performing arrears_bucket
        check(
            f"arrears[{tape_bucket}] balance",
            got / total * 100,
            want_balance / total * 100,
            SHARE_TOLERANCE_PCT,
            "%",
        )

    # ...and against the report's own loan counts, because the platform renders
    # the arrears breakdown by count. One loan is up to 0.04% of the smaller
    # pools and the Dutch report counts loanparts rather than loans, so the
    # tolerance is the coarser of the share tolerance and one whole loan.
    wanted_counts: dict[str, float] = {}
    report_total = sum(
        bucket["count"] for bucket in spec["distributions"]["arrears_bucket"]["buckets"]
    )
    for bucket in spec["distributions"]["arrears_bucket"]["buckets"]:
        tape_bucket, _ = map_arrears_bucket(bucket["label"])
        wanted_counts[tape_bucket] = wanted_counts.get(tape_bucket, 0.0) + bucket["count"]
    loans = len(frame)
    one_loan_pct = 100.0 / loans
    defaulted = int((frame["default_crr_flag"] == "Y").sum())
    counted = frame["arrears_bucket"].value_counts()
    for tape_bucket, want_count in wanted_counts.items():
        if tape_bucket == "default":
            got_count = float(defaulted)
        elif tape_bucket == "Performing":
            got_count = float(counted.get("Performing", 0)) - defaulted
        else:
            got_count = float(counted.get(tape_bucket, 0))
        check(
            f"arrears[{tape_bucket}] count",
            got_count / loans * 100,
            want_count / report_total * 100,
            max(SHARE_TOLERANCE_PCT, one_loan_pct * 1.5),
            "%",
        )

    if problems:
        raise FitDivergence(
            f"{spec['deal_id']}: the generated pool does not reproduce "
            f"{len(problems)} of {len(checked)} fitted aggregates, so it would "
            "contradict the deal's own investor report. Refusing to write it.\n  "
            + "\n  ".join(problems)
        )
    return checked


def tape_filename(spec: dict) -> str:
    """The committed tape's name. Carries ``synthetic`` for the registry census."""
    stamp = spec["reporting_date"].replace("-", "")[:6]
    return f"{spec['deal_id'].replace('-', '_')}_{stamp}_synthetic_loan_tape.csv.gz"


def registered_url(spec: dict) -> str:
    return f"synthetic:{REGISTERED_PREFIX}/{tape_filename(spec)}"


def write_tape(frame: pd.DataFrame, spec: dict) -> Path:
    """Write the frame with a fixed gzip mtime, so the bytes are reproducible."""
    TAPE_DIR.mkdir(parents=True, exist_ok=True)
    destination = TAPE_DIR / tape_filename(spec)
    payload = frame.to_csv(index=False, lineterminator="\n").encode("utf-8")
    with open(destination, "wb") as handle:
        with gzip.GzipFile(fileobj=handle, mode="wb", mtime=0) as gz:
            gz.write(payload)
    return destination


def generate(deal_id: str) -> tuple[Path, int]:
    spec = json.loads(fit_path(deal_id).read_text(encoding="utf-8"))
    frame = build_tape(spec)
    checked = reconcile(frame, spec)
    return write_tape(frame, spec), len(checked)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--deal", action="append", dest="deals", help="limit to one deal id")
    args = parser.parse_args(argv)

    for deal_id in args.deals or sorted(DEALS):
        path, checks = generate(deal_id)
        size = path.stat().st_size
        print(
            f"  tape     {deal_id}: {path.name} ({size:,} bytes, "
            f"{checks} fitted aggregates reproduced)"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
