"""Read a securitisation's risk-retention undertaking from its offering document.

UK/EU Securitisation Regulation obliges an institutional investor to verify,
before holding a position, that an originator, sponsor or original lender
retains a material net economic interest of not less than 5% — and to say *by
which* of the Article 6(3) methods. "Retention verified" without the method is
not what the obligation asks for, so this module refuses to produce a record
that omits it.

Why a deterministic parser rather than the Docling pipeline (#528, #548)
-----------------------------------------------------------------------
The undertaking is *unreachable* through this repo's LLM extraction today.
Cairn CLO XVII's committed glossary stops at ``Payment Date``, so the whole
R range — ``Retention Requirements``
included — is lost to the ``max_chars`` truncation #548 landed its coverage
check for. Judged from that artefact alone the honest-looking conclusion is
"the document does not state retention", which is false: the source states it
in full. That is the absence-vs-refusal distinction, and it is why
:func:`assess_retention` exists beside the parser.

The locator is the citation, not the figure (#539)
--------------------------------------------------
Searching for "5%" finds the *regulation's* generic description as readily as
the deal's own undertaking — in the Cairn document, four pages of the former
precede one page of the latter, and the generic pages name no retaining
entity. So the canonical key here is the **Article 6(3) sub-paragraph letter**,
mapped through a closed enum. Three real documents write that citation three
different ways and pick two different methods:

* Cairn CLO XVII — ``in accordance with Article 6(3)(d)``; the method is never
  named in words.
* Contego CLO XI — ``material net economic interest in the first loss tranche
  ... pursuant to Article 6(3)(d)``; words *and* citation.
* Leone Arancio RMBS 2023-1 — ``option 3 (a) of article 6``; inverted,
  lower-case, and a different method.

A parser keyed on the words "first loss tranche" would find one and refuse two.
Where a document states both, they must agree — a contradiction is a refusal,
not a coin toss.

The level is never a bare percentage
------------------------------------
"5%" means nothing without the base it is 5% *of*: Cairn retains 5% of the
Aggregate Collateral Balance, Contego 5% of the nominal value of the
securitised exposures. :class:`RiskRetention` therefore stores ``level_pct``
and ``level_basis`` together, per #539's fact-scope rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "RetentionCoverage",
    "RetentionMethodLetter",
    "RiskRetention",
    "UnsourcedRetention",
    "assess_retention",
    "parse_retention_method",
    "parse_risk_retention",
]


#: The five methods Article 6(3) of the EU/UK Securitisation Regulation permits.
#: A closed set: a citation outside it is not a method this reader recognises,
#: and is refused rather than mapped to the nearest neighbour (#453).
RetentionMethodLetter = Literal["a", "b", "c", "d", "e"]

_METHOD_NAMES: dict[str, str] = {
    "a": "vertical slice",
    "b": "seller's share",
    "c": "randomly selected exposures",
    "d": "first-loss tranche",
    "e": "first-loss exposure",
}

#: The capacities in which the Regulation lets an entity retain. Also a closed
#: set — "the Retention Holder" alone is a designation, not a capacity.
_CAPACITIES = ("originator", "sponsor", "original lender")


class UnsourcedRetention(ValueError):
    """The text states no retention undertaking this reader can name in full.

    Raised rather than returning a partial record, and deliberately never
    rescued by "it says five per cent., so that is probably the vertical
    slice". A record naming a level but not a method, or a method but not a
    retaining entity, is *confidently incomplete* — worse than an admitted gap,
    because a compliance reader cannot see what is missing.

    The message always names **which limb** was absent, so the refusal is
    actionable rather than a shrug (#493, #457).
    """


@dataclass(frozen=True)
class RiskRetention:
    """One deal's retention undertaking, as its offering document states it.

    Attributes
    ----------
    retainer:
        The retaining entity **as the document designates it** — a legal name
        where the document gives one (``"Five Arrows Global Loan Investments II
        PLC"``), the role where it does not (``"The Investment Manager"``).
        Resolving a role to a legal name is a cross-section inference; it is
        left to the reader with the evidence beside it rather than silently
        substituted here.
    retainer_capacity:
        ``originator``, ``sponsor`` or ``original lender`` — the capacity the
        Regulation recognises, which is what makes the undertaking bind.
    method_letter:
        The Article 6(3) sub-paragraph, ``a``–``e``. The canonical key: it is
        the one form all three surveyed documents agree on.
    level_pct:
        The retained interest floor, as a percentage.
    level_basis:
        What ``level_pct`` is a percentage **of**. A level without its base is
        not a verified level (#539).
    instrument:
        The notes actually held, where the document names them — usually the
        Subordinated Notes for a 6(3)(d) retention. ``None`` when unnamed.
    """

    retainer: str
    retainer_capacity: str
    method_letter: RetentionMethodLetter
    level_pct: float
    level_basis: str
    instrument: str | None = None

    @property
    def method(self) -> str:
        """The Article 6(3) method's name — ``"first-loss tranche"``.

        Derived from :attr:`method_letter` through the closed map rather than
        stored, so a record can never carry a letter and a name that disagree.
        """
        return _METHOD_NAMES[self.method_letter]

    @property
    def article(self) -> str:
        """The citation as the Regulation writes it — ``"Article 6(3)(d)"``."""
        return f"Article 6(3)({self.method_letter})"

    @property
    def source(self) -> str:
        """Human-readable provenance, for the seed and the data card."""
        return f"Listing Particulars, {self.article}"

    def to_dict(self) -> dict[str, object]:
        """Serialise for the deal seed."""
        return {
            "retainer": self.retainer,
            "retainer_capacity": self.retainer_capacity,
            "method_letter": self.method_letter,
            "method": self.method,
            "level_pct": self.level_pct,
            "level_basis": self.level_basis,
            "instrument": self.instrument,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "RiskRetention":
        """Rebuild from a seed's ``risk_retention`` block.

        ``method`` and ``source`` are *derived*, not read back: they are
        renderings of ``method_letter``, and admitting them would let a seed
        carry a method name that contradicted the sub-paragraph it cites —
        the precise failure this module refuses to produce when parsing.
        """
        letter = str(raw["method_letter"]).lower()
        if letter not in _METHOD_NAMES:
            raise UnsourcedRetention(
                f"seed cites Article 6(3)({letter}), which is not one of the "
                f"methods Article 6(3) permits ({', '.join(sorted(_METHOD_NAMES))})"
            )
        capacity = str(raw["retainer_capacity"]).lower()
        if capacity not in _CAPACITIES:
            raise UnsourcedRetention(
                f"seed states retention capacity {capacity!r}, which is not one "
                f"the Regulation recognises ({', '.join(_CAPACITIES)})"
            )
        instrument = raw.get("instrument")
        return cls(
            retainer=str(raw["retainer"]),
            retainer_capacity=capacity,
            method_letter=letter,  # type: ignore[arg-type]
            level_pct=float(raw["level_pct"]),  # type: ignore[arg-type]
            level_basis=str(raw["level_basis"]),
            instrument=None if instrument is None else str(instrument),
        )


# ===========================================================================
# Text normalisation
# ===========================================================================

#: pypdf's text layer wraps lines mid-sentence and sprinkles stray spaces around
#: hyphens. Collapsing whitespace fixes both. It does **not** repair spaces
#: *inside* words (``"econom i c"``, ``"securiti sed"``) — no pattern below
#: depends on a word pypdf split, and a rejoining heuristic would be free to
#: invent tokens the document does not contain.
_HYPHEN_SPACES = re.compile(r"(?<=[A-Za-z0-9])\s*-\s*(?=[A-Za-z0-9])")


def _collapse(text: str) -> str:
    """Collapse pypdf's line wrapping into one whitespace-normalised string."""
    return _HYPHEN_SPACES.sub("-", re.sub(r"\s+", " ", text)).strip()


# ===========================================================================
# The method: the Article 6(3) sub-paragraph
# ===========================================================================

#: ``Article 6(3)(d)`` / ``article 6 (3) (a)`` — the direct citation.
_ARTICLE_DIRECT = re.compile(
    r"articles?\s*6\s*\(\s*3\s*\)\s*\(\s*([a-e])\s*\)", re.IGNORECASE
)
#: ``option 3 (a) of article 6`` — the inverted form Leone Arancio uses.
_ARTICLE_INVERTED = re.compile(
    r"option\s*\(?\s*3\s*\)?\s*\(\s*([a-e])\s*\)\s*of\s*articles?\s*6", re.IGNORECASE
)

#: The method named in words. Checked against the citation, never used in place
#: of it: only one of the three surveyed documents names the method this way.
_METHOD_WORDS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"first[\s-]loss\s+tranche", re.IGNORECASE), "d"),
    (re.compile(r"first[\s-]loss\s+exposures?", re.IGNORECASE), "e"),
    (re.compile(r"vertical\s+slice", re.IGNORECASE), "a"),
    (re.compile(r"seller'?s?\s+share", re.IGNORECASE), "b"),
    (re.compile(r"randomly\s+selected\s+exposures", re.IGNORECASE), "c"),
)


