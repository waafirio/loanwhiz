"""Tests for the cross-deal relative-value / spread screener (#324).

The screener is the *quantitative* sibling of the qualitative deal-comparison
tool (#283): it ranks tranches ACROSS deals by structural relative value
(subordination/CE, WAL, trigger headroom, pool quality) into one comparable
scorecard. Like the capability matrix it runs offline over committed seed
models, and its load-bearing honesty contract is that dimensions needing live
period data are reported ``available=false`` with a real reason — never
fabricated.

These tests pin:
- the builder contract on synthetic models (per-dimension availability, the
  CE = junior/total computation, cross-cohort normalisation, the composite
  blending only available dimensions, deterministic cross-deal ranking),
- the honest edge cases (single-tranche deal, empty/low-completeness model,
  zero-trigger deal) that must not crash or fabricate, and
- the screener over the *real* shipped registry + committed seeds.
"""

from __future__ import annotations

from typing import Any

from loanwhiz.config import DEAL_REGISTRY
from loanwhiz.extraction.assembler import DealModel
from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY
from loanwhiz.primitives.relative_value_screener import (
    DEFAULT_WEIGHTS,
    DIM_POOL_QUALITY,
    DIM_SUBORDINATION_CE,
    DIM_TRIGGER_HEADROOM,
    DIM_WAL,
    DIMENSIONS,
    RelativeValueScorecard,
    RelativeValueScreener,
    RelativeValueScreenerInput,
    build_relative_value_scorecard,
    factor_subordination_ce,
    factor_trigger_headroom,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_model(
    *,
    tranches: list[dict[str, Any]],
    triggers: list[dict[str, Any]] | None = None,
    completeness: float = 0.8,
) -> DealModel:
    """Build a minimal in-memory DealModel for screener-contract tests."""
    triggers = triggers if triggers is not None else []
    return DealModel.model_validate(
        {
            "metadata": {
                "deal_name": "Fake Deal",
                "prospectus_url": "http://example/p.pdf",
                "extracted_at": "2026-01-01T00:00:00Z",
                "extraction_duration_sec": 0.0,
                "sections_found": [],
                "completeness_score": completeness,
                "cache_path": "",
            },
            "definitions": {},
            "waterfalls": {},
            "covenants": {
                "deal_name": "Fake Deal",
                "triggers": triggers,
                "issuer_covenants": [],
                "extraction_confidence": 0.6,
            },
            "tranche_structure": tranches,
            "trigger_names": [t.get("name", "?") for t in triggers],
        }
    )


def _abc_tranches() -> list[dict[str, Any]]:
    """A standard A/B/C capital structure (senior→junior)."""
    return [
        {"name": "Class A", "size_eur": 900.0, "rating": "AAA", "seniority": 0},
        {"name": "Class B", "size_eur": 70.0, "rating": "A", "seniority": 1},
        {"name": "Class C", "size_eur": 30.0, "rating": None, "seniority": 2},
    ]


def _row(card: RelativeValueScorecard, deal_id: str, tranche: str):
    for r in card.tranches:
        if r.deal_id == deal_id and r.tranche_name == tranche:
            return r
    raise AssertionError(f"no row for ({deal_id}, {tranche})")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_screener_is_registered_additively() -> None:
    reg = PRIMITIVE_REGISTRY.get("relative_value_screener")
    assert reg is not None
    assert reg.version == "1.0.0"
    # The additive registration did not disturb the existing primitives.
    assert PRIMITIVE_REGISTRY.get("esma_tape_normaliser") is not None
    assert PRIMITIVE_REGISTRY.get("covenant_monitor") is not None


# ---------------------------------------------------------------------------
# Subordination / CE — the always-available structural dimension
# ---------------------------------------------------------------------------


def test_ce_is_junior_capital_over_total() -> None:
    tranches = _abc_tranches()
    # Class A: 100 of 1000 sits junior -> CE = 0.10.
    a = factor_subordination_ce(tranches[0], tranches)
    assert a.available is True
    assert a.value == 0.10
    # Class B: 30 of 1000 junior -> 0.03.
    b = factor_subordination_ce(tranches[1], tranches)
    assert b.value == 0.03
    # Class C (junior-most): nothing below -> CE = 0.0 but STILL available.
    c = factor_subordination_ce(tranches[2], tranches)
    assert c.available is True
    assert c.value == 0.0


def test_ce_unavailable_when_no_sized_structure() -> None:
    tranches = [{"name": "Class A", "seniority": 0}]  # no size_eur
    f = factor_subordination_ce(tranches[0], tranches)
    assert f.available is False
    assert f.value is None
    assert f.reason.strip()


# ---------------------------------------------------------------------------
# WAL — structural proxy available, true WAL unavailable
# ---------------------------------------------------------------------------


def test_wal_proxy_available_but_flagged_structural() -> None:
    card = build_relative_value_scorecard(
        {"d": {"deal_name": "D"}},
        seed_loader=lambda _ctx: _fake_model(tranches=_abc_tranches()),
    )
    a = _row(card, "d", "Class A")
    wal = a.factors[DIM_WAL]
    assert wal.available is True
    assert wal.basis == "structural"
    assert "live period data" in wal.reason
    # Senior (shorter life) must score HIGHER than junior on the inverted WAL.
    c = _row(card, "d", "Class C")
    assert a.factors[DIM_WAL].score > c.factors[DIM_WAL].score


# ---------------------------------------------------------------------------
# Trigger headroom — coverage proxy; live numeric form unavailable
# ---------------------------------------------------------------------------


def test_trigger_headroom_coverage_proxy() -> None:
    model = _fake_model(
        tranches=_abc_tranches(),
        triggers=[
            {"name": "t1", "metric": "m", "threshold": 0.05},  # quantified
            {"name": "t2", "metric": "m", "threshold": None},  # qualitative
        ],
    )
    f = factor_trigger_headroom(_abc_tranches()[0], model)
    assert f.available is True
    assert f.value == 0.5  # 1 of 2 quantified


def test_trigger_headroom_unavailable_with_no_triggers() -> None:
    model = _fake_model(tranches=_abc_tranches(), triggers=[])
    f = factor_trigger_headroom(_abc_tranches()[0], model)
    assert f.available is False
    assert f.reason.strip()


# ---------------------------------------------------------------------------
# Pool quality — completeness proxy; true pool quality unavailable
# ---------------------------------------------------------------------------


def test_pool_quality_proxy_and_unavailable_without_model() -> None:
    card = build_relative_value_scorecard(
        {
            "good": {"deal_name": "Good"},
            "nomodel": {"deal_name": "NoModel"},
        },
        seed_loader=lambda ctx: (
            _fake_model(tranches=_abc_tranches(), completeness=0.9)
            if ctx["deal_name"] == "Good"
            else None
        ),
    )
    # The no-model deal produced no rows at all (no tranche structure).
    assert all(r.deal_id != "nomodel" for r in card.tranches)
    pq = _row(card, "good", "Class A").factors[DIM_POOL_QUALITY]
    assert pq.available is True
    assert pq.value == 0.9
    assert "ESMA tape" in pq.reason


# ---------------------------------------------------------------------------
# Normalisation + composite + ranking
# ---------------------------------------------------------------------------


def test_composite_blends_only_available_dimensions() -> None:
    # A lone single-tranche deal: WAL & pool-quality available, but CE has no
    # junior capital (CE=0, still available) and only-one-value normalisation
    # makes every sub-score the neutral 50. The composite must be a real number.
    card = build_relative_value_scorecard(
        {"solo": {"deal_name": "Solo"}},
        seed_loader=lambda _ctx: _fake_model(
            tranches=[{"name": "Class A", "size_eur": 100.0, "seniority": 0}],
            triggers=[{"name": "t", "metric": "m", "threshold": 0.1}],
        ),
    )
    r = _row(card, "solo", "Class A")
    assert r.composite_score is not None
    assert r.rank == 1
    # Single-member cohort -> every available sub-score is the neutral midpoint.
    for dim in DIMENSIONS:
        f = r.factors[dim]
        if f.available:
            assert f.score == 50.0


def test_ranking_is_cross_deal_and_deterministic() -> None:
    deals = {"d1": {"deal_name": "D1"}, "d2": {"deal_name": "D2"}}

    def loader(ctx):
        if ctx["deal_name"] == "D1":
            # Strong deal: 3 quantified triggers, high completeness.
            return _fake_model(
                tranches=_abc_tranches(),
                triggers=[{"name": f"t{i}", "metric": "m", "threshold": 0.1} for i in range(3)],
                completeness=1.0,
            )
        # Weak deal: qualitative triggers, low completeness.
        return _fake_model(
            tranches=_abc_tranches(),
            triggers=[{"name": "t", "metric": "m", "threshold": None}],
            completeness=0.3,
        )

    card_a = build_relative_value_scorecard(deals, seed_loader=loader)
    card_b = build_relative_value_scorecard(deals, seed_loader=loader)
    # Deterministic: identical ranking across two builds.
    assert [(r.deal_id, r.tranche_name, r.rank) for r in card_a.tranches] == [
        (r.deal_id, r.tranche_name, r.rank) for r in card_b.tranches
    ]
    # Ranks are a contiguous 1..N with no gaps (all rows scored here).
    ranks = sorted(r.rank for r in card_a.tranches)
    assert ranks == list(range(1, len(card_a.tranches) + 1))
    # The strong deal's senior tranche ranks first.
    assert card_a.tranches[0].deal_id == "d1"
    assert card_a.tranches[0].tranche_name == "Class A"


def test_empty_or_no_tranche_deal_is_skipped_without_crashing() -> None:
    card = build_relative_value_scorecard(
        {
            "empty": {"deal_name": "Empty"},
            "real": {"deal_name": "Real"},
        },
        seed_loader=lambda ctx: (
            _fake_model(tranches=[]) if ctx["deal_name"] == "Empty"
            else _fake_model(tranches=_abc_tranches())
        ),
    )
    assert card.tally["deals_screened"] == 1
    assert all(r.deal_id == "real" for r in card.tranches)


def test_weights_default_sums_to_one() -> None:
    assert abs(sum(DEFAULT_WEIGHTS.values()) - 1.0) < 1e-9
    assert set(DEFAULT_WEIGHTS) == set(DIMENSIONS)


# ---------------------------------------------------------------------------
# Real registry + committed seeds (integration)
# ---------------------------------------------------------------------------


def test_screener_over_real_registry() -> None:
    from loanwhiz.api.main import _load_cached_deal_model

    card = build_relative_value_scorecard(
        DEAL_REGISTRY, seed_loader=_load_cached_deal_model
    )
    assert isinstance(card, RelativeValueScorecard)
    # At least the Green Lion deals carry A/B/C structures -> multiple tranches.
    assert card.tally["tranches_scored"] >= 3
    # Every row has all four dimensions present (available or honestly not).
    for r in card.tranches:
        assert set(r.factors) == set(DIMENSIONS)
        for dim, f in r.factors.items():
            assert f.reason.strip(), (r.deal_id, dim)
            if not f.available:
                # Honesty contract: unavailable factors carry no fabricated value.
                assert f.value is None and f.score is None
    # Scored rows have a contiguous rank prefix.
    scored = [r for r in card.tranches if r.composite_score is not None]
    assert [r.rank for r in scored] == list(range(1, len(scored) + 1))


# ---------------------------------------------------------------------------
# Primitive wrapper
# ---------------------------------------------------------------------------


def test_primitive_wrapper_returns_scorecard_with_confidence() -> None:
    model = _fake_model(
        tranches=_abc_tranches(),
        triggers=[{"name": "t", "metric": "m", "threshold": 0.1}],
        completeness=0.9,
    )
    screener = RelativeValueScreener(seed_loader=lambda _ctx: model)
    result = screener.execute(
        RelativeValueScreenerInput(deals={"d": {"deal_name": "D"}})
    )
    assert isinstance(result.output.scorecard, RelativeValueScorecard)
    assert 0.0 <= result.confidence <= 1.0
    assert result.confidence > 0.0  # at least some dimensions available
    assert result.citations
    assert result.audit_entry.primitive_name == "relative_value_screener"


def test_bare_primitive_is_constructible_and_returns_empty() -> None:
    # A bare RelativeValueScreener() uses a no-op loader -> empty scorecard,
    # so it stays constructible for registry introspection.
    screener = RelativeValueScreener()
    result = screener.execute(
        RelativeValueScreenerInput(deals={"d": {"deal_name": "D"}})
    )
    assert result.output.scorecard.tally["tranches_scored"] == 0
    assert result.confidence == 0.0
