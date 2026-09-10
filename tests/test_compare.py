"""Tests for the cross-deal comparison tool (#283, Epic 7).

Two layers, mirroring the plan's load-bearing-paths contract:

* **Integration** over ``GET /compare`` via the FastAPI ``TestClient`` — the
  real assembly path (cached ``DealModel`` → ``build_deal_rules`` → structural
  diff; ``_reconstruct_series`` → performance panel; ``CovenantMonitor`` → risk
  summary). No mocks for the code under test; these run offline against the
  committed seed models and the offline report loader (``green-lion-2024-1``).
* **Unit** over the pure alignment / median / vintage helpers in
  :mod:`loanwhiz.api.compare`, on fixed vectors that pin the maths.
"""

from __future__ import annotations

import statistics

import pytest
from fastapi.testclient import TestClient

from loanwhiz.api import app
from loanwhiz.api import compare as cmp
from loanwhiz.domain.rules import (
    AmountRule,
    DealRules,
    MetricType,
    RateRule,
    RecipientType,
    ReserveRule,
    StepRule,
    TrancheRule,
    TriggerRule,
)
from loanwhiz.primitives.deal_state import DealState

client = TestClient(app)

# Three seeded deals across jurisdictions that all carry a committed DealModel
# (so the structural diff renders offline); green-lion-2024-1 also reconstructs
# a real series via the offline report loader (the performance/risk path).
SEEDED_THREE = "green-lion-2024-1,green-lion-2023-1,leone-arancio-2023-1"
MODELABLE = "green-lion-2024-1"


# ---------------------------------------------------------------------------
# Integration — GET /compare contract.
# ---------------------------------------------------------------------------


def test_compare_structural_renders_for_three_deals():
    """≥3 deals across jurisdictions render an aligned structural diff."""
    resp = client.get("/compare", params={"deals": SEEDED_THREE})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert [d["deal_id"] for d in body["deals"]] == [
        "green-lion-2024-1",
        "green-lion-2023-1",
        "leone-arancio-2023-1",
    ]
    # Every deal got a structural diff (cached models present).
    assert all(d["has_structural"] for d in body["deals"])
    # Rows span every structural section, aligned by canonical taxonomy.
    sections = {row["section"] for row in body["structural_rows"]}
    assert {"tranche", "waterfall:revenue", "trigger", "reserve"} <= sections
    # Each structural row has exactly one cell per deal, in column order.
    for row in body["structural_rows"]:
        assert [c["deal_id"] for c in row["cells"]] == [
            "green-lion-2024-1",
            "green-lion-2023-1",
            "leone-arancio-2023-1",
        ]


def test_compare_rows_align_by_canonical_taxonomy():
    """Waterfall rows key on RecipientType; trigger rows key on MetricType."""
    body = client.get("/compare", params={"deals": SEEDED_THREE}).json()
    recipient_keys = {r.value for r in RecipientType}
    metric_keys = {m.value for m in MetricType}
    for row in body["structural_rows"]:
        if row["section"].startswith("waterfall:") and not row["key"].endswith("_unmapped"):
            assert row["key"] in recipient_keys, row
        if row["section"] == "trigger" and row["key"] != "trigger_unmapped":
            assert row["key"] in metric_keys, row


def test_compare_diff_highlight_flags_differing_rows():
    """A row whose deals differ is flagged ``differs`` (diff-highlight)."""
    body = client.get("/compare", params={"deals": SEEDED_THREE}).json()
    # The senior tranche balances differ across these three deals.
    tranche0 = next(r for r in body["structural_rows"] if r["key"] == "tranche_rank_0")
    assert tranche0["differs"] is True


def test_compare_performance_series_for_modelable_deal():
    """A reconstructable deal emits an overlaid performance series."""
    body = client.get("/compare", params={"deals": SEEDED_THREE}).json()
    gl = next(d for d in body["deals"] if d["deal_id"] == MODELABLE)
    assert gl["has_performance"] is True
    series = next(s for s in body["performance_series"] if s["deal_id"] == MODELABLE)
    assert series["points"], "expected a non-empty series"
    pt = series["points"][0]
    # Each point carries the spec's Panel-2 metrics.
    for field in (
        "pool_factor",
        "reserve_balance",
        "reserve_target",
        "total_pdl",
        "cumulative_losses",
        "cumulative_loss_rate_pct",
    ):
        assert field in pt


def test_compare_risk_summary_emits_proximity():
    """The risk summary carries a latest-period covenant proximity per deal."""
    body = client.get("/compare", params={"deals": SEEDED_THREE}).json()
    assert {rs["deal_id"] for rs in body["risk_summary"]} == {
        "green-lion-2024-1",
        "green-lion-2023-1",
        "leone-arancio-2023-1",
    }
    gl = next(rs for rs in body["risk_summary"] if rs["deal_id"] == MODELABLE)
    # A modelable deal has a latest period + an evaluable tightest trigger.
    assert gl["latest_period"] is not None
    assert gl["tightest_trigger"] is not None
    assert gl["tightest_proximity_pct"] is not None


