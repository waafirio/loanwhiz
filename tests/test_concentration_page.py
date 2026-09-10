"""What the look-through concentration surface must render — and cannot undo (#565).

``web/`` has no JS test runner, so this asserts the components' **source**, not
their rendered output. That is the trade
``tests/test_capability_matrix.py::test_the_user_facing_no_tape_card_does_not_carry_the_retracted_claim``
already makes to reach ``page-states.tsx``; it is stated here rather than
implied, because a source guard proves the code says a thing, never that a
browser paints it.

The reason for the second half of this file is more specific. Two sibling
epics have now shipped a guard that was itself unguarded:

* #575's auth guard passed with a clickable "Generate access token" anchor in
  the page, because the ban listed ``onClick`` and ``<button`` and an ``<a>``
  styled as a button is neither;
* #568's score ban passed a rendered "1 of 7 verified", because the ban looked
  for a ratio (``.length /``) and summing two kinds needs no division.

Both are the same failure: the acceptance criterion passed while the thing it
banned was on screen. A ban is only worth what it rejects, and nothing about
reading one tells you what it rejects. So :data:`_MUTANTS` rewrites the *real*
source and requires :func:`_violations` to catch each rewrite, and
:func:`test_no_check_is_decorative` requires every check to be the one that
catches at least one of them — a check no mutant reaches is a check that has
never been observed to fail.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SHEET = _REPO_ROOT / "web" / "components" / "evidence-pack-sheet.tsx"
_PAGE = _REPO_ROOT / "web" / "app" / "(routes)" / "concentration" / "page.tsx"
_NAV = _REPO_ROOT / "web" / "lib" / "nav.ts"

#: Opening line of the region this issue added to the shared provenance sheet.
#: Every ban below is scoped to the region, so its absence would make them all
#: scan an empty string and pass — hence the vacuity assert in :func:`_region`.
_REGION_MARKER = "Look-through concentration disclosure (#565"


def _code_only(source: str) -> str:
    """Strip comments so a sentence *refusing* a claim does not trip its ban.

    The same shape ``tests/test_mcp_page.py`` uses, for the same reason (#471):
    the region's own header comment explains that it must not net an unresolved
    name into "Other", and a ban reading that comment as code would fire on the
    documentation of the rule it enforces. Strings are deliberately NOT
    stripped — a banned word rendered inside a JSX string is exactly the thing
    a reader sees.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return "\n".join(
        line
        for line in source.splitlines()
        if not line.lstrip().startswith(("//", "*"))
    )


def _region(sheet: str) -> str:
    """The look-through region of the sheet, marker to end of file.

    Deliberately a superset — a ban that over-reaches fails loudly, one that
    under-reaches passes silently.
    """
    assert _REGION_MARKER in sheet, (
        "the look-through region marker is gone from evidence-pack-sheet.tsx; "
        "every check below would scan an empty string and pass vacuously"
    )
    return _code_only(sheet[sheet.index(_REGION_MARKER) :])


def _sources() -> dict[str, str]:
    """The three real files, as committed."""
    return {
        "sheet": _SHEET.read_text(encoding="utf-8"),
        "page": _PAGE.read_text(encoding="utf-8"),
        "nav": _NAV.read_text(encoding="utf-8"),
    }


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------

#: Identifiers naming one obligor-resolution tier or the residual derived from
#: them. Any arithmetic on one of these is a netting: adding two tiers produces
#: an "identified" total, dividing one by the book produces a grade, and #549's
#: rule is that a number rendered beside a refusal is read as the measurement.
_TIER_TOKENS = (
    "proven_shared",
    "candidate_proposed",
    "unresolved",
    "unproven_name_count",
    "not_proven_share_pct",
)

_TIER_ALT = "|".join(_TIER_TOKENS)
#: A tier identifier with an arithmetic operator on either side of it.
#: The alternation is wrapped on BOTH sides on purpose: ``a|b\s*[-+*/]`` binds
#: the alternation looser than the suffix, so the un-grouped form matches a bare
#: tier name anywhere and reports every honest render as a netting. It did, on
#: the first run of this file.
_TIER_ARITHMETIC = (
    re.compile(rf"(?:^|[^A-Za-z0-9_])(?:{_TIER_ALT})[ \t]*[-+*/](?!>)", re.M),
    re.compile(rf"[-+*/][ \t]*(?:[A-Za-z0-9_$]+\.)*(?:{_TIER_ALT})\b", re.M),
)

#: Ways to build an aggregate without naming a tier — the #575-shaped hole,
#: where the banned thing is spelled with tokens the ban list does not contain.
#: ``reduce``/``Object.values`` fold the tiers without an operator between two
#: of their names; an indexed ``tiers[0]`` elevates one tier into a headline;
#: ``concat`` is how the unattributed record becomes a bucket.
#: ``.filter``/``.slice``/``.find``/``.at`` are here for the same reason: each
#: renders a *subset* of a collection the surrounding labels present as whole,
#: which is how a tier or a deal disappears without any arithmetic happening.
_AGGREGATE_SHAPES = (
    "reduce(",
    "Object.values(",
    ".concat(",
    "toFixed",
    "tiers[0]",
    "tiers[1]",
    "tiers[2]",
    ".filter(",
    ".slice(",
    ".find(",
    ".at(",
)

#: A condition that cannot vary. ``{false ? (…) : null}`` deletes a block while
#: leaving every identifier in it findable, so a guard that only greps for the
#: identifiers reads the deleted block as rendered — the #575 shape again, in
#: the other direction.
_LITERAL_CONDITION = re.compile(r"\{\s*(?:true|false)\s*\?")

#: A tier key as a *string literal*. The tier names are keys and props, never
#: values, so a quoted one means something is comparing against a single tier —
#: the way one gets skipped at render time with no arithmetic and no grade word
#: anywhere near it.
_QUOTED_TIERS = ('"proven_shared"', '"candidate_proposed"', '"unresolved"')

#: Words that assert obligor identity as a *grade* rather than a split. The
#: #568-shaped hole: "1 of 7 verified" is a grade with no division in it, so
#: banning the arithmetic does not reach it — the vocabulary has to be banned
#: too. "proven"/"unproven" are absent on purpose: they are the honest words
#: this surface is required to use.
_GRADE_WORDS = (
    "identified",
    "verified",
    "coverage",
    "score",
    "match rate",
    "resolution rate",
    "confidence",
)

#: One rendered ``<Badge>`` — the region's most prominent element, and the one
#: a reader takes in before any prose. Sliced rather than searched file-wide
#: because "the count appears somewhere in the file" is what a first pass of
#: this guard checked, and a mutant that moved the count out of the badge and
#: left it in a paragraph passed it.
_BADGE = re.compile(r"<Badge\b.*?</Badge>", re.S)

#: Labels that would name a residual bucket. This repo has none anywhere
#: (#496/#514) and the screen is where one gets added by accident.
_RESIDUAL_LABELS = ("Other", "Unclassified", "Unknown", "Misc")


def _violations(*, region: str, page: str, nav: str) -> list[str]:
    """Every rule this surface must satisfy, as ``"<check-id>: <why>"`` lines.

    Returning the failures rather than asserting them is what lets
    :data:`_MUTANTS` run the same code against a rewritten source.
    """
    out: list[str] = []
    both = f"{region}\n{page}"

    def need(check: str, token: str, where: str, why: str) -> None:
        if token not in {"region": region, "page": page, "nav": nav}[where]:
            out.append(f"{check}: {where} no longer renders {token!r} — {why}")

    # 1. The headline residual, as figures of the same weight as the shares.
    for token in (
        "{figure.unproven_name_count}",
        "{figure.name_count}",
        "figure.not_proven_share_pct",
    ):
        need("residual-count", token, "region", "the unproven set must be a figure")

    # 1b. …and rendered as prominently as the shares they qualify. #549: a
    #     refusal that keeps the value is not a refusal, and a residual a
    #     reader has to find in a paragraph beneath the figure is one the
    #     figure has already outrun.
    badges = "\n".join(_BADGE.findall(region))
    for token in (
        "figure.unproven_name_count",
        "figure.not_proven_share_pct",
        "figure.obligor_bounds",
    ):
        if token not in badges:
            out.append(
                f"residual-is-prominent: {token} renders in no <Badge>; the "
                f"residual must carry the same weight as the shares it qualifies"
            )

    # 2. The obligor count stays a range; a point estimate assumes what #562
    #    refused to assume.
    for token in ("figure.obligor_bounds.lower", "figure.obligor_bounds.upper"):
        need("bounds-range", token, "region", "both bounds must render")

    # 3/4. All three tiers, named and looped — never two, never one.
    for tier in ("proven_shared", "candidate_proposed", "unresolved"):
        need("three-tiers", f"{tier}:", "region", "every tier needs its own label")
    need("tier-loop", "figure.tiers.map(", "region", "the tiers render as data")

    # 5. Every bucket's split, as three figures.
    for tier in ("proven_shared", "candidate_proposed", "unresolved"):
        need("bucket-split", f"split.{tier}", "region", "the split qualifies the share")
    need("bucket-split-wired", "<BucketSplit", "page", "every row carries its split")

    # 6. No arithmetic over a tier: no sums, no ratios, no grades.
    for pattern in _TIER_ARITHMETIC:
        for hit in pattern.findall(both):
            out.append(
                f"no-tier-arithmetic: a resolution tier is combined arithmetically "
                f"near {hit!r}; a tier total or ratio is a grade over two kinds"
            )

    # 7. The same netting spelled without naming two tiers.
    for shape in _AGGREGATE_SHAPES:
        if shape in both:
            out.append(
                f"no-aggregate-shape: {shape!r} folds or elevates the tiers without "
                f"naming two of them — the shape #575's anchor took past its ban"
            )

    # 7b. No block deleted behind a condition that cannot vary.
    if _LITERAL_CONDITION.search(both):
        out.append(
            "no-literal-condition: a block renders behind a literal true/false; "
            "its identifiers stay greppable while nothing reaches the screen"
        )

    # 7c. No single tier singled out for different treatment.
    for quoted in _QUOTED_TIERS:
        if quoted in both:
            out.append(
                f"no-tier-singled-out: {quoted} is compared against as a value; "
                f"the three tiers render as one set or the set is not the truth"
            )

    # 8. No vocabulary that reads as a grade.
    lowered = both.lower()
    for word in _GRADE_WORDS:
        if word in lowered:
            out.append(
                f"no-grade-vocabulary: {word!r} states obligor identity as a grade; "
                f"'1 of 7 verified' needs no division and is still a score"
            )

    # 9. No residual bucket, under any spelling.
    for label in _RESIDUAL_LABELS:
        if f'"{label}"' in both or f">{label}<" in both:
            out.append(
                f"no-residual-bucket: {label!r} is rendered as a label; an "
                f"unresolved name must not be netted into a bucket"
            )

    # 10/11. Both stated dates, and the fact that they disagree.
    need("both-dates", "figure.as_of.map(", "region", "each deal states its own date")
    need("both-dates", "{d.stated}", "region", "the stated date is what renders")
    need("dates-align", "figure.dates_align", "region", "the mismatch must render")
    need("dates-align", "different dates", "region", "the mismatch is stated in words")

    # 12/13. The axis names its taxonomy wherever a share is quoted.
    if page.count("{figure.axis.label}") < 2:
        out.append(
            "axis-named: the page quotes shares without naming the axis twice "
            "(card title and column head) — a share without its taxonomy is not "
            "comparable to any other book"
        )
    need("axis-named", "figure.axis.label", "region", "the disclosure names the axis")

    # 14. The backend's own sentences, rendered rather than paraphrased.
    need("disclosure-sentences", "{figure.disclosure}", "region", "figure sentence")
    need(
        "disclosure-sentences",
        "{figure.obligor_disclosure}",
        "region",
        "obligor sentence",
    )
    need("disclosure-sentences", "{bucket.disclosure}", "page", "per-bucket sentence")

    # 15. The unattributed record is not a bucket and does not reach the table.
    need("unattributed-kept-apart", "figure.unattributed", "region", "it must render")
    if "unattributed" in page:
        out.append(
            "unattributed-kept-apart: the page names the unattributed record; it "
            "belongs to the disclosure block, not to the bucket table"
        )

    # 16. The residual renders above the figures it qualifies.
    need("disclosure-first", "<ConcentrationDisclosure", "page", "it must render")
    if "<ConcentrationDisclosure" in page and "<Table" in page:
        if page.index("<ConcentrationDisclosure") > page.index("<Table"):
            out.append(
                "disclosure-first: the residual renders below the bucket table; a "
                "share read before its qualifier is read as a measurement"
            )

    # 17. The route is reachable.
    need("nav-entry", '"/concentration"', "nav", "the route ships unreachable")

    # 18. One provenance surface, not two.
    need(
        "extends-the-sheet",
        '"@/components/evidence-pack-sheet"',
        "page",
        "the disclosure block is the shared surface, not a parallel one",
    )
    return out


def _check_ids(violations: list[str]) -> set[str]:
    return {v.split(":", 1)[0] for v in violations}


#: Every check id :func:`_violations` can emit. Kept as data so
#: :func:`test_no_check_is_decorative` can require a mutant for each.
_ALL_CHECKS = {
    "residual-count",
    "residual-is-prominent",
    "bounds-range",
    "three-tiers",
    "tier-loop",
    "bucket-split",
    "bucket-split-wired",
    "no-tier-arithmetic",
    "no-aggregate-shape",
    "no-literal-condition",
    "no-tier-singled-out",
    "no-grade-vocabulary",
    "no-residual-bucket",
    "both-dates",
    "dates-align",
    "axis-named",
    "disclosure-sentences",
    "unattributed-kept-apart",
    "disclosure-first",
    "nav-entry",
    "extends-the-sheet",
}


# ---------------------------------------------------------------------------
# Criterion: the surface, as committed, discloses its residual
# ---------------------------------------------------------------------------


def test_the_surface_as_committed_passes_every_check() -> None:
    sources = _sources()
    found = _violations(
        region=_region(sources["sheet"]),
        page=_code_only(sources["page"]),
        nav=sources["nav"],
    )
    assert found == [], "\n".join(found)


def test_the_region_marker_is_what_scopes_every_ban() -> None:
    """The vacuity assert has to fire, or the bans scan an empty string."""
    with pytest.raises(AssertionError, match="scan an empty string"):
        _region("a sheet with no look-through region in it")


def test_the_page_renders_its_three_states() -> None:
    """`web/CONTRACT.md`: loading skeleton, error card, data — all three."""
    page = _sources()["page"]
    for token in ("<LoadingState", "<ErrorState", "<ConcentrationContent", '"use client"'):
        assert token in page


def test_the_disclosure_lives_in_the_shared_provenance_sheet() -> None:
    """Extended, not duplicated: one file a reader learns to look in."""
    sheet = _sources()["sheet"]
    assert "export function ConcentrationDisclosure" in sheet
    assert "export function BucketSplit" in sheet
    assert "export function ReportingDates" in sheet


# ---------------------------------------------------------------------------
# Criterion: the guard rejects every mutant — the half two siblings skipped
# ---------------------------------------------------------------------------

#: ``(id, target, [(old, new), ...])``. Each rewrite is applied to the REAL
#: source, so a mutant whose ``old`` no longer occurs fails as stale rather
#: than silently passing — a mutant table that has drifted off the code is the
#: same false comfort as a ban nobody tested.
_MUTANTS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "sum-two-tiers-into-one-figure",
        "region",
        [("{formatCurrency(split.proven_shared)}", "{formatCurrency(split.proven_shared + split.candidate_proposed)}")],
    ),
    (
        "divide-a-tier-by-the-book",
        "region",
        [("{formatCurrency(split.unresolved)}", "{formatPct(split.unresolved / 100, 2)}")],
    ),
    (
        "fold-the-tiers-without-naming-two",
        "region",
        [
            (
                "{figure.unproven_name_count} of {figure.name_count} names unproven",
                "{figure.tiers.reduce((a, t) => a + t.name_count, 0)} names",
            )
        ],
    ),
    (
        "a-grade-with-no-division-in-it",
        "region",
        [
            (
                "{figure.unproven_name_count} of {figure.name_count} names unproven",
                "{figure.tiers[0].name_count} of {figure.name_count} names identified",
            )
        ],
    ),
    (
        "call-the-split-a-confidence",
        "region",
        [("Obligor identity could not be proven for", "Obligor identity confidence for")],
    ),
    (
        "render-only-the-first-deals-date",
        "region",
        [("{figure.as_of.map((d) => (", "{figure.as_of.slice(0, 1).map((d) => (")],
    ),
    (
        "replace-both-dates-with-one-as-of",
        "region",
        [('<dd className="text-right font-medium tabular-nums">{d.stated}</dd>', "<dd>31/12/2024</dd>")],
    ),
    (
        "hide-the-date-mismatch",
        "region",
        [("{figure.dates_align ? null : (", "{true ? null : (")],
    ),
    (
        "collapse-the-obligor-bounds-to-a-point",
        "region",
        [
            (
                "Distinct obligors {figure.obligor_bounds.lower}&ndash;",
                "Distinct obligors ",
            )
        ],
    ),
    (
        "drop-the-unresolved-tier-label",
        "region",
        [('  unresolved: "Unresolved",\n', "")],
    ),
    (
        "stop-rendering-the-figures-own-sentence",
        "region",
        [("{figure.disclosure}", "Concentration by industry."),],
    ),
    (
        "drop-the-unproven-count-entirely",
        "region",
        [("{figure.unproven_name_count} of {figure.name_count} names unproven", "Cross-deal exposure")],
    ),
    (
        "drop-the-unproven-share-of-balance",
        "region",
        [("{formatPct(figure.not_proven_share_pct, 2)} of balance", "of balance")],
    ),
    (
        "stop-looping-the-tiers",
        "region",
        [("{figure.tiers.map((t) => (", "{[].map((t: ConcentrationTier) => (")],
    ),
    (
        "blank-the-candidate-tier-in-the-split",
        "region",
        [("{formatCurrency(split.candidate_proposed)}", "{formatCurrency(0)}")],
    ),
    (
        "skip-the-unresolved-tier-at-render-time",
        "region",
        [
            (
                "        {figure.tiers.map((t) => (",
                '        {figure.tiers.map((t) => t.tier === "unresolved" ? null : (',
            )
        ],
    ),
    (
        "hide-the-unattributed-block-behind-a-dead-branch",
        "region",
        [("      {figure.unattributed.asset_count > 0 ? (", "      {false ? (")],
    ),
    (
        "add-an-other-row-to-the-table",
        "page",
        [
            (
                "{figure.buckets.map((bucket) => (",
                '{[...figure.buckets, { label: "Other" }].map((bucket) => (',
            )
        ],
    ),
    (
        "net-the-unattributed-record-into-the-buckets",
        "page",
        [
            (
                "{figure.buckets.map((bucket) => (",
                "{figure.buckets.concat(figure.unattributed).map((bucket) => (",
            )
        ],
    ),
    (
        "drop-the-split-column-from-every-row",
        "page",
        [("<BucketSplit split={bucket.split} />", "<span>{bucket.asset_count}</span>")],
    ),
    (
        "quote-the-share-without-naming-the-axis",
        "page",
        [("<TableHead>{figure.axis.label}</TableHead>", "<TableHead>Industry</TableHead>")],
    ),
    (
        "drop-the-per-bucket-sentence",
        "page",
        [("{bucket.disclosure}", "{bucket.label}")],
    ),
    (
        "remove-the-disclosure-block",
        "page",
        [("<ConcentrationDisclosure figure={figure} />", "<div />")],
    ),
    (
        "move-the-disclosure-below-the-table",
        "page",
        [
            ("      <ConcentrationDisclosure figure={figure} />\n\n      <Card>", "      <Card>"),
            ("      </Card>\n    </div>", "      </Card>\n      <ConcentrationDisclosure figure={figure} />\n    </div>"),
        ],
    ),
    (
        "build-a-parallel-disclosure-component",
        "page",
        [('} from "@/components/evidence-pack-sheet";', '} from "@/components/concentration-disclosure";')],
    ),
    (
        "ship-the-route-unreachable",
        "nav",
        [('{ title: "Concentration", href: "/concentration", icon: Layers3 },', "")],
    ),
]


