"""Tapes **derived** from a source document, and the URI that says so.

Every other LoanWhiz tape is a published file: ``deal["tape_urls"]`` holds a URL,
:func:`~loanwhiz.primitives.esma_tape_normaliser._load_tape` reads it, and the
result is tagged ``data_source="direct"``. A *derived* tape has no such file. It
is reconstructed at ingest from a document the deal already registers — for
Cairn CLO XVII DAC, the collateral schedule inside its monthly trustee reports,
parsed by :mod:`~loanwhiz.primitives.collateral_schedule_parser` and resolved
onto canonical Annex 4 columns by
:mod:`~loanwhiz.primitives.collateral_tape_mapping`.

The honesty problem, and why it is solved with a URI scheme
-----------------------------------------------------------
A derived tape that a reader cannot tell apart from a filed Article 7(1)(a) tape
is provenance laundering. Cairn *does* file real Loan Reports; LoanWhiz does not
have them. So the requirement is not "label it carefully somewhere" — it is that
**no surface can report this tape as a filing**, including surfaces nobody has
written yet.

That is why the derivation lives in the **tape's own identifier** rather than in
a sibling registry field:

    derived+trustee-report:https://…/monthly-report.pdf#period=December%202024
    └──── scheme ────────┘└──── the real source document ────┘└── which cut ──┘

Three properties follow, and none of them depend on a caller remembering
anything:

1. **Every provenance answer is a pure function of the string.** A call site
   holding only ``tape["url"]`` — and three of them hold nothing else — can
   still recover the source kind (:func:`source_kind_for`) without loading the
   tape. A sibling ``{"source_kind": …}`` field could be read by the loader and
   forgotten by the other five readers; a scheme cannot be forgotten, because
   the readers already read the string it is part of.
2. **A derived tape cannot be tagged ``direct``.** ``_load_tape`` dispatches on
   the scheme before it dispatches on the file extension, so the provenance
   label is decided by the identifier rather than by which branch of the loader
   happened to run.
3. **The source document is in the identifier, not merely near it.** The URI
   carries the very report the rows were reconciled against, so a reader who
   sees only the tape entry still sees what grounds it.

The trade-off is that ``tape_urls`` now holds URIs rather than URLs. That is the
honest widening: the field's job is to identify a tape, and a derived tape's
identity genuinely is "this document, this period, reconstructed".

What this module does *not* do
------------------------------
It performs no mapping of its own. :func:`derive_tape` parses, reconciles and
delegates; the field-by-field correspondence, the 13 genuine ``CRPL`` locators
and the 13 declared absences all live in ``collateral_tape_mapping`` and stay
the single place that decides what a column means.

Adding the next derived source
------------------------------
Register a member on :class:`DerivedTapeScheme`, its source kind in
:data:`_SCHEME_KINDS`, and its deriver in :data:`_DERIVERS`. The import-time
guard below refuses a member missing from either table, so a new scheme cannot
become well-formed by accident — the closed-registry discipline #453 established
for ``NEED_CALCULATORS``. Nothing in this module special-cases Cairn.
"""

from __future__ import annotations

import hashlib
import logging
import urllib.parse
import urllib.request
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from loanwhiz.domain.tape_provenance import TapeSourceKind

if TYPE_CHECKING:  # pragma: no cover - typing only
    from loanwhiz.primitives.collateral_tape_mapping import MappedCollateralTape

# The parser and the mapping table are imported lazily, inside the two functions
# that need them, for two reasons. Layering: ``capability_matrix`` asks this
# module what a registered tape *is* while deciding a cell, and that decision
# path is required to stay offline and cheap — it must not drag in a PDF parser.
# Initialisation order: ``collateral_tape_mapping`` reaches back into
# ``loanwhiz.domain``, so a module-level import here closes a cycle through
# ``primitives/__init__`` and fails on a partially initialised
# ``domain.provenance``. Deferring costs one lookup per derivation, which is
# already the expensive call.

