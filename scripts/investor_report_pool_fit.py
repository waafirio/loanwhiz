"""Read a deal's published pool aggregates out of its investor report (#484).

Four registered deals publish **no loan tape at all**, so every pool-driven
chart is empty for them. #484 gives each a synthetic Annex 2 pool — but a
synthetic pool that contradicts the deal's own investor reports is worse than
no pool, so the pool has to be *fitted* to figures the deal itself states.

This module is where those figures come from. It is an **offline authoring
tool**, the same shape as ``seed_deal_models.py``: ``--refresh`` fetches the
published PDFs and writes a committed text fixture per deal, and the default
run parses those fixtures into a committed **fit spec** under
``src/loanwhiz/data/pool_fits/``. Nothing here runs at request time, and no
figure reaches a fit spec without the report section it was read from named
beside it.

Two report families, both published by ING, both parseable deterministically
with ``pypdf`` — no LLM is involved, so the output is reproducible from the
repo alone:

``dutch_portfolio_performance``
    Green Lion 2023-1 / 2024-1. A 42-page *Portfolio and Performance Report*
    whose section 1 (Key Characteristics) states the pool aggregates and whose
    sections 2–30 are stratification tables — including Property Description
    and Energy Performance Certificate, which is why only these two deals get
    an Annex-2-conforming tape (see ``ABSENT_IBERIAN`` below).

``iberian_monthly``
    Leone Arancio 2023-1 (Italy) and Sol-Lion II (Spain). A ~32-page *Monthly
    Investor Report* whose section 1 (Summary) states the same aggregate vector
    in current/at-issue column pairs, with its own stratification tables.

Both families lay a table out the same way in extracted text — a run of header
lines, then one label line per bucket followed by that bucket's values, then a
``Total`` row — so one reader serves both once it knows how many value columns
to expect.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "investor_reports"
FIT_DIR = REPO_ROOT / "src" / "loanwhiz" / "data" / "pool_fits"

PAGE_MARKER = "=== page {n} ==="
_PAGE_RE = re.compile(r"^=== page (\d+) ===$", re.M)

#: A line that is purely a number, a thousands-separated amount, or a
#: percentage. Deliberately does not match a date (``28-05-2026``) or a bucket
#: label (``0.51% - 1.01%``): both carry an interior ``-``.
_NUMERIC_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?%?$")


class ReportParseError(ValueError):
    """A fit figure the report is expected to state could not be read.

    Raised rather than defaulted. A missing aggregate means the generated pool
    would be fitted to something invented, which is the failure this whole
    module exists to prevent.
    """


# ---------------------------------------------------------------------------
# Text extraction and section location
# ---------------------------------------------------------------------------


def report_text(pdf_bytes: bytes) -> str:
    """Extract *pdf_bytes* to page-marked plain text.

    Page markers keep a fixture readable and let a citation name the page the
    figure was read from, which is what makes a fitted number checkable against
    the published document.
    """
    from pypdf import PdfReader  # local import: only the --refresh path needs it

    import io

    reader = PdfReader(io.BytesIO(pdf_bytes))
    parts = []
    for number, page in enumerate(reader.pages, start=1):
        parts.append(PAGE_MARKER.format(n=number))
        parts.append(page.extract_text() or "")
    return "\n".join(parts)


def pages(text: str) -> list[tuple[int, str]]:
    """Split page-marked *text* into ``(page_number, page_text)`` pairs."""
    splits = _PAGE_RE.split(text)
    # splits == ["", "1", "<page 1>", "2", "<page 2>", ...]
    return [
        (int(splits[i]), splits[i + 1]) for i in range(1, len(splits) - 1, 2)
    ]


def find_section(text: str, heading: re.Pattern[str]) -> tuple[int, list[str]]:
    """Return ``(page_number, lines)`` for the section whose heading matches.

    Sections are located by their **heading text**, never by page index: the
    two Iberian reports run to 32 and 34 pages and number their sections
    differently, so an index would silently read the wrong table.
    """
    for number, page_text in pages(text):
        lines = [line.strip() for line in page_text.splitlines()]
        for index, line in enumerate(lines):
            if heading.match(line):
                return number, [candidate for candidate in lines[index:] if candidate]
    raise ReportParseError(f"no section matching {heading.pattern!r} in this report")


# ---------------------------------------------------------------------------
# The two table shapes
# ---------------------------------------------------------------------------


def _as_number(token: str) -> float:
    return float(token.rstrip("%").replace(",", ""))


def _is_numeric(line: str) -> bool:
    return bool(_NUMERIC_RE.match(line))


def parse_label_values(lines: Iterable[str]) -> list[tuple[str, list[float]]]:
    """Group *lines* into ``(label, values)`` pairs.

    Both report families extract as alternating runs: some non-numeric lines,
    then the numeric values belonging to the **last** of them. A bucket the
    report lists but does not populate (``B``, ``C``, ``D`` in an EPC table
    where the pool has none) therefore comes back with an empty value list —
    which is a real statement by the report ("this bucket exists and is
    empty"), distinct from a bucket the report does not list at all.
    """
    grouped: list[tuple[str, list[float]]] = []
    label: str | None = None
    values: list[float] = []

    for line in lines:
        if _is_numeric(line):
            values.append(_as_number(line))
            continue
        if label is not None:
            grouped.append((label, values))
        label, values = line, []
    if label is not None:
        grouped.append((label, values))
    return grouped


def key_values(lines: Iterable[str]) -> dict[str, list[float]]:
    """Read a ``label / current / at-issue`` summary block into a mapping.

    The first value is the current-period one in both families (the Dutch
    report's columns are "As per Reporting Date" then "As per Closing Date";
    the Iberian report's are "Current" then "At Issue").
    """
    return {label: values for label, values in parse_label_values(lines) if values}


def stratification(lines: Iterable[str], columns: int) -> list[tuple[str, list[float]]]:
    """Read a stratification table into its buckets, header and totals dropped.

    *columns* is how many values a fully populated bucket row carries. Rows
    with a different count are **kept but flagged** by the caller rather than
    silently reinterpreted: the extracted text of these tables occasionally
    drops a cell, and guessing which column a lone value belongs to is how a
    distribution quietly becomes wrong.
    """
    grouped = parse_label_values(lines)
    started = False
    buckets: list[tuple[str, list[float]]] = []
    for label, values in grouped:
        if not started:
            # The header is the run of labels before the first populated row.
            if len(values) != columns:
                continue
            started = True
        if label.lower().startswith("total"):
            break
        buckets.append((label, values))
    return buckets


# ---------------------------------------------------------------------------
# Fit spec
# ---------------------------------------------------------------------------


@dataclass
class Fitted:
    """One published aggregate the generated pool must reproduce."""

    value: float
    unit: str
    cited: str

    def as_json(self) -> dict:
        return {"value": self.value, "unit": self.unit, "cited": self.cited}


@dataclass
class PoolFit:
    """Everything a synthetic pool for one deal-period is fitted to."""

    deal_id: str
    deal_name: str
    family: str
    report_period: str
    report_url: str
    reporting_date: str
    fitted: dict[str, Fitted]
    distributions: dict[str, dict] = field(default_factory=dict)
    absent: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_json(self) -> dict:
        return {
            "format_version": 1,
            "deal_id": self.deal_id,
            "deal_name": self.deal_name,
            "source": {
                "family": self.family,
                "period": self.report_period,
                "url": self.report_url,
            },
            "reporting_date": self.reporting_date,
            "fitted": {name: f.as_json() for name, f in sorted(self.fitted.items())},
            "distributions": {
                name: dist for name, dist in sorted(self.distributions.items())
            },
            "absent": self.absent,
            "notes": self.notes,
        }


def _require(block: dict[str, list[float]], label: str, where: str) -> float:
    """Return the current-period value stated against *label*, or refuse."""
    if label not in block:
        raise ReportParseError(
            f"{where}: expected a figure against {label!r}; the report states none. "
            "Refusing rather than fitting the pool to an invented aggregate."
        )
    return block[label][0]


def _distribution(
    buckets: list[tuple[str, list[float]]],
    *,
    balance_index: int,
    count_index: int,
    cited: str,
) -> dict:
    """Shape a parsed stratification table into the fit spec's distribution form."""
    entries = []
    for label, values in buckets:
        if not values:
            entries.append({"label": label, "balance": 0.0, "count": 0})
            continue
        if max(balance_index, count_index) >= len(values):
            continue
        entries.append(
            {
                "label": label,
                "balance": values[balance_index],
                "count": int(values[count_index]),
            }
        )
    return {"cited": cited, "buckets": entries}


# ---------------------------------------------------------------------------
# Rate type: the one classification this module makes
# ---------------------------------------------------------------------------

_FIXED_RE = re.compile(r"fixed", re.I)
_FLOATING_RE = re.compile(r"float|euribor|libor|bce|boe|^\d+\s*M$", re.I)


def classify_rate_type(label: str) -> str:
    """Map an Interest Type bucket label onto ``Fixed`` / ``Floating``.

    The Iberian reports nest their floating buckets — ``Floating Rate EURIBOR``
    is an empty parent above ``1M`` and ``3M`` rows carrying the balances — so a
    label cannot be read positionally. It can be read *lexically*: every fixed
    bucket in both families says "Fixed", and every remaining populated bucket
    names a floating index or one of its tenors.

    A label matching neither raises. Guessing would silently move a quarter of
    Leone Arancio's pool between rate types, and the caller's 100% check would
    not notice because the shares would still sum.
    """
    if _FIXED_RE.search(label):
        return "Fixed"
    if _FLOATING_RE.search(label):
        return "Floating"
    raise ReportParseError(
        f"interest-type bucket {label!r} is neither fixed nor floating by its "
        "label. Refusing to assign it rather than guessing a rate type."
    )


def rate_type_shares(buckets: list[tuple[str, list[float]]], share_index: int) -> dict[str, float]:
    """Collapse an Interest Type table into ``{Fixed: pct, Floating: pct}``.

    Refuses unless the populated buckets account for the whole pool — a nested
    table that lost a row would otherwise produce a confident, wrong mix.
    """
    shares = {"Fixed": 0.0, "Floating": 0.0}
    for label, values in buckets:
        if not values or share_index >= len(values):
            continue
        shares[classify_rate_type(label)] += values[share_index]

    total = sum(shares.values())
    if abs(total - 100.0) > 0.5:
        raise ReportParseError(
            f"interest-type buckets account for {total:.2f}% of the pool, not 100%. "
            "The table did not parse cleanly; refusing to fit a rate-type mix to it."
        )
    return {name: round(pct, 4) for name, pct in shares.items()}


# ---------------------------------------------------------------------------
# Arrears: the report's buckets onto the tape's closed vocabulary
# ---------------------------------------------------------------------------

#: The tape's ``arrears_bucket`` vocabulary is closed and narrow —
#: ``esma_tape_normaliser`` reads exactly ``"<29d"``, ``"180+d"`` and
#: ``default_crr_flag == "Y"``, and drops everything else into *current*. Both
#: report families publish finer buckets than that, so writing their own words
#: into the column would land every delinquent loan in ``current_pct``: the
#: silent clean pool #471 records. The mapping is therefore explicit, recorded
#: in every fit spec, and prudent where the vocabulary cannot express the
#: report's bucket — a loan 90+ days down that the report has not called
#: defaulted maps to ``180+d`` rather than to the mild bucket.
#:
#: ``days_in_arrears`` carries the report bucket's own lower bound, so the
#: finer statement survives in the tape even though this column cannot hold it.
ARREARS_MAP: tuple[tuple[str, str, int], ...] = (
    # (matching pattern on the report's bucket label, tape bucket, days floor)
    (r"^(no arrear|performing)", "Performing", 0),
    (r"^<\s*29", "<29d", 1),
    (r"^30\s*(-|days)", "<29d", 30),
    (r"^60\s*(-|days)", "<29d", 60),
    (r"^90\s*(-|days)", "180+d", 90),
    (r"^120\s*(-|days)", "180+d", 120),
    (r"^150\s*(-|days)", "180+d", 150),
    (r"^180\s*(-|days|>)", "180+d", 180),
    (r"^default", "default", 365),
    # Forbearance, not delinquency. Leone Arancio states a Payment Holiday
    # bucket outside its days-past-due ladder, so the report does not say these
    # loans are in arrears — but the tape's vocabulary cannot say "forbearance"
    # either. They are written as Performing and the fit spec records the share
    # that was moved, so the concession is visible rather than absorbed.
    (r"^payment holiday", "Performing", 0),
)

#: Report buckets that map onto ``Performing`` without being the report's own
#: "no arrears" row. Each one is a claim the tape cannot express, so it is
#: named in the fit spec's notes with the share it covers.
_FORBEARANCE_RE = re.compile(r"^payment holiday", re.I)


def map_arrears_bucket(label: str) -> tuple[str, int]:
    """Map a report arrears-bucket label to ``(tape_bucket, days_floor)``.

    ``tape_bucket`` is ``"default"`` for the report's own default bucket, which
    the generator writes as ``default_crr_flag == "Y"`` rather than as an
    ``arrears_bucket`` value — the normaliser reads default from the flag and
    gives it priority over every arrears bucket.

    Raises rather than defaulting: an unmapped bucket would silently become
    *current*, which is the one outcome this mapping exists to prevent.
    """
    cleaned = label.strip().lower()
    for pattern, bucket, floor in ARREARS_MAP:
        if re.match(pattern, cleaned):
            return bucket, floor
    raise ReportParseError(
        f"arrears bucket {label!r} maps onto no tape bucket. Refusing rather "
        "than letting it fall through to 'current', which would report these "
        "loans as performing."
    )


#: How far a stratification table's balances may sit from the pool balance the
#: same report states. The Iberian tables are stated to whole euros against a
#: cent-precise total, so the tolerance is relative and tiny — big enough for
#: that rounding, far too small to hide a dropped row.
RECONCILE_TOLERANCE_PCT = 0.01


def reconcile_distribution(distribution: dict, pool_balance: float, name: str) -> None:
    """Refuse a distribution whose balances contradict the report's own total.

    #469's rule, one layer up: reconcile a parse against the source document's
    own stated aggregate before anything downstream trusts it, and refuse
    rather than return on a divergence. A stratification table that lost a row
    to the text extraction still *looks* like a distribution — every bucket
    plausible, the shares summing to something near 100 — so nothing but this
    check distinguishes it from a clean parse.
    """
    total = sum(bucket["balance"] for bucket in distribution["buckets"])
    if pool_balance == 0:
        raise ReportParseError("pool balance is zero; nothing to reconcile against")
    drift_pct = abs(total - pool_balance) / pool_balance * 100
    if drift_pct > RECONCILE_TOLERANCE_PCT:
        raise ReportParseError(
            f"{name} ({distribution['cited']}) sums to {total:,.2f} against a "
            f"stated pool balance of {pool_balance:,.2f} — {drift_pct:.4f}% off. "
            "The table did not parse cleanly; refusing to fit a pool to it."
        )


# ---------------------------------------------------------------------------
# The reporting cut-off, read from the report rather than assumed
# ---------------------------------------------------------------------------

_MONTHS = {
    m: i
    for i, m in enumerate(
        "jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1
    )
}
_DMY_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")
_TEXT_DATE_RE = re.compile(r"^(\d{1,2})\s+([A-Za-z]{3})[a-z]*\s+(\d{4})$")


def parse_cutoff(lines: Iterable[str], label: re.Pattern[str]) -> str:
    """Return the ISO cut-off date stated against *label*.

    Read rather than assumed: Leone Arancio's May 2026 report carries a
    31-03-2026 portfolio cut-off while Sol-Lion II's carries 30-04-2026, so a
    date inferred from the report's own period would be wrong for one of them —
    and the reporting date is what every downstream period is keyed on.
    """
    rows = [line.strip() for line in lines if line.strip()]
    for index, line in enumerate(rows[:-1]):
        if not label.match(line):
            continue
        for candidate in rows[index + 1 : index + 3]:
            dmy = _DMY_RE.match(candidate)
            if dmy:
                day, month, year = dmy.groups()
                return f"{year}-{month}-{day}"
            text = _TEXT_DATE_RE.match(candidate)
            if text:
                day, month, year = text.groups()
                return f"{year}-{_MONTHS[month.lower()]:02d}-{int(day):02d}"
    raise ReportParseError(
        f"no cut-off date stated against {label.pattern!r}; refusing to infer "
        "the reporting date from the report's period label."
    )


# ---------------------------------------------------------------------------
# The two family readers
# ---------------------------------------------------------------------------

#: Standard Dutch stratification row: balance, % of total, count, % of total,
#: WA coupon, WA maturity, WA CLTOMV, % of notional at closing.
_NL_COLUMNS = 8
_NL_BALANCE, _NL_COUNT = 0, 2
_NL_SHARE = 1
#: The Dutch Delinquencies table leads with the arrears amount, so its balance
#: sits one column further right than every other table on the same report.
_NL_DELINQ_BALANCE, _NL_DELINQ_COUNT = 1, 3

#: Iberian stratification row: current balance, % of total, count, % of total,
#: then the same four at issue.
_IB_COLUMNS = 8
_IB_BALANCE, _IB_COUNT, _IB_SHARE = 0, 2, 1
#: The Iberian Arrears table: count, principal, interest, total arrears,
#: outstanding notional, % of total, % of total.
_IB_ARREARS_COLUMNS = 7
_IB_ARREARS_BALANCE, _IB_ARREARS_COUNT = 4, 0

#: Canonical Annex 2 columns the Iberian reports state in no form. Declared so
#: a consumer can tell "this source does not publish it" from "it is empty".
ABSENT_IBERIAN = [
    {
        "canonical_column": "epc_label",
        "rts_code": "RREL17",
        "reason": (
            "The Monthly Investor Report publishes no energy-performance "
            "stratification; the Dutch Portfolio and Performance Report does."
        ),
    },
    {
        "canonical_column": "property_type",
        "rts_code": "RREL16",
        "reason": (
            "The Monthly Investor Report stratifies by loan product and "
            "occupancy, neither of which is the property's type."
        ),
    },
]

_ARREARS_NOTE = (
    "arrears_bucket carries the tape's closed vocabulary, not the report's own "
    "words: the normaliser reads only '<29d', '180+d' and default_crr_flag, so "
    "the report's finer buckets are mapped (see ARREARS_MAP) and the report "
    "bucket's own lower bound is preserved in days_in_arrears."
)
_NL_LOANPART_NOTE = (
    "The Dutch report counts Delinquencies by loanpart and Property/EPC/province "
    "by loan, so only the balance column of each distribution is comparable "
    "across tables; the generated tape is loan-level and is fitted on balance."
)
_SHAPE_NOTE = (
    "Only the aggregates under 'fitted' and the distributions under "
    "'distributions' are fitted. Per-loan dispersion within a bucket is "
    "generated, not published, and no figure computed from it is evidence "
    "about the real pool."
)


def forbearance_notes(distribution: dict, pool_balance: float) -> list[str]:
    """Note any bucket written as Performing that the report did not call current.

    The tape's ``arrears_bucket`` has three states and a default flag; a
    forbearance bucket fits none of them. Writing those loans as Performing is
    the least-wrong option — the report places them outside its days-past-due
    ladder, so it does not state them as delinquent — but it is still a claim
    the source does not make, so it is recorded here rather than absorbed.
    """
    notes = []
    for bucket in distribution["buckets"]:
        if not _FORBEARANCE_RE.match(bucket["label"].strip()):
            continue
        share = bucket["balance"] / pool_balance * 100 if pool_balance else 0.0
        notes.append(
            f"{bucket['label']} ({share:.3f}% of balance, {bucket['count']:,} loans, "
            f"{distribution['cited']}) sits outside the report's days-past-due "
            "ladder. The tape's arrears vocabulary cannot express forbearance, so "
            "these loans are written as Performing; the report does not state "
            "them as in arrears."
        )
    return notes


def build_dutch_fit(text: str, *, deal_id, deal_name, period, url) -> PoolFit:
    """Read a Dutch *Portfolio and Performance Report* into a fit spec."""
    _, date_lines = find_section(text, re.compile(r"^Securitisation Dates"))
    reporting_date = parse_cutoff(date_lines, re.compile(r"^Portfolio Cut-off Date"))
    key_page, key_lines = find_section(text, re.compile(r"^1\.\s+Key Characteristics"))
    block = key_values(key_lines)
    where = f"section 1 Key Characteristics (p{key_page})"

    fitted = {
        "loan_count": Fitted(
            _require(block, "Number of loans", where), "loans", f"{where} · Number of loans"
        ),
        "pool_balance_eur": Fitted(
            _require(block, "Net principal balance", where),
            "EUR",
            f"{where} · Net principal balance",
        ),
        "wtd_coupon_pct": Fitted(
            _require(block, "Weighted average current interest rate", where),
            "percent",
            f"{where} · Weighted average current interest rate",
        ),
        "wtd_seasoning_months": Fitted(
            round(_require(block, "Weighted average seasoning (in years)", where) * 12, 4),
            "months",
            f"{where} · Weighted average seasoning (in years), x12",
        ),
        "wtd_remaining_term_months": Fitted(
            round(_require(block, "Weighted average maturity (in years)", where) * 12, 4),
            "months",
            f"{where} · Weighted average maturity (in years), x12",
        ),
        "wtd_ltv_pct": Fitted(
            _require(block, "Weighted average CLTOMV", where),
            "percent",
            f"{where} · Weighted average CLTOMV",
        ),
    }

    distributions = {}
    delinq_page, delinq_lines = find_section(text, re.compile(r"^2\.\s+Delinquencies"))
    distributions["arrears_bucket"] = _distribution(
        stratification(delinq_lines, _NL_COLUMNS),
        balance_index=_NL_DELINQ_BALANCE,
        count_index=_NL_DELINQ_COUNT,
        cited=f"section 2 Delinquencies (p{delinq_page})",
    )

    for name, pattern, title in (
        ("property_type", r"^15\.\s+Property Description", "15 Property Description"),
        ("epc_label", r"^21\.\s+Energy Performance Certificate", "21 Energy Performance Certificate"),
        ("province", r"^16\.\s+Geographical Distribution \(by province\)", "16 Geographical Distribution (by province)"),
    ):
        page, lines = find_section(text, re.compile(pattern))
        distributions[name] = _distribution(
            stratification(lines, _NL_COLUMNS),
            balance_index=_NL_BALANCE,
            count_index=_NL_COUNT,
            cited=f"section {title} (p{page})",
        )

    for name, distribution in distributions.items():
        reconcile_distribution(distribution, fitted["pool_balance_eur"].value, name)

    rate_page, rate_lines = find_section(text, re.compile(r"^14\.\s+Interest Payment Type"))
    distributions["rate_type"] = {
        "cited": f"section 14 Interest Payment Type (p{rate_page})",
        "shares_pct": rate_type_shares(
            stratification(rate_lines, _NL_COLUMNS), _NL_SHARE
        ),
    }

    return PoolFit(
        deal_id=deal_id,
        deal_name=deal_name,
        family="dutch_portfolio_performance",
        report_period=period,
        report_url=url,
        reporting_date=reporting_date,
        fitted=fitted,
        distributions=distributions,
        absent=[],
        notes=[_ARREARS_NOTE, _SHAPE_NOTE, _NL_LOANPART_NOTE]
        + forbearance_notes(
            distributions["arrears_bucket"], fitted["pool_balance_eur"].value
        ),
    )


#: The Iberian reports do not agree on what they call current LTV: Sol-Lion II
#: states "Current Loan to Indexed Market Value", Leone Arancio "Loan to Market
#: Value" (its *original* measure is labelled separately). Both are the deal's
#: current loan-to-value; the citation records which one was read.
_IB_LTV_LABELS = (
    "Weighted Average Current Loan to Indexed Market Value",
    "Weighted Average Loan to Indexed Market Value",
    "Weighted Average Loan to Market Value",
)


def build_iberian_fit(text: str, *, deal_id, deal_name, period, url) -> PoolFit:
    """Read an Iberian *Monthly Investor Report* into a fit spec."""
    summary_page, summary_lines = find_section(text, re.compile(r"^1\.\s+Summary"))
    reporting_date = parse_cutoff(summary_lines, re.compile(r"^Portfolio Cut off Date"))
    block = key_values(summary_lines)
    where = f"section 1 Summary (p{summary_page})"

    ltv_label = next((label for label in _IB_LTV_LABELS if label in block), None)
    if ltv_label is None:
        raise ReportParseError(
            f"{where}: the report states no current loan-to-value under any of "
            f"{_IB_LTV_LABELS}. Refusing to fit an invented LTV."
        )

    fitted = {
        "loan_count": Fitted(
            _require(block, "Number of Loans", where), "loans", f"{where} · Number of Loans"
        ),
        "pool_balance_eur": Fitted(
            _require(block, "Of which Active Outstanding Notional Amount", where),
            "EUR",
            f"{where} · Of which Active Outstanding Notional Amount",
        ),
        "wtd_coupon_pct": Fitted(
            _require(block, "Coupon: Weighted Average", where),
            "percent",
            f"{where} · Coupon: Weighted Average",
        ),
        "wtd_seasoning_months": Fitted(
            _require(block, "Seasoning (months): Weighted Average", where),
            "months",
            f"{where} · Seasoning (months): Weighted Average",
        ),
        "wtd_remaining_term_months": Fitted(
            _require(block, "Remaining Tenor (months): Weighted Average", where),
            "months",
            f"{where} · Remaining Tenor (months): Weighted Average",
        ),
        "wtd_ltv_pct": Fitted(
            block[ltv_label][0], "percent", f"{where} · {ltv_label}"
        ),
    }

    arrears_page, arrears_lines = find_section(text, re.compile(r"^\d+\.\s+Arrears"))
    distributions = {
        "arrears_bucket": _distribution(
            stratification(arrears_lines, _IB_ARREARS_COLUMNS),
            balance_index=_IB_ARREARS_BALANCE,
            count_index=_IB_ARREARS_COUNT,
            cited=f"section Arrears (p{arrears_page})",
        )
    }

    geo_page, geo_lines = find_section(text, re.compile(r"^\d+\.\s+Geography Region"))
    distributions["province"] = _distribution(
        stratification(geo_lines, _IB_COLUMNS),
        balance_index=_IB_BALANCE,
        count_index=_IB_COUNT,
        cited=f"section Geography Region (p{geo_page})",
    )

    for name, distribution in distributions.items():
        reconcile_distribution(distribution, fitted["pool_balance_eur"].value, name)

    rate_page, rate_lines = find_section(text, re.compile(r"^\d+\.\s+Interest Type"))
    distributions["rate_type"] = {
        "cited": f"section Interest Type (p{rate_page})",
        "shares_pct": rate_type_shares(
            stratification(rate_lines, _IB_COLUMNS), _IB_SHARE
        ),
    }

    return PoolFit(
        deal_id=deal_id,
        deal_name=deal_name,
        family="iberian_monthly",
        report_period=period,
        report_url=url,
        reporting_date=reporting_date,
        fitted=fitted,
        distributions=distributions,
        absent=ABSENT_IBERIAN,
        notes=[_ARREARS_NOTE, _SHAPE_NOTE]
        + forbearance_notes(
            distributions["arrears_bucket"], fitted["pool_balance_eur"].value
        ),
    )


BUILDERS = {
    "dutch_portfolio_performance": build_dutch_fit,
    "iberian_monthly": build_iberian_fit,
}

#: Which report family each tape-less deal belongs to. The fitted period and
#: its cut-off are both read from the report itself, never asserted here.
DEALS: dict[str, str] = {
    "green-lion-2023-1": "dutch_portfolio_performance",
    "green-lion-2024-1": "dutch_portfolio_performance",
    "leone-arancio-2023-1": "iberian_monthly",
    "sol-lion-ii": "iberian_monthly",
}


def fixture_path(deal_id: str) -> Path:
    return FIXTURE_DIR / f"{deal_id}.txt"


def fit_path(deal_id: str) -> Path:
    return FIT_DIR / f"{deal_id}.json"


def _latest_report(deal_id: str) -> dict:
    from loanwhiz.config import DEAL_REGISTRY

    reports = DEAL_REGISTRY[deal_id].get("investor_report_urls") or []
    if not reports:
        raise ReportParseError(f"{deal_id} registers no investor report to fit to")
    return reports[-1]


def build_fit(deal_id: str) -> PoolFit:
    """Parse the committed fixture for *deal_id* into its fit spec."""
    from loanwhiz.config import DEAL_REGISTRY

    report = _latest_report(deal_id)
    text = fixture_path(deal_id).read_text(encoding="utf-8")
    return BUILDERS[DEALS[deal_id]](
        text,
        deal_id=deal_id,
        deal_name=DEAL_REGISTRY[deal_id]["deal_name"],
        period=report["period"],
        url=report["url"],
    )


def refresh_fixtures(deal_ids: Iterable[str]) -> None:
    """Re-fetch each deal's latest published report and rewrite its fixture."""
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    for deal_id in deal_ids:
        report = _latest_report(deal_id)
        with urllib.request.urlopen(report["url"], timeout=180) as response:
            payload = response.read()
        fixture_path(deal_id).write_text(report_text(payload), encoding="utf-8")
        print(f"  fixture  {deal_id}: {report['period']} ({len(payload):,} PDF bytes)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="re-fetch the published PDFs and rewrite the committed text fixtures",
    )
    parser.add_argument("--deal", action="append", dest="deals", help="limit to one deal id")
    args = parser.parse_args(argv)

    deal_ids = args.deals or sorted(DEALS)
    if args.refresh:
        refresh_fixtures(deal_ids)

    FIT_DIR.mkdir(parents=True, exist_ok=True)
    for deal_id in deal_ids:
        fit = build_fit(deal_id)
        fit_path(deal_id).write_text(
            json.dumps(fit.as_json(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(
            f"  fit      {deal_id}: {fit.fitted['loan_count'].value:,.0f} loans, "
            f"EUR {fit.fitted['pool_balance_eur'].value:,.2f}, "
            f"WAC {fit.fitted['wtd_coupon_pct'].value}%"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
