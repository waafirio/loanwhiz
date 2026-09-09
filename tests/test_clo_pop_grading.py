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
   EUR 838,738.61 undistributed** — 12% of the period's available revenue, and the
   sum of two separately-named mechanisms. The pot is the report's own stated
   figure and no step is starved (``total_shortfall`` is EUR 0.00). Before #514
   the remainder was EUR 2,014,490.53 and its largest part was a join failure;
   what is left is two engine refusals with two different owners.

   a. **EUR 644,398.50 is Class B's interest, which the engine refuses to
      compute.** Until #514 this line read "EUR 1,820,150.42 has no cascade step
      to go to at all": the report prints 62 rows against 29 top-level labels and
      20 of them joined nothing, because the fold handled only Green Lion's
      purely-numeric ``(b)(1..n)`` wrap. ``report_label_fold`` now joins the
      report's hierarchy — prefixed children, amountless headers, re-lettered
      children — so **every published row reaches a step** and EUR 1,175,751.92
      of that gap is simply distributed. What is left of it is Class B's
      ``(H)(i)``/``(H)(ii)`` money, which now has a step and is *compared* rather
      than ignored: the engine cannot evaluate it and the line fails by its full
      published value. The failure moved from the join to the engine, which is
      where it belongs. **The refusal's cause is neither this issue's nor
      #520's**: since #520 the deal seeds all eight classes and both Class B
      strips carry a real balance, but the cascade's ``class_b_notes_interest``
      resolves to a canonical ``class_b_interest`` whose need is looked up
      against a tranche named ``class_b`` — and Cairn has ``class_b_1`` and
      ``class_b_2``, never a ``class_b``. That is a recipient-to-tranche naming
      seam, distinct from the fold and from the truncation #520 removed.

   b. **EUR 194,340.11 is the day-count gap on the two lines the engine now
      computes for itself.** Since #511 the Class A and Class C interest steps
      are engine-computed rather than handed the report's figure, and since #512
      supplied the published applied rates both of them resolve a coupon and
      produce a number. Each is short by the same factor, for the same single
      reason, and neither is this issue's to fix — see item 3 and **#521**.

3. **Two lines of the cascade are now genuinely engine-computed, and neither
   ties — which is how you can tell they are computed.**
   Before #511 the membership test ran against the *raw* extracted recipient, so
   Cairn's ``class_a_notes_interest`` missed the set's ``class_a_interest`` and
   every step was classified ``report-supplied`` — its amount taken from the
   report under its own label and compared to itself. Resolving the spelling
   first makes the three note-interest steps engine-computed. With #512's
   published applied rates in place, Classes A and C each resolve a coupon, and
   the engine derives the need from the seed's own tranche size and that rate —
   ``balance × rate/100 / 360 × days`` — with **no report input**::

     Class A:  EUR 248,000,000 @ 5.008%  ->  3,104,960.00   published 3,277,457.78
     Class C:  EUR  23,100,000 @ 6.808%  ->    393,162.00   published   415,004.33

   **The disagreement is the proof.** While the engine echoed the report, a tie
   asserted nothing: a figure copied from the report matches the report by
   construction, and that circularity is the whole thing epic #510 exists to
   remove. Before this change Class A appeared to tie to EUR 3,277,457.78 to the
   cent — but only because the fold, lacking a published rate, fell back to a
   coupon back-solved from the very amount being checked. It was still the
   report agreeing with itself, one layer down. These two lines are now derived
   from the deal model alone, so they *can* disagree with the report, and here
   they do.

   **The residual has one cause, and it is not this issue's.** Both classes are
   short by exactly the same factor: ``waterfall_interpreter``'s
   ``WaterfallFunds.days_in_period`` defaults to 90 — the quarterly Act/360
   approximation — while Cairn's accrual period for this reporting date is 95
   days. At 95 days each figure reproduces its published counterpart **to the
   cent**, which ``test_the_residual_is_exactly_the_day_count_default`` asserts
   below so the handoff is measured rather than argued. Two lines with different
   balances and different coupons landing on the same 95 days is one shared
   cause, not a coincidence. Owned by **#521**.

   Class B is the third engine-claimed step and the only one that still refuses
   (``not_evaluable``): the deal's stack is spelled ``class_b_1``/``class_b_2``
   while the report path seeds a canonical ``class_b``, so no tranche attaches —
   #512's own recorded gotcha. It was already reconciling zero-against-zero (its
   money sits in the unjoined ``(H)(i)``/``(H)(ii)`` rows), so refusing costs
   nothing it was not already failing to place.

   ``steps_passed`` falls 29 → 27 and the undistributed total rises; both are
   the measurement improving, not the engine regressing.

