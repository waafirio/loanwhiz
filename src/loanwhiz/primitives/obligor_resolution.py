"""Which obligors two deals actually share, and which cannot be resolved (#562).

Two CLOs identify the same borrower by their own conventions, and no single
field gives the join. Cairn and Contego both carry ``identifier``,
``issuer_name`` and ``facility_name`` on every
:class:`~loanwhiz.primitives.collateral_schedule_parser.CollateralAsset`, and
each of the three is insufficient alone:

- an **identifier** join is sound but partial — it proves a shared obligor and
  never invents one, yet two deals may hold one borrower under two loan ids;
- a **name** join is neither. It misses pairs the identifier proves, and it can
  claim a match that is not one.

The direction of error, and why it is not symmetric
---------------------------------------------------
A missed join reports one exposure as two, so the book reads as **more
diversified than it is** — the error that harms a buyer and looks like good
news. That is the error this module is built to make small and, where it cannot
be removed, to *name*.

Measured on the two committed schedules (Cairn March 2025, Contego August
2024), the case for identifier-first is not an argument but an observation:
of the identifiers both deals hold, **most carry issuer names that disagree**,
and for many the two names are not recognisably the same company —
``LX213528`` is ``Alpha AB Bidco B.V.`` in one report and ``Ammega Group BV``
in the other. Name equality alone would find barely a third of the joins the
identifier proves. Conversely, some pairs share a folded name under *different*
identifiers, which an identifier join alone would miss. So neither rule
subsumes the other, and the two are kept in separate tiers rather than pooled
behind one score.

Three record kinds, never blended
---------------------------------
Following #513: the part that could not be resolved is a **different kind of
record**, not an empty instance of the resolved kind. A consumer that wants
only what was proved reads :attr:`CrossDealObligorResolution.proven_shared`; a
compliance reader who must see the residue reads
:attr:`~CrossDealObligorResolution.unresolved` and finds every name enumerated
with the reason it could not be resolved. Nothing is dropped and nothing is
folded into a residual bucket — this repo has none anywhere (#496, #514).

The count is **bounds, never a point** (:class:`ObligorBounds`). A point
estimate has to assume something about the unresolved set, and the only
available assumption — that every unresolved name is a distinct obligor — is
precisely the diversified-looking direction. There is deliberately no accessor
returning a single number.

Two claims are re-derived rather than trusted
---------------------------------------------
A partition check proves nothing was *dropped* while still admitting an entry
that claims two unrelated assets are one obligor — the overstatement mirroring
the understatement above. So, following ``industry_taxonomy.JoinedLabel``
(#563), each record re-derives its own claim:
:class:`ObligorGroup` re-checks that its members really are connected under the
proven rules, and :class:`ObligorCandidate` re-folds both names rather than
believing the folded form it was handed.

What this module will not do
----------------------------
It never merges a candidate. A folded-name match is *proposed*, with both
spellings carried, and applying it is the reader's decision — visible in
:attr:`ObligorBounds.lower` as the effect of accepting every proposal at once.

It also never treats a **malformed** name as a name. Both committed schedules
contain rows whose ``issuer_name`` has absorbed neighbouring text from the
report — a balance (``Ziggo Secured Finance B.V. 411,342,140.14``) or a whole
subtotal line. That is an upstream parsing defect, and this module contains it
rather than repairing it: such a name is never a match key, so it cannot
manufacture a join. The asset still resolves by identifier if it can, and is
otherwise named in ``unresolved`` with
:attr:`UnresolvedReason.name_unusable`. A refusal that is named can be reviewed;
a silent one cannot (#493, #549).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from loanwhiz.primitives.collateral_schedule_parser import (
    CollateralAsset,
    CollateralSchedule,
)

__all__ = [
    "AssetRef",
    "CrossDealObligorResolution",
    "ObligorBounds",
    "ObligorCandidate",
    "ObligorGroup",
    "ResolutionEvidence",
    "UnresolvedObligor",
    "UnresolvedReason",
    "candidate_fold",
    "name_is_usable",
    "resolve_obligors",
    "within_deal_name",
]


class ResolutionEvidence(str, Enum):
    """What joined two assets into one obligor group.

    ``shared_identifier`` and ``same_deal_name`` are *proven*; ``folded_name``
    is only ever a **candidate** and never appears on an
    :class:`ObligorGroup` — it is carried by :class:`ObligorCandidate`, which
    is not applied. The enum is closed so that a new rule cannot be added
    without deciding, in the type, which tier it belongs to.
    """

    #: The same loan identifier or ISIN in both deals. Cross-deal, proven.
    shared_identifier = "shared_identifier"
    #: The same issuer name inside one deal — one house style, one document.
    same_deal_name = "same_deal_name"
    #: The same name across deals after suffix folding. Candidate only.
    folded_name = "folded_name"


class UnresolvedReason(str, Enum):
    """Why an obligor's cross-deal status could not be settled.

    Both mean "not proven to be shared" and neither means "proven distinct" —
    the distinction this module exists to keep. ``name_unusable`` is the
    strictly worse case: the group cannot even be *offered* as a candidate.
    """

    #: Seen in one deal only; no shared identifier and no name candidate.
    single_deal_only = "single_deal_only"
    #: The issuer name is malformed upstream, so it is not a usable match key.
    name_unusable = "name_unusable"


#: A run of digits grouped as a money amount (``411,342,140.14``). A legal name
#: may legitimately contain digits — ``Blitz 20-487 GmbH``, ``Platin2025
#: Holdings`` — so the test is the *thousands-separated decimal* shape, not the
#: presence of a digit, which would reject real companies.
_BALANCE_TOKEN = re.compile(r"\d{1,3}(?:,\d{3})+\.\d{2}")

#: A report section label that has bled into a name field.
_SECTION_TOKEN = re.compile(r"\b(?:sub)?total\s*:", re.IGNORECASE)

#: Legal-form suffixes, stripped from the tail only, and only for the
#: **candidate** fold. Tail-only matters: ``Ineos Finance PLC`` and ``Ineos
#: Quattro Holdings UK Limited`` must not both collapse onto ``INEOS``.
_LEGAL_SUFFIXES = frozenset(
    {
        "SARL",
        "SASU",
        "GMBH",
        "LIMITED",
        "HOLDINGS",
        "HOLDING",
        "GROUP",
        "SPA",
        "PLC",
        "LTD",
        "INC",
        "LLC",
        "SAS",
        "MBH",
        "NV",
        "BV",
        "AG",
        "AB",
        "AS",
        "SA",
        "LP",
    }
)


def name_is_usable(name: str | None) -> bool:
    """Whether ``name`` can serve as a match key at all.

    ``False`` for an absent name and for one carrying text the parser dragged
    in from a neighbouring column — a money amount or a ``Subtotal:`` label.
    Such a string is not a legal-entity name, and matching on it would
    manufacture a join out of a parsing defect rather than out of the data.
    """
    if name is None:
        return False
    if not name.strip():
        return False
    if _BALANCE_TOKEN.search(name):
        return False
    return not _SECTION_TOKEN.search(name)


def within_deal_name(name: str) -> str:
    """Normalise a name for **within-deal** grouping: case and whitespace only.

    Inside one report the issuer names come from one house style, so exact
    equality after this light touch is strong evidence. No suffix is stripped
    here: ``Ineos Finance PLC`` and ``Ineos Quattro`` are two obligors in one
    document and must not be merged by an aggressive fold.
    """
    return " ".join(name.upper().split())


def candidate_fold(name: str) -> str:
    """Fold a name for **cross-deal** candidate matching.

    Strips punctuation and legal-form suffixes, which is how the same borrower
    is spelled differently by two trustees (``Sunrise Bidco S.a r.l.`` /
    ``Sunrise Bidco Sarl``). Deliberately more aggressive than
    :func:`within_deal_name`, and deliberately never proof: it is exactly this
    aggressiveness that makes a match a *proposal*.

    Returns ``""`` for a name that folds away to nothing, which never matches.
    """
    tokens = _merge_initialisms(re.sub(r"[^A-Z0-9 ]+", " ", name.upper()).split())
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _merge_initialisms(tokens: list[str]) -> list[str]:
    """Rejoin a dotted abbreviation that punctuation-stripping split apart.

    ``B.V.`` becomes ``B V`` once the dots go, while the same company spelled
    ``BV`` in the other report stays one token — so the two would fold apart
    and the pair would never be proposed. That is the understatement direction
    (a real overlap left unnamed), and it is the exact punctuation drift #563
    found on the industry labels.

    A run of **two or more** consecutive single characters is one abbreviation
    (``S A R L`` → ``SARL``); a lone trailing letter is left alone, because a
    facility suffix like ``Bidco B`` is part of the name, not a legal form.
    """
    merged: list[str] = []
    run: list[str] = []
    for token in tokens + [""]:
        if len(token) == 1 and token.isalpha():
            run.append(token)
            continue
        if len(run) >= 2:
            merged.append("".join(run))
        else:
            merged.extend(run)
        run = []
        if token:
            merged.append(token)
    return merged


class AssetRef(BaseModel, frozen=True):
    """One asset, identified by the pair that is unique within a deal.

    Frozen and hashable so groups can be compared and censused as sets. Carries
    the balance because an obligor's exposure is the sum over its members, and
    a consumer that had to re-join to the schedule to get it would be one
    re-join away from the collapse this module guards against.
    """

    deal: str
    identifier: str
    issuer_name: str
    facility_name: str
    principal_balance: Decimal

    @property
    def key(self) -> tuple[str, str]:
        """The census key: ``(deal, identifier)``."""
        return (self.deal, self.identifier)


class ObligorGroup(BaseModel, frozen=True):
    """Assets the proven rules say are one borrower.

    A group is a connected component under the two proven rules, so a
    singleton is a perfectly ordinary group: one asset nothing else joined to.

    The validator re-derives the connection rather than trusting it. Without
    it, a partition check could confirm no asset was lost while still admitting
    a group asserting that two unrelated borrowers are one — pooling two
    exposures into a single obligor, which overstates concentration exactly as
    a missed join understates it. ``industry_taxonomy.JoinedLabel`` (#563)
    makes the same move for the same reason.
    """

    members: tuple[AssetRef, ...]
    evidence: tuple[ResolutionEvidence, ...] = ()

    @model_validator(mode="after")
    def _connection_is_derived_not_asserted(self) -> ObligorGroup:
        if not self.members:
            raise ValueError("an obligor group must carry at least one asset")

        keys = [member.key for member in self.members]
        if len(set(keys)) != len(keys):
            raise ValueError("an obligor group carries the same asset twice")

        if ResolutionEvidence.folded_name in self.evidence:
            raise ValueError(
                "folded_name is candidate evidence and can never form a proven group"
            )

        if len(self.members) == 1:
            if self.evidence:
                raise ValueError("a single-asset group claims evidence that joined nothing")
            return self

        if not self.evidence:
            raise ValueError("a multi-asset group must name the evidence that joined it")

        reached = _component_of(self.members[0], self.members)
        if len(reached) != len(self.members):
            unreachable = sorted(m.key for m in self.members if m not in reached)
            raise ValueError(
                "group members are not connected under the proven rules; "
                f"unreachable: {unreachable}"
            )

        derived = _evidence_within(self.members)
        if derived != set(self.evidence):
            raise ValueError(
                f"claimed evidence {sorted(e.value for e in self.evidence)} is not what "
                f"joins these members ({sorted(e.value for e in derived)})"
            )
        return self

    @property
    def deals(self) -> frozenset[str]:
        """Every deal this obligor appears in."""
        return frozenset(member.deal for member in self.members)

    @property
    def principal_balance(self) -> Decimal:
        """Total exposure to this obligor across every member asset."""
        return sum((member.principal_balance for member in self.members), Decimal(0))

    @property
    def display_name(self) -> str:
        """A stable name for the group — the first member's, by sort order."""
        return sorted(self.members, key=lambda m: m.key)[0].issuer_name


class ObligorCandidate(BaseModel, frozen=True):
    """A **proposed** cross-deal link between two proven groups. Never applied.

    Both spellings are carried so a reviewer can judge the proposal without
    re-joining to the schedules, and the folded form is re-derived from each
    side rather than trusted — a candidate that could assert an arbitrary
    ``folded`` value would be a way to claim two unrelated obligors matched,
    the overstatement ``ObligorGroup`` guards against on the proven side.
    """

    folded: str
    left: ObligorGroup
    right: ObligorGroup

    @model_validator(mode="after")
    def _fold_is_derived_not_asserted(self) -> ObligorCandidate:
        if not self.folded:
            raise ValueError("a candidate cannot be keyed on an empty fold")
        for side, group in (("left", self.left), ("right", self.right)):
            folds = {candidate_fold(m.issuer_name) for m in group.members}
            if folds != {self.folded}:
                raise ValueError(
                    f"{side} group folds to {sorted(folds)}, not to the claimed "
                    f"{self.folded!r}"
                )
        if self.left.deals & self.right.deals:
            raise ValueError("a cross-deal candidate must link groups from different deals")
        shared = {m.identifier for m in self.left.members} & {
            m.identifier for m in self.right.members
        }
        if shared:
            raise ValueError(
                f"these groups share identifier(s) {sorted(shared)} and are proven, "
                "not candidates"
            )
        return self

    @property
    def spellings_differ(self) -> bool:
        """Whether the fold did any work, or the two names were already equal."""
        return self.left.display_name != self.right.display_name


class UnresolvedObligor(BaseModel, frozen=True):
    """An obligor whose cross-deal status could not be settled (#513).

    Its own record kind, with its own reason — never an
    :class:`ObligorGroup` with an empty partner list, because an empty
    collection passes every check applied to it and the unresolved fraction
    would read as free green.

    "Unresolved" is not "distinct". It means only that this deal's report and
    the other's give no evidence either way.
    """

    group: ObligorGroup
    reason: UnresolvedReason
    detail: str = ""


class ObligorBounds(BaseModel, frozen=True):
    """How many distinct obligors the two deals hold between them — as a range.

    ``upper`` rejects every candidate (each proposal is two different
    borrowers); ``lower`` accepts them all. There is intentionally **no**
    accessor returning one number: a point estimate must assume something about
    what was not proved, and assuming every unresolved name is distinct is the
    assumption that makes a book look diversified.
    """

    lower: int
    upper: int

    @model_validator(mode="after")
    def _lower_does_not_exceed_upper(self) -> ObligorBounds:
        if self.lower > self.upper:
            raise ValueError(f"lower bound {self.lower} exceeds upper bound {self.upper}")
        return self

    @property
    def is_exact(self) -> bool:
        """Whether the two bounds coincide — i.e. no candidate was proposed."""
        return self.lower == self.upper

    def __str__(self) -> str:
        return str(self.lower) if self.is_exact else f"{self.lower}-{self.upper}"


class CrossDealObligorResolution(BaseModel, frozen=True):
    """What two deals share at the obligor, and what could not be resolved.

    ``proven_shared`` and ``unresolved`` **partition** every obligor group, and
    between them account for every asset in both input schedules. ``candidates``
    sits alongside rather than inside: it proposes links between groups already
    counted in ``unresolved``, and is never folded in.
    """

    deals: tuple[str, ...]
    proven_shared: tuple[ObligorGroup, ...] = ()
    candidates: tuple[ObligorCandidate, ...] = ()
    unresolved: tuple[UnresolvedObligor, ...] = ()

    @model_validator(mode="after")
    def _tiers_are_disjoint(self) -> CrossDealObligorResolution:
        declared = set(self.deals)
        seen: set[tuple[str, str]] = set()
        for group in self.all_groups:
            for member in group.members:
                if member.key in seen:
                    raise ValueError(
                        f"asset {member.key} appears in more than one obligor group"
                    )
                if member.deal not in declared:
                    raise ValueError(
                        f"asset {member.key} belongs to a deal this resolution does "
                        f"not declare: {sorted(declared)}"
                    )
                seen.add(member.key)

        for group in self.proven_shared:
            if len(group.deals) < 2:
                raise ValueError(
                    f"proven_shared holds {group.display_name!r}, which appears in one deal"
                )
        for entry in self.unresolved:
            if len(entry.group.deals) > 1:
                raise ValueError(
                    f"unresolved holds {entry.group.display_name!r}, which is proven "
                    "to span both deals"
                )
        return self

    @property
    def all_groups(self) -> tuple[ObligorGroup, ...]:
        """Every obligor group, proven-shared and unresolved alike."""
        return self.proven_shared + tuple(entry.group for entry in self.unresolved)

    @property
    def asset_count(self) -> int:
        """How many assets this resolution accounts for."""
        return sum(len(group.members) for group in self.all_groups)

    def distinct_obligor_bounds(self) -> ObligorBounds:
        """Distinct obligors across both deals, as a range rather than a point.

        ``upper`` is the number of groups — every candidate rejected. ``lower``
        accepts every candidate at once and counts the components that remain,
        so a chain of proposals collapses to one obligor rather than being
        double-counted.
        """
        upper = len(self.all_groups)
        parent: dict[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]] = {}

        def find(node: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
            parent.setdefault(node, node)
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def union(a: tuple[tuple[str, str], ...], b: tuple[tuple[str, str], ...]) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for group in self.all_groups:
            find(_group_id(group))
        for candidate in self.candidates:
            union(_group_id(candidate.left), _group_id(candidate.right))

        lower = len({find(node) for node in parent})
        return ObligorBounds(lower=lower, upper=upper)

    def shared_balance(self, deal: str) -> Decimal:
        """One deal's balance sitting in obligors *proven* shared with the other."""
        return sum(
            (
                member.principal_balance
                for group in self.proven_shared
                for member in group.members
                if member.deal == deal
            ),
            Decimal(0),
        )

    def unresolved_for(self, reason: UnresolvedReason) -> tuple[UnresolvedObligor, ...]:
        """Every unresolved obligor carrying ``reason``."""
        return tuple(entry for entry in self.unresolved if entry.reason is reason)


def _group_id(group: ObligorGroup) -> tuple[tuple[str, str], ...]:
    """A hashable identity for a group: its member keys, sorted."""
    return tuple(sorted(member.key for member in group.members))


def _joined(left: AssetRef, right: AssetRef) -> ResolutionEvidence | None:
    """Which proven rule, if any, joins two assets directly."""
    if left.key == right.key:
        return None
    if left.deal != right.deal and left.identifier == right.identifier:
        return ResolutionEvidence.shared_identifier
    if (
        left.deal == right.deal
        and name_is_usable(left.issuer_name)
        and name_is_usable(right.issuer_name)
        and within_deal_name(left.issuer_name) == within_deal_name(right.issuer_name)
    ):
        return ResolutionEvidence.same_deal_name
    return None


def _component_of(start: AssetRef, members: Sequence[AssetRef]) -> set[AssetRef]:
    """Every member reachable from ``start`` under the proven rules."""
    reached = {start}
    frontier = [start]
    while frontier:
        current = frontier.pop()
        for other in members:
            if other in reached:
                continue
            if _joined(current, other) is not None:
                reached.add(other)
                frontier.append(other)
    return reached


def _evidence_within(members: Sequence[AssetRef]) -> set[ResolutionEvidence]:
    """Every proven rule that actually joins some pair of ``members``."""
    found: set[ResolutionEvidence] = set()
    for i, left in enumerate(members):
        for right in members[i + 1 :]:
            evidence = _joined(left, right)
            if evidence is not None:
                found.add(evidence)
    return found


def _asset_refs(deal: str, assets: Iterable[CollateralAsset]) -> list[AssetRef]:
    """Project a deal's parsed assets onto the fields identity needs."""
    return [
        AssetRef(
            deal=deal,
            identifier=asset.identifier,
            issuer_name=asset.issuer_name or "",
            facility_name=asset.facility_name,
            principal_balance=asset.principal_balance,
        )
        for asset in assets
    ]


def _assert_every_asset_placed(
    inputs: Sequence[AssetRef], resolution: CrossDealObligorResolution
) -> None:
    """Refuse a resolution that lost — or invented — an asset.

    This is the guard that makes a name-keyed collapse **loud**. Grouping
    obligors by name is the obvious implementation and it silently drops every
    asset after the first sharing a name; both committed schedules repeat names
    across facilities, so the loss is real rather than theoretical. Dropping
    rows shrinks a *denominator* and therefore reads as health (#478), and no
    type catches it because a type is satisfied by a shorter list.

    It lives in the builder, not in a test, so that the check runs on every
    resolution ever constructed rather than only on the inputs a test happened
    to choose.
    """
    expected = [ref.key for ref in inputs]
    if len(set(expected)) != len(expected):
        duplicates = sorted({key for key in expected if expected.count(key) > 1})
        raise ValueError(f"input assets are not unique within their deal: {duplicates}")

    placed = [member.key for group in resolution.all_groups for member in group.members]
    missing = set(expected) - set(placed)
    invented = set(placed) - set(expected)
    if missing or invented:
        raise ValueError(
            f"resolution does not account for its inputs: {len(missing)} asset(s) lost "
            f"(e.g. {sorted(missing)[:3]}), {len(invented)} invented "
            f"(e.g. {sorted(invented)[:3]})"
        )
    if len(placed) != len(expected):
        raise ValueError(
            f"resolution places {len(placed)} assets against {len(expected)} inputs"
        )


def resolve_obligors(
    schedules: Mapping[str, CollateralSchedule],
) -> CrossDealObligorResolution:
    """Resolve obligor identity across two or more parsed collateral schedules.

    Pure and deterministic: no network, no LLM, no file access. ``schedules``
    maps a deal name to its parsed schedule; ordering of the result follows the
    sorted deal names so two runs agree.

    Raises ``ValueError`` if the result would not account for every input asset
    — see :func:`_assert_every_asset_placed`.
    """
    if len(schedules) < 2:
        raise ValueError(
            "obligor resolution compares deals; pass at least two parsed schedules"
        )

    deals = tuple(sorted(schedules))
    refs: list[AssetRef] = []
    for deal in deals:
        refs.extend(_asset_refs(deal, schedules[deal].assets))

    # Components under the proven rules only. Candidates are computed after,
    # from the groups, and never fed back in.
    remaining = list(refs)
    groups: list[ObligorGroup] = []
    while remaining:
        component = _component_of(remaining[0], remaining)
        members = tuple(sorted(component, key=lambda m: m.key))
        groups.append(
            ObligorGroup(members=members, evidence=tuple(sorted(_evidence_within(members))))
        )
        remaining = [ref for ref in remaining if ref not in component]

    proven_shared = tuple(
        group for group in sorted(groups, key=_group_id) if len(group.deals) > 1
    )
    single_deal = [group for group in sorted(groups, key=_group_id) if len(group.deals) == 1]

    # Candidates: single-deal groups from different deals folding to one name.
    by_fold: dict[str, list[ObligorGroup]] = {}
    for group in single_deal:
        if any(not name_is_usable(m.issuer_name) for m in group.members):
            continue
        folds = {candidate_fold(m.issuer_name) for m in group.members}
        if len(folds) != 1:
            continue
        fold = folds.pop()
        if fold:
            by_fold.setdefault(fold, []).append(group)

    candidates: list[ObligorCandidate] = []
    for fold in sorted(by_fold):
        peers = by_fold[fold]
        for i, left in enumerate(peers):
            for right in peers[i + 1 :]:
                if left.deals & right.deals:
                    continue
                candidates.append(ObligorCandidate(folded=fold, left=left, right=right))

    proposed = {_group_id(g) for c in candidates for g in (c.left, c.right)}
    unresolved = tuple(
        UnresolvedObligor(
            group=group,
            reason=(
                UnresolvedReason.name_unusable
                if any(not name_is_usable(m.issuer_name) for m in group.members)
                else UnresolvedReason.single_deal_only
            ),
            detail=(
                "issuer name is malformed upstream, so it cannot serve as a match key"
                if any(not name_is_usable(m.issuer_name) for m in group.members)
                else (
                    "a folded-name candidate was proposed; see candidates"
                    if _group_id(group) in proposed
                    else "seen in one deal only; no shared identifier and no name match"
                )
            ),
        )
        for group in single_deal
    )

    resolution = CrossDealObligorResolution(
        deals=deals,
        proven_shared=proven_shared,
        candidates=tuple(candidates),
        unresolved=unresolved,
    )
    _assert_every_asset_placed(refs, resolution)
    return resolution
