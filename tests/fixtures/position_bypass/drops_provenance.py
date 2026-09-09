"""Control module for the position-provenance bypass guard (#571).

**This file is deliberately wrong.** It is never imported by the package; it
exists so ``tests/test_position_provenance_bypass.py`` can prove its scanner
still *fires*. A guard that passes by finding nothing cannot tell "there is no
bypass" from "I can no longer see one", so the suite points it at a file that
must be flagged and fails if it is not (#483's ``test_tape_seam_bypass`` shape).

Do not "fix" this module. Flagging it is the point.
"""

from __future__ import annotations

from typing import Any


def render_position(position: Any) -> dict[str, Any]:
    """Hand-build a position record, dropping the qualifier.

    This is exactly the bypass ``Position.to_record`` exists to prevent: every
    field the consumer wants, and no statement of what the holding *is* — so a
    surface rendering this dict shows an illustrative position as though it
    were somebody's actual exposure.
    """
    return {
        "deal_id": position.deal_id,
        "tranche": position.tranche,
        "strips": list(position.strips),
        "size": position.size,
        "as_of": position.as_of.isoformat(),
    }
