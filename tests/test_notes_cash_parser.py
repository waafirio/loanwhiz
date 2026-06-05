"""Tests for the Notes & Cash (liability) report parser (V3 / #209).

All tests here are offline (fast suite): they drive the pure parse path
(:func:`parse_report_text`) against a committed text fixture — the real
``pypdf``-extracted text of the Green Lion 2024-1 March-2026 quarterly Notes &
Cash report — plus small synthetic snippets for edge cases, and the
durable-cache round-trip with a monkeypatched extractor. The live PDF fetch
(``_extract_pdf_text``) is integration-gated and not exercised here.

The committed fixture is the genuine report layout (Bond Report / Revenue +
Redemption Priority of Payments / Issuer Transaction Accounts / Transaction
Triggers), so a regression in the parser against the real document shape is
caught here without any network.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from loanwhiz.primitives.notes_cash_parser import (
    NotesCashPeriod,
    NotesCashReport,
    _parse_money,
    _slug,
    parse_notes_cash_report,
    parse_report_text,
)

FIXTURE = Path(__file__).parent / "fixtures" / "notes_cash" / "green-lion-2024-1-march-2026.txt"


@pytest.fixture()
def report_text() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture()
def period(report_text: str) -> NotesCashPeriod:
    return parse_report_text(report_text, period_label="March 2026")


# ---------------------------------------------------------------------------
# Numeric coercion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1,402,891.43", 1402891.43),
        ("-67,557.12", -67557.12),
        ("0.00", 0.0),
        ("25.00 %", 25.0),
        ("10,000,000.00", 10000000.0),
        ("N/A", None),
        ("Not Applicable", None),
        ("-/-", None),
        ("", None),
        ("Class A Notes", None),
    ],
)
def test_parse_money(raw: str, expected: float | None) -> None:
    got = _parse_money(raw)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected)


def test_slug() -> None:
    assert _slug("Green Lion 2024-1 B.V.") == "green-lion-2024-1-bv"


# ---------------------------------------------------------------------------
# Header / keying
# ---------------------------------------------------------------------------


def test_header_and_reporting_date(period: NotesCashPeriod) -> None:
    assert period.reporting_date == "2026-04-23"  # parsed from "Reporting Date: 23 April 2026"
    assert period.deal_name == "Green Lion 2024-1 B.V."
    assert period.esma_identifier == "3TK20IVIUJ8J3ZU0QE75N202401"
    assert period.reporting_period == "23 January 2026 - 23 April 2026"
    assert period.period_label == "March 2026"


def test_reporting_date_override(report_text: str) -> None:
    p = parse_report_text(report_text, period_label="X", reporting_date="2099-12-31")
    assert p.reporting_date == "2099-12-31"


def test_missing_reporting_date_raises() -> None:
    # No header date and no override → cannot key the report.
    with pytest.raises(ValueError, match="reporting date"):
        parse_report_text("Some report with no date at all\n0.00", period_label="Bad")


def test_empty_reporting_date_rejected() -> None:
    with pytest.raises(ValueError, match="reporting_date"):
        NotesCashPeriod(reporting_date="   ", period_label="Bad")


# ---------------------------------------------------------------------------
# Bond Report — per-class note balances
# ---------------------------------------------------------------------------


def test_bond_report_note_balances(period: NotesCashPeriod) -> None:
    a = period.note_balance("class_a")
    b = period.note_balance("class_b")
    c = period.note_balance("class_c")
    assert a is not None and b is not None and c is not None

    assert a.principal_balance_after_payment == pytest.approx(1_000_000_000.00)
    assert b.principal_balance_after_payment == pytest.approx(53_100_000.00)
    assert c.principal_balance_after_payment == pytest.approx(10_500_000.00)

    # No amortisation this period — all classes pay 0 principal, factor 1.0.
    assert a.total_principal_payments == pytest.approx(0.0)
    assert a.factor_after_payment == pytest.approx(1.0)

    # Class A interest paid this period (the Bond Report "Total Interest Payments").
    assert a.total_interest_payments == pytest.approx(6_135_000.00)
    # B/C carry no separate interest line in this report (N/A) → None.
    assert b.total_interest_payments is None

    # PDL after payment is zero for A and B; C is N/A.
    assert a.pdl_balance_after_payment == pytest.approx(0.0)
    assert b.pdl_balance_after_payment == pytest.approx(0.0)
    assert period.total_pdl == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Revenue / Redemption Priority of Payments — per-step distributions
# ---------------------------------------------------------------------------


def test_revenue_pop_steps(period: NotesCashPeriod) -> None:
    # The revenue PoP distribution block parsed its executed steps.
    assert len(period.revenue_pop) >= 5

    # Step (c): Swap Counterparty payment, current period.
    assert period.revenue_step("(c)").amount == pytest.approx(6_043_550.85)
    # Step (d): Class A interest, on a pari-passu/pro-rata basis.
    d = period.revenue_step("(d)")
    assert d is not None
    assert d.amount == pytest.approx(6_135_000.00)
    assert d.previous_amount == pytest.approx(6_281_555.56)
    assert "Class A" in d.recipient
    # Step (k): Deferred Purchase Price Instalment to the Seller.
    assert period.revenue_step("(k)").amount == pytest.approx(1_336_466.99)


def test_revenue_pop_ties_to_available_funds(period: NotesCashPeriod) -> None:
    # The published revenue distribution sums to the available revenue funds —
    # the self-consistency check that makes this an engine-validation target.
    assert period.available_revenue_funds == pytest.approx(13_615_514.93)
    assert period.revenue_distributed_total() == pytest.approx(
        period.available_revenue_funds, abs=0.01
    )


def test_redemption_pop_steps(period: NotesCashPeriod) -> None:
    assert len(period.redemption_pop) >= 3
    # (a): revolving-period purchase of new receivables (where principal goes
    # while the deal is still revolving).
    a = period.redemption_step("(a)")
    assert a is not None
    assert a.amount == pytest.approx(43_486_010.58)
    # Class A/B principal redemption steps are present and zero this period.
    assert period.redemption_step("(b)").amount == pytest.approx(0.0)
    assert period.available_principal_funds == pytest.approx(43_486_011.27)


def test_pop_step_vocabulary_matches_interpreter(period: NotesCashPeriod) -> None:
    # The PoPStep carries priority + recipient + amount, the vocabulary V4
    # reconciles against the waterfall interpreter's StepResult.
    step = period.revenue_pop[0]
    assert step.priority.startswith("(")
    assert isinstance(step.recipient, str) and step.recipient
    assert isinstance(step.amount, float)


# ---------------------------------------------------------------------------
# Issuer Transaction Accounts — reserve / cash
# ---------------------------------------------------------------------------


def test_issuer_accounts_reserve(period: NotesCashPeriod) -> None:
    reserve = period.account("reserve_account")
    assert reserve is not None
    assert reserve.balance_end == pytest.approx(10_500_000.00)
    assert reserve.target == pytest.approx(10_500_000.00)
    assert reserve.drawings == pytest.approx(0.0)
    # Convenience properties.
    assert period.reserve_balance == pytest.approx(10_500_000.00)
    assert period.reserve_target == pytest.approx(10_500_000.00)


def test_issuer_accounts_others(period: NotesCashPeriod) -> None:
    assert period.account("issuer_expense_account").balance_end == pytest.approx(50_000.00)
    assert period.account("construction_deposit_account").balance_end == pytest.approx(339_400.37)
    assert period.account("swap_collateral_account").balance_end == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Transaction Triggers and Events
# ---------------------------------------------------------------------------


def test_triggers_parsed_not_breached(period: NotesCashPeriod) -> None:
    assert len(period.triggers) >= 10
    # Every trigger in this report is OK (none breached).
    assert period.any_trigger_breached is False
    assert all(t.status == "OK" for t in period.triggers)

    # Trigger (a): WA Loan-to-Income required <= 4.8, current 3.97.
    a = period.trigger("(a)")
    assert a is not None
    assert a.required_value == pytest.approx(4.8)
    assert a.current_value == pytest.approx(3.97)
    assert a.breached is False


def test_breached_trigger_synthetic() -> None:
    # A synthetic snippet where a trigger is marked Breached — the parser must
    # flip `breached` and `any_trigger_breached`.
    snippet = (
        "Green Lion Test B.V.\n"
        "Reporting Date: 23 April 2026\n"
        "Transaction Triggers and Events\n"
        "Required Value\n"
        "Current Value\n"
        "Status Breached\n"
        "(a) some loss ratio threshold\n"
        "0.40\n"
        "0.55\n"
        "Breached\n"
        "Early Amortisation Event\n"
    )
    p = parse_report_text(snippet, period_label="Synthetic")
    t = p.trigger("(a)")
    assert t is not None
    assert t.breached is True
    assert t.status == "Breached"
    assert p.any_trigger_breached is True
    assert t.required_value == pytest.approx(0.40)
    assert t.current_value == pytest.approx(0.55)


# ---------------------------------------------------------------------------
# NotesCashReport — aggregation, sort, lookup (mirrors CollateralLedger)
# ---------------------------------------------------------------------------


def test_report_sorts_and_looks_up(period: NotesCashPeriod) -> None:
    earlier = period.model_copy(update={"reporting_date": "2026-01-23"})
    report = NotesCashReport(deal_name="Green Lion 2024-1 B.V.", periods=[period, earlier])
    # Sorted by reporting date (validator), earliest first.
    assert report.reporting_dates == ["2026-01-23", "2026-04-23"]
    assert report.period_for("2026-04-23") is period or report.period_for("2026-04-23") == period
    assert report.period_for("2026-04-23").reserve_balance == pytest.approx(10_500_000.00)
    assert report.period_for("2025-12-31") is None
    assert set(report.by_date) == {"2026-01-23", "2026-04-23"}


# ---------------------------------------------------------------------------
# parse_notes_cash_report — cache round-trip & cold extraction (offline)
# ---------------------------------------------------------------------------

_DEAL_CONTEXT: dict[str, Any] = {
    "deal_name": "Green Lion 2024-1 B.V.",
    "notes_cash_report_urls": [
        {"period": "March 2026", "url": "https://example/march.pdf"},
    ],
}


def test_cold_cache_extracts_then_durable_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report_text: str
) -> None:
    cache_dir = tmp_path / "extraction_cache"
    calls = {"n": 0}

    def _fake_extract(url: str) -> str:
        calls["n"] += 1
        return report_text

    monkeypatch.setattr(
        "loanwhiz.primitives.notes_cash_parser._extract_pdf_text", _fake_extract
    )

    report = parse_notes_cash_report(_DEAL_CONTEXT, cache_dir=cache_dir)
    assert report.reporting_dates == ["2026-04-23"]
    assert calls["n"] == 1  # one PDF fetched + parsed
    durable = cache_dir / "notes-cash-green-lion-2024-1-bv.json"
    assert durable.exists()

    # Second call hits the durable cache — no re-extraction.
    report2 = parse_notes_cash_report(_DEAL_CONTEXT, cache_dir=cache_dir)
    assert calls["n"] == 1  # still 1 — warm cache served it
    assert report2 == report


def test_force_refresh_bypasses_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report_text: str
) -> None:
    cache_dir = tmp_path / "extraction_cache"
    calls = {"n": 0}

    def _fake_extract(url: str) -> str:
        calls["n"] += 1
        return report_text

    monkeypatch.setattr(
        "loanwhiz.primitives.notes_cash_parser._extract_pdf_text", _fake_extract
    )

    parse_notes_cash_report(_DEAL_CONTEXT, cache_dir=cache_dir)
    parse_notes_cash_report(_DEAL_CONTEXT, cache_dir=cache_dir, force_refresh=True)
    assert calls["n"] == 2  # force_refresh re-fetched


def test_warm_cache_skips_extraction_entirely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, period: NotesCashPeriod
) -> None:
    # Pre-seed a durable cache; the extractor must never be called.
    cache_dir = tmp_path / "extraction_cache"
    cache_dir.mkdir()
    report = NotesCashReport(deal_name=_DEAL_CONTEXT["deal_name"], periods=[period])
    (cache_dir / "notes-cash-green-lion-2024-1-bv.json").write_text(
        report.model_dump_json(indent=2), encoding="utf-8"
    )

    def _boom(url: str) -> str:
        raise AssertionError("must not extract when durable cache is present")

    monkeypatch.setattr(
        "loanwhiz.primitives.notes_cash_parser._extract_pdf_text", _boom
    )

    served = parse_notes_cash_report(_DEAL_CONTEXT, cache_dir=cache_dir)
    assert served.reporting_dates == ["2026-04-23"]
    # The round-trip preserved the parsed structure (note balances, PoP, etc.).
    assert served.period_for("2026-04-23").reserve_balance == pytest.approx(10_500_000.00)
    assert served.period_for("2026-04-23").revenue_step("(d)").amount == pytest.approx(6_135_000.00)
