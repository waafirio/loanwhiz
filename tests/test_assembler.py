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

# Import the primitives package before any loanwhiz.domain import so the
# domain<->primitives module graph is populated in the cycle-safe order
# (a pre-existing import-order sensitivity; see
# loanwhiz.primitives.__init__.__getattr__). Harmless when domain is
# already imported by an earlier-collected test.
import loanwhiz.primitives  # noqa: F401  (import-order guard)

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from loanwhiz.extraction.assembler import (
    DEFAULT_DEAL_CACHE_DIR,
    DEFAULT_DOCLING_CACHE_DIR,
    DealModel,
    DealModelMetadata,
    _completeness_score,
    _docling_cache_path,
    _download_and_convert,
    _extract_tranches,
    _slug,
    extract_deal_model,
)
from loanwhiz.extraction.section_router import route_sections
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
                "name": "Class A",
                "size_eur": 1_000_000_000.0,
                "rating": "AAA",
                "rate": "3m EURIBOR + 0.43%",
                "seniority": 0,
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


# Representative Green Lion tranche table as Docling renders it: the first
# table of the prospectus, class-as-column with attribute rows.  This is the
# fixture the unit tests feed to _extract_tranches so the slow full pipeline
# (Docling + Gemini) is never required to validate the tranche parse.
_GREEN_LION_TRANCHE_TABLE_MD = """\
# Green Lion 2026-1 B.V. — Notes

|                  | Class A             | Class B          | Class C          |
|------------------|---------------------|------------------|------------------|
| Principal Amount | €1,000,000,000      | €53,100,000      | €10,500,000      |
| Issue Price      | 100%                | 100%             | 100%             |
| Interest rate    | 3m EURIBOR + 0.43%  | 3m EURIBOR + 1.10% | 2.50%          |
| Expected Ratings | AAA / Aaa           | AA / Aa2         | NR               |

## Conditions of the Notes
Some following text.
"""


def _waterfall_with_class_recipients() -> dict:
    """A waterfall whose steps reference class_a/class_b/class_c recipients."""
    steps = [
        _make_step(priority="(a)", recipient="class_a_notes_principal"),
        _make_step(priority="(b)", recipient="class_b_notes_principal"),
        _make_step(priority="(c)", recipient="class_c_notes_principal"),
    ]
    wf = ExtractedWaterfall(
        deal_name=_DEAL_NAME,
        waterfall_type="post_enforcement",
        steps=steps,
        source_section="Post-Enforcement Priority of Payments",
        extraction_confidence=1.0,
    )
    return {"post_enforcement": wf}


# A Sol-Lion-shaped multi-series tranche table (Series A1–A6 + Class B + Class C
# as columns). Exercises the structure-agnostic parse (#397): the old
# single-letter regex collapsed all six A-series onto one "Class A".
_SOL_LION_TRANCHE_TABLE_MD = """\
# Sol-Lion II RMBS Fondo de Titulización — Notes

|                  | Series A1   | Series A2   | Series A3   | Series A4   | Series A5   | Series A6   | Class B     | Class C   |
|------------------|-------------|-------------|-------------|-------------|-------------|-------------|-------------|-----------|
| Principal Amount | €200,000,000| €180,000,000| €160,000,000| €140,000,000| €120,000,000| €100,000,000| €53,100,000 | €10,500,000|
| Expected Ratings | AAA         | AAA         | AAA         | AAA         | AA          | AA          | A           | NR        |

## Conditions of the Notes
Some following text.
"""


# A single-class deal (one Class A note only), laid out class-per-row so the
# parse does not depend on a multi-column header.
_SINGLE_CLASS_TRANCHE_TABLE_MD = """\
# Single-Class Deal — Notes

| Class   | Principal Amount | Rating |
|---------|------------------|--------|
| Class A | €500,000,000     | AAA    |

## Conditions of the Notes
Some following text.
"""


# A row-per-class table containing a junk "Class O" cell (the artifact source):
# a non-tranche label with a stray "42" nearby. The real classes A/B carry real
# sizes; "Class O" must be dropped (#397).
_CLASS_O_ARTIFACT_TABLE_MD = """\
# Deal With Junk Row — Notes

| Class   | Amount        |
|---------|---------------|
| Class A | €1,000,000,000|
| Class B | €53,100,000   |
| Class O | 42            |

## Conditions of the Notes
Some following text.
"""


def _waterfall_with_series_recipients() -> dict:
    """A waterfall referencing series_a1…a6 + class_b/class_c recipients."""
    steps = [
        _make_step(priority="(a)", recipient="series_a1_notes_redemption"),
        _make_step(priority="(b)", recipient="series_a2_notes_redemption"),
        _make_step(priority="(c)", recipient="series_a3_notes_redemption"),
        _make_step(priority="(d)", recipient="series_a4_notes_redemption"),
        _make_step(priority="(e)", recipient="series_a5_notes_redemption"),
        _make_step(priority="(f)", recipient="series_a6_notes_redemption"),
        _make_step(priority="(g)", recipient="class_b_notes_redemption"),
        _make_step(priority="(h)", recipient="class_c_notes_redemption"),
    ]
    wf = ExtractedWaterfall(
        deal_name=_DEAL_NAME,
        waterfall_type="post_enforcement",
        steps=steps,
        source_section="Post-Enforcement Priority of Payments",
        extraction_confidence=1.0,
    )
    return {"post_enforcement": wf}


