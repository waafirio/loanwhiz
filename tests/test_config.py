"""Tests for the Green Lion 2026-1 tape wiring in ``loanwhiz.config``.

Green Lion 2026-1 is its own deal: only its three 2026 tapes (Feb/Mar/Apr,
~EUR 1bn pool) belong to it. The 2024-2025 ``green-lion-2024-2025`` dataset is a
SEPARATE deal (~EUR 139bn) and is deliberately NOT chained in here — different
deals' loan tapes are not interchangeable. Asserts the 3-tape scope, chronological
ordering, that all tapes resolve from ``HF_BASE``, the irregular April filename,
and that Jan-2026 is absent.
"""

from __future__ import annotations

from loanwhiz.config import GREEN_LION, HF_BASE

TAPE_URLS = GREEN_LION["tape_urls"]


def test_deal_has_its_own_three_2026_tapes() -> None:
    assert len(TAPE_URLS) == 3
    # All three belong to Green Lion 2026-1 (HF_BASE / Hackathon_Data) — none are
    # drawn from the separate 2024-2025 dataset.
    for entry in TAPE_URLS:
        assert entry["url"].startswith(HF_BASE)
        assert "Hackathon_Data" in entry["url"]
        assert entry["date"] >= "2026-01-01"


def test_tape_history_is_chronologically_ordered() -> None:
    dates = [entry["date"] for entry in TAPE_URLS]
    assert dates == sorted(dates)
    # Strictly increasing — no duplicate dates would shadow a tape.
    assert len(set(dates)) == len(dates)
    assert dates[0] == "2026-02-28"
    assert dates[-1] == "2026-04-30"


def test_jan_2026_is_absent() -> None:
    dates = {entry["date"] for entry in TAPE_URLS}
    assert "2026-01-31" not in dates
    assert not any("202601" in entry["url"] for entry in TAPE_URLS)


def test_april_2026_irregular_filename_preserved() -> None:
    # The April-2026 tape keeps its irregular ``2026_1`` filename; downstream
    # (tests/test_esma_tape_normaliser.py) looks it up by this exact date.
    april = next(e for e in TAPE_URLS if e["date"] == "2026-04-30")
    assert april["url"] == f"{HF_BASE}/green_lion_2026_1_synthetic_loan_tape.csv"


def test_investor_reports_unchanged() -> None:
    assert len(GREEN_LION["investor_report_urls"]) == 3
