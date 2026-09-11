"""What ``/compare`` must keep on screen: plotted lines, and labels inside their box (#629).

``web/`` has no JS test runner, so this asserts the components' **source**, not
their rendered output — the same trade ``tests/test_concentration_page.py`` and
``tests/test_book_page.py`` already make, stated here rather than implied
because a source guard proves the code *says* a thing, never that a browser
paints it. What a browser paints was checked separately, with CDP, and both
defects below were invisible to a source-level reading until then:

* the four performance charts drew their lines on the plot **frame** — a pool
  factor of 0.986 at y=8 on a plot spanning y=8-202, a 0.00% cumulative loss
  rate at y=202 — so every chart read as empty while plotting real data;
* the structural-diff row labels "Unmapped steps (not comparable)" and
  "Unmapped triggers (not comparable)" measured 225px and 240px against a 192px
  sticky column and painted 49px and 64px past it, printing on top of the
  scrolled value columns.

Both survived review precisely because nothing in the source *looks* wrong: the
first is recharts' default domain doing what it documents, the second is a
shared ``TableCell`` default. So the checks below are about the two decisions
that are easy to revert by accident, and :data:`_MUTANTS` rewrites the real
source to prove each check rejects something. :func:`test_no_check_is_decorative`
requires every check to be the one that catches at least one mutant — a check no
mutant reaches has never been observed to fail.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _REPO_ROOT / "web" / "components" / "compare" / "performance-panel.tsx"
_DIFF = _REPO_ROOT / "web" / "components" / "compare" / "structural-diff.tsx"

#: The sticky row-label cell in the structural diff, identified by the classes
#: that make it sticky and pin its width. Every label check is scoped to this
#: element, so its absence would make them scan an empty string and pass —
#: hence the vacuity assert in :func:`_row_label_cell`.
_STICKY_LABEL_ANCHOR = "sticky left-0 z-10 w-48"

#: Identifiers that must survive in the panel for the chart checks to mean
#: anything. Same vacuity guard, pointed at the other file.
_PANEL_ANCHORS = ("paddedDomain", "YAxis", "LineChart")


def _code_only(source: str) -> str:
    """Strip comments so prose *describing* a defect does not satisfy a check.

    Load-bearing in both directions here. This file's own subject matter is
    CSS class names and prop names, and both components carry comments that
    name them: ``structural-diff.tsx`` explains why the label is not
    ``truncate``\\ d, and a ban reading that sentence as code would fire on the
    documentation of the rule it enforces. In the other direction, a check that
    accepted a comment would let ``domain={domain}`` be deleted from the JSX and
    still pass because the word survives in a docstring.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return "\n".join(
        line
        for line in source.splitlines()
        if not line.lstrip().startswith(("//", "*"))
    )


def _row_label_cell(diff: str) -> str:
    """The opening tag of the sticky row-label ``<TableCell>``, comments stripped."""
    code = _code_only(diff)
    assert _STICKY_LABEL_ANCHOR in code, (
        "the sticky row-label cell is gone from structural-diff.tsx (no "
        f"{_STICKY_LABEL_ANCHOR!r}); every label check below would scan an "
        "empty string and pass vacuously"
    )
    start = code.index(_STICKY_LABEL_ANCHOR)
    # Back up to the enclosing <TableCell, forward to the end of its opening tag.
    open_at = code.rfind("<TableCell", 0, start)
    assert open_at != -1, "the sticky row label is no longer a <TableCell>"
    end = code.index(">", start)
    return code[open_at : end + 1]


def _y_axis(panel: str) -> str:
    """The ``<YAxis .../>`` element of the overlay charts, comments stripped."""
    code = _code_only(panel)
    found = re.findall(r"<YAxis\b[^>]*/>", code, re.S)
    assert len(found) == 1, (
        f"expected exactly one <YAxis/> in performance-panel.tsx, found "
        f"{len(found)}; the chart checks below are scoped to it"
    )
    return found[0]


def _sources() -> dict[str, str]:
    """The two real files, as committed."""
    return {
        "panel": _PANEL.read_text(encoding="utf-8"),
        "diff": _DIFF.read_text(encoding="utf-8"),
    }


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------

#: Class names that would clip the row label instead of wrapping it. Clipping
#: removes the overlap too, which is why it has to be banned by name rather
#: than left to the overlap check: "Unmapped steps (not comparable)" truncated
#: to "Unmapped steps (not compar…" drops the qualifier that is the entire
#: content of the row, and the row would then assert a comparison it is
#: explicitly refusing to make.
_CLIPPING_CLASSES = ("truncate", "overflow-hidden", "text-ellipsis")

