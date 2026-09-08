"""Tests for the shared step-source classifier (#266).

The classifier is the ONE place the engine slice decides, per waterfall step,
whether its amount is engine-computed, report-supplied, or a residual sweep — so
the live path and the validation harness cannot drift. These are focused unit
tests over the pure ``build_step_specs`` kernel with small hand-built step dicts
(no fixtures, no network). The Reconciler's to-the-cent reconciliation
(``test_reconciler.py``) is the integration guard that proves the in-tree
callers (``ReportAdapter`` / the live fold) still route correctly.
"""

from __future__ import annotations

import pytest

# `loanwhiz.primitives` must be imported before `loanwhiz.domain` — a
# pre-existing base-branch import cycle (domain/__init__ -> provenance ->
# primitives.base -> primitives/__init__ -> ... -> domain.rules) makes
# `loanwhiz.domain` unimportable as the first loanwhiz import. Unrelated to
# this change; the same skip is in `test_recipient_need_contract.py`.
from loanwhiz.primitives.step_source_classifier import (  # isort: skip
    _ENGINE_COMPUTED_CANONICAL,
    ENGINE_COMPUTED_RECIPIENTS,
    _canonical_view,
    build_step_specs,
)
from loanwhiz.domain.rules import (  # isort: skip
    CLO_RECIPIENT_SPELLINGS,
    RECOGNISED_UNEVALUABLE_RECIPIENTS,
    RecipientType,
)


def _step(priority: str, recipient: str, *, condition: str = "") -> dict:
    return {"priority": priority, "recipient": recipient, "condition": condition}


# ---------------------------------------------------------------------------
# engine branch — formulaic recipients the engine computes with no report input
# ---------------------------------------------------------------------------


def test_engine_computed_recipient_is_engine_with_no_override() -> None:
    steps = [_step("(d)", "class_a_interest")]
    specs, overrides, source = build_step_specs(
        steps,
        residual_label="(k)",
        report_supplied_labels=frozenset(),
        report_amounts={"(d)": 6_135_000.00},
    )
    assert source == {"class_a_interest": "engine"}
    # Engine-computed lines carry NO override — that is the independent proof.
    assert overrides == {}
    assert len(specs) == 1
    assert specs[0].priority == "(d)"
    assert specs[0].recipient == "class_a_interest"
    assert specs[0].residual is False


def test_all_registry_recipients_classify_as_engine() -> None:
    # Every recipient in the registry is engine-computed when not residual and
    # not under a report-supplied label.
    steps = [_step(f"(p{i})", r) for i, r in enumerate(sorted(ENGINE_COMPUTED_RECIPIENTS))]
    _, overrides, source = build_step_specs(
        steps,
        residual_label="(zzz)",
        report_supplied_labels=frozenset(),
        report_amounts={},
    )
    assert set(source.values()) == {"engine"}
    assert overrides == {}


# ---------------------------------------------------------------------------
# report-supplied branch — no prospectus formula; amount taken from the report
# ---------------------------------------------------------------------------


def test_non_registry_recipient_is_report_supplied_with_override() -> None:
    steps = [_step("(a)", "swap_payment")]
    _, overrides, source = build_step_specs(
        steps,
        residual_label="(k)",
        report_supplied_labels=frozenset(),
        report_amounts={"(a)": 1_234.56},
    )
    assert source == {"swap_payment": "report-supplied"}
    # The amount is pulled from the report by the step's label.
    assert overrides == {"swap_payment": 1_234.56}


def test_registry_recipient_under_report_supplied_label_is_report_supplied() -> None:
    # A label listed in report_supplied_labels forces report-supplied EVEN for an
    # otherwise-computable recipient (the report overrides the engine formula).
    steps = [_step("(a)", "class_a_interest")]
    _, overrides, source = build_step_specs(
        steps,
        residual_label="(k)",
        report_supplied_labels=frozenset({"(a)"}),
        report_amounts={"(a)": 9_999.00},
    )
    assert source == {"class_a_interest": "report-supplied"}
    assert overrides == {"class_a_interest": 9_999.00}


