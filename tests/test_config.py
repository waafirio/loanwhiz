"""Tests for the Green Lion tape-history wiring in ``loanwhiz.config``.

Covers the 27-month chronological tape history (issue #163): 24 historical
monthly tapes (2024-01 … 2025-12) plus the 3 existing 2026 entries, built
programmatically. Asserts the count, chronological ordering, the leap-year
month-end date, that both repo base URLs are used in the right places, and
that Jan-2026 is intentionally absent.
"""

from __future__ import annotations

from loanwhiz.config import (
    GREEN_LION,
    HF_BASE,
    HF_HISTORICAL_BASE,
)

TAPE_URLS = GREEN_LION["tape_urls"]


def test_tape_history_has_27_entries() -> None:
    assert len(TAPE_URLS) == 27


def test_tape_history_is_chronologically_ordered() -> None:
    dates = [entry["date"] for entry in TAPE_URLS]
    assert dates == sorted(dates)
    # Strictly increasing — no duplicate dates would shadow a tape.
    assert len(set(dates)) == len(dates)
    # Endpoints anchor the full 2024-01 → 2026-04 span.
    assert dates[0] == "2024-01-31"
    assert dates[-1] == "2026-04-30"


def test_leap_year_february_uses_29th() -> None:
    dates = {entry["date"] for entry in TAPE_URLS}
    assert "2024-02-29" in dates  # 2024 is a leap year
    assert "2024-02-28" not in dates
    assert "2025-02-28" in dates  # 2025 is not


def test_jan_2026_is_absent() -> None:
    dates = {entry["date"] for entry in TAPE_URLS}
    assert "2026-01-31" not in dates
    assert not any("202601" in entry["url"] for entry in TAPE_URLS)


def test_two_base_urls_used_correctly() -> None:
    # The 24 historical entries use HF_HISTORICAL_BASE (no Hackathon_Data
    # segment); the 3 newest (2026) use HF_BASE.
    historical = [e for e in TAPE_URLS if e["date"] < "2026-01-01"]
    twenty26 = [e for e in TAPE_URLS if e["date"] >= "2026-01-01"]

    assert len(historical) == 24
    assert len(twenty26) == 3

    for entry in historical:
        assert entry["url"].startswith(HF_HISTORICAL_BASE)
        assert "Hackathon_Data" not in entry["url"]

    for entry in twenty26:
        assert entry["url"].startswith(HF_BASE)
        assert "Hackathon_Data" in entry["url"]


def test_historical_filename_pattern() -> None:
    # e.g. the first historical month resolves to the expected URL.
    first = TAPE_URLS[0]
    assert first["date"] == "2024-01-31"
    assert first["url"] == (
        f"{HF_HISTORICAL_BASE}/green_lion_202401_1_synthetic_loan_tape.csv"
    )


def test_april_2026_irregular_filename_preserved() -> None:
    # The April-2026 tape keeps its irregular ``2026_1`` filename; downstream
    # (tests/test_esma_tape_normaliser.py) looks it up by this exact date.
    april = next(e for e in TAPE_URLS if e["date"] == "2026-04-30")
    assert april["url"] == f"{HF_BASE}/green_lion_2026_1_synthetic_loan_tape.csv"


def test_investor_reports_unchanged() -> None:
    # Issue #163 explicitly leaves investor reports as the existing 3.
    assert len(GREEN_LION["investor_report_urls"]) == 3
