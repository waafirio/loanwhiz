"""Where the "Ask LoanWhiz" launcher is allowed to live (#623).

``web/`` has no JS test runner, so this asserts the components' **source**, not
their rendered output — the same trade ``tests/test_concentration_page.py``
makes, stated rather than implied. **A source guard proves the code says a
thing; it never proves what a browser paints.** That bound is the whole reason
this file exists: the defect it guards was found by screenshot and by no
existing check. The launcher was a `fixed bottom-6 right-6 z-50` pill, so it
held the viewport's bottom-right corner through any scroll and painted over
whatever the page rendered there. On ``/concentration`` at 1440x900 that was a
balance figure — ``elementsFromPoint`` at the pill's centre returned the button
over ``td:€65,550,004`` — and a half-covered number on the surface whose whole
argument is that every figure is shown honestly is worse than no number.

Two things follow, and both are checked here.

**Not-pinned is not the same as out-of-the-way.** Measured at four scroll
offsets, the pinned pill covered four *different* balance cells, so reserving
clearance under the last row would have fixed one of them; and lowering the
z-index only hides the control behind the table, which is the same defect
wearing the other hat. The fix has to put the launcher somewhere the page
never paints: the sidebar rail is reserved layout width (content starts at
256px expanded, 48px icon-collapsed — measured both ways), so
:func:`_launcher_is_a_rail_control` requires the trigger to render the rail's
own button, not merely to have lost its positioning classes.

**A ban is only worth what it rejects.** :data:`_MUTANTS` rewrites the real
source the way a regression would and requires :func:`_violations` to catch
each rewrite; :func:`test_no_check_is_decorative` then requires every check to
be the one that catches at least one of them. A check no mutant reaches has
never been observed to fail.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _REPO_ROOT / "web" / "components" / "chat-panel.tsx"
_SIDEBAR = _REPO_ROOT / "web" / "components" / "app-sidebar.tsx"
_LAYOUT = _REPO_ROOT / "web" / "app" / "layout.tsx"


def _sources() -> dict[str, str]:
    """The three real files, as committed."""
    return {
        "panel": _PANEL.read_text(encoding="utf-8"),
        "sidebar": _SIDEBAR.read_text(encoding="utf-8"),
        "layout": _LAYOUT.read_text(encoding="utf-8"),
    }


def _code_only(source: str) -> str:
    """Strip comments, so prose *about* the banned thing does not trip its ban.

    The shape ``tests/test_concentration_page.py`` uses, for the same reason:
    both files here carry a comment explaining that the launcher must not be
    `fixed bottom-6 right-6`, and a ban reading that comment as code would fire
    on the documentation of the rule it enforces. Strings are deliberately NOT
    stripped — a className is a string literal, and it is exactly what is being
    policed.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    source = re.sub(r"\{/\*.*?\*/\}", "", source, flags=re.S)
    return "\n".join(
        line
        for line in source.splitlines()
        if not line.lstrip().startswith(("//", "*"))
    )


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

#: Tailwind utilities that take an element out of normal flow and pin it to the
#: viewport (or to an ancestor), plus the z-index that decides what it covers.
#: Written as class *tokens* — bounded by quote, space or brace — so
#: ``side="right"`` and ``justify-start`` cannot match, and so the ban stays
#: narrow enough that over-reach cannot false-positive (#599). A corner offset
#: needs a digit or a bracket after it: ``right-6``, ``inset-y-0``, ``z-[60]``.
_PINNING = (
    ("position", re.compile(r"(?<![\w-])(?:fixed|absolute|sticky)(?![\w-])")),
    ("offset", re.compile(r"(?<![\w-])(?:top|bottom|left|right|inset(?:-[xy])?)-(?:\d|\[)")),
    ("z-index", re.compile(r"(?<![\w-])z-(?:\d|\[)")),
)

#: The class strings the ban actually reads. Scanning only what appears inside
#: a `className="…"` keeps the ban off prose and off prop values that merely
#: share a word with a utility.
_CLASSNAME = re.compile(r'className=(?:"([^"]*)"|\{`([^`]*)`\})')


