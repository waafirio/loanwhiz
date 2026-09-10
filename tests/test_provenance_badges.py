"""What every provenance badge must say about its source — and cannot unsay (#599).

``web/`` has no JS test runner, so this asserts the components' **source**, not
their rendered output. That is the trade
``tests/test_capability_matrix.py::test_the_user_facing_no_tape_card_does_not_carry_the_retracted_claim``
already makes to reach ``page-states.tsx``, and it is stated here rather than
implied: a source guard proves the code *says* a thing, never that a browser
painted it, never that a reader saw it. This repo also has no ``.github/``, so
nothing runs this on the PR — it is a local gate, and its value is entirely in
being run.

The property, in one sentence: **a provenance label is a total-table lookup on
every surface that renders one.**

The defect it closes was the same defect twice. ``DATA_SOURCE_LABELS`` replaced
a binary that called every non-``deeploans`` channel a "direct URL"; the badge
rendered directly above the sentence that table feeds kept its own binary —
``{src === "deeploans" ? "deeploans" : "direct"} ingestion`` — so a ``derived``
or ``synthetic`` tape was badged "direct ingestion" above a sentence reading
"SYNTHETIC — generated, describes no real obligor". Two claims about one
source, on one screen, disagreeing. Meanwhile #484 committed synthetic pools
labelled correctly *in the data* and the Pool and Waterfall pages rendered no
badge at all, which is the same lie told by omission.

So the rules below are not "the badge says something". They are:

* the badge's text **is** the sentence's string, not a second phrasing of it;
* no conditional anywhere decides what a channel is called;
* every table is total, so widening ``DataSource`` is a compile error;
* "we could not tell" is a **key** in those tables, never silence — rendering
  nothing where provenance is unknown reads, to a reader, exactly like an
  ordinary published tape;
* the marking is rendered from **inside the loop** that renders the rows it
  marks (#575), because one written beside a table survives only until someone
  adds a row.

Guarding by mutation, not by care
---------------------------------
Four siblings shipped a guard that was itself unguarded: #575's auth guard
passed a live "Generate access token" anchor; #568's score ban passed a
rendered "1 of 7 verified"; #565's first guard let a mutant demote its headline
count out of its ``<Badge>`` into body prose; and #573 found five holes before
merge, the important one a component slice running to end of file, so a sibling
component could satisfy a rule on behalf of the component under test. Every
rule here is therefore scored against :data:`_MUTANTS`, and
:func:`test_no_check_is_decorative` reds — in **both** directions — if a rule is
never the one that catches something, or emits an id the table does not list.

**Two slicing rules, because #568 and #573 pull opposite ways.** #568: take a
region to end-of-file, since an over-reaching region fails loudly and an
under-reaching one passes silently. #573: a component slice that runs to
end-of-file lets a *sibling* satisfy a positive rule on the component's behalf.
Both are right about different rule classes, so this file splits them:

* a **positive** rule about one component reads a **bounded** slice
  (:func:`_pack_body`, :func:`_row_loop`), whose end anchor must be *found* —
  an end that silently fell back to EOF is the #573 hole;
* a **ban** reads whole files, and is written narrowly enough (a quoted channel
  name inside a conditional) that over-reach cannot produce a false positive.

**The blind spot, stated.** A ban list is only as good as the affordances it
enumerates, and this one cannot see paint: nothing here would catch a badge
rendered off-screen, styled invisible, or covered by a sibling element. What it
can see is whether the code still *says* the thing, and whether the thing it
says can still disagree with itself.

The ``_code_only`` / ``_BADGE`` / ``_MUTANTS`` scaffold is copied from
``tests/test_concentration_page.py`` (#565), which copied ``_code_only`` from
``tests/test_mcp_page.py`` (#471); ``_mutate``'s ambiguity refusal is #573's.
It is duplicated rather than factored out because that is the house shape; each
copy cites where it came from.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MODULE = _REPO_ROOT / "web" / "components" / "provenance-badge.tsx"
_SHEET = _REPO_ROOT / "web" / "components" / "evidence-pack-sheet.tsx"
_POOL = _REPO_ROOT / "web" / "app" / "(routes)" / "pool" / "page.tsx"
_WATERFALL = _REPO_ROOT / "web" / "app" / "(routes)" / "waterfall" / "page.tsx"
_API = _REPO_ROOT / "web" / "lib" / "api.ts"


def _code_only(source: str) -> str:
    """Strip comments so a sentence *describing* a rule does not trip its ban.

    The shape ``tests/test_mcp_page.py`` uses, for the same reason (#471), and
    the #575 gotcha this file would hit hardest: the module's own header quotes
    the banned ternary verbatim in order to explain why it is banned, and a ban
    reading that comment as code would fire on the documentation of the rule it
    enforces. Strings are deliberately NOT stripped — a channel name rendered
    inside a JSX string is exactly what a reader sees.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return "\n".join(
        line
        for line in source.splitlines()
        if not line.lstrip().startswith(("//", "*"))
    )


def _sources() -> dict[str, str]:
    """The five real files, as committed."""
    return {
        "module": _MODULE.read_text(encoding="utf-8"),
        "sheet": _SHEET.read_text(encoding="utf-8"),
        "pool": _POOL.read_text(encoding="utf-8"),
        "waterfall": _WATERFALL.read_text(encoding="utf-8"),
        "api": _API.read_text(encoding="utf-8"),
    }


# ---------------------------------------------------------------------------
# Bounded slices — for the positive rules only (see the module docstring)
# ---------------------------------------------------------------------------

_PACK_START = "export function PackBody({"
#: The next top-level declaration after `PackBody`. Bounded, not to-EOF: every
#: positive rule below asks whether *PackBody* renders something, and a slice
#: running past its closing brace would let `ToolCall` or `CitationItem` answer
#: for it — #573's hole, found before merge only because a mutant reached it.
_TOP_LEVEL = re.compile(r"^(?:export )?function |^/\*\*|^// ---", re.M)


def _pack_body(sheet: str) -> str:
    """`PackBody`'s own body, start marker to the next top-level declaration."""
    assert _PACK_START in sheet, (
        "PackBody is gone from evidence-pack-sheet.tsx; every pack check below "
        "would scan an empty string and pass vacuously"
    )
    start = sheet.index(_PACK_START)
    end_match = _TOP_LEVEL.search(sheet, start + len(_PACK_START))
    assert end_match is not None, (
        "no top-level declaration follows PackBody, so this slice ran to end of "
        "file; a sibling component could then satisfy a PackBody rule on its "
        "behalf (#573)"
    )
    return _code_only(sheet[start : end_match.start()])


#: Anchored on the table's NAME, never on its type annotation: a mutant that
#: rewrites `Record<DataSource, string>` must still be *scanned* and reported,
#: not make the slice vanish and crash the run. A slicer whose marker is the
#: thing a rule tests cannot report on that thing.
_TABLE_START = "export const DATA_SOURCE_LABELS"


def _label_table(module: str) -> str:
    """The label table's own literal, opening brace to its closing ``};``.

    Bounded, and the third instance of the #573 hole this file found in itself:
    the first draft asked whether ``  derived: `` appeared *anywhere* in the
    module, and the variant table — which also keys on every channel — answered
    on the label table's behalf. Deleting the real label passed. A rule about
    one table must read that table.
    """
    assert _TABLE_START in module, (
        "the label table is gone from provenance-badge.tsx; the per-channel "
        "check would scan an empty string and pass vacuously"
    )
    start = module.index(_TABLE_START)
    end = module.find("\n};", start)
    assert end != -1, (
        "the label table literal is not closed, so this slice ran to end of "
        "file; the variant table could then answer for it (#573)"
    )
    return module[start:end]


_ROW_START = "{pagination.pageItems.map((p) => ("
_ROW_END = "</TableBody>"


def _row_loop(pool: str) -> str:
    """The Pool table's per-period row loop — the loop that renders the rows.

    Bounded for the same reason as :func:`_pack_body`: the rule this feeds is
    "every period is marked", and a slice reaching past the loop would be
    satisfied by the page-level badge row above it — which is precisely the
    "one caveat per screen" that #484 already had and #575 rejected.
    """
    assert _ROW_START in pool, (
        "the per-period row loop is gone from the pool page; the per-row check "
        "would scan an empty string and pass vacuously"
    )
    start = pool.index(_ROW_START)
    end = pool.find(_ROW_END, start)
    assert end != -1, (
        "the row loop has no </TableBody> after it, so this slice ran to end of "
        "file; the page-level badge could then satisfy the per-row rule (#573)"
    )
    return _code_only(pool[start:end])


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

#: One rendered ``<Badge>``. Sliced rather than searched file-wide because "the
#: label appears somewhere in the file" is what #565's first pass checked, and a
#: mutant that moved it out of the badge into a paragraph passed it. ``\b`` so
#: ``<BadgeGroup`` does not match; non-greedy with ``re.S`` so a multi-line
#: badge is captured whole and two adjacent badges do not merge.
_BADGE = re.compile(r"<Badge\b.*?</Badge>", re.S)

_CHANNELS = ("deeploans", "direct", "derived", "synthetic")
_CHANNEL_ALT = "|".join(_CHANNELS)

#: A provenance label decided by a conditional rather than read from a total
#: table. Deliberately narrow — a *quoted channel name* inside a conditional —
#: so it can be run over whole files without a false positive, which is what
#: lets it catch a ternary moved out of the component under test into a helper
#: above it. The first alternative is the original defect
#: (``src === "deeploans" ? …``); the second is its branches
#: (``? "direct" : "deeploans"``).
_PROVENANCE_TERNARY = (
    re.compile(rf'"(?:{_CHANNEL_ALT})"[^\n]{{0,40}}\?[^\n]*:'),
    re.compile(rf'\?[^\n?]{{0,40}}"(?:{_CHANNEL_ALT})"[^\n]{{0,40}}:'),
)

#: A condition that cannot vary. ``{false ? (…) : null}`` deletes a rendered
#: block while leaving every identifier in it greppable, so a guard that only
#: looks for the identifiers reads the deleted block as rendered — #565's own
#: caught hole, and the mutant that survived this file's first sweep.
_LITERAL_CONDITION = re.compile(r"\{\s*(?:true|false)\s*\?")

#: A marking rendered where nobody sees it. #573 found this exact hole in its
#: own guard before merge: a required element moved into ``sr-only`` satisfies
#: every text rule and shows a reader nothing. Scoped to the module and the row
#: loop — the two places a provenance marking actually renders — because
#: ``sr-only`` is legitimate elsewhere on these pages.
_HIDDEN_MARKING = re.compile(r"sr-only|aria-hidden|hidden=|display:\s*none")

#: An unknown channel given a real one as its default. ``?? "direct"`` is the
#: single most plausible regression here: it type-checks, reads as a harmless
#: fallback, and reinstates exactly the claim #599 removed.
_DEFAULTED_CHANNEL = re.compile(rf'(?:\?\?|\|\|)\s*"(?:{_CHANNEL_ALT})"')

#: The quoted members of a TS string-union alias or a string-array literal.
_QUOTED = re.compile(r'"([a-z_]+)"')


def _union_members(api: str) -> set[str] | None:
    """The union's members, or ``None`` when the declaration is unreadable.

    ``None`` rather than an assert, because these two feed a *rule* rather than
    scope a slice: an unreadable declaration is a real finding about the code
    under test, and a guard that raises where it could report turns a mutant
    into an error — neither a pass nor a catch. The vacuity asserts above are
    the opposite case and stay asserts: an empty slice is a finding about the
    guard, not about the code.
    """
    m = re.search(r"export type DataSource =([^;]+);", api)
    return None if m is None else set(_QUOTED.findall(m.group(1)))


def _enumerated_members(api: str) -> set[str] | None:
    m = re.search(
        r"export const DATA_SOURCES: readonly DataSource\[\] = \[([^\]]*)\]", api
    )
    return None if m is None else set(_QUOTED.findall(m.group(1)))


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def _violations(sources: dict[str, str]) -> list[str]:
    """Every way these four surfaces could misname a source. Ids are stable."""
    out: list[str] = []

    module = _code_only(sources["module"])
    pack = _pack_body(sources["sheet"])
    pool = _code_only(sources["pool"])
    rows = _row_loop(sources["pool"])
    waterfall = _code_only(sources["waterfall"])
    api = sources["api"]

    # --- The vocabulary itself -------------------------------------------
    # 1. Total, so widening the union is a compile error until the table
    #    answers. A `Record<string, …>` accepts a new member silently, which is
    #    the property this whole module exists to keep.
    if "DATA_SOURCE_LABELS: Record<DataSource, string>" not in module:
        out.append(
            "label-table-is-total: DATA_SOURCE_LABELS is no longer keyed by "
            "DataSource; widening the union stops being a compile error"
        )

    # 2. …and every channel is actually in it.
    table = _label_table(sources["module"])
    for channel in _CHANNELS:
        if f"  {channel}: " not in table:
            out.append(
                f"every-channel-is-named: the label table has no {channel!r} "
                f"entry; an unnamed channel is one a surface renders blank"
            )
            break

    # 3. …and the one that matters says what it costs a reader. A table that is
    #    total, renders in a Badge, and says "synthetic" has satisfied every
    #    structural rule and told nobody anything.
    for token in ("SYNTHETIC", "no real obligor"):
        if token not in module:
            out.append(
                f"synthetic-names-its-consequence: the vocabulary no longer "
                f"says {token!r}; a badge naming no consequence is one a reader "
                f"learns to skip"
            )
            break

    # 4/5. The colour table is total too, and synthetic is the loud one.
    if 'Record<ProvenanceKey, "outline" | "destructive">' not in module:
        out.append(
            "variants-are-total: the badge-variant table is no longer keyed by "
            "ProvenanceKey; a new channel would render with no decision made"
        )
    if 'synthetic: "destructive"' not in module:
        out.append(
            "synthetic-is-loud: synthetic no longer renders destructive; in a "
            "row of outline badges the one describing no real obligor reads as "
            "one more neutral fact"
        )

    # 6. The label renders INSIDE a <Badge>, sliced — not merely somewhere in
    #    the file. #565's own caught hole: a mutant demoting the label into body
    #    prose left every identifier greppable and survived.
    badges = "\n".join(_BADGE.findall(module))
    if "PROVENANCE_LABELS[key]" not in badges:
        out.append(
            "badge-text-is-the-label: no <Badge> in the module renders "
            "PROVENANCE_LABELS[key]; a qualifier a reader must find in prose "
            "has already lost to the figure beside it"
        )

    # 7. "Could not tell" is a key in the same tables, at the same weight.
    if 'unresolved: "ingestion channel not reported"' not in module:
        out.append(
            "unresolved-is-a-key: the unresolved label is gone; an unknown "
            "channel then renders as nothing, which a reader reads as an "
            "ordinary published tape"
        )

    # 8. …and the list renderer never renders silence.
    if "return <ProvenanceBadge source={null} />;" not in module:
        out.append(
            "badges-never-render-silence: ProvenanceBadges no longer falls back "
            "to the unresolved badge, so an empty list renders nothing at all"
        )

    # 9. Display order comes off the exported union, not a local literal — the
    #    pack summary once filtered a hardcoded ["deeploans", "direct"] and
    #    silently dropped derived tapes from the one place a reader looks.
    if "DATA_SOURCES.filter((s) => seen.has(s))" not in module:
        out.append(
            "order-comes-off-the-union: distinctDataSources no longer filters "
            "DATA_SOURCES; a local literal can fall behind the union again"
        )

    # 9b. …and the page-level row reads the periods actually rendered, not a
    #     value that can be emptied out from under it while the JSX still looks
    #     right. A badge row wired to nothing renders "not reported" forever.
    if "distinctDataSources(periods.map((p) => p.data_source))" not in pool:
        out.append(
            "pool-row-reads-the-periods: the deal-level badge row no longer "
            "derives from the periods on screen, so it can report nothing "
            "while the table below shows synthetic tapes"
        )

    # 10/11/11b. The bans, over whole files (see the docstring's slicing note).
    for name, text in (("module", module), ("pool rows", rows)):
        if _HIDDEN_MARKING.search(text):
            out.append(
                f"no-hidden-marking: {name} renders a provenance marking "
                f"invisibly; a source guard cannot see paint, so hiding one is "
                f"how every text rule here passes while a reader is told nothing"
            )
            break

    for name, text in (
        ("module", module),
        ("pack", pack),
        ("pool", pool),
        ("waterfall", waterfall),
    ):
        if _DEFAULTED_CHANNEL.search(text):
            out.append(
                f"no-defaulted-channel: {name} defaults an unknown source to a "
                f"real channel; that is the claim #599 removed, reinstated as a "
                f"harmless-looking fallback"
            )
            break
    for name, text in (
        ("module", module),
        ("pack", pack),
        ("pool", pool),
        ("waterfall", waterfall),
    ):
        if _LITERAL_CONDITION.search(text):
            out.append(
                f"no-literal-condition: {name} renders a provenance surface "
                f"behind a condition that cannot vary; the block is deleted "
                f"while every identifier in it stays greppable"
            )
            break
    for name, text in (
        ("module", module),
        ("pack", pack),
        ("pool", pool),
        ("waterfall", waterfall),
    ):
        if any(p.search(text) for p in _PROVENANCE_TERNARY):
            out.append(
                f"no-provenance-ternary: a conditional in {name} decides a "
                f"provenance label; that is exactly how PackBody called a "
                f"derived tape a direct ingestion"
            )
            break

    # --- PackBody: the surface that contradicted itself -------------------
    # 12. It renders the shared component, not a hand-rolled badge.
    if "<ProvenanceBadge key={src} source={src} />" not in pack:
        out.append(
            "pack-badge-is-shared: PackBody no longer badges through "
            "ProvenanceBadge; a second badge is a second vocabulary, and two "
            "vocabularies on one screen is the defect itself"
        )

    # 13. …and badge and sentence resolve through ONE table. This is the
    #     "same string" property: the sentence calls dataSourceLabel, the badge
    #     renders a table spread from the one dataSourceLabel reads. Fork them
    #     and they can disagree again without either being wrong in isolation.
    if "dataSourceLabel(" not in pack:
        out.append(
            "pack-sentence-shares-the-table: the disclosure sentence no longer "
            "calls dataSourceLabel, so badge and sentence can drift apart"
        )
    if "...DATA_SOURCE_LABELS," not in module:
        out.append(
            "pack-sentence-shares-the-table: PROVENANCE_LABELS no longer "
            "spreads DATA_SOURCE_LABELS, so the badge and the sentence are two "
            "vocabularies that merely happen to agree"
        )

    # --- Pool: #484's surface --------------------------------------------
    # 14. Marked from inside the loop that renders the rows (#575).
    if "<ProvenanceBadge" not in rows:
        out.append(
            "pool-marks-every-period: no badge renders inside the per-period "
            "row loop; a marking written once beside the table survives only "
            "until someone adds a period"
        )
    # 15. …and off that row's own source, not a value hoisted out of the loop.
    elif "source={p.data_source ?? null}" not in rows:
        out.append(
            "pool-badge-reads-its-row: the per-period badge no longer reads "
            "that period's own data_source; a hoisted value marks every row "
            "with one row's provenance"
        )
    # 16. …and the page says it above the fold too, for a reader who never
    #     scrolls to the table.
    if "<ProvenanceBadges sources={dataSources} />" not in pool:
        out.append(
            "pool-marks-the-page: the deal-level badge row is gone; every "
            "figure above the table then renders with no provenance at all"
        )

    # --- Waterfall: the same surface, with no provenance of its own -------
    # 17. WaterfallResult carries none, so the page must go and read it.
    if (
        "useDealDataSources(dealId)" not in waterfall
        or "<ProvenanceBadges sources={dataSources} />" not in waterfall
    ):
        out.append(
            "waterfall-marks-the-cascade: the cascade no longer reads or "
            "renders the deal's channels, so generated collateral renders "
            "exactly like real collateral"
        )

    # --- The union the tables are total over ------------------------------
    # 18. A table total over a list that has itself lost a member is total over
    #     nothing. `DATA_SOURCES` is what orders the badges, so a member missing
    #     here disappears from every surface with no type error anywhere.
    union, enumerated = _union_members(api), _enumerated_members(api)
    if union is None or enumerated is None:
        out.append(
            "union-list-is-complete: the DataSource union or its exported "
            "DATA_SOURCES list is unreadable in lib/api.ts, so nothing here "
            "knows what set the tables above are supposed to be total over"
        )
    elif union != enumerated:
        out.append(
            "union-list-is-complete: DATA_SOURCES no longer enumerates every "
            "member of the DataSource union, so a channel silently renders on "
            "no surface at all"
        )

    return out


def _check_ids(violations: list[str]) -> set[str]:
    return {v.split(":", 1)[0] for v in violations}


#: Every check id :func:`_violations` can emit. Kept as data so
#: :func:`test_no_check_is_decorative` can require a mutant for each.
_ALL_CHECKS = {
    "label-table-is-total",
    "every-channel-is-named",
    "synthetic-names-its-consequence",
    "variants-are-total",
    "synthetic-is-loud",
    "badge-text-is-the-label",
    "unresolved-is-a-key",
    "badges-never-render-silence",
    "order-comes-off-the-union",
    "no-defaulted-channel",
    "no-literal-condition",
    "no-hidden-marking",
    "pool-row-reads-the-periods",
    "no-provenance-ternary",
    "pack-badge-is-shared",
    "pack-sentence-shares-the-table",
    "pool-marks-every-period",
    "pool-badge-reads-its-row",
    "pool-marks-the-page",
    "waterfall-marks-the-cascade",
    "union-list-is-complete",
}


# ---------------------------------------------------------------------------
# Criterion: the surfaces, as committed, name every source honestly
# ---------------------------------------------------------------------------


def test_the_surfaces_as_committed_pass_every_check() -> None:
    assert _violations(_sources()) == [], "\n".join(_violations(_sources()))


def test_the_slice_markers_are_what_scope_the_positive_rules() -> None:
    """Both vacuity asserts must fire, or the positive rules scan "" and pass."""
    with pytest.raises(AssertionError, match="pass vacuously"):
        _pack_body("a sheet with no PackBody in it")
    with pytest.raises(AssertionError, match="pass vacuously"):
        _row_loop("a pool page with no row loop in it")
    with pytest.raises(AssertionError, match="pass vacuously"):
        _label_table("a module with no label table in it")


def test_a_slice_that_ran_to_end_of_file_is_refused() -> None:
    """#573's hole: an unbounded component slice lets a sibling answer for it.

    Both slices must *find* their end anchor, not fall back to the end of the
    file — otherwise `pool-marks-every-period` is satisfied by the page-level
    badge row, and the per-row rule this file exists for never fails.
    """
    with pytest.raises(AssertionError, match="ran to end of file"):
        _pack_body(_PACK_START + "\n  nothing top-level follows this\n")
    with pytest.raises(AssertionError, match="ran to end of file"):
        _row_loop(_ROW_START + "\n  no TableBody closes this\n")
    with pytest.raises(AssertionError, match="ran to end of file"):
        _label_table(_TABLE_START + "\n  nothing closes this literal\n")


def test_the_pages_still_render_their_three_states() -> None:
    """`web/CONTRACT.md`: loading skeleton, error card, data — all three."""
    sources = _sources()
    for key, content in (("pool", "<PoolContent"), ("waterfall", "<WaterfallContent")):
        page = sources[key]
        for token in ("<LoadingState", "<ErrorState", content, '"use client"'):
            assert token in page, f"{key} page no longer renders {token}"


# ---------------------------------------------------------------------------
# Criterion: the guard rejects every mutant — the half four siblings skipped
# ---------------------------------------------------------------------------

#: ``(id, target, [(old, new), ...])``. Each rewrite is applied to the REAL
#: source, so a mutant whose ``old`` no longer occurs fails as stale rather than
#: silently passing — a mutant table that has drifted off the code is the same
#: false comfort as a ban nobody tested.
_MUTANTS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "let-the-label-table-accept-a-new-channel-silently",
        "module",
        [
            (
                "DATA_SOURCE_LABELS: Record<DataSource, string>",
                "DATA_SOURCE_LABELS: Record<string, string>",
            )
        ],
    ),
    (
        "drop-the-derived-channel-from-the-vocabulary",
        "module",
        [
            (
                '  derived: "derived from a source document (not a published tape)",\n',
                "",
            )
        ],
    ),
    (
        "soften-synthetic-to-a-channel-name",
        "module",
        [
            (
                'synthetic: "SYNTHETIC — generated, describes no real obligor",',
                'synthetic: "synthetic ingestion",',
            )
        ],
    ),
    (
        "let-a-new-channel-render-with-no-colour-decided",
        "module",
        [
            (
                'Record<ProvenanceKey, "outline" | "destructive">',
                'Record<ProvenanceKey | string, "outline" | "destructive">',
            )
        ],
    ),
    (
        "render-synthetic-as-one-more-neutral-fact",
        "module",
        [('  synthetic: "destructive",\n', '  synthetic: "outline",\n')],
    ),
    (
        "demote-the-label-out-of-its-badge-into-prose",
        "module",
        [
            (
                '    <Badge variant={PROVENANCE_VARIANTS[key]} className="font-normal">\n'
                '      <Database className="mr-1 size-3" />\n'
                "      {PROVENANCE_LABELS[key]}\n"
                "    </Badge>",
                "    <>\n"
                '      <Badge variant={PROVENANCE_VARIANTS[key]} className="font-normal">\n'
                '        <Database className="mr-1 size-3" />\n'
                "      </Badge>\n"
                "      <span>{PROVENANCE_LABELS[key]}</span>\n"
                "    </>",
            )
        ],
    ),
    (
        "delete-the-we-could-not-tell-answer",
        "module",
        [('  unresolved: "ingestion channel not reported",\n', "")],
    ),
    (
        "render-nothing-when-the-list-is-empty",
        "module",
        [
            (
                "    return <ProvenanceBadge source={null} />;\n",
                "    return null;\n",
            )
        ],
    ),
    (
        "order-the-badges-off-a-local-literal-again",
        "module",
        [
            (
                "  return DATA_SOURCES.filter((s) => seen.has(s));",
                '  const order = ["deeploans", "direct"] as DataSource[];\n'
                "  return order.filter((s) => seen.has(s));",
            )
        ],
    ),
    (
        "default-an-unknown-channel-to-direct",
        "module",
        [
            (
                'const key: ProvenanceKey = source ?? "unresolved";',
                'const key: ProvenanceKey = source ?? "direct";',
            )
        ],
    ),
    (
        "restore-the-binary-badge-in-packbody",
        "sheet",
        [
            (
                "            <ProvenanceBadge key={src} source={src} />",
                '            <Badge key={src} variant="outline" className="font-normal">\n'
                '              {src === "deeploans" ? "deeploans" : "direct"} ingestion\n'
                "            </Badge>",
            )
        ],
    ),
    (
        "hand-roll-a-second-badge-in-packbody",
        "sheet",
        [
            (
                "            <ProvenanceBadge key={src} source={src} />",
                '            <Badge key={src} variant="outline" className="font-normal">\n'
                "              {dataSourceLabel(src)}\n"
                "            </Badge>",
            )
        ],
    ),
    (
        "fork-the-badge-vocabulary-from-the-sentences",
        "module",
        [
            (
                "  ...DATA_SOURCE_LABELS,\n",
                '  deeploans: "deeploans",\n'
                '  direct: "direct",\n'
                '  derived: "derived",\n'
                '  synthetic: "SYNTHETIC — generated, describes no real obligor",\n',
            )
        ],
    ),
    (
        "drop-dataSourceLabel-from-the-disclosure-sentence",
        "sheet",
        [
            (
                '            {dataSources.map((s) => dataSourceLabel(s)).join(", ")}.',
                '            {dataSources.join(", ")}.',
            )
        ],
    ),
    (
        "mark-the-pool-once-per-screen-instead-of-per-period",
        "pool",
        [
            (
                "                      <ProvenanceBadge source={p.data_source ?? null} />\n",
                "",
            )
        ],
    ),
    (
        "mark-every-period-with-the-first-periods-provenance",
        "pool",
        [
            (
                "<ProvenanceBadge source={p.data_source ?? null} />",
                "<ProvenanceBadge source={dataSources[0] ?? null} />",
            )
        ],
    ),
    (
        "strip-the-provenance-row-above-the-pool-charts",
        "pool",
        [("        <ProvenanceBadges sources={dataSources} />\n", "")],
    ),
    (
        "hide-the-pool-row-behind-a-dead-branch",
        "pool",
        [
            (
                "        <ProvenanceBadges sources={dataSources} />",
                "        {false ? <ProvenanceBadges sources={dataSources} /> : null}",
            )
        ],
    ),
    (
        "hide-the-per-period-badge-from-sight",
        "pool",
        [
            (
                "                      <ProvenanceBadge source={p.data_source ?? null} />",
                '                      <span className="sr-only">\n'
                "                        <ProvenanceBadge source={p.data_source ?? null} />\n"
                "                      </span>",
            )
        ],
    ),
    (
        "wire-the-pool-badge-row-to-nothing",
        "pool",
        [
            (
                "distinctDataSources(periods.map((p) => p.data_source))",
                "distinctDataSources([])",
            )
        ],
    ),
    (
        "stop-the-waterfall-reading-its-deals-channels",
        "waterfall",
        [("        <ProvenanceBadges sources={dataSources} />\n", "")],
    ),
    (
        "unexport-the-union-list-entirely",
        "api",
        [
            (
                "export const DATA_SOURCES: readonly DataSource[] = [",
                "const DATA_SOURCES: readonly DataSource[] = [",
            )
        ],
    ),
    (
        "drop-synthetic-from-the-enumerated-union",
        "api",
        [('  "synthetic",\n', "")],
    ),
]