def _locate_citation(collapsed: str) -> re.Match[str] | None:
    """The Article 6(3) sub-paragraph citation, in either form, or ``None``.

    This is the module's locator, and it earns the job by being *rare*:
    ``Article 6(3)`` appears on exactly one page of each ~420-page document
    surveyed, while the threshold figure and the retention vocabulary appear
    throughout. Everything else is read relative to where this lands.
    """
    return _ARTICLE_DIRECT.search(collapsed) or _ARTICLE_INVERTED.search(collapsed)


#: How far either side of the citation the method-in-words may be stated and
#: still be describing *this* undertaking. Contego separates the two by roughly
#: 500 characters within one sentence; the Regulation's own use of the same
#: phrase elsewhere in the document is not a contradiction, it is a different
#: subject, and treating it as one produced a false refusal on an unambiguous
#: document.
_METHOD_WORDS_SPAN = 800


def parse_retention_method(text: str) -> str | None:
    """Return the Article 6(3) sub-paragraph letter the text cites, or ``None``.

    Both citation forms are accepted; where the text *also* names the method in
    words, the two must agree.

    Args:
        text: Offering-document text, as ``pypdf`` extracts it.

    Returns:
        The lower-case sub-paragraph letter, or ``None`` when the text cites no
        sub-paragraph of Article 6(3).

    Raises:
        UnsourcedRetention: The method named in words contradicts the
            sub-paragraph cited. Two readings of one document disagree, so
            neither is reported (#511's "canonicalise both sides" applied to a
            document rather than a lookup).
    """
    collapsed = _collapse(text)
    match = _locate_citation(collapsed)
    return None if match is None else _check_method_words(collapsed, match)