class TestExtractTranches:
    def test_no_sources_returns_empty_list(self) -> None:
        assert _extract_tranches(None, None) == []
        assert _extract_tranches(None, {}) == []

    def test_parses_green_lion_tranche_table(self) -> None:
        section_map = route_sections(_GREEN_LION_TRANCHE_TABLE_MD)
        result = _extract_tranches(section_map)
        assert len(result) >= 3
        names = [t["name"] for t in result]
        assert names == ["Class A", "Class B", "Class C"]

    def test_tranche_sizes_correct(self) -> None:
        section_map = route_sections(_GREEN_LION_TRANCHE_TABLE_MD)
        by_name = {t["name"]: t for t in _extract_tranches(section_map)}
        assert by_name["Class A"]["size_eur"] == 1_000_000_000.0
        assert by_name["Class B"]["size_eur"] == 53_100_000.0
        assert by_name["Class C"]["size_eur"] == 10_500_000.0

    def test_tranche_ratings_and_rate(self) -> None:
        section_map = route_sections(_GREEN_LION_TRANCHE_TABLE_MD)
        by_name = {t["name"]: t for t in _extract_tranches(section_map)}
        assert by_name["Class A"]["rating"] == "AAA"
        assert "EURIBOR" in by_name["Class A"]["rate"]
        assert "0.43" in by_name["Class A"]["rate"]

    def test_tranche_has_required_keys(self) -> None:
        section_map = route_sections(_GREEN_LION_TRANCHE_TABLE_MD)
        for tranche in _extract_tranches(section_map):
            assert "name" in tranche
            assert "size_eur" in tranche
            assert "rating" in tranche
            assert "rate" in tranche
            assert "seniority" in tranche

    def test_seniority_order_senior_to_junior(self) -> None:
        section_map = route_sections(_GREEN_LION_TRANCHE_TABLE_MD)
        result = _extract_tranches(section_map)
        seniorities = [t["seniority"] for t in result]
        assert seniorities == sorted(seniorities)
        assert result[0]["name"] == "Class A"

    def test_table_preferred_over_waterfall(self) -> None:
        section_map = route_sections(_GREEN_LION_TRANCHE_TABLE_MD)
        result = _extract_tranches(section_map, _waterfall_with_class_recipients())
        # Table wins → sizes are populated (waterfall fallback can't supply them).
        assert all(t["size_eur"] is not None for t in result)

    def test_falls_back_to_waterfall_when_no_table(self) -> None:
        section_map = route_sections("# Definitions\nNo table here.\n")
        result = _extract_tranches(section_map, _waterfall_with_class_recipients())
        names = [t["name"] for t in result]
        assert names == ["Class A", "Class B", "Class C"]
        # Fallback has no sizes/ratings.
        assert all(t["size_eur"] is None for t in result)

    def test_waterfall_only_fallback(self) -> None:
        result = _extract_tranches(None, _waterfall_with_class_recipients())
        assert [t["name"] for t in result] == ["Class A", "Class B", "Class C"]

    # --- #397: structure-agnostic / exotic-stack parsing ------------------

    def test_multi_series_columns_stay_distinct(self) -> None:
        """Series A1–A6 + B + C parse as 8 distinct tranches, senior→junior."""
        section_map = route_sections(_SOL_LION_TRANCHE_TABLE_MD)
        result = _extract_tranches(section_map)
        names = [t["name"] for t in result]
        assert names == [
            "Class A1", "Class A2", "Class A3", "Class A4",
            "Class A5", "Class A6", "Class B", "Class C",
        ]
        # Series digit is the sub-order: A1 < A2 < … < A6 < B < C.
        seniorities = [t["seniority"] for t in result]
        assert seniorities == sorted(seniorities)
        by_name = {t["name"]: t for t in result}
        assert by_name["Class A1"]["size_eur"] == 200_000_000.0
        assert by_name["Class A6"]["size_eur"] == 100_000_000.0
        assert by_name["Class B"]["size_eur"] == 53_100_000.0
        assert by_name["Class C"]["size_eur"] == 10_500_000.0

    def test_single_class_deal(self) -> None:
        """A one-class (Class A only) deal yields exactly that tranche."""
        section_map = route_sections(_SINGLE_CLASS_TRANCHE_TABLE_MD)
        result = _extract_tranches(section_map)
        assert [t["name"] for t in result] == ["Class A"]
        assert result[0]["size_eur"] == 500_000_000.0

    def test_class_o_artifact_dropped(self) -> None:
        """A stray non-tranche 'Class O' row never becomes a phantom tranche."""
        section_map = route_sections(_CLASS_O_ARTIFACT_TABLE_MD)
        result = _extract_tranches(section_map)
        names = [t["name"] for t in result]
        assert "Class O" not in names
        # The real classes survive.
        assert names == ["Class A", "Class B"]
        # And specifically no phantom 42-EUR tranche.
        assert all(t["size_eur"] != 42.0 for t in result)

    def test_waterfall_fallback_multi_series(self) -> None:
        """series_a1…a6 + class_b/class_c recipients map to the full stack."""
        result = _extract_tranches(None, _waterfall_with_series_recipients())
        names = [t["name"] for t in result]
        assert names == [
            "Class A1", "Class A2", "Class A3", "Class A4",
            "Class A5", "Class A6", "Class B", "Class C",
        ]
        # Fallback supplies no sizes.
        assert all(t["size_eur"] is None for t in result)


# ---------------------------------------------------------------------------
# Unit tests — completeness score
# ---------------------------------------------------------------------------


