"""The report→cascade join, and what it refuses to guess (#514, epic #510).

``report_label_fold.fold_report_pop`` replaced four hand-mirrored folds — two in
``reconciler``, two in ``report_adapter`` — that each asked the other not to
drift. This module pins the three things that consolidation has to be true of:

1. **It still folds Green Lion's wrap artefact identically.** That deal is
   validated to the cent against its own published report, so the old fold's
   output is ground truth here and the new one is asserted *against a
   reimplementation of it*, period by period, on both waterfalls.
2. **It places Cairn's hierarchical report**, whose 62 rows the old fold left 20
   of joined to nothing — including the two re-lettered bare ``(a)`` rows that
   collide with each other and belong to different parents.
3. **It refuses rather than guesses.** An orphan run a gap does not uniquely
   determine comes back unplaced, never folded onto a neighbouring label and never
   swept into a residual — #496 named the sweep as the fix this failure invites
   and rejected it, because the money belongs to named recipients.

Offline and deterministic: the committed fixtures plus a handful of synthetic
rows for the shapes no committed report happens to contain.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from loanwhiz.primitives.notes_cash_parser import NotesCashPeriod, parse_report_text
from loanwhiz.primitives.reconciler import (
    _FIXTURE_DIR,
    _GREEN_LION_2023_1_FIXTURES,
    _GREEN_LION_2023_1_SEED_PATH,
    _GREEN_LION_2024_1_FIXTURES,
    _SEED_PATH,
)
from loanwhiz.primitives.report_label_fold import fold_report_pop
from tests.clo_answer_key_source import clo_note_valuation_report

_REPO_ROOT = Path(__file__).resolve().parents[1]
CLO_SEED_PATH = (
    _REPO_ROOT / "src" / "loanwhiz" / "data" / "deals" / "seed" / "cairn-clo-xvii-dac.json"
)


@dataclass(frozen=True)
class Row:
    """A published row: a label and an amount, which is all the fold reads."""

    priority: str
    amount: float


def cascade_labels(seed_path: Path, waterfall: str) -> list[str]:
    """The extracted cascade's step labels, in cascade order — read off the seed."""
    model = json.loads(Path(seed_path).read_text(encoding="utf-8"))
    return [step["priority"] for step in model["waterfalls"][waterfall]["steps"]]


def legacy_fold(period: NotesCashPeriod) -> dict[str, float]:
    """The fold this change retired, reimplemented verbatim as the regression pin.

    Green Lion's report prints step ``(b)`` as wrapped sub-items ``(1)…(n)`` (a
    ``pypdf`` layout artefact) and the old fold summed every purely-numeric label
    into ``(b)``, passing everything else through. Keeping it here rather than
    transcribing its *output* means the comparison below re-derives both sides
    from the fixture on every run: a re-parse that changed either the rows or the
    amounts moves both, and only a genuine divergence between the two folds reds.
    """
    folded: dict[str, float] = {}
    b_total = 0.0
    saw_sub_item = False
    for step in period.revenue_pop:
        if step.priority.strip("()").strip().isdigit():
            b_total += step.amount
            saw_sub_item = True
        else:
            folded[step.priority] = folded.get(step.priority, 0.0) + step.amount
    if saw_sub_item:
        folded["(b)"] = b_total
    return folded


def green_lion_periods() -> list[tuple[str, NotesCashPeriod, Path]]:
    """Every committed Green Lion period, with the seed whose cascade it folds onto."""
    out: list[tuple[str, NotesCashPeriod, Path]] = []
    for seed_path, fixtures in (
        (_SEED_PATH, _GREEN_LION_2024_1_FIXTURES),
        (_GREEN_LION_2023_1_SEED_PATH, _GREEN_LION_2023_1_FIXTURES),
    ):
        for filename, label in fixtures:
            period = parse_report_text(
                (_FIXTURE_DIR / filename).read_text(encoding="utf-8"), period_label=label
            )
            out.append((f"{Path(seed_path).stem} {label}", period, Path(seed_path)))
    return out


