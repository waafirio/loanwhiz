"""The cross-deal industry taxonomy decision, as assertions (#563).

Every figure below is **re-derived from the committed report fixtures** at test
time rather than transcribed, so a card or docstring quoting a stale one reds
here instead of misinforming a reader (#441).

The decision these tests pin: a cross-deal industry concentration is expressed
in **Fitch**, joined by orthographic canonicalisation only, with no map across
GICS vintages and no residual bucket. The reason is measured, not asserted —
``test_fitch_joins_across_the_deals_where_sp_does_not`` computes both joins from
the two deals' own published concentration tables and compares them.
"""

from __future__ import annotations

import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import loanwhiz.primitives.base  # noqa: F401  (import-order guard)

import pytest
from pydantic import ValidationError

from loanwhiz.primitives.collateral_schedule_parser import (
    CollateralSchedule,
    parse_schedule_text,
)
from loanwhiz.primitives.industry_taxonomy import (
    CROSS_DEAL_TAXONOMY,
    DECLINED_CROSS_VINTAGE_PAIRS,
    IndustryTaxonomy,
    LabelCollisionError,
    TaxonomyJoin,
    canonical_label,
    canonicalise_vocabulary,
    join_vocabularies,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "collateral_schedule"

CAIRN = "cairn-clo-xvii"
CONTEGO = "contego-clo-xi"

#: Every committed collateral-schedule period, by deal. The vocabularies below
#: are taken across *all* of a deal's periods, because a deal's published
#: vocabulary genuinely moves between them — Cairn's ``Banks`` bucket is present
#: in December and gone by February — and a join built on one period would be a
#: join against an arbitrary cut.
PERIODS: dict[str, tuple[tuple[str, str], ...]] = {
    CAIRN: (
        ("cairn-clo-xvii-december-2024.txt", "December 2024"),
        ("cairn-clo-xvii-february-2025.txt", "February 2025"),
        ("cairn-clo-xvii-march-2025.txt", "March 2025"),
    ),
    CONTEGO: (
        ("contego-clo-xi-august-2024.txt", "August 2024"),
        ("contego-clo-xi-september-2024.txt", "September 2024"),
    ),
}


@lru_cache(maxsize=None)
def _parse(filename: str, period_label: str) -> CollateralSchedule:
    """Parse one committed period **strictly**, once per session.

    Cached because these are 15k-line reports and the assertions below read the
    same few periods repeatedly; parsing is pure and deterministic, so a shared
    result is the same result.

    ``strict=True`` reconciles the parse against the report's own stated totals
    and refuses on divergence, so every vocabulary below comes from a table that
    tied out. That matters here more than usual: the argument for choosing an
    axis is that its buckets are checked against the document that published
    them, and a vocabulary lifted from an unreconciled parse would not be.
    """
    text = (FIXTURE_DIR / filename).read_text(encoding="utf-8")
    return parse_schedule_text(text, period_label=period_label)


def _published_tables(deal: str, taxonomy: IndustryTaxonomy) -> list[list[str]]:
    """One label list per committed period — the report's own table, per period."""
    return [
        [
            bucket.label
            for bucket in getattr(
                _parse(filename, label).aggregates, taxonomy.asset_attribute
            )
        ]
        for filename, label in PERIODS[deal]
    ]


def _vocabulary(deal: str, taxonomy: IndustryTaxonomy) -> list[str]:
    """A deal's published vocabulary on one axis, across every committed period."""
    seen: dict[str, None] = {}
    for table in _published_tables(deal, taxonomy):
        for label in table:
            seen.setdefault(label, None)
    return list(seen)


def _join(taxonomy: IndustryTaxonomy) -> TaxonomyJoin:
    return join_vocabularies(
        taxonomy,
        left_deal=CAIRN,
        left=_vocabulary(CAIRN, taxonomy),
        right_deal=CONTEGO,
        right=_vocabulary(CONTEGO, taxonomy),
    )


def _unjoined_share(join: TaxonomyJoin) -> float:
    return len(join.unjoined_labels) / len(join.canonical_union)


# ---------------------------------------------------------------------------
# The canonicaliser is orthographic — and provably so, on real tables.
# ---------------------------------------------------------------------------


def test_canonical_label_folds_only_presentation() -> None:
    """Case, ``&``/``and``, punctuation and whitespace are one label's clothing."""
    assert canonical_label("Aerospace & Defense") == canonical_label(
        "aerospace  and   defense"
    )
    assert canonical_label("Oil, Gas & Consumable Fuels") == canonical_label(
        "Oil, gas, and consumable Fuels"
    )
    # ...and nothing beyond it. These are two sectors, not two spellings.
    assert canonical_label("Auto Components") != canonical_label(
        "Automobile components"
    )
    assert canonical_label("Aerospace and defense") != canonical_label(
        "Aerospace and defence"
    )


@pytest.mark.parametrize("deal", sorted(PERIODS))
@pytest.mark.parametrize("taxonomy", list(IndustryTaxonomy))
def test_canonicalisation_never_merges_two_buckets_one_table_publishes(
    deal: str, taxonomy: IndustryTaxonomy
) -> None:
    """The safety property, on every published table of every committed period.

    A report that prints two buckets considers them two buckets. If the fold
    collapsed them the join would restate the document's own table as something
    it does not say — and it would do so *upward*, since the merged bucket is
    larger than either. So the fold must be injective over each table as
    published, which ``canonicalise_vocabulary`` enforces at the seam.
    """
    for table in _published_tables(deal, taxonomy):
        assert table, f"{deal} publishes no {taxonomy.display_name} table"
        canonicalise_vocabulary(table)  # raises LabelCollisionError on a merge


def test_the_injectivity_guard_has_a_real_near_miss_to_survive() -> None:
    """Guards the test above against passing because nothing could collide.

    An all-pass property with no near-miss in its corpus never fires (#481).
    Cairn's December 2024 Fitch table publishes **both** ``Building and
    materials`` and ``Buildings and materials`` as separate buckets — a single
    trailing ``s``. The canonicaliser must keep them apart, and it is one
    plural-strip away from not doing so.
    """
    december = _parse("cairn-clo-xvii-december-2024.txt", "December 2024")
    labels = [bucket.label for bucket in december.aggregates.fitch_industry]
    assert "Building and materials" in labels
    assert "Buildings and materials" in labels
    assert canonical_label("Building and materials") != canonical_label(
        "Buildings and materials"
    )


def test_the_seam_refuses_a_collision_rather_than_merging_it() -> None:
    """The other direction of the same guard — it can actually fail.

    ``canonicalise_vocabulary`` accepting every real table proves nothing on its
    own; a function that never raises would pass it too (#493). Feed it two
    genuinely distinct labels that *do* share a canonical form and it must
    refuse, naming both.
    """
    with pytest.raises(LabelCollisionError) as raised:
        canonicalise_vocabulary(["Building and materials", "Building & materials"])
    assert "Building and materials" in str(raised.value)
    assert "Building & materials" in str(raised.value)

    # A label repeated across periods is not a collision — it is the same bucket.
    assert canonicalise_vocabulary(["Cable", "Cable"]) == {"cable": "Cable"}


# ---------------------------------------------------------------------------
# The join is a partition — nothing is dropped, nothing is swept into "Other".
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("taxonomy", list(IndustryTaxonomy))
def test_the_join_partitions_the_canonical_union(taxonomy: IndustryTaxonomy) -> None:
    """Joined, left-only and right-only cover the union exactly once each.

    This is the issue's "an unmappable category must remain visible" as an
    invariant rather than a promise: there is no fourth bucket to fold into, so
    a label that does not join has nowhere to go but its own side's list.
    """
    join = _join(taxonomy)
    left = canonicalise_vocabulary(_vocabulary(CAIRN, taxonomy))
    right = canonicalise_vocabulary(_vocabulary(CONTEGO, taxonomy))

    assert join.canonical_union == set(left) | set(right)
    assert len(join.joined) + len(join.left_only) + len(join.right_only) == len(
        join.canonical_union
    )
    # Every unjoined canonical form is reported under the label as published.
    assert set(join.unjoined_labels) == {
        left[key] for key in join.left_only
    } | {right[key] for key in join.right_only}


@pytest.mark.parametrize("taxonomy", list(IndustryTaxonomy))
def test_every_published_label_survives_the_join(taxonomy: IndustryTaxonomy) -> None:
    """No label is dropped: each side's every label is reachable from the result."""
    join = _join(taxonomy)
    reached = (
        {entry.left for entry in join.joined}
        | {entry.right for entry in join.joined}
        | set(join.unjoined_labels)
    )
    for deal in (CAIRN, CONTEGO):
        for label in _vocabulary(deal, taxonomy):
            assert label in reached, f"{deal} label {label!r} vanished in the join"


def test_a_join_that_lost_a_label_is_unrepresentable() -> None:
    """The partition is enforced by the model, not merely produced by the builder.

    A consumer handed a hand-built ``TaxonomyJoin`` gets the same guarantee, so
    the invariant does not depend on going through ``join_vocabularies``.
    """
    with pytest.raises(ValidationError):
        TaxonomyJoin(
            taxonomy=IndustryTaxonomy.fitch,
            left_deal=CAIRN,
            right_deal=CONTEGO,
            joined=[{"canonical": "cable", "left": "Cable", "right": "Cable"}],
            left_only={"cable": "Cable"},  # already joined — not a partition
        )


# ---------------------------------------------------------------------------
# Why Fitch — the measurement the decision rests on.
# ---------------------------------------------------------------------------


def test_fitch_joins_across_the_deals_where_sp_does_not() -> None:
    """The decision's whole basis, re-derived from the two deals' own tables.

    Both taxonomies are tied out per bucket against the report that published
    them, so the oracle does not choose between them. The join does: the deals'
    Fitch vocabularies land on substantially one vocabulary, while their S&P
    ones were published against different GICS vintages and do not.

    Asserted as a comparison plus a loose bound rather than exact counts, so a
    better canonicaliser or a new period is free to improve it — but it reds if
    the two ever come close, which is what would make choosing Fitch arbitrary.
    """
    fitch = _join(IndustryTaxonomy.fitch)
    sp = _join(IndustryTaxonomy.sp)

    assert _unjoined_share(fitch) < _unjoined_share(sp) / 2
    assert _unjoined_share(fitch) < 0.25
    assert _unjoined_share(sp) > 0.40
    assert CROSS_DEAL_TAXONOMY is IndustryTaxonomy.fitch


def test_the_sp_gap_is_vintage_drift_not_different_holdings() -> None:
    """Pin the *cause* of the S&P gap, not just its size.

    If the S&P vocabularies diverged because the deals hold different sectors,
    Fitch's would diverge the same way and the choice would be noise. They do
    not: the labels below are the same exposure under two GICS vintages, and
    each sits on its own side of the S&P join.
    """
    sp = _join(IndustryTaxonomy.sp)
    cairn_only = set(sp.left_only.values())
    contego_only = set(sp.right_only.values())

    assert {"Food & Staples Retailing", "Diversified Financial Services"} <= cairn_only
    assert {
        "Consumer staples distribution and retail",
        "Financial services",
    } <= contego_only


def test_each_declined_cross_vintage_pair_stays_on_its_own_side() -> None:
    """The refusal is real: no declined pair is quietly joined.

    ``DECLINED_CROSS_VINTAGE_PAIRS`` is documentation with a test attached, not
    a lookup table. Adding either half to any mapping — or weakening the fold
    until the two collide — reds here.
    """
    sp = _join(IndustryTaxonomy.sp)
    joined_spellings = {entry.left for entry in sp.joined} | {
        entry.right for entry in sp.joined
    }
    assert DECLINED_CROSS_VINTAGE_PAIRS, "the declined set must not be empty"

    for pair in DECLINED_CROSS_VINTAGE_PAIRS:
        assert pair.taxonomy is IndustryTaxonomy.sp
        assert pair.earlier in sp.left_only.values(), pair.earlier
        assert pair.later in sp.right_only.values(), pair.later
        assert pair.earlier not in joined_spellings
        assert pair.later not in joined_spellings
        assert canonical_label(pair.earlier) != canonical_label(pair.later)
        assert len(pair.reason) > 40, "a declined pair must say why"


# ---------------------------------------------------------------------------
# The figure must name its taxonomy.
# ---------------------------------------------------------------------------


def test_a_join_cannot_be_built_without_naming_its_taxonomy() -> None:
    """Enforced by the type, so a downstream figure cannot omit the axis."""
    with pytest.raises(TypeError):
        join_vocabularies(  # type: ignore[call-arg]
            left_deal=CAIRN, left=["Cable"], right_deal=CONTEGO, right=["Cable"]
        )
    with pytest.raises(ValidationError):
        TaxonomyJoin(left_deal=CAIRN, right_deal=CONTEGO)  # type: ignore[call-arg]

    assert _join(IndustryTaxonomy.fitch).describe().startswith("Fitch industry")
    assert _join(IndustryTaxonomy.sp).describe().startswith("S&P industry")


def _largest_industry_pct(text: str, marker: str) -> Decimal:
    """The percentage the report's own compliance row states for *marker*."""
    match = re.search(
        rf"{re.escape(marker)} Industry Concentration \(Largest Industry\)\s*"
        r"(\d+\.\d+)%",
        text,
    )
    assert match is not None, f"no {marker} largest-industry row in the report"
    return Decimal(match.group(1))


def test_the_two_taxonomies_report_different_largest_industry_figures() -> None:
    """Why naming the axis is mandatory, taken from the deal's own covenant rows.

    Cairn's March 2025 report tests the same portfolio against both taxonomies
    at the same limits and gets two different concentrations. A figure that does
    not say which one it is expressed in is therefore not merely imprecise — it
    is not comparable to either published number.
    """
    text = (FIXTURE_DIR / "cairn-clo-xvii-march-2025.txt").read_text(encoding="utf-8")
    sp_pct = _largest_industry_pct(text, "S&P")
    fitch_pct = _largest_industry_pct(text, "Fitch")
    assert sp_pct != fitch_pct

    # The module's own argument quotes these; a drifted docstring reds here.
    module = (
        REPO_ROOT / "src" / "loanwhiz" / "primitives" / "industry_taxonomy.py"
    ).read_text(encoding="utf-8")
    assert f"{sp_pct}%" in module
    assert f"{fitch_pct}%" in module


# ---------------------------------------------------------------------------
# The decision reaches the next registrant.
# ---------------------------------------------------------------------------


def test_the_data_card_records_the_decision_where_the_next_registrant_meets_it() -> None:
    """The card must state the axis, the refusal, and how to regenerate it.

    Re-derived, not transcribed (#441): the percentages come from the report and
    the axis name from the module, so a card that drifts from either reds here
    rather than misinforming a reader.
    """
    card = (REPO_ROOT / "docs" / "data-card.md").read_text(encoding="utf-8")
    text = (FIXTURE_DIR / "cairn-clo-xvii-march-2025.txt").read_text(encoding="utf-8")

    assert "industry_taxonomy" in card, "the card must name the module that decides"
    assert CROSS_DEAL_TAXONOMY.display_name in card
    assert f"{_largest_industry_pct(text, 'S&P')}%" in card
    assert f"{_largest_industry_pct(text, 'Fitch')}%" in card
    # The regeneration route, not the bucket counts (#441).
    assert "tests/test_industry_taxonomy.py" in card
    # The refusal has to survive as prose, or it becomes folklore.
    assert "GICS" in card
    assert "DECLINED_CROSS_VINTAGE_PAIRS" in card
