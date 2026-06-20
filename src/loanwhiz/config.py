"""Central config — GCP project, model names, dataset URLs."""

from __future__ import annotations

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


GREEN_LION = {
    "deal_name": "Green Lion 2026-1 B.V.",
    "prospectus_url": f"{HF_BASE}/green-lion-2026-1-prospectus.pdf",
    # Green Lion 2026-1's own 3 monthly tapes (Feb/Mar/Apr 2026, ~EUR 1bn pool).
    # The "green-lion-2024-2025" dataset (~EUR 139bn) is a SEPARATE deal, not
    # this deal's pre-history — different deals' data is not interchangeable, so
    # it is deliberately NOT chained in here (doing so produced a 112bn→1bn cliff
    # the reconstruction read as a ~140% loss).
    "tape_urls": [
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
# ``tape_urls[].url`` may point at a ``.csv`` *or* a ``.parquet``/``.pq`` tape:
# the ESMA tape loader (:func:`loanwhiz.primitives.esma_tape_normaliser._load_tape`)
# is format-agnostic and dispatches on the URL suffix. The loader can also slice
# a single reporting period out of a combined multi-month parquet (its ``period``
# selector filters rows to ``reporting_date == period``) — a primitive-level
# capability, not a registry key.
#
# Per-deal structural config keys (resolution + loud fallback, #268)
# ------------------------------------------------------------------
# Beyond the four required keys above, the deal engine needs per-deal STRUCTURAL
# config — the tranche figures, the reserve target, the original pool balance,
# the projection base. The API resolves each value, independently, in this
# priority order:
#
#   1. the deal's explicit ``deals.json`` context key (one of those below);
#   2. the deal's *extracted model* (the cached ``DealModel`` from the
#      extraction pipeline), where it yields a complete engine-ready value —
#      today only ``capital_structure``, and only when the extracted tranches
#      carry a numeric coupon (a EURIBOR/margin reference string is not coerced);
#   3. the ``_GREEN_LION_*`` constants in ``loanwhiz.api.main`` as a **labelled
#      last-resort fallback consulted ONLY for the in-code Green Lion 2026-1
#      deal** (whose context deliberately omits these keys because those
#      constants ARE its config).
#
# A NON-Green-Lion deal that resolves no value for a required structural key
# (no context key, no usable extracted value) **fails loudly** — the endpoint
# returns HTTP 422 naming the deal and the missing key — rather than silently
# borrowing Green Lion's numbers. (This reverses the old silent
# ``deal.get(..., _GREEN_LION_*)`` fallback, which would have served a different
# deal numbers computed against Green Lion's structure.) Green Lion 2026-1's own
# resolution is unchanged, so its output stays byte-identical.
#
# The structural config keys a deal-context dict may carry:
#
#   - ``capital_structure``: dict of ``class_a_balance``, ``class_a_rate_pct``,
#       ``class_b_balance``, ``class_c_balance`` — the tranche figures the
#       revenue/redemption waterfall runs on. Used by ``/deal/{id}/waterfall``.
#   - ``reserve_account_target``: float (EUR) — the reserve account's funded
#       target, seeding the reconstructed ``DealState``. Used by
#       ``/deal/{id}/compliance`` (and the reconstructed series).
#   - ``original_pool_balance``: float (EUR) — the pool balance at deal closing,
#       used as the denominator for clean-up-call proximity and the loss-rate.
#       Used by ``/deal/{id}/compliance``.
#   - ``projection_base``: dict carrying ``current_pool_balance`` plus the
#       capital-structure / reserve-account figures the forward projection runs
#       on. Used by ``/deal/{id}/project``.
#
# Covenant TRIGGERS are NOT a deal-context key: ``/deal/{id}/compliance`` reads
# the deal model's *extracted* ``covenants.triggers`` from the cached deal model
# (built by the extraction pipeline), falling back to ``CovenantMonitor``'s
# defaults when the deal has no cached model or no extracted triggers.
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