def test_report_supplied_override_defaults_to_zero_when_label_absent() -> None:
    steps = [_step("(g)", "issuer_expense_topup")]
    _, overrides, _ = build_step_specs(
        steps,
        residual_label="(k)",
        report_supplied_labels=frozenset(),
        report_amounts={},  # no amount for (g)
    )
    assert overrides == {"issuer_expense_topup": 0.0}


# ---------------------------------------------------------------------------
# residual branch — the terminal "whatever remains" sweep
# ---------------------------------------------------------------------------


def test_residual_label_classifies_as_residual_and_flags_spec() -> None:
    steps = [_step("(k)", "deferred_purchase_price")]
    specs, overrides, source = build_step_specs(
        steps,
        residual_label="(k)",
        report_supplied_labels=frozenset(),
        report_amounts={"(k)": 1_336_466.99},
    )
    assert source == {"deferred_purchase_price": "residual"}
    # A residual sweep takes no override — it distributes whatever is left.
    assert overrides == {}
    assert specs[0].residual is True


def test_residual_wins_over_engine_classification() -> None:
    # Even an engine-computable recipient is residual when it sits on the
    # residual label (residual is checked first).
    steps = [_step("(k)", "class_a_interest")]
    specs, overrides, source = build_step_specs(
        steps,
        residual_label="(k)",
        report_supplied_labels=frozenset(),
        report_amounts={},
    )
    assert source == {"class_a_interest": "residual"}
    assert overrides == {}
    assert specs[0].residual is True


# ---------------------------------------------------------------------------
# invariants — conditions cleared, empty residual label disables the flag
# ---------------------------------------------------------------------------


def test_extracted_conditions_are_cleared_on_built_specs() -> None:
    # The report is the post-resolution actual; the built spec must carry no
    # condition so the interpreter never re-suppresses a step the report paid.
    steps = [_step("(d)", "class_a_interest", condition="if cumulative_loss < 2%")]
    specs, _, _ = build_step_specs(
        steps,
        residual_label="(k)",
        report_supplied_labels=frozenset(),
        report_amounts={},
    )
    assert specs[0].condition is None


def test_empty_residual_label_disables_the_residual_flag() -> None:
    # The redemption case passes residual_label="" so NO real step (whose labels
    # are (a)/(b)/(c)/(d)) is a residual sweep — the report leaves a documented
    # unapplied-rounding remainder instead of sweeping the pot.
    steps = [
        _step("(a)", "class_a_principal"),
        _step("(b)", "class_b_principal"),
        _step("(c)", "class_c_principal"),
    ]
    specs, _, source = build_step_specs(
        steps,
        residual_label="",
        report_supplied_labels=frozenset({"(a)", "(b)", "(c)"}),
        report_amounts={"(a)": 43_486_010.58, "(b)": 0.0, "(c)": 0.0},
    )
    # No spec is flagged residual.
    assert all(s.residual is False for s in specs)
    # No recipient is classified residual.
    assert "residual" not in source.values()


# ---------------------------------------------------------------------------
# mixed waterfall — all three sources coexist (no fabricated 100%)
# ---------------------------------------------------------------------------


def test_mixed_waterfall_produces_all_three_sources() -> None:
    steps = [
        _step("(a)", "swap_payment"),  # report-supplied (non-registry)
        _step("(d)", "class_a_interest"),  # engine
        _step("(k)", "deferred_purchase_price"),  # residual
    ]
    specs, overrides, source = build_step_specs(
        steps,
        residual_label="(k)",
        report_supplied_labels=frozenset(),
        report_amounts={"(a)": 100.0, "(d)": 200.0, "(k)": 300.0},
    )
    assert source == {
        "swap_payment": "report-supplied",
        "class_a_interest": "engine",
        "deferred_purchase_price": "residual",
    }
    # Only the report-supplied line carries an override.
    assert overrides == {"swap_payment": 100.0}
    assert [s.priority for s in specs] == ["(a)", "(d)", "(k)"]


# ---------------------------------------------------------------------------
# the "one classifier" guarantee — the in-tree callers consume this module
# ---------------------------------------------------------------------------


