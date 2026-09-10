"""What the *second* CLO exposed — measured, not assumed (#535, epic #530).

Epic #530 onboarded Contego CLO XI DAC beside Cairn CLO XVII DAC to answer a
question one specimen cannot: is the platform's CLO support a **general
capability** or a **fitted specimen**? This module is the terminal check, and
its job is not "does the screen render" — `tests/test_clo_live_screens.py`
already grades Cairn's three screens. Its job is to pin, as assertions rather
than prose, *where the two deals agree and where they diverge*, so the epic's
claim is checkable instead of decorative.

What is measured here, and what each measurement means
-----------------------------------------------------
* **Both CLOs reach the cross-deal comparison with real structure.** `/compare`
  serves both, and every one of the eight seniority ranks carries a real
  figure for each deal. That is the *general* half of the answer, and it is
  asserted here rather than asserted-about. (Epic #530's own diff touched no
  file under `src/loanwhiz/api/` — a fact about that epic's history, recorded
  in `docs/data-card.md`, deliberately not restated here as a present-tense
  claim about the tree that nothing would keep true.)
* **Only Cairn reaches the performance panel.** Contego has no reconstructable
  series, so it carries no `latest_period`. This is the *fitted* half, and it
  is deliberately left visible: the refusal is asserted **by name** — the
  engine wants a `class_a_rate_pct` that Contego's extracted seed does not
  carry, because every tranche in that seed has a null `rate`. Pinning the
  reason rather than the bare 422 is the #534 discipline: when someone sources
  that coupon, this test reds and must be rewritten, instead of passing quietly
  on a refusal that has silently become a different refusal.
* **The two deals differ structurally, in both directions.** Cairn carries a
  per-class interest / deferred-interest / coverage-test cascade and per-class
  OC & IC triggers that Contego's model does not. Contego carries a *redemption*
  ladder — senior expenses, administrative expenses, a reserve replenishment,
  a subordinated management fee — that Cairn's does not. Neither is tuned to
  match the other: two CLOs that differ for real reasons are worth more than
  two smoothed into agreement, and the assertions below fail if either
  direction is flattened.

What is deliberately NOT asserted here
--------------------------------------
* **A null ``value`` on a waterfall or qualitative-trigger row is not a CLO
  defect.** #525 established it is a property of ``StructuralCell.value`` — a
  cross-deal comparable scalar a cascade step has for *no* deal, including both
  externally validated Dutch RMBS. The control for that claim already exists as
  ``test_no_waterfall_row_carries_a_numeric_value_for_any_deal`` in
  ``tests/test_clo_live_screens.py``; this issue extended its parametrisation
  with the two-CLO set rather than restating the assertion here.
* **Grading.** Contego carries a committed answer key (#534) whose ``covenants``
  cell is ``not-applicable``; that key's bounds are asserted in
  ``tests/test_quality_harness.py`` and stated in ``answer_keys/README.md``.
  Nothing here claims Contego is graded.

Nothing in this module changes an engine, seed, answer key or endpoint. It is
measurement over the committed offline fixtures.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from loanwhiz.api.main import DEAL_REGISTRY, _load_cached_deal_model, app

CAIRN = "cairn-clo-xvii"
CONTEGO = "contego-clo-xi"
#: The comparison this whole module is about.
BOTH_CLOS = f"{CAIRN},{CONTEGO}"

#: The one period Cairn's Note Valuation Report publishes, and therefore the
#: only date its reconstructed series can carry.
CAIRN_PERIOD = "2025-01-08"

#: The config key the engine refuses on for Contego. Transcribed from the
#: refusal, and the reason this deal has no series — not a proxy for it.
MISSING_COUPON_KEY = "class_a_rate_pct"

#: Every seniority rank the aligned structural panel renders for a CLO. Both
#: deals are eight-class stacks, which is why the panel lines them up at all.
TRANCHE_ROW_KEYS = tuple(f"tranche_rank_{i}" for i in range(8))


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(scope="module")
def comparison(client: TestClient) -> dict:
    """The one ``/compare`` payload every assertion below reads."""
    response = client.get("/compare", params={"deals": BOTH_CLOS})
    assert response.status_code == 200, response.text
    return response.json()


def _cell(row: dict, deal_id: str) -> dict | None:
    return next((c for c in row["cells"] if c["deal_id"] == deal_id), None)


def _seed_tranche_rates(deal_id: str) -> list[float | None]:
    """Every class's coupon as the committed seed carries it, in seniority order."""
    model = _load_cached_deal_model(DEAL_REGISTRY[deal_id])
    return [tranche.get("rate") for tranche in model.tranche_structure]


