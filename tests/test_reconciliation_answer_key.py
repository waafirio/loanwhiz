"""Tests for the per-deal ground-truth answer-key format (#427, epic #425).

These pin the *format + loader + reconciler-consume adapter* — the data-driven
generalization of the hand-built ``_VALIDATION_BUILDERS``. They run fully offline
(no network, no LLM): the round-trip + loader tests use synthetic keys, and the
end-to-end consume test reuses Green Lion 2024-1's *existing committed* report and
folded series to prove a config-loaded answer key reconciles identically to the
hand-built ``validate_green_lion_2024_1`` builder, to the cent.

Scope note (#427): this issue defines the format and proves it is consumable. It
deliberately does NOT commit a production deal's real answer key (that is the
backfill, #429) — every key here is built in-memory or written to a tmp dir.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from loanwhiz.config import DEAL_REGISTRY
from loanwhiz.primitives.base import Citation
from loanwhiz.primitives.covenant_monitor import TriggerDefinition
from loanwhiz.primitives.reconciler import (
    fold_green_lion_2024_1,
    load_green_lion_2024_1_report,
    validate_green_lion_2024_1,
)
from loanwhiz.primitives.reconciliation_answer_key import (
    ANSWER_KEY_FORMAT_VERSION,
    AnswerKeyPeriod,
    AnswerKeyPopStep,
    CovenantResult,
    DealAnswerKey,
    answer_key_path,
    load_answer_key,
    merge_answer_keys,
    published_metric_values,
    published_thresholds,
    quantify_triggers,
    reconcile_against_answer_key,
    write_answer_key,
)

GL_DEAL_ID = "green-lion-2024-1"
GL_DEAL_NAME = "Green Lion 2024-1 B.V."


def _synthetic_key(deal_id: str = "example-deal-2024-1") -> DealAnswerKey:
    """A small, fully-populated synthetic answer key (all three categories)."""
    return DealAnswerKey(
        deal_id=deal_id,
        deal_name="Example Deal 2024-1 B.V.",
        periods=[
            AnswerKeyPeriod(
                reporting_date="2025-09-30",
                period_label="September 2025",
                available_revenue_funds=1_000_000.0,
                available_principal_funds=5_000_000.0,
                revenue_pop=[
                    AnswerKeyPopStep(priority="(a)", amount=12_345.67, recipient="Senior expenses"),
                    AnswerKeyPopStep(priority="(b)", amount=50_000.0),
                ],
                redemption_pop=[
                    AnswerKeyPopStep(priority="(a)", amount=4_500_000.0, recipient="Class A"),
                ],
                covenants=[
                    CovenantResult(name="sequential_pay", threshold=1.5, actual=0.4, passed=True),
                ],
                pool_stats={"pool_balance_end": 95_000_000.0, "principal_collected": 5_000_000.0},
            )
        ],
    )


# ---------------------------------------------------------------------------
# Model defaults + JSON round-trip
# ---------------------------------------------------------------------------


def test_format_version_and_tolerance_defaults() -> None:
    key = _synthetic_key()
    assert key.format_version == ANSWER_KEY_FORMAT_VERSION == 1
    assert key.tolerance_eur == pytest.approx(0.01)


def test_json_round_trip_is_lossless() -> None:
    key = _synthetic_key()
    restored = DealAnswerKey.model_validate_json(key.model_dump_json())
    assert restored == key
    # covenants + pool stats survive the round-trip (typed + loadable now, graded by #428)
    period = restored.periods[0]
    assert period.covenants[0].name == "sequential_pay"
    assert period.pool_stats["pool_balance_end"] == pytest.approx(95_000_000.0)


# ---------------------------------------------------------------------------
# Bridge to / from the reconciler's NotesCashReport
# ---------------------------------------------------------------------------


def test_to_notes_cash_report_preserves_pop() -> None:
    key = _synthetic_key()
    report = key.to_notes_cash_report()
    assert report.deal_name == key.deal_name
    assert [p.reporting_date for p in report.periods] == ["2025-09-30"]
    period = report.periods[0]
    assert period.available_revenue_funds == pytest.approx(1_000_000.0)
    assert [(s.priority, s.amount) for s in period.revenue_pop] == [
        ("(a)", 12_345.67),
        ("(b)", 50_000.0),
    ]
    assert [(s.priority, s.amount) for s in period.redemption_pop] == [("(a)", 4_500_000.0)]


def test_from_notes_cash_report_captures_published_pop() -> None:
    """from_notes_cash_report faithfully captures the green-lion published PoP."""
    report = load_green_lion_2024_1_report()
    key = DealAnswerKey.from_notes_cash_report(report, deal_id=GL_DEAL_ID)

    assert key.deal_id == GL_DEAL_ID
    assert key.deal_name == report.deal_name
    assert len(key.periods) == len(report.periods)

    # The PoP projects back to the same report shape (priorities + amounts to the cent).
    rebuilt = key.to_notes_cash_report()
    assert rebuilt.reporting_dates == report.reporting_dates
    for src, out in zip(report.periods, rebuilt.periods):
        assert [(s.priority, s.amount) for s in out.revenue_pop] == [
            (s.priority, s.amount) for s in src.revenue_pop
        ]
        assert [(s.priority, s.amount) for s in out.redemption_pop] == [
            (s.priority, s.amount) for s in src.redemption_pop
        ]
        assert out.available_revenue_funds == src.available_revenue_funds
        assert out.available_principal_funds == src.available_principal_funds


# ---------------------------------------------------------------------------
# Loader — resolve by deal-name slug from the answer-key data dir
# ---------------------------------------------------------------------------


def test_answer_key_path_uses_deal_name_slug(tmp_path) -> None:
    path = answer_key_path(GL_DEAL_NAME, base_dir=tmp_path)
    assert path == tmp_path / "green-lion-2024-1-bv.json"


def test_load_answer_key_round_trips_through_disk(tmp_path) -> None:
    key = _synthetic_key()
    written = write_answer_key(key, base_dir=tmp_path)
    assert written.exists()

    # Loadable by the deal-context mapping shape the registry yields...
    loaded = load_answer_key({"deal_name": key.deal_name}, base_dir=tmp_path)
    assert loaded == key
    # ...and by a bare deal-name string.
    assert load_answer_key(key.deal_name, base_dir=tmp_path) == key


def test_load_answer_key_miss_returns_none(tmp_path) -> None:
    assert load_answer_key({"deal_name": "No Such Deal B.V."}, base_dir=tmp_path) is None


def test_load_answer_key_malformed_raises(tmp_path) -> None:
    # A present-but-corrupt key must fail loudly, never silently grade nothing.
    path = answer_key_path("Broken Deal B.V.", base_dir=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not valid json", encoding="utf-8")
    # pydantic's model_validate_json wraps the JSON error in ValidationError — the
    # point is it raises (fails loudly), never returns None / grades nothing.
    with pytest.raises(ValidationError):
        load_answer_key("Broken Deal B.V.", base_dir=tmp_path)


# ---------------------------------------------------------------------------
# End-to-end: a config-loaded answer key reconciles like the hand-built builder
# ---------------------------------------------------------------------------


def test_reconcile_against_answer_key_matches_hand_built_builder(tmp_path) -> None:
    """A GL-2024-1 answer key (authored from the committed report, persisted to a
    tmp dir, then loaded back) reconciles to the same PASS as the hand-built
    ``validate_green_lion_2024_1`` — proving the data-driven format is a faithful,
    consumable generalization of ``_VALIDATION_BUILDERS``.
    """
    # Author the key from the committed report, write + read it through disk so the
    # full loader path is exercised (not just the in-memory model).
    report = load_green_lion_2024_1_report()
    authored = DealAnswerKey.from_notes_cash_report(report, deal_id=GL_DEAL_ID)
    write_answer_key(authored, base_dir=tmp_path)
    key = load_answer_key(DEAL_REGISTRY[GL_DEAL_ID], base_dir=tmp_path)
    assert key is not None

    series, _ = fold_green_lion_2024_1()
    got = reconcile_against_answer_key(series, key)
    baseline = validate_green_lion_2024_1()

    # Same to-the-cent verdict and coverage as the hand-built builder.
    assert got.passed is True
    assert got.passed == baseline.passed
    assert got.periods_checked == baseline.periods_checked == 3
    assert got.periods_passed == baseline.periods_passed

    # Per-period revenue + redemption totals tie out to the hand-built proof.
    for got_p, base_p in zip(got.periods, baseline.periods):
        assert got_p.reporting_date == base_p.reporting_date
        assert got_p.revenue.engine_total == pytest.approx(base_p.revenue.engine_total)
        assert got_p.redemption.engine_total == pytest.approx(base_p.redemption.engine_total)
        assert got_p.revenue.report_total == pytest.approx(base_p.revenue.report_total)
        assert got_p.redemption.report_total == pytest.approx(base_p.redemption.report_total)


def test_reconcile_against_answer_key_honors_tolerance_override(tmp_path) -> None:
    report = load_green_lion_2024_1_report()
    key = DealAnswerKey.from_notes_cash_report(report, deal_id=GL_DEAL_ID)
    series, _ = fold_green_lion_2024_1()
    # An impossibly tight (negative) tolerance must fail every step — proves the
    # override is threaded through to reconcile_series, not ignored.
    got = reconcile_against_answer_key(series, key, tolerance=-1.0)
    assert got.passed is False


# ---------------------------------------------------------------------------
# The trustee-report constructor and the published-threshold seam (#481)
# ---------------------------------------------------------------------------
#
# The sibling constructor a deal earns a key through when its investor reporting
# is a monthly trustee report rather than a Notes & Cash report: it publishes no
# Priority of Payments, but it does state each coverage test's ratio, required
# level and outcome. These pin the constructor's refusals and the one direction
# a published figure is allowed to travel — into the engine, never out of it.


def _clo_source():
    """The committed CLO key's authoring path (see ``tests/clo_answer_key_source.py``)."""
    from clo_answer_key_source import (  # noqa: PLC0415
        CLO_DEAL_ID,
        CLO_DEAL_NAME,
        clo_key_from_reports,
        clo_report_summaries,
    )

    return CLO_DEAL_ID, CLO_DEAL_NAME, clo_key_from_reports, clo_report_summaries


