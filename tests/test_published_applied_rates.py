"""The report's published applied rate reaches the engine's accrual (#512, epic #510).

Gap 2 of #496's grade: #495 committed Cairn CLO XVII's per-class all-in applied
rates into the answer key's ``pool_stats`` and **nothing read them**. The seed's
coupon is the prospectus margin ``"3 month EURIBOR + 1.80%"``, which
``capital_structure.numeric_rate_pct`` rightly refuses to coerce into a number,
so every floating class reached ``_make_tranche_interest_need`` with no rate and
#493 refused it — correct, and useless.

What this module pins
---------------------

- **The rate, never the amount.** The engine's input is the rate the report says
  it *applied*; the report's distributed amount is the answer being checked and
  is not read here. ``_report_period_rates`` reads
  ``NoteClassBalance.interest_rate_applied`` and nothing else.
- **Per period, not a deal constant.** A floating class's applied rate moves
  every payment date, so the rate that reaches a period's ``TrancheFunds`` is
  that period's. Accruing one period's coupon against another's balance would
  land a wrong figure that still looks plausible, which is why it is asserted
  rather than assumed.
- **The refusal survives.** A class the report publishes no rate for stays
  ``not_evaluable`` and never accrues a silent zero. Asserted in **both**
  directions per #493 — the same step, one input away, flips to evaluable — so
  the assertion cannot pass for whichever layer happens to refuse first.

The published figures below are regenerated from the committed answer key and
the committed report fixture at read time; none is transcribed into this file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loanwhiz.api.main import _period_coupon_pct, _report_period_rates
from loanwhiz.domain.inputs import PeriodInputs
from loanwhiz.primitives.deal_state import DealState
from loanwhiz.primitives.notes_cash_parser import NoteClassBalance, NotesCashPeriod
from loanwhiz.primitives.period_state_machine import published_rate_inputs, run_period
from loanwhiz.primitives.reconciliation_answer_key import APPLIED_RATE_STAT_PREFIX
from loanwhiz.primitives.reconciler import (
    load_green_lion_2024_1_report,
    validate_green_lion_2024_1,
)
from loanwhiz.primitives.waterfall_interpreter import StepSpec
from tests.clo_answer_key_source import clo_note_valuation_report

#: The committed answer key #495 authored — the durable record of the published
#: rates. Read, never written; this module changes neither side of the grade.
_ANSWER_KEY_PATH = (
    Path(__file__).resolve().parents[1]
    / "src" / "loanwhiz" / "data" / "deals" / "answer_keys" / "cairn-clo-xvii-dac.json"
)

#: The Note Valuation Report's period — the key's only PoP-bearing one.
_NVR_REPORTING_DATE = "2025-01-08"

_DAYS = 90


@pytest.fixture(scope="module")
def nvr_period() -> NotesCashPeriod:
    """Cairn's parsed Note Valuation Report period (committed fixture, offline)."""
    return clo_note_valuation_report().periods[0]


@pytest.fixture(scope="module")
def key_applied_rates() -> dict[str, float]:
    """``{class key: rate}`` the committed answer key publishes for the NVR period."""
    key = json.loads(_ANSWER_KEY_PATH.read_text(encoding="utf-8"))
    period = next(
        p for p in key["periods"] if p["reporting_date"] == _NVR_REPORTING_DATE
    )
    return {
        stat.removeprefix(APPLIED_RATE_STAT_PREFIX): rate
        for stat, rate in period["pool_stats"].items()
        if stat.startswith(APPLIED_RATE_STAT_PREFIX)
    }


def _opening(**balances: float) -> DealState:
    """An opening state carrying the named tranches (canonical A/B/C, rest at 0).

    ``seed_from_prospectus`` requires all three canonical balances of a
    purely-canonical structure, so the unnamed ones open fully amortised — no
    step below pays them, and a zero balance accrues zero either way.
    """
    structure = {f"class_{c}_balance": 0.0 for c in "abc"}
    structure.update({f"{name}_balance": bal for name, bal in balances.items()})
    total = sum(balances.values())
    return DealState.seed_from_prospectus(
        structure,
        reserve_target=0.0,
        original_pool_balance=total,
        opening_pool_balance=total,
        reporting_date="2024-10-08",
    )


