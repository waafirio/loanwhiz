"""Tests for the per-deal ground-truth answer-key format (#427, epic #425).

These pin the *format + loader + reconciler-consume adapter* — the data-driven
generalization of the hand-built ``_VALIDATION_BUILDERS``. They run fully offline
(no network, no LLM): the round-trip + loader tests use synthetic keys, and the
end-to-end consume test reuses Green Lion 2024-1's *existing committed* report and
folded series to prove a config-loaded answer key reconciles identically to the
hand-built ``validate_green_lion_2024_1`` builder, to the cent.

Scope note (#427): this issue defines the format and proves it is consumable. It
deliberately does NOT commit a production deal's real answer key (that is the
backfill, #429) — every key here is built in-memory or written to a tmp dir.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from loanwhiz.config import DEAL_REGISTRY
from loanwhiz.primitives.reconciler import (
    fold_green_lion_2024_1,
    load_green_lion_2024_1_report,
    validate_green_lion_2024_1,
)
from loanwhiz.primitives.reconciliation_answer_key import (
    ANSWER_KEY_FORMAT_VERSION,
    AnswerKeyPeriod,
    AnswerKeyPopStep,
    CovenantResult,
    DealAnswerKey,
    answer_key_path,
    load_answer_key,
    reconcile_against_answer_key,
    write_answer_key,
)

GL_DEAL_ID = "green-lion-2024-1"
GL_DEAL_NAME = "Green Lion 2024-1 B.V."


def _synthetic_key(deal_id: str = "example-deal-2024-1") -> DealAnswerKey:
    """A small, fully-populated synthetic answer key (all three categories)."""
    return DealAnswerKey(
        deal_id=deal_id,
        deal_name="Example Deal 2024-1 B.V.",
        periods=[
            AnswerKeyPeriod(
                reporting_date="2025-09-30",
                period_label="September 2025",
                available_revenue_funds=1_000_000.0,
                available_principal_funds=5_000_000.0,
                revenue_pop=[
                    AnswerKeyPopStep(priority="(a)", amount=12_345.67, recipient="Senior expenses"),
                    AnswerKeyPopStep(priority="(b)", amount=50_000.0),
                ],
                redemption_pop=[
                    AnswerKeyPopStep(priority="(a)", amount=4_500_000.0, recipient="Class A"),
                ],
                covenants=[
                    CovenantResult(name="sequential_pay", threshold=1.5, actual=0.4, passed=True),
                ],
                pool_stats={"pool_balance_end": 95_000_000.0, "principal_collected": 5_000_000.0},
            )
        ],
    )


# ---------------------------------------------------------------------------
# Model defaults + JSON round-trip
# ---------------------------------------------------------------------------


def test_format_version_and_tolerance_defaults() -> None:
    key = _synthetic_key()
    assert key.format_version == ANSWER_KEY_FORMAT_VERSION == 1
    assert key.tolerance_eur == pytest.approx(0.01)


def test_json_round_trip_is_lossless() -> None:
    key = _synthetic_key()
    restored = DealAnswerKey.model_validate_json(key.model_dump_json())
    assert restored == key
    # covenants + pool stats survive the round-trip (typed + loadable now, graded by #428)
    period = restored.periods[0]
    assert period.covenants[0].name == "sequential_pay"
    assert period.pool_stats["pool_balance_end"] == pytest.approx(95_000_000.0)


# ---------------------------------------------------------------------------
# Bridge to / from the reconciler's NotesCashReport
# ---------------------------------------------------------------------------


def test_to_notes_cash_report_preserves_pop() -> None:
    key = _synthetic_key()
    report = key.to_notes_cash_report()
    assert report.deal_name == key.deal_name
    assert [p.reporting_date for p in report.periods] == ["2025-09-30"]
    period = report.periods[0]
    assert period.available_revenue_funds == pytest.approx(1_000_000.0)
    assert [(s.priority, s.amount) for s in period.revenue_pop] == [
        ("(a)", 12_345.67),
        ("(b)", 50_000.0),
    ]
    assert [(s.priority, s.amount) for s in period.redemption_pop] == [("(a)", 4_500_000.0)]


def test_from_notes_cash_report_captures_published_pop() -> None:
    """from_notes_cash_report faithfully captures the green-lion published PoP."""
    report = load_green_lion_2024_1_report()
    key = DealAnswerKey.from_notes_cash_report(report, deal_id=GL_DEAL_ID)

    assert key.deal_id == GL_DEAL_ID
    assert key.deal_name == report.deal_name
    assert len(key.periods) == len(report.periods)

    # The PoP projects back to the same report shape (priorities + amounts to the cent).
    rebuilt = key.to_notes_cash_report()
    assert rebuilt.reporting_dates == report.reporting_dates
    for src, out in zip(report.periods, rebuilt.periods):
        assert [(s.priority, s.amount) for s in out.revenue_pop] == [
            (s.priority, s.amount) for s in src.revenue_pop
        ]
        assert [(s.priority, s.amount) for s in out.redemption_pop] == [
            (s.priority, s.amount) for s in src.redemption_pop
        ]
        assert out.available_revenue_funds == src.available_revenue_funds
        assert out.available_principal_funds == src.available_principal_funds


# ---------------------------------------------------------------------------
# Loader — resolve by deal-name slug from the answer-key data dir
# ---------------------------------------------------------------------------


def test_answer_key_path_uses_deal_name_slug(tmp_path) -> None:
    path = answer_key_path(GL_DEAL_NAME, base_dir=tmp_path)
    assert path == tmp_path / "green-lion-2024-1-bv.json"


def test_load_answer_key_round_trips_through_disk(tmp_path) -> None:
    key = _synthetic_key()
    written = write_answer_key(key, base_dir=tmp_path)
    assert written.exists()

    # Loadable by the deal-context mapping shape the registry yields...
    loaded = load_answer_key({"deal_name": key.deal_name}, base_dir=tmp_path)
    assert loaded == key
    # ...and by a bare deal-name string.
    assert load_answer_key(key.deal_name, base_dir=tmp_path) == key


def test_load_answer_key_miss_returns_none(tmp_path) -> None:
    assert load_answer_key({"deal_name": "No Such Deal B.V."}, base_dir=tmp_path) is None


def test_load_answer_key_malformed_raises(tmp_path) -> None:
    # A present-but-corrupt key must fail loudly, never silently grade nothing.
    path = answer_key_path("Broken Deal B.V.", base_dir=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not valid json", encoding="utf-8")
    # pydantic's model_validate_json wraps the JSON error in ValidationError — the
    # point is it raises (fails loudly), never returns None / grades nothing.
    with pytest.raises(ValidationError):
        load_answer_key("Broken Deal B.V.", base_dir=tmp_path)


# ---------------------------------------------------------------------------
# End-to-end: a config-loaded answer key reconciles like the hand-built builder
# ---------------------------------------------------------------------------


def test_reconcile_against_answer_key_matches_hand_built_builder(tmp_path) -> None:
    """A GL-2024-1 answer key (authored from the committed report, persisted to a
    tmp dir, then loaded back) reconciles to the same PASS as the hand-built
    ``validate_green_lion_2024_1`` — proving the data-driven format is a faithful,
    consumable generalization of ``_VALIDATION_BUILDERS``.
    """
    # Author the key from the committed report, write + read it through disk so the
    # full loader path is exercised (not just the in-memory model).
    report = load_green_lion_2024_1_report()
    authored = DealAnswerKey.from_notes_cash_report(report, deal_id=GL_DEAL_ID)
    write_answer_key(authored, base_dir=tmp_path)
    key = load_answer_key(DEAL_REGISTRY[GL_DEAL_ID], base_dir=tmp_path)
    assert key is not None

    series, _ = fold_green_lion_2024_1()
    got = reconcile_against_answer_key(series, key)
    baseline = validate_green_lion_2024_1()

    # Same to-the-cent verdict and coverage as the hand-built builder.
    assert got.passed is True
    assert got.passed == baseline.passed
    assert got.periods_checked == baseline.periods_checked == 3
    assert got.periods_passed == baseline.periods_passed

    # Per-period revenue + redemption totals tie out to the hand-built proof.
    for got_p, base_p in zip(got.periods, baseline.periods):
        assert got_p.reporting_date == base_p.reporting_date
        assert got_p.revenue.engine_total == pytest.approx(base_p.revenue.engine_total)
        assert got_p.redemption.engine_total == pytest.approx(base_p.redemption.engine_total)
        assert got_p.revenue.report_total == pytest.approx(base_p.revenue.report_total)
        assert got_p.redemption.report_total == pytest.approx(base_p.redemption.report_total)


def test_reconcile_against_answer_key_honors_tolerance_override(tmp_path) -> None:
    report = load_green_lion_2024_1_report()
    key = DealAnswerKey.from_notes_cash_report(report, deal_id=GL_DEAL_ID)
    series, _ = fold_green_lion_2024_1()
    # An impossibly tight (negative) tolerance must fail every step — proves the
    # override is threaded through to reconcile_series, not ignored.
    got = reconcile_against_answer_key(series, key, tolerance=-1.0)
    assert got.passed is False
