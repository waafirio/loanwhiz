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
* ``tape_urls`` — one ``derived+trustee-report:`` URI per period since #555
  parsed the BNY reports; empty on registration, because #532 registered the
  deal before the reports could be read.
* Structural config — a registration is not licence to invent a capital structure.

The tests load the *real shipped* ``deals.json`` (via ``DEALS_DATA_FILE``), not a
fixture, so a regression in the data file is caught here.
"""

from __future__ import annotations

import json
from unittest import mock

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


def test_contego_registers_its_derived_tape_over_the_existing_channel() -> None:
    """#532 registered the deal with an empty tape; #555 filled it.

    This test pinned ``tape_urls == []`` while the BNY column work was
    outstanding — the key present but empty, because ``api.main`` subscripts it
    directly and omitting it would 500 the deal-model route rather than reading
    as "no tape". Now that the reports parse, the same slot carries one derived
    URI per period, and what is worth pinning is that they went through the
    **existing** channel: #471's ``derived+trustee-report:`` scheme, resolved at
    read time, with no second scheme invented for a second administrator.
    """
    deal = DEAL_REGISTRY[CONTEGO_DEAL_ID]

    assert "tape_urls" in deal
    tapes = deal["tape_urls"]
    assert [entry["date"] for entry in tapes] == ["2024-08-30", "2024-09-30"]
    assert all(
        entry["url"].startswith("derived+trustee-report:") for entry in tapes
    ), "a second family must not bring a second provenance scheme"


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

    ``_reconstruct_series`` raises rather than serving an empty cascade that
    would read as a real, all-clear result. Offline — no network fetch is
    attempted.

    **Which refusal, not just that one fired (#535).** When this test was
    written the deal had neither tape nor report, so the 422 came from
    ``_not_modelable_deal``. #555 registered its derived tapes, so the deal now
    takes the *tape* path and gets as far as ``_resolve_structural_config``
    before stopping — a different refusal, ``_misconfigured_deal``, on a senior
    coupon its extracted seed never carried. A bare ``422`` + deal-id assertion
    is satisfied by both and so cannot tell them apart; it would have gone on
    passing through that change without recording it. The key is named here so
    that sourcing the coupon reds this line instead of sliding past it.
    """
    from loanwhiz.api.main import _reconstruct_series

    with pytest.raises(HTTPException) as exc:
        _reconstruct_series(CONTEGO_DEAL_ID, DEAL_REGISTRY[CONTEGO_DEAL_ID])
    assert exc.value.status_code == 422
    detail = str(exc.value.detail)
    assert CONTEGO_DEAL_ID in detail
    assert "class_a_rate_pct" in detail, (
        f"the refusal no longer names the missing senior coupon: {detail}"
    )


# ---------------------------------------------------------------------------
# The committed seed — extracted from the pre-reset document, and honest about
# what the extraction did not get.
# ---------------------------------------------------------------------------


def _seed():
    from loanwhiz.api.main import _load_cached_deal_model

    return _load_cached_deal_model(DEAL_REGISTRY[CONTEGO_DEAL_ID])


def test_contego_seed_carries_the_pre_reset_capital_stack() -> None:
    """The seed is the **2023** stack, and the sizes are the document's.

    This is the reset guard at the seed layer rather than the registry layer,
    and it is the one that would actually catch a bad re-extraction: swapping
    `prospectus_url` to the reset and re-running would produce a well-formed
    eight-class model whose Class A is EUR 310m of `Class A-R` due 2038. Every
    downstream figure would compute. So the sizes are checked against the cover
    page — EUR 380.6m across eight tranches, transcribed from the document, not
    read back from the seed — and the reset's headline figure is excluded by
    name. A partial parse still totals a plausible number, which is why the sum
    is pinned rather than the tranche count alone.
    """
    model = _seed()
    assert model is not None
    tranches = model.tranche_structure
    assert [t["name"] for t in tranches] == [
        "Class A", "Class B-1", "Class B-2", "Class C",
        "Class D", "Class E", "Class F", "Subordinated Notes",
    ]
    assert sum(t["size_eur"] for t in tranches) == 380_600_000.0
    senior = tranches[0]
    assert senior["size_eur"] == 228_700_000.0
    # The reset's Class A-R. Its absence is the point of the whole registration.
    assert senior["size_eur"] != 310_000_000.0
    assert "-R" not in senior["name"]
    seniorities = [t["seniority"] for t in tranches]
    assert all(a < b for a, b in zip(seniorities, seniorities[1:])), seniorities