__all__ = [
    "DerivedTapeScheme",
    "DerivationError",
    "DEFAULT_DERIVED_TAPE_CACHE_DIR",
    "derive_tape",
    "derived_tape_uri",
    "declared_annex_id_for",
    "is_derived_uri",
    "source_document_for",
    "source_kind_for",
]

_log = logging.getLogger(__name__)

#: Durable derivation cache. Mirrors ``notes_cash_parser``'s
#: ``data/extraction_cache/notes-cash-{slug}.json``: gitignored, rebuilt from the
#: source document on a cold cache, and never the authority for what the tape
#: says — the report is.
DEFAULT_DERIVED_TAPE_CACHE_DIR = Path(__file__).resolve().parents[3] / "data" / "extraction_cache"

#: Fragment parameter naming which reporting period of the source document this
#: tape is. Required: a trustee report is one cut, and a tape that cannot say
#: which cut it is has lost the fact that makes it a period.
_PERIOD_PARAM = "period"

#: Suffixes read as already-extracted report **text** rather than as a PDF. The
#: text seam is what the committed fixtures are, so an operator (or a test) can
#: derive from a local extraction without a PDF stack or a network round-trip.
_TEXT_SUFFIXES = (".txt",)


class DerivedTapeScheme(str, Enum):
    """URI schemes naming a derivation LoanWhiz can perform.

    A closed vocabulary with no default member, for the same reason
    :class:`~loanwhiz.domain.tape_provenance.TapeSourceKind` has none: the
    scheme decides the most consequential claim the system makes about a tape,
    and a default would let it be made by omission.
    """

    #: Reconstructed from the collateral schedule of a monthly trustee report.
    TRUSTEE_REPORT = "derived+trustee-report"


class DerivationError(ValueError):
    """A derived URI could not be turned into a tape.

    Raised for a malformed URI, an unknown scheme, or a source document whose
    parsed schedule does not reconcile to the document's own stated aggregates.
    The last case matters most: ``_load_tape`` surfaces it, so
    ``POST /deal/{id}/ingest/tape`` answers 422 rather than persisting a tape
    the source itself contradicts.
    """


def _derive_from_trustee_report(source_url: str, period_label: str) -> MappedCollateralTape:
    """Parse a monthly trustee report into a canonical Annex 4 tape.

    The reconciliation contract #469 built is load-bearing here and is *not*
    softened: ``parse_schedule_text`` refuses a schedule that does not tie back
    to the report's own asset count, aggregate balance and per-bucket
    distributions, so an unparseable or foreign report fails loudly instead of
    yielding a plausible-looking tape.
    """
    from loanwhiz.primitives.collateral_schedule_parser import parse_schedule_text
    from loanwhiz.primitives.collateral_tape_mapping import map_schedule

    text = _read_source_document(source_url)
    schedule = parse_schedule_text(text, period_label=period_label)
    return map_schedule(schedule, source_document=source_url)


#: Scheme → what a tape derived through it **is**. Every member is present; the
#: guard below refuses a member that is not, because a scheme with no declared
#: kind would answer the derived-vs-filed question by falling off the end of a
#: lookup.
_SCHEME_KINDS: dict[DerivedTapeScheme, TapeSourceKind] = {
    DerivedTapeScheme.TRUSTEE_REPORT: TapeSourceKind.DERIVED_FROM_INVESTOR_REPORT,
}

#: Scheme → the derivation itself. Same totality requirement.
_DERIVERS: dict[DerivedTapeScheme, Callable[[str, str], MappedCollateralTape]] = {
    DerivedTapeScheme.TRUSTEE_REPORT: _derive_from_trustee_report,
}

_missing_kinds = sorted(s.value for s in DerivedTapeScheme if s not in _SCHEME_KINDS)
_missing_derivers = sorted(s.value for s in DerivedTapeScheme if s not in _DERIVERS)
if _missing_kinds or _missing_derivers:  # pragma: no cover - import-time guard
    raise RuntimeError(
        "DerivedTapeScheme members are not fully registered — "
        f"missing a source kind: {_missing_kinds}; missing a deriver: "
        f"{_missing_derivers}. Every scheme must declare both: a scheme with no "
        "kind would make the derived-vs-filed claim by omission, and one with no "
        "deriver would parse as a valid tape identifier that nothing can load."
    )