def _check_method_words(collapsed: str, match: re.Match[str]) -> str:
    """The cited letter, once the method named in words nearby is seen to agree.

    Raises:
        UnsourcedRetention: The words contradict the citation.
    """
    letter = match.group(1).lower()
    near = collapsed[
        max(0, match.start() - _METHOD_WORDS_SPAN) : match.end() + _METHOD_WORDS_SPAN
    ]
    for pattern, worded in _METHOD_WORDS:
        if pattern.search(near) and worded != letter:
            raise UnsourcedRetention(
                f"the document names the {_METHOD_NAMES[worded]!r} method in "
                f"words but cites Article 6(3)({letter}) "
                f"({_METHOD_NAMES[letter]!r}); the two readings disagree"
            )
    return letter


# ===========================================================================
# The undertaking: who retains, in what capacity, at what level
# ===========================================================================

#: ``... shall/will act as (the) Retention Holder`` — the anchor. The entity is
#: whatever immediately precedes it, so the name is read *backwards* from here
#: rather than captured in one pattern: in both CLOs the sentence is preceded by
#: the section heading "Description of the Retention Holder", and a single
#: forward regex anchors on the earliest boundary it can reach, swallowing that
#: heading into the name.
_RETAINER_ANCHOR = re.compile(
    r"\s+(?:shall|will|has\s+agreed\s+to|agrees\s+to)\s+act\s+as\s+"
    r"(?:the\s+)?[Rr]etention\s+[Hh]older"
)
#: How far back from the anchor the entity may be stated. The name itself is
#: capped at 80 characters, so this is generous; it exists to bound the search.
_RETAINER_LOOKBEHIND = 240

