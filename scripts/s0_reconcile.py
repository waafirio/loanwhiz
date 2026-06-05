#!/usr/bin/env python3
"""S0 ground-truth spike — reconcile tape-derived figures vs investor reports.

Reads two caches that already exist on the spike machine:

  * ``/tmp/loanwhiz_cache/tape_analytics/*.json`` — esma_tape_normaliser output
    (one file per period; pool_balance_eur, wtd_coupon_pct, ...).
  * ``/tmp/loanwhiz_cache/report_extract_full.json`` — written by
    ``s0_extract_reports.py`` (the figures the reports actually carry).

It compares, per period, every figure the two sources have in common and prints
the absolute + percentage discrepancy, then runs three consistency checks:

  1. pool_balance(end): tape sum vs report Net Outstanding balance (end).
  2. principal: tape balance-delta vs report (begin - end) roll-forward.
  3. period chaining: report.begin[N] vs tape.pool[N-1] vs report.end[N-1].

No network access required — pure arithmetic over the two caches.
Run:  python scripts/s0_reconcile.py
"""

from __future__ import annotations

import calendar
import glob
import json
import pathlib

TAPE_DIR = pathlib.Path("/tmp/loanwhiz_cache/tape_analytics")
REPORT_CACHE = pathlib.Path("/tmp/loanwhiz_cache/report_extract_full.json")

# (report period label, tape reporting_date, prior tape reporting_date or None)
PERIODS = [
    ("February 2026", "2026-02-28", None),  # Jan-2026 tape absent → no prior delta
    ("March 2026", "2026-03-31", "2026-02-28"),
    ("April 2026", "2026-04-30", "2026-03-31"),
]


def _pct(a: float, b: float) -> float:
    return (a - b) / b * 100.0 if b else float("nan")


def _days_in(date: str) -> int:
    y, m, _ = (int(x) for x in date.split("-"))
    return calendar.monthrange(y, m)[1]


def main() -> None:
    reports = json.loads(REPORT_CACHE.read_text())
    tapes = {}
    for f in glob.glob(str(TAPE_DIR / "*.json")):
        d = json.loads(pathlib.Path(f).read_text())
        tapes[d["reporting_date"]] = d

    print(f"{'PERIOD':<14}{'FIGURE':<28}{'TAPE':>18}{'REPORT':>18}{'D_abs':>15}{'D_%':>9}")
    print("-" * 102)
    for label, end, prev in PERIODS:
        t, r = tapes[end], reports[label]

        tp, rp = t["pool_balance_eur"], r["balance_end"]
        print(f"{label:<14}{'pool_balance (end)':<28}{tp:>18,.2f}{rp:>18,.2f}{tp - rp:>15,.2f}{_pct(tp, rp):>9.4f}")

        tc, rc = t["pool_stats"]["wtd_coupon_pct"], r["wtd_avg_coupon_pct"]
        print(f"{'':<14}{'wtd_coupon_pct':<28}{tc:>18.4f}{rc:>18.4f}{tc - rc:>15.4f}{_pct(tc, rc):>9.4f}")

        rprin = r["repayments"] + r["prepayments"]
        if prev in tapes:
            tdelta = tapes[prev]["pool_balance_eur"] - tp
            # apples-to-apples: full report roll-forward (begin-end) vs tape delta
            report_rollfwd = r["balance_begin"] - r["balance_end"]
            print(f"{'':<14}{'principal: tape_delta vs report_rollfwd':<28}"
                  f"{tdelta:>18,.2f}{report_rollfwd:>18,.2f}{tdelta - report_rollfwd:>15,.4f}{_pct(tdelta, report_rollfwd):>9.4f}")
            print(f"{'':<14}{'  (vs rep+prepay only; other line)':<28}"
                  f"{tdelta:>18,.2f}{rprin:>18,.2f}{tdelta - rprin:>15,.2f}{_pct(tdelta, rprin):>9.4f}"
                  f"   other_balance_change={r['other_balance_change']:,.2f}")
        else:
            print(f"{'':<14}{'principal (no prior tape; Jan absent)':<28}{'N/A':>18}{rprin:>18,.2f}")

        tint = tp * tc / 100.0 * _days_in(end) / 360.0
        print(f"{'':<14}{'interest: tape pool*c*days/360':<28}{tint:>18,.2f}{'(absent in report)':>18}")
        print()

    print("INTERNAL ROLL-FORWARD (report begin - repay - prepay + further + other == end):")
    for label, _, _ in PERIODS:
        r = reports[label]
        calc = (r["balance_begin"] - r["repayments"] - r["prepayments"]
                + r["further_advances"] + r["other_balance_change"])
        print(f"  {label:<14} computed_end={calc:,.2f}  reported_end={r['balance_end']:,.2f}  D={calc - r['balance_end']:,.4f}")

    print("\nPERIOD CHAINING (report.begin[N] == tape.pool[N-1] == report.end[N-1]):")
    for cur, prev_lbl, prev_date in [("March 2026", "February 2026", "2026-02-28"),
                                     ("April 2026", "March 2026", "2026-03-31")]:
        print(f"  {cur}.begin={reports[cur]['balance_begin']:,.2f}  "
              f"tape({prev_date})={tapes[prev_date]['pool_balance_eur']:,.2f}  "
              f"{prev_lbl}.end={reports[prev_lbl]['balance_end']:,.2f}")

    print("\nLIABILITY-SIDE FIGURES PRESENT IN REPORTS (tranche/PDL/reserve/distributions):")
    for label, _, _ in PERIODS:
        r = reports[label]
        present = [k for k in ("class_a_balance_end", "class_a_interest_paid",
                               "class_a_principal_paid", "pdl_balance_total",
                               "reserve_fund_balance", "total_collections")
                   if r.get(k) is not None]
        print(f"  {label:<14} has_tranche_section={r['has_tranche_section']}  "
              f"liability_figures_present={present or 'NONE'}")


if __name__ == "__main__":
    main()
