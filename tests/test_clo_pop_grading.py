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
   EUR 2,235,154.75 undistributed** — 31% of the period's available revenue, and
   the sum of two separately-named mechanisms. The pot is the report's own stated
   figure and no step is starved (``total_shortfall`` is EUR 0.00).

   a. **EUR 1,820,150.42 has no cascade step to go to at all.** The report prints
      62 rows; the extracted cascade carries 29 top-level labels; 20 report rows
      are joined by no step, and the eight of those that carry money (``(A)(i)``,
      ``(A)(ii)``, ``(H)(i)``, ``(H)(ii)``, ``(CC)(1)(a)``, and two the report
      re-letters bare ``(a)``) account for it to the cent.
      ``reconciler._fold_report_revenue_steps`` folds only Green Lion's
      purely-numeric ``(b)(1..n)`` wrap artefact, which is not this report's
      shape. Owned by #514.

   b. **EUR 415,004.33 is a step the engine now claims and cannot compute.**
      Since #511 the Class C interest step is engine-computed rather than handed
      the report's figure, and its coupon does not resolve, so it refuses
      (``not_evaluable``) and distributes EUR 0.00 against a published
      EUR 415,004.33. This is the #493 layered refusal working: the engine says
      "I cannot answer" where it used to say "the report's answer is mine".
      Owned by #512, which wires the published applied rates.

3. **One line of the cascade is now genuinely engine-computed, and it ties.**
   Before #511 the membership test ran against the *raw* extracted recipient, so
   Cairn's ``class_a_notes_interest`` missed the set's ``class_a_interest`` and
   every step was classified ``report-supplied`` — its amount taken from the
   report under its own label and compared to itself. Resolving the spelling
   first makes the three note-interest steps engine-computed; Class A's coupon
   resolves, and the engine derives EUR 3,277,457.78 from balance and rate with
   **no report input**, matching the published figure to the cent. That is the
   first independently-computed line this deal has ever had.

   The other two are the honest cost of the same change: Class B and Class C now
   refuse rather than echo. Class B was already reconciling zero-against-zero
   (its money is in the unjoined ``(H)(i)``/``(H)(ii)`` rows), so only Class C
   turns a vacuous pass into a visible EUR 415,004.33 disagreement — which is
   the point. ``steps_passed`` falls 29 → 28 and the undistributed total rises;
   both are the measurement improving, not the engine regressing.

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
from loanwhiz.primitives.report_adapter import ReportAdapter
from loanwhiz.primitives.step_source_classifier import ENGINE_COMPUTED_RECIPIENTS
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
ENGINE_DISTRIBUTED_REVENUE = 5_019_907.60

#: The shortfall's two mechanisms, named separately because they have different
#: owners and different fixes (#511 split them; before it, only the first
#: existed because the second was masked by the engine echoing the report).
#:
#: Keeping one number for both would let a fix to either look like a fix to the
#: whole, which is exactly the reading epic #510 is trying to avoid.
#:
#: - the published rows the fold joins to no cascade step at all — #514's gap;
#: - the Class C interest the engine now claims and cannot compute — #512's gap.
UNJOINED_REVENUE_ROWS_TOTAL = 1_820_150.42
UNCOMPUTED_CLASS_C_INTEREST = 415_004.33

#: Derived, never transcribed: a fix to either mechanism must move this too.
REVENUE_SHORTFALL = UNJOINED_REVENUE_ROWS_TOTAL + UNCOMPUTED_CLASS_C_INTEREST

#: Class A interest, computed by the engine from balance and rate with no report
#: input, and equal to the published figure to the cent — the deal's first
#: independently-reconciled line (#511).
ENGINE_COMPUTED_CLASS_A_INTEREST = 3_277_457.78

#: The money-carrying Interest rows no engine label joins, and what each pays.
#: (Twenty report rows join nothing; the rest of them are zero.) The report
#: re-letters two management-fee rows bare ``(a)``, so the labels are not
#: unique — hence a list of pairs rather than a mapping.
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