#: The undertaking runs from the designation to a little past the citation.
#: Capacity, level and instrument are read from that span alone, so a capacity
#: stated anywhere else in the document — the Issuer being an originator "for
#: some other purpose" — is never attributed to whoever holds the retention.
_SPAN_AFTER_CITATION = 1500

#: The entity, right-anchored against the text preceding the anchor. The
#: leading greedy ``.*`` is load-bearing: it forces the engine to take the
#: **last** boundary that still reaches the end, which is the tightest one and
#: therefore the name alone. Without it the match starts at ``^`` and the name
#: swallows the heading. (``re.finditer`` does not help — the first match
#: consumes to ``$``, so no later one is ever scanned.)
_RETAINER_TAIL = re.compile(
    r".*(?:^|[.;:]\s|\b[Hh]older\s)(?P<name>[A-Z][A-Za-z0-9&.,'’()\- ]{2,80})$"
)

#: The capacity claim — ``qualifies as an "originator"``, ``believes that it is
#: an "originator"``. Quotation marks are optional and may be curly.
_CAPACITY = re.compile(
    r"(?:qualifies\s+as|believes\s+(?:that\s+)?it\s+is|acts\s+as|is)\s+an?\s*"
    r"[\"“‘']?\s*(originator|sponsor|original\s+lender)",
    re.IGNORECASE,
)

#: ``not less than five (5) per cent. of the Aggregate Collateral Balance``.
#: The base is captured with the level because a level without one is not a
#: verified level (#539).
_LEVEL = re.compile(
    r"not\s+less\s+than\s+"
    r"(?P<num>\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten)"
    r"\s*(?:\(\s*\d+(?:\.\d+)?\s*\))?\s*per\s*cent\.?"
    r"(?:\s*of\s+(?P<basis>[^,;:]{3,120}?)"
    r"(?=\s+(?:in accordance|pursuant|through|until|pro vided|provided|and\b)|[.,;:]|$))?",
    re.IGNORECASE,
)

_NUMBER_WORDS = {
    "one": 1.0, "two": 2.0, "three": 3.0, "four": 4.0, "five": 5.0,
    "six": 6.0, "seven": 7.0, "eight": 8.0, "nine": 9.0, "ten": 10.0,
}

#: The notes the retention is held through, most informative first. ``Retention
#: Notes`` is the *last* resort on purpose: it is the defined term a document
#: coins for whatever it just said must be held ("such Notes required to be held
#: the 'Retention Notes'"), so reporting it says nothing a reader did not
#: already know. Cairn coins it two sentences before naming the actual class.
_INSTRUMENT_PREFERRED = re.compile(
    r"\b((?:Subordinated|Class\s+[A-Z0-9-]+)\s+Notes)\b"
)
_INSTRUMENT_FALLBACK = re.compile(r"\b(Retention\s+Notes)\b")