# ---------------------------------------------------------------------------
# 1. The validated deal does not move.
# ---------------------------------------------------------------------------


def test_green_lion_folds_exactly_as_the_fold_it_replaced() -> None:
    """Every committed Green Lion period folds byte-identically to the old rule.

    This is the assertion that lets the generalisation ship. Both vintages are
    validated to EUR 0.01 against their own published Notes & Cash reports, and
    the fold sits on the engine's *input* side as well as the reconciliation's —
    the adapter turns it into each report-supplied step's need — so a fold that
    shifted by a cent here would move a proof the repo's headline claim rests on,
    in a place the CLO's own tests would never look.
    """
    checked = 0
    for name, period, seed_path in green_lion_periods():
        labels = cascade_labels(seed_path, "revenue")
        folded = fold_report_pop(period.revenue_pop, labels)
        assert folded.amounts == legacy_fold(period), name
        assert folded.unplaced == [], name
        checked += 1
    # The loop must have run: a fixture list that stopped resolving would leave
    # every assertion above unexecuted and this test green on nothing (#494).
    assert checked == len(_GREEN_LION_2024_1_FIXTURES) + len(_GREEN_LION_2023_1_FIXTURES)


def test_green_lion_redemption_still_passes_every_label_through() -> None:
    """The redemption side needs no folding, and the general fold adds none."""
    for name, period, seed_path in green_lion_periods():
        labels = cascade_labels(seed_path, "redemption")
        folded = fold_report_pop(period.redemption_pop, labels)
        by_label: dict[str, float] = {}
        for step in period.redemption_pop:
            by_label[step.priority] = by_label.get(step.priority, 0.0) + step.amount
        assert folded.amounts == by_label, name
        assert folded.unplaced == [], name


def test_the_wrap_artefact_folds_onto_the_step_it_belongs_to() -> None:
    """`(1)…(14)` lands on `(b)` because `(b)` is the label the run skipped.

    The old fold knew "purely numeric means ``(b)``" — a rule true of one report.
    The new one derives it: the run sits between the printed ``(a)`` and ``(c)``
    rows, and the cascade has exactly one label in that gap. Asserted separately
    from the equality above because the equality would also hold if both folds
    were wrong in the same way.
    """
    _, period, seed_path = green_lion_periods()[0]
    labels = cascade_labels(seed_path, "revenue")
    assert labels[:3] == ["(a)", "(b)", "(c)"]
    assert not [s for s in period.revenue_pop if s.priority == "(b)"]

    folded = fold_report_pop(period.revenue_pop, labels)
    wrapped = [s for s in period.revenue_pop if s.priority.strip("()").isdigit()]
    assert len(wrapped) == 14
    assert folded.amounts["(b)"] == pytest.approx(
        sum(s.amount for s in wrapped), abs=0.01
    )


# ---------------------------------------------------------------------------
# 2. The report this issue exists for.
# ---------------------------------------------------------------------------


def test_the_clo_report_places_every_published_row() -> None:
    """All 62 of Cairn's Interest rows reach a step, and nothing is left over.

    The finding #496 recorded: 20 rows joined no step and the eight carrying money
    accounted for EUR 1,820,150.42 of the shortfall to the cent. The rows have not
    changed — the join has.
    """
    (period,) = clo_note_valuation_report().periods
    labels = cascade_labels(CLO_SEED_PATH, "revenue")
    assert len(period.revenue_pop) == 62
    assert len(labels) == 29

    folded = fold_report_pop(period.revenue_pop, labels)

    assert folded.unplaced == []
    # Derived from the document, never transcribed: the fold must conserve the
    # published total exactly, so a row placed twice or dropped reds here.
    assert folded.total == pytest.approx(
        sum(s.amount for s in period.revenue_pop), abs=0.01
    )
    assert set(folded.amounts) <= set(labels)


