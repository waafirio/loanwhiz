"""Look-through exposure across two or more CLOs, with its residuals named (#564).

Hold both deals and your exposure is neither report: it is the two collateral
schedules added together, by obligor, industry, country and rating. Summing is
the easy part. What makes the sum *reportable* is that three different things
can move any figure it produces, and each has to be visible beside the figure
rather than folded into it:

- **the obligor residual.** #562 resolves identity into three tiers that are
  never blended — proven, proposed, unresolved. A bucket's balance is exact
  whichever tier its assets sit in, but the number of *distinct borrowers*
  behind that balance is not, so every bucket here carries the tier split that
  produced it. A buyer reading "3.2% exposure to Chemicals" can then see how
  much of it sits in names the two reports gave no way to join.
- **the attribute residual.** An asset whose axis value the report never
  published cannot be bucketed, and must not be. It goes to
  :class:`UnattributedExposure` — a different record kind, following #513, so
  that no loop over ``buckets`` can pick it up as though it were a
  concentration. This repo has no residual bucket anywhere (#496, #514) and
  gains none here.
- **the reporting date.** The two committed schedules are as of different
  months, and an aggregate that silently mixes them is a figure no one can
  reconcile back to either source. Every contribution to every bucket carries
  the date its deal stated, so the mixing is on the face of the figure rather
  than in a caveat somewhere else.

The axis is part of the figure, not a caption
---------------------------------------------
#563 settled that a cross-deal industry concentration must name its taxonomy,
because Cairn's own March 2025 report states its largest industry as 11.36% on
S&P and 10.83% on Fitch — the same portfolio, two numbers. That is enforced
here by :class:`ExposureAxis`, whose validator refuses an industry axis with no
:class:`~loanwhiz.primitives.industry_taxonomy.IndustryTaxonomy` and a rating
axis with no :class:`RatingAgency`. An unnamed cross-deal figure is not
discouraged; it is unconstructible.

Why each axis folds its own labels
----------------------------------
Two deals spell one label two ways, so bucketing needs a fold — and the fold
that is right for industries is **wrong** for ratings.
``industry_taxonomy.canonical_label`` drops punctuation, which is exactly what
makes ``Aerospace & Defense`` and ``Aerospace and defence`` one bucket; applied
to ratings it makes ``B``, ``B+`` and ``B-`` one bucket too, merging three
notches into one and reading as a *better* book. So the axis chooses its fold,
and — because choosing wrongly is silent — every fold is then proved injective
over each deal's own published vocabulary before anything is summed. Contego
publishes ``B`` and ``B+`` in the same report, so the industry fold applied to
its ratings raises rather than merges. A refusal that is named can be reviewed;
a merge that halves a notch count cannot.

There is deliberately no cross-deal rating agency constant, the counterpart of
:data:`~loanwhiz.primitives.industry_taxonomy.CROSS_DEAL_TAXONOMY`. #563 chose
Fitch for industry by measuring which vocabulary actually joined. Measured the
same way, *neither* rating agency joins across these two deals: Cairn publishes
no Fitch rating at all and states S&P ratings only for its CCC bucket, which is
the one detail section that carries them. A rating axis is therefore still
offered — and returns a figure whose unattributed record dwarfs its buckets.
Reporting that hole at full size is the useful answer; inventing an ``NR``
bucket to cover it would be the harmful one. ``docs/data-card.md`` records the
finding, and ``tests/test_cross_deal_exposure.py`` re-derives it.

What this module will not do
----------------------------
It never applies a candidate. #562's candidates are proposals over two
unresolved groups, and folding them into the proven tier would make the count
non-decomposable — the property both siblings were built to hold.
:class:`ObligorExposure` lists them as proposals with the balance each would
merge, so accepting one stays the reader's decision.

It never nets an unresolved name into any bucket, including "Other". A bucket
label that reads as a residual is refused at construction rather than reported.

It never adds balances across currencies. Two deals reporting in different
currencies produce one number that means nothing, and the addition is invisible
once done, so :func:`build_portfolio` refuses instead.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, model_validator

from loanwhiz.primitives.collateral_schedule_parser import (
    CollateralAsset,
    CollateralSchedule,
)
from loanwhiz.primitives.industry_taxonomy import (
    IndustryTaxonomy,
    LabelCollisionError,
    canonical_label,
)
from loanwhiz.primitives.obligor_resolution import (
    CrossDealObligorResolution,
    ObligorBounds,
    ObligorGroup,
    resolve_obligors,
)

__all__ = [
    "AssetAttributes",
    "AttributeAbsence",
    "AxisKind",
    "BucketedExposure",
    "CrossDealPortfolio",
    "DealAsOf",
    "DealContribution",
    "ExposureAxis",
    "ExposureBucket",
    "ObligorExposure",
    "ObligorExposureRow",
    "ObligorProposal",
    "ObligorTier",
    "RatingAgency",
    "ResolutionSplit",
    "UnattributedExposure",
    "aggregate_by_axis",
    "aggregate_by_obligor",
    "build_portfolio",
    "country_axis",
    "cross_deal_industry_axis",
    "industry_axis",
    "rating_axis",
]

#: Labels that name a residual rather than a thing held. A bucket carrying one
#: would be the "Other" bucket this repo refuses everywhere (#496), reached by
#: accident rather than by decision, so :class:`BucketedExposure` refuses it at
#: construction. Compared against the *folded* label, so spelling cannot slip
#: one past. An asset whose axis value is genuinely absent never reaches a
#: bucket at all — it becomes :class:`UnattributedExposure`.
_RESIDUAL_LABELS = frozenset(
    {
        "misc",
        "miscellaneous",
        "n a",
        "na",
        "none",
        "not applicable",
        "not available",
        "not rated",
        "nr",
        "other",
        "others",
        "unavailable",
        "unclassified",
        "unknown",
    }
)


#: Published currency spellings that mean one currency. Only the euro pair is
#: drawn from data — Cairn's schedule prints the ISO code ``EUR`` and Contego's
#: prints ``Euro``; the other codes are identity entries so a report already
#: printing ISO passes through unchanged. This is an **alias table, not a
#: fold** — no orthographic rule turns ``Euro`` into ``EUR``, and one loose
#: enough to try would merge currencies that merely look alike. A spelling not
#: listed is compared as published, so an unseen one (``Sterling``) **raises**
#: rather than joining silently. That is the recoverable direction: a refusal is
#: fixed by adding a line here, whereas two currencies quietly summed produce a
#: number in no currency at all and nothing downstream can tell.
_CURRENCY_ALIASES: Mapping[str, str] = {
    "eur": "EUR",
    "euro": "EUR",
    "gbp": "GBP",
    "usd": "USD",
}


def _currency_code(raw: str) -> str:
    """One published currency spelling as a comparable code."""
    return _CURRENCY_ALIASES.get(canonical_label(raw), raw.strip().upper())


class AxisKind(str, Enum):
    """What a bucketed figure is bucketed by.

    Closed, so a new axis cannot be added without deciding — in the type —
    whether it needs a taxonomy, an agency, or neither.
    """

    industry = "industry"
    country = "country"
    rating = "rating"


class RatingAgency(str, Enum):
    """Whose rating scale a rating figure is expressed in.

    The counterpart of
    :class:`~loanwhiz.primitives.industry_taxonomy.IndustryTaxonomy`, and a
    *required* argument for the same reason: the two agencies rate the same
    asset differently, so a rating concentration that does not say whose scale
    it used is not comparable to either report.
    """

    sp = "sp"
    fitch = "fitch"

    @property
    def display_name(self) -> str:
        """How the reports themselves name this agency."""
        return "S&P" if self is RatingAgency.sp else "Fitch"

    @property
    def asset_attribute(self) -> str:
        """The ``CollateralAsset`` attribute this agency's rating lives on."""
        return "sp_rating" if self is RatingAgency.sp else "fitch_rating"


