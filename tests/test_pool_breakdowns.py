"""What ``/pool`` must say about a distribution it cannot show (#630).

``web/`` has no JS test runner, so this asserts the component's **source**, not
its rendered output — the same trade
``tests/test_capability_matrix.py::test_the_user_facing_no_tape_card_does_not_carry_the_retracted_claim``
makes to reach ``page-states.tsx``, and stated here rather than implied because
a source guard proves the code says a thing, never that a browser paints it.

The property under guard is the platform's own argument: a refusal is stated,
not silent (#549). Before this issue, ``/pool`` on a CLO rendered "No EPC
breakdown in this tape." — an *energy performance rating* asserted as merely
missing for a corporate loan pool, which has no such concept — while the
geographic section removed itself entirely and the rate-type and property-type
distributions were fetched and never rendered at all.

Two failure directions matter, and they pull against each other:

* **Under-claiming** — an absence with no reason, or a section that vanishes.
* **Over-claiming** — "not applicable" asserted from emptiness alone. A
  not-applicable reason is an assertion about the world (#457), so it may say
  only what the input encodes: ``asset_class``. Contego CLO XI is the live
  counter-example — its trustee report *does* publish a per-asset All-In-Rate,
  in a section LoanWhiz does not yet parse, so wording that blamed the issuer
  for the gap would be a fabricated refusal.

Following ``tests/test_concentration_page.py``, :data:`_MUTANTS` rewrites the
real source and requires :func:`_violations` to catch each rewrite, so a check
that no mutant reaches is reported as decorative — a ban is worth only what it
rejects, and reading one tells you nothing about what that is.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PAGE = _REPO_ROOT / "web" / "app" / "(routes)" / "pool" / "page.tsx"

#: The wording this issue retracted. Each asserted a *mortgage* concept as
#: merely missing, with no reason and no asset class behind it.
_RETRACTED = (
    "No EPC breakdown in this tape.",
    "No arrears breakdown in this tape.",
    "No geographic breakdown in this tape.",
    "No property type breakdown in this tape.",
    "No rate type breakdown in this tape.",
)

#: Every distribution the page must render a state for. The page fetches all
#: five; before #630 it rendered three and silently dropped two.
_BREAKDOWN_KEYS = ("arrears", "rate_type", "epc", "property_type", "geographic")

#: The response fields each of those reads. A key present in ``BREAKDOWNS``
#: whose field is never picked would render a permanently empty section.
_BREAKDOWN_FIELDS = (
    "p.arrears_breakdown",
    "p.rate_type_breakdown",
    "p.epc_breakdown",
    "p.property_type_breakdown",
    "p.geographic_breakdown",
)

#: Distributions that genuinely apply to a corporate loan pool. A CLO obligor
#: has a domicile and can fall into arrears; only the mortgage-shaped concepts
#: may be called inapplicable, so these two appearing in the Corporate arm is
#: an over-claim, not a nicety.
_APPLICABLE_TO_CORPORATE = ("arrears", "geographic")


def _strip_comments(src: str) -> str:
    """Drop comments so a ban is not tripped by the prose explaining it (#575).

    ``//`` preceded by ``:`` is left alone — that is a URL scheme, not a
    comment leader.
    """
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.DOTALL)
    return re.sub(r"(?<!:)//[^\n]*", " ", src)


def _corporate_arm(src: str) -> str:
    """The body of ``NOT_APPLICABLE``'s ``Corporate`` entry.

    Anchored on the declaration *name* rather than on any text a check below
    tests, so a mutant rewriting the guarded content cannot delete the marker
    and make the slice raise instead of flag (#599).
    """
    start = src.find("const NOT_APPLICABLE")
    if start == -1:
        return ""
    corporate = src.find("Corporate:", start)
    if corporate == -1:
        return ""
    end = src.find("const BREAKDOWNS", corporate)
    return src[corporate : end if end != -1 else len(src)]


def _violations(raw: str) -> set[str]:
    """Every named check the source fails."""
    src = _strip_comments(raw)
    arm = _corporate_arm(src)
    bad: set[str] = set()

    # The retracted wording is gone. Paired with the positive checks below so
    # the ban cannot pass by the section it guards being deleted (#471, #568).
    for retracted in _RETRACTED:
        if retracted in src:
            bad.add("retracted-wording")

    # Every distribution renders, from one list — a sixth cannot be added
    # without a state, and none may be conditionally omitted.
    for key in _BREAKDOWN_KEYS:
        if f'key: "{key}"' not in src:
            bad.add("breakdown-missing-a-state")
    for field in _BREAKDOWN_FIELDS:
        if field not in src:
            bad.add("breakdown-field-never-read")
    if "BREAKDOWNS.map(" not in src:
        bad.add("breakdowns-not-rendered-from-one-loop")

    # Not-applicable is claimed from the tape's asset class, never from a
    # breakdown being empty (#457).
    if "NOT_APPLICABLE[assetClass]" not in src:
        bad.add("applicability-not-keyed-on-asset-class")
    if "latest.asset_class" not in src:
        bad.add("asset-class-never-read")

    # ...and only for the two mortgage-shaped concepts.
    if "epc:" not in arm or "property_type:" not in arm:
        bad.add("corporate-arm-incomplete")
    for key in _APPLICABLE_TO_CORPORATE:
        if f"{key}:" in arm:
            bad.add("over-claims-not-applicable")

    # The reason is the part worth reading, so it must reach the DOM, and the
    # absent wording must not blame the issuer for a column this platform has
    # merely not parsed.
    if "{state.reason}" not in src:
        bad.add("reason-never-rendered")
    if '"Not applicable" : "Not in this tape"' not in src.replace("\n", " "):
        bad.add("states-are-not-labelled-apart")
    if "does not claim the" not in src:
        bad.add("absent-wording-over-claims")

    return bad


#: ``(name, old, new, check)`` — rewriting the real source with ``old -> new``
#: must make ``check`` fire. A check no mutant reaches has never been observed
#: to fail, which is what :func:`test_no_check_is_decorative` refuses.
_MUTANTS: tuple[tuple[str, str, str, str], ...] = (
    (
        "reinstates the retracted EPC wording",
        "{state.reason}",
        '{"No EPC breakdown in this tape."}',
        "retracted-wording",
    ),
    (
        "drops the rate-type distribution",
        'key: "rate_type"',
        'key: "rate_type_disabled"',
        "breakdown-missing-a-state",
    ),
    (
        "stops reading the property-type field",
        "p.property_type_breakdown",
        "null",
        "breakdown-field-never-read",
    ),
    (
        "claims not-applicable from emptiness rather than asset class",
        "NOT_APPLICABLE[assetClass]",
        "NOT_APPLICABLE.Corporate",
        "applicability-not-keyed-on-asset-class",
    ),
    (
        "calls a CLO's geography inapplicable",
        "    epc: (annex) =>",
        "    geographic: (annex) => `n/a`,\n    epc: (annex) =>",
        "over-claims-not-applicable",
    ),
    (
        "renders the badge but swallows the reason",
        "{state.reason}",
        "{null}",
        "reason-never-rendered",
    ),
    (
        "blames the issuer for a column LoanWhiz has not parsed",
        "does not claim the",
        "proves the issuer published no",
        "absent-wording-over-claims",
    ),
    (
        "renders the sections by hand instead of from the list",
        "BREAKDOWNS.map(",
        "[].map(",
        "breakdowns-not-rendered-from-one-loop",
    ),
    (
        "hardcodes the asset class instead of reading the tape's",
        "latest.asset_class",
        '"Corporate"',
        "asset-class-never-read",
    ),
    (
        "drops property type from the Corporate arm",
        "    property_type: (annex) =>",
        "    property_type_unused: (annex) =>",
        "corporate-arm-incomplete",
    ),
    (
        "gives both stated absences the same label",
        '{notApplicable ? "Not applicable" : "Not in this tape"}',
        "{null}",
        "states-are-not-labelled-apart",
    ),
)


@pytest.fixture(scope="module")
def source() -> str:
    return _PAGE.read_text(encoding="utf-8")


def test_the_pool_page_states_every_absence(source: str) -> None:
    """The live source passes every check."""
    assert _violations(source) == set()


def test_the_corporate_arm_is_present_and_reasoned(source: str) -> None:
    """The slice the over-claim check reads is non-empty, and carries a reason.

    Without this, deleting ``NOT_APPLICABLE`` entirely would empty the slice and
    every assertion over it would pass by having nothing to read (#568).
    """
    arm = _corporate_arm(_strip_comments(source))
    assert arm.strip(), "the NOT_APPLICABLE Corporate arm is gone"
    assert "corporate loan pool" in arm, (
        "the not-applicable reason must name why the concept does not apply, "
        "not merely that it does not"
    )


@pytest.mark.parametrize(
    ("name", "old", "new", "check"),
    _MUTANTS,
    ids=[m[0] for m in _MUTANTS],
)
def test_each_mutant_is_caught(
    source: str, name: str, old: str, new: str, check: str
) -> None:
    """Every rewrite above must be rejected by the check that names it."""
    assert old in source, f"mutant {name!r} no longer applies to the source"
    assert check in _violations(source.replace(old, new, 1)), (
        f"mutant {name!r} was not caught by check {check!r}"
    )


def test_no_check_is_decorative() -> None:
    """Every check must be the one that catches at least one mutant."""
    source = _PAGE.read_text(encoding="utf-8")
    reached = set()
    for _name, old, new, _check in _MUTANTS:
        reached |= _violations(source.replace(old, new, 1))
    all_checks = {
        "retracted-wording",
        "breakdown-missing-a-state",
        "breakdown-field-never-read",
        "breakdowns-not-rendered-from-one-loop",
        "applicability-not-keyed-on-asset-class",
        "over-claims-not-applicable",
        "reason-never-rendered",
        "absent-wording-over-claims",
        "asset-class-never-read",
        "corporate-arm-incomplete",
        "states-are-not-labelled-apart",
    }
    assert all_checks <= reached, f"never observed to fail: {all_checks - reached}"
