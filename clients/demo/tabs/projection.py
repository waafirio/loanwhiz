"""Cashflow Projection tab — live ``CashflowProjector`` base vs stress run.

This tab projects the Green Lion 2026-1 B.V. deal forward over a 12-month
horizon under two scenarios (a base case and a 2× default / +100bps stress
case) using the :class:`CashflowProjector` primitive, and visualises how the
Class A / B / C note balances amortise, the per-scenario Class A weighted-
average life (WAL), total distributions, and any reserve-fund breach period.

It follows the tab-plugin contract (``clients/demo/CONTRACT.md``): it exposes a
single ``render(state)`` that builds UI inside the shell's open tab context and
reads the shared :class:`DealState` by wiring ``state`` as an *input* to its
event handler. It never re-fetches tapes — it reads ``state.tapes`` (loaded once
by the shell) for the current pool balance and only runs analytics in-process.

The analytics chain, run live on a button click:

1. The current pool balance is read from the latest tape in ``state.tapes``.
2. :class:`CashflowProjector` is invoked with Green Lion's current capital
   structure (Class A €1.0B @ 3.62 %, Class B €53.1M, Class C €10.5M) and the
   reserve-fund balance, projecting its default base + stress scenarios over 12
   months.
3. The projected tranche balances are reconstructed period-by-period from each
   :class:`ScenarioProjection`'s ``periods`` (opening balances less the
   cumulative principal distributed), and rendered as a multi-series line chart
   (Class A / B / C, base vs stress). A summary table lists, per scenario per
   tranche, the WAL, total distributions, and the first reserve-breach period.
"""

from __future__ import annotations

from typing import Any

import gradio as gr
import pandas as pd

from loanwhiz.primitives.cashflow_projector import (
    CashflowProjector,
    CashflowProjectorInput,
)

# Green Lion 2026-1 capital structure (prospectus section 5; also the
# primitives' own defaults — restated here so the tab is explicit about the
# structure it projects).
_CLASS_A_BALANCE = 1_000_000_000.0
_CLASS_A_RATE_PCT = 3.62
_CLASS_B_BALANCE = 53_100_000.0
_CLASS_C_BALANCE = 10_500_000.0

# Reserve fund — Green Lion's reserve is ~€10.6M (see prospectus / deal model).
_RESERVE_FUND_BALANCE = 10_600_000.0

# Fallback current pool balance if no tape is available (the deal's ~€1.06B
# opening pool); the live handler always prefers the latest tape's balance.
_FALLBACK_POOL_BALANCE = 1_063_600_000.0

# Tranche labels and their opening balances, keyed by the snake-case class id.
_TRANCHE_OPENING: dict[str, float] = {
    "Class A": _CLASS_A_BALANCE,
    "Class B": _CLASS_B_BALANCE,
    "Class C": _CLASS_C_BALANCE,
}

_SUMMARY_COLUMNS = [
    "Scenario",
    "Tranche",
    "WAL (yr)",
    "Total distributions",
    "Breach period",
]

_BALANCE_COLUMNS = ["month", "balance_eur", "series"]


def _eur(value: float) -> str:
    """Format a EUR amount compactly (millions with two decimals)."""
    return f"€{value / 1_000_000:.2f}m"


def _current_pool_balance(tapes: list[dict] | None) -> float:
    """Return the latest tape's pool balance, or the deal's opening fallback."""
    if tapes:
        latest = tapes[-1]
        bal = latest.get("pool_balance_eur")
        if isinstance(bal, (int, float)) and bal > 0:
            return float(bal)
    return _FALLBACK_POOL_BALANCE


def _empty_balance_df() -> pd.DataFrame:
    """Return an empty, correctly-columned tranche-balance frame."""
    return pd.DataFrame(columns=_BALANCE_COLUMNS)


def _empty_summary_df() -> pd.DataFrame:
    """Return an empty, correctly-columned summary frame."""
    return pd.DataFrame(columns=_SUMMARY_COLUMNS)


