"""The per-deal investor due-diligence record — what was verified, and what was not.

UK Securitisation Regulation obliges an institutional investor to verify risk
retention **before** holding a position. This module produces the evidence that
the verification was attempted: for one deal, what was established, from which
document, when that document was read — and, for anything that could not be
established, a reason naming the document that was looked in.

**This is not the deal's own compliance state.** Whether a deal sits inside its
structural covenants is ``/deal/{id}/compliance`` and a different question for a
different reader: a deal can pass every covenant while the holder's retention
verification is undocumented, and the reverse. The two surfaces share
infrastructure — :class:`~loanwhiz.primitives.base.Citation`,
:class:`~loanwhiz.primitives.base.PrimitiveResult`, the honest-absence
discipline — and deliberately share none of their vocabulary, so this module
says ``verified`` / ``not-established`` where the capability matrix says
``validated`` / ``ran`` / ``not-applicable``. Conflating the two is the category
error #241, #481 and #549 each had to unpick.

Three properties carry the whole surface:

**A refusal names the document it looked in.** "Retention is not established" is
a claim about a *document*, never about the deal (#480). A future reader must be
able to re-ask the question of a document this one did not read, so every
not-established check names the slot and URL that were consulted.

**Verified and not-established are different kinds of record**, kept in two
lists rather than one list with a flag. An empty instance of the graded kind
passes vacuously — ``all([])`` is ``True`` — so "verified nothing" and "nothing
to verify" would otherwise be one output, and the ungraded fraction would read
as free green (#513).

**Every check carries a non-empty reason, on both outcomes.** The capability
matrix requires this of its ``not-applicable`` cells and enforces it with a
builder-side backstop that silently substitutes a generic sentence. Here it is a
model validator: a blank reason is unconstructable, so the honesty contract
cannot be satisfied by a placeholder (#241, wording corrected by #457).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from loanwhiz.extraction.assembler import DealModel
from loanwhiz.extraction.retention_parser import RiskRetention, UnsourcedRetention
from loanwhiz.primitives.base import AuditEntry, BaseInput, Citation, PrimitiveResult

__all__ = [
    "CHECK_RISK_RETENTION",
    "CheckOutcome",
    "DueDiligenceCheck",
    "DueDiligenceInput",
    "DueDiligenceRecord",
    "SourceDocument",
    "assemble_due_diligence",
]


#: The outcomes a due-diligence check can reach. **Closed on purpose**, and
#: closed at *construction* rather than at the surface: a third state would
#: reach a compliance reader as an unhandled value, and the two here are
#: exhaustive because a check either established its fact or did not.
#:
#: Deliberately NOT the capability matrix's ``validated`` / ``ran`` /
#: ``not-applicable``. That trichotomy answers "did the primitive run, and was
#: its output reconciled?" — a question about this platform. These two answer
#: "is the regulatory fact established?" — a question about the deal's
#: documents. Sharing the words would invite reading one as the other.
CheckOutcome = Literal["verified", "not-established"]

#: The stable key for the risk-retention check. A module constant so a test
#: asserting the check is present cannot drift from the string that produces it.
CHECK_RISK_RETENTION = "risk_retention"

#: This module's identity in the audit trail.
PRIMITIVE_NAME = "due_diligence_record"
PRIMITIVE_VERSION = "1.0.0"

#: The registry slot every retention check is answered from. Named as a
#: constant because the refusal reasons quote it: a reader who wants to
#: challenge a refusal needs to know which field was consulted.
PROSPECTUS_SLOT = "prospectus_url"

#: Keys of :meth:`RiskRetention.to_dict` that are *renderings*, not facts read
#: from the document, and so must not reach a compliance reader as provenance.
#:
#: ``source`` is the one that matters: it renders as ``"Listing Particulars,
#: Article 6(3)(d)"`` for **every** deal, because the document kind is a literal
#: in the parser rather than something checked against the registry. This record
#: already carries real provenance in :class:`SourceDocument` — the slot and the
#: URL actually consulted — and two provenance claims where only one is
#: established is precisely the confident wrongness the record exists to avoid.
#: The Article citation itself is not lost: it is the citation's ``page_or_row``.
_DERIVED_PROVENANCE: frozenset[str] = frozenset({"source"})

# ---------------------------------------------------------------------------
# Refusal vocabulary — closed, and imported by tests rather than transcribed.
#
# A reworded reason must red a test rather than pass one asserting the old
# sentence. Each says only what the *input* encodes (#457): "no block is
# committed" is a statement about this repository, whereas "this deal publishes
# no retention undertaking" would be a claim about the world that nothing here
# establishes.
# ---------------------------------------------------------------------------

#: The deal has no registered offering document at all, so no document was
#: looked in. Distinct from the two below: there is nothing to re-ask.
NO_SOURCE_DOCUMENT = (
    "no offering document is registered for this deal, so no document was read "
    "for a retention undertaking"
)

#: An offering document is registered but **no extracted model is committed**
#: for this deal, so the document has not been read at all. Distinct from the
#: next one, which is a statement about a reading that did happen: a reason
#: covering both would claim a model exists and lacks the undertaking, which is
#: a wider claim than the input encodes (#457).
NO_COMMITTED_MODEL = (
    "no extracted model is committed for this deal, so the registered offering "
    "document has not been read for a retention undertaking"
)

#: An offering document is registered and its extracted model carries no
#: undertaking, with nothing on record saying the extraction was cut short.
#: The absence is a fact about *this reading of that document*, and the sentence
#: says so rather than claiming the document states none.
NOT_READ_FROM_DOCUMENT = (
    "the committed model for this deal carries no retention undertaking; it was "
    "not read from the registered offering document, which is not the same as "
    "that document stating none"
)

#: The extraction that produced the committed model recorded a truncated or
#: implausible section. The undertaking's absence is then *unproven* rather than
#: absent — the distinction :func:`assess_retention` exists to draw, recorded
#: here as the case that applies.
ABSENCE_UNPROVEN = (
    "the extraction that produced this model recorded a truncated or implausible "
    "section, so the absence of a retention undertaking is unproven rather than "
    "established"
)

#: The committed block exists but does not survive
#: :meth:`RiskRetention.from_dict` — an unrecognised Article 6(3) sub-paragraph
#: or a capacity the Regulation does not recognise. A partial record would be
#: *confidently incomplete*, which is worse than an admitted gap, so it refuses.
UNSOURCED_PREFIX = "the committed retention block is not a complete undertaking: "

#: No document date is recorded anywhere in the registry, for any deal. This is
#: a gap in the registry, not in the document, and the sentence says which.
NO_DOCUMENT_DATE = (
    "the registry records no publication date for this document; only the date "
    "it was read is known"
)

#: Everything the record refuses to say, as one set. Tests assert membership
#: against this rather than against a transcribed sentence.
REFUSAL_VOCABULARY: frozenset[str] = frozenset(
    {NO_SOURCE_DOCUMENT, NO_COMMITTED_MODEL, NOT_READ_FROM_DOCUMENT, ABSENCE_UNPROVEN}
)


class SourceDocument(BaseModel):
    """The document a check was answered from, and the two dates that differ.

    ``read_at`` is when *this platform* read the document. ``document_date`` is
    when the document itself is dated — a different fact, and one no registry
    field carries today, so it is ``None`` with ``document_date_reason`` saying
    why. Passing the first off as the second is the #479 error in a new place:
    a prospectus extraction states closing-date terms and no as-of-date fact,
    and its ``extracted_at`` is our clock.

    Contego CLO XI is why this matters concretely. It has two Listing
    Particulars — 29-Jun-2023 and the 19-Nov-2024 reset — and the pre-reset one
    is registered on purpose, because every trustee report parsed against it is
    pre-reset (#532). A record citing the wrong one would be confidently wrong
    rather than visibly broken, so the URL is carried verbatim and the
    registry's own note travels with it.
    """

    registry_slot: str = Field(..., description="The registry key this document came from.")
    url: str = Field(..., description="The document URL, verbatim from the registry.")
    read_at: str | None = Field(
        default=None,
        description="ISO 8601 timestamp of when this platform read the document, or None.",
    )
    document_date: str | None = Field(
        default=None,
        description="The document's own date. None when the registry records none.",
    )
    document_date_reason: str = Field(
        ...,
        description="Why document_date is absent — REQUIRED and non-empty when it is None.",
    )
    registry_note: str | None = Field(
        default=None,
        description="The registry's registration_note, where the entry carries one.",
    )

    @model_validator(mode="after")
    def _absent_date_carries_a_reason(self) -> "SourceDocument":
        """An absent date without a reason is a silent blank, which is the failure."""
        if self.document_date is None and not self.document_date_reason.strip():
            raise ValueError(
                "document_date is None and document_date_reason is empty — an "
                "absent date must name why it is absent"
            )
        return self


class DueDiligenceCheck(BaseModel):
    """One regulatory question asked of one deal, and the answer or the refusal.

    ``reason`` is mandatory and non-empty on **both** outcomes, enforced by the
    validator below rather than by a builder that substitutes a default. On
    ``verified`` it says what was established; on ``not-established`` it names
    the document that was looked in, so the limitation can be re-asked of a
    document this reading did not reach (#480).
    """

    check: str = Field(..., description="Stable check identifier.")
    outcome: CheckOutcome = Field(..., description="'verified' or 'not-established'.")
    reason: str = Field(..., description="Human reason — REQUIRED and non-empty on both outcomes.")
    source: SourceDocument | None = Field(
        default=None,
        description="The document this check was answered from; None when none was registered.",
    )
    citations: list[Citation] = Field(
        default_factory=list,
        description="Source references grounding a verified check.",
    )
    detail: dict[str, Any] = Field(
        default_factory=dict,
        description="Structured, JSON-serialisable detail of what was established.",
    )

    @model_validator(mode="after")
    def _reason_is_never_blank(self) -> "DueDiligenceCheck":
        """A blank reason is unconstructable — the honesty contract, as a type.

        The capability matrix enforces the same rule with a builder-side
        backstop that quietly substitutes a generic sentence when the reason is
        empty. That makes a missing reason invisible. Here the model refuses,
        so a check reaching a compliance reader without one cannot exist.
        """
        if not self.reason.strip():
            raise ValueError(
                f"check {self.check!r} has a blank reason — every outcome, "
                "verified included, must say why"
            )
        return self


class DueDiligenceRecord(BaseModel):
    """One deal's record: what was established, and what was not, as two kinds.

    The two lists are **not** one list with an outcome flag. A grader that keeps
    its skips in the graded collection passes vacuously on them, because an
    empty instance of the graded kind satisfies every aggregate over it
    (#513) — so "verified nothing" and "had nothing to verify" would produce the
    same output and the unestablished fraction would read as free green.
    Counting reads ``verified``; the refusals are their own kind and carry their
    own reasons.
    """

    deal_id: str = Field(..., description="Canonical deal id.")
    deal_name: str = Field(..., description="Human-readable deal name.")
    verified: list[DueDiligenceCheck] = Field(
        default_factory=list,
        description="Checks that established their fact.",
    )
    not_established: list[DueDiligenceCheck] = Field(
        default_factory=list,
        description="Checks that did not, each naming why and which document was read.",
    )

    @model_validator(mode="after")
    def _each_list_holds_its_own_outcome(self) -> "DueDiligenceRecord":
        """A refusal filed under ``verified`` would defeat the split entirely."""
        for check in self.verified:
            if check.outcome != "verified":
                raise ValueError(
                    f"check {check.check!r} is filed under 'verified' but its "
                    f"outcome is {check.outcome!r}"
                )
        for check in self.not_established:
            if check.outcome != "not-established":
                raise ValueError(
                    f"check {check.check!r} is filed under 'not_established' but "
                    f"its outcome is {check.outcome!r}"
                )
        return self


class DueDiligenceInput(BaseInput):
    """The identity of what was assembled, for the audit trail's input hash."""

    deal_id: str


def _source_document(
    deal: Mapping[str, Any], model: DealModel | None
) -> SourceDocument | None:
    """Build the source document from the registry entry, or ``None`` if unregistered.

    ``read_at`` comes from the committed model's ``extracted_at`` — our clock,
    and labelled as such. ``document_date`` is always ``None`` today: no
    registry field carries a publication date, and inferring one from an S3
    upload path would manufacture the confident wrongness this record exists to
    avoid.
    """
    url = deal.get(PROSPECTUS_SLOT)
    if not url:
        return None
    note = deal.get("registration_note")
    return SourceDocument(
        registry_slot=PROSPECTUS_SLOT,
        url=str(url),
        read_at=model.metadata.extracted_at if model is not None else None,
        document_date=None,
        document_date_reason=NO_DOCUMENT_DATE,
        registry_note=str(note) if note else None,
    )


def _extraction_recorded_a_gap(model: DealModel | None) -> bool:
    """Whether the extraction that produced ``model`` recorded a cut-short section.

    Two mechanisms leave a section unusable and look identical downstream — a
    character budget clipped it, or a heading promotion ended the routed span
    early — so both surfaces are consulted rather than either alone. When either
    fired, an absent undertaking is *unproven* rather than absent.
    """
    if model is None:
        return False
    if model.metadata.truncations:
        return True
    coverage = model.metadata.glossary_coverage
    return bool(coverage) and bool(coverage.get("implausible"))


def _retention_check(
    deal: Mapping[str, Any], model: DealModel | None, source: SourceDocument | None
) -> DueDiligenceCheck:
    """Answer the retention question for one deal, or refuse and say why.

    The refusal branches are ordered by what they claim, narrowest first: no
    document read at all, an unusable committed block, an unproven absence, and
    finally a plain unread absence. Only the last is reached when nothing is
    known to have gone wrong, and even it does not say the document states no
    undertaking — nothing on record establishes that.
    """
    if source is None:
        return DueDiligenceCheck(
            check=CHECK_RISK_RETENTION,
            outcome="not-established",
            reason=NO_SOURCE_DOCUMENT,
            source=None,
        )

    if model is None:
        return DueDiligenceCheck(
            check=CHECK_RISK_RETENTION,
            outcome="not-established",
            reason=f"{NO_COMMITTED_MODEL} ({source.registry_slot}: {source.url})",
            source=source,
        )

    block = model.risk_retention
    if block is None:
        reason = ABSENCE_UNPROVEN if _extraction_recorded_a_gap(model) else NOT_READ_FROM_DOCUMENT
        return DueDiligenceCheck(
            check=CHECK_RISK_RETENTION,
            outcome="not-established",
            reason=f"{reason} ({source.registry_slot}: {source.url})",
            source=source,
        )

    try:
        retention = RiskRetention.from_dict(dict(block))
    except (UnsourcedRetention, KeyError, TypeError, ValueError) as exc:
        return DueDiligenceCheck(
            check=CHECK_RISK_RETENTION,
            outcome="not-established",
            reason=f"{UNSOURCED_PREFIX}{exc}",
            source=source,
        )

    return DueDiligenceCheck(
        check=CHECK_RISK_RETENTION,
        outcome="verified",
        reason=(
            f"{retention.retainer} retains as {retention.retainer_capacity} under "
            f"{retention.article} ({retention.method}), at {retention.level_pct}% of "
            f"{retention.level_basis}"
        ),
        source=source,
        citations=[
            Citation(
                document=source.url,
                page_or_row=retention.article,
                excerpt=(
                    f"{retention.retainer}, as {retention.retainer_capacity}, retains a "
                    f"material net economic interest of not less than {retention.level_pct}% "
                    f"of {retention.level_basis} by way of the {retention.method} "
                    f"({retention.article})"
                ),
            )
        ],
        detail={k: v for k, v in retention.to_dict().items() if k not in _DERIVED_PROVENANCE},
    )


def assemble_due_diligence(
    deal_id: str,
    deal: Mapping[str, Any],
    *,
    model: DealModel | None,
) -> PrimitiveResult[DueDiligenceRecord]:
    """Assemble one deal's due-diligence record from committed data.

    Args:
        deal_id: The canonical deal id.
        deal: The deal's registry entry, as ``deals.json`` carries it.
        model: The deal's committed extracted model, or ``None`` when none is
            committed. Passed in rather than loaded, so the seam is testable
            and the caller owns document resolution.

    Returns:
        A :class:`~loanwhiz.primitives.base.PrimitiveResult` whose ``output`` is
        the record. ``confidence`` is ``1.0`` because the assembly is
        deterministic — it is a statement about *reading committed data*, never
        about how sure the platform is that the underlying fact is true. A
        refusal is a certain refusal.
    """
    started = time.perf_counter()

    source = _source_document(deal, model)
    checks = [_retention_check(deal, model, source)]

    record = DueDiligenceRecord(
        deal_id=deal_id,
        deal_name=str(deal.get("deal_name", deal_id)),
        verified=[c for c in checks if c.outcome == "verified"],
        not_established=[c for c in checks if c.outcome == "not-established"],
    )

    return PrimitiveResult[DueDiligenceRecord](
        output=record,
        confidence=1.0,
        citations=[c for check in checks for c in check.citations],
        audit_entry=AuditEntry.now(
            primitive_name=PRIMITIVE_NAME,
            version=PRIMITIVE_VERSION,
            input_hash=DueDiligenceInput(deal_id=deal_id).input_hash(),
            duration_ms=(time.perf_counter() - started) * 1000.0,
        ),
    )
