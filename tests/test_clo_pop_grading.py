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

2. **Reconciled against that one document directly, the Interest cascade is
   short by EUR 1,820,150.42** — 25% of the period's available revenue. The
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
# 1. The committed key cannot be graded as it stands.
# ---------------------------------------------------------------------------


def test_the_committed_key_refuses_the_grade_on_its_period_count(
    clo_series: DealStateSeries, clo_key: DealAnswerKey
) -> None:
    """Four key periods, one foldable document — the reconciler refuses to join.

    This is the first half of the finding and it is a property of the *key's
    shape*, not of any number in it: three of its four periods are authored from
    monthly trustee reports, which state no Priority of Payments and therefore
    give a fold nothing to produce a period result from. ``reconcile_series``
    raises rather than grading a partial answer, so no engine change can reach
    this grade while the key unions two document sets of different cadence.

    Left alone deliberately (#496 grades; it does not author). Recorded here so
    that whoever re-shapes the key finds an assertion, not a memory.
    """
    assert len(clo_series.period_results) == 1
    assert len(clo_key.periods) == 4
    assert sum(1 for p in clo_key.periods if p.revenue_pop or p.redemption_pop) == 1

    with pytest.raises(ValueError, match="Reconciler join mismatch"):
        reconcile_against_answer_key(clo_series, clo_key)


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
    recon: ReconciliationReport,
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
    assert CLASS_B_PUBLISHED_INTEREST == pytest.approx(644_398.50, abs=0.01)


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
    """Folding all eight classes rather than the adapter's default three changes nothing.

    ``ReportAdapter``'s ``DEFAULT_TRANCHE_CLASSES`` is Green-Lion-shaped, so a
    Cairn fold built on it seeds three of the deal's eight classes. That is the
    one modelling decision #496 declined to make, and this pins why declining it
    is safe: because no step is engine-computed, no tranche balance reaches any
    amount, and the grade is identical either way. It also reds if a later change
    makes a step engine-computed without revisiting the seeding.
    """
    eight_class = ReportAdapter.from_deal_model(
        clo_model,
        tranche_classes=(
            "class_a", "class_b_1", "class_b_2", "class_c",
            "class_d", "class_e", "class_f", "subordinated",
        ),
    )
    series = fold_report_series(clo_model, nvr_report, eight_class)
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
        ),
    ),
    (
        "README.md",
        ("no answer key is authored, so no cell of it is",),
    ),
    (
        "src/loanwhiz/data/deals/answer_keys/README.md",
        ("what a graded pop cell here will *not* prove",),
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