def _rows_present_only_for(payload: dict, deal_id: str, other_id: str) -> set[str]:
    """``(section, key)`` pairs one deal renders and the other does not."""
    return {
        f"{row['section']}::{row['key']}"
        for row in payload["structural_rows"]
        if (_cell(row, deal_id) or {}).get("present")
        and not (_cell(row, other_id) or {}).get("present")
    }


# ---------------------------------------------------------------------------
# The general half — what worked on the second deal with no change.
# ---------------------------------------------------------------------------


def test_both_clos_reach_the_comparison_with_structure(comparison: dict) -> None:
    """Two CLOs align in one panel, and neither is a column of blanks.

    This is the epic's headline claim and the cheapest thing to get wrong: a
    deal that fails to resolve is kept in the set with flags rather than
    dropped, so "both appear" has to mean *both carry structure*, not both
    appear as names. `has_structural` is what separates the two.
    """
    refs = {d["deal_id"]: d for d in comparison["deals"]}
    assert set(refs) == {CAIRN, CONTEGO}
    for deal_id in (CAIRN, CONTEGO):
        assert refs[deal_id]["has_structural"] is True, (
            f"{deal_id} is in the comparison set but renders no structure"
        )
        assert refs[deal_id]["jurisdiction"] == "Ireland"


def test_every_seniority_rank_carries_a_real_figure_for_both_clos(
    comparison: dict,
) -> None:
    """The aligned capital stack is the capability that generalised unchanged.

    Nothing in ``compare.py`` was touched to make Contego place; its extracted
    seed produced an eight-class stack and the existing tranche alignment read
    it. Every rank carries a positive balance for *both* deals — the assertion
    a blank second column would fail.
    """
    by_key = {row["key"]: row for row in comparison["structural_rows"]}
    missing = [k for k in TRANCHE_ROW_KEYS if k not in by_key]
    assert not missing, f"the panel rendered no row for {missing}"

    for key in TRANCHE_ROW_KEYS:
        for deal_id in (CAIRN, CONTEGO):
            cell = _cell(by_key[key], deal_id)
            assert cell is not None and cell["present"], f"{deal_id} absent on {key}"
            assert isinstance(cell["value"], float) and cell["value"] > 0.0, (
                f"{deal_id} renders {key} with no balance: {cell['value']!r}"
            )


def test_the_two_stacks_are_not_the_same_stack(comparison: dict) -> None:
    """Guards the test above against passing on a coincidence.

    Two deals reading the same numbers would satisfy every "both carry a real
    figure" assertion while proving nothing about the second deal. The stacks
    are genuinely different documents, so at least one rank must differ — and
    the totals must not coincide either.
    """
    by_key = {row["key"]: row for row in comparison["structural_rows"]}
    per_deal = {
        deal_id: [_cell(by_key[k], deal_id)["value"] for k in TRANCHE_ROW_KEYS]
        for deal_id in (CAIRN, CONTEGO)
    }
    assert per_deal[CAIRN] != per_deal[CONTEGO]
    assert sum(per_deal[CAIRN]) != sum(per_deal[CONTEGO])


# ---------------------------------------------------------------------------
# The fitted half — where the second deal stopped, and why, by name.
# ---------------------------------------------------------------------------


def test_both_clos_reach_the_panel_on_visibly_different_bases(
    comparison: dict,
) -> None:
    """The gap this module recorded has closed — and the asymmetry replaced it.

    #614 gave Contego a resolvable basis, so the old assertion (Contego absent)
    has served its purpose and fired. What replaces it is the property that now
    matters and did not exist before: both deals reach the panel, and **a reader
    cannot mistake one for the other**. Cairn folds reported history off its
    Note Valuation Report; Contego is a forward projection resting on a
    generated coupon. Asserting only that both are present would be the
    relaxation the old message warned against — so the two are pinned apart on
    every axis the payload carries.

    The date labels are the load-bearing half. Contego's periods are stamped
    ``projected-period-NN`` rather than reporting dates, which is what stops the
    overlay reading as two histories side by side.
    """
    series = {s["deal_id"]: s for s in comparison["performance_series"]}
    assert set(series) == {CAIRN, CONTEGO}

    assert {p["reporting_date"] for p in series[CAIRN]["points"]} == {CAIRN_PERIOD}
    contego_dates = {p["reporting_date"] for p in series[CONTEGO]["points"]}
    assert all(d.startswith("projected-period-") for d in contego_dates), (
        f"Contego's projected series is wearing reporting dates: {contego_dates}"
    )
    assert not (contego_dates & {CAIRN_PERIOD}), (
        "a projected period is passing itself off as Cairn's reported one"
    )

    risk = {r["deal_id"]: r for r in comparison["risk_summary"]}
    assert set(risk) == {CAIRN, CONTEGO}, "risk_summary drops a deal it should flag"
    assert risk[CAIRN]["latest_period"] == CAIRN_PERIOD


