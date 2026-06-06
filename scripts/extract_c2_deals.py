#!/usr/bin/env python3
"""Extract the Italian + Spanish deal-model seeds (C2, epic #236).

The cross-jurisdiction proof: run the **unmodified** extraction pipeline
(:func:`loanwhiz.extraction.assembler.extract_deal_model`) against the two
non-Dutch prospectuses C1 (#244) registered in ``deals.json`` — Leone Arancio
(Italy) and Sol-Lion II (Spain) — to show the pipeline isn't Dutch-RMBS-specific.

This script *drives* the pipeline; it does not modify it. It reads each deal's
``prospectus_url`` straight from ``src/loanwhiz/data/deals.json`` (nothing
hardcoded) and calls ``extract_deal_model`` for each, which writes the assembled
``DealModel`` JSON into the durable runtime cache (``data/deals/{slug}.json``).
A separate step (``scripts/seed_deal_models.py``) then normalises those runtime
artifacts into the committed seed directory.

The Docling OCR is a ~20-35 min/prospectus long-pole, so this is meant to be
launched detached (``nohup python scripts/extract_c2_deals.py &``) with the GCP
env set::

    GOOGLE_CLOUD_PROJECT=loanwhiz GOOGLE_GENAI_USE_VERTEXAI=true \\
        nohup python scripts/extract_c2_deals.py > /tmp/c2-extract.log 2>&1 &

Extraction is incremental and resumable: each deal is cached the moment it
completes, and ``extract_deal_model`` no-ops on a cache hit, so a re-run after a
crash only re-extracts the deal that didn't finish.

Usage
-----
    python scripts/extract_c2_deals.py                 # both deals
    python scripts/extract_c2_deals.py --deal leone-arancio-2023-1
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

# Make ``loanwhiz`` importable when run as a plain script (no install needed).
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from loanwhiz.extraction.assembler import extract_deal_model  # noqa: E402

# The deal-registry keys (in deals.json) for the two C2 jurisdictions.
_C2_DEAL_KEYS = ("leone-arancio-2023-1", "sol-lion-ii")

_DEALS_JSON = _SRC / "loanwhiz" / "data" / "deals.json"


def _load_deals() -> dict:
    return json.loads(_DEALS_JSON.read_text(encoding="utf-8"))


def _extract_one(deal_key: str, deals: dict) -> bool:
    """Extract one deal by its registry key. Returns True on success."""
    deal = deals.get(deal_key)
    if deal is None:
        print(f"[c2] ERROR: deal key {deal_key!r} not in {_DEALS_JSON}", file=sys.stderr)
        return False
    url = deal.get("prospectus_url")
    name = deal.get("deal_name")
    if not url or not name:
        print(
            f"[c2] ERROR: deal {deal_key!r} missing prospectus_url/deal_name",
            file=sys.stderr,
        )
        return False

    print(f"[c2] extracting {deal_key} ({name}) <- {url}", flush=True)
    try:
        model = extract_deal_model(prospectus_url=url, deal_name=name)
    except Exception:  # noqa: BLE001 — long-pole driver: report, keep going
        print(f"[c2] FAILED to extract {deal_key}:", file=sys.stderr)
        traceback.print_exc()
        return False

    m = model.metadata
    n_def = len(model.definitions)
    n_tr = len(model.tranche_structure)
    n_trig = len(model.trigger_names)
    wf_steps = {k: len(v.get("steps", [])) for k, v in model.waterfalls.items()}
    print(
        f"[c2] DONE {deal_key}: completeness={m.completeness_score:.2f} "
        f"definitions={n_def} tranches={n_tr} triggers={n_trig} "
        f"waterfall_steps={wf_steps} duration={m.extraction_duration_sec:.0f}s "
        f"-> {m.cache_path}",
        flush=True,
    )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--deal",
        action="append",
        dest="deals",
        metavar="DEAL_KEY",
        help="Restrict to this deal-registry key (repeatable). "
        f"Default: {', '.join(_C2_DEAL_KEYS)}.",
    )
    args = parser.parse_args(argv)

    keys = tuple(args.deals) if args.deals else _C2_DEAL_KEYS
    deals = _load_deals()

    results = {k: _extract_one(k, deals) for k in keys}
    ok = sum(results.values())
    print(f"\n[c2] extracted {ok}/{len(keys)}: " + ", ".join(
        f"{k}={'ok' if v else 'FAIL'}" for k, v in results.items()
    ), flush=True)
    # Exit non-zero only if *every* requested deal failed; a partial success
    # still leaves a real seed to commit.
    return 0 if ok > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
