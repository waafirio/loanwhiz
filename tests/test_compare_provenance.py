"""The comparison panel names what every series rests on — guarded by mutation.

#599 gave the tape-channel vocabulary one total label table and onboarded five
surfaces onto it. The **comparison panel was not one of them**, and nobody had
counted it: `web/components/compare/performance-panel.tsx` never imported
`provenance-badge`, and hand-rolled `d.performance_provenance === "projected"`
in two places. That is the exact shape #599's `no-provenance-ternary` rule bans
everywhere else, and it fails the same way — a literal condition names the one
branch it tests and calls every other member of the union by the fallback, so a
series resting on a generated coupon rendered as indistinguishable from Cairn's
reported one. An unguarded sixth surface, found by checking rather than assuming
(#614).

What this file guards
---------------------
One property: **a compare series' basis is a total-table lookup on the surface
that renders it, and the assumption under it reaches the screen.** Two unions
carry that basis — how the series was *built* (`PerformanceProvenance`) and what
the coupon under it *came from* (`RateProvenance`) — and they vary
independently, so both are guarded and neither may be dropped in favour of the
other.

Why a source-text guard
-----------------------
`web/` has no JS test runner in this repo, so the same discipline
`test_provenance_badges.py` and `test_book_page.py` use applies: read the `.tsx`
from pytest and assert over its text. Every expectation below is an independent
constant, never parsed out of the code under test — a guard that derives its
expectation from its subject passes by construction.

Two slicing rules, chosen by rule class (#568/#599)
---------------------------------------------------
A **positive** rule ("this function reads the table") takes a **bounded slice**,
because anything appended later to the file would otherwise satisfy it. A **ban**
takes the **whole file**, written narrowly enough that over-reach cannot
false-positive. Slicers anchor on a declaration's *name*, never on text a rule
inside the slice tests (#599's gotcha: a mutant that rewrites the anchor makes
the slicer raise, and a crash is neither a pass nor a catch).

Comments are stripped before any ban runs (#575): the comment in
`performance-panel.tsx` explaining why a literal `=== "projected"` is forbidden
would otherwise trip the ban that enforces it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_WEB = Path(__file__).resolve().parent.parent / "web"

_MODULE_PATH = _WEB / "components" / "compare" / "series-basis.tsx"
_PANEL_PATH = _WEB / "components" / "compare" / "performance-panel.tsx"
_API_PATH = _WEB / "lib" / "api.ts"


def _code_only(src: str) -> str:
    """`src` with block and line comments removed, string literals intact.

    Bans run over this. A rule that greps for a construct must not be tripped by
    the prose promising the construct is absent — #575's lesson, and the reason
    the panel can carry a comment naming the very literal it must not contain.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


def _sources() -> dict[str, str]:
    """The three files this surface's basis marking lives in."""
    return {
        "module": _MODULE_PATH.read_text(encoding="utf-8"),
        "panel": _PANEL_PATH.read_text(encoding="utf-8"),
        "api": _API_PATH.read_text(encoding="utf-8"),
    }


# ---------------------------------------------------------------------------
# Bounded slicers — positive rules only. Anchored on a declaration's NAME.
# ---------------------------------------------------------------------------

_NEXT_DECL = re.compile(r"^export (?:const|function|type|interface) ", re.M)


def _type_alias(src: str, name: str) -> str:
    """The one line declaring a union type.

    Scoped to the declaration rather than the file because `api.ts` documents
    each member in prose above the field that carries it: a doc comment reading
    `"projected" when derived from the canonical model` satisfies a whole-file
    grep for `"projected"` even after the union has dropped it. The rule is
    about the union, so it reads the union.
    """
    match = re.search(rf"^export type {name} = .*$", src, re.M)
    assert match, f"no type alias {name!r} — the guard cannot scope its rule"
    return match.group(0)