def _trigger(name: str, *, threshold: float | None) -> TriggerDefinition:
    return TriggerDefinition(
        name=name,
        description=name,
        metric=f"{name}_metric",
        threshold=threshold,
        direction="below",
        consequence="n/a",
        citation=Citation(document="test", locator="n/a", excerpt="n/a"),
    )


def test_trustee_constructor_reads_only_the_report() -> None:
    """Every figure in the key traces to a stated figure in the report."""
    _, _, key_from_reports, summaries_of = _clo_source()
    key, summaries = key_from_reports(), summaries_of()
    assert len(key.periods) == len(summaries)
    for period, summary in zip(key.periods, summaries, strict=True):
        assert period.period_label == summary.period_label
        stated = {t.trigger_key: t for t in summary.coverage_tests}
        for covenant in period.covenants:
            source = stated[covenant.name]
            assert covenant.threshold == pytest.approx(float(source.required_pct))
            assert covenant.actual == pytest.approx(float(source.current_pct))
            assert covenant.passed is (source.result.value == "Passed")
            assert source.stated_in in (covenant.note or "")


def test_trustee_constructor_excludes_a_published_not_applicable_result() -> None:
    """``N/A`` is dropped, never coerced — ``passed: bool`` cannot express it.

    Both directions, because the one-way version passes by finding nothing: the
    report really does state a ``N/A`` test (Class F, every period), and it
    really is absent from the key while its pass/fail siblings are present.
    """
    _, _, key_from_reports, summaries_of = _clo_source()
    key, summaries = key_from_reports(), summaries_of()
    for period, summary in zip(key.periods, summaries, strict=True):
        not_applicable = {
            t.trigger_key for t in summary.coverage_tests if t.result.value == "N/A"
        }
        decided = {t.trigger_key for t in summary.coverage_tests if t.result.value != "N/A"}
        assert not_applicable, "fixture no longer states an N/A test — this guard is vacuous"
        names = {c.name for c in period.covenants}
        assert names == decided
        assert not names & not_applicable


