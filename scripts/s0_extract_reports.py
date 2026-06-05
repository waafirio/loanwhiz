#!/usr/bin/env python3
"""S0 ground-truth spike — extract reconcilable figures from the 3 investor reports.

These reports are ESMA *Portfolio & Performance* reports (collateral-side), so
they carry a pool roll-forward but NO liability-side data (no tranche balances,
note factors, PDL, reserve account, or priority-of-payments distributions). This
script extracts the figures that DO exist and that the tapes can be checked
against, plus a ``has_tranche_section`` probe that records the absence.

Output is cached to ``/tmp/loanwhiz_cache/report_extract_full.json`` so the
companion reconciliation script (``s0_reconcile.py``) can run offline.

Requires GCP ADC + Vertex:  GOOGLE_CLOUD_PROJECT=loanwhiz GOOGLE_GENAI_USE_VERTEXAI=true
Run:  python scripts/s0_extract_reports.py
"""

from __future__ import annotations

import json
import pathlib
import re

from google import genai

from loanwhiz.config import GCP_LOCATION, GCP_PROJECT, MODEL_PRO, GREEN_LION

CACHE = pathlib.Path("/tmp/loanwhiz_cache/report_extract_full.json")

PROMPT = """You are a structured finance analyst reading a Green Lion 2026-1 monthly ESMA Portfolio & Performance report for {period}.
Extract EXACTLY this JSON (EUR numbers, no commas/symbols; use null if the figure is genuinely absent from the document):
{{
 "reporting_period_start": "<date>",
 "reporting_period_end": "<date>",
 "reporting_date": "<date the report was published>",
 "loans_begin": <int>, "loans_end": <int>,
 "balance_begin": <float>, "balance_end": <float>,
 "repayments": <float>, "prepayments": <float>, "further_advances": <float>, "other_balance_change": <float>,
 "wtd_avg_coupon_pct": <float>,
 "default_amount_crr": <float>,
 "cpr_life_pct": <float>, "ppr_life_pct": <float>, "cdr_pct": <float>, "payment_ratio_pct": <float>,
 "class_a_balance_end": <float or null>, "class_b_balance_end": <float or null>, "class_c_balance_end": <float or null>,
 "class_a_note_factor": <float or null>,
 "class_a_interest_paid": <float or null>, "class_a_principal_paid": <float or null>,
 "pdl_balance_total": <float or null>, "reserve_fund_balance": <float or null>,
 "total_collections": <float or null>,
 "has_tranche_section": <true/false: does the report contain ANY note/tranche balances, note factors, PDL, reserve account, or priority-of-payments/waterfall distribution tables?>,
 "section_headings": [<list of top-level section headings / table-of-contents entries>]
}}
Repayments=scheduled principal repayments (the "Repayments" line in the Amounts roll-forward). Prepayments=the "Prepayments" line. Report them as POSITIVE numbers even if shown with a -/- reduction sign.
Return ONLY valid JSON, no markdown fences."""


def _strip_fences(text: str) -> str:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
    return re.sub(r"\s*```$", "", text)


def main() -> None:
    client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)
    out: dict[str, dict] = {}
    for entry in GREEN_LION["investor_report_urls"]:
        period, url = entry["period"], entry["url"]
        resp = client.models.generate_content(
            model=MODEL_PRO,
            contents=[
                {
                    "role": "user",
                    "parts": [
                        {"file_data": {"mime_type": "application/pdf", "file_uri": url}},
                        {"text": PROMPT.format(period=period)},
                    ],
                }
            ],
        )
        data = json.loads(_strip_fences(resp.text))
        out[period] = data
        print(f"=== {period} ===")
        print(json.dumps(data, indent=2))

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(out, indent=2))
    print(f"\nWROTE {CACHE}")


if __name__ == "__main__":
    main()