def _breach_period(scenario_projection: Any) -> int | None:
    """Return the first period where cumulative losses exhaust the reserve.

    The reserve fund is the deal's first loss-absorbing buffer. A "breach" for
    the demo summary is the first period whose ``cumulative_losses`` exceed the
    available reserve fund — the point at which losses begin to erode the notes
    rather than being absorbed by the reserve. This is the signal that genuinely
    differs between the base and stress scenarios (the primitive itself clamps
    the per-period reserve balance to its internal target, so that field alone
    does not distinguish scenarios). Returns ``None`` when no period breaches.
    """
    for p in scenario_projection.periods:
        if p.cumulative_losses > _RESERVE_FUND_BALANCE:
            return p.period
    return None


def _balance_series(scenario_projection: Any, label: str) -> list[dict]:
    """Reconstruct projected per-month tranche balances for one scenario.

    The primitive's :class:`PeriodProjection` carries per-period *distributions*
    (interest + principal) rather than running balances, so the running note
    balances are reconstructed here by peeling the cumulative principal off each
    tranche's opening balance. We approximate per-period principal as the period
    distribution (interest is small relative to principal once amortisation
    begins, and the demo's purpose is the amortisation shape, base vs stress).

    Returns long-format rows ``{"month", "balance_eur", "series"}`` suitable for
    a multi-series :class:`gr.LinePlot` (``color="series"``). ``series`` is e.g.
    ``"Class A (base)"``.
    """
    rows: list[dict] = []
    balances = dict(_TRANCHE_OPENING)
    # Month 0 — opening balances (in € millions).
    for tranche, opening in _TRANCHE_OPENING.items():
        rows.append(
            {
                "month": 0,
                "balance_eur": opening / 1_000_000.0,
                "series": f"{tranche} ({label})",
            }
        )
    for p in scenario_projection.periods:
        dist_by_tranche = {
            "Class A": p.class_a_distribution,
            "Class B": p.class_b_distribution,
            "Class C": p.class_c_distribution,
        }
        for tranche in _TRANCHE_OPENING:
            balances[tranche] = max(0.0, balances[tranche] - dist_by_tranche[tranche])
            rows.append(
                {
                    "month": p.period,
                    "balance_eur": balances[tranche] / 1_000_000.0,
                    "series": f"{tranche} ({label})",
                }
            )
    return rows


def _summary_rows(scenario_projection: Any) -> list[dict]:
    """Build per-tranche summary rows for one scenario.

    WAL is the primitive's Class A WAL (months → years); Class B/C WAL is not
    computed by the primitive, so those rows show the total distribution and the
    shared breach period but leave WAL blank. Total distributions come from the
    scenario totals (Class A/B) and the summed Class C period distributions.
    """
    name = scenario_projection.scenario.name.title()
    breach = _breach_period(scenario_projection)
    breach_str = f"Month {breach}" if breach is not None else "—"

    wal_a_yr = scenario_projection.wal_class_a_months / 12.0
    total_c = sum(p.class_c_distribution for p in scenario_projection.periods)

    return [
        {
            "Scenario": name,
            "Tranche": "Class A",
            "WAL (yr)": f"{wal_a_yr:.2f}",
            "Total distributions": _eur(scenario_projection.total_class_a),
            "Breach period": breach_str,
        },
        {
            "Scenario": name,
            "Tranche": "Class B",
            "WAL (yr)": "—",
            "Total distributions": _eur(scenario_projection.total_class_b),
            "Breach period": breach_str,
        },
        {
            "Scenario": name,
            "Tranche": "Class C",
            "WAL (yr)": "—",
            "Total distributions": _eur(total_c),
            "Breach period": breach_str,
        },
    ]