def test_a_deal_whose_basis_does_not_resolve_still_stays_out(
    client: TestClient,
) -> None:
    """The tripwire, re-aimed at the property that outlived Contego's move.

    The original guard fired when Contego crossed the line, which is what a
    tripwire is for; deleting it would leave the *next* deal registered without
    a basis to slide into the panel unnoticed. Green Lion 2023-1 is that deal
    today — registered, real, and refused by ``_resolve_structural_config`` —
    so it holds the line the old assertion held, on a subject that has not
    changed.
    """
    response = client.get(
        "/compare", params={"deals": f"{CAIRN},green-lion-2023-1"}
    )
    assert response.status_code == 200, response.text
    payload = response.json()

    series = {s["deal_id"]: s for s in payload["performance_series"]}
    assert "green-lion-2023-1" not in series, (
        "a deal whose structural config does not resolve reached the panel"
    )
    refs = {d["deal_id"]: d for d in payload["deals"]}
    assert refs["green-lion-2023-1"]["has_performance"] is False
    assert refs["green-lion-2023-1"]["rate_provenance"] is None, (
        "a deal with no series must claim no rate provenance"
    )
    assert refs["green-lion-2023-1"]["note"], "the blank column carries no reason"


def test_the_panel_says_what_each_series_rests_on(comparison: dict) -> None:
    """Honest presence: the *filled* column now needs the label the blank did.

    A blank column labelled "unavailable" was the old honesty. Contego's column
    is no longer blank, so the same discipline moves to what fills it: the two
    provenance axes have to state, separately, how the series was built and what
    the coupon under it came from. They vary independently — Cairn is
    reported-on-a-stated-rate, Contego projected-on-a-generated-one — and a
    payload that carried only one of them could not tell those apart.
    """
    refs = {d["deal_id"]: d for d in comparison["deals"]}

    assert refs[CAIRN]["has_performance"] is True
    assert refs[CAIRN]["performance_provenance"] == "reported"
    assert refs[CAIRN]["rate_provenance"] == "stated"

    assert refs[CONTEGO]["has_performance"] is True
    assert refs[CONTEGO]["performance_provenance"] == "projected"
    assert refs[CONTEGO]["rate_provenance"] == "synthetic"

    note = refs[CONTEGO]["note"] or ""
    assert note, "the projected-on-synthetic column carries no explanation"
    # The three facts a reader needs to tell a chosen number from a read one.
    assert "3-month EURIBOR" in note, f"the note names no tenor: {note}"
    assert "3.56" in note, f"the note names no assumed value: {note}"
    assert "no 3-month EURIBOR fixing is published" in note, (
        f"the note does not say the fixing is absent from the reports: {note}"
    )
    assert "5.41" in note, f"the note does not trace to the all-in coupon: {note}"
    assert any(CONTEGO in n for n in comparison["notes"]), (
        "the payload's notes do not mention the deal that rests on an assumption"
    )