3b. **The reconciliation now labels the three note-interest steps ``engine``.**
   ``reconciler._source_of`` was a second raw-membership test over the same set
   that #511 could not reach (``reconciler.py`` belonged to #512 and #514); #514
   routed both readers through one
   ``step_source_classifier.is_engine_computed``. ``engine_computed_passed`` still
   reads 0 — but for an honest reason now, not a labelling one: all three
   engine-computed lines genuinely disagree with the report. The label was hiding
   which failures were the engine's.

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
from pathlib import Path

import pytest

from loanwhiz.api.main import fold_report_series
from loanwhiz.config import DEAL_REGISTRY
from loanwhiz.extraction.assembler import DealModel
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

#: The report's stated available revenue for that period, and what the fold
#: actually distributes through the deal's extracted 29-step Interest cascade.
#: The difference is this issue's finding.
PUBLISHED_AVAILABLE_REVENUE = 7_255_062.35
ENGINE_DISTRIBUTED_REVENUE = 6_416_323.74

#: The published rows that joined **no** cascade step before #514, and what they
#: paid. Kept as a constant after the join was fixed for two reasons: it is the
#: figure ``docs/data-card.md``, ``README.md`` and the answer-key README still
#: state (restating them is #515's — ``docs/**`` is outside this issue's scope,
#: and editing the constant instead of the cards would hide the drift rather than
#: report it), and it is what
#: ``test_the_rows_that_joined_no_step_now_reach_their_steps`` re-derives from the
#: document to prove the join closed rather than that a number was retyped.
UNJOINED_REVENUE_ROWS_TOTAL = 1_820_150.42

#: The two engine-computed interest lines: what the engine derives from the deal
#: model, and what the report publishes. Since #512 supplied the applied rates
#: **both** classes compute; neither ties, and both miss by the same day-count
#: factor — see ``CAIRN_ACCRUAL_DAYS`` below and #521.
ENGINE_COMPUTED_CLASS_A_INTEREST = 3_104_960.00
PUBLISHED_CLASS_A_INTEREST = 3_277_457.78
ENGINE_COMPUTED_CLASS_C_INTEREST = 393_162.00
PUBLISHED_CLASS_C_INTEREST = 415_004.33

#: The inputs those two lines are computed from: the seed's tranche sizes and
#: #512's published applied rates. No report figure is among them — which is
#: what makes the lines independent, and the disagreement below meaningful.
CLASS_A_SIZE_EUR, CLASS_A_APPLIED_RATE_PCT = 248_000_000.00, 5.008
CLASS_C_SIZE_EUR, CLASS_C_APPLIED_RATE_PCT = 23_100_000.00, 6.808

#: ``waterfall_interpreter.WaterfallFunds.days_in_period`` defaults to 90 — the
#: quarterly Act/360 approximation — while Cairn's actual accrual period for
#: this reporting date is 95 days. That single default is the entire residual
#: between every figure below and its published counterpart. **Owned by #521;
#: deliberately not fixed here.**
INTERPRETER_DEFAULT_DAYS = 90
CAIRN_ACCRUAL_DAYS = 95

#: Class B's published interest, split across the two sub-lettered rows the
#: cascade's single ``(H)`` step now claims. Since #514 that step compares against
#: this figure instead of a folded EUR 0.00; it still cannot produce a number for
#: it, so the comparison fails by the whole amount.
CLASS_B_PUBLISHED_INTEREST = 386_773.50 + 257_625.00

#: Derived, never transcribed: a fix to either mechanism must move these too.
CLASS_A_DAY_COUNT_GAP = PUBLISHED_CLASS_A_INTEREST - ENGINE_COMPUTED_CLASS_A_INTEREST
CLASS_C_DAY_COUNT_GAP = PUBLISHED_CLASS_C_INTEREST - ENGINE_COMPUTED_CLASS_C_INTEREST
DAY_COUNT_SHORTFALL = CLASS_A_DAY_COUNT_GAP + CLASS_C_DAY_COUNT_GAP