class AttributeAbsence(str, Enum):
    """Why an asset could not be placed on an axis.

    "Absent" is not "zero" and not "other": it means the report this asset came
    from did not publish the field, so the asset has no bucket and is not
    evidence about any bucket.
    """

    #: The report carried no value for this axis on this asset.
    not_published = "not_published"


class ObligorTier(str, Enum):
    """Which of #562's tiers an asset's obligor group sits in.

    Carried per bucket so a concentration can be read as decomposable: exact in
    balance, bounded in distinct-borrower count. The three are never summed into
    a "resolved" total, because ``candidate_proposed`` is a proposal and adding
    it to ``proven_shared`` is precisely the claim #562 refuses to make.
    """

    #: Proven to span more than one deal — a real cross-deal single-name risk.
    proven_shared = "proven_shared"
    #: Seen in one deal, with a folded-name candidate proposed against another.
    candidate_proposed = "candidate_proposed"
    #: Seen in one deal, with no evidence either way about the other.
    unresolved = "unresolved"


def _rating_label(raw: str) -> str:
    """Fold a published rating to canonical form **without losing the notch**.

    Case and whitespace only. ``+`` and ``-`` are kept because they are the
    notch: ``industry_taxonomy.canonical_label`` would strip them and fold
    ``B``, ``B+`` and ``B-`` onto one bucket, which merges three rating steps
    into one and makes a book read better than it is.
    """
    text = re.sub(r"[^a-z0-9+-]+", " ", (raw or "").casefold())
    return " ".join(text.split())


class ExposureAxis(BaseModel, frozen=True):
    """The axis a bucketed figure is expressed on, named so it cannot be omitted.

    The validator is the enforcement #563 asked for: an industry axis without a
    taxonomy and a rating axis without an agency are not discouraged, they do
    not exist. The mirror rules matter too — a country axis carrying a taxonomy
    would be a figure claiming an axis it does not use.
    """

    kind: AxisKind
    taxonomy: IndustryTaxonomy | None = None
    agency: RatingAgency | None = None

    @model_validator(mode="after")
    def _axis_names_what_it_needs(self) -> ExposureAxis:
        if self.kind is AxisKind.industry:
            if self.taxonomy is None:
                raise ValueError(
                    "an industry axis must name its taxonomy: the same portfolio "
                    "gives a different concentration on S&P than on Fitch (#563)"
                )
            if self.agency is not None:
                raise ValueError("an industry axis does not take a rating agency")
            return self
        if self.kind is AxisKind.rating:
            if self.agency is None:
                raise ValueError(
                    "a rating axis must name its agency: the two agencies rate "
                    "the same asset differently"
                )
            if self.taxonomy is not None:
                raise ValueError("a rating axis does not take an industry taxonomy")
            return self
        if self.taxonomy is not None or self.agency is not None:
            raise ValueError(f"a {self.kind.value} axis takes neither taxonomy nor agency")
        return self

    @property
    def label(self) -> str:
        """How a figure on this axis names itself — always including the axis."""
        if self.kind is AxisKind.industry:
            return f"{self.taxonomy.display_name} industry"  # type: ignore[union-attr]
        if self.kind is AxisKind.rating:
            return f"{self.agency.display_name} rating"  # type: ignore[union-attr]
        return "country"

    @property
    def asset_attribute(self) -> str:
        """The ``CollateralAsset`` / :class:`AssetAttributes` field it reads."""
        if self.kind is AxisKind.industry:
            return self.taxonomy.asset_attribute  # type: ignore[union-attr]
        if self.kind is AxisKind.rating:
            return self.agency.asset_attribute  # type: ignore[union-attr]
        return "country"

    @property
    def fold(self) -> Callable[[str], str]:
        """The label fold this axis uses — see :func:`_rating_label`."""
        return _rating_label if self.kind is AxisKind.rating else canonical_label


def industry_axis(taxonomy: IndustryTaxonomy) -> ExposureAxis:
    """An industry axis on ``taxonomy``. The argument has no default on purpose."""
    return ExposureAxis(kind=AxisKind.industry, taxonomy=taxonomy)


def cross_deal_industry_axis() -> ExposureAxis:
    """The industry axis #563 settled for cross-deal figures (Fitch)."""
    from loanwhiz.primitives.industry_taxonomy import CROSS_DEAL_TAXONOMY

    return industry_axis(CROSS_DEAL_TAXONOMY)


