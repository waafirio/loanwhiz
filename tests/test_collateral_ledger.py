"""Tests for the collateral ground-truth ledger (S2 / #182).

All tests here are offline (fast suite): they drive the pure parse path
(``_period_from_extract`` / ``_ledger_from_extracts``) and the durable-cache
round-trip with a ``tmp_path`` cache dir. The live Gemini extraction is
integration-gated and not exercised here.

The fixture mirrors the real ``report_extract_full.json`` shape (the S0 spike
cache), including the collateral-only contract: ``has_tranche_section=false`` and
all liability figures ``null``.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from loanwhiz.extraction.collateral_ledger import (
    CollateralLedger,
    CollateralPeriod,
    _ledger_from_extracts,
    _period_from_extract,
    _slug,
    extract_collateral_ledger,
)

# ---------------------------------------------------------------------------
# Fixtures — the real 3-period Green Lion extract shape (from S0's cache)
# ---------------------------------------------------------------------------

_FEB: dict[str, Any] = {
    "reporting_period_start": "2026-02-01",
    "reporting_period_end": "2026-02-28",
    "reporting_date": "2026-03-23",
    "loans_begin": 3283,
    "loans_end": 3275,
    "balance_begin": 1053099999.98,
    "balance_end": 1048763811.94,
    "repayments": 1846449.61,
    "prepayments": 2659344.91,
    "further_advances": 0.0,
    "other_balance_change": 169606.48,
    "wtd_avg_coupon_pct": 3.14,
    "default_amount_crr": 342783.0,
    "cpr_life_pct": 2.995,
    "ppr_life_pct": 2.09,
    "cdr_pct": 0.392,
    "payment_ratio_pct": 99.48,
    "class_a_balance_end": None,
    "pdl_balance_total": None,
    "reserve_fund_balance": None,
    "total_collections": None,
    "has_tranche_section": False,
}

_MAR: dict[str, Any] = {
    "reporting_period_start": "2026-03-01",
    "reporting_period_end": "2026-03-31",
    "reporting_date": "2026-04-23",
    "loans_begin": 3275,
    "loans_end": 3261,
    "balance_begin": 1048763811.94,
    "balance_end": 1042493289.74,
    "repayments": 1839613.2,
    "prepayments": 4439265.19,
    "further_advances": 0.0,
    "other_balance_change": 8356.19,
    "wtd_avg_coupon_pct": 3.14,
    "default_amount_crr": 342231.0,
    "cpr_life_pct": 3.99,
    "ppr_life_pct": 2.093,
    "cdr_pct": 0.0,
    "payment_ratio_pct": 99.52,
    "has_tranche_section": False,
}

_APR: dict[str, Any] = {
    "reporting_period_start": "2026-04-01",
    "reporting_period_end": "2026-04-30",
    "reporting_date": "2026-05-26",
    "loans_begin": 3261,
    "loans_end": 3237,
    "balance_begin": 1042493289.74,
    "balance_end": 1033412063.04,
    "repayments": 1833436.31,
    "prepayments": 7202449.32,
    "further_advances": 0.0,
    "other_balance_change": -45341.07,
    "wtd_avg_coupon_pct": 3.15,
    "default_amount_crr": 341677.0,
    "cpr_life_pct": 5.346,
    "ppr_life_pct": 2.097,
    "cdr_pct": 0.131,
    "payment_ratio_pct": 99.73,
    "has_tranche_section": False,
}


@pytest.fixture()
def extracts() -> dict[str, dict[str, Any]]:
    # Deliberately out of chronological order to prove the ledger sorts.
    return {"March 2026": _MAR, "April 2026": _APR, "February 2026": _FEB}


@pytest.fixture()
def ledger(extracts: dict[str, dict[str, Any]]) -> CollateralLedger:
    return _ledger_from_extracts("Green Lion 2026-1 B.V.", extracts)


# ---------------------------------------------------------------------------
# _period_from_extract — field mapping
# ---------------------------------------------------------------------------


def test_period_field_mapping() -> None:
    p = _period_from_extract("February 2026", _FEB)
    assert p.reporting_date == "2026-02-28"
    assert p.period_label == "February 2026"
    assert p.period_start == "2026-02-01"
    assert p.report_published_date == "2026-03-23"
    assert p.loans_begin == 3283
    assert p.loans_end == 3275
    assert p.pool_balance_begin == pytest.approx(1053099999.98)
    assert p.pool_balance_end == pytest.approx(1048763811.94)
    assert p.repayments == pytest.approx(1846449.61)
    assert p.prepayments == pytest.approx(2659344.91)
    assert p.further_advances == 0.0
    assert p.other_balance_change == pytest.approx(169606.48)
    assert p.wtd_avg_coupon_pct == pytest.approx(3.14)
    assert p.default_amount == pytest.approx(342783.0)
    assert p.payment_ratio_pct == pytest.approx(99.48)


def test_derived_principal_collected() -> None:
    p = _period_from_extract("April 2026", _APR)
    assert p.principal_collected == pytest.approx(1833436.31 + 7202449.32)


def test_roll_forward_residual_is_zero() -> None:
    # S0 verified the report roll-forward ties to 0.0000 for every period.
    for label, raw in (("February 2026", _FEB), ("March 2026", _MAR), ("April 2026", _APR)):
        p = _period_from_extract(label, raw)
        assert abs(p.roll_forward_residual) < 0.01, label


def test_pool_factor() -> None:
    p = _period_from_extract("April 2026", _APR)
    original = 1063600000.0
    assert p.pool_factor(original) == pytest.approx(1033412063.04 / original)
    assert p.pool_factor(0.0) == 0.0


def test_repayments_normalised_positive() -> None:
    # The report shows repayments as reductions; a signed extract must still
    # land as a non-negative figure (ge=0 on the field).
    signed = dict(_FEB, repayments=-1846449.61, prepayments=-2659344.91)
    p = _period_from_extract("February 2026", signed)
    assert p.repayments == pytest.approx(1846449.61)
    assert p.prepayments == pytest.approx(2659344.91)


# ---------------------------------------------------------------------------
# Collateral-only contract probe
# ---------------------------------------------------------------------------


def test_has_liability_section_false_and_no_liability_figures(ledger: CollateralLedger) -> None:
    # The reports carry no liability side (spike S0) — the probe encodes it.
    assert all(p.has_liability_section is False for p in ledger.periods)
    # CollateralPeriod has no tranche/PDL/reserve fields at all — assert the
    # model surface is collateral-only so a future field addition is deliberate.
    forbidden = {"class_a_balance", "pdl", "reserve_balance", "reserve_fund_balance"}
    assert forbidden.isdisjoint(set(CollateralPeriod.model_fields))


def test_has_liability_section_true_when_report_has_tranche_section() -> None:
    p = _period_from_extract("February 2026", dict(_FEB, has_tranche_section=True))
    assert p.has_liability_section is True


# ---------------------------------------------------------------------------
# Ledger assembly, sort, lookup, chaining
# ---------------------------------------------------------------------------


def test_ledger_sorted_by_reporting_date(ledger: CollateralLedger) -> None:
    assert ledger.reporting_dates == ["2026-02-28", "2026-03-31", "2026-04-30"]


def test_period_for_lookup(ledger: CollateralLedger) -> None:
    p = ledger.period_for("2026-03-31")
    assert p is not None
    assert p.period_label == "March 2026"
    assert ledger.period_for("2026-01-31") is None
    assert set(ledger.by_date) == {"2026-02-28", "2026-03-31", "2026-04-30"}


def test_chains_cleanly(ledger: CollateralLedger) -> None:
    # end[N] == begin[N+1] exactly (S0 verified).
    assert ledger.chains_cleanly() is True


def test_chains_cleanly_detects_break() -> None:
    broken_apr = dict(_APR, balance_begin=999999999.0)  # break Mar.end -> Apr.begin
    led = _ledger_from_extracts("Green Lion 2026-1 B.V.", {"February 2026": _FEB, "March 2026": _MAR, "April 2026": broken_apr})
    assert led.chains_cleanly() is False


def test_missing_reporting_period_end_raises() -> None:
    with pytest.raises(ValueError, match="reporting_period_end"):
        _period_from_extract("Bad 2026", {k: v for k, v in _FEB.items() if k != "reporting_period_end"})


def test_empty_reporting_date_rejected() -> None:
    # The CollateralPeriod field validator rejects an empty reporting date.
    with pytest.raises(ValueError, match="reporting_date"):
        CollateralPeriod(
            reporting_date="   ",
            period_label="Bad 2026",
            pool_balance_begin=1.0,
            pool_balance_end=1.0,
        )


# ---------------------------------------------------------------------------
# extract_collateral_ledger — cache round-trip & warm-start (offline)
# ---------------------------------------------------------------------------

_DEAL_CONTEXT = {
    "deal_name": "Green Lion 2026-1 B.V.",
    "investor_report_urls": [
        {"period": "February 2026", "url": "https://example/feb.pdf"},
    ],
}


def test_warm_start_from_legacy_cache_then_durable_roundtrip(tmp_path, monkeypatch) -> None:
    # Legacy spike cache present → ledger is built from it AND promoted to the
    # durable cache, with NO Gemini call.
    legacy = tmp_path / "report_extract_full.json"
    legacy.write_text(json.dumps({"February 2026": _FEB, "March 2026": _MAR, "April 2026": _APR}))
    cache_dir = tmp_path / "extraction_cache"

    def _boom(*a: Any, **k: Any):  # any live extraction must not be reached
        raise AssertionError("Gemini extraction should not run on a warm cache")

    monkeypatch.setattr(
        "loanwhiz.extraction.collateral_ledger._extract_extracts_with_gemini", _boom
    )

    led = extract_collateral_ledger(
        _DEAL_CONTEXT, cache_dir=cache_dir, legacy_cache=legacy
    )
    assert led.reporting_dates == ["2026-02-28", "2026-03-31", "2026-04-30"]

    # Durable cache was written; a second call serves it directly (still no Gemini).
    durable = cache_dir / "collateral-ledger-green-lion-2026-1-bv.json"
    assert durable.exists()
    led2 = extract_collateral_ledger(
        _DEAL_CONTEXT, cache_dir=cache_dir, legacy_cache=tmp_path / "absent.json"
    )
    assert led2 == led


def test_durable_cache_hit_skips_warm_start(tmp_path, monkeypatch) -> None:
    cache_dir = tmp_path / "extraction_cache"
    cache_dir.mkdir()
    ledger = _ledger_from_extracts(_DEAL_CONTEXT["deal_name"], {"February 2026": _FEB})
    (cache_dir / "collateral-ledger-green-lion-2026-1-bv.json").write_text(
        ledger.model_dump_json(indent=2)
    )

    def _boom(*a: Any, **k: Any):
        raise AssertionError("must not extract when durable cache is present")

    monkeypatch.setattr(
        "loanwhiz.extraction.collateral_ledger._extract_extracts_with_gemini", _boom
    )

    led = extract_collateral_ledger(
        _DEAL_CONTEXT, cache_dir=cache_dir, legacy_cache=tmp_path / "absent.json"
    )
    assert led.reporting_dates == ["2026-02-28"]


def test_cold_cache_calls_extraction(tmp_path, monkeypatch) -> None:
    cache_dir = tmp_path / "extraction_cache"
    called = {}

    def _fake_extract(deal_name: str, urls: list[dict[str, str]]):
        called["yes"] = True
        return {"February 2026": _FEB}

    monkeypatch.setattr(
        "loanwhiz.extraction.collateral_ledger._extract_extracts_with_gemini", _fake_extract
    )

    led = extract_collateral_ledger(
        _DEAL_CONTEXT, cache_dir=cache_dir, legacy_cache=tmp_path / "absent.json"
    )
    assert called.get("yes") is True
    assert led.reporting_dates == ["2026-02-28"]
    # Result was persisted to the durable cache.
    assert (cache_dir / "collateral-ledger-green-lion-2026-1-bv.json").exists()


def test_slug() -> None:
    assert _slug("Green Lion 2026-1 B.V.") == "green-lion-2026-1-bv"
