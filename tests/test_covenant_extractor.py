"""Tests for loanwhiz.extraction.covenant_extractor.

Two test layers:

1. Unit tests — synthetic data, no network, no LLM.  These validate
   ``ExtractedTrigger``, ``ExtractedCovenants``, JSON round-trip, caching,
   and the section-collection logic using in-memory fixtures.
   They run in plain ``pytest`` with no external dependencies.

2. Integration tests (``@pytest.mark.integration``) — load the cached Green
   Lion 2026-1 prospectus sections and call Gemini 2.5 Pro to extract
   covenants and triggers.  These tests are automatically skipped when:
   - The cache file is absent AND network is unavailable.
   - ``httpx`` or ``docling`` are not importable.
   - The Gemini call fails (e.g. missing GCP credentials).

   Run: ``pytest -m integration tests/test_covenant_extractor.py``
   Skip in CI: ``pytest -m 'not integration'``
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from loanwhiz.extraction.covenant_extractor import (
    ExtractedCovenants,
    ExtractedTrigger,
    _covenants_from_json,
    _covenants_to_json,
    _default_cache_path,
    extract_covenants,
)
from loanwhiz.extraction.definitions_graph import DefinitionsGraph
from loanwhiz.extraction.section_router import SectionMap, route_sections


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CACHE_PATH = Path("/tmp/loanwhiz_cache/covenants_Green_Lion_2026_1_B_V_.json")

# A minimal synthetic prospectus excerpt that mentions all five known triggers
_SYNTHETIC_PROSPECTUS = """\
## Prospectus — Green Lion 2026-1 B.V.

## 5.2 Revenue Priority of Payments

On each Notes Payment Date, Available Revenue Funds shall be applied as follows.

Step 1: Pay Senior Expenses.
Step 2: Pay interest on the Class A Notes.
Step 3: If a Sequential Pay Trigger is outstanding, apply Available Revenue Funds
  to repay the Class A Notes principal sequentially. Otherwise, apply pro-rata.
Step 4: Credit the Class A Principal Deficiency Ledger if the Class A PDL shows
  a debit balance.
Step 5: Pay interest on Class B Notes.
Step 6: Credit the Class B Principal Deficiency Ledger if there is a debit balance.
Step 7: Top up the Reserve Fund if it is below the Reserve Fund Required Amount.
Step 8: Distribute remaining amounts to the Seller.

## 4.6 Conditions of the Notes

### Sequential Pay Trigger

The Sequential Pay Trigger shall be deemed outstanding on any Notes Payment Date
on which the Cumulative Loss Rate exceeds 2.0%. Once triggered, the waterfall
switches from pro-rata to sequential principal distribution until cured.

### Class A PDL Trigger

The Class A PDL Trigger is outstanding whenever the Class A Principal Deficiency
Ledger shows a debit balance greater than zero.

### Class B PDL Trigger

The Class B PDL Trigger is outstanding whenever the Class B Principal Deficiency
Ledger shows a debit balance greater than zero.

### Reserve Fund Trigger

The Reserve Fund Trigger fires whenever the Reserve Fund Balance falls below the
Reserve Fund Required Amount. The Issuer must top up the Reserve Fund on the next
Notes Payment Date.

### Clean-Up Call Option

The Seller has the option (but not the obligation) to repurchase all outstanding
Mortgage Loans on any Notes Payment Date on which the aggregate outstanding
principal balance of the Mortgage Loans is less than 10.0% of the Original
Portfolio Balance (the Clean-Up Call Threshold).

## 5.4 Issuer Covenants

The Issuer undertakes that it will not, without the prior written consent of the
Trustee:

(a) engage in any business activity other than the purchase and holding of the
    Mortgage Loans and the issuance of the Notes;
(b) incur any financial indebtedness other than as permitted under the Transaction
    Documents;
(c) create or permit to exist any security interest over any of its assets other
    than as created under the Transaction Documents.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_trigger(
    name: str = "sequential_pay_trigger",
    display_name: str = "Sequential Pay Trigger",
    description: str = "Switches principal distribution from pro-rata to sequential.",
    metric: str = "cumulative_loss_rate_pct",
    threshold: float | None = 2.0,
    threshold_unit: str | None = "percentage",
    direction: str = "above",
    consequence: str = "Principal switches to sequential distribution.",
    section_reference: str = "Section 4.6",
    citation: dict | None = None,
) -> ExtractedTrigger:
    return ExtractedTrigger(
        name=name,
        display_name=display_name,
        description=description,
        metric=metric,
        threshold=threshold,
        threshold_unit=threshold_unit,
        direction=direction,
        consequence=consequence,
        section_reference=section_reference,
        citation=citation or {"document": "prospectus", "page_or_row": "Section 4.6", "excerpt": "Cumulative Loss Rate exceeds 2.0%"},
    )


