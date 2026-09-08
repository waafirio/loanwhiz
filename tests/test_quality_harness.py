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

from contextlib import suppress
from pathlib import Path
from tempfile import mkdtemp

import pytest
from fastapi.testclient import TestClient

from loanwhiz.api import app
from loanwhiz.api.main import _load_cached_deal_model
from loanwhiz.config import ANSWER_KEY_DATA_DIR, DEAL_REGISTRY
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
    answer_key_path,
    load_answer_key,
)
from loanwhiz.primitives.quality_harness import (
    GRADE_FAILED,
    GRADE_NOT_APPLICABLE,
    GRADE_PASSED,
    QualityMatrix,
    _DealGrading,
    _grade_covenants,
    build_quality_matrix,
    quality_check_rows,
)

client = TestClient(app)

GL_DEAL_ID = "green-lion-2024-1"
GL_DEAL_NAME = "Green Lion 2024-1 B.V."
GL23_DEAL_ID = "green-lion-2023-1"
GL23_DEAL_NAME = "Green Lion 2023-1 B.V."
CLO_DEAL_ID = "cairn-clo-xvii"
CLO_DEAL_NAME = "Cairn CLO XVII DAC"

#: Deals whose committed answer key carries a **Priority-of-Payments** section —
#: Green Lion's from its quarterly Notes & Cash reports (#429, #440), Cairn's from
#: the Note Valuation Report that is the CLO analogue of one (#495). This is the
#: set the registry's ``notes_cash_report_urls`` promise must equal.
POP_KEYED_DEAL_IDS = {GL_DEAL_ID, GL23_DEAL_ID, CLO_DEAL_ID}

#: Of those, the deals the harness can actually *grade* to the cent: a PoP key
#: alone grades nothing, because the engine side comes from a committed offline
#: fold registered in ``_default_series_provider`` (the #440 lesson). Cairn has
#: no such fold, so its PoP cells stay honestly not-applicable on the series
#: precondition rather than on the key.
#:
#: #496 ran that grade without registering one and reports why the set does not
#: grow: the Interest cascade is short EUR 1,820,150.42 of the report's stated
#: available revenue, no step is engine-computed, and the key's four periods
#: cannot join a fold built from its one PoP-bearing document.
#: ``tests/test_clo_pop_grading.py`` holds the measured result; adding Cairn here
#: without changing that is what this line exists to stop.
POP_GRADED_DEAL_IDS = {GL_DEAL_ID, GL23_DEAL_ID}

#: Deals whose published **coverage-test results** are committed as an answer key
#: (#481). A trustee report states no Priority of Payments, so these results reach
#: the key through the other constructor and grade the covenants row.
COVENANT_GRADED_DEAL_IDS = {CLO_DEAL_ID}

#: Every deal carrying a committed key, by any route.
GRADED_DEAL_IDS = POP_KEYED_DEAL_IDS | COVENANT_GRADED_DEAL_IDS

#: The published coverage results the CLO key carries: 8 decided tests (Class F
#: is published N/A and is excluded, not coerced) across 3 reporting dates.
CLO_COVENANTS_GRADED = 24
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


