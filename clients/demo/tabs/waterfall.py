"""Waterfall tab — live ``CollectionsAggregator`` → ``WaterfallRunner``.

This tab visualises the Green Lion 2026-1 B.V. **Revenue Priority of Payments**
(the 11-step revenue cascade, steps (a)–(k)) and the per-tranche distributions
(Class A / B / C interest + principal) for the most recent loaded reporting
period.

It follows the tab-plugin contract (``clients/demo/CONTRACT.md``): it exposes a
single ``render(state)`` that builds UI inside the shell's open tab context and
reads the shared :class:`DealState` by wiring ``state`` as an *input* to its
event handler. It never re-fetches tapes — it reads ``state.tapes`` (loaded once
by the shell) and only re-derives analytics in-process.

The analytics chain, run live on a button click:

1. The latest tape in ``state.tapes`` is selected. Its ``period`` label is
   mapped back to the source CSV URL via ``GREEN_LION["tape_urls"]`` (the tapes
   are loaded in the same chronological order), because
   :class:`CollectionsInput` reads the tape CSV directly (``tape_file_url``) —
   the normalised tape dict does not carry that URL. ``prev_pool_balance`` is
   taken from the prior period's ``pool_balance_eur`` so scheduled principal is
   the reliable balance-delta path.
2. :class:`CollectionsAggregator` turns the tape into waterfall-ready
   Available Revenue Funds (ARF) / Available Principal Funds (APF), plus the
   Class A interest due and the senior-fees estimate.
3. :class:`WaterfallRunner` runs the Revenue and Redemption waterfalls against
   those funds using the Green Lion capital-structure defaults (Class A €1.0B
   @ 3.62 %, Class B €53.1M, Class C €10.5M).

The output is rendered as a step table (the 11 revenue-priority steps with
computed amounts), a per-tranche distribution table, and a bonus cascade bar
chart of the distributed amounts.
"""

from __future__ import annotations

from typing import Any

import gradio as gr
import pandas as pd

from loanwhiz.config import GREEN_LION
from loanwhiz.primitives.collections_aggregator import (
    CollectionsAggregator,
    CollectionsInput,
)
from loanwhiz.primitives.waterfall_runner import (
    WaterfallInput,
    WaterfallRunner,
)

# Green Lion 2026-1 capital structure (prospectus section 5; also the
# primitives' own defaults — restated here so the tab is explicit about the
# structure it runs).
_CLASS_A_BALANCE = 1_000_000_000.0
_CLASS_A_RATE_PCT = 3.62
_CLASS_B_BALANCE = 53_100_000.0
_CLASS_C_BALANCE = 10_500_000.0

# Human labels for the 11 revenue-priority recipients, keyed by the snake-case
# ``recipient`` the runner emits. Keeps the step table readable for a demo
# audience without re-deriving the prospectus text.
_RECIPIENT_LABELS: dict[str, str] = {
    "senior_fees": "Senior fees (Security Trustee)",
    "operating_fees": "Operating fees (Servicer, Admin, Paying Agent)",
    "swap_payment": "Swap payments (non-subordinated)",
    "class_a_interest": "Class A interest",
    "class_a_pdl_replenishment": "Class A PDL replenishment",
    "reserve_account_replenishment": "Reserve Account replenishment",
    "expense_account_replenishment": "Expense Account replenishment",
    "class_b_pdl_replenishment": "Class B PDL replenishment",
    "subordinated_swap_payment": "Subordinated swap payments",
    "class_c_principal_from_revenue": "Class C principal (from First Optional Redemption Date)",
    "deferred_purchase_price_seller": "Deferred Purchase Price to Seller",
}

_TRANCHE_LABELS: dict[str, str] = {
    "class_a": "Class A",
    "class_b": "Class B",
    "class_c": "Class C",
}


def _eur(value: float) -> str:
    """Format a EUR amount compactly (millions with two decimals)."""
    return f"€{value / 1_000_000:.2f}m"


