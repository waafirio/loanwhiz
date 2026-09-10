"""Render the capability-matrix tally as the one sentence the documents quote.

Why this script exists
----------------------
Three committed documents each published a different capability tally, and none
of them matched the system: ``README.md`` said ``2 validated / 15 ran /
13 not-applicable``, ``SYSTEM-STATUS.md`` said ``1 validated / 14 ran /
15 not-applicable`` "over 6 deal columns", and ``presentation/loanwhiz-deck.json``
still carried ``1 validated, 9 ran, 15 not-applicable`` — the figure #441 had
already recorded as superseded. The matrix reported none of the three.

A transcribed number is the defect, not the wrong number: correcting it by hand
buys three weeks. So the sentence has exactly **one** definition — :func:`render`,
below — and the documents carry a generated region filled from it. When a deal is
registered or an answer key lands, ``tests/test_published_capability_tally.py``
reds and the whole fix is::

    PYTHONPATH=src python -m scripts.render_capability_tally --write

The tally is read through the **production wiring** (``capability_matrix()`` in
``loanwhiz.api.main``, i.e. the live ``DEAL_REGISTRY`` plus the committed seed,
answer-key and engine-series loaders), never through a fixture. #574's lesson:
a census taken against your own stand-in agrees with the stand-in, and the
disagreement with the real surface is the whole thing you were looking for.

Usage
-----
``--write`` fills every region; a bare run prints the sentence; ``--check``
exits non-zero naming each document whose region has drifted. ``--check`` also
accepts explicit paths, which is how the checker is demonstrated to fire.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The generated region's fences. A document opts in by carrying both.
MARKER_START = "<!-- capability-tally:start -->"
MARKER_END = "<!-- capability-tally:end -->"

#: Documents carrying a generated region. `--write` fills each; `--check` and
#: the test require the fences to be **present** in every one of them, so a
#: document cannot quietly drop its region and take the guard with it (#568).
GENERATED_DOCS: tuple[str, ...] = (
    "README.md",
    "SYSTEM-STATUS.md",
)


def _load_matrix():
    """Build the matrix exactly as ``GET /capability-matrix`` does.

    Imported lazily so ``--help`` costs nothing and so the import error, when
    ``src`` is missing from ``PYTHONPATH``, names this line rather than the
    module top.
    """
    from loanwhiz.api.main import capability_matrix

    return capability_matrix()


def _oxford(names: list[str]) -> str:
    """``a``, ``a and b``, ``a, b and c`` — the tally may name any number."""
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return f"{', '.join(names[:-1])} and {names[-1]}"


def render(matrix=None) -> str:
    """The one sentence. Every figure in it is derived from *matrix*.

    Nothing here is a literal but the connective prose: the three state counts,
    the deal and cell denominators, the names of the validated deals and the
    jurisdictions they sit in all come off the matrix. That is the property the
    test enforces — a figure a reader can check against the endpoint, rather
    than one they have to trust.
    """
    matrix = matrix or _load_matrix()
    tally = matrix.tally
    validated = sorted(
        {
            next(d.deal_name for d in matrix.deals if d.deal_id == cell.deal_id)
            for cell in matrix.cells
            if cell.state == "validated"
        }
    )
    jurisdictions = sorted(
        {
            d.jurisdiction
            for d in matrix.deals
            if d.deal_id in {c.deal_id for c in matrix.cells if c.state == "validated"}
        }
    )
    counts = (
        f"**{tally['validated']} validated / {tally['ran']} ran / "
        f"{tally['not-applicable']} not-applicable**"
    )
    denominator = (
        f"{len(matrix.deals)} registered deals × {len(matrix.capabilities)} "
        f"capabilities = {len(matrix.cells)} cells"
    )
    if validated:
        named = (
            f" The validated cells are {_oxford(validated)} "
            f"({_oxford(jurisdictions)}), whose engines reproduce those deals' own "
            "published Priorities of Payments to the cent. Every other cell "
            "carries its own reason, and `ran` is not `validated`."
        )
    else:
        named = (
            " No cell is currently `validated`. Every cell carries its own "
            "reason, and `ran` is not `validated`."
        )
    return (
        f"`GET /capability-matrix` reports {counts} over {denominator}."
        f"{named}"
    )


def _region(text: str) -> str | None:
    """The text between the fences, or ``None`` when either fence is missing.

    Returning ``None`` rather than ``""`` is the point. #568: a guard scoped to
    a slice passes vacuously the moment the slice is empty, so "no region" and
    "an empty region" must not be the same value — callers treat ``None`` as a
    failure, never as a match.
    """
    start = text.find(MARKER_START)
    end = text.find(MARKER_END)
    if start == -1 or end == -1 or end < start:
        return None
    return text[start + len(MARKER_START) : end].strip()


def _fill(text: str, sentence: str) -> str:
    """Replace the region's contents, leaving the fences and the rest alone."""
    return re.sub(
        re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END),
        f"{MARKER_START}\n{sentence}\n{MARKER_END}",
        text,
        flags=re.S,
    )


def _paths(explicit: list[str]) -> list[Path]:
    return (
        [Path(p) for p in explicit]
        if explicit
        else [REPO_ROOT / name for name in GENERATED_DOCS]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--write", action="store_true", help="fill each document's region"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit 1 if any region is missing or has drifted",
    )
    parser.add_argument("paths", nargs="*", help="documents to check (default: all)")
    args = parser.parse_args(argv)

    sentence = render()
    if not args.write and not args.check:
        print(sentence)
        return 0

    drifted: list[str] = []
    for path in _paths(args.paths):
        text = path.read_text(encoding="utf-8")
        if args.write:
            path.write_text(_fill(text, sentence), encoding="utf-8")
            print(f"wrote {path}")
            continue
        current = _region(text)
        if current is None:
            drifted.append(f"{path}: no capability-tally region (both fences required)")
        elif current != sentence:
            drifted.append(f"{path}: region has drifted from the matrix")
    for line in drifted:
        print(line, file=sys.stderr)
    return 1 if drifted else 0


if __name__ == "__main__":
    raise SystemExit(main())