def _named_trigger(name: str, *, metric: str, threshold: float | None) -> TriggerDefinition:
    return TriggerDefinition(
        name=name,
        description=name,
        metric=metric,
        threshold=threshold,
        direction="below",
        consequence="n/a",
        citation=Citation(document="test", excerpt="test"),
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
    """The honest verdict over the *live* registry, by route.

    Two routes to ground truth are committed, and they grade different rows.
    Green Lion 2024-1 (#429) and 2023-1 (#440) publish a Notes & Cash report, so
    their revenue + redemption PoP grade ``passed`` to the cent. Cairn CLO XVII
    (#481) publishes monthly trustee reports stating each coverage test's result
    and required level, so its covenants row grades ``passed``.

    Since #495 Cairn's key **also** carries a Priority of Payments, authored from
    its Note Valuation Report — and its PoP rows are still not-applicable, for a
    reason that moved: not "no ground truth" but "no offline engine series to
    reconcile it against" (#440's half, supplied by #496). That is asserted
    below rather than left implicit, because a cell whose grade is unchanged but
    whose reason is now false is exactly the #457/#471 failure.

    Leone Arancio and Sol-Lion II publish neither, so they have no ground truth
    to author a key from and stay wholly not-applicable — the #193 discipline,
    pinned here so a future backfill cannot fabricate one."""
    m = _real_matrix()

    # Exactly the deals with committed published ground truth carry an answer key.
    assert {d.deal_id for d in m.deals if d.has_answer_key} == GRADED_DEAL_IDS

    # Route 1 — each PoP-graded deal's two PoP checks passed to the cent.
    for deal_id in POP_GRADED_DEAL_IDS:
        rev = _cell(m, deal_id, "revenue_pop")
        red = _cell(m, deal_id, "redemption_pop")
        assert rev.grade == GRADE_PASSED and rev.score == pytest.approx(1.0)
        assert red.grade == GRADE_PASSED and red.score == pytest.approx(1.0)

    # Route 2 — the covenant-graded deal's published outcomes all matched, and
    # the COUNT is pinned. A cell that quietly stopped resolving its metrics
    # would grade not-applicable rather than fail, so "did not go red" is not
    # evidence here; only the number of things actually graded is.
    for deal_id in COVENANT_GRADED_DEAL_IDS:
        cov = _cell(m, deal_id, "covenants")
        assert cov.grade == GRADE_PASSED and cov.score == pytest.approx(1.0)
        assert cov.evidence["covenants_graded"] == CLO_COVENANTS_GRADED
        assert cov.evidence["covenants_matched"] == CLO_COVENANTS_GRADED
        assert cov.evidence["not_evaluable_covenant_names"] == []
        assert cov.evidence["unmatched_covenant_names"] == []

    # Honest, not green-painted: exactly those pass, nothing fails, and every
    # other cell is not-applicable — including each graded deal's *other* rows,
    # which have no committed published figures of that kind.
    passed_cells = 2 * len(POP_GRADED_DEAL_IDS) + len(COVENANT_GRADED_DEAL_IDS)
    assert m.tally[GRADE_PASSED] == passed_cells
    assert m.tally.get(GRADE_FAILED, 0) == 0
    assert m.tally[GRADE_NOT_APPLICABLE] == len(m.cells) - passed_cells
    for deal_id in POP_GRADED_DEAL_IDS:
        for ck in ("covenants", "pool_stats"):
            assert _cell(m, deal_id, ck).grade == GRADE_NOT_APPLICABLE
    for deal_id in COVENANT_GRADED_DEAL_IDS:
        for ck in ("revenue_pop", "redemption_pop", "pool_stats"):
            cell = _cell(m, deal_id, ck)
            assert cell.grade == GRADE_NOT_APPLICABLE
            # The reason names the series, and no longer claims the key is
            # empty — that claim was retracted when #495 committed the PoP.
            assert "engine series" in cell.reason.lower()
            assert "carries no" not in cell.reason.lower()
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


def test_answer_keys_exist_exactly_where_published_ground_truth_does() -> None:
    """The #193 honesty discipline, pinned as a test: a committed answer key
    exists for exactly those deals that publish ground truth to author it from.

    The proxy for "publishes ground truth" is now two routes, not one, because
    a second kind of document earned a key (#481) — but the shape of the guard
    is unchanged, and deliberately so.

    Route 1 is a **registry fact**: ``notes_cash_report_urls`` is what says a
    deal publishes a Priority of Payments, and the equality below is what makes
    setting that key a promise rather than a URL slot.

    Route 2 cannot use a registry fact — nothing in ``deals.json`` distinguishes
    a trustee report that states its coverage tests from one that does not — so
    membership is **earned by regeneration instead**: every covenant-graded key
    must reproduce byte-for-byte from committed report fixtures. That is what
    stops the constant below being widened by declaration: adding a deal to it
    without an authoring path fails in
    ``test_committed_clo_answer_key_regenerates_from_its_report_fixtures``, and
    adding one *with* a fabricated path fails against the documents.

    Asserted in BOTH directions on purpose. A one-way "ground-truth-less deals
    have no key" loop passes by finding nothing, so it would go quiet if the
    registry ever lost its ground-truth-less deals; the equalities below cannot,
    and the explicit non-empty guard makes the vacuous case a failure rather
    than a silent pass."""
    with_reports = {
        deal_id
        for deal_id, ctx in DEAL_REGISTRY.items()
        if ctx.get("notes_cash_report_urls")
    }
    keyed = {
        deal_id
        for deal_id in DEAL_REGISTRY
        if load_answer_key(DEAL_REGISTRY[deal_id]) is not None
    }
    without_ground_truth = set(DEAL_REGISTRY) - GRADED_DEAL_IDS
    assert with_reports and COVENANT_GRADED_DEAL_IDS and without_ground_truth, (
        "this guard is only meaningful while the registry holds deals of all "
        "three kinds; it must never pass vacuously"
    )

    # Route 1: registering a report that publishes a Priority of Payments is
    # exactly what earns a PoP-bearing key — no more, no less.
    assert with_reports == POP_KEYED_DEAL_IDS
    for deal_id in with_reports:
        key = load_answer_key(DEAL_REGISTRY[deal_id])
        assert key is not None, f"{deal_id} publishes a report but has no committed key"
        assert any(p.revenue_pop or p.redemption_pop for p in key.periods)

    # Route 2: a covenant-bearing key carries published test results. Since #495
    # one deal earns both routes, so the "and no PoP" half is asserted **per
    # period** rather than per key: a trustee report states no Priority of
    # Payments, so a period carrying its covenants must carry no PoP — while the
    # same key may hold a separate period authored from a different document.
    # A key-level check would have to be dropped here; a period-level one gets
    # stricter, because it now also pins that the two documents stay unmixed.
    for deal_id in COVENANT_GRADED_DEAL_IDS:
        key = load_answer_key(DEAL_REGISTRY[deal_id])
        assert key is not None
        covenant_periods = [p for p in key.periods if p.covenants]
        assert covenant_periods
        for period in covenant_periods:
            assert not period.revenue_pop and not period.redemption_pop

    # Both directions, over the whole registry.
    assert keyed == GRADED_DEAL_IDS
    for deal_id in without_ground_truth:
        assert load_answer_key(DEAL_REGISTRY[deal_id]) is None, (
            f"{deal_id} publishes no ground truth this repo can read, so its "
            "answer key could only be fabricated"
        )

    # And no key file exists under a slug no registered deal resolves to — a
    # committed key nothing loads would slip past every check above.
    expected_slugs = {
        answer_key_path(DEAL_REGISTRY[deal_id]["deal_name"]).stem for deal_id in GRADED_DEAL_IDS
    }
    assert {p.stem for p in ANSWER_KEY_DATA_DIR.glob("*.json")} == expected_slugs


def test_committed_clo_answer_key_regenerates_from_its_report_fixtures() -> None:
    """The committed CLO key is what the documents say — not what someone typed.

    This is the guard the whole graded-CLO claim rests on. An answer key
    inferred from the engine's own output would grade the engine against itself
    and make the cell vacuously green, which is the most damaging failure this
    surface has available to it. Regenerating the committed bytes from the
    committed report fixtures, through a path with no engine module on it, is
    what makes that unrepresentable rather than merely discouraged.

    Since #495 the key is authored from **two** document sets — the trustee
    reports and the Note Valuation Report — so the regeneration runs through the
    union. Both halves are asserted to be present first: a byte comparison
    against a builder that had silently stopped reading one document would still
    pass if the committed file had been regenerated from the same broken builder,
    so "the bytes match" is only worth what the shape check in front of it is.

    Byte-for-byte against the file on disk, so hand-editing a single published
    threshold or one waterfall step reds here."""
    from clo_answer_key_source import clo_key_from_all_documents  # noqa: PLC0415

    from loanwhiz.primitives.reconciliation_answer_key import write_answer_key  # noqa: PLC0415

    committed = answer_key_path(CLO_DEAL_NAME)
    assert committed.exists(), "the CLO answer key is not committed"

    key = clo_key_from_all_documents()
    assert any(p.covenants for p in key.periods), "the trustee half is missing"
    assert any(p.revenue_pop and p.redemption_pop for p in key.periods), (
        "the Note Valuation half is missing"
    )

    regenerated = write_answer_key(key, base_dir=Path(mkdtemp()))
    assert regenerated.read_text(encoding="utf-8") == committed.read_text(encoding="utf-8")


def test_clo_cells_revert_without_the_key() -> None:
    """Delete the key and every CLO cell returns to its prior not-applicable state.

    The inverse of committing it, exercised through the real ``load_answer_key``
    against an empty directory rather than by injecting ``None`` — so the loader's
    own miss path is what produces the reversion, as it would on disk."""
    empty = Path(mkdtemp())
    m = build_quality_matrix(
        DEAL_REGISTRY,
        seed_loader=_load_cached_deal_model,
        answer_key_loader=lambda ctx: load_answer_key(ctx, base_dir=empty),
    )
    assert not any(d.has_answer_key for d in m.deals)
    for ck in _EXPECTED_CHECK_KEYS:
        cell = _cell(m, CLO_DEAL_ID, ck)
        assert cell.grade == GRADE_NOT_APPLICABLE
        assert "no committed answer key" in cell.reason.lower()


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


def _key_with_two_covenant_only_periods() -> DealAnswerKey:
    """Green Lion's all-PoP key plus two periods shaped like trustee-report ones."""
    key = _gl_key_from_report()
    covenant_only = [
        AnswerKeyPeriod(
            reporting_date=date,
            period_label=label,
            covenants=[
                CovenantResult(name="class_a_par_value_test", threshold=1.2, actual=1.4, passed=True)
            ],
        )
        for date, label in (("2023-12-31", "December 2023"), ("2025-06-30", "June 2025"))
    ]
    return key.model_copy(
        update={"periods": [covenant_only[0], *key.periods, covenant_only[1]]}
    )


def test_a_partly_pop_bearing_key_grades_its_pop_periods_and_reports_the_rest() -> None:
    """The scorecard's denominator is the graded periods, and it says so (#513).

    A key unioned from two document families (#495) carries a Priority of
    Payments for only some of its periods. The grade must be over those, and the
    others must be *reported* — the failure this guards is the tally silently
    counting all five as graded-and-passed, since a covenant-only period grades
    vacuously if it is graded at all.
    """
    m = build_quality_matrix(
        {GL_DEAL_ID: {"deal_name": GL_DEAL_NAME}},
        seed_loader=_load_cached_deal_model,
        answer_key_loader=_single_loader(GL_DEAL_NAME, _key_with_two_covenant_only_periods()),
    )
    rev = _cell(m, GL_DEAL_ID, "revenue_pop")

    assert rev.grade == GRADE_PASSED
    # Three graded, three passed — never five, which is what counting the
    # covenant-only periods as graded would produce.
    assert rev.evidence["periods_checked"] == 3
    assert rev.evidence["periods_passed"] == 3
    assert rev.evidence["periods_skipped"] == 2
    # And the operator-facing reason discloses it: "reconciled across 3 period(s)"
    # alone would be true of this five-period key and still misleading.
    assert "2 further period(s) of this key were not graded" in rev.reason
    assert "publish no Priority of Payments" in rev.reason


def test_the_skip_disclosure_is_absent_when_every_period_was_graded() -> None:
    """An all-PoP key discloses no skips — the clause fires only where it is true."""
    m = build_quality_matrix(
        {GL_DEAL_ID: {"deal_name": GL_DEAL_NAME}},
        seed_loader=_load_cached_deal_model,
        answer_key_loader=_single_loader(GL_DEAL_NAME, _gl_key_from_report()),
    )
    rev = _cell(m, GL_DEAL_ID, "revenue_pop")

    assert rev.evidence["periods_skipped"] == 0
    assert "not graded" not in rev.reason


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
    # Offline + the three committed answer keys: GL-2024-1 (#429) and GL-2023-1
    # (#440) pass both PoP checks; Cairn CLO XVII (#481) passes covenants. The
    # honest verdict is those five cells, nothing fails, the rest not-applicable.
    passed_cells = 2 * len(POP_GRADED_DEAL_IDS) + len(COVENANT_GRADED_DEAL_IDS)
    assert body["tally"][GRADE_PASSED] == passed_cells
    assert body["tally"].get(GRADE_FAILED, 0) == 0
    assert body["tally"][GRADE_NOT_APPLICABLE] == len(body["cells"]) - passed_cells
    # The graded CLO cell is reachable through the endpoint, with its count.
    clo = next(
        c for c in body["cells"] if c["deal_id"] == CLO_DEAL_ID and c["check_key"] == "covenants"
    )
    assert clo["grade"] == GRADE_PASSED
    assert clo["evidence"]["covenants_graded"] == CLO_COVENANTS_GRADED


def test_a_triggers_metric_name_cannot_displace_the_period_structure(monkeypatch) -> None:
    """A published value is offered under its trigger's metric name, and a metric
    is a free string — so ``pool_stats`` and ``reporting_date`` are writable
    names. They are written last on purpose.

    The damage a collision would do is not local: replacing ``pool_stats`` with
    a float makes ``_extract_metric``'s ``period.get("pool_stats", {})`` lookup
    raise for **every other** trigger, so one hostile metric name would take the
    whole deal's grading down. (A trigger genuinely named after a structural key
    still fails on its own — ``float()`` of a dict — which is pre-existing and
    honest; what must not happen is it corrupting its neighbours.)

    The period dict is captured at the monitor boundary rather than inferred
    from a grade: the invariant is about the dict's shape, so assert the shape.
    """
    from loanwhiz.primitives.covenant_monitor import CovenantMonitor  # noqa: PLC0415

    key = DealAnswerKey(
        deal_id="d",
        deal_name="D",
        periods=[
            AnswerKeyPeriod(
                reporting_date="2025-03-18",
                period_label="March 2025",
                covenants=[
                    CovenantResult(name="hostile_stats", threshold=1.0, actual=99.0, passed=True),
                    CovenantResult(name="hostile_date", threshold=1.0, actual=98.0, passed=True),
                    CovenantResult(name="neighbour", threshold=50.0, passed=True),
                ],
                pool_stats={"neighbour_ratio": 75.0},
            )
        ],
    )
    triggers = [
        _named_trigger("hostile_stats", metric="pool_stats", threshold=1.0),
        _named_trigger("hostile_date", metric="reporting_date", threshold=1.0),
        _named_trigger("neighbour", metric="neighbour_ratio", threshold=None),
    ]
    captured: list[dict] = []
    real_execute = CovenantMonitor.execute

    def spy(self, input):
        captured.extend(input.periods)
        return real_execute(self, input)

    monkeypatch.setattr(CovenantMonitor, "execute", spy)

    ctx = _DealGrading(
        deal_id="d",
        deal_ctx={"deal_name": "D"},
        model=None,
        answer_key=key,
        series=None,
        recon=None,
        fold_error=None,
    )
    # The hostile trigger may or may not fail on its own metric; that is not what
    # is under test and must not decide the outcome, so it is suppressed and the
    # captured period's SHAPE is the assertion.
    with suppress(Exception):
        _grade_covenants("covenants", ctx, triggers_loader=lambda _ctx: triggers)

    assert len(captured) == 1
    period = captured[0]
    assert period["reporting_date"] == "2025-03-18"
    assert period["pool_stats"] == {"neighbour_ratio": 75.0}
    # The neighbour's published pool statistic is still reachable at top level.
    assert period["neighbour_ratio"] == pytest.approx(75.0)
