"""What the book surface must render — and cannot undo (#573).

``web/`` has no JS test runner, so this asserts the components' **source**, not
their rendered output. That is the trade
``tests/test_capability_matrix.py::test_the_user_facing_no_tape_card_does_not_carry_the_retracted_claim``
already makes to reach ``page-states.tsx``; it is stated here rather than
implied, because a source guard proves the code *says* a thing, never that a
browser paints it. This repo also has no ``.github/``, so nothing runs it on the
PR — it is a local gate, and its value is entirely in being run.

Two properties are guarded, and both are the point of #573 rather than
decoration on it:

* **Every position says what it is.** #484 committed synthetic pools correctly
  labelled *in the data* whose Pool and Waterfall pages render no badge at all,
  so a viewer sees generated collateral presented exactly like real collateral.
  A qualifier per *screen* is not the property — a book-level caveat says
  nothing about the row a reader is looking at.
* **A refusal renders at a value's weight** (#549). A cell that could not
  resolve says so where the figure would have been and prints its cause; a
  blank in a numeric column is read as zero. On the committed book every
  position's coupon refuses, so this is the common case here, not the edge one.

Guarding by mutation, not by care
---------------------------------
Three siblings shipped a guard that was itself unguarded: #575's auth guard
passed a clickable "Generate access token"; #568's score ban passed a rendered
"1 of 7 verified"; and #565's first guard had two holes its own sweep caught —
a mutant that demoted the headline out of its ``<Badge>`` into body prose
survived because the identifier stayed greppable file-wide, and one hiding a
block behind ``{false ? … : null}`` would have. So every rule here is scored
against :data:`_MUTANTS`, and :func:`test_no_check_is_decorative` reds if any
rule is never the one that catches something.

**The blind spot, stated:** a ban list is only as good as the affordances it
enumerates. Nothing here would catch a surface that renders every required
token off-screen, or one whose badge is styled invisible — a source guard
cannot see paint. What it can see is whether the code still *says* the thing.

The ``_code_only`` / ``_region`` / ``_MUTANTS`` scaffold is copied from
``tests/test_concentration_page.py`` (#565), which copied ``_code_only`` from
``tests/test_mcp_page.py`` (#471). It is duplicated rather than factored out
because that is the house shape; each copy cites where it came from.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SHEET = _REPO_ROOT / "web" / "components" / "evidence-pack-sheet.tsx"
_PAGE = _REPO_ROOT / "web" / "app" / "(routes)" / "book" / "page.tsx"
_NAV = _REPO_ROOT / "web" / "lib" / "nav.ts"
_API = _REPO_ROOT / "web" / "lib" / "api.ts"

#: Opening line of the region this issue added to the shared provenance sheet.
#: Every region-scoped ban below is sliced from here, so its absence would make
#: them all scan an empty string and pass — hence the vacuity assert in
#: :func:`_region`, which :func:`test_the_region_marker_is_what_scopes_every_ban`
#: proves actually fires.
_REGION_MARKER = "Holdings book (#573"


def _code_only(source: str) -> str:
    """Strip comments so a sentence *describing* a rule does not trip its ban.

    The shape ``tests/test_mcp_page.py`` uses, for the same reason (#471): this
    region's own comments explain that a label must not be decided by a ternary
    and that a refusal must not keep its value, and a ban reading those comments
    as code would fire on the documentation of the rule it enforces. Strings are
    deliberately NOT stripped — a word rendered inside a JSX string is exactly
    what a reader sees.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return "\n".join(
        line
        for line in source.splitlines()
        if not line.lstrip().startswith(("//", "*"))
    )


def _region(sheet: str) -> str:
    """The holdings-book region of the sheet, marker to end of file.

    Deliberately a superset — a ban that over-reaches fails loudly, one that
    under-reaches passes silently.
    """
    assert _REGION_MARKER in sheet, (
        "the holdings-book region marker is gone from evidence-pack-sheet.tsx; "
        "every check below would scan an empty string and pass vacuously"
    )
    return _code_only(sheet[sheet.index(_REGION_MARKER) :])


def _sources() -> dict[str, str]:
    """The four real files, as committed."""
    return {
        "sheet": _SHEET.read_text(encoding="utf-8"),
        "page": _PAGE.read_text(encoding="utf-8"),
        "nav": _NAV.read_text(encoding="utf-8"),
        "api": _API.read_text(encoding="utf-8"),
    }


#: One rendered ``<Badge>``. Sliced rather than searched file-wide because
#: "the label appears somewhere in the file" is what #565's first pass checked,
#: and a mutant that moved it out of the badge into a paragraph passed it.
#: ``\b`` so ``<BadgeGroup`` does not match; non-greedy with ``re.S`` so a
#: multi-line badge is captured whole and two adjacent badges do not merge.
_BADGE = re.compile(r"<Badge\b.*?</Badge>", re.S)

#: A condition that cannot vary. ``{false ? (…) : null}`` deletes a block while
#: leaving every identifier in it greppable, so a guard that only looks for the
#: identifiers reads the deleted block as rendered.
_LITERAL_CONDITION = re.compile(r"\{\s*(?:true|false)\s*\?")

#: A provenance label decided by a conditional rather than read from the total
#: table. This is the exact defect still live in ``PackBody`` above the region
#: (``src === "deeploans" ? "deeploans" : "direct"``), which labels a derived or
#: synthetic tape as a direct ingestion. #565 left that one alone deliberately;
#: this ban keeps the new region from growing its own copy of it.
_PROVENANCE_CONDITIONAL = re.compile(r"provenance\s*(?:===|!==|\?)")

#: Ways to coalesce a refused value into something printable. Each turns "the
#: platform could not resolve this" back into a figure or a dash a reader scans
#: past — the #549 failure, one layer down from the one the type system now
#: forbids.
_COALESCE_SHAPES = ("?? 0", '?? "', "?? '", "|| 0", "Number(fact.value)")

#: Ways to render a required element and still keep it off the screen. The
#: #575/#568 family: the affordance is present, greppable and unreachable. A
#: source guard cannot see paint (see the module docstring), but it can refuse
#: the utility classes and elements whose whole purpose is to hide something.
_HIDING_AFFORDANCES = ("sr-only", "<details", "line-clamp", "truncate", "opacity-0")

#: ``hidden`` as its own class or JSX attribute — NOT as the tail of
#: ``overflow-hidden``, which is ordinary layout CSS. The bare substring was in
#: the first version of this list and flagged ``overflow-hidden rounded-lg``;
#: a ban that reds a legitimate edit teaches the next worker to weaken it.
_HIDDEN_ATTR = re.compile(r"(?<![-\w])hidden\b")

#: Shapes that silently drop a row or a field. A ``.find()`` that misses renders
#: an empty cell, and an empty cell and a refused one must not read alike
#: (#572/#494); ``.slice()`` and ``[0]`` narrow a census to its first element,
#: which is how a header derived from one row hides every other row's fields.
_NARROWING_SHAPES = (
    ".find(",
    ".filter(",
    ".reduce(",
    ".slice(",
    "positions[0]",
    "facts[0]",
)


def _label_table(region: str) -> str:
    """The POSITION_PROVENANCE_LABELS literal, opening line to its closing brace.

    Sliced because "the region mentions ILLUSTRATIVE somewhere" is not the
    property — ``BookDisclosure``'s own badge says it too, so a file-wide search
    would report a label table gutted to ``illustrative: "Position"`` as fine.
    The same demotion hole #565 found, one level in.
    """
    marker = "const POSITION_PROVENANCE_LABELS"
    if marker not in region:
        return ""
    start = region.index(marker)
    end = region.find("};", start)
    return region[start:] if end == -1 else region[start : end + 2]


#: The start of the next top-level declaration — where one component ends.
_NEXT_DECL = re.compile(r"\n(?:export )?(?:function|const|type|interface) ")


def _component(region: str, name: str) -> str:
    """One component's source, its declaration to the next top-level one.

    **Bounded, not run to end of file.** The first version of this helper
    sliced ``PositionFactCell`` to EOF, which is the same source as the
    component only because that component happens to be last. Append anything
    after it and that sibling's ``{fact.reason}`` counts toward the refusal
    cell's rule, satisfying it on behalf of a cell that stopped saying it —
    #565's demotion hole with a different lever, the token still countable
    while the component goes quiet. ``satisfy-the-refusal-rule-from-a-decoy``
    in :data:`_MUTANTS` is the mutant that proves the bound is load-bearing.

    Returns an empty string when the component is gone; ``extends-the-sheet``
    is the rule that reports that, so this never raises and never turns a
    violation into a collection error.
    """
    marker = f"function {name}("
    if marker not in region:
        return ""
    body = region[region.index(marker) + len(marker) :]
    end = _NEXT_DECL.search(body)
    return body[: end.start()] if end else body


def _violations(*, region: str, page: str, nav: str, api: str) -> list[str]:
    """Every rule this surface must satisfy, as ``"<check-id>: <why>"`` lines.

    Returning the failures rather than asserting them is what lets
    :data:`_MUTANTS` run the same rules against a rewritten source.
    """
    out: list[str] = []
    where = {"region": region, "page": page, "nav": nav, "api": api}

    def need(check: str, token: str, target: str, why: str) -> None:
        if token not in where[target]:
            out.append(f"{check}: {target} no longer carries {token!r} — {why}")

    # Each rule scores the badges of the component it is a rule ABOUT. The
    # first version joined every badge in the region, so `BookDisclosure`'s
    # badge could satisfy a rule about `PositionProvenanceBadge`.
    provenance_badge = _component(region, "PositionProvenanceBadge")
    disclosure = _component(region, "BookDisclosure")
    cell = _component(region, "PositionFactCell")

    # 1. The components live in the shared sheet, not a forked parallel file.
    #    "Extend the existing vocabulary" is only true while there is one place
    #    a reader learns to look.
    for symbol in (
        "export function PositionProvenanceBadge",
        "export function BookDisclosure",
        "export function PositionFactCell",
    ):
        need("extends-the-sheet", symbol, "region", "it must live in the sheet")
    need(
        "extends-the-sheet",
        'from "@/components/evidence-pack-sheet"',
        "page",
        "the page must import the shared components, not fork them",
    )

    # 2. The label table is TOTAL, like DATA_SOURCE_LABELS beside it: widening
    #    PositionProvenance is a compile error until the table answers for the
    #    new member. Partial<> re-opens exactly the hole it closes.
    need(
        "provenance-labels-total",
        "Record<PositionProvenance, string>",
        "region",
        "a non-total table answers for a new member by omission",
    )
    if "Partial<Record<PositionProvenance" in region:
        out.append(
            "provenance-labels-total: the label table is Partial<>, so a "
            "provenance kind can go unlabelled without a compile error"
        )

    # 2b. …and the label a total table returns actually says the thing. A table
    #     that is total, whose value renders in a Badge, and which reads
    #     "Position" has satisfied every structural rule and told the reader
    #     nothing. The wording mirrors DATA_SOURCE_LABELS' own
    #     "SYNTHETIC — generated, describes no real obligor".
    table = _label_table(region)
    for token in ("ILLUSTRATIVE", "nobody holds"):
        if token not in table:
            out.append(
                f"illustrative-label-says-what-it-is: the label table no longer "
                f"says {token!r}; a badge that names no consequence is a badge "
                f"a reader learns to ignore"
            )

    # 3. …and no conditional decides a label behind its back.
    if _PROVENANCE_CONDITIONAL.search(region):
        out.append(
            "no-provenance-ternary: a conditional decides a provenance label; "
            "that is how PackBody still calls a derived tape a direct ingestion"
        )

    # 4. The label renders inside a <Badge>, sliced — not merely somewhere in
    #    the file. #565's own caught hole: a mutant that demoted the headline
    #    into body prose left every identifier greppable and survived.
    if "POSITION_PROVENANCE_LABELS[provenance]" not in "\n".join(
        _BADGE.findall(provenance_badge)
    ):
        out.append(
            "badge-is-a-badge: the provenance label renders in no <Badge>; a "
            "qualifier a reader has to find in prose has lost to the figure"
        )

    # 5. Per row, not once per screen — #484's failure mode exactly.
    if "book.positions.map(" in page:
        row = page[page.index("book.positions.map(") :]
        if "<PositionProvenanceBadge" not in row:
            out.append(
                "provenance-badge-per-row: no badge renders inside the position "
                "row; a book-level caveat says nothing about the row a reader "
                "is looking at"
            )
    else:
        out.append(
            "provenance-badge-per-row: the page no longer maps book.positions, "
            "so there is no row for a per-position badge to render in"
        )

    # 6. A refusal states its cause, in both branches — the reason is body text
    #    in each, so the count is two. One means a branch renders a bare state.
    if cell.count("{fact.reason}") < 2:
        out.append(
            "refusal-renders-its-reason: a branch of PositionFactCell renders "
            "no {fact.reason}; a state without its cause is a blank with a "
            "label on it"
        )

    # 7. …at the same weight as a value: in a Badge, in the figure's slot.
    if "Not resolved" not in "\n".join(_BADGE.findall(cell)):
        out.append(
            "refusal-is-prominent: the refusal renders in no <Badge>; #549 — a "
            "refusal a reader scans past is one the figures beside it outran"
        )

    # 8. Nothing coalesces a refused value back into something printable.
    both = f"{region}\n{page}"
    for shape in _COALESCE_SHAPES:
        if shape in both:
            out.append(
                f"no-value-coalescing: {shape!r} turns a refusal back into a "
                f"printable figure; suppress the value and state the cause"
            )

    # 9. fact.value is reachable in the `ran` branch only — once in the cell.
    if cell.count("fact.value") != 1:
        out.append(
            "value-in-ran-branch-only: fact.value is read somewhere other than "
            "the single `ran` branch; a refusal that keeps its value is not one"
        )

    # 10. Every distinct disclosure renders, quoted from the API. A book with
    #     two kinds of holding in it makes two different claims.
    if "book.disclosures.map(" not in disclosure:
        out.append(
            "disclosure-renders-every: BookDisclosure no longer maps "
            "book.disclosures; every disclosure must render, not the first"
        )
    if "disclosures[0]" in disclosure:
        out.append(
            "disclosure-renders-every: only the first disclosure renders; a "
            "mixed book's second claim would never reach the screen"
        )

    # 11. …above the positions it qualifies. Both asserted explicitly: an
    #     order-only check passes when the section is deleted and index raises.
    need("disclosure-first", "<BookDisclosure", "page", "it must render")
    if "<BookDisclosure" in page and "<Table" in page:
        if page.index("<BookDisclosure") > page.index("<Table"):
            out.append(
                "disclosure-first: the disclosure renders below the positions; "
                "a size read before its qualifier is read as an exposure"
            )

    # 12. No shape that drops a row or a field on the way to the screen.
    for shape in _NARROWING_SHAPES:
        if shape in page:
            out.append(
                f"no-narrowing-shape: the page uses {shape!r}; a dropped field "
                f"and a refused one render alike (#572/#494)"
            )

    # 12b. Nothing required is rendered into a hiding place.
    found_hiding = [shape for shape in _HIDING_AFFORDANCES if shape in both]
    if _HIDDEN_ATTR.search(both):
        found_hiding.append("hidden")
    for shape in found_hiding:
        out.append(
            f"no-hiding-affordance: {shape!r} renders a required element out "
            f"of sight; present-and-unreachable is how #575 and #568 both "
            f"passed while violated"
        )

    # 13. No block deleted behind a condition that cannot vary.
    if _LITERAL_CONDITION.search(both):
        out.append(
            "no-literal-condition: a block renders behind a literal true/false; "
            "its identifiers stay greppable while nothing reaches the screen"
        )

    # 14. The three states web/CONTRACT.md requires of every data page.
    for token in ('"use client"', "<LoadingState", "<ErrorState", "<BookContent"):
        need("page-three-states", token, "page", "web/CONTRACT.md requires it")

    # 15. The type keeps the refusal honest, so no renderer has to. Collapsing
    #     the union back to one nullable field is what makes `?? 0` typecheck.
    need(
        "refusal-carries-no-value-by-type",
        "export type PositionField = PositionFieldRan | PositionFieldRefused;",
        "api",
        "a discriminated union is what makes a valued refusal not compile",
    )
    need(
        "refusal-carries-no-value-by-type",
        "value: null;",
        "api",
        "the refusing half of the union must carry no value",
    )

    # 16. The nav reaches it. A screen nothing links to is one nobody sees.
    need("nav-entry", 'href: "/book"', "nav", "the book must be reachable")

    return out


def _check_ids(violations: list[str]) -> set[str]:
    return {v.split(":", 1)[0] for v in violations}


#: Every check id :func:`_violations` can emit. Kept as data so
#: :func:`test_no_check_is_decorative` can require a mutant for each.
_ALL_CHECKS = {
    "extends-the-sheet",
    "provenance-labels-total",
    "no-provenance-ternary",
    "illustrative-label-says-what-it-is",
    "badge-is-a-badge",
    "provenance-badge-per-row",
    "refusal-renders-its-reason",
    "refusal-is-prominent",
    "no-value-coalescing",
    "value-in-ran-branch-only",
    "disclosure-renders-every",
    "disclosure-first",
    "no-narrowing-shape",
    "no-hiding-affordance",
    "no-literal-condition",
    "page-three-states",
    "refusal-carries-no-value-by-type",
    "nav-entry",
}

#: ``(id, target, [(old, new), ...])``. Each rewrite is applied to the REAL
#: source, so a mutant whose ``old`` no longer occurs fails as stale rather than
#: silently passing — a mutant table drifted off the code is the same false
#: comfort as a ban nobody tested. Every entry is a way this surface could
#: mislead a reader while still looking finished.
_MUTANTS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "fork-the-components-into-a-parallel-file",
        "page",
        [('from "@/components/evidence-pack-sheet"', 'from "@/components/book-provenance"')],
    ),
    (
        "make-the-label-table-partial",
        "region",
        [
            (
                "const POSITION_PROVENANCE_LABELS: Record<PositionProvenance, string> = {",
                "const POSITION_PROVENANCE_LABELS: Partial<Record<PositionProvenance, string>> = {",
            )
        ],
    ),
    (
        "gut-the-illustrative-label",
        "region",
        [
            (
                '  illustrative: "ILLUSTRATIVE — generated, nobody holds this",',
                '  illustrative: "Position",',
            )
        ],
    ),
    (
        "hide-the-provenance-badge-from-sighted-readers",
        "region",
        [
            (
                '      variant={describesARealHolding ? "secondary" : "destructive"}\n'
                '      className="font-normal"',
                '      variant={describesARealHolding ? "secondary" : "destructive"}\n'
                '      className="font-normal sr-only"',
            )
        ],
    ),
    (
        "decide-the-label-with-a-ternary",
        "region",
        [
            (
                "      {POSITION_PROVENANCE_LABELS[provenance]}",
                '      {provenance === "illustrative" ? "Illustrative" : "Client-stated"}',
            )
        ],
    ),
    (
        "demote-the-label-out-of-its-badge",
        "region",
        [
            (
                '      <ShieldCheck className="mr-1 size-3" />\n'
                "      {POSITION_PROVENANCE_LABELS[provenance]}\n"
                "    </Badge>",
                '      <ShieldCheck className="mr-1 size-3" />\n'
                "    </Badge>\n"
                "    <span>{POSITION_PROVENANCE_LABELS[provenance]}</span>",
            )
        ],
    ),
    (
        "badge-the-book-instead-of-the-rows",
        "page",
        [
            (
                "                        <PositionProvenanceBadge\n"
                "                          provenance={position.provenance}",
                "                        <span\n"
                "                          data-provenance={position.provenance}",
            )
        ],
    ),
    (
        "drop-the-refused-cells-reason",
        "region",
        [
            (
                "        Not resolved\n"
                "      </Badge>\n"
                '      <p className="text-xs leading-snug text-muted-foreground">\n'
                "        {fact.reason}\n"
                "      </p>",
                "        Not resolved\n      </Badge>",
            )
        ],
    ),
    (
        "satisfy-the-refusal-rule-from-a-decoy",
        "region",
        [
            (
                '      <p className="text-xs leading-snug text-muted-foreground">\n'
                "        {fact.reason}\n"
                "      </p>\n"
                "    </div>\n"
                "  );\n"
                "}",
                '      <p className="text-xs leading-snug text-muted-foreground">\n'
                "      </p>\n"
                "    </div>\n"
                "  );\n"
                "}\n"
                "\n"
                "export function DecoyCell({ fact }: { fact: PositionField }) {\n"
                "  return <p>{fact.reason}</p>;\n"
                "}",
            )
        ],
    ),
    (
        "demote-the-refusal-out-of-its-badge",
        "region",
        [
            (
                '      <Badge variant="destructive" className="font-normal">\n'
                "        Not resolved\n"
                "      </Badge>",
                "      <span>Not resolved</span>",
            )
        ],
    ),
    (
        "coalesce-the-refused-value-into-a-dash",
        "region",
        [
            (
                '      <Badge variant="destructive" className="font-normal">\n'
                "        Not resolved",
                '      <span>{fact.value ?? "—"}</span>\n'
                '      <Badge variant="destructive" className="font-normal">\n'
                "        Not resolved",
            )
        ],
    ),
    (
        "render-the-refused-value-beside-the-refusal",
        "region",
        [
            (
                '      <Badge variant="destructive" className="font-normal">\n'
                "        Not resolved",
                "      <span>{fact.value}</span>\n"
                '      <Badge variant="destructive" className="font-normal">\n'
                "        Not resolved",
            )
        ],
    ),
    (
        "render-only-the-first-disclosure",
        "region",
        [
            (
                "      {book.disclosures.map((disclosure) => (",
                "      {[book.disclosures[0]].map((disclosure) => (",
            )
        ],
    ),
    (
        "put-the-disclosure-below-the-positions",
        "page",
        [
            ("      <BookDisclosure book={book} />\n\n      <Card>", "      <Card>"),
            (
                "      </Card>\n    </div>\n  );\n}",
                "      </Card>\n      <BookDisclosure book={book} />\n    </div>\n  );\n}",
            ),
        ],
    ),
    (
        "derive-the-columns-from-the-first-position",
        "page",
        [
            (
                "  for (const position of book.positions) {",
                "  for (const position of book.positions.slice(0, 1)) {",
            )
        ],
    ),
    (
        "look-each-fact-up-by-name",
        "page",
        [
            (
                "                    {position.facts.map((fact: PositionField) => (",
                "                    {fields.map((n) => position.facts.find((f) => f.field === n)).map((fact: PositionField) => (",
            )
        ],
    ),
    (
        "hide-the-disclosure-behind-a-dead-branch",
        "page",
        [
            (
                "      <BookDisclosure book={book} />",
                "      {false ? <BookDisclosure book={book} /> : null}",
            )
        ],
    ),
    (
        "drop-the-error-state",
        "page",
        [
            (
                '        <ErrorState title="Could not load the book" message={error} />',
                "        <LoadingState />",
            )
        ],
    ),
    (
        "collapse-the-field-union-back-to-a-nullable-value",
        "api",
        [
            (
                "export type PositionField = PositionFieldRan | PositionFieldRefused;",
                "export type PositionField = {\n"
                "  field: string;\n"
                "  state: CapabilityCellState;\n"
                "  value: number | null;\n"
                "  reason: string;\n"
                "};",
            ),
            ("  value: null;\n", "  value: number | null;\n"),
        ],
    ),
    (
        "unlink-the-book-from-the-nav",
        "nav",
        [('      { title: "Book", href: "/book", icon: BookOpen },\n', "")],
    ),
]


