"""Grade the engine against Cairn's committed Priority of Payments (#496, epic #491).

The epic's terminal question: fold Cairn CLO XVII's *own* extracted cascades and
reconcile them against the deal's *own* published Priorities of Payments, then
record whatever comes back. **The Interest cascade now reconciles** — every step,
to the cent, on inputs sourced from documents rather than from the answer — and
this module is the durable record of how it got there, so a regression has a
specific assertion to red rather than a paragraph to re-derive. It did not
reconcile for most of this epic's life; the history below is kept because what
each gap turned out to be is the finding, not the final green.

Nothing here tunes either side. The answer key (#495) and every engine module are
read, never written; the reconciliation runs at the key's own EUR 0.01 tolerance.
Offline and deterministic end to end: the committed seed model plus the committed
Note Valuation Report fixture, through the same ``fold_report_series`` the live
cold-start path and the Green Lion proofs use.

What the run says
-----------------

1. **The grade cannot be reached through the committed key at all.**
   ``reconcile_against_answer_key`` refuses before comparing a single figure: the
   key carries four periods (three authored from monthly trustee reports, one
   from the Note Valuation Report) and only the fourth has a Priority of
   Payments, so a fold built from the one PoP-bearing document supplies one
   period result against the key's four.

2. **Reconciled against that one document directly, every step of the Interest
   cascade now agrees.** The undistributed remainder was EUR 644,398.50; #514,
   #528 and #538 took it to EUR 0.00, the last by resolving Class B onto the two
   strips it was issued in so its step could compute at all. That left two steps
   wrong by equal and opposite amounts — Class B over-accruing its fixed-rate
   strip by EUR 14,312.50 on the floating day count, and the ``(CC)`` residual
   sweep beneath it starved by exactly that — which the aggregate could not see,
   because the pot is fixed and the sweep absorbs any senior step's error.
   **#539 closed it** by giving each strip the day-count basis its own Condition
   states. ``steps_passed`` is asserted throughout rather than the tie-out, for
   the reason that period made plain: a perfect total is compatible with two
   wrong steps, so the aggregate is corroboration and never the grade.

   a. **The join is closed; what is left of it is Class B's.** The report prints
      62 rows and the extracted cascade carries 29 top-level labels, so 20 report
      rows matched none of them and the eight carrying money (``(A)(i)``,
      ``(A)(ii)``, ``(H)(i)``, ``(H)(ii)``, ``(CC)(1)(a)``, and two the report
      re-letters bare ``(a)``) accounted for EUR 1,820,150.42 to the cent.
      ``report_label_fold`` now joins the report's hierarchy — prefixed children,
      amountless headers, re-lettered children — so **every published row reaches
      a step**. Class B's ``(H)(i)``/``(H)(ii)`` money is the share that then had
      a step but no figure: the engine could not evaluate it, so the line failed
      by its full published value. The failure moved from the join to the engine,
      which is where it belonged. **Its cause was neither #514's nor #520's**:
      since #520 both Class B strips are seeded with real balances, but
      ``class_b_notes_interest`` resolves to a canonical ``class_b_interest``
      whose need was looked up against a tranche named ``class_b``, and Cairn has
      ``class_b_1`` and ``class_b_2`` — a recipient-to-tranche naming seam, and
      **#538 closed it** by resolving a class recipient onto every strip the class
      was issued in and summing their accruals.

   b. **The EUR 194,340.11 day-count gap is closed.** Since #511 the Class A and
      Class C interest steps are engine-computed rather than handed the report's
      figure; #512 supplied the published applied rates so both resolve a coupon;
      and **#528** supplied the accrual period, which was the single factor both
      were short by. See item 3.

   c. **The second day-count gap was a different kind, and #539 sourced it.**
      Class B-2 is the deal's only fixed-rate strip — the report prints it
      ``FXR`` where every other class is ``FLR`` — and it accrues on a different
      *fraction*, not merely a different period, so no single day count could
      serve both strips of one class. #538 stopped rather than choose a
      convention, because picking 30/360 *because* it reproduces the published
      figure is the back-solving #511 and #528 removed. #539 read it from the
      document instead: Condition 6(e)(iii) states *"a 360-day year consisting of
      12 months of 30 days each"* for the Class B-2 Notes, Condition 6(e)(ii)
      states the actual number of days for the six floating classes, and the
      *Accrual Period* proviso puts the fixed class's period between
      **unadjusted** Payment Dates. The seed carries the parsed bases per class;
      ``tests/test_day_count_parser.py`` holds the parse and its refusals.

3. **Three lines of the cascade are genuinely engine-computed, and all three now
   tie — with every input sourced from a document rather than from the answer.**
   Classes A and C are shown first; Class B is the third and is shown below,
   because it ties over two strips on two conventions. (This item read "two"
   while its own body already named the third — #515 measured the count and
   corrected the heading rather than the assertion.)
   Before #511 the membership test ran against the *raw* extracted recipient, so
   Cairn's ``class_a_notes_interest`` missed the set's ``class_a_interest`` and
   every step was classified ``report-supplied`` — its amount taken from the
   report under its own label and compared to itself. Resolving the spelling
   first makes the three note-interest steps engine-computed. With #512's
   published applied rates in place, Classes A and C each resolve a coupon, and
   the engine derives the need from the seed's own tranche size and that rate —
   ``balance × rate/100 / 360 × days`` — with **no report input**::

     Class A:  EUR 248,000,000 @ 5.008% x 95/360  ->  3,277,457.78  published 3,277,457.78
     Class C:  EUR  23,100,000 @ 6.808% x 95/360  ->    415,004.33  published   415,004.33

   **Provenance is the proof, not the tie.** While the engine echoed the report a
   tie asserted nothing: a figure copied from the report matches it by
   construction, and that circularity is what epic #510 exists to remove. Class A
   appeared to tie before #512 only because the fold, lacking a published rate,
   fell back to a coupon back-solved from the very amount being checked — the
   report agreeing with itself one layer down. So the question to ask of the
   agreement above is not whether it holds but where each half came from.

   Every input is now a document's: the tranche size from the seed's capital
   structure, the applied rate from the trustee report's resolved-coupon column
   (#512), and the accrual period from the Listing Particulars' **stated Payment
   Date schedule** (#528) — 18 January, April, July and October, modified
   following over a Business Day spanning T2, London, Dublin and New York. The
   period runs from the 18 October 2024 Payment Date to the 21 January 2025 one:
   95 days, measured between two dates the prospectus names.

   **That 95 was previously reachable only by back-solving it** from the
   published interest, which is why #521 stood down rather than commit it. It is
   now derived from the schedule, and ``test_the_residual_was_the_day_count``
   asserts the residual moved for that reason. The derivation's agreement with
   the back-solved figure is its result, not its method.

   Class B is the third, and it ties too — over **two** strips on **two**
   conventions::

     Class B-1: EUR 24,600,000 @ 5.958% x 95/360  ->    386,773.50  published   386,773.50
     Class B-2: EUR 15,000,000 @ 6.870% x 90/360  ->    257,625.00  published   257,625.00

   The 95 is Act/360 between the adjusted Payment Dates, the 90 is 30/360 between
   the unadjusted ones. Neither was divided out of a published amount: the
   fractions come from the two Conditions above and the dates from the schedule,
   so the agreement is a result exactly as Class A's is.

   ``steps_passed`` fell 29 → 27 at #511 as two lines started computing, returned
   to 29 at #528 as they started agreeing, fell again at #514 and #538 as Class B
   became a real comparison, and returns to 29 at #539. None of those moves is the
   engine regressing or the grade being relaxed: each dip is a step that stopped
   being compared against itself, and the 29 now are comparisons against
   independently derived halves.

3b. **The reconciliation once *labelled* every step ``report-supplied``.**
   ``reconciler._source_of`` was a second raw-membership test over the same set
   that #511 did not touch, so ``engine_computed_passed`` read 0 while the fold
   genuinely computed Class A. #514 routed both readers through one
   ``step_source_classifier.is_engine_computed``; the count now reads 3, and is
   pinned below so a regression to a second membership test reds.

4. **The Principal cascade reconciles, and proves nothing.** The report states
   EUR 0.00 of available principal funds, so an engine that never pays anything
   reproduces it exactly — the bound ``answer_keys/README.md`` predicted before
   the grade was run, now measured.

Why this is a test and not a script: each figure above is an assertion that reds
if the engine, the parser or the key moves, which is the only form in which a
finding stays true. See ``docs/data-card.md``'s Cairn row for the operator-facing
statement of the same result.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from loanwhiz.api.main import fold_report_series
from loanwhiz.config import DEAL_REGISTRY
from loanwhiz.extraction.assembler import DealModel
from loanwhiz.extraction.day_count_parser import ClassDayCount, class_accrual_days
from loanwhiz.extraction.payment_schedule_parser import (
    PaymentDateSchedule,
    accrual_period_days,
    payment_date_on_or_after,
)
from loanwhiz.primitives.notes_cash_parser import NotesCashReport
from loanwhiz.primitives.period_state_machine import DealStateSeries
from loanwhiz.primitives.reconciler import ReconciliationReport, reconcile_series
from loanwhiz.primitives.reconciliation_answer_key import (
    DealAnswerKey,
    load_answer_key,
    reconcile_against_answer_key,
)
from loanwhiz.primitives.report_adapter import DEFAULT_TRANCHE_CLASSES, ReportAdapter
from loanwhiz.primitives.report_label_fold import fold_report_pop
from loanwhiz.primitives.step_source_classifier import ENGINE_COMPUTED_RECIPIENTS
from loanwhiz.domain.rules import RecipientType
from loanwhiz.primitives.waterfall_interpreter import WaterfallFunds, _canonical_recipient
from tests.clo_answer_key_source import CLO_DEAL_ID, CLO_DEAL_NAME, clo_note_valuation_report

SEED_PATH = (
    Path(__file__).resolve().parents[1]
    / "src" / "loanwhiz" / "data" / "deals" / "seed" / "cairn-clo-xvii-dac.json"
)

#: The period the Note Valuation Report covers — the key's only PoP-bearing one.
NVR_PERIOD = "January 2025"

#: The report's stated available revenue for that period.
PUBLISHED_AVAILABLE_REVENUE = 7_255_062.35

#: The published rows that joined no cascade step at all before #514. Kept as a
#: constant after the join closed for two reasons: it is the figure
#: ``docs/data-card.md``, ``README.md`` and the answer-key README still state
#: (restating them is #515's — ``docs/**`` is outside #514's scope, and editing
#: the constant instead of the cards would hide the drift rather than report it),
#: and ``test_the_rows_that_joined_no_step_now_reach_their_steps`` re-derives it
#: from the document to prove the join closed rather than that a number was
#: retyped.
UNJOINED_REVENUE_ROWS_TOTAL = 1_820_150.42

#: Class B's published interest, split across the two sub-lettered rows the
#: cascade's single ``(H)`` step claims since #514 — one row per strip, ``(H)(i)``
#: for B-1 and ``(H)(ii)`` for B-2. Since #538 the step computes a figure of its
#: own to compare against this one, so the comparison fails by the difference
#: rather than by the whole amount.
CLASS_B_PUBLISHED_INTEREST = 386_773.50 + 257_625.00

#: The money #514's fold moved onto its parent steps — the rows above, less Class
#: B's share, which #538 moved onto the engine's side of the ledger. Derived from
#: the pair either side of it so a change to either reds rather than silently
#: re-balancing.
PLACED_BY_THE_FOLD = UNJOINED_REVENUE_ROWS_TOTAL - CLASS_B_PUBLISHED_INTEREST

#: What the report publishes for the two lines the engine computes for itself.
PUBLISHED_CLASS_A_INTEREST = 3_277_457.78
PUBLISHED_CLASS_C_INTEREST = 415_004.33

#: The inputs those two lines are computed from: the seed's tranche sizes and
#: #512's published applied rates. No report figure is among them — which is what
#: makes the lines independent, and their agreement below meaningful.
CLASS_A_SIZE_EUR, CLASS_A_APPLIED_RATE_PCT = 248_000_000.00, 5.008
CLASS_C_SIZE_EUR, CLASS_C_APPLIED_RATE_PCT = 23_100_000.00, 6.808

#: Class B's two strips, the same two inputs each. Cairn sells Class B as B-1
#: floating and B-2 fixed, and the report publishes an applied rate for each; the
#: class's need is the sum over both (#538). ``class_b`` names no tranche at all,
#: which is the seam that issue closed.
CLASS_B_1_SIZE_EUR, CLASS_B_1_APPLIED_RATE_PCT = 24_600_000.00, 5.958
CLASS_B_2_SIZE_EUR, CLASS_B_2_APPLIED_RATE_PCT = 15_000_000.00, 6.87

#: ``WaterfallFunds.days_in_period`` still defaults to 90 — the quarterly Act/360
#: approximation — and #528 deliberately left it there: it is legitimate where a
#: deal states nothing better, and every deal without a committed schedule keeps
#: the numbers it had.
INTERPRETER_DEFAULT_DAYS = 90

#: The reporting date of the Note Valuation Report the PoP period is graded from.
NVR_REPORTING_DATE = date(2025, 1, 8)

#: Cairn's accrual period for that reporting date — **derived here, never
#: transcribed** (#528). It is read out of the seed's own stated Payment Date
#: schedule by the same code the engine uses, so this constant cannot drift from
#: the engine's day count and cannot be quietly edited to whatever makes the
#: assertions below pass. Writing ``95`` here as a literal is what the epic
#: forbids: that number was originally reachable only by dividing the published
#: interest by the engine's own figure.
_CAIRN_SCHEDULE = PaymentDateSchedule.from_dict(
    json.loads(SEED_PATH.read_text(encoding="utf-8"))["payment_schedule"]
)
_CAIRN_PAYMENT_DATE = payment_date_on_or_after(_CAIRN_SCHEDULE, NVR_REPORTING_DATE)
CAIRN_ACCRUAL_DAYS = accrual_period_days(_CAIRN_SCHEDULE, _CAIRN_PAYMENT_DATE)

#: Each class's own day-count basis, read from the seed the same way the schedule
#: is — so these cannot drift from what the engine applies, and cannot be edited
#: to whatever makes an assertion pass (#539). The bases come from Condition 6(e);
#: the *numbers* below are what those bases produce over the two Payment Dates the
#: schedule states, never a figure divided out of a published amount.
_CAIRN_DAY_COUNTS = {
    key: ClassDayCount.from_dict(raw)
    for key, raw in json.loads(SEED_PATH.read_text(encoding="utf-8"))[
        "note_day_counts"
    ].items()
}
CLASS_B_1_ACCRUAL_DAYS = class_accrual_days(
    _CAIRN_SCHEDULE, _CAIRN_DAY_COUNTS["B-1"], _CAIRN_PAYMENT_DATE
)
CLASS_B_2_ACCRUAL_DAYS = class_accrual_days(
    _CAIRN_SCHEDULE, _CAIRN_DAY_COUNTS["B-2"], _CAIRN_PAYMENT_DATE
)

#: What the engine derives for each line: size x published rate x that day count.
#: Derived rather than transcribed for the same reason — these are the engine's
#: own arithmetic restated, so a change in the day count moves them automatically
#: and the tie asserted below stays a real comparison of two independent halves.
ENGINE_COMPUTED_CLASS_A_INTEREST = (
    CLASS_A_SIZE_EUR * CLASS_A_APPLIED_RATE_PCT / 100 / 360 * CAIRN_ACCRUAL_DAYS
)
ENGINE_COMPUTED_CLASS_C_INTEREST = (
    CLASS_C_SIZE_EUR * CLASS_C_APPLIED_RATE_PCT / 100 / 360 * CAIRN_ACCRUAL_DAYS
)

#: Class B's engine need: both strips, each at its own published rate, and since
#: #539 each over **its own** day count. There is no single accrual period for this
#: class — B-1 floats over the adjusted Payment Dates, B-2 is fixed and measures
#: 30/360 between the unadjusted ones — which is precisely why #538's single-period
#: version missed. Derived like its siblings, so a regression in either mechanism
#: moves it rather than being absorbed.
ENGINE_COMPUTED_CLASS_B_INTEREST = (
    CLASS_B_1_SIZE_EUR * CLASS_B_1_APPLIED_RATE_PCT / 100 / 360 * CLASS_B_1_ACCRUAL_DAYS
    + CLASS_B_2_SIZE_EUR
    * CLASS_B_2_APPLIED_RATE_PCT
    / 100
    / 360
    * CLASS_B_2_ACCRUAL_DAYS
)

#: Derived, never transcribed: a regression in any mechanism moves these.
#: A's and C's day-count gaps are **zero** since #528 — that is the finding, and
#: writing them as differences rather than as ``0.0`` is what keeps them able to
#: reopen.
CLASS_A_DAY_COUNT_GAP = PUBLISHED_CLASS_A_INTEREST - ENGINE_COMPUTED_CLASS_A_INTEREST
CLASS_C_DAY_COUNT_GAP = PUBLISHED_CLASS_C_INTEREST - ENGINE_COMPUTED_CLASS_C_INTEREST
DAY_COUNT_SHORTFALL = CLASS_A_DAY_COUNT_GAP + CLASS_C_DAY_COUNT_GAP

#: **Class B's is zero since #539**, and how it reached zero is the point. B-1
#: (floating) already tied on the Act/360 period above. B-2 is the deal's one
#: fixed-rate strip — the report prints it ``FXR`` where every other class is
#: ``FLR`` — and the engine over-accrued it on the floating convention. #538
#: measured that residual and stood down rather than name the fraction; #539
#: sourced it from Condition 6(e)(iii), which states a 360-day year of twelve
#: 30-day months, with the *Accrual Period* proviso putting that class's period
#: between **unadjusted** Payment Dates. The gap closing is the **result** of
#: reading those two sentences, never the method: no test here divides a
#: published figure by anything, and the residual was not used as a target, a
#: check or a bound. Written as a difference rather than as ``0.0`` so it reopens
#: if either half regresses.
CLASS_B_DAY_COUNT_GAP = CLASS_B_PUBLISHED_INTEREST - ENGINE_COMPUTED_CLASS_B_INTEREST

#: The residual sweep's side of that same error, now also zero. The cascade's pot
#: is fixed, so every cent Class B over-claimed was one the ``(CC)`` sweep beneath
#: it was denied: the two were equal and opposite **by construction**, which is
#: why the aggregate could not see either. Kept as a derived term rather than
#: deleted — it is the shadow that reappears the moment a senior step over-draws
#: again, and a test asserting it is zero is what notices.
RESIDUAL_SWEEP_SHORTFALL = -CLASS_B_DAY_COUNT_GAP

#: What the fold distributes through the 29-step Interest cascade. Since #538
#: every step evaluates, so the cascade pays out the published pot exactly.
ENGINE_DISTRIBUTED_REVENUE = PUBLISHED_AVAILABLE_REVENUE

#: What the cascade fails to place across the whole Interest waterfall: **zero**,
#: and this module's sharpest point is that **the zero is not the grade.**
#:
#: Before #538 this aggregate was the headline — EUR 644,398.50 of published
#: revenue the engine never placed. Now every published cent reaches a step and
#: is distributed, so it reads perfectly while two steps are wrong by equal and
#: opposite amounts. A grade reporting only this total would call the cascade
#: correct; ``steps_passed`` and the per-step deltas are the only surviving
#: signal, and the tests below assert the aggregate's zero **together with** the
#: two non-zero deltas so the cancellation can never pass for success.
REVENUE_SHORTFALL = PUBLISHED_AVAILABLE_REVENUE - ENGINE_DISTRIBUTED_REVENUE

#: The money-carrying Interest rows that joined no engine label before #514, and
#: what each pays. The report re-letters two management-fee rows bare ``(a)``, so
#: the labels are not unique — hence a list of pairs rather than a mapping, and
#: hence why the fold cannot key on the label alone.
UNJOINED_REVENUE_ROWS: tuple[tuple[str, float], ...] = (
    ("(A)(i)", 6_388.00),
    ("(A)(i)", 57.50),
    ("(A)(ii)", 250.00),
    ("(a)", 155_457.93),
    ("(H)(i)", 386_773.50),
    ("(H)(ii)", 257_625.00),
    ("(a)", 362_735.16),
    ("(CC)(1)(a)", 650_863.33),
)

#: (``CLASS_B_PUBLISHED_INTEREST`` is defined above, beside the rows it is drawn
#: from — ``REVENUE_SHORTFALL`` derives from it.)


# ---------------------------------------------------------------------------
# Fixtures — the offline fold, built the one way that needs no new decision.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def clo_model() -> DealModel:
    """The committed extracted seed — 29 Interest steps, 23 Principal, 8 classes."""
    return DealModel.model_validate_json(SEED_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def nvr_report() -> NotesCashReport:
    """The Note Valuation Report the key's PoP period was authored from.

    Read through the *same* helper that authored the key, so the engine is graded
    against the document the key states, not a second reading of it.
    """
    return clo_note_valuation_report()


@pytest.fixture(scope="module")
def clo_series(clo_model: DealModel, nvr_report: NotesCashReport) -> DealStateSeries:
    """Fold the CLO through the shared report path — no per-deal constant.

    ``ReportAdapter.from_deal_model`` is used with its defaults deliberately.
    Since #520 the tranche classes are no longer one of those defaults: they are
    read off the deal's own ``tranche_structure``, so all eight of Cairn's classes
    are seeded and there is no modelling decision left to make here. The residual
    label still is one — #496 is forbidden from choosing it to reach a cell state,
    and ``test_the_finding_survives_the_adapter_choice`` shows the choice cannot
    change this result anyway.
    """
    return fold_report_series(clo_model, nvr_report, ReportAdapter.from_deal_model(clo_model))


@pytest.fixture(scope="module")
def clo_key() -> DealAnswerKey:
    """Cairn's committed answer key — #481's covenant periods plus #495's PoP one."""
    key = load_answer_key(DEAL_REGISTRY[CLO_DEAL_ID])
    assert key is not None, "the CLO's answer key should be committed"
    return key