def test_compare_degrades_honestly_for_unmodelable_deal():
    """A deal with no reconstructable / projectable series stays unavailable."""
    body = client.get("/compare", params={"deals": SEEDED_THREE}).json()
    thin = next(d for d in body["deals"] if d["deal_id"] == "green-lion-2023-1")
    assert thin["has_structural"] is True  # cached model exists
    assert thin["has_performance"] is False  # no series offline (#345: no proj config)
    assert thin["performance_provenance"] is None
    assert thin["note"] is not None
    assert any("green-lion-2023-1" in n for n in body["notes"])


def test_compare_reported_series_carries_reported_provenance():
    """A reconstructed (tape/report) series is flagged provenance 'reported' (#345)."""
    body = client.get("/compare", params={"deals": SEEDED_THREE}).json()
    gl = next(d for d in body["deals"] if d["deal_id"] == MODELABLE)
    assert gl["has_performance"] is True
    assert gl["performance_provenance"] == "reported"


# A config-bearing, tape/report-absent deal context: it carries the forward-
# projection config (capital structure + reserve target + original pool balance +
# projection base) but no tape and no foldable report. The canonical-model
# projection fallback (#345) should light it up as a *projected* series.
_PROJECTABLE_CTX = {
    "deal_name": "Projectable Test 2024-1",
    "jurisdiction": "Netherlands",
    "tape_urls": [],
    "capital_structure": {
        "class_a_balance": 1_000_000_000.0,
        "class_a_rate_pct": 3.62,
        "class_b_balance": 53_100_000.0,
        "class_c_balance": 10_500_000.0,
    },
    "reserve_account_target": 10_636_000.0,
    "original_pool_balance": 1_033_412_063.0,
    "projection_base": {
        "current_pool_balance": 1_033_412_063.0,
        "class_a_rate_pct": 3.62,
    },
}


def test_compare_projects_tape_report_absent_deal(monkeypatch):
    """A deal with no tape/report but resolvable canonical config gets a
    projected-not-reported Panel-2 series, clearly labelled (#345)."""
    import loanwhiz.api.main as main

    patched = dict(main.DEALS)
    patched["projectable-test"] = _PROJECTABLE_CTX
    monkeypatch.setattr(main, "DEALS", patched)

    body = client.get(
        "/compare", params={"deals": f"{MODELABLE},projectable-test"}
    ).json()
    proj = next(d for d in body["deals"] if d["deal_id"] == "projectable-test")
    assert proj["has_performance"] is True
    assert proj["performance_provenance"] == "projected"
    assert proj["note"] is not None and "projected" in proj["note"].lower()
    # A non-empty projected series is emitted in the overlay.
    series = next(
        s for s in body["performance_series"] if s["deal_id"] == "projectable-test"
    )
    assert series["points"], "expected a non-empty projected series"
    # The overlay renders a curve, not a single collapsed point: distinct dates.
    dates = [p["reporting_date"] for p in series["points"]]
    assert len(set(dates)) > 1
    # The reported deal in the same set keeps its 'reported' provenance.
    rep = next(d for d in body["deals"] if d["deal_id"] == MODELABLE)
    assert rep["performance_provenance"] == "reported"


def test_projected_series_from_canonical_returns_none_when_unprojectable():
    """The projection fallback is non-raising: a deal with no resolvable
    forward-projection config yields None (degrade, never 500) (#345)."""
    import loanwhiz.api.main as main

    # green-lion-2023-1 has a cached model but no projection_base / numeric
    # capital structure / reserve / pool config → _resolve_* raises → None.
    ctx = main.DEALS["green-lion-2023-1"]
    assert main._projected_series_from_canonical("green-lion-2023-1", ctx) is None


def test_projected_series_from_canonical_builds_series_when_projectable():
    """The projection fallback builds a non-empty amortizing series from a
    config-bearing deal context (#345)."""
    import loanwhiz.api.main as main

    series = main._projected_series_from_canonical("projectable-test", _PROJECTABLE_CTX)
    assert series is not None
    assert len(series.states) > 1
    # Pool amortizes forward: the last pool factor is below the seed's.
    assert series.states[-1].pool_factor < series.states[0].pool_factor
    # Each state carries a DISTINCT, ordered reporting_date, so the /compare
    # overlay (keyed by reporting_date) renders a curve rather than collapsing
    # every point onto a single X value (#345 regression guard).
    dates = [st.reporting_date for st in series.states]
    assert len(set(dates)) == len(dates)
    assert dates == sorted(dates)