def _mutate(
    sources: dict[str, str], target: str, edits: list[tuple[str, str]]
) -> dict[str, str]:
    """Apply *edits* to *target*, refusing a rewrite that no longer applies."""
    key = "sheet" if target == "region" else target
    mutated = dict(sources)
    for old, new in edits:
        hits = mutated[key].count(old)
        assert hits, (
            f"the mutant no longer applies to {key}: {old!r} is not in the "
            f"source. A stale mutant proves nothing — update it to the code "
            f"as written."
        )
        # Uniqueness is not tidiness. `evidence-pack-sheet.tsx` carries
        # `PackBody`'s badges above the region, and a shorter-indented anchor is
        # a *substring* of a deeper-indented line there — so an ambiguous anchor
        # silently rewrote code the region slice never scans, and two mutants
        # "survived" while testing nothing. An anchor that matches twice is not
        # a mutant, it is a coin flip.
        assert hits == 1, (
            f"the mutant is ambiguous in {key}: {old!r} matches {hits} times, "
            f"so it may rewrite a line no check scans. Anchor it on text "
            f"unique to the region under test."
        )
        mutated[key] = mutated[key].replace(old, new, 1)
    return mutated


def _run(sources: dict[str, str]) -> list[str]:
    return _violations(
        region=_region(sources["sheet"]),
        page=_code_only(sources["page"]),
        nav=sources["nav"],
        api=sources["api"],
    )


