"""WaterfallRunner must be able to express the deal's pro-rata principal state.

MODELING-GAPS B6: the registered ``waterfall_runner`` primitive and the agent /
API reconstruction share the same S4 interpreter engine, but diverged on the
*Sequential Pay Trigger* decision. The reconstruction injects a trigger-driven
evaluator (healthy deal → pro-rata / pari passu, so Class B receives principal),
while the standalone runner had no way to express that — ``WaterfallInput``
carried no ``sequential_pay`` flag, so it fell to ``DefaultConditionEvaluator``'s
conservative senior-protective default (sequential → Class A takes 100%).

This plumbs ``sequential_pay`` onto ``WaterfallInput`` so the registered
primitive can be driven to the same pari-passu allocation the platform uses —
without changing the default (None → sequential, unchanged).
"""

from __future__ import annotations

from loanwhiz.primitives.waterfall_runner import WaterfallInput, WaterfallRunner


def _base_input(**overrides) -> WaterfallInput:
    # 9M Class A + 1M Class B, both performing (PDL 0). 1M of principal collected.
    # Pro-rata by outstanding balance splits 90/10 → A 900k, B 100k. Sequential
    # pays A first → A 1,000k, B 0.
    kwargs = dict(
        reporting_period="2026-04-30",
        available_revenue_funds=500_000.0,
        available_principal_funds=1_000_000.0,
        senior_fees=0.0,
        swap_payment=0.0,
        class_a_balance=9_000_000.0,
        class_a_rate_pct=3.62,
        class_b_balance=1_000_000.0,
        class_c_balance=0.0,
        class_a_pdl_balance=0.0,
        class_b_pdl_balance=0.0,
        reserve_account_balance=0.0,
        reserve_account_target=0.0,
        days_in_period=30,
    )
    kwargs.update(overrides)
    return WaterfallInput(**kwargs)


def _principal(output, tranche: str) -> float:
    return next(t.principal_received for t in output.tranche_distributions if t.tranche == tranche)


def test_sequential_pay_false_splits_principal_pro_rata():
    """sequential_pay=False → pari passu: Class B receives its pro-rata share."""
    out = WaterfallRunner().execute(_base_input(sequential_pay=False)).output
    assert _principal(out, "class_a") == 900_000.0
    assert _principal(out, "class_b") == 100_000.0


def test_sequential_pay_default_stays_sequential():
    """Default (no flag) is unchanged: senior-protective sequential, A takes all."""
    out = WaterfallRunner().execute(_base_input()).output
    assert _principal(out, "class_a") == 1_000_000.0
    assert _principal(out, "class_b") == 0.0