@pytest.fixture(scope="module")
def recon(clo_series: DealStateSeries, nvr_report: NotesCashReport) -> ReconciliationReport:
    """The grade, run at the key's own tolerance against the key's own document."""
    return reconcile_series(
        clo_series, nvr_report, deal_name=CLO_DEAL_NAME, tolerance=0.01
    )


# ---------------------------------------------------------------------------
# 1. The committed key grades the period it has ground truth for (#513).
# ---------------------------------------------------------------------------


def test_the_committed_key_grades_its_one_pop_bearing_period(
    clo_series: DealStateSeries, clo_key: DealAnswerKey, recon: ReconciliationReport
) -> None:
    """Four key periods, one foldable document — and the grade is now reachable.

    #496 recorded a refusal here: three of the key's four periods are authored
    from monthly trustee reports, which state no Priority of Payments and give a
    fold nothing to produce a period result from, so ``reconcile_series`` saw one
    period result against four report periods and raised on the join before
    comparing a figure. #513 narrowed the join to the PoP-bearing periods, so the
    key grades what it has ground truth for.

    The assertion that matters is the *equality*: grading through the committed
    key must reach exactly the figures #496 reached by bypassing it. Anything
    else would mean the key path and the direct path disagree about the same
    document, and every number this module pins would be true of only one of them.
    """
    assert len(clo_series.period_results) == 1
    assert len(clo_key.periods) == 4
    assert sum(1 for p in clo_key.periods if p.revenue_pop or p.redemption_pop) == 1

    via_key = reconcile_against_answer_key(clo_series, clo_key)

    assert via_key.periods_checked == 1
    assert [p.model_dump() for p in via_key.periods] == [
        p.model_dump() for p in recon.periods
    ]


