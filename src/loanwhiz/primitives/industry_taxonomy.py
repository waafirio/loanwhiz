"""Which industry taxonomy a cross-deal concentration is expressed in (#563).

Every :class:`~loanwhiz.primitives.collateral_schedule_parser.CollateralAsset`
carries both an S&P and a Fitch industry, and the two deals on this platform do
not spell either the same way. Aggregating across deals therefore needs a
decision — one taxonomy, or a map between them — and a figure that does not say
which one it chose is not comparable to the concentration the deal's own report
publishes. Cairn's March 2025 report states its largest industry as **11.36%**
on S&P and **10.83%** on Fitch: the same portfolio, two different numbers, so
an unnamed axis makes a figure wrong rather than merely vague.

The decision, and why
---------------------
**Fitch** is the cross-deal axis (:data:`CROSS_DEAL_TAXONOMY`). S&P remains a
per-deal axis and is deliberately **not** joined across deals.

It is not a granularity preference, and it is not an oracle argument — since
#530 both taxonomies are tied out per bucket against the report's own published
concentration table (``_BUCKET_TABLES`` in ``collateral_schedule_parser``), on
both deals, every period. It is the join, measured:

- the two deals' **Fitch** vocabularies canonicalise onto substantially one
  vocabulary; the labels that do not join are genuine portfolio differences
  (one deal holds a utility, the other does not);
- their **S&P** vocabularies do not, because they were published against
  *different GICS vintages*. Contego emits the post-2023 spellings
  (``Consumer staples distribution and retail``, ``Financial services``) and
  Cairn the pre-2023 ones (``Food & Staples Retailing``,
  ``Diversified Financial Services``). Close to half the combined S&P
  vocabulary is unjoinable, and almost all of that is vintage drift rather
  than a real difference in what the two deals hold.

That asymmetry matters in one direction only. An unjoined pair holds one
exposure apart in two buckets, so the concentration reported for it is **lower
than the truth** — a book reads as more diversified than it is, which is the
error that harms a buyer and looks like good news. Choosing the axis whose
vocabularies actually join is how that error is made small; naming the declined
pairs is how the residue stays visible.

What this module will not do
----------------------------
It canonicalises **orthography** and never **semantics**. Case, ``&``/``and``,
punctuation and whitespace are presentation of one label; ``Auto Components``
and ``Automobile components`` are two GICS vintages' names for one sector, and
deciding they are the same is a claim about the taxonomy, not about the string.
Mapping across vintages needs a GICS concordance this repo does not hold, so
those pairs are listed in :data:`DECLINED_CROSS_VINTAGE_PAIRS` and left
unjoined. A refusal that is named can be reviewed and reversed; one that is
silent cannot.

Nothing is ever folded into an "Other" bucket. :class:`TaxonomyJoin` is a
**partition** of the canonical union — joined, left-only and right-only, with
nothing dropped — following ``report_label_fold.FoldedPoP.unplaced`` (#514) and
``pool_stratification.UNAVAILABLE_BUCKET``. This repo has no residual bucket
anywhere and gains none here: a bucket that absorbs the unknown is how a
concentration understates itself (#496).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from enum import Enum

from pydantic import BaseModel, Field, model_validator

__all__ = [
    "CROSS_DEAL_TAXONOMY",
    "DECLINED_CROSS_VINTAGE_PAIRS",
    "DeclinedPair",
    "IndustryTaxonomy",
    "JoinedLabel",
    "LabelCollisionError",
    "TaxonomyJoin",
    "canonical_label",
    "canonicalise_vocabulary",
    "join_vocabularies",
]


class IndustryTaxonomy(str, Enum):
    """The industry classification a figure is expressed in.

    A closed enum rather than a free string, and a *required* argument
    everywhere below, so that a cross-deal industry figure cannot be built
    without naming its axis. The issue's "whichever is chosen, the figure must
    say so" is enforced by the type rather than by a docstring asking nicely.
    """

    sp = "sp"
    fitch = "fitch"

    @property
    def display_name(self) -> str:
        """How the reports themselves name this taxonomy."""
        return "S&P" if self is IndustryTaxonomy.sp else "Fitch"

    @property
    def asset_attribute(self) -> str:
        """The ``CollateralAsset`` / ``ReportAggregates`` attribute it lives on."""
        return "sp_industry" if self is IndustryTaxonomy.sp else "fitch_industry"


#: The taxonomy a **cross-deal** industry concentration is expressed in (#563).
#: Recorded in ``docs/data-card.md``. Per-deal figures may use either; only the
#: cross-deal join is settled here, and it is settled in one place so that a
#: consumer cannot pick a different axis by accident.
CROSS_DEAL_TAXONOMY: IndustryTaxonomy = IndustryTaxonomy.fitch


class LabelCollisionError(ValueError):
    """Two distinct published labels canonicalise to one form.

    Raised at the join seam rather than reported downstream. A report that
    prints two buckets is a report that considers them two buckets; merging
    them would restate the document's own table as something it does not say.
    Cairn's December 2024 Fitch table is the live near-miss — it publishes both
    ``Building and materials`` and ``Buildings and materials`` — so this guard
    sits one plural-strip away from firing, and is not a hypothetical.
    """


def canonical_label(raw: str) -> str:
    """Fold one published industry label to its orthographic canonical form.

    Case, diacritics, ``&`` versus ``and``, punctuation and whitespace only.
    The fold is deliberately **weaker** than
    ``loanwhiz.extraction.taxonomy._normalise``, which collapses every
    non-alphanumeric run to a separator and so folds ``Aerospace & Defense``
    to ``aerospace_defense`` — losing exactly the conjunction that decides
    whether it is the same label as ``Aerospace and defence``.

    It does **not** stem, singularise, or fold spelling variants. Each of those
    would merge labels a report prints separately; see
    :class:`LabelCollisionError`.
    """
    text = unicodedata.normalize("NFKD", raw or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.casefold()
    # `&` and `and` are one conjunction rendered two ways, not two words.
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def canonicalise_vocabulary(labels: Iterable[str]) -> dict[str, str]:
    """Map each label's canonical form to the label as published.

    Raises :class:`LabelCollisionError` if two *distinct* published labels share
    a canonical form. Repeating one label is harmless — a vocabulary gathered
    across several periods will do it — but two different ones colliding means
    the fold has become semantic, and the seam refuses rather than merging.
    """
    canonical: dict[str, str] = {}
    for label in labels:
        key = canonical_label(label)
        if not key:
            raise LabelCollisionError(f"label {label!r} canonicalises to nothing")
        seen = canonical.get(key)
        if seen is not None and seen != label:
            raise LabelCollisionError(
                f"{seen!r} and {label!r} both canonicalise to {key!r}; "
                "the report publishes them as two buckets, so folding them "
                "together would restate its own table"
            )
        canonical[key] = label
    return canonical


class JoinedLabel(BaseModel):
    """One canonical form both vocabularies reached, and how each spelled it."""

    canonical: str
    left: str
    right: str

    @property
    def spellings_differ(self) -> bool:
        """Whether the join actually did any work for this label."""
        return self.left != self.right


class TaxonomyJoin(BaseModel):
    """Two deals' vocabularies on one axis, as a partition.

    ``joined``, ``left_only`` and ``right_only`` are disjoint and together cover
    every canonical form either side published. There is no fourth bucket and no
    residual: a label that does not join is *named*, on the side that published
    it. The model validator enforces the partition, so a consumer cannot be
    handed a join that quietly lost a label.
    """

    taxonomy: IndustryTaxonomy
    left_deal: str
    right_deal: str
    joined: list[JoinedLabel] = Field(default_factory=list)
    #: Canonical form → the label as the left deal published it.
    left_only: dict[str, str] = Field(default_factory=dict)
    right_only: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _partition_is_disjoint(self) -> TaxonomyJoin:
        joined = {entry.canonical for entry in self.joined}
        if len(joined) != len(self.joined):
            raise ValueError("joined carries the same canonical form twice")
        overlaps = (
            joined & set(self.left_only),
            joined & set(self.right_only),
            set(self.left_only) & set(self.right_only),
        )
        for overlap in overlaps:
            if overlap:
                raise ValueError(
                    f"the join is not a partition: {sorted(overlap)} appears twice"
                )
        return self

    @property
    def canonical_union(self) -> set[str]:
        """Every canonical form either deal published — the partition's total."""
        return (
            {entry.canonical for entry in self.joined}
            | set(self.left_only)
            | set(self.right_only)
        )

    @property
    def unjoined_labels(self) -> list[str]:
        """Every published label that reached no counterpart, both sides."""
        return sorted([*self.left_only.values(), *self.right_only.values()])

    def describe(self) -> str:
        """One line naming the axis — what a figure built on this must carry."""
        return (
            f"{self.taxonomy.display_name} industry, "
            f"{self.left_deal} × {self.right_deal}: "
            f"{len(self.joined)} joined, {len(self.unjoined_labels)} unjoined"
        )


