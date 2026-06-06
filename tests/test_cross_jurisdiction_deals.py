"""Tests for the cross-jurisdiction deals registered in ``data/deals.json`` (#237).

Leone Arancio RMBS 2023-1 (Italian) and Sol-Lion II RMBS (Spanish, a *fondo de
titulización*) are the first NON-Dutch deals in the registry — the
cross-jurisdiction proof for the reusable-primitives epic (#236). Like the
seasoned Green Lion deals (#207) they are registered as *data*, not code, via
``src/loanwhiz/data/deals.json``, which ``loanwhiz.config._load_deal_registry``
merges into ``DEAL_REGISTRY`` at import.

These deals:
  * have their documents on ING's investor portal (``ing.com``), not HuggingFace;
  * have **no public loan tapes**, so ``tape_urls`` is empty by design;
  * have **no published Notes & Cash / liability report** on ING's portal, so
    (unlike the seasoned Green Lion deals) they carry NO ``notes_cash_report_urls``
    key — extraction (C2) works from the monthly investor reports;
  * carry a ``jurisdiction`` key (``Italy`` / ``Spain``) — the additive,
    optional registry key that makes the cross-jurisdiction registration legible.

The tests load the *real shipped* ``deals.json`` (via ``DEALS_DATA_FILE``), not a
fixture, so a regression in the data file is caught here.
"""

from __future__ import annotations

import pytest

from loanwhiz.config import DEAL_REGISTRY, DEALS_DATA_FILE, _load_deal_registry

# deal_id -> expected jurisdiction.
CROSS_JURISDICTION_DEALS = {
    "leone-arancio-2023-1": "Italy",
    "sol-lion-ii": "Spain",
}

# The four standard deal-context keys every registry entry must carry.
STANDARD_KEYS = ("deal_name", "prospectus_url", "tape_urls", "investor_report_urls")
JURISDICTION_KEY = "jurisdiction"


def _registry_from_real_data_file() -> dict[str, dict]:
    """Build the registry from the real shipped ``deals.json`` on disk."""
    return _load_deal_registry(DEALS_DATA_FILE)


def test_both_cross_jurisdiction_deals_resolve_from_live_registry() -> None:
    # The import-time ``DEAL_REGISTRY`` carries both non-Dutch deals.
    for deal_id in CROSS_JURISDICTION_DEALS:
        assert deal_id in DEAL_REGISTRY, f"{deal_id} missing from DEAL_REGISTRY"
    # The in-code Green Lion 2026-1 default is never displaced.
    assert "green-lion-2026-1" in DEAL_REGISTRY


def test_both_cross_jurisdiction_deals_resolve_from_shipped_data_file() -> None:
    # Loading the real data file directly yields the same two deals — proves the
    # registration lives in ``data/deals.json`` (data, not code).
    registry = _registry_from_real_data_file()
    for deal_id in CROSS_JURISDICTION_DEALS:
        assert deal_id in registry


@pytest.mark.parametrize("deal_id", CROSS_JURISDICTION_DEALS)
def test_cross_jurisdiction_deal_has_expected_keys(deal_id: str) -> None:
    deal = DEAL_REGISTRY[deal_id]
    for key in STANDARD_KEYS:
        assert key in deal, f"{deal_id} missing standard key {key!r}"
    assert JURISDICTION_KEY in deal, f"{deal_id} missing {JURISDICTION_KEY!r}"


@pytest.mark.parametrize("deal_id,expected", CROSS_JURISDICTION_DEALS.items())
def test_cross_jurisdiction_deal_jurisdiction(deal_id: str, expected: str) -> None:
    assert DEAL_REGISTRY[deal_id][JURISDICTION_KEY] == expected


@pytest.mark.parametrize("deal_id", CROSS_JURISDICTION_DEALS)
def test_cross_jurisdiction_deal_has_no_public_tape(deal_id: str) -> None:
    # No public loan tapes for these deals — empty by design (no tapes published).
    assert DEAL_REGISTRY[deal_id]["tape_urls"] == []


@pytest.mark.parametrize("deal_id", CROSS_JURISDICTION_DEALS)
def test_cross_jurisdiction_deal_has_no_notes_cash_report(deal_id: str) -> None:
    # Unlike the seasoned Green Lion deals, neither non-Dutch deal publishes a
    # Notes & Cash / liability report on ING's portal — the key is absent.
    assert "notes_cash_report_urls" not in DEAL_REGISTRY[deal_id]


@pytest.mark.parametrize("deal_id", CROSS_JURISDICTION_DEALS)
def test_cross_jurisdiction_deal_prospectus_is_ing_pdf(deal_id: str) -> None:
    prospectus = DEAL_REGISTRY[deal_id]["prospectus_url"]
    assert prospectus.startswith("https://ing.com/")
    assert prospectus.endswith("prospectus-dated-25-november-2020.pdf") or prospectus.endswith(
        "prospectus-dated-12-september-2023.pdf"
    )
    assert "huggingface" not in prospectus.lower()


@pytest.mark.parametrize("deal_id", CROSS_JURISDICTION_DEALS)
def test_cross_jurisdiction_deal_report_urls_well_formed(deal_id: str) -> None:
    # Monthly collateral (investor) reports use the {"period", "url"} entry shape
    # pointing at real ING-portal PDFs. There is a non-trivial history of months.
    entries = DEAL_REGISTRY[deal_id]["investor_report_urls"]
    assert isinstance(entries, list) and len(entries) > 12, f"{deal_id} report history too short"
    for entry in entries:
        assert set(entry) >= {"period", "url"}
        assert entry["period"]
        assert entry["url"].startswith("https://ing.com/")
        assert entry["url"].endswith(".pdf")
