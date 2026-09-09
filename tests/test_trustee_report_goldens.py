"""Byte-identity goldens for every committed Cairn CLO XVII parse.

Why this file exists
--------------------
``collateral_schedule_parser`` and ``note_valuation_parser`` were written
against U.S. Bank's report layout and are being generalised onto a
family-detected dispatch (#531). A generalisation that alters the existing
deal's output is a rewrite wearing a generalisation's clothes, so the
requirement is that Cairn's parse stays **byte-identical** — asserted, not
intended.

The existing regressions do not assert that. ``test_quality_harness`` does
regenerate ``answer_keys/cairn-clo-xvii-dac.json`` byte-for-byte, but only over
the slice of figures the answer key surfaces; the parser tests assert
reconciliation, which is an oracle about *internal consistency* rather than
about the exact bytes. A section that stopped parsing but still reconciled — or
any field the answer key never reads — would move silently. These goldens close
that gap by pinning the **whole** of every parse this deal has fixtures for.

What is pinned
--------------
Every committed fixture, through every public text-parsing entry point that
reads it, with ``strict=True`` so each parse also passes its own acceptance
oracle before it is compared:

- three monthly trustee reports → ``parse_schedule_text``
- the same three → ``parse_liability_summary_text``
- one Note Valuation Report → ``parse_note_valuation_text``

Golden shape
------------
Canonical JSON — ``sort_keys``, two-space indent, trailing newline — so a
regression shows up as a readable diff rather than an opaque hash mismatch.

The one departure is the collateral schedule's ``assets`` list: 193 rows of ~19
fields is ~190 KB per report, and three of those would put half a megabyte of
generated JSON into every reviewer's diff. It is replaced by
:func:`_asset_digests` — one ``<identifier> <sha256>`` line per asset, in parse
order — which still pins every field of every row (any change to any field
changes that row's digest) while naming the row that moved. Order is pinned too,
because the list is a sequence, not a set.

Regenerating
------------
Deliberately not automatic: these bytes are the regression, so refreshing them
is an explicit act that must be justified in review.

    PYTHONPATH=src python -m tests.test_trustee_report_goldens

Re-run it only when a change is *intended* to move parser output, and say in
the PR why the new bytes are correct.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from loanwhiz.primitives.collateral_schedule_parser import (
    parse_liability_summary_text,
    parse_schedule_text,
)
from loanwhiz.primitives.note_valuation_parser import parse_note_valuation_text

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "golden"
COLLATERAL_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "collateral_schedule"
NOTE_VALUATION_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "note_valuation"

#: The monthly trustee reports, with the period label each is parsed under.
#: Mirrors ``clo_answer_key_source.CLO_REPORT_FIXTURES`` — same documents, so a
#: fixture added there belongs here too.
COLLATERAL_FIXTURES: tuple[tuple[str, str], ...] = (
    ("cairn-clo-xvii-december-2024.txt", "December 2024"),
    ("cairn-clo-xvii-february-2025.txt", "February 2025"),
    ("cairn-clo-xvii-march-2025.txt", "March 2025"),
)

#: The Note Valuation Reports, same convention.
NOTE_VALUATION_FIXTURES: tuple[tuple[str, str], ...] = (
    ("cairn-clo-xvii-january-2025.txt", "January 2025"),
)


def _canonical_json(payload: Any) -> str:
    """Serialise *payload* so equal parses always produce equal bytes.

    ``sort_keys`` removes dict-ordering as a source of spurious diffs, and the
    trailing newline keeps the files well-formed for line-oriented tooling.
    """
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _asset_digests(assets: list[dict[str, Any]]) -> list[str]:
    """One ``<identifier> <sha256>`` line per asset, in parse order.

    The digest covers the asset's whole canonical serialisation, so any change
    to any field of any row changes exactly that row's line. Parse order is
    preserved rather than sorted: the schedule is a sequence, and a reordering
    is itself a regression worth catching.
    """
    return [
        f"{asset['identifier']} "
        f"{hashlib.sha256(json.dumps(asset, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()}"
        for asset in assets
    ]


def _schedule_payload(filename: str, period_label: str) -> Any:
    """The full ``parse_schedule_text`` output, with assets digested."""
    text = (COLLATERAL_FIXTURE_DIR / filename).read_text(encoding="utf-8")
    dumped = parse_schedule_text(
        text, period_label=period_label, strict=True
    ).model_dump(mode="json")
    dumped["assets"] = _asset_digests(dumped["assets"])
    return dumped


def _liability_payload(filename: str, period_label: str) -> Any:
    """The full ``parse_liability_summary_text`` output."""
    text = (COLLATERAL_FIXTURE_DIR / filename).read_text(encoding="utf-8")
    return parse_liability_summary_text(
        text, period_label=period_label, strict=True
    ).model_dump(mode="json")


def _note_valuation_payload(filename: str, period_label: str) -> Any:
    """The full ``parse_note_valuation_text`` output."""
    text = (NOTE_VALUATION_FIXTURE_DIR / filename).read_text(encoding="utf-8")
    return parse_note_valuation_text(
        text, period_label=period_label, strict=True
    ).model_dump(mode="json")


def golden_cases() -> list[tuple[str, Callable[[], Any]]]:
    """Every (golden filename, payload builder) pair this module pins.

    One place, two callers: the test parametrisation and the regeneration
    entry point, so a golden can never be asserted against a payload different
    from the one that wrote it.
    """
    cases: list[tuple[str, Callable[[], Any]]] = []
    for filename, period_label in COLLATERAL_FIXTURES:
        stem = filename.removesuffix(".txt")
        cases.append(
            (
                f"{stem}.schedule.json",
                lambda f=filename, p=period_label: _schedule_payload(f, p),
            )
        )
        cases.append(
            (
                f"{stem}.liability.json",
                lambda f=filename, p=period_label: _liability_payload(f, p),
            )
        )
    for filename, period_label in NOTE_VALUATION_FIXTURES:
        stem = filename.removesuffix(".txt")
        cases.append(
            (
                f"{stem}.note-valuation.json",
                lambda f=filename, p=period_label: _note_valuation_payload(f, p),
            )
        )
    return cases


@pytest.mark.parametrize(
    ("golden_name", "build_payload"),
    golden_cases(),
    ids=[name for name, _ in golden_cases()],
)
def test_parse_output_is_byte_identical_to_golden(
    golden_name: str, build_payload: Callable[[], Any]
) -> None:
    """Every Cairn parse reproduces its committed golden byte for byte.

    This is the regression that makes "the generalisation did not change the
    existing deal" a checked claim rather than a hope.
    """
    golden_path = GOLDEN_DIR / golden_name
    assert golden_path.exists(), (
        f"missing golden {golden_name} — regenerate with "
        "`PYTHONPATH=src python -m tests.test_trustee_report_goldens`"
    )
    assert _canonical_json(build_payload()) == golden_path.read_text(encoding="utf-8")


def test_every_committed_golden_is_claimed_by_a_case() -> None:
    """No golden sits in the directory unasserted.

    A golden whose case was renamed away stops being checked while still
    looking like coverage — the same "nothing to find" vs "I cannot see"
    failure the parsers' own presence checks exist to prevent (#494).
    """
    committed = {path.name for path in GOLDEN_DIR.glob("*.json")}
    claimed = {name for name, _ in golden_cases()}
    assert committed == claimed


def write_goldens() -> list[Path]:
    """(Re)write every golden from the current parser output. Not run by tests."""
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for golden_name, build_payload in golden_cases():
        path = GOLDEN_DIR / golden_name
        path.write_text(_canonical_json(build_payload()), encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    for written_path in write_goldens():
        print(f"wrote {written_path}")
