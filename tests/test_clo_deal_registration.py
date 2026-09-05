"""Tests for the CLO deal registered in ``data/deals.json`` (#455, epic #454).

**Cairn CLO XVII DAC** is the registry's first NON-RMBS deal — an Irish
collateralised loan obligation, listed on Euronext Dublin's Global Exchange
Market, trustee U.S. Bank Global Corporate Trust, Class A ISIN ``XS2650750537``
(page 395 of the 420-page Listing Particulars). Like every deal since #207 it is
registered as *data*, not code, via ``src/loanwhiz/data/deals.json``, which
``loanwhiz.config._load_deal_registry`` merges into ``DEAL_REGISTRY`` at import.

**This is registration only — no extraction (#456) and no engine wiring (#457).**
The deal therefore has no committed seed model, no answer key and no validation
builder, and these tests exist to pin exactly that: what is *absent* is asserted
as hard as what is present, so a later change that quietly fabricates a green
cell for this deal reds here.

What the sourcing established, and what these tests pin
-------------------------------------------------------
* **Obtainable, unauthenticated** (verified ``200 application/pdf``, no redirect,
  no cookies): the 420pp Listing Particulars, three 74pp U.S. Bank monthly
  trustee reports (as-of 16/12/2024, 18/02/2025, 18/03/2025) and the 83pp Note
  Valuation Report (as-of 08/01/2025).
* **The Note Valuation Report is the CLO analogue of an RMBS Notes & Cash
  report** — it is the only one of the five documents carrying both an *Interest
  Priority of Payments* and a *Principal Priority of Payments*. It is nevertheless
  **deliberately NOT registered under** ``notes_cash_report_urls`` yet: that key is
  a *routing promise*, not a URL slot. ``_reconstruct_series`` dispatches on it and
  ``test_quality_harness.test_answer_keys_exist_exactly_where_published_reports_do``
  treats its presence as an assertion that a committed answer key exists. Setting
  it for a deal with nothing extracted, no parser for the CLO report format and no
  key would assert a promise this deal cannot keep. The key is earned at extraction
  (#456), not at sourcing. The NVR's URL is recorded in ``docs/data-card.md`` so
  nothing has to be re-sourced, and ``NVR_URL_NOT_YET_REGISTERED`` below keeps the
  omission deliberate and greppable rather than an oversight.
* **No machine-readable loan tape exists**, so ``tape_urls`` is empty by design.
  Loan-level collateral detail *is* published — as PDF tables inside the trustee
  reports — but that is not an ESMA Annex tape and the normaliser cannot read it.

The tests load the *real shipped* ``deals.json`` (via ``DEALS_DATA_FILE``), not a
fixture, so a regression in the data file is caught here.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from loanwhiz.config import (
    ANSWER_KEY_DATA_DIR,
    DEAL_REGISTRY,
    DEALS_DATA_FILE,
    GREEN_LION,
    _load_deal_registry,
)

CLO_DEAL_ID = "cairn-clo-xvii"
CLO_DEAL_NAME = "Cairn CLO XVII DAC"

#: Euronext Dublin's public document store. Every Cairn document is a plain,
#: unauthenticated object here — no investor portal, no login.
EURONEXT_DOC_HOST = (
    "https://ise-prodnr-eu-west-1-data-integration.s3-eu-west-1.amazonaws.com/"
)

#: The trustee-report periods registered under ``investor_report_urls``. Euronext's
#: listing for this issuer carries exactly these three monthly reports — January
#: 2025 is absent from the exchange's filing, so the series is deliberately NOT
#: contiguous and nothing is interpolated to make it look complete.
EXPECTED_REPORT_PERIODS = ["December 2024", "February 2025", "March 2025"]

#: The Note Valuation Report (as-of 08/01/2025, 83pp) — sourced and verified
#: obtainable, but **deliberately not registered** (see the module docstring).
#: Held here so the finding is machine-visible and #456 need not re-source it.
NVR_URL_NOT_YET_REGISTERED = (
    EURONEXT_DOC_HOST + "202502/12423666-a060-4e34-b3e8-f5510297ac6f.pdf"
)

#: The four RMBS deals that predate this one. The CLO must not become the
#: registry's special case: every one of them carries ``asset_class`` too.
PRE_EXISTING_RMBS_DEAL_IDS = [
    "green-lion-2023-1",
    "green-lion-2024-1",
    "leone-arancio-2023-1",
    "sol-lion-ii",
]

#: Per-deal STRUCTURAL config keys (config.py). Sourcing a deal does not license
#: inventing its capital structure — #456/#457 derive these from the documents.
STRUCTURAL_KEYS = (
    "capital_structure",
    "reserve_account_target",
    "original_pool_balance",
    "projection_base",
)


def _shipped_data_file() -> dict[str, dict]:
    """The raw shipped ``deals.json`` object (deal_id -> context)."""
    return json.loads(DEALS_DATA_FILE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Positives — the deal is registered, as data, with the documents that exist.
# ---------------------------------------------------------------------------


def test_clo_resolves_from_live_registry() -> None:
    assert CLO_DEAL_ID in DEAL_REGISTRY, f"{CLO_DEAL_ID} missing from DEAL_REGISTRY"
    assert DEAL_REGISTRY[CLO_DEAL_ID]["deal_name"] == CLO_DEAL_NAME
    # The in-code Green Lion 2026-1 default is never displaced by the new entry.
    assert "green-lion-2026-1" in DEAL_REGISTRY


def test_clo_resolves_from_shipped_data_file() -> None:
    # Loading the real data file directly yields the CLO — proving the
    # registration lives in ``data/deals.json`` (data, not code).
    assert CLO_DEAL_ID in _load_deal_registry(DEALS_DATA_FILE)


def test_clo_registration_required_no_config_code_change() -> None:
    """The CLO exists *only* in the data file — nothing was added to config.py.

    The in-code default registry is the single ``GREEN_LION`` entry; if a future
    change smuggles a CLO-shaped special case into ``config.py`` this reds.

    Both file arguments are pinned at non-existent paths on purpose: the runtime
    overlay (``data/deals.runtime.json``) is gitignored, so leaving it defaulted
    would let a stray local file decide this negative's result.
    """
    bare = _load_deal_registry(
        Path("/nonexistent-deals.json"), Path("/nonexistent-runtime.json")
    )
    assert CLO_DEAL_ID not in bare
    assert set(bare) == {"green-lion-2026-1"}
    assert GREEN_LION["deal_name"] != CLO_DEAL_NAME


def test_clo_is_irish_and_declares_its_asset_class() -> None:
    deal = DEAL_REGISTRY[CLO_DEAL_ID]
    assert deal["jurisdiction"] == "Ireland"
    # The deal says what it IS, rather than the registry inferring non-RMBS from
    # the absence of RMBS-shaped keys.
    assert deal["asset_class"] == "CLO"


def test_clo_prospectus_is_a_euronext_pdf() -> None:
    prospectus = DEAL_REGISTRY[CLO_DEAL_ID]["prospectus_url"]
    assert prospectus.startswith(EURONEXT_DOC_HOST)
    assert prospectus.endswith(".pdf")
    # Not an ING-portal or HuggingFace document like every prior deal's.
    assert "ing.com" not in prospectus
    assert "huggingface" not in prospectus.lower()


def test_clo_trustee_reports_registered_as_investor_reports() -> None:
    """The three monthly trustee reports use the standard ``{period, url}`` shape."""
    entries = DEAL_REGISTRY[CLO_DEAL_ID]["investor_report_urls"]
    assert [e["period"] for e in entries] == EXPECTED_REPORT_PERIODS
    for entry in entries:
        assert set(entry) >= {"period", "url"}
        assert entry["url"].startswith(EURONEXT_DOC_HOST)
        assert entry["url"].endswith(".pdf")


def test_clo_note_valuation_report_is_obtainable_but_not_yet_registered() -> None:
    """A **not-yet**, not a never — and the distinction is the whole finding.

    Leone Arancio and Sol-Lion II carry no ``notes_cash_report_urls`` because no
    such report is published *at all*. Cairn carries none for the opposite reason:
    its Note Valuation Report **is** published, free and unauthenticated, and
    carries both an Interest and a Principal Priority of Payments — it simply has
    not been extracted, parsed or graded, so claiming the key would assert a
    routing promise the deal cannot keep (see the module docstring). Flattening
    those two absences into "another deal with no report" would understate what
    was found, which is as dishonest as overstating it.
    """
    assert "notes_cash_report_urls" not in DEAL_REGISTRY[CLO_DEAL_ID]
    # The document exists and its location is known — recorded, not registered.
    assert NVR_URL_NOT_YET_REGISTERED.startswith(EURONEXT_DOC_HOST)
    assert NVR_URL_NOT_YET_REGISTERED.endswith(".pdf")
    registered = {
        DEAL_REGISTRY[CLO_DEAL_ID]["prospectus_url"],
        *(e["url"] for e in DEAL_REGISTRY[CLO_DEAL_ID]["investor_report_urls"]),
    }
    assert NVR_URL_NOT_YET_REGISTERED not in registered


def test_every_registered_document_url_is_distinct() -> None:
    """Four distinct registered documents — no URL copy-pasted across two slots."""
    deal = DEAL_REGISTRY[CLO_DEAL_ID]
    urls = [deal["prospectus_url"], *(e["url"] for e in deal["investor_report_urls"])]
    assert len(urls) == 4
    assert len(set(urls)) == 4


def test_the_clo_is_not_the_registry_special_case() -> None:
    """``asset_class`` is on EVERY shipped entry, not bolted onto the CLO alone.

    The point of the key is the seam, not the exception: the four RMBS deals
    declare their asset class as explicitly as the CLO declares its own, so a
    reader (#457) can dispatch on the key rather than on "is this Cairn?".
    """
    shipped = _shipped_data_file()
    assert all("asset_class" in ctx for ctx in shipped.values())
    for deal_id in PRE_EXISTING_RMBS_DEAL_IDS:
        assert shipped[deal_id]["asset_class"] == "RMBS"
    assert shipped[CLO_DEAL_ID]["asset_class"] == "CLO"


def test_asset_class_is_optional_like_jurisdiction_on_the_in_code_default() -> None:
    """``asset_class`` is an ADDITIVE, optional key — exactly like ``jurisdiction``.

    The in-code Green Lion 2026-1 default carries neither, because adding one
    would be a ``config.py`` code change and registering a deal must stay data.
    A reader resolves a default for the absent key, the way
    ``capability_matrix._resolve_jurisdiction`` already does for jurisdiction.
    Pinned so the gap is visible rather than silently assumed away.
    """
    assert "jurisdiction" not in GREEN_LION
    assert "asset_class" not in GREEN_LION


# ---------------------------------------------------------------------------
# Negatives — what is absent, asserted as hard as what is present.
# ---------------------------------------------------------------------------


def test_clo_has_no_loan_tape() -> None:
    """No machine-readable ESMA loan tape is published for this deal.

    Loan-level collateral detail IS obtainable — as PDF tables inside the monthly
    trustee reports — but the ESMA tape normaliser cannot read those, so claiming
    a tape here would be a lie the pool analytics would then act on.
    """
    assert DEAL_REGISTRY[CLO_DEAL_ID]["tape_urls"] == []


@pytest.mark.parametrize("key", STRUCTURAL_KEYS)
def test_clo_carries_no_invented_structural_config(key: str) -> None:
    """Sourcing a deal is not licence to invent its capital structure.

    These keys are derived from the documents by #456/#457. Absent them the
    engine fails loudly (see the not-modelable test below) rather than borrowing
    another deal's numbers.
    """
    assert key not in DEAL_REGISTRY[CLO_DEAL_ID]


def test_clo_seed_model_carries_the_whole_capital_stack() -> None:
    """#456 ran the extraction, so this deal now HAS a model — and all of it.

    #455 asserted the negative here ("no extraction has run"). That is no longer
    true, and the replacement is not merely the inverse: the failure this deal
    actually produced was a model that existed and looked well-formed while
    silently missing three of eight tranches, so the assertion is on the whole
    stack rather than on the model's existence.

    Sizes are the cover page's, senior to junior, and they sum to the stated
    EUR 404.1m. A partial parse still totals a plausible number, so the sum is
    checked against the document rather than against itself.
    """
    from loanwhiz.api.main import _load_cached_deal_model

    model = _load_cached_deal_model(DEAL_REGISTRY[CLO_DEAL_ID])
    assert model is not None
    tranches = model.tranche_structure
    assert [t["name"] for t in tranches] == [
        "Class A", "Class B-1", "Class B-2", "Class C",
        "Class D", "Class E", "Class F", "Subordinated Notes",
    ]
    assert sum(t["size_eur"] for t in tranches) == 404_100_000.0
    seniorities = [t["seniority"] for t in tranches]
    assert all(a < b for a, b in zip(seniorities, seniorities[1:])), seniorities
    # The first-loss tranche is unrated and pays no stated coupon. Recording
    # that honestly is the point: the row's "N/A" cells previously yielded a
    # fabricated "A" rating and the issue price as a coupon.
    residual = tranches[-1]
    assert residual["rating"] is None and residual["rate"] is None


def test_clo_seed_model_carries_both_priorities_of_payments() -> None:
    """Interest and Principal are DIFFERENT cascades, from different sections.

    A CLO names them "Application of Interest Proceeds" and "Application of
    Principal Proceeds"; the canonical schema calls the roles revenue and
    redemption. The failure mode worth pinning is not absence but COLLAPSE —
    both roles resolving to one section, which is what happened on the Italian
    deal and what the router prompt explicitly permits when a single cascade
    serves both. Asserting distinct sources is what tells them apart.
    """
    from loanwhiz.api.main import _load_cached_deal_model

    model = _load_cached_deal_model(DEAL_REGISTRY[CLO_DEAL_ID])
    assert model is not None
    revenue = model.waterfalls["revenue"]
    redemption = model.waterfalls["redemption"]
    assert revenue["source_section"] != redemption["source_section"]
    assert "interest" in revenue["source_section"].lower()
    assert "principal" in redemption["source_section"].lower()
    assert len(revenue["steps"]) > 20
    assert len(redemption["steps"]) > 20


def test_clo_coverage_tests_land_on_real_coverage_metrics() -> None:
    """Every extracted coverage test resolves to an OC/IC metric or says why.

    This is the deliverable #456 exists for: the coverage-test definitions
    reaching the per-attachment-point metrics #452 added. The senior-most test
    is the combined "Class A/B Par Value Test", whose ratio the document defines
    over Class A + Class B outstanding — a Class B attachment point.

    A trigger that resolves to `unmapped` is allowed and expected (the
    reinvestment test names no attachment point; the incentive fee is
    deliberately excluded), but it must keep its prose. What is NOT allowed is a
    coverage metric carrying a threshold it can never breach.
    """
    from loanwhiz.api.main import _load_cached_deal_model
    from loanwhiz.domain.rules import MetricType
    from loanwhiz.extraction.assembler import _trigger_rules_from_covenants
    from loanwhiz.extraction.taxonomy import is_coverage_metric

    model = _load_cached_deal_model(DEAL_REGISTRY[CLO_DEAL_ID])
    assert model is not None
    rules = _trigger_rules_from_covenants(
        model.covenants, deal_name="Cairn", provenance={}, use_llm=False
    )
    resolved = {r.metric for r in rules if r.metric != MetricType.unmapped}
    # Both families, at several attachment points, including the senior block.
    assert MetricType.class_b_oc_ratio in resolved
    assert MetricType.class_b_ic_ratio in resolved
    assert MetricType.class_f_oc_ratio in resolved
    # No coverage test may carry a threshold that can never be breached: a
    # non-positive level reads as permanently satisfied and never fires.
    for rule in rules:
        if is_coverage_metric(rule.metric) and rule.threshold is not None:
            assert rule.threshold > 0, rule.name


def test_clo_has_no_committed_answer_key() -> None:
    """No ground truth is authored for the CLO.

    The Note Valuation Report publishes both Priorities of Payments, so an answer
    key is *feasible* here in a way it never was for the Italian and Spanish
    deals — but feasible is not authored, and inventing one would poison every
    grading claim downstream.

    Resolved through the real ``load_answer_key`` rather than by testing one
    hardcoded filename: a key committed under any other slug would slip past a
    filename check, making the negative pass for the wrong reason.
    """
    from loanwhiz.primitives.reconciliation_answer_key import load_answer_key

    assert load_answer_key(DEAL_REGISTRY[CLO_DEAL_ID]) is None
    # And no committed key file names this deal under any slug.
    assert not list(ANSWER_KEY_DATA_DIR.glob("*cairn*"))


def test_clo_has_no_validation_builder() -> None:
    """No committed engine-validation builder ⇒ the CLO cannot reach ``validated``."""
    from loanwhiz.api.main import _VALIDATION_BUILDERS

    assert CLO_DEAL_ID not in _VALIDATION_BUILDERS


def test_clo_is_registered_but_not_modelable() -> None:
    """Registered ≠ modelable: the engine degrades to a labelled 422.

    The CLO has neither a loan tape nor a report the engine can fold, so
    ``_reconstruct_series`` raises rather than serving an empty cascade that would
    read as a real, all-clear result. Offline — no network fetch is attempted.
    """
    from loanwhiz.api.main import _reconstruct_series

    with pytest.raises(HTTPException) as exc:
        _reconstruct_series(CLO_DEAL_ID, DEAL_REGISTRY[CLO_DEAL_ID])
    assert exc.value.status_code == 422
    assert CLO_DEAL_ID in str(exc.value.detail)


# ---------------------------------------------------------------------------
# Capability matrix — registering a deal must not fabricate a green cell.
# ---------------------------------------------------------------------------


def _live_matrix():
    from loanwhiz.api.main import _load_cached_deal_model, _VALIDATION_BUILDERS
    from loanwhiz.primitives.capability_matrix import build_capability_matrix

    return build_capability_matrix(
        deals=DEAL_REGISTRY,
        seed_loader=_load_cached_deal_model,
        validators=_VALIDATION_BUILDERS,
    )


def test_every_clo_capability_cell_is_ran_or_reasoned_not_applicable() -> None:
    """Extraction moved two cells to `ran`; the rest stay refused WITH a reason.

    #455 asserted every cell was not-applicable, which was true while nothing
    had been extracted. Now that #456 has run, the honest shape is two states
    and no third: a cell either ran, or is not-applicable and says why. The
    reason is the load-bearing half — a cell that is merely absent of a claim is
    indistinguishable from one nobody assessed.

    Deliberately NOT asserted here: which cells are `ran`. That is a property of
    what the extraction produced, and pinning the pair by name would make this
    test a second, weaker copy of the assertions above.
    """
    from loanwhiz.primitives.capability_matrix import (
        STATE_NOT_APPLICABLE,
        STATE_RAN,
    )

    cells = [c for c in _live_matrix().cells if c.deal_id == CLO_DEAL_ID]
    assert cells, "CLO produced no capability cells"
    for cell in cells:
        assert cell.state in (STATE_RAN, STATE_NOT_APPLICABLE), (
            f"{cell.capability_key} is {cell.state}"
        )
        if cell.state == STATE_NOT_APPLICABLE:
            assert cell.reason, f"{cell.capability_key} has no reason"


def test_registering_the_clo_added_no_validated_cell() -> None:
    """The single ``validated`` cell stays Green Lion 2024-1's, and only its."""
    from loanwhiz.primitives.capability_matrix import STATE_VALIDATED

    matrix = _live_matrix()
    validated = [c for c in matrix.cells if c.state == STATE_VALIDATED]
    assert [c.deal_id for c in validated] == ["green-lion-2024-1"]
    assert matrix.tally[STATE_VALIDATED] == 1


def test_matrix_covers_every_registered_deal() -> None:
    """One column per registry deal, one cell per (capability × deal) pair —
    so a newly registered deal is never silently omitted from the honest tally."""
    matrix = _live_matrix()
    assert {d.deal_id for d in matrix.deals} == set(DEAL_REGISTRY)
    assert len(matrix.cells) == len(matrix.capabilities) * len(DEAL_REGISTRY)
    assert sum(matrix.tally.values()) == len(matrix.cells)
