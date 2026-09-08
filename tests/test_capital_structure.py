"""The N-class capital structure and the contract it must keep (#478).

Four things are pinned here, and the first is the one that matters most:

1. **Refusal, not fallback.** A deal whose structure cannot be resolved fails
   loudly with the deal named, and never borrows Green Lion 2026-1's numbers.
   That reversal (#268) is the behaviour this change *preserves*; generalising
   the shape must not quietly re-open the fallback.
2. **The stack is placed in full or refused.** A truncated stack is the
   dangerous failure — the total is a denominator for the coverage metrics, so
   dropping a class reports subordination the deal does not have.
3. **Green Lion is byte-identical.** Its resolved config must not move.
4. **The next deep-stack deal needs no code change.** A twelve-class structure
   folds through the engine with nothing edited.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from fastapi import HTTPException

from loanwhiz.api import main as api_main
from loanwhiz.config import DEAL_REGISTRY
from loanwhiz.primitives.capital_structure import (
    CapitalStructure,
    UnresolvableCapitalStructure,
    engine_tranche_name,
    numeric_rate_pct,
)
from loanwhiz.primitives.deal_state import DealState, PeriodCollections
from loanwhiz.primitives.period_state_machine import (
    DEFAULT_REDEMPTION_STEPS,
    DEFAULT_REVENUE_STEPS,
    PeriodInput,
    reconstruct_period_series,
)
from loanwhiz.primitives.series_invariants import check_series

_CLO_DEAL_ID = "cairn-clo-xvii"
_GREEN_LION_DEAL_ID = "green-lion-2026-1"
_SEED_DIR = pathlib.Path(api_main.__file__).resolve().parents[1] / "data" / "deals" / "seed"


def _cairn_tranche_structure() -> list[dict]:
    """The CLO's committed extracted tranche stack — eight classes as filed."""
    seed = json.loads((_SEED_DIR / "cairn-clo-xvii-dac.json").read_text(encoding="utf-8"))
    return seed["tranche_structure"]


# ===========================================================================
# 1. The refusal is preserved — a deal never borrows another deal's numbers
# ===========================================================================


class TestRefusalNotFallback:
    """The contract #268 established, restated against the generalised shape."""

    def test_the_clo_refuses_and_names_itself_rather_than_using_green_lions_numbers(
        self,
    ) -> None:
        """Cairn resolves no complete config, so it must refuse — by name.

        This is the regression guard on the whole change. Generalising the shape
        made Cairn's *structure* resolvable; it must not have made Cairn
        resolvable by quietly reaching the Green Lion tier, which would publish a
        waterfall computed over a EUR 1.06bn Dutch RMBS under a EUR 404m Irish
        CLO's name.
        """
        with pytest.raises(HTTPException) as excinfo:
            api_main._resolve_structural_config(
                _CLO_DEAL_ID, dict(DEAL_REGISTRY[_CLO_DEAL_ID])
            )

        detail = excinfo.value.detail
        assert excinfo.value.status_code == 422
        assert _CLO_DEAL_ID in detail
        assert "Refusing to fall back to Green Lion 2026-1's numbers" in detail

    def test_no_green_lion_figure_ever_reaches_a_non_green_lion_deal(self) -> None:
        """Whatever a non-GL deal resolves, none of it is Green Lion's.

        Asserted over every registered deal rather than one, so a future tier
        added to the resolver is covered by construction.
        """
        green_lion_figures = {
            api_main._GREEN_LION_CLASS_A_BALANCE,
            api_main._GREEN_LION_CLASS_B_BALANCE,
            api_main._GREEN_LION_CLASS_C_BALANCE,
            api_main._GREEN_LION_RESERVE_TARGET,
            api_main._GREEN_LION_ORIGINAL_POOL_BALANCE,
        }
        for deal_id, ctx in DEAL_REGISTRY.items():
            if deal_id == _GREEN_LION_DEAL_ID:
                continue
            try:
                cap, reserve, pool = api_main._resolve_structural_config(
                    deal_id, dict(ctx)
                )
            except HTTPException:
                continue  # refused — the other half of the contract
            resolved = set(cap.values()) | {reserve, pool}
            assert not (resolved & green_lion_figures), deal_id

    def test_the_senior_coupon_is_refused_rather_than_defaulted_to_zero(self) -> None:
        """An unresolved coupon must refuse, never model an interest-free note.

        A ``0.0`` default here would be the silent-zero failure the constitution
        names: the revenue waterfall would compute no interest need for the
        senior class and report a deal comfortably meeting obligations it was
        never charged for.
        """
        structure = CapitalStructure.from_tranche_structure(
            _cairn_tranche_structure()
        ).to_engine_mapping()
        assert "class_a_rate_pct" not in structure

        with pytest.raises(HTTPException) as excinfo:
            api_main._with_senior_coupon(
                _CLO_DEAL_ID, structure, is_green_lion=False
            )
        assert "class_a_rate_pct" in excinfo.value.detail