def country_axis() -> ExposureAxis:
    """A country axis. Countries have one vocabulary, so nothing to name."""
    return ExposureAxis(kind=AxisKind.country)


def rating_axis(agency: RatingAgency) -> ExposureAxis:
    """A rating axis on ``agency``. See the module docstring on coverage."""
    return ExposureAxis(kind=AxisKind.rating, agency=agency)


class DealAsOf(BaseModel, frozen=True):
    """When one deal's contribution is as of, carried on every figure it feeds.

    Both committed schedules state a reporting date and they are months apart,
    so an aggregate that reported one date, or none, would be unreconcilable to
    either source. ``period_label`` is always present and is the fallback when a
    report states no date — an honest coarser answer, never a guessed precise
    one.
    """

    deal: str
    deal_name: str | None = None
    reporting_date: str | None = None
    period_label: str

    @model_validator(mode="after")
    def _deal_and_period_are_named(self) -> DealAsOf:
        if not self.deal.strip():
            raise ValueError("a deal contribution must name its deal")
        if not self.period_label.strip():
            raise ValueError(f"deal {self.deal!r} states no period label")
        return self

    @property
    def stated(self) -> str:
        """The date this deal's report stated, or its period when it stated none."""
        return self.reporting_date or self.period_label

    def __str__(self) -> str:
        return f"{self.deal_name or self.deal} as of {self.stated}"


class AssetAttributes(BaseModel, frozen=True):
    """One asset's bucketable fields, keyed the way the re-join is keyed.

    #562's ``AssetRef`` carries identity and balance but no industry, country or
    rating, so bucketing has to go back to the schedule for them. The key is
    ``(deal, identifier)`` and never the issuer name: both committed schedules
    repeat issuer names across facilities, so a name-keyed index silently drops
    rows — and dropping rows shrinks a denominator, which reads as a *less*
    concentrated book (#478, #571).
    """

    deal: str
    identifier: str
    principal_balance: Decimal
    currency: str | None = None
    sp_industry: str | None = None
    fitch_industry: str | None = None
    country: str | None = None
    sp_rating: str | None = None
    fitch_rating: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        """The census key: ``(deal, identifier)``, unique within a deal."""
        return (self.deal, self.identifier)

    def published_on(self, axis: ExposureAxis) -> str | None:
        """This asset's published value on ``axis``, or ``None`` if absent.

        Whitespace-only is absence, not a label: a blank cell is the report not
        publishing the field, and treating it as a bucket name would invent a
        concentration out of formatting.
        """
        raw = getattr(self, axis.asset_attribute)
        if raw is None:
            return None
        text = str(raw).strip()
        return text or None


class ResolutionSplit(BaseModel, frozen=True):
    """How a balance divides across #562's three tiers. Never summed to one number.

    The point of carrying it beside every bucket is decomposability: the balance
    is exact, but how many distinct borrowers stand behind it is not, and the
    two non-proven parts are exactly the amount that could move if the two
    reports were ever joined properly.
    """

    proven_shared: Decimal = Decimal(0)
    candidate_proposed: Decimal = Decimal(0)
    unresolved: Decimal = Decimal(0)

    @model_validator(mode="after")
    def _no_tier_is_negative(self) -> ResolutionSplit:
        for tier in ObligorTier:
            if getattr(self, tier.value) < 0:
                raise ValueError(f"{tier.value} balance is negative")
        return self

    @property
    def total(self) -> Decimal:
        """The whole balance this split accounts for."""
        return self.proven_shared + self.candidate_proposed + self.unresolved

    @property
    def not_proven(self) -> Decimal:
        """Balance whose cross-deal obligor identity was never settled.

        The candidate part is included because a proposal is not a proof — this
        is the amount a reader must treat as "could move this figure".
        """
        return self.candidate_proposed + self.unresolved


class DealContribution(BaseModel, frozen=True):
    """What one deal contributed to one bucket, and the date it is as of.

    The as-of date lives here, at the finest grain, rather than only on the
    figure: a bucket both deals reach is the one place a mixed-date aggregate
    would otherwise look like a single clean number.
    """

    deal: str
    as_of: str
    balance: Decimal
    asset_count: int

    @model_validator(mode="after")
    def _contribution_is_real(self) -> DealContribution:
        if not self.as_of.strip():
            raise ValueError(f"deal {self.deal!r} contributes with no stated as-of date")
        if self.asset_count < 1:
            raise ValueError(
                f"deal {self.deal!r} contributes {self.asset_count} assets; a "
                "contribution of nothing is an absent contribution, not a zero one"
            )
        return self

    def __str__(self) -> str:
        return f"{self.deal} {self.balance} as of {self.as_of}"


def _contributions_reconcile(
    per_deal: Sequence[DealContribution],
    balance: Decimal,
    asset_count: int | None,
    what: str,
) -> None:
    """Refuse a record whose per-deal parts do not add up to its own totals.

    ``asset_count`` is ``None`` for a record that *derives* its count from these
    same contributions rather than carrying one. Checking a derived count here
    would compare a value against itself and could never fire, and a check that
    cannot fire is worse than no check because it reads as one — so the caller
    says so in the type instead.
    """
    deals = [c.deal for c in per_deal]
    if len(set(deals)) != len(deals):
        raise ValueError(f"{what} carries the same deal's contribution twice: {sorted(deals)}")
    summed = sum((c.balance for c in per_deal), Decimal(0))
    if summed != balance:
        raise ValueError(
            f"{what} states balance {balance} but its per-deal contributions sum to {summed}"
        )
    if asset_count is None:
        return
    counted = sum(c.asset_count for c in per_deal)
    if counted != asset_count:
        raise ValueError(
            f"{what} states {asset_count} assets but its contributions carry {counted}"
        )