def test_trustee_constructor_publishes_no_pop_or_pool_stats() -> None:
    """A trustee report states no Priority of Payments, so the key claims none."""
    _, _, key_from_reports, _ = _clo_source()
    for period in key_from_reports().periods:
        assert period.revenue_pop == []
        assert period.redemption_pop == []
        assert period.pool_stats == {}
        assert period.available_revenue_funds is None
        assert period.available_principal_funds is None


def test_trustee_constructor_refuses_an_unreconciled_summary() -> None:
    """A summary that never met the parser's acceptance oracle is not ground truth."""
    _, deal_name, _, summaries_of = _clo_source()
    summaries = summaries_of()
    summaries[0] = summaries[0].model_copy(update={"reconciled": False})
    with pytest.raises(ValueError, match="not reconciled"):
        DealAnswerKey.from_trustee_liability_summaries(
            summaries, deal_id="cairn-clo-xvii", deal_name=deal_name
        )


def test_trustee_constructor_refuses_another_deals_report() -> None:
    """The guard against authoring one deal's key from another deal's document."""
    _, deal_name, _, summaries_of = _clo_source()
    summaries = summaries_of()
    with pytest.raises(ValueError, match="refusing to author"):
        DealAnswerKey.from_trustee_liability_summaries(
            summaries, deal_id="green-lion-2024-1", deal_name="Green Lion 2024-1 B.V."
        )
    assert summaries[0].deal_name == deal_name  # the fixture really does name the CLO