def _launcher_carries_no_pinning(sources: dict[str, str]) -> list[str]:
    """The launcher's component pins nothing to the viewport.

    Whole-file, because a ban scoped to a slice passes the moment the slice is
    empty (#568/#599): deleting the marker a region starts at would leave every
    `assert x not in region` asserting over `""`. The *positive* rule below
    takes the bounded slice instead.
    """
    out = []
    for match in _CLASSNAME.finditer(_code_only(sources["panel"])):
        classes = match.group(1) or match.group(2) or ""
        for label, pattern in _PINNING:
            hit = pattern.search(classes)
            if hit:
                out.append(
                    f"chat-panel.tsx pins the launcher ({label}: {hit.group(0)!r} "
                    f'in className="{classes}") — a viewport-pinned control '
                    "covers page content at every scroll offset (#623)"
                )
    return out


def _launcher_is_a_rail_control(sources: dict[str, str]) -> list[str]:
    """The trigger renders the sidebar rail's own button.

    The positive half of the rule, and the one that carries the actual
    guarantee: losing the positioning classes would leave the launcher in
    whatever container it was mounted in, which for a floating element is the
    page. Rendering ``SidebarMenuButton`` is what makes "in the rail" true.

    Bounded to the trigger, and anchored on ``SheetTrigger`` — the element's
    own name, which no check here tests, rather than on text a rule inside the
    slice reads (#599).
    """
    panel = _code_only(sources["panel"])
    if "<SheetTrigger" not in panel:
        return [
            "chat-panel.tsx has no <SheetTrigger — the launcher's anchor is "
            "gone, so this check would scan an empty string and pass vacuously"
        ]
    start = panel.index("<SheetTrigger")
    end = panel.find("</SheetTrigger>", start)
    region = panel[start:] if end == -1 else panel[start:end]
    if "Ask LoanWhiz" not in region:
        return [
            "the <SheetTrigger region no longer names the launcher — the "
            "region shrank, so this check would scan too little and pass"
        ]
    if "SidebarMenuButton" not in region:
        return [
            "the launcher's trigger no longer renders a SidebarMenuButton, so "
            "it is not a rail control — only the rail is layout width the page "
            "never paints into (#623)"
        ]
    return []


def _launcher_is_mounted_in_the_rail_footer(sources: dict[str, str]) -> list[str]:
    """``<ChatPanel />`` is mounted inside the rail's footer.

    A bounded slice for a positive rule, per #599: anything appended after the
    footer would satisfy a whole-file "the source mentions ChatPanel".
    """
    sidebar = _code_only(sources["sidebar"])
    if "<SidebarFooter>" not in sidebar or "</SidebarFooter>" not in sidebar:
        return [
            "app-sidebar.tsx has no <SidebarFooter> — the launcher's mount "
            "point is gone, so this check would scan an empty string and pass"
        ]
    start = sidebar.index("<SidebarFooter>")
    footer = sidebar[start : sidebar.index("</SidebarFooter>", start)]
    if "<ChatPanel" not in footer:
        return [
            "<ChatPanel /> is not inside app-sidebar.tsx's <SidebarFooter> — "
            "mounted anywhere else it floats over the page again (#623)"
        ]
    return []


def _layout_mounts_no_floating_launcher(sources: dict[str, str]) -> list[str]:
    """The app shell does not mount the launcher beside the page content.

    Whole-file ban. ``layout.tsx`` renders the chrome around every route, so a
    ``<ChatPanel />`` here is a launcher floating over the content — which is
    where this one came from.
    """
    if "ChatPanel" in _code_only(sources["layout"]):
        return [
            "layout.tsx mounts ChatPanel beside the page content; it belongs "
            "in the sidebar rail's footer, which is reserved width (#623)"
        ]
    return []


_CHECKS = {
    "no-pinning": _launcher_carries_no_pinning,
    "rail-control": _launcher_is_a_rail_control,
    "rail-footer": _launcher_is_mounted_in_the_rail_footer,
    "not-in-layout": _layout_mounts_no_floating_launcher,
}