@pytest.mark.parametrize(
    ("label", "rows", "expected"),
    [
        # Prefixed children — the parent is written into the label.
        ("(A)", ("(A)(i)", "(A)(i)", "(A)(ii)"), 6_388.00 + 57.50 + 250.00),
        ("(H)", ("(H)(i)", "(H)(ii)"), 386_773.50 + 257_625.00),
        ("(CC)", ("(CC)(1)(a)",), 650_863.33),
        # Re-lettered children of an amountless header — placed by position alone.
        ("(E)", ("(a)",), 155_457.93),
        ("(X)", ("(a)",), 362_735.16),
    ],
)
def test_each_clo_parent_gets_its_own_childrens_money(
    label: str, rows: tuple[str, ...], expected: float
) -> None:
    """The join is right per parent, not just right in total.

    A fold that conserved the total while mis-assigning two rows to each other's
    parent would pass the conservation assertion above and pay named recipients
    the wrong amounts. ``(E)`` and ``(X)`` are the pair that makes this concrete:
    the report spells both their money-carrying rows bare ``(a)``, so the label
    alone cannot tell them apart and only position within the section can.
    """
    (period,) = clo_note_valuation_report().periods
    folded = fold_report_pop(
        period.revenue_pop, cascade_labels(CLO_SEED_PATH, "revenue")
    )
    assert folded.amounts[label] == pytest.approx(expected, abs=0.01)
    # And the rows this parent is credited with really are printed under it.
    published = [s for s in period.revenue_pop if s.priority in rows]
    assert published, (label, rows)


def test_the_two_bare_a_rows_do_not_collapse_into_one_bucket() -> None:
    """The collision the issue names: one label, two parents, two amounts.

    Stated as its own assertion because the failure mode is a *pass* elsewhere —
    fold both onto one label and the total still conserves, one parent is paid
    double and the other nothing, and every aggregate check stays green.
    """
    (period,) = clo_note_valuation_report().periods
    bare_a = [s for s in period.revenue_pop if s.priority == "(a)"]
    assert [s.amount for s in bare_a] == [155_457.93, 0.0, 362_735.16, 0.0]

    folded = fold_report_pop(
        period.revenue_pop, cascade_labels(CLO_SEED_PATH, "revenue")
    )
    assert folded.amounts["(E)"] != folded.amounts["(X)"]
    assert folded.amounts["(E)"] + folded.amounts["(X)"] == pytest.approx(
        sum(s.amount for s in bare_a), abs=0.01
    )


def test_a_run_that_opens_no_section_continues_the_open_one() -> None:
    """Cairn's Principal cascade prints a whole sub-cascade inside one step.

    Its step ``(A)`` pays "in the order of ``(A)`` to ``(I)`` of the Interest
    Priority", so the report prints that sub-cascade's re-lettered children
    between two rows that both belong to ``(A)``. The run skips no cascade label,
    which is what says it never left the step it started in.
    """
    (period,) = clo_note_valuation_report().periods
    labels = cascade_labels(CLO_SEED_PATH, "redemption")
    folded = fold_report_pop(period.redemption_pop, labels)
    assert folded.unplaced == []
    assert set(folded.amounts) <= set(labels)


# ---------------------------------------------------------------------------
# 3. What it refuses to guess.
# ---------------------------------------------------------------------------


