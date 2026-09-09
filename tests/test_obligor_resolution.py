"""Obligor identity across two deals: what is proven, proposed, and unresolved (#562).

The suite is built around one asymmetry. A join that is *missed* reports one
exposure as two and makes a book read as more diversified than it is; a join
that is *invented* pools two borrowers and overstates concentration. Both are
wrong, only the first looks like good news, and the tests below are arranged so
that neither can pass silently:

- the census tests prove nothing was dropped (understatement);
- the derived-not-asserted tests prove nothing was invented (overstatement);
- the refusal tests are paired both ways per #493 — supply the one missing
  input and assert the same record flips — so a refusal reached for the wrong
  reason cannot pass for the right one.

The two committed schedules are the fixture because a matcher for two naming
conventions cannot be validated on one: a vocabulary never fails to join with
itself (#481), so a synthetic stand-in would score 100% on the very join this
module exists to get right.
"""

from __future__ import annotations

import loanwhiz.primitives.base  # noqa: F401  (import-order guard)

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

import pytest
from pydantic import ValidationError

from loanwhiz.primitives.collateral_schedule_parser import (
    CollateralAsset,
    CollateralSchedule,
    parse_schedule_text,
)
from loanwhiz.primitives import obligor_resolution
from loanwhiz.primitives.obligor_resolution import (
    AssetRef,
    CrossDealObligorResolution,
    ObligorBounds,
    ObligorCandidate,
    ObligorGroup,
    ResolutionEvidence,
    UnresolvedReason,
    _assert_every_asset_placed,
    candidate_fold,
    name_is_usable,
    resolve_obligors,
    within_deal_name,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "collateral_schedule"

CAIRN = "cairn-clo-xvii"
CONTEGO = "contego-clo-xi"


@lru_cache(maxsize=None)
def _schedule(filename: str, period_label: str) -> CollateralSchedule:
    return parse_schedule_text(
        FIXTURE_DIR.joinpath(filename).read_text(), period_label=period_label
    )


@lru_cache(maxsize=None)
def _resolution() -> CrossDealObligorResolution:
    return resolve_obligors(
        {
            CAIRN: _schedule("cairn-clo-xvii-march-2025.txt", "2025-03"),
            CONTEGO: _schedule("contego-clo-xi-august-2024.txt", "2024-08"),
        }
    )


def _asset(identifier: str, issuer: str, facility: str = "Facility B", balance: str = "1000000.00") -> CollateralAsset:
    return CollateralAsset(
        identifier=identifier,
        issuer_name=issuer,
        facility_name=facility,
        principal_balance=Decimal(balance),
    )


def _sched(*assets: CollateralAsset) -> CollateralSchedule:
    return CollateralSchedule(period_label="synthetic", assets=list(assets))


def _ref(deal: str, identifier: str, issuer: str, balance: str = "1000000.00") -> AssetRef:
    return AssetRef(
        deal=deal,
        identifier=identifier,
        issuer_name=issuer,
        facility_name="Facility B",
        principal_balance=Decimal(balance),
    )


# ---------------------------------------------------------------------------
# Criterion 1 — every asset is accounted for, and a collapse is loud
# ---------------------------------------------------------------------------


def test_every_input_asset_lands_in_exactly_one_group() -> None:
    """The partition covers both schedules with nothing lost and nothing doubled."""
    resolution = _resolution()
    cairn = _schedule("cairn-clo-xvii-march-2025.txt", "2025-03")
    contego = _schedule("contego-clo-xi-august-2024.txt", "2024-08")

    expected = {(CAIRN, a.identifier) for a in cairn.assets} | {
        (CONTEGO, a.identifier) for a in contego.assets
    }
    placed = [m.key for g in resolution.all_groups for m in g.members]

    assert len(placed) == len(cairn.assets) + len(contego.assets)
    assert set(placed) == expected
    assert len(set(placed)) == len(placed), "an asset was placed in two groups"
    assert resolution.asset_count == len(expected)


def test_group_balance_sums_every_member_rather_than_picking_one() -> None:
    """Exposure to an obligor is the sum over its facilities, not one of them.

    The calculators downstream sum; a resolution that kept one member per
    obligor would tie out against nothing and simply report less exposure.
    """
    resolution = _resolution()
    multi = [g for g in resolution.all_groups if len(g.members) > 1]
    assert multi, "the fixtures carry obligors holding more than one facility"
    for group in multi:
        assert group.principal_balance == sum(
            (m.principal_balance for m in group.members), Decimal(0)
        )
        assert group.principal_balance > max(m.principal_balance for m in group.members)


def test_name_keyed_index_is_caught_by_the_builder_census() -> None:
    """fires-when: a deal repeating one name over two facilities → the guard raises.

    This is the #571/#478 defect in its natural habitat: indexing obligors by
    name is the obvious implementation, and it silently drops every asset after
    the first sharing a name. Understating balance per obligor makes a book
    read *less* concentrated, so it reads as health and no total contradicts it.
    """
    refs = [
        _ref(CAIRN, "LX000001", "Repeated Name Ltd", "5000000.00"),
        _ref(CAIRN, "LX000002", "Repeated Name Ltd", "3000000.00"),
        _ref(CONTEGO, "LX000003", "Other Ltd"),
    ]
    # Exactly what a name-keyed dict yields: the second Cairn row is gone.
    collapsed = CrossDealObligorResolution(
        deals=(CAIRN, CONTEGO),
        unresolved=(
            _unresolved(ObligorGroup(members=(refs[0],))),
            _unresolved(ObligorGroup(members=(refs[2],))),
        ),
    )
    with pytest.raises(ValueError, match="does not account for its inputs"):
        _assert_every_asset_placed(refs, collapsed)


def test_census_also_refuses_an_invented_asset() -> None:
    """The guard is two-sided: inventing a row is as wrong as dropping one."""
    refs = [_ref(CAIRN, "LX000001", "Only Ltd")]
    inflated = CrossDealObligorResolution(
        deals=(CAIRN, CONTEGO),
        unresolved=(
            _unresolved(ObligorGroup(members=(refs[0],))),
            _unresolved(ObligorGroup(members=(_ref(CONTEGO, "LX999999", "Ghost Ltd"),))),
        ),
    )
    with pytest.raises(ValueError, match="invented"):
        _assert_every_asset_placed(refs, inflated)


def test_the_builder_actually_runs_the_census(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard is wired into `resolve_obligors`, not merely defined beside it.

    Without this, deleting the `_assert_every_asset_placed(...)` call from the
    builder reds nothing: every other census test calls the guard directly, so
    they prove the function works while saying nothing about whether anything
    invokes it. A checker nobody calls and a checker that finds nothing produce
    the same silence, which is the failure mode the guard itself exists to
    prevent — so it is asserted here at the one seam that can tell them apart.

    The patch drops a group from the result the builder is about to return,
    standing in for any grouping bug that loses a row.
    """
    real = obligor_resolution.CrossDealObligorResolution

    def lossy(**kwargs: object) -> CrossDealObligorResolution:
        unresolved = kwargs.get("unresolved", ())
        assert isinstance(unresolved, tuple) and unresolved, "fixture must have unresolved"
        return real(**{**kwargs, "unresolved": unresolved[:-1]})  # type: ignore[arg-type]

    monkeypatch.setattr(obligor_resolution, "CrossDealObligorResolution", lossy)
    with pytest.raises(ValueError, match="does not account for its inputs"):
        resolve_obligors(
            {
                "a": _sched(_asset("LX1", "Alpha Ltd"), _asset("LX2", "Beta Ltd")),
                "b": _sched(_asset("LX3", "Gamma Ltd")),
            }
        )


def test_census_passes_on_an_intact_resolution() -> None:
    """The paired direction: the same guard is silent when nothing was lost."""
    refs = [
        _ref(CAIRN, "LX000001", "Repeated Name Ltd"),
        _ref(CAIRN, "LX000002", "Repeated Name Ltd"),
    ]
    intact = CrossDealObligorResolution(
        deals=(CAIRN, CONTEGO),
        unresolved=(
            _unresolved(
                ObligorGroup(
                    members=tuple(refs), evidence=(ResolutionEvidence.same_deal_name,)
                )
            ),
        ),
    )
    _assert_every_asset_placed(refs, intact)


def test_duplicate_input_identifiers_are_refused_rather_than_deduped() -> None:
    """A deal whose identifiers are not unique is a bad input, not a silent merge."""
    refs = [_ref(CAIRN, "LX000001", "A Ltd"), _ref(CAIRN, "LX000001", "A Ltd")]
    resolution = CrossDealObligorResolution(deals=(CAIRN,))
    with pytest.raises(ValueError, match="not unique within their deal"):
        _assert_every_asset_placed(refs, resolution)


def test_identifiers_are_unique_within_each_committed_deal_but_names_are_not() -> None:
    """The premise the identifier-keyed design rests on, asserted on real data."""
    for filename, label in (
        ("cairn-clo-xvii-march-2025.txt", "2025-03"),
        ("contego-clo-xi-august-2024.txt", "2024-08"),
    ):
        assets = _schedule(filename, label).assets
        identifiers = [a.identifier for a in assets]
        names = [within_deal_name(a.issuer_name or "") for a in assets]
        assert len(set(identifiers)) == len(identifiers)
        assert len(set(names)) < len(names), "a name-keyed index would lose rows here"


# ---------------------------------------------------------------------------
# Criterion 2 — every shared identifier resolves proven
# ---------------------------------------------------------------------------


def test_every_shared_identifier_resolves_to_one_proven_obligor() -> None:
    resolution = _resolution()
    cairn_ids = {a.identifier for a in _schedule("cairn-clo-xvii-march-2025.txt", "2025-03").assets}
    contego_ids = {
        a.identifier for a in _schedule("contego-clo-xi-august-2024.txt", "2024-08").assets
    }
    shared = cairn_ids & contego_ids
    assert shared, "the two committed deals do share identifiers"

    proven_ids = {m.identifier for g in resolution.proven_shared for m in g.members}
    assert shared <= proven_ids

    unresolved_ids = {m.identifier for u in resolution.unresolved for m in u.group.members}
    assert not (shared & unresolved_ids), "a proven identifier leaked into unresolved"


def test_isin_keyed_joins_are_proven_not_only_loanx_ones() -> None:
    """red-when: drop the ISIN branch from the identifier key → this reds alone."""
    resolution = _resolution()
    proven_ids = {m.identifier for g in resolution.proven_shared for m in g.members}
    isins = {i for i in proven_ids if i.startswith("XS")}
    loanx = {i for i in proven_ids if i.startswith("LX")}
    assert isins, "ISIN-identified assets are joined too, not just LX loan ids"
    assert loanx


def test_lx213528_joins_despite_two_unrecognisable_names() -> None:
    """The pair that settles identifier-first: name equality would never find it."""
    resolution = _resolution()
    group = _group_holding(resolution.proven_shared, "LX213528")
    names = {m.issuer_name for m in group.members}
    assert names == {"Alpha AB Bidco B.V.", "Ammega Group BV"}
    assert ResolutionEvidence.shared_identifier in group.evidence
    assert group.deals == frozenset({CAIRN, CONTEGO})
    # The two names share no folded form — only the identifier joins them.
    assert len({candidate_fold(n) for n in names}) == 2


def test_name_equality_alone_would_miss_most_proven_joins() -> None:
    """Why names are a candidate tier and never the proof.

    Asserted as a strict inequality rather than a transcribed count, so it
    re-derives at read time and cannot go stale against the fixtures.
    """
    resolution = _resolution()
    cross_deal = [g for g in resolution.proven_shared if len(g.deals) > 1]
    agreeing = [
        g
        for g in cross_deal
        if len({within_deal_name(m.issuer_name) for m in g.members}) == 1
    ]
    assert len(agreeing) * 2 < len(cross_deal), (
        "most proven cross-deal pairs disagree on issuer name; if this ever "
        "flips, name equality became viable and the tiering should be revisited"
    )


def test_a_proven_group_may_span_more_facilities_than_identifiers() -> None:
    """Two shared identifiers can belong to one obligor, so groups <= identifiers.

    Cheplapharm is held twice in each deal. Counting shared *identifiers* as
    shared *obligors* would double-count it — the overstatement direction.
    """
    resolution = _resolution()
    big = [g for g in resolution.proven_shared if len(g.members) > 2]
    assert big, "at least one obligor is held through several facilities in both deals"
    for group in big:
        assert ResolutionEvidence.same_deal_name in group.evidence
        assert ResolutionEvidence.shared_identifier in group.evidence
    assert len(resolution.proven_shared) <= len(
        {m.identifier for g in resolution.proven_shared for m in g.members}
    )


# ---------------------------------------------------------------------------
# Criterion 3 — candidates stay out of the proven set
# ---------------------------------------------------------------------------


def test_candidates_are_never_counted_as_proven() -> None:
    resolution = _resolution()
    assert resolution.candidates, "the fixtures do carry folded-name candidates"

    proven_keys = {m.key for g in resolution.proven_shared for m in g.members}
    for candidate in resolution.candidates:
        for group in (candidate.left, candidate.right):
            assert len(group.deals) == 1, "a candidate links two single-deal groups"
            for member in group.members:
                assert member.key not in proven_keys


def test_candidate_links_cross_deals_and_never_shares_an_identifier() -> None:
    resolution = _resolution()
    for candidate in resolution.candidates:
        assert not (candidate.left.deals & candidate.right.deals)
        left_ids = {m.identifier for m in candidate.left.members}
        right_ids = {m.identifier for m in candidate.right.members}
        assert not (left_ids & right_ids), "an identifier match is proven, not a candidate"


def test_candidate_refuses_a_fold_it_did_not_derive() -> None:
    """The overstatement mirror of the census, following JoinedLabel (#563).

    A candidate that could assert an arbitrary fold would be a way to claim two
    unrelated obligors matched — the exact inversion of the dropped-row defect
    the census guards.
    """
    left = ObligorGroup(members=(_ref(CAIRN, "LX000001", "Alpha Bidco BV"),))
    right = ObligorGroup(members=(_ref(CONTEGO, "LX000002", "Omega Holdings Ltd"),))
    with pytest.raises(ValidationError, match="not to the claimed"):
        ObligorCandidate(folded="ALPHA BIDCO", left=left, right=right)


def test_candidate_accepts_a_fold_both_sides_really_reach() -> None:
    """The paired direction (#493): the same constructor succeeds when true."""
    left = ObligorGroup(members=(_ref(CAIRN, "LX000001", "Nobian Finance B.V."),))
    right = ObligorGroup(members=(_ref(CONTEGO, "LX000002", "Nobian Finance BV"),))
    candidate = ObligorCandidate(folded="NOBIAN FINANCE", left=left, right=right)
    assert candidate.spellings_differ


def test_dotted_and_undotted_legal_forms_fold_together() -> None:
    """`B.V.` and `BV` are one company; folding them apart loses a real overlap.

    red-when: stop merging single-character runs in the fold → this reds alone.
    """
    assert candidate_fold("Nobian Finance B.V.") == candidate_fold("Nobian Finance BV")
    assert candidate_fold("Root Bidco S.A.R.L") == candidate_fold("Root Bidco Sarl")
    assert candidate_fold("Peer Holding III B.V.") == candidate_fold("Peer Holding III BV")


def test_the_fold_strips_only_the_tail_and_keeps_distinct_borrowers_apart() -> None:
    """Two Ineos entities are two obligors; an anywhere-strip would merge them."""
    assert candidate_fold("Ineos Finance PLC") != candidate_fold(
        "Ineos Quattro Holdings UK Limited"
    )
    assert candidate_fold("Bidco B") == "BIDCO B", "a lone facility letter is not a suffix"
    assert candidate_fold("Holdings Ltd") == "", "an all-suffix name folds to nothing"


# ---------------------------------------------------------------------------
# Criterion 4 — the count is bounds, never a point
# ---------------------------------------------------------------------------


def test_distinct_obligor_count_is_a_range_while_candidates_exist() -> None:
    resolution = _resolution()
    bounds = resolution.distinct_obligor_bounds()
    assert resolution.candidates
    assert bounds.lower < bounds.upper
    assert not bounds.is_exact
    assert bounds.upper == len(resolution.all_groups)
    assert bounds.lower == bounds.upper - len(resolution.candidates)


def test_no_accessor_returns_a_single_obligor_count() -> None:
    """A point estimate must assume every unresolved name is distinct.

    That assumption is the diversified-looking direction, so the type does not
    offer it: there is no attribute collapsing the range to one number.
    """
    bounds = _resolution().distinct_obligor_bounds()
    for banned in ("value", "count", "point", "estimate", "distinct_obligors"):
        assert not hasattr(bounds, banned)
    assert str(bounds) == f"{bounds.lower}-{bounds.upper}"


def test_bounds_coincide_only_when_nothing_was_proposed() -> None:
    exact = ObligorBounds(lower=7, upper=7)
    assert exact.is_exact
    assert str(exact) == "7"
    with pytest.raises(ValidationError, match="exceeds upper bound"):
        ObligorBounds(lower=9, upper=8)


def test_a_chain_of_candidates_collapses_once_not_twice() -> None:
    """Accepting every proposal counts components, so a chain is one obligor."""
    schedules = {
        "a": _sched(_asset("LX1", "Chained Finance B.V.")),
        "b": _sched(_asset("LX2", "Chained Finance BV")),
        "c": _sched(_asset("LX3", "Chained Finance Bv")),
    }
    resolution = resolve_obligors(schedules)
    bounds = resolution.distinct_obligor_bounds()
    assert len(resolution.candidates) == 3, "three pairwise proposals across three deals"
    assert bounds.upper == 3
    assert bounds.lower == 1, "the chain is one obligor, not three minus three"


# ---------------------------------------------------------------------------
# Criterion 5 — a malformed name is never a match key
# ---------------------------------------------------------------------------


def test_a_name_carrying_a_balance_is_not_usable() -> None:
    assert not name_is_usable("Ziggo Secured Finance B.V. 411,342,140.14")
    assert not name_is_usable("Subtotal: 323,513,954.72 Bond Allied Unvl Holdco LLC")
    assert not name_is_usable("")
    assert not name_is_usable(None)


def test_legitimate_digits_in_a_company_name_stay_usable() -> None:
    """The test is the thousands-separated money shape, not the presence of a digit.

    Rejecting any digit would discard real borrowers, which would push them into
    `unresolved` and overstate how much could not be resolved.
    """
    assert name_is_usable("Blitz 20-487 GmbH")
    assert name_is_usable("Platin2025 Holdings S.A R.L.")
    assert name_is_usable("Techem Verwaltungsgesellschaft 675 MBH")
    assert name_is_usable("Fortis 333, Inc")


def test_the_contaminated_row_is_named_unresolved_rather_than_dropped() -> None:
    resolution = _resolution()
    unusable = resolution.unresolved_for(UnresolvedReason.name_unusable)
    assert unusable, "the committed Contego schedule carries a subtotal-contaminated name"
    for entry in unusable:
        assert entry.detail
        assert any(not name_is_usable(m.issuer_name) for m in entry.group.members)
    # It is still counted — carried forward, never silently discarded.
    placed = {m.key for g in resolution.all_groups for m in g.members}
    for entry in unusable:
        for member in entry.group.members:
            assert member.key in placed


def test_an_unusable_name_never_produces_a_candidate() -> None:
    """Containment, not repair: a parsing defect must not manufacture a join."""
    schedules = {
        "a": _sched(_asset("LX1", "Subtotal: 1,234,567.89 Widget Holdings BV")),
        "b": _sched(_asset("LX2", "Subtotal: 9,876,543.21 Gadget Holdings BV")),
    }
    resolution = resolve_obligors(schedules)
    assert resolution.candidates == ()
    assert len(resolution.unresolved_for(UnresolvedReason.name_unusable)) == 2


def test_a_contaminated_name_still_resolves_when_the_identifier_matches() -> None:
    """Identifier-first rescues the row the name cannot: LX183461 in both deals."""
    resolution = _resolution()
    group = _group_holding(resolution.proven_shared, "LX183461")
    assert any(not name_is_usable(m.issuer_name) for m in group.members)
    assert group.deals == frozenset({CAIRN, CONTEGO})


# ---------------------------------------------------------------------------
# Criterion 6 — refusals asserted both ways (#493)
# ---------------------------------------------------------------------------


def test_unresolved_asset_flips_to_proven_when_the_identifier_matches() -> None:
    """Supply the one missing input, change nothing else, assert the flip.

    Without the pair, asserting only "this is unresolved" would keep passing
    with the identifier rule reverted, because everything would be unresolved.
    """
    apart = resolve_obligors(
        {
            "a": _sched(_asset("LX1", "Alpha Bidco B.V.")),
            "b": _sched(_asset("LX2", "Wholly Different Name Ltd")),
        }
    )
    assert apart.proven_shared == ()
    assert len(apart.unresolved) == 2
    assert apart.distinct_obligor_bounds().lower == 2

    together = resolve_obligors(
        {
            "a": _sched(_asset("LX1", "Alpha Bidco B.V.")),
            "b": _sched(_asset("LX1", "Wholly Different Name Ltd")),
        }
    )
    assert len(together.proven_shared) == 1
    assert together.unresolved == ()
    assert together.distinct_obligor_bounds() == ObligorBounds(lower=1, upper=1)


def test_unresolved_means_unknown_and_never_proven_distinct() -> None:
    """The two reasons both say "no evidence", which is not "different borrower"."""
    resolution = _resolution()
    reasons = {u.reason for u in resolution.unresolved}
    assert reasons <= {
        UnresolvedReason.single_deal_only,
        UnresolvedReason.name_unusable,
    }
    assert all(u.detail for u in resolution.unresolved)
    # Every unresolved obligor is in exactly one deal — that is what unknown means.
    for entry in resolution.unresolved:
        assert len(entry.group.deals) == 1


# ---------------------------------------------------------------------------
# The group record refuses an invented obligor
# ---------------------------------------------------------------------------


def test_group_refuses_members_no_rule_connects() -> None:
    with pytest.raises(ValidationError, match="not connected under the proven rules"):
        ObligorGroup(
            members=(
                _ref(CAIRN, "LX000001", "Alpha Ltd"),
                _ref(CONTEGO, "LX000002", "Omega Ltd"),
            ),
            evidence=(ResolutionEvidence.shared_identifier,),
        )


def test_group_refuses_evidence_that_is_not_what_joined_it() -> None:
    with pytest.raises(ValidationError, match="is not what joins these members"):
        ObligorGroup(
            members=(
                _ref(CAIRN, "LX000001", "Same Name Ltd"),
                _ref(CAIRN, "LX000002", "Same Name Ltd"),
            ),
            evidence=(ResolutionEvidence.shared_identifier,),
        )


def test_group_refuses_candidate_evidence_entirely() -> None:
    with pytest.raises(ValidationError, match="can never form a proven group"):
        ObligorGroup(
            members=(_ref(CAIRN, "LX000001", "Alpha Ltd"),),
            evidence=(ResolutionEvidence.folded_name,),
        )


def test_group_refuses_the_same_asset_twice() -> None:
    ref = _ref(CAIRN, "LX000001", "Alpha Ltd")
    with pytest.raises(ValidationError, match="the same asset twice"):
        ObligorGroup(members=(ref, ref), evidence=(ResolutionEvidence.same_deal_name,))


def test_group_accepts_the_connection_it_really_has() -> None:
    """Paired direction: the same two members, with the evidence that joins them."""
    group = ObligorGroup(
        members=(
            _ref(CAIRN, "LX000001", "Same Name Ltd"),
            _ref(CAIRN, "LX000002", "Same Name Ltd"),
        ),
        evidence=(ResolutionEvidence.same_deal_name,),
    )
    assert group.deals == frozenset({CAIRN})
    assert group.principal_balance == Decimal("2000000.00")


def test_resolution_refuses_a_single_deal_group_in_the_proven_tier() -> None:
    with pytest.raises(ValidationError, match="which appears in one deal"):
        CrossDealObligorResolution(
            deals=(CAIRN, CONTEGO),
            proven_shared=(ObligorGroup(members=(_ref(CAIRN, "LX1", "Alpha Ltd"),)),),
        )


def test_resolution_refuses_the_same_asset_in_two_tiers() -> None:
    ref = _ref(CAIRN, "LX1", "Alpha Ltd")
    pair = ObligorGroup(
        members=(ref, _ref(CONTEGO, "LX1", "Alpha Ltd")),
        evidence=(ResolutionEvidence.shared_identifier,),
    )
    with pytest.raises(ValidationError, match="more than one obligor group"):
        CrossDealObligorResolution(
            deals=(CAIRN, CONTEGO),
            proven_shared=(pair,),
            unresolved=(_unresolved(ObligorGroup(members=(ref,))),),
        )


def test_resolution_needs_two_deals_to_compare() -> None:
    with pytest.raises(ValueError, match="at least two parsed schedules"):
        resolve_obligors({"a": _sched(_asset("LX1", "Alpha Ltd"))})


def test_resolution_is_deterministic_across_runs_and_input_order() -> None:
    """Pure and order-independent: the same facts give the same answer."""
    cairn = _schedule("cairn-clo-xvii-march-2025.txt", "2025-03")
    contego = _schedule("contego-clo-xi-august-2024.txt", "2024-08")
    one = resolve_obligors({CAIRN: cairn, CONTEGO: contego})
    two = resolve_obligors({CONTEGO: contego, CAIRN: cairn})
    assert one == two
    assert one.distinct_obligor_bounds() == two.distinct_obligor_bounds()


def test_shared_balance_is_reported_per_deal_not_pooled() -> None:
    """Each deal's own exposure to the shared obligors, since the pools differ."""
    resolution = _resolution()
    cairn_shared = resolution.shared_balance(CAIRN)
    contego_shared = resolution.shared_balance(CONTEGO)
    assert cairn_shared > 0
    assert contego_shared > 0
    assert cairn_shared != contego_shared, "two deals hold different amounts of one obligor"
    total = sum(g.principal_balance for g in resolution.proven_shared)
    assert cairn_shared + contego_shared == total


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _unresolved(group: ObligorGroup):
    from loanwhiz.primitives.obligor_resolution import UnresolvedObligor

    return UnresolvedObligor(
        group=group, reason=UnresolvedReason.single_deal_only, detail="synthetic"
    )


def _group_holding(groups: tuple[ObligorGroup, ...], identifier: str) -> ObligorGroup:
    matches = [g for g in groups if any(m.identifier == identifier for m in g.members)]
    assert len(matches) == 1, f"{identifier} should sit in exactly one group"
    return matches[0]
