"""No committed surface may describe a sidebar grouping that ``NAV_GROUPS`` denies.

The defect this pins is not a wrong section name, it is a **transcribed** one.
#613 split the rail into a third section — ``Portfolio``, the holder's own
surfaces — and five committed surfaces went on describing two: ``README.md``
and ``docs/quickstart.md`` said "two sections" and enumerated the membership by
hand, ``presentation/loanwhiz-deck.json`` presented a two-card
"Two layers, one product" slide to a live audience,
``web/components/app-sidebar.tsx`` named two of three groups in its own
docstring, and ``web/app/(routes)/due-diligence/page.tsx`` said the page sits in
"the sidebar's 'Platform & Governance' group" — after #613 not merely stale but
**false**. None of the five was guarded, so nothing red, and correcting them by
hand buys exactly one regrouping.

So the grouping has one definition (``NAV_GROUPS``), one reader
(``scripts.render_nav_sections.parse_groups`` — the same parser
``tests/test_nav_grouping.py`` uses, so the two cannot disagree), and every
surface either carries a **generated region** or is **checked against the
declaration**. Which of the two a surface gets is a judgment about its content,
not a ranking: the deck's card bodies and the two ``.tsx`` docstrings carry
editorial voice a generator would destroy, so they are guarded; the Markdown
docs' grouping is pure membership, so it is generated.

The checks below — ``_ALL_CHECKS`` is the list, not this sentence, because a
transcribed count is the defect this whole module exists to stop. Every one is
reached by a mutant that reaches **no other**
(``.liz/memory/guard-mutation-tables.md`` #613 — a check no mutant reaches
alone has never been observed to fail, and reads as coverage), and the same
rule is applied one granularity down: each accepted *phrasing* inside a check
needs its own mutant too, since a check id can fire while an alternative
inside it has never matched anything.

* ``region-drift`` / ``region-missing`` — the generated regions equal what
  ``NAV_GROUPS`` says, and the fences are asserted **present** before anything
  is compared, so the guard cannot pass by the region vanishing (#568).
* ``named-groups`` — every section the sidebar's own docstring names exists,
  and it names them all. Both directions: a docstring that invents a group and
  one that forgets a group are the same defect seen from either end.
* ``placement-claim`` — a page that says which section it sits in is checked
  against the section ``NAV_GROUPS`` actually files its route under. This is
  the one the due-diligence page failed.
* ``count-claim`` — a stated number of sections matches the real one.
* ``deck-cards`` — the demo deck carries one card per section, in order, each
  listing that section's entries.

Two things this file gets right on purpose, both learned elsewhere:

**It flattens before it scans.** The placement claim in the due-diligence page
is broken across two comment lines by a ``*`` leader, so a literal byte-scan
for ``the sidebar's "Portfolio" group`` does not see it — and that is not
hypothetical, it is how #602's ban missed a claim split by ``{" "}`` and a tag.
:func:`_flatten` reads the sentence a *reader* sees rather than the bytes the
file holds, and :func:`test_the_placement_claim_survives_a_rewrap` pins that
the flattening is load-bearing rather than decorative.

**It enumerates rather than being clever.** A repo-wide ban on spelled-out
counts was measured and rejected: ``(one|two|…)\\s+(sections?|layers?)`` matches
25+ non-nav uses here — "one layer down" in a dozen memory entries, "three
sections of a structured finance prospectus", "all 8 sections" in the demo
runner. The count check is therefore scoped to the surfaces below, where a
count of sections can only mean the rail's (#602's lesson: prefer a checkable
claim list to a regex that has to be right about English).

This file names the labels, which is why it scans only the surfaces listed
below and never itself (#575: the prose promising a property must not trip the
guard enforcing it).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts.render_nav_sections import (
    GENERATED_DOCS,
    MARKER_START,
    _region,
    count_word,
    group_for_route,
    main,
    nav_source,
    parse_groups,
    render,
    strip_regions,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

_SIDEBAR = "web/components/app-sidebar.tsx"
_DECK = "presentation/loanwhiz-deck.json"
_DUE_DILIGENCE = "web/app/(routes)/due-diligence/page.tsx"

#: Every committed surface that describes the rail's grouping. A grouping is
#: only fixed once no copy of it disagrees, so this list is the fix's real
#: extent. It is also the whole extent: a sweep for either of the two
#: pre-#613 labels returns these five plus `nav.ts` itself and two tests that
#: read it as a declaration rather than restating it.
GUARDED_SURFACES: tuple[str, ...] = GENERATED_DOCS + (
    _SIDEBAR,
    _DECK,
    _DUE_DILIGENCE,
)

#: The declaration the sidebar's docstring sits above. Anchored on the
#: component's **name**, which no rule here tests — never on text a rule
#: inside the slice reads, or a mutant editing that text makes the slicer
#: raise and the sweep records an error rather than the violation it found
#: (`.liz/memory/ui-surface-guards.md`, #599).
_SIDEBAR_DECL = "export function AppSidebar()"

#: The deck slide that draws the rail, anchored on its kicker for the same
#: reason: no check below reads the kicker.
_DECK_KICKER = "What we built — the dashboard"

#: A claim about which section a surface sits in. An enumerated list of the
#: forms this repo actually writes, not a general parser of English — the
#: measured alternative over-matched badly (see the module docstring).
_PLACEMENT_RES = (
    re.compile(r"sidebar's\s+\"([^\"]+)\"\s+(?:group|section)"),
    re.compile(r"\"([^\"]+)\"\s+(?:group|section)\s+of\s+the\s+sidebar"),
)

#: The nouns a count of the rail can be written against. "layers" is here
#: because the deck said "Two layers, one product" before this issue, so a
#: future editor reaching for that word must not slip the count past the check.
#: Kept as data, not inlined, so :func:`test_every_accepted_count_noun_is_exercised`
#: can require a mutant for each.
_COUNT_NOUNS = ("sections", "layers")

#: A stated number of sections. Scoped to GUARDED_SURFACES, where these nouns
#: can only mean the rail's — measured repo-wide the same pattern matches 25+
#: innocent uses (see the module docstring).
_COUNT_RE = re.compile(
    r"(?<![\w-])(one|two|three|four|five|six|seven|eight|\d+)\s+"
    r"(?:sidebar\s+)?(?:" + "|".join(f"{n[:-1]}s?" for n in _COUNT_NOUNS) + r")\b",
    re.I,
)

#: Any double-quoted run inside the sidebar docstring. That docstring quotes
#: section labels and nothing else, which is what makes "every quoted name is a
#: real section" a checkable claim rather than a guess.
_QUOTED_RE = re.compile(r"\"([^\"]+)\"")

#: `web/app/(routes)/due-diligence/page.tsx` -> `/due-diligence`, so a page's
#: claim about its own section has a derivable right answer.
_ROUTE_RE = re.compile(r"^web/app/\(routes\)/(?P<route>[^/]+)/page\.tsx$")


def _flatten(text: str) -> str:
    """Prose with markup, comment leaders and line breaks removed.

    The claim this guard checks is committed as::

         * page sits in the sidebar's
         * "Portfolio" group and shares no vocabulary with the covenant screen.

    — one sentence broken by a newline and a ``*`` leader. A literal scan over
    the raw file does not see it. #602 hit the same shape one layer in, where
    a claim split by ``{" "}`` and a ``<span>`` survived a byte-level ban and
    rendered to users anyway. Flattening first makes the guard read the
    sentence a reader sees rather than the bytes the file holds.
    """
    text = re.sub(r"\{\s*[\"'][^\"']*[\"']\s*\}", " ", text)  # {" "} and friends
    # Bounded to a single line: a tag never spans one here, and an unbounded
    # `[^>]*` would swallow everything between a stray `<` and the next `>`
    # several lines later — including claims this guard exists to read.
    text = re.sub(r"<[^>\n]*>", " ", text)  # JSX/HTML tags, markdown comments
    text = re.sub(r"^[ \t]*/\*\*?", " ", text, flags=re.M)  # /** opener
    text = re.sub(r"^[ \t]*\*/", " ", text, flags=re.M)  # */ closer
    # A single `*` leader, never `**bold**`: the docs mark section names with
    # emphasis, and eating it would hide the very labels being checked.
    text = re.sub(r"^[ \t]*\*(?!\*)[ \t]?", " ", text, flags=re.M)
    text = re.sub(r"^[ \t]*//[ \t]?", " ", text, flags=re.M)  # `//` leader
    text = text.replace('\\"', '"').replace("\\n", " ")  # JSON string escapes
    text = text.replace("&apos;", "'").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text)


def _read(relative_path: str) -> str:
    return (_REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _corpus() -> dict[str, str]:
    return {path: _read(path) for path in GUARDED_SURFACES}


def _doc_comment_above(src: str, declaration: str) -> str:
    """The block comment immediately above *declaration*.

    Asserted present before it is used to slice (#568): an ``index()`` on a
    marker that vanished raises, and a guard that raises is recorded as an
    error rather than as the violation it actually found.
    """
    assert declaration in src, (
        f"{declaration!r} is gone, so this guard's slice no longer exists"
    )
    idx = src.index(declaration)
    start = src.rindex("/**", 0, idx)
    return src[start:idx]


def _violations(docs: dict[str, str], nav: str) -> list[str]:
    """Every way a committed surface could describe a rail that is not this one."""
    out: list[str] = []
    groups = parse_groups(nav)
    labels = [label for label, _ in groups]
    block = render(groups)

    # --- the generated regions ------------------------------------------
    for path in GENERATED_DOCS:
        region = _region(docs[path])
        if region is None:
            out.append(
                f"region-missing: {path} carries no nav-sections region. Both "
                "fences are required — a document that drops its region takes "
                "the guard with it and every rule below passes vacuously"
            )
        elif region != block:
            out.append(
                f"region-drift: {path}'s region has drifted from NAV_GROUPS. "
                "Do not retype it — run "
                "`PYTHONPATH=src python -m scripts.render_nav_sections --write`"
            )

    # --- the sidebar's own docstring ------------------------------------
    comment = _flatten(_doc_comment_above(docs[_SIDEBAR], _SIDEBAR_DECL))
    named = set(_QUOTED_RE.findall(comment))
    invented = sorted(named - set(labels))
    forgotten = [label for label in labels if label not in named]
    if invented:
        out.append(
            f"named-groups: {_SIDEBAR} names {invented}, which NAV_GROUPS does "
            "not contain. A rail's own component describing a section that does "
            "not exist is the defect this guard was built for"
        )
    elif forgotten:
        out.append(
            f"named-groups: {_SIDEBAR} does not name {forgotten}. Naming some "
            "sections and not others is how the two-section description "
            "survived a three-section rail"
        )

    # --- a surface's claim about which section it sits in -----------------
    for path, text in docs.items():
        flat = _flatten(text)
        route = _ROUTE_RE.match(path)
        expected = group_for_route(f"/{route.group('route')}", groups) if route else None
        for pattern in _PLACEMENT_RES:
            for claimed in pattern.findall(flat):
                if claimed not in labels:
                    out.append(
                        f"placement-claim: {path} places a surface in the "
                        f"{claimed!r} group, which NAV_GROUPS does not contain"
                    )
                elif expected is not None and claimed != expected:
                    out.append(
                        f"placement-claim: {path} says it sits in {claimed!r}, "
                        f"but NAV_GROUPS files its route under {expected!r}. A "
                        "comment asserting a structure the structure denies "
                        "misleads exactly as much as a wrong route"
                    )

    # --- a stated number of sections --------------------------------------
    allowed = {count_word(len(groups)).lower(), str(len(groups))}
    for path, text in docs.items():
        for stated in _COUNT_RE.findall(_flatten(strip_regions(text))):
            if stated.lower() not in allowed:
                out.append(
                    f"count-claim: {path} says the rail has {stated!r} sections; "
                    f"NAV_GROUPS declares {len(groups)}"
                )

    # --- the demo deck ------------------------------------------------------
    out.extend(_deck_violations(docs[_DECK], groups))
    return out


def _deck_violations(text: str, groups) -> list[str]:
    """The deck is demo-facing: an operator presents from it to an audience.

    #602 already found this file carrying a capability tally #441 had recorded
    as superseded. Treat it as a surface a viewer reads, never as a build
    artefact.
    """
    out: list[str] = []
    try:
        deck = json.loads(text)
    except json.JSONDecodeError as exc:  # pragma: no cover - a broken deck
        return [f"deck-cards: {_DECK} is not valid JSON ({exc})"]

    slides = [s for s in deck.get("slides", []) if s.get("kicker") == _DECK_KICKER]
    if len(slides) != 1:
        return [
            f"deck-cards: expected exactly one {_DECK_KICKER!r} slide, found "
            f"{len(slides)}. The slide that draws the rail is this guard's "
            "subject; without it there is nothing to check"
        ]
    slide = slides[0]
    cards = slide.get("cards", [])

    # A sequence, not a set: the deck is read left to right, and a rail
    # presented in the wrong order tells the audience the wrong story about
    # which reader each section serves.
    titles = [card.get("title") for card in cards]
    if titles != [label for label, _ in groups]:
        out.append(
            f"deck-cards: the deck presents {titles}, not "
            f"{[label for label, _ in groups]}. The operator demos from this file"
        )
        return out

    if slide.get("columns") != len(groups):
        out.append(
            f"deck-cards: the slide lays out {slide.get('columns')} columns for "
            f"{len(groups)} sections, so a section renders off the slide"
        )

    for card, (label, items) in zip(cards, groups):
        # The lead run before the em dash is membership; the tail after it is
        # editorial voice a generator would flatten, which is why this file is
        # guarded rather than generated.
        lead = card.get("body", "").split(" — ", 1)[0]
        listed = [part.strip() for part in lead.split("·")]
        if listed != [title for title, _, _ in items]:
            out.append(
                f"deck-cards: the {label!r} card lists {listed}, not "
                f"{[title for title, _, _ in items]}"
            )
    return out


def _check_ids(violations: list[str]) -> set[str]:
    return {v.split(":", 1)[0] for v in violations}


#: Every check id :func:`_violations` can emit, as data — so
#: :func:`test_no_check_is_decorative` can require a mutant for each.
_ALL_CHECKS = {
    "region-drift",
    "region-missing",
    "named-groups",
    "placement-claim",
    "count-claim",
    "deck-cards",
}

#: ``(id, path, [(old, new), ...])``. Each rewrite is applied to the real
#: committed file, so a mutant whose ``old`` has drifted off it fails as stale
#: rather than passing silently (#573). Every entry is a way a surface could
#: describe the wrong rail while still looking finished — and every one of them
#: was free before this issue.
_MUTANTS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "hand-edit-a-generated-region",
        "README.md",
        [("into three sections", "into two sections")],
    ),
    (
        # Removing one fence, not the content: `_region` must distinguish "no
        # region" from "an empty region", and both are failures (#568).
        "delete-a-generated-region",
        "docs/quickstart.md",
        [(MARKER_START, "")],
    ),
    (
        "drop-a-section-from-the-sidebar-docstring",
        _SIDEBAR,
        [
            (
                ' * ("Deal Analytics" — the per-deal analyst views; "Portfolio" — the holder\'s\n'
                " * own surfaces, grouped by who is asking rather than by what they are;\n",
                ' * ("Deal Analytics" — the per-deal analyst views;\n',
            )
        ],
    ),
    (
        # The issue's explicit proof: name a group that does not exist.
        "name-a-section-the-rail-does-not-have",
        _SIDEBAR,
        [('"Portfolio" — the holder\'s', '"Holdings" — the holder\'s')],
    ),
    (
        # Restores the exact defect #617 was filed for.
        "refile-the-page-under-the-wrong-section",
        _DUE_DILIGENCE,
        [('* "Portfolio" group and shares', '* "Platform & Governance" group and shares')],
    ),
    (
        "state-a-stale-section-count",
        _DECK,
        [("Three sections, one product", "Two sections, one product")],
    ),
    (
        "drop-a-card-from-the-deck",
        _DECK,
        [
            (
                '        { "title": "Portfolio", "accent": "accent", "body": "Book · Concentration · Due Diligence — what a holder owns, and what the platform can prove about it. Grouped by who is asking, not by what the surfaces are." },\n',
                "",
            )
        ],
    ),
    (
        # Exercises the `layers` noun — the deck's own pre-#617 wording, which
        # nothing else in this table reaches.
        "state-a-stale-count-against-the-old-noun",
        _DECK,
        [("Three sections, one product", "Two layers, one product")],
    ),
    (
        # Exercises the second accepted placement phrasing. Written so the
        # first pattern does NOT also match, or it would prove nothing about
        # the second.
        "refile-the-page-using-the-other-phrasing",
        _DUE_DILIGENCE,
        [
            (
                " * page sits in the sidebar's\n * \"Portfolio\" group and shares",
                " * page sits in the\n * \"Platform & Governance\" group of the sidebar and shares",
            )
        ],
    ),
    (
        "let-a-deck-card-forget-an-entry",
        _DECK,
        [
            (
                "Showcase · Validation · Framework · MCP · Governance",
                "Showcase · Validation · Framework · Governance",
            )
        ],
    ),
]


def _mutate(docs: dict[str, str], path: str, edits: list[tuple[str, str]]) -> dict[str, str]:
    """Apply *edits* to one surface, refusing a rewrite that no longer fits."""
    text = docs[path]
    for old, new in edits:
        hits = text.count(old)
        assert hits, (
            f"the mutant no longer applies: {old!r} is not in {path}. A stale "
            f"mutant proves nothing — update it to the file as written."
        )
        assert hits == 1, (
            f"the mutant is ambiguous: {old!r} matches {hits} times in {path}, "
            f"so it may rewrite text no check reads (#573). Anchor it uniquely."
        )
        text = text.replace(old, new, 1)
    return {**docs, path: text}


def test_every_published_surface_agrees_with_the_rail() -> None:
    """The committed tree satisfies every rule. Without this the mutants prove nothing."""
    found = _violations(_corpus(), nav_source())
    assert found == [], "\n".join(found)


def test_every_guarded_surface_is_present_and_carries_its_claim() -> None:
    """A surface that vanished must not read as a surface that violates nothing.

    Every rule above is scoped to a file, and a file that emptied would satisfy
    all of them at once (#568). So the corpus is asserted first.
    """
    for path in GUARDED_SURFACES:
        text = _read(path)
        assert text.strip(), f"{path} is empty — the guard would pass vacuously"
    for path in GENERATED_DOCS:
        assert MARKER_START in _read(path), f"{path} lost its nav-sections region"
    assert _SIDEBAR_DECL in _read(_SIDEBAR)
    assert _DECK_KICKER in _read(_DECK)


@pytest.mark.parametrize(
    "mutant_id,path,edits", _MUTANTS, ids=[m[0] for m in _MUTANTS]
)
def test_the_guard_reds_on_every_mutant(
    mutant_id: str, path: str, edits: list[tuple[str, str]]
) -> None:
    """Each mutant is a way a document could describe the wrong rail. None may pass."""
    found = _violations(_mutate(_corpus(), path, edits), nav_source())
    assert found, (
        f"mutant {mutant_id!r} survived: a committed surface may describe a "
        f"grouping the rail denies, which is the state this issue exists to end."
    )


def test_no_check_is_decorative() -> None:
    """Every check must be the one that catches some mutant, alone.

    Not merely "reached by some mutant": a check only ever reached alongside a
    stronger one has never been observed to fire on its own, and reads as
    coverage while being unreachable (`.liz/memory/guard-mutation-tables.md`,
    #613). So the requirement is a mutant whose *only* violation is this check.
    """
    docs, nav = _corpus(), nav_source()
    alone: set[str] = set()
    for _, path, edits in _MUTANTS:
        fired = _check_ids(_violations(_mutate(docs, path, edits), nav))
        if len(fired) == 1:
            alone |= fired
    assert _ALL_CHECKS - alone == set(), (
        f"no mutant reaches these checks on their own, so nothing shows they "
        f"can be the check that fires: {sorted(_ALL_CHECKS - alone)}"
    )


def _mutated_corpora() -> list[dict[str, str]]:
    docs = _corpus()
    return [docs] + [_mutate(docs, path, edits) for _, path, edits in _MUTANTS]


def test_no_placement_phrasing_is_decorative() -> None:
    """Every accepted phrasing must be one some mutant actually trips.

    :func:`test_no_check_is_decorative` works at check-id granularity, which is
    too coarse to see an unused alternative *inside* a check: ``placement-claim``
    fired on a mutant while its second pattern had never matched anything, on
    the committed tree or in the table. An accepted phrasing nothing exercises
    is an untested claim about English wearing a passing test's clothes.
    """
    seen = [False] * len(_PLACEMENT_RES)
    for corpus in _mutated_corpora():
        for i, pattern in enumerate(_PLACEMENT_RES):
            if any(pattern.findall(_flatten(text)) for text in corpus.values()):
                seen[i] = True
    assert all(seen), (
        f"these placement phrasings are matched by nothing, so nothing shows "
        f"they work: {[p.pattern for p, ok in zip(_PLACEMENT_RES, seen) if not ok]}"
    )


def test_every_accepted_count_noun_is_exercised() -> None:
    """Same rule for the count nouns: an unreached alternation is not coverage."""
    seen = {noun: False for noun in _COUNT_NOUNS}
    for corpus in _mutated_corpora():
        for text in corpus.values():
            for match in _COUNT_RE.finditer(_flatten(strip_regions(text))):
                for noun in _COUNT_NOUNS:
                    if noun[:-1] in match.group(0).lower():
                        seen[noun] = True
    assert all(seen.values()), (
        f"no mutant states a count against these nouns, so the check has never "
        f"been observed to read one: {sorted(n for n, ok in seen.items() if not ok)}"
    )


def test_a_new_section_in_the_rail_reds_the_documents() -> None:
    """Add a section and every surface that describes the rail goes red.

    This is the whole issue in one assertion: #613 added ``Portfolio`` and five
    documents went on describing two sections with nothing to stop them. The
    mutation is applied to ``nav.ts`` and **no document is touched**, so what
    reds is precisely the drift.
    """
    nav = nav_source()
    anchor = '  {\n    label: "Platform & Governance",'
    assert nav.count(anchor) == 1, "the nav anchor drifted"
    mutated = nav.replace(
        anchor,
        '  {\n    label: "Risk",\n    items: [\n'
        '      { title: "Limits", href: "/limits", icon: Scale },\n'
        "    ],\n  },\n" + anchor,
        1,
    )
    assert len(parse_groups(mutated)) == 4, "the mutant did not add a section"
    fired = _check_ids(_violations(_corpus(), mutated))
    for check in ("region-drift", "named-groups", "count-claim", "deck-cards"):
        assert check in fired, (
            f"a fourth section left {check!r} silent — a document may describe "
            f"a rail that has grown without anything going red"
        )


def test_the_placement_claim_survives_a_rewrap() -> None:
    """The guard reads the sentence, not the bytes.

    #602's ban was blind to a claim broken by ``{" "}``, a tag and two
    newlines. The same claim here is broken by a ``*`` comment leader. Re-wrap
    it at a different column and the check must still see it — otherwise the
    guard passes or fails on where a formatter happened to break the line.
    """
    docs = _corpus()
    original = docs[_DUE_DILIGENCE]
    rewrapped = original.replace(
        ' * page sits in the sidebar\'s\n * "Portfolio" group and shares',
        ' * page sits in the\n * sidebar\'s "Platform & Governance"\n * group and shares',
        1,
    )
    assert rewrapped != original, "the wrap anchor drifted off the page"
    fired = _check_ids(_violations({**docs, _DUE_DILIGENCE: rewrapped}, nav_source()))
    assert "placement-claim" in fired, (
        "a claim wrapped across comment lines was invisible to the guard — the "
        "exact failure mode #602 recorded, one layer out"
    )


def test_the_block_is_derived_from_the_rail_not_hard_coded() -> None:
    """Feed a different rail and every figure in the block moves.

    Without this, :func:`render` could return today's correct string as a
    literal and every assertion above would still pass — the grouping would be
    transcribed once more, one layer further in.
    """
    fake = """
export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Only Section",
    items: [
      { title: "Alpha", href: "/alpha", icon: Boxes },
      { title: "Beta", href: "/beta", icon: Scale },
    ],
  },
];
"""
    out = render(parse_groups(fake))
    assert "one sections" in out  # the count word is derived, not written
    assert "**Only Section** — Alpha · Beta" in out
    assert "Portfolio" not in out and "Deal Analytics" not in out
    assert group_for_route("/alpha", parse_groups(fake)) == "Only Section"
    assert group_for_route("/nowhere", parse_groups(fake)) is None


def test_the_checker_reds_on_a_hand_edited_region(tmp_path: Path) -> None:
    """A grouping corrected by hand is caught — the `red-when` for the whole fix.

    Hand-correcting a published grouping is the behaviour this issue exists to
    stop, so the checker has to fail on exactly that edit and not merely on a
    missing file.
    """
    doctored = tmp_path / "README.md"
    doctored.write_text(
        _read("README.md").replace("- **Portfolio** —", "- **Holdings** —"),
        encoding="utf-8",
    )
    assert main(["--check", str(doctored)]) == 1


def test_the_checker_reds_on_a_deleted_region(tmp_path: Path) -> None:
    """Deleting the region fails loudly, rather than passing by having nothing to check.

    #568: a guard scoped to a slice passes vacuously once the slice is empty.
    ``_region`` returns ``None`` for "no fences" and ``""`` only for a genuinely
    empty one; both are failures.
    """
    gutted = tmp_path / "quickstart.md"
    gutted.write_text(strip_regions(_read("docs/quickstart.md")), encoding="utf-8")
    assert main(["--check", str(gutted)]) == 1


def test_the_checker_passes_on_the_committed_documents() -> None:
    """`--check` is the command the PR body advertises; it must be green here.

    A checker that cannot pass is as useless as one that cannot fail — and this
    is the run that proves the two red cases above are about the mutation
    rather than about the checker being broken.
    """
    assert main(["--check"]) == 0