def test_trustee_constructor_refuses_an_undated_or_unparseable_period() -> None:
    """A period key in the wrong shape matches nothing and reads as a grading verdict."""
    _, deal_name, _, summaries_of = _clo_source()
    for bad in (None, "2025-03-18"):
        summaries = summaries_of()
        summaries[0] = summaries[0].model_copy(update={"reporting_date": bad})
        with pytest.raises(ValueError):
            DealAnswerKey.from_trustee_liability_summaries(
                summaries, deal_id="cairn-clo-xvii", deal_name=deal_name
            )


def test_published_thresholds_stays_silent_when_periods_disagree() -> None:
    """Two published levels for one test is not a level — say nothing, grade nothing."""
    key = DealAnswerKey(
        deal_id="d",
        deal_name="D",
        periods=[
            AnswerKeyPeriod(
                reporting_date="2025-01-31",
                period_label="January 2025",
                covenants=[CovenantResult(name="steady", threshold=100.0, passed=True),
                           CovenantResult(name="moved", threshold=100.0, passed=True)],
            ),
            AnswerKeyPeriod(
                reporting_date="2025-02-28",
                period_label="February 2025",
                covenants=[CovenantResult(name="steady", threshold=100.0, passed=True),
                           CovenantResult(name="moved", threshold=105.0, passed=True)],
            ),
        ],
    )
    assert published_thresholds(key) == {"steady": 100.0}


