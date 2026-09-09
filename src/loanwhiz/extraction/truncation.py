"""Structural record of a prospectus section clipped before it reached the LLM.

Two extractors send one section of a prospectus to Gemini under a character
budget — :func:`~loanwhiz.extraction.definitions_graph.extract_definitions`
(``max_chars=40_000``) and
:func:`~loanwhiz.extraction.waterfall_extractor.extract_waterfall`
(``max_chars=20_000``).  Both used to slice with a bare ``text[:max_chars]``.
One then emitted a ``logger.warning``; the other emitted nothing at all, so a
Priority-of-Payments cascade cut mid-waterfall arrived looking like a shorter
deal rather than a broken extraction (#548).

A log line is not a fact the result carries.  Nothing downstream — a caller, a
data card, a test — could ask "was this complete?", so two load-bearing facts
went missing for a month behind a warning nobody read (#528, #539).  This
module makes the answer **structural**: :func:`clip` is the single slicing path
for both sites, and it returns the :class:`Truncation` alongside the text so
the result object can carry it into the cache, the deal model and the API.

Keeping one implementation is the point.  The two sites drifted precisely
because each owned its own slice; a shared helper cannot silently lose its
warning on one side only.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Truncation:
    """What a character budget discarded, and where it stopped.

    ``last_line`` is the tail of the *kept* text — the point the extractor
    stopped reading.  For an alphabetical glossary that is the single most
    useful field: it names the term after which nothing was seen, which is what
    turns "25 terms" from a healthy-looking count into a known prefix.
    """

    section_title: str
    original_chars: int
    kept_chars: int
    last_line: str

    @property
    def discarded_chars(self) -> int:
        """Characters dropped by the budget (never negative)."""
        return max(0, self.original_chars - self.kept_chars)

    @property
    def discarded_fraction(self) -> float:
        """Share of the section discarded, in ``[0, 1]``.

        ``0.0`` for an empty section rather than a ``ZeroDivisionError`` — a
        caller rendering this into a data card should not have to guard.
        """
        if self.original_chars <= 0:
            return 0.0
        return self.discarded_chars / self.original_chars

    def to_dict(self) -> dict:
        """Serialise to the on-disk cache shape.

        Only the four stored fields are written; ``discarded_chars`` and
        ``discarded_fraction`` are derived and recomputed on read, so the cache
        can never disagree with itself.
        """
        return {
            "section_title": self.section_title,
            "original_chars": self.original_chars,
            "kept_chars": self.kept_chars,
            "last_line": self.last_line,
        }

    @classmethod
    def from_dict(cls, data: object) -> "Truncation | None":
        """Rebuild from a cache payload, tolerating every pre-#548 shape.

        A cache file written before this change has **no** ``truncation`` key at
        all, so the caller passes ``None`` and gets ``None`` back — a warm cache
        keeps loading rather than raising.  Anything that is not a dict (a
        stray string, a list) reads as "no record" for the same reason.
        """
        if not isinstance(data, dict):
            return None
        return cls(
            section_title=str(data.get("section_title", "")),
            original_chars=int(data.get("original_chars", 0)),
            kept_chars=int(data.get("kept_chars", 0)),
            last_line=str(data.get("last_line", "")),
        )


def _tail_line(text: str, window: int = 200) -> str:
    """Last non-blank line within the final ``window`` chars of ``text``.

    The tail of a truncated span is usually mid-sentence, so this reports the
    line the cut landed in rather than pretending to a clean boundary.
    """
    tail = text[-window:].strip()
    if not tail:
        return ""
    return tail.split("\n")[-1].strip()[:120]


def clip(section_title: str, text: str, max_chars: int | None) -> tuple[str, Truncation | None]:
    """Clip ``text`` to ``max_chars``, reporting what that cost.

    Returns ``(kept_text, truncation)``.  ``truncation`` is ``None`` — and
    ``kept_text is text`` — whenever the budget did not bite, so the common case
    is byte-identical to the bare slice it replaces and costs one comparison.

    A ``max_chars`` of ``None`` or a non-positive value means "no budget": the
    text is returned whole.  That keeps a caller wanting the unclipped section
    from having to invent a sentinel large number.
    """
    if max_chars is None or max_chars <= 0 or len(text) <= max_chars:
        return text, None

    kept = text[:max_chars]
    return kept, Truncation(
        section_title=section_title,
        original_chars=len(text),
        kept_chars=len(kept),
        last_line=_tail_line(kept),
    )