class ExposureBucket(BaseModel, frozen=True):
    """One label's combined exposure, decomposed by deal and by obligor tier.

    Every number on it is re-derived rather than trusted: the per-deal
    contributions must sum to the balance, and the tier split must too. A bucket
    that could assert a balance its parts do not reach would be the overstatement
    mirroring the dropped-row understatement the census guards against.
    """

    label: str
    published_spellings: tuple[str, ...]
    balance: Decimal
    asset_count: int
    split: ResolutionSplit
    per_deal: tuple[DealContribution, ...]

    @model_validator(mode="after")
    def _parts_are_derived_not_asserted(self) -> ExposureBucket:
        if not self.label.strip():
            raise ValueError("a bucket must carry a label")
        if not self.published_spellings:
            raise ValueError(f"bucket {self.label!r} names no published spelling")
        if len(set(self.published_spellings)) != len(self.published_spellings):
            raise ValueError(f"bucket {self.label!r} repeats a published spelling")
        if not self.per_deal:
            raise ValueError(f"bucket {self.label!r} has no deal contributing to it")
        _contributions_reconcile(
            self.per_deal, self.balance, self.asset_count, f"bucket {self.label!r}"
        )
        if self.split.total != self.balance:
            raise ValueError(
                f"bucket {self.label!r} states balance {self.balance} but its obligor "
                f"tiers sum to {self.split.total}"
            )
        return self

    @property
    def deals(self) -> tuple[str, ...]:
        """The deals contributing to this bucket, in contribution order."""
        return tuple(c.deal for c in self.per_deal)

    @property
    def reached_by_one_deal(self) -> bool:
        """Whether only one deal reaches this label.

        Not itself a defect: it may be a genuine portfolio difference (one deal
        holds a utility, the other does not) or two vintages' names for one
        sector that #563 declined to map. Reported, never diagnosed.
        """
        return len(self.per_deal) == 1

    @property
    def spellings_differ(self) -> bool:
        """Whether the two deals spelled this label differently."""
        return len(self.published_spellings) > 1

    def as_of_clause(self) -> str:
        """The dates this bucket's contributions are as of, one per deal."""
        return "; ".join(f"{c.deal} as of {c.as_of}" for c in self.per_deal)


class UnattributedExposure(BaseModel, frozen=True):
    """Balance an axis could not place, as its own record kind (#513).

    Deliberately **not** an :class:`ExposureBucket`. If it were, every consumer
    that loops over ``buckets`` — a top-N concentration, a chart, a limit check
    — would silently treat "the report did not publish this field" as a holding
    called "unknown", and the residual would read as an ordinary, small,
    ignorable slice. Being a different type is what makes ignoring it a
    decision rather than an oversight.

    An empty one is a true zero, not an unknown: the axis was applied and every
    asset carried a value.
    """

    reason: AttributeAbsence = AttributeAbsence.not_published
    balance: Decimal = Decimal(0)
    asset_count: int = 0
    per_deal: tuple[DealContribution, ...] = ()

    @model_validator(mode="after")
    def _absence_is_accounted(self) -> UnattributedExposure:
        if self.asset_count < 0:
            raise ValueError("unattributed asset count is negative")
        if not self.per_deal:
            if self.asset_count or self.balance:
                raise ValueError(
                    "unattributed exposure names no deal but claims assets or balance"
                )
            return self
        _contributions_reconcile(
            self.per_deal, self.balance, self.asset_count, "unattributed exposure"
        )
        return self

    @property
    def is_empty(self) -> bool:
        """Whether every asset carried a value on this axis."""
        return self.asset_count == 0


def _percent(part: Decimal, whole: Decimal) -> Decimal:
    """``part`` as a percentage of ``whole``, to two places; zero whole gives zero."""
    if whole == 0:
        return Decimal("0.00")
    return (part * 100 / whole).quantize(Decimal("0.01"))


class BucketedExposure(BaseModel, frozen=True):
    """A cross-deal concentration on one named axis, with both residuals beside it.

    Three things are structural rather than optional, because each is a figure
    someone would otherwise publish without: the axis it is expressed on, the
    as-of date of every deal that fed it, and the two residuals — obligor
    identity (per bucket, in :class:`ResolutionSplit`) and unpublished attribute
    (once, in :class:`UnattributedExposure`).

    ``total_balance`` is re-derived from the buckets plus the unattributed
    record, so a bucket that lost rows or a residual that was quietly netted
    into one cannot produce a figure that still adds up.
    """

    axis: ExposureAxis
    as_of: tuple[DealAsOf, ...]
    buckets: tuple[ExposureBucket, ...]
    unattributed: UnattributedExposure
    bounds: ObligorBounds
    total_balance: Decimal

    @model_validator(mode="after")
    def _figure_accounts_for_its_own_total(self) -> BucketedExposure:
        if not self.as_of:
            raise ValueError("a cross-deal figure must state which date each deal is as of")
        dates = [entry.deal for entry in self.as_of]
        if len(set(dates)) != len(dates):
            raise ValueError(f"the figure states two as-of dates for one deal: {sorted(dates)}")

        labels = [bucket.label for bucket in self.buckets]
        if len(set(labels)) != len(labels):
            raise ValueError(f"the figure carries one label twice: {sorted(labels)}")
        for bucket in self.buckets:
            if self.axis.fold(bucket.label) in _RESIDUAL_LABELS:
                raise ValueError(
                    f"bucket {bucket.label!r} names a residual rather than a holding. "
                    "If it came from an absent value, that asset belongs in "
                    "`unattributed`, a different record kind for exactly this reason. "
                    "If a report genuinely publishes this label, this figure would "
                    "inherit that report's residual bucket (#496), so it refuses "
                    "rather than reporting a concentration that absorbs the unknown "
                    "— giving such a label its own record kind is the change to make."
                )

        declared = {entry.deal for entry in self.as_of}
        contributing = {
            c.deal
            for record in (*self.buckets, self.unattributed)
            for c in record.per_deal
        }
        undeclared = contributing - declared
        if undeclared:
            raise ValueError(
                f"contributions from deals with no stated as-of date: {sorted(undeclared)}"
            )

        placed = sum((bucket.balance for bucket in self.buckets), Decimal(0))
        accounted = placed + self.unattributed.balance
        if accounted != self.total_balance:
            raise ValueError(
                f"the figure states a total of {self.total_balance} but its buckets and "
                f"unattributed record account for {accounted}"
            )
        return self

    @property
    def attributed_balance(self) -> Decimal:
        """Balance the axis could place — the total less the unattributed part."""
        return self.total_balance - self.unattributed.balance

    @property
    def asset_count(self) -> int:
        """Every asset this figure accounts for, placed and unplaced alike."""
        return (
            sum(bucket.asset_count for bucket in self.buckets) + self.unattributed.asset_count
        )

    def share(self, bucket: ExposureBucket) -> Decimal:
        """One bucket's percentage of the whole book, not of what was placed.

        The denominator is deliberately the full total. Dividing by the
        attributed balance would rescale every concentration upward in exact
        proportion to how much the report failed to publish, which is the one
        arithmetic that turns a coverage hole into a stronger-looking figure.
        """
        return _percent(bucket.balance, self.total_balance)

    @property
    def unattributed_share(self) -> Decimal:
        """How much of the book this axis could not place at all."""
        return _percent(self.unattributed.balance, self.total_balance)

    @property
    def not_proven_share(self) -> Decimal:
        """How much of the book sits in obligors never proven across the deals."""
        not_proven = sum(
            (bucket.split.not_proven for bucket in self.buckets), Decimal(0)
        )
        return _percent(not_proven, self.total_balance)

    def as_of_clause(self) -> str:
        """Every contributing deal and the date it is as of."""
        return "; ".join(str(entry) for entry in self.as_of)

    def describe_bucket(self, bucket: ExposureBucket) -> str:
        """One bucket as a sentence that carries everything it needs to be read."""
        return (
            f"{bucket.label} — {self.share(bucket)}% of {self.total_balance} "
            f"on {self.axis.label} ({bucket.as_of_clause()}); "
            f"proven shared {_percent(bucket.split.proven_shared, self.total_balance)}%, "
            f"proposed {_percent(bucket.split.candidate_proposed, self.total_balance)}%, "
            f"unresolved {_percent(bucket.split.unresolved, self.total_balance)}%"
        )

    def describe(self) -> str:
        """The figure's headline, with both residuals and every as-of date on it."""
        return (
            f"{self.axis.label} exposure across {len(self.as_of)} deals "
            f"({self.as_of_clause()}): {len(self.buckets)} buckets over "
            f"{self.total_balance}. Unplaced by this axis: "
            f"{self.unattributed_share}% ({self.unattributed.reason.value}). "
            f"Obligor identity unproven on {self.not_proven_share}% of the book; "
            f"distinct obligors {self.bounds}."
        )


