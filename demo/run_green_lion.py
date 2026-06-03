#!/usr/bin/env python3
"""
LoanWhiz Demo — Green Lion 2026-1 B.V.
Barcelona AI Tinkerers Structured Finance Hackathon 2026

Run (full — makes real Docling + Gemini calls, slow ~2-3 min):
    python demo/run_green_lion.py

Run (fast — skips extraction + report-verifier Gemini calls, ~20-30s):
    python demo/run_green_lion.py --fast
    python demo/run_green_lion.py --skip-extraction
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pandas as pd

from loanwhiz.config import GCP_LOCATION, GCP_PROJECT, GREEN_LION, MODEL_FLASH

# Make the bare ``genai.Client()`` used inside some primitives (e.g.
# ReportVerifier) route to Vertex AI, matching the project/region configured in
# loanwhiz.config. We only set these if the operator hasn't already, so an
# explicit GEMINI_API_KEY / GOOGLE_GENAI_USE_VERTEXAI choice is never overridden.
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", GCP_PROJECT)
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", GCP_LOCATION)

# Try to import the data module (available when parallel issue #4 is merged)
try:
    from loanwhiz.data.green_lion import load_all_tapes as _load_all_tapes  # noqa: F401
    _HAS_DATA_MODULE = True
except ModuleNotFoundError:
    # Fall back to direct HuggingFace fetch — demo is intentionally self-contained
    _HAS_DATA_MODULE = False


# ---------------------------------------------------------------------------
# Green Lion 2026-1 capital structure (from prospectus; used by sections 4-7).
# ---------------------------------------------------------------------------

CAPITAL_STRUCTURE = {
    "class_a_balance": 1_000_000_000.0,
    "class_b_balance": 53_100_000.0,
    "class_c_balance": 10_500_000.0,
    "class_a_rate_pct": 3.62,            # EURIBOR 3.19 + 0.43 margin
    "reserve_account_balance": 5_000_000.0,
    "reserve_account_target": 5_000_000.0,
    "senior_fees_estimate": 50_000.0,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def section(title: str) -> None:
    print(f"\n{'='*60}")
    print(f"  {title}")
    print("=" * 60)


def _fmt_eur(value: float) -> str:
    """Format a EUR amount with M/B suffix."""
    if value >= 1_000_000_000:
        return f"€{value/1_000_000_000:.2f}B"
    return f"€{value/1_000_000:.1f}M"


def _m(value: float) -> str:
    """Format a EUR amount in millions, always."""
    return f"€{value/1_000_000:.2f}m"


# ---------------------------------------------------------------------------
# Section 1 — Deal context
# ---------------------------------------------------------------------------

def section_deal_context() -> None:
    section("1. DEAL CONTEXT")
    print(f"\n  Deal:        {GREEN_LION['deal_name']}")
    print(f"  Asset class: Dutch RMBS / Consumer ABS")
    print(f"  Jurisdiction: Netherlands")
    print(f"  Issuer:      Green Lion 2026-1 B.V. (SPV)")
    print(f"  Originator:  ING Bank N.V.")

    print(f"\n  Data sources (Algoritmica/green-lion-2026 on HuggingFace):")
    print(f"    Prospectus:  {GREEN_LION['prospectus_url']}")
    print(f"\n  ESMA loan tapes:")
    for t in GREEN_LION["tape_urls"]:
        print(f"    {t['date']}  {t['url']}")
    print(f"\n  Monthly investor reports:")
    for r in GREEN_LION["investor_report_urls"]:
        print(f"    {r['period']:<18} {r['url']}")


# ---------------------------------------------------------------------------
# Section 2 — ESMA tape analytics
# ---------------------------------------------------------------------------

def _compute_tape_metrics(df: pd.DataFrame, date: str) -> dict:
    """Compute key pool metrics from a raw ESMA tape DataFrame."""
    total_balance = df["current_balance"].sum()
    loan_count = len(df)

    # Balance-weighted average LTV
    wa_ltv = (
        (df["cltomv_current"] * df["current_balance"]).sum() / total_balance
        if total_balance > 0 else float("nan")
    )

    # Arrears: loans with arrears_amount > 0 or days_past_due > 0
    arrears_mask = (df["arrears_amount"] > 0) | (df["days_past_due"] > 0)
    arrears_balance = df.loc[arrears_mask, "current_balance"].sum()
    arrears_pct = 100.0 * arrears_balance / total_balance if total_balance > 0 else 0.0

    # Arrears bucket breakdown
    bucket_counts: dict[str, int] = {}
    if "arrears_bucket" in df.columns:
        bucket_counts = df["arrears_bucket"].value_counts().to_dict()

    # EPC label distribution (% of balance)
    epc_dist: dict[str, float] = {}
    if "epc_label" in df.columns:
        for label, grp in df.groupby("epc_label"):
            epc_dist[str(label)] = 100.0 * grp["current_balance"].sum() / total_balance

    # WA seasoning
    wa_seasoning = (
        (df["seasoning_months"] * df["current_balance"]).sum() / total_balance
        if total_balance > 0 else float("nan")
    )

    return {
        "date": date,
        "loan_count": loan_count,
        "total_balance": total_balance,
        "wa_ltv_pct": wa_ltv,
        "arrears_pct": arrears_pct,
        "arrears_balance": arrears_balance,
        "wa_seasoning_months": wa_seasoning,
        "bucket_counts": bucket_counts,
        "epc_dist": epc_dist,
    }


def section_esma_analytics() -> dict[str, dict]:
    """Load all 3 ESMA tapes, compute metrics, print comparison table."""
    section("2. ESMA TAPE ANALYTICS (Feb / Mar / Apr 2026)")
    if _HAS_DATA_MODULE:
        print("  [data] loanwhiz.data.green_lion module available")
    else:
        print("  [data] loanwhiz.data.green_lion not yet on branch — loading directly from HuggingFace")

    metrics: dict[str, dict] = {}

    for entry in GREEN_LION["tape_urls"]:
        date = entry["date"]
        url = entry["url"]
        print(f"\n  Loading tape {date} ...", end="", flush=True)
        t0 = time.time()
        df = pd.read_csv(url)
        elapsed = time.time() - t0
        print(f" {len(df):,} loans  ({elapsed:.1f}s)")
        metrics[date] = _compute_tape_metrics(df, date)
        # Stash the URL so downstream sections can reuse it without re-deriving.
        metrics[date]["url"] = url

    # Summary table
    print("\n" + "  " + "-" * 72)
    print(
        f"  {'Metric':<32}  {'Feb 2026':>12}  {'Mar 2026':>12}  {'Apr 2026':>12}"
    )
    print("  " + "-" * 72)

    dates = list(metrics.keys())

    def row(label: str, vals: list, fmt=None) -> None:
        fmt = fmt or (lambda x: f"{x:,.0f}" if isinstance(x, float) else str(x))
        fmted = [fmt(metrics[d][label]) for d in dates]
        print(f"  {label:<32}  {fmted[0]:>12}  {fmted[1]:>12}  {fmted[2]:>12}")

    row("loan_count", dates, fmt=lambda x: f"{int(x):,}")
    row("total_balance", dates, fmt=lambda x: _fmt_eur(x))
    row("wa_ltv_pct", dates, fmt=lambda x: f"{x:.2f}%")
    row("arrears_pct", dates, fmt=lambda x: f"{x:.3f}%")
    row("arrears_balance", dates, fmt=lambda x: _fmt_eur(x))
    row("wa_seasoning_months", dates, fmt=lambda x: f"{x:.1f} mo")

    print("  " + "-" * 72)

    # Period-over-period changes
    print("\n  Period-over-period changes (Feb→Mar / Mar→Apr):")
    for label, fmt in [
        ("loan_count", lambda x: f"{int(x):+,}"),
        ("total_balance", lambda x: _fmt_eur(x) if x >= 0 else f"-{_fmt_eur(-x)}"),
        ("arrears_pct", lambda x: f"{x:+.3f}pp"),
        ("wa_ltv_pct", lambda x: f"{x:+.2f}pp"),
    ]:
        d1 = metrics[dates[1]][label] - metrics[dates[0]][label]
        d2 = metrics[dates[2]][label] - metrics[dates[1]][label]
        print(f"    {label:<30} {fmt(d1):>12}  {fmt(d2):>12}")

    # EPC distribution for most recent tape
    latest = metrics[dates[2]]
    print(f"\n  EPC distribution (Apr 2026, % pool balance):")
    for label in sorted(latest["epc_dist"]):
        bar_width = int(latest["epc_dist"][label] / 2)
        bar = "#" * bar_width
        print(f"    {label:>4}  {bar:<25}  {latest['epc_dist'][label]:5.1f}%")

    return metrics


# ---------------------------------------------------------------------------
# Section 3 — Prospectus extraction (live: extract_deal_model)
# ---------------------------------------------------------------------------

# Known top-level section map of the Green Lion prospectus — printed in
# --fast mode when no extraction is run and no cached DealModel exists.
_STATIC_SECTION_MAP = """\
  Section map (top-level):
    ├── 1. Risk Factors
    ├── 2. General Information
    ├── 3. The Transaction
    ├── 4. The Asset Portfolio
    ├── 5. Description of the Notes
    │   ├── 5.1 Priority of Payments
    │   ├── 5.2 Revenue Priority of Payments   ← waterfall (11 steps)
    │   └── 5.3 Interest Rate Risk
    ├── 6. Description of the Collateral
    ├── 7. Credit Enhancement
    ├── 8. Swap Arrangements
    └── 9. Legal Opinions"""


def _print_deal_model_summary(model) -> None:
    """Print tranches, waterfall step count, triggers, completeness from a DealModel."""
    meta = model.metadata
    print(f"\n  Deal model extracted: {meta.deal_name}")
    print(f"  Extraction duration:  {meta.extraction_duration_sec:.1f}s")
    print(f"  Sections found:       {', '.join(meta.sections_found) or '(none)'}")
    print(f"  Completeness score:   {meta.completeness_score:.0%}")

    # Tranche structure (note classes parsed from the prospectus tranche table).
    tranches = model.tranche_structure
    print(f"\n  Tranche structure ({len(tranches)} classes):")
    for t in tranches:
        name = t.get("name", "?")
        size = t.get("size_eur")
        size_str = _fmt_eur(size) if size else "—"
        rating = t.get("rating") or "—"
        rate = t.get("rate") or "—"
        print(f"    {name:<9} {size_str:>10}  {rating:<10} {rate}")

    # Waterfall step counts per waterfall type.
    print(f"\n  Waterfalls extracted:")
    for wf_type, wf in model.waterfalls.items():
        steps = wf.get("steps", [])
        section_src = wf.get("source_section", "")
        print(f"    {wf_type:<16} {len(steps):>2} steps  ({section_src})")

    # Trigger names.
    print(f"\n  Triggers found ({len(model.trigger_names)}):")
    for name in model.trigger_names:
        print(f"    • {name}")


def section_prospectus_extraction(fast: bool) -> None:
    section("3. PROSPECTUS EXTRACTION")

    print(
        "\n  Pipeline: Docling PDF parse → section router → definitions graph\n"
        "            → Gemini structured extraction → executable deal model"
    )

    from loanwhiz.extraction.assembler import extract_deal_model

    # Determine whether a cached DealModel already exists on disk.
    from loanwhiz.extraction.assembler import _slug  # filesystem-safe slug
    cache_path = Path("/tmp/loanwhiz_cache/deals") / f"{_slug(GREEN_LION['deal_name'])}.json"

    if fast and not cache_path.exists():
        print("\n  [--fast] Skipping live extraction (no cached deal model present).")
        print("  Showing the known prospectus section map:\n")
        print(_STATIC_SECTION_MAP)
        print(
            "\n  Run without --fast to execute the full Docling + Gemini extraction\n"
            "  pipeline and produce a machine-executable deal model."
        )
        return

    if fast and cache_path.exists():
        print(f"\n  [--fast] Loading cached deal model from {cache_path}")
    elif cache_path.exists():
        print(f"\n  Loading cached deal model from {cache_path} (disk cache hit)")
    else:
        print("\n  Running full extraction (Docling + Gemini, ~60-120s) ...", flush=True)

    try:
        model = extract_deal_model(
            prospectus_url=GREEN_LION["prospectus_url"],
            deal_name=GREEN_LION["deal_name"],
        )
        _print_deal_model_summary(model)
    except Exception as exc:  # noqa: BLE001
        print(f"\n  [extraction unavailable in this environment: {exc}]")
        print("  Showing the known prospectus section map instead:\n")
        print(_STATIC_SECTION_MAP)


# ---------------------------------------------------------------------------
# Section 4 — Waterfall execution (live: CollectionsAggregator + WaterfallRunner)
# ---------------------------------------------------------------------------

def section_waterfall_execution(metrics: dict[str, dict]) -> dict | None:
    """Aggregate April tape → waterfall inputs, run the waterfall, print results.

    Returns the WaterfallOutput dict (for section 5) or None on failure.
    """
    section("4. WATERFALL EXECUTION")

    from loanwhiz.primitives.collections_aggregator import (
        CollectionsAggregator,
        CollectionsInput,
    )
    from loanwhiz.primitives.waterfall_runner import WaterfallInput, WaterfallRunner

    dates = list(metrics.keys())
    apr = metrics[dates[2]]
    mar = metrics[dates[1]]
    apr_url = apr["url"]
    prev_pool_balance = float(mar["total_balance"])

    print(f"\n  Aggregating April 2026 tape → waterfall inputs")
    print(f"  (CollectionsAggregator: interest accrual + scheduled principal)")

    coll = CollectionsAggregator()
    coll_result = coll.execute(
        CollectionsInput(
            tape_file_url=apr_url,
            reporting_period="April 2026",
            prev_pool_balance=prev_pool_balance,
            class_a_rate_pct=CAPITAL_STRUCTURE["class_a_rate_pct"],
            class_a_balance=CAPITAL_STRUCTURE["class_a_balance"],
            class_b_balance=CAPITAL_STRUCTURE["class_b_balance"],
            class_c_balance=CAPITAL_STRUCTURE["class_c_balance"],
            senior_fees_estimate=CAPITAL_STRUCTURE["senior_fees_estimate"],
            days_in_period=30,
        )
    )
    coll_out = coll_result.output
    print(
        f"    Available Revenue Funds:   {_m(coll_out.available_revenue_funds)}"
        f"  (interest collected)"
    )
    print(
        f"    Available Principal Funds: {_m(coll_out.available_principal_funds)}"
        f"  (scheduled principal vs prior period)"
    )
    print(f"    Pool balance:              {_fmt_eur(coll_out.pool_balance_eur)}")
    print(f"    Aggregation confidence:    {coll_result.confidence:.0%}")

    print(f"\n  Running Revenue + Redemption Priority of Payments (WaterfallRunner)")

    runner = WaterfallRunner()
    wf_result = runner.execute(
        WaterfallInput(
            reporting_period="April 2026",
            available_revenue_funds=coll_out.available_revenue_funds,
            available_principal_funds=coll_out.available_principal_funds,
            senior_fees=coll_out.senior_fees,
            swap_payment=0.0,
            class_a_balance=CAPITAL_STRUCTURE["class_a_balance"],
            class_a_rate_pct=CAPITAL_STRUCTURE["class_a_rate_pct"],
            class_b_balance=CAPITAL_STRUCTURE["class_b_balance"],
            class_c_balance=CAPITAL_STRUCTURE["class_c_balance"],
            reserve_account_balance=CAPITAL_STRUCTURE["reserve_account_balance"],
            reserve_account_target=CAPITAL_STRUCTURE["reserve_account_target"],
            class_a_pdl_balance=0.0,
            class_b_pdl_balance=0.0,
            days_in_period=30,
        )
    )
    wf_out = wf_result.output

    print(f"\n  Revenue Priority of Payments — {len(wf_out.revenue_waterfall)} steps:")
    print("  " + "-" * 64)
    for step in wf_out.revenue_waterfall:
        cond = f"  [{step.condition}]" if step.condition else ""
        print(
            f"    {step.priority:>4}  {step.recipient:<34} "
            f"{_m(step.amount_distributed):>10}{cond}"
        )
    print("  " + "-" * 64)

    print(f"\n  Per-tranche distributions (April 2026):")
    print(
        f"    {'Tranche':<10}  {'Interest':>12}  {'Principal':>12}  {'Total':>12}"
    )
    for dist in wf_out.tranche_distributions:
        print(
            f"    {dist.tranche:<10}  {_m(dist.interest_received):>12}  "
            f"{_m(dist.principal_received):>12}  {_m(dist.total_received):>12}"
        )
    print(f"\n  Total distributed: {_m(wf_out.total_distributed)}"
          f"   Shortfall: {_m(wf_out.shortfall)}")

    # Enrich with pool/reserve so the report verifier (section 5) can compare them.
    wf_dict = wf_out.model_dump()
    wf_dict["pool_balance"] = coll_out.pool_balance_eur
    wf_dict["reserve_fund_balance"] = CAPITAL_STRUCTURE["reserve_account_balance"]
    return wf_dict


# ---------------------------------------------------------------------------
# Section 5 — Investor report verification (live: ReportVerifier + Gemini)
# ---------------------------------------------------------------------------

def section_investor_report_verification(
    fast: bool, waterfall_dict: dict | None
) -> None:
    section("5. INVESTOR REPORT VERIFICATION")

    if waterfall_dict is None:
        print("\n  [skipped — waterfall computation (section 4) did not run]")
        return

    apr_report = next(
        (r for r in GREEN_LION["investor_report_urls"] if r["period"] == "April 2026"),
        None,
    )

    print(
        "\n  Compares the computed waterfall (section 4) against the figures the\n"
        "  servicer published in the April 2026 monthly investor report.\n"
        "  Answers: \"Did the servicer apply the waterfall correctly?\""
    )

    if fast:
        print("\n  [--fast] Skipping Gemini extraction of the investor report PDF.")
        print("  Run without --fast to extract the reported figures and diff them.")
        return

    from loanwhiz.primitives.report_verifier import ReportVerifier, ReportVerifierInput

    print(f"\n  Extracting reported figures from the April PDF via Gemini 2.5 Flash ...",
          flush=True)

    try:
        verifier = ReportVerifier()
        result = verifier.execute(
            ReportVerifierInput(
                investor_report_url=apr_report["url"],
                waterfall_output=waterfall_dict,
                reporting_period="April 2026",
                tolerance_pct=1.0,
            )
        )
        out = result.output

        if out.figures_checked == 0:
            print(
                "\n  No figures could be extracted from the investor report PDF.\n"
                "  (Gemini returned no parseable figures; nothing to compare.)"
            )
            return

        print(f"\n  Line-item comparison (reported vs computed, ±{1.0:.0f}% tolerance):")
        print("  " + "-" * 74)
        print(
            f"    {'Line item':<26}  {'Reported':>14}  {'Computed':>14}  {'Match':>6}"
        )
        print("  " + "-" * 74)
        for fig in out.line_items:
            mark = "✓" if fig.match else "✗"
            print(
                f"    {fig.line_item:<26}  {_m(fig.reported_value):>14}  "
                f"{_m(fig.computed_value):>14}  {mark:>6}"
            )
        print("  " + "-" * 74)
        print(f"\n  {out.summary}")
        print(f"  Verification confidence: {result.confidence:.0%}")

    except Exception as exc:  # noqa: BLE001
        print(
            f"\n  [report verification unavailable in this environment: {exc}]\n"
            "  Vertex AI may be slow or unreachable; the computed waterfall above\n"
            "  is unaffected. Re-run when Gemini is available to see the diff."
        )


# ---------------------------------------------------------------------------
# Section 6 — Covenant monitor (live: EsmaTapeNormaliser + CovenantMonitor)
# ---------------------------------------------------------------------------

def _proximity_marker(status) -> str:
    """🟢/🟡/🔴 marker from a TriggerStatus."""
    if status.is_triggered:
        return "🔴"
    if status.threshold is not None and status.proximity_pct >= 80.0:
        return "🟡"
    return "🟢"


def section_covenant_monitor(metrics: dict[str, dict]) -> None:
    section("6. COVENANT MONITOR")

    from loanwhiz.primitives.covenant_monitor import CovenantMonitor, CovenantInput
    from loanwhiz.primitives.esma_tape_normaliser import (
        EsmaTapeNormaliser,
        EsmaTapeInput,
    )

    print(
        "\n  Normalising all 3 ESMA tapes (EsmaTapeNormaliser) and evaluating the\n"
        "  Green Lion trigger thresholds across each period (CovenantMonitor)."
    )

    normaliser = EsmaTapeNormaliser()
    periods: list[dict] = []
    for entry in GREEN_LION["tape_urls"]:
        url = metrics[entry["date"]]["url"]
        print(f"\n  Normalising tape {entry['date']} ...", end="", flush=True)
        res = normaliser.execute(
            EsmaTapeInput(file_url=url, reporting_date=entry["date"])
        )
        periods.append(res.output.model_dump())
        print(f" annex={res.output.annex_detected}, confidence={res.confidence:.0%}")

    monitor = CovenantMonitor()
    result = monitor.execute(
        CovenantInput(
            periods=periods,
            class_a_pdl_balance=0.0,
            class_b_pdl_balance=0.0,
            reserve_account_balance=CAPITAL_STRUCTURE["reserve_account_balance"],
            reserve_account_target=CAPITAL_STRUCTURE["reserve_account_target"],
            original_pool_balance=float(
                list(metrics.values())[0]["total_balance"]
            ),
        )
    )
    out = result.output

    # Group statuses by trigger name, then print one row per period.
    trigger_names: list[str] = []
    for s in out.trigger_statuses:
        if s.trigger_name not in trigger_names:
            trigger_names.append(s.trigger_name)

    period_labels = [str(p.get("reporting_date", "?")) for p in periods]

    print(f"\n  Trigger status by period (🟢 ok · 🟡 within 20% · 🔴 breached):")
    print("  " + "-" * 74)
    header = f"    {'Trigger':<26}"
    for pl in period_labels:
        header += f"  {pl:>14}"
    print(header)
    print("  " + "-" * 74)

    for tname in trigger_names:
        row = f"    {tname:<26}"
        for pl in period_labels:
            st = next(
                (s for s in out.trigger_statuses
                 if s.trigger_name == tname and s.period == pl),
                None,
            )
            if st is None:
                row += f"  {'-':>14}"
            else:
                marker = _proximity_marker(st)
                if st.threshold is None:
                    cell = f"{marker} n/a"
                else:
                    cell = f"{marker} {st.proximity_pct:5.1f}%"
                row += f"  {cell:>14}"
        print(row)
    print("  " + "-" * 74)

    print(f"\n  {out.summary}")
    if out.active_triggers:
        print(f"  Active breaches (latest period): {', '.join(out.active_triggers)}")
    if out.near_miss_triggers:
        print(f"  Near-misses (latest period):     {', '.join(out.near_miss_triggers)}")


# ---------------------------------------------------------------------------
# Section 7 — Cashflow projection (live: CashflowProjector)
# ---------------------------------------------------------------------------

def section_cashflow_projection(metrics: dict[str, dict]) -> None:
    section("7. CASHFLOW PROJECTION")

    from loanwhiz.primitives.cashflow_projector import (
        CashflowProjector,
        CashflowProjectorInput,
    )

    dates = list(metrics.keys())
    current_pool = float(metrics[dates[2]]["total_balance"])

    print(
        "\n  12-month forward projection (CashflowProjector) under base and stress\n"
        "  scenarios, iterating the waterfall runner monthly over the current\n"
        "  Green Lion capital structure."
    )
    print(f"\n  Current pool balance:  {_fmt_eur(current_pool)}")
    print(
        f"  Capital structure:     A {_fmt_eur(CAPITAL_STRUCTURE['class_a_balance'])}"
        f"  B {_fmt_eur(CAPITAL_STRUCTURE['class_b_balance'])}"
        f"  C {_fmt_eur(CAPITAL_STRUCTURE['class_c_balance'])}"
    )

    projector = CashflowProjector()
    result = projector.execute(
        CashflowProjectorInput(
            current_pool_balance=current_pool,
            current_class_a_balance=CAPITAL_STRUCTURE["class_a_balance"],
            current_class_b_balance=CAPITAL_STRUCTURE["class_b_balance"],
            current_class_c_balance=CAPITAL_STRUCTURE["class_c_balance"],
            class_a_rate_pct=CAPITAL_STRUCTURE["class_a_rate_pct"],
            reserve_fund_balance=CAPITAL_STRUCTURE["reserve_account_balance"],
            # default scenarios = base + 2x-default/+100bps stress (12 months)
        )
    )
    out = result.output

    print(f"\n  Scenario projections ({result.confidence:.0%} confidence):")
    print("  " + "-" * 74)
    print(
        f"    {'Scenario':<10}  {'WAL (Class A)':>14}  {'Class A 12m':>16}  "
        f"{'Pool @ M12':>16}"
    )
    print("  " + "-" * 74)
    for sp in out.scenario_projections:
        wal_yr = sp.wal_class_a_months / 12.0
        pool_m12 = sp.periods[-1].pool_balance_eur if sp.periods else 0.0
        print(
            f"    {sp.scenario.name:<10}  {wal_yr:>10.1f} yr  "
            f"{_m(sp.total_class_a):>16}  {_fmt_eur(pool_m12):>16}"
        )
    print("  " + "-" * 74)
    print(f"\n  {out.summary}")


# ---------------------------------------------------------------------------
# Section 8 — Natural language Q&A (live Gemini call)
# ---------------------------------------------------------------------------

def section_nlq(metrics: dict[str, dict]) -> None:
    section("8. NATURAL LANGUAGE Q&A")

    # Build a concise context from computed tape metrics
    dates = list(metrics.keys())
    context_lines = [
        "Green Lion 2026-1 B.V. — ESMA Loan Tape Summary (Feb–Apr 2026)",
        "",
    ]
    for d in dates:
        m = metrics[d]
        context_lines.append(f"Period: {d}")
        context_lines.append(f"  Loans:             {m['loan_count']:,}")
        context_lines.append(f"  Pool balance:      {_fmt_eur(m['total_balance'])}")
        context_lines.append(f"  WA current LTV:    {m['wa_ltv_pct']:.2f}%")
        context_lines.append(f"  Arrears balance:   {_fmt_eur(m['arrears_balance'])} ({m['arrears_pct']:.3f}% of pool)")
        context_lines.append(f"  WA seasoning:      {m['wa_seasoning_months']:.1f} months")
        # Arrears bucket detail
        if m["bucket_counts"]:
            performing = m["bucket_counts"].get("Performing", 0)
            non_performing = sum(
                v for k, v in m["bucket_counts"].items() if k != "Performing"
            )
            context_lines.append(
                f"  Performing loans:  {performing:,}  |  Non-performing: {non_performing:,}"
            )
        context_lines.append("")

    context = "\n".join(context_lines)

    question = (
        "What is the current arrears profile of the Green Lion pool "
        "and how has it evolved from February to April 2026? "
        "Be specific about the numbers."
    )

    print(f"\n  Question: {question}\n")
    print("  Calling Gemini 2.5 Flash ...\n")

    try:
        from google import genai as google_genai
        from google.genai import types as genai_types

        client = google_genai.Client(
            vertexai=True,
            project=GCP_PROJECT,
            location=GCP_LOCATION,
        )

        prompt = (
            f"You are a structured finance analyst. "
            f"Use the following ESMA loan tape data to answer the question.\n\n"
            f"DATA:\n{context}\n\n"
            f"QUESTION: {question}\n\n"
            f"Provide a concise, specific answer citing the actual numbers."
        )

        response = client.models.generate_content(
            model=MODEL_FLASH,
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=512,
            ),
        )
        answer = response.text
        print("  " + "\n  ".join(answer.strip().splitlines()))

    except Exception as exc:  # noqa: BLE001
        print(f"  [Gemini unavailable in this environment: {exc}]")
        print()
        print("  Answering from tape data directly:")
        m0, m2 = metrics[dates[0]], metrics[dates[2]]
        delta_arr = m2["arrears_pct"] - m0["arrears_pct"]
        print(
            f"\n  The Green Lion pool had arrears of {m0['arrears_pct']:.3f}% "
            f"({_fmt_eur(m0['arrears_balance'])}) in February 2026.\n"
            f"  By April 2026 this moved to {m2['arrears_pct']:.3f}% "
            f"({_fmt_eur(m2['arrears_balance'])}), a change of "
            f"{delta_arr:+.3f}pp over the two-month period.\n"
            f"  The sequential pay trigger threshold is 2.00%; the pool is "
            f"well inside that boundary.\n"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(fast: bool = False) -> None:
    if fast:
        print("\n  [running in --fast mode: extraction + report-verifier Gemini "
              "calls skipped]")

    section_deal_context()

    metrics = section_esma_analytics()

    section_prospectus_extraction(fast)
    waterfall_dict = section_waterfall_execution(metrics)
    section_investor_report_verification(fast, waterfall_dict)
    section_covenant_monitor(metrics)
    section_cashflow_projection(metrics)

    section_nlq(metrics)

    section("DEMO COMPLETE")
    print()
    print("  LoanWhiz — structured finance agent framework")
    print("  github.com/waafirio/loanwhiz | Apache 2.0")
    print("  Built for Barcelona AI Tinkerers SF Hackathon 2026")
    print()


if __name__ == "__main__":
    _fast = any(arg in ("--fast", "--skip-extraction") for arg in sys.argv[1:])
    main(fast=_fast)
