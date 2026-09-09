"""Cross-deal look-through exposure, and the residuals it must report (#564).

The suite is arranged around the same asymmetry as #562's. Every failure mode
here makes a book read as **more diversified than it is** — the direction that
harms a buyer and looks like good news:

- a keyed re-join that drops rows shrinks the denominator every concentration
  divides by, so the census tests prove nothing was lost;
- a residual netted into a bucket, or into an "Other", turns "we could not
  resolve this" into "this is a small holding", so the residual tests prove
  both residuals stay outside every bucket;
- a label fold that is too strong merges buckets that should stay apart —
  ``B``, ``B+`` and ``B-`` onto one rating — so the fold tests prove each axis
  keeps what distinguishes its labels;
- a figure that names neither its axis nor its as-of dates cannot be checked
  against either report at all, so those are asserted to be unconstructible
  rather than merely absent.

Refusals are paired both ways per #493: supply the one missing input and assert
the same record flips, so a refusal reached for the wrong reason cannot pass for
the right one. The two committed schedules are the fixture because a join across
two naming conventions cannot be validated on one (#481).
"""

from __future__ import annotations

import loanwhiz.primitives.base  # noqa: F401  (import-order guard)

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import pytest
from pydantic import ValidationError

from loanwhiz.primitives import cross_deal_exposure
from loanwhiz.primitives.collateral_schedule_parser import (
    CollateralAsset,
    CollateralSchedule,
    parse_schedule_text,
)
from loanwhiz.primitives.cross_deal_exposure import (
    AssetAttributes,
    AttributeAbsence,
    AxisKind,
    BucketedExposure,
    CrossDealPortfolio,
    DealAsOf,
    DealContribution,
    ExposureAxis,
    ExposureBucket,
    ObligorExposure,
    ObligorTier,
    RatingAgency,
    ResolutionSplit,
    UnattributedExposure,
    _assert_every_asset_attributed,
    _currency_code,
    aggregate_by_axis,
    aggregate_by_obligor,
    build_portfolio,
    country_axis,
    cross_deal_industry_axis,
    industry_axis,
    rating_axis,
)
from loanwhiz.primitives.industry_taxonomy import (
    CROSS_DEAL_TAXONOMY,
    IndustryTaxonomy,
    LabelCollisionError,
    canonical_label,
)
from loanwhiz.primitives.obligor_resolution import ObligorBounds

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "collateral_schedule"

CAIRN = "cairn-clo-xvii"
CONTEGO = "contego-clo-xi"


@lru_cache(maxsize=None)
def _schedule(filename: str, period_label: str) -> CollateralSchedule:
    return parse_schedule_text(
        FIXTURE_DIR.joinpath(filename).read_text(), period_label=period_label
    )


@lru_cache(maxsize=None)
def _committed() -> CrossDealPortfolio:
    return build_portfolio(
        {
            CAIRN: _schedule("cairn-clo-xvii-march-2025.txt", "2025-03"),
            CONTEGO: _schedule("contego-clo-xi-august-2024.txt", "2024-08"),
        }
    )


def _bucketed_axes() -> list[ExposureAxis]:
    """Every axis this module offers, so a property is asserted on all of them."""
    return [
        cross_deal_industry_axis(),
        industry_axis(IndustryTaxonomy.sp),
        country_axis(),
        rating_axis(RatingAgency.fitch),
        rating_axis(RatingAgency.sp),
    ]


def _asset(
    identifier: str,
    issuer: str,
    *,
    balance: str = "1000000.00",
    industry: str | None = "Chemicals",
    country: str | None = "France",
    rating: str | None = None,
    currency: str | None = "EUR",
    facility: str = "Facility B",
) -> CollateralAsset:
    return CollateralAsset(
        identifier=identifier,
        issuer_name=issuer,
        facility_name=facility,
        principal_balance=Decimal(balance),
        fitch_industry=industry,
        sp_industry=industry,
        country=country,
        fitch_rating=rating,
        sp_rating=rating,
        currency=currency,
    )


def _sched(
    *assets: CollateralAsset,
    period_label: str = "2025-03",
    reporting_date: str | None = "18/03/2025",
) -> CollateralSchedule:
    return CollateralSchedule(
        period_label=period_label,
        reporting_date=reporting_date,
        assets=list(assets),
    )


def _contribution(deal: str, balance: str, count: int = 1) -> DealContribution:
    return DealContribution(
        deal=deal, as_of="18/03/2025", balance=Decimal(balance), asset_count=count
    )


# ---------------------------------------------------------------------------
# Criterion: the re-join keeps every asset, and the builder proves it
# ---------------------------------------------------------------------------