#: Class B's published interest, split across the two sub-lettered rows under the
#: cascade's single ``(H)`` step. The engine step for ``(H)`` reconciles at zero.
CLASS_B_PUBLISHED_INTEREST = 386_773.50 + 257_625.00


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
    Choosing Cairn's tranche classes and residual label is a modelling decision
    with no published figure to check it against, and #496 is forbidden from
    making one to reach a cell state. ``test_the_finding_survives_the_adapter_choice``
    shows the choice cannot change this result anyway.
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
    """The headline: EUR 2,235,154.75 of published revenue the engine never places.

    ``WaterfallReconciliation.passed`` requires both that every joined step agrees
    and that the distributed total ties to available funds. Since #511 **both**
    gates fail: the tie-out gap is still the larger finding, but one step now
    carries a real delta instead of echoing the report at itself.
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


def test_exactly_one_step_disagrees_and_the_rest_is_the_tie_out_gap(
    recon: ReconciliationReport,
) -> None:
    """28 of 29 steps match; the one that does not is the engine refusing.

    Stated as its own assertion because the numbers read as contradictory and a
    reader who only saw ``steps_passed`` would report a near-pass. Two distinct
    failures are in play, and #510's whole discipline is not conflating them:

    - **the tie-out gate** — EUR 1,820,150.42 of published rows join no step
      (#514); and
    - **one step-level delta** — Class C interest, which #511 made
      engine-computed and whose coupon does not resolve, so it refuses instead
      of quoting the report back (#512).

    Before #511 this file asserted ``steps_passed == 29`` and every delta zero.
    That was 29 comparisons of the report against itself; this is 28 of them
    plus one honest disagreement, and the second is worth more than the first.
    """
    revenue = recon.periods[0].revenue
    assert len(revenue.steps) == 29
    assert revenue.steps_passed == 28

    disagreeing = [step for step in revenue.steps if step.delta != 0.0]
    assert [(s.priority, s.recipient) for s in disagreeing] == [
        ("(J)", "class_c_notes_interest")
    ]
    (class_c,) = disagreeing
    assert class_c.engine_amount == 0.0
    assert class_c.report_amount == pytest.approx(UNCOMPUTED_CLASS_C_INTEREST, abs=0.01)


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

    Since #511 the remainder has two sources, and the ``not_evaluable`` steps are
    the new one: recipients the engine now *claims* (their spelling resolves into
    ``ENGINE_COMPUTED_RECIPIENTS``) but cannot compute for want of a coupon. That
    is #493's layered refusal reaching the CLO, and it must stay visible here —
    an empty ``not_evaluable`` list would now mean the echo came back.
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

    # Exactly the two note-interest steps whose coupon does not resolve refuse.
    # Class A, the third engine-computed step, is absent from this list because
    # it computed — which is the whole of #511's result in one assertion.
    assert [step.recipient for step in execution.steps if step.not_evaluable] == [
        "class_b_notes_interest",
        "class_c_notes_interest",
    ]
    # And the reconciliation's shortfall is that same remainder, not a second
    # number that happens to be close.
    revenue = recon.periods[0].revenue
    assert revenue.report_total - revenue.engine_total == pytest.approx(
        execution.remaining, abs=0.01
    )


def test_the_shortfall_is_exactly_the_rows_no_engine_label_joins(
    recon: ReconciliationReport, nvr_report: NotesCashReport
) -> None:
    """Name the missing money: eight sub-lettered published rows, to the cent.

    The engine's 29 labels are the cascade's top-level ones; the report prints 62
    rows because it splits several of them into sub-lettered components. The
    reconciler's report-label folding handles Green Lion's purely-numeric
    ``(b)(1..n)`` wrap and nothing else, so these eight rows are joined by no step
    and their money is simply absent from the engine total.
    """
    revenue = recon.periods[0].revenue
    engine_labels = {step.priority for step in revenue.steps}
    (report_period,) = nvr_report.periods

    unjoined = [
        (step.priority, step.amount)
        for step in report_period.revenue_pop
        if step.priority not in engine_labels and step.amount != 0.0
    ]
    assert unjoined == [
        (label, pytest.approx(amount, abs=0.01)) for label, amount in UNJOINED_REVENUE_ROWS
    ]
    assert sum(amount for _, amount in unjoined) == pytest.approx(
        UNJOINED_REVENUE_ROWS_TOTAL, abs=0.01
    )


def test_class_b_interest_passes_without_ever_being_compared(
    recon: ReconciliationReport, nvr_report: NotesCashReport
) -> None:
    """The sharpest edge of the join gap: a real payment graded as zero-vs-zero.

    The report pays Class B EUR 644,398.50 across ``(H)(i)`` and ``(H)(ii)``. The
    cascade's single ``(H)`` step finds no ``(H)`` row in the report, takes EUR
    0.00, and is compared against a folded EUR 0.00 — a passing step that asserts
    nothing about the largest single figure the engine failed to place. A grade
    that reported only ``steps_passed`` would call this correct.
    """
    (class_b,) = [s for s in recon.periods[0].revenue.steps if s.priority == "(H)"]
    assert class_b.recipient == "class_b_notes_interest"
    assert class_b.engine_amount == 0.0
    assert class_b.report_amount == 0.0
    assert class_b.passed is True

    # Read the money off the document, so this reds if the report is ever
    # re-parsed into a shape where (H) does carry its children's total.
    (report_period,) = nvr_report.periods
    published = [s for s in report_period.revenue_pop if s.priority in ("(H)(i)", "(H)(ii)")]
    assert [s.priority for s in published] == ["(H)(i)", "(H)(ii)"]
    assert sum(s.amount for s in published) == pytest.approx(
        CLASS_B_PUBLISHED_INTEREST, abs=0.01
    )
    assert not [s for s in report_period.revenue_pop if s.priority == "(H)"]


# ---------------------------------------------------------------------------
# 3. What the passing lines are worth, and what the Principal side proves.
# ---------------------------------------------------------------------------


def test_class_a_interest_is_computed_from_the_deal_model_and_ties(
    clo_series: DealStateSeries, recon: ReconciliationReport
) -> None:
    """The independent line: EUR 3,277,457.78 derived, not copied, and it matches.

    This is what #511 bought. ``ENGINE_COMPUTED_RECIPIENTS`` names the recipients
    the interpreter derives from the deal model with no report input — the
    independent half of any reconciliation. It is spelled in canonical vocabulary
    (``class_a_interest``) while Cairn's cascade carries the document's own
    spelling (``class_a_notes_interest``), and the classifier used to test the raw
    string, so the membership test missed every step including the three classes
    the set does name. Every step was then handed the report's own figure as its
    "need" and agreed with it by construction.

    Resolving the spelling first, Class A's coupon resolves and the engine
    computes ``balance × rate/100 / 360 × days`` itself. Asserted through the
    fold (the need the interpreter actually produced) *and* the reconciliation
    (that need against the published figure), because only the pair distinguishes
    "computed and correct" from "copied and therefore correct".
    """
    execution = clo_series.period_results[0].revenue_execution
    (class_a,) = [s for s in execution.steps if s.recipient == "class_a_notes_interest"]
    assert class_a.not_evaluable is False
    assert class_a.need == pytest.approx(ENGINE_COMPUTED_CLASS_A_INTEREST, abs=0.01)

    (reconciled,) = [
        s for s in recon.periods[0].revenue.steps if s.recipient == "class_a_notes_interest"
    ]
    assert reconciled.engine_amount == pytest.approx(
        ENGINE_COMPUTED_CLASS_A_INTEREST, abs=0.01
    )
    assert reconciled.report_amount == pytest.approx(
        ENGINE_COMPUTED_CLASS_A_INTEREST, abs=0.01
    )
    assert reconciled.delta == 0.0

    # The set itself gained no CLO spelling — the fix is at the comparison, which
    # is what keeps one vocabulary in the table (#503).
    assert "class_a_notes_interest" not in ENGINE_COMPUTED_RECIPIENTS
    assert "class_a_interest" in ENGINE_COMPUTED_RECIPIENTS


def test_the_reconciliation_still_labels_every_step_report_supplied(
    recon: ReconciliationReport,
) -> None:
    """The half of the gap #511 could not close, pinned for whoever closes it.

    ``reconciler._source_of`` is a *second* raw-membership test over the same
    set, so the reconciliation report still calls every step ``report-supplied``
    — including the Class A line the test above proves was computed from the deal
    model. ``engine_computed_passed`` therefore reads 0 and understates the
    grade.

    #511's declared paths are the classifier and ``tests/``; ``reconciler.py``
    belongs to #512 and #514, and racing two sibling PRs on one file to fix a
    label is a worse trade than naming it. **This assertion is expected to red
    when either lands** — that is its job. Flip it to
    ``engine_computed_passed == 1`` for revenue and delete this note.
    """
    for waterfall in (recon.periods[0].revenue, recon.periods[0].redemption):
        assert waterfall.engine_computed_passed == 0
        assert {step.source for step in waterfall.steps} == {"report-supplied"}


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
    """Folding all eight classes rather than the adapter's default three changes nothing.

    ``ReportAdapter``'s ``DEFAULT_TRANCHE_CLASSES`` is Green-Lion-shaped, so a
    Cairn fold built on it seeds three of the deal's eight classes. That is the
    one modelling decision #496 declined to make, and this pins why declining it
    is safe.

    #496's reason — "no step is engine-computed, so no tranche balance reaches
    any amount" — stopped being true at #511, and this test red when it did,
    exactly as its last line promised. The reason is now narrower but still
    sound: the three engine-computed recipients (Classes A, B, C interest) are
    precisely the three the default already seeds, and the five classes widening
    adds (D, E, F and the two Class B strips beyond the first, plus the
    subordinated notes) name no recipient in ``ENGINE_COMPUTED_RECIPIENTS``. So
    the extra balances still reach no need calculator and the grade is identical
    either way — asserted, not assumed.

    It keeps its original job: it reds again if a *further* change makes a
    D-through-F step engine-computed without revisiting the seeding.
    """
    # Derived from the seed, not transcribed: a re-extraction that changed the
    # capital structure would otherwise leave this widening silently partial.
    every_class = tuple(
        re.sub(r"[^a-z0-9]+", "_", tranche["name"].lower()).strip("_")
        for tranche in clo_model.tranche_structure
    )
    assert len(every_class) == 8, every_class
    widened_adapter = ReportAdapter.from_deal_model(clo_model, tranche_classes=every_class)
    series = fold_report_series(clo_model, nvr_report, widened_adapter)
    widened = reconcile_series(series, nvr_report, deal_name=CLO_DEAL_NAME, tolerance=0.01)

    revenue = widened.periods[0].revenue
    assert widened.passed is False
    assert revenue.engine_total == pytest.approx(ENGINE_DISTRIBUTED_REVENUE, abs=0.01)
    assert revenue.engine_computed_passed == 0


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

    **The figure checked is the fold gap, not the total (#511).** The cards were
    written when the two were the same number. #511 made Class C's refusal
    visible, so the engine's total remainder is now ``REVENUE_SHORTFALL`` =
    ``UNJOINED_REVENUE_ROWS_TOTAL + UNCOMPUTED_CLASS_C_INTEREST`` while the cards
    still state only the first — which remains true of the rows they describe,
    but understates the deal's gap. Restating them is #515's ("Re-grade the CLO
    and record the verdict"); ``docs/**`` is outside #511's declared paths, and
    editing the constant instead of the cards would have hidden the drift rather
    than reported it.
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
