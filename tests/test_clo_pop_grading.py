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
   EUR 1,820,150.42 undistributed** — 25% of the period's available revenue. The
   pot is the report's own stated figure and no step is starved (``total_shortfall``
   is EUR 0.00): the money has no cascade step to go to at all. The
   report prints 62 rows; the extracted cascade carries 29 top-level labels; 20
   report rows are joined by no step at all, and the eight of those that carry
   money (``(A)(i)``, ``(A)(ii)``, ``(H)(i)``, ``(H)(ii)``, ``(CC)(1)(a)``, and
   two the report re-letters bare ``(a)``) account for the shortfall to the cent.
   ``reconciler._fold_report_revenue_steps`` folds only Green Lion's
   purely-numeric ``(b)(1..n)`` wrap artefact, which is not this report's shape.

3. **Every one of the 29 steps "passes", and none of those passes is evidence.**
   No Cairn recipient is in ``ENGINE_COMPUTED_RECIPIENTS``, so every step is
   classified ``report-supplied`` — its amount is taken from the report under its
   own label and compared to itself. Where the label is absent from the report
   the engine takes EUR 0.00 and compares it to a folded EUR 0.00, so Class B's
   published EUR 644,398.50 reconciles as a pass while never being looked at.

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
ENGINE_DISTRIBUTED_REVENUE = 5_434_911.93
REVENUE_SHORTFALL = 1_820_150.42

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
    """The headline: EUR 1,820,150.42 of published revenue the engine never places.

    ``WaterfallReconciliation.passed`` requires both that every joined step agrees
    and that the distributed total ties to available funds. Only the second gate
    fails, which is what makes the shortfall — not a step-level delta — the thing
    to name.
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


def test_no_step_disagrees_which_is_why_the_shortfall_is_the_finding(
    recon: ReconciliationReport,
) -> None:
    """Every joined step matches to the cent; the cascade still fails.

    Stated as its own assertion because the two facts read as contradictory and a
    reader who only saw ``steps_passed`` would report a pass. The failure lives
    entirely in the tie-out gate.
    """
    revenue = recon.periods[0].revenue
    assert len(revenue.steps) == 29
    assert revenue.steps_passed == 29
    assert all(step.delta == 0.0 for step in revenue.steps)


def test_the_money_is_undistributed_not_underfunded(
    clo_series: DealStateSeries, recon: ReconciliationReport
) -> None:
    """The pot is right; the cascade has nowhere to put part of it.

    Read off the fold directly rather than through the reconciliation, because
    the two readings answer different questions and only this one distinguishes
    the failure modes. The first step sees the report's full stated available
    revenue, no step is gated, and ``total_shortfall`` is EUR 0.00 — so every
    step that exists received its whole need. The gap is the engine's
    ``remaining``: money the 29-step cascade was never asked to place, because
    the rows carrying it join no step at all.

    That distinction is what stops the obvious wrong fix. A cascade whose steps
    were each short would be an amount problem, and sweeping the remainder into
    a residual step would look like a repair; here the remainder belongs to named
    recipients the cascade has no line for, so a residual sweep would pay it to
    the wrong party and turn a visible failure into a silent one.
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
    assert not [step for step in execution.steps if step.not_evaluable]
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
    assert sum(amount for _, amount in unjoined) == pytest.approx(REVENUE_SHORTFALL, abs=0.01)


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


def test_not_one_line_of_the_grade_is_engine_computed(
    recon: ReconciliationReport,
) -> None:
    """Every step's amount comes from the report, so agreement is a tautology.

    ``ENGINE_COMPUTED_RECIPIENTS`` names the recipients the interpreter derives
    from the deal model with no report input — the independent half of any
    reconciliation. It is spelled in canonical vocabulary (``class_a_interest``)
    while Cairn's extracted cascade carries the report's own spelling
    (``class_a_notes_interest``), so the membership test misses every step,
    including the three classes the set does name. The engine is therefore proven
    only to *route* these lines in priority order, never to compute one.
    """
    for waterfall in (recon.periods[0].revenue, recon.periods[0].redemption):
        assert waterfall.engine_computed_passed == 0
        assert {step.source for step in waterfall.steps} == {"report-supplied"}

    revenue_recipients = {step.recipient for step in recon.periods[0].revenue.steps}
    assert revenue_recipients & ENGINE_COMPUTED_RECIPIENTS == set()
    # The near-miss that makes this a naming seam rather than an absence.
    assert "class_a_notes_interest" in revenue_recipients
    assert "class_a_interest" in ENGINE_COMPUTED_RECIPIENTS


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

    The direction of this test inverted at #520. It used to widen off a
    Green-Lion-shaped ``DEFAULT_TRANCHE_CLASSES`` default; the default is now the
    deal's own eight classes, so the *narrowing* is what has to be pinned. The
    property it protects is unchanged and is still #496's: because no step is
    engine-computed, no tranche balance reaches any amount, so the grade is
    identical either way — and this reds if a later change makes a step
    engine-computed without revisiting the seeding.
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


def test_every_declared_class_reaches_the_adapter_seed_not_just_the_name_list(
    clo_model: DealModel, nvr_report: NotesCashReport
) -> None:
    """The eight classes arrive as real tranches on the adapter's seed, by name.

    #512's lesson, asserted where it bites: a complete, correct per-class map can
    reach nothing with no error anywhere, because the lookup is by **tranche
    name** and the class simply has no tranche. So the derived name list and the
    per-tranche *arrival* are two separate assertions — one passing does not imply
    the other. Asserting only the first is how this stayed invisible.

    This covers the adapter's own boundary (#520's scope). It does **not** yet
    reach the folded engine state: ``api.main._primitives_seed_from_report_seed``
    flattens this list back onto ``class_{a,b,c}_balance`` kwargs, so the stack is
    truncated a second time one layer down. That site is outside this issue's
    declared paths and is raised as a scope-expansion request on #520 rather than
    silently fixed here.

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
    adapter = ReportAdapter.from_deal_model(clo_model)
    assert adapter.tranche_classes == every_class

    # 2. They arrive — as real tranches carrying the report's own balances, which
    #    is what a resolved per-class rate needs in order to land anywhere.
    seed = adapter.seed(nvr_report.periods[0])
    seeded = [t.name for t in seed.tranches]
    assert seeded == list(every_class)
    # The five the Green Lion triple could never reach, named so a regression to
    # a prefix of the stack reds here rather than passing vacuously.
    assert {"class_b_1", "class_b_2", "class_d", "class_e", "class_f"} <= set(seeded)
    # Class B is #515's sharpest case: both its sub-classes carry a real balance,
    # so its EUR 644,398.50 has somewhere to attach rather than folding to 0.00.
    by_name = {t.name: t for t in seed.tranches}
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
    """
    repo_root = Path(__file__).resolve().parents[1]
    shortfall = f"{REVENUE_SHORTFALL:,.2f}"

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
