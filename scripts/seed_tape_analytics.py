#!/usr/bin/env python3
"""Refresh the committed tape-analytics seeds from the live ESMA tapes (#347).

Why
---
The Pool & Performance page (``/pool``) renders per-period analytics straight
from ``GET /deal/{id}/tape-analytics``, which normalises each ESMA loan tape.
The normaliser reads the tape **directly from its HuggingFace URL at request
time** (``pd.read_csv(<HF url>)``), so on a clean / offline host the runtime
cache (``/tmp/loanwhiz_cache/tape_analytics`` — ephemeral) is empty and the
request fails: the whole Pool section blanks out (surfacing as a CORS error on
the 500). The rest of the demo is offline-capable because it ships *committed*
seed artifacts (deal models under ``src/loanwhiz/data/deals/seed/``); tape
analytics was the one path with no committed seed.

This script (re)generates those committed seed artifacts by normalising every
tape URL in the deal registry **once** (this is the only step that needs the
network) and writing the deterministic ``EsmaTapeOutput`` JSON under the seed
dir the API reads on a cache miss
(``loanwhiz.api.main.TAPE_ANALYTICS_SEED_DIR``). Each seed is keyed by the
``{sha256(url)}.json`` name the loader uses, so the committed file matches the
URL key exactly.

Usage
-----
    # Default: seed every tape URL across the whole deal registry.
    python scripts/seed_tape_analytics.py

    # Restrict to one deal (by registry id).
    python scripts/seed_tape_analytics.py --deal green-lion-2026-1

    # Overwrite seeds that already exist (default skips existing).
    python scripts/seed_tape_analytics.py --force
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

# Make ``loanwhiz`` importable when run as a plain script (no install needed).
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from loanwhiz.config import DEAL_REGISTRY  # noqa: E402  (after sys.path tweak)
from loanwhiz.primitives.esma_tape_normaliser import (  # noqa: E402
    EsmaTapeInput,
    EsmaTapeNormaliser,
)

# The committed seed directory the API reads from on a runtime-cache miss.
# Mirrors ``loanwhiz.api.main.TAPE_ANALYTICS_SEED_DIR``.
SEED_DIR = _SRC / "loanwhiz" / "data" / "tapes" / "seed"


def _seed_name(url: str) -> str:
    """``{sha256(url)}.json`` — must match ``api.main._tape_cache_name``."""
    return f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}.json"


def _tape_urls(deal_filter: str | None) -> list[str]:
    """Every distinct tape URL across the registry (optionally one deal)."""
    urls: list[str] = []
    seen: set[str] = set()
    for deal_id, deal in DEAL_REGISTRY.items():
        if deal_filter and deal_id != deal_filter:
            continue
        for tape in deal.get("tape_urls", []):
            url = tape["url"]
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--deal",
        default=None,
        help="Restrict to one deal registry id (default: all deals).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite seeds that already exist (default: skip existing).",
    )
    args = parser.parse_args()

    urls = _tape_urls(args.deal)
    if not urls:
        print(f"No tape URLs found (deal filter: {args.deal!r}).", file=sys.stderr)
        return 1

    SEED_DIR.mkdir(parents=True, exist_ok=True)
    normaliser = EsmaTapeNormaliser()
    written = 0
    for url in urls:
        dest = SEED_DIR / _seed_name(url)
        if dest.exists() and not args.force:
            print(f"skip (exists): {dest.name}  <- {url}")
            continue
        print(f"normalising: {url}")
        result = normaliser.execute(EsmaTapeInput(file_url=url))
        output = result.output.model_dump()
        dest.write_text(json.dumps(output, indent=1, sort_keys=True), encoding="utf-8")
        written += 1
        print(f"  wrote {dest.name} ({len(json.dumps(output))} bytes)")

    print(f"\nDone — {written} seed(s) written to {SEED_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
