"""Compliance & Covenants tab — the demo's differentiator.

This tab answers two structured-finance questions for the Green Lion 2026-1
B.V. deal, live, and then visualises the one that matters most over time:

1. **Did the servicer apply the waterfall correctly?** — a live
   :class:`ReportVerifier` run that builds a representative waterfall for the
   latest loaded period (``CollectionsAggregator`` → ``WaterfallRunner``) and
   compares the computed figures against the figures extracted from the April
   investor-report PDF. Rendered as a per-line-item match (🟢) / mismatch (🔴)
   table. ``ReportVerifier`` extracts figures via Gemini under the hood, so the
   handler degrades gracefully (a notice, never a crash) when Vertex is slow or
   unreachable.

2. **Are the deal's covenants in compliance?** — a live :class:`CovenantMonitor`
   run over all three loaded tapes using ``CovenantMonitor.DEFAULT_TRIGGERS``
   (sequential-pay / cumulative-loss, Class A/B PDL, reserve fund, clean-up
   call). Rendered as a per-period × per-trigger grid with 🟢 (compliant) / 🟡
   (near-miss, within 20 % of threshold) / 🔴 (triggered) proximity markers.
   ``CovenantMonitor`` is deterministic (no LLM) so it always runs live.

3. **The differentiator chart** — a :class:`gr.LinePlot` for the sequential-pay
   (cumulative-loss-rate) trigger overlaying (a) the three actual monthly loss
   points from the tapes, (b) the :class:`CashflowProjector` *base* forward
   path, (c) the *stress* forward path, and (d) the trigger threshold as a flat
   horizontal limit line. The compelling story is the stress path climbing
   toward the sequential-pay trigger while the actuals sit comfortably below it.

It follows the tab-plugin contract (``clients/demo/CONTRACT.md``): a single
``render(state)`` that builds UI inside the shell's open tab context and reads
the shared :class:`DealState` by wiring ``state`` as an *input* to its event
handlers. It never re-fetches tapes — it reads ``state.tapes`` (loaded once by
the shell) and only re-derives analytics in-process. It mirrors the established
live-primitive-from-a-tab pattern of ``waterfall.py`` (Part 1) and
``projection.py`` (Part 3).
"""

from __future__ import annotations

from typing import Any

import gradio as gr
import pandas as pd

from loanwhiz.config import GREEN_LION
from loanwhiz.primitives.cashflow_projector import (
    CashflowProjector,
    CashflowProjectorInput,
)
from loanwhiz.primitives.collections_aggregator import (
    CollectionsAggregator,
    CollectionsInput,
)
from loanwhiz.primitives.covenant_monitor import CovenantInput, CovenantMonitor
from loanwhiz.primitives.report_verifier import ReportVerifier, ReportVerifierInput
from loanwhiz.primitives.waterfall_runner import WaterfallInput, WaterfallRunner

# Green Lion 2026-1 capital structure (prospectus section 5; also the
# primitives' own defaults — restated here so the tab is explicit about the
# structure it runs).
_CLASS_A_BALANCE = 1_000_000_000.0
_CLASS_A_RATE_PCT = 3.62
_CLASS_B_BALANCE = 53_100_000.0
_CLASS_C_BALANCE = 10_500_000.0

# Reserve fund ≈ €10.6M; original (closing) pool ≈ €1.0636B. Used for the
# covenant monitor's reserve-fund and clean-up-call proximity computations and
# for converting the projector's cumulative-loss EUR into a loss *rate*.
_RESERVE_FUND_BALANCE = 10_600_000.0
_RESERVE_FUND_TARGET = 10_600_000.0
_ORIGINAL_POOL_BALANCE = 1_063_600_000.0
_FALLBACK_POOL_BALANCE = 1_063_600_000.0

# The sequential-pay trigger is the demo's headline covenant; its metric is the
# cumulative-loss rate (proxied by the tape's ``default_pct``). We surface its
# threshold both in the covenant grid and as the horizontal limit line on the
# differentiator chart. Looked up from the primitive's defaults so the tab and
# the primitive never disagree on the number.
_LOSS_TRIGGER_NAME = "cumulative_loss_trigger"