def _declaration(src: str, name: str) -> str:
    """The text of one exported declaration, bounded at the next one.

    Bounded rather than run to end-of-file (#573): a rule scoped to
    `src[index(name):]` is satisfied by anything appended after `name`, so a
    sibling declaration's tokens would count toward the rule about this one and
    the guard would pass while the declaration itself went quiet.
    """
    start = src.find(f"export const {name}")
    if start == -1:
        start = src.find(f"export function {name}")
    assert start != -1, (
        f"slicer anchor {name!r} is absent — the guard cannot scope its rule, "
        f"which is a broken guard, not a passing one"
    )
    rest = src[start + 1 :]
    match = _NEXT_DECL.search(rest)
    end = (start + 1 + match.start()) if match else len(src)
    sliced = src[start:end]
    assert sliced.strip(), f"slice for {name!r} is empty"
    return sliced


# ---------------------------------------------------------------------------
# Vocabulary — independent constants, never parsed from the subject.
# ---------------------------------------------------------------------------

#: Every member of `PerformanceProvenance`, as `lib/api.ts` declares it.
_PERFORMANCE_KINDS = ("reported", "projected")

#: Every member of `RateProvenance`.
_RATE_KINDS = ("stated", "synthetic")

#: A literal comparison against a provenance value — the construct that made
#: this surface wrong. Narrow on purpose: it matches a comparison to one of the
#: known member strings, not the bare word, so prose and identifiers are safe.
_LITERAL_CONDITION = re.compile(
    r"===\s*\"(?:reported|projected|stated|synthetic)\""
)

#: Affordances that render a marking present-but-unreadable.
_HIDING_AFFORDANCES = ("sr-only", "opacity-0", "line-clamp", "truncate", "<details")