def test_the_three_trustee_periods_are_skipped_and_never_pass(
    clo_series: DealStateSeries, clo_key: DealAnswerKey
) -> None:
    """The other three periods are reported not-graded — not graded as passes.

    This is the hazard #513 names, and it earns its own assertion because the
    natural implementation walks straight into it: an ``AnswerKeyPeriod`` with no
    PoP projects to a period with no steps and a ``None`` pot, and
    ``WaterfallReconciliation.passed`` is ``all()`` over an empty step list plus a
    tie-out of 0.00 against 0.00 — so a skipped period modelled as a
    ``PeriodValidation`` would read ``passed is True``. This key would then report
    three-quarters green having compared nothing, which is exactly the vacuity
    ``answer_keys/README.md`` exists to prevent.

    So: the skipped periods are absent from ``periods`` and from both counts, and
    the tally is 1-of-1 rather than 4-of-4. The graded period passes since #539,
    which makes the distinction sharper rather than moot: a key reporting 4-of-4
    would now be claiming three periods reconciled that nothing compared.
    """
    via_key = reconcile_against_answer_key(clo_series, clo_key)

    graded_dates = {p.reporting_date for p in via_key.periods}
    skipped_dates = {sp.reporting_date for sp in via_key.skipped_periods}
    assert graded_dates == {"2025-01-08"}
    assert skipped_dates == {"2024-12-16", "2025-02-18", "2025-03-18"}
    assert not graded_dates & skipped_dates

    # The counts are over the graded set alone — never over the key's periods.
    assert (via_key.periods_checked, via_key.periods_passed) == (1, 1)
    assert via_key.periods_skipped == 3
    assert via_key.passed is True

    # Each skip names the source document as the reason, not the engine or the key.
    for skipped in via_key.skipped_periods:
        assert "publishes no Priority of Payments" in skipped.reason

    # And the human summary says so too: "0/1 periods reconciled" beside three
    # unnamed absences would read as a complete grade of a one-period deal.
    summary = via_key.summary()
    assert "1/1 periods" in summary
    for label in ("December 2024", "February 2025", "March 2025"):
        assert f"{label}): not graded" in summary