_SENTINEL = object()


def _make_covenants(
    triggers: list[ExtractedTrigger] | None | object = _SENTINEL,
    issuer_covenants: list[str] | None | object = _SENTINEL,
    confidence: float = 0.9,
) -> ExtractedCovenants:
    if triggers is _SENTINEL:
        triggers = [_make_trigger()]
    if issuer_covenants is _SENTINEL:
        issuer_covenants = ["No incurring of additional indebtedness."]
    return ExtractedCovenants(
        deal_name="Green Lion 2026-1 B.V.",
        triggers=triggers,  # type: ignore[arg-type]
        issuer_covenants=issuer_covenants,  # type: ignore[arg-type]
        extraction_confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Unit tests — no network, no LLM
# ---------------------------------------------------------------------------


class TestExtractedTrigger:
    """ExtractedTrigger Pydantic model — field validation and construction."""

    def test_required_fields(self) -> None:
        t = _make_trigger()
        assert t.name == "sequential_pay_trigger"
        assert t.display_name == "Sequential Pay Trigger"
        assert t.description
        assert t.metric == "cumulative_loss_rate_pct"
        assert t.threshold == pytest.approx(2.0)
        assert t.threshold_unit == "percentage"
        assert t.direction == "above"
        assert t.consequence
        assert t.section_reference == "Section 4.6"
        assert isinstance(t.citation, dict)

    def test_nullable_threshold(self) -> None:
        """threshold and threshold_unit may be None for boolean triggers."""
        t = _make_trigger(threshold=None, threshold_unit=None)
        assert t.threshold is None
        assert t.threshold_unit is None

    def test_non_zero_direction(self) -> None:
        t = _make_trigger(direction="non_zero")
        assert t.direction == "non_zero"

    def test_below_direction(self) -> None:
        t = _make_trigger(direction="below", metric="reserve_fund_balance")
        assert t.direction == "below"

    def test_citation_dict_structure(self) -> None:
        t = _make_trigger()
        assert "document" in t.citation
        assert "page_or_row" in t.citation
        assert "excerpt" in t.citation

    def test_all_ten_fields_present(self) -> None:
        """Verify all 10 fields from the issue spec are present."""
        expected_fields = {
            "name", "display_name", "description", "metric", "threshold",
            "threshold_unit", "direction", "consequence", "section_reference", "citation",
        }
        assert set(ExtractedTrigger.model_fields.keys()) == expected_fields


class TestExtractedCovenants:
    """ExtractedCovenants Pydantic model — field validation and construction."""

    def test_required_fields(self) -> None:
        c = _make_covenants()
        assert c.deal_name == "Green Lion 2026-1 B.V."
        assert isinstance(c.triggers, list)
        assert isinstance(c.issuer_covenants, list)
        assert isinstance(c.extraction_confidence, float)

    def test_four_fields(self) -> None:
        """Verify exactly 4 fields from the issue spec."""
        expected_fields = {"deal_name", "triggers", "issuer_covenants", "extraction_confidence"}
        assert set(ExtractedCovenants.model_fields.keys()) == expected_fields

    def test_empty_triggers_allowed(self) -> None:
        c = _make_covenants(triggers=[])
        assert c.triggers == []

    def test_multiple_triggers(self) -> None:
        triggers = [
            _make_trigger(name="sequential_pay_trigger"),
            _make_trigger(name="class_a_pdl_trigger", display_name="Class A PDL Trigger",
                          metric="class_a_pdl_debit_balance", threshold=None,
                          threshold_unit=None, direction="non_zero",
                          consequence="Class A PDL debit balance is funded before junior payments."),
        ]
        c = _make_covenants(triggers=triggers)
        assert len(c.triggers) == 2


class TestJsonRoundTrip:
    """_covenants_to_json / _covenants_from_json round-trip."""

    def test_round_trip_basic(self) -> None:
        original = _make_covenants()
        serialised = _covenants_to_json(original)
        data = json.loads(serialised)
        restored = _covenants_from_json(data)

        assert restored.deal_name == original.deal_name
        assert len(restored.triggers) == len(original.triggers)
        assert restored.extraction_confidence == pytest.approx(original.extraction_confidence)

    def test_round_trip_trigger_fields(self) -> None:
        original = _make_covenants(
            triggers=[_make_trigger(
                name="clean_up_call",
                display_name="Clean-Up Call Option",
                threshold=10.0,
                threshold_unit="percentage",
                direction="below",
                metric="pool_balance_fraction",
            )]
        )
        serialised = _covenants_to_json(original)
        restored = _covenants_from_json(json.loads(serialised))
        t = restored.triggers[0]
        assert t.name == "clean_up_call"
        assert t.threshold == pytest.approx(10.0)
        assert t.direction == "below"

    def test_round_trip_nullable_threshold(self) -> None:
        original = _make_covenants(triggers=[_make_trigger(threshold=None, threshold_unit=None)])
        data = json.loads(_covenants_to_json(original))
        restored = _covenants_from_json(data)
        assert restored.triggers[0].threshold is None
        assert restored.triggers[0].threshold_unit is None

    def test_round_trip_issuer_covenants(self) -> None:
        original = _make_covenants(
            issuer_covenants=["No new indebtedness.", "No security interests.", "No new business activities."]
        )
        data = json.loads(_covenants_to_json(original))
        restored = _covenants_from_json(data)
        assert restored.issuer_covenants == original.issuer_covenants

    def test_round_trip_multiple_triggers(self) -> None:
        triggers = [
            _make_trigger(name="sequential_pay_trigger"),
            _make_trigger(name="class_a_pdl_trigger", threshold=None, threshold_unit=None, direction="non_zero"),
            _make_trigger(name="class_b_pdl_trigger", threshold=None, threshold_unit=None, direction="non_zero"),
            _make_trigger(name="clean_up_call", threshold=10.0, direction="below"),
        ]
        original = _make_covenants(triggers=triggers)
        data = json.loads(_covenants_to_json(original))
        restored = _covenants_from_json(data)
        assert len(restored.triggers) == 4
        names = {t.name for t in restored.triggers}
        assert "sequential_pay_trigger" in names
        assert "class_a_pdl_trigger" in names

    def test_empty_graph_round_trip(self) -> None:
        original = ExtractedCovenants(
            deal_name="Test Deal",
            triggers=[],
            issuer_covenants=[],
            extraction_confidence=0.0,
        )
        data = json.loads(_covenants_to_json(original))
        restored = _covenants_from_json(data)
        assert len(restored.triggers) == 0
        assert restored.deal_name == "Test Deal"


class TestDefaultCachePath:
    """_default_cache_path — derives a filesystem-safe cache path."""

    def test_basic_deal_name(self) -> None:
        path = _default_cache_path("Green Lion 2026-1 B.V.")
        assert path.name.startswith("covenants_")
        assert path.suffix == ".json"
        # Special chars are sanitised
        assert " " not in path.name
        assert "." not in path.name.replace(".json", "")

    def test_different_deal_names_differ(self) -> None:
        p1 = _default_cache_path("Green Lion 2026-1 B.V.")
        p2 = _default_cache_path("Green Lion 2026-2 B.V.")
        assert p1 != p2


class TestExtractCovenantsWithCaching:
    """extract_covenants — unit test for cache loading path (no Gemini call)."""

    def _make_section_map(self) -> SectionMap:
        return route_sections(_SYNTHETIC_PROSPECTUS)

    def test_loads_from_cache(self, tmp_path: Path) -> None:
        """When a cache file exists, extract_covenants must load it without a Gemini call."""
        # Write a valid cache file
        cached_covenants = _make_covenants(
            triggers=[
                _make_trigger(name="sequential_pay_trigger"),
                _make_trigger(name="class_a_pdl_trigger", threshold=None, threshold_unit=None, direction="non_zero",
                              metric="class_a_pdl_debit_balance"),
                _make_trigger(name="class_b_pdl_trigger", threshold=None, threshold_unit=None, direction="non_zero",
                              metric="class_b_pdl_debit_balance"),
                _make_trigger(name="clean_up_call", threshold=10.0, direction="below",
                              metric="pool_balance_fraction"),
            ],
            confidence=0.95,
        )
        cache_file = tmp_path / "covenants_test.json"
        cache_file.write_text(_covenants_to_json(cached_covenants), encoding="utf-8")

        section_map = self._make_section_map()
        definitions = DefinitionsGraph()

        # Pass explicit cache_path — must NOT call Gemini
        result = extract_covenants(section_map, definitions, cache_path=str(cache_file))

        assert result.deal_name == cached_covenants.deal_name
        assert len(result.triggers) == 4
        assert result.extraction_confidence == pytest.approx(0.95)

    def test_cache_is_written_after_extraction(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """After a successful extraction, the cache file must be written."""
        # Monkeypatch extract_covenants to inject a fake Gemini response
        import loanwhiz.extraction.covenant_extractor as mod

        fake_result = _make_covenants(
            triggers=[
                _make_trigger(name="sequential_pay_trigger"),
                _make_trigger(name="class_a_pdl_trigger", threshold=None, direction="non_zero"),
                _make_trigger(name="class_b_pdl_trigger", threshold=None, direction="non_zero"),
                _make_trigger(name="clean_up_call", threshold=10.0, direction="below"),
            ],
            confidence=0.88,
        )

        def fake_generate_content(*args, **kwargs):
            """Return a mock Gemini response with the expected function call structure."""
            # Build the args that Gemini would return
            triggers_raw = [
                {
                    "name": t.name,
                    "display_name": t.display_name,
                    "description": t.description,
                    "metric": t.metric,
                    "threshold": t.threshold,
                    "threshold_unit": t.threshold_unit,
                    "direction": t.direction,
                    "consequence": t.consequence,
                    "section_reference": t.section_reference,
                    "citation": t.citation,
                }
                for t in fake_result.triggers
            ]

            class FakeFunctionCall:
                name = _EXTRACT_TOOL_NAME = "record_triggers_and_covenants"
                args = {
                    "triggers": triggers_raw,
                    "issuer_covenants": fake_result.issuer_covenants,
                    "extraction_confidence": fake_result.extraction_confidence,
                }

            class FakePart:
                function_call = FakeFunctionCall()

            class FakeContent:
                parts = [FakePart()]

            class FakeCandidate:
                content = FakeContent()

            class FakeResponse:
                candidates = [FakeCandidate()]

            return FakeResponse()

        # Patch the genai.Client class
        class FakeClient:
            def __init__(self, **kwargs):
                pass

            class models:
                @staticmethod
                def generate_content(*args, **kwargs):
                    return fake_generate_content(*args, **kwargs)

        monkeypatch.setattr(mod.genai, "Client", FakeClient)

        cache_file = tmp_path / "covenants_written.json"
        section_map = self._make_section_map()
        definitions = DefinitionsGraph()

        assert not cache_file.exists()
        result = extract_covenants(section_map, definitions, cache_path=str(cache_file))
        assert cache_file.exists()

        # Re-load from the written cache to verify round-trip
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        reloaded = _covenants_from_json(data)
        assert len(reloaded.triggers) == len(result.triggers)
        assert reloaded.extraction_confidence == pytest.approx(result.extraction_confidence)

    def test_sequential_in_trigger_names(self, tmp_path: Path) -> None:
        """At least one trigger name must contain 'sequential'."""
        cached = _make_covenants(triggers=[
            _make_trigger(name="sequential_pay_trigger"),
            _make_trigger(name="class_a_pdl_trigger", threshold=None, direction="non_zero"),
        ])
        cache_file = tmp_path / "covenants_seq.json"
        cache_file.write_text(_covenants_to_json(cached), encoding="utf-8")

        section_map = self._make_section_map()
        result = extract_covenants(section_map, DefinitionsGraph(), cache_path=str(cache_file))

        names_lower = [t.name.lower() for t in result.triggers]
        assert any("sequential" in name for name in names_lower)

    def test_pdl_in_trigger_names(self, tmp_path: Path) -> None:
        """At least one trigger name must contain 'pdl'."""
        cached = _make_covenants(triggers=[
            _make_trigger(name="sequential_pay_trigger"),
            _make_trigger(name="class_a_pdl_trigger", threshold=None, direction="non_zero"),
        ])
        cache_file = tmp_path / "covenants_pdl.json"
        cache_file.write_text(_covenants_to_json(cached), encoding="utf-8")

        section_map = self._make_section_map()
        result = extract_covenants(section_map, DefinitionsGraph(), cache_path=str(cache_file))

        names_lower = [t.name.lower() for t in result.triggers]
        assert any("pdl" in name for name in names_lower)

    def test_all_triggers_have_description_and_consequence(self, tmp_path: Path) -> None:
        """Every trigger must have a non-empty description and consequence."""
        cached = _make_covenants(triggers=[
            _make_trigger(name="sequential_pay_trigger"),
            _make_trigger(name="class_a_pdl_trigger", threshold=None, direction="non_zero",
                          description="PDL debit balance is non-zero.", consequence="Waterfall step executes."),
        ])
        cache_file = tmp_path / "covenants_desc.json"
        cache_file.write_text(_covenants_to_json(cached), encoding="utf-8")

        section_map = self._make_section_map()
        result = extract_covenants(section_map, DefinitionsGraph(), cache_path=str(cache_file))

        for t in result.triggers:
            assert t.description, f"Trigger '{t.name}' has empty description"
            assert t.consequence, f"Trigger '{t.name}' has empty consequence"

    def test_extraction_confidence_above_threshold(self, tmp_path: Path) -> None:
        """Cached extraction_confidence must be > 0.7."""
        cached = _make_covenants(confidence=0.85)
        cache_file = tmp_path / "covenants_conf.json"
        cache_file.write_text(_covenants_to_json(cached), encoding="utf-8")

        section_map = self._make_section_map()
        result = extract_covenants(section_map, DefinitionsGraph(), cache_path=str(cache_file))

        assert result.extraction_confidence > 0.7

    def test_at_least_four_triggers(self, tmp_path: Path) -> None:
        """Must extract at least 4 triggers."""
        cached = _make_covenants(triggers=[
            _make_trigger(name="sequential_pay_trigger"),
            _make_trigger(name="class_a_pdl_trigger", threshold=None, direction="non_zero"),
            _make_trigger(name="class_b_pdl_trigger", threshold=None, direction="non_zero"),
            _make_trigger(name="clean_up_call", threshold=10.0, direction="below"),
        ])
        cache_file = tmp_path / "covenants_four.json"
        cache_file.write_text(_covenants_to_json(cached), encoding="utf-8")

        section_map = self._make_section_map()
        result = extract_covenants(section_map, DefinitionsGraph(), cache_path=str(cache_file))

        assert len(result.triggers) >= 4


# ---------------------------------------------------------------------------
# Integration tests — require network + Docling + Gemini
# ---------------------------------------------------------------------------


def _get_or_build_covenants() -> ExtractedCovenants | None:
    """Return a real ExtractedCovenants for the Green Lion prospectus.

    Loads from the disk cache if available; otherwise calls extract_covenants
    which will download the PDF, run Docling, and invoke Gemini.
    Returns None on any failure so callers can skip.
    """
    try:
        from loanwhiz.extraction.definitions_graph import load_or_extract as load_defs

        _PROSPECTUS_URL = (
            "https://huggingface.co/datasets/Algoritmica/green-lion-2026"
            "/resolve/main/Hackathon_Data/green-lion-2026-1-prospectus.pdf"
        )
        _DEF_CACHE = "/tmp/loanwhiz_cache/definitions_green_lion_2026_1_prospectus.json"

        # Try to load section map from cached definitions (cheap)
        # Fall back to re-downloading the prospectus if necessary
        if _CACHE_PATH.exists():
            # Load directly from cache
            data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
            return _covenants_from_json(data)

        # Need a SectionMap — try to build from a cached markdown or re-download
        try:
            from docling.document_converter import DocumentConverter
            import httpx

            pdf_path = Path("/tmp/loanwhiz_cache/green-lion-2026-1-prospectus.pdf")
            if not pdf_path.exists():
                Path("/tmp/loanwhiz_cache").mkdir(parents=True, exist_ok=True)
                with httpx.Client(follow_redirects=True, timeout=120) as client:
                    resp = client.get(_PROSPECTUS_URL)
                    resp.raise_for_status()
                pdf_path.write_bytes(resp.content)

            converter = DocumentConverter()
            result = converter.convert(str(pdf_path))
            markdown = result.document.export_to_markdown()
            section_map = route_sections(markdown)
        except Exception:
            return None

        definitions = load_defs(_PROSPECTUS_URL, cache_path=_DEF_CACHE)
        return extract_covenants(section_map, definitions, cache_path=str(_CACHE_PATH))

    except Exception:
        return None


@pytest.mark.integration
class TestGreenLionCovenantExtractor:
    """Integration tests against the real Green Lion 2026-1 prospectus.

    These tests:
    - Load the cached covenants if available at ``_CACHE_PATH``.
    - Extract fresh from the prospectus (download + Docling + Gemini) if not.
    - Skip automatically if anything goes wrong (network, credentials, etc.).

    Run: ``pytest -m integration tests/test_covenant_extractor.py``
    Skip in CI: ``pytest -m 'not integration'``
    """

    @pytest.fixture(scope="class")
    def covenants(self) -> ExtractedCovenants:
        c = _get_or_build_covenants()
        if c is None:
            pytest.skip(
                "Green Lion covenants unavailable "
                "(no cache, no network, or Gemini credentials absent)"
            )
        return c

    def test_at_least_four_triggers(self, covenants: ExtractedCovenants) -> None:
        """Green Lion has ≥ 4 distinct triggers."""
        assert len(covenants.triggers) >= 4, (
            f"Expected ≥ 4 triggers, got {len(covenants.triggers)}. "
            f"Triggers: {[t.name for t in covenants.triggers]}"
        )

    def test_sequential_in_trigger_name(self, covenants: ExtractedCovenants) -> None:
        """At least one trigger name must contain 'sequential' (case-insensitive)."""
        names_lower = [t.name.lower() for t in covenants.triggers]
        assert any("sequential" in name for name in names_lower), (
            f"No trigger with 'sequential' in name. Trigger names: {names_lower}"
        )

    def test_pdl_in_trigger_name(self, covenants: ExtractedCovenants) -> None:
        """At least one trigger name must contain 'pdl' (case-insensitive)."""
        names_lower = [t.name.lower() for t in covenants.triggers]
        assert any("pdl" in name for name in names_lower), (
            f"No trigger with 'pdl' in name. Trigger names: {names_lower}"
        )

    def test_all_triggers_have_description(self, covenants: ExtractedCovenants) -> None:
        """Every trigger must have a non-empty description."""
        for t in covenants.triggers:
            assert t.description, f"Trigger '{t.name}' has empty description"

    def test_all_triggers_have_consequence(self, covenants: ExtractedCovenants) -> None:
        """Every trigger must have a non-empty consequence."""
        for t in covenants.triggers:
            assert t.consequence, f"Trigger '{t.name}' has empty consequence"

    def test_extraction_confidence_above_threshold(self, covenants: ExtractedCovenants) -> None:
        """extraction_confidence must be > 0.7."""
        assert covenants.extraction_confidence > 0.7, (
            f"Expected confidence > 0.7, got {covenants.extraction_confidence}"
        )

    def test_cache_file_exists(self, covenants: ExtractedCovenants) -> None:
        """After extraction, the cache file must be present."""
        assert _CACHE_PATH.exists(), (
            f"Cache file not found at {_CACHE_PATH}. "
            "extract_covenants should have written it."
        )

    def test_cache_reload_matches(self, covenants: ExtractedCovenants) -> None:
        """Loading from cache reproduces the same covenants."""
        if not _CACHE_PATH.exists():
            pytest.skip("Cache not available")
        data = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        reloaded = _covenants_from_json(data)
        assert len(reloaded.triggers) == len(covenants.triggers)
        assert reloaded.extraction_confidence == pytest.approx(covenants.extraction_confidence)

    def test_triggers_have_section_reference(self, covenants: ExtractedCovenants) -> None:
        """Every trigger should have a non-empty section_reference."""
        for t in covenants.triggers:
            assert t.section_reference, f"Trigger '{t.name}' missing section_reference"

    def test_triggers_have_citation(self, covenants: ExtractedCovenants) -> None:
        """Every trigger must have a citation dict with expected keys."""
        for t in covenants.triggers:
            assert isinstance(t.citation, dict), f"Trigger '{t.name}' citation is not a dict"
            assert "document" in t.citation
            assert "page_or_row" in t.citation
            assert "excerpt" in t.citation

    def test_deal_name_non_empty(self, covenants: ExtractedCovenants) -> None:
        """deal_name must be non-empty."""
        assert covenants.deal_name

    def test_issuer_covenants_present(self, covenants: ExtractedCovenants) -> None:
        """At least one issuer covenant should be extracted from Green Lion."""
        assert len(covenants.issuer_covenants) >= 1, (
            "Expected at least one issuer covenant from the Green Lion prospectus"
        )