def _read_undertaking(collapsed: str, citation: re.Match[str]) -> RiskRetention:
    """Read one undertaking, anchored on ``citation``. See :func:`parse_risk_retention`.

    Every limb must be present. The undertaking is what binds a named entity in
    a recognised capacity to a named method at a stated level; a text carrying
    only some of that is the Regulation being *described*, not this deal's
    commitment, and the two are indistinguishable in a record that reports
    whichever limbs it happened to find.

    Raises:
        UnsourcedRetention: Naming the limb that is absent, or the
            contradiction that made the reading unsafe.
    """
    letter = _check_method_words(collapsed, citation)

    # The designation that governs is the last one stated *before* the
    # commitment, not the first in the document: a 420-page offering circular
    # names its Retention Holder in the summary long before the section that
    # binds it, and the earlier mention states no capacity or level. Choosing
    # by proximity to the citation is what lets this read a whole document.
    anchor = None
    for candidate in _RETAINER_ANCHOR.finditer(collapsed):
        if candidate.start() >= citation.end():
            break
        anchor = candidate

    # The lookbehind is bounded, and not only for speed: the entity sits
    # immediately before the verb in every document surveyed, so a wider window
    # buys nothing and costs correctness. Unbounded, `_RETAINER_TAIL`'s greedy
    # `.*` also backtracks over the whole prefix — handed a 420-page document it
    # does not return.
    tail = (
        _RETAINER_TAIL.search(
            collapsed[max(0, anchor.start() - _RETAINER_LOOKBEHIND) : anchor.start()]
        )
        if anchor
        else None
    )
    if tail is None:
        raise UnsourcedRetention(
            f"the text cites Article 6(3)({letter}) "
            f"({_METHOD_NAMES[letter]}) but names no retaining entity, so "
            "'who retains' is not stated in extractable form"
        )
    retainer = tail.group("name").strip(" .,;:")

    # Every remaining limb is read from the undertaking's own span rather than
    # from the whole text. A capacity found anywhere in a 420-page document is
    # not this retainer's capacity — the Issuer being an originator "for some
    # other purpose" would otherwise be attributed to whoever holds the
    # retention, which is the inference this module exists to refuse.
    span = collapsed[anchor.end() : citation.end() + _SPAN_AFTER_CITATION]

    capacity_match = _CAPACITY.search(span)
    if capacity_match is None:
        raise UnsourcedRetention(
            f"the text names {retainer!r} as Retention Holder but states no "
            f"capacity ({', '.join(_CAPACITIES)}); the designation alone does "
            "not establish that the retention binds"
        )
    capacity = re.sub(r"\s+", " ", capacity_match.group(1)).lower()

    level_match = _LEVEL.search(span)
    if level_match is None or level_match.group("basis") is None:
        raise UnsourcedRetention(
            f"the text states no retained level with a base for {retainer!r}; "
            "a bare percentage is not a verified level"
        )
    raw_num = level_match.group("num").lower()
    level_pct = _NUMBER_WORDS.get(raw_num, None)
    if level_pct is None:
        level_pct = float(raw_num)

    instrument_match = _INSTRUMENT_PREFERRED.search(
        span
    ) or _INSTRUMENT_FALLBACK.search(span)

    return RiskRetention(
        retainer=retainer,
        retainer_capacity=capacity,
        method_letter=letter,  # type: ignore[arg-type]
        level_pct=level_pct,
        level_basis=level_match.group("basis").strip(" .,;:"),
        instrument=instrument_match.group(1) if instrument_match else None,
    )


def parse_risk_retention(text: str) -> RiskRetention:
    """Read the deal's own retention undertaking from offering-document text.

    Every limb must be present. The undertaking is what binds a named entity in
    a recognised capacity to a named method at a stated level; a text carrying
    only some of that is the Regulation being *described*, not this deal's
    commitment, and the two are indistinguishable in a record reporting
    whichever limbs it happened to find.

    Accepts a whole document as readily as a hand-sliced section. Where the text
    cites Article 6(3) more than once — a document may cite a sub-paragraph
    while *describing* the Regulation, before committing to one — each citation
    is tried in turn and the first that yields a complete undertaking wins.
    Refusing on the first citation when a later one states the undertaking in
    full would be a false refusal, which is the failure direction this surface
    is least allowed.

    Args:
        text: Offering-document text, as ``pypdf`` extracts it.

    Returns:
        The parsed :class:`RiskRetention`.

    Raises:
        UnsourcedRetention: Naming the limb that is absent, or the
            contradiction that made the reading unsafe. Where several citations
            were tried, the first refusal is raised — it is the one describing
            the passage that looked most like an undertaking.
    """
    collapsed = _collapse(text)
    citations = list(_ARTICLE_DIRECT.finditer(collapsed)) or list(
        _ARTICLE_INVERTED.finditer(collapsed)
    )
    if not citations:
        raise UnsourcedRetention(
            "the text cites no sub-paragraph of Article 6(3), so the retention "
            "method is unstated; a level without a method is not what the "
            "verification obligation asks for"
        )

    refusals: list[UnsourcedRetention] = []
    for citation in citations:
        try:
            return _read_undertaking(collapsed, citation)
        except UnsourcedRetention as refusal:
            refusals.append(refusal)
    # ``citations`` is non-empty above, so reaching here means every one of them
    # refused and ``refusals`` is non-empty too.
    raise refusals[0]