def _run_interest_only(
    opening: DealState, *, rates: dict[str, float], recipients: list[str], funds: float
):
    """Run one period through a cascade of nothing but tranche-interest steps."""
    period = PeriodInputs(
        reporting_date="2025-01-08",
        days_in_period=_DAYS,
        available_revenue=funds,
        available_principal=0.0,
        realized_loss=0.0,
        source="report",
    )
    return run_period(
        opening,
        period,
        rates=rates,
        revenue_steps=[
            StepSpec(priority=f"({chr(97 + i)})", recipient=r)
            for i, r in enumerate(recipients)
        ],
        redemption_steps=[],
        principal_classes=(),
    )


def _step(result, recipient: str):
    return next(s for s in result.revenue_execution.steps if s.recipient == recipient)


# ---------------------------------------------------------------------------
# The builder: a published rate becomes a rate input; an unpublished one does not.
# ---------------------------------------------------------------------------


class TestPublishedRateInputs:
    def test_a_published_rate_becomes_the_tranches_rate_input(self):
        assert published_rate_inputs({"class_a": 5.008}) == {"class_a_rate_pct": 5.008}

    def test_the_note_classs_own_spelling_is_preserved(self):
        """``class_b_1`` is a tranche name, not a letter — no A/B/C alphabet here."""
        assert published_rate_inputs({"class_b_1": 5.958, "class_f": 12.848}) == {
            "class_b_1_rate_pct": 5.958,
            "class_f_rate_pct": 12.848,
        }

    def test_an_unpublished_rate_carries_no_key_rather_than_a_zero(self):
        """``None`` means the report prints no rate — never 'zero per cent' (#481)."""
        assert published_rate_inputs({"class_subordinated": None}) == {}

    def test_a_genuine_zero_rate_is_kept(self):
        """0% is a real published answer (owed nothing); only ``None`` is unknown."""
        assert published_rate_inputs({"class_z": 0.0}) == {"class_z_rate_pct": 0.0}


# ---------------------------------------------------------------------------
# The wiring: the fold's rate map is the report's published rates.
# ---------------------------------------------------------------------------


class TestTheFoldReadsTheReportsPublishedRates:
    def test_the_periods_rate_map_is_the_committed_keys_applied_rates(
        self, nvr_period, key_applied_rates
    ):
        """Every rate #495 committed reaches the fold, under the engine's spelling.

        The key is the durable record of what the document published; the fold
        reads the document. Asserting them equal is what makes the key's
        ``applied_rate_*`` stats checked ground truth instead of a figure nothing
        consumes — without importing the key into the engine's input path.
        """
        rates = _report_period_rates(nvr_period)
        assert rates == {
            f"{cls}_rate_pct": rate for cls, rate in key_applied_rates.items()
        }
        assert rates, "the key publishes rates; an empty map means nothing was read"

    def test_the_class_the_report_publishes_no_rate_for_is_absent(self, nvr_period):
        """The Subordinated Notes are excluded deliberately — not accrued at zero."""
        unpublished = {
            b.note_class for b in nvr_period.note_balances if b.interest_rate_applied is None
        }
        assert unpublished, "this fixture's point is that one class publishes no rate"
        rates = _report_period_rates(nvr_period)
        for note_class in unpublished:
            assert f"{note_class}_rate_pct" not in rates

    def test_the_amount_the_report_distributed_is_not_read(self, nvr_period):
        """A rate is an input; the distributed amount is the answer being checked.

        Recovering Class A's rate from the report's own interest payment — the
        RMBS fallback — would be the circularity this epic exists to remove, so
        for a report that publishes its rates the recovered figure must not win.
        """
        published = nvr_period.note_balance("class_a").interest_rate_applied
        recovered = _period_coupon_pct(nvr_period)
        assert recovered != pytest.approx(published), (
            "fixture no longer distinguishes the two sources — the assertion below "
            "would pass either way"
        )
        assert _report_period_rates(nvr_period)["class_a_rate_pct"] == pytest.approx(
            published
        )


# ---------------------------------------------------------------------------
# The accrual: the rate reaches TrancheFunds and the interest need.
# ---------------------------------------------------------------------------