def test_the_keys_pop_period_is_the_report_the_grade_uses(
    clo_key: DealAnswerKey, nvr_report: NotesCashReport
) -> None:
    """The document graded below is the one the key's PoP period was authored from.

    Without this the run would be reconciling against *a* reading of the report
    rather than against the committed ground truth, and a disagreement could be
    two parses differing rather than the engine missing.
    """
    (pop_period,) = [p for p in clo_key.periods if p.revenue_pop or p.redemption_pop]
    (report_period,) = nvr_report.periods

    assert pop_period.period_label == report_period.period_label == NVR_PERIOD
    assert pop_period.available_revenue_funds == report_period.available_revenue_funds
    assert pop_period.available_principal_funds == report_period.available_principal_funds
    assert [s.priority for s in pop_period.revenue_pop] == [
        s.priority for s in report_period.revenue_pop
    ]
    assert [s.amount for s in pop_period.revenue_pop] == [
        s.amount for s in report_period.revenue_pop
    ]


# ---------------------------------------------------------------------------
# 2. The grade itself — the Interest cascade reconciles, and why that is a claim.
# ---------------------------------------------------------------------------


def test_the_interest_cascade_reconciles(recon: ReconciliationReport) -> None:
    """The headline: every step agrees and the total ties, on independent halves.

    ``WaterfallReconciliation.passed`` requires both that every joined step agrees
    and that the distributed total ties to available funds. #511 broke **both**
    gates; #514 closed the join, #528 restored the step-level gate for Classes A
    and C, #538 gave Class B a computable need by resolving the class onto its
    strips, and #539 gave each strip the day-count basis its own Condition states.

    **The tie-out gate is still not what makes this pass**, and that is worth
    keeping in view now that it is green. The pot is fixed and the ``(CC)``
    residual sweep absorbs whatever the steps above it leave, so a step that
    over-claims is funded by starving the sweep and the total never moves — the
    aggregate was a perfect zero through the whole period when two steps were
    wrong by equal and opposite amounts. So the aggregate is asserted here
    **beside** the step-level result, never instead of it: ``steps_passed`` and
    the per-step deltas are the only signal that can tell a reconciled cascade
    from a cancelling one.
    """
    assert recon.passed is True
    assert recon.periods_passed == 1
    assert recon.periods_checked == 1

    revenue = recon.periods[0].revenue
    assert revenue.passed is True
    assert revenue.available_funds == pytest.approx(PUBLISHED_AVAILABLE_REVENUE, abs=0.01)
    assert revenue.report_total == pytest.approx(PUBLISHED_AVAILABLE_REVENUE, abs=0.01)
    assert revenue.engine_total == pytest.approx(ENGINE_DISTRIBUTED_REVENUE, abs=0.01)
    assert revenue.unapplied_rounding == 0.0

    # The aggregate ties — and this pair of assertions is still the point. The
    # zero was equally real when two steps were wrong, so it is asserted together
    # with the step count that gives it meaning.
    assert revenue.report_total - revenue.engine_total == pytest.approx(
        REVENUE_SHORTFALL, abs=0.01
    )
    assert REVENUE_SHORTFALL == pytest.approx(0.0, abs=0.01)
    assert revenue.steps_passed == len(revenue.steps)

    # No published row is missing from the comparison — the gap is a statement
    # about the engine alone. A regression in the fold would refill this list and
    # quietly re-inflate the figure above (#514).
    assert revenue.unjoined_report_rows == []


def test_every_step_agrees(
    recon: ReconciliationReport,
) -> None:
    """Every step of the Interest cascade matches its published row.

    Stated as its own assertion because several distinct failures were in play
    and #510's whole discipline is not conflating them:

    - **the join** — EUR 1,820,150.42 of published rows matched no step (#514).
      **Closed**: every row now reaches one;
    - **two step-level deltas** — Class A and Class C interest, which #511 made
      engine-computed and #512 gave resolvable published coupons. Both were short
      by the interpreter's 90-day default against the deal's real accrual period.
      **#528 closed those**, and this test is where that shows;
    - **Class B's refusal** — the recipient named a class the deal issued in two
      strips and the lookup wanted one tranche of that name. **#538 closed that**:
      the class resolves to both strips and the step computes.

    - **Class B's day count** — the class resolved onto two strips accruing on
      two different conventions, and one day count cannot serve both.
      **#539 closed that**: each strip is counted on the basis its own Condition
      states, and the ``(CC)`` residual sweep beneath it — short by exactly what
      Class B over-drew from the shared pot — recovered with it.

    **This agreement is not the circular one #511 deleted.** The tie it asserted
    before #511 was the report echoing itself: with no published rate wired, the
    fold back-solved a coupon from the very figure being checked. The tie here is
    between independently sourced halves — the seed's tranche sizes, #512's
    published applied rates, a day count measured between two Payment Dates the
    prospectus states, and a fraction read out of the Condition that states it —
    against the report's published figure. Each half can move without the other,
    so the agreement is a result rather than an identity.

    The two deltas are asserted at zero rather than dropped. They were equal and
    opposite by construction on a shared pot, so a senior step that over-draws
    again reappears here as a pair; a test that simply stopped looking at them
    would let the aggregate's blindness back in.
    """
    revenue = recon.periods[0].revenue
    assert len(revenue.steps) == 29
    assert revenue.steps_passed == 29
    assert [(s.priority, s.recipient) for s in revenue.steps if abs(s.delta) > 0.01] == []

    by_priority = {s.priority: s for s in revenue.steps}
    assert by_priority["(H)"].delta == pytest.approx(-CLASS_B_DAY_COUNT_GAP, abs=0.01)
    assert by_priority["(CC)"].delta == pytest.approx(
        -RESIDUAL_SWEEP_SHORTFALL, abs=0.01
    )
    assert CLASS_B_DAY_COUNT_GAP == pytest.approx(0.0, abs=0.01)
    assert RESIDUAL_SWEEP_SHORTFALL == pytest.approx(0.0, abs=0.01)

    # The three engine-computed lines specifically, named rather than left to the
    # aggregate: a step that agreed because it stopped computing (a EUR 0.00
    # engine amount, the coupon no longer resolving) would otherwise be
    # indistinguishable from one that agreed because it computed correctly. That
    # risk grew with #539, not shrank — every one of these now passes, so a
    # silent refusal would read exactly like a correct answer here.
    by_step = {(s.priority, s.recipient): s for s in revenue.steps}
    class_a = by_step[("(G)", "class_a_notes_interest")]
    class_b = by_step[("(H)", "class_b_notes_interest")]
    class_c = by_step[("(J)", "class_c_notes_interest")]
    assert class_a.engine_amount == pytest.approx(
        ENGINE_COMPUTED_CLASS_A_INTEREST, abs=0.01
    )
    assert class_a.report_amount == pytest.approx(PUBLISHED_CLASS_A_INTEREST, abs=0.01)
    assert class_c.engine_amount == pytest.approx(
        ENGINE_COMPUTED_CLASS_C_INTEREST, abs=0.01
    )
    assert class_c.report_amount == pytest.approx(PUBLISHED_CLASS_C_INTEREST, abs=0.01)
    assert class_b.engine_amount == pytest.approx(
        ENGINE_COMPUTED_CLASS_B_INTEREST, abs=0.01
    )
    assert class_b.report_amount == pytest.approx(CLASS_B_PUBLISHED_INTEREST, abs=0.01)
    assert class_a.engine_amount > 0.0
    assert class_b.engine_amount > 0.0
    assert class_c.engine_amount > 0.0
    assert abs(DAY_COUNT_SHORTFALL) < 0.01

    # The waterfall passes. The tie-out is asserted last and on purpose: it read
    # a perfect zero while two steps were wrong, so it is corroboration for
    # ``steps_passed`` above and never a substitute for it.
    assert revenue.passed is True
    assert revenue.report_total - revenue.engine_total == pytest.approx(0.0, abs=0.01)


