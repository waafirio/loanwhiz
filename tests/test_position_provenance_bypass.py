"""No surface serialises a position without saying what it is (#571).

``Position.provenance`` is required, so a position cannot be *constructed*
without a qualifier. The remaining way to lose it is at the **serialisation**
boundary: a module that reads a position's fields into a dict of its own and
happens not to copy ``provenance`` produces a record that reads exactly like a
real holding. ``Position.to_record`` is the one serialisation and always emits
both the kind and its disclosure — this guard is what stops a second one being
written next to it.

Guarded **statically**, for the reason #483 guarded the tape seam statically:
a per-reader test only covers readers that exist and have tests, and the reader
that breaks is the one nobody wrote a test for. Walking the AST reds on a
bypassing module written tomorrow, in a file with no tests of its own.

This checker passes by finding nothing, so "there is no bypass" and "I can no
longer see one" are the same output. ``test_the_scanner_flags_its_control``
closes that: a committed control module under ``tests/fixtures/position_bypass/``
must be flagged, so a scanner pointed at nothing fails rather than reading
clean.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "loanwhiz"
CONTROL = REPO_ROOT / "tests" / "fixtures" / "position_bypass" / "drops_provenance.py"

#: The fields a position record carries. A dict literal naming several of these
#: is building a position record, whatever it calls itself.
_POSITION_FIELDS = {"deal_id", "tranche", "strips", "size", "as_of"}

#: How many of them a dict must name before we treat it as a position record.
#: Two is too loose — ``{"deal_id": ..., "size": ...}`` is a plausible shape for
#: plenty of things that are not holdings. Three is the point at which a dict is
#: describing a position rather than merely mentioning a deal.
_MIN_FIELDS = 3


def _dict_keys(node: ast.Dict) -> set[str]:
    """The literal string keys of a dict node."""
    return {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def bypassing_dicts(source: str, path: Path) -> list[str]:
    """Dict literals in *source* that build a position record without its qualifier.

    Returns one ``"<file>:<line>"`` per finding, so a failure names the exact
    place to fix rather than only the file.
    """
    findings: list[str] = []
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if not isinstance(node, ast.Dict):
            continue
        keys = _dict_keys(node)
        if len(keys & _POSITION_FIELDS) < _MIN_FIELDS:
            continue
        if "provenance" in keys:
            continue
        findings.append(f"{path}:{node.lineno}")
    return findings


def _scan(paths: list[Path]) -> list[str]:
    findings: list[str] = []
    for path in paths:
        findings.extend(bypassing_dicts(path.read_text(encoding="utf-8"), path))
    return findings


class TestPositionProvenanceBypass:
    def test_the_scanner_flags_its_control(self) -> None:
        """The control drops ``provenance``; the scanner must say so.

        Without this the whole guard could be silently pointed at nothing — an
        empty walk over an empty file list reports a clean tree.
        """
        assert CONTROL.exists(), f"the bypass control is missing: {CONTROL}"
        findings = _scan([CONTROL])
        assert findings, (
            "the bypass scanner did not flag its own control. It is now blind: "
            "a real serialiser dropping `provenance` would also pass unseen."
        )

    def test_the_scanner_accepts_a_record_that_keeps_provenance(self) -> None:
        """A record carrying the qualifier is not flagged — the guard is not a ban on dicts."""
        kept = '''
def render(p):
    return {
        "deal_id": p.deal_id,
        "tranche": p.tranche,
        "strips": list(p.strips),
        "size": p.size,
        "provenance": p.provenance.value,
    }
'''
        assert _scan_source(kept) == []

    def test_no_module_serialises_a_position_without_its_qualifier(self) -> None:
        """The real assertion: nothing under ``src/loanwhiz`` bypasses ``to_record``."""
        modules = sorted(SRC_ROOT.rglob("*.py"))
        assert modules, f"found no modules under {SRC_ROOT} — the walk is misconfigured"
        findings = _scan(modules)
        assert findings == [], (
            "these dict literals build a position record without a `provenance` "
            "key, so a consumer cannot tell an illustrative holding from a real "
            "one. Serialise through `Position.to_record` instead:\n  "
            + "\n  ".join(findings)
        )


def _scan_source(source: str) -> list[str]:
    return bypassing_dicts(source, Path("<inline>"))
