"""Coverage tests read from the trustee report, as reported (#626).

A CLO's OC ("par value") and IC tests are quantified by the transaction's own
schedules and restated monthly by the trustee, never by the offering circular.
So every extracted CLO trigger arrives with ``threshold=None`` and the whole
section reported not-evaluable -- honestly blank, per #493, but blank.

These guards pin the two things that make it not blank, and the three it must
still refuse. Every number below is transcribed from the committed fixture, so
a parser that silently returns ``None`` again, or one that swaps the two
like-typed percentage columns (#480), reds here rather than shipping.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from loanwhiz.api import main as api
from loanwhiz.primitives.collateral_schedule_parser import (
    CoverageTestOutcome,
    parse_liability_summary_text,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "collateral_schedule"

#: The February 2025 report's coverage-test table, transcribed from the fixture
#: as ``name -> (threshold, current, result)``. The column order is the one the
#: report's own header states -- ``Test Description | Threshold | Current |
#: Result`` -- and NOT an assumption about which percentage comes first (#480).
#: Pinning the pair rather than a count is the point: "some triggers parsed"
#: passes on one, and a swapped pair passes any test that only checks presence.
_FEBRUARY_2025 = {
    "Class A/B Par Value Test": ("130.08", "139.43", CoverageTestOutcome.PASSED),
    "Class C Par Value Test": ("121.74", "129.07", CoverageTestOutcome.PASSED),
    "Class D Par Value Test": ("112.62", "118.92", CoverageTestOutcome.PASSED),
    "Class E Par Value Test": ("107.87", "113.15", CoverageTestOutcome.PASSED),
    "Class F Par Value Test": ("103.90", "108.67", CoverageTestOutcome.NOT_APPLICABLE),
    "Reinvestment Overcollateralisation Test": (
        "104.40",
        "108.67",
        CoverageTestOutcome.PASSED,
    ),
    "Class A/B Interest Coverage Test": ("120.00", "192.76", CoverageTestOutcome.PASSED),
    "Class C Interest Coverage Test": ("110.00", "174.06", CoverageTestOutcome.PASSED),
    "Class D Interest Coverage Test": ("105.00", "152.53", CoverageTestOutcome.PASSED),
}


def _february_summary():
    return parse_liability_summary_text(
        (_FIXTURES / "cairn-clo-xvii-february-2025.txt").read_text(encoding="utf-8"),
        period_label="February 2025",
    )


class TestFebruaryFixtureParsesEveryCoverageTest:
    """The nine rows, with both their numbers -- not "some rows"."""

    def test_every_coverage_test_is_present_with_both_percentages(self):
        parsed = {t.name: t for t in _february_summary().coverage_tests}
        # The count is asserted against the transcribed table, not a bare
        # literal: a fixture that grows a tenth test fails here until the
        # expectation above is updated to say what the tenth one is.
        assert len(parsed) == len(_FEBRUARY_2025) == 9
        assert set(parsed) == set(_FEBRUARY_2025)
        for name, (threshold, current, result) in _FEBRUARY_2025.items():
            test = parsed[name]
            assert test.required_pct == Decimal(threshold), name
            assert test.current_pct == Decimal(current), name
            assert test.result is result, name

    def test_the_two_like_typed_columns_are_not_interchangeable(self):
        """Every row's threshold is strictly below its current value.

        The failure this catches is a swapped pair, which is invisible to any
        check that only asks whether a number is present: both columns are
        percentages in the same range. A report whose tests all pass states a
        ratio above its level, so a swap inverts all nine at once.
        """
        for test in _february_summary().coverage_tests:
            assert test.required_pct < test.current_pct, test.name

    def test_class_a_b_headroom_is_the_reported_one(self):
        """9.35pp -- the figure a CLO buyer reads, from the report, not derived."""
        a_b = next(
            t
            for t in _february_summary().coverage_tests
            if t.name == "Class A/B Par Value Test"
        )
        assert a_b.current_pct - a_b.required_pct == Decimal("9.35")


class TestCoverageIsSelectedFromTheReportThatPrecedesThePeriod:
    def test_december_report_is_read_for_an_early_january_period(self):
        coverage, as_of = api._coverage_as_reported("cairn-clo-xvii", "2025-01-08")
        assert as_of == "16/12/2024"
        a_b = coverage["class_a_b_par_value_test"]
        assert (a_b.required_pct, a_b.current_pct) == (
            Decimal("130.08"),
            Decimal("139.20"),
        )

    def test_a_later_report_is_never_borrowed_backwards(self):
        """February's 139.43 must not be stated as of a January period."""
        coverage, as_of = api._coverage_as_reported("cairn-clo-xvii", "2025-01-08")
        assert as_of != "18/02/2025"
        assert coverage["class_a_b_par_value_test"].current_pct != Decimal("139.43")

    def test_a_february_period_reads_the_february_report(self):
        coverage, as_of = api._coverage_as_reported("cairn-clo-xvii", "2025-02-20")
        assert as_of == "18/02/2025"
        assert coverage["class_a_b_par_value_test"].current_pct == Decimal("139.43")
        assert coverage["class_a_b_interest_coverage_test"].required_pct == Decimal("120.00")

    def test_a_period_before_every_report_gets_nothing(self):
        coverage, as_of = api._coverage_as_reported("cairn-clo-xvii", "2024-01-01")
        assert coverage == {}
        assert as_of is None

    def test_a_test_the_trustee_grades_not_applicable_is_withheld(self):
        """Class F: the report states both numbers and declines to grade it.

        Repeating the pair while manufacturing the Passed/Failed verdict the
        trustee withheld would assert something the source does not (#549).
        """
        coverage, _ = api._coverage_as_reported("cairn-clo-xvii", "2025-02-20")
        assert "class_f_par_value_test" not in coverage
        assert "class_e_par_value_test" in coverage

    def test_a_deal_with_no_committed_report_gets_nothing(self):
        """Contego's table is column-major and is not attempted (#533/#555)."""
        coverage, as_of = api._coverage_as_reported("contego-clo-xi", "2025-02-20")
        assert coverage == {}
        assert as_of is None