class ObligorExposureRow(BaseModel, frozen=True):
    """One obligor group's exposure across the deals, with its tier stated.

    A row is never a merge of two groups. A proven group's members span deals
    because #562 proved they do; an unresolved or candidate row is one deal's
    holding, listed on its own, with the proposal (if any) recorded separately.
    """

    display_name: str
    tier: ObligorTier
    balance: Decimal
    per_deal: tuple[DealContribution, ...]

    @model_validator(mode="after")
    def _row_matches_its_tier(self) -> ObligorExposureRow:
        if not self.per_deal:
            raise ValueError(f"obligor {self.display_name!r} has no contributing deal")
        _contributions_reconcile(
            self.per_deal,
            self.balance,
            None,  # a row derives its count from these contributions; see the helper
            f"obligor {self.display_name!r}",
        )
        spans = len(self.per_deal) > 1
        if self.tier is ObligorTier.proven_shared and not spans:
            raise ValueError(
                f"obligor {self.display_name!r} is in the proven-shared tier but "
                "contributes from one deal"
            )
        if self.tier is not ObligorTier.proven_shared and spans:
            raise ValueError(
                f"obligor {self.display_name!r} contributes from more than one deal, "
                f"so it is proven shared, not {self.tier.value}"
            )
        return self

    @property
    def asset_count(self) -> int:
        """How many facilities this obligor is held through."""
        return sum(c.asset_count for c in self.per_deal)


class ObligorProposal(BaseModel, frozen=True):
    """A #562 candidate, reported as what it is: a link not applied.

    It carries the balance the two sides would merge into so a reviewer can see
    the size of the decision without re-joining anything — and so that
    *not* applying it stays visible rather than silently costing a
    concentration.
    """

    folded: str
    left: ObligorExposureRow
    right: ObligorExposureRow

    @model_validator(mode="after")
    def _proposal_links_two_unproven_rows(self) -> ObligorProposal:
        if not self.folded.strip():
            raise ValueError("a proposal must name the fold it rests on")
        for side, row in (("left", self.left), ("right", self.right)):
            if row.tier is ObligorTier.proven_shared:
                raise ValueError(f"{side} row is proven shared and needs no proposal")
        if {c.deal for c in self.left.per_deal} & {c.deal for c in self.right.per_deal}:
            raise ValueError("a cross-deal proposal must link rows from different deals")
        return self

    @property
    def combined_balance(self) -> Decimal:
        """What the two sides would total if a reader accepted this proposal."""
        return self.left.balance + self.right.balance


