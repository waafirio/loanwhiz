"""Tests for the seasoned ING deals registered in ``data/deals.json`` (#207).

Green Lion 2023-1 and 2024-1 are real, already-seasoned ING securitisations
registered as *data* (not code) via ``src/loanwhiz/data/deals.json``, which
``loanwhiz.config._load_deal_registry`` merges into ``DEAL_REGISTRY`` at import.

Unlike Green Lion 2026-1 (the in-code default), these deals:
  * have their documents on ING's investor portal (``ing.com``), not HuggingFace;
  * have **no public loan tapes**, so ``tape_urls`` is empty by design — the
    validation epic (#206) uses their published reports, not tapes;
  * carry an extra ``notes_cash_report_urls`` key holding the quarterly
    Notes & Cash (liability) reports the engine is validated against.

These tests assert both deals resolve from the registry with the expected keys.
They load the *real shipped* ``deals.json`` (via ``DEALS_DATA_FILE``), not a
fixture, so a regression in the data file is caught here.
"""

from __future__ import annotations

import pytest

from loanwhiz.config import DEAL_REGISTRY, DEALS_DATA_FILE, _load_deal_registry

SEASONED_DEAL_IDS = ("green-lion-2023-1", "green-lion-2024-1")

# The four standard deal-context keys every registry entry must carry, plus the
# seasoned-deal-specific liability-report key added by #207.
STANDARD_KEYS = ("deal_name", "prospectus_url", "tape_urls", "investor_report_urls")
NOTES_CASH_KEY = "notes_cash_report_urls"


def _registry_from_real_data_file() -> dict[str, dict]:
    """Build the registry from the real shipped ``deals.json`` on disk."""
    return _load_deal_registry(DEALS_DATA_FILE)


def test_both_seasoned_deals_resolve_from_live_registry() -> None:
    # The import-time ``DEAL_REGISTRY`` carries both seasoned deals plus the
    # in-code Green Lion 2026-1 default (three deals total minimum).
    for deal_id in SEASONED_DEAL_IDS:
        assert deal_id in DEAL_REGISTRY, f"{deal_id} missing from DEAL_REGISTRY"
    assert "green-lion-2026-1" in DEAL_REGISTRY  # default never displaced


def test_both_seasoned_deals_resolve_from_shipped_data_file() -> None:
    # Loading the real data file directly yields the same two deals — proves the
    # registration lives in ``data/deals.json`` (data, not code).
    registry = _registry_from_real_data_file()
    for deal_id in SEASONED_DEAL_IDS:
        assert deal_id in registry
    assert "green-lion-2026-1" in registry


@pytest.mark.parametrize("deal_id", SEASONED_DEAL_IDS)
def test_seasoned_deal_has_expected_keys(deal_id: str) -> None:
    deal = DEAL_REGISTRY[deal_id]
    for key in STANDARD_KEYS:
        assert key in deal, f"{deal_id} missing standard key {key!r}"
    assert NOTES_CASH_KEY in deal, f"{deal_id} missing {NOTES_CASH_KEY!r}"


@pytest.mark.parametrize("deal_id", SEASONED_DEAL_IDS)
def test_seasoned_deal_name_and_prospectus(deal_id: str) -> None:
    deal = DEAL_REGISTRY[deal_id]
    # deal_name is the "Green Lion <year>-1 B.V." display string.
    year = deal_id.removeprefix("green-lion-").removesuffix("-1")
    assert deal["deal_name"] == f"Green Lion {year}-1 B.V."
    # Prospectus is a real ING-portal PDF (not HuggingFace).
    assert deal["prospectus_url"].startswith("https://ing.com/")
    assert deal["prospectus_url"].endswith("prospectus.pdf")
    assert "huggingface" not in deal["prospectus_url"].lower()


@pytest.mark.parametrize("deal_id", SEASONED_DEAL_IDS)
def test_seasoned_deal_has_no_public_tape(deal_id: str) -> None:
    # No public loan tapes for the seasoned deals — empty by design (#206).
    assert DEAL_REGISTRY[deal_id]["tape_urls"] == []


@pytest.mark.parametrize("deal_id", SEASONED_DEAL_IDS)
def test_seasoned_deal_report_urls_well_formed(deal_id: str) -> None:
    deal = DEAL_REGISTRY[deal_id]
    # Monthly collateral (investor) reports and quarterly Notes & Cash reports
    # are both present and use the {"period", "url"} entry shape pointing at
    # real ING-portal PDFs.
    for key in ("investor_report_urls", NOTES_CASH_KEY):
        entries = deal[key]
        assert isinstance(entries, list) and entries, f"{deal_id}.{key} is empty"
        for entry in entries:
            assert set(entry) >= {"period", "url"}
            assert entry["period"]
            assert entry["url"].startswith("https://ing.com/")
            assert entry["url"].endswith(".pdf")


def test_prospectus_path_conventions_differ_between_deals() -> None:
    # The URL conventions are deliberately inconsistent across deals (enumerated
    # from ING's portal, not hand-guessed): 2023-1 uses an underscore-dash
    # ``_-_prospectus``; 2024-1 uses a triple-dash ``---prospectus``.
    assert "_-_prospectus.pdf" in DEAL_REGISTRY["green-lion-2023-1"]["prospectus_url"]
    assert "---prospectus.pdf" in DEAL_REGISTRY["green-lion-2024-1"]["prospectus_url"]