#: The remainder's two mechanisms since #514 closed the join, named separately
#: because they have different owners and different fixes. Keeping one number for
#: both would let a fix to either look like a fix to the whole — the reading epic
#: #510 exists to prevent:
#:
#: - Class B's interest, which the engine has a step for and refuses to compute
#:   because its recipient resolves to a canonical ``class_b`` and the deal
#:   carries ``class_b_1``/``class_b_2`` — a naming seam, not a seeding gap;
#: - the day-count gap on the two lines that do compute (#521's).
REVENUE_SHORTFALL = CLASS_B_PUBLISHED_INTEREST + DAY_COUNT_SHORTFALL

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

#: The money #514's fold moved: the rows above, now placed on their parent steps.
#: Derived from the pair either side of it so a change to either reds here.
PLACED_BY_THE_FOLD = UNJOINED_REVENUE_ROWS_TOTAL - CLASS_B_PUBLISHED_INTEREST


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
# 2. The grade itself — the Interest cascade does not tie out.
# ---------------------------------------------------------------------------


def test_the_interest_cascade_does_not_reconcile(recon: ReconciliationReport) -> None:
    """The headline: EUR 838,738.61 of published revenue the engine never places.

    ``WaterfallReconciliation.passed`` requires both that every joined step agrees
    and that the distributed total ties to available funds, and both still fail —
    but the remainder is now entirely step-level. Since #514 every published row
    reaches a step, so nothing is missing from the comparison; what is left is
    three engine-computed lines that each produce a figure the report disagrees
    with (or, for Class B, no figure at all). The number got smaller because the
    join closed, not because the engine improved.
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
    # And no published row is missing from the comparison — the tie-out gap is
    # now a statement about the engine alone. A regression in the fold would
    # refill this list and quietly re-inflate the gap above (#514).
    assert revenue.unjoined_report_rows == []


def test_the_three_engine_claimed_steps_are_exactly_the_three_that_disagree(
    recon: ReconciliationReport,
) -> None:
    """26 of 29 steps match; the three that do not are the three the engine claims.

    The set equality is the assertion, not the count. Every step whose amount is
    taken from the report agrees with the report by construction, so the only
    steps that *can* disagree are the ones the engine derives for itself — and
    here all three of them do. Two distinct causes are in play and #510's whole
    discipline is not conflating them:

    - **Class B** has no figure at all: its recipient resolves to a canonical
      ``class_b`` and the deal's strips are ``class_b_1``/``class_b_2``, so the
      need is ``not_evaluable`` and the step fails by its full published
      EUR 644,398.50;
    - **Classes A and C** each produce a number and are each short by the
      interpreter's 90-day default against a 95-day accrual period (#521).

    The count fell 29 → 27 → 26 across #511 and #514, and every step of that is
    the measurement improving. Before #511 this file asserted 29 passes and every
    delta zero — 29 comparisons of the report against itself. #514 then turned
    Class B's vacuous 0.00-vs-0.00 pass into a real comparison it fails, which is
    the point: a step that *can* disagree is a step that was actually computed.
    """
    revenue = recon.periods[0].revenue
    assert len(revenue.steps) == 29
    assert revenue.steps_passed == 26

    disagreeing = [step for step in revenue.steps if step.delta != 0.0]
    assert [(s.priority, s.recipient) for s in disagreeing] == [
        ("(G)", "class_a_notes_interest"),
        ("(H)", "class_b_notes_interest"),
        ("(J)", "class_c_notes_interest"),
    ]
    # The disagreeing set IS the engine-claimed set — neither wider (a
    # report-supplied line drifting from its own override) nor narrower (an
    # engine line quietly echoing the report again).
    assert disagreeing == [s for s in revenue.steps if s.source == "engine"]
    class_a, class_b, class_c = disagreeing
    assert class_a.engine_amount == pytest.approx(
        ENGINE_COMPUTED_CLASS_A_INTEREST, abs=0.01
    )
    assert class_a.report_amount == pytest.approx(PUBLISHED_CLASS_A_INTEREST, abs=0.01)
    assert class_c.engine_amount == pytest.approx(
        ENGINE_COMPUTED_CLASS_C_INTEREST, abs=0.01
    )
    assert class_c.report_amount == pytest.approx(PUBLISHED_CLASS_C_INTEREST, abs=0.01)

    # A and C are not refusals. A EUR 0.00 engine amount on either would mean the
    # coupon stopped resolving — a different failure, with a different owner, that
    # would otherwise hide inside the same "step disagrees" count.
    assert class_a.engine_amount > 0.0
    assert class_c.engine_amount > 0.0
    # Class B is exactly that refusal, and it is kept distinguishable from them:
    # its whole published figure is the delta because the engine produced nothing.
    assert class_b.engine_amount == 0.0
    assert class_b.report_amount == pytest.approx(CLASS_B_PUBLISHED_INTEREST, abs=0.01)

    # The three deltas are the whole tie-out gap — so the two owners' shares add
    # up with nothing unexplained between them.
    assert sum(abs(s.delta) for s in disagreeing) == pytest.approx(
        REVENUE_SHORTFALL, abs=0.01
    )
    assert sum(abs(s.delta) for s in (class_a, class_c)) == pytest.approx(
        DAY_COUNT_SHORTFALL, abs=0.01
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

    Since #514 the remainder has two sources and **neither is a join failure**:
    Class B's outright refusal — its tranche is never attached on the report path
    (#512's recorded gotcha) — and the day-count gap on the two lines that do
    compute (#521). That refusal must stay visible here: #493's layered refusal
    reaching the CLO. The wrong fix is now *more* tempting than it was, because
    the pot is right, the join is complete, and one step simply will not answer;
    sweeping its money onward would still pay Class B's coupon to the wrong party.
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
    # And the refusal is the larger half of what is left, which is why it is named
    # rather than folded into one number with the day count.
    assert CLASS_B_PUBLISHED_INTEREST > DAY_COUNT_SHORTFALL
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
    step list — so the *only* thing that can make them reach a step is the join,
    which is what this asserts.

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

    # Changed: every published cent now reaches a step. Asserted as a
    # conservation property rather than a per-label figure, so it is the join
    # being complete that makes it true — not a list that happens to match.
    assert revenue.unjoined_report_rows == []
    assert sum(step.report_amount for step in revenue.steps) == pytest.approx(
        revenue.report_total, abs=0.01
    )

    # And the money actually moved: the four report-supplied parents of those
    # rows now distribute it. Class B's ``(H)`` is the fifth parent and is absent
    # here on purpose — it is engine-claimed and pays nothing, which is why
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
    single ``(H)`` step found no ``(H)`` row, took EUR 0.00, and was compared
    against a folded EUR 0.00 — a green cell asserting nothing about the largest
    single figure the engine failed to place, and a grade reporting only
    ``steps_passed`` would have called it correct.

    The fold now gives the step its children's total, so the comparison is real
    and the engine loses it: the need is ``not_evaluable`` and the step fails by
    the whole EUR 644,398.50. **The step going red is this issue working.**
    Nothing about the engine changed here — only whether anyone was looking.

    Since #520 the cause is no longer a missing tranche. Both Class B strips are
    seeded and carry real balances; the cascade's ``class_b_notes_interest``
    resolves to a canonical ``class_b_interest``, whose need is looked up against
    a tranche named ``class_b``, and the deal has ``class_b_1``/``class_b_2``.
    Asserted below rather than described, so this reds if that seam closes.

    **Expected to change when it does**: the engine amount becomes a real coupon
    and the delta shrinks to whatever the day count leaves, as A and C show.
    """
    (class_b,) = [s for s in recon.periods[0].revenue.steps if s.priority == "(H)"]
    assert class_b.recipient == "class_b_notes_interest"
    assert class_b.engine_amount == 0.0
    assert class_b.report_amount == pytest.approx(CLASS_B_PUBLISHED_INTEREST, abs=0.01)
    assert class_b.passed is False
    assert class_b.source == "engine"

    # The refusal is a naming seam, not a seeding gap: since #520 both strips are
    # seeded with real balances, and no tranche is spelled the canonical name the
    # recipient resolves to. Pinned so the cause cannot be misattributed again.
    seeded = {t.name: t for t in clo_series.states[0].tranches}
    assert {"class_b_1", "class_b_2"} <= set(seeded)
    assert seeded["class_b_1"].balance > 0.0 and seeded["class_b_2"].balance > 0.0
    assert "class_b" not in seeded
    assert _canonical_recipient("class_b_notes_interest") is RecipientType.class_b_interest

    # Read the money off the document, so the figure the step is graded against
    # is the report's own and not one this module carries.
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


