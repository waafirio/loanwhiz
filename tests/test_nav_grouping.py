"""The sidebar's sections must group by *reader*, and be pinned to that claim.

``web/lib/nav.ts`` is the only place the rail's shape is declared, and this
repo has no JS test runner — so every nav guard is a source-text assertion over
that file (``ui-surface-guards.md``).

The defect this pins is not a wrong link, it is a wrong *neighbour*. Two of the
three sections split on what a surface IS; the third, ``Portfolio``, splits on
who is asking — what do I hold, and what can this platform prove about it? Its
three entries used to be scattered across the other two (#613), each sitting in
a group about something else, and nothing red when they were. A reader infers a
surface's purpose from the heading above it before reading a word of it, so a
misfiled entry misleads exactly as much as a wrong route and costs nothing to
introduce.

The rules here are **positive** — this section holds these entries, in this
order — so each takes a bounded slice of the file rather than a whole-file scan
(``ui-surface-guards.md`` #599: a positive rule scanned whole-file is satisfied
by anything appended anywhere). Each slice is bounded on BOTH sides, by its own
``label:`` and the next one, so an entry cannot satisfy a section's rule by
sitting after it.

Two anchoring rules this file obeys, learned the hard way:

* Anchor on ``label: "X"``, never on the bare label. The file's own docstring
  names all three sections in prose, and an anchor matching prose resolves to
  the wrong offset entirely (#599 — never anchor a slicer on text that is not
  the declaration).
* Every mutant is applied to the REAL source and must match exactly once
  (#573): ``str.replace`` takes the first hit, so an ambiguous anchor rewrites
  a line no check reads and the mutant "survives" while testing nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_NAV = _REPO_ROOT / "web" / "lib" / "nav.ts"

#: The declaration, not the prose. `nav.ts`'s docstring names every section in
#: sentences; matching one of those would slice from the wrong offset.
_LABEL_RE = re.compile(r'^\s*label: "(?P<label>[^"]+)",$', re.MULTILINE)

#: One nav entry, exactly as the file writes them.
_ENTRY_RE = re.compile(
    r'\{ title: "(?P<title>[^"]+)", href: "(?P<href>[^"]+)", icon: (?P<icon>\w+) \}'
)

#: The sections, in the order a reader meets them: the deal, then what they
#: hold, then the platform underneath both.
_SECTIONS = ("Deal Analytics", "Portfolio", "Platform & Governance")

#: The holder section's entries, in the mandated order — the holding first,
#: then the analyses of it. Icons are pinned with the entries: they were chosen
#: with each surface (#573/#565/#568) and are not this issue's to re-pick.
_HOLDER = (
    ("Book", "/book", "BookOpen"),
    ("Concentration", "/concentration", "Layers3"),
    ("Due Diligence", "/due-diligence", "FileSearch"),
)


def _source() -> str:
    return _NAV.read_text(encoding="utf-8")


def _groups(nav: str) -> list[tuple[str, list[tuple[str, str, str]]]]:
    """``[(label, [(title, href, icon), ...]), ...]`` in declaration order.

    Each group's slice runs from its own ``label:`` to the next one, and the
    last to the array terminator — bounded on both sides, so no group's rule
    can be satisfied by an entry belonging to another.
    """
    labels = list(_LABEL_RE.finditer(nav))
    out: list[tuple[str, list[tuple[str, str, str]]]] = []
    for i, match in enumerate(labels):
        start = match.end()
        end = labels[i + 1].start() if i + 1 < len(labels) else nav.index("\n];", start)
        out.append((match.group("label"), _ENTRY_RE.findall(nav[start:end])))
    return out


def _violations(nav: str) -> list[str]:
    """Every way the rail could group its surfaces misleadingly."""
    out: list[str] = []
    groups = _groups(nav)
    by_label = {label: items for label, items in groups}

    if tuple(label for label, _ in groups) != _SECTIONS:
        out.append(
            "three-sections: the sidebar's sections are "
            f"{[label for label, _ in groups]}, not {list(_SECTIONS)}. The "
            "holder's section is what stops these surfaces reading as per-deal "
            "analytics or as platform plumbing"
        )

    holder = by_label.get("Portfolio", [])

    # Membership, order and icons are three separate claims, compared three
    # separate ways on purpose. Fold them into one positional comparison and
    # the weaker checks become unreachable — a misfiled entry would always trip
    # the membership check first, and the order rule could never be observed to
    # fail. `test_no_check_is_decorative` catches exactly that.
    if sorted((t, h) for t, h, _ in holder) != sorted((t, h) for t, h, _ in _HOLDER):
        out.append(
            f"holder-membership: the holder section holds {holder}, not the "
            "book, its concentration and its diligence record. An entry filed "
            "elsewhere is read as answering a different reader's question"
        )
    elif [t for t, _, _ in holder] != [t for t, _, _ in _HOLDER]:
        out.append(
            "holder-order: the holder section reads "
            f"{[t for t, _, _ in holder]}. A reader must meet the holding "
            "before either analysis of it"
        )

    if sorted((t, i) for t, _, i in holder) != sorted((t, i) for t, _, i in _HOLDER):
        out.append(
            f"icons-kept: the holder entries carry {[(t, i) for t, _, i in holder]}; "
            "each icon was picked with its surface and moves with it, unchanged"
        )

    if ("Compliance", "/compliance", "ShieldCheck") not in by_label.get(
        "Deal Analytics", []
    ):
        out.append(
            "compliance-stays-per-deal: Compliance left the per-deal group. It "
            "answers whether the DEAL is inside its covenants, which is not the "
            "holder's question and must not be filed as though it were"
        )

    return out


def _check_ids(violations: list[str]) -> set[str]:
    return {v.split(":", 1)[0] for v in violations}


#: Every check id :func:`_violations` can emit, as data — so
#: :func:`test_no_check_is_decorative` can require a mutant for each.
_ALL_CHECKS = {
    "three-sections",
    "holder-membership",
    "holder-order",
    "icons-kept",
    "compliance-stays-per-deal",
}

#: ``(id, [(old, new), ...])``. Each rewrite is applied to the real `nav.ts`,
#: so a mutant whose ``old`` has drifted off the file fails as stale rather
#: than passing silently. Every entry is a way the rail could mislead a reader
#: while still looking finished — and every one of them was free before #613.
_MUTANTS: list[tuple[str, list[tuple[str, str]]]] = [
    (
        "reorder-the-holding-after-its-analysis",
        [
            # Three edits, not two: swapping directly would leave the second
            # anchor matching twice, and an ambiguous anchor is a coin flip
            # rather than a mutant (#573).
            ('{ title: "Book", href: "/book", icon: BookOpen },', "__SWAP__"),
            (
                '{ title: "Concentration", href: "/concentration", icon: Layers3 },',
                '{ title: "Book", href: "/book", icon: BookOpen },',
            ),
            (
                "__SWAP__",
                '{ title: "Concentration", href: "/concentration", icon: Layers3 },',
            ),
        ],
    ),
    (
        "file-due-diligence-back-under-platform",
        [
            (
                '      { title: "Due Diligence", href: "/due-diligence", icon: FileSearch },\n',
                "",
            ),
            (
                '      { title: "Governance", href: "/governance", icon: Scale },\n',
                '      { title: "Governance", href: "/governance", icon: Scale },\n'
                '      { title: "Due Diligence", href: "/due-diligence", icon: FileSearch },\n',
            ),
        ],
    ),
    (
        "name-the-section-for-the-mechanism",
        [('label: "Portfolio",', 'label: "Holder-Level Surfaces",')],
    ),
    (
        "repick-the-book-icon",
        [
            (
                '{ title: "Book", href: "/book", icon: BookOpen },',
                '{ title: "Book", href: "/book", icon: Layers },',
            )
        ],
    ),
    (
        "pull-compliance-in-beside-the-holder-surfaces",
        [
            (
                '      { title: "Compliance", href: "/compliance", icon: ShieldCheck },\n',
                "",
            ),
            (
                '      { title: "Book", href: "/book", icon: BookOpen },\n',
                '      { title: "Compliance", href: "/compliance", icon: ShieldCheck },\n'
                '      { title: "Book", href: "/book", icon: BookOpen },\n',
            ),
        ],
    ),
]


def _mutate(nav: str, edits: list[tuple[str, str]]) -> str:
    """Apply *edits* to the nav source, refusing a rewrite that no longer fits."""
    for old, new in edits:
        hits = nav.count(old)
        assert hits, (
            f"the mutant no longer applies: {old!r} is not in nav.ts. A stale "
            f"mutant proves nothing — update it to the file as written."
        )
        assert hits == 1, (
            f"the mutant is ambiguous: {old!r} matches {hits} times, so it may "
            f"rewrite a line no check reads (#573). Anchor it uniquely."
        )
        nav = nav.replace(old, new, 1)
    return nav


def test_the_rail_as_committed_groups_by_reader() -> None:
    """The real file satisfies every rule. Without this the mutants prove nothing."""
    assert _violations(_source()) == [], "\n".join(_violations(_source()))


def test_the_three_sections_are_parsed_not_assumed() -> None:
    """A slicer that finds nothing must not read as a rail that violates nothing.

    Every rule above is scoped to a slice, and a slice that vanished would
    satisfy all of them at once (#568). So the parse is asserted first: three
    labelled groups, none of them empty.
    """
    groups = _groups(_source())
    assert [label for label, _ in groups] == list(_SECTIONS)
    for label, items in groups:
        assert items, f"the {label!r} section parsed as empty — the anchor drifted"


@pytest.mark.parametrize("mutant_id,edits", _MUTANTS, ids=[m[0] for m in _MUTANTS])
def test_the_guard_reds_on_every_mutant(
    mutant_id: str, edits: list[tuple[str, str]]
) -> None:
    """Each mutant is a way the rail could mislead a reader. None may pass."""
    found = _violations(_mutate(_source(), edits))
    assert found, (
        f"mutant {mutant_id!r} survived: the guard accepts a rail that files a "
        f"surface under the wrong reader's heading."
    )


def test_no_check_is_decorative() -> None:
    """Every check must be the one that catches some mutant.

    A check no mutant reaches has never been observed to fail, which is the
    state this whole surface was in before #613 — the grouping was asserted
    nowhere, so it drifted for free.
    """
    nav = _source()
    fired: set[str] = set()
    for _, edits in _MUTANTS:
        fired |= _check_ids(_violations(_mutate(nav, edits)))
    assert _ALL_CHECKS - fired == set(), (
        f"no mutant reaches these checks, so nothing shows they can fail: "
        f"{sorted(_ALL_CHECKS - fired)}"
    )
