"""The domain → engine seed bridge carries the deal's whole stack (#520).

``api.main._primitives_seed_from_report_seed`` maps the ``ReportAdapter``'s
canonical ``loanwhiz.domain.state.DealState`` onto the fold kernel's
``loanwhiz.primitives.deal_state.DealState``. It used to name the three classes
``class_{a,b,c}`` in six flat constructor kwargs, so an 8-class stack arrived as
three tranches — two of them zero-filled — however many classes the adapter had
resolved. That made it the **fourth** site of #478's truncation, and it outlived
the other three because #478 catalogued the defect by *dict key* while this
instance is spelled as constructor *arguments*.

Two things are pinned here, and they pull in opposite directions on purpose:

1. **Cairn's eight classes survive the bridge** — the fix.
2. **Green Lion is byte-identical** — the constraint. ``api/main.py`` feeds the
   Green Lion proofs, which carry the repo's only ``validated`` cells, so the
   regression guard is built by *reconstructing the old flat-kwarg construction
   in the test itself* and asserting the two states serialise identically. That
   is a real comparison against the previous behaviour rather than a restatement
   of the new one, and it keeps working without anyone remembering what the old
   code looked like.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from loanwhiz.api.main import _primitives_seed_from_report_seed
from loanwhiz.domain.state import DealState as DomainDealState
from loanwhiz.extraction.assembler import DealModel
from loanwhiz.primitives.deal_state import DealState as PrimitivesDealState
from loanwhiz.primitives.reconciler import (
    load_green_lion_2024_1_model,
    load_green_lion_2024_1_report,
)
from loanwhiz.primitives.report_adapter import ReportAdapter

_REPO_ROOT = Path(__file__).resolve().parents[1]
CAIRN_SEED = (
    _REPO_ROOT / "src" / "loanwhiz" / "data" / "deals" / "seed" / "cairn-clo-xvii-dac.json"
)


def _collapsed_the_old_way(seed: DomainDealState) -> PrimitivesDealState:
    """The pre-#520 bridge, rebuilt verbatim, as the comparison target.

    Kept in the test rather than in the source so the Green Lion guard compares
    against the behaviour that actually shipped, not against a paraphrase of it.
    A reader can diff this against the current implementation directly.
    """
    by_name = {t.name: t for t in seed.tranches}

    def _bal(name: str) -> float:
        t = by_name.get(name)
        return t.balance if t else 0.0

    def _pdl(name: str) -> float:
        t = by_name.get(name)
        return t.pdl_balance if t else 0.0

    return PrimitivesDealState(
        reporting_date=seed.reporting_date,
        class_a_balance=_bal("class_a"),
        class_b_balance=_bal("class_b"),
        class_c_balance=_bal("class_c"),
        class_a_pdl=_pdl("class_a"),
        class_b_pdl=_pdl("class_b"),
        class_c_pdl=_pdl("class_c"),
        reserve_balance=seed.reserve_balance,
        reserve_target=seed.reserve_target,
        cumulative_losses=seed.cumulative_losses,
        pool_balance=seed.pool_balance,
        original_pool_balance=seed.original_pool_balance,
    )


@pytest.fixture(scope="module")
def green_lion_seed() -> DomainDealState:
    """Green Lion 2024-1's period-0 domain seed, off the committed fixtures."""
    model = load_green_lion_2024_1_model()
    report = load_green_lion_2024_1_report()
    return ReportAdapter.from_deal_model(model).seed(report.periods[0])


def load_cairn_report():
    """Cairn's Note Valuation Report, through the helper that authored its key."""
    from tests.clo_answer_key_source import clo_note_valuation_report

    return clo_note_valuation_report()


@pytest.fixture(scope="module")
def cairn_seed() -> DomainDealState:
    """Cairn CLO XVII's period-0 domain seed — an 8-class stack."""
    model = DealModel.model_validate_json(CAIRN_SEED.read_text(encoding="utf-8"))
    return ReportAdapter.from_deal_model(model).seed(load_cairn_report().periods[0])


# ---------------------------------------------------------------------------
# 1. The constraint — Green Lion must not move at all.
# ---------------------------------------------------------------------------


def test_green_lion_bridges_byte_identically_to_the_old_collapse(
    green_lion_seed: DomainDealState,
) -> None:
    """The relayed state serialises exactly as the flat-kwarg collapse did.

    Green Lion states exactly ``class_a``/``class_b``/``class_c``, so relaying the
    stack and naming the three classes produce the same object — which is *why*
    generalising the bridge is safe, and the assertion that makes that safety
    checked rather than merely intended. ``model_dump()`` compares every field,
    so a change to the tranche list, a legacy accessor, or any other field reds.
    """
    relayed = _primitives_seed_from_report_seed(green_lion_seed)
    collapsed = _collapsed_the_old_way(green_lion_seed)

    assert relayed.model_dump() == collapsed.model_dump()


