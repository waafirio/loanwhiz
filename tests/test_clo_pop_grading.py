"""Grade the engine against Cairn's committed Priority of Payments (#496, epic #491).

The epic's terminal question: fold Cairn CLO XVII's *own* extracted cascades and
reconcile them against the deal's *own* published Priorities of Payments, then
record whatever comes back. **It does not reconcile**, and this module is the
durable record of exactly how — so a later fix has a specific assertion to flip
rather than a paragraph to re-derive.

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

2. **Reconciled against that one document directly, the Interest cascade leaves
   EUR 644,398.50 undistributed** — 9% of the period's available revenue. It was
   the sum of two separately-named mechanisms; #528 closed the second and #514
   closed the first, and what remains is a single step the engine cannot compute.
   The pot is the report's own stated figure and no step is starved
   (``total_shortfall`` is EUR 0.00).

   a. **The join is closed; what is left of it is Class B's.** The report prints
      62 rows and the extracted cascade carries 29 top-level labels, so 20 report
      rows matched none of them and the eight carrying money (``(A)(i)``,
      ``(A)(ii)``, ``(H)(i)``, ``(H)(ii)``, ``(CC)(1)(a)``, and two the report
      re-letters bare ``(a)``) accounted for EUR 1,820,150.42 to the cent.
      ``report_label_fold`` now joins the report's hierarchy — prefixed children,
      amountless headers, re-lettered children — so **every published row reaches
      a step** and EUR 1,175,751.92 of that is distributed. The remainder is
      Class B's ``(H)(i)``/``(H)(ii)`` money, which now has a step and is
      *compared* rather than ignored: the engine cannot evaluate it, so the line
      fails by its full published value. The failure moved from the join to the
      engine, which is where it belongs. **Its cause is neither #514's nor
      #520's**: since #520 both Class B strips are seeded with real balances, but
      ``class_b_notes_interest`` resolves to a canonical ``class_b_interest``
      whose need is looked up against a tranche named ``class_b``, and Cairn has
      ``class_b_1`` and ``class_b_2`` — a recipient-to-tranche naming seam.

   b. **The EUR 194,340.11 day-count gap is closed.** Since #511 the Class A and
      Class C interest steps are engine-computed rather than handed the report's
      figure; #512 supplied the published applied rates so both resolve a coupon;
      and **#528** supplied the accrual period, which was the single factor both
      were short by. See item 3.

3. **Two lines of the cascade are genuinely engine-computed, and both now tie —
   with every input sourced from a document rather than from the answer.**
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

   Class B is the third engine-claimed step and the only one that still refuses
   (``not_evaluable``): the deal's stack is spelled ``class_b_1``/``class_b_2``
   while the report path seeds a canonical ``class_b``, so no tranche attaches —
   #512's own recorded gotcha. It was already reconciling zero-against-zero (its
   money sits in the unjoined ``(H)(i)``/``(H)(ii)`` rows), so refusing costs
   nothing it was not already failing to place.

   ``steps_passed`` fell 29 → 27 at #511 as the two lines started computing, and
   returns to 29 at #528 as they start agreeing. Neither move is the engine
   regressing or the grade being relaxed: the 29 before #511 were comparisons of
   the report against itself, and the 29 now are comparisons against an
   independently derived half.

3b. **The reconciliation still *labels* every step ``report-supplied``.**
   ``reconciler._source_of`` is a second raw-membership test over the same set
   and #511 did not touch it (``reconciler.py`` belongs to #512 and #514), so
   ``engine_computed_passed`` reads 0 while the fold genuinely computed Class A.
   Pinned below as a specific assertion for whichever of those children flips it.

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
#: cascade's single ``(H)`` step claims since #514. It is the whole of what the
#: engine still fails to place: the step compares against this figure and cannot
#: produce one, so the comparison fails by the entire amount.
CLASS_B_PUBLISHED_INTEREST = 386_773.50 + 257_625.00

#: The money #514's fold moved onto its parent steps — the rows above, less Class
#: B's share, which has a step but no figure. Derived from the pair either side of
#: it so a change to either reds rather than silently re-balancing.
PLACED_BY_THE_FOLD = UNJOINED_REVENUE_ROWS_TOTAL - CLASS_B_PUBLISHED_INTEREST

#: What the report publishes for the two lines the engine computes for itself.
PUBLISHED_CLASS_A_INTEREST = 3_277_457.78
PUBLISHED_CLASS_C_INTEREST = 415_004.33

#: The inputs those two lines are computed from: the seed's tranche sizes and
#: #512's published applied rates. No report figure is among them — which is what
#: makes the lines independent, and their agreement below meaningful.
CLASS_A_SIZE_EUR, CLASS_A_APPLIED_RATE_PCT = 248_000_000.00, 5.008
CLASS_C_SIZE_EUR, CLASS_C_APPLIED_RATE_PCT = 23_100_000.00, 6.808

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
CAIRN_ACCRUAL_DAYS = accrual_period_days(
    _CAIRN_SCHEDULE, payment_date_on_or_after(_CAIRN_SCHEDULE, NVR_REPORTING_DATE)
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

#: Derived, never transcribed: a regression in either mechanism moves these.
#: Both day-count gaps are **zero** since #528 — that is the finding, and writing
#: them as differences rather than as ``0.0`` is what keeps them able to reopen.
CLASS_A_DAY_COUNT_GAP = PUBLISHED_CLASS_A_INTEREST - ENGINE_COMPUTED_CLASS_A_INTEREST
CLASS_C_DAY_COUNT_GAP = PUBLISHED_CLASS_C_INTEREST - ENGINE_COMPUTED_CLASS_C_INTEREST
DAY_COUNT_SHORTFALL = CLASS_A_DAY_COUNT_GAP + CLASS_C_DAY_COUNT_GAP

#: What the cascade still fails to place, and since #514 it has **one** mechanism
#: rather than two. The unjoined rows are joined now, so the only published money
#: the engine does not distribute is Class B's — its step exists, its comparison
#: is real, and it produces no figure. The day-count term stays in the sum rather
#: than being dropped: it is zero since #528, and writing it as a term is what
#: lets it reopen if that regresses instead of silently re-balancing this total.
REVENUE_SHORTFALL = CLASS_B_PUBLISHED_INTEREST + DAY_COUNT_SHORTFALL

#: What the fold actually distributes through the 29-step Interest cascade —
#: stated as the published pot less what is still unplaced, so it moves with the
#: mechanisms above instead of standing as a number someone must maintain.
ENGINE_DISTRIBUTED_REVENUE = PUBLISHED_AVAILABLE_REVENUE - REVENUE_SHORTFALL

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
    the tally is 0-of-1 rather than 3-of-4.
    """
    via_key = reconcile_against_answer_key(clo_series, clo_key)

    graded_dates = {p.reporting_date for p in via_key.periods}
    skipped_dates = {sp.reporting_date for sp in via_key.skipped_periods}
    assert graded_dates == {"2025-01-08"}
    assert skipped_dates == {"2024-12-16", "2025-02-18", "2025-03-18"}
    assert not graded_dates & skipped_dates

    # The counts are over the graded set alone — never over the key's periods.
    assert (via_key.periods_checked, via_key.periods_passed) == (1, 0)
    assert via_key.periods_skipped == 3
    assert via_key.passed is False

    # Each skip names the source document as the reason, not the engine or the key.
    for skipped in via_key.skipped_periods:
        assert "publishes no Priority of Payments" in skipped.reason

    # And the human summary says so too: "0/1 periods reconciled" beside three
    # unnamed absences would read as a complete grade of a one-period deal.
    summary = via_key.summary()
    assert "0/1 periods" in summary
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
# 2. The grade itself — the Interest cascade still does not tie out, on one gap.
# ---------------------------------------------------------------------------


