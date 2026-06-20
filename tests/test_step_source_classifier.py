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

from loanwhiz.primitives.step_source_classifier import (
    ENGINE_COMPUTED_RECIPIENTS,
    build_step_specs,
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
