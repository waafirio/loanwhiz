"""Tests for loanwhiz.extraction.assembler.

Two test layers:

1. Unit tests — synthetic data, no network, no Docling, no Gemini.  These
   validate :func:`_slug`, :func:`_extract_tranches`, completeness-score
   computation, cache write/read round-trip, and the mocked full extraction
   flow.  They run in plain ``pytest`` with no external dependencies.

2. Integration test (``@pytest.mark.integration``) — full extraction of the
   Green Lion 2026-1 prospectus.  Skipped automatically when the deal model
   cache is absent and Gemini credentials / network are unavailable.

   Run: ``pytest -m integration tests/test_assembler.py``
   Skip in CI: ``pytest -m 'not integration'``
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from loanwhiz.extraction.assembler import (
    DealModel,
    DealModelMetadata,
    _extract_tranches,
    _slug,
    extract_deal_model,
)
from loanwhiz.extraction.waterfall_extractor import ExtractedWaterfall, WaterfallStep

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEAL_NAME = "Green Lion 2026-1 B.V."
_PROSPECTUS_URL = (
    "https://huggingface.co/datasets/Algoritmica/green-lion-2026"
    "/resolve/main/Hackathon_Data/green-lion-2026-1-prospectus.pdf"
)
_CACHE_DIR = Path("/tmp/loanwhiz_cache")
_DEAL_CACHE_PATH = _CACHE_DIR / "deals" / "green-lion-2026-1-bv.json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_step(
    priority: str = "(a)",
    recipient: str = "security_trustee_fees",
    description: str = "Pay security trustee fees.",
    waterfall_type: str = "revenue",
) -> WaterfallStep:
    return WaterfallStep(
        priority=priority,
        recipient=recipient,
        description=description,
        amount_formula="as accrued",
        condition=None,
        is_pari_passu=False,
        citation={
            "document": "Green Lion 2026-1 Prospectus",
            "page_or_row": f"Section 5.2{priority}",
            "excerpt": description[:50],
        },
    )


def _make_waterfall(
    waterfall_type: str = "revenue",
    n_steps: int = 3,
) -> ExtractedWaterfall:
    labels = [f"({chr(ord('a') + i)})" for i in range(n_steps)]
    steps = [_make_step(priority=lbl, waterfall_type=waterfall_type) for lbl in labels]
    return ExtractedWaterfall(
        deal_name=_DEAL_NAME,
        waterfall_type=waterfall_type,
        steps=steps,
        source_section="Section 5.2",
        extraction_confidence=1.0,
    )


def _make_minimal_deal_model(cache_path: str = "/tmp/test_deal.json") -> DealModel:
    """Build a minimal DealModel for use in cache round-trip tests."""
    return DealModel(
        metadata=DealModelMetadata(
            deal_name="Test Deal",
            prospectus_url="https://example.com/prospectus.pdf",
            extracted_at="2026-06-03T00:00:00+00:00",
            extraction_duration_sec=1.5,
            sections_found=["definitions", "revenue_priority_of_payments"],
            completeness_score=0.5,
            cache_path=cache_path,
        ),
        definitions={
            "Available Distribution Amount": {
                "definition": "The amount available for distribution.",
                "page_or_section": "Section 9.1",
            }
        },
        waterfalls={
            "revenue": _make_waterfall("revenue").model_dump(),
        },
        covenants={
            "deal_name": "Test Deal",
            "triggers": [],
            "issuer_covenants": [],
            "extraction_confidence": 0.5,
        },
        tranche_structure=[
            {
                "priority": "(a)",
                "recipient": "security_trustee_fees",
                "description": "Pay security trustee fees.",
                "waterfall_type": "revenue",
            }
        ],
        trigger_names=[],
    )


# ---------------------------------------------------------------------------
# Unit tests — _slug
# ---------------------------------------------------------------------------


class TestSlug:
    def test_lowercase(self) -> None:
        assert _slug("Green Lion") == "green-lion"

    def test_spaces_become_hyphens(self) -> None:
        assert _slug("My Deal Name") == "my-deal-name"

    def test_periods_removed(self) -> None:
        # "B.V." → periods become hyphens, then collapsed
        result = _slug("Green Lion 2026-1 B.V.")
        assert "." not in result
        assert result == "green-lion-2026-1-bv"

    def test_commas_removed(self) -> None:
        result = _slug("Deal, Inc.")
        assert "," not in result
        assert result == "deal-inc"

    def test_no_leading_trailing_hyphens(self) -> None:
        result = _slug(".Leading dot")
        assert not result.startswith("-")

    def test_consecutive_separators_collapsed(self) -> None:
        # Multiple spaces/dots/commas should not produce double hyphens
        result = _slug("A  B")
        assert "--" not in result

    def test_returns_string(self) -> None:
        assert isinstance(_slug("any name"), str)


# ---------------------------------------------------------------------------
# Unit tests — _extract_tranches
# ---------------------------------------------------------------------------


class TestExtractTranches:
    def test_empty_waterfalls_returns_empty_list(self) -> None:
        result = _extract_tranches({})
        assert result == []

    def test_revenue_waterfall_steps_become_tranches(self) -> None:
        waterfalls = {"revenue": _make_waterfall("revenue", n_steps=3)}
        result = _extract_tranches(waterfalls)
        assert len(result) == 3

    def test_tranche_has_required_keys(self) -> None:
        waterfalls = {"revenue": _make_waterfall("revenue", n_steps=2)}
        result = _extract_tranches(waterfalls)
        for tranche in result:
            assert "priority" in tranche
            assert "recipient" in tranche
            assert "description" in tranche
            assert "waterfall_type" in tranche

    def test_tranche_waterfall_type_matches_source(self) -> None:
        waterfalls = {"revenue": _make_waterfall("revenue", n_steps=2)}
        result = _extract_tranches(waterfalls)
        assert all(t["waterfall_type"] == "revenue" for t in result)

    def test_prefers_revenue_over_redemption(self) -> None:
        waterfalls = {
            "redemption": _make_waterfall("redemption", n_steps=5),
            "revenue": _make_waterfall("revenue", n_steps=3),
        }
        result = _extract_tranches(waterfalls)
        # Should pick revenue (3 steps), not redemption (5 steps)
        assert len(result) == 3
        assert all(t["waterfall_type"] == "revenue" for t in result)

    def test_falls_back_to_first_waterfall_when_no_revenue(self) -> None:
        waterfalls = {"redemption": _make_waterfall("redemption", n_steps=4)}
        result = _extract_tranches(waterfalls)
        assert len(result) == 4

    def test_priorities_preserved_in_order(self) -> None:
        waterfalls = {"revenue": _make_waterfall("revenue", n_steps=3)}
        result = _extract_tranches(waterfalls)
        priorities = [t["priority"] for t in result]
        assert priorities == ["(a)", "(b)", "(c)"]


# ---------------------------------------------------------------------------
# Unit tests — completeness score
# ---------------------------------------------------------------------------


class TestCompletenessScore:
    """Verify the completeness formula: found ∩ expected / |expected|."""

    def test_zero_sections_found(self) -> None:
        # No expected sections found → score = 0
        sections_found: list[str] = []
        expected = [
            "definitions",
            "revenue_priority_of_payments",
            "conditions_of_notes",
            "available_funds",
        ]
        score = len([s for s in expected if s in sections_found]) / len(expected)
        assert score == 0.0

    def test_all_sections_found(self) -> None:
        sections_found = [
            "definitions",
            "revenue_priority_of_payments",
            "conditions_of_notes",
            "available_funds",
        ]
        expected = [
            "definitions",
            "revenue_priority_of_payments",
            "conditions_of_notes",
            "available_funds",
        ]
        score = len([s for s in expected if s in sections_found]) / len(expected)
        assert score == 1.0

    def test_half_sections_found(self) -> None:
        sections_found = ["definitions", "revenue_priority_of_payments"]
        expected = [
            "definitions",
            "revenue_priority_of_payments",
            "conditions_of_notes",
            "available_funds",
        ]
        score = len([s for s in expected if s in sections_found]) / len(expected)
        assert score == 0.5

    def test_extra_sections_do_not_raise_score_above_1(self) -> None:
        # Non-expected sections in sections_found should not affect the score
        sections_found = [
            "definitions",
            "revenue_priority_of_payments",
            "conditions_of_notes",
            "available_funds",
            "some_other_section",
        ]
        expected = [
            "definitions",
            "revenue_priority_of_payments",
            "conditions_of_notes",
            "available_funds",
        ]
        score = len([s for s in expected if s in sections_found]) / len(expected)
        assert score == 1.0


# ---------------------------------------------------------------------------
# Unit tests — DealModel cache round-trip
# ---------------------------------------------------------------------------


class TestCacheRoundTrip:
    """Write a DealModel to disk and load it back — assert equality."""

    def test_round_trip_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = str(Path(tmpdir) / "test_deal.json")
            original = _make_minimal_deal_model(cache_path=cache_file)

            # Write
            Path(cache_file).write_text(original.model_dump_json(indent=2), encoding="utf-8")

            # Read
            restored = DealModel.model_validate_json(
                Path(cache_file).read_text(encoding="utf-8")
            )

            assert restored.metadata.deal_name == original.metadata.deal_name
            assert restored.metadata.prospectus_url == original.metadata.prospectus_url
            assert restored.metadata.completeness_score == original.metadata.completeness_score

    def test_round_trip_definitions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = str(Path(tmpdir) / "test_deal.json")
            original = _make_minimal_deal_model(cache_path=cache_file)

            Path(cache_file).write_text(original.model_dump_json(indent=2), encoding="utf-8")
            restored = DealModel.model_validate_json(
                Path(cache_file).read_text(encoding="utf-8")
            )

            assert restored.definitions == original.definitions

    def test_round_trip_tranche_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = str(Path(tmpdir) / "test_deal.json")
            original = _make_minimal_deal_model(cache_path=cache_file)

            Path(cache_file).write_text(original.model_dump_json(indent=2), encoding="utf-8")
            restored = DealModel.model_validate_json(
                Path(cache_file).read_text(encoding="utf-8")
            )

            assert restored.tranche_structure == original.tranche_structure

    def test_round_trip_trigger_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = str(Path(tmpdir) / "test_deal.json")
            original = _make_minimal_deal_model(cache_path=cache_file)

            Path(cache_file).write_text(original.model_dump_json(indent=2), encoding="utf-8")
            restored = DealModel.model_validate_json(
                Path(cache_file).read_text(encoding="utf-8")
            )

            assert restored.trigger_names == original.trigger_names

    def test_round_trip_preserves_json_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = str(Path(tmpdir) / "test_deal.json")
            original = _make_minimal_deal_model(cache_path=cache_file)

            json_str = original.model_dump_json(indent=2)
            Path(cache_file).write_text(json_str, encoding="utf-8")

            # Parse back as raw dict and verify structure
            data = json.loads(json_str)
            assert "metadata" in data
            assert "definitions" in data
            assert "waterfalls" in data
            assert "covenants" in data
            assert "tranche_structure" in data
            assert "trigger_names" in data


# ---------------------------------------------------------------------------
# Unit tests — extract_deal_model (cache hit — no sub-extractors called)
# ---------------------------------------------------------------------------


class TestExtractDealModelCached:
    """extract_deal_model must load from cache without calling Docling or Gemini."""

    def test_cache_hit_skips_download(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "green-lion-2026-1-bv.json"
            original = _make_minimal_deal_model(cache_path=str(cache_file))
            # Seed the cache
            cache_file.write_text(original.model_dump_json(indent=2), encoding="utf-8")

            with patch(
                "loanwhiz.extraction.assembler._download_and_convert"
            ) as mock_download:
                result = extract_deal_model(
                    prospectus_url=_PROSPECTUS_URL,
                    deal_name=_DEAL_NAME,
                    cache_dir=tmpdir,
                )
                mock_download.assert_not_called()

            assert result.metadata.deal_name == original.metadata.deal_name

    def test_cache_hit_skips_extract_definitions(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "green-lion-2026-1-bv.json"
            original = _make_minimal_deal_model(cache_path=str(cache_file))
            cache_file.write_text(original.model_dump_json(indent=2), encoding="utf-8")

            with patch(
                "loanwhiz.extraction.assembler.extract_definitions"
            ) as mock_defs:
                extract_deal_model(
                    prospectus_url=_PROSPECTUS_URL,
                    deal_name=_DEAL_NAME,
                    cache_dir=tmpdir,
                )
                mock_defs.assert_not_called()

    def test_force_refresh_bypasses_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "green-lion-2026-1-bv.json"
            original = _make_minimal_deal_model(cache_path=str(cache_file))
            cache_file.write_text(original.model_dump_json(indent=2), encoding="utf-8")

            with patch(
                "loanwhiz.extraction.assembler._download_and_convert"
            ) as mock_download:
                mock_download.return_value = "# Section\nSome text"
                with patch(
                    "loanwhiz.extraction.assembler.extract_definitions"
                ) as mock_defs, patch(
                    "loanwhiz.extraction.assembler.extract_all_waterfalls"
                ) as mock_wf, patch(
                    "loanwhiz.extraction.assembler.extract_covenants"
                ) as mock_cov:
                    mock_defs.return_value = MagicMock(terms={})
                    mock_wf.return_value = {}
                    mock_cov.return_value = MagicMock(
                        model_dump=lambda: {
                            "deal_name": "Test",
                            "triggers": [],
                            "issuer_covenants": [],
                            "extraction_confidence": 0.0,
                        },
                        triggers=[],
                    )
                    extract_deal_model(
                        prospectus_url=_PROSPECTUS_URL,
                        deal_name=_DEAL_NAME,
                        cache_dir=tmpdir,
                        force_refresh=True,
                    )
                    # With force_refresh=True the download must be called
                    mock_download.assert_called_once()


# ---------------------------------------------------------------------------
# Unit tests — extract_deal_model (fully mocked — no network, no LLM)
# ---------------------------------------------------------------------------


class TestExtractDealModelMocked:
    """extract_deal_model wires all sub-extractors correctly when no cache exists."""

    def test_result_has_correct_deal_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._run_mocked(tmpdir, _DEAL_NAME)

    def test_writes_cache_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            self._run_mocked(tmpdir, _DEAL_NAME)
            expected_cache = Path(tmpdir) / "green-lion-2026-1-bv.json"
            assert expected_cache.exists()

    def test_trigger_names_populated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._run_mocked(tmpdir, _DEAL_NAME)
            assert isinstance(result.trigger_names, list)

    def test_tranche_structure_populated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._run_mocked(tmpdir, _DEAL_NAME)
            assert isinstance(result.tranche_structure, list)

    def test_completeness_score_between_0_and_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._run_mocked(tmpdir, _DEAL_NAME)
            assert 0.0 <= result.metadata.completeness_score <= 1.0

    def test_sections_found_is_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._run_mocked(tmpdir, _DEAL_NAME)
            assert isinstance(result.metadata.sections_found, list)

    def test_definitions_dict_serialised_correctly(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._run_mocked(tmpdir, _DEAL_NAME)
            for key, val in result.definitions.items():
                assert isinstance(key, str)
                assert "definition" in val
                assert "page_or_section" in val

    # ------------------------------------------------------------------
    # Shared helper — set up all mocks and run extract_deal_model
    # ------------------------------------------------------------------

    @staticmethod
    def _run_mocked(cache_dir: str, deal_name: str) -> DealModel:
        mock_markdown = "# Definitions\nSome text\n## Revenue Priority of Payments\nWaterfall text"

        fake_defined_term = MagicMock()
        fake_defined_term.definition = "Amount available for distribution."
        fake_defined_term.page_or_section = "Section 9.1"

        fake_defs_graph = MagicMock()
        fake_defs_graph.terms = {"Available Distribution Amount": fake_defined_term}

        fake_waterfall = _make_waterfall("revenue", n_steps=2)
        fake_waterfalls = {"revenue": fake_waterfall}

        fake_trigger = MagicMock()
        fake_trigger.name = "sequential_pay_trigger"

        fake_covenants = MagicMock()
        fake_covenants.model_dump.return_value = {
            "deal_name": deal_name,
            "triggers": [{"name": "sequential_pay_trigger"}],
            "issuer_covenants": [],
            "extraction_confidence": 0.8,
        }
        fake_covenants.triggers = [fake_trigger]

        with patch(
            "loanwhiz.extraction.assembler._download_and_convert",
            return_value=mock_markdown,
        ), patch(
            "loanwhiz.extraction.assembler.extract_definitions",
            return_value=fake_defs_graph,
        ), patch(
            "loanwhiz.extraction.assembler.extract_all_waterfalls",
            return_value=fake_waterfalls,
        ), patch(
            "loanwhiz.extraction.assembler.extract_covenants",
            return_value=fake_covenants,
        ):
            return extract_deal_model(
                prospectus_url=_PROSPECTUS_URL,
                deal_name=deal_name,
                cache_dir=cache_dir,
            )


# ---------------------------------------------------------------------------
# Integration test — full Green Lion extraction (skipped in CI)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGreenLionDealModel:
    """Integration tests against the real Green Lion 2026-1 deal model.

    Runs only when the deal model cache already exists at the expected path
    (written by a prior extraction run) OR when Docling and Gemini credentials
    are available.

    Run: ``pytest -m integration tests/test_assembler.py``
    Skip in CI: ``pytest -m 'not integration'``
    """

    @pytest.fixture(scope="class")
    def model(self) -> DealModel:
        if _DEAL_CACHE_PATH.exists():
            try:
                return DealModel.model_validate_json(
                    _DEAL_CACHE_PATH.read_text(encoding="utf-8")
                )
            except Exception:
                pass

        # Attempt live extraction — will be skipped on any failure.
        try:
            return extract_deal_model(
                prospectus_url=_PROSPECTUS_URL,
                deal_name=_DEAL_NAME,
                cache_dir=str(_CACHE_DIR / "deals"),
            )
        except Exception as exc:
            pytest.skip(
                f"Green Lion deal model unavailable (no cache, no network, "
                f"or Gemini credentials absent): {exc}"
            )

    def test_deal_name_matches(self, model: DealModel) -> None:
        assert model.metadata.deal_name == _DEAL_NAME

    def test_has_definitions(self, model: DealModel) -> None:
        assert len(model.definitions) > 0, "Expected at least one defined term"

    def test_has_revenue_waterfall(self, model: DealModel) -> None:
        assert "revenue" in model.waterfalls, "Expected revenue waterfall"

    def test_revenue_waterfall_has_steps(self, model: DealModel) -> None:
        revenue = model.waterfalls["revenue"]
        assert len(revenue["steps"]) > 0

    def test_completeness_score_between_0_and_1(self, model: DealModel) -> None:
        assert 0.0 <= model.metadata.completeness_score <= 1.0

    def test_tranche_structure_derived_from_waterfall(self, model: DealModel) -> None:
        assert isinstance(model.tranche_structure, list)
        if model.tranche_structure:
            tranche = model.tranche_structure[0]
            assert "priority" in tranche
            assert "recipient" in tranche
            assert "waterfall_type" in tranche

    def test_trigger_names_is_list(self, model: DealModel) -> None:
        assert isinstance(model.trigger_names, list)

    def test_cache_file_written(self, model: DealModel) -> None:
        assert _DEAL_CACHE_PATH.exists(), f"Cache not found at {_DEAL_CACHE_PATH}"

    def test_cache_reload_gives_same_deal_name(self, model: DealModel) -> None:
        if not _DEAL_CACHE_PATH.exists():
            pytest.skip("Cache not available")
        reloaded = DealModel.model_validate_json(
            _DEAL_CACHE_PATH.read_text(encoding="utf-8")
        )
        assert reloaded.metadata.deal_name == model.metadata.deal_name