def test_the_interest_cascade_does_not_reconcile(recon: ReconciliationReport) -> None:
    """The headline: EUR 644,398.50 of published revenue the engine never places.

    ``WaterfallReconciliation.passed`` requires both that every joined step agrees
    and that the distributed total ties to available funds. #511 broke **both**
    gates; #528 restored the step-level one by supplying the accrual period, and
    #514 closed the join. What is left is a single step-level failure — Class B's
    — and it is the tie-out gap too, because that step distributes nothing.

    One named mechanism, and for the first time the two gates fail for the *same*
    reason rather than for two that happened to add up.
    """
    assert recon.passed is False
    assert recon.periods_passed == 0
    assert recon.periods_checked == 1

    revenue = recon.periods[0].revenue
    assert revenue.passed is False
    assert revenue.available_funds == pytest.approx(PUBLISHED_AVAILABLE_REVENUE, abs=0.01)
    assert revenue.report_total == pytest.approx(PUBLISHED_AVAILABLE_REVENUE, abs=0.01)
    assert revenue.engine_total == pytest.approx(ENGINE_DISTRIBUTED_REVENUE, abs=0.01)
    assert revenue.unapplied_rounding == 0.0
    assert revenue.report_total - revenue.engine_total == pytest.approx(
        REVENUE_SHORTFALL, abs=0.01
    )
    # No published row is missing from the comparison — the gap is a statement
    # about the engine alone. A regression in the fold would refill this list and
    # quietly re-inflate the figure above (#514).
    assert revenue.unjoined_report_rows == []


