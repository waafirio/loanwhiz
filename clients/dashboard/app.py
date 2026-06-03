"""LoanWhiz Dashboard — Green Lion 2026-1 Deal Monitor

Run: python clients/dashboard/app.py
"""

import sys
from pathlib import Path

import gradio as gr

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from loanwhiz.config import GREEN_LION
from loanwhiz.primitives.covenant_monitor import CovenantInput, CovenantMonitor
from loanwhiz.primitives.esma_tape_normaliser import EsmaTapeInput, EsmaTapeNormaliser


def load_all_tapes() -> list[dict]:
    """Load all 3 Green Lion tapes from HuggingFace."""
    normaliser = EsmaTapeNormaliser()
    results = []
    for tape_info in GREEN_LION["tape_urls"]:
        r = normaliser.execute(EsmaTapeInput(file_url=tape_info["url"]))
        results.append({"period": tape_info["date"], **r.output.model_dump()})
    return results


def build_pool_trend_table(tapes: list[dict]) -> list[list]:
    """Build period-over-period pool metrics table.

    Args:
        tapes: List of dicts with keys from EsmaTapeOutput plus ``"period"``.

    Returns:
        List of rows, each with 6 columns:
        [Period, Pool Balance, Loans, Current %, Default %, WTD LTV].
    """
    rows = []
    for t in tapes:
        pool_stats = t.get("pool_stats", {})
        wtd_ltv = pool_stats.get("wtd_ltv")
        ltv_str = f"{wtd_ltv:.1f}%" if wtd_ltv is not None else "N/A"
        arrears = t.get("arrears_breakdown", {})
        rows.append(
            [
                t["period"],
                f"€{t['pool_balance_eur'] / 1e6:.1f}M",
                f"{t['loan_count']:,}",
                f"{arrears.get('current_pct', 0.0):.2f}%",
                f"{arrears.get('default_pct', 0.0):.3f}%",
                ltv_str,
            ]
        )
    return rows


def build_epc_table(tape: dict) -> list[list]:
    """Build EPC breakdown table from a tape dict.

    Args:
        tape: Single tape dict (typically the latest period).

    Returns:
        List of [EPC label, percentage string] rows, sorted by label.
    """
    epc = tape.get("epc_breakdown") or {}
    return [[label, f"{pct:.1f}%"] for label, pct in sorted(epc.items())]


def build_covenant_table(tapes: list[dict]) -> list[list]:
    """Build covenant status table across all periods.

    Args:
        tapes: List of tape dicts (EsmaTapeOutput fields + ``"period"``).

    Returns:
        List of rows with 4 columns:
        [Trigger, Period, Proximity to Breach, Status].
    """
    # CovenantMonitor expects the ``reporting_date`` key — use the tape's
    # own ``reporting_date`` field (set by EsmaTapeNormaliser from the CSV).
    monitor = CovenantMonitor()
    result = monitor.execute(
        CovenantInput(
            periods=tapes,
            triggers=CovenantMonitor.DEFAULT_TRIGGERS,
        )
    )
    rows = []
    for status in result.output.trigger_statuses:
        if status.is_triggered:
            indicator = "🔴 TRIGGERED"
        elif status.proximity_pct > 80:
            indicator = "🟡 OK"
        else:
            indicator = "🟢 OK"
        rows.append(
            [
                status.trigger_name,
                status.period,
                f"{status.proximity_pct:.1f}%",
                indicator,
            ]
        )
    return rows


def create_dashboard() -> gr.Blocks:
    """Build and return the Gradio Blocks dashboard."""
    with gr.Blocks(title="LoanWhiz Dashboard — Green Lion 2026-1") as demo:
        gr.Markdown(
            "# LoanWhiz Dashboard\n"
            "### Green Lion 2026-1 B.V. — Dutch RMBS | ING Bank N.V."
        )

        refresh_btn = gr.Button("Load / Refresh Deal Data", variant="primary")
        status = gr.Markdown(
            "*Click 'Load Deal Data' to fetch Green Lion tape data from HuggingFace.*"
        )

        with gr.Tabs():
            with gr.Tab("Pool Performance"):
                pool_table = gr.Dataframe(
                    headers=["Period", "Pool Balance", "Loans", "Current %", "Default %", "WTD LTV"],
                    label="Pool Metrics — Feb to Apr 2026",
                )

            with gr.Tab("EPC Distribution"):
                epc_table = gr.Dataframe(
                    headers=["EPC Label", "% of Pool (by balance)"],
                    label="Energy Performance Certificate Distribution (Apr 2026)",
                )

            with gr.Tab("Covenant Monitor"):
                covenant_table = gr.Dataframe(
                    headers=["Trigger", "Period", "Proximity to Breach", "Status"],
                    label="Covenant Compliance Status",
                )

        def refresh():
            tapes = load_all_tapes()
            pool_rows = build_pool_trend_table(tapes)
            epc_rows = build_epc_table(tapes[-1])
            covenant_rows = build_covenant_table(tapes)
            return (
                "✅ Data loaded from HuggingFace (Algoritmica/green-lion-2026)",
                pool_rows,
                epc_rows,
                covenant_rows,
            )

        refresh_btn.click(
            refresh,
            outputs=[status, pool_table, epc_table, covenant_table],
        )

    return demo


if __name__ == "__main__":
    demo = create_dashboard()
    demo.launch(server_name="0.0.0.0", server_port=7861)