def _loss_trigger_threshold() -> float:
    """Return the sequential-pay (cumulative-loss) trigger threshold (percent).

    Read from :data:`CovenantMonitor.DEFAULT_TRIGGERS` so the chart's limit line
    and the covenant grid share one source of truth (1.5 % for Green Lion).
    """
    for trigger in CovenantMonitor.DEFAULT_TRIGGERS:
        if trigger.name == _LOSS_TRIGGER_NAME and trigger.threshold is not None:
            return float(trigger.threshold)
    return 1.5  # documented Green Lion default; defensive fallback.


# Human labels for the report-verifier line items and the covenant triggers,
# keyed by the snake-case identifiers the primitives emit.
_FIGURE_LABELS: dict[str, str] = {
    "class_a_interest_paid": "Class A interest paid",
    "class_a_principal_paid": "Class A principal paid",
    "reserve_fund_balance": "Reserve fund balance",
    "pool_balance": "Pool balance",
    "total_collections": "Total collections",
}

_TRIGGER_LABELS: dict[str, str] = {
    "cumulative_loss_trigger": "Sequential-pay (cumulative loss)",
    "pdl_class_a": "Class A PDL",
    "pdl_class_b": "Class B PDL",
    "reserve_fund_trigger": "Reserve fund",
    "clean_up_call": "Clean-up call",
}

_VERIFY_COLUMNS = ["", "Line item", "Reported", "Computed", "Δ", "Δ %"]

_CHART_COLUMNS = ["month", "value", "series"]

# Chart series labels (kept as constants so the test can assert them exactly).
_SERIES_ACTUAL = "Actual"
_SERIES_BASE = "Base (projected)"
_SERIES_STRESS = "Stress (projected)"
_SERIES_LIMIT = "Trigger threshold"


def _eur(value: float) -> str:
    """Format a EUR amount compactly (millions with two decimals)."""
    return f"€{value / 1_000_000:.2f}m"


def _tape_csv_url(period: str) -> str | None:
    """Map a loaded tape's ``period`` label back to its source CSV URL.

    The normalised tape dict carries a ``period`` label but not the source CSV
    URL, while :class:`CollectionsInput` reads the CSV directly. The tapes load
    from ``GREEN_LION["tape_urls"]`` in the same order, so the label (``date``)
    is the join key. Returns ``None`` when no URL matches.
    """
    for entry in GREEN_LION["tape_urls"]:
        if entry.get("date") == period:
            return entry.get("url")
    return None


def _april_report_url() -> str | None:
    """Return the April 2026 investor-report PDF URL, or ``None`` if absent."""
    for entry in GREEN_LION.get("investor_report_urls", []):
        if "april" in entry.get("period", "").lower():
            return entry.get("url")
    return None


def _current_pool_balance(tapes: list[dict] | None) -> float:
    """Return the latest tape's pool balance, or the deal's opening fallback."""
    if tapes:
        bal = tapes[-1].get("pool_balance_eur")
        if isinstance(bal, (int, float)) and bal > 0:
            return float(bal)
    return _FALLBACK_POOL_BALANCE


# ---------------------------------------------------------------------------
# Part 1 — report verification (live, button-triggered)
# ---------------------------------------------------------------------------


def _empty_verify_df() -> pd.DataFrame:
    """Return an empty, correctly-columned report-verification frame."""
    return pd.DataFrame(columns=_VERIFY_COLUMNS)