def _mutate(
    sources: dict[str, str], target: str, edits: list[tuple[str, str]]
) -> dict[str, str]:
    """Apply *edits* to *target*, refusing a rewrite that no longer applies."""
    mutated = dict(sources)
    for old, new in edits:
        hits = mutated[target].count(old)
        assert hits, (
            f"the mutant no longer applies to {target}: {old!r} is not in the "
            f"source. A stale mutant proves nothing — update it to the code as "
            f"written."
        )
        # Uniqueness is not tidiness (#573). `evidence-pack-sheet.tsx` carries
        # three components' badges, and a shorter-indented anchor is a
        # *substring* of a deeper-indented line elsewhere — so an ambiguous
        # anchor silently rewrites code no slice scans, and the mutant
        # "survives" while testing nothing. An anchor matching twice is not a
        # mutant, it is a coin flip.
        assert hits == 1, (
            f"the mutant is ambiguous in {target}: {old!r} matches {hits} "
            f"times, so it may rewrite a line no check scans. Anchor it on text "
            f"unique to the region under test."
        )
        mutated[target] = mutated[target].replace(old, new, 1)
    return mutated


def test_both_mutate_refusals_fire() -> None:
    """A harness that silently no-ops turns a whole mutant table decorative."""
    sources = _sources()
    with pytest.raises(AssertionError, match="no longer applies"):
        _mutate(sources, "module", [("a string that is not in the module", "x")])
    with pytest.raises(AssertionError, match="ambiguous"):
        _mutate(sources, "module", [("ProvenanceKey", "x")])