def test_class_a_interest_is_computed_from_the_deal_model_and_does_not_yet_tie(
    clo_series: DealStateSeries, recon: ReconciliationReport
) -> None:
    """The independent line: EUR 3,104,960.00 derived, not copied — and it disagrees.

    This is what #511 bought, and the **disagreement is the receipt.**
    ``ENGINE_COMPUTED_RECIPIENTS`` names the recipients the interpreter derives
    from the deal model with no report input — the independent half of any
    reconciliation. It is spelled in canonical vocabulary (``class_a_interest``)
    while Cairn's cascade carries the document's own spelling
    (``class_a_notes_interest``), and the classifier used to test the raw string,
    so the membership test missed every step including the three classes the set
    does name. Every step was then handed the report's own figure as its "need"
    and agreed with it by construction.

    **This test used to assert a tie, and the tie was the bug.** On #511's own
    branch no published rate existed, so the fold fell back to an
    amount-recovered coupon — back-solved from the very figure being checked —
    and reproduced EUR 3,277,457.78 to the cent. That agreement was the report
    agreeing with itself one layer down, which is exactly the circularity epic
    #510 exists to delete. #512 then supplied the genuinely published 5.008%,
    the fallback stopped firing, and the agreement went with it. The tie
    disappearing is this change working, not breaking.

    What the engine computes now comes from the seed's own tranche size and that
    published rate: ``248,000,000 × 5.008/100 / 360 × 90``. It falls short of the
    published EUR 3,277,457.78 for exactly one reason — ``days_in_period``
    defaults to 90 while Cairn accrues over 95 days.
    **That default is #521's, not this issue's**, and
    ``test_the_residual_is_exactly_the_day_count_default`` below proves it is the
    whole gap. **The expected value here changes when #521 lands:** it becomes
    ``PUBLISHED_CLASS_A_INTEREST``, the delta becomes zero, and this test is
    renamed back to ``..._and_ties``.

    Asserted through the fold (the need the interpreter actually produced) *and*
    the reconciliation (that need against the published figure), because only the
    pair distinguishes "computed" from "copied".
    """
    execution = clo_series.period_results[0].revenue_execution
    (class_a,) = [s for s in execution.steps if s.recipient == "class_a_notes_interest"]
    assert class_a.not_evaluable is False
    assert class_a.need == pytest.approx(ENGINE_COMPUTED_CLASS_A_INTEREST, abs=0.01)

    # Derived from the deal model's own two inputs, so this reds if the need ever
    # starts arriving from somewhere other than size × published rate × day count
    # — the amount-recovered fallback creeping back would be caught right here.
    assert class_a.need == pytest.approx(
        CLASS_A_SIZE_EUR * CLASS_A_APPLIED_RATE_PCT / 100 / 360 * INTERPRETER_DEFAULT_DAYS,
        abs=0.01,
    )

    (reconciled,) = [
        s for s in recon.periods[0].revenue.steps if s.recipient == "class_a_notes_interest"
    ]
    assert reconciled.engine_amount == pytest.approx(
        ENGINE_COMPUTED_CLASS_A_INTEREST, abs=0.01
    )
    # The report's figure is emphatically *not* the engine's. Before #511 these
    # two assertions read the same constant; that they now differ is the finding.
    assert reconciled.report_amount == pytest.approx(PUBLISHED_CLASS_A_INTEREST, abs=0.01)
    assert reconciled.engine_amount != reconciled.report_amount
    assert reconciled.delta == pytest.approx(-CLASS_A_DAY_COUNT_GAP, abs=0.01)
    assert reconciled.passed is False

    # The set itself gained no CLO spelling — the fix is at the comparison, which
    # is what keeps one vocabulary in the table (#503).
    assert "class_a_notes_interest" not in ENGINE_COMPUTED_RECIPIENTS
    assert "class_a_interest" in ENGINE_COMPUTED_RECIPIENTS