def test_contego_seed_keeps_its_two_cascades_distinct() -> None:
    """Interest and Principal are different cascades from different sections.

    The failure mode worth pinning is not absence but COLLAPSE — both roles
    resolving to one section, which is what happened on the Italian deal. This
    deal is a live trap for reading collapse into a coincidence: both cascades
    extracted to the *same number* of steps (37), so a reviewer glancing at the
    counts could conclude they are one cascade twice. They are not, and the
    source sections are what tell them apart.
    """
    model = _seed()
    assert model is not None
    revenue = model.waterfalls["revenue"]
    redemption = model.waterfalls["redemption"]
    assert revenue["source_section"] != redemption["source_section"]
    assert "revenue" in revenue["source_section"].lower()
    assert "redemption" in redemption["source_section"].lower()
    assert revenue["steps"] != redemption["steps"]
    assert len(revenue["steps"]) > 20 and len(redemption["steps"]) > 20


def test_contego_seed_understates_the_glossary_and_the_card_says_why() -> None:
    """The extraction captured a handful of defined terms out of hundreds.

    Contego's Condition 1 (Definitions) reaches the definitions extractor as a
    **3,255-character** fragment, so only terms in the leading alphabetical
    range survive. That is *not* the 40k `max_chars` truncation that cost Cairn
    its Payment Date schedule (#528): 3,255 is comfortably inside a 40,000
    budget, so that guard never engaged here. Docling renders many of this
    document's defined terms as markdown headings, and `route_sections` ends a
    section at the next heading — so the glossary body became sibling sections
    the extractor was never handed, `' Payment Date ' means:` among them.

    Pinned as a **recorded limitation**, per #480: the sentence naming it must
    name the document it is true of. The assertion is a bound rather than an
    exact count so a better router is free to improve it — but it reds if the
    shortfall is silently closed or silently worsened, and it reds if the data
    card stops explaining it, which is what stops this becoming folklore.
    """
    model = _seed()
    assert model is not None
    # Far below a CLO glossary's real size — the recorded gap, not a baseline.
    assert 0 < len(model.definitions) < 25
    card = (DEALS_DATA_FILE.parents[3] / "docs" / "data-card.md").read_text(
        encoding="utf-8"
    )
    assert "route_sections" in card, "the card must name the mechanism, not just the gap"
    assert "3,255" in card, "the card must state the fragment size it is true of"


def test_contego_seed_states_no_payment_schedule() -> None:
    """No stated schedule is parsed for this deal, so the seed says so.

    ``tests/test_payment_schedule_parser.py`` sweeps every committed seed except
    Cairn's and requires exactly this, so an accidental non-null here would red
    a file three directories away. Asserted at the source too, because that
    sweep's failure message would not name Contego as the cause.
    """
    model = _seed()
    assert model is not None
    assert model.payment_schedule is None
    assert model.note_day_counts is None


def test_deal_model_route_serves_contego_from_its_committed_seed() -> None:
    """The registry entry survives the real route, not just a dict lookup.

    This is the integration half of the ``tape_urls`` finding. ``deal_model``
    reads ``deal["tape_urls"]`` and ``deal["investor_report_urls"]`` by direct
    subscript, so a registry entry that omitted either would raise ``KeyError``
    and 500 — and no assertion over ``DEAL_REGISTRY`` alone would catch it,
    because the dict is well-formed either way. Only a request through the app
    exercises the subscript.

    It also pins that the committed seed is what the route serves on a cold
    runtime cache (the seed-dir fallback), and that what it serves is the
    **pre-reset** stack.
    """
    from fastapi.testclient import TestClient

    from loanwhiz.api.main import app

    client = TestClient(app)
    with mock.patch("loanwhiz.api.main.DEAL_MODEL_CACHE_DIR", "/nonexistent-cache"):
        resp = client.get(f"/deal/{CONTEGO_DEAL_ID}/model")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deal_name"] == CONTEGO_DEAL_NAME
    assert body["prospectus_url"] == PRE_RESET_LP_URL
    assert [entry["date"] for entry in body["tape_urls"]] == ["2024-08-30", "2024-09-30"]
    assert [e["period"] for e in body["investor_report_urls"]] == EXPECTED_REPORT_PERIODS
    # Served from the committed seed, and it is the 2023 stack.
    assert body["deal_model"] is not None
    tranches = body["deal_model"]["tranche_structure"]
    assert sum(t["size_eur"] for t in tranches) == 380_600_000.0