def test_every_asset_is_attributed_exactly_once() -> None:
    """The whole point of the second index: it holds what #562 resolved."""
    portfolio = _committed()
    resolved = {
        member.key
        for group in portfolio.resolution.all_groups
        for member in group.members
    }
    attributed = [a.key for a in portfolio.attributes]

    assert len(attributed) == len(set(attributed))
    assert set(attributed) == resolved
    assert portfolio.asset_count == portfolio.resolution.asset_count


def test_a_name_keyed_attribute_index_would_lose_rows_on_the_committed_deals() -> None:
    """The premise the ``(deal, identifier)`` key rests on, measured on real data.

    Re-derived at read time rather than transcribed, so it cannot go stale
    against the fixtures.
    """
    portfolio = _committed()
    for deal in portfolio.deals:
        assets = [a for a in portfolio.attributes if a.deal == deal]
        identifiers = [a.identifier for a in assets]
        assert len(set(identifiers)) == len(identifiers)

    names = [
        member.issuer_name
        for group in portfolio.resolution.all_groups
        for member in group.members
    ]
    assert len(set(names)) < len(names), "a name-keyed index would lose rows here"


def test_a_name_keyed_attribute_index_is_caught_by_the_builder_census() -> None:
    """Key the index the obvious way and the census refuses it, loudly."""
    portfolio = _committed()
    by_name = {
        member.issuer_name: attributes
        for group in portfolio.resolution.all_groups
        for member in group.members
        for attributes in [next(a for a in portfolio.attributes if a.key == member.key)]
    }
    collapsed = portfolio.model_copy(update={"attributes": tuple(by_name.values())})

    with pytest.raises(ValueError, match="does not account for the resolved assets"):
        _assert_every_asset_attributed(portfolio.resolution, collapsed)


