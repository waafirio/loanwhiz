"""The forward-projection base resolves through tiers, or refuses (#479).

What this module pins
---------------------
``_resolve_projection_base`` used to read ``deal.get("projection_base")`` and
raise. Unlike ``capital_structure`` — which had ``_extracted_capital_structure``
beneath its ``deals.json`` key — this key had **no second tier at all**, so every
deal without a hand-written context key failed to project, not only the CLO.

Three things are asserted here, in the order they can fail:

1. **A base is complete or it does not exist.** Before this, a ``deals.json``
   entry carrying only ``current_pool_balance`` resolved *successfully* and then
   ``KeyError``-ed at the read site — an HTTP **500** out of a misconfigured
   deal, which is the one outcome the whole ``_resolve_*`` family exists to
   replace with a labelled 422.
2. **The derived tier reproduces the hand-written convention rather than
   inventing a second one.** Green Lion 2026-1's declared
   ``current_pool_balance`` is its newest tape's summed ``pool_balance_eur``, and
   its declared rate is its senior coupon. The derivation is checked against
   *that* declared value through the real seam, which is the only way to know it
   derives the same quantity an operator wrote by hand.
3. **Refusal, never fallback.** A deal that cannot resolve a base gets a 422
   naming what was missing — never another deal's pool or rate.

On mocks
--------
The Green Lion derivation deliberately runs through the **real**
``_normalised_tape_output`` (memo → runtime cache → committed seed → live), so it
exercises the seam a live projection would use rather than a stand-in. The
shaped-deal cases patch it, because their subject is the tier's branching and a
fake tape URL has nothing to resolve.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from loanwhiz.api import main as api_main
from loanwhiz.config import DEAL_REGISTRY

_GREEN_LION_DEAL_ID = "green-lion-2026-1"
_CLO_DEAL_ID = "cairn-clo-xvii"
_CONTEGO_DEAL_ID = "contego-clo-xi"

#: A stack whose senior class states a genuinely numeric coupon.
_RESOLVED_STRUCTURE = {
    "class_a_balance": 480_000_000.0,
    "class_a_rate_pct": 4.10,
    "class_b_balance": 15_000_000.0,
}

#: The same stack quoted as a reference rate — a balance, and no coupon.
_COUPONLESS_STRUCTURE = {
    "class_a_balance": 480_000_000.0,
    "class_b_balance": 15_000_000.0,
}


#: A deal that resolves its structural config and declares no base — the only
#: shape that reaches the Green Lion fallback guard. No registered deal has it
#: (see ``TestRefusalNotFallback``), so the registry-wide sweeps need it supplied.
_SELF_CONFIGURED_NO_BASE = {
    "capital_structure": _RESOLVED_STRUCTURE,
    "reserve_account_target": 5_000_000.0,
    "original_pool_balance": 500_000_000.0,
    "tape_urls": [],
}


def _deal(**extra) -> dict:
    deal = {
        "deal_name": "Sponsor Deal 2025-1 B.V.",
        "prospectus_url": "https://example.test/sponsor-prospectus.pdf",
        "tape_urls": [
            {"date": "2025-11-30", "url": "https://example.test/sponsor-202511.csv"},
            {"date": "2025-12-31", "url": "https://example.test/sponsor-202512.csv"},
        ],
        "investor_report_urls": [],
    }
    deal.update(extra)
    return deal


# ===========================================================================
# 1. A declared base is complete, or it is refused — never half-built
# ===========================================================================


class TestADeclaredBaseIsCompleteOrRefused:
    def test_a_complete_declaration_resolves_to_both_values(self) -> None:
        base = api_main.ProjectionBase.from_declared(
            "sponsor-2025-1",
            {"current_pool_balance": 500_000_000.0, "class_a_rate_pct": 4.10},
        )
        assert base.current_pool_balance == 500_000_000.0
        assert base.senior_rate_pct == 4.10

    @pytest.mark.parametrize(
        ("declared", "missing_key"),
        [
            ({"current_pool_balance": 5.0}, "class_a_rate_pct"),
            ({"class_a_rate_pct": 4.1}, "current_pool_balance"),
            ({}, "current_pool_balance"),
            ({"current_pool_balance": 5.0, "class_a_rate_pct": None}, "class_a_rate_pct"),
            ({"current_pool_balance": "lots", "class_a_rate_pct": 4.1}, "current_pool_balance"),
        ],
    )
    def test_a_partial_declaration_refuses_by_name(self, declared, missing_key) -> None:
        """The regression this type exists for: a 422, not a KeyError 500.

        Each of these used to resolve, because nothing checked the mapping was
        complete — the failure surfaced three lines later at ``base[...]`` as an
        unhandled ``KeyError``, i.e. a 500 telling the operator nothing about
        which deal or which key. The refusal must name both.
        """
        with pytest.raises(HTTPException) as excinfo:
            api_main.ProjectionBase.from_declared("sponsor-2025-1", declared)
        assert excinfo.value.status_code == 422
        assert "sponsor-2025-1" in excinfo.value.detail
        assert missing_key in excinfo.value.detail

    def test_a_non_mapping_declaration_refuses_rather_than_500ing(self) -> None:
        """``deals.json`` is operator-authored and only checked to be JSON.

        A list or a string where an object belongs must reach the labelled 422,
        not an ``AttributeError`` from inside the type.
        """
        for malformed in ([], "1033412063", 7.0):
            with pytest.raises(HTTPException) as excinfo:
                api_main.ProjectionBase.from_declared("sponsor-2025-1", malformed)
            assert excinfo.value.status_code == 422

    def test_unread_declared_keys_are_ignored(self) -> None:
        """``_GREEN_LION_PROJECTION_BASE`` carries five keys nothing reads.

        The tranche and reserve figures in it are read by no consumer — the base
        is consumed for a pool balance and a rate, and only those. Ignoring the
        rest is what lets the constant resolve unchanged through the type.
        """
        base = api_main.ProjectionBase.from_declared(
            _GREEN_LION_DEAL_ID, api_main._GREEN_LION_PROJECTION_BASE
        )
        assert base.current_pool_balance == 1_033_412_063.0
        assert base.senior_rate_pct == 3.62


# ===========================================================================
# 2. The derived tier — the one this key never had
# ===========================================================================


class TestTheDerivedTier:
    def test_a_deal_with_a_tape_and_a_coupon_needs_no_hand_written_key(self) -> None:
        """The point of the change: no ``deals.json`` entry, and it still resolves.

        Both halves come from what the system already knows — the senior coupon
        the structural resolver produced, and the latest tape's pool balance.
        """
        with patch.object(
            api_main, "_normalised_tape_output", return_value={"pool_balance_eur": 4.2e8}
        ):
            base = api_main._resolve_projection_base(
                "sponsor-2025-1", _deal(), _RESOLVED_STRUCTURE
            )
        assert base.current_pool_balance == 4.2e8
        assert base.senior_rate_pct == 4.10

    def test_the_balance_comes_from_the_LATEST_tape(self) -> None:
        """Chronologically newest, matching the forward starting point.

        An earlier tape's balance is a real number from the same deal, so this
        cannot fail loudly — it would simply project from a stale opening pool.
        """
        seen: list[str] = []

        def _spy(url: str) -> dict:
            seen.append(url)
            return {"pool_balance_eur": 4.2e8}

        with patch.object(api_main, "_normalised_tape_output", side_effect=_spy):
            api_main._resolve_projection_base(
                "sponsor-2025-1", _deal(), _RESOLVED_STRUCTURE
            )
        assert seen == ["https://example.test/sponsor-202512.csv"]

    @pytest.mark.parametrize(
        "output",
        [
            {"pool_balance_eur": 0.0},
            {"pool_balance_eur": -1.0},
            {"pool_balance_eur": None},
            {"pool_balance_eur": "1033412063.04"},
            {},
        ],
        ids=["zero", "negative", "absent-value", "non-numeric", "no-key"],
    )
    def test_a_tape_stating_no_usable_balance_refuses(self, output) -> None:
        """"No value here", never a base built from half the inputs.

        ``0.0`` is included on purpose: it is either a fully amortised pool or a
        normalisation that read no balances, indistinguishable from here and
        un-projectable either way. Seeding a projection at zero would be the
        silent-zero failure, not a degradation.
        """
        with patch.object(api_main, "_normalised_tape_output", return_value=output):
            with pytest.raises(HTTPException) as excinfo:
                api_main._resolve_projection_base(
                    "sponsor-2025-1", _deal(), _RESOLVED_STRUCTURE
                )
        assert excinfo.value.status_code == 422
        assert "sponsor-2025-1" in excinfo.value.detail

    def test_an_unreadable_tape_degrades_to_a_422_not_a_500(self) -> None:
        """A flaky fetch means "cannot project", never an endpoint crash."""
        with patch.object(
            api_main, "_normalised_tape_output", side_effect=OSError("network down")
        ):
            with pytest.raises(HTTPException) as excinfo:
                api_main._resolve_projection_base(
                    "sponsor-2025-1", _deal(), _RESOLVED_STRUCTURE
                )
        assert excinfo.value.status_code == 422

    def test_a_structure_without_a_senior_coupon_derives_nothing(self) -> None:
        """A margin is not a rate, so the base cannot be completed from one.

        The stack is fully known and the tape is readable; the deal still gets no
        base, because inventing the missing coupon is exactly what the refusal
        exists to prevent. In the live endpoints this state never reaches here —
        ``_resolve_structural_config`` refuses first, by the coupon's name — but
        the tier must not answer it either.
        """
        with patch.object(
            api_main, "_normalised_tape_output", return_value={"pool_balance_eur": 4.2e8}
        ):
            assert (
                api_main._derived_projection_base(_deal(), _COUPONLESS_STRUCTURE) is None
            )

    def test_the_refusal_names_the_tape_not_a_model_extraction(self) -> None:
        """The message the issue called misleading, corrected.

        "no extracted-model value is available" implied a path was tried and came
        up empty. No such path existed — and could not: the extracted deal model
        is a *prospectus* extraction and states no pool balance at any date. The
        refusal must point at the source that would actually help.
        """
        with pytest.raises(HTTPException) as excinfo:
            api_main._resolve_projection_base(
                "sponsor-2025-1", _deal(tape_urls=[]), _RESOLVED_STRUCTURE
            )
        detail = excinfo.value.detail
        assert "loan tape" in detail
        assert "extract its model" not in detail


# ===========================================================================
# 3. The derivation is the same quantity the hand-written keys hold
# ===========================================================================


class TestTheDerivationReproducesTheDeclaredValue:
    """Runs through the REAL ``_normalised_tape_output`` — the live seam.

    Everything else in this module shapes its own inputs, which proves branching
    and nothing about whether the derivation reads the right number off a real
    tape. Green Lion's committed tape analytics ship with the repo, so this one
    resolves offline through the same ladder a live projection uses.
    """

    def test_green_lions_derived_base_reproduces_its_declared_constant(self) -> None:
        derived = api_main._derived_projection_base(
            dict(DEAL_REGISTRY[_GREEN_LION_DEAL_ID]),
            api_main._GREEN_LION_CAPITAL_STRUCTURE,
        )
        assert derived is not None, "Green Lion's committed tape analytics should resolve"
        declared = api_main._GREEN_LION_PROJECTION_BASE
        # Within a cent: the hand-written constant is the tape total rounded.
        assert derived.current_pool_balance == pytest.approx(
            declared["current_pool_balance"], abs=0.05
        )
        assert derived.senior_rate_pct == declared["class_a_rate_pct"]

    def test_it_is_the_newest_tapes_balance_and_not_an_earlier_ones(self) -> None:
        """Guards the "latest" choice against a real, plausible alternative.

        Green Lion's three committed tapes each state a different pool balance,
        all of them genuine figures for this deal — so reading the wrong one is
        silent. Only the newest matches the declared forward starting point.
        """
        gl = dict(DEAL_REGISTRY[_GREEN_LION_DEAL_ID])
        derived = api_main._derived_projection_base(
            gl, api_main._GREEN_LION_CAPITAL_STRUCTURE
        )
        others = [
            api_main._normalised_tape_output(t["url"])["pool_balance_eur"]
            for t in gl["tape_urls"]
            if t["date"] != max(x["date"] for x in gl["tape_urls"])
        ]
        assert others, "expected Green Lion to register more than one tape"
        for balance in others:
            assert derived.current_pool_balance != balance


# ===========================================================================
# 4. Refusal, not fallback — the contract being preserved
# ===========================================================================


class TestRefusalNotFallback:
    def test_no_non_green_lion_deal_ever_resolves_a_green_lion_figure(self) -> None:
        """No non-GL deal resolves a Green Lion figure — registry, plus one shape.

        Swept over every registered deal so a future tier is covered by
        construction. **The registry alone makes this vacuous**, which is the
        #478 lesson landing on the test written to prevent it: every registered
        non-GL deal either refuses at the structural config (the two older Green
        Lions, the CLO — no resolved senior coupon) or declares its own
        ``projection_base`` (leone-arancio, sol-lion-ii), so **none of them ever
        reaches the Green Lion fallback guard**. Dropping that guard entirely
        left this sweep green.

        ``_SELF_CONFIGURED_NO_BASE`` is the missing shape — structural config it
        resolves, no declared base — and it is what actually exercises the
        guard. Keep it here rather than in its own test: the sweep is the
        registry-wide claim, and a claim no member can falsify is not one.
        """
        green_lion_figures = {
            api_main._GREEN_LION_PROJECTION_BASE["current_pool_balance"],
            api_main._GREEN_LION_PROJECTION_BASE["class_a_rate_pct"],
        }
        deals = {**DEAL_REGISTRY, "sponsor-2025-1": _deal(**_SELF_CONFIGURED_NO_BASE)}
        for deal_id, ctx in deals.items():
            if deal_id == _GREEN_LION_DEAL_ID:
                continue
            ctx = dict(ctx)
            try:
                capital_structure, _, _ = api_main._resolve_structural_config(deal_id, ctx)
                base = api_main._resolve_projection_base(deal_id, ctx, capital_structure)
            except HTTPException:
                continue  # refused — the other half of the contract
            resolved = {base.current_pool_balance, base.senior_rate_pct}
            assert not (resolved & green_lion_figures), deal_id

    def test_the_registered_resolve_refuse_set_is_unchanged(self) -> None:
        """Adding a tier moved no deal across the line.

        The same deals project and the same refuse as before #479 — the change
        is which key the refusal *names*, not who refuses. Registering a deal
        (#532 added Contego) joins the refusing side and never the resolving
        one: a registration states no capital structure, so the partition below
        is what stops a later change quietly promoting one.

        **Contego crossed the line in #614, deliberately and once.** It is named
        in ``resolved`` below rather than dropped from the assertion, because a
        deal moving sides is exactly the event this partition exists to make
        loud. What moved it was not a loosened resolver: its senior coupon comes
        from a committed synthetic index fixing and its par from the Target Par
        Amount its own Listing Particulars state, so it now satisfies the same
        tiering every other resolving deal does. The guard still holds for
        everyone else — a *second* registration appearing in ``resolved`` reds
        this line, which is the property worth keeping.
        """
        resolved, refused = set(), set()
        for deal_id, ctx in DEAL_REGISTRY.items():
            ctx = dict(ctx)
            try:
                capital_structure, _, _ = api_main._resolve_structural_config(deal_id, ctx)
                api_main._resolve_projection_base(deal_id, ctx, capital_structure)
                resolved.add(deal_id)
            except HTTPException:
                refused.add(deal_id)
        assert resolved == {
            "green-lion-2026-1",
            "leone-arancio-2023-1",
            "sol-lion-ii",
            _CONTEGO_DEAL_ID,  # #614 — synthetic coupon + read Target Par
        }
        assert refused == {
            "green-lion-2023-1",
            "green-lion-2024-1",
            _CLO_DEAL_ID,
        }

    def test_the_clo_refuses_by_its_coupon_rather_than_by_projection_base(self) -> None:
        """Cairn's ``/project`` refusal names the thing actually missing.

        It resolves eight classes and a pool it could read from its tapes; the one
        thing it does not state is a resolved senior coupon (its notes pay
        ``3 month EURIBOR + 1.80%``). Reporting "missing ``projection_base``" sent
        the reader to hand-write a base — five of whose six figures the system
        already had. It now names ``class_a_rate_pct``, the same key
        ``/waterfall`` reports for this deal, which is #481's to supply.
        """
        with pytest.raises(HTTPException) as excinfo:
            capital_structure, _, _ = api_main._resolve_structural_config(
                _CLO_DEAL_ID, dict(DEAL_REGISTRY[_CLO_DEAL_ID])
            )
            api_main._resolve_projection_base(
                _CLO_DEAL_ID, dict(DEAL_REGISTRY[_CLO_DEAL_ID]), capital_structure
            )
        detail = excinfo.value.detail
        assert "class_a_rate_pct" in detail
        assert "projection_base" not in detail
        assert _CLO_DEAL_ID in detail


# ===========================================================================
# 5. Green Lion is unchanged
# ===========================================================================


def test_green_lion_projects_from_its_constant_not_from_its_tape() -> None:
    """Its output must not move by so much as four cents.

    The derivation agrees with the declared constant to within a cent — which is
    the evidence the tier is honest, and also exactly why it must not replace it:
    serving 1_033_412_063.04 where the deal has always served 1_033_412_063.0
    would move a published number for no reason.
    """
    gl = dict(DEAL_REGISTRY[_GREEN_LION_DEAL_ID])
    base = api_main._resolve_projection_base(
        _GREEN_LION_DEAL_ID, gl, api_main._GREEN_LION_CAPITAL_STRUCTURE
    )
    assert base.current_pool_balance == 1_033_412_063.0
    derived = api_main._derived_projection_base(gl, api_main._GREEN_LION_CAPITAL_STRUCTURE)
    assert derived.current_pool_balance != base.current_pool_balance
