"""Tests for Contego CLO XI DAC, registered in ``data/deals.json`` (#532, epic #530).

**Contego CLO XI DAC** is the registry's second CLO and its second trustee-report
family: Five Arrows Managers LLP manages it, and its collateral administrator is
**The Bank of New York Mellon S.A./N.V., Dublin Branch** rather than Cairn's U.S.
Bank. It is listed on Euronext Dublin under issuer 29975.

The reset boundary, which is the whole reason this file exists
----------------------------------------------------------------
Euronext publishes **two** Listing Particulars for this issuer: the original
**29-Jun-2023** and a **19-Nov-2024 reset**. They describe different capital
stacks — the 2023 document's Class A is EUR 228.7m due 2035, the reset's Class
**A-R** is EUR 310m due **2038** — and every trustee report the exchange
publishes for this deal is from **August and September 2024**, i.e. *before* the
reset.

So the 2023 document is the one that describes the structure those reports report
on, and registering the newer, more obvious-looking one would produce a seed
whose tranches, coupons and coverage tests belong to a different stack than the
reports later parsed against it. That failure is **silent**: the figures would
still compute, and every one of them would be wrong. This is the same hazard that
disqualified Nassau Euro CLO II as a candidate.

The assertions below pin the choice *and* its reason, because a future editor
swapping in the reset URL would be doing something that looks like an upgrade.

What is deliberately absent
---------------------------
* ``notes_cash_report_urls`` — Contego's 86pp NOTE VALUATION REPORT is obtainable
  and is the PoP-bearing document, but that key is a *routing promise*, not a URL
  slot: ``test_quality_harness.test_answer_keys_exist_exactly_where_published_ground_truth_does``
  reads its presence as an assertion that a PoP-bearing answer key exists. Setting
  it before #534 authors that key would assert a promise this deal cannot keep —
  exactly the #455 discipline Cairn's registration followed.
* ``tape_urls`` — empty until #533 parses the BNY reports and derives the tape.
* Structural config — a registration is not licence to invent a capital structure.

The tests load the *real shipped* ``deals.json`` (via ``DEALS_DATA_FILE``), not a
fixture, so a regression in the data file is caught here.
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from loanwhiz.config import DEAL_REGISTRY, DEALS_DATA_FILE

CONTEGO_DEAL_ID = "contego-clo-xi"
CONTEGO_DEAL_NAME = "Contego CLO XI DAC"

#: Euronext Dublin's public document store — the same host Cairn's documents sit
#: on. Every Contego document is a plain, unauthenticated object here.
EURONEXT_DOC_HOST = (
    "https://ise-prodnr-eu-west-1-data-integration.s3-eu-west-1.amazonaws.com/"
)

#: The **29-Jun-2023** Listing Particulars (415pp) — the pre-reset document, and
#: the one this deal is registered against. Transcribed here from the exchange's
#: filing rather than read from the registry, so the assertion compares the data
#: file against a figure this test states rather than against itself.
PRE_RESET_LP_URL = EURONEXT_DOC_HOST + "202306/769c88dc-c38d-42af-b7d7-49cbe7c9bf4e.pdf"

#: The **19-Nov-2024** reset Listing Particulars (421pp). Named here **only** so
#: its absence from every registry slot can be asserted. This is the document a
#: future editor is most likely to mistake for an upgrade.
RESET_LP_URL = EURONEXT_DOC_HOST + "202411/2c3fcac2-3b84-43e3-8000-15092934c4fa.pdf"

#: The two BNY Mellon COMPLIANCE REPORTs (74pp each), registered under
#: ``investor_report_urls``. Both are pre-reset, which is what makes the 2023
#: Listing Particulars the right offering document for them.
EXPECTED_REPORT_PERIODS = ["August 2024", "September 2024"]

#: The 86pp NOTE VALUATION REPORT (payment date 20-Aug-2024) — obtainable, and
#: deliberately NOT registered until #534 can keep the promise its key makes.
WITHHELD_NVR_URL = EURONEXT_DOC_HOST + "202410/4457ecc3-3086-447d-b258-3e72f551ab2e.pdf"

#: Per-deal STRUCTURAL config keys (config.py). Sourcing a deal does not license
#: inventing its capital structure — these are derived from the documents.
STRUCTURAL_KEYS = (
    "capital_structure",
    "reserve_account_target",
    "original_pool_balance",
    "projection_base",
)

#: Every registry key whose value carries document URLs. The reset-document scan
#: below walks these rather than grepping the file's raw text, because the entry's
#: ``registration_note`` names the reset URL on purpose — documenting the trap is
#: the opposite of falling into it, and a text grep cannot tell the two apart.
_URL_BEARING_LIST_KEYS = ("tape_urls", "investor_report_urls", "notes_cash_report_urls")


def _shipped_data_file() -> dict[str, dict]:
    """The raw shipped ``deals.json`` object (deal_id -> context)."""
    return json.loads(DEALS_DATA_FILE.read_text(encoding="utf-8"))


def _every_registered_url() -> list[str]:
    """Every document URL any registered deal points at, across all slots."""
    urls: list[str] = []
    for ctx in DEAL_REGISTRY.values():
        prospectus = ctx.get("prospectus_url")
        if prospectus:
            urls.append(prospectus)
        for key in _URL_BEARING_LIST_KEYS:
            urls.extend(entry["url"] for entry in ctx.get(key) or [])
    return urls


# ---------------------------------------------------------------------------
# The deal is registered, as data, with the documents that exist.
# ---------------------------------------------------------------------------


def test_contego_resolves_from_the_shipped_data_file() -> None:
    """The registration lives in ``data/deals.json`` — data, not code."""
    assert CONTEGO_DEAL_ID in _shipped_data_file()
    assert CONTEGO_DEAL_ID in DEAL_REGISTRY
    assert DEAL_REGISTRY[CONTEGO_DEAL_ID]["deal_name"] == CONTEGO_DEAL_NAME


def test_contego_declares_its_jurisdiction_asset_class_and_vintage() -> None:
    """The deal says what it IS rather than leaving readers to infer it.

    ``vintage`` is carried explicitly because the id contains no year:
    ``breadth_harness._resolve_vintage`` falls back to a regex over the deal_id,
    which would silently resolve ``contego-clo-xi`` to ``None``.
    """
    deal = DEAL_REGISTRY[CONTEGO_DEAL_ID]
    assert deal["jurisdiction"] == "Ireland"
    assert deal["asset_class"] == "CLO"
    assert deal["vintage"] == 2023


def test_contego_is_registered_against_the_pre_reset_listing_particulars() -> None:
    """The 29-Jun-2023 document, not the 19-Nov-2024 reset.

    Both halves are asserted. The positive pins the document actually chosen;
    the negative is the one that matters, because the reset is the plausible
    wrong answer rather than an arbitrary one.
    """
    prospectus = DEAL_REGISTRY[CONTEGO_DEAL_ID]["prospectus_url"]
    assert prospectus == PRE_RESET_LP_URL
    assert prospectus != RESET_LP_URL
    assert prospectus.startswith(EURONEXT_DOC_HOST) and prospectus.endswith(".pdf")


def test_the_reset_listing_particulars_is_registered_in_no_document_slot() -> None:
    """The reset document appears in no URL slot of ANY registered deal.

    Scanned across the whole registry rather than Contego's entry alone: the
    failure this guards against is the reset being registered *somewhere* — as a
    second deal, or slipped into another deal's reports — not specifically as
    Contego's prospectus, which the test above already pins.

    The non-empty guards make the vacuous pass a failure. A scan that finds
    nothing because it looked at nothing reports exactly what a clean registry
    reports, and this checker's whole value is telling those two apart.
    """
    urls = _every_registered_url()
    assert urls, "the URL scan collected nothing — it cannot have checked anything"
    assert PRE_RESET_LP_URL in urls, "the scan does not reach Contego's own slots"
    assert RESET_LP_URL not in urls


def test_contego_registers_its_two_pre_reset_compliance_reports() -> None:
    """Both BNY Mellon COMPLIANCE REPORTs, in the standard ``{period, url}`` shape.

    Both are pre-reset, which is the property that makes them consistent with the
    2023 Listing Particulars. A report from the other side of the boundary landing
    here is the same silent-wrongness failure in a different slot.
    """
    entries = DEAL_REGISTRY[CONTEGO_DEAL_ID]["investor_report_urls"]
    assert [e["period"] for e in entries] == EXPECTED_REPORT_PERIODS
    for entry in entries:
        assert set(entry) >= {"period", "url"}
        assert entry["url"].startswith(EURONEXT_DOC_HOST)
        assert entry["url"].endswith(".pdf")


def test_contego_registry_entry_records_why_the_pre_reset_document_was_chosen() -> None:
    """The reason travels with the data, not only with the issue that decided it.

    A future editor meets ``prospectus_url`` in this file, not #532. The note is
    required to name the reset it rejected and the consequence of choosing it,
    because "we picked the older document" without a reason reads as an oversight
    to correct rather than a decision to keep.
    """
    note = DEAL_REGISTRY[CONTEGO_DEAL_ID]["registration_note"]
    assert "2c3fcac2" in note, "the note must name the reset document it rejected"
    assert "2023" in note and "reset" in note.lower()
    # The consequence, not just the choice: silence is what makes this trap
    # dangerous, so the note has to say so.
    assert "confidently wrong" in note


# ---------------------------------------------------------------------------
# Negatives — what is absent, asserted as hard as what is present.
# ---------------------------------------------------------------------------


def test_contego_withholds_notes_cash_report_urls_until_the_promise_can_be_kept() -> None:
    """The PoP-bearing document is obtainable and deliberately unregistered.

    ``notes_cash_report_urls`` is a routing promise (#455): the quality harness
    reads its presence as an assertion that a PoP-bearing answer key exists. Both
    halves are pinned — the key is unset, *and* the report it would point at is
    absent from every slot, so this cannot pass by the URL merely moving.
    """
    deal = DEAL_REGISTRY[CONTEGO_DEAL_ID]
    assert not deal.get("notes_cash_report_urls")
    assert WITHHELD_NVR_URL not in _every_registered_url()


def test_contego_registers_no_tape_yet() -> None:
    """No tape until #533 derives one from the BNY reports.

    The key is present but empty rather than absent: ``api.main`` subscripts
    ``deal["tape_urls"]`` directly, so omitting it would 500 the deal-model route
    rather than reading as "no tape".
    """
    deal = DEAL_REGISTRY[CONTEGO_DEAL_ID]
    assert "tape_urls" in deal
    assert deal["tape_urls"] == []


@pytest.mark.parametrize("key", STRUCTURAL_KEYS)
def test_contego_carries_no_invented_structural_config(key: str) -> None:
    """Registering a deal is not licence to invent its capital structure."""
    assert key not in DEAL_REGISTRY[CONTEGO_DEAL_ID]


def test_every_contego_document_url_is_distinct() -> None:
    """No URL doing two jobs across this deal's slots.

    Both halves matter: the count catches a document silently dropped, the set
    catches one URL copied into a second slot. Asserting only that a deduplicated
    list has no duplicates asserts nothing at all.
    """
    deal = DEAL_REGISTRY[CONTEGO_DEAL_ID]
    urls = [deal["prospectus_url"], *(e["url"] for e in deal["investor_report_urls"])]
    assert len(urls) == 3
    assert len(set(urls)) == 3


def test_contego_is_registered_but_not_modelable() -> None:
    """Registered != modelable: the engine degrades to a labelled 422.

    With no tape and no report the engine can fold, ``_reconstruct_series``
    raises rather than serving an empty cascade that would read as a real,
    all-clear result. Offline — no network fetch is attempted.
    """
    from loanwhiz.api.main import _reconstruct_series

    with pytest.raises(HTTPException) as exc:
        _reconstruct_series(CONTEGO_DEAL_ID, DEAL_REGISTRY[CONTEGO_DEAL_ID])
    assert exc.value.status_code == 422
    assert CONTEGO_DEAL_ID in str(exc.value.detail)