def test_the_money_is_undistributed_not_underfunded(
    clo_series: DealStateSeries, recon: ReconciliationReport
) -> None:
    """Nothing is unanswered and nothing is misfed — asserted as the pair.

    Read off the fold directly rather than through the reconciliation, because
    the two readings answer different questions and only this one distinguishes
    the failure modes. The first step sees the report's full stated available
    revenue and nothing is gated.

    **The remainder was undistributed, then misallocated, and is now neither.**
    ``remaining`` was EUR 644,398.50 — money the cascade could not place because
    Class B's step refused — and #538 took it to EUR 0.00 by making that step
    compute. That briefly turned ``total_shortfall`` non-zero: the ``(CC)``
    residual sweep asked for its published figure and was denied part of it,
    because Class B drew more from the shared pot than it was owed. #539 closed
    that by giving Class B's fixed strip its own day count, so the sweep is paid
    in full and both figures are zero together.

    That distinction is what kept the obvious wrong fix off the table throughout.
    An unanswered need wanted a *resolution* (#538); a step denied money because a
    senior step over-drew wanted the senior step's arithmetic corrected (#539),
    never a bigger pot or a re-swept residual. #496 named the residual-sweep fix
    and rejected it. Both figures are asserted at zero **together** below, because
    a shortfall reappearing while ``remaining`` stays zero is exactly the
    misallocation shape, and that is what this test exists to keep visible.

    ``not_evaluable`` is now empty, and that is #538's result stated at the layer
    it happened: every recipient in this cascade has an answer. It is asserted as
    a whole list rather than as Class B's absence so a *new* refusal cannot slip
    in unnoticed (#493's layered refusal, one level up).
    """
    execution = clo_series.period_results[0].revenue_execution

    assert execution.steps[0].amount_available == pytest.approx(
        PUBLISHED_AVAILABLE_REVENUE, abs=0.01
    )
    assert execution.total_distributed == pytest.approx(ENGINE_DISTRIBUTED_REVENUE, abs=0.01)
    assert execution.remaining == pytest.approx(REVENUE_SHORTFALL, abs=0.01)
    assert not [step for step in execution.steps if step.gated]

    # No step is denied money it is owed. Asserted through the derived term rather
    # than a bare 0.0, so a senior step over-drawing again moves both this and the
    # ``(CC)`` delta rather than silently rebalancing.
    assert execution.total_shortfall == pytest.approx(
        RESIDUAL_SWEEP_SHORTFALL, abs=0.01
    )
    assert RESIDUAL_SWEEP_SHORTFALL == pytest.approx(0.0, abs=0.01)

    # Every step answers. Class B was the last refusal in this cascade and #538
    # resolved it onto the two strips the class was issued in.
    assert [step.recipient for step in execution.steps if step.not_evaluable] == []

    # And the reconciliation agrees the pot tied out — the aggregate that no
    # longer detects the misallocation above.
    revenue = recon.periods[0].revenue
    assert revenue.report_total - revenue.engine_total == pytest.approx(
        execution.remaining, abs=0.01
    )


def test_the_rows_that_joined_no_step_now_reach_their_steps(
    recon: ReconciliationReport, nvr_report: NotesCashReport
) -> None:
    """The eight sub-lettered rows are still sub-lettered — and now they are placed.

    #496 named this money: the engine's 29 labels are the cascade's top-level
    ones, the report prints 62 rows because it splits several of them into
    sub-lettered components, and eight money-carrying rows matched no label. The
    document has not changed — every one of those labels is still absent from the
    step list — so the *only* thing that can make them reach a step is the join.

    Both halves are asserted deliberately. The first re-derives the old finding
    from the document, so this cannot pass by the report being re-parsed into
    friendlier labels; the second says the fold placed exactly that money, less
    Class B's share, which the engine now computes for itself.
    """
    revenue = recon.periods[0].revenue
    engine_labels = {step.priority for step in revenue.steps}
    (report_period,) = nvr_report.periods

    # Unchanged: not one of these labels appears in the cascade's step list.
    literally_unmatched = [
        (step.priority, step.amount)
        for step in report_period.revenue_pop
        if step.priority not in engine_labels and step.amount != 0.0
    ]
    assert literally_unmatched == [
        (label, pytest.approx(amount, abs=0.01)) for label, amount in UNJOINED_REVENUE_ROWS
    ]
    assert sum(amount for _, amount in literally_unmatched) == pytest.approx(
        UNJOINED_REVENUE_ROWS_TOTAL, abs=0.01
    )

    # Changed: every published cent now reaches a step. Asserted as a conservation
    # property rather than a per-label figure, so it is the join being complete
    # that makes it true — not a list that happens to match.
    assert revenue.unjoined_report_rows == []
    assert sum(step.report_amount for step in revenue.steps) == pytest.approx(
        revenue.report_total, abs=0.01
    )

    # And the money actually moved: the four report-supplied parents of those rows
    # now distribute it. Class B's ``(H)`` is the fifth parent and is absent here
    # on purpose — it is engine-claimed and computes its own figure, which is why
    # PLACED_BY_THE_FOLD is the recovered total less its share.
    #
    # One of those four parents is the ``(CC)`` residual sweep, so since #538 this
    # total carries the sweep's starvation too: it is short by exactly what Class
    # B over-claims. The term is written into the expectation rather than folded
    # into a new constant, so closing #539 reds this line instead of silently
    # re-balancing it — #514's rule, applied to its own assertion.
    recovered = sum(
        step.engine_amount
        for step in revenue.steps
        if step.priority in {"(A)", "(E)", "(X)", "(CC)"}
    )
    assert recovered == pytest.approx(
        PLACED_BY_THE_FOLD - RESIDUAL_SWEEP_SHORTFALL, abs=0.01
    )


def test_class_b_interest_is_computed_from_both_strips_on_their_own_day_counts(
    recon: ReconciliationReport,
    nvr_report: NotesCashReport,
    clo_series: DealStateSeries,
) -> None:
    """The last refusal, resolved — and what it uncovered underneath.

    Before #514 this was a *passing* step and the pass meant nothing: the cascade's
    single ``(H)`` step found no ``(H)`` row, took EUR 0.00 and was compared
    against a folded EUR 0.00. #514 gave it its children's total, which made the
    comparison real and the engine lose it by the full EUR 644,398.50.

    **#538 is why it now computes.** The cause was never a seeding gap — since
    #520 both strips carry real balances — but a naming seam: ``class_b_interest``
    looked its need up against a tranche named ``class_b``, and Cairn issued the
    class as ``class_b_1`` and ``class_b_2``. A class recipient now resolves onto
    every strip the class was issued in and sums their accruals, so the step
    produces a figure derived from the seed's two tranche sizes, #512's two
    published applied rates, and #528's accrual period.

    **#539 is why it now ties.** B-1 already tied to the cent. B-2 is the deal's
    one fixed-rate strip — printed ``FXR`` where every other class is ``FLR`` —
    and the engine over-stated it on the floating Act/360 day count. #538 stopped
    short of naming the fraction on purpose, because choosing 30/360 *because* it
    makes the figure tie is the back-solving #511 and #528 removed. #539 read it
    instead: Condition 6(e)(iii) states a 360-day year of twelve 30-day months for
    the Class B-2 Notes, and the *Accrual Period* proviso puts that class's period
    between unadjusted Payment Dates.

    **So the tie below is a result, not a method.** Nothing in this file divides a
    published figure by anything to obtain a day count, and the residual #538
    measured was used as neither target, check nor bound. Had the Condition said
    something else, this assertion would be red and that would be the finding.

    Both halves are asserted rather than described — the tranche names the deal
    really seeds, the two day counts their Conditions produce, and the engine
    figure they sum to — so a regression to a single-tranche lookup or to one
    shared day count reds here rather than quietly halving or inflating the need.
    """
    (class_b,) = [s for s in recon.periods[0].revenue.steps if s.priority == "(H)"]
    assert class_b.recipient == "class_b_notes_interest"
    assert class_b.report_amount == pytest.approx(CLASS_B_PUBLISHED_INTEREST, abs=0.01)
    assert class_b.source == "engine"

    # The step computes, and computes the SUM of the strips. Both bounds are
    # asserted: either strip alone is roughly half the need and would tie to
    # nothing, so a resolution that picked one would land between them.
    assert class_b.engine_amount == pytest.approx(
        ENGINE_COMPUTED_CLASS_B_INTEREST, abs=0.01
    )
    b1_alone = (
        CLASS_B_1_SIZE_EUR
        * CLASS_B_1_APPLIED_RATE_PCT
        / 100
        / 360
        * CLASS_B_1_ACCRUAL_DAYS
    )
    b2_alone = (
        CLASS_B_2_SIZE_EUR
        * CLASS_B_2_APPLIED_RATE_PCT
        / 100
        / 360
        * CLASS_B_2_ACCRUAL_DAYS
    )
    assert class_b.engine_amount > b1_alone
    assert class_b.engine_amount > b2_alone

    # The two strips do NOT share a day count, which is the property that makes
    # this per-class rather than per-deal. Asserted directly: a regression that
    # put both back on one count would still sum to something plausible.
    assert CLASS_B_1_ACCRUAL_DAYS != CLASS_B_2_ACCRUAL_DAYS
    assert CLASS_B_1_ACCRUAL_DAYS == CAIRN_ACCRUAL_DAYS

    # And it ties. Stated per strip rather than only as the total, so a future
    # drift in one cannot be hidden by a compensating drift in the other.
    assert class_b.passed is True
    assert class_b.delta == pytest.approx(-CLASS_B_DAY_COUNT_GAP, abs=0.01)
    assert b1_alone == pytest.approx(386_773.50, abs=0.01)
    assert b2_alone == pytest.approx(257_625.00, abs=0.01)

    # A naming seam, not a seeding gap: both strips are seeded with real balances
    # and no tranche is spelled the canonical name the recipient resolves to. The
    # class resolves onto them anyway — which is the whole of #538.
    seeded = {t.name: t for t in clo_series.states[0].tranches}
    assert {"class_b_1", "class_b_2"} <= set(seeded)
    assert seeded["class_b_1"].balance > 0.0 and seeded["class_b_2"].balance > 0.0
    assert "class_b" not in seeded
    assert (
        _canonical_recipient("class_b_notes_interest") is RecipientType.class_b_interest
    )

    # Read the money off the document, so the figure the step is graded against is
    # the report's own and not one this module carries.
    (report_period,) = nvr_report.periods
    published = [s for s in report_period.revenue_pop if s.priority in ("(H)(i)", "(H)(ii)")]
    assert [s.priority for s in published] == ["(H)(i)", "(H)(ii)"]
    assert sum(s.amount for s in published) == pytest.approx(
        CLASS_B_PUBLISHED_INTEREST, abs=0.01
    )
    # The parent label is still absent from the document — so the step's figure
    # came from folding its children, not from a row that was there all along.
    assert not [s for s in report_period.revenue_pop if s.priority == "(H)"]