def _violations(sources: dict[str, str]) -> dict[str, list[str]]:
    """Every check's findings, keyed by check id."""
    return {name: check(sources) for name, check in _CHECKS.items()}


# ---------------------------------------------------------------------------
# The guard
# ---------------------------------------------------------------------------


def test_the_launcher_stays_out_of_the_data_region() -> None:
    """The committed source places the launcher in the rail, unpinned."""
    found = {k: v for k, v in _violations(_sources()).items() if v}
    assert not found, "\n".join(
        f"[{name}] {message}" for name, messages in found.items() for message in messages
    )


#: Rewrites of the real source, each the shape of a regression that has either
#: happened here or is one edit away. ``(id, file key, old, new)``; every one
#: must be caught by at least one check, and every check must catch at least
#: one of these.
_MUTANTS: tuple[tuple[str, str, str, str], ...] = (
    (
        "the original defect: a pinned corner pill",
        "panel",
        "<SidebarMenuButton\n                variant=\"outline\"",
        "<SidebarMenuButton\n                className=\"fixed bottom-6 right-6 z-50\"\n                variant=\"outline\"",
    ),
    (
        "pinned with an arbitrary-value offset instead of a scale step",
        "panel",
        "<SidebarMenuButton\n                variant=\"outline\"",
        "<SidebarMenuButton\n                className=\"absolute bottom-[24px] right-[24px]\"\n                variant=\"outline\"",
    ),
    (
        "left in the rail but raised above the table",
        "panel",
        "<SidebarMenuButton\n                variant=\"outline\"",
        "<SidebarMenuButton\n                className=\"z-[60]\"\n                variant=\"outline\"",
    ),
    (
        "trigger reverts to a plain button, so nothing holds it in the rail",
        "panel",
        "<SidebarMenuButton\n                variant=\"outline\"\n                tooltip=\"Ask LoanWhiz\"",
        "<Button\n                size=\"sm\"\n                tooltip=\"Ask LoanWhiz\"",
    ),
    (
        "launcher dropped from the rail footer",
        "sidebar",
        "      <SidebarFooter>\n        <ChatPanel />\n      </SidebarFooter>\n",
        "      <SidebarFooter />\n",
    ),
    (
        "launcher moved out of the footer but still rendered by the rail",
        "sidebar",
        "      <SidebarFooter>\n        <ChatPanel />\n      </SidebarFooter>\n",
        "      <SidebarFooter />\n      <ChatPanel />\n",
    ),
    (
        "app shell mounts a second launcher beside the content",
        "layout",
        "            </SidebarInset>\n",
        "            </SidebarInset>\n            <ChatPanel />\n",
    ),
)


def _mutate(key: str, old: str, new: str) -> dict[str, str]:
    sources = _sources()
    assert old in sources[key], (
        f"mutant anchor {old!r} is not in {key} — the mutant no longer rewrites "
        "anything, so the check it exercises is unobserved"
    )
    sources[key] = sources[key].replace(old, new, 1)
    return sources


@pytest.mark.parametrize(
    ("name", "key", "old", "new"),
    _MUTANTS,
    ids=[m[0] for m in _MUTANTS],
)
def test_each_regression_is_caught(name: str, key: str, old: str, new: str) -> None:
    """Every rewrite above is rejected by at least one check."""
    found = [m for messages in _violations(_mutate(key, old, new)).values() for m in messages]
    assert found, f"no check rejects {name!r}"


def test_no_check_is_decorative() -> None:
    """Every check catches at least one mutant.

    A check no mutant reaches is a check that has never been observed to fail —
    indistinguishable, from the outside, from one that cannot.
    """
    caught: set[str] = set()
    for _name, key, old, new in _MUTANTS:
        for check_id, messages in _violations(_mutate(key, old, new)).items():
            if messages:
                caught.add(check_id)
    idle = sorted(set(_CHECKS) - caught)
    assert not idle, f"no mutant is caught by {idle} — those checks are decorative"
