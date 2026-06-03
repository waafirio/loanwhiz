"""Green Lion 2026-1 B.V. — HuggingFace data loader.

Primary data source for the LoanWhiz hackathon demo (10 June 2026).

Why this module exists
----------------------
The deeploans backend (see ``loanwhiz.data.deeploans_client``) is the
canonical ESMA tape data source when running locally, but it requires a
running FastAPI process and (optionally) a BigQuery connection that may not
be available in the demo environment. This module provides a zero-dependency
fallback: it loads Green Lion 2026-1 data **directly from HuggingFace** using
only ``pandas`` (already a project dependency) and Python's standard library.

Green Lion 2026-1 B.V. is a Dutch RMBS / consumer ABS deal. Algoritmica.ai
published a complete synthetic dataset on HuggingFace under
``Algoritmica/green-lion-2026``, comprising three monthly ESMA loan tapes and
three monthly investor reports — the only publicly available complete deal
package for the hackathon.

Usage
-----
>>> from loanwhiz.data.green_lion import load_tape, list_tapes
>>> tape_df = load_tape("2026-02-28")   # fetches from HuggingFace
>>> tape_df.shape
(N, M)

Constants are imported from ``loanwhiz.config`` to ensure there is exactly
one source of truth for URLs across the project.
"""

from __future__ import annotations

import logging

import pandas as pd

from loanwhiz.config import GREEN_LION, HF_BASE  # noqa: F401 — re-exported for convenience

logger = logging.getLogger(__name__)

# Convenience re-exports so callers can do:
#   from loanwhiz.data.green_lion import DEAL_NAME, PROSPECTUS_URL
DEAL_NAME: str = GREEN_LION["deal_name"]
PROSPECTUS_URL: str = GREEN_LION["prospectus_url"]


def list_tapes() -> list[dict[str, str]]:
    """Return metadata for all available ESMA loan tape snapshots.

    Returns
    -------
    list[dict[str, str]]
        Each entry has keys ``"date"`` (ISO date string, e.g. ``"2026-02-28"``)
        and ``"url"`` (direct HuggingFace download URL for the CSV).

    Examples
    --------
    >>> tapes = list_tapes()
    >>> [t["date"] for t in tapes]
    ['2026-02-28', '2026-03-31', '2026-04-30']
    """
    return list(GREEN_LION["tape_urls"])


def list_investor_reports() -> list[dict[str, str]]:
    """Return metadata for all available monthly investor report PDFs.

    Returns
    -------
    list[dict[str, str]]
        Each entry has keys ``"period"`` (human-readable, e.g. ``"February 2026"``)
        and ``"url"`` (direct HuggingFace download URL for the PDF).

    Examples
    --------
    >>> reports = list_investor_reports()
    >>> [r["period"] for r in reports]
    ['February 2026', 'March 2026', 'April 2026']
    """
    return list(GREEN_LION["investor_report_urls"])


def load_tape(date: str) -> pd.DataFrame:
    """Fetch an ESMA loan tape CSV from HuggingFace and return it as a DataFrame.

    Parameters
    ----------
    date:
        ISO date string matching one of the available tape snapshots:
        ``"2026-02-28"``, ``"2026-03-31"``, or ``"2026-04-30"``.

    Returns
    -------
    pd.DataFrame
        All columns from the ESMA loan tape CSV, with no transformations
        applied (raw field names and values as published by Algoritmica.ai).

    Raises
    ------
    ValueError
        If *date* does not match any known tape snapshot.
    IOError / requests.HTTPError
        If the HuggingFace endpoint is unreachable or returns an error.

    Examples
    --------
    >>> df = load_tape("2026-02-28")
    >>> df.columns.tolist()[:5]  # first five ESMA field names
    [...]
    """
    tapes = {entry["date"]: entry["url"] for entry in GREEN_LION["tape_urls"]}
    if date not in tapes:
        available = sorted(tapes.keys())
        raise ValueError(
            f"Unknown tape date {date!r}. Available dates: {available}"
        )
    url = tapes[date]
    logger.info("Loading Green Lion tape %s from %s", date, url)
    df = pd.read_csv(url)
    logger.info("Loaded %d rows, %d columns from tape %s", len(df), len(df.columns), date)
    return df


def load_all_tapes() -> dict[str, pd.DataFrame]:
    """Fetch all three monthly ESMA loan tape CSVs and return them keyed by date.

    Convenience wrapper over :func:`load_tape` that loads every available
    snapshot in chronological order. Useful for multi-period pool analytics.

    Returns
    -------
    dict[str, pd.DataFrame]
        ``{"2026-02-28": df1, "2026-03-31": df2, "2026-04-30": df3}``
    """
    return {entry["date"]: load_tape(entry["date"]) for entry in GREEN_LION["tape_urls"]}