# ---------------------------------------------------------------------------
# 3. What the passing lines are worth, and what the Principal side proves.
# ---------------------------------------------------------------------------


def test_class_a_interest_is_computed_from_the_deal_model_and_ties(
    clo_series: DealStateSeries, recon: ReconciliationReport
) -> None:
    """The independent line: EUR 3,277,457.78 derived, not copied — and it agrees.

    This is what #511, #512 and #528 bought together, and **the provenance is the
    receipt, not the agreement.** ``ENGINE_COMPUTED_RECIPIENTS`` names the
    recipients the interpreter derives from the deal model with no report input.
    It is spelled in canonical vocabulary (``class_a_interest``) while Cairn's
    cascade carries the document's own spelling (``class_a_notes_interest``), and
    the classifier used to test the raw string, so the membership test missed
    every step including the three classes the set does name. Every step was then
    handed the report's own figure as its "need" and agreed with it by
    construction.

    **This test asserted a tie before #511, and that tie was the bug.** On #511's
    branch no published rate existed, so the fold fell back to an
    amount-recovered coupon — back-solved from the very figure being checked —
    and reproduced EUR 3,277,457.78 to the cent. That was the report agreeing
    with itself one layer down. #512 supplied the genuinely published 5.008%, the
    fallback stopped firing, and the agreement went with it, leaving a residual
    that was purely the 90-day day-count default.

    **The tie is back, and this time all three inputs are sourced.** #528 takes
    the day count from the deal's own stated Payment Date schedule — 18 October
    2024 to 21 January 2025, both Payment Dates the Listing Particulars define —
    rather than from the published interest. So the engine's half is now the
    seed's tranche size, a published applied rate, and a day count measured
    between two stated dates; none of them is the number being checked, and each
    can move without the others. That is what distinguishes this agreement from
    the one deleted above.

    Asserted through the fold (the need the interpreter actually produced) *and*
    the reconciliation (that need against the published figure), because only the
    pair distinguishes "computed" from "copied".
    """
    execution = clo_series.period_results[0].revenue_execution
    (class_a,) = [s for s in execution.steps if s.recipient == "class_a_notes_interest"]
    assert class_a.not_evaluable is False
    assert class_a.need == pytest.approx(ENGINE_COMPUTED_CLASS_A_INTEREST, abs=0.01)

    # Derived from the deal model's own three inputs, so this reds if the need
    # ever starts arriving from somewhere other than size x published rate x the
    # scheduled day count — the amount-recovered fallback creeping back would be
    # caught right here, and so would a day count quietly reverting to 90.
    assert class_a.need == pytest.approx(
        CLASS_A_SIZE_EUR * CLASS_A_APPLIED_RATE_PCT / 100 / 360 * CAIRN_ACCRUAL_DAYS,
        abs=0.01,
    )
    assert CAIRN_ACCRUAL_DAYS != INTERPRETER_DEFAULT_DAYS

    (reconciled,) = [
        s for s in recon.periods[0].revenue.steps if s.recipient == "class_a_notes_interest"
    ]
    assert reconciled.engine_amount == pytest.approx(
        ENGINE_COMPUTED_CLASS_A_INTEREST, abs=0.01
    )
    assert reconciled.report_amount == pytest.approx(PUBLISHED_CLASS_A_INTEREST, abs=0.01)
    assert reconciled.engine_amount == pytest.approx(reconciled.report_amount, abs=0.01)
    assert reconciled.delta == pytest.approx(0.0, abs=0.01)
    assert reconciled.passed is True

    # The set itself gained no CLO spelling — the fix is at the comparison, which
    # is what keeps one vocabulary in the table (#503).
    assert "class_a_notes_interest" not in ENGINE_COMPUTED_RECIPIENTS
    assert "class_a_interest" in ENGINE_COMPUTED_RECIPIENTS


def test_the_residual_was_the_day_count() -> None:
    """Both lines tie at the scheduled period and miss at 90 — one shared cause.

    Class A and Class C have different balances (EUR 248,000,000 /
    EUR 23,100,000) and different coupons (5.008% / 6.808%), so the *only* input
    they share is the day count. Each reproduces its published figure exactly at
    the accrual period the deal's schedule states, and each misses at the 90-day
    default. Two independent lines moving together on one input is a shared
    cause, not a coincidence — which is what made #521's handoff worth measuring
    and what #528 then closed.

    **The day count here is derived, not asserted.** ``CAIRN_ACCRUAL_DAYS`` is
    read out of the seed's stated Payment Date schedule by the same code the
    engine uses. Nothing in this test transcribes it, and nothing divides a
    published figure by an engine figure to obtain it: writing the literal would
    reintroduce exactly the circularity epic #510 exists to remove, because that
    number was originally reachable only that way.

    The two Payment Dates it spans are the prospectus's own — the 18 October 2024
    Payment Date and the 21 January 2025 one — and
    ``tests/test_payment_schedule_parser.py`` checks that resolution against the
    payment dates the committed trustee reports independently print.
    """
    for size, rate, computed, published in (
        (CLASS_A_SIZE_EUR, CLASS_A_APPLIED_RATE_PCT,
         ENGINE_COMPUTED_CLASS_A_INTEREST, PUBLISHED_CLASS_A_INTEREST),
        (CLASS_C_SIZE_EUR, CLASS_C_APPLIED_RATE_PCT,
         ENGINE_COMPUTED_CLASS_C_INTEREST, PUBLISHED_CLASS_C_INTEREST),
    ):
        per_day_act360 = size * rate / 100 / 360
        assert per_day_act360 * CAIRN_ACCRUAL_DAYS == pytest.approx(computed, abs=0.01)
        assert per_day_act360 * CAIRN_ACCRUAL_DAYS == pytest.approx(published, abs=0.01)
        # The old default is what the tie is *against*: asserting the miss keeps
        # this test able to fail. Without it, a day count that silently reverted
        # to 90 would still satisfy every line above.
        assert per_day_act360 * INTERPRETER_DEFAULT_DAYS != pytest.approx(
            published, abs=0.01
        )

    # The schedule is the deal's, read from the seed rather than restated here.
    assert (_CAIRN_SCHEDULE.day_of_month, _CAIRN_SCHEDULE.months) == (18, (1, 4, 7, 10))
    assert _CAIRN_SCHEDULE.commencing == date(2024, 4, 18)
    assert _CAIRN_SCHEDULE.convention == "modified_following"

    # The 90-day default itself is deliberately untouched (#528): it is a fair
    # approximation where a deal states nothing better, and every deal without a
    # committed schedule still takes it. Read off the interpreter rather than
    # restated, so this reds if anyone "fixes" the default instead of the input.
    assert WaterfallFunds.model_fields["days_in_period"].default == INTERPRETER_DEFAULT_DAYS


def test_the_reconciliation_labels_the_computed_steps_engine(
    recon: ReconciliationReport,
) -> None:
    """#511's pinned flip, landed — and the count it was hiding is finally real.

    ``reconciler._source_of`` was a *second* raw-membership test over the same
    set, so the reconciliation called every step ``report-supplied`` — including
    the Class A line #511 proved was computed from the deal model.
    ``engine_computed_passed`` read 0 and understated the grade. #511 could not
    reach it (``reconciler.py`` belonged to #512 and #514) and left this
    assertion red-on-landing by design; #514 routed both readers through one
    ``step_source_classifier.is_engine_computed``.

    **All three engine-claimed steps now reconcile to the cent**, from the deal
    model alone: Class A, Class B and Class C, each from the seed's own tranche
    sizes, #512's published applied rates, #528's measured accrual period and
    — for Class B's two strips — #539's per-class day-count bases. No report
    figure is among the inputs to any of them.

    That number is the epic's actual claim: before #511 it was 0 because nothing
    was computed, after #511 still 0 because nothing was *labelled* computed,
    then 2 while Class B had no computable need. Only now does it mean what it
    says for every step that claims it.
    """
    revenue, redemption = recon.periods[0].revenue, recon.periods[0].redemption

    assert {step.source for step in revenue.steps} == {"engine", "report-supplied"}
    assert [s.recipient for s in revenue.steps if s.source == "engine"] == [
        "class_a_notes_interest",
        "class_b_notes_interest",
        "class_c_notes_interest",
    ]
    assert revenue.engine_computed_passed == 3
    assert [
        s.recipient for s in revenue.steps if s.source == "engine" and not s.passed
    ] == []

    # The Principal cascade names no engine-computed recipient at all, so its 0 is
    # a different fact from revenue's and must not be asserted the same way.
    assert {step.source for step in redemption.steps} == {"report-supplied"}
    assert redemption.engine_computed_passed == 0