def test_report_adapter_consumes_shared_classifier() -> None:
    """The ``ReportAdapter`` builds its per-waterfall maps off this same
    ``build_step_specs`` kernel, so the live path and the classifier cannot
    drift. (The old ``engine_validation_harness._build_specs`` alias that this
    test used to compare against was deleted when its proof folded into the
    Reconciler, #270 — the Reconciler's to-the-cent test is now the integration
    guard.)"""
    steps = [
        _step("(a)", "swap_payment"),
        _step("(d)", "class_a_interest"),
        _step("(k)", "deferred_purchase_price"),
    ]
    specs, overrides, source = build_step_specs(
        steps,
        residual_label="(k)",
        report_supplied_labels=frozenset({"(a)"}),
        report_amounts={"(a)": 100.0, "(d)": 200.0, "(k)": 300.0},
    )
    assert source == {
        "swap_payment": "report-supplied",
        "class_a_interest": "engine",
        "deferred_purchase_price": "residual",
    }
    assert overrides == {"swap_payment": 100.0}
    assert [s.priority for s in specs] == ["(a)", "(d)", "(k)"]


# ---------------------------------------------------------------------------
# one vocabulary — the recipient is canonicalised BEFORE it is classified (#511)
#
# An extracted step carries the *document's* spelling. Cairn CLO XVII's cascade
# says ``class_a_notes_interest`` where the enum says ``class_a_interest``, and
# testing the raw string sent every one of its steps to ``report-supplied``,
# where its need was overwritten with the report's own figure. That is the
# mechanism #496 measured: 52 steps "matching" a report they were copied from.
# ---------------------------------------------------------------------------


def test_clo_spelling_of_an_engine_recipient_classifies_as_engine() -> None:
    """The headline: a CLO-spelled note-interest step is engine-computed.

    Before #511 this was ``report-supplied`` with an override of 5_012_345.67 —
    the report handed back its own figure as the engine's "computed" need.
    """
    steps = [_step("(A)", "class_a_notes_interest")]
    _, overrides, source = build_step_specs(
        steps,
        residual_label="",
        report_supplied_labels=frozenset(),
        report_amounts={"(A)": 5_012_345.67},
    )
    assert source == {"class_a_notes_interest": "engine"}
    # No override: the engine must compute this one, not be told the answer.
    assert overrides == {}


@pytest.mark.parametrize(
    "spelling",
    sorted(
        s
        for s, r in CLO_RECIPIENT_SPELLINGS.items()
        if r.value in ENGINE_COMPUTED_RECIPIENTS
    ),
)
def test_every_clo_spelling_of_a_computable_recipient_is_engine(spelling: str) -> None:
    """Derived from the real spelling table, so it grows with the vocabulary."""
    _, overrides, source = build_step_specs(
        [_step("(A)", spelling)],
        residual_label="",
        report_supplied_labels=frozenset(),
        report_amounts={"(A)": 1.0},
    )
    assert source == {spelling: "engine"}
    assert overrides == {}


@pytest.mark.parametrize(
    "spelling",
    ["class_a_pdl_replenishment", "class_b_pdl_replenishment", "reserve_account_replenishment"],
)
def test_legacy_spellings_still_classify_as_engine(spelling: str) -> None:
    """The regression #511 had to avoid while fixing the CLO ones.

    ``ENGINE_COMPUTED_RECIPIENTS`` is itself a mixed vocabulary: these members
    are legacy spellings whose canonical forms (``class_*_pdl_cure``,
    ``reserve_replenishment``) do NOT appear in it. Canonicalising only the
    incoming recipient — and leaving the declaration unresolved — flips exactly
    these three from ``engine`` to ``report-supplied``.
    """
    _, overrides, source = build_step_specs(
        [_step("(e)", spelling)],
        residual_label="",
        report_supplied_labels=frozenset(),
        report_amounts={"(e)": 42.0},
    )
    assert source == {spelling: "engine"}
    assert overrides == {}


