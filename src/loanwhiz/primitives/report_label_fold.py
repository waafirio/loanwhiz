"""One fold from a published Priority of Payments onto a cascade's labels (#514).

The **join** between a report's printed rows and the extracted cascade's steps.
A trustee report prints its Priority of Payments as a *document*, not as the
cascade's step list: it splits one cascade step across several printed rows, and
it labels those rows in whatever way the document's own typography wanted. The
engine's step list carries one label per step. Something has to say which rows
belong to which step, and this module is that something — for every deal, on both
waterfalls, in one place.

Why one function and not four
-----------------------------
This replaces a hand-mirrored pair of pairs: ``reconciler._fold_report_*_steps``
and ``report_adapter._fold_*_pop``, whose docstrings each asked the other not to
drift. They were not redundant — they answer the same question on **opposite
sides of the same comparison**. The adapter's answer becomes the engine's *need*
for a report-supplied step; the reconciler's becomes the *published figure* that
need is checked against. Two copies of a join can only ever agree by luck, and
when they disagree the engine is graded against a number it was never given.

What the fold has to survive
----------------------------
Green Lion's report prints step ``(b)`` as fourteen wrapped sub-items ``(1)…(14)``
— a ``pypdf`` layout artefact — and nothing else needs folding. Cairn's Note
Valuation Report is genuinely hierarchical and needs three different things at
once:

- **prefixed children** — ``(A)(i)``, ``(H)(ii)``, ``(CC)(1)(a)``: the parent is
  written into the label, so the first bracket group names it;
- **amountless headers** — ``(E)``, ``(F)``, ``(X)`` print a heading row with no
  figure, so the parent label never reaches the parsed rows at all;
- **re-lettered children** — those headers' rows restart at bare ``(a)``/``(i)``,
  and two *different* parents' children are both spelled ``(a)``.

The last is why this is not a string problem. Cairn pays EUR 155,457.93 under an
``(a)`` belonging to ``(E)`` and EUR 362,735.16 under an ``(a)`` belonging to
``(X)``; a fold keyed on the label alone puts both in one bucket and pays one of
them to the wrong recipient. What distinguishes them is **where they sit** — the
report prints its rows in cascade order, so a row's parent is bounded by the
labelled rows either side of it.

The rule
--------
Walk the rows in order, carrying a cursor into the cascade's label list.

1. A row whose label — or whose **first bracket group** — is a cascade label at or
   after the cursor **opens that section**; its amount folds onto that label. (At
   or after, not strictly after: a report prints nine consecutive ``(C)`` rows for
   one ``(C)`` step, and they all belong to it.)
2. Any other row is an **orphan**, held until the next row that opens a section.
3. A held run is then split across the cascade labels **in the gap** the cursor
   skipped. The split is the **unique** partition of the run into contiguous
   sub-runs, each of which reads as one monotone sub-label sequence starting at
   its own first ordinal — ``(a),(a),(b),(c)`` then ``(i),(ii),(iii)`` is the only
   way to cut Cairn's seven orphans between ``(E)`` and ``(F)``.
4. A run that skipped **no** label is a continuation of the section already open,
   and folds there. Cairn's Principal cascade needs this: its step ``(A)`` pays
   "in the order of ``(A)`` to ``(I)`` of the Interest Priority", so the report
   prints that whole sub-cascade — re-lettered children and all — inside one step.

**No unique partition means the rows stay unplaced.** They are returned in
:attr:`FoldedPoP.unplaced`, never folded onto a nearby label and never swept into
a residual step. #496 named the residual sweep as the fix this failure invites and
rejected it with a reason: the money belongs to *named* recipients, so sweeping it
pays it to the wrong party and turns a visible failure into a silent one. A fold
that cannot place a row must leave it unplaced and say so — which is also why
:attr:`FoldedPoP.total` counts the unplaced rows, so a tie-out gate still sees the
whole published distribution and still fails.

Pure and offline: labels and floats in, labels and floats out. No deal constant
appears here — the cascade's own label list is the only per-deal input.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Protocol, Sequence

from pydantic import BaseModel, Field

#: Roman numerals a report plausibly uses to letter sub-items. Bounded on purpose:
#: an open-ended roman parser would read ``(m)`` and ``(d)`` — ordinary latin
#: sub-labels — as 1000 and 500, and silently reorder a run.
_ROMAN_ORDINALS: dict[str, int] = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5,
    "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10,
}

#: Ceilings on the gap-splitting search. A run of orphans between two labelled
#: rows is a handful of lines in every report we have; these stop a malformed
#: parse turning a combinatorial split into a hang, and a refusal here is the
#: honest answer anyway (the rows come back unplaced).
_MAX_ORPHAN_RUN = 40
_MAX_GAP_LABELS = 8

_BRACKET_GROUP = re.compile(r"\(([^)]*)\)")


class _PublishedRow(Protocol):
    """The shape this fold reads off a parsed report row.

    Structural rather than nominal so the fold depends on neither
    ``notes_cash_parser`` nor its models — a report row is a label and an amount,
    and anything carrying those two can be folded.
    """

    @property
    def priority(self) -> str: ...

    @property
    def amount(self) -> float: ...


class UnplacedReportRow(BaseModel):
    """A published row the fold could not attribute to any cascade step.

    Its own type, in its own list, rather than a zero-valued entry in the folded
    amounts — the #513 discipline reaching the join. An unplaceable row folded onto
    *some* label would be compared against that step and would usually pass or fail
    for reasons that have nothing to do with it; reported separately it stays
    legible as "the join could not answer this", which is a different fact from
    "the engine paid the wrong amount".
    """

    priority: str = Field(..., description="The row's published label, verbatim.")
    amount: float = Field(..., description="What the report distributed on it.")


class FoldedPoP(BaseModel):
    """A published Priority of Payments folded onto one cascade's labels.

    ``amounts`` is what the reconciler compares against and what the adapter feeds
    the engine as a report-supplied need. ``unplaced`` is everything the join could
    not answer for.
    """

    amounts: dict[str, float] = Field(
        default_factory=dict, description="cascade label -> published total."
    )
    unplaced: list[UnplacedReportRow] = Field(
        default_factory=list, description="Rows no cascade label claims (#514)."
    )

    @property
    def total(self) -> float:
        """Everything the report published, placed or not.

        The unplaced rows are counted here on purpose. A tie-out gate compares the
        engine's distribution against this figure, so dropping what the fold could
        not place would make the gate agree by simply not looking at the missing
        money — the failure this whole module exists to make visible.
        """
        return sum(self.amounts.values()) + sum(row.amount for row in self.unplaced)


def _bracket_groups(label: str) -> list[str]:
    """``"(CC)(1)(a)"`` -> ``["CC", "1", "a"]``; a label with no brackets -> ``[]``."""
    return _BRACKET_GROUP.findall(label)


def _parent_label(label: str) -> str:
    """The cascade label a prefixed child names — ``"(H)(ii)"`` -> ``"(H)"``.

    A label carrying no bracket group is returned unchanged rather than mangled:
    the caller only uses this as a lookup key, and a miss is an orphan.
    """
    groups = _bracket_groups(label)
    return f"({groups[0]})" if groups else label


def _ordinal_readings(token: str) -> set[tuple[str, int]]:
    """Every ``(style, ordinal)`` this sub-label token could be.

    Deliberately plural. ``i`` is the ninth latin letter *and* the first roman
    numeral, and which one a report meant is not knowable from the token — only
    from the run it sits in. Returning both readings lets :func:`_reads_as_one_run`
    decide with the run's own evidence instead of guessing per token.
    """
    readings: set[tuple[str, int]] = set()
    lowered = token.strip().lower()
    if lowered.isdigit():
        readings.add(("digit", int(lowered)))
    if len(lowered) == 1 and "a" <= lowered <= "z":
        readings.add(("latin", ord(lowered) - ord("a") + 1))
    if lowered in _ROMAN_ORDINALS:
        readings.add(("roman", _ROMAN_ORDINALS[lowered]))
    return readings


def _reads_as_one_run(tokens: Sequence[str]) -> bool:
    """Do these tokens read as one parent's sub-labels, in one style, from the top?

    Three conditions, all in one style: the run **starts at ordinal 1** (a parent's
    children begin at its first sub-label, which is what makes a restart detectable
    at all), and it never goes backwards. Repeats are allowed and are not a
    restart — Cairn prints ``(a) Senior Management Fee`` and ``(a) VAT - Senior
    Management Fee`` as two rows of the same sub-item.
    """
    if not tokens:
        return False
    styles = {style for token in tokens for style, _ in _ordinal_readings(token)}
    for style in styles:
        ordinals: list[int] = []
        for token in tokens:
            in_style = [n for s, n in _ordinal_readings(token) if s == style]
            if not in_style:
                break
            ordinals.append(in_style[0])
        else:
            if ordinals[0] == 1 and all(
                later >= earlier for earlier, later in zip(ordinals, ordinals[1:])
            ):
                return True
    return False


def _contiguous_partitions(
    tokens: Sequence[str], parts: int
) -> Iterable[list[Sequence[str]]]:
    """Every way to cut ``tokens`` into ``parts`` contiguous non-empty runs."""
    if parts == 1:
        yield [tokens]
        return
    for cut in range(1, len(tokens) - parts + 2):
        for rest in _contiguous_partitions(tokens[cut:], parts - 1):
            yield [tokens[:cut]] + rest


def _split_across_gap(
    orphans: Sequence[_PublishedRow], gap_labels: Sequence[str]
) -> list[Sequence[_PublishedRow]] | None:
    """Assign a run of orphan rows to the cascade labels it sits between.

    Returns one row-run per gap label, or ``None`` when the assignment is not
    **uniquely** determined — no valid partition, several valid ones, or a search
    wide enough to be a symptom rather than a report shape. ``None`` is a refusal,
    and the caller turns it into unplaced rows.

    Uniqueness is the whole guarantee. With Cairn's ``(E)``/``(F)`` gap and the
    orphan run ``a, a, b, c, i, ii, iii`` exactly one cut works: any earlier cut
    leaves the second run starting mid-alphabet, and any later one leaves it
    starting at roman 2 or 3. Requiring that — rather than taking the first
    partition that parses — is what stops the fold inventing an answer where the
    document does not have one.
    """
    if not gap_labels or not orphans:
        return None
    if len(orphans) < len(gap_labels):
        return None
    if len(orphans) > _MAX_ORPHAN_RUN or len(gap_labels) > _MAX_GAP_LABELS:
        return None

    tokens = [_bracket_groups(row.priority) for row in orphans]
    # A multi-group orphan is a prefixed child whose parent is not in this gap;
    # its sub-label sequence says nothing about where it belongs.
    if any(len(groups) != 1 for groups in tokens):
        return None
    flat = [groups[0] for groups in tokens]

    valid: list[list[Sequence[str]]] = []
    for candidate in _contiguous_partitions(flat, len(gap_labels)):
        if all(_reads_as_one_run(run) for run in candidate):
            valid.append(candidate)
            if len(valid) > 1:
                return None
    if len(valid) != 1:
        return None

    assignment: list[Sequence[_PublishedRow]] = []
    position = 0
    for run in valid[0]:
        assignment.append(orphans[position : position + len(run)])
        position += len(run)
    return assignment


def fold_report_pop(
    rows: Sequence[Any], cascade_labels: Sequence[str]
) -> FoldedPoP:
    """Fold a report's published PoP rows onto a cascade's step labels.

    ``rows`` are the parsed report rows for one waterfall, in printed order (each
    carrying ``.priority`` and ``.amount``); ``cascade_labels`` are the extracted
    cascade's step labels, in cascade order. Both orders are load-bearing: the
    report prints its distribution in the cascade's own sequence, and that is the
    evidence that places a re-lettered row.

    Every row lands in exactly one of :attr:`FoldedPoP.amounts` or
    :attr:`FoldedPoP.unplaced`, so :attr:`FoldedPoP.total` always equals the
    report's published total. Nothing is dropped and nothing is swept.
    """
    label_index = {label: i for i, label in enumerate(cascade_labels)}
    amounts: dict[str, float] = {}
    unplaced: list[UnplacedReportRow] = []
    pending: list[Any] = []
    cursor = -1

    def flush(next_index: int) -> None:
        """Attribute the held orphan run to the labels the cursor skipped."""
        nonlocal pending
        if not pending:
            return
        gap = list(cascade_labels[cursor + 1 : next_index])
        if not gap and cursor >= 0:
            # The run opened no new section, so it is a continuation of the open
            # one — the same inference the parser already makes for a row printed
            # with no label at all. It cannot cross a step boundary, because
            # crossing one requires a labelled row, which would have ended the run.
            # Cairn's Principal cascade needs exactly this: its step (A) pays "in
            # the order of (A) to (I) of the Interest Priority", so the report
            # prints that sub-cascade's own re-lettered children inside one step.
            for row in pending:
                label = cascade_labels[cursor]
                amounts[label] = amounts.get(label, 0.0) + row.amount
            pending = []
            return
        assignment = _split_across_gap(pending, gap)
        if assignment is None:
            unplaced.extend(
                UnplacedReportRow(priority=row.priority, amount=row.amount)
                for row in pending
            )
        else:
            for label, run in zip(gap, assignment):
                for row in run:
                    amounts[label] = amounts.get(label, 0.0) + row.amount
        pending = []

    for row in rows:
        label = str(row.priority)
        for key in (label, _parent_label(label)):
            index = label_index.get(key)
            if index is not None and index >= cursor:
                flush(index)
                cursor = index
                amounts[key] = amounts.get(key, 0.0) + row.amount
                break
        else:
            pending.append(row)
    flush(len(cascade_labels))

    return FoldedPoP(amounts=amounts, unplaced=unplaced)