# ===========================================================================
# 2. The stack is placed in full, or refused
# ===========================================================================


class TestTheStackIsPlacedInFull:
    def test_the_clo_resolves_all_eight_classes_and_ties_to_its_total(self) -> None:
        """Every class survives, and the balances still sum to what was filed.

        404,100,000 is the CLO's own total. The retired four-field shape could
        carry at most three of these eight; the positional mapper in the pool
        harness would have carried A / B-1 / B-2 and dropped the rest, reporting
        a stack 39% smaller than the deal.
        """
        structure = CapitalStructure.from_tranche_structure(_cairn_tranche_structure())

        assert structure.names == (
            "class_a",
            "class_b_1",
            "class_b_2",
            "class_c",
            "class_d",
            "class_e",
            "class_f",
            "subordinated_notes",
        )
        assert structure.total_balance == pytest.approx(404_100_000.0)
        assert len(structure.tranches) == 8

    def test_seniority_ordinals_need_not_be_contiguous(self) -> None:
        """0, 101, 102, 200 … orders exactly as 0, 1, 2, 3 would.

        The retired lookup asked for seniority ``0/1/2`` *by value*, so a stack
        numbered in hundreds resolved nothing at all. Order is what seniority
        means; the ordinals themselves are the document's own bookkeeping.
        """
        structure = CapitalStructure.from_tranche_structure(
            [
                {"name": "Junior", "size_eur": 1.0, "seniority": 2600},
                {"name": "Senior", "size_eur": 3.0, "seniority": 0},
                {"name": "Mezz", "size_eur": 2.0, "seniority": 101},
            ]
        )
        assert structure.names == ("senior", "mezz", "junior")
        assert structure.senior.name == "senior"

    def test_a_class_that_cannot_be_sized_refuses_the_whole_stack(self) -> None:
        """Refuse, never skip the row (#452).

        Skipping errs toward *health*: a smaller stack total is a smaller
        denominator, so the deal reports more subordination than it has and the
        defect never looks like one.
        """
        with pytest.raises(UnresolvableCapitalStructure) as excinfo:
            CapitalStructure.from_tranche_structure(
                [
                    {"name": "Class A", "size_eur": 100.0, "seniority": 0},
                    {"name": "Class B", "size_eur": None, "seniority": 1},
                ]
            )
        assert "Class B" in str(excinfo.value)

    def test_an_unnamed_class_refuses_the_whole_stack(self) -> None:
        with pytest.raises(UnresolvableCapitalStructure):
            CapitalStructure.from_tranche_structure(
                [{"name": "", "size_eur": 100.0, "seniority": 0}]
            )

    def test_two_classes_sharing_an_engine_name_refuse_rather_than_overwrite(
        self,
    ) -> None:
        """``"Class B-1"`` and ``"Class B 1"`` normalise alike — one would win.

        A dict-shaped structure cannot hold both, so the second would silently
        replace the first and the stack would come up one class short with its
        total quietly wrong.
        """
        with pytest.raises(UnresolvableCapitalStructure) as excinfo:
            CapitalStructure.from_tranche_structure(
                [
                    {"name": "Class B-1", "size_eur": 10.0, "seniority": 0},
                    {"name": "Class B 1", "size_eur": 20.0, "seniority": 1},
                ]
            )
        assert "class_b_1" in str(excinfo.value)

    def test_an_empty_stack_refuses(self) -> None:
        with pytest.raises(UnresolvableCapitalStructure):
            CapitalStructure.from_tranche_structure([])

    def test_a_reference_rate_is_never_coerced_into_a_coupon(self) -> None:
        """A margin is not a rate — and the trailing-% form still parses.

        The two retired parsers disagreed on the second case: the pool harness
        rejected ``"6.87%"``, so Cairn's one genuinely fixed-rate tranche parsed
        on the API path and not the harness path.
        """
        assert numeric_rate_pct("3 month EURIBOR + 1.80%") is None
        assert numeric_rate_pct("3m EURIBOR + 0.43") is None
        assert numeric_rate_pct("6.87%") == pytest.approx(6.87)
        assert numeric_rate_pct(4.10) == pytest.approx(4.10)
        assert numeric_rate_pct(None) is None
        assert numeric_rate_pct(True) is None

    def test_engine_names_match_the_spelling_the_engine_folds_on(self) -> None:
        assert engine_tranche_name("Class B-1") == "class_b_1"
        assert engine_tranche_name("Subordinated Notes") == "subordinated_notes"
        assert engine_tranche_name("Class A") == "class_a"

    def test_the_legacy_four_key_config_still_parses(self) -> None:
        """``deals.json``'s declared shape is a three-tranche instance, not a
        separate format — the two registered deals using it keep working."""
        structure = CapitalStructure.from_engine_mapping(
            {
                "class_a_balance": 12_036_900_000.0,
                "class_a_rate_pct": 1.07,
                "class_b_balance": 1_643_800_000.0,
                "class_c_balance": 375_800_000.0,
            }
        )
        assert structure.names == ("class_a", "class_b", "class_c")
        assert structure.senior.rate_pct == pytest.approx(1.07)
        assert structure.tranche("class_b").rate_pct is None