def test_the_principal_cascade_reconciles_on_zero_and_proves_nothing(
    recon: ReconciliationReport,
) -> None:
    """The Principal side passes, and the pass carries no signal.

    The report states EUR 0.00 of available principal funds for this period, so
    all 23 extracted Principal steps distribute nothing and an engine that never
    paid anything would reproduce the cascade exactly. Asserted rather than
    omitted, because a graded cell that showed one green waterfall and one red
    would otherwise read as half-validated.
    """
    redemption = recon.periods[0].redemption
    assert redemption.passed is True
    assert redemption.available_funds == 0.0
    assert redemption.engine_total == 0.0
    assert len(redemption.steps) == 23
    assert all(step.report_amount == 0.0 for step in redemption.steps)


def test_the_finding_survives_the_adapter_choice(
    clo_model: DealModel, nvr_report: NotesCashReport
) -> None:
    """Narrowing the fold back to three classes now DOES change the grade — and why.

    This test has inverted twice, and each inversion is a real result rather than
    a maintenance edit. #496 wrote it to show the adapter choice could not reach
    the finding ("no step is engine-computed, so no tranche balance reaches any
    amount"); #511 made three steps engine-computed and #520 made the deal's own
    eight classes the default, so it became a pin on *narrowing* instead.

    **#538 breaks the equivalence, and that is the point.** A class recipient now
    resolves onto the strips the class was issued in, so Class B's need depends on
    ``class_b_1`` and ``class_b_2`` **existing as tranches** — which they do only
    because #520 seeds the deal's own class list. Narrowed back to the
    Green-Lion-shaped ``class_a/b/c`` triple, the deal's Class B strips are never
    seeded at all: the name ``class_b`` is seeded instead, the report publishes no
    balance or rate under that name, and the step refuses exactly as it did before
    #538 for exactly the reason #493 requires.

    So the two issues are coupled, and this asserts the coupling rather than
    describing it: **#538's resolution is only reachable on a deal whose own
    classes are seeded.** A regression in either — a resolution that stopped
    matching strips, or a seeding that truncated back to three classes — lands the
    engine on the pre-#538 total below, and the equality of *that* number with the
    old headline is the tell.

    It keeps its original job too: it reds if a further change makes a
    D-through-F step engine-computed without revisiting the seeding.
    """
    narrowed_adapter = ReportAdapter.from_deal_model(
        clo_model, tranche_classes=DEFAULT_TRANCHE_CLASSES
    )
    series = fold_report_series(clo_model, nvr_report, narrowed_adapter)
    narrowed = reconcile_series(series, nvr_report, deal_name=CLO_DEAL_NAME, tolerance=0.01)

    revenue = narrowed.periods[0].revenue
    assert narrowed.passed is False

    # Narrowed, the strips are gone and Class B refuses again — the pre-#538
    # state, reached by removing #520's seeding rather than by reverting #538.
    seeded = [t.name for t in series.states[0].tranches]
    assert seeded == list(DEFAULT_TRANCHE_CLASSES)
    assert "class_b_1" not in seeded and "class_b_2" not in seeded
    execution = series.period_results[0].revenue_execution
    assert [s.recipient for s in execution.steps if s.not_evaluable] == [
        "class_b_notes_interest",
    ]

    # And the grade falls back by exactly Class B's published figure: the whole
    # class goes unplaced again, rather than the EUR 14,312.50 #539 still owes.
    assert revenue.engine_total == pytest.approx(
        ENGINE_DISTRIBUTED_REVENUE - CLASS_B_PUBLISHED_INTEREST, abs=0.01
    )
    assert revenue.engine_total < ENGINE_DISTRIBUTED_REVENUE

    # Unchanged either way: the fold is a property of the report and the cascade,
    # not of how many classes the adapter seeds.
    assert revenue.engine_computed_passed == 2
    assert revenue.unjoined_report_rows == []


def test_every_declared_class_reaches_the_folded_state_not_just_the_name_list(
    clo_model: DealModel, nvr_report: NotesCashReport, clo_series: DealStateSeries
) -> None:
    """The eight classes arrive as tranches in the folded engine state, by name.

    #512's lesson, asserted where it bites: a complete, correct per-class map can
    reach nothing with no error anywhere, because the lookup is by **tranche
    name** and the class simply has no tranche. The derived name list and the
    per-tranche *arrival* are two separate assertions — one passing does not imply
    the other, and asserting only the first is how this stayed invisible.

    Both layers are checked here on purpose, because #520 found the truncation
    written twice in two different syntaxes: ``ReportAdapter`` held it as a fixed
    tuple, and ``api.main._primitives_seed_from_report_seed`` held it again as
    flat ``class_{a,b,c}_balance=`` constructor kwargs. Fixing the first alone
    left this assertion red.

    Expected names are derived from the committed seed rather than transcribed, so
    a re-extraction that changed the capital structure cannot leave this test
    quietly asserting a stale stack.
    """
    every_class = tuple(
        re.sub(r"[^a-z0-9]+", "_", tranche["name"].lower()).strip("_")
        for tranche in clo_model.tranche_structure
    )
    assert len(every_class) == 8, every_class

    # 1. The adapter names them.
    assert ReportAdapter.from_deal_model(clo_model).tranche_classes == every_class

    # 2. They survive the domain -> engine bridge and arrive on the period-0 state
    #    the fold opens from, which is where a resolved per-class rate must land.
    seeded = [t.name for t in clo_series.states[0].tranches]
    assert seeded == list(every_class)
    # The five the Green Lion triple could never reach, named so a regression to
    # a prefix of the stack reds here rather than passing vacuously.
    assert {"class_b_1", "class_b_2", "class_d", "class_e", "class_f"} <= set(seeded)
    # Class B is #515's sharpest case: both strips carry a real balance, so its
    # published EUR 644,398.50 has a tranche to attach to.
    by_name = {t.name: t for t in clo_series.states[0].tranches}
    assert by_name["class_b_1"].balance > 0.0
    assert by_name["class_b_2"].balance > 0.0



# ---------------------------------------------------------------------------
# 4. The grade changed no committed ground truth.
# ---------------------------------------------------------------------------


def test_grading_left_the_committed_answer_key_untouched(clo_key: DealAnswerKey) -> None:
    """#496 grades; #495 authors. The key on disk is what the reconciler read.

    The authorship/grading split is what stops a failing reconciliation being
    closed from the ground-truth side, and it is worth an assertion rather than a
    convention: a key edited to fit the engine would make every cell vacuously
    green, which is the most damaging failure this surface has available.
    """
    committed = json.loads(
        (
            SEED_PATH.parent.parent / "answer_keys" / "cairn-clo-xvii-dac.json"
        ).read_text(encoding="utf-8")
    )
    assert committed["deal_id"] == CLO_DEAL_ID
    assert committed["tolerance_eur"] == clo_key.tolerance_eur == 0.01
    assert [p["period_label"] for p in committed["periods"]] == [
        p.period_label for p in clo_key.periods
    ]
    (pop_period,) = [p for p in committed["periods"] if p["revenue_pop"]]
    assert pop_period["available_revenue_funds"] == PUBLISHED_AVAILABLE_REVENUE
    assert len(pop_period["revenue_pop"]) == 62


# ---------------------------------------------------------------------------
# 5. The published statements say what was measured, and not what it retracted.
# ---------------------------------------------------------------------------