def _run_report_verification(state: Any) -> tuple[str, pd.DataFrame]:
    """Run the live waterfall → ReportVerifier chain for the latest period.

    Builds a representative waterfall for the latest loaded tape (the same
    ``CollectionsAggregator`` → ``WaterfallRunner`` chain ``waterfall.py`` uses),
    enriches the waterfall output dict with the pool / reserve balances the
    verifier needs, then runs :class:`ReportVerifier` against the April
    investor-report PDF. Returns ``(status_markdown, line_items_df)``. Any guard
    failure or verifier exception (Gemini slow / unreachable) degrades to a
    notice with an empty table — the demo never crashes.
    """
    empty = _empty_verify_df()

    if state is None or not getattr(state, "loaded", False) or not getattr(state, "tapes", None):
        return (
            "⚠️ Load a deal first (use **Load Green Lion 2026-1 Deal** at the top).",
            empty,
        )

    tapes = state.tapes
    latest = tapes[-1]
    period = latest.get("period", "latest period")

    csv_url = _tape_csv_url(period)
    if csv_url is None:
        return (
            f"❌ Could not resolve the source tape CSV for period **{period}**.",
            empty,
        )

    report_url = _april_report_url()
    if report_url is None:
        return ("❌ No April investor-report URL configured for this deal.", empty)

    prev_pool_balance = tapes[-2].get("pool_balance_eur") if len(tapes) >= 2 else None

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
                reserve_account_balance=_RESERVE_FUND_BALANCE,
                reserve_account_target=_RESERVE_FUND_TARGET,
                class_a_pdl_balance=0.0,
                class_b_pdl_balance=0.0,
            )
        ).output

        # Enrich the dump with the two figures the verifier cannot derive from
        # WaterfallOutput alone (pool / reserve balances) so all five line items
        # have a computed counterpart.
        waterfall_dump = waterfall.model_dump()
        waterfall_dump["pool_balance"] = collections.pool_balance_eur
        waterfall_dump["reserve_fund_balance"] = _RESERVE_FUND_BALANCE

        result = ReportVerifier().execute(
            ReportVerifierInput(
                investor_report_url=report_url,
                waterfall_output=waterfall_dump,
                reporting_period="April 2026",
            )
        )
        output = result.output
    except Exception as exc:  # noqa: BLE001 — degrade gracefully, never crash the demo.
        return (
            "⚠️ Report verification is unavailable right now "
            f"(the investor-report extraction did not complete): {exc}. "
            "The covenant monitor and trigger charts below run offline and are "
            "unaffected.",
            empty,
        )

    rows = [
        {
            "": "🟢" if fig.match else "🔴",
            "Line item": _FIGURE_LABELS.get(fig.line_item, fig.line_item),
            "Reported": _eur(fig.reported_value),
            "Computed": _eur(fig.computed_value),
            "Δ": _eur(fig.delta),
            "Δ %": f"{fig.delta_pct:+.2f}%",
        }
        for fig in output.line_items
    ]
    df = pd.DataFrame(rows, columns=_VERIFY_COLUMNS)

    verdict = "✅ all figures reconcile" if output.overall_match else "🔴 discrepancies found"
    status = (
        f"**April 2026** — {output.figures_matched}/{output.figures_checked} "
        f"computed-vs-reported figures match within tolerance ({verdict}). "
        f"{output.summary}"
    )
    return status, df


# ---------------------------------------------------------------------------
# Part 2 — covenant monitor (live, deterministic)
# ---------------------------------------------------------------------------


def _covenant_periods(tapes: list[dict]) -> list[dict]:
    """Adapt loaded tapes into the period dicts ``CovenantMonitor`` expects.

    The monitor labels each period by ``period["reporting_date"]`` and reads
    ``default_pct`` from the period's ``arrears_breakdown`` dict, so we inject a
    ``reporting_date`` key (from the tape's ``period`` label) and carry through
    ``arrears_breakdown`` / ``pool_balance_eur``. Tapes are already in
    chronological order, which the monitor's trend logic relies on.
    """
    periods: list[dict] = []
    for tape in tapes:
        periods.append(
            {
                "reporting_date": tape.get("period", "unknown"),
                "pool_balance_eur": tape.get("pool_balance_eur", 0.0),
                "arrears_breakdown": tape.get("arrears_breakdown", {}) or {},
            }
        )
    return periods


def _marker(status: Any) -> str:
    """Map a ``TriggerStatus`` to a 🟢 / 🟡 / 🔴 proximity marker.

    🔴 = triggered (breached); 🟡 = near-miss (within 20 % of an applicable
    threshold but not at/over it, i.e. ``80 <= proximity_pct < 100`` and not
    yet triggered — mirrors ``CovenantMonitor``'s own near-miss definition);
    🟢 = in compliance (including exactly at threshold, e.g. a reserve fund
    funded right to target).
    """
    if status.is_triggered:
        return "🔴"
    if status.threshold is not None and 80.0 <= status.proximity_pct < 100.0:
        return "🟡"
    return "🟢"