def _tape_csv_url(period: str) -> str | None:
    """Map a loaded tape's ``period`` label back to its source CSV URL.

    The normalised tape dict carries a ``period`` label but not the source
    CSV URL, while :class:`CollectionsInput` reads the CSV directly. The tapes
    are loaded from ``GREEN_LION["tape_urls"]`` in the same order, so the label
    (``date``) is the join key. Returns ``None`` when no URL matches.
    """
    for entry in GREEN_LION["tape_urls"]:
        if entry.get("date") == period:
            return entry.get("url")
    return None


def _empty_dataframes() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return empty, correctly-columned step and tranche dataframes."""
    steps = pd.DataFrame(
        columns=["Step", "Recipient", "Available", "Distributed", "Shortfall"]
    )
    tranches = pd.DataFrame(
        columns=[
            "Tranche",
            "Interest",
            "Principal",
            "Total",
            "Opening balance",
            "Closing balance",
        ]
    )
    return steps, tranches


def _run_waterfall(state: Any) -> tuple[str, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run the live collections → waterfall chain for the latest tape.

    Parameters
    ----------
    state:
        The shared :class:`DealState` (passed as the handler input value).

    Returns
    -------
    tuple
        ``(status_markdown, revenue_steps_df, tranche_df, cascade_df)``. On any
        guard failure the dataframes are empty and the status explains why.
    """
    empty_steps, empty_tranches = _empty_dataframes()
    empty_cascade = pd.DataFrame(columns=["step", "amount_eur"])

    if state is None or not getattr(state, "loaded", False) or not getattr(state, "tapes", None):
        return (
            "⚠️ Load a deal first (use **Load Green Lion 2026-1 Deal** at the top).",
            empty_steps,
            empty_tranches,
            empty_cascade,
        )

    tapes = state.tapes
    latest = tapes[-1]
    period = latest.get("period", "latest period")

    csv_url = _tape_csv_url(period)
    if csv_url is None:
        return (
            f"❌ Could not resolve the source tape CSV for period **{period}** "
            f"(no matching entry in the Green Lion tape set).",
            empty_steps,
            empty_tranches,
            empty_cascade,
        )

    # prev_pool_balance from the prior period's tape (reliable balance-delta
    # path for scheduled principal). None when this is the first period.
    prev_pool_balance = None
    if len(tapes) >= 2:
        prev_pool_balance = tapes[-2].get("pool_balance_eur")

    try:
        collections = CollectionsAggregator().execute(
            CollectionsInput(
                tape_file_url=csv_url,
                reporting_period=period,
                prev_pool_balance=prev_pool_balance,
                class_a_rate_pct=_CLASS_A_RATE_PCT,
                class_a_balance=_CLASS_A_BALANCE,
                class_b_balance=_CLASS_B_BALANCE,
                class_c_balance=_CLASS_C_BALANCE,
            )
        ).output

        waterfall = WaterfallRunner().execute(
            WaterfallInput(
                reporting_period=period,
                available_revenue_funds=collections.available_revenue_funds,
                available_principal_funds=collections.available_principal_funds,
                senior_fees=collections.senior_fees,
                swap_payment=0.0,
                class_a_balance=_CLASS_A_BALANCE,
                class_a_rate_pct=_CLASS_A_RATE_PCT,
                class_b_balance=_CLASS_B_BALANCE,
                class_c_balance=_CLASS_C_BALANCE,
                reserve_account_balance=0.0,
                reserve_account_target=0.0,
                class_a_pdl_balance=0.0,
                class_b_pdl_balance=0.0,
            )
        ).output
    except Exception as exc:  # noqa: BLE001 — surface in the UI, don't crash the demo.
        return (
            f"❌ Waterfall run failed for **{period}**: {exc}",
            empty_steps,
            empty_tranches,
            empty_cascade,
        )

    # Step table — the 11 revenue-priority steps with computed amounts.
    step_rows = [
        {
            "Step": step.priority,
            "Recipient": _RECIPIENT_LABELS.get(step.recipient, step.recipient),
            "Available": _eur(step.amount_available),
            "Distributed": _eur(step.amount_distributed),
            "Shortfall": _eur(step.shortfall),
        }
        for step in waterfall.revenue_waterfall
    ]
    steps_df = pd.DataFrame(step_rows)

    # Per-tranche distribution table.
    tranche_rows = [
        {
            "Tranche": _TRANCHE_LABELS.get(t.tranche, t.tranche),
            "Interest": _eur(t.interest_received),
            "Principal": _eur(t.principal_received),
            "Total": _eur(t.total_received),
            "Opening balance": _eur(t.opening_balance),
            "Closing balance": _eur(t.closing_balance),
        }
        for t in waterfall.tranche_distributions
    ]
    tranche_df = pd.DataFrame(tranche_rows)

    # Bonus cascade chart — distributed amount per revenue step (in € millions).
    cascade_df = pd.DataFrame(
        {
            "step": [step.priority for step in waterfall.revenue_waterfall],
            "amount_eur": [
                step.amount_distributed / 1_000_000.0
                for step in waterfall.revenue_waterfall
            ],
        }
    )

    status = (
        f"✅ **{period}** — Available Revenue Funds "
        f"{_eur(collections.available_revenue_funds)}, "
        f"Available Principal Funds "
        f"{_eur(collections.available_principal_funds)}. "
        f"Total distributed {_eur(waterfall.total_distributed)}; "
        f"total shortfall {_eur(waterfall.shortfall)}."
    )
    return status, steps_df, tranche_df, cascade_df