#: Where the grade is stated for a reader, and what each may no longer claim.
#: Each entry is the whole retracted assertion, lower-cased — never a fragment
#: of one (#471): the corrected prose still contains "the cell refuses on the
#: second, the offline engine series", which is true, and a crude ban on that
#: substring would flag the sentence that now carries the result.
RETRACTED_CLAIMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "docs/data-card.md",
        (
            "the offline engine series, which is #496's",
            "refuses on the second, which is #496's",
            "all 65 of its extracted step recipients resolve to no canonical",
            "no published-report reconciliation",
            # Retracted by #513: the grade IS now reachable through the key.
            "the grade is not reachable at all",
            "so the reconciler refuses the join",
            # Also #513: registering a series would now yield a real failing
            # grade, not a join error. The corrected sentence still says
            # "join-error string", so the ban has to carry the whole claim.
            "replaced a true refusal with a join-error string",
        ),
    ),
    (
        "README.md",
        ("no answer key is authored, so no cell of it is",),
    ),
    (
        "src/loanwhiz/data/deals/answer_keys/README.md",
        (
            "what a graded pop cell here will *not* prove",
            # Retracted by #513. The corrected paragraph still describes what
            # #496 measured ("it raised on the count before comparing a
            # figure"), which is true of its past, so the ban names the
            # present-tense claim that stopped being true.
            "raises on this key before comparing a figure",
            "the union's cadence, not any number in it, is what stands between",
            # Retracted by #514/#538/#539, measured by #515: the row reconciles.
            "and the interest row, run, does not reconcile",
            "so not one line is",
        ),
    ),
    # #515 added this file to the ban-list. Its Limitation 1 was the last
    # committed surface still stating the pre-#514 result in the present tense,
    # and nothing flagged it because no entry named it here — a card is only
    # covered once it has a row, so adding the row IS the fix.
    (
        "docs/model-card.md",
        (
            "and it does not reconcile",
            "no cairn line is engine-computed",
            "(25%) undistributed",
            # #492 brought Green Lion 2023-1's grade onto the matrix, so a
            # transcribed "exactly one" has been false since.
            "exactly one cell is validated",
        ),
    ),
)


def test_the_published_statements_carry_the_measured_result() -> None:
    """The cards state the grade, and no longer state what it disproved.

    Three documents predicted this reconciliation before it was run, and each
    said something the run falsified — the data card twice over (it deferred the
    cell to "#496's" and still said every extracted recipient resolved to no
    canonical ``RecipientType``, which #503 had already closed), the top-level
    README once ("no answer key is authored"). A reason that stopped being true
    tells this deal a story true only of its past, which is the #457/#471 failure
    this repo has now had to correct three times.

    Both directions are asserted, for the reason ``test_the_user_facing_no_tape_card``
    gives: a ban alone passes by deleting the paragraph, so the shortfall itself
    must appear in each card. That also makes the published figure re-derived
    rather than transcribed — it is the same constant the reconciliation above
    asserts, so a card quoting a stale number reds here.

    **The figure checked is the fold gap, and it is no longer the total.**
    The cards were written when the two were one number. #511 split them, #528
    rejoined them, and **#538 has parted them for good**: every published cent now
    reaches a step and is distributed, so ``REVENUE_SHORTFALL`` is zero while
    ``UNJOINED_REVENUE_ROWS_TOTAL`` — what the *join* had to recover, a property of
    the report and the cascade — is unchanged and is what the cards state.

    That is deliberate and it is why this test still checks the join figure rather
    than the total. Pointing the cards at the total would now have them publish
    ``0.00`` for a cascade with two wrong steps in it, which is precisely the
    false green ``REVENUE_SHORTFALL``'s own comment warns about. Restating the
    cards is #515's — it was outside this issue's scope, and editing the
    constant instead of the cards would hide drift rather than report it. #515
    has since done that restatement and added ``docs/model-card.md`` to the
    table above, which was the last committed card still stating the pre-#514
    result in the present tense with nothing to flag it.
    """
    repo_root = Path(__file__).resolve().parents[1]
    shortfall = f"{UNJOINED_REVENUE_ROWS_TOTAL:,.2f}"

    for relative_path, retracted_claims in RETRACTED_CLAIMS:
        prose = (repo_root / relative_path).read_text(encoding="utf-8")
        lowered = prose.lower()
        # `in` is evaluated into a bool first: asserting the operator directly
        # makes pytest print the whole document on failure, burying the claim.
        still_present = [claim for claim in retracted_claims if claim in lowered]
        assert still_present == [], (relative_path, still_present)
        # And it still states the result, so this cannot pass by deleting it.
        states_the_result = shortfall in prose
        assert states_the_result, (relative_path, shortfall)


#: The cards that must state the Interest cascade's engine/report split. A card
#: naming the grade without the split publishes "it reconciles" while hiding that
#: most of what reconciled was the report compared with itself (#496).
CARDS_STATING_THE_SPLIT: tuple[str, ...] = (
    "README.md",
    "docs/model-card.md",
    "src/loanwhiz/data/deals/answer_keys/README.md",
)


def _collapsed(text: str) -> str:
    """Prose with runs of whitespace flattened, so a wrapped line still matches."""
    return re.sub(r"\s+", " ", text)


def test_the_cards_state_the_split_as_a_figure_this_run_re_derives(
    recon: ReconciliationReport,
) -> None:
    """Every count the cards publish is regenerated here, never transcribed.

    #515's brief asks for the engine-computed / report-supplied split "as a
    figure, not an adjective", and a figure written into four documents is four
    places to drift. So the split is **derived from this run** and each card is
    required to contain the derived sentence: change the engine's classification
    and every card that still quotes the old number reds here, which is the
    #492 lesson (a converged surface whose docs quote the old answer claims
    worse) applied to a count rather than to a tally of cells.

    The direction matters as much as the figure. A card may say the cascade
    reconciles only while it also says how little of it was computed — 3 lines
    of 29 in the Interest cascade, 3 of 52 across both — because "it
    reconciles" and "the engine computes it" are different claims and this deal
    is the one that made the difference visible.
    """
    period = recon.periods[0]
    revenue, redemption = period.revenue, period.redemption
    every_step = [*revenue.steps, *redemption.steps]

    revenue_engine = [s for s in revenue.steps if s.source == "engine"]
    revenue_reported = [s for s in revenue.steps if s.source != "engine"]
    all_engine = [s for s in every_step if s.source == "engine"]
    all_reported = [s for s in every_step if s.source != "engine"]
    engine_money = sum(s.engine_amount for s in all_engine)
    vacuous = [
        s
        for s in every_step
        if abs(s.engine_amount) <= recon.tolerance_eur
        and abs(s.report_amount) <= recon.tolerance_eur
    ]

    # The split is a real split: neither side is empty, or the sentence below
    # would be true of a grade that computed nothing (#496) or of one with no
    # report-supplied lines left to warn about.
    assert revenue_engine and revenue_reported
    assert len(all_engine) + len(all_reported) == len(every_step)

    split = (
        f"{len(revenue_engine)} of th",
        f"{len(revenue.steps)} lines are engine-computed "
        f"and {len(revenue_reported)} are report-supplied",
    )
    for relative_path in CARDS_STATING_THE_SPLIT:
        prose = _collapsed(
            (Path(__file__).resolve().parents[1] / relative_path).read_text(
                encoding="utf-8"
            )
        )
        missing = [fragment for fragment in split if fragment not in prose]
        assert missing == [], (relative_path, missing)

    # The data card carries the cross-cascade form and the money behind it,
    # because that is where a reader meets what `validated` would mean here.
    data_card = _collapsed(
        (Path(__file__).resolve().parents[1] / "docs/data-card.md").read_text(
            encoding="utf-8"
        )
    )
    for fragment in (
        f"{len(every_step)} steps are compared and **{len(all_engine)} are "
        f"engine-computed; {len(all_reported)} are report-supplied**",
        f"{len(all_engine)} lines carrying EUR {engine_money:,.2f} of the "
        f"Interest cascade's EUR {revenue.available_funds:,.2f} pot",
        f"{len(vacuous)} of the {len(every_step)} compare EUR 0.00 with EUR 0.00",
    ):
        assert fragment in data_card, fragment

    # And the redemption cascade is named as proving nothing wherever it is
    # counted, since all of its steps are in that vacuous set.
    assert len(redemption.steps) == len([s for s in vacuous if s in redemption.steps])


def test_both_sides_of_the_comparison_fold_the_report_the_same_way(
    clo_model: DealModel, nvr_report: NotesCashReport, clo_series: DealStateSeries
) -> None:
    """The adapter and the reconciler must join the report identically (#514).

    They now call one fold, which is the point of the consolidation — but they
    reach it with **separately derived** label lists: the adapter reads the
    extracted model's steps, the reconciler reads the labels off the execution the
    fold produced. If those two lists ever diverge, the single shared function
    silently becomes two different joins again, and the engine is graded against a
    published figure it was never given. Nothing else asserts they agree.

    Left as its own test rather than folded into the grade above because it must
    keep holding for a deal whose grade nobody pins — it is a property of the
    seam, not of Cairn's numbers.
    """
    adapter = ReportAdapter.from_deal_model(clo_model)
    (period,) = nvr_report.periods

    for waterfall, adapter_steps, execution in (
        ("revenue", adapter.revenue_steps, clo_series.period_results[0].revenue_execution),
        (
            "redemption",
            adapter.redemption_steps,
            clo_series.period_results[0].redemption_execution,
        ),
    ):
        from_model = [str(step.get("priority", "")) for step in adapter_steps]
        from_execution = [step.priority for step in execution.steps]
        assert from_model == from_execution, waterfall

        rows = period.revenue_pop if waterfall == "revenue" else period.redemption_pop
        assert fold_report_pop(rows, from_model) == fold_report_pop(rows, from_execution)