class TestCompletenessScore:
    """Verify the *real* completeness coverage metric (``_completeness_score``).

    The old metric was the fraction of expected section headers found, which
    could read 1.0 even when every waterfall section was empty. The new score
    blends section coverage, populated-waterfall coverage (≥1 step), trigger
    presence and tranche presence — so a header-only-but-empty model scores
    strictly below 1.0.
    """

    _ALL_SECTIONS = [
        "definitions",
        "revenue_priority_of_payments",
        "conditions_of_notes",
        "available_funds",
    ]

    @staticmethod
    def _wf(n_steps: int):
        """A stub waterfall object exposing ``.steps`` of the given length."""

        class _WF:
            steps = list(range(n_steps))

        return _WF()

    @staticmethod
    def _covenants(n_triggers: int):
        class _Cov:
            triggers = list(range(n_triggers))

        return _Cov()

    def test_fully_populated_model_scores_high(self) -> None:
        score = _completeness_score(
            sections_found=self._ALL_SECTIONS,
            waterfalls={"revenue": self._wf(8), "redemption": self._wf(5)},
            covenants=self._covenants(3),
            tranche_structure=[{"name": "A"}, {"name": "B"}],
        )
        assert score == pytest.approx(1.0)

    def test_empty_model_scores_zero(self) -> None:
        score = _completeness_score(
            sections_found=[],
            waterfalls={},
            covenants=self._covenants(0),
            tranche_structure=[],
        )
        assert score == 0.0

    def test_all_headers_but_zero_steps_scores_below_one(self) -> None:
        # The headline bug: every section header found, but the waterfalls
        # extracted ZERO steps. The old metric scored 1.0; the new one must not.
        score = _completeness_score(
            sections_found=self._ALL_SECTIONS,
            waterfalls={"revenue": self._wf(0), "redemption": self._wf(0)},
            covenants=self._covenants(0),
            tranche_structure=[],
        )
        # Sections fully present (0.30) but nothing else → well below 1.0.
        assert score < 1.0
        assert score == pytest.approx(0.30)

    def test_steps_present_raises_score_over_headers_only(self) -> None:
        headers_only = _completeness_score(
            sections_found=self._ALL_SECTIONS,
            waterfalls={"revenue": self._wf(0), "redemption": self._wf(0)},
            covenants=self._covenants(0),
            tranche_structure=[],
        )
        with_steps = _completeness_score(
            sections_found=self._ALL_SECTIONS,
            waterfalls={"revenue": self._wf(6), "redemption": self._wf(4)},
            covenants=self._covenants(0),
            tranche_structure=[],
        )
        assert with_steps > headers_only

    def test_score_stays_in_unit_interval(self) -> None:
        score = _completeness_score(
            sections_found=self._ALL_SECTIONS + ["some_other_section"],
            waterfalls={"revenue": self._wf(8), "redemption": self._wf(5)},
            covenants=self._covenants(5),
            tranche_structure=[{"name": "A"}],
        )
        assert 0.0 <= score <= 1.0
        assert score == pytest.approx(1.0)

    def test_accepts_dict_shaped_waterfalls(self) -> None:
        # The helper tolerates already-dumped waterfall dicts (``{"steps": [...]}``)
        # as well as ExtractedWaterfall objects.
        score = _completeness_score(
            sections_found=self._ALL_SECTIONS,
            waterfalls={"revenue": {"steps": [1, 2]}, "redemption": {"steps": []}},
            covenants=self._covenants(1),
            tranche_structure=[{"name": "A"}],
        )
        # sections 0.30 + half waterfalls 0.20 + triggers 0.15 + tranches 0.15
        assert score == pytest.approx(0.80)


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
# Unit tests — definitions linking into the assembled model (#395)
# ---------------------------------------------------------------------------