class TestTheRateReachesTheAccrual:
    def test_the_published_rate_accrues_act_360_on_the_tranche_balance(
        self, nvr_period, key_applied_rates
    ):
        """The whole point: a floating class the seed could not price now accrues."""
        balance = nvr_period.note_balance("class_a").principal_balance_after_payment
        rate = key_applied_rates["class_a"]
        result = _run_interest_only(
            _opening(class_a=balance),
            rates=_report_period_rates(nvr_period),
            recipients=["class_a_interest"],
            funds=1e9,
        )
        step = _step(result, "class_a_interest")
        assert not step.not_evaluable
        assert step.need == pytest.approx(balance * (rate / 100.0) / 360.0 * _DAYS)

    def test_the_same_step_refuses_with_the_rate_withheld(self, nvr_period):
        """#493's pairing: one input away, and the refusal is the only difference.

        Asserted against the evaluable twin above, so it cannot pass for a layer
        that refuses earlier (an unregistered recipient, an absent tranche).
        """
        balance = nvr_period.note_balance("class_a").principal_balance_after_payment
        result = _run_interest_only(
            _opening(class_a=balance),
            rates={},
            recipients=["class_a_interest"],
            funds=1e9,
        )
        step = _step(result, "class_a_interest")
        assert step.not_evaluable
        assert step.need == 0.0
        assert step.amount_distributed == 0.0

    def test_one_class_accrues_while_its_unpublished_sibling_refuses(self):
        """Both directions in ONE run — same funds, same period, same cascade."""
        result = _run_interest_only(
            _opening(class_a=100_000_000.0, class_c=50_000_000.0),
            rates=published_rate_inputs({"class_a": 5.0, "class_c": None}),
            recipients=["class_a_interest", "class_c_interest"],
            funds=1e9,
        )
        priced = _step(result, "class_a_interest")
        unpriced = _step(result, "class_c_interest")
        assert not priced.not_evaluable
        assert priced.need == pytest.approx(100_000_000.0 * 0.05 / 360.0 * _DAYS)
        assert unpriced.not_evaluable
        assert unpriced.need == 0.0


# ---------------------------------------------------------------------------
# Per period, not per deal.
# ---------------------------------------------------------------------------


def _period_publishing(rate: float | None, *, reporting_date: str) -> NotesCashPeriod:
    """A minimal report period publishing one Class A applied rate."""
    return NotesCashPeriod(
        reporting_date=reporting_date,
        period_label=reporting_date,
        note_balances=[
            NoteClassBalance(
                note_class="class_a",
                principal_balance_after_payment=100_000_000.0,
                total_interest_payments=0.0,
                interest_rate_applied=rate,
            )
        ],
    )


class TestTheRateIsPerPeriod:
    def test_two_periods_publishing_different_rates_give_different_rate_maps(self):
        first = _report_period_rates(_period_publishing(4.0, reporting_date="2025-01-08"))
        second = _report_period_rates(_period_publishing(6.5, reporting_date="2025-04-08"))
        assert first["class_a_rate_pct"] == 4.0
        assert second["class_a_rate_pct"] == 6.5

    def test_each_periods_accrual_uses_that_periods_own_rate(self):
        """The same balance under two periods' rates must not accrue the same.

        A deal-constant rate would make these equal — the failure mode that reads
        as plausible output because only the coupon is wrong, never the shape.
        """
        opening = _opening(class_a=100_000_000.0)
        needs = [
            _step(
                _run_interest_only(
                    opening,
                    rates=_report_period_rates(
                        _period_publishing(rate, reporting_date="2025-01-08")
                    ),
                    recipients=["class_a_interest"],
                    funds=1e9,
                ),
                "class_a_interest",
            ).need
            for rate in (4.0, 6.5)
        ]
        assert needs[0] == pytest.approx(100_000_000.0 * 0.04 / 360.0 * _DAYS)
        assert needs[1] == pytest.approx(needs[0] * 6.5 / 4.0)


# ---------------------------------------------------------------------------
# The RMBS path is untouched.
# ---------------------------------------------------------------------------


class TestGreenLionIsUnchanged:
    def test_a_report_publishing_no_rates_still_recovers_class_a_per_quarter(self):
        """Notes & Cash prints no rate column, so the recovery remains the source."""
        periods = load_green_lion_2024_1_report().periods
        maps = [_report_period_rates(p) for p in periods]
        assert maps == [{"class_a_rate_pct": _period_coupon_pct(p)} for p in periods]
        rates = [m["class_a_rate_pct"] for m in maps]
        assert len(set(rates)) == len(rates), (
            "the notes are floating-rate; identical quarters would mean the "
            "per-period recovery stopped happening"
        )

    def test_the_headline_proof_still_reconciles_to_the_cent(self):
        """The fold reads these maps, so the published proof is the integration check."""
        report = validate_green_lion_2024_1()
        assert report.passed
