"""LoanWhiz Compliance View — Green Lion 2026-1 Report Verification & Covenant Tracking

A standalone Gradio compliance report that calls LoanWhiz primitives directly
(no REST API dependency). It has two tabular sections:

1. Covenant Compliance Over Time — runs the CovenantMonitor across all three
   Green Lion reporting periods (Feb/Mar/Apr 2026) and shows each trigger's
   proximity-to-breach trajectory and current status.
2. Report Verification — runs the ReportVerifier comparing waterfall-computed
   distributions against the Green Lion investor report actuals. This requires a
   Gemini/Vertex AI extraction call; when that is unavailable the section
   degrades gracefully to a single explanatory row.

Run: python clients/compliance/app.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import gradio as gr

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from loanwhiz.config import GREEN_LION
from loanwhiz.primitives.covenant_monitor import CovenantInput, CovenantMonitor
from loanwhiz.primitives.esma_tape_normaliser import EsmaTapeInput, EsmaTapeNormaliser
from loanwhiz.primitives.waterfall_runner import WaterfallInput, WaterfallRunner

# Column headers (shared by the UI and tests).
COVENANT_HEADERS = ["Trigger", "Period", "Metric", "Threshold", "Proximity", "Status"]
REPORT_HEADERS = ["Line Item", "Computed", "Reported", "Delta", "Delta %", "Status"]


# ---------------------------------------------------------------------------
# Covenant compliance
# ---------------------------------------------------------------------------


def build_covenant_rows(statuses: list[Any]) -> list[list]:
    """Build covenant table rows from a list of TriggerStatus-like objects.

    Pure formatting helper (no I/O), so it can be unit-tested with mock
    TriggerStatus objects. Each row has six columns matching
    ``COVENANT_HEADERS``.

    Status icon convention:
    - 🔴 BREACH when the trigger is currently triggered.
    - 🟡 OK     when proximity_pct > 80 (near-miss) but not yet triggered.
    - 🟢 OK     otherwise (comfortably within compliance).

    Args:
        statuses: Iterable of objects exposing ``trigger_name``, ``period``,
                  ``metric_value``, ``threshold``, ``proximity_pct`` and
                  ``is_triggered``.

    Returns:
        List of 6-column rows.
    """
    rows: list[list] = []
    for status in statuses:
        if status.is_triggered:
            icon = "🔴"
        elif status.proximity_pct > 80:
            icon = "🟡"
        else:
            icon = "🟢"
        threshold = status.threshold
        threshold_str = f"{threshold}" if threshold is not None else "N/A"
        rows.append(
            [
                status.trigger_name,
                status.period,
                f"{status.metric_value:.3f}",
                threshold_str,
                f"{status.proximity_pct:.1f}%",
                f"{icon} {'BREACH' if status.is_triggered else 'OK'}",
            ]
        )
    return rows


def run_covenant_compliance() -> tuple[list[list], str]:
    """Run the covenant monitor across all 3 Green Lion periods.

    Loads each reporting period's tape via ``EsmaTapeNormaliser`` (live from
    HuggingFace), runs ``CovenantMonitor`` once over all periods, and returns
    the formatted table rows plus the monitor's plain-English summary.

    Returns:
        ``(rows, summary)`` where ``rows`` is a list of 6-column lists and
        ``summary`` is the covenant compliance summary string.
    """
    normaliser = EsmaTapeNormaliser()
    tapes: list[dict[str, Any]] = []
    for tape_info in GREEN_LION["tape_urls"]:
        r = normaliser.execute(EsmaTapeInput(file_url=tape_info["url"]))
        tapes.append(r.output.model_dump())

    monitor = CovenantMonitor()
    result = monitor.execute(
        CovenantInput(
            periods=tapes,
            triggers=CovenantMonitor.DEFAULT_TRIGGERS,
        )
    )

    rows = build_covenant_rows(result.output.trigger_statuses)
    return rows, result.output.summary


# ---------------------------------------------------------------------------
# Report verification
# ---------------------------------------------------------------------------


def build_report_rows(line_items: list[Any]) -> list[list]:
    """Build report-verification table rows from ReportedFigure-like objects.

    Pure formatting helper (no I/O). Each row has six columns matching
    ``REPORT_HEADERS``: line item, computed value, reported value, delta,
    delta percentage, and a 🟢 MATCH / 🔴 MISMATCH status.

    Args:
        line_items: Iterable of objects exposing ``line_item``,
                    ``computed_value``, ``reported_value``, ``delta``,
                    ``delta_pct`` and ``match``.

    Returns:
        List of 6-column rows.
    """
    rows: list[list] = []
    for fig in line_items:
        icon = "🟢" if fig.match else "🔴"
        rows.append(
            [
                fig.line_item,
                f"{fig.computed_value:,.0f}",
                f"{fig.reported_value:,.0f}",
                f"{fig.delta:,.0f}",
                f"{fig.delta_pct:.2f}%",
                f"{icon} {'MATCH' if fig.match else 'MISMATCH'}",
            ]
        )
    return rows


def run_report_verification() -> tuple[list[list], str]:
    """Run the report verifier on the latest Green Lion investor report.

    Computes a representative April waterfall, then verifies the investor
    report's reported figures against it. The verifier extracts figures from
    the report PDF via Gemini, which needs Vertex AI access; if anything in
    that path fails (no credentials, network error, etc.) the section degrades
    gracefully to a single explanatory row rather than crashing the view.

    Returns:
        ``(rows, summary)``. On success, ``rows`` is one 6-column row per
        compared line item and ``summary`` is the verifier's summary. On
        failure, ``rows`` is a single explanatory row and ``summary`` notes
        that verification is unavailable.
    """
    try:
        # Compute a representative waterfall for April 2026.
        runner = WaterfallRunner()
        wf = runner.execute(
            WaterfallInput(
                reporting_period="April 2026",
                available_revenue_funds=9_500_000,
                available_principal_funds=9_000_000,
                senior_fees=50_000,
                swap_payment=0.0,
                class_a_balance=1_000_000_000,
                class_a_rate_pct=3.62,
                class_b_balance=53_100_000,
                class_c_balance=10_500_000,
                reserve_account_balance=10_636_000,
                reserve_account_target=10_636_000,
                class_a_pdl_balance=0.0,
                class_b_pdl_balance=0.0,
            )
        )

        # Import lazily: the report verifier pulls in google-genai at import
        # time, and we want the covenant tab to work even if that dependency
        # is missing in the environment.
        from loanwhiz.primitives.report_verifier import (
            ReportVerifier,
            ReportVerifierInput,
        )

        verifier = ReportVerifier()
        result = verifier.execute(
            ReportVerifierInput(
                investor_report_url=GREEN_LION["investor_report_urls"][-1]["url"],
                waterfall_output=wf.output.model_dump(),
                reporting_period="April 2026",
            )
        )

        rows = build_report_rows(result.output.line_items)
        return rows, result.output.summary
    except Exception as exc:  # noqa: BLE001 — graceful degradation is the contract.
        explanatory_row = [
            "(report verification requires Vertex AI access)",
            "",
            "",
            "",
            "",
            str(exc)[:80],
        ]
        return [explanatory_row], "Report verification unavailable in this environment."


# ---------------------------------------------------------------------------
# Gradio view
# ---------------------------------------------------------------------------


def create_compliance_view() -> gr.Blocks:
    """Build and return the Gradio Blocks compliance view."""
    with gr.Blocks(title="LoanWhiz Compliance — Green Lion 2026-1") as demo:
        gr.Markdown(
            "# LoanWhiz Compliance View\n"
            "### Green Lion 2026-1 — Report Verification & Covenant Tracking"
        )

        with gr.Tabs():
            with gr.Tab("Covenant Compliance Over Time"):
                cov_btn = gr.Button(
                    "Run Covenant Check (Feb–Apr 2026)", variant="primary"
                )
                cov_summary = gr.Markdown()
                cov_table = gr.Dataframe(
                    headers=COVENANT_HEADERS,
                    label="Trigger proximity-to-breach per period",
                )
                # Single callable returning (rows, summary) — runs the monitor
                # exactly once per click.
                cov_btn.click(
                    run_covenant_compliance,
                    outputs=[cov_table, cov_summary],
                )

            with gr.Tab("Report Verification"):
                rep_btn = gr.Button("Verify April Investor Report", variant="primary")
                rep_summary = gr.Markdown()
                rep_table = gr.Dataframe(
                    headers=REPORT_HEADERS,
                    label="Computed vs reported distributions (April 2026)",
                )
                # Single callable returning (rows, summary) — runs the verifier
                # exactly once per click.
                rep_btn.click(
                    run_report_verification,
                    outputs=[rep_table, rep_summary],
                )

    return demo


if __name__ == "__main__":
    demo = create_compliance_view()
    demo.launch(server_name="0.0.0.0", server_port=7862)
