"""Render the sidebar's section grouping as the block the documents quote.

Why this script exists
----------------------
#613 split the rail into a third section — ``Portfolio``, the holder's own
surfaces — and five committed surfaces went on describing two: ``README.md``
and ``docs/quickstart.md`` each said "two sections" and enumerated the
membership by hand, ``presentation/loanwhiz-deck.json`` presented a two-card
"Two layers, one product" slide to a live audience, ``web/components/app-sidebar.tsx``
named two of the three groups in its own docstring, and
``web/app/(routes)/due-diligence/page.tsx`` said the page sits in "the sidebar's
'Platform & Governance' group" — which after #613 is not merely stale but
false. None of the five was guarded, so nothing red.

That is #602's defect one layer out, and this is #602's remedy: the claim gets
exactly **one** definition and the documents carry a generated region filled
from it. The definition is not a constant in this file — it is
``NAV_GROUPS`` in ``web/lib/nav.ts``, which already *is* the single place the
rail's shape is declared. This module only reads it.

There is one parser, not two. :func:`parse_groups` was #613's ``_groups`` in
``tests/test_nav_grouping.py``; that module now imports it from here, so the
grouping rules and the prose guard cannot disagree about what ``nav.ts`` says.
The repo has no JS test runner, so reading the declaration as source text is
the only route available (``.liz/memory/ui-surface-guards.md``).

Usage
-----
``--write`` fills every region; a bare run prints the block; ``--check`` exits
non-zero naming each document whose region is missing or has drifted.
``--check`` also accepts explicit paths, which is how the checker is
demonstrated to fire.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The single definition. Everything this module emits is read out of it.
NAV_TS = REPO_ROOT / "web" / "lib" / "nav.ts"

#: The generated region's fences. A document opts in by carrying both.
MARKER_START = "<!-- nav-sections:start -->"
MARKER_END = "<!-- nav-sections:end -->"

#: Documents carrying a generated region. `--write` fills each; `--check` and
#: the test require both fences to be **present** in every one of them, so a
#: document cannot quietly drop its region and take the guard with it (#568).
GENERATED_DOCS: tuple[str, ...] = (
    "README.md",
    "docs/quickstart.md",
)

#: The declaration, not the prose. `nav.ts`'s own docstring names every section
#: in sentences, and an anchor matching one of those slices from the wrong
#: offset entirely (`.liz/memory/ui-surface-guards.md`, #599 — never anchor a
#: slicer on text that is not the declaration).
_LABEL_RE = re.compile(r'^\s*label: "(?P<label>[^"]+)",$', re.MULTILINE)

#: One nav entry. Deliberately tolerant of the whitespace between its fields:
#: an entry the parser cannot see is an entry missing from its section, and a
#: guard a cosmetic rewrap can red is a guard someone deletes (#568).
_ENTRY_RE = re.compile(
    r'\{\s*title:\s*"(?P<title>[^"]+)",'
    r'\s*href:\s*"(?P<href>[^"]+)",'
    r'\s*icon:\s*(?P<icon>\w+),?\s*\}'
)

#: Small numbers as English, so the rendered sentence reads as prose. The
#: rail will not plausibly grow past this, and a count with no word falls back
#: to the numeral rather than raising — a document is never worth crashing for.
_COUNT_WORDS = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
}


def count_word(n: int) -> str:
    """``3`` -> ``"three"``. The rendered count and the guard share this map.

    The guard reads a document's *stated* count back through the same table it
    was written with, so "three" and "3" cannot be judged by different rules.
    """
    return _COUNT_WORDS.get(n, str(n))


def nav_source() -> str:
    """The single definition, as source text."""
    return NAV_TS.read_text(encoding="utf-8")


def parse_groups(nav: str) -> list[tuple[str, list[tuple[str, str, str]]]]:
    """``[(label, [(title, href, icon), ...]), ...]`` in declaration order.

    Each group's slice runs from its own ``label:`` to the next one, and the
    last to the array terminator — bounded on **both** sides, so no group's
    membership can be satisfied by an entry belonging to another (#573: a
    region bounded only at its near edge is satisfied by anything appended
    after it).

    This is #613's ``_groups``, moved here so that ``tests/test_nav_grouping.py``
    and the prose guard read the declaration through the same code.
    """
    labels = list(_LABEL_RE.finditer(nav))
    out: list[tuple[str, list[tuple[str, str, str]]]] = []
    for i, match in enumerate(labels):
        start = match.end()
        end = labels[i + 1].start() if i + 1 < len(labels) else nav.index("\n];", start)
        out.append((match.group("label"), _ENTRY_RE.findall(nav[start:end])))
    return out


def group_labels(groups=None) -> list[str]:
    """Every section label, in the order a reader meets it."""
    groups = parse_groups(nav_source()) if groups is None else groups
    return [label for label, _ in groups]


def group_for_route(href: str, groups=None) -> str | None:
    """The label of the section that files *href*, or ``None`` if none does.

    This is what lets a page's own comment be checked rather than trusted: the
    claim "this page sits in section X" has a derivable right answer.
    """
    groups = parse_groups(nav_source()) if groups is None else groups
    for label, items in groups:
        if any(h == href for _, h, _ in items):
            return label
    return None


def render(groups=None) -> str:
    """The block the documents carry. Every figure in it comes off *groups*.

    Nothing here is a literal but the connective prose: the number of sections,
    their labels and their membership are all read from ``NAV_GROUPS``. That is
    the property the test enforces — a grouping a reader can check against the
    rail, rather than one they have to trust.
    """
    groups = parse_groups(nav_source()) if groups is None else groups
    lead = (
        f"The sidebar groups the views into {count_word(len(groups))} sections "
        f"(`NAV_GROUPS` in `web/lib/nav.ts`):"
    )
    lines = [
        f"- **{label}** — {' · '.join(title for title, _, _ in items)}"
        for label, items in groups
    ]
    return "\n".join([lead, ""] + lines)


def _region(text: str) -> str | None:
    """The text between the fences, or ``None`` when either fence is missing.

    Returning ``None`` rather than ``""`` is the point (#568): a guard scoped
    to a slice passes vacuously the moment the slice is empty, so "no region"
    and "an empty region" must not be the same value. Callers treat ``None``
    as a failure, never as a match.
    """
    start = text.find(MARKER_START)
    end = text.find(MARKER_END)
    if start == -1 or end == -1 or end < start:
        return None
    return text[start + len(MARKER_START) : end].strip()


def _fill(text: str, block: str) -> str:
    """Replace the region's contents, leaving the fences and the rest alone.

    The replacement is a **callable**, not a string: ``re.sub`` parses a string
    replacement for escapes, so a section label carrying a backslash would
    raise ``re.error`` or silently mangle the write. A callable's return is
    substituted literally.
    """
    return re.sub(
        re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END),
        lambda _: f"{MARKER_START}\n{block}\n{MARKER_END}",
        text,
        flags=re.S,
    )


def strip_regions(text: str) -> str:
    """Prose with the generated regions removed.

    The generated block *is* a transcribed-looking grouping; it is the one
    place the grouping is allowed, because it is regenerated rather than typed.
    The bans in the guard therefore run over everything else.
    """
    return re.sub(
        re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END), "", text, flags=re.S
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

    block = render()
    if not args.write and not args.check:
        print(block)
        return 0

    drifted: list[str] = []
    for path in _paths(args.paths):
        text = path.read_text(encoding="utf-8")
        if args.write:
            path.write_text(_fill(text, block), encoding="utf-8")
            print(f"wrote {path}")
            continue
        current = _region(text)
        if current is None:
            drifted.append(f"{path}: no nav-sections region (both fences required)")
        elif current != block:
            drifted.append(f"{path}: region has drifted from NAV_GROUPS")
    for line in drifted:
        print(line, file=sys.stderr)
    return 1 if drifted else 0


if __name__ == "__main__":
    raise SystemExit(main())