def _empty_covenant_df(tapes: list[dict] | None = None) -> pd.DataFrame:
    """Return an empty, correctly-columned covenant grid (one col per period)."""
    cols = ["Trigger"]
    if tapes:
        cols += [t.get("period", "period") for t in tapes]
    return pd.DataFrame(columns=cols)


def _run_covenant_monitor(state: Any) -> tuple[str, pd.DataFrame]:
    """Run the live :class:`CovenantMonitor` over all loaded tapes.

    Builds a per-trigger (row) × per-period (column) grid of 🟢/🟡/🔴 markers
    using ``CovenantMonitor.DEFAULT_TRIGGERS``. Deterministic and offline — no
    degradation path needed beyond the unloaded-state guard.
    """
    if state is None or not getattr(state, "loaded", False) or not getattr(state, "tapes", None):
        return (
            "⚠️ Load a deal first (use **Load Green Lion 2026-1 Deal** at the top).",
            _empty_covenant_df(),
        )

    tapes = state.tapes
    periods = _covenant_periods(tapes)

    try:
        output = CovenantMonitor().execute(
            CovenantInput(
                periods=periods,
                class_a_pdl_balance=0.0,
                class_b_pdl_balance=0.0,
                reserve_account_balance=_RESERVE_FUND_BALANCE,
                reserve_account_target=_RESERVE_FUND_TARGET,
                original_pool_balance=_ORIGINAL_POOL_BALANCE,
            )
        ).output
    except Exception as exc:  # noqa: BLE001 — surface, don't crash the demo.
        return (f"❌ Covenant monitor failed: {exc}", _empty_covenant_df(tapes))

    # Index the per-period statuses by (trigger_name, period) for the grid.
    period_labels = [p["reporting_date"] for p in periods]
    by_key = {(s.trigger_name, s.period): s for s in output.trigger_statuses}

    rows: list[dict] = []
    for trigger in CovenantMonitor.DEFAULT_TRIGGERS:
        row = {"Trigger": _TRIGGER_LABELS.get(trigger.name, trigger.name)}
        for tape, label in zip(tapes, period_labels):
            status = by_key.get((trigger.name, label))
            row[tape.get("period", label)] = _marker(status) if status else "—"
        rows.append(row)

    columns = ["Trigger"] + [t.get("period", "period") for t in tapes]
    df = pd.DataFrame(rows, columns=columns)

    if output.active_triggers:
        status = f"🔴 **Breached (latest period):** {', '.join(output.active_triggers)}. {output.summary}"
    elif output.near_miss_triggers:
        status = f"🟡 **Near-miss (latest period):** {', '.join(output.near_miss_triggers)}. {output.summary}"
    else:
        status = f"🟢 {output.summary}"
    return status, df


# ---------------------------------------------------------------------------
# Part 3 — the differentiator chart (cumulative-loss rate: actual + projected
# base/stress vs the sequential-pay trigger line)
# ---------------------------------------------------------------------------


def _actual_loss_rate(tape: dict) -> float:
    """Return a tape's realised loss-rate proxy (``default_pct``), in percent.

    Mirrors :class:`CovenantMonitor`'s ``cumulative_loss_trigger`` metric, which
    reads ``default_pct`` from the tape's ``arrears_breakdown``. Falls back to a
    top-level ``default_pct`` key, then 0.0.
    """
    arrears = tape.get("arrears_breakdown") or {}
    if "default_pct" in arrears:
        return float(arrears["default_pct"])
    if "default_pct" in tape:
        return float(tape["default_pct"])
    return 0.0