def test_quantify_triggers_fills_an_absent_threshold_but_never_overrides_one() -> None:
    """The whole contract, both halves, in one place.

    A published level may quantify a trigger the prospectus left open; it must
    never redefine a level the deal already declares, because ground truth that
    can rewrite the test it grades is not grading anything.
    """
    key = DealAnswerKey(
        deal_id="d",
        deal_name="D",
        periods=[
            AnswerKeyPeriod(
                reporting_date="2025-01-31",
                period_label="January 2025",
                covenants=[
                    CovenantResult(name="open", threshold=130.08, passed=True),
                    CovenantResult(name="declared", threshold=999.0, passed=True),
                    CovenantResult(name="unpublished", passed=True),
                ],
            )
        ],
    )
    triggers = [
        _trigger("open", threshold=None),
        _trigger("declared", threshold=1.5),
        _trigger("unpublished", threshold=None),
        _trigger("absent_from_key", threshold=None),
    ]
    got = quantify_triggers(triggers, key)
    assert [t.name for t in got] == [t.name for t in triggers]
    by_name = {t.name: t for t in got}
    assert by_name["open"].threshold == pytest.approx(130.08)
    assert by_name["declared"].threshold == pytest.approx(1.5)
    assert by_name["unpublished"].threshold is None
    assert by_name["absent_from_key"].threshold is None
    # Nothing but the threshold moves, and the untouched entries are the very
    # objects passed in — so no field can be quietly rewritten alongside.
    assert by_name["declared"] is triggers[1]
    assert by_name["open"].metric == triggers[0].metric
    assert by_name["open"].direction == triggers[0].direction


def test_published_metric_values_stays_silent_when_two_triggers_disagree() -> None:
    """Two triggers may share a metric; two different published values for it is
    ambiguity, and ambiguity resolved by picking one is a wrong number reported
    three layers downstream as a grading verdict."""
    period = AnswerKeyPeriod(
        reporting_date="2025-03-18",
        period_label="March 2025",
        covenants=[
            CovenantResult(name="agree_a", actual=108.68, passed=True),
            CovenantResult(name="agree_b", actual=108.68, passed=True),
            CovenantResult(name="clash_a", actual=100.0, passed=True),
            CovenantResult(name="clash_b", actual=101.0, passed=True),
            CovenantResult(name="no_actual", passed=True),
            CovenantResult(name="not_a_trigger", actual=7.0, passed=True),
        ],
    )
    metric_by_name = {
        "agree_a": "shared_ratio",
        "agree_b": "shared_ratio",
        "clash_a": "contested_ratio",
        "clash_b": "contested_ratio",
        "no_actual": "silent_ratio",
    }
    assert published_metric_values(period, metric_by_name) == {"shared_ratio": 108.68}


# ---------------------------------------------------------------------------
# The third constructor — a CLO's Note Valuation Report (#495)
# ---------------------------------------------------------------------------


def _nvr_source():
    """The committed CLO key's Note-Valuation authoring path."""
    from clo_answer_key_source import (  # noqa: PLC0415
        CLO_DEAL_ID,
        CLO_DEAL_NAME,
        clo_key_from_note_valuation,
        clo_note_valuation_report,
    )

    return CLO_DEAL_ID, CLO_DEAL_NAME, clo_key_from_note_valuation, clo_note_valuation_report


def test_note_valuation_constructor_carries_the_report_it_read() -> None:
    """Every figure in the PoP key traces to the parsed report, step for step.

    The constructor's own faithfulness check, distinct from the committed key's
    byte-for-byte regeneration: this compares the key against the *parse*, so a
    constructor that dropped, reordered or rounded a step reds here with the step
    named, rather than as an opaque byte diff.
    """
    _, _, key_from_nvr, report_of = _nvr_source()
    key, report = key_from_nvr(), report_of()

    assert len(key.periods) == len(report.periods)
    for period, source in zip(key.periods, report.periods, strict=True):
        assert period.reporting_date == source.reporting_date
        assert period.period_label == source.period_label
        assert period.available_revenue_funds == source.available_revenue_funds
        assert period.available_principal_funds == source.available_principal_funds
        for side, published in (
            (period.revenue_pop, source.revenue_pop),
            (period.redemption_pop, source.redemption_pop),
        ):
            assert published, "the fixture parsed no steps — this guard is vacuous"
            assert [(s.priority, s.amount, s.recipient) for s in side] == [
                (s.priority, s.amount, s.recipient) for s in published
            ]