def test_every_step_agrees_and_the_gap_is_only_the_unjoined_rows(
    recon: ReconciliationReport,
) -> None:
    """28 of 29 steps match, and the one that does not is the one still owed.

    Stated as its own assertion because several distinct failures were in play
    and #510's whole discipline is not conflating them:

    - **the join** — EUR 1,820,150.42 of published rows matched no step (#514).
      **Closed**: every row now reaches one, and EUR 1,175,751.92 of that is
      distributed. What remains of it is Class B's share, which the step compares
      against and cannot pay;
    - **two step-level deltas** — Class A and Class C interest, which #511 made
      engine-computed and #512 gave resolvable published coupons. Both were short
      by the interpreter's 90-day default against the deal's real accrual period.
      **#528 closed those**, and this test is where that shows.

    **This agreement is not the circular one #511 deleted.** The tie it asserted
    before #511 was the report echoing itself: with no published rate wired, the
    fold back-solved a coupon from the very figure being checked. The tie here is
    between two independently sourced halves — the seed's tranche size, #512's
    published applied rate, and a day count measured between two Payment Dates
    the prospectus states — against the report's published figure. Each half can
    move without the other, so the agreement is a result rather than an identity.

    ``steps_passed`` reaches 28 rather than 29, and the missing one is not a
    regression: #514 turned Class B's vacuous 0.00-vs-0.00 pass into a real
    comparison, which the engine loses. A step that *can* fail is worth more than
    one that passed by never being compared.
    """
    revenue = recon.periods[0].revenue
    assert len(revenue.steps) == 29
    assert revenue.steps_passed == 28
    assert [(s.priority, s.recipient) for s in revenue.steps if abs(s.delta) > 0.01] == [
        ("(H)", "class_b_notes_interest"),
    ]

    # The two engine-computed lines specifically, named rather than left to the
    # aggregate: a step that agreed because it stopped computing (a EUR 0.00
    # engine amount, the coupon no longer resolving) would otherwise be
    # indistinguishable from one that agreed because it computed correctly.
    by_step = {(s.priority, s.recipient): s for s in revenue.steps}
    class_a = by_step[("(G)", "class_a_notes_interest")]
    class_c = by_step[("(J)", "class_c_notes_interest")]
    assert class_a.engine_amount == pytest.approx(
        ENGINE_COMPUTED_CLASS_A_INTEREST, abs=0.01
    )
    assert class_a.report_amount == pytest.approx(PUBLISHED_CLASS_A_INTEREST, abs=0.01)
    assert class_c.engine_amount == pytest.approx(
        ENGINE_COMPUTED_CLASS_C_INTEREST, abs=0.01
    )
    assert class_c.report_amount == pytest.approx(PUBLISHED_CLASS_C_INTEREST, abs=0.01)
    assert class_a.engine_amount > 0.0
    assert class_c.engine_amount > 0.0
    assert abs(DAY_COUNT_SHORTFALL) < 0.01

    # The waterfall still fails, and now for one reason rather than three.
    assert revenue.passed is False
    assert revenue.report_total - revenue.engine_total == pytest.approx(
        CLASS_B_PUBLISHED_INTEREST, abs=0.01
    )