# ===========================================================================
# 3. Green Lion 2026-1 is unchanged
# ===========================================================================


class TestGreenLionIsUnchanged:
    def test_green_lions_resolved_config_is_exactly_its_constants(self) -> None:
        """Its output must not move by so much as a rounding.

        Green Lion now resolves its *balances* from its own extracted stack
        rather than wholesale from the constants, because the coupon is a
        separate tier. The values are the same either way — this is what asserts
        that rather than assuming it.
        """
        cap, reserve, pool = api_main._resolve_structural_config(
            _GREEN_LION_DEAL_ID, dict(DEAL_REGISTRY[_GREEN_LION_DEAL_ID])
        )
        assert cap == api_main._GREEN_LION_CAPITAL_STRUCTURE
        assert reserve == api_main._GREEN_LION_RESERVE_TARGET
        assert pool == api_main._GREEN_LION_ORIGINAL_POOL_BALANCE


# ===========================================================================
# 4. The next deep-stack deal needs no code change
# ===========================================================================

_TWELVE_CLASSES = [
    {"name": f"Class {letter}", "size_eur": size, "rate": rate, "seniority": seniority}
    for letter, size, rate, seniority in [
        ("A-1", 300_000_000.0, 3.50, 0),
        ("A-2", 100_000_000.0, 3.60, 1),
        ("B-1", 50_000_000.0, 4.00, 100),
        ("B-2", 25_000_000.0, 4.10, 101),
        ("C", 40_000_000.0, 4.75, 200),
        ("D", 30_000_000.0, 5.50, 300),
        ("E", 20_000_000.0, 7.00, 400),
        ("F", 15_000_000.0, 9.00, 500),
        ("G", 10_000_000.0, 11.00, 600),
        ("H", 8_000_000.0, 13.00, 700),
        ("J", 5_000_000.0, "3m EURIBOR + 8.00%", 800),
        ("Subordinated", 12_000_000.0, None, 2600),
    ]
]
_TWELVE_TOTAL = 615_000_000.0