del _missing_kinds, _missing_derivers


def _split_uri(uri: str) -> tuple[DerivedTapeScheme, str, str]:
    """Split a derived URI into (scheme, source URL, period label).

    Raises:
        DerivationError: on an unknown scheme, an empty source URL, or a missing
            ``#period=`` fragment.
    """
    scheme_text, _, remainder = uri.partition(":")
    try:
        scheme = DerivedTapeScheme(scheme_text)
    except ValueError as exc:
        known = ", ".join(sorted(s.value for s in DerivedTapeScheme))
        raise DerivationError(
            f"{scheme_text!r} is not a derivation LoanWhiz knows how to perform. "
            f"Known schemes: {known}."
        ) from exc

    source_url, _, fragment = remainder.partition("#")
    if not source_url:
        raise DerivationError(
            f"Derived URI {uri!r} names no source document. The form is "
            f"'{scheme.value}:<source document URL>#{_PERIOD_PARAM}=<label>'."
        )

    params = urllib.parse.parse_qs(fragment)
    period_values = params.get(_PERIOD_PARAM) or []
    if not period_values or not period_values[0].strip():
        raise DerivationError(
            f"Derived URI {uri!r} states no '{_PERIOD_PARAM}'. A trustee report is "
            "one reporting cut, and a tape that cannot say which cut it is has "
            "lost the fact that makes it a period — so the fragment is required "
            "rather than guessed from the document."
        )
    return scheme, source_url, period_values[0].strip()


def _read_source_document(source_url: str) -> str:
    """Fetch *source_url* and return the report as extracted text.

    A ``.txt`` source is read as an already-extracted report (what the committed
    fixtures are); anything else is fetched as PDF bytes and run through the
    coordinate-based extraction ``collateral_schedule_parser`` owns. Both go
    through ``urllib``, so ``file://`` and ``http(s)://`` work identically.
    """
    request = urllib.request.Request(  # noqa: S310 - scheme validated below
        source_url, headers={"User-Agent": "Mozilla/5.0 (loanwhiz)"}
    )
    parsed = urllib.parse.urlparse(source_url)
    if parsed.scheme not in ("http", "https", "file"):
        raise DerivationError(
            f"Source document {source_url!r} uses unsupported scheme "
            f"{parsed.scheme!r}; expected http, https or file."
        )
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        payload = response.read()

    if parsed.path.lower().endswith(_TEXT_SUFFIXES):
        return payload.decode("utf-8")

    from loanwhiz.primitives.collateral_schedule_parser import extract_report_lines

    return extract_report_lines(payload)


def derived_tape_uri(source_url: str, period_label: str, *, scheme: DerivedTapeScheme) -> str:
    """Build the derived URI for *source_url* at *period_label*.

    The one place the URI is spelled, so a registry entry and a test never drift
    on escaping.
    """
    fragment = urllib.parse.urlencode({_PERIOD_PARAM: period_label})
    return f"{scheme.value}:{source_url}#{fragment}"


def is_derived_uri(url: str) -> bool:
    """Whether *url* identifies a derived tape rather than a published file."""
    return any(url.startswith(f"{scheme.value}:") for scheme in DerivedTapeScheme)


def source_kind_for(url: str) -> TapeSourceKind | None:
    """What the tape at *url* **is**, decided by its identifier alone.

    Returns ``None`` for an ordinary published tape URL. ``None`` means *the
    source kind is not declared* — deliberately **not**
    ``FILED_ARTICLE_7_1_A``. LoanWhiz's other tapes are read from published
    files without any evidence in this repo about whether those files are the
    originator's Article 7(1)(a) disclosure or a redistribution of it, and
    inventing that claim as a default is precisely the fabrication the derived
    path exists to avoid. A surface rendering provenance must therefore handle
    three answers, not two.
    """
    for scheme in DerivedTapeScheme:
        if url.startswith(f"{scheme.value}:"):
            return _SCHEME_KINDS[scheme]
    return None