def test_note_valuation_constructor_records_rates_and_excludes_the_class_without_one() -> None:
    """A published rate is recorded; a class the report states none for is absent.

    #481's rule on the other constructor, applied here: the Subordinated Notes
    have no coupon, so ``interest_rate_applied`` is ``None`` — which means the
    report prints no rate, never "zero per cent". Writing ``0.0`` would publish a
    figure the document does not state, and it would grade as a real rate.
    """
    _, _, key_from_nvr, report_of = _nvr_source()
    key, report = key_from_nvr(), report_of()

    for period, source in zip(key.periods, report.periods, strict=True):
        published = {
            b.note_class: b.interest_rate_applied
            for b in source.note_balances
            if b.interest_rate_applied is not None
        }
        omitted = {
            b.note_class for b in source.note_balances if b.interest_rate_applied is None
        }
        assert omitted, "the fixture states a rate for every class — guard is vacuous"
        assert period.pool_stats == {
            f"applied_rate_{cls}": rate for cls, rate in published.items()
        }
        for cls in omitted:
            assert f"applied_rate_{cls}" not in period.pool_stats


def test_note_valuation_constructor_publishes_no_covenants() -> None:
    """A Note Valuation Report states no coverage test, so the key claims none.

    The mirror of ``test_trustee_constructor_publishes_no_pop_or_pool_stats``:
    each document contributes only what it states, which is what keeps the merged
    key from implying either document said more than it did.
    """
    _, _, key_from_nvr, _ = _nvr_source()
    for period in key_from_nvr().periods:
        assert period.covenants == []


def test_note_valuation_constructor_refuses_a_period_that_never_tied_to_its_funds() -> None:
    """Available funds are the parser's tie-out; without them the steps are unverified.

    ``note_valuation_parser`` ties each waterfall's step sum to the report's own
    stated available funds and refuses on a divergence. A period reaching here
    with no stated funds never passed that oracle, so its steps are a regex result
    rather than ground truth — and this constructor refuses rather than routing
    around a refusal the parser already made.
    """
    _, deal_name, _, report_of = _nvr_source()
    report = report_of()
    report.periods[0] = report.periods[0].model_copy(
        update={"available_revenue_funds": None}
    )
    with pytest.raises(ValueError, match="never tied|no available funds"):
        DealAnswerKey.from_note_valuation_report(
            report, deal_id="cairn-clo-xvii", deal_name=deal_name
        )


def test_note_valuation_constructor_refuses_a_period_with_no_steps() -> None:
    """A period with neither waterfall parsed carries no Priority of Payments.

    Committing it would put a period in the key that claims a PoP section it does
    not have — the overclaim ``capability_matrix._has_pop_section`` reads, and the
    exact shape a silently-renamed report section would produce (#480's lesson).
    """
    _, deal_name, _, report_of = _nvr_source()
    report = report_of()
    report.periods[0] = report.periods[0].model_copy(
        update={"revenue_pop": [], "redemption_pop": []}
    )
    with pytest.raises(ValueError, match="no Priority-of-Payments steps"):
        DealAnswerKey.from_note_valuation_report(
            report, deal_id="cairn-clo-xvii", deal_name=deal_name
        )


def test_note_valuation_constructor_refuses_another_deals_report() -> None:
    """The deal name checked is the one the parser read off the report's own page.

    Not the argument echoed back: a guard comparing the caller's ``deal_name`` to
    itself would pass for any document. This is what stops one deal's published
    waterfall being committed under another's slug.
    """
    _, deal_name, _, report_of = _nvr_source()
    report = report_of()
    assert report.periods[0].deal_name == deal_name, "the parse read no deal name"
    with pytest.raises(ValueError, match="refusing to author"):
        DealAnswerKey.from_note_valuation_report(
            report, deal_id="green-lion-2024-1", deal_name=GL_DEAL_NAME
        )


# ---------------------------------------------------------------------------
# merge_answer_keys — one deal, two published documents, one committed key
# ---------------------------------------------------------------------------