class TestTheNextDeepStackDealNeedsNoCodeChange:
    """The generality test the issue asks for, stated as an assertion."""

    def test_a_twelve_class_stack_resolves_whole(self) -> None:
        structure = CapitalStructure.from_tranche_structure(_TWELVE_CLASSES)
        assert len(structure.tranches) == 12
        assert structure.total_balance == pytest.approx(_TWELVE_TOTAL)
        # The one reference-rate class contributes a balance and no coupon; the
        # eleven fixed-rate classes each contribute both.
        assert structure.tranche("class_j").rate_pct is None
        mapping = structure.to_engine_mapping()
        assert len([k for k in mapping if k.endswith("_rate_pct")]) == 10

    def test_a_twelve_class_stack_folds_through_the_engine(self) -> None:
        """Seeded, folded and chained with nothing in the engine edited."""
        capital_structure = CapitalStructure.from_tranche_structure(
            _TWELVE_CLASSES
        ).to_engine_mapping()

        opening = DealState.seed_from_prospectus(
            capital_structure,
            reserve_target=6_000_000.0,
            original_pool_balance=_TWELVE_TOTAL,
            reporting_date="2026-01-31",
        )
        assert len(opening.tranches) == 12
        assert sum(t.balance for t in opening.tranches) == pytest.approx(_TWELVE_TOTAL)

        series = reconstruct_period_series(
            capital_structure=capital_structure,
            reserve_target=6_000_000.0,
            original_pool_balance=_TWELVE_TOTAL,
            seed_reporting_date="2026-01-31",
            periods=[
                PeriodInput(
                    reporting_date="2026-02-28",
                    collections=PeriodCollections(
                        interest=6_000_000.0,
                        scheduled_principal=10_000_000.0,
                        prepayment=2_000_000.0,
                        recovery=50_000.0,
                        realized_loss=100_000.0,
                    ),
                )
            ],
        )
        # Every class is still carried at the end of the fold.
        assert len(series.states[-1].tranches) == 12

    def test_every_stated_coupon_reaches_the_waterfall(self) -> None:
        """The rate inputs cover all twelve classes, not the first three.

        The retired ``_DEFAULT_RATE_KEYS`` triple dropped the coupon of every
        class past the third, so a deeper deal's mezzanine notes accrued no
        interest need at all — a shortfall that never appears, reading as a deal
        comfortably servicing notes it was never charged for.
        """
        from loanwhiz.primitives.period_state_machine import _rate_inputs

        capital_structure = CapitalStructure.from_tranche_structure(
            _TWELVE_CLASSES
        ).to_engine_mapping()
        rates = _rate_inputs(capital_structure)

        assert len(rates) == 10
        assert rates["class_h_rate_pct"] == pytest.approx(13.00)
        assert "class_j_rate_pct" not in rates  # reference rate, honestly absent


# ===========================================================================
# 5. The collections leg refuses a stack it cannot represent
# ===========================================================================