# ===========================================================================
# Output plausibility — the #548 sibling
# ===========================================================================

#: Below this, the text supplied cannot plausibly be a retention section: the
#: undertaking alone runs to well over a thousand characters in both CLOs
#: surveyed.
_MIN_PLAUSIBLE_SOURCE_CHARS = 400

#: Retention vocabulary that appears wherever the subject is discussed at all.
_RETENTION_VOCABULARY = re.compile(
    r"retention|retain(?:s|ed|ing)?|net\s+economic\s+interest", re.IGNORECASE
)


@dataclass(frozen=True)
class RetentionCoverage:
    """Whether the text handed to the parser could plausibly hold the answer.

    #548's rule, applied to a second fact: judge the stage on **its own
    output's plausibility**, not on the cause you last diagnosed. A retention
    section can arrive unusable two ways that look identical downstream — a
    character budget clipped it, or Docling promoted a defined term to a
    heading and the routed span ended early — and a check aimed at either
    misses the other.

    What it buys is the distinction this whole surface exists for: a document
    that *does not state* its retention and a section that *did not arrive*
    produce the same empty parse, and only one of them is a fact about the
    deal. ``implausible`` says the absence is unproven, so a refusal is
    recorded instead of an absence.
    """

    source_chars: int
    retention_mentions: int
    cites_article_6_3: bool
    implausible: bool
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        """Serialise for the deal-model metadata."""
        return {
            "source_chars": self.source_chars,
            "retention_mentions": self.retention_mentions,
            "cites_article_6_3": self.cites_article_6_3,
            "implausible": self.implausible,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: object) -> "RetentionCoverage | None":
        """Rebuild from metadata, tolerating a cache written before this existed."""
        if not isinstance(data, dict):
            return None
        return cls(
            source_chars=int(data.get("source_chars", 0)),
            retention_mentions=int(data.get("retention_mentions", 0)),
            cites_article_6_3=bool(data.get("cites_article_6_3", False)),
            implausible=bool(data.get("implausible", False)),
            reason=str(data.get("reason", "")),
        )


def assess_retention(text: str, truncated: bool = False) -> RetentionCoverage:
    """Judge whether ``text`` could plausibly contain a retention undertaking.

    Three ordered signals; the first to fire writes ``reason``, and
    ``implausible`` is exactly ``bool(reason)`` so the two can never disagree.

    Args:
        text: The text that was, or would be, handed to
            :func:`parse_risk_retention`.
        truncated: Whether the section arrived clipped by a character budget.
            Passed in rather than inferred, because the *other* mechanism —
            section orphaning — leaves no such record and must still be caught.

    Returns:
        The :class:`RetentionCoverage` record.
    """
    collapsed = _collapse(text)
    mentions = len(_RETENTION_VOCABULARY.findall(collapsed))
    cites = _ARTICLE_DIRECT.search(collapsed) is not None or (
        _ARTICLE_INVERTED.search(collapsed) is not None
    )

    reason = ""
    if truncated:
        reason = (
            f"the retention section arrived truncated at {len(collapsed)} "
            "characters, so anything absent from it is unproven rather than "
            "absent from the document"
        )
    elif len(collapsed) < _MIN_PLAUSIBLE_SOURCE_CHARS:
        reason = (
            f"{len(collapsed)} characters is too little to be a retention "
            f"section (expected at least {_MIN_PLAUSIBLE_SOURCE_CHARS}); the "
            "span probably ended early"
        )
    elif mentions and not cites:
        reason = (
            f"the text mentions retention {mentions} times but cites no "
            "sub-paragraph of Article 6(3) — the undertaking itself is "
            "probably outside the span that was routed"
        )

    return RetentionCoverage(
        source_chars=len(collapsed),
        retention_mentions=mentions,
        cites_article_6_3=cites,
        implausible=bool(reason),
        reason=reason,
    )