def _violations(sources: dict[str, str]) -> list[str]:
    """Every rule this surface must satisfy, as `"<check-id>: <message>"`."""
    module, panel, api = sources["module"], sources["panel"], sources["api"]
    module_code, panel_code = _code_only(module), _code_only(panel)
    out: list[str] = []

    # 1. Both unions are declared where the tables can be total against them.
    build_union = _type_alias(api, "PerformanceProvenance")
    for kind in _PERFORMANCE_KINDS:
        if f'"{kind}"' not in build_union:
            out.append(f"union-declares-every-build: the union names no {kind!r}")
    rate_union = _type_alias(api, "RateProvenance")
    for kind in _RATE_KINDS:
        if f'"{kind}"' not in rate_union:
            out.append(f"union-declares-every-rate: the union names no {kind!r}")

    # 2. The API type carries the rate axis at all. Without this field the panel
    #    has nothing to mark with, however good its tables are.
    if "rate_provenance: RateProvenance" not in api:
        out.append(
            "api-carries-the-rate-axis: CompareDealRef declares no rate_provenance"
        )

    # 3/4. Each label table is TOTAL over its union — the property that makes
    #      adding a union member a build error rather than a silent fallback.
    labels = _declaration(module, "PERFORMANCE_PROVENANCE_LABELS")
    for kind in _PERFORMANCE_KINDS:
        if f"{kind}:" not in labels:
            out.append(f"build-labels-are-total: no label for {kind!r}")
    rate_labels = _declaration(module, "RATE_PROVENANCE_LABELS")
    for kind in _RATE_KINDS:
        if f"{kind}:" not in rate_labels:
            out.append(f"rate-labels-are-total: no label for {kind!r}")

    # 5/6. The legend suffix tables are total too. The legend is where two series
    #      appear as bare names side by side, so an unmarked one reads as equal.
    build_suffix = _declaration(module, "PERFORMANCE_PROVENANCE_SUFFIX")
    for kind in _PERFORMANCE_KINDS:
        if f"{kind}:" not in build_suffix:
            out.append(f"build-suffix-is-total: no legend suffix for {kind!r}")
    rate_suffix = _declaration(module, "RATE_PROVENANCE_SUFFIX")
    for kind in _RATE_KINDS:
        if f"{kind}:" not in rate_suffix:
            out.append(f"rate-suffix-is-total: no legend suffix for {kind!r}")

    # 7. A synthetic series must be marked in the legend, not only in the banner.
    if '  synthetic: " (synthetic rate)"' not in rate_suffix:
        out.append(
            "synthetic-marks-the-legend: the synthetic legend suffix is empty or "
            "reworded, so a generated series shares the reported one's name"
        )

    # 8/9. The variants table is total, and synthetic reads loudly. A generated
    #      input rendering at a published input's weight is #484's defect.
    variants = _declaration(module, "RATE_PROVENANCE_VARIANTS")
    for kind in _RATE_KINDS:
        if f"{kind}:" not in variants:
            out.append(f"variants-are-total: no variant for {kind!r}")
    # The ENTRY, not the annotation: `Record<RateProvenance, "outline" |
    # "destructive">` contains the word, so a bare grep passes while the entry
    # itself has gone quiet (#599 — a rule pinning a call and not its argument
    # pins the half that cannot vary).
    if 'synthetic: "destructive"' not in variants:
        out.append("synthetic-is-loud: the synthetic entry is not destructive")

    # 10. The synthetic label says what the marking is FOR — that the number was
    #     generated. A label reading only "synthetic" names a category, not a
    #     consequence, and the reader learns nothing from it.
    if "generated" not in rate_labels:
        out.append(
            "synthetic-names-its-consequence: the synthetic label never says the "
            "coupon was generated"
        )

    # 11. THE BAN. No literal provenance comparison anywhere on the panel — the
    #     construct this whole file exists because of. Whole file, comments
    #     stripped.
    if _LITERAL_CONDITION.search(panel_code):
        out.append(
            "no-provenance-ternary: the panel compares a provenance value to a "
            "literal instead of reading a total table"
        )

    # 12. The panel routes through the module rather than growing its own copy.
    if "@/components/compare/series-basis" not in panel_code:
        out.append("panel-imports-the-module: the panel imports no basis module")

    # 13. The legend name is built from the tables, inside the module.
    legend = _declaration(module, "seriesLegendName")
    if "PERFORMANCE_PROVENANCE_SUFFIX" not in legend:
        out.append("legend-reads-the-build-table: legend name ignores the table")
    if "RATE_PROVENANCE_SUFFIX" not in legend:
        out.append("legend-reads-the-rate-table: legend name ignores the rate table")

    # 14/15/16. The basis row renders both badges and the API's own disclosure.
    row = _declaration(module, "SeriesBasisRow")
    if "PERFORMANCE_PROVENANCE_LABELS[" not in row:
        out.append("row-badges-the-build: the row looks up no build label")
    if "RATE_PROVENANCE_LABELS[" not in row:
        out.append("row-badges-the-rate: the row looks up no rate label")
    if "deal.note" not in row:
        out.append(
            "row-renders-the-disclosure: the row drops `note`, so the tenor, the "
            "assumed value and the absent fixing never reach the screen"
        )

    # 17. The panel marks EVERY qualified deal, from inside the loop that renders
    #     them (#575) — not one sentence written once beside the list.
    if "qualified.map(" not in panel_code:
        out.append(
            "panel-marks-every-deal: the panel does not render a basis row per "
            "deal, so a marking survives only until a deal is added"
        )
    # Rendered, not imported: the import line carries the name, so grepping the
    # bare identifier passes for a panel that imports it and renders something
    # else entirely.
    if "<SeriesBasisRow" not in panel_code:
        out.append("panel-renders-the-row: the panel renders no basis row")

    # 18. `hasQualifiedBasis` is written as "not the plain case", so a union
    #     member added later is flagged by default rather than falling silently
    #     into the quiet branch.
    qualified = _declaration(module, "hasQualifiedBasis")
    if "!==" not in qualified:
        out.append(
            "qualified-is-not-a-whitelist: the predicate lists loud cases, so a "
            "new union member defaults to unmarked"
        )

    # 19/20. The marking may not be rendered invisibly or clipped.
    for affordance in _HIDING_AFFORDANCES:
        if affordance in module_code:
            out.append(f"no-hidden-marking: the basis module uses {affordance!r}")
    if "hidden" in module_code:
        out.append("no-hidden-marking: the basis module hides a marking")

    return out