def join_vocabularies(
    taxonomy: IndustryTaxonomy,
    *,
    left_deal: str,
    left: Iterable[str],
    right_deal: str,
    right: Iterable[str],
) -> TaxonomyJoin:
    """Join two deals' published vocabularies on one axis, orthographically.

    ``taxonomy`` is positional and required: a join that did not name its axis
    would be the unnamed figure this issue exists to prevent.
    """
    left_canonical = canonicalise_vocabulary(left)
    right_canonical = canonicalise_vocabulary(right)
    shared = sorted(set(left_canonical) & set(right_canonical))
    return TaxonomyJoin(
        taxonomy=taxonomy,
        left_deal=left_deal,
        right_deal=right_deal,
        joined=[
            JoinedLabel(
                canonical=key, left=left_canonical[key], right=right_canonical[key]
            )
            for key in shared
        ],
        left_only={
            key: value
            for key, value in sorted(left_canonical.items())
            if key not in right_canonical
        },
        right_only={
            key: value
            for key, value in sorted(right_canonical.items())
            if key not in left_canonical
        },
    )


class DeclinedPair(BaseModel):
    """Two labels this repo can see are related and refuses to merge."""

    taxonomy: IndustryTaxonomy
    earlier: str
    later: str
    reason: str


#: Cross-vintage S&P pairs left unjoined **on purpose**.
#:
#: Each is a pre-2023 GICS name beside its post-2023 replacement, observed in
#: the two deals' own published concentration tables. They are recorded rather
#: than mapped because merging them is a claim about the GICS revision — which
#: sub-industries moved, and whether the two deals' holdings are on the same
#: side of the move — and this repo holds no concordance that could support it.
#: A similarity score dressed up as a mapping would under- or over-match with
#: equal confidence, so the pairs stay visible and unmerged until a real
#: concordance is registered.
#:
#: This constant is documentation with a test attached, not a lookup table:
#: nothing joins on it, and :func:`join_vocabularies` must leave every pair
#: below on its own side.
DECLINED_CROSS_VINTAGE_PAIRS: tuple[DeclinedPair, ...] = (
    DeclinedPair(
        taxonomy=IndustryTaxonomy.sp,
        earlier="Auto Components",
        later="Automobile components",
        reason=(
            "GICS 2023 renamed the sub-industry; whether the two deals' "
            "holdings sit in the same successor bucket is not derivable from "
            "the names."
        ),
    ),
    DeclinedPair(
        taxonomy=IndustryTaxonomy.sp,
        earlier="Food & Staples Retailing",
        later="Consumer staples distribution and retail",
        reason=(
            "GICS 2023 replaced the Food & Staples Retailing industry and "
            "redistributed its constituents; the new name is broader than a "
            "rename."
        ),
    ),
    DeclinedPair(
        taxonomy=IndustryTaxonomy.sp,
        earlier="Diversified Financial Services",
        later="Financial services",
        reason=(
            "GICS 2023 merged Diversified Financial Services and Consumer "
            "Finance into Financial Services, so the older labels are two "
            "buckets where the newer is one — a merge in the wrong direction "
            "for a concentration figure."
        ),
    ),
    DeclinedPair(
        taxonomy=IndustryTaxonomy.sp,
        earlier="Consumer Finance",
        later="Financial services",
        reason=(
            "The second half of the same GICS 2023 merge; joining it would "
            "silently pool it with Diversified Financial Services."
        ),
    ),
)