def test_the_money_is_undistributed_not_underfunded(
    clo_series: DealStateSeries, recon: ReconciliationReport
) -> None:
    """The pot is right; the cascade is not short of funds — it is short of answers.

    Read off the fold directly rather than through the reconciliation, because
    the two readings answer different questions and only this one distinguishes
    the failure modes. The first step sees the report's full stated available
    revenue, nothing is gated, and ``total_shortfall`` is EUR 0.00 — so no step
    that asked for money was denied it.

    That distinction is what stops the obvious wrong fix. A cascade whose steps
    were each short would be an amount problem, and sweeping the remainder into
    a residual step would look like a repair; here the remainder belongs to named
    recipients — some the cascade has no line for, one whose line refuses to
    guess — so a residual sweep would pay it to the wrong party and turn a
    visible failure into a silent one. #496 named that fix and rejected it.

    Since #528 the remainder has one source again: the unjoined rows. The
    day-count gap #511 exposed on the two engine-computed lines is closed, so
    nothing here is an arithmetic shortfall. A single step still refuses
    outright — Class B, whose
    tranche the report path never attaches (#512's recorded gotcha) — and that
    refusal must stay visible here: #493's layered refusal reaching the CLO.
    """
    execution = clo_series.period_results[0].revenue_execution

    assert execution.steps[0].amount_available == pytest.approx(
        PUBLISHED_AVAILABLE_REVENUE, abs=0.01
    )
    assert execution.total_distributed == pytest.approx(ENGINE_DISTRIBUTED_REVENUE, abs=0.01)
    assert execution.remaining == pytest.approx(REVENUE_SHORTFALL, abs=0.01)
    # Nothing was starved and nothing was suppressed — the two other ways a
    # cascade under-pays, each of which would want a different fix.
    assert execution.total_shortfall == 0.0
    assert not [step for step in execution.steps if step.gated]

    # Only Class B still refuses. Classes A and C — the other two engine-claimed
    # steps — are absent from this list because they computed, which is #511's
    # result plus #512's rates in one assertion. Their disagreement shows up as a
    # delta (asserted above), not as an absence.
    assert [step.recipient for step in execution.steps if step.not_evaluable] == [
        "class_b_notes_interest",
    ]
    # And the reconciliation's shortfall is that same remainder, not a second
    # number that happens to be close.
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
    Class B's share, which the engine has a step for but no figure for.
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
    # on purpose — it is engine-claimed and pays nothing, which is why
    # PLACED_BY_THE_FOLD is the recovered total less its share.
    recovered = sum(
        step.engine_amount
        for step in revenue.steps
        if step.priority in {"(A)", "(E)", "(X)", "(CC)"}
    )
    assert recovered == pytest.approx(PLACED_BY_THE_FOLD, abs=0.01)