def _loss_trigger_chart_data(
    tapes: list[dict],
    projector_output: Any | None,
    *,
    pool_balance: float | None = None,
) -> pd.DataFrame:
    """Build the differentiator chart's long-format series.

    Produces one frame with the four overlay series keyed by ``series``:

    - :data:`_SERIES_ACTUAL` — the realised loss rate (``default_pct``) at each
      of the loaded monthly tapes, plotted at months ``0 .. n-1``.
    - :data:`_SERIES_BASE` / :data:`_SERIES_STRESS` — the forward cumulative
      loss rate from each :class:`CashflowProjector` scenario, converted from
      the projector's per-period ``cumulative_losses`` (EUR) into a percentage
      of the current pool balance so it shares the trigger's ``default_pct``
      units. The forward path begins at the last actual point (month ``n-1``)
      so the projected lines visually continue the actuals.
    - :data:`_SERIES_LIMIT` — the sequential-pay trigger threshold as a flat
      horizontal line spanning the full month axis.

    ``projector_output`` is a :class:`CashflowProjectorOutput` (or ``None`` to
    omit the projected/limit series — e.g. before a run). ``pool_balance``
    scales the EUR→% conversion; defaults to the latest tape's balance.

    Returns a frame with columns :data:`_CHART_COLUMNS`. The whole function is
    pure (no primitive calls) so it is asserted directly in the offline test.
    """
    rows: list[dict] = []

    n_actual = len(tapes)
    for i, tape in enumerate(tapes):
        rows.append({"month": i, "value": _actual_loss_rate(tape), "series": _SERIES_ACTUAL})

    threshold = _loss_trigger_threshold()

    if projector_output is None:
        return pd.DataFrame(rows, columns=_CHART_COLUMNS)

    pb = pool_balance if pool_balance and pool_balance > 0 else _current_pool_balance(tapes)
    last_actual_month = max(0, n_actual - 1)
    last_actual_value = _actual_loss_rate(tapes[-1]) if tapes else 0.0

    # Map scenario name → chart series label; only the two known scenarios are
    # drawn (extra scenarios, if any, are ignored to keep the overlay legible).
    series_for = {"base": _SERIES_BASE, "stress": _SERIES_STRESS}

    max_month = last_actual_month
    for sp in projector_output.scenario_projections:
        label = series_for.get(sp.scenario.name)
        if label is None:
            continue
        # Anchor the projected path at the last actual point so the lines join.
        rows.append({"month": last_actual_month, "value": last_actual_value, "series": label})
        for p in sp.periods:
            month = last_actual_month + p.period
            max_month = max(max_month, month)
            loss_rate_pct = (p.cumulative_losses / pb * 100.0) if pb > 0 else 0.0
            rows.append({"month": month, "value": loss_rate_pct, "series": label})

    # The trigger limit line spans the full month range (start to the furthest
    # projected month) as two endpoints — enough for gr.LinePlot to draw a flat
    # horizontal reference.
    rows.append({"month": 0, "value": threshold, "series": _SERIES_LIMIT})
    rows.append({"month": max_month, "value": threshold, "series": _SERIES_LIMIT})

    return pd.DataFrame(rows, columns=_CHART_COLUMNS)


def _run_loss_chart(state: Any) -> tuple[str, pd.DataFrame]:
    """Run :class:`CashflowProjector` live and build the differentiator chart.

    Returns ``(status_markdown, chart_df)``. On the unloaded-state guard or a
    projector exception, returns the actuals-only frame (or empty) with a notice
    so the chart still renders the three actual points.
    """
    if state is None or not getattr(state, "loaded", False) or not getattr(state, "tapes", None):
        return (
            "⚠️ Load a deal first (use **Load Green Lion 2026-1 Deal** at the top).",
            pd.DataFrame(columns=_CHART_COLUMNS),
        )

    tapes = state.tapes
    pool_balance = _current_pool_balance(tapes)

    try:
        output = CashflowProjector().execute(
            CashflowProjectorInput(
                current_pool_balance=pool_balance,
                current_class_a_balance=_CLASS_A_BALANCE,
                current_class_b_balance=_CLASS_B_BALANCE,
                current_class_c_balance=_CLASS_C_BALANCE,
                class_a_rate_pct=_CLASS_A_RATE_PCT,
                reserve_fund_balance=_RESERVE_FUND_BALANCE,
            )
        ).output
    except Exception as exc:  # noqa: BLE001 — degrade to actuals-only.
        df = _loss_trigger_chart_data(tapes, None, pool_balance=pool_balance)
        return (
            f"⚠️ Projection unavailable ({exc}); showing the {len(tapes)} actual "
            "loss points only.",
            df,
        )

    df = _loss_trigger_chart_data(tapes, output, pool_balance=pool_balance)
    threshold = _loss_trigger_threshold()
    n_months = (
        len(output.scenario_projections[0].periods)
        if output.scenario_projections
        else 0
    )
    status = (
        f"✅ Cumulative-loss rate vs the **sequential-pay trigger** ({threshold:.2f}%): "
        f"{len(tapes)} actual monthly point(s) plus the base and stress forward "
        f"paths over {n_months} projected months. {output.summary}"
    )
    return status, df


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------


