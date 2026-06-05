#!/usr/bin/env python3
"""Refresh the committed deal-model seeds from a warm extraction cache (#196).

Why
---
The Overview landing page renders Capital Structure / Triggers / Completeness
straight from the extracted :class:`~loanwhiz.extraction.assembler.DealModel`
that ``GET /deal/{id}/model`` serves. On a clean host the runtime extraction
cache (``data/deals/*.json``) is cold *and gitignored*, so the first screen the
demo audience sees is blank. To avoid a ~30-min cold Docling+Gemini run on a
fresh checkout, schema-valid extracted models are committed under
``src/loanwhiz/data/deals/seed/`` and the API falls back to them on a runtime
cache miss (``loanwhiz.api.main.DEAL_MODEL_SEED_DIR``).

This script (re)generates those committed seed artifacts from a source cache
directory of already-extracted models — it never runs an extraction itself.
It is deal-agnostic: every ``{slug}.json`` found in the source directory that
validates as a ``DealModel`` is copied into the seed directory.

Usage
-----
    # Default: copy every extracted model from the durable runtime cache
    # (data/deals/) into the committed seed dir.
    python scripts/seed_deal_models.py

    # From an explicit source (e.g. a warm host cache) and/or a subset of
    # deal slugs.
    python scripts/seed_deal_models.py --source /var/tmp/loanwhiz-main/data/deals
    python scripts/seed_deal_models.py --deal green-lion-2026-1-bv

Each artifact is re-serialised through ``DealModel`` so only schema-valid
models land, and the ``metadata.cache_path`` is normalised to a host-agnostic
repo-relative path so the committed seed carries no machine-specific absolute
path.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make ``loanwhiz`` importable when run as a plain script (no install needed).
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from loanwhiz.extraction.assembler import (  # noqa: E402  (after sys.path tweak)
    DEFAULT_DEAL_CACHE_DIR,
    DealModel,
    _slug,
)

# The committed seed directory the API reads from on a runtime-cache miss.
# Mirrors ``loanwhiz.api.main.DEAL_MODEL_SEED_DIR``.
SEED_DIR = _SRC / "loanwhiz" / "data" / "deals" / "seed"


def _normalise_seed(model: DealModel) -> DealModel:
    """Return ``model`` with a host-agnostic, repo-relative ``cache_path``.

    The source model's ``metadata.cache_path`` is whatever absolute path the
    warm host wrote it to; committing that leaks a machine-specific path. Rewrite
    it to the repo-relative runtime-cache location the deal *would* live at, so
    every checkout sees the same value.
    """
    model.metadata.cache_path = f"data/deals/{_slug(model.metadata.deal_name)}.json"
    return model


def seed_deal_models(
    source_dir: Path,
    seed_dir: Path = SEED_DIR,
    only: set[str] | None = None,
) -> list[str]:
    """Copy every schema-valid ``{slug}.json`` from ``source_dir`` into ``seed_dir``.

    Args:
        source_dir: directory of already-extracted ``DealModel`` JSON files.
        seed_dir: committed seed directory to write into (created if absent).
        only: when given, restrict to these deal slugs.

    Returns:
        The slugs that were seeded (sorted).

    Raises:
        FileNotFoundError: if ``source_dir`` does not exist.
    """
    if not source_dir.is_dir():
        raise FileNotFoundError(f"source directory not found: {source_dir}")

    seed_dir.mkdir(parents=True, exist_ok=True)
    seeded: list[str] = []
    for src in sorted(source_dir.glob("*.json")):
        slug = src.stem
        if only is not None and slug not in only:
            continue
        try:
            model = DealModel.model_validate_json(src.read_text(encoding="utf-8"))
        except ValueError as exc:  # not a valid DealModel — skip, don't commit junk
            print(f"skip {src.name}: not a valid DealModel ({exc})", file=sys.stderr)
            continue
        dest = seed_dir / f"{slug}.json"
        dest.write_text(
            _normalise_seed(model).model_dump_json(indent=2), encoding="utf-8"
        )
        seeded.append(slug)
        print(f"seeded {slug} -> {dest}")
    return sorted(seeded)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_DEAL_CACHE_DIR,
        help="Directory of extracted DealModel JSON files "
        "(default: the durable runtime cache, data/deals/).",
    )
    parser.add_argument(
        "--seed-dir",
        type=Path,
        default=SEED_DIR,
        help="Committed seed directory to write into "
        "(default: src/loanwhiz/data/deals/seed).",
    )
    parser.add_argument(
        "--deal",
        action="append",
        dest="deals",
        metavar="SLUG",
        help="Restrict to this deal slug (repeatable). Default: all found.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    only = set(args.deals) if args.deals else None
    seeded = seed_deal_models(args.source, args.seed_dir, only=only)
    if not seeded:
        print(
            f"no deal models seeded from {args.source} — nothing to do.",
            file=sys.stderr,
        )
        return 1
    print(f"\nseeded {len(seeded)} deal model(s): {', '.join(seeded)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