def test_the_builder_actually_runs_the_census(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard is wired into ``build_portfolio``, not merely defined beside it.

    Without this, deleting the ``_assert_every_asset_attributed(...)`` call reds
    nothing: every other census test calls the guard directly, so they prove the
    function works while saying nothing about whether anything invokes it. "The
    checker found nothing" and "nothing ran the checker" are one silence — which
    is the failure mode the guard itself exists to prevent (#562).

    The patch drops one attribute row from the portfolio the builder is about to
    return, standing in for any re-join bug that loses a row.
    """
    real = cross_deal_exposure.CrossDealPortfolio

    def lossy(**kwargs: object) -> CrossDealPortfolio:
        attributes = kwargs.get("attributes", ())
        assert isinstance(attributes, tuple) and attributes, "fixture must have assets"
        return real(**{**kwargs, "attributes": attributes[:-1]})  # type: ignore[arg-type]

    monkeypatch.setattr(cross_deal_exposure, "CrossDealPortfolio", lossy)
    with pytest.raises(ValueError, match="does not account for the resolved assets"):
        build_portfolio(
            {
                "a": _sched(_asset("LX1", "Alpha Ltd"), _asset("LX2", "Beta Ltd")),
                "b": _sched(_asset("LX3", "Gamma Ltd")),
            }
        )


def test_the_census_refuses_an_invented_asset() -> None:
    """The opposite direction: a row the resolution never saw is also a defect."""
    portfolio = _committed()
    invented = portfolio.attributes + (
        AssetAttributes(deal=CAIRN, identifier="LX-NOT-REAL", principal_balance=Decimal(1)),
    )
    with pytest.raises(ValueError, match="invented"):
        _assert_every_asset_attributed(
            portfolio.resolution, portfolio.model_copy(update={"attributes": invented})
        )


def test_the_census_refuses_a_balance_that_disagrees_with_the_resolution() -> None:
    """An index that resolved a key to the wrong row keeps the count and moves money."""
    portfolio = _committed()
    first, *rest = portfolio.attributes
    tampered = (first.model_copy(update={"principal_balance": Decimal("1.00")}), *rest)

    with pytest.raises(ValueError, match="in the attribute index but"):
        _assert_every_asset_attributed(
            portfolio.resolution, portfolio.model_copy(update={"attributes": tampered})
        )


def test_the_census_is_silent_on_an_intact_portfolio() -> None:
    """The paired direction: the same guard says nothing when nothing was lost."""
    portfolio = _committed()
    _assert_every_asset_attributed(portfolio.resolution, portfolio)


def test_a_duplicate_key_in_the_index_is_refused_rather_than_deduped() -> None:
    portfolio = _committed()
    doubled = portfolio.attributes + (portfolio.attributes[0],)
    with pytest.raises(ValueError, match="not unique on"):
        _assert_every_asset_attributed(
            portfolio.resolution, portfolio.model_copy(update={"attributes": doubled})
        )


# ---------------------------------------------------------------------------
# Criterion: a figure cannot be built without naming its axis
# ---------------------------------------------------------------------------


def test_an_industry_axis_cannot_be_built_without_its_taxonomy() -> None:
    """#563's requirement, enforced by the type rather than by a docstring."""
    with pytest.raises(ValidationError, match="must name its taxonomy"):
        ExposureAxis(kind=AxisKind.industry)


def test_a_rating_axis_cannot_be_built_without_its_agency() -> None:
    with pytest.raises(ValidationError, match="must name its agency"):
        ExposureAxis(kind=AxisKind.rating)


def test_each_axis_accepts_the_name_it_requires() -> None:
    """The paired direction: supply the one missing input and the axis exists."""
    assert ExposureAxis(kind=AxisKind.industry, taxonomy=IndustryTaxonomy.fitch).label == (
        "Fitch industry"
    )
    assert ExposureAxis(kind=AxisKind.rating, agency=RatingAgency.sp).label == "S&P rating"
    assert country_axis().label == "country"


def test_an_axis_refuses_a_name_it_does_not_use() -> None:
    """A country figure claiming a taxonomy would name an axis it never applied."""
    with pytest.raises(ValidationError, match="neither taxonomy nor agency"):
        ExposureAxis(kind=AxisKind.country, taxonomy=IndustryTaxonomy.fitch)
    with pytest.raises(ValidationError, match="does not take a rating agency"):
        ExposureAxis(kind=AxisKind.industry, taxonomy=IndustryTaxonomy.fitch, agency=RatingAgency.sp)
    with pytest.raises(ValidationError, match="does not take an industry taxonomy"):
        ExposureAxis(kind=AxisKind.rating, agency=RatingAgency.sp, taxonomy=IndustryTaxonomy.sp)


def test_the_cross_deal_industry_axis_is_the_one_563_settled() -> None:
    assert cross_deal_industry_axis().taxonomy is CROSS_DEAL_TAXONOMY
    assert CROSS_DEAL_TAXONOMY is IndustryTaxonomy.fitch


def test_every_figure_names_its_axis_in_its_own_description() -> None:
    """The figure carries the axis in prose, not only in a field a caller may skip."""
    portfolio = _committed()
    for axis in _bucketed_axes():
        figure = aggregate_by_axis(portfolio, axis)
        assert axis.label in figure.describe()
        assert axis.label in figure.describe_bucket(figure.buckets[0])


# ---------------------------------------------------------------------------
# Criterion: each axis folds its own labels, and a wrong fold raises
# ---------------------------------------------------------------------------


def test_the_industry_fold_applied_to_ratings_would_merge_notches_and_is_refused() -> None:
    """The live near-miss: ``canonical_label`` drops ``+`` and ``-``.

    Contego publishes ``B`` and ``B+`` in one report, so an axis folding ratings
    the way industries are folded would put three notches in one bucket and make
    the book read better than it is. The injectivity check turns that silent
    merge into a refusal — this is the `fires-when:` input for that checker.
    """
    published = ["B", "B+", "B-"]
    assert len({canonical_label(r) for r in published}) == 1, (
        "premise: the industry fold really does collapse these three notches"
    )
    with pytest.raises(LabelCollisionError, match="restate the report's own table"):
        cross_deal_exposure._fold_one_deals_vocabulary(
            ExposureAxis(kind=AxisKind.industry, taxonomy=IndustryTaxonomy.fitch),
            CONTEGO,
            published,
        )


def test_the_rating_fold_keeps_the_notch_on_the_committed_deals() -> None:
    """The paired direction: the axis's own fold keeps the three apart."""
    figure = aggregate_by_axis(_committed(), rating_axis(RatingAgency.fitch))
    labels = {bucket.label for bucket in figure.buckets}
    assert {"B", "B+", "B-"} <= labels


def test_two_deals_spellings_of_one_label_join_rather_than_splitting() -> None:
    """A cross-deal spelling difference is a join, not two thin buckets (#563)."""
    figure = aggregate_by_axis(_committed(), cross_deal_industry_axis())
    joined = [bucket for bucket in figure.buckets if bucket.spellings_differ]
    assert joined, "the two committed Fitch vocabularies do differ in spelling"
    for bucket in joined:
        assert len(bucket.per_deal) == 2, "a joined spelling means both deals reached it"


def test_a_within_deal_collision_is_refused_rather_than_merged() -> None:
    """Two labels one report prints separately must never become one bucket."""
    with pytest.raises(LabelCollisionError, match="restate the report's own table"):
        cross_deal_exposure._fold_one_deals_vocabulary(
            cross_deal_industry_axis(), CAIRN, ["Aerospace & Defence", "Aerospace and defence"]
        )


def test_a_label_that_folds_to_nothing_is_refused_rather_than_bucketed() -> None:
    with pytest.raises(ValueError, match="folds to nothing"):
        cross_deal_exposure._fold_one_deals_vocabulary(
            cross_deal_industry_axis(), CAIRN, ["***"]
        )


# ---------------------------------------------------------------------------
# Criterion: neither residual is ever netted into a bucket
# ---------------------------------------------------------------------------


def test_no_bucket_on_any_axis_names_a_residual() -> None:
    portfolio = _committed()
    for axis in _bucketed_axes():
        figure = aggregate_by_axis(portfolio, axis)
        for bucket in figure.buckets:
            assert axis.fold(bucket.label) not in cross_deal_exposure._RESIDUAL_LABELS


def test_a_residual_named_bucket_is_refused_at_construction() -> None:
    """Reaching an "Other" bucket by accident is refused, not reported."""
    other = ExposureBucket(
        label="Other",
        published_spellings=("Other",),
        balance=Decimal("10"),
        asset_count=1,
        split=ResolutionSplit(unresolved=Decimal("10")),
        per_deal=(_contribution(CAIRN, "10"),),
    )
    with pytest.raises(ValidationError, match="names a residual rather than a holding"):
        BucketedExposure(
            axis=country_axis(),
            as_of=(DealAsOf(deal=CAIRN, period_label="2025-03"),),
            buckets=(other,),
            unattributed=UnattributedExposure(),
            bounds=ObligorBounds(lower=1, upper=1),
            total_balance=Decimal("10"),
        )


def test_a_bucket_that_names_a_real_holding_is_accepted() -> None:
    """The paired direction: the guard is about residuals, not about all labels."""
    figure = BucketedExposure(
        axis=country_axis(),
        as_of=(DealAsOf(deal=CAIRN, period_label="2025-03"),),
        buckets=(
            ExposureBucket(
                label="France",
                published_spellings=("France",),
                balance=Decimal("10"),
                asset_count=1,
                split=ResolutionSplit(unresolved=Decimal("10")),
                per_deal=(_contribution(CAIRN, "10"),),
            ),
        ),
        unattributed=UnattributedExposure(),
        bounds=ObligorBounds(lower=1, upper=1),
        total_balance=Decimal("10"),
    )
    assert figure.buckets[0].label == "France"


def test_unattributed_is_a_different_record_kind_from_a_bucket() -> None:
    """#513: an empty instance of the reported kind would read as free green."""
    figure = aggregate_by_axis(_committed(), rating_axis(RatingAgency.fitch))
    assert isinstance(figure.unattributed, UnattributedExposure)
    assert not isinstance(figure.unattributed, ExposureBucket)
    assert figure.unattributed not in figure.buckets
    assert figure.unattributed.reason is AttributeAbsence.not_published


def test_the_rating_axis_reports_its_hole_at_full_size() -> None:
    """Cairn publishes no Fitch rating, and the figure says so rather than hiding it.

    Re-derived from the fixtures at read time. The finding recorded in
    ``docs/data-card.md`` is this assertion, not a transcribed percentage.
    """
    portfolio = _committed()
    figure = aggregate_by_axis(portfolio, rating_axis(RatingAgency.fitch))

    assert not figure.unattributed.is_empty
    unplaced_deals = {c.deal for c in figure.unattributed.per_deal}
    assert CAIRN in unplaced_deals
    cairn_unplaced = next(c for c in figure.unattributed.per_deal if c.deal == CAIRN)
    cairn_total = sum(
        (a.principal_balance for a in portfolio.attributes if a.deal == CAIRN), Decimal(0)
    )
    assert cairn_unplaced.balance == cairn_total, (
        "every Cairn asset is unplaced on the Fitch rating axis"
    )
    for bucket in figure.buckets:
        assert CAIRN not in bucket.deals


def test_an_industry_axis_places_everything_the_rating_axis_cannot() -> None:
    """The paired direction: the hole is the rating axis's, not the module's."""
    figure = aggregate_by_axis(_committed(), cross_deal_industry_axis())
    assert figure.unattributed.is_empty
    assert figure.unattributed.balance == Decimal(0)
    assert figure.attributed_balance == figure.total_balance


def test_buckets_plus_unattributed_equal_the_portfolio_total_on_every_axis() -> None:
    portfolio = _committed()
    for axis in _bucketed_axes():
        figure = aggregate_by_axis(portfolio, axis)
        placed = sum((bucket.balance for bucket in figure.buckets), Decimal(0))
        assert placed + figure.unattributed.balance == portfolio.total_principal_balance
        counted = sum(bucket.asset_count for bucket in figure.buckets)
        assert counted + figure.unattributed.asset_count == portfolio.asset_count


def test_a_share_is_taken_over_the_whole_book_not_over_what_was_placed() -> None:
    """Rescaling by the attributed balance would turn a coverage hole into a
    stronger-looking concentration, in exact proportion to the hole."""
    figure = aggregate_by_axis(_committed(), rating_axis(RatingAgency.fitch))
    biggest = figure.buckets[0]
    over_whole = figure.share(biggest)
    over_placed = (biggest.balance * 100 / figure.attributed_balance).quantize(Decimal("0.01"))
    assert over_whole < over_placed


def test_the_axis_builder_actually_runs_its_population_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second wiring assertion: dropping the call must red something.

    The lossy factory drops a bucket *and* lowers the stated total to match, so
    the model validator is satisfied and only the builder's population check can
    catch it — which is exactly the shape of a netting bug that keeps the
    arithmetic intact while changing what the figure says.
    """
    real = cross_deal_exposure.BucketedExposure

    def lossy(**kwargs: object) -> BucketedExposure:
        buckets = kwargs.get("buckets", ())
        total = kwargs.get("total_balance", Decimal(0))
        assert isinstance(buckets, tuple) and buckets, "fixture must have buckets"
        assert isinstance(total, Decimal)
        return real(
            **{
                **kwargs,
                "buckets": buckets[:-1],
                "total_balance": total - buckets[-1].balance,
            }  # type: ignore[arg-type]
        )

    monkeypatch.setattr(cross_deal_exposure, "BucketedExposure", lossy)
    with pytest.raises(ValueError, match="against the portfolio's"):
        aggregate_by_axis(_committed(), country_axis())


def test_the_obligor_builder_actually_runs_its_population_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = cross_deal_exposure.ObligorExposure

    def lossy(**kwargs: object) -> ObligorExposure:
        rows = kwargs.get("rows", ())
        total = kwargs.get("total_balance", Decimal(0))
        assert isinstance(rows, tuple) and rows
        assert isinstance(total, Decimal)
        return real(
            **{**kwargs, "rows": rows[:-1], "total_balance": total - rows[-1].balance}  # type: ignore[arg-type]
        )

    monkeypatch.setattr(cross_deal_exposure, "ObligorExposure", lossy)
    with pytest.raises(ValueError, match="against the portfolio's"):
        aggregate_by_obligor(_committed())


def test_a_figure_whose_parts_do_not_reach_its_total_is_refused() -> None:
    with pytest.raises(ValidationError, match="account for"):
        BucketedExposure(
            axis=country_axis(),
            as_of=(DealAsOf(deal=CAIRN, period_label="2025-03"),),
            buckets=(),
            unattributed=UnattributedExposure(),
            bounds=ObligorBounds(lower=0, upper=0),
            total_balance=Decimal("10"),
        )


def test_an_unattributed_record_claiming_balance_from_nowhere_is_refused() -> None:
    with pytest.raises(ValidationError, match="names no deal but claims"):
        UnattributedExposure(balance=Decimal("10"), asset_count=1)


# ---------------------------------------------------------------------------
# Criterion: the obligor tier split is real, and moves
# ---------------------------------------------------------------------------


def test_every_buckets_tier_split_re_derives_to_its_balance() -> None:
    portfolio = _committed()
    for axis in _bucketed_axes():
        for bucket in aggregate_by_axis(portfolio, axis).buckets:
            assert bucket.split.total == bucket.balance


def _tier_of_each_asset(portfolio: CrossDealPortfolio) -> dict[tuple[str, str], ObligorTier]:
    """Re-derive each asset's tier from the resolution, independently of the module.

    Deliberately a second implementation rather than a call into
    ``_tier_by_asset``: a test that reuses the builder's own classifier agrees
    with it by construction, including when both are wrong.
    """
    resolution = portfolio.resolution
    proposed = {
        tuple(sorted(m.key for m in group.members))
        for candidate in resolution.candidates
        for group in (candidate.left, candidate.right)
    }
    tiers: dict[tuple[str, str], ObligorTier] = {}
    for group in resolution.proven_shared:
        for member in group.members:
            tiers[member.key] = ObligorTier.proven_shared
    for entry in resolution.unresolved:
        key = tuple(sorted(m.key for m in entry.group.members))
        tier = (
            ObligorTier.candidate_proposed if key in proposed else ObligorTier.unresolved
        )
        for member in entry.group.members:
            tiers[member.key] = tier
    return tiers


def test_each_tiers_balance_is_the_balance_of_the_assets_really_in_that_tier() -> None:
    """The classification, not just the conservation law.

    ``split.total == balance`` survives *any* netting between tiers — moving the
    unresolved balance into ``proven_shared`` keeps it exactly true while turning
    "we could not join these names" into "we proved this is one borrower", the
    overstatement the split exists to prevent. So each tier is re-derived here
    from #562's resolution and compared bucket by bucket.
    """
    portfolio = _committed()
    tiers = _tier_of_each_asset(portfolio)
    assert set(tiers) == {a.key for a in portfolio.attributes}

    for axis in _bucketed_axes():
        figure = aggregate_by_axis(portfolio, axis)
        expected: dict[str, dict[ObligorTier, Decimal]] = {}
        for attributes in portfolio.attributes:
            value = attributes.published_on(axis)
            if value is None:
                continue
            parts = expected.setdefault(
                axis.fold(value), {tier: Decimal(0) for tier in ObligorTier}
            )
            parts[tiers[attributes.key]] += attributes.principal_balance

        for bucket in figure.buckets:
            parts = expected[axis.fold(bucket.label)]
            for tier in ObligorTier:
                assert getattr(bucket.split, tier.value) == parts[tier], (
                    f"{axis.label} bucket {bucket.label!r} misreports its {tier.value} tier"
                )


def test_the_committed_book_exercises_all_three_tiers_on_every_axis() -> None:
    """Otherwise the tier assertions above could pass on an all-one-tier book."""
    portfolio = _committed()
    for axis in _bucketed_axes():
        figure = aggregate_by_axis(portfolio, axis)
        for tier in ObligorTier:
            assert any(
                getattr(bucket.split, tier.value) > 0 for bucket in figure.buckets
            ), f"no {tier.value} balance on {axis.label}"


def test_a_bucket_cannot_assert_a_split_that_misses_its_balance() -> None:
    with pytest.raises(ValidationError, match="obligor tiers sum to"):
        ExposureBucket(
            label="Chemicals",
            published_spellings=("Chemicals",),
            balance=Decimal("10"),
            asset_count=1,
            split=ResolutionSplit(unresolved=Decimal("9")),
            per_deal=(_contribution(CAIRN, "10"),),
        )


def test_a_bucket_cannot_assert_an_asset_count_its_contributions_do_not_carry() -> None:
    """The population arm, not the balance arm.

    A bucket that reported a count its per-deal parts do not reach would let a
    dropped facility keep the money and lose the name behind it, which is the
    half a balance check cannot see.
    """
    with pytest.raises(ValidationError, match="its contributions carry"):
        ExposureBucket(
            label="Chemicals",
            published_spellings=("Chemicals",),
            balance=Decimal("10"),
            asset_count=4,
            split=ResolutionSplit(unresolved=Decimal("10")),
            per_deal=(_contribution(CAIRN, "10", count=1),),
        )


def test_an_unattributed_record_cannot_assert_a_count_its_parts_do_not_carry() -> None:
    with pytest.raises(ValidationError, match="its contributions carry"):
        UnattributedExposure(
            balance=Decimal("10"),
            asset_count=9,
            per_deal=(_contribution(CAIRN, "10", count=1),),
        )


def test_the_split_moves_when_a_group_flips_from_proposed_to_proven() -> None:
    """The paired assertion that makes the split load-bearing rather than decorative.

    Two deals holding one borrower under different loan ids give a *candidate*;
    give the two rows the same identifier and #562 proves the join. The bucket's
    balance is identical either way — only the split moves, which is the whole
    reason it is reported.
    """
    proposed = build_portfolio(
        {
            "a": _sched(_asset("LX1", "Alpha Holdings SA")),
            "b": _sched(_asset("LX2", "Alpha Holdings SA"), period_label="2024-08"),
        }
    )
    proven = build_portfolio(
        {
            "a": _sched(_asset("LX1", "Alpha Holdings SA")),
            "b": _sched(_asset("LX1", "Alpha Holdings SA"), period_label="2024-08"),
        }
    )

    proposed_bucket = aggregate_by_axis(proposed, country_axis()).buckets[0]
    proven_bucket = aggregate_by_axis(proven, country_axis()).buckets[0]

    assert proposed_bucket.balance == proven_bucket.balance
    assert proposed_bucket.split.candidate_proposed == proposed_bucket.balance
    assert proposed_bucket.split.proven_shared == Decimal(0)
    assert proven_bucket.split.proven_shared == proven_bucket.balance
    assert proven_bucket.split.candidate_proposed == Decimal(0)


def test_a_candidate_is_never_added_into_the_proven_tier() -> None:
    """``not_proven`` keeps the proposal on the unproven side, where #562 put it."""
    split = ResolutionSplit(
        proven_shared=Decimal("1"),
        candidate_proposed=Decimal("2"),
        unresolved=Decimal("4"),
    )
    assert split.not_proven == Decimal("6")
    assert split.total == Decimal("7")


def test_unresolved_balance_is_reported_beside_every_axis_figure() -> None:
    """Constraint 1: the residual sits next to each figure it could have moved."""
    portfolio = _committed()
    for axis in _bucketed_axes():
        figure = aggregate_by_axis(portfolio, axis)
        assert figure.not_proven_share > 0
        assert str(figure.not_proven_share) in figure.describe()
        assert str(figure.bounds) in figure.describe()


# ---------------------------------------------------------------------------
# Criterion: every contribution says which date it is as of
# ---------------------------------------------------------------------------


def test_every_contribution_on_every_axis_names_its_own_deals_date() -> None:
    portfolio = _committed()
    stated = {entry.deal: entry.stated for entry in portfolio.as_of}
    for axis in _bucketed_axes():
        figure = aggregate_by_axis(portfolio, axis)
        for record in (*figure.buckets, figure.unattributed):
            for contribution in record.per_deal:
                assert contribution.as_of == stated[contribution.deal]


def test_the_two_committed_deals_report_as_of_different_dates() -> None:
    """The condition the whole as-of requirement exists for, on real data."""
    portfolio = _committed()
    assert not portfolio.dates_align
    assert len({entry.stated for entry in portfolio.as_of}) == 2
    for entry in portfolio.as_of:
        assert entry.reporting_date, "both committed reports state a date"


def test_two_deals_reporting_on_one_date_are_reported_as_aligned() -> None:
    """The paired direction: ``dates_align`` tracks the data, not a constant."""
    aligned = build_portfolio(
        {
            "a": _sched(_asset("LX1", "Alpha Ltd"), reporting_date="30/06/2025"),
            "b": _sched(_asset("LX2", "Beta Ltd"), reporting_date="30/06/2025"),
        }
    )
    assert aligned.dates_align


def test_a_report_stating_no_date_falls_back_to_its_period_rather_than_inventing_one() -> None:
    undated = DealAsOf(deal="a", period_label="2025-03", reporting_date=None)
    assert undated.stated == "2025-03"
    assert "2025-03" in str(undated)


def test_a_contribution_with_no_stated_date_is_refused() -> None:
    with pytest.raises(ValidationError, match="no stated as-of date"):
        DealContribution(deal=CAIRN, as_of="  ", balance=Decimal("1"), asset_count=1)


def test_a_figure_must_state_an_as_of_for_every_contributing_deal() -> None:
    with pytest.raises(ValidationError, match="no stated as-of date"):
        BucketedExposure(
            axis=country_axis(),
            as_of=(DealAsOf(deal=CAIRN, period_label="2025-03"),),
            buckets=(
                ExposureBucket(
                    label="France",
                    published_spellings=("France",),
                    balance=Decimal("10"),
                    asset_count=1,
                    split=ResolutionSplit(unresolved=Decimal("10")),
                    per_deal=(_contribution(CONTEGO, "10"),),
                ),
            ),
            unattributed=UnattributedExposure(),
            bounds=ObligorBounds(lower=1, upper=1),
            total_balance=Decimal("10"),
        )


def test_a_figure_names_every_deals_date_in_its_own_description() -> None:
    portfolio = _committed()
    figure = aggregate_by_axis(portfolio, cross_deal_industry_axis())
    for entry in portfolio.as_of:
        assert entry.stated in figure.describe()
        assert entry.stated in aggregate_by_obligor(portfolio).describe()


# ---------------------------------------------------------------------------
# Criterion: the obligor axis keeps candidates as proposals and bounds as bounds
# ---------------------------------------------------------------------------


def test_every_obligor_row_carries_its_tier_and_the_tiers_partition_the_book() -> None:
    portfolio = _committed()
    figure = aggregate_by_obligor(portfolio)

    assert len(figure.rows) == len(portfolio.resolution.all_groups)
    assert figure.asset_count == portfolio.asset_count
    tiered = sum((figure.tier_balance(tier) for tier in ObligorTier), Decimal(0))
    assert tiered == portfolio.total_principal_balance
    for tier in ObligorTier:
        assert figure.rows_in_tier(tier), f"the fixtures carry {tier.value} obligors"


def test_a_proven_row_spans_deals_and_an_unproven_row_does_not() -> None:
    figure = aggregate_by_obligor(_committed())
    for row in figure.rows:
        spans = len(row.per_deal) > 1
        assert spans is (row.tier is ObligorTier.proven_shared)


def test_proposals_are_reported_as_proposals_and_never_merged_into_a_row() -> None:
    portfolio = _committed()
    figure = aggregate_by_obligor(portfolio)
    assert figure.proposals, "the committed deals do carry folded-name candidates"

    for proposal in figure.proposals:
        assert proposal.left.tier is ObligorTier.candidate_proposed
        assert proposal.right.tier is ObligorTier.candidate_proposed
        assert proposal.left in figure.rows and proposal.right in figure.rows
        assert proposal.combined_balance == proposal.left.balance + proposal.right.balance
        assert proposal.left.balance < proposal.combined_balance, (
            "a proposal names two real holdings, so neither side is the whole of it"
        )


def test_the_row_count_is_the_upper_bound_because_nothing_was_merged() -> None:
    portfolio = _committed()
    figure = aggregate_by_obligor(portfolio)
    assert len(figure.rows) == figure.bounds.upper
    assert figure.bounds.lower < figure.bounds.upper


def test_bounds_are_carried_through_and_nothing_collapses_them_to_one_number() -> None:
    """#562 built bounds so no accessor returns a point estimate; nor does this."""
    portfolio = _committed()
    figures = [aggregate_by_obligor(portfolio)] + [
        aggregate_by_axis(portfolio, axis) for axis in _bucketed_axes()
    ]
    for figure in figures:
        assert figure.bounds == portfolio.resolution.distinct_obligor_bounds()
        assert not figure.bounds.is_exact
        assert "-" in str(figure.bounds)
        assert figure.bounds.lower != figure.bounds.upper


def test_an_obligor_row_cannot_claim_a_tier_its_deals_contradict() -> None:
    with pytest.raises(ValidationError, match="contributes from one deal"):
        cross_deal_exposure.ObligorExposureRow(
            display_name="Alpha Ltd",
            tier=ObligorTier.proven_shared,
            balance=Decimal("10"),
            per_deal=(_contribution(CAIRN, "10"),),
        )
    with pytest.raises(ValidationError, match="so it is proven shared"):
        cross_deal_exposure.ObligorExposureRow(
            display_name="Alpha Ltd",
            tier=ObligorTier.unresolved,
            balance=Decimal("20"),
            per_deal=(_contribution(CAIRN, "10"), _contribution(CONTEGO, "10")),
        )


def test_an_obligor_rows_per_deal_parts_must_reach_its_balance() -> None:
    with pytest.raises(ValidationError, match="per-deal contributions sum to"):
        cross_deal_exposure.ObligorExposureRow(
            display_name="Alpha Ltd",
            tier=ObligorTier.unresolved,
            balance=Decimal("99"),
            per_deal=(_contribution(CAIRN, "10"),),
        )


# ---------------------------------------------------------------------------
# Criterion: balances are never added across currencies
# ---------------------------------------------------------------------------


def test_one_currency_spelled_two_ways_is_joined_on_the_committed_deals() -> None:
    """Cairn prints the ISO code and Contego the name; both are euro."""
    portfolio = _committed()
    spellings = {a.currency for a in portfolio.attributes if a.currency}
    assert len(spellings) > 1, "the two reports really do spell it differently"
    assert portfolio.currency == "EUR"


def test_two_real_currencies_are_refused_rather_than_summed() -> None:
    with pytest.raises(ValidationError, match="more than one currency"):
        build_portfolio(
            {
                "a": _sched(_asset("LX1", "Alpha Ltd", currency="EUR")),
                "b": _sched(_asset("LX2", "Beta Ltd", currency="GBP")),
            }
        )


def test_an_unrecognised_currency_spelling_refuses_rather_than_joining_silently() -> None:
    """The recoverable direction: add an alias, never sum two currencies."""
    assert _currency_code("Sterling") != _currency_code("GBP")
    with pytest.raises(ValidationError, match="more than one currency"):
        build_portfolio(
            {
                "a": _sched(_asset("LX1", "Alpha Ltd", currency="Sterling")),
                "b": _sched(_asset("LX2", "Beta Ltd", currency="GBP")),
            }
        )


# ---------------------------------------------------------------------------
# Criterion: the whole thing is deterministic
# ---------------------------------------------------------------------------


def test_output_is_deterministic_across_runs_and_permuted_input_order() -> None:
    cairn = _schedule("cairn-clo-xvii-march-2025.txt", "2025-03")
    contego = _schedule("contego-clo-xi-august-2024.txt", "2024-08")

    forward = build_portfolio({CAIRN: cairn, CONTEGO: contego})
    reversed_order = build_portfolio({CONTEGO: contego, CAIRN: cairn})

    assert forward == reversed_order
    assert aggregate_by_obligor(forward) == aggregate_by_obligor(reversed_order)
    for axis in _bucketed_axes():
        assert aggregate_by_axis(forward, axis) == aggregate_by_axis(reversed_order, axis)


def test_buckets_are_ordered_largest_first_so_a_top_n_read_is_stable() -> None:
    figure = aggregate_by_axis(_committed(), cross_deal_industry_axis())
    balances = [bucket.balance for bucket in figure.buckets]
    assert balances == sorted(balances, reverse=True)


def test_a_portfolio_needs_at_least_two_deals_to_be_a_cross_deal_figure() -> None:
    with pytest.raises(ValueError, match="at least two parsed schedules"):
        build_portfolio({"a": _sched(_asset("LX1", "Alpha Ltd"))})


def test_a_blank_axis_value_is_absence_rather_than_a_bucket_called_blank() -> None:
    """Formatting must not invent a concentration."""
    portfolio = build_portfolio(
        {
            "a": _sched(_asset("LX1", "Alpha Ltd", country="   ")),
            "b": _sched(_asset("LX2", "Beta Ltd", country="France")),
        }
    )
    figure = aggregate_by_axis(portfolio, country_axis())
    assert [bucket.label for bucket in figure.buckets] == ["France"]
    assert figure.unattributed.asset_count == 1
    assert figure.unattributed.per_deal[0].deal == "a"
