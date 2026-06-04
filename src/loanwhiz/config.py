"""Central config — GCP project, model names, dataset URLs."""

from __future__ import annotations

import calendar
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

GCP_PROJECT = "loanwhiz"
GCP_LOCATION = "us-central1"

# Gemini 2.5 Flash for orchestration/planning (fast, 1M context)
# Gemini 2.5 Pro for extraction tasks (highest quality)
MODEL_FLASH = "gemini-2.5-flash"
MODEL_PRO = "gemini-2.5-pro"

# 2026 tapes + investor reports live in the current Hackathon repo.
HF_BASE = "https://huggingface.co/datasets/Algoritmica/green-lion-2026/resolve/main/Hackathon_Data"
# The 2024–2025 monthly history lives in a *separate* repo with a *different*
# base — note there is no ``Hackathon_Data/`` path segment here.
HF_HISTORICAL_BASE = (
    "https://huggingface.co/datasets/Algoritmica/green-lion-2024-2025/resolve/main"
)


def _month_end(year: int, month: int) -> str:
    """Return the month-end date as ``YYYY-MM-DD`` (leap-year aware).

    ``calendar.monthrange`` returns ``(weekday, days_in_month)``; the second
    value is the last day, so Feb-2024 correctly yields ``2024-02-29``.
    """
    last_day = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-{last_day:02d}"


def _historical_tape_entries() -> list[dict]:
    """Build the 24 monthly historical tape entries (2024-01 … 2025-12).

    Every month Jan-2024 through Dec-2025 has a tape in ``HF_HISTORICAL_BASE``
    named ``green_lion_<YYYYMM>_1_synthetic_loan_tape.csv``, keyed by its
    month-end date. Built programmatically (a loop) rather than hand-typed.
    """
    entries: list[dict] = []
    for year in (2024, 2025):
        for month in range(1, 13):
            stamp = f"{year:04d}{month:02d}"
            entries.append(
                {
                    "date": _month_end(year, month),
                    "url": (
                        f"{HF_HISTORICAL_BASE}/"
                        f"green_lion_{stamp}_1_synthetic_loan_tape.csv"
                    ),
                }
            )
    return entries


GREEN_LION = {
    "deal_name": "Green Lion 2026-1 B.V.",
    "prospectus_url": f"{HF_BASE}/green-lion-2026-1-prospectus.pdf",
    # 27 chronological monthly tapes: 24 historical (2024-01 … 2025-12, from
    # HF_HISTORICAL_BASE) + the existing 3 for 2026 (Feb/Mar/Apr, from HF_BASE).
    # Jan-2026 (202601) exists in neither repo and is intentionally absent.
    "tape_urls": _historical_tape_entries() + [
        {"date": "2026-02-28", "url": f"{HF_BASE}/green_lion_202602_1_synthetic_loan_tape.csv"},
        {"date": "2026-03-31", "url": f"{HF_BASE}/green_lion_202603_1_synthetic_loan_tape.csv"},
        {"date": "2026-04-30", "url": f"{HF_BASE}/green_lion_2026_1_synthetic_loan_tape.csv"},
    ],
    "investor_report_urls": [
        {"period": "February 2026", "url": f"{HF_BASE}/monthly-investor-report-green-lion-2026-1-february-2026.pdf"},
        {"period": "March 2026",    "url": f"{HF_BASE}/monthly-investor-report-green-lion-2026-1-march-2026.pdf"},
        {"period": "April 2026",    "url": f"{HF_BASE}/monthly-investor-report-green-lion-2026-1-april-2026.pdf"},
    ],
}

# ---------------------------------------------------------------------------
# Deal registry — config-driven, so adding a deal is *data*, not code.
# ---------------------------------------------------------------------------
#
# The registry is keyed by the canonical ``deal_id`` clients use in the
# ``/deal/{deal_id}/...`` API routes. Each value is a deal-context dict with
# the same shape as ``GREEN_LION``:
#
#   {
#     "deal_name": str,
#     "prospectus_url": str,
#     "tape_urls": [{"date": str, "url": str}, ...],
#     "investor_report_urls": [{"period": str, "url": str}, ...],
#   }
#
# Green Lion is the in-code default first entry (so the app never depends on a
# data file being present). Additional deals are added as *data*: drop entries
# into the sibling ``data/deals.json`` file (a JSON object: deal_id -> context)
# and they are merged into the registry at import time — no code edit required.
# A ``deals.json`` entry that reuses an existing deal_id overrides the default.

# Optional data file holding extra deals. ``data/deals.json`` lives next to this
# module's ``data`` package. Absent by default; present it to add deals.
DEALS_DATA_FILE = Path(__file__).resolve().parent / "data" / "deals.json"


def _load_deal_registry(data_file: Path = DEALS_DATA_FILE) -> dict[str, dict]:
    """Build the deal registry: in-code defaults merged with ``data_file``.

    Starts from the in-code default (Green Lion) so the registry is never empty,
    then merges every entry from the optional JSON ``data_file`` (a mapping of
    ``deal_id -> deal-context dict``) over it. A malformed or absent file is
    tolerated — the in-code defaults still load — so a bad data file can never
    take the API down; it is logged and skipped.
    """
    registry: dict[str, dict] = {"green-lion-2026-1": GREEN_LION}

    if not data_file.exists():
        return registry

    try:
        extra = json.loads(data_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # unreadable / invalid JSON
        logger.warning("Ignoring unreadable deals data file %s: %s", data_file, exc)
        return registry

    if not isinstance(extra, dict):
        logger.warning(
            "deals data file %s must be a JSON object (deal_id -> context); got %s",
            data_file,
            type(extra).__name__,
        )
        return registry

    for deal_id, context in extra.items():
        if not isinstance(context, dict):
            logger.warning("Skipping deal %r: context is not an object", deal_id)
            continue
        registry[deal_id] = context

    return registry


# The canonical registry. Built once at import; the API sources its ``DEALS``
# from here. Adding a deal is editing ``data/deals.json``, not this code.
DEAL_REGISTRY: dict[str, dict] = _load_deal_registry()