def test_green_lion_legacy_class_accessors_still_read_the_relayed_list(
    green_lion_seed: DomainDealState,
) -> None:
    """The ``class_{a,b,c}_balance`` accessors keep working over the relayed list.

    The flat fields are backward-compatible accessors over ``tranches`` (#363), so
    callers that never learned about the list are unaffected. Asserted against the
    domain seed's own values rather than transcribed figures, so a re-extraction
    cannot leave this passing on stale numbers.
    """
    relayed = _primitives_seed_from_report_seed(green_lion_seed)
    by_name = {t.name: t for t in green_lion_seed.tranches}

    assert [t.name for t in relayed.tranches] == ["class_a", "class_b", "class_c"]
    assert relayed.class_a_balance == by_name["class_a"].balance
    assert relayed.class_b_balance == by_name["class_b"].balance
    assert relayed.class_c_balance == by_name["class_c"].balance
    assert relayed.class_a_pdl == by_name["class_a"].pdl_balance


# ---------------------------------------------------------------------------
# 2. The fix — a deeper stack survives.
# ---------------------------------------------------------------------------


def test_cairns_eight_classes_survive_the_bridge(cairn_seed: DomainDealState) -> None:
    """All eight classes reach the engine state, in the deal's own order.

    The defect this file exists for: the adapter resolved eight and the bridge
    delivered three, with no error at either end. Names are compared against the
    domain seed the adapter actually produced, so the two layers are asserted to
    agree rather than each being checked against a transcribed list.
    """
    relayed = _primitives_seed_from_report_seed(cairn_seed)

    assert [t.name for t in relayed.tranches] == [t.name for t in cairn_seed.tranches]
    assert len(relayed.tranches) == 8
    assert {"class_b_1", "class_b_2", "class_d", "class_e", "class_f"} <= {
        t.name for t in relayed.tranches
    }


def test_the_old_collapse_would_have_dropped_six_cairn_classes(
    cairn_seed: DomainDealState,
) -> None:
    """The falsifier: the pre-#520 bridge really did truncate this deal.

    Without this, every assertion above could pass against a bridge that was never
    broken. Running the old construction on the same seed shows the truncation
    directly: eight classes in, three tranches out. Cairn has ``class_a`` and
    ``class_c`` but no plain ``class_b``, so that slot was *invented* at 0.0 while
    six real classes — including both Class B strips, which do carry balances —
    were dropped. Inventing one and dropping six is the shape of the #452 lesson:
    the stack reads as a smaller, healthier deal rather than as a bug.
    """
    collapsed = _collapsed_the_old_way(cairn_seed)

    assert [t.name for t in collapsed.tranches] == ["class_a", "class_b", "class_c"]
    # Cairn has no plain ``class_b`` — the old bridge invented the slot at zero
    # while dropping ``class_b_1`` and ``class_b_2``, which do carry balances.
    assert "class_b" not in {t.name for t in cairn_seed.tranches}
    assert collapsed.class_b_balance == 0.0
    dropped = {t.name for t in cairn_seed.tranches} - {t.name for t in collapsed.tranches}
    assert dropped == {
        "class_b_1",
        "class_b_2",
        "class_d",
        "class_e",
        "class_f",
        "subordinated_notes",
    }


# ---------------------------------------------------------------------------
# 3. The bridge names no class of its own.
# ---------------------------------------------------------------------------


def test_the_bridge_invents_no_tranche_for_a_stack_that_names_none(
    green_lion_seed: DomainDealState,
) -> None:
    """An empty stack relays as empty — no Green-Lion-shaped fallback here.

    The refusal direction (#452, #478) is decided once, upstream, in
    ``report_adapter.tranche_classes_from_model``. This pins that the bridge adds
    no second place for a triple to reappear: given no tranches it produces none,
    rather than three zero-filled ones. The legacy accessors then read 0.0, which
    is the honest answer for a stack that states nothing.
    """
    empty = green_lion_seed.model_copy(update={"tranches": []})

    relayed = _primitives_seed_from_report_seed(empty)

    assert relayed.tranches == []
    assert relayed.class_a_balance == 0.0


