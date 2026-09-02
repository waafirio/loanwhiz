"""Tests for the cross-deal graded quality harness (#428, epic #425).

The quality harness is the *graded* sibling of the capability matrix: for each
``(deal × check)`` it grades the engine's output against the deal's committed
ground-truth answer key (#427) to tolerance — ``passed`` / ``failed`` /
``not-applicable`` with a score, evidence and an honest reason. The whole point
(the #193 honesty discipline) is that it tells the *true* story, not a wall of
green — so these tests pin both the shape AND the honest grading behaviour.

They run fully offline (no network, no LLM):

- A **live-registry** test exercises the harness over the *real* shipped
  ``DEAL_REGISTRY`` + committed seeds + committed answer keys. The backfill (#429)
  committed Green Lion 2024-1's answer key (authored from its published Notes &
  Cash report), so the honest current verdict is: GL-2024-1's revenue + redemption
  PoP grade ``passed`` to the cent, while every other ``(deal × check)`` — including
  GL-2024-1's covenants / pool stats, which have no committed published figures —
  grades ``not-applicable`` with a real reason. This pins that honest mixed state
  and the per-cell reason contract (never a fabricated wall of green).
- **Machinery** tests inject synthetic / committed-report-derived answer keys (the
  same discipline ``test_reconciliation_answer_key.py`` uses) to prove the grading
  path lights up: a passing grade (matching the hand-built
  ``validate_green_lion_2024_1`` to the cent), a failing grade, pool-statistic
  grading, covenant grading, and the honest not-applicable reasons.
- An **endpoint** test pins ``GET /quality-matrix`` returns the graded matrix
  offline.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from loanwhiz.api import app
from loanwhiz.api.main import _load_cached_deal_model
from loanwhiz.config import DEAL_REGISTRY
from loanwhiz.primitives.base import Citation
from loanwhiz.primitives.covenant_monitor import TriggerDefinition
from loanwhiz.primitives.reconciler import (
    fold_green_lion_2024_1,
    load_green_lion_2023_1_report,
    load_green_lion_2024_1_report,
    validate_green_lion_2024_1,
)
from loanwhiz.primitives.reconciliation_answer_key import (
    AnswerKeyPeriod,
    CovenantResult,
    DealAnswerKey,
    load_answer_key,
)
from loanwhiz.primitives.quality_harness import (
    GRADE_FAILED,
    GRADE_NOT_APPLICABLE,
    GRADE_PASSED,
    QualityMatrix,
    build_quality_matrix,
    quality_check_rows,
)

client = TestClient(app)

GL_DEAL_ID = "green-lion-2024-1"
GL_DEAL_NAME = "Green Lion 2024-1 B.V."
GL23_DEAL_ID = "green-lion-2023-1"
GL23_DEAL_NAME = "Green Lion 2023-1 B.V."
#: Every deal whose published Notes & Cash ground truth is committed as an answer
#: key, so the harness genuinely grades it (#429 GL-2024-1, #440 GL-2023-1).
GRADED_DEAL_IDS = {GL_DEAL_ID, GL23_DEAL_ID}
_EXPECTED_CHECK_KEYS = ["revenue_pop", "redemption_pop", "covenants", "pool_stats"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cell(matrix: QualityMatrix, deal_id: str, check_key: str):
    for c in matrix.cells:
        if c.deal_id == deal_id and c.check_key == check_key:
            return c
    raise AssertionError(f"no cell for ({deal_id}, {check_key})")


def _real_matrix() -> QualityMatrix:
    """Build the matrix over the real registry + committed seeds + answer keys."""
    return build_quality_matrix(
        DEAL_REGISTRY,
        seed_loader=_load_cached_deal_model,
        answer_key_loader=load_answer_key,
    )


def _gl_key_from_report() -> DealAnswerKey:
    """A GL-2024-1 answer key authored from its committed Notes & Cash report."""
    return DealAnswerKey.from_notes_cash_report(load_green_lion_2024_1_report(), deal_id=GL_DEAL_ID)


def _gl_2023_key_from_report() -> DealAnswerKey:
    """A GL-2023-1 answer key authored from its committed Notes & Cash report."""
    return DealAnswerKey.from_notes_cash_report(
        load_green_lion_2023_1_report(), deal_id=GL23_DEAL_ID
    )


def _single_loader(deal_name: str, key: DealAnswerKey | None):
    """An answer-key loader returning ``key`` only for the named deal, else None."""

    def loader(deal_ctx):
        return key if deal_ctx.get("deal_name") == deal_name else None

    return loader


# ---------------------------------------------------------------------------
# Shape + catalogue
# ---------------------------------------------------------------------------


def test_check_catalogue_keys_and_primitives() -> None:
    rows = quality_check_rows()
    assert [r.key for r in rows] == _EXPECTED_CHECK_KEYS
    # Each row names a real underlying primitive + a non-empty label/description.
    assert {r.primitive_name for r in rows} == {
        "waterfall_runner",
        "covenant_monitor",
        "collections_aggregator",
    }
    for r in rows:
        assert r.label and r.description and r.category


def test_matrix_shape_covers_every_deal_x_check() -> None:
    m = _real_matrix()
    assert [d.deal_id for d in m.deals] == list(DEAL_REGISTRY)
    assert [c.key for c in m.checks] == _EXPECTED_CHECK_KEYS
    assert len(m.cells) == len(m.deals) * len(m.checks)
    # Tally is exhaustive over the grade vocabulary and sums to the cell count.
    assert set(m.tally) <= {GRADE_PASSED, GRADE_FAILED, GRADE_NOT_APPLICABLE}
    assert sum(m.tally.values()) == len(m.cells)
    assert m.note  # standing honesty disclosure present


# ---------------------------------------------------------------------------
# Live-registry honesty (#193) — backfilled GL-2024-1 PoP grades real; the rest
# stay honestly not-applicable. Never a fabricated wall of green.
# ---------------------------------------------------------------------------


def test_live_registry_reflects_the_backfilled_answer_keys_honestly() -> None:
    """The backfills committed Green Lion 2024-1's (#429) and Green Lion 2023-1's
    (#440) answer keys from their published Notes & Cash reports, so the honest
    verdict over the *live* registry is mixed: both deals' revenue + redemption PoP
    grade `passed` to the cent, every other (deal × check) — including those two
    deals' covenants / pool stats, which have no committed published figures —
    grades `not-applicable` with a real reason. Nothing is fabricated and nothing
    fails.

    Leone Arancio and Sol-Lion II publish no Notes & Cash report at all, so they
    have no ground truth to author a key from and stay wholly not-applicable —
    the #193 discipline, pinned here so a future backfill cannot fabricate one."""
    m = _real_matrix()

    # Exactly the deals with committed published ground truth carry an answer key.
    assert {d.deal_id for d in m.deals if d.has_answer_key} == GRADED_DEAL_IDS

    # Each graded deal's two PoP checks passed to the cent.
    for deal_id in GRADED_DEAL_IDS:
        rev = _cell(m, deal_id, "revenue_pop")
        red = _cell(m, deal_id, "redemption_pop")
        assert rev.grade == GRADE_PASSED and rev.score == pytest.approx(1.0)
        assert red.grade == GRADE_PASSED and red.score == pytest.approx(1.0)

    # Honest, not green-painted: exactly those pass, nothing fails, and every other
    # cell (incl. their covenants/pool_stats with no published figures) is
    # not-applicable.
    graded_cells = 2 * len(GRADED_DEAL_IDS)
    assert m.tally[GRADE_PASSED] == graded_cells
    assert m.tally.get(GRADE_FAILED, 0) == 0
    assert m.tally[GRADE_NOT_APPLICABLE] == len(m.cells) - graded_cells
    for deal_id in GRADED_DEAL_IDS:
        for ck in ("covenants", "pool_stats"):
            assert _cell(m, deal_id, ck).grade == GRADE_NOT_APPLICABLE
    for d in m.deals:
        if d.deal_id in GRADED_DEAL_IDS:
            continue
        assert not d.has_answer_key
        for ck in _EXPECTED_CHECK_KEYS:
            assert _cell(m, d.deal_id, ck).grade == GRADE_NOT_APPLICABLE


def test_committed_gl_answer_key_loads_and_matches_its_published_report() -> None:
    """Regression for the #429 backfill: the *committed* GL-2024-1 answer key
    resolves through the real loader (no base_dir override, from
    ``ANSWER_KEY_DATA_DIR``) and is faithful to its source published report —
    proving the on-disk key is genuine ground truth, not hand-edited drift."""
    loaded = load_answer_key(DEAL_REGISTRY[GL_DEAL_ID])
    assert loaded is not None, "committed green-lion-2024-1-bv.json must resolve"
    assert loaded.deal_id == GL_DEAL_ID
    assert loaded.deal_name == GL_DEAL_NAME
    assert loaded.format_version == 1
    assert len(loaded.periods) == 3
    # The committed key equals one freshly authored from the published report: it
    # was authored via from_notes_cash_report and never hand-tweaked.
    assert loaded == _gl_key_from_report()


def test_committed_gl_2023_1_key_matches_its_published_report() -> None:
    """Regression for the #440 backfill: the *committed* GL-2023-1 answer key
    resolves through the real loader (from ``ANSWER_KEY_DATA_DIR``, so the slug
    must match the seed) and is byte-faithful to its source published report —
    proving the on-disk key is genuine ground truth, not hand-edited drift."""
    loaded = load_answer_key(DEAL_REGISTRY[GL23_DEAL_ID])
    assert loaded is not None, "committed green-lion-2023-1-bv.json must resolve"
    assert loaded.deal_id == GL23_DEAL_ID
    assert loaded.deal_name == GL23_DEAL_NAME
    assert loaded.format_version == 1
    assert len(loaded.periods) == 3
    # Authored via from_notes_cash_report from the committed fixtures, never tweaked.
    assert loaded == _gl_2023_key_from_report()


def test_answer_keys_exist_exactly_where_published_reports_do() -> None:
    """The #193 honesty discipline, pinned as a test: a committed answer key exists
    for exactly those deals that publish a Notes & Cash report to author it from.

    Asserted in BOTH directions on purpose. A one-way "report-less deals have no
    key" loop passes by finding nothing, so it would go quiet if the registry ever
    lost its report-less deals; the equality below cannot, and the explicit
    non-empty guard makes the vacuous case a failure rather than a silent pass."""
    with_reports = {
        deal_id
        for deal_id, ctx in DEAL_REGISTRY.items()
        if ctx.get("notes_cash_report_urls")
    }
    without_reports = set(DEAL_REGISTRY) - with_reports
    assert with_reports and without_reports, (
        "this guard is only meaningful while the registry holds deals of both "
        "kinds; it must never pass vacuously"
    )

    # Publishing a report is exactly what earns a key — no more, no less.
    assert with_reports == GRADED_DEAL_IDS
    for deal_id in with_reports:
        assert load_answer_key(DEAL_REGISTRY[deal_id]) is not None, (
            f"{deal_id} publishes a Notes & Cash report but has no committed key"
        )
    for deal_id in without_reports:
        assert load_answer_key(DEAL_REGISTRY[deal_id]) is None, (
            f"{deal_id} publishes no Notes & Cash report, so its answer key could "
            "only be fabricated"
        )


def test_every_not_applicable_cell_carries_a_real_reason() -> None:
    """The honesty contract: every skip carries its real, non-empty reason, and a
    not-applicable cell never claims a score."""
    m = _real_matrix()
    for c in m.cells:
        if c.grade == GRADE_NOT_APPLICABLE:
            assert c.reason.strip(), f"empty reason on {c.deal_id}/{c.check_key}"
            assert c.score is None


# ---------------------------------------------------------------------------
# Machinery — a graded answer key lights up a passing cell
# ---------------------------------------------------------------------------


def test_injected_answer_key_grades_pop_passed_to_the_cent() -> None:
    """A GL-2024-1 answer key (from its committed report) + the default offline
    series provider grades revenue + redemption PoP `passed`, score 1.0 — matching
    the hand-built `validate_green_lion_2024_1` proof."""
    key = _gl_key_from_report()
    deals = {
        GL_DEAL_ID: {"deal_name": GL_DEAL_NAME},
        "no-key-deal": {"deal_name": "No Key Deal B.V."},
    }
    m = build_quality_matrix(
        deals,
        seed_loader=_load_cached_deal_model,
        answer_key_loader=_single_loader(GL_DEAL_NAME, key),
    )

    rev = _cell(m, GL_DEAL_ID, "revenue_pop")
    red = _cell(m, GL_DEAL_ID, "redemption_pop")
    assert rev.grade == GRADE_PASSED and rev.score == pytest.approx(1.0)
    assert red.grade == GRADE_PASSED and red.score == pytest.approx(1.0)
    assert rev.tolerance_eur == pytest.approx(key.tolerance_eur)
    # The grade reflects the same to-the-cent proof the hand-built builder asserts.
    baseline = validate_green_lion_2024_1()
    assert baseline.passed is True
    assert rev.evidence["periods_checked"] == baseline.periods_checked == 3

    # The deal column reflects the injected key; the no-key deal grades n/a.
    assert _cell(m, GL_DEAL_ID, "revenue_pop").deal_id == GL_DEAL_ID
    for ck in _EXPECTED_CHECK_KEYS:
        assert _cell(m, "no-key-deal", ck).grade == GRADE_NOT_APPLICABLE
    assert m.tally[GRADE_PASSED] >= 2


def test_wrong_tolerance_grades_failed_not_green() -> None:
    """A negative (impossible) tolerance must FAIL the PoP grade — the harness
    surfaces failures, never a wall of green."""
    key = _gl_key_from_report().model_copy(update={"tolerance_eur": -1.0})
    m = build_quality_matrix(
        {GL_DEAL_ID: {"deal_name": GL_DEAL_NAME}},
        seed_loader=_load_cached_deal_model,
        answer_key_loader=_single_loader(GL_DEAL_NAME, key),
    )
    rev = _cell(m, GL_DEAL_ID, "revenue_pop")
    assert rev.grade == GRADE_FAILED
    assert rev.score is not None and rev.score < 1.0
    assert m.tally[GRADE_FAILED] >= 1


# ---------------------------------------------------------------------------
# Pool-statistics grading against the folded engine series
# ---------------------------------------------------------------------------


def _gl_states_by_date() -> dict[str, object]:
    series, _ = fold_green_lion_2024_1()
    by_date: dict[str, object] = {}
    for st in series.states:
        existing = by_date.get(st.reporting_date)
        if existing is None or (
            getattr(existing, "collections", None) is None
            and getattr(st, "collections", None) is not None
        ):
            by_date[st.reporting_date] = st
    return by_date


def test_pool_stats_grade_passes_when_matching_the_series() -> None:
    """An answer key whose pool_balance_end matches the folded engine series'
    closing balances grades pool_stats `passed`."""
    states = _gl_states_by_date()
    # Author a pool-stats-only key from the series' own closing balances.
    periods = [
        AnswerKeyPeriod(
            reporting_date=date,
            period_label=date,
            pool_stats={"pool_balance_end": float(st.pool_balance)},
        )
        for date, st in states.items()
    ]
    key = DealAnswerKey(deal_id=GL_DEAL_ID, deal_name=GL_DEAL_NAME, periods=periods)
    m = build_quality_matrix(
        {GL_DEAL_ID: {"deal_name": GL_DEAL_NAME}},
        seed_loader=_load_cached_deal_model,
        answer_key_loader=_single_loader(GL_DEAL_NAME, key),
    )
    ps = _cell(m, GL_DEAL_ID, "pool_stats")
    assert ps.grade == GRADE_PASSED and ps.score == pytest.approx(1.0)
    assert ps.evidence["stats_graded"] >= 1
    # No PoP in this key ⇒ the PoP checks degrade honestly to not-applicable.
    assert _cell(m, GL_DEAL_ID, "revenue_pop").grade == GRADE_NOT_APPLICABLE


def test_pool_stats_grade_fails_on_a_wrong_published_balance() -> None:
    states = _gl_states_by_date()
    first_date = next(iter(states))
    periods = [
        AnswerKeyPeriod(
            reporting_date=first_date,
            period_label=first_date,
            # Deliberately wrong by far more than one cent.
            pool_stats={"pool_balance_end": float(states[first_date].pool_balance) + 1_000_000.0},
        )
    ]
    key = DealAnswerKey(deal_id=GL_DEAL_ID, deal_name=GL_DEAL_NAME, periods=periods)
    m = build_quality_matrix(
        {GL_DEAL_ID: {"deal_name": GL_DEAL_NAME}},
        seed_loader=_load_cached_deal_model,
        answer_key_loader=_single_loader(GL_DEAL_NAME, key),
    )
    ps = _cell(m, GL_DEAL_ID, "pool_stats")
    assert ps.grade == GRADE_FAILED
    assert ps.evidence["max_abs_delta_eur"] > 1.0


# ---------------------------------------------------------------------------
# Covenant grading against the engine's CovenantMonitor
# ---------------------------------------------------------------------------


def _trigger(metric: str, threshold: float) -> TriggerDefinition:
    return TriggerDefinition(
        name="test_cov",
        description="synthetic test covenant",
        metric=metric,
        threshold=threshold,
        direction="above",
        consequence="test",
        citation=Citation(document="test", excerpt="test"),
    )


def _covenant_key(metric_value: float, published_passed: bool) -> DealAnswerKey:
    return DealAnswerKey(
        deal_id="cov-deal",
        deal_name="Covenant Deal B.V.",
        periods=[
            AnswerKeyPeriod(
                reporting_date="2025-09-30",
                period_label="September 2025",
                covenants=[CovenantResult(name="test_cov", passed=published_passed)],
                pool_stats={"test_metric": metric_value},
            )
        ],
    )


def test_covenant_grade_passes_when_engine_agrees_with_published_outcome() -> None:
    """metric 5 < threshold 10 ⇒ engine not breached ⇒ engine_passed=True; the
    published covenant also passed ⇒ they agree ⇒ graded passed."""
    deals = {"cov-deal": {"deal_name": "Covenant Deal B.V."}}
    m = build_quality_matrix(
        deals,
        seed_loader=lambda _ctx: None,
        answer_key_loader=_single_loader("Covenant Deal B.V.", _covenant_key(5.0, True)),
        triggers_loader=lambda _ctx: [_trigger("test_metric", 10.0)],
    )
    cov = _cell(m, "cov-deal", "covenants")
    assert cov.grade == GRADE_PASSED and cov.score == pytest.approx(1.0)
    assert cov.evidence["covenants_graded"] == 1


def test_covenant_grade_fails_when_engine_disagrees() -> None:
    """metric 20 > threshold 10 ⇒ engine breached ⇒ engine_passed=False; the
    published covenant claims passed=True ⇒ they disagree ⇒ graded failed."""
    deals = {"cov-deal": {"deal_name": "Covenant Deal B.V."}}
    m = build_quality_matrix(
        deals,
        seed_loader=lambda _ctx: None,
        answer_key_loader=_single_loader("Covenant Deal B.V.", _covenant_key(20.0, True)),
        triggers_loader=lambda _ctx: [_trigger("test_metric", 10.0)],
    )
    cov = _cell(m, "cov-deal", "covenants")
    assert cov.grade == GRADE_FAILED
    assert cov.evidence["covenants_matched"] == 0


def test_covenant_grade_not_applicable_when_metric_unresolvable() -> None:
    """A published covenant whose metric the engine cannot resolve from the answer
    key grades not-applicable (honest 'couldn't evaluate'), never a fake pass."""
    deals = {"cov-deal": {"deal_name": "Covenant Deal B.V."}}
    key = DealAnswerKey(
        deal_id="cov-deal",
        deal_name="Covenant Deal B.V.",
        periods=[
            AnswerKeyPeriod(
                reporting_date="2025-09-30",
                period_label="September 2025",
                covenants=[CovenantResult(name="test_cov", passed=True)],
                # No pool_stats ⇒ the trigger's metric is not resolvable.
            )
        ],
    )
    m = build_quality_matrix(
        deals,
        seed_loader=lambda _ctx: None,
        answer_key_loader=_single_loader("Covenant Deal B.V.", key),
        triggers_loader=lambda _ctx: [_trigger("test_metric", 10.0)],
    )
    cov = _cell(m, "cov-deal", "covenants")
    assert cov.grade == GRADE_NOT_APPLICABLE
    assert cov.reason.strip()


# ---------------------------------------------------------------------------
# Honest not-applicable reasons
# ---------------------------------------------------------------------------


def test_empty_pop_section_is_not_applicable_with_reason() -> None:
    """A key present but with no revenue PoP ⇒ revenue_pop not-applicable, with a
    reason that names the missing section — not a silent blank or a fake pass."""
    key = DealAnswerKey(
        deal_id=GL_DEAL_ID,
        deal_name=GL_DEAL_NAME,
        periods=[AnswerKeyPeriod(reporting_date="2025-09-30", period_label="Sep 2025")],
    )
    m = build_quality_matrix(
        {GL_DEAL_ID: {"deal_name": GL_DEAL_NAME}},
        seed_loader=_load_cached_deal_model,
        answer_key_loader=_single_loader(GL_DEAL_NAME, key),
    )
    rev = _cell(m, GL_DEAL_ID, "revenue_pop")
    assert rev.grade == GRADE_NOT_APPLICABLE
    assert "revenue" in rev.reason.lower()


def test_answer_key_but_no_offline_series_is_not_applicable() -> None:
    """A deal with an answer key but no committed offline engine series grades the
    PoP checks not-applicable with that honest reason (the default provider only
    knows Green Lion 2024-1)."""
    key = DealAnswerKey(
        deal_id="leone-arancio-2023-1",
        deal_name="Leone Arancio RMBS 2023-1 S.r.l.",
        periods=[
            AnswerKeyPeriod(
                reporting_date="2025-09-30",
                period_label="Sep 2025",
                revenue_pop=[],
            )
        ],
    )
    # Give it a revenue step so the section is non-empty, forcing the series gate.
    from loanwhiz.primitives.reconciliation_answer_key import AnswerKeyPopStep

    key.periods[0].revenue_pop = [AnswerKeyPopStep(priority="(a)", amount=1.0)]
    m = build_quality_matrix(
        {"leone-arancio-2023-1": {"deal_name": "Leone Arancio RMBS 2023-1 S.r.l."}},
        seed_loader=_load_cached_deal_model,
        answer_key_loader=_single_loader("Leone Arancio RMBS 2023-1 S.r.l.", key),
    )
    rev = _cell(m, "leone-arancio-2023-1", "revenue_pop")
    assert rev.grade == GRADE_NOT_APPLICABLE
    assert "series" in rev.reason.lower()


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------


def test_quality_matrix_endpoint_returns_graded_matrix_offline() -> None:
    resp = client.get("/quality-matrix")
    assert resp.status_code == 200
    body = resp.json()
    assert [c["key"] for c in body["checks"]] == _EXPECTED_CHECK_KEYS
    assert [d["deal_id"] for d in body["deals"]] == list(DEAL_REGISTRY)
    assert len(body["cells"]) == len(body["deals"]) * len(body["checks"])
    assert sum(body["tally"].values()) == len(body["cells"])
    assert body["note"]
    # Offline + the committed GL-2024-1 (#429) and GL-2023-1 (#440) answer keys:
    # the honest verdict is each graded deal's two PoP checks pass, nothing fails,
    # the rest are not-applicable.
    graded_cells = 2 * len(GRADED_DEAL_IDS)
    assert body["tally"][GRADE_PASSED] == graded_cells
    assert body["tally"].get(GRADE_FAILED, 0) == 0
    assert body["tally"][GRADE_NOT_APPLICABLE] == len(body["cells"]) - graded_cells