class ObligorExposure(BaseModel, frozen=True):
    """Look-through exposure by obligor: every name, its tier, and the proposals.

    ``rows`` is the whole book — proven, candidate and unresolved together, each
    labelled — because a "top names" list that quietly showed only the proven
    ones would be a concentration built from the third of the book that happened
    to join. ``bounds`` comes straight from #562 and stays a range; nothing here
    returns a single obligor count.
    """

    as_of: tuple[DealAsOf, ...]
    rows: tuple[ObligorExposureRow, ...]
    proposals: tuple[ObligorProposal, ...]
    bounds: ObligorBounds
    total_balance: Decimal

    @model_validator(mode="after")
    def _rows_account_for_the_book(self) -> ObligorExposure:
        if not self.as_of:
            raise ValueError("a cross-deal figure must state which date each deal is as of")
        declared = {entry.deal for entry in self.as_of}
        contributing = {c.deal for row in self.rows for c in row.per_deal}
        undeclared = contributing - declared
        if undeclared:
            raise ValueError(
                f"contributions from deals with no stated as-of date: {sorted(undeclared)}"
            )
        summed = sum((row.balance for row in self.rows), Decimal(0))
        if summed != self.total_balance:
            raise ValueError(
                f"the figure states a total of {self.total_balance} but its rows sum to {summed}"
            )
        return self

    def rows_in_tier(self, tier: ObligorTier) -> tuple[ObligorExposureRow, ...]:
        """Every row in one tier, in the figure's own order."""
        return tuple(row for row in self.rows if row.tier is tier)

    def tier_balance(self, tier: ObligorTier) -> Decimal:
        """One tier's share of the book, in balance."""
        return sum((row.balance for row in self.rows_in_tier(tier)), Decimal(0))

    @property
    def asset_count(self) -> int:
        """Every asset accounted for across every row."""
        return sum(row.asset_count for row in self.rows)

    def as_of_clause(self) -> str:
        """Every contributing deal and the date it is as of."""
        return "; ".join(str(entry) for entry in self.as_of)

    def describe(self) -> str:
        """The obligor figure's headline, residual and bounds included."""
        proven = _percent(self.tier_balance(ObligorTier.proven_shared), self.total_balance)
        proposed = _percent(
            self.tier_balance(ObligorTier.candidate_proposed), self.total_balance
        )
        unresolved = _percent(self.tier_balance(ObligorTier.unresolved), self.total_balance)
        return (
            f"Obligor exposure across {len(self.as_of)} deals ({self.as_of_clause()}): "
            f"{len(self.rows)} names over {self.total_balance}. "
            f"Proven shared {proven}%, proposed {proposed}%, unresolved {unresolved}%. "
            f"{len(self.proposals)} proposal(s) outstanding; "
            f"distinct obligors {self.bounds}."
        )


class CrossDealPortfolio(BaseModel, frozen=True):
    """Two or more deals' schedules, resolved and re-joined, ready to aggregate.

    Built once by :func:`build_portfolio` so that every figure taken off it
    shares one resolution, one attribute re-join and one set of as-of dates. Two
    figures derived independently could disagree about how many assets exist,
    and the disagreement would be invisible in either one.
    """

    as_of: tuple[DealAsOf, ...]
    resolution: CrossDealObligorResolution
    attributes: tuple[AssetAttributes, ...]

    @model_validator(mode="after")
    def _portfolio_is_internally_consistent(self) -> CrossDealPortfolio:
        declared = tuple(entry.deal for entry in self.as_of)
        if len(set(declared)) != len(declared):
            raise ValueError(f"two as-of dates for one deal: {sorted(declared)}")
        if set(declared) != set(self.resolution.deals):
            raise ValueError(
                f"as-of dates cover {sorted(declared)} but the resolution covers "
                f"{sorted(self.resolution.deals)}"
            )
        spellings = {a.currency for a in self.attributes if a.currency}
        currencies = {_currency_code(c) for c in spellings}
        if len(currencies) > 1:
            raise ValueError(
                f"these deals report in more than one currency ({sorted(currencies)}, "
                f"published as {sorted(spellings)}); adding their balances would "
                "produce a number in no currency at all"
            )
        return self

    @property
    def currency(self) -> str | None:
        """The one currency every balance here is denominated in, if stated.

        Single by construction — the validator refuses a portfolio spanning two.
        """
        codes = {_currency_code(a.currency) for a in self.attributes if a.currency}
        return codes.pop() if len(codes) == 1 else None

    @property
    def deals(self) -> tuple[str, ...]:
        """The deals in this portfolio, in the resolution's sorted order."""
        return self.resolution.deals

    @property
    def total_principal_balance(self) -> Decimal:
        """Every asset's balance, across every deal."""
        return sum((a.principal_balance for a in self.attributes), Decimal(0))

    @property
    def asset_count(self) -> int:
        """How many assets this portfolio accounts for."""
        return len(self.attributes)

    @property
    def dates_align(self) -> bool:
        """Whether every deal states the same as-of date.

        False on the two committed schedules, and a consumer that wants to say
        so can. It is never used to *suppress* a figure — a mixed-date aggregate
        is reportable as long as it says so, and that is what
        :class:`DealContribution` makes it do.
        """
        return len({entry.stated for entry in self.as_of}) == 1

    def as_of_for(self, deal: str) -> DealAsOf:
        """One deal's as-of record, by deal key."""
        for entry in self.as_of:
            if entry.deal == deal:
                return entry
        raise KeyError(f"{deal!r} is not in this portfolio: {sorted(self.deals)}")

    def attributes_by_key(self) -> dict[tuple[str, str], AssetAttributes]:
        """The attribute index, keyed ``(deal, identifier)``.

        Safe to build because :func:`_assert_every_asset_attributed` has already
        proved the key is unique across the whole portfolio; the same index
        keyed on issuer name would lose rows in both committed deals.
        """
        return {a.key: a for a in self.attributes}


def _group_key(group: ObligorGroup) -> tuple[tuple[str, str], ...]:
    """A hashable identity for an obligor group: its member keys, sorted."""
    return tuple(sorted(member.key for member in group.members))


def _tier_by_asset(resolution: CrossDealObligorResolution) -> dict[tuple[str, str], ObligorTier]:
    """Which tier each asset's obligor group sits in.

    Derived from the resolution rather than recomputed, so this module cannot
    disagree with #562 about what was proven.
    """
    proposed = {
        _group_key(group)
        for candidate in resolution.candidates
        for group in (candidate.left, candidate.right)
    }
    tiers: dict[tuple[str, str], ObligorTier] = {}
    for group in resolution.proven_shared:
        for member in group.members:
            tiers[member.key] = ObligorTier.proven_shared
    for entry in resolution.unresolved:
        tier = (
            ObligorTier.candidate_proposed
            if _group_key(entry.group) in proposed
            else ObligorTier.unresolved
        )
        for member in entry.group.members:
            tiers[member.key] = tier
    return tiers


def _asset_attributes(deal: str, assets: Iterable[CollateralAsset]) -> list[AssetAttributes]:
    """Project a deal's parsed assets onto the fields bucketing needs."""
    return [
        AssetAttributes(
            deal=deal,
            identifier=asset.identifier,
            principal_balance=asset.principal_balance,
            currency=asset.currency,
            sp_industry=asset.sp_industry,
            fitch_industry=asset.fitch_industry,
            country=asset.country,
            sp_rating=asset.sp_rating,
            fitch_rating=asset.fitch_rating,
        )
        for asset in assets
    ]