class TestDefinitionsLinking:
    """The #395 link: extract_deal_model attaches the resolved defined terms onto
    each serialised waterfall step's condition (``condition_terms``) and each
    trigger's metric/display-name (``metric_terms``).

    Exercises the real assembly + serialisation path (extract_deal_model →
    _apply_definitions_links); only the three sub-extractors and the
    download/convert boundary are stubbed.
    """

    @staticmethod
    def _run(cache_dir: str) -> DealModel:
        mock_markdown = "# Definitions\nx\n## Revenue Priority of Payments\ny"

        # Two defined terms; only the trigger one is referenced by the step
        # condition below.
        seq_term = MagicMock()
        seq_term.definition = "Sequential Pay Trigger switches to sequential."
        seq_term.page_or_section = "Section 9.1"
        pdl_term = MagicMock()
        pdl_term.definition = "PDL Debit Balance is the principal deficiency."
        pdl_term.page_or_section = "Section 9.1"

        fake_defs_graph = MagicMock()
        fake_defs_graph.terms = {
            "Sequential Pay Trigger": seq_term,
            "PDL Debit Balance": pdl_term,
        }

        # A waterfall whose step condition references the defined trigger by name.
        conditional_step = _make_step(priority="(g)", recipient="class_b_interest")
        conditional_step = conditional_step.model_copy(
            update={"condition": "while the Sequential Pay Trigger is in effect"}
        )
        unconditional_step = _make_step(priority="(a)", recipient="senior_fees")
        fake_waterfall = ExtractedWaterfall(
            deal_name=_DEAL_NAME,
            waterfall_type="revenue",
            steps=[unconditional_step, conditional_step],
            source_section="Section 5.2",
            extraction_confidence=1.0,
        )

        fake_trigger = MagicMock()
        fake_trigger.name = "sequential_pay_trigger"

        fake_covenants = MagicMock()
        fake_covenants.model_dump.return_value = {
            "deal_name": _DEAL_NAME,
            "triggers": [
                {
                    "name": "sequential_pay_trigger",
                    # snake_case slug metric that won't lexically match a
                    # capitalised term — the display_name fallback must catch it.
                    "metric": "pdl_debit_balance",
                    "display_name": "PDL Debit Balance",
                }
            ],
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
            return_value={"revenue": fake_waterfall},
        ), patch(
            "loanwhiz.extraction.assembler.extract_covenants",
            return_value=fake_covenants,
        ):
            return extract_deal_model(
                prospectus_url=_PROSPECTUS_URL,
                deal_name=_DEAL_NAME,
                cache_dir=cache_dir,
            )

    def test_step_condition_links_to_defined_term(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            model = self._run(tmpdir)
            steps = model.waterfalls["revenue"]["steps"]
            # Unconditional step → empty link.
            assert steps[0]["condition_terms"] == []
            # Conditional step → linked to the Sequential Pay Trigger.
            assert steps[1]["condition_terms"] == ["Sequential Pay Trigger"]

    def test_trigger_metric_links_via_display_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            model = self._run(tmpdir)
            trig = model.covenants["triggers"][0]
            # The snake_case metric won't match, but the display name resolves.
            assert trig["metric_terms"] == ["PDL Debit Balance"]

    def test_empty_graph_links_to_empty_lists(self) -> None:
        """With no defined terms, every link is an empty list (serialise unchanged
        apart from the new keys)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_markdown = "# Definitions\nx\n## Revenue Priority of Payments\ny"
            empty_graph = MagicMock()
            empty_graph.terms = {}
            step = _make_step(priority="(g)", recipient="class_b_interest").model_copy(
                update={"condition": "while the Sequential Pay Trigger is in effect"}
            )
            wf = ExtractedWaterfall(
                deal_name=_DEAL_NAME,
                waterfall_type="revenue",
                steps=[step],
                source_section="Section 5.2",
                extraction_confidence=1.0,
            )
            cov = MagicMock()
            cov.model_dump.return_value = {
                "deal_name": _DEAL_NAME,
                "triggers": [{"name": "t", "metric": "m", "display_name": "M"}],
                "issuer_covenants": [],
                "extraction_confidence": 0.0,
            }
            cov.triggers = []
            with patch(
                "loanwhiz.extraction.assembler._download_and_convert",
                return_value=mock_markdown,
            ), patch(
                "loanwhiz.extraction.assembler.extract_definitions",
                return_value=empty_graph,
            ), patch(
                "loanwhiz.extraction.assembler.extract_all_waterfalls",
                return_value={"revenue": wf},
            ), patch(
                "loanwhiz.extraction.assembler.extract_covenants",
                return_value=cov,
            ):
                model = extract_deal_model(
                    prospectus_url=_PROSPECTUS_URL,
                    deal_name=_DEAL_NAME,
                    cache_dir=tmpdir,
                )
            assert model.waterfalls["revenue"]["steps"][0]["condition_terms"] == []
            assert model.covenants["triggers"][0]["metric_terms"] == []


# ---------------------------------------------------------------------------
# Unit tests — language-agnostic section wiring (#274)
# ---------------------------------------------------------------------------


class TestExtractDealModelLanguageAgnostic:
    """extract_deal_model wires the LLM section router (classify_segments_llm)
    behind the keyword router and threads the resolved sections into the
    sub-extractors — the #274 fix. The genai boundary is the only thing stubbed.
    """

    # A non-English (Italian) markdown whose load-bearing headings the English
    # keyword router cannot match — so without the LLM fallback the definitions
    # extractor raises and the waterfalls come back empty (the original defect).
    _NON_ENGLISH_MD = (
        "# Definizioni\n"
        '"Fondi Disponibili" indica la somma dei seguenti importi.\n'
        "# Ordine di Priorità dei Pagamenti — Interessi\n"
        "- (a) commissioni del fiduciario;\n- (b) interessi Classe A.\n"
        "# Ordine di Priorità dei Pagamenti — Capitale\n"
        "- (a) capitale Classe A.\n"
        "# Priorità Post-Escussione\n"
        "- (a) commissioni del fiduciario.\n"
    )

    def test_llm_fallback_threads_resolved_sections_to_extractors(self) -> None:
        from loanwhiz.extraction import section_router

        captured: dict = {}

        def _fake_classify(section_map, **_kwargs):
            by_title = {s.title: s for s in section_map.sections}
            return {
                "definitions": by_title["Definizioni"],
                "revenue_priority_of_payments": by_title[
                    "Ordine di Priorità dei Pagamenti — Interessi"
                ],
                "redemption_priority_of_payments": by_title[
                    "Ordine di Priorità dei Pagamenti — Capitale"
                ],
                "post_enforcement_priority": by_title["Priorità Post-Escussione"],
            }

        fake_defs_graph = MagicMock()
        fake_defs_graph.terms = {}

        fake_covenants = MagicMock()
        fake_covenants.model_dump.return_value = {
            "deal_name": "Leone",
            "triggers": [],
            "issuer_covenants": [],
            "extraction_confidence": 0.0,
        }
        fake_covenants.triggers = []

        def _capture_defs(section_map, **kwargs):
            captured["defs_section"] = kwargs.get("section")
            return fake_defs_graph

        def _capture_wf(section_map, definitions, **kwargs):
            captured["wf_sections"] = kwargs.get("sections")
            return {"revenue": _make_waterfall("revenue", n_steps=2)}

        def _capture_cov(section_map, definitions, **kwargs):
            captured["cov_extra"] = kwargs.get("extra_sections")
            return fake_covenants

        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            section_router, "classify_segments_llm", _fake_classify
        ), patch(
            "loanwhiz.extraction.assembler._download_and_convert",
            return_value=self._NON_ENGLISH_MD,
        ), patch(
            "loanwhiz.extraction.assembler.extract_definitions",
            side_effect=_capture_defs,
        ), patch(
            "loanwhiz.extraction.assembler.extract_all_waterfalls",
            side_effect=_capture_wf,
        ), patch(
            "loanwhiz.extraction.assembler.extract_covenants",
            side_effect=_capture_cov,
        ):
            extract_deal_model(
                prospectus_url="https://example.com/leone.pdf",
                deal_name="Leone Arancio RMBS 2023-1 S.r.l.",
                cache_dir=tmpdir,
            )

        # Definitions extractor received the LLM-located Italian definitions
        # section (so it won't raise ValueError on the missing English heading).
        assert captured["defs_section"] is not None
        assert captured["defs_section"].title == "Definizioni"

        # Waterfall extractor received the resolved sections for all three types.
        wf_sections = captured["wf_sections"]
        assert wf_sections is not None
        assert set(wf_sections) == {"revenue", "redemption", "post_enforcement"}
        assert wf_sections["revenue"].title.startswith("Ordine di Priorità")

        # Covenant extractor received the resolved PoP/triggers spans.
        cov_extra = captured["cov_extra"]
        assert cov_extra and any(
            sec.title == "Ordine di Priorità dei Pagamenti — Interessi"
            for _label, sec in cov_extra
        )


# ---------------------------------------------------------------------------
# Unit tests — durable cache location (#132)
# ---------------------------------------------------------------------------


class TestDurableCacheLocation:
    """The default cache dirs must be the repo's durable data/ tree.

    The invariant is *location-relative-to-the-module*, NOT an absolute-path
    predicate: the cache dirs are derived from the assembler module's own
    location (``Path(__file__).resolve().parents[3] / "data" / ...``), so they
    must resolve under the repo's committed ``data/`` tree wherever the repo is
    checked out — including a checkout rooted under ``/tmp`` (the promotion
    build-verify path). The old assertions ``not str(...).startswith("/tmp/")``
    conflated "is not the retired ``/tmp/loanwhiz_cache`` literal" with "the
    absolute path must not begin with ``/tmp/``", so they false-failed on any
    ``/tmp``-rooted checkout even though the code was correct (#389). These
    tests assert the real, location-independent invariant instead.
    """

    @staticmethod
    def _expected_repo_data_root() -> Path:
        """The repo ``data/`` dir derived from the assembler module's location.

        Mirrors how :mod:`loanwhiz.extraction.assembler` derives ``_REPO_ROOT``
        (``src/loanwhiz/extraction/assembler.py`` → ``parents[3]`` is the repo
        root), so the assertion tracks the code regardless of checkout path.
        """
        from loanwhiz.extraction import assembler

        return Path(assembler.__file__).resolve().parents[3] / "data"

    def test_deal_cache_default_is_data_deals(self) -> None:
        assert DEFAULT_DEAL_CACHE_DIR.parts[-2:] == ("data", "deals")
        # Resolves under the repo's own committed data/ tree (module-relative),
        # independent of where the repo is checked out.
        assert DEFAULT_DEAL_CACHE_DIR == self._expected_repo_data_root() / "deals"
        # Must NOT be the old ephemeral /tmp/loanwhiz_cache location (the literal
        # that was retired in #132) — a *substring* check, not an absolute prefix.
        assert "loanwhiz_cache" not in str(DEFAULT_DEAL_CACHE_DIR)

    def test_docling_cache_default_is_data_docling_cache(self) -> None:
        assert DEFAULT_DOCLING_CACHE_DIR.parts[-2:] == ("data", "docling_cache")
        assert (
            DEFAULT_DOCLING_CACHE_DIR
            == self._expected_repo_data_root() / "docling_cache"
        )
        assert "loanwhiz_cache" not in str(DEFAULT_DOCLING_CACHE_DIR)

    def test_deal_and_docling_caches_share_repo_data_root(self) -> None:
        # Both resolve under the same committed repo data/ directory.
        assert DEFAULT_DEAL_CACHE_DIR.parent == DEFAULT_DOCLING_CACHE_DIR.parent
        assert DEFAULT_DEAL_CACHE_DIR.parent == self._expected_repo_data_root()
        assert DEFAULT_DEAL_CACHE_DIR.parent.name == "data"

    def test_default_cache_dir_signature_uses_constant(self) -> None:
        """extract_deal_model's default cache_dir resolves to the durable path."""
        import inspect

        sig = inspect.signature(extract_deal_model)
        # The signature default is the constant verbatim — so it tracks the
        # module-relative invariant above and never hardcodes an absolute path.
        assert sig.parameters["cache_dir"].default == str(DEFAULT_DEAL_CACHE_DIR)
        assert "loanwhiz_cache" not in sig.parameters["cache_dir"].default


# ---------------------------------------------------------------------------
# Unit tests — Docling markdown cache (#132)
# ---------------------------------------------------------------------------


class TestDoclingMarkdownCache:
    """_download_and_convert reuses cached OCR markdown instead of re-running."""

    def test_cache_path_keyed_by_url_hash(self) -> None:
        p1 = _docling_cache_path(_PROSPECTUS_URL, "/some/dir")
        p2 = _docling_cache_path(_PROSPECTUS_URL, "/some/dir")
        p3 = _docling_cache_path("https://example.com/other.pdf", "/some/dir")
        # Same URL → same path; different URL → different path.
        assert p1 == p2
        assert p1 != p3
        assert p1.suffix == ".md"
        assert p1.parent == Path("/some/dir")

    def test_cache_hit_skips_docling_conversion(self) -> None:
        """When the markdown cache exists, no download/Docling is invoked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = _docling_cache_path(_PROSPECTUS_URL, tmpdir)
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text("# Cached markdown\nbody", encoding="utf-8")

            # Patch the lazy imports so a real download/Docling would blow up.
            with patch.dict(
                "sys.modules",
                {
                    "requests": MagicMock(),
                    "docling": MagicMock(),
                    "docling.document_converter": MagicMock(),
                },
            ):
                import sys

                result = _download_and_convert(_PROSPECTUS_URL, cache_dir=tmpdir)
                # Docling's DocumentConverter must never have been called.
                conv = sys.modules["docling.document_converter"].DocumentConverter
                conv.assert_not_called()

            assert result == "# Cached markdown\nbody"

    def test_force_refresh_busts_markdown_cache(self) -> None:
        """force_refresh=True ignores the cached markdown and re-converts."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = _docling_cache_path(_PROSPECTUS_URL, tmpdir)
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text("# Stale markdown", encoding="utf-8")

            fake_requests = MagicMock()
            fake_requests.get.return_value = MagicMock(
                content=b"%PDF-1.4 fake", raise_for_status=lambda: None
            )
            fake_converter_mod = MagicMock()
            fake_doc = MagicMock()
            fake_doc.document.export_to_markdown.return_value = "# Fresh markdown"
            fake_converter_mod.DocumentConverter.return_value.convert.return_value = (
                fake_doc
            )

            with patch.dict(
                "sys.modules",
                {
                    "requests": fake_requests,
                    "docling": MagicMock(),
                    "docling.document_converter": fake_converter_mod,
                },
            ):
                result = _download_and_convert(
                    _PROSPECTUS_URL, cache_dir=tmpdir, force_refresh=True
                )
                fake_converter_mod.DocumentConverter.assert_called_once()

            # Fresh result returned AND written back to the cache.
            assert result == "# Fresh markdown"
            assert cache_file.read_text(encoding="utf-8") == "# Fresh markdown"

    def test_cache_miss_writes_markdown(self) -> None:
        """On a cold cache, the converted markdown is persisted for reuse."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = _docling_cache_path(_PROSPECTUS_URL, tmpdir)
            assert not cache_file.exists()

            fake_requests = MagicMock()
            fake_requests.get.return_value = MagicMock(
                content=b"%PDF-1.4 fake", raise_for_status=lambda: None
            )
            fake_converter_mod = MagicMock()
            fake_doc = MagicMock()
            fake_doc.document.export_to_markdown.return_value = "# Converted"
            fake_converter_mod.DocumentConverter.return_value.convert.return_value = (
                fake_doc
            )

            with patch.dict(
                "sys.modules",
                {
                    "requests": fake_requests,
                    "docling": MagicMock(),
                    "docling.document_converter": fake_converter_mod,
                },
            ):
                result = _download_and_convert(_PROSPECTUS_URL, cache_dir=tmpdir)

            assert result == "# Converted"
            assert cache_file.read_text(encoding="utf-8") == "# Converted"


# ---------------------------------------------------------------------------
# Unit tests — force_refresh propagation to sub-extractors (#132)
# ---------------------------------------------------------------------------


class TestForceRefreshPropagation:
    """extract_deal_model(force_refresh=True) must bust the sub-extractor caches.

    The #125 revenue-waterfall fix was masked on re-warm because the waterfall
    extractor served its own stale disk cache.  force_refresh must reach every
    sub-extractor (definitions / waterfalls / covenants) and the Docling cache.
    """

    @staticmethod
    def _common_mocks() -> dict:
        fake_defined_term = MagicMock()
        fake_defined_term.definition = "x"
        fake_defined_term.page_or_section = "Section 9.1"
        fake_defs_graph = MagicMock()
        fake_defs_graph.terms = {"Term": fake_defined_term}

        fake_waterfalls = {"revenue": _make_waterfall("revenue", n_steps=2)}

        fake_covenants = MagicMock()
        fake_covenants.model_dump.return_value = {
            "deal_name": "Test",
            "triggers": [],
            "issuer_covenants": [],
            "extraction_confidence": 0.0,
        }
        fake_covenants.triggers = []
        return {
            "defs": fake_defs_graph,
            "waterfalls": fake_waterfalls,
            "covenants": fake_covenants,
        }

    def test_force_refresh_propagates_to_all_sub_extractors(self) -> None:
        mocks = self._common_mocks()
        with tempfile.TemporaryDirectory() as tmpdir:
            # Seed a stale deal-model cache so force_refresh must override it.
            cache_file = Path(tmpdir) / "green-lion-2026-1-bv.json"
            cache_file.write_text(
                _make_minimal_deal_model(cache_path=str(cache_file)).model_dump_json(
                    indent=2
                ),
                encoding="utf-8",
            )

            with patch(
                "loanwhiz.extraction.assembler._download_and_convert",
                return_value="# Definitions\ntext\n## Revenue Priority of Payments\nx",
            ) as mock_dl, patch(
                "loanwhiz.extraction.assembler.extract_definitions",
                return_value=mocks["defs"],
            ) as mock_defs, patch(
                "loanwhiz.extraction.assembler.extract_all_waterfalls",
                return_value=mocks["waterfalls"],
            ) as mock_wf, patch(
                "loanwhiz.extraction.assembler.extract_covenants",
                return_value=mocks["covenants"],
            ) as mock_cov:
                extract_deal_model(
                    prospectus_url=_PROSPECTUS_URL,
                    deal_name=_DEAL_NAME,
                    cache_dir=tmpdir,
                    docling_cache_dir=tmpdir,
                    force_refresh=True,
                )

            # Docling download/convert called with force_refresh=True.
            assert mock_dl.call_args.kwargs.get("force_refresh") is True
            # Each sub-extractor called with force_refresh=True.
            assert mock_defs.call_args.kwargs.get("force_refresh") is True
            assert mock_wf.call_args.kwargs.get("force_refresh") is True
            assert mock_cov.call_args.kwargs.get("force_refresh") is True

    def test_no_force_refresh_passes_false_to_sub_extractors(self) -> None:
        mocks = self._common_mocks()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "loanwhiz.extraction.assembler._download_and_convert",
                return_value="# Definitions\ntext\n## Revenue Priority of Payments\nx",
            ) as mock_dl, patch(
                "loanwhiz.extraction.assembler.extract_definitions",
                return_value=mocks["defs"],
            ) as mock_defs, patch(
                "loanwhiz.extraction.assembler.extract_all_waterfalls",
                return_value=mocks["waterfalls"],
            ) as mock_wf, patch(
                "loanwhiz.extraction.assembler.extract_covenants",
                return_value=mocks["covenants"],
            ) as mock_cov:
                extract_deal_model(
                    prospectus_url=_PROSPECTUS_URL,
                    deal_name=_DEAL_NAME,
                    cache_dir=tmpdir,
                    docling_cache_dir=tmpdir,
                )

            assert mock_dl.call_args.kwargs.get("force_refresh") is False
            assert mock_defs.call_args.kwargs.get("force_refresh") is False
            assert mock_wf.call_args.kwargs.get("force_refresh") is False
            assert mock_cov.call_args.kwargs.get("force_refresh") is False


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

    def test_tranche_structure_is_note_classes(self, model: DealModel) -> None:
        assert isinstance(model.tranche_structure, list)
        # Green Lion has Class A/B/C — expect at least three tranches.
        assert len(model.tranche_structure) >= 3
        tranche = model.tranche_structure[0]
        assert "name" in tranche
        assert "size_eur" in tranche
        assert "rating" in tranche
        assert "rate" in tranche
        assert "seniority" in tranche
        names = [t["name"] for t in model.tranche_structure]
        assert any("Class A" in n for n in names)

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


# ===========================================================================
# Canonical DealRules assembly (#273) — build_deal_rules + tranche/section paths
# ===========================================================================


def _gl_seed_deal_model() -> "DealModel":
    """Load the committed Green Lion 2026-1 seed into a DealModel (no network)."""
    import json as _json
    from pathlib import Path as _Path

    seed = (
        _Path(__file__).resolve().parents[1]
        / "src/loanwhiz/data/deals/seed/green-lion-2026-1-bv.json"
    )
    return DealModel.model_validate(_json.loads(seed.read_text(encoding="utf-8")))


class TestBuildDealRules:
    """build_deal_rules maps an extracted DealModel onto canonical DealRules."""

    def test_gl_seed_builds_valid_deal_rules(self) -> None:
        from loanwhiz.domain.rules import DealRules, RecipientType
        from loanwhiz.extraction.assembler import build_deal_rules

        model = _gl_seed_deal_model()
        result = build_deal_rules(model, jurisdiction="Netherlands", use_llm=False)
        rules = result.output
        assert isinstance(rules, DealRules)

        rev = rules.waterfalls["revenue"]
        red = rules.waterfalls["redemption"]
        assert len(rev) >= 1 and len(red) >= 1

        # The standard noteholder / reserve / fee steps all map onto the canonical
        # taxonomy (no unmapped) — the executable core. The GL revolving-period
        # "initial purchase price of new mortgage receivables" step has no
        # canonical recipient and degrades honestly to unmapped (the design's
        # explicit escape), so the revenue waterfall (all standard steps) is the
        # one asserted fully-mapped.
        for step in rev:
            assert step.recipient != RecipientType.unmapped, step.priority_label
        # Redemption is mostly mapped; at least the class principal steps are.
        red_recipients = {s.recipient for s in red}
        assert RecipientType.class_a_principal in red_recipients
        assert RecipientType.class_b_principal in red_recipients

        # The amount basis is bound from the mapped recipient.
        a_int = next(s for s in rev if s.recipient == RecipientType.class_a_interest)
        assert a_int.amount.basis == "interest_accrual"
        assert a_int.amount.raw_text  # prose retained for audit

    def test_gl_seed_tranches_become_tranche_rules(self) -> None:
        from loanwhiz.extraction.assembler import build_deal_rules

        rules = build_deal_rules(_gl_seed_deal_model(), use_llm=False).output
        names = {t.name for t in rules.tranches}
        assert {"Class A", "Class B", "Class C"} <= names
        class_a = next(t for t in rules.tranches if t.name == "Class A")
        assert class_a.seniority == 0
        assert class_a.original_balance == 1_000_000_000.0
        # "3m EURIBOR + 0.43" parses to a floating rate with a bps margin.
        assert class_a.rate.kind == "floating"
        assert class_a.rate.index == "EURIBOR_3M"
        assert class_a.rate.margin_bps == pytest.approx(43.0)

    def test_gl_seed_triggers_map_to_metric_type(self) -> None:
        from loanwhiz.domain.rules import MetricType
        from loanwhiz.extraction.assembler import build_deal_rules

        rules = build_deal_rules(_gl_seed_deal_model(), use_llm=False).output
        assert len(rules.triggers) >= 1
        metrics = {t.metric for t in rules.triggers}
        # The seeded GL PDL triggers map onto the canonical PDL metric.
        assert MetricType.class_a_pdl in metrics
        # threshold_unit is normalised to the canonical enum on every trigger.
        for t in rules.triggers:
            assert t.threshold_unit in {"percent", "fraction", "bps", "eur"}

    def test_completeness_is_field_based(self) -> None:
        from loanwhiz.extraction.assembler import build_deal_rules

        rules = build_deal_rules(_gl_seed_deal_model(), use_llm=False).output
        # Field-based completeness equals compute_completeness() (not a header count).
        assert rules.completeness == rules.compute_completeness()
        # GL has tranches, an evaluable revenue step, redemption steps, and
        # quantified triggers → completeness is high (≥ 0.6 of the 5 checks).
        assert rules.completeness >= 0.6

    def test_unmapped_step_does_not_lift_completeness(self) -> None:
        from loanwhiz.domain.rules import RecipientType
        from loanwhiz.extraction.assembler import build_deal_rules

        model = _gl_seed_deal_model()
        # Inject an exotic recipient the taxonomy cannot map; offline → unmapped.
        model.waterfalls["revenue"]["steps"].append(
            _make_step(
                priority="(z)",
                recipient="exotic_equity_kicker_distribution",
                description="Pay the exotic equity kicker.",
            ).model_dump()
        )
        rules = build_deal_rules(model, use_llm=False).output
        unmapped = [
            s for s in rules.waterfalls["revenue"]
            if s.recipient == RecipientType.unmapped
        ]
        assert len(unmapped) == 1
        # The unmapped step is report_supplied, prose retained, never executed.
        assert unmapped[0].amount.basis == "report_supplied"
        assert unmapped[0].amount.raw_text

    def test_per_field_provenance_and_primitive_result_envelope(self) -> None:
        from loanwhiz.domain.provenance import FieldProvenance
        from loanwhiz.extraction.assembler import build_deal_rules

        result = build_deal_rules(_gl_seed_deal_model(), use_llm=False)
        rules = result.output
        # Per-field provenance is populated for steps and triggers.
        assert rules.provenance, "provenance map should not be empty"
        assert any(k.startswith("waterfalls.revenue.") for k in rules.provenance)
        assert any(k.startswith("triggers.") for k in rules.provenance)
        for fp in rules.provenance.values():
            assert isinstance(fp, FieldProvenance)
            assert 0.0 <= fp.confidence <= 1.0
        # Governed envelope: confidence + citations + audit travel with the result.
        assert 0.0 <= result.confidence <= 1.0
        assert result.citations  # GL steps carry citations
        assert result.audit_entry.primitive_name == (
            "prospectus_extractor.build_deal_rules"
        )

    def test_condition_becomes_condition_ref(self) -> None:
        from loanwhiz.extraction.assembler import build_deal_rules

        model = _gl_seed_deal_model()
        injected_label = model.waterfalls["redemption"]["steps"][1]["priority"]
        model.waterfalls["redemption"]["steps"][1]["condition"] = (
            "if the Sequential Pay Trigger is not in effect"
        )
        rules = build_deal_rules(model, use_llm=False).output
        # The injected "...is not in effect" condition becomes a not_breached gate.
        step = next(
            s for s in rules.waterfalls["redemption"]
            if s.priority_label == injected_label
        )
        assert step.condition is not None
        assert step.condition.when == "not_breached"


class TestClassifySegmentsLlm:
    """The LLM-semantic section classifier degrades safely and maps indices."""

    def test_returns_all_none_on_empty_section_map(self) -> None:
        from loanwhiz.extraction.section_router import (
            classify_segments_llm,
            route_sections,
        )

        result = classify_segments_llm(route_sections(""))
        assert all(v is None for v in result.values())

    def test_maps_llm_indices_to_sections(self) -> None:
        from unittest.mock import MagicMock, patch

        from loanwhiz.extraction.section_router import (
            classify_segments_llm,
            route_sections,
        )

        md = (
            "# Definizioni\nText A\n"
            "# Ordine di priorità dei pagamenti correnti\nText B\n"
            "# Ordine di priorità dei rimborsi\nText C\n"
        )
        section_map = route_sections(md)

        # Stub the Gemini client to return a role→index mapping (Italian titles).
        fake_response = MagicMock()
        fake_response.text = (
            '{"definitions": 0, "revenue_priority_of_payments": 1, '
            '"redemption_priority_of_payments": 2, "post_enforcement_priority": -1, '
            '"triggers_covenants": -1, "tranche_table": -1}'
        )
        fake_client = MagicMock()
        fake_client.models.generate_content.return_value = fake_response

        with patch("google.genai.Client", return_value=fake_client):
            result = classify_segments_llm(section_map)

        assert result["definitions"].title == "Definizioni"
        assert result["revenue_priority_of_payments"].title.startswith("Ordine")
        assert result["redemption_priority_of_payments"].title.startswith("Ordine")
        assert result["post_enforcement_priority"] is None
