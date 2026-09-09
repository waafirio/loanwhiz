"""Guards on the MCP page, and above all on its authentication sketch.

The MCP server has no authentication of any kind — no token, no bearer, no API
key. That is a deliberate decision: LoanWhiz is intended to run on top of
waafir-platform, which owns that concern. The page may still draw a setup flow
as an illustration of where authentication is going.

**The whole risk is a viewer mistaking that illustration for a mechanism.** A
credential setup rendered like a working one, in front of an institutional
audience, is a false claim about a security property — the most expensive
confident-wrong this platform could ship, and the exact inverse of what makes
it credible everywhere else, where a synthetic tape says so and a refusal names
its reason.

So the marking is asserted here rather than left to an author's care. The
precedent is not hypothetical: #484 committed synthetic pools correctly
labelled *in the data*, and the Pool and Waterfall pages still render no
synthetic badge — labelling that lives only in an author's intention does not
survive. #571 met the same boundary from the other side: its provenance bypass
guard walks ``src/loanwhiz/**`` Python only, so a ``web/`` qualifier was
guaranteed to *arrive* present and never to be *displayed*.

Guarded from Python because the repo has no JS test runner; the file is a
string in either language. This is the same route
``test_the_user_facing_no_tape_card_does_not_carry_the_retracted_claim`` in
``tests/test_capability_matrix.py`` takes to reach ``page-states.tsx``.

**These guards ban markup, not prose.** #471's lesson is that a grep over
prose must ban the whole assertion rather than a fragment, because a sentence
that *refuses* a claim contains the claim's words — "this server has no
authentication" would trip a naive ban on "authentication". Structural markup
carries no such ambiguity: an ``<input>`` or an ``onSubmit`` in this section is
the thing that makes a drawing look operable, whoever wrote it and however they
worded the caption around it.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SKETCH = _REPO_ROOT / "web" / "components" / "mcp-auth-sketch.tsx"
_PAGE = _REPO_ROOT / "web" / "app" / "(routes)" / "mcp" / "page.tsx"

# Markup that would make a drawn flow look operable. None of these can be
# tripped by a caption disclaiming the flow — they are elements and handlers,
# not words about them.
_OPERABLE_MARKUP = (
    "<input",
    "<form",
    "<textarea",
    'type="password"',
    "onSubmit",
    "onClick",
    "action=",
    "fetch(",
    "useState",
)


def _code_only(source: str) -> str:
    """Strip comments, so the markup ban reads code rather than prose about it.

    Without this the guard trips on its own subject's documentation: the
    sketch's header comment says it has "no ``onSubmit``", and a naive
    substring ban flags exactly the sentence that promises the property. That
    is #471's lesson — ban the assertion, not a fragment — recurring one layer
    down, and the fix is to scan the region the claim is actually about.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return "\n".join(
        line
        for line in source.splitlines()
        if not line.lstrip().startswith(("//", "*"))
    )


def _sketch() -> str:
    return _SKETCH.read_text(encoding="utf-8")


def _page() -> str:
    return _PAGE.read_text(encoding="utf-8")


def test_the_auth_sketch_marks_itself_as_not_implemented() -> None:
    """The marking is present, and the sketch it marks is still there.

    The second half matters as much as the first: a ban-only guard passes by
    deleting the thing it guards, so this pins the sketch's own content too.
    If the section is ever genuinely removed, this test should be removed with
    it — deliberately, by someone who read this docstring.
    """
    sketch = _sketch()
    assert "NOT IMPLEMENTED" in sketch
    assert "this server has no authentication" in sketch
    # ...and it still draws the flow, so this cannot pass by gutting the card.
    assert "Register the client" in sketch
    assert "Scope the tool surface" in sketch


def test_the_auth_sketch_names_where_authentication_will_live() -> None:
    """A sketch that does not name its destination is just a missing feature.

    Naming waafir-platform is what turns "unauthenticated" from an oversight
    into a stated architectural decision the reader can evaluate.
    """
    sketch = _sketch()
    assert "waafir-platform" in sketch
    assert "Nothing on this page authenticates anything today" in sketch


def test_every_drawn_step_carries_the_marking() -> None:
    """A crop of any one step must still read as a sketch.

    The steps render from a single ``SKETCH_STEPS.map(...)``, and the marking
    badge lives inside that map — so a step cannot be added without one. This
    asserts the badge is genuinely inside the map body rather than only in the
    card header, which is what a crop of a middle step would miss.
    """
    sketch = _sketch()
    start = sketch.index("SKETCH_STEPS.map(")
    end = sketch.index("))}", start)
    assert "{SKETCH_MARKING}" in sketch[start:end], (
        "the per-step marking badge is no longer rendered inside the step map; "
        "a screenshot of a single step would not read as a sketch"
    )


def test_the_auth_sketch_is_inert() -> None:
    """The sketch cannot do anything, and that is enforced rather than assumed.

    This is the guard that fires if someone starts *wiring the sketch up* —
    the failure mode where the drawing quietly becomes a real credential form
    while its caption still calls it an illustration.
    """
    sketch = _code_only(_sketch())
    for markup in _OPERABLE_MARKUP:
        assert markup not in sketch, (
            f"{markup!r} appears in the authentication sketch — it must stay "
            "inert; a drawn flow that can be interacted with is no longer a drawing"
        )


def test_the_mcp_page_draws_no_credential_form_of_its_own() -> None:
    """Confinement: the page must not grow a second, unmarked setup flow.

    The sketch is the one place the future flow is drawn, and it is marked. A
    credential form added directly to the page would inherit none of that.
    """
    page = _code_only(_page())
    for markup in _OPERABLE_MARKUP:
        if markup == "useState":
            continue  # the page legitimately holds fetched surface data
        assert markup not in page, (
            f"{markup!r} appears on the MCP page outside the marked sketch"
        )


def test_the_mcp_page_renders_the_sketch() -> None:
    """The marking only protects a viewer if the marked component is on screen.

    Without this, the sketch file could keep every assertion above while the
    page rendered something else entirely.
    """
    page = _page()
    assert "McpAuthSketch" in page
    assert "@/components/mcp-auth-sketch" in page


def test_the_mcp_page_transcribes_no_tally() -> None:
    """Counts are derived from the rows rendered, never written into the page.

    ``GET /mcp/surface`` deliberately ships no tally, because two tallies
    transcribed into prose in this repo went stale in silence (#484, #492). A
    literal "6 tools" here would reintroduce exactly that.
    """
    page = _page()
    stale = re.findall(r"\b\d+\s+(?:tools|primitives|of them)\b", page)
    assert not stale, f"a count is transcribed into the MCP page: {stale}"


def test_the_quickstart_points_at_the_endpoint_rather_than_restating_it() -> None:
    """The docs name the surface; they do not mirror its contents."""
    quickstart = (_REPO_ROOT / "docs" / "quickstart.md").read_text(encoding="utf-8")
    assert "GET /mcp/surface" in quickstart