def _assert_every_asset_attributed(
    resolution: CrossDealObligorResolution, portfolio: CrossDealPortfolio
) -> None:
    """Refuse a portfolio whose attribute re-join lost — or invented — an asset.

    This is the guard that makes a keying collapse **loud**. #562 proved every
    asset lands in exactly one obligor group; this proves the second index, the
    one bucketing reads, still holds all of them. Keying it on ``issuer_name``
    is the obvious implementation and it drops every asset after the first
    sharing a name — both committed schedules repeat names across facilities, so
    the loss is real rather than theoretical, and it shrinks the denominator
    every concentration divides by, which reads as health (#478).

    It lives in the builder rather than in a test so that it runs on every
    portfolio ever constructed, not only the inputs a test happened to choose.
    A balance is compared too: an index that silently resolved two rows to one
    would keep the count and change the money.
    """
    expected = {
        member.key: member.principal_balance
        for group in resolution.all_groups
        for member in group.members
    }
    keys = [a.key for a in portfolio.attributes]
    if len(set(keys)) != len(keys):
        duplicates = sorted({k for k in keys if keys.count(k) > 1})
        raise ValueError(
            f"the attribute index is not unique on (deal, identifier): {duplicates}"
        )

    missing = sorted(set(expected) - set(keys))
    invented = sorted(set(keys) - set(expected))
    if missing or invented:
        raise ValueError(
            f"the attribute re-join does not account for the resolved assets: "
            f"{len(missing)} lost (e.g. {missing[:3]}), {len(invented)} invented "
            f"(e.g. {invented[:3]})"
        )
    for attributes in portfolio.attributes:
        if attributes.principal_balance != expected[attributes.key]:
            raise ValueError(
                f"asset {attributes.key} carries balance "
                f"{attributes.principal_balance} in the attribute index but "
                f"{expected[attributes.key]} in the resolution"
            )


def build_portfolio(schedules: Mapping[str, CollateralSchedule]) -> CrossDealPortfolio:
    """Resolve identity across two or more schedules and re-join what buckets need.

    Pure and deterministic: no network, no LLM, no file access. ``schedules``
    maps a deal key to its parsed schedule; the result follows the sorted deal
    keys so two runs, and two input orderings, agree.

    Raises ``ValueError`` if the re-join would not account for every resolved
    asset (see :func:`_assert_every_asset_attributed`), or if the deals report in
    more than one currency.
    """
    resolution = resolve_obligors(schedules)

    as_of: list[DealAsOf] = []
    attributes: list[AssetAttributes] = []
    for deal in resolution.deals:
        schedule = schedules[deal]
        as_of.append(
            DealAsOf(
                deal=deal,
                deal_name=schedule.deal_name,
                reporting_date=schedule.reporting_date,
                period_label=schedule.period_label,
            )
        )
        attributes.extend(_asset_attributes(deal, schedule.assets))

    portfolio = CrossDealPortfolio(
        as_of=tuple(as_of),
        resolution=resolution,
        attributes=tuple(attributes),
    )
    _assert_every_asset_attributed(resolution, portfolio)
    return portfolio


def _fold_one_deals_vocabulary(
    axis: ExposureAxis, deal: str, published: Iterable[str]
) -> dict[str, str]:
    """Fold one deal's published labels, refusing a fold that merges two of them.

    #563's rule, applied per axis: prove the fold injective over each *published
    table*, not over your examples. Two distinct labels one report prints
    separately must not become one bucket — that would restate the report's own
    table — and this is the check that catches an axis using the wrong fold. The
    industry fold drops ``+`` and ``-``, so applying it to a report publishing
    ``B`` and ``B+`` raises here rather than merging three notches into one.
    """
    folded: dict[str, str] = {}
    for label in published:
        key = axis.fold(label)
        if not key:
            raise ValueError(
                f"{deal}: label {label!r} folds to nothing on {axis.label}, so it "
                "cannot key a bucket"
            )
        seen = folded.get(key)
        if seen is not None and seen != label:
            raise LabelCollisionError(
                f"{deal} publishes {seen!r} and {label!r} as two {axis.label} values, "
                f"but this axis folds both to {key!r}; merging them would restate the "
                "report's own table"
            )
        folded[key] = label
    return folded