def test_class_b_interest_is_now_compared_and_fails(
    recon: ReconciliationReport,
    nvr_report: NotesCashReport,
    clo_series: DealStateSeries,
) -> None:
    """The sharpest edge of the join gap, closed: a real payment now really graded.

    Before #514 this was a *passing* step and the pass meant nothing. The report
    pays Class B EUR 644,398.50 across ``(H)(i)`` and ``(H)(ii)``; the cascade's
    single ``(H)`` step found no ``(H)`` row, took EUR 0.00 and was compared
    against a folded EUR 0.00 — a green cell asserting nothing about the largest
    single figure the engine failed to place, and a grade reporting only
    ``steps_passed`` would have called it correct.

    The fold now gives the step its children's total, so the comparison is real
    and the engine loses it. **The step going red is this issue working.** Nothing
    about the engine changed here — only whether anyone was looking.

    The refusal's cause is neither #514's nor #520's. Since #520 the deal seeds
    all eight classes and both Class B strips carry a real balance, so it is no
    longer a missing tranche: the cascade's ``class_b_notes_interest`` resolves to
    a canonical ``class_b_interest`` whose need is looked up against a tranche
    named ``class_b``, and Cairn has ``class_b_1`` and ``class_b_2``. That
    recipient-to-tranche naming seam is asserted below rather than described, so
    this reds when it closes — at which point the engine amount becomes a real
    coupon and this deal's Interest cascade has nothing left unplaced.
    """
    (class_b,) = [s for s in recon.periods[0].revenue.steps if s.priority == "(H)"]
    assert class_b.recipient == "class_b_notes_interest"
    assert class_b.engine_amount == 0.0
    assert class_b.report_amount == pytest.approx(CLASS_B_PUBLISHED_INTEREST, abs=0.01)
    assert class_b.passed is False
    assert class_b.source == "engine"

    # A naming seam, not a seeding gap: both strips are seeded with real balances
    # and no tranche is spelled the canonical name the recipient resolves to.
    # Pinned so the cause cannot be misattributed to #520's truncation again.
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

    **This is the first non-zero ``engine_computed_passed`` this deal has had.**
    Two of its three engine-claimed steps now reconcile to the cent against the
    published report from the deal model alone — Class A and Class C, each from
    the seed's own tranche size, #512's published applied rate and #528's
    measured accrual period, with no report figure among the inputs. Class B is
    the third and refuses, which is why the count is 2 and not 3.

    That number is the epic's actual claim: before #511 it was 0 because nothing
    was computed, and after #511 it was still 0 because nothing was *labelled*
    computed. Only now does it mean what it says.
    """
    revenue, redemption = recon.periods[0].revenue, recon.periods[0].redemption

    assert {step.source for step in revenue.steps} == {"engine", "report-supplied"}
    assert [s.recipient for s in revenue.steps if s.source == "engine"] == [
        "class_a_notes_interest",
        "class_b_notes_interest",
        "class_c_notes_interest",
    ]
    assert revenue.engine_computed_passed == 2
    assert [
        s.recipient for s in revenue.steps if s.source == "engine" and not s.passed
    ] == ["class_b_notes_interest"]

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
    """Narrowing the fold back to three classes changes nothing about the grade.

    The direction of this test inverted at #520. It used to *widen* off a
    Green-Lion-shaped ``DEFAULT_TRANCHE_CLASSES`` default; that default is now the
    deal's own eight classes, so the *narrowing* is what has to be pinned. The
    property protected is unchanged.

    #496's original reason — "no step is engine-computed, so no tranche balance
    reaches any amount" — stopped being true at #511. The reason is now narrower
    but still sound: the engine-computed recipients (Classes A, B, C interest) are
    precisely the three the old default seeded, and the five classes the deal's
    own list adds (D, E, F, the second Class B strip, and the subordinated notes)
    name no recipient in ``ENGINE_COMPUTED_RECIPIENTS``. So the extra balances
    still reach no need calculator and the grade is identical either way —
    asserted, not assumed.

    It keeps its original job: it reds if a *further* change makes a D-through-F
    step engine-computed without revisiting the seeding.
    """
    narrowed_adapter = ReportAdapter.from_deal_model(
        clo_model, tranche_classes=DEFAULT_TRANCHE_CLASSES
    )
    series = fold_report_series(clo_model, nvr_report, narrowed_adapter)
    narrowed = reconcile_series(series, nvr_report, deal_name=CLO_DEAL_NAME, tolerance=0.01)

    revenue = narrowed.periods[0].revenue
    assert narrowed.passed is False
    assert revenue.engine_total == pytest.approx(ENGINE_DISTRIBUTED_REVENUE, abs=0.01)
    assert revenue.engine_computed_passed == 2
    # The fold is a property of the report and the cascade, not of how many
    # classes the adapter seeds, so narrowing cannot change what got placed.
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

    **The figure checked is the fold gap, and since #528 it is the total again.**
    The cards were written when the two were the same number. #511 split them: the
    engine began computing two interest lines for itself and fell short on both,
    so the remainder became ``UNJOINED_REVENUE_ROWS_TOTAL + DAY_COUNT_SHORTFALL``
    while the cards stated only the first. #528 supplied the accrual period those
    lines were short by, ``DAY_COUNT_SHORTFALL`` went to zero, and the two
    quantities coincide once more — so the cards' figure is the whole gap rather
    than an understatement of it. ``REVENUE_SHORTFALL`` is still written as the
    sum, not collapsed, because that is what lets the day-count mechanism reopen
    visibly if it ever regresses.
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