def _check_ids(violations: list[str]) -> set[str]:
    return {v.split(":", 1)[0] for v in violations}


#: The rule inventory. `test_no_check_is_decorative` requires this set and the
#: set every mutant fires to be EQUAL — so a rule no mutant reaches reds, and a
#: rule emitting an id nobody listed reds too.
_ALL_CHECKS = {
    "union-declares-every-build",
    "union-declares-every-rate",
    "api-carries-the-rate-axis",
    "build-labels-are-total",
    "rate-labels-are-total",
    "build-suffix-is-total",
    "rate-suffix-is-total",
    "synthetic-marks-the-legend",
    "variants-are-total",
    "synthetic-is-loud",
    "synthetic-names-its-consequence",
    "no-provenance-ternary",
    "panel-imports-the-module",
    "legend-reads-the-build-table",
    "legend-reads-the-rate-table",
    "row-badges-the-build",
    "row-badges-the-rate",
    "row-renders-the-disclosure",
    "panel-marks-every-deal",
    "panel-renders-the-row",
    "qualified-is-not-a-whitelist",
    "no-hidden-marking",
}


def test_the_surface_as_committed_passes_every_check() -> None:
    """The committed surface satisfies every rule."""
    assert _violations(_sources()) == []


def test_the_slicers_are_anchored_on_names_no_rule_tests() -> None:
    """A mutant must never be able to make a slicer raise instead of a rule fire.

    #599's gotcha, pinned: anchoring a slice on text a rule *inside* it tests
    means the mutant that edits that text deletes the marker, the slicer raises,
    and the sweep records an error — which is neither a pass nor a catch. Every
    anchor below is a declaration name, and no rule asserts over a name.
    """
    module = _sources()["module"]
    for name in (
        "PERFORMANCE_PROVENANCE_LABELS",
        "RATE_PROVENANCE_LABELS",
        "PERFORMANCE_PROVENANCE_SUFFIX",
        "RATE_PROVENANCE_SUFFIX",
        "RATE_PROVENANCE_VARIANTS",
        "seriesLegendName",
        "hasQualifiedBasis",
        "SeriesBasisRow",
    ):
        sliced = _declaration(module, name)
        assert sliced.strip(), name
        assert len(sliced) < len(module), (
            f"the slice for {name} runs to end of file — it would be satisfied "
            f"by anything appended after it (#573)"
        )


def test_a_bounded_slice_excludes_a_later_declaration() -> None:
    """The bound is real: a token supplied by the NEXT declaration must not count.

    The tell #573 asks for — strip a token from one declaration and re-supply it
    from a decoy just after. If the bound were end-of-file the rule would pass.
    """
    module = _sources()["module"]
    decoy = module.replace(
        'export function hasQualifiedBasis',
        'export const DECOY_SUFFIX = "PERFORMANCE_PROVENANCE_SUFFIX";\n\n'
        "export function hasQualifiedBasis",
        1,
    )
    legend = _declaration(decoy, "seriesLegendName")
    assert "DECOY_SUFFIX" not in legend, "the slice reached past its bound"


# ---------------------------------------------------------------------------
# The mutation table — each entry must red at least one check.
# ---------------------------------------------------------------------------