def test_the_surface_as_committed_passes_every_check() -> None:
    """The real files satisfy every rule. Without this the bans prove nothing."""
    found = _run(_sources())
    assert found == [], "\n".join(found)


def test_the_region_marker_is_what_scopes_every_ban() -> None:
    """The vacuity assert has to fire, or the region bans scan an empty string."""
    with pytest.raises(AssertionError, match="scan an empty string"):
        _region("a sheet with no holdings-book region in it")


def test_a_stale_or_ambiguous_mutant_is_refused() -> None:
    """Both `_mutate` guards must fire, or a mutant can test nothing silently.

    The ambiguity half is not hypothetical: two mutants here originally
    anchored on ``'      <Badge variant="destructive" …'``, which is a
    *substring* of a more deeply indented line in ``PackBody`` above the
    region. They rewrote that line instead, the region slice never saw the
    edit, and both "survived" — reported as a hole in the surface when the
    hole was in the harness.
    """
    sources = _sources()
    with pytest.raises(AssertionError, match="no longer applies"):
        _mutate(sources, "page", [("a string that is not in the page", "x")])
    with pytest.raises(AssertionError, match="ambiguous"):
        _mutate(sources, "region", [('className="font-normal"', "x")])


@pytest.mark.parametrize(
    "mutant_id,target,edits", _MUTANTS, ids=[m[0] for m in _MUTANTS]
)
def test_the_guard_reds_on_every_mutant(
    mutant_id: str, target: str, edits: list[tuple[str, str]]
) -> None:
    """Each mutant is a way this surface could mislead. None may pass."""
    found = _run(_mutate(_sources(), target, edits))
    assert found, (
        f"mutant {mutant_id!r} survived: the guard accepts a surface that "
        f"misleads. This is the #575/#568/#565 failure — the criterion passing "
        f"while violated."
    )


def test_no_check_is_decorative() -> None:
    """Every check must be the one that catches some mutant.

    A check no mutant reaches has never been observed to fail, which is the
    state all three sibling guards were in when they shipped. Adding a rule
    without a mutant for it reds here.
    """
    sources = _sources()
    fired: set[str] = set()
    for _, target, edits in _MUTANTS:
        fired |= _check_ids(_run(_mutate(sources, target, edits)))
    assert _ALL_CHECKS - fired == set(), (
        f"no mutant reaches these checks, so nothing shows they can fail: "
        f"{sorted(_ALL_CHECKS - fired)}"
    )
    assert fired - _ALL_CHECKS == set(), (
        f"a check emits an id _ALL_CHECKS does not list: "
        f"{sorted(fired - _ALL_CHECKS)}"
    )