def test_removing_the_synthetic_fixing_restores_the_coupon_refusal() -> None:
    """The falsification test: the fixing is load-bearing, not decorative.

    #493 made an unresolved coupon refuse rather than default to 0.0, and #614
    must not have undone that while appearing to. The only assertion that can
    tell "the fixing supplies the coupon" from "something now defaults it" is to
    take the fixing away and require the original refusal back, **naming the
    same key**.

    Deleting one registry key is the narrowest possible edit that distinguishes
    the two programs, which is what makes this a falsifier rather than a
    restatement: if a later change adds a default anywhere in the tier stack,
    this line reds while every other test in the file stays green.
    """
    from loanwhiz.api.main import _projected_series_from_canonical

    without = dict(DEAL_REGISTRY[CONTEGO])
    del without["synthetic_index_fixing"]

    assert _projected_series_from_canonical(CONTEGO, without) is None, (
        "a series still resolves with the synthetic fixing removed — a coupon "
        "is being defaulted somewhere, which is exactly what #493 forbids"
    )

    from loanwhiz.api.main import _resolve_structural_config

    with pytest.raises(HTTPException) as exc:
        _resolve_structural_config(CONTEGO, without)
    detail = str(exc.value.detail)
    assert exc.value.status_code == 422
    assert MISSING_COUPON_KEY in detail, (
        f"the restored refusal no longer names {MISSING_COUPON_KEY}: {detail}"
    )
    assert CONTEGO in detail

    # ...and with it present, the same resolver yields the fixing's all-in rate
    # rather than any other number: index 3.56 + margin 1.85.
    resolved, _, _ = _resolve_structural_config(CONTEGO, DEAL_REGISTRY[CONTEGO])
    assert resolved[MISSING_COUPON_KEY] == pytest.approx(5.41)


def test_the_missing_coupon_is_a_gap_in_the_extracted_seed() -> None:
    """The cause behind the refusal, one layer down — and why it is not a bug.

    The engine is not failing to read a rate that is there; the rate is not
    there. Contego's committed seed places a full eight-class stack and gives
    every class a null ``rate``, so the refusal is the engine declining to
    borrow another deal's coupon. Cairn's seed is the control: same route,
    same reader, a senior coupon present.
    """
    contego_rates = _seed_tranche_rates(CONTEGO)
    assert contego_rates, "Contego's seed places no tranches at all"
    assert all(rate is None for rate in contego_rates), (
        f"Contego's seed now carries a coupon ({contego_rates}) — the refusal "
        "above has a different cause and this module must be re-derived"
    )

    cairn_rates = _seed_tranche_rates(CAIRN)
    assert any(rate is not None for rate in cairn_rates), (
        "the control is vacuous: Cairn's seed carries no coupon either"
    )


# ---------------------------------------------------------------------------
# What each deal does that the other does not — kept visible on purpose.
# ---------------------------------------------------------------------------


def test_cairn_carries_a_per_class_cascade_contego_does_not(comparison: dict) -> None:
    """Cairn's 420-page Listing Particulars yielded structure Contego's did not.

    The per-class interest, deferred-interest and coverage-test-cure steps, and
    the per-class OC/IC triggers, are all Cairn-only. This is a real difference
    between two extractions, recorded rather than smoothed: the platform aligns
    the rows and marks the absent cells absent.
    """
    cairn_only = _rows_present_only_for(comparison, CAIRN, CONTEGO)
    for key in (
        "waterfall:revenue::class_a_interest",
        "waterfall:revenue::class_f_deferred_interest",
        "waterfall:revenue::class_c_coverage_test_cure",
        "trigger::class_b_oc_ratio",
        "trigger::class_d_ic_ratio",
    ):
        assert key in cairn_only, f"{key} is no longer Cairn-only"


def test_contego_carries_a_redemption_ladder_cairn_does_not(
    comparison: dict,
) -> None:
    """The second deal is not a subset of the first, and that is the point.

    Contego's model states a *redemption* cascade with its own expense ladder
    and a reserve replenishment; Cairn's does not. A comparison tuned to make
    the newer deal look like the older one would lose this. Asserted in the
    opposite direction to the test above so that flattening either way reds.
    """
    contego_only = _rows_present_only_for(comparison, CONTEGO, CAIRN)
    for key in (
        "waterfall:redemption::senior_expenses",
        "waterfall:redemption::administrative_expenses",
        "waterfall:redemption::reserve_replenishment",
        "waterfall:redemption::subordinated_management_fee",
        "waterfall:revenue::reserve_replenishment",
    ):
        assert key in contego_only, f"{key} is no longer Contego-only"


def test_the_differences_run_in_both_directions(comparison: dict) -> None:
    """A single guard against the whole comparison collapsing one way.

    If either set empties, the two deals have been made to agree — by a seed
    change, an alignment change, or a test tuned to the symmetry the issue
    explicitly warned against. Either is worth a human look.
    """
    cairn_only = _rows_present_only_for(comparison, CAIRN, CONTEGO)
    contego_only = _rows_present_only_for(comparison, CONTEGO, CAIRN)
    assert cairn_only and contego_only, (
        f"the two CLOs no longer differ in both directions: "
        f"{len(cairn_only)} Cairn-only, {len(contego_only)} Contego-only"
    )