def test_a_stack_with_no_class_a_keeps_its_own_names(
    green_lion_seed: DomainDealState,
) -> None:
    """A deal whose senior class is not ``class_a`` is relayed under its own names.

    Leone Arancio's stack is ``class_a1``/``class_a2``/``class_j``: under the old
    bridge every one of those names missed the three it looked for, so the deal
    arrived as three zero-filled ``class_{a,b,c}`` tranches — a whole deal
    modelled as empty, silently. Built by substitution on a real seed so the test
    needs no second fixture.
    """
    leone = _REPO_ROOT / "src" / "loanwhiz" / "data" / "deals" / "seed" / (
        "leone-arancio-rmbs-2023-1-srl.json"
    )
    names = tuple(
        re.sub(r"[^a-z0-9]+", "_", t["name"].lower()).strip("_")
        for t in json.loads(leone.read_text(encoding="utf-8"))["tranche_structure"]
    )
    renamed = green_lion_seed.model_copy(
        update={
            "tranches": [
                t.model_copy(update={"name": n})
                for t, n in zip(green_lion_seed.tranches, names)
            ]
        }
    )

    relayed = _primitives_seed_from_report_seed(renamed)

    assert [t.name for t in relayed.tranches] == list(names)
    assert relayed.class_a_balance == 0.0, "no class_a in this stack — 0.0, not invented"
    assert sum(t.balance for t in relayed.tranches) > 0.0, "the balances still arrived"


# ---------------------------------------------------------------------------
# 4. The payoff — #512's published rates now have somewhere to land.
# ---------------------------------------------------------------------------


def test_the_published_rates_reach_the_classes_that_now_have_tranches(
    cairn_seed: DomainDealState,
) -> None:
    """Seven of Cairn's eight classes carry both a balance and a published rate.

    This is the property #520 exists to deliver, and the reason it is asserted
    here rather than inferred: #512's rate map was already complete and correct —
    the Note Valuation Report publishes an applied rate for seven classes — but it
    is keyed by tranche **name**, so before the stack survived the bridge only
    ``class_a`` and ``class_c`` had a tranche for a rate to attach to. Two of
    eight became seven of eight without the rate map changing at all.

    The eighth is the Subordinated Notes, which the report publishes no rate for.
    It is asserted as ``None`` rather than skipped: an unresolved coupon must stay
    absent so the interest need reports ``not_evaluable``, never accrue at zero and
    service the class for free (#471's "no key, not zero", #493's layered refusal).

    Read off the report and the seed rather than transcribed, so a re-extraction
    or a re-parse cannot leave this asserting a stale set.
    """
    from loanwhiz.api.main import _report_period_rates

    rates = _report_period_rates(load_cairn_report().periods[0])
    relayed = _primitives_seed_from_report_seed(cairn_seed)

    attached = {
        t.name: rates.get(f"{t.name}_rate_pct")
        for t in relayed.tranches
        if t.balance > 0.0
    }
    assert set(attached) == {
        "class_a",
        "class_b_1",
        "class_b_2",
        "class_c",
        "class_d",
        "class_e",
        "class_f",
    }
    assert all(rate is not None and rate > 0.0 for rate in attached.values())

    # The one class with no published rate keeps no key at all.
    (unrated,) = [t for t in relayed.tranches if t.balance == 0.0]
    assert unrated.name == "subordinated_notes"
    assert f"{unrated.name}_rate_pct" not in rates

    # The falsifier: under the old collapse only two of these had a tranche, so
    # five of the seven rates reached nothing. This is what actually changed.
    collapsed = {t.name for t in _collapsed_the_old_way(cairn_seed).tranches}
    assert {n for n in attached if n in collapsed} == {"class_a", "class_c"}


def test_the_relay_copies_rather_than_shares_tranche_objects(
    green_lion_seed: DomainDealState,
) -> None:
    """The engine state's tranches are independent of the adapter seed's.

    ``TrancheState`` is not frozen and pydantic does not re-validate a model
    instance passed into a typed field, so relaying the objects themselves would
    leave the two states sharing them — an aliasing hazard the old flat-kwarg
    construction did not have, because it always built fresh instances. Nothing in
    the engine mutates a tranche in place today, so this pins a property rather
    than fixing a live bug; it reds if the copy is dropped.
    """
    relayed = _primitives_seed_from_report_seed(green_lion_seed)

    assert [t.name for t in relayed.tranches] == [t.name for t in green_lion_seed.tranches]
    assert all(
        a is not b for a, b in zip(relayed.tranches, green_lion_seed.tranches)
    ), "the bridge must not hand the engine the adapter seed's own objects"

    relayed.tranches[0].balance = -1.0
    assert green_lion_seed.tranches[0].balance != -1.0