class TestComplianceReportsTheTrusteesOwnFigures:
    """What reaches the screen, and which of the two sources each figure is from.

    The split is the whole point. A coverage test's LEVEL is a covenant — the
    same 130.08% in December, February and March — so the report supplies it and
    the section stops being blank. A test's RATIO is a measurement OF a period,
    so the engine's own-dated computation wins where it has one, and the report
    fills only what the engine cannot compute at all.
    """

    @pytest.fixture(scope="class")
    def statuses(self):
        result = api.deal_compliance("cairn-clo-xvii")
        return {s["trigger_name"]: s for s in result["trigger_statuses"]}

    def test_the_coverage_section_is_no_longer_blank(self, statuses):
        evaluable = [n for n, s in statuses.items() if s["evaluable"]]
        assert len(evaluable) == 8, sorted(evaluable)

    def test_every_quantified_test_carries_the_level_the_trustee_states(self, statuses):
        """The levels, pinned. This is what was `None` on all ten rows."""
        for name, level in {
            "class_a_b_par_value_test": 130.08,
            "class_c_par_value_test": 121.74,
            "class_d_par_value_test": 112.62,
            "class_e_par_value_test": 107.87,
            "reinvestment_overcollateralisation_test": 104.40,
            "class_a_b_interest_coverage_test": 120.00,
            "class_c_interest_coverage_test": 110.00,
            "class_d_interest_coverage_test": 105.00,
        }.items():
            assert statuses[name]["threshold"] == level, name

    def test_class_a_b_headroom_is_real(self, statuses):
        a_b = statuses["class_a_b_par_value_test"]
        assert a_b["threshold"] == 130.08
        assert a_b["evaluable"] is True
        assert a_b["is_triggered"] is False
        assert a_b["metric_value"] > a_b["threshold"]

    def test_a_par_value_ratio_stays_the_engines_own_dated_figure(self, statuses):
        """139.0768 — which reproduces THIS period's own report, to the cent.

        The nearest trustee report states 139.20 for the same test, as of
        16/12/2024, a different month. Preferring it would overwrite a correctly
        dated figure with a stale one — #524's failure, entered from the other
        side — so the report is never allowed to displace a computed ratio.
        ``tests/test_clo_live_screens.py`` pins the same five figures against the
        document that publishes them; this pins that #626 did not disturb them.
        """
        a_b = statuses["class_a_b_par_value_test"]
        assert a_b["value_source"] == "engine_computed"
        assert a_b["value_as_of"] is None
        assert round(a_b["metric_value"], 2) == 139.08
        assert round(a_b["metric_value"], 2) != 139.20

    def test_an_interest_coverage_test_is_filled_from_the_report(self, statuses):
        """The engine has no IC denominator for this deal, so the report fills it.

        Strictly more than the blank it replaces, and never passed off as this
        period's own: ``value_as_of`` states the month it is from.
        """
        ic = statuses["class_a_b_interest_coverage_test"]
        assert ic["threshold"] == 120.0
        assert ic["metric_value"] == 180.24
        assert ic["evaluable"] is True
        assert ic["value_source"] == "report_supplied"
        assert ic["value_as_of"] == "16/12/2024"

    def test_a_borrowed_figure_always_says_which_month_it_is_from(self, statuses):
        """No report-supplied figure is ever silently re-dated to the period."""
        borrowed = [s for s in statuses.values() if s["value_source"] == "report_supplied"]
        assert borrowed, "the IC and reinvestment tests are report-supplied"
        for status in borrowed:
            assert status["value_as_of"], status["trigger_name"]
            assert status["value_as_of"] != status["period"]

    def test_a_test_the_report_does_not_quantify_still_refuses(self, statuses):
        """No threshold is defaulted to 0.0 (#493), and the reason is kept.

        Class F is graded ``N/A`` by the trustee; the IRR threshold is in no
        report at all. Both keep exactly the refusal they had.
        """
        for name in (
            "class_f_par_value_test",
            "incentive_investment_management_fee_irr_threshold",
        ):
            status = statuses[name]
            assert status["threshold"] is None, name
            assert status["evaluable"] is False, name
            assert status["proximity_pct"] is None, name
            assert status["not_evaluable_reason"], name