def test_an_ambiguous_gap_leaves_the_rows_unplaced() -> None:
    """Two labels, an orphan run that cuts two ways — so the fold places neither.

    ``(a),(a),(a)`` between ``(A)`` and ``(D)`` can be cut ``[a][a,a]`` or
    ``[a,a][a]``, and both read as valid child sequences. Guessing would put real
    money on a step the document does not assign it to, so the rows come back
    unplaced with their amounts intact — visible to a tie-out gate, absent from
    every step comparison.
    """
    labels = ["(A)", "(B)", "(C)", "(D)"]
    rows = [
        Row("(A)", 10.0),
        Row("(a)", 1.0),
        Row("(a)", 2.0),
        Row("(a)", 4.0),
        Row("(D)", 20.0),
    ]
    folded = fold_report_pop(rows, labels)

    assert [(r.priority, r.amount) for r in folded.unplaced] == [
        ("(a)", 1.0),
        ("(a)", 2.0),
        ("(a)", 4.0),
    ]
    assert "(B)" not in folded.amounts and "(C)" not in folded.amounts
    # Still counted: an unplaceable row is money the report distributed, so a
    # tie-out gate must see it and fail rather than agree by not looking.
    assert folded.total == pytest.approx(37.0)
    assert sum(folded.amounts.values()) == pytest.approx(30.0)


def test_a_run_no_partition_fits_is_unplaced_rather_than_forced() -> None:
    """No valid cut either: a run that starts mid-alphabet names no parent's first child."""
    folded = fold_report_pop(
        [Row("(A)", 5.0), Row("(b)", 1.0), Row("(c)", 2.0), Row("(D)", 7.0)],
        ["(A)", "(B)", "(C)", "(D)"],
    )
    assert [r.priority for r in folded.unplaced] == ["(b)", "(c)"]
    assert folded.amounts == {"(A)": 5.0, "(D)": 7.0}


def test_a_gap_the_run_does_determine_is_placed() -> None:
    """The refusals above are not the fold declining to work.

    Same two-label gap, an orphan run that cuts exactly one way — ``[a,b][a,b]``,
    since any other cut leaves a run starting at ``b``. Without this the two tests
    above would pass on a fold that refused everything.
    """
    folded = fold_report_pop(
        [
            Row("(A)", 5.0),
            Row("(a)", 1.0),
            Row("(b)", 2.0),
            Row("(a)", 4.0),
            Row("(b)", 8.0),
            Row("(D)", 7.0),
        ],
        ["(A)", "(B)", "(C)", "(D)"],
    )
    assert folded.unplaced == []
    assert folded.amounts == {"(A)": 5.0, "(B)": 3.0, "(C)": 12.0, "(D)": 7.0}


def test_roman_and_latin_runs_are_told_apart_by_the_run_not_the_token() -> None:
    """``(i)`` is the ninth letter and the first roman numeral; the run decides.

    Read as latin it continues ``(a),(b),(c)``; read as roman it restarts. Both
    readings are carried per token precisely so the *partition* picks the one that
    makes two whole parents, which is what places Cairn's ``(E)``/``(F)`` split.
    """
    folded = fold_report_pop(
        [
            Row("(A)", 1.0),
            Row("(a)", 2.0),
            Row("(b)", 4.0),
            Row("(c)", 8.0),
            Row("(i)", 16.0),
            Row("(ii)", 32.0),
            Row("(D)", 64.0),
        ],
        ["(A)", "(B)", "(C)", "(D)"],
    )
    assert folded.unplaced == []
    assert folded.amounts["(B)"] == pytest.approx(14.0)
    assert folded.amounts["(C)"] == pytest.approx(48.0)


def test_a_prefixed_child_of_an_absent_parent_is_unplaced() -> None:
    """A label naming a parent the cascade does not carry is reported, not guessed.

    The one case the section cursor cannot help with: the row states its own
    parent, and that parent is not a step. Its sub-label sequence says nothing
    about where it belongs, so there is nothing to infer from.
    """
    folded = fold_report_pop(
        [Row("(A)", 1.0), Row("(ZZ)(i)", 9.0), Row("(D)", 2.0)],
        ["(A)", "(B)", "(C)", "(D)"],
    )
    assert [r.priority for r in folded.unplaced] == ["(ZZ)(i)"]
    assert folded.total == pytest.approx(12.0)