def render(state: gr.State) -> None:
    """Populate the Waterfall tab. Called inside an open ``gr.Tab`` context.

    Builds a "Run waterfall" control, a status line, the 11-step revenue
    cascade table, the per-tranche distribution table, and a bonus cascade bar
    chart. The button click runs the live :class:`CollectionsAggregator` →
    :class:`WaterfallRunner` chain against the latest loaded tape.
    """
    gr.Markdown(
        "### 💧 Revenue Waterfall\n"
        "Runs the **Revenue Priority of Payments** (11 steps, (a)–(k)) and the "
        "per-tranche distributions live: latest loaded tape → "
        "`CollectionsAggregator` → `WaterfallRunner`, on the Green Lion 2026-1 "
        "capital structure (Class A €1.0B @ 3.62 %, Class B €53.1M, "
        "Class C €10.5M)."
    )

    run_btn = gr.Button("▶️ Run waterfall on latest tape", variant="primary")
    status = gr.Markdown(
        "*Load a deal, then click **Run waterfall** to compute the revenue "
        "cascade and tranche distributions.*"
    )

    empty_steps, empty_tranches = _empty_dataframes()

    gr.Markdown("#### Revenue Priority of Payments — 11 steps")
    steps_table = gr.Dataframe(
        value=empty_steps,
        headers=["Step", "Recipient", "Available", "Distributed", "Shortfall"],
        interactive=False,
        wrap=True,
        label="Revenue waterfall steps (a)–(k)",
    )

    gr.Markdown("#### Per-tranche distributions")
    tranche_table = gr.Dataframe(
        value=empty_tranches,
        headers=[
            "Tranche",
            "Interest",
            "Principal",
            "Total",
            "Opening balance",
            "Closing balance",
        ],
        interactive=False,
        wrap=True,
        label="Class A / B / C distributions",
    )

    gr.Markdown("#### Cascade — distributed per step (€m)")
    cascade_plot = gr.BarPlot(
        value=pd.DataFrame(columns=["step", "amount_eur"]),
        x="step",
        y="amount_eur",
        x_title="Revenue priority step",
        y_title="Distributed (€m)",
        title="Revenue cascade",
    )

    run_btn.click(
        _run_waterfall,
        inputs=state,
        outputs=[status, steps_table, tranche_table, cascade_plot],
    )