def test_the_residual_is_exactly_the_day_count_default() -> None:
    """Both computed lines tie to the cent at 95 days — so #521 is the whole gap.

    A handoff is only worth making if it is measured. Class A and Class C have
    different balances (EUR 248,000,000 / EUR 23,100,000) and different coupons
    (5.008% / 6.808%), so the *only* input they share is the day count. Each
    recomputed at Cairn's actual 95-day accrual period reproduces its published
    figure exactly, with nothing else about either line moving. Two independent
    lines landing on the same 95 days is one shared cause, not a coincidence.

    That turns the residual into a single-cause defect owned elsewhere rather
    than a vague "close enough". When #521 replaces the 90-day default with the
    deal's real accrual period, both deltas go to zero and ``steps_passed``
    reaches 29 with nothing in #511's classifier changing.

    **If this test ever reds, the gap is no longer only the day count** and the
    #521 handoff — in this module's docstring and in every ``expected to change``
    note above — needs re-stating before anything else is believed.
    """
    for size, rate, computed, published in (
        (CLASS_A_SIZE_EUR, CLASS_A_APPLIED_RATE_PCT,
         ENGINE_COMPUTED_CLASS_A_INTEREST, PUBLISHED_CLASS_A_INTEREST),
        (CLASS_C_SIZE_EUR, CLASS_C_APPLIED_RATE_PCT,
         ENGINE_COMPUTED_CLASS_C_INTEREST, PUBLISHED_CLASS_C_INTEREST),
    ):
        per_day_act360 = size * rate / 100 / 360
        assert per_day_act360 * INTERPRETER_DEFAULT_DAYS == pytest.approx(computed, abs=0.01)
        assert per_day_act360 * CAIRN_ACCRUAL_DAYS == pytest.approx(published, abs=0.01)

    # Read the default off the interpreter itself rather than restating it, so
    # this reds the moment #521 changes it — which is precisely the signal that
    # every expected value pinned above has gone stale.
    assert WaterfallFunds.model_fields["days_in_period"].default == INTERPRETER_DEFAULT_DAYS


