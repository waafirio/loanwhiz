"""Pool & Performance tab for the unified LoanWhiz demo app (issue #79).

This tab absorbs the standalone dashboard's pool analytics into the unified
demo shell. It reads the three normalised ESMA tapes already loaded into the
shared :class:`~clients.demo.shell.DealState` (it never re-fetches them — see
``clients/demo/CONTRACT.md``) and renders period-over-period pool analytics:

- a **pool-trend table** — balance, loan count, arrears %, default %, and
  balance-weighted LTV across the three reporting periods;
- the **EPC distribution** for the latest period (energy-performance mix); and
- **geographic** and **property-type** breakdowns for the latest period,
  shown as bar plots when present.

The tape dict shape is identical to the one the standalone dashboard
(``clients/dashboard/app.py``) consumes, so the formatting logic here mirrors
that module's ``build_pool_trend_table`` / ``build_epc_table`` helpers — the
two clients share the same per-tape contract (``EsmaTapeOutput.model_dump()``
plus a ``"period"`` label key).
"""

from __future__ import annotations

import gradio as gr

# Column headers for the period-over-period pool-trend table.
POOL_TREND_HEADERS = [
    "Period",
    "Pool Balance",
    "Loans",
    "Arrears %",
    "Default %",
    "WTD LTV",
]

# Column headers for the single-period distribution tables.
EPC_HEADERS = ["EPC Label", "% of Pool (by balance)"]
GEO_HEADERS = ["Region / Province", "% of Pool"]
PROPERTY_HEADERS = ["Property Type", "% of Pool"]


def build_pool_trend_rows(tapes: list[dict]) -> list[list]:
    """Build the period-over-period pool-metrics table rows.

    Mirrors the standalone dashboard's ``build_pool_trend_table`` but adds an
    explicit **Arrears %** column (the share of the pool *not* current, i.e.
    the sum of the 1–2m / 180d+ / default buckets) alongside **Default %**.

    Parameters
    ----------
    tapes:
        Tape dicts (``EsmaTapeOutput`` fields + a ``"period"`` label key), in
        chronological order.

    Returns
    -------
    list[list]
        One row per tape with the six :data:`POOL_TREND_HEADERS` columns.
    """
    rows: list[list] = []
    for t in tapes:
        pool_stats = t.get("pool_stats") or {}
        wtd_ltv = pool_stats.get("wtd_ltv")
        ltv_str = f"{wtd_ltv:.1f}%" if wtd_ltv is not None else "N/A"

        arrears = t.get("arrears_breakdown") or {}
        default_pct = arrears.get("default_pct", 0.0)
        # Arrears % = everything not current. Derive from current_pct so it
        # stays correct regardless of which buckets a tape populates.
        current_pct = arrears.get("current_pct")
        if current_pct is not None:
            arrears_pct = max(0.0, 100.0 - current_pct)
        else:
            arrears_pct = (
                arrears.get("arrears_1_2m_pct", 0.0)
                + arrears.get("arrears_180d_plus_pct", 0.0)
                + default_pct
            )

        balance = t.get("pool_balance_eur") or 0.0
        loan_count = t.get("loan_count") or 0
        rows.append(
            [
                t.get("period", "N/A"),
                f"€{balance / 1e6:.1f}M",
                f"{loan_count:,}",
                f"{arrears_pct:.2f}%",
                f"{default_pct:.3f}%",
                ltv_str,
            ]
        )
    return rows


def build_distribution_rows(breakdown: dict | None) -> list[list]:
    """Build ``[label, "NN.N%"]`` rows from a ``{label: pct}`` breakdown.

    Returns rows sorted descending by percentage (largest slice first), which
    reads better for geography / property-type / EPC mixes. A ``None`` or empty
    breakdown yields an empty list (the caller renders an "unavailable" note).
    """
    if not breakdown:
        return []
    return [
        [label, f"{pct:.1f}%"]
        for label, pct in sorted(
            breakdown.items(), key=lambda kv: kv[1], reverse=True
        )
    ]


def build_plot_data(breakdown: dict | None) -> list[dict]:
    """Shape a ``{label: pct}`` breakdown into ``gr.BarPlot`` records.

    Returns a list of ``{"category": label, "percent": pct}`` dicts sorted
    descending by percentage. Empty when the breakdown is absent.
    """
    if not breakdown:
        return []
    return [
        {"category": label, "percent": round(float(pct), 2)}
        for label, pct in sorted(
            breakdown.items(), key=lambda kv: kv[1], reverse=True
        )
    ]


def _latest_period_label(tapes: list[dict]) -> str:
    """Human label for the latest (last, chronological) tape's period."""
    if not tapes:
        return "N/A"
    return str(tapes[-1].get("period", "N/A"))