def aggregate_by_axis(portfolio: CrossDealPortfolio, axis: ExposureAxis) -> BucketedExposure:
    """Combined look-through exposure on one **named** axis.

    The axis argument has no default: #563's requirement that a cross-deal
    industry figure name its taxonomy is enforced by :class:`ExposureAxis`, and
    there is no path to a figure that skipped it.

    Every bucket carries the date each contributing deal is as of and the obligor
    tier split behind its balance. Assets the report gave no value for are
    returned in ``unattributed`` and never placed in a bucket.
    """
    tiers = _tier_by_asset(portfolio.resolution)

    # Called for its refusal, not its result: it proves this axis's fold does not
    # merge two labels one report publishes separately, before anything is summed.
    for entry in portfolio.as_of:
        _fold_one_deals_vocabulary(
            axis,
            entry.deal,
            [
                value
                for a in portfolio.attributes
                if a.deal == entry.deal and (value := a.published_on(axis)) is not None
            ],
        )

    # folded label -> deal -> [balance, count]; and the published spellings that
    # reached it. Keyed on the fold, never on the raw string, so two deals'
    # spellings of one label join instead of becoming two thin buckets.
    placed: dict[str, dict[str, list]] = {}
    spellings: dict[str, set[str]] = {}
    split_parts: dict[str, dict[ObligorTier, Decimal]] = {}
    unplaced: dict[str, list] = {}

    for attributes in portfolio.attributes:
        value = attributes.published_on(axis)
        if value is None:
            row = unplaced.setdefault(attributes.deal, [Decimal(0), 0])
            row[0] += attributes.principal_balance
            row[1] += 1
            continue
        key = axis.fold(value)
        spellings.setdefault(key, set()).add(value)
        row = placed.setdefault(key, {}).setdefault(attributes.deal, [Decimal(0), 0])
        row[0] += attributes.principal_balance
        row[1] += 1
        tier = tiers[attributes.key]
        parts = split_parts.setdefault(key, {t: Decimal(0) for t in ObligorTier})
        parts[tier] += attributes.principal_balance

    stated = {entry.deal: entry.stated for entry in portfolio.as_of}

    def contributions(rows: Mapping[str, Sequence]) -> tuple[DealContribution, ...]:
        return tuple(
            DealContribution(
                deal=deal,
                as_of=stated[deal],
                balance=rows[deal][0],
                asset_count=rows[deal][1],
            )
            for deal in sorted(rows)
        )

    buckets: list[ExposureBucket] = []
    for key in sorted(placed):
        per_deal = contributions(placed[key])
        parts = split_parts[key]
        buckets.append(
            ExposureBucket(
                label=sorted(spellings[key])[0],
                published_spellings=tuple(sorted(spellings[key])),
                balance=sum((c.balance for c in per_deal), Decimal(0)),
                asset_count=sum(c.asset_count for c in per_deal),
                split=ResolutionSplit(
                    proven_shared=parts[ObligorTier.proven_shared],
                    candidate_proposed=parts[ObligorTier.candidate_proposed],
                    unresolved=parts[ObligorTier.unresolved],
                ),
                per_deal=per_deal,
            )
        )
    buckets.sort(key=lambda b: (-b.balance, b.label))

    unattributed_contributions = contributions(unplaced)
    figure = BucketedExposure(
        axis=axis,
        as_of=portfolio.as_of,
        buckets=tuple(buckets),
        unattributed=UnattributedExposure(
            reason=AttributeAbsence.not_published,
            balance=sum((c.balance for c in unattributed_contributions), Decimal(0)),
            asset_count=sum(c.asset_count for c in unattributed_contributions),
            per_deal=unattributed_contributions,
        ),
        bounds=portfolio.resolution.distinct_obligor_bounds(),
        total_balance=portfolio.total_principal_balance,
    )
    _assert_axis_accounts_for_every_asset(portfolio, figure)
    return figure


def _assert_axis_accounts_for_every_asset(
    portfolio: CrossDealPortfolio, figure: BucketedExposure
) -> None:
    """Refuse a figure that placed fewer assets than the portfolio holds.

    The model validator already proves the *balances* add up. This proves the
    *population* does, which is the half a netting bug can survive: moving a
    residual into a bucket keeps the total intact while changing what the
    figure says. It lives in the builder for the same reason the census does.
    """
    if figure.asset_count != portfolio.asset_count:
        raise ValueError(
            f"the {figure.axis.label} figure accounts for {figure.asset_count} assets "
            f"against the portfolio's {portfolio.asset_count}"
        )
    if figure.total_balance != portfolio.total_principal_balance:
        raise ValueError(
            f"the {figure.axis.label} figure states a total of {figure.total_balance} "
            f"against the portfolio's {portfolio.total_principal_balance}"
        )


def _row_for_group(
    group: ObligorGroup, tier: ObligorTier, stated: Mapping[str, str]
) -> ObligorExposureRow:
    """One obligor group as a row, split by the deal each member came from."""
    rows: dict[str, list] = {}
    for member in group.members:
        row = rows.setdefault(member.deal, [Decimal(0), 0])
        row[0] += member.principal_balance
        row[1] += 1
    per_deal = tuple(
        DealContribution(
            deal=deal, as_of=stated[deal], balance=rows[deal][0], asset_count=rows[deal][1]
        )
        for deal in sorted(rows)
    )
    return ObligorExposureRow(
        display_name=group.display_name,
        tier=tier,
        balance=sum((c.balance for c in per_deal), Decimal(0)),
        per_deal=per_deal,
    )


def aggregate_by_obligor(portfolio: CrossDealPortfolio) -> ObligorExposure:
    """Combined look-through exposure by obligor, every name carrying its tier.

    Candidates are reported as :class:`ObligorProposal` and never merged into a
    row: applying one would make the distinct-obligor count a point estimate,
    which is the thing #562 built bounds to avoid.
    """
    stated = {entry.deal: entry.stated for entry in portfolio.as_of}
    resolution = portfolio.resolution
    proposed = {
        _group_key(group)
        for candidate in resolution.candidates
        for group in (candidate.left, candidate.right)
    }

    rows_by_group: dict[tuple[tuple[str, str], ...], ObligorExposureRow] = {}
    for group in resolution.proven_shared:
        rows_by_group[_group_key(group)] = _row_for_group(
            group, ObligorTier.proven_shared, stated
        )
    for entry in resolution.unresolved:
        key = _group_key(entry.group)
        tier = (
            ObligorTier.candidate_proposed if key in proposed else ObligorTier.unresolved
        )
        rows_by_group[key] = _row_for_group(entry.group, tier, stated)

    rows = tuple(
        sorted(rows_by_group.values(), key=lambda r: (-r.balance, r.display_name, r.tier.value))
    )
    proposals = tuple(
        ObligorProposal(
            folded=candidate.folded,
            left=rows_by_group[_group_key(candidate.left)],
            right=rows_by_group[_group_key(candidate.right)],
        )
        for candidate in resolution.candidates
    )

    figure = ObligorExposure(
        as_of=portfolio.as_of,
        rows=rows,
        proposals=proposals,
        bounds=resolution.distinct_obligor_bounds(),
        total_balance=portfolio.total_principal_balance,
    )
    _assert_obligor_rows_account_for_every_asset(portfolio, figure)
    return figure


def _assert_obligor_rows_account_for_every_asset(
    portfolio: CrossDealPortfolio, figure: ObligorExposure
) -> None:
    """Refuse an obligor figure that lost a facility while keeping the balance."""
    if figure.asset_count != portfolio.asset_count:
        raise ValueError(
            f"the obligor figure accounts for {figure.asset_count} assets against the "
            f"portfolio's {portfolio.asset_count}"
        )
    if len(figure.rows) != len(portfolio.resolution.all_groups):
        raise ValueError(
            f"the obligor figure carries {len(figure.rows)} rows against "
            f"{len(portfolio.resolution.all_groups)} resolved groups"
        )