@pytest.mark.parametrize("mutant_id,target,edits", _MUTANTS, ids=[m[0] for m in _MUTANTS])
def test_every_mutant_is_caught(
    mutant_id: str, target: str, edits: list[tuple[str, str]]
) -> None:
    """Each mutant is a way one of these surfaces could misname a source."""
    found = _violations(_mutate(_sources(), target, edits))
    assert found, (
        f"mutant {mutant_id!r} survived: the guard accepts a surface that "
        f"misnames its source. That is the #565/#568/#573 failure — the "
        f"criterion passing while violated."
    )


def test_no_check_is_decorative() -> None:
    """Every check must be the one that catches some mutant, and vice versa.

    A check no mutant reaches has never been observed to fail, which is the
    state four sibling guards were in when they shipped. Bidirectional on
    purpose: the forward half reds when a rule is added with no mutant, the
    reverse half reds when a rule emits an id `_ALL_CHECKS` has fallen behind —
    an id nobody listed is one nobody can require a mutant for.
    """
    sources = _sources()
    fired: set[str] = set()
    for _, target, edits in _MUTANTS:
        fired |= _check_ids(_violations(_mutate(sources, target, edits)))
    assert _ALL_CHECKS - fired == set(), (
        f"no mutant reaches these checks, so nothing shows they can fail: "
        f"{sorted(_ALL_CHECKS - fired)}"
    )
    assert fired - _ALL_CHECKS == set(), (
        f"a check emits an id _ALL_CHECKS does not list: {sorted(fired - _ALL_CHECKS)}"
    )
