"""The per-deal due-diligence record — and, above all, what it refuses to say.

The record is the deliverable and its refusals are as load-bearing as its
verifications, so this suite asserts the refusals at least as hard. Three
properties carry it, and each is tested in both directions because a check
asserted in one direction proves nothing (#493):

**A refusal names the document it looked in.** "Retention is not established" is
a claim about a document, never about the deal (#480) — so the negative twin of
"Cairn verifies" is not "Contego refuses" but "Contego refuses *and says which
document was read*", and the paired positive supplies the one missing input and
watches the same deal flip to verified.

**Two guards here pass by finding nothing**, which makes "nothing is wrong" and
"I saw nothing" the same output (#494). Both therefore have a counter-example
test that constructs the thing they hunt and asserts they flag it. A guard with
no such twin is decoration.

**The Cairn/Contego asymmetry is the subject, not an accident.** A capability
verified on one deal is a claim about that deal (#535), and the second CLO is
exactly where this one stops: #566 taught the parser to read Contego's
undertaking but committed the block only for Cairn. The tests pin that stopping
point by name, so the day Contego's seed gains a block they red rather than
quietly keeping the old answer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from loanwhiz.config import DEAL_REGISTRY
from loanwhiz.extraction.assembler import DealModel
from loanwhiz.primitives.due_diligence import (
    ABSENCE_UNPROVEN,
    CHECK_RISK_RETENTION,
    NO_DOCUMENT_DATE,
    NO_SOURCE_DOCUMENT,
    NOT_READ_FROM_DOCUMENT,
    REFUSAL_VOCABULARY,
    UNSOURCED_PREFIX,
    DueDiligenceCheck,
    DueDiligenceRecord,
    SourceDocument,
    assemble_due_diligence,
)

TESTS_DIR = Path(__file__).resolve().parent
SEED_DIR = TESTS_DIR.parent / "src" / "loanwhiz" / "data" / "deals" / "seed"

CAIRN_DEAL_ID = "cairn-clo-xvii"
CONTEGO_DEAL_ID = "contego-clo-xi"

#: Seed filenames are the deal's legal name, not its registry key, so the two
#: cannot be derived from one another.
SEED_NAMES = {
    CAIRN_DEAL_ID: "cairn-clo-xvii-dac.json",
    CONTEGO_DEAL_ID: "contego-clo-xi-dac.json",
}


def _model(deal_id: str) -> DealModel:
    return DealModel.model_validate_json(
        (SEED_DIR / SEED_NAMES[deal_id]).read_text(encoding="utf-8")
    )


def _record(deal_id: str, model: DealModel | None = None) -> DueDiligenceRecord:
    if model is None:
        model = _model(deal_id)
    return assemble_due_diligence(deal_id, DEAL_REGISTRY[deal_id], model=model).output


def _only(checks: list[DueDiligenceCheck]) -> DueDiligenceCheck:
    assert len(checks) == 1, f"expected exactly one check, got {len(checks)}"
    return checks[0]


# ---------------------------------------------------------------------------
# 1. The verification — and everything it is obliged to carry with it.
# ---------------------------------------------------------------------------


def test_cairn_verifies_its_retention_undertaking() -> None:
    """The deal whose seed carries the block reaches ``verified``, with its citation."""
    record = _record(CAIRN_DEAL_ID)

    check = _only(record.verified)
    assert check.check == CHECK_RISK_RETENTION
    assert check.outcome == "verified"
    assert not record.not_established

    assert check.detail["method_letter"] == "d"
    assert check.detail["retainer_capacity"] == "originator"
    assert "Cairn Loan Investments" in check.detail["retainer"] or check.detail[
        "retainer"
    ].startswith("The Investment Manager")


def test_a_verification_cites_the_document_it_was_read_from() -> None:
    """"Retention verified" is worth nothing without the document behind it."""
    check = _only(_record(CAIRN_DEAL_ID).verified)

    citation = _only(check.citations)
    assert citation.document == DEAL_REGISTRY[CAIRN_DEAL_ID]["prospectus_url"]
    assert citation.page_or_row == "Article 6(3)(d)"
    assert "5.0%" in citation.excerpt or "5.0" in citation.excerpt


def test_a_verified_check_still_carries_a_reason() -> None:
    """The reason is not a refusal-only field — a verification says what it established."""
    check = _only(_record(CAIRN_DEAL_ID).verified)

    assert check.reason.strip()
    assert "Article 6(3)(d)" in check.reason
    assert "originator" in check.reason


# ---------------------------------------------------------------------------
# 2. The refusal — asserted harder than the verification.
# ---------------------------------------------------------------------------


def test_contego_is_not_established_and_names_the_document_looked_in() -> None:
    """The second CLO is where this stops, and the refusal says which document was read.

    #566 taught the parser to read Contego's undertaking; it committed the block
    only for Cairn. So the honest answer for Contego today is a refusal — and a
    refusal that names its document, because the limitation belongs to *this
    reading of that document*, not to the deal (#480).
    """
    record = _record(CONTEGO_DEAL_ID)

    assert not record.verified
    check = _only(record.not_established)
    assert check.outcome == "not-established"
    assert check.source is not None
    assert check.source.url == DEAL_REGISTRY[CONTEGO_DEAL_ID]["prospectus_url"]
    # The document is named inside the sentence, not merely attached beside it.
    assert DEAL_REGISTRY[CONTEGO_DEAL_ID]["prospectus_url"] in check.reason
    assert check.reason.startswith(NOT_READ_FROM_DOCUMENT)


def test_the_refusal_flips_to_verified_when_the_block_is_supplied() -> None:
    """The paired positive: supply the one missing input, change nothing else.

    Without this, the Contego assertion above passes for whichever layer refuses
    first and keeps passing with the fix reverted (#493). Cairn's committed
    block is grafted onto Contego's model so that the *only* difference is the
    input the refusal named.
    """
    contego = _model(CONTEGO_DEAL_ID)
    assert contego.risk_retention is None, "premise: Contego's seed carries no block"

    patched = contego.model_copy(update={"risk_retention": _model(CAIRN_DEAL_ID).risk_retention})
    record = _record(CONTEGO_DEAL_ID, model=patched)

    assert not record.not_established
    assert _only(record.verified).outcome == "verified"


def test_contego_carries_its_pre_reset_registration_note_into_the_record() -> None:
    """Contego has two Listing Particulars; the record must show which one was read.

    #532 registered the 29-Jun-2023 document deliberately, because every trustee
    report parsed against it is pre-reset. A record that cited the reset would be
    confidently wrong rather than visibly broken, so the registry's own reason
    travels with the citation rather than staying in `deals.json`.
    """
    source = _only(_record(CONTEGO_DEAL_ID).not_established).source
    assert source is not None
    assert source.registry_note is not None
    assert "2023" in source.registry_note and "reset" in source.registry_note.lower()


def test_an_unregistered_document_refuses_differently_from_an_unread_one() -> None:
    """"No document to read" and "read it, found nothing" are different facts.

    Collapsing them would tell a reader to go and re-read a document that was
    never registered, or to register one that already is.
    """
    record = assemble_due_diligence(
        "no-such-deal", {"deal_name": "Unregistered"}, model=None
    ).output

    check = _only(record.not_established)
    assert check.reason == NO_SOURCE_DOCUMENT
    assert check.source is None
    assert check.reason != NOT_READ_FROM_DOCUMENT


def test_a_truncated_extraction_makes_the_absence_unproven_not_absent() -> None:
    """The distinction the whole surface exists for, recorded as the case that applies.

    A section clipped by a character budget and a section that ended early leave
    the same empty parse as a document that genuinely says nothing — and only
    one of those is a fact about the deal. When the extraction recorded a gap,
    the record says the absence is unproven rather than established.
    """
    contego = _model(CONTEGO_DEAL_ID)
    truncated = contego.model_copy(
        update={
            "metadata": contego.metadata.model_copy(
                update={"truncations": [{"section": "definitions", "max_chars": 40000}]}
            )
        }
    )

    check = _only(_record(CONTEGO_DEAL_ID, model=truncated).not_established)
    assert check.reason.startswith(ABSENCE_UNPROVEN)
    assert not check.reason.startswith(NOT_READ_FROM_DOCUMENT)


def test_an_implausible_glossary_also_makes_the_absence_unproven() -> None:
    """The other mechanism, which leaves no truncation record at all.

    Section orphaning ends the routed span early without any character budget
    engaging, so a check reading only ``truncations`` misses it (#548). Both
    surfaces are consulted, and this pins the second.
    """
    contego = _model(CONTEGO_DEAL_ID)
    orphaned = contego.model_copy(
        update={
            "metadata": contego.metadata.model_copy(
                update={"glossary_coverage": {"implausible": True, "reason": "4 terms"}}
            )
        }
    )

    assert _only(_record(CONTEGO_DEAL_ID, model=orphaned).not_established).reason.startswith(
        ABSENCE_UNPROVEN
    )


def test_a_plausible_glossary_does_not_make_the_absence_unproven() -> None:
    """The guard's negative twin — a check that fires on everything proves nothing."""
    contego = _model(CONTEGO_DEAL_ID)
    healthy = contego.model_copy(
        update={
            "metadata": contego.metadata.model_copy(
                update={"glossary_coverage": {"implausible": False, "reason": ""}}
            )
        }
    )

    assert _only(_record(CONTEGO_DEAL_ID, model=healthy).not_established).reason.startswith(
        NOT_READ_FROM_DOCUMENT
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [("method_letter", "z"), ("retainer_capacity", "the Retention Holder")],
)
def test_a_block_outside_the_regulations_vocabulary_is_refused_not_repaired(
    field: str, value: str
) -> None:
    """A partial record is *confidently incomplete*, which is worse than an admitted gap.

    Both near-misses are mutations of the real committed block rather than
    invented shapes, so the test cannot pass because its input was malformed in
    some other way.
    """
    cairn = _model(CAIRN_DEAL_ID)
    assert cairn.risk_retention is not None
    broken = dict(cairn.risk_retention)
    broken[field] = value

    check = _only(
        _record(CAIRN_DEAL_ID, model=cairn.model_copy(update={"risk_retention": broken})).not_established
    )
    assert check.reason.startswith(UNSOURCED_PREFIX)
    # The parser reports the offending value case-folded, so the refusal is
    # matched on the value it actually names rather than the input's casing.
    assert value.lower() in check.reason.lower()


# ---------------------------------------------------------------------------
# 3. The two dates that must never be conflated.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("deal_id", sorted(SEED_NAMES))
def test_the_document_date_is_refused_with_a_reason(deal_id: str) -> None:
    """No registry field carries a publication date, so the record refuses to state one."""
    check = _only(_record(deal_id).verified + _record(deal_id).not_established)
    assert check.source is not None
    assert check.source.document_date is None
    assert check.source.document_date_reason == NO_DOCUMENT_DATE


@pytest.mark.parametrize("deal_id", sorted(SEED_NAMES))
def test_the_read_date_is_not_passed_off_as_the_documents_date(deal_id: str) -> None:
    """``extracted_at`` is our clock (#479); it fills ``read_at`` and nothing else."""
    source = _only(_record(deal_id).verified + _record(deal_id).not_established).source
    assert source is not None
    assert source.read_at == _model(deal_id).metadata.extracted_at
    assert source.read_at is not None
    assert source.document_date != source.read_at


def test_an_absent_document_date_cannot_ship_without_a_reason() -> None:
    """A silent blank is the failure this field exists to prevent."""
    with pytest.raises(ValidationError, match="must name why it is absent"):
        SourceDocument(
            registry_slot="prospectus_url",
            url="https://example.invalid/lp.pdf",
            document_date=None,
            document_date_reason="   ",
        )


# ---------------------------------------------------------------------------
# 4. The honesty contract, as a type rather than a backstop.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", ["verified", "not-established"])
def test_a_blank_reason_is_unconstructable_on_either_outcome(outcome: str) -> None:
    """Both outcomes, not only refusals — a verification with no reason says nothing.

    The capability matrix enforces the same rule with a builder-side default
    substitution, which makes a missing reason invisible rather than impossible.
    Here the model refuses.
    """
    with pytest.raises(ValidationError, match="blank reason"):
        DueDiligenceCheck(check=CHECK_RISK_RETENTION, outcome=outcome, reason="  ")


def test_the_outcome_vocabulary_is_closed_at_construction() -> None:
    """A third state would reach a compliance reader as an unhandled value."""
    for invalid in ("validated", "ran", "not-applicable", "partial", "pending"):
        with pytest.raises(ValidationError):
            DueDiligenceCheck(check=CHECK_RISK_RETENTION, outcome=invalid, reason="x")


# ---------------------------------------------------------------------------
# 5. Two kinds of record, so an empty graded list is not free green.
# ---------------------------------------------------------------------------


def test_a_refusal_cannot_be_filed_under_verified() -> None:
    """The split is worthless if a refusal can sit in the graded list."""
    refusal = DueDiligenceCheck(
        check=CHECK_RISK_RETENTION, outcome="not-established", reason=NO_SOURCE_DOCUMENT
    )
    with pytest.raises(ValidationError, match="filed under 'verified'"):
        DueDiligenceRecord(deal_id="d", deal_name="D", verified=[refusal])


def test_a_verification_cannot_be_filed_under_not_established() -> None:
    """The inverse, which would understate what was established."""
    verified = DueDiligenceCheck(
        check=CHECK_RISK_RETENTION, outcome="verified", reason="established"
    )
    with pytest.raises(ValidationError, match="filed under 'not_established'"):
        DueDiligenceRecord(deal_id="d", deal_name="D", not_established=[verified])


def test_a_record_that_established_nothing_has_an_empty_verified_list() -> None:
    """"Verified nothing" and "nothing to verify" must not be one output (#513).

    Contego establishes nothing today, so its graded list is empty *and* its
    refusal list is not — the shape that stops an aggregate over ``verified``
    reading as a pass.
    """
    record = _record(CONTEGO_DEAL_ID)
    assert record.verified == []
    assert record.not_established
    assert all(c.reason.strip() for c in record.not_established)


# ---------------------------------------------------------------------------
# 6. The two grep-for-absence guards, each with the counter-example that fires it.
# ---------------------------------------------------------------------------

#: Vocabulary belonging to `/deal/{id}/compliance` — the deal's own structural
#: tests. None of it may appear in an investor's due-diligence record: they are
#: different questions for different readers, and a reader who sees covenant
#: words here will read one as the other.
COMPLIANCE_VOCABULARY: tuple[str, ...] = (
    "covenant",
    "trigger",
    "breach",
    "overcollateralisation",
    "coverage test",
    "oc_ratio",
    "ic_ratio",
    "not_evaluable",
)

#: Claims about the *world* that nothing on record establishes. The record may
#: say a document was not read; it may never say a document states no retention
#: (#457 — say only what the input encodes).
ABSENCE_OVERCLAIMS: tuple[str, ...] = (
    "states no retention",
    "publishes no retention",
    "retains nothing",
    "does not retain",
    "no retention undertaking exists",
)


def _words_found(record: DueDiligenceRecord, vocabulary: tuple[str, ...]) -> set[str]:
    """Every term from ``vocabulary`` present in the record's serialised form."""
    blob = record.model_dump_json().lower()
    return {word for word in vocabulary if word in blob}


def _every_registered_record() -> list[DueDiligenceRecord]:
    """One record per registered deal, seeded where a seed exists."""
    records = []
    for deal_id, entry in DEAL_REGISTRY.items():
        model = _model(deal_id) if deal_id in SEED_NAMES else None
        records.append(assemble_due_diligence(deal_id, entry, model=model).output)
    return records


def test_the_leak_guard_reaches_every_registered_deal() -> None:
    """A scan that finds nothing because it looked at nothing is a clean report (#494)."""
    records = _every_registered_record()
    assert records, "the scan collected no records — it cannot have checked anything"
    assert {r.deal_id for r in records} >= {CAIRN_DEAL_ID, CONTEGO_DEAL_ID}


def test_no_record_carries_the_deals_own_compliance_vocabulary() -> None:
    """The umbrella's whole argument: different subject, different reader."""
    for record in _every_registered_record():
        assert _words_found(record, COMPLIANCE_VOCABULARY) == set(), record.deal_id


def test_the_leak_guard_flags_a_covenant_flavoured_check() -> None:
    """fires-when: the guard must flag the thing it hunts, or it is decoration."""
    leaked = DueDiligenceRecord(
        deal_id="d",
        deal_name="D",
        not_established=[
            DueDiligenceCheck(
                check=CHECK_RISK_RETENTION,
                outcome="not-established",
                reason="the Class A overcollateralisation trigger is in breach",
            )
        ],
    )
    assert _words_found(leaked, COMPLIANCE_VOCABULARY) >= {
        "trigger",
        "breach",
        "overcollateralisation",
    }


def test_no_record_claims_a_document_states_no_retention() -> None:
    """An absence read from a document is not the document asserting the absence."""
    for record in _every_registered_record():
        assert _words_found(record, ABSENCE_OVERCLAIMS) == set(), record.deal_id


def test_the_wording_guard_flags_a_reason_claiming_the_document_states_none() -> None:
    """fires-when: the overclaim the shipped vocabulary is written to avoid."""
    overclaiming = DueDiligenceRecord(
        deal_id="d",
        deal_name="D",
        not_established=[
            DueDiligenceCheck(
                check=CHECK_RISK_RETENTION,
                outcome="not-established",
                reason="the offering document states no retention undertaking",
            )
        ],
    )
    assert _words_found(overclaiming, ABSENCE_OVERCLAIMS) == {"states no retention"}


def test_the_shipped_refusal_vocabulary_is_what_the_records_actually_use() -> None:
    """Reasons come from the module's closed set, so a rewording reds a test.

    Asserted against the imported constants rather than transcribed sentences:
    a test carrying its own copy of the prose passes while the prose drifts.
    """
    for record in _every_registered_record():
        for check in record.not_established:
            assert any(
                check.reason.startswith(known)
                for known in (*REFUSAL_VOCABULARY, UNSOURCED_PREFIX)
            ), f"{record.deal_id}: {check.reason}"


# ---------------------------------------------------------------------------
# 7. The envelope.
# ---------------------------------------------------------------------------


def test_the_result_carries_a_deterministic_confidence_and_an_audit_entry() -> None:
    """Confidence is about the assembly, never about the underlying fact.

    A refusal is a *certain* refusal: reading committed data is deterministic,
    so 1.0 says the assembly is exact and says nothing about whether the deal
    retains.
    """
    result = assemble_due_diligence(
        CONTEGO_DEAL_ID, DEAL_REGISTRY[CONTEGO_DEAL_ID], model=_model(CONTEGO_DEAL_ID)
    )

    assert result.confidence == 1.0
    assert result.output.verified == []
    assert result.audit_entry.primitive_name == "due_diligence_record"
    assert len(result.audit_entry.input_hash) == 64
    assert result.citations == []


def test_the_envelope_citations_are_the_checks_citations() -> None:
    """The result-level list is the union, not a second hand-built one that can drift."""
    result = assemble_due_diligence(
        CAIRN_DEAL_ID, DEAL_REGISTRY[CAIRN_DEAL_ID], model=_model(CAIRN_DEAL_ID)
    )
    assert result.citations == [c for check in result.output.verified for c in check.citations]
    assert result.citations


def test_the_record_serialises_for_a_surface_to_render(tmp_path: Path) -> None:
    """#568 renders this; a record that cannot round-trip cannot be surfaced."""
    record = _record(CAIRN_DEAL_ID)
    restored = DueDiligenceRecord.model_validate(json.loads(record.model_dump_json()))
    assert restored == record


@pytest.mark.parametrize("deal_id", sorted(DEAL_REGISTRY))
def test_every_registered_deal_assembles_a_record(deal_id: str) -> None:
    """No deal is silently missing from the surface, seeded or not."""
    model = _model(deal_id) if deal_id in SEED_NAMES else None
    record = assemble_due_diligence(deal_id, DEAL_REGISTRY[deal_id], model=model).output

    assert record.deal_id == deal_id
    checks: list[Any] = [*record.verified, *record.not_established]
    assert checks, "a deal with no checks at all would be an invisible gap"
    assert all(c.reason.strip() for c in checks)