def render(state: gr.State) -> None:
    """Populate the Pool & Performance tab. Called inside an open ``gr.Tab``.

    Builds the static layout (trend table + latest-period distributions) and
    wires a refresh handler that reads the shared :class:`DealState` and fills
    every component. The handler is wired to both the tab's own refresh button
    and (best-effort) the state's ``.change`` event so loading the deal in the
    shell repopulates the tab automatically.

    Parameters
    ----------
    state:
        The shared session ``gr.State`` whose ``.value`` is a ``DealState``.
        Read as a handler **input**; never mutated here (this tab is
        read-only).
    """
    gr.Markdown(
        "## Pool & Performance\n"
        "Three-period ESMA tape analytics — pool balance, arrears, and "
        "balance-weighted LTV over time, plus the latest period's EPC, "
        "geographic, and property-type mix."
    )

    status = gr.Markdown("*Load the deal to populate pool analytics.*")

    pool_table = gr.Dataframe(
        headers=POOL_TREND_HEADERS,
        label="Pool Metrics — period over period",
        interactive=False,
        wrap=True,
    )

    with gr.Row():
        with gr.Column():
            epc_label = gr.Markdown("### EPC Distribution")
            epc_plot = gr.BarPlot(
                x="category",
                y="percent",
                title="EPC distribution (latest period)",
                x_title="EPC label",
                y_title="% of pool",
                visible=False,
            )
            epc_table = gr.Dataframe(
                headers=EPC_HEADERS,
                label="Energy Performance Certificate distribution",
                interactive=False,
                wrap=True,
            )
        with gr.Column():
            geo_label = gr.Markdown("### Geographic Distribution")
            geo_plot = gr.BarPlot(
                x="category",
                y="percent",
                title="Geographic distribution (latest period)",
                x_title="Region / province",
                y_title="% of pool",
                visible=False,
            )
            geo_table = gr.Dataframe(
                headers=GEO_HEADERS,
                label="Geographic / regional distribution",
                interactive=False,
                wrap=True,
            )

    property_label = gr.Markdown("### Property-Type Distribution")
    property_table = gr.Dataframe(
        headers=PROPERTY_HEADERS,
        label="Property-type distribution",
        interactive=False,
        wrap=True,
    )

    refresh_btn = gr.Button("Refresh from loaded deal", size="sm")

    def _refresh(s) -> tuple:
        """Compute all tab views from the shared DealState.

        Returns updates for: status, pool table, EPC plot/table, geo
        plot/table, and the property-type table.
        """
        empty = (
            "*Load the deal first (use the **Load** button at the top).*",
            [],
            gr.update(visible=False),
            [],
            gr.update(visible=False),
            [],
            [],
        )
        if (
            s is None
            or not getattr(s, "loaded", False)
            or not getattr(s, "tapes", None)
        ):
            return empty

        tapes = s.tapes
        latest = tapes[-1]
        period = _latest_period_label(tapes)

        pool_rows = build_pool_trend_rows(tapes)

        epc_breakdown = latest.get("epc_breakdown")
        epc_rows = build_distribution_rows(epc_breakdown)
        epc_data = build_plot_data(epc_breakdown)

        geo_breakdown = latest.get("geographic_breakdown")
        geo_rows = build_distribution_rows(geo_breakdown)
        geo_data = build_plot_data(geo_breakdown)

        prop_rows = build_distribution_rows(latest.get("property_type_breakdown"))

        note = (
            f"✅ **{getattr(s, 'deal_name', 'deal')}** — "
            f"{len(tapes)} reporting period(s); latest distributions for "
            f"**{period}**."
        )
        if getattr(s, "load_error", None):
            note += f"  ⚠️ {s.load_error}"

        return (
            note,
            pool_rows,
            gr.update(value=epc_data, visible=bool(epc_data)),
            epc_rows,
            gr.update(value=geo_data, visible=bool(geo_data)),
            geo_rows,
            prop_rows,
        )

    outputs = [
        status,
        pool_table,
        epc_plot,
        epc_table,
        geo_plot,
        geo_table,
        property_table,
    ]
    refresh_btn.click(_refresh, inputs=state, outputs=outputs)
    # Best-effort auto-populate when the shared state changes (deal loaded).
    # Guarded: not every Gradio build exposes ``.change`` on gr.State.
    try:
        state.change(_refresh, inputs=state, outputs=outputs)
    except (AttributeError, TypeError):  # pragma: no cover - build-dependent
        pass

    # Silence "assigned but unused" for the section-label handles; they are
    # static captions rendered for layout and intentionally not updated.
    _ = (epc_label, geo_label, property_label)