#: A numeric literal assigned to one of the padding constants. Both are read
#: rather than pattern-matched, because a padding of zero is the defect
#: restored while every other check still passes: the props are present, the
#: helper is called, and the line goes straight back onto the frame.
_CONSTANT = {
    "HEADROOM": re.compile(r"^const HEADROOM\s*=\s*([0-9.]+);", re.M),
    "RANGE_INSET": re.compile(r"^const RANGE_INSET\s*=\s*([0-9.]+);", re.M),
}

#: The zero anchor in :func:`paddedDomain`. Without it the domain collapses to
#: the data's own extent, which fixes the frame collision by *fabricating*
#: range: a pool factor wobbling between 0.9857 and 0.9860 would be redrawn as
#: a full-height cliff. The issue's one explicit constraint — a flat series must
#: still read as flat — lives in this expression.
_ZERO_ANCHOR = re.compile(r"Math\.min\(\s*0\s*,")

#: Both domain endpoints routed through :func:`niceBound`. recharts only picks
#: round numbers while the domain is ``'auto'``; handed explicit endpoints it
#: uses them verbatim, so the padded bound put ``11912320`` on the reserve axis
#: where ``12000000`` had been. Reverting this is not a correctness bug — the
#: line stays off the frame either way — which is exactly why nothing else here
#: would catch it.
_ROUNDED_BOUNDS = re.compile(r"niceBound\(\s*hi \+ pad\s*\)")


def _violations(*, panel: str, diff: str) -> list[str]:
    """Every check, by id. Empty list means the surface as committed is fine.

    :data:`_MUTANTS` run this same function against a rewritten source.
    """
    bad: list[str] = []
    panel_code = _code_only(panel)
    axis = _y_axis(panel)
    label = _row_label_cell(diff)

    # --- the charts ---------------------------------------------------------
    if "domain=" not in axis:
        bad.append("yaxis-unpadded")
    if "padding=" not in axis:
        bad.append("yaxis-no-range-inset")
    if not re.search(r"function paddedDomain\b", panel_code):
        bad.append("domain-helper-missing")
    for name, pattern in _CONSTANT.items():
        found = pattern.search(panel_code)
        if found is None or float(found.group(1)) <= 0:
            bad.append(f"padding-constant-{name.lower()}-not-positive")
    if not _ZERO_ANCHOR.search(panel_code):
        bad.append("zero-anchor-dropped")
    if not _ROUNDED_BOUNDS.search(panel_code):
        bad.append("axis-bounds-not-rounded")

    # --- the row label ------------------------------------------------------
    if "whitespace-normal" not in label:
        bad.append("row-label-does-not-wrap")
    for cls in _CLIPPING_CLASSES:
        if re.search(rf'(?:^|[\s"]){re.escape(cls)}(?:$|[\s"])', label):
            bad.append("row-label-clipped")
            break

    return bad


def test_the_compare_surface_as_committed_passes_every_check() -> None:
    """The real files, unmodified. Every mutant below is measured against this."""
    assert _violations(**_sources()) == []


def test_the_anchors_are_what_scope_every_check() -> None:
    """A check scoped to a vanished element passes by scanning nothing."""
    sources = _sources()
    assert _STICKY_LABEL_ANCHOR in _code_only(sources["diff"])
    for anchor in _PANEL_ANCHORS:
        assert anchor in sources["panel"], anchor


def test_the_padded_domain_is_the_one_the_charts_render() -> None:
    """The helper is wired into the axis, not merely defined beside it.

    ``paddedDomain`` computing a perfect domain that no ``<YAxis>`` consumes is
    the defect with a passing unit test on top of it.
    """
    panel = _code_only(_PANEL.read_text(encoding="utf-8"))
    assert "paddedDomain(rows" in panel, "the helper is never called on the chart rows"
    assert "domain={domain}" in _y_axis(panel), "the computed domain never reaches the axis"


# ---------------------------------------------------------------------------
# What each check rejects
# ---------------------------------------------------------------------------