@pytest.mark.parametrize("deal_id", ["leone-arancio-2023-1", "sol-lion-ii"])
def test_projected_series_from_canonical_renders_for_registry_it_es_deals(deal_id):
    """Registry-data guard (#358): the IT/ES flagship deals carry
    prospectus-sourced projection config in ``deals.json``, so the #345
    fallback resolves a non-empty amortizing series from the LIVE registry
    (not a synthetic context). A future registry edit that drops or breaks
    one of the four config keys (``capital_structure`` /
    ``reserve_account_target`` / ``original_pool_balance`` /
    ``projection_base``) regresses the projected ``/compare`` panel back to
    "unavailable" and trips this test."""
    import loanwhiz.api.main as main

    series = main._projected_series_from_canonical(deal_id, main.DEALS[deal_id])
    assert series is not None, f"{deal_id} should project from the live registry"
    assert len(series.states) > 1
    # Pool amortizes forward: last pool factor below the seed's.
    assert series.states[-1].pool_factor < series.states[0].pool_factor
    # Distinct, ordered reporting_dates so the /compare overlay renders a curve.
    dates = [st.reporting_date for st in series.states]
    assert len(set(dates)) == len(dates)
    assert dates == sorted(dates)


def test_compare_benchmark_lens_sets_median_and_deviation():
    """With a target, structural cells carry comp-set median + signed deviation."""
    resp = client.get(
        "/compare",
        params={"deals": SEEDED_THREE, "target": MODELABLE},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_deal_id"] == MODELABLE
    target_ref = next(d for d in body["deals"] if d["deal_id"] == MODELABLE)
    assert target_ref["is_target"] is True

    # The senior-tranche row is comparable + numeric — the target cell gets a
    # median over the comps and a signed deviation = value - median.
    tranche0 = next(r for r in body["structural_rows"] if r["key"] == "tranche_rank_0")
    tcell = next(c for c in tranche0["cells"] if c["deal_id"] == MODELABLE)
    comps = [c["value"] for c in tranche0["cells"] if c["deal_id"] != MODELABLE]
    expected_median = statistics.median(comps)
    assert tcell["comp_median"] == pytest.approx(expected_median)
    assert tcell["deviation"] == pytest.approx(tcell["value"] - expected_median)


def test_compare_comp_suggestions_returns_registry_deals():
    """A target yields comp suggestions from the registry, excluding the set."""
    body = client.get(
        "/compare", params={"deals": SEEDED_THREE, "target": MODELABLE}
    ).json()
    suggestions = body["comp_suggestions"]
    # Suggestions never include a deal already in the comparison set.
    assert not set(suggestions) & {
        "green-lion-2024-1",
        "green-lion-2023-1",
        "leone-arancio-2023-1",
    }


@pytest.mark.parametrize(
    "params, why",
    [
        ({"deals": MODELABLE}, "fewer than 2 deals"),
        ({"deals": f"{MODELABLE},{MODELABLE}"}, "fewer than 2 distinct deals"),
        ({"deals": f"{MODELABLE},does-not-exist"}, "unknown deal id"),
        ({"deals": SEEDED_THREE, "target": "does-not-exist"}, "target not in set"),
        ({"deals": ""}, "empty deals"),
    ],
)
def test_compare_invalid_input_is_422_not_500(params, why):
    """Bad comparison requests return a labelled 422, never a 500."""
    resp = client.get("/compare", params=params)
    assert resp.status_code == 422, f"{why}: {resp.status_code} {resp.text}"


# ---------------------------------------------------------------------------
# Unit — pure helpers in loanwhiz.api.compare.
# ---------------------------------------------------------------------------


def _rules(
    deal_id: str,
    *,
    jurisdiction: str = "Netherlands",
    class_a_balance: float = 1_000.0,
    loss_threshold_pct: float = 5.0,
) -> DealRules:
    """A minimal but valid DealRules for unit tests of the alignment layer."""
    return DealRules(
        deal_id=deal_id,
        deal_name=f"Test Deal {deal_id} 2023-1",
        jurisdiction=jurisdiction,
        tranches=[
            TrancheRule(
                name="Class A",
                seniority=0,
                original_balance=class_a_balance,
                rate=RateRule(kind="fixed", fixed_pct=0.03),
                rating="AAA",
            ),
            TrancheRule(
                name="Class B",
                seniority=1,
                original_balance=100.0,
                rate=RateRule(kind="fixed", fixed_pct=0.05),
            ),
        ],
        waterfalls={
            "revenue": [
                StepRule(
                    order=0,
                    priority_label="(a)",
                    recipient=RecipientType.senior_expenses,
                    amount=AmountRule(
                        calculator=RecipientType.senior_expenses,
                        basis="report_supplied",
                        raw_text="senior expenses",
                    ),
                ),
                StepRule(
                    order=1,
                    priority_label="(b)",
                    recipient=RecipientType.class_a_interest,
                    amount=AmountRule(
                        calculator=RecipientType.class_a_interest,
                        basis="interest_accrual",
                        raw_text="class A interest",
                    ),
                ),
            ],
            "redemption": [],
            "post_enforcement": [],
        },
        triggers=[
            TriggerRule(
                name="cum_loss",
                metric=MetricType.cumulative_loss_rate,
                operator=">",
                threshold=loss_threshold_pct,
                threshold_unit="percent",
                consequence="switch to sequential pay",
            )
        ],
        reserve=ReserveRule(floor=50.0, pct_of_note_balance=0.01),
    )


def test_parse_vintage():
    assert cmp.parse_vintage("Green Lion 2024-1 B.V.") == 2024
    assert cmp.parse_vintage("Leone Arancio RMBS 2023-1 S.r.l.") == 2023
    assert cmp.parse_vintage("No Year Here") is None


def test_normalise_threshold_units():
    pct = TriggerRule(
        name="p", metric=MetricType.cumulative_loss_rate, operator=">",
        threshold=5.0, threshold_unit="percent", consequence="x",
    )
    bps = TriggerRule(
        name="b", metric=MetricType.cumulative_loss_rate, operator=">",
        threshold=250.0, threshold_unit="bps", consequence="x",
    )
    frac = TriggerRule(
        name="f", metric=MetricType.cumulative_loss_rate, operator=">",
        threshold=0.05, threshold_unit="fraction", consequence="x",
    )
    qual = TriggerRule(
        name="q", metric=MetricType.unmapped, operator=">",
        threshold=None, threshold_unit="percent", consequence="x",
    )
    assert cmp._normalise_threshold(pct) == pytest.approx(0.05)
    assert cmp._normalise_threshold(bps) == pytest.approx(0.025)
    assert cmp._normalise_threshold(frac) == pytest.approx(0.05)
    assert cmp._normalise_threshold(qual) is None


def test_build_structural_diff_aligns_and_flags_differences():
    a = _rules("a", class_a_balance=1_000.0)
    b = _rules("b", class_a_balance=2_000.0)
    rows = cmp.build_structural_diff({"a": a, "b": b}, ["a", "b"])
    # Senior tranche differs (1000 vs 2000).
    tranche0 = next(r for r in rows if r.key == "tranche_rank_0")
    assert tranche0.differs is True
    assert [c.value for c in tranche0.cells] == [1_000.0, 2_000.0]
    # The shared revenue recipients line up on the same canonical keys.
    rev_keys = {r.key for r in rows if r.section == "waterfall:revenue"}
    assert RecipientType.class_a_interest.value in rev_keys


def test_build_structural_diff_surfaces_unmapped_as_not_comparable():
    a = _rules("a")
    # Inject an unmapped step into deal a's revenue waterfall.
    a.waterfalls["revenue"].append(
        StepRule(
            order=2,
            priority_label="(z)",
            recipient=RecipientType.unmapped,
            amount=AmountRule(
                calculator=RecipientType.unmapped,
                basis="report_supplied",
                raw_text="some deal-specific step",
            ),
        )
    )
    b = _rules("b")
    rows = cmp.build_structural_diff({"a": a, "b": b}, ["a", "b"])
    unmapped_row = next(r for r in rows if r.key == "revenue_unmapped")
    a_cell = next(c for c in unmapped_row.cells if c.deal_id == "a")
    assert a_cell.comparable is False
    assert a_cell.present is True  # deal a has the unmapped step
    # Deal b has none.
    b_cell = next(c for c in unmapped_row.cells if c.deal_id == "b")
    assert b_cell.present is False


def test_intersect_periods():
    s1 = [
        DealState(reporting_date="2024-01-31", class_a_balance=0, class_b_balance=0,
                  class_c_balance=0, pool_balance=10, original_pool_balance=10),
        DealState(reporting_date="2024-02-29", class_a_balance=0, class_b_balance=0,
                  class_c_balance=0, pool_balance=10, original_pool_balance=10),
    ]
    s2 = [
        DealState(reporting_date="2024-02-29", class_a_balance=0, class_b_balance=0,
                  class_c_balance=0, pool_balance=10, original_pool_balance=10),
        DealState(reporting_date="2024-03-31", class_a_balance=0, class_b_balance=0,
                  class_c_balance=0, pool_balance=10, original_pool_balance=10),
    ]
    assert cmp.intersect_periods({"a": s1, "b": s2}) == ["2024-02-29"]
    # Single deal → its own dates.
    assert cmp.intersect_periods({"a": s1}) == ["2024-01-31", "2024-02-29"]
    assert cmp.intersect_periods({}) == []


def test_apply_benchmark_median_and_deviation_on_fixed_vector():
    """Benchmark median + deviation pinned on a hand-checked vector."""
    a = _rules("a", class_a_balance=1_000.0)  # target
    b = _rules("b", class_a_balance=2_000.0)
    c = _rules("c", class_a_balance=4_000.0)
    rows = cmp.build_structural_diff({"a": a, "b": b, "c": c}, ["a", "b", "c"])
    resp = cmp.CompareResponse(
        deals=[
            cmp.DealRef(deal_id="a", deal_name="A 2023-1", jurisdiction="NL", is_target=True),
            cmp.DealRef(deal_id="b", deal_name="B 2023-1", jurisdiction="NL"),
            cmp.DealRef(deal_id="c", deal_name="C 2023-1", jurisdiction="NL"),
        ],
        target_deal_id="a",
        structural_rows=rows,
    )
    cmp.apply_benchmark(resp, "a")
    tranche0 = next(r for r in resp.structural_rows if r.key == "tranche_rank_0")
    target_cell = next(cl for cl in tranche0.cells if cl.deal_id == "a")
    # Comps are 2000 and 4000 → median 3000; target 1000 → deviation -2000.
    assert target_cell.comp_median == pytest.approx(3_000.0)
    assert target_cell.deviation == pytest.approx(-2_000.0)


def test_suggest_comps_filters_by_jurisdiction_and_vintage():
    registry = {
        "t": {"deal_name": "Target 2024-1", "jurisdiction": "Netherlands"},
        "same": {"deal_name": "Sibling 2024-1", "jurisdiction": "Netherlands"},
        "near": {"deal_name": "Near 2023-1", "jurisdiction": "Netherlands"},
        "far_vintage": {"deal_name": "Old 2018-1", "jurisdiction": "Netherlands"},
        "other_juris": {"deal_name": "Italian 2024-1", "jurisdiction": "Italy"},
    }
    out = cmp.suggest_comps(
        target_deal_id="t",
        target_jurisdiction="Netherlands",
        target_vintage=2024,
        registry=registry,
        already_selected={"t"},
    )
    assert set(out) == {"same", "near"}  # same/near juris+vintage; far/other excluded


# ---------------------------------------------------------------------------
# #400 — scoring + reasoning wired into /compare.
# ---------------------------------------------------------------------------


from loanwhiz.primitives.relative_value_screener import (  # noqa: E402
    DIM_POOL_QUALITY,
    DIM_SUBORDINATION_CE,
    DIM_TRIGGER_HEADROOM,
    DIM_WAL,
    RelativeValueScorecard,
    RvFactor,
    TrancheScore,
)


def _rv_factor(dim: str, *, available: bool, value=None, score=None, reason="r") -> RvFactor:
    return RvFactor(
        dimension=dim,
        available=available,
        value=value,
        score=score,
        basis="structural" if available else "live-required",
        reason=reason,
    )


def _scored_tranche(deal_id: str, name: str, composite: float, *, ce=0.18) -> TrancheScore:
    """A fully-scored tranche row for verdict unit tests."""
    return TrancheScore(
        deal_id=deal_id,
        deal_name=deal_id.upper(),
        tranche_name=name,
        seniority=0,
        rating="AAA",
        size_eur=900.0,
        factors={
            DIM_SUBORDINATION_CE: _rv_factor(
                DIM_SUBORDINATION_CE, available=True, value=ce, score=80.0,
                reason=f"{ce:.1%} junior",
            ),
            DIM_WAL: _rv_factor(DIM_WAL, available=True, value=0.0, score=70.0),
            DIM_TRIGGER_HEADROOM: _rv_factor(
                DIM_TRIGGER_HEADROOM, available=True, value=0.5, score=60.0,
                reason="2/4 triggers quantified",
            ),
            DIM_POOL_QUALITY: _rv_factor(
                DIM_POOL_QUALITY, available=False,
                reason="needs the ESMA tape (live-only)",
            ),
        },
        composite_score=composite,
    )


def _scorecard(rows: list[TrancheScore]) -> RelativeValueScorecard:
    return RelativeValueScorecard(
        dimensions=[DIM_SUBORDINATION_CE, DIM_WAL, DIM_TRIGGER_HEADROOM, DIM_POOL_QUALITY],
        weights={DIM_SUBORDINATION_CE: 1.0},
        tranches=rows,
        tally={},
    )


def _ref(deal_id: str) -> cmp.DealRef:
    return cmp.DealRef(deal_id=deal_id, deal_name=deal_id.upper(), jurisdiction="NL")


def _series(deal_id: str, *, points: int = 2) -> cmp.PerformanceSeries:
    """A deal's performance evidence. ``points=0`` is the present-but-empty shell."""
    return cmp.PerformanceSeries(
        deal_id=deal_id,
        points=[
            cmp.PerformancePoint(
                reporting_date=f"2025-0{n + 1}-01",
                pool_factor=1.0,
                reserve_balance=10.0,
                reserve_target=10.0,
                total_pdl=0.0,
                cumulative_losses=0.0,
                cumulative_loss_rate_pct=0.0,
            )
            for n in range(points)
        ],
    )


def _risk(deal_id: str, *, latest_period: str | None = "2025-02-01", **kw) -> cmp.RiskSummary:
    """A deal's risk evidence. ``latest_period=None`` is the bare shell
    ``_deal_risk_summary`` returns for a deal with no states."""
    return cmp.RiskSummary(deal_id=deal_id, latest_period=latest_period, **kw)


#: A complete two-deal comparison set: both halves of evidence for both deals.
#: Every verdict test states its evidence explicitly — the gate under test reads
#: exactly this, so a helper that quietly supplied it would hide the axis (#615).
def _complete_evidence(*deal_ids: str):
    return (
        [_series(d) for d in deal_ids],
        [_risk(d) for d in deal_ids],
    )


def test_build_comparative_verdict_picks_clear_winner():
    """Two scored deals → a ranked verdict citing the winner's real CE."""
    card = _scorecard(
        [
            _scored_tranche("a", "Class A", 90.0, ce=0.20),
            _scored_tranche("b", "Class A", 40.0, ce=0.05),
        ]
    )
    verdict = cmp.build_comparative_verdict(
        card,
        [_ref("a"), _ref("b")],
        [
            _risk("a", tightest_trigger="PDL", tightest_proximity_pct=12.0),
            _risk("b", tightest_trigger="PDL", tightest_proximity_pct=3.0),
        ],
        [_series("a"), _series("b")],
    )
    assert verdict.confidence == "scored"
    assert verdict.winner_deal_id == "a"
    assert verdict.ranking == ["a", "b"]
    # A reason cites the winner's real 20% credit enhancement.
    assert any("20.0%" in r and "credit enhancement" in r for r in verdict.reasons)
    # And the live covenant proximity is surfaced.
    assert any("12.0%" in r and "breach" in r for r in verdict.reasons)


def test_build_comparative_verdict_insufficient_data_when_under_two_score():
    """Only one deal scores → honest insufficient-data, no fabricated winner."""
    card = _scorecard(
        [
            _scored_tranche("a", "Class A", 90.0),
            # b's tranche came back unscored (no composite).
            TrancheScore(
                deal_id="b",
                deal_name="B",
                tranche_name="Class A",
                factors={
                    DIM_SUBORDINATION_CE: _rv_factor(
                        DIM_SUBORDINATION_CE, available=False,
                        reason="No sized tranche structure extracted",
                    )
                },
                composite_score=None,
            ),
        ]
    )
    # Evidence is complete for both deals, so this isolates the *structural*
    # refusal — it cannot pass by way of #615's incomplete-set gate.
    perf, risk = _complete_evidence("a", "b")
    verdict = cmp.build_comparative_verdict(card, [_ref("a"), _ref("b")], risk, perf)
    assert verdict.confidence == "insufficient-data"
    assert verdict.winner_deal_id is None
    assert verdict.ranking == []
    assert verdict.reasons == []
    assert "Insufficient data" in verdict.summary


def test_build_comparative_verdict_surfaces_unavailable_caveats():
    """Live-only (unavailable) dimensions surface as caveats from real reasons."""
    card = _scorecard([_scored_tranche("a", "Class A", 90.0), _scored_tranche("b", "Class A", 50.0)])
    perf, risk = _complete_evidence("a", "b")
    verdict = cmp.build_comparative_verdict(card, [_ref("a"), _ref("b")], risk, perf)
    assert verdict.confidence == "scored"
    # The pool-quality factor was flagged unavailable → its reason is a caveat.
    assert any("ESMA tape" in c and DIM_POOL_QUALITY in c for c in verdict.caveats)


def test_compare_endpoint_returns_scorecard_and_verdict():
    """GET /compare now carries a subset relative_value scorecard + a verdict."""
    body = client.get("/compare", params={"deals": SEEDED_THREE}).json()
    # Subset scorecard scoped to exactly the requested deals.
    assert body["relative_value"] is not None
    screened = {r["deal_id"] for r in body["relative_value"]["tranches"]}
    assert screened <= {"green-lion-2024-1", "green-lion-2023-1", "leone-arancio-2023-1"}
    verdict = body["comparative_verdict"]
    assert verdict is not None
    assert verdict["confidence"] in {"scored", "insufficient-data", "incomplete-set"}
    if verdict["confidence"] == "scored":
        assert verdict["winner_deal_id"] in screened
        assert verdict["reasons"]
    else:
        # #549 — a refusal that keeps the value is not a refusal. Whichever
        # refusal fired, nothing a reader could take a winner from survives.
        assert verdict["winner_deal_id"] is None
        assert verdict["winner_deal_name"] is None
        assert verdict["ranking"] == []
        assert verdict["reasons"] == []


def test_compare_endpoint_verdict_is_honest_on_thin_set(monkeypatch):
    """A set where no deal yields a scored tranche degrades to insufficient-data."""
    # Force every cached model to None → screener emits no scorable tranche.
    import loanwhiz.api.main as main_mod

    monkeypatch.setattr(main_mod, "_load_cached_deal_model", lambda _deal: None)
    body = client.get("/compare", params={"deals": SEEDED_THREE}).json()
    verdict = body["comparative_verdict"]
    assert verdict["confidence"] == "insufficient-data"
    assert verdict["winner_deal_id"] is None


# ---------------------------------------------------------------------------
# #615 — the verdict must require its inputs, not merely decorate itself with
# them. Every test below states each deal's evidence explicitly; the guard reads
# exactly that, so a fixture that supplied it implicitly would hide the axis.
# ---------------------------------------------------------------------------


def _two_scored_deals():
    """A card where BOTH deals score structurally.

    Load-bearing for every test in this section: it holds `len(best) >= 2`
    constant, so the incomplete-set gate is the only thing that can refuse. A
    case where one deal failed to score would pass for the *old* reason
    ("insufficient-data") and prove nothing about #615.
    """
    return _scorecard(
        [
            _scored_tranche("a", "Class A", 90.0, ce=0.20),
            _scored_tranche("b", "Class A", 40.0, ce=0.05),
        ]
    )


def test_verdict_refuses_when_a_deal_has_no_series():
    """A deal contributing neither half is never ranked, and no winner is named."""
    verdict = cmp.build_comparative_verdict(
        _two_scored_deals(),
        [_ref("a"), _ref("b")],
        [_risk("a"), _risk("b", latest_period=None)],
        [_series("a"), _series("b", points=0)],
    )
    assert verdict.confidence == "incomplete-set"
    # #549 — all four value-bearing fields go together, or it is not a refusal.
    assert verdict.winner_deal_id is None
    assert verdict.winner_deal_name is None
    assert verdict.ranking == []
    assert verdict.reasons == []
    # It names the deal and BOTH missing sections, not a bare "no data".
    assert "B" in verdict.summary
    assert "performance series" in verdict.summary
    assert "risk summary" in verdict.summary
    assert any("performance/risk" in c and "B" in c for c in verdict.caveats)


def test_verdict_scores_when_the_missing_evidence_is_supplied():
    """#493's other direction: supply only the missing evidence, and it flips.

    Identical card and deal refs to the refusal above — the *only* difference is
    that b now brings a series and a risk row. Without this pair, the refusal
    test would keep passing with the guard reverted.
    """
    verdict = cmp.build_comparative_verdict(
        _two_scored_deals(),
        [_ref("a"), _ref("b")],
        [_risk("a"), _risk("b")],
        [_series("a"), _series("b")],
    )
    assert verdict.confidence == "scored"
    assert verdict.winner_deal_id == "a"
    assert verdict.ranking == ["a", "b"]


def test_verdict_refuses_when_the_series_entry_is_present_but_empty():
    """An entry with no points is ABSENT evidence, not thin evidence (#513).

    The empty instance of the graded kind is how this class of bug keeps
    recurring: a row exists, so a presence-by-row-count check reads it as data.
    """
    verdict = cmp.build_comparative_verdict(
        _two_scored_deals(),
        [_ref("a"), _ref("b")],
        [_risk("a"), _risk("b")],  # risk row is real; only the series is hollow
        [_series("a"), _series("b", points=0)],
    )
    assert verdict.confidence == "incomplete-set"
    assert verdict.winner_deal_id is None
    # Only the half that is actually missing is named.
    assert "performance series" in verdict.summary
    assert "risk summary" not in verdict.summary


def test_verdict_names_only_the_missing_risk_half():
    """A deal with a series but the bare risk shell is refused for risk alone."""
    verdict = cmp.build_comparative_verdict(
        _two_scored_deals(),
        [_ref("a"), _ref("b")],
        [_risk("a"), _risk("b", latest_period=None)],
        [_series("a"), _series("b")],
    )
    assert verdict.confidence == "incomplete-set"
    assert "risk summary" in verdict.summary
    assert "performance series" not in verdict.summary


def test_verdict_still_scores_a_risk_row_with_no_quantified_trigger():
    """The gate must not over-fire: 'nothing is tight' is a finding, not a gap.

    Cairn's real shape — a genuine series and a real latest period, but no
    trigger quantified, so `tightest_proximity_pct` is None. Requiring proximity
    instead of `latest_period` would refuse a deal that in fact reported.
    """
    verdict = cmp.build_comparative_verdict(
        _two_scored_deals(),
        [_ref("a"), _ref("b")],
        [_risk("a", tightest_proximity_pct=None), _risk("b", tightest_proximity_pct=None)],
        [_series("a"), _series("b")],
    )
    assert verdict.confidence == "scored"
    assert verdict.winner_deal_id == "a"


def test_verdict_refuses_when_no_deal_brought_evidence():
    """Two structurally-scored deals and no live data at all still names nobody."""
    verdict = cmp.build_comparative_verdict(
        _two_scored_deals(),
        [_ref("a"), _ref("b")],
        [_risk("a", latest_period=None), _risk("b", latest_period=None)],
        [_series("a", points=0), _series("b", points=0)],
    )
    assert verdict.confidence == "incomplete-set"
    assert verdict.winner_deal_id is None
    assert "A" in verdict.summary and "B" in verdict.summary


def test_structural_refusal_still_records_the_performance_gap():
    """A set failing BOTH ways names both causes, not just the first to fire.

    `insufficient-data` wins on precedence, but the missing performance/risk
    evidence must still be recorded — a cause nobody mentions reads exactly like
    a cause that isn't there (#572), and an operator who fixed only the
    structural half would walk straight into the second refusal.
    """
    card = _scorecard(
        [
            _scored_tranche("a", "Class A", 90.0),
            TrancheScore(
                deal_id="b",
                deal_name="B",
                tranche_name="Class A",
                factors={
                    DIM_SUBORDINATION_CE: _rv_factor(
                        DIM_SUBORDINATION_CE, available=False,
                        reason="No sized tranche structure extracted",
                    )
                },
                composite_score=None,
            ),
        ]
    )
    verdict = cmp.build_comparative_verdict(
        card,
        [_ref("a"), _ref("b")],
        [_risk("a"), _risk("b", latest_period=None)],
        [_series("a"), _series("b", points=0)],
    )
    # The structural cause keeps precedence...
    assert verdict.confidence == "insufficient-data"
    assert verdict.winner_deal_id is None
    # ...but the evidence gap is still on the record.
    assert any("performance/risk" in c and "B" in c for c in verdict.caveats)


# --- the same contract, through the real endpoint (#562) -------------------

COMPLETE_PAIR = "green-lion-2024-1,leone-arancio-2023-1"


def _blank_one_deals_series(monkeypatch, deal_id: str):
    """Make exactly one deal of a set contribute no states, changing nothing else.

    Constructed rather than inherited: this must keep reding for the next deal
    without a series, so it must not depend on some particular deal in the
    committed data staying broken.
    """
    import loanwhiz.api.main as main_mod
    from fastapi import HTTPException

    real_reconstruct = main_mod._reconstruct_series
    real_projected = main_mod._projected_series_from_canonical

    def _no_series(did, ctx):
        if did == deal_id:
            raise HTTPException(status_code=422, detail="test: no reconstructable series")
        return real_reconstruct(did, ctx)

    def _no_projection(did, ctx):
        if did == deal_id:
            return None
        return real_projected(did, ctx)

    monkeypatch.setattr(main_mod, "_reconstruct_series", _no_series)
    monkeypatch.setattr(main_mod, "_projected_series_from_canonical", _no_projection)


def test_compare_endpoint_still_names_a_winner_on_complete_set():
    """The control: two fully-populated deals still get a ranked winner.

    Paired with the refusal below — same two deals, same request; only the
    series data differs. Without this, a gate that refused everything would pass.
    """
    body = client.get("/compare", params={"deals": COMPLETE_PAIR}).json()
    assert [d["deal_id"] for d in body["deals"] if not d["has_performance"]] == []
    verdict = body["comparative_verdict"]
    assert verdict["confidence"] == "scored"
    assert verdict["winner_deal_id"] is not None
    assert len(verdict["ranking"]) == 2


def test_compare_endpoint_refuses_verdict_on_incomplete_set(monkeypatch):
    """Blank one deal's series on the real request path → no winner survives."""
    _blank_one_deals_series(monkeypatch, "leone-arancio-2023-1")
    body = client.get("/compare", params={"deals": COMPLETE_PAIR}).json()

    # The set is genuinely incomplete, and the panel says so.
    assert not [s for s in body["performance_series"] if s["deal_id"] == "leone-arancio-2023-1"]
    assert any("leone-arancio-2023-1" in n for n in body["notes"])
    # Both deals still score structurally, so the refusal cannot be the older
    # "insufficient-data" one firing for a different reason.
    scored = {t["deal_id"] for t in body["relative_value"]["tranches"] if t["composite_score"] is not None}
    assert scored == {"green-lion-2024-1", "leone-arancio-2023-1"}

    verdict = body["comparative_verdict"]
    assert verdict["confidence"] == "incomplete-set"
    assert verdict["winner_deal_id"] is None
    assert verdict["winner_deal_name"] is None
    assert verdict["ranking"] == []
    assert verdict["reasons"] == []
    assert "performance series" in verdict["summary"]
