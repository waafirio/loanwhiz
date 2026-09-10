"""Grading what the CLO's three live screens actually render (#525).

``tests/test_clo_deal_registration.py`` pins that
``/deal/cairn-clo-xvii/compliance``, ``/deal/cairn-clo-xvii/waterfall`` and
``/compare`` **serve** the deal rather than the labelled 422 they used to
(#523/#524). Its own docstring defers the next question here: *what each
rendered figure is worth*. A populated screen carrying a wrong number is worse
than a 422, so a status code verifies nothing on its own.

Three things are pinned, and the second is the one that matters:

* **The cascade is graded against the published document.** Every step the
  waterfall screen renders is compared to the Note Valuation Report's own
  published rows, folded onto the cascade's labels by the repo's own join
  (:func:`~loanwhiz.primitives.report_label_fold.fold_report_pop`, #514) — not
  a second join written here, which would grade the engine against a
  re-implementation of itself.

* **The source split, measured from the live path.** 26 of the 29 rendered
  revenue steps are handed the report's own figure as an override and hand it
  back; 3 are computed from the deal model with no report input. So 26 of the
  29 agreements are true *by construction* — the document reconciling with
  itself — and only 3 are evidence about the engine. That distinction is the
  whole finding, and it is asserted from ``PeriodInputs.revenue_step_sources``
  and the override map rather than from ``ENGINE_COMPUTED_RECIPIENTS``: the
  allowlist is an authored *declaration* that a recipient is computable
  (#515's "declaration not data"), while the override's presence or absence is
  what the fold actually did.

* **What the 200s still hide.** The compliance screen evaluates no trigger at
  all, and the two screens report on periods that do not overlap. Both are
  recorded here as measured, and neither is tuned away.

Nothing in this module changes an engine, seed, answer key or endpoint; it is
measurement over the committed offline fixtures.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from loanwhiz.api.main import DEAL_REGISTRY, _load_cached_deal_model, app
from loanwhiz.primitives.report_adapter import ReportAdapter
from loanwhiz.primitives.report_label_fold import fold_report_pop

CLO_DEAL_ID = "cairn-clo-xvii"
#: The one period the Note Valuation Report publishes a Priority of Payments for.
POP_PERIOD = "2025-01-08"
#: The report's stated available revenue for that period — the pot the cascade
#: distributes. It is the report's figure, not the engine's.
PUBLISHED_AVAILABLE_REVENUE = 7_255_062.35
#: The key's own tolerance for a to-the-cent comparison.
TOLERANCE_EUR = 0.01

#: The revenue steps the fold computes from the deal model itself, and the figure
#: each reproduces from the published report with no report input on the engine's
#: side (#511/#512/#528/#538/#539/#598). These are the only non-circular
#: comparisons the waterfall screen supports.
#:
#: Six since #598 — every interest-bearing class the deal issues. The last three
#: were computing all along and grading ``report-supplied`` because membership was
#: an authored list that ended at ``class_c_interest``.
ENGINE_COMPUTED_STEPS = {
    "(G)": ("class_a_notes_interest", 3_277_457.78),
    "(H)": ("class_b_notes_interest", 644_398.50),
    "(J)": ("class_c_notes_interest", 415_004.33),
    "(M)": ("class_d_notes_interest", 594_969.17),
    "(P)": ("class_e_notes_interest", 484_208.67),
    "(S)": ("class_f_notes_interest", 495_004.89),
}


def _adapter_and_published_period():
    """The report adapter and the one period the CLO publishes a PoP for.

    Resolved exactly as ``_reconstruct_series_from_reports`` resolves them, so
    the fixtures below read the same inputs the live path folded rather than a
    parallel reconstruction of them.
    """
    from loanwhiz.api.main import REPORT_EXTRACTION_CACHE_DIR
    from loanwhiz.primitives.report_extractor import resolve_parsed_report

    deal = DEAL_REGISTRY[CLO_DEAL_ID]
    model = _load_cached_deal_model(deal)
    report = resolve_parsed_report(
        CLO_DEAL_ID, deal, cache_dir=REPORT_EXTRACTION_CACHE_DIR
    ).to_notes_cash_report()
    (period,) = [p for p in report.periods if p.reporting_date == POP_PERIOD]
    return ReportAdapter.from_deal_model(model), period


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def rendered_waterfall(client: TestClient) -> dict:
    """What ``/deal/cairn-clo-xvii/waterfall`` actually serves."""
    response = client.get(f"/deal/{CLO_DEAL_ID}/waterfall")
    assert response.status_code == 200
    return response.json()


@pytest.fixture(scope="module")
def published_revenue() -> dict[str, float]:
    """The report's published revenue rows, folded onto the cascade's labels.

    The fold is the repo's own (#514), given the same two inputs the engine's
    report path gives it: the report's rows in printed order and the extracted
    cascade's labels in cascade order.
    """
    adapter, period = _adapter_and_published_period()
    # The adapter's own step list is the fold's second input on the live path.
    labels = [str(step.get("priority", "")) for step in adapter.revenue_steps]
    return dict(fold_report_pop(period.revenue_pop, labels).amounts)


@pytest.fixture(scope="module")
def live_step_sources() -> tuple[dict[str, str], dict[str, float]]:
    """``(source, override)`` per revenue label, as the live fold received them.

    This is the engine's *input* side — what the report path handed ``run_period``
    — which is what makes it evidence rather than a restatement of the output.
    """
    adapter, period = _adapter_and_published_period()
    inputs = adapter.period_inputs(period)
    sources = dict(inputs.revenue_step_sources or inputs.step_sources or {})
    overrides = dict(
        getattr(inputs, "revenue_step_overrides", None) or inputs.step_overrides or {}
    )
    return sources, overrides


# ---------------------------------------------------------------------------
# The waterfall screen — graded against the published report.
# ---------------------------------------------------------------------------


def test_waterfall_screen_renders_the_published_period_and_pot(
    rendered_waterfall: dict,
) -> None:
    """The pot the screen distributes is the report's figure, not the engine's.

    Worth stating plainly because every downstream agreement inherits it: the
    cascade is folded over a total the document supplied, so a tie-out between
    distributed and available cannot be evidence that the engine sized the pot.
    """
    assert rendered_waterfall["reporting_period"] == POP_PERIOD
    assert rendered_waterfall["available_revenue_funds"] == pytest.approx(
        PUBLISHED_AVAILABLE_REVENUE, abs=TOLERANCE_EUR
    )
    # The Principal cascade runs on nothing, so reproducing it proves nothing.
    assert rendered_waterfall["available_principal_funds"] == 0.0


def test_every_rendered_revenue_step_agrees_with_the_published_report(
    rendered_waterfall: dict, published_revenue: dict[str, float]
) -> None:
    """Each step the screen renders matches the document's own published rows.

    The assertion is per-step, not on the total. An aggregate tie-out is blind
    here: the pot is fixed and the ``(CC)`` residual sweep absorbs any senior
    step's over-draw, so two steps wrong by equal and opposite amounts still
    sum correctly (#538). The per-step deltas are the signal.

    Read this with the source split below: for the 26 report-supplied steps the
    agreement is true by construction and carries no information about the
    engine.
    """
    steps = rendered_waterfall["revenue_waterfall"]
    assert steps, "the CLO waterfall screen rendered no steps"

    disagreements = []
    for step in steps:
        label = step["priority"]
        assert label in published_revenue, (
            f"step {label} ({step['recipient']}) joined no published row — "
            "every rendered step must be gradeable against the document"
        )
        delta = step["amount_distributed"] - published_revenue[label]
        if abs(delta) > TOLERANCE_EUR:
            disagreements.append((label, step["recipient"], delta))

    assert not disagreements, (
        "rendered figures disagree with the published report: " f"{disagreements}"
    )


def test_the_report_leaves_no_published_row_off_the_screen(
    rendered_waterfall: dict, published_revenue: dict[str, float]
) -> None:
    """Agreement on what is shown is worth little if rows are quietly dropped.

    The complement of the previous test: a screen could agree on every step it
    renders while omitting published rows entirely. Every label the fold placed
    must reach a rendered step.
    """
    rendered = {step["priority"] for step in rendered_waterfall["revenue_waterfall"]}
    assert not set(published_revenue) - rendered


# ---------------------------------------------------------------------------
# The source split — which figures the engine computed, as a figure.
# ---------------------------------------------------------------------------


def test_source_split(
    rendered_waterfall: dict,
    live_step_sources: tuple[dict[str, str], dict[str, float]],
) -> None:
    """6 of the 29 rendered revenue steps are computed; 23 are report-supplied.

    The override map is the evidence. A ``reported`` step arrives with the
    document's own figure attached, so the engine returns what it was given; an
    ``engine`` step arrives with none and the fold must derive the amount from
    tranche size, applied rate and a day count. Asserting the *absence* of an
    override on the six is what makes this a claim about the engine rather
    than about a naming table.

    The tally is asserted both ways round on purpose. A check that only looked
    for missing overrides would read the same on an empty input as on a correct
    one — "nothing to find" and "I cannot see" would be one output (#494).

    **If this count moves, that is the event, not the breakage.** It rose from 3
    to 6 in #598 — real progress, the declaration catching up with what the engine
    already computed — and a fall is a regression to the circularity this guards;
    either way the figure is published in ``docs/data-card.md``'s Cairn row and
    in ``README.md``, so move those with it rather than bumping the number here
    to restore green.
    """
    sources, overrides = live_step_sources
    rendered = {step["priority"] for step in rendered_waterfall["revenue_waterfall"]}

    engine = {label for label, src in sources.items() if src == "engine"}
    reported = {label for label, src in sources.items() if src == "reported"}

    # The split, as a figure — and non-vacuous: the two classes must partition
    # a non-empty set that is exactly what the screen renders.
    assert sources, "the live path supplied no step sources at all"
    assert engine | reported == rendered
    assert len(engine) == 6
    assert len(reported) == 23

    # Report-supplied steps carry the document's figure; computed steps do not.
    assert engine == set(ENGINE_COMPUTED_STEPS)
    for label in engine:
        assert label not in overrides, (
            f"{label} is declared engine-computed but was handed a report "
            "override — its agreement with the document would be circular"
        )
    for label in reported:
        assert label in overrides


def test_the_computed_steps_reproduce_their_published_figures(
    rendered_waterfall: dict,
    live_step_sources: tuple[dict[str, str], dict[str, float]],
    published_revenue: dict[str, float],
) -> None:
    """The only non-circular comparison the waterfall screen supports.

    These six are derived from the deal model — each class's size, its published
    applied rate (#512) and a day count measured between two stated Payment Dates
    on the basis its own Condition states (#528/#539) — with no report figure on
    the engine's side. That they land on the document's published amounts is
    therefore evidence; the other 23 agreements are not.
    """
    _, overrides = live_step_sources
    rendered = {s["priority"]: s for s in rendered_waterfall["revenue_waterfall"]}

    for label, (recipient, published_amount) in ENGINE_COMPUTED_STEPS.items():
        step = rendered[label]
        assert step["recipient"] == recipient
        assert label not in overrides
        # Agrees with the document...
        assert step["amount_distributed"] == pytest.approx(
            published_amount, abs=TOLERANCE_EUR
        )
        # ...and the document is what the fold independently placed there.
        assert published_revenue[label] == pytest.approx(
            published_amount, abs=TOLERANCE_EUR
        )


# ---------------------------------------------------------------------------
# What the 200s still hide.
# ---------------------------------------------------------------------------


def test_compliance_screen_serves_but_evaluates_no_trigger(client: TestClient) -> None:
    """The compliance screen renders its ratios but still evaluates no trigger.

    The *cause* has moved twice. It was the numerator: the seed's pool balance
    was the note total, so every ratio was notes over notes (#549 refused the
    value as well as the verdict). #550 made the numerator real. Then it was the
    pairing: the periods came from the trustee-report tapes while the state came
    from the January report, so no period had a state of its own date and the
    figure was withheld rather than rendered under a date it is not stated as of.

    #583 fixed the pairing, and the five par value ratios now render. What is
    left is the last honest gap: none of these coverage tests carries a
    quantified threshold, so there is nothing to compare the ratio against. A
    rendered metric beside ``evaluable: false`` and a stated cause is the right
    shape for that — the ratio is a measurement, the verdict is not available.
    """
    body = client.get(f"/deal/{CLO_DEAL_ID}/compliance").json()
    statuses = body["trigger_statuses"]

    assert statuses, "the compliance screen rendered no trigger statuses"
    assert not [s for s in statuses if s["evaluable"]]
    assert not [s for s in statuses if s["threshold"] is not None]
    # The par value tests now carry a real, own-dated measurement.
    assert [s for s in statuses if s["metric_value"] is not None]
    # A refusal must say why; a silent null is the thing this guards against.
    assert all(s["not_evaluable_reason"] for s in statuses)
    assert not body["active_triggers"] and not body["near_miss_triggers"]
    assert body["unevaluable_triggers"]


def test_the_two_screens_now_report_on_the_same_period(
    client: TestClient, rendered_waterfall: dict
) -> None:
    """Cairn's two screens describe the same point in the deal's life.

    They did not. The waterfall folded the Note Valuation Report's single
    published period while compliance ran over the trustee reports' periods, so
    two screens sitting side by side in one UI described different dates — the
    #524 disjointness, one layer out, recorded here rather than corrected.

    #583 corrected it at the source named in #524's own contract: the periods it
    sets aside "stop being the *ledger* /waterfall and /compliance fold", and
    compliance was still folding them. With them dropped, both screens fold the
    January report and agree on its date.
    """
    compliance_periods = {
        s["period"] for s in client.get(f"/deal/{CLO_DEAL_ID}/compliance").json()["trigger_statuses"]
    }
    assert compliance_periods == {rendered_waterfall["reporting_period"]}


def test_compare_renders_the_clo_but_shares_no_period_with_a_comp(
    client: TestClient,
) -> None:
    """The CLO is in the panel; the panel still has no common basis to compare on.

    ``performance_series`` carrying the deal is the improvement #524 delivered.
    It is not the same thing as a comparison: the CLO's one published period
    coincides with no comp's, so ``common_periods`` is empty and the deal's
    series is flat across the single date it has.
    """
    body = client.get(
        "/compare", params={"deals": f"{CLO_DEAL_ID},green-lion-2024-1"}
    ).json()

    series = {s["deal_id"]: s for s in body["performance_series"]}
    assert CLO_DEAL_ID in series
    assert {p["reporting_date"] for p in series[CLO_DEAL_ID]["points"]} == {POP_PERIOD}
    assert body["common_periods"] == []


# ---------------------------------------------------------------------------
# The structural panel — the "46 blank rows" count is not a CLO property.
# ---------------------------------------------------------------------------


def _cell(row: dict, deal_id: str) -> dict | None:
    return next((c for c in row["cells"] if c["deal_id"] == deal_id), None)


def test_the_clo_is_present_on_most_structural_rows(client: TestClient) -> None:
    """The CLO renders more structure than the validated deal it sits beside.

    #522 recorded 46 of 55 structural rows as "blank" for this deal and expected
    the coupon work to fill them. Measured against the panel, the CLO holds a
    *present* cell on the large majority of rows, and carries strictly more of
    them than Green Lion — 8 tranches to 3, and its full 29-step cascade. The
    rows where it is genuinely absent are RMBS-shaped concepts a CLO does not
    have (a PDL cure, a reserve replenishment, a residual certificate).
    """
    rows = client.get(
        "/compare", params={"deals": f"{CLO_DEAL_ID},green-lion-2024-1"}
    ).json()["structural_rows"]

    present = [r for r in rows if (_cell(r, CLO_DEAL_ID) or {}).get("present")]
    absent = [r for r in rows if not (_cell(r, CLO_DEAL_ID) or {}).get("present")]

    assert len(present) > len(absent) * 3, (
        f"the CLO is present on only {len(present)} of {len(rows)} structural rows"
    )
    # It is the richer column, not the degraded one.
    gl_present = [r for r in rows if (_cell(r, "green-lion-2024-1") or {}).get("present")]
    assert len(present) > len(gl_present)


@pytest.mark.parametrize(
    "deals",
    [
        f"{CLO_DEAL_ID},green-lion-2024-1",
        "green-lion-2024-1,green-lion-2023-1,leone-arancio-2023-1",
        # Both CLOs together (#535). The second deal does not change the
        # property: a cascade row has no comparable scalar for any deal, so
        # neither CLO's nulls can be read as a defect of the CLO support.
        f"{CLO_DEAL_ID},contego-clo-xi",
    ],
)
def test_no_waterfall_row_carries_a_numeric_value_for_any_deal(
    client: TestClient, deals: str
) -> None:
    """A null ``value`` on a cascade row is the row's shape, not a missing figure.

    This is what "46 of 55 rows blank" was counting, and it is not a property of
    the CLO: ``StructuralCell.value`` is a scalar for *comparing deals*, and a
    waterfall step has none, so the field is null on every waterfall row for
    every deal — including both externally validated Dutch RMBS, which is why
    the second parametrisation carries no CLO at all. The rows are not empty;
    they render the step's priority letter and its basis.

    Scoped to waterfall rows on purpose. Trigger rows are **not** deal-
    independent — a trigger stated as a number (Green Lion's PDL trigger,
    "> 0 EUR") does carry a value where a qualitative one does not — so
    asserting the same of them would be false. They are still swept in below
    for the labelling check, which does hold for both.

    Per-period amounts are a different surface, and the waterfall screen graded
    above is where they live.
    """
    rows = client.get("/compare", params={"deals": deals}).json()["structural_rows"]
    cascade_rows = [
        r for r in rows if r["section"].startswith("waterfall:") or r["section"] == "trigger"
    ]
    assert cascade_rows, "no cascade rows rendered, so this asserts nothing"

    quantified = [
        (r["section"], r["key"], c["deal_id"])
        for r in cascade_rows
        for c in r["cells"]
        if c.get("present") and c.get("value") is not None
    ]
    assert not [q for q in quantified if q[0].startswith("waterfall:")]

    # And the rows are populated with structure even where value is null.
    labelled = [
        c
        for r in cascade_rows
        for c in r["cells"]
        if c.get("present") and c.get("label")
    ]
    assert labelled, "cascade rows rendered no labels either — genuinely blank"


# ---------------------------------------------------------------------------
# The numerator is real; what still blocks the screen is the period split (#550)
# ---------------------------------------------------------------------------


def test_the_seed_carries_the_collateral_numerator_the_report_states() -> None:
    """End to end: the figure on the report's own page reaches the deal state.

    ``399,984,890.74`` is the total the January 2025 Note Valuation Report
    prints under its Par Value Tests Detail numerator — the same document the
    live series folds, which is why sourcing it moved no period and left #524's
    precedence decision untouched. The pool balance must be that figure and not
    the note total, which is what made the ratio an identity before.
    """
    from loanwhiz.api.main import DEAL_REGISTRY, _reconstruct_series

    series = _reconstruct_series(CLO_DEAL_ID, DEAL_REGISTRY[CLO_DEAL_ID])
    seed = series.states[0]
    note_total = sum(t.balance for t in seed.tranches)

    assert seed.reporting_date == "2025-01-08"
    assert seed.pool_balance == pytest.approx(399_984_890.74)
    assert seed.pool_balance != pytest.approx(note_total)


def test_the_seed_numerator_reproduces_the_published_par_value_ratios() -> None:
    """What the numerator is worth: the report's own five ratios, to the cent.

    Computed off the seed's own tranche balances, so this exercises the same
    numerator and denominators a coverage test would — the arithmetic the screen
    would render if its periods had states of their own date.
    """
    from decimal import Decimal

    from loanwhiz.api.main import DEAL_REGISTRY, _reconstruct_series

    seed = _reconstruct_series(CLO_DEAL_ID, DEAL_REGISTRY[CLO_DEAL_ID]).states[0]
    balances = {t.name: t.balance for t in seed.tranches}
    published = {
        ("class_a", "class_b_1", "class_b_2"): "139.08",
        ("class_a", "class_b_1", "class_b_2", "class_c"): "128.74",
        ("class_a", "class_b_1", "class_b_2", "class_c", "class_d"): "118.62",
        ("class_a", "class_b_1", "class_b_2", "class_c", "class_d", "class_e"): "112.86",
        (
            "class_a",
            "class_b_1",
            "class_b_2",
            "class_c",
            "class_d",
            "class_e",
            "class_f",
        ): "108.40",
    }
    for classes, expected in published.items():
        denominator = sum(balances[name] for name in classes)
        ratio = (
            Decimal(str(seed.pool_balance)) / Decimal(str(denominator)) * 100
        ).quantize(Decimal("0.01"))
        assert str(ratio) == expected, f"{classes} computed {ratio}, report states {expected}"


def test_the_compliance_refusal_names_the_threshold_it_does_not_have(
    client: TestClient,
) -> None:
    """The refusal must name its real cause, or it misdirects the next reader.

    Three times now this screen has refused for a reason that was not the
    deal's (#457, #549, and the as-of-date mismatch #550 recorded). The par
    value tests no longer lack a numerator, and no longer lack a state of the
    period being evaluated — #583 pairs each period to the state stating the
    same date, and for this deal that leaves the one period the report covers.

    What they lack now is a quantified threshold, and the reason has to say so.
    A ratio against no required level is a measurement without a verdict, which
    is why the metric renders while ``evaluable`` stays false.
    """
    statuses = client.get(f"/deal/{CLO_DEAL_ID}/compliance").json()["trigger_statuses"]
    par_value = [s for s in statuses if "par_value" in s["trigger_name"]]
    assert par_value, "the deal's par value tests are on the screen"

    for status in par_value:
        reason = status["not_evaluable_reason"] or ""
        assert status["metric_value"] is not None, "the ratio is a real measurement"
        assert status["threshold"] is None
        assert "threshold" in reason, f"the cause is the missing threshold: {reason}"
        # Each superseded cause would be the false cause all over again.
        assert "#550" not in reason and "notes over notes" not in reason
        assert "not a measurement of this one" not in reason
        assert "no deal state was reconstructed" not in reason


def test_every_compliance_period_carries_a_state_of_its_own_date(
    client: TestClient,
) -> None:
    """The pairing invariant, stated on the live deal (#583).

    ``/compliance`` drew its periods from the tapes and its states from the
    report those tapes yielded to, and #524 had already established the two
    overlap on no date at all — so every figure rendered under a date it is not
    stated as of. The screen now runs only on periods that have a state.
    """
    statuses = client.get(f"/deal/{CLO_DEAL_ID}/compliance").json()["trigger_statuses"]
    periods = {s["period"] for s in statuses}

    assert periods == {"2025-01-08"}, f"expected the report's own date, got {periods}"
    # The dates #524 set aside are the tapes', and are not this screen's axis.
    assert not (periods & {"2024-12-16", "2025-02-18", "2025-03-18"})


def test_the_par_value_ratios_render_the_figures_the_report_publishes(
    client: TestClient,
) -> None:
    """The five published ratios reach the screen, under the date they are stated as of.

    #550 parsed the numerator and reproduced these exactly, but the screen could
    not show them: the only periods on the axis belonged to other documents. The
    pairing was the last thing between a correct numerator and a rendered ratio.
    """
    published = {
        "class_a_b_par_value_test": 139.08,
        "class_c_par_value_test": 128.74,
        "class_d_par_value_test": 118.62,
        "class_e_par_value_test": 112.86,
        "class_f_par_value_test": 108.40,
    }
    statuses = client.get(f"/deal/{CLO_DEAL_ID}/compliance").json()["trigger_statuses"]
    rendered = {
        s["trigger_name"]: s["metric_value"]
        for s in statuses
        if s["trigger_name"] in published
    }

    assert set(rendered) == set(published), "all five par value tests are on the screen"
    for name, expected in published.items():
        assert rendered[name] is not None, f"{name} rendered no ratio"
        assert round(rendered[name], 2) == expected, (
            f"{name} rendered {rendered[name]}, the report publishes {expected}"
        )
