"""Guards on the due-diligence screen, and above all on how it renders a refusal.

#567 produces a record whose whole value is an asymmetry: over the shipped
registry it verifies **one** deal and refuses the rest by name. #549's rule is
the binding one on any surface built over it — *a refusal that keeps the value
is not a refusal; ``not_evaluable`` protects the grade, not the screen.* An
evidence file that shows the green item and hides the unestablished ones is
worse than one showing nothing, because a compliance officer will sign it.

**The whole risk is that the refusals render quietly.** Not by being deleted —
that would be visible — but by being second, collapsed, clamped, or reduced to
a ratio that reads as a rounding error. Each of those is a one-line edit that
no reviewer would flag and no runtime error would catch, so the marking is
asserted here rather than left to an author's care.

The precedent is not hypothetical, and it is in this repo. #484 committed
synthetic pools labelled correctly **in the data**, and the Pool and Waterfall
pages still render no synthetic badge; ``PackBody`` still resolves its
provenance badge to "direct ingestion" for a ``derived`` or ``synthetic``
source. Labelling that lives only in an author's intention does not survive.

Guarded from Python because the repo has no JS test runner; the file is a
string in either language. This is the same route
``test_the_user_facing_no_tape_card_does_not_carry_the_retracted_claim`` in
``tests/test_capability_matrix.py`` takes to reach ``page-states.tsx``. **It is
a real limit, not a disclaimed one:** a guard over source cannot see what a
browser paints. It can prove the affordance is absent from the file; it cannot
prove the rendered screen reads the way this docstring says it should.

**These guards ban markup and identifiers, not prose.** #471's lesson is that a
grep over prose must ban the whole assertion rather than a fragment, because a
sentence that *refuses* a claim contains the claim's words — this file's own
subject documents itself as "never collapsed, never truncated", which a naive
ban on "truncate" would flag. Comments are therefore stripped before scanning,
and the bans name elements, class names and call shapes.

**A ban list is only as good as the affordances it enumerates.** It is blind to
a refusal hidden through a wrapper it does not name — a future ``<Disclosure/>``
or a CSS module class would pass. That is a real blind spot; the list below
already grew once, when writing it surfaced that a ``Tabs`` component putting
refusals on a non-default tab defeats every ban aimed at ``<details>``.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SHEET = _REPO_ROOT / "web" / "components" / "evidence-pack-sheet.tsx"
_PAGE = _REPO_ROOT / "web" / "app" / "(routes)" / "due-diligence" / "page.tsx"
_NAV = _REPO_ROOT / "web" / "lib" / "nav.ts"

#: Where the due-diligence surface begins inside the shared evidence-pack file.
#: The region is taken to the end of the file, so it is a *superset* of the
#: component — a ban that over-reaches fails loudly, one that under-reaches
#: passes silently, and only the second is dangerous here.
_REGION_MARKER = "Investor due-diligence record (#568"

# Ways to put a refusal on screen without a reader seeing it. None of these can
# be tripped by a caption disclaiming them — they are elements, class names and
# call shapes, not words about them.
_HIDING_AFFORDANCES = (
    "<details",
    "<summary",
    "Accordion",
    "Collapsible",
    "Disclosure",
    "<Tabs",
    "TabsContent",
    "useState",          # a local expand/collapse toggle
    "overflow-hidden",
    "max-h-",
    "line-clamp",
    "truncate",
    "text-ellipsis",
    ".slice(",
    ".substring(",
)

# Ways to reduce the two kinds to one grade. #549's rule again: the number is
# what a reader treats as the measurement, so this surface must carry none.
_SCORE_SHAPES = (
    "formatPct",
    "toFixed",
    "percent",
    "aggregate_confidence",
    # The two ways to build a grade out of the two kinds. `.length /` is the
    # ratio; `.length +` is the denominator of "1 of 7 verified", which this
    # file's own review found slipping past every other ban here — a grade with
    # no division in it. Both are banned because both produce the number a
    # reader treats as the measurement (#549).
    ".length /",
    ".length +",
)

# The capability matrix's trichotomy. #567 deliberately does not share it: that
# vocabulary answers "did the primitive run, and was its output reconciled?" —
# a question about this platform — while this one answers "is the regulatory
# fact established?", a question about the deal's documents. Sharing the words
# would invite reading one as the other.
_MATRIX_VOCABULARY = ("not-applicable", "validated")


def _code_only(source: str) -> str:
    """Strip comments, so the bans read code rather than prose about it.

    Without this every guard trips on its own subject's documentation: the
    component's header says the refusals are "never collapsed, never
    truncated", and a naive substring ban flags exactly the sentence promising
    the property. That is #471's lesson recurring one layer down — ban the
    assertion, not a fragment — and the fix is to scan the region the claim is
    actually about.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return "\n".join(
        line
        for line in source.splitlines()
        if not line.lstrip().startswith(("//", "*"))
    )