#: `(id, target, [(old, new), ...])`. Applied to the REAL source, so a mutant
#: whose anchor goes stale is refused rather than silently surviving.
_MUTANTS: list[tuple[str, str, list[tuple[str, str]]]] = [
    # --- the unions themselves ---
    ("api-drops-projected", "api", [('export type PerformanceProvenance = "reported" | "projected";', 'export type PerformanceProvenance = "reported";')]),
    ("api-drops-synthetic", "api", [('export type RateProvenance = "stated" | "synthetic";', 'export type RateProvenance = "stated";')]),
    ("api-drops-the-rate-field", "api", [("  rate_provenance: RateProvenance | null;", "  rateProvenance: RateProvenance | null;")]),
    # --- label tables lose a member ---
    ("labels-drop-projected", "module", [("  projected:\n    \"PROJECTED", "  projectedTypo:\n    \"PROJECTED")]),
    ("labels-drop-reported", "module", [('  reported: "REPORTED', '  reportedTypo: "REPORTED')]),
    ("rate-labels-drop-synthetic", "module", [("  synthetic:\n    \"SYNTHETIC RATE", "  syntheticTypo:\n    \"SYNTHETIC RATE")]),
    ("rate-labels-drop-stated", "module", [('  stated: "STATED RATE', '  statedTypo: "STATED RATE')]),
    # --- the synthetic label stops saying what it means ---
    ("synthetic-label-goes-vague", "module", [('"SYNTHETIC RATE — the coupon rests on a generated index fixing, not a published one",', '"SYNTHETIC RATE",')]),
    # --- suffix tables ---
    ("build-suffix-drops-projected", "module", [('  projected: " (projected)",', '  projectedTypo: " (projected)",')]),
    ("build-suffix-drops-reported", "module", [('  reported: "",\n  projected:', '  reportedTypo: "",\n  projected:')]),
    ("rate-suffix-drops-stated", "module", [('  stated: "",\n  synthetic:', '  statedTypo: "",\n  synthetic:')]),
    ("synthetic-legend-goes-silent", "module", [('  synthetic: " (synthetic rate)",', '  synthetic: "",')]),
    # --- variants ---
    ("variants-drop-synthetic", "module", [('  synthetic: "destructive",', '  syntheticTypo: "destructive",')]),
    ("variants-drop-stated", "module", [('  stated: "outline",', '  statedTypo: "outline",')]),
    ("synthetic-renders-quietly", "module", [('  synthetic: "destructive",', '  synthetic: "outline",')]),
    # --- the panel regresses to what it was ---
    ("panel-hand-rolls-the-ternary", "panel", [("    () => new Map(deals.map((d) => [d.deal_id, seriesLegendName(d)])),", '    () => new Map(deals.map((d) => [d.deal_id, d.performance_provenance === "projected" ? `${d.deal_name} (projected)` : d.deal_name])),')]),
    ("panel-stops-importing-the-module", "panel", [('import {\n  SeriesBasisRow,\n  hasQualifiedBasis,\n  seriesLegendName,\n} from "@/components/compare/series-basis";', "const SeriesBasisRow = () => null;\nconst hasQualifiedBasis = () => false;\nconst seriesLegendName = (d: CompareDealRef) => d.deal_name;")]),
    ("panel-marks-once-not-per-deal", "panel", [("              {qualified.map((d) => (\n                <SeriesBasisRow key={d.deal_id} deal={d} />\n              ))}", "              <SeriesBasisRow deal={qualified[0]} />")]),
    ("panel-drops-the-row", "panel", [("                <SeriesBasisRow key={d.deal_id} deal={d} />", "                <li key={d.deal_id}>{d.deal_name}</li>")]),
    # --- the legend stops reading the tables ---
    ("legend-ignores-the-build-table", "module", [("      : PERFORMANCE_PROVENANCE_SUFFIX[deal.performance_provenance];", '      : "";')]),
    ("legend-ignores-the-rate-table", "module", [("      : RATE_PROVENANCE_SUFFIX[deal.rate_provenance];", '      : "";')]),
    # --- the row stops rendering its parts ---
    ("row-drops-the-build-badge", "module", [("            {PERFORMANCE_PROVENANCE_LABELS[deal.performance_provenance]}", "            {deal.performance_provenance}")]),
    ("row-drops-the-rate-badge", "module", [("            {RATE_PROVENANCE_LABELS[deal.rate_provenance]}", "            {deal.rate_provenance}")]),
    ("row-drops-the-disclosure", "module", [("      {deal.note && <p className=\"text-xs leading-relaxed\">{deal.note}</p>}", "      {null}")]),
    # --- the predicate becomes a whitelist ---
    ("qualified-becomes-a-whitelist", "module", [('  return (\n    deal.performance_provenance !== "reported" ||\n    deal.rate_provenance !== "stated"\n  );', '  return (\n    deal.performance_provenance === "projected" ||\n    deal.rate_provenance === "synthetic"\n  );')]),
    # --- the marking is present but unreadable ---
    ("disclosure-is-clipped", "module", [('<p className="text-xs leading-relaxed">{deal.note}</p>', '<p className="text-xs leading-relaxed truncate">{deal.note}</p>')]),
    ("disclosure-is-screenreader-only", "module", [('<p className="text-xs leading-relaxed">{deal.note}</p>', '<p className="sr-only">{deal.note}</p>')]),
]


