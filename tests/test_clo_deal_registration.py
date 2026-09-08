"""Tests for the CLO deal registered in ``data/deals.json`` (#455, epic #454).

**Cairn CLO XVII DAC** is the registry's first NON-RMBS deal — an Irish
collateralised loan obligation, listed on Euronext Dublin's Global Exchange
Market, trustee U.S. Bank Global Corporate Trust, Class A ISIN ``XS2650750537``
(page 395 of the 420-page Listing Particulars). Like every deal since #207 it is
registered as *data*, not code, via ``src/loanwhiz/data/deals.json``, which
``loanwhiz.config._load_deal_registry`` merges into ``DEAL_REGISTRY`` at import.

**This began as registration only — no extraction (#456) and no engine wiring
(#457)** — and these tests exist to pin what is *absent* as hard as what is
present, so a later change that quietly fabricates a green cell for this deal
reds here. Extraction (#456), an answer key authored from the trustee reports'
published coverage-test results (#481) and — since #495 — a Priority-of-Payments
section authored from the Note Valuation Report have since landed, so some
assertions here are now positive; the standard did not move, the evidence did.
What is still absent and still pinned: no validation builder, no offline engine
series to reconcile that PoP against, and no structural config invented for the
deal.

What the sourcing established, and what these tests pin
-------------------------------------------------------
* **Obtainable, unauthenticated** (verified ``200 application/pdf``, no redirect,
  no cookies): the 420pp Listing Particulars, three 74pp U.S. Bank monthly
  trustee reports (as-of 16/12/2024, 18/02/2025, 18/03/2025) and the 83pp Note
  Valuation Report (as-of 08/01/2025).
* **The Note Valuation Report is the CLO analogue of an RMBS Notes & Cash
  report** — it is the only one of the five documents carrying both an *Interest
  Priority of Payments* and a *Principal Priority of Payments*. It was
  **deliberately NOT registered under** ``notes_cash_report_urls`` until #495,
  because that key is a *routing promise*, not a URL slot: ``_reconstruct_series``
  dispatches on it and
  ``test_quality_harness.test_answer_keys_exist_exactly_where_published_ground_truth_does``
  treats its presence as an assertion that a **PoP-bearing** answer key exists.
  #481 committed a key by the *other* route — published coverage-test results, no
  Priority of Payments — which is why that invariant distinguishes the two. #494
  then parsed the report and #495 committed the PoP section, so the promise can
  now be kept and the URL is registered. What the registration does **not** claim
  is asserted just as hard: the deal still has no offline engine series, so
  nothing has yet reconciled an engine cascade against that ground truth.
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
from loanwhiz.primitives.derived_tape import (
    is_derived_uri,
    source_document_for,
    source_kind_for,
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

#: The Note Valuation Report (as-of 08/01/2025, 83pp), registered under
#: ``notes_cash_report_urls`` since #495. Transcribed here independently of the
#: registry so the assertion below compares the data file against a figure this
#: test states, rather than against itself.
NVR_URL = EURONEXT_DOC_HOST + "202502/12423666-a060-4e34-b3e8-f5510297ac6f.pdf"

#: The period the Note Valuation Report covers. It is exactly the month the
#: trustee-report series is missing, so the deal's two document sets cover
#: disjoint reporting dates and nothing has to be reconciled across them.
NVR_PERIOD = "January 2025"

#: The Note Valuation Report's own ``As of: 08/01/2025``, transcribed here from
#: the document rather than read from the parse. It is the genuinely ambiguous
#: shape — ``08/01/2025`` is a valid date read either way round — so the key's
#: reporting date for this period is worth checking against a figure stated by
#: hand, exactly as the trustee periods are checked against the tape dates.
NVR_AS_OF = "2025-01-08"

#: Every period the committed answer key carries, in reporting-date order — the
#: three trustee months plus the Note Valuation Report's January. Which document
#: a period came from decides what it may claim, which is what the assertions
#: below pin: a covenant period states no Priority of Payments and a PoP period
#: states no coverage test, because neither document states the other's figures.
EXPECTED_KEY_PERIODS = [
    "December 2024",
    "January 2025",
    "February 2025",
    "March 2025",
]

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


def test_clo_note_valuation_report_is_registered_now_that_it_can_be_kept() -> None:
    """The not-yet became a yes, and the distinction it drew survives it.

    The registry key was withheld until the promise could be kept: the report had
    to be parsed (#494) and a PoP-bearing key committed (#495) before setting it
    stopped being a claim this deal could not honour. Both halves are asserted —
    the key is set, *and* the answer key it promises genuinely carries a Priority
    of Payments, so this can never pass on the URL alone.

    The finding it used to pin has not gone away, it has moved: Leone Arancio and
    Sol-Lion II still carry no ``notes_cash_report_urls`` because no such report is
    published *at all*, which was never the same absence as Cairn's. Asserting
    that here keeps "published but unread" and "not published" from flattening
    into one state now that Cairn has left the first.
    """
    from loanwhiz.primitives.reconciliation_answer_key import load_answer_key

    entries = DEAL_REGISTRY[CLO_DEAL_ID]["notes_cash_report_urls"]
    assert [e["period"] for e in entries] == [NVR_PERIOD]
    assert [e["url"] for e in entries] == [NVR_URL]

    # The promise the key makes, kept: a PoP-bearing answer key really exists.
    key = load_answer_key(DEAL_REGISTRY[CLO_DEAL_ID])
    assert key is not None
    assert any(p.revenue_pop or p.redemption_pop for p in key.periods)

    # And the opposite absence still reads as itself, not as Cairn's old one.
    for deal_id in ("leone-arancio-2023-1", "sol-lion-ii"):
        assert not DEAL_REGISTRY[deal_id].get("notes_cash_report_urls")
        assert load_answer_key(DEAL_REGISTRY[deal_id]) is None


def test_every_registered_document_url_is_distinct() -> None:
    """Every registered document is a distinct one — no URL copied across slots.

    The Note Valuation Report joined this set in #495, and it is the slot where a
    copy-paste would do most damage: pointing ``notes_cash_report_urls`` at a
    trustee report would route the PoP reader at a document that states no
    Priority of Payments.
    """
    deal = DEAL_REGISTRY[CLO_DEAL_ID]
    urls = [
        deal["prospectus_url"],
        *(e["url"] for e in deal["investor_report_urls"]),
        *(e["url"] for e in deal["notes_cash_report_urls"]),
    ]
    # Both halves matter: the count catches a document silently dropped from the
    # registry, the set catches one URL doing two jobs. Asserting only that a
    # deduplicated list has no duplicates asserts nothing at all.
    assert len(urls) == 5
    assert len(set(urls)) == 5
    assert NVR_URL not in {
        deal["prospectus_url"],
        *(e["url"] for e in deal["investor_report_urls"]),
    }


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


def test_clo_registers_only_derived_tapes() -> None:
    """The CLO's tapes are reconstructed, and every one of them says so.

    No machine-readable ESMA loan tape is *published* for this deal — that part
    of the original finding is unchanged, and Cairn's own Article 7(1)(a) Loan
    Reports remain out of reach. What changed is that the loan-level collateral
    detail inside the monthly trustee reports is now parsed, reconciled against
    each report's own stated aggregates and resolved onto canonical Annex 4
    columns, so a tape can be registered without claiming to be a filing.

    Pinned per #455: register the document only when the promise can be kept,
    and pin the shape of what was registered rather than only its presence. An
    entry here that carried a plain URL would be indistinguishable from a
    published tape at every downstream reader, which is the failure this asserts
    against.
    """
    tapes = DEAL_REGISTRY[CLO_DEAL_ID]["tape_urls"]
    assert len(tapes) == 3, "one per registered trustee report"
    for entry in tapes:
        assert is_derived_uri(entry["url"]), entry
        kind = source_kind_for(entry["url"])
        # Asserted by value + predicate rather than by importing the enum:
        # ``loanwhiz.domain.tape_provenance`` cannot be the first ``loanwhiz``
        # import in a module (a pre-existing package-init cycle through
        # ``domain.provenance``), and the serialised value is the durable
        # contract anyway — it is what a registry entry and an evidence pack
        # both carry.
        assert kind is not None and kind.value == "derived_from_investor_report"
        assert not kind.is_regulatory_filing


def test_each_derived_tape_names_a_report_the_deal_actually_registers() -> None:
    """A derived tape's source must be a document this deal registers, not a new one.

    The whole reason the derivation is reproducible is that its input is already
    a registry fact. A source URL drifting away from ``investor_report_urls``
    would quietly make the tape depend on a document nothing else in the deal
    points at.
    """
    deal = DEAL_REGISTRY[CLO_DEAL_ID]
    registered_reports = {r["url"] for r in deal["investor_report_urls"]}
    for entry in deal["tape_urls"]:
        assert source_document_for(entry["url"]) in registered_reports, entry


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


def test_clo_answer_key_carries_published_test_results_and_claims_nothing_else() -> None:
    """The CLO's ground truth is authored, and only from what a document states.

    This test was the negative of itself until #481: no key was authored, and it
    pinned the absence so nothing could quietly fabricate one. What changed is
    the evidence, not the standard. The trustee reports state each coverage
    test's computed ratio, its required level and its outcome, so those — and
    strictly those — are committed. See
    ``test_quality_harness.test_committed_clo_answer_key_regenerates_from_its_report_fixtures``
    for the guard that the committed bytes are what the documents say.

    What the key must NOT claim is asserted as hard as what it carries, and since
    #495 that runs **per period** rather than over the key as a whole. Two
    documents contribute, and neither states the other's figures: a trustee report
    publishes coverage-test results and no Priority of Payments, the Note
    Valuation Report publishes both Priorities of Payments and no coverage test.
    A period claiming both would be claiming a figure no document states, which is
    the failure this pins — a key-wide "some period has covenants" check would not
    see it.

    Resolved through the real ``load_answer_key`` rather than a hardcoded
    filename: a key committed under any other slug would slip past a filename
    check, making the assertion pass for the wrong reason.
    """
    from loanwhiz.primitives.reconciliation_answer_key import load_answer_key

    key = load_answer_key(DEAL_REGISTRY[CLO_DEAL_ID])
    assert key is not None, "the CLO answer key is not committed"
    assert key.deal_id == CLO_DEAL_ID
    assert key.deal_name == CLO_DEAL_NAME
    assert [p.period_label for p in key.periods] == EXPECTED_KEY_PERIODS

    covenant_periods = [p for p in key.periods if p.period_label != NVR_PERIOD]
    assert [p.period_label for p in covenant_periods] == EXPECTED_REPORT_PERIODS
    for period in covenant_periods:
        assert period.covenants, f"{period.period_label} carries no published results"
        for covenant in period.covenants:
            # A published result without its published level is not gradeable
            # ground truth — the level is the half the prospectus never stated.
            assert covenant.threshold is not None
            assert covenant.actual is not None
        # A trustee report states no Priority of Payments and no pool statistic.
        assert period.revenue_pop == []
        assert period.redemption_pop == []
        assert period.available_revenue_funds is None
        assert period.available_principal_funds is None
        assert period.pool_stats == {}

    (pop_period,) = [p for p in key.periods if p.period_label == NVR_PERIOD]
    assert pop_period.revenue_pop and pop_period.redemption_pop
    assert pop_period.available_revenue_funds is not None
    assert pop_period.available_principal_funds is not None
    # Every published rate is a per-class applied rate the report states; the
    # document publishes no index fixing, so the key records none.
    assert pop_period.pool_stats
    assert all(k.startswith("applied_rate_") for k in pop_period.pool_stats)
    # And the Note Valuation Report states no coverage test, so none is claimed.
    assert pop_period.covenants == []

    # Exactly one committed key file names this deal, under the seed model's slug.
    assert [p.name for p in ANSWER_KEY_DATA_DIR.glob("*cairn*")] == ["cairn-clo-xvii-dac.json"]


def test_clo_answer_key_states_only_the_required_levels_the_reports_do() -> None:
    """Every published threshold in the key is a level a trustee report states.

    The cross-check that keeps the key tied to the source: ``REQUIRED_LEVELS``
    is transcribed in ``test_collateral_schedule_parser`` from the report text,
    and the key's thresholds must be exactly those, per test. Both directions,
    so neither a dropped test nor an invented one passes.

    Class F is the one test the reports state as ``N/A`` in every period. It has
    a required level like the rest, so it appears in ``REQUIRED_LEVELS`` — and
    it is deliberately absent from the key, because ``passed`` is a ``bool`` and
    cannot express "did not apply".
    """
    from loanwhiz.primitives.reconciliation_answer_key import load_answer_key

    from tests.test_collateral_schedule_parser import REQUIRED_LEVELS  # noqa: PLC0415

    key = load_answer_key(DEAL_REGISTRY[CLO_DEAL_ID])
    assert key is not None
    expected = {name: float(level) for name, level in REQUIRED_LEVELS.items()}
    assert "class_f_par_value_test" in expected, "fixture no longer states Class F"
    del expected["class_f_par_value_test"]

    graded = [p for p in key.periods if p.covenants]
    assert [p.period_label for p in graded] == EXPECTED_REPORT_PERIODS
    for period in graded:
        assert {c.name: c.threshold for c in period.covenants} == expected


def test_clo_cannot_reach_validated_and_the_reason_is_the_missing_series() -> None:
    """It still cannot reach ``validated`` — but for a different reason than before.

    Since #492 the capability matrix reads the answer-key registry rather than
    ``_VALIDATION_BUILDERS``; the map survives only for
    ``GET /deal/{id}/validation``, and it must still refuse the CLO.

    The *other* half of the refusal has now moved twice, and the movement is the
    point. Before epic #477 was merged in, this asserted no key was committed at
    all; #481's key made that false, so it was re-founded on the premise that the
    key carried **no Priority-of-Payments section** — explicitly "until one is
    authored from the Note Valuation Report (#495)". #495 has authored one, so it
    is re-founded once more, on the precondition that is now the one genuinely
    missing: no offline engine series (#496).

    Both the new premise and the retracted reason are asserted, because a cell
    that keeps wording that stopped being true is telling this deal a story true
    only of its past — the #457/#471 failure, and the reason this test has had to
    move rather than be deleted each time.
    """
    from loanwhiz.api.main import _VALIDATION_BUILDERS

    from loanwhiz.primitives.capability_matrix import (
        STATE_NOT_APPLICABLE,
        _NO_ENGINE_SERIES,
        _NO_POP_SECTION,
        _has_pop_section,
    )
    from loanwhiz.primitives.quality_harness import _default_series_provider
    from loanwhiz.primitives.reconciliation_answer_key import load_answer_key

    assert CLO_DEAL_ID not in _VALIDATION_BUILDERS

    # The premise the previous refusal rested on, now inverted: the key does
    # carry a Priority-of-Payments section, so that reason no longer applies.
    key = load_answer_key(DEAL_REGISTRY[CLO_DEAL_ID])
    assert key is not None, "the CLO's answer key should be committed"
    assert _has_pop_section(key), "#495 authored the PoP section this test rests on"

    # And the precondition that is actually missing — #496 supplies it.
    assert (
        _default_series_provider()(CLO_DEAL_ID, DEAL_REGISTRY[CLO_DEAL_ID], None) is None
    )

    (cell,) = [
        c
        for c in _live_matrix().cells
        if c.deal_id == CLO_DEAL_ID and c.capability_key == "engine_validation"
    ]
    assert cell.state == STATE_NOT_APPLICABLE
    assert cell.reason == _NO_ENGINE_SERIES
    assert cell.reason != _NO_POP_SECTION


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
    from loanwhiz.api.main import _load_cached_deal_model
    from loanwhiz.primitives.capability_matrix import build_capability_matrix
    from loanwhiz.primitives.quality_harness import _default_series_provider
    from loanwhiz.primitives.reconciliation_answer_key import load_answer_key

    return build_capability_matrix(
        deals=DEAL_REGISTRY,
        seed_loader=_load_cached_deal_model,
        answer_key_loader=load_answer_key,
        series_provider=_default_series_provider(),
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
    """Registering the CLO fabricates no green cell — it stays out of the set.

    The validated set is data-driven since #492, so this pins the property that
    matters here rather than a count that moves when another deal's key lands:
    no Cairn cell is validated, and every validated cell belongs to a deal with
    a committed PoP-bearing answer key.
    """
    from loanwhiz.primitives.capability_matrix import STATE_VALIDATED
    from loanwhiz.primitives.reconciliation_answer_key import load_answer_key

    matrix = _live_matrix()
    validated = [c for c in matrix.cells if c.state == STATE_VALIDATED]
    assert CLO_DEAL_ID not in {c.deal_id for c in validated}
    for cell in validated:
        key = load_answer_key(DEAL_REGISTRY[cell.deal_id])
        assert key is not None and any(
            p.revenue_pop or p.redemption_pop for p in key.periods
        ), cell.deal_id


def test_matrix_covers_every_registered_deal() -> None:
    """One column per registry deal, one cell per (capability × deal) pair —
    so a newly registered deal is never silently omitted from the honest tally."""
    matrix = _live_matrix()
    assert {d.deal_id for d in matrix.deals} == set(DEAL_REGISTRY)
    assert len(matrix.cells) == len(matrix.capabilities) * len(DEAL_REGISTRY)
    assert sum(matrix.tally.values()) == len(matrix.cells)


def test_answer_key_periods_agree_with_the_registered_tape_dates() -> None:
    """The key's reporting dates corroborate against a figure authored elsewhere.

    ``tape_urls`` carries a date per derived tape, transcribed by #468 from the
    same three reports but through a different path and by different hands. The
    answer key's ``reporting_date`` is converted from the report header's
    ``DD/MM/YYYY`` by #481. Agreement is evidence the conversion reads the date
    it thinks it does; a silent off-by-one or a day/month swap — ``16/12/2024``
    is unambiguous, but ``12/02/2025`` would not be — would show up here rather
    than as covenants that quietly match nothing.

    The Note Valuation Report's period has no derived tape to corroborate
    against, so it is checked against ``NVR_AS_OF`` instead — transcribed by hand
    from the document. It is the case that most needs it: ``08/01/2025`` reads as
    a valid date either way round, and a swap would file the period in August.
    """
    from loanwhiz.primitives.reconciliation_answer_key import load_answer_key

    key = load_answer_key(DEAL_REGISTRY[CLO_DEAL_ID])
    assert key is not None
    registered = [t["date"] for t in DEAL_REGISTRY[CLO_DEAL_ID]["tape_urls"]]
    covenant_dates = [p.reporting_date for p in key.periods if p.covenants]
    assert covenant_dates == registered

    (pop_period,) = [p for p in key.periods if p.period_label == NVR_PERIOD]
    assert pop_period.reporting_date == NVR_AS_OF
    assert pop_period.reporting_date not in registered


def test_the_committed_pop_period_is_the_shape_the_published_bound_rests_on() -> None:
    """The figures the answer-keys README and data-card state, as assertions.

    Both documents publish a bound on what a graded redemption row could ever
    prove: the report states EUR 0.00 of available principal funds, so all of its
    Principal steps are zero and an engine that never pays reproduces that
    waterfall exactly, while the Interest side distributes real money across a
    minority of its steps. That claim is the honest half of committing this
    ground truth — and as prose it is transcription, which a re-parse would leave
    silently stale.

    So the figures live here too, where a change reds. This does not duplicate
    the byte-for-byte regeneration test: that one asks whether the committed key
    matches the documents, and would stay green through a parser change (the key
    would simply be regenerated). This asks whether the *claim published about*
    the key is still true of it.
    """
    from loanwhiz.primitives.reconciliation_answer_key import load_answer_key

    key = load_answer_key(DEAL_REGISTRY[CLO_DEAL_ID])
    assert key is not None
    (period,) = [p for p in key.periods if p.period_label == NVR_PERIOD]

    # The Principal waterfall ran on nothing and paid nothing, every step of it.
    assert period.available_principal_funds == 0.0
    assert len(period.redemption_pop) == 62
    assert all(step.amount == 0.0 for step in period.redemption_pop)

    # The Interest waterfall is where the signal is: the steps sum to the funds
    # the report states, which is the parser's own tie-out, re-asserted on the
    # committed figures rather than on the parse.
    assert period.available_revenue_funds == 7_255_062.35
    assert len(period.revenue_pop) == 62
    assert sum(1 for step in period.revenue_pop if step.amount) == 22
    assert round(sum(step.amount for step in period.revenue_pop), 2) == 7_255_062.35