def render(state: gr.State) -> None:
    """Populate the Compliance & Covenants tab. Called inside an open ``gr.Tab``.

    Builds three live sections — report verification, covenant monitor, and the
    cumulative-loss-rate trigger chart — each wired to a button that runs the
    relevant primitive(s) against the shared :class:`DealState`.
    """
    gr.Markdown(
        "### 🛡️ Compliance & Covenants\n"
        "Live verification and covenant monitoring for Green Lion 2026-1 "
        "(Class A €1.0B @ 3.62 %, Class B €53.1M, Class C €10.5M; reserve fund "
        "≈ €10.6M). Did the servicer apply the waterfall correctly? Are the "
        "covenant triggers in compliance — and where are losses heading versus "
        "the sequential-pay trigger?"
    )

    # --- Part 1: report verification -------------------------------------
    gr.Markdown(
        "#### 1 · Report verification — computed vs reported\n"
        "Builds the latest period's waterfall live (`CollectionsAggregator` → "
        "`WaterfallRunner`) and reconciles it against the April investor-report "
        "PDF via `ReportVerifier`. 🟢 = match within tolerance, 🔴 = discrepancy."
    )
    verify_btn = gr.Button("▶️ Verify April investor report", variant="primary")
    verify_status = gr.Markdown(
        "*Click **Verify** to reconcile the computed waterfall against the "
        "reported figures. (Uses Gemini extraction — may take a moment; degrades "
        "gracefully if Vertex is slow.)*"
    )
    verify_table = gr.Dataframe(
        value=_empty_verify_df(),
        headers=_VERIFY_COLUMNS,
        column_count=len(_VERIFY_COLUMNS),
        interactive=False,
        wrap=True,
        label="Computed vs reported — per line item",
    )

    # --- Part 2: covenant monitor ----------------------------------------
    gr.Markdown(
        "#### 2 · Covenant monitor — per-period trigger status\n"
        "Runs `CovenantMonitor` over all loaded tapes against the Green Lion "
        "default triggers. 🟢 = compliant · 🟡 = near-miss (within 20 % of "
        "threshold) · 🔴 = triggered."
    )
    covenant_btn = gr.Button("▶️ Check covenant triggers", variant="primary")
    covenant_status = gr.Markdown(
        "*Click **Check** to evaluate every trigger across every reporting "
        "period.*"
    )
    covenant_table = gr.Dataframe(
        value=_empty_covenant_df(),
        interactive=False,
        wrap=True,
        label="Trigger × period proximity grid",
    )

    # --- Part 3: the differentiator chart --------------------------------
    gr.Markdown(
        "#### 3 · Cumulative-loss rate vs the sequential-pay trigger\n"
        "Overlays the three **actual** monthly loss points with the "
        "`CashflowProjector` **base** and **stress** forward paths against the "
        "sequential-pay **trigger threshold** (horizontal line). The stress path "
        "climbing toward the line is the deal's headline early-warning signal."
    )
    chart_btn = gr.Button("▶️ Project loss rate vs trigger", variant="primary")
    chart_status = gr.Markdown(
        "*Click **Project** to overlay actual + base + stress loss-rate paths "
        "against the trigger threshold.*"
    )
    loss_chart = gr.LinePlot(
        value=pd.DataFrame(columns=_CHART_COLUMNS),
        x="month",
        y="value",
        color="series",
        x_title="Month (0 = first tape; forward = projected)",
        y_title="Cumulative loss rate (%)",
        title="Loss rate: actual + base/stress projection vs sequential-pay trigger",
    )

    verify_btn.click(
        _run_report_verification, inputs=state, outputs=[verify_status, verify_table]
    )
    covenant_btn.click(
        _run_covenant_monitor, inputs=state, outputs=[covenant_status, covenant_table]
    )
    chart_btn.click(_run_loss_chart, inputs=state, outputs=[chart_status, loss_chart])
