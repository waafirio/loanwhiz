"""Central config — GCP project, model names, dataset URLs."""

from __future__ import annotations

import json
import logging
import os
import tempfile
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

# Two-tier deal data files, both living next to this module's ``data`` package:
#
#   * ``data/deals.json`` — the **committed, human-curated** registry. Tracked in
#     git, edited by hand (or a seed-refresh script), and NEVER mutated at runtime.
#   * ``data/deals.runtime.json`` — **runtime state** written by the self-service
#     ingest API (``register_deal`` and the tape/report ingest routes, #399). It is
#     gitignored and process-durable; it overlays the committed file on load so a
#     runtime-registered deal (or a runtime mutation of one) survives within the
#     running container without ever touching the curated source of truth.
#
# Both are absent by default; either may be present to add/override deals.
DEALS_DATA_FILE = Path(__file__).resolve().parent / "data" / "deals.json"
DEALS_RUNTIME_FILE = Path(__file__).resolve().parent / "data" / "deals.runtime.json"


def _merge_data_file(registry: dict[str, dict], data_file: Path, *, kind: str) -> None:
    """Overlay one ``deal_id -> context`` JSON ``data_file`` onto ``registry`` in place.

    A malformed or absent file is tolerated — it is logged and skipped — so a bad
    data file (committed OR runtime) can never take the API down. ``kind`` is a
    short label ("committed" / "runtime") used only in the log line.
    """
    if not data_file.exists():
        return

    try:
        extra = json.loads(data_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # unreadable / invalid JSON
        logger.warning("Ignoring unreadable %s deals data file %s: %s", kind, data_file, exc)
        return

    if not isinstance(extra, dict):
        logger.warning(
            "%s deals data file %s must be a JSON object (deal_id -> context); got %s",
            kind,
            data_file,
            type(extra).__name__,
        )
        return

    for deal_id, context in extra.items():
        if not isinstance(context, dict):
            logger.warning("Skipping deal %r in %s file: context is not an object", deal_id, kind)
            continue
        registry[deal_id] = context


def _load_deal_registry(
    data_file: Path = DEALS_DATA_FILE,
    runtime_file: Path = DEALS_RUNTIME_FILE,
) -> dict[str, dict]:
    """Build the deal registry: in-code defaults, then committed, then runtime.

    Resolution order (later overrides earlier by ``deal_id``):

      1. the in-code default (Green Lion) so the registry is never empty;
      2. the committed, human-curated ``data_file`` (``data/deals.json``);
      3. the runtime ``runtime_file`` (``data/deals.runtime.json``), the
         self-service ingest API's write target — runtime entries add to / override
         the committed ones.

    Both files are merged with :func:`_merge_data_file`, which tolerates an absent
    or malformed file (logged and skipped). Cold-start therefore always serves the
    committed deals even if the runtime file is missing or corrupt.
    """
    registry: dict[str, dict] = {"green-lion-2026-1": GREEN_LION}
    _merge_data_file(registry, data_file, kind="committed")
    _merge_data_file(registry, runtime_file, kind="runtime")
    return registry


def _load_runtime_registry(runtime_file: Path = DEALS_RUNTIME_FILE) -> dict[str, dict]:
    """Return the runtime overlay's current ``deal_id -> context`` map (or ``{}``).

    Reads ONLY the runtime file — never the committed one — so a runtime write
    preserves exactly the runtime overlay (the curated ``data/deals.json`` is never
    folded into it). An absent or malformed runtime file yields an empty map.
    """
    overlay: dict[str, dict] = {}
    _merge_data_file(overlay, runtime_file, kind="runtime")
    return overlay


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write ``payload`` to ``path`` atomically (temp file in the same dir + replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        # Best-effort cleanup of the temp file on any failure before the replace.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def register_deal(
    deal_id: str,
    context: dict,
    *,
    runtime_file: Path = DEALS_RUNTIME_FILE,
) -> dict[str, dict]:
    """Persist a deal's context into the RUNTIME overlay (``data/deals.runtime.json``).

    Loads the current runtime overlay (NOT the committed file — see the file-split
    note above), sets/overwrites ``deal_id``'s entry, and writes the overlay back
    atomically. The committed ``data/deals.json`` is never read or mutated here, so
    it stays the human-curated source of truth. Returns the updated runtime overlay
    map (committed deals are merged in only at :func:`_load_deal_registry` load).
    """
    overlay = _load_runtime_registry(runtime_file)
    overlay[deal_id] = context
    _atomic_write_json(runtime_file, overlay)
    return overlay


# The canonical registry. Built once at import; the API sources its ``DEALS`` from
# here (committed ``data/deals.json`` overlaid by runtime ``data/deals.runtime.json``).
DEAL_REGISTRY: dict[str, dict] = _load_deal_registry()