def _mutate(sources: dict[str, str], target: str, edits: list[tuple[str, str]]) -> dict[str, str]:
    """Apply *edits* to *target*, refusing a rewrite that no longer applies."""
    key = "sheet" if target == "region" else target
    mutated = dict(sources)
    for old, new in edits:
        assert old in mutated[key], (
            f"the mutant no longer applies to {key}: {old!r} is not in the source. "
            f"A stale mutant proves nothing — update it to the code as written."
        )
        mutated[key] = mutated[key].replace(old, new, 1)
    return mutated


@pytest.mark.parametrize("mutant_id,target,edits", _MUTANTS, ids=[m[0] for m in _MUTANTS])
def test_the_guard_reds_on_every_mutant(
    mutant_id: str, target: str, edits: list[tuple[str, str]]
) -> None:
    """Each mutant is a way this surface could mislead. None may pass."""
    mutated = _mutate(_sources(), target, edits)
    found = _violations(
        region=_region(mutated["sheet"]),
        page=_code_only(mutated["page"]),
        nav=mutated["nav"],
    )
    assert found, (
        f"mutant {mutant_id!r} survived: the guard accepts a surface that "
        f"misleads. This is the #575/#568 failure — the criterion passing while "
        f"violated."
    )


def test_no_check_is_decorative() -> None:
    """Every check must be the one that catches some mutant.

    A check no mutant reaches has never been observed to fail, which is the
    state both sibling guards were in when they shipped. This is the assertion
    that keeps the table honest as the surface grows: adding a check without a
    mutant for it reds here.
    """
    sources = _sources()
    fired: set[str] = set()
    for _, target, edits in _MUTANTS:
        mutated = _mutate(sources, target, edits)
        fired |= _check_ids(
            _violations(
                region=_region(mutated["sheet"]),
                page=_code_only(mutated["page"]),
                nav=mutated["nav"],
            )
        )
    assert _ALL_CHECKS - fired == set(), (
        f"no mutant reaches these checks, so nothing shows they can fail: "
        f"{sorted(_ALL_CHECKS - fired)}"
    )
    assert fired - _ALL_CHECKS == set(), (
        f"a check emits an id _ALL_CHECKS does not list: {sorted(fired - _ALL_CHECKS)}"
    )