def test_report_supplied_label_still_wins_over_a_canonicalised_recipient() -> None:
    """The escape hatch survives canonicalisation.

    A step the engine *could* compute but shouldn't stays honest — and now that
    a CLO spelling reaches the engine branch at all, this is the only thing
    holding it back.
    """
    _, overrides, source = build_step_specs(
        [_step("(A)", "class_a_notes_interest")],
        residual_label="",
        report_supplied_labels=frozenset({"(A)"}),
        report_amounts={"(A)": 777.0},
    )
    assert source == {"class_a_notes_interest": "report-supplied"}
    assert overrides == {"class_a_notes_interest": 777.0}


def test_recognised_unevaluable_recipient_stays_report_supplied() -> None:
    """``unmapped`` must never leak into the engine branch.

    ``_canonical_recipient`` answers ``RecipientType.unmapped`` for every string
    in ``RECOGNISED_UNEVALUABLE_RECIPIENTS`` — the strings we recognise and have
    decided the engine cannot place. Were ``unmapped`` a member of the canonical
    set, all of them would classify ``engine`` at once.
    """
    unevaluable = "incentive_investment_management_fee"
    assert unevaluable in RECOGNISED_UNEVALUABLE_RECIPIENTS
    _, overrides, source = build_step_specs(
        [_step("(Y)", unevaluable)],
        residual_label="",
        report_supplied_labels=frozenset(),
        report_amounts={"(Y)": 55.0},
    )
    assert source == {unevaluable: "report-supplied"}
    assert overrides == {unevaluable: 55.0}
    assert RecipientType.unmapped not in _ENGINE_COMPUTED_CANONICAL


def test_canonical_view_drops_unplaceable_and_undeclared_names() -> None:
    """The derivation rule itself, on inputs the real declaration does not have.

    No member of ``ENGINE_COMPUTED_RECIPIENTS`` resolves to ``unmapped`` today, so
    asserting over the real set cannot tell the ``unmapped`` guard from its
    absence. Feed the rule a set that does: every string in
    ``RECOGNISED_UNEVALUABLE_RECIPIENTS`` collapses onto that one member, so
    admitting it even once would make all of them engine-computed.
    """
    unplaceable = "purchase_of_substitute_collateral"
    assert unplaceable in RECOGNISED_UNEVALUABLE_RECIPIENTS

    view = _canonical_view(
        frozenset({"class_a_interest", unplaceable, "no_such_recipient_anywhere"})
    )
    assert view == {RecipientType.class_a_interest}


def test_unspelled_recipient_stays_report_supplied() -> None:
    """A name nobody declared resolves to ``None`` and must not classify engine."""
    _, overrides, source = build_step_specs(
        [_step("(Z)", "amounts_referred_to_in_paragraph_qq")],
        residual_label="",
        report_supplied_labels=frozenset(),
        report_amounts={"(Z)": 12.0},
    )
    assert source == {"amounts_referred_to_in_paragraph_qq": "report-supplied"}
    assert overrides == {"amounts_referred_to_in_paragraph_qq": 12.0}


def test_engine_computed_recipients_carries_no_clo_spelling() -> None:
    """The shortcut #511 forbids, pinned.

    Hand-widening the declaration with CLO spellings would fix the same symptom
    while encoding a second vocabulary in a table that should only ever name
    recipients the registry can compute — the whack-a-mole #503 removed. The fix
    belongs at the comparison, not in the set.
    """
    assert ENGINE_COMPUTED_RECIPIENTS & set(CLO_RECIPIENT_SPELLINGS) == set()


def test_returned_dicts_stay_keyed_by_the_raw_extracted_recipient() -> None:
    """Only the membership test canonicalises; the keys do not.

    ``waterfall_interpreter`` looks a step's override up by ``spec.recipient``,
    which is the raw extracted string — re-keying these dicts to the canonical
    name would silently detach every override from its step.
    """
    steps = [_step("(A)", "class_a_notes_interest"), _step("(H)", "hedge_payments")]
    specs, overrides, source = build_step_specs(
        steps,
        residual_label="",
        report_supplied_labels=frozenset(),
        report_amounts={"(H)": 8.0},
    )
    assert set(source) == {"class_a_notes_interest", "hedge_payments"}
    assert set(overrides) == {"hedge_payments"}
    assert [s.recipient for s in specs] == ["class_a_notes_interest", "hedge_payments"]