def _region() -> str:
    """The due-diligence surface's own source, comments stripped."""
    sheet = _SHEET.read_text(encoding="utf-8")
    assert _REGION_MARKER in sheet, (
        "the due-diligence section marker is gone from evidence-pack-sheet.tsx; "
        "every ban below would scan an empty string and pass vacuously"
    )
    return _code_only(sheet[sheet.index(_REGION_MARKER):])


def _page() -> str:
    return _code_only(_PAGE.read_text(encoding="utf-8"))


def test_the_refusals_render_before_the_verifications() -> None:
    """Order is the cheapest way to bury a refusal, so it is pinned.

    Both headings must exist and the unestablished one must come first. The
    presence half matters as much as the order: an order-only assertion passes
    when the refusal section is deleted outright and ``str.index`` raises —
    which is why both are asserted explicitly rather than inferred.
    """
    region = _region()
    assert "Not established" in region
    assert "Verified" in region
    assert region.index("Not established") < region.index("Verified"), (
        "the verified section now renders before the not-established one; a "
        "compliance reader sees the green items first"
    )


def test_no_affordance_can_hide_a_refusal() -> None:
    """A refusal behind a disclosure widget or a clamp is not on the screen."""
    region = _region()
    for affordance in _HIDING_AFFORDANCES:
        assert affordance not in region, (
            f"{affordance!r} appears in the due-diligence surface — a refusal "
            "must not be collapsible, clamped, tabbed away or sliced"
        )


def test_the_record_renders_no_aggregate_score() -> None:
    """A ratio turns two kinds into one grade, and a grade invites signing it."""
    region = _region()
    for shape in _SCORE_SHAPES:
        assert shape not in region, (
            f"{shape!r} appears in the due-diligence surface — the two kinds "
            "are counted separately and never reduced to a score"
        )


def test_both_outcomes_render_through_one_component() -> None:
    """Two renderers is how the weights drift apart later, a line at a time.

    A single ``CheckItem`` used by both sections makes equal weight structural
    rather than a thing an author has to keep remembering.
    """
    region = _region()
    assert region.count("<CheckItem") == 2, (
        "the two outcome sections no longer render through the same component; "
        "their visual weight can now drift apart independently"
    )
    # ...and it still branches on the outcome, so this cannot pass by rendering
    # the two kinds identically and losing the distinction altogether.
    assert 'check.outcome === "verified"' in region


def test_a_refusal_carries_the_document_it_looked_in() -> None:
    """"Not established" is a claim about a document, never about the deal (#480).

    A reader must be able to re-ask the question of a document this reading did
    not reach, which needs the slot and the URL that were consulted — and, for
    Contego, the registration note saying *which* of its two Listing
    Particulars answered.
    """
    region = _region()
    for field in ("source.url", "source.registry_slot"):
        assert f"{{{field}}}" in region, (
            f"{field} is no longer rendered; a refusal that does not name the "
            "document it read cannot be challenged"
        )

    # The registration note is asserted through its own CELL, not by the field
    # appearing anywhere in the region. This guard's first version checked only
    # that `source.registry_note` occurred somewhere — and it survived replacing
    # the rendered `{source.registry_note}` with `{null}`, because the field is
    # *also* named in the conditional that decides whether to draw the row. A
    # reference is not a rendering; only the cell's contents prove the note
    # reaches a reader. This is Contego's whole point: without the note, nothing
    # on screen says which of its two Listing Particulars answered.
    start = region.index("<dt>Registry note</dt>")
    cell = region[start : region.index("</dd>", start)]
    assert "{source.registry_note}" in cell, (
        "the registry-note cell no longer renders the note itself; a reader "
        "cannot tell which of a deal's documents was consulted"
    )

    # The reason itself renders on both outcomes, unconditionally.
    assert "{check.reason}" in region