def source_document_for(url: str) -> str | None:
    """The source document a derived tape was reconstructed from, or ``None``."""
    if not is_derived_uri(url):
        return None
    return _split_uri(url)[1]


def _cache_path(uri: str, cache_dir: str | Path) -> Path:
    digest = hashlib.sha256(uri.encode("utf-8")).hexdigest()[:16]
    return Path(cache_dir) / f"derived-tape-{digest}.json"


#: In-process memo, so the several surfaces that each ask a question about the
#: same tape (its frame, its annex, its disclosure) derive it once per process.
#: Keyed by cache directory as well as URI: two callers pointed at different
#: caches are asking different questions, and a URI-only key would silently
#: serve one the other's answer.
_MEMO: dict[tuple[str, str], MappedCollateralTape] = {}


def derive_tape(
    uri: str,
    *,
    cache_dir: str | Path | None = None,
    force_refresh: bool = False,
) -> MappedCollateralTape:
    """Return the canonical Annex 4 tape *uri* identifies.

    Resolution order, cheapest first — the shape
    :func:`~loanwhiz.primitives.notes_cash_parser.parse_notes_cash_report`
    already uses:

    1. in-process memo;
    2. durable cache under *cache_dir*;
    3. derivation from the source document, then persisted to the cache.

    The cache is an accelerator and never an authority: deleting it re-derives
    the same tape from the same report, which is the property a committed tape
    artefact would not have had.

    Raises:
        DerivationError: on a malformed URI or unknown scheme.
        ScheduleReconciliationError: when the parsed schedule does not tie back
            to the source report's own stated aggregates (#469). Deliberately
            not caught here — a tape the document itself contradicts must not
            reach a caller.
    """
    # Resolved here rather than as a parameter default, which would bind the
    # module constant at definition time and make the cache location impossible
    # to redirect afterwards — for a test that must not write into the repo
    # tree, or a deployment that mounts its cache elsewhere.
    cache_dir = DEFAULT_DERIVED_TAPE_CACHE_DIR if cache_dir is None else cache_dir
    memo_key = (uri, str(cache_dir))
    if not force_refresh and memo_key in _MEMO:
        return _MEMO[memo_key]

    scheme, source_url, period_label = _split_uri(uri)

    path = _cache_path(uri, cache_dir)
    if not force_refresh and path.is_file():
        try:
            from loanwhiz.primitives.collateral_tape_mapping import MappedCollateralTape

            tape = MappedCollateralTape.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — a stale/corrupt cache re-derives
            _log.warning("Ignoring unreadable derived-tape cache at %s", path)
        else:
            _MEMO[memo_key] = tape
            return tape

    tape = _DERIVERS[scheme](source_url, period_label)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(tape.model_dump_json(indent=2), encoding="utf-8")
    except OSError:  # pragma: no cover — a read-only cache dir must not fail ingest
        _log.warning("Could not persist derived-tape cache to %s", path)

    _MEMO[memo_key] = tape
    return tape


def declared_annex_id_for(url: str, *, cache_dir: str | Path | None = None) -> str | None:
    """The annex a derived tape **states** it resolves through, or ``None``.

    Annex detection sniffs a signature, and Annex 4's entire signature is
    ``enterprise_size`` — a field no trustee report publishes, which is why
    ``ANNEX_REGISTRY.detect()`` returns ``None`` for this tape (#470 pinned it,
    and refused to widen the signature or fabricate the column). Detection is an
    inference for a tape of unknown origin; a derived tape knows its annex at
    derivation time, so it states one and the normaliser prefers the statement.
    """
    if not is_derived_uri(url):
        return None
    return derive_tape(url, cache_dir=cache_dir).annex_id