#: ``(id, target, [(old, new), ...])`` — each rewrite is a plausible regression,
#: not a scrambling of the file, and each must make :func:`_violations` red.
_MUTANTS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "domain-prop-removed",
        "panel",
        [("                    domain={domain}\n", "")],
    ),
    (
        "range-inset-prop-removed",
        "panel",
        [
            (
                "                    padding={{ top: RANGE_INSET, bottom: RANGE_INSET }}\n",
                "",
            )
        ],
    ),
    (
        "headroom-zeroed",
        "panel",
        [("const HEADROOM = 0.12;", "const HEADROOM = 0;")],
    ),
    (
        "range-inset-zeroed",
        "panel",
        [("const RANGE_INSET = 10;", "const RANGE_INSET = 0;")],
    ),
    (
        "zero-anchor-dropped",
        "panel",
        [("lo = Math.min(0, v);", "lo = v;")],
    ),
    (
        "helper-renamed-out-of-use",
        "panel",
        [
            ("export function paddedDomain(", "export function unusedDomain("),
            ("paddedDomain(rows, dealIds)", "unusedDomain(rows, dealIds)"),
        ],
    ),
    (
        # Reverts the rounding without touching the padding: the line stays off
        # the frame, and only the axis labels regress.
        "axis-bounds-left-unrounded",
        "panel",
        [("niceBound(hi + pad)", "hi + pad")],
    ),
    (
        "row-label-nowrap-restored",
        "diff",
        [('                        "whitespace-normal break-words align-top",\n', "")],
    ),
    (
        "row-label-truncated-instead-of-wrapped",
        "diff",
        [
            (
                '"whitespace-normal break-words align-top"',
                '"truncate align-top"',
            )
        ],
    ),
    (
        # Wraps *and* clips: the overlap is gone, so every other check is
        # happy, and the label is still cut off mid-qualifier. Without this
        # mutant "row-label-clipped" only ever fires alongside
        # "row-label-does-not-wrap" and is never the check that rejects
        # anything on its own (#613/#617).
        "row-label-wrapped-but-still-clipped",
        "diff",
        [
            (
                '"whitespace-normal break-words align-top"',
                '"whitespace-normal break-words align-top overflow-hidden"',
            )
        ],
    ),
]


@pytest.mark.parametrize("mutant_id,target,edits", _MUTANTS, ids=[m[0] for m in _MUTANTS])
def test_the_guard_reds_on_every_mutant(
    mutant_id: str, target: str, edits: list[tuple[str, str]]
) -> None:
    """Each rewrite must be caught. A mutant that passes names a hole in the guard."""
    sources = _sources()
    for old, new in edits:
        assert old in sources[target], (
            f"mutant {mutant_id!r} no longer applies: {old!r} is not in the "
            f"{target} source, so this mutant tests nothing"
        )
        sources[target] = sources[target].replace(old, new, 1)
    assert _violations(**sources), f"mutant {mutant_id!r} slipped past every check"


def test_no_check_is_decorative() -> None:
    """Every check needs a mutant whose **only** violation is that check.

    The weaker form — "every check fires on some mutant" — passes for a check
    that can never be the one rejecting anything, because a stricter check
    always fires first and subsumes it. That is unreachable, not merely
    redundant, and it reads as coverage (``.liz/memory``: 2026-09-10 · pitfall ·
    #613, made mechanical by #617). Asking for a mutant that isolates each
    check is what surfaces it: here it forced a "wraps but still clips" mutant,
    without which ``row-label-clipped`` only ever fired behind
    ``row-label-does-not-wrap``.
    """
    isolated: set[str] = set()
    seen: set[str] = set()
    for mutant_id, target, edits in _MUTANTS:
        sources = _sources()
        for old, new in edits:
            sources[target] = sources[target].replace(old, new, 1)
        fired = set(_violations(**sources))
        assert fired, f"mutant {mutant_id!r} slipped past every check"
        seen.update(fired)
        if len(fired) == 1:
            isolated.update(fired)

    assert _violations(**_sources()) == [], "the clean tree must violate nothing"

    expected = {
        "yaxis-unpadded",
        "yaxis-no-range-inset",
        "domain-helper-missing",
        "padding-constant-headroom-not-positive",
        "padding-constant-range_inset-not-positive",
        "zero-anchor-dropped",
        "axis-bounds-not-rounded",
        "row-label-does-not-wrap",
        "row-label-clipped",
    }
    assert expected <= seen, (
        f"these checks never fired on any mutant: {sorted(expected - seen)}"
    )
    unreachable = expected - isolated
    assert not unreachable, (
        f"no mutant isolates these checks: {sorted(unreachable)} — each fires "
        "only behind another check, so it has never been the check that "
        "rejected anything"
    )