def _run_projection(
    state: Any,
) -> tuple[str, pd.DataFrame, pd.DataFrame]:
    """Run the live cashflow projection for the current capital structure.

    Parameters
    ----------
    state:
        The shared :class:`DealState` (passed as the handler input value).

    Returns
    -------
    tuple
        ``(status_markdown, balances_df, summary_df)``. On any guard failure the
        dataframes are empty and the status explains why. ``balances_df`` is
        long-format (``month`` / ``balance_eur`` / ``series``) carrying every
        Class A/B/C series for both scenarios; ``summary_df`` has one row per
        scenario per tranche.
    """
    empty_balances = _empty_balance_df()
    empty_summary = _empty_summary_df()

    if state is None or not getattr(state, "loaded", False):
        return (
            "⚠️ Load a deal first (use **Load Green Lion 2026-1 Deal** at the top).",
            empty_balances,
            empty_summary,
        )

    pool_balance = _current_pool_balance(getattr(state, "tapes", None))

    try:
        result = CashflowProjector().execute(
            CashflowProjectorInput(
                current_pool_balance=pool_balance,
                current_class_a_balance=_CLASS_A_BALANCE,
                current_class_b_balance=_CLASS_B_BALANCE,
                current_class_c_balance=_CLASS_C_BALANCE,
                class_a_rate_pct=_CLASS_A_RATE_PCT,
                reserve_fund_balance=_RESERVE_FUND_BALANCE,
            )
        )
        output = result.output
    except Exception as exc:  # noqa: BLE001 — surface in the UI, don't crash the demo.
        return (
            f"❌ Cashflow projection failed: {exc}",
            empty_balances,
            empty_summary,
        )

    balance_rows: list[dict] = []
    summary_rows: list[dict] = []
    for sp in output.scenario_projections:
        balance_rows.extend(_balance_series(sp, sp.scenario.name))
        summary_rows.extend(_summary_rows(sp))

    balances_df = pd.DataFrame(balance_rows, columns=_BALANCE_COLUMNS)
    summary_df = pd.DataFrame(summary_rows, columns=_SUMMARY_COLUMNS)

    status = (
        f"✅ Projected **{len(output.scenario_projections)} scenario(s)** over "
        f"12 months from a current pool balance of {_eur(pool_balance)}. "
        f"{output.summary}"
    )
    return status, balances_df, summary_df


def render(state: gr.State) -> None:
    """Populate the Cashflow Projection tab. Called inside an open ``gr.Tab``.

    Builds a "Run projection" control, a status line, a multi-series line chart
    of projected Class A/B/C balances (base vs stress) over 12 months, and a
    summary table of per-scenario WAL, total distributions, and breach period.
    The button click runs the live :class:`CashflowProjector` against the Green
    Lion capital structure, reading the current pool balance from the latest
    loaded tape.
    """
    gr.Markdown(
        "### 📈 Cashflow Projection\n"
        "Projects the deal forward **12 months** under a **base** and a "
        "**stress** scenario (2× default rate, +100bps rates) live via "
        "`CashflowProjector`, on the Green Lion 2026-1 capital structure "
        "(Class A €1.0B @ 3.62 %, Class B €53.1M, Class C €10.5M; reserve fund "
        "≈ €10.6M). The current pool balance is read from the latest loaded "
        "tape."
    )

    run_btn = gr.Button("▶️ Run base + stress projection", variant="primary")
    status = gr.Markdown(
        "*Load a deal, then click **Run projection** to project tranche "
        "balances and WAL under base and stress scenarios.*"
    )

    gr.Markdown("#### Projected tranche balances (€m) — base vs stress")
    balances_plot = gr.LinePlot(
        value=_empty_balance_df(),
        x="month",
        y="balance_eur",
        color="series",
        x_title="Projection month",
        y_title="Note balance (€m)",
        title="Class A / B / C amortisation — base vs stress",
    )

    gr.Markdown("#### Scenario summary — WAL, total distributions, breach period")
    summary_table = gr.Dataframe(
        value=_empty_summary_df(),
        headers=_SUMMARY_COLUMNS,
        column_count=len(_SUMMARY_COLUMNS),
        interactive=False,
        wrap=True,
        label="WAL per scenario per tranche, total distributions, breach period",
    )

    run_btn.click(
        _run_projection,
        inputs=state,
        outputs=[status, balances_plot, summary_table],
    )