def test_the_reconciliation_labels_the_computed_steps_engine(
    recon: ReconciliationReport,
) -> None:
    """#511's pinned flip, landed — and what it reveals is that zero was honest.

    ``reconciler._source_of`` used to be a *second* raw-membership test over
    ``ENGINE_COMPUTED_RECIPIENTS``, so the reconciliation called every step
    ``report-supplied`` — including the Class A line #511 proved was computed from
    the deal model. #511 could not reach it (``reconciler.py`` belonged to #512
    and #514) and left this assertion red-on-landing by design. #514 routed both
    readers through one ``step_source_classifier.is_engine_computed``.

    ``engine_computed_passed`` is **still 0**, and that is the finding rather than
    the bug: all three engine-claimed lines genuinely disagree with the report. The
    old label made a real zero and a bookkeeping zero look the same, which is
    precisely why it was worth fixing — the number did not move, its meaning did.

    **Expected to change when #521 lands**: Classes A and C tie at a 95-day accrual
    period and revenue's count becomes 2.
    """
    revenue, redemption = recon.periods[0].revenue, recon.periods[0].redemption

    assert {step.source for step in revenue.steps} == {"engine", "report-supplied"}
    assert [s.recipient for s in revenue.steps if s.source == "engine"] == [
        "class_a_notes_interest",
        "class_b_notes_interest",
        "class_c_notes_interest",
    ]
    assert revenue.engine_computed_passed == 0
    assert not any(step.passed for step in revenue.steps if step.source == "engine")

    # The Principal cascade names no engine-computed recipient at all, so its 0
    # is a different fact from revenue's and must not be asserted the same way.
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
    # The Principal report is sub-lettered too — its step (A) pays "in the order
    # of (A) to (I) of the Interest Priority", so it prints that sub-cascade's
    # own re-lettered rows inside one step. They are all EUR 0.00, so the join
    # moves no money here; asserting it anyway is what keeps this side's zero a
    # measured zero rather than an unexamined one (#494).
    assert redemption.unjoined_report_rows == []


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
    assert revenue.engine_computed_passed == 0
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

    **The figure checked is the old fold gap, and since #514 the cards overstate
    the deal's remainder.** They were written when the gap and the total were one
    number. #511 then made two interest lines engine-computed, and #514 closed the
    join, so the measured remainder is ``REVENUE_SHORTFALL`` =
    ``CLASS_B_PUBLISHED_INTEREST + DAY_COUNT_SHORTFALL`` = EUR 838,738.61 — while
    the cards still say EUR 1,820,150.42 and still describe 20 rows joining no
    step. Both statements were true when written and neither is now.

    Restating them is **#515**'s ("Re-grade the CLO and record the verdict"), and
    this test deliberately does not do it: ``docs/**`` is outside #514's declared
    paths, #515 is a live sibling that would race an edit here, and moving the
    constant to match the cards would hide the drift rather than report it. What
    this test still guarantees is that the cards cannot quietly go *further* out
    of step — the constant they are checked against is the same one the
    reconciliation above re-derives.
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
