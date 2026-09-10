"""No committed surface may state a capability tally the matrix disagrees with.

The defect this pins is not a wrong number, it is a **transcribed** one. Three
documents each published a different hand-written tally and the matrix reported
none of them: ``README.md`` said ``2 validated / 15 ran / 13 not-applicable``,
``SYSTEM-STATUS.md`` said ``1 validated / 14 ran / 15 not-applicable`` "over 6
deal columns", and ``presentation/loanwhiz-deck.json`` still carried the
``1 validated, 9 ran, 15 not-applicable`` that #441 had already recorded as
superseded. Correcting them by hand would buy about three weeks — the next deal
registration moves the tally and every copy is wrong again, silently.

So the figure has one definition (``scripts/render_capability_tally.render``)
and the documents carry a generated region filled from it. This module holds
both halves of the contract:

* the regions **equal** what the matrix says right now, derived through the
  production wiring rather than a fixture (#574 — a census taken against your
  own stand-in agrees with the stand-in);
* no listed surface states a tally or a validated-cell count **outside** a
  region, in numerals or in prose.

Both directions are asserted on purpose. A ban list alone passes by deleting
the paragraph, and a slice-scoped guard passes vacuously the moment the slice
is empty (#568) — so the fences are asserted **present** before anything is
compared, and each document is required to still carry its claim.

This file names the banned strings, which is why it scans only the documents
listed below and never itself (#575: the prose promising a property must not
trip the guard enforcing it).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from loanwhiz.api.main import capability_matrix
from scripts.render_capability_tally import (
    GENERATED_DOCS,
    MARKER_END,
    MARKER_START,
    main,
    render,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

#: Every committed surface that talks about what is validated. A tally is only
#: fixed once no copy of it disagrees, so this list is the fix's real extent —
#: the two documents the issue named plus every other one carrying the claim.
GUARDED_SURFACES: tuple[str, ...] = (
    "README.md",
    "SYSTEM-STATUS.md",
    "docs/data-card.md",
    "docs/model-card.md",
    "docs/quickstart.md",
    "docs/tape-ingestion.md",
    "presentation/loanwhiz-deck.json",
    "presentation/build_deck.py",
    "web/app/(routes)/showcase/page.tsx",
    "web/components/capability-matrix-grid.tsx",
)

#: A tally written as numerals. Two refinements, both learned from real hits:
#: ``ran`` is pinned to a preceding count or it matches ordinary prose ("the
#: engine ran"), and the count must not be an issue reference — ``#496 ran it``
#: is a sentence about history, not a tally, and banning it would have made
#: this guard unsatisfiable on the data card.
_TRANSCRIBED_TALLY = re.compile(
    r"(?<![#\w])\d+\s+(?:validated|ran|not-applicable|not applicable)\b", re.I
)

#: The same claim in prose. Each of these was committed and each was false when
#: this ran: the matrix carries two validated cells, not one, and Green Lion
#: 2026-1 — named as "the validated tape-driven deal" — has none at all.
_RETRACTED_CLAIMS: tuple[str, ...] = (
    "the single validated cell",
    "the single `validated` cell",
    "only one deal is externally validated",
    "exactly one cell is validated",
    "the one validated deal",
    "the validated tape-driven deal is",
    "is the only `validated` cell",
    "validated to the cent on one real deal",
    # The deck asserted this of a deal whose engine-validation cell reads
    # not-applicable: it has no committed answer key, so there is nothing for
    # the engine to be reconciled against.
    "validated on green lion 2026-1",
)


@pytest.fixture(scope="module")
def sentence() -> str:
    """The tally as the live matrix reports it, through the production wiring."""
    return render()


def _read(relative_path: str) -> str:
    return (_REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _flatten(text: str) -> str:
    """Prose with markup and JSX interpolations removed, whitespace collapsed.

    The claim this guard bans was committed as::

        The single{" "}
        <span className="...">validated</span> cell

    — one sentence broken by an interpolation, a tag and two newlines. A literal
    ban over the raw file does not see it, and that is not hypothetical: it is
    how the live page came to render "The single validated cell" beside a tally
    reading 2, and the whole-repo sweep found it while a literal ban did not.
    Flattening first makes the guard read the sentence a *viewer* sees rather
    than the bytes the file holds.
    """
    text = re.sub(r"\{\s*[\"'][^\"']*[\"']\s*\}", " ", text)  # {" "} and friends
    text = re.sub(r"<[^>]*>", " ", text)  # JSX/HTML tags, markdown comments
    text = text.replace("&apos;", "'").replace("&amp;", "&")
    return re.sub(r"\s+", " ", text)


def _retracted_claims_in(text: str) -> list[str]:
    """Every banned claim visible in *text* once it is read as prose."""
    return [c for c in _RETRACTED_CLAIMS if c in _flatten(_strip_regions(text)).lower()]


def _strip_regions(text: str) -> str:
    """Prose with the generated regions removed.

    The generated sentence *is* a transcribed-looking tally; it is the one
    place a tally is allowed, because it is regenerated rather than typed. The
    ban below therefore runs over everything else.
    """
    return re.sub(
        re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END), "", text, flags=re.S
    )


def test_every_generated_region_matches_the_matrix(sentence: str) -> None:
    """The published tally equals what `GET /capability-matrix` reports.

    This is the assertion the issue asked for: the documents and the matrix
    cannot disagree without something going red. Register a deal or land an
    answer key and this reds — and the whole fix is
    ``python -m scripts.render_capability_tally --write``, never retyping a
    number.
    """
    for relative_path in GENERATED_DOCS:
        text = _read(relative_path)
        # Asserted before the slice is taken, so the guard cannot pass by the
        # region vanishing (#568).
        assert MARKER_START in text, f"{relative_path} has no capability-tally region"
        assert MARKER_END in text, f"{relative_path} has an unterminated region"
        region = text.split(MARKER_START, 1)[1].split(MARKER_END, 1)[0].strip()
        assert region == sentence, (relative_path, region, sentence)


def test_the_sentence_is_derived_from_the_matrix_not_hard_coded() -> None:
    """Feed a different matrix and every figure in the sentence moves.

    Without this, `render` could return the correct string as a literal and
    every assertion above would still pass — the tally would be transcribed
    once more, one layer further in.
    """
    # Coherent on purpose: 2 deals × 3 capabilities really is 6 cells, so the
    # denominator the sentence prints is checkable rather than decorative.
    fake = SimpleNamespace(
        tally={"validated": 1, "ran": 2, "not-applicable": 3},
        capabilities=[object(), object(), object()],
        deals=[
            SimpleNamespace(deal_id="a", deal_name="Deal A", jurisdiction="Ireland"),
            SimpleNamespace(deal_id="b", deal_name="Deal B", jurisdiction="Spain"),
        ],
        cells=[
            SimpleNamespace(deal_id="a", state="validated"),
            SimpleNamespace(deal_id="a", state="ran"),
            SimpleNamespace(deal_id="a", state="not-applicable"),
            SimpleNamespace(deal_id="b", state="ran"),
            SimpleNamespace(deal_id="b", state="not-applicable"),
            SimpleNamespace(deal_id="b", state="not-applicable"),
        ],
    )
    out = render(fake)
    assert "**1 validated / 2 ran / 3 not-applicable**" in out
    assert "2 registered deals × 3 capabilities = 6 cells" in out
    assert "Deal A" in out and "Ireland" in out
    # B only `ran`, so it is not named as validated and its jurisdiction is not
    # claimed — the vocabulary distinction the cards are held to (#193/#457).
    assert "Deal B" not in out and "Spain" not in out


def test_no_validated_cell_renders_as_a_refusal_not_an_empty_sentence() -> None:
    """An empty validated set must say so, not trail off.

    The matrix has had zero validated cells before and will again; a renderer
    that emitted a dangling clause there would put a broken sentence into two
    published documents on the day the claim got weakest.
    """
    fake = SimpleNamespace(
        tally={"validated": 0, "ran": 4, "not-applicable": 6},
        capabilities=[object()],
        deals=[SimpleNamespace(deal_id="a", deal_name="Deal A", jurisdiction="Italy")],
        cells=[SimpleNamespace(deal_id="a", state="ran")],
    )
    out = render(fake)
    assert "No cell is currently `validated`." in out
    assert "Deal A" not in out


def test_a_backslash_in_a_deal_name_does_not_break_the_write(tmp_path: Path) -> None:
    """`--write` must substitute the sentence literally, escapes and all.

    Deal names are registry data an operator edits. Passed to `re.sub` as a
    replacement *string*, a name containing `\\1` or `\\g` raises `re.error` and
    the write fails — or worse, resolves to a group and writes something else.
    """
    from scripts.render_capability_tally import _fill

    hostile = r"Deal \1 (a.k.a. C:\group) — 2 validated"
    out = _fill(f"{MARKER_START}\nold\n{MARKER_END}", hostile)
    assert hostile in out
    assert out.startswith(MARKER_START) and out.rstrip().endswith(MARKER_END)


def test_the_checker_reds_on_a_hand_edited_region(tmp_path: Path) -> None:
    """A number changed by hand is caught — the `red-when` for the whole fix.

    Hand-correcting a published figure is the behaviour this issue exists to
    stop, so the guard has to fail on exactly that edit and not merely on a
    missing file.
    """
    doctored = tmp_path / "README.md"
    doctored.write_text(
        _read("README.md").replace("24 ran", "25 ran"), encoding="utf-8"
    )
    assert main(["--check", str(doctored)]) == 1


def test_the_checker_reds_on_a_deleted_region(tmp_path: Path) -> None:
    """Deleting the region must fail loudly, not pass by having nothing to check.

    #568: a guard scoped to a slice passes vacuously once the slice is empty.
    The distinction is real here — `_region` returns `None` for "no fences" and
    `""` only for a genuinely empty one, and both are failures.
    """
    gutted = tmp_path / "SYSTEM-STATUS.md"
    text = _read("SYSTEM-STATUS.md")
    gutted.write_text(
        re.sub(
            re.escape(MARKER_START) + r".*?" + re.escape(MARKER_END),
            "",
            text,
            flags=re.S,
        ),
        encoding="utf-8",
    )
    assert main(["--check", str(gutted)]) == 1


def test_no_surface_transcribes_a_tally_outside_a_generated_region() -> None:
    """Numerals stating the tally live in one place or nowhere."""
    offenders: list[tuple[str, list[str]]] = []
    for relative_path in GUARDED_SURFACES:
        # Flattened for the same reason the prose ban is: `2{" "}validated`
        # renders as a transcribed tally and would otherwise evade a raw scan.
        hits = _TRANSCRIBED_TALLY.findall(_flatten(_strip_regions(_read(relative_path))))
        if hits:
            offenders.append((relative_path, hits))
    assert not offenders, f"a capability tally is transcribed: {offenders}"


def test_no_surface_states_a_validated_count_in_prose() -> None:
    """The prose form of the same defect — "the single validated cell"."""
    offenders = [
        (path, claim)
        for path in GUARDED_SURFACES
        for claim in _retracted_claims_in(_read(path))
    ]
    assert not offenders, f"a retracted validated-count claim survives: {offenders}"


def test_the_prose_guard_sees_a_claim_split_across_jsx() -> None:
    """The guard must catch the exact shape the defect actually had.

    This is the historical text from `web/app/(routes)/showcase/page.tsx`, byte
    for byte. A ban that misses it is decorative: it would have passed on the
    day the page rendered a false count to every viewer, which is the outcome
    this whole issue is about.
    """
    historical = (
        'Hover any cell for the honest reason behind its state. The single{" "}\n'
        '            <span className="font-medium text-emerald-700">validated</span>'
        " cell\n            links through to its proof"
    )
    assert _retracted_claims_in(historical) == ["the single validated cell"]
    # And the flattener must not invent the claim out of unrelated prose.
    assert _retracted_claims_in("The single deal below is validated by nobody") == []


def test_any_registered_deal_count_matches_the_registry() -> None:
    """A stated deal count is the tally's denominator and drifts with it.

    ``6 registered deals`` was committed in two documents while the registry
    carried seven, which is the same defect one field over: the generated
    sentence prints ``7 registered deals × 5 capabilities = 35 cells``, so a
    stale count a paragraph away contradicts it on the same page. This does not
    force a numeral into any document — it only requires that one written down
    is the right one, and it reds the day an eighth deal registers.
    """
    live = len(capability_matrix().deals)
    offenders: list[tuple[str, str]] = []
    for relative_path in GUARDED_SURFACES:
        for stated in re.findall(
            r"(\d+)\s+registered deals", _read(relative_path), re.I
        ):
            if int(stated) != live:
                offenders.append((relative_path, stated))
    assert not offenders, (
        f"a stale registered-deal count survives (registry has {live}): {offenders}"
    )


def test_the_guarded_surfaces_still_carry_their_claim() -> None:
    """The bans above must not be satisfiable by deleting the paragraph.

    Each surface has to keep pointing at the matrix, so a future edit that
    quietly drops the honest-scope statement reds here rather than passing two
    ban lists by saying nothing at all.
    """
    for relative_path in GENERATED_DOCS:
        assert MARKER_START in _read(relative_path), relative_path
    for relative_path in ("docs/data-card.md", "docs/model-card.md"):
        assert "/capability-matrix" in _read(relative_path), relative_path
    deck = json.loads(_read("presentation/loanwhiz-deck.json"))
    assert "capability-matrix" in json.dumps(deck) or "Showcase" in json.dumps(deck)


def test_the_governance_cards_finos_tally_matches_the_catalogue() -> None:
    """The other transcribed tally in these docs, now checked rather than trusted.

    ``docs/governance.md`` publishes a FINOS AIR conformance tally that nothing
    read back — ``test_finos_conformance.py`` only asserts the three counts sum
    to the total, which is true of any three numbers that add up. It happens to
    be right today; so did ``2 validated`` on the day someone typed it. Guarding
    it here closes the defect class instead of the one instance the issue named.
    """
    from loanwhiz.governance.finos_conformance import finos_conformance_summary

    counts = finos_conformance_summary()["counts"]
    prose = _read("docs/governance.md")
    stated = re.search(
        r"\*\*(\d+) satisfied · (\d+) partial · (\d+) not applicable\*\*", prose
    )
    assert stated, "docs/governance.md no longer states a FINOS conformance tally"
    assert [int(g) for g in stated.groups()] == [
        counts["satisfied"],
        counts["partial"],
        counts["not_applicable"],
    ], (stated.groups(), counts)


def test_the_showcase_page_derives_its_count_from_the_tally_it_renders() -> None:
    """The page already holds the live tally; its prose must use it.

    A rendered sentence contradicting the grid beside it is the worst copy of
    this defect — it is the surface an institutional reader actually looks at.
    #575 fixed the MCP page the same way: derive the count from what is
    rendered, never write it into the file.
    """
    page = _read("web/app/(routes)/showcase/page.tsx")
    assert "tally.validated" in page, "the page must read the live validated count"