class TestTheCollectionsLegRefusesRatherThanKeyErrors:
    """The tape reconstruction's collections input is still three-class.

    Generalising the *config* without generalising that leg leaves a gap: a
    deeper stack reaching it would raise ``KeyError('class_b_balance')`` — a 500
    naming nothing, from inside a network-backed path. It must refuse the same
    loud, deal-named way every other unmet structural precondition does.
    """

    def test_a_deeper_stack_refuses_and_names_the_deal_and_the_classes(self) -> None:
        structure = CapitalStructure.from_tranche_structure(
            _cairn_tranche_structure()
        ).to_engine_mapping()
        structure["class_a_rate_pct"] = 4.544  # as #480 will supply it

        with pytest.raises(HTTPException) as excinfo:
            api_main._collections_tranche_args(_CLO_DEAL_ID, structure)

        detail = excinfo.value.detail
        assert excinfo.value.status_code == 422
        assert _CLO_DEAL_ID in detail
        assert "class_b_1_balance" in detail  # the classes it actually has
        assert "subset of the deal's classes" in detail

    def test_a_three_class_stack_passes_through_unchanged(self) -> None:
        """Green Lion's shape must still reach the aggregator untouched."""
        args = api_main._collections_tranche_args(
            _GREEN_LION_DEAL_ID, dict(api_main._GREEN_LION_CAPITAL_STRUCTURE)
        )
        assert args == {
            "class_a_balance": api_main._GREEN_LION_CLASS_A_BALANCE,
            "class_a_rate_pct": api_main._GREEN_LION_CLASS_A_RATE_PCT,
            "class_b_balance": api_main._GREEN_LION_CLASS_B_BALANCE,
            "class_c_balance": api_main._GREEN_LION_CLASS_C_BALANCE,
        }

    def test_a_three_class_stack_missing_its_coupon_still_refuses(self) -> None:
        """The leg needs a rate; absent, it names the rate rather than KeyError."""
        with pytest.raises(HTTPException) as excinfo:
            api_main._collections_tranche_args(
                "sponsor-2025-1",
                {
                    "class_a_balance": 1.0,
                    "class_b_balance": 1.0,
                    "class_c_balance": 1.0,
                },
            )
        assert "class_a_rate_pct" in excinfo.value.detail


# ===========================================================================
# 6. The invariants see every class
# ===========================================================================


class TestInvariantsSeeEveryClass:
    """``series_invariants`` checked three fixed names, so a CLO passed vacuously."""

    def _deep_series(self):
        capital_structure = CapitalStructure.from_tranche_structure(
            _TWELVE_CLASSES
        ).to_engine_mapping()
        return reconstruct_period_series(
            capital_structure=capital_structure,
            reserve_target=6_000_000.0,
            original_pool_balance=_TWELVE_TOTAL,
            seed_reporting_date="2026-01-31",
            periods=[
                PeriodInput(
                    reporting_date="2026-02-28",
                    collections=PeriodCollections(
                        interest=6_000_000.0,
                        scheduled_principal=10_000_000.0,
                        prepayment=2_000_000.0,
                        recovery=50_000.0,
                        realized_loss=100_000.0,
                    ),
                )
            ],
        )

    def test_a_healthy_deep_stack_passes(self) -> None:
        report = check_series(
            self._deep_series(),
            revenue_steps=DEFAULT_REVENUE_STEPS,
            redemption_steps=DEFAULT_REDEMPTION_STEPS,
        )
        assert report.errors == []

    def test_a_negative_balance_on_a_non_canonical_class_is_flagged(self) -> None:
        """The `fires-when` input: a class the old fixed list never looked at.

        Before this, the check read ``class_a/b/c`` only, and the accessors
        answer ``0.0`` for a class a deal does not have — so a twelve-class
        deal's ``class_d`` could go arbitrarily negative and the non-negativity
        invariant stayed silent. A checker whose empty result means "nothing is
        wrong" has to be shown flagging something.
        """
        series = self._deep_series()
        corrupted = series.model_copy(deep=True)
        state = corrupted.states[-1]
        broken = [
            t.model_copy(update={"balance": -1_000.0}) if t.name == "class_d" else t
            for t in state.tranches
        ]
        corrupted.states[-1] = state.model_copy(update={"tranches": broken})

        report = check_series(
            corrupted,
            revenue_steps=DEFAULT_REVENUE_STEPS,
            redemption_steps=DEFAULT_REDEMPTION_STEPS,
        )
        negatives = [
            f for f in report.findings if f.invariant == "non_negative"
        ]
        assert any(f.recipient == "class_d_balance" for f in negatives), report.findings