def _mutate(src: str, edits: list[tuple[str, str]]) -> str:
    """Apply `edits`, refusing a stale or ambiguous anchor.

    #573's hole: `str.replace(old, new, 1)` takes the first match, and a
    shorter-indented anchor is a substring of a deeper-indented line, so a
    mutant can rewrite an unrelated region and "survive" while testing nothing.
    Counting the matches and refusing anything but exactly one is what makes a
    surviving mutant a fact about the surface rather than about the harness.
    """
    for old, new in edits:
        hits = src.count(old)
        if hits == 0:
            raise AssertionError(f"stale mutant anchor, 0 matches: {old[:70]!r}")
        if hits != 1:
            raise AssertionError(
                f"ambiguous mutant anchor, {hits} matches: {old[:70]!r}"
            )
        src = src.replace(old, new)
    return src


def test_both_mutate_refusals_fire() -> None:
    """The harness's own guards work — a guard's guard needs one too."""
    with pytest.raises(AssertionError, match="stale"):
        _mutate("abc", [("zzz", "y")])
    with pytest.raises(AssertionError, match="ambiguous"):
        _mutate("abab", [("ab", "y")])


@pytest.mark.parametrize("mutant", _MUTANTS, ids=[m[0] for m in _MUTANTS])
def test_every_mutant_is_caught(mutant: tuple[str, str, list[tuple[str, str]]]) -> None:
    """Every way of defeating this marking that we thought of, reds."""
    name, target, edits = mutant
    sources = _sources()
    sources[target] = _mutate(sources[target], edits)
    assert _violations(sources), f"mutant {name!r} survived — the guard has a hole"


def test_no_check_is_decorative() -> None:
    """Bidirectional: every rule is reached by a mutant, and every id is listed.

    Forward — a rule no mutant fires has never been observed to fail, which is
    the state four sibling guards shipped in (#575, #568, #599, and this
    surface). Reverse — a rule emitting an id `_ALL_CHECKS` does not list is one
    nobody can be required to write a mutant for, so it hides from the forward
    half.
    """
    fired: set[str] = set()
    for name, target, edits in _MUTANTS:
        sources = _sources()
        sources[target] = _mutate(sources[target], edits)
        fired |= _check_ids(_violations(sources))

    assert _ALL_CHECKS - fired == set(), (
        "rules no mutant reaches — write a mutant or drop the rule: "
        f"{sorted(_ALL_CHECKS - fired)}"
    )
    assert fired - _ALL_CHECKS == set(), (
        "rules firing an id the inventory does not list: "
        f"{sorted(fired - _ALL_CHECKS)}"
    )