def test_read_at_is_never_rendered_as_the_document_date() -> None:
    """Our clock is not the document's date, and the screen must not blur them.

    No registry field carries a publication date, so ``document_date`` is
    ``None`` for every shipped deal and the row shows ``document_date_reason``
    instead. Substituting ``read_at`` there would be #479's error in a new
    place: an ``extracted_at`` is when *we* read the file, and passing it off
    as the document's own date is confident wrongness rather than an admitted
    gap. This scans the document-date cell specifically, not the whole file —
    ``read_at`` legitimately appears elsewhere, under its own label.
    """
    region = _region()
    start = region.index("<dt>Document date</dt>")
    cell = region[start : region.index("</dd>", start)]
    assert "document_date" in cell
    assert "read_at" not in cell, (
        "the document-date cell now renders read_at — LoanWhiz's own clock "
        "shown as the document's publication date"
    )
    # And the read timestamp still renders, under a label naming whose clock it
    # is, so this cannot pass by dropping the honest field instead.
    assert "<dt>Read by LoanWhiz</dt>" in region
    assert "source.read_at" in region


def test_the_surface_does_not_borrow_the_capability_matrix_vocabulary() -> None:
    """Two trichotomies that answer different questions must not share words."""
    region = _region()
    for word in _MATRIX_VOCABULARY:
        assert word not in region, (
            f"{word!r} appears in the due-diligence surface — that is the "
            "capability matrix's vocabulary and answers a different question"
        )


def test_the_screen_says_it_is_not_the_deal_s_compliance() -> None:
    """The separation is the umbrella's argument; the screen has to state it.

    A reader who lands here from ``/compliance`` needs to be told these answer
    different questions, or the two surfaces silently merge in their head.

    Whitespace is collapsed first: this is the one assertion over rendered
    prose rather than markup, and JSX reflows a sentence across lines whenever
    the surrounding indentation changes. A guard that a reformat can red is a
    guard someone deletes.
    """
    region = " ".join(_region().split())
    assert "covenant compliance" in region


def test_the_route_is_reachable_and_sits_beside_governance() -> None:
    """A screen nobody can navigate to renders nothing, correctly."""
    nav = _NAV.read_text(encoding="utf-8")
    assert '"/due-diligence"' in nav, "the due-diligence route is not in the sidebar"

    # Asserted against the GROUP boundary, not against a neighbour's position.
    # The first version of this compared route indices pairwise, which passes or
    # fails on the incidental ordering of entries this test has no opinion
    # about; the claim being made is which *group* each route sits in.
    boundary = nav.index('label: "Platform & Governance"')
    assert nav.index('"/compliance"') < boundary, (
        "Compliance left the Deal Analytics group — it answers whether the DEAL "
        "is inside its covenants, a different question for a different reader"
    )
    assert boundary < nav.index('"/due-diligence"'), (
        "the due-diligence route left the Platform & Governance group; it now "
        "sits beside the covenant screen it is deliberately not part of"
    )
    assert nav.index('"/governance"') < nav.index('"/due-diligence"')


def test_the_page_renders_the_record_for_the_selected_deal() -> None:
    """A record painted under the wrong deal's heading is the one fatal error.

    The page keys its state by the deal the response is *for*, so a slow
    response for a previously-selected deal cannot land under the current one.
    """
    page = _page()
    assert "getDueDiligence" in page
    assert "state.dealId === dealId" in page, (
        "the page no longer discards a response for a different deal; a "
        "record can now paint under the wrong deal's name"
    )