def _key_with(*dates: str, **overrides) -> DealAnswerKey:
    """A minimal key for a fixed deal, carrying one period per date."""
    fields = {
        "deal_id": "cairn-clo-xvii",
        "deal_name": "Cairn CLO XVII DAC",
        "periods": [
            AnswerKeyPeriod(reporting_date=d, period_label=d) for d in dates
        ],
        **overrides,
    }
    return DealAnswerKey(**fields)


def test_merge_unions_periods_in_reporting_date_order() -> None:
    """The union is ordered by date, not by argument order.

    The committed file must be a function of the documents rather than of the
    order a caller happened to pass them, or the byte-for-byte regeneration
    regression would red on a harmless reordering and pass on a real change to
    which documents were read.
    """
    merged = merge_answer_keys(
        _key_with("2025-03-18", "2024-12-16"), _key_with("2025-01-08")
    )
    assert [p.reporting_date for p in merged.periods] == [
        "2024-12-16",
        "2025-01-08",
        "2025-03-18",
    ]
    assert merged.deal_id == "cairn-clo-xvii"
    assert merged.deal_name == "Cairn CLO XVII DAC"


def test_merge_keeps_each_periods_own_content() -> None:
    """A merged period is the one its document produced, not a blend of both."""
    covenants = _key_with("2024-12-16")
    covenants.periods[0].covenants = [CovenantResult(name="par_value", passed=True)]
    pop = _key_with("2025-01-08")
    pop.periods[0].revenue_pop = [AnswerKeyPopStep(priority="(A)", amount=1.0)]

    merged = merge_answer_keys(covenants, pop)
    by_date = {p.reporting_date: p for p in merged.periods}
    assert [c.name for c in by_date["2024-12-16"].covenants] == ["par_value"]
    assert by_date["2024-12-16"].revenue_pop == []
    assert by_date["2025-01-08"].covenants == []
    assert [s.priority for s in by_date["2025-01-08"].revenue_pop] == ["(A)"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"deal_id": "green-lion-2024-1"},
        {"deal_name": "Green Lion 2024-1 B.V."},
        {"tolerance_eur": 1.0},
        {"format_version": ANSWER_KEY_FORMAT_VERSION + 1},
    ],
)
def test_merge_refuses_keys_that_disagree_about_what_they_describe(overrides) -> None:
    """Two keys that disagree about identity or tolerance are not one deal's key.

    Filing one deal's ground truth under another's identity is the damage on the
    first two; on ``tolerance_eur`` it is quieter and worse — silently adopting
    one of two disagreeing values as the gate every step is then graded to.
    """
    with pytest.raises(ValueError, match="disagreeing on"):
        merge_answer_keys(_key_with("2024-12-16"), _key_with("2025-01-08", **overrides))


def test_merge_refuses_two_documents_claiming_one_period() -> None:
    """Two keys carrying one reporting date is a reconciliation, not a union.

    Nothing here can decide which document's figure for a shared period is right,
    and picking one would publish a figure as ground truth on no authority. Cairn's
    two document sets are disjoint by date today, so this refusal never fires — it
    exists so a later filing that overlaps fails loudly instead of quietly
    preferring whichever key was passed first.
    """
    with pytest.raises(ValueError, match="both carry reporting date"):
        merge_answer_keys(_key_with("2025-01-08"), _key_with("2025-01-08"))


def test_merge_of_a_single_key_returns_that_key_sorted() -> None:
    """The degenerate case is the identity, so a one-document deal needs no branch."""
    merged = merge_answer_keys(_key_with("2025-01-08", "2024-12-16"))
    assert [p.reporting_date for p in merged.periods] == ["2024-12-16", "2025-01-08"]


def test_merge_refuses_no_keys_at_all() -> None:
    """An empty merge would return nothing to commit, not an empty key."""
    with pytest.raises(ValueError, match="at least one key"):
        merge_answer_keys()
