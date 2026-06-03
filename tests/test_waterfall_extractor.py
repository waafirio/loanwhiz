"""Tests for loanwhiz.extraction.waterfall_extractor.

Two test layers:

1. Unit tests — synthetic data, no network, no LLM.  These validate
   ``WaterfallStep``, ``ExtractedWaterfall``, cache round-trip, and the
   helper functions using in-memory fixtures.  They run in plain ``pytest``
   with no external dependencies.

2. Integration tests (``@pytest.mark.integration``) — use a cached waterfall
   JSON (written by a prior Gemini extraction run against the real Green Lion
   prospectus) and assert real extraction properties.  These tests:
   - Load from cache if available at ``_CACHE_PATH_REVENUE``.
   - Skip automatically when the cache is absent AND Gemini credentials are
     not available (so they are safe in CI without network/GCP access).

   Run: ``pytest -m integration tests/test_waterfall_extractor.py``
   Skip in CI: ``pytest -m 'not integration'``
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from loanwhiz.extraction.waterfall_extractor import (
    ExtractedWaterfall,
    WaterfallStep,
    _cache_path_for,
    _waterfall_from_dict,
    _waterfall_to_dict,
    extract_all_waterfalls,
    extract_waterfall,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CACHE_DIR = Path("/tmp/loanwhiz_cache")
_CACHE_PATH_REVENUE = _CACHE_DIR / "waterfall_Green_Lion_2026_1_B_V__revenue.json"
_CACHE_PATH_REDEMPTION = _CACHE_DIR / "waterfall_Green_Lion_2026_1_B_V__redemption.json"

_DEAL_NAME = "Green Lion 2026-1 B.V."
_PROSPECTUS_URL = (
    "https://huggingface.co/datasets/Algoritmica/green-lion-2026"
    "/resolve/main/Hackathon_Data/green-lion-2026-1-prospectus.pdf"
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_step(
    priority: str = "(a)",
    recipient: str = "security_trustee_fees",
    description: str = "Pay security trustee fees.",
    amount_formula: str = "as accrued and unpaid",
    condition: str | None = None,
    is_pari_passu: bool = False,
    citation: dict | None = None,
) -> WaterfallStep:
    return WaterfallStep(
        priority=priority,
        recipient=recipient,
        description=description,
        amount_formula=amount_formula,
        condition=condition,
        is_pari_passu=is_pari_passu,
        citation=citation or {
            "document": "Green Lion 2026-1 Prospectus",
            "page_or_row": "Section 5.2(a)",
            "excerpt": "Firstly, to pay the security trustee fees.",
        },
    )


def _make_waterfall(
    waterfall_type: str = "revenue",
    n_steps: int = 11,
    deal_name: str = _DEAL_NAME,
) -> ExtractedWaterfall:
    """Build a synthetic ExtractedWaterfall with n_steps sequential steps."""
    labels = [f"({chr(ord('a') + i)})" for i in range(n_steps)]
    steps = [_make_step(priority=lbl, recipient=f"recipient_{lbl[1]}") for lbl in labels]
    return ExtractedWaterfall(
        deal_name=deal_name,
        waterfall_type=waterfall_type,
        steps=steps,
        source_section="Section 5.2",
        extraction_confidence=1.0,
    )


def _write_cache(waterfall: ExtractedWaterfall, path: Path) -> None:
    """Write a waterfall to a cache file for test setup."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_waterfall_to_dict(waterfall), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Unit tests — WaterfallStep
# ---------------------------------------------------------------------------


class TestWaterfallStep:
    """WaterfallStep is a Pydantic model — validate field types and defaults."""

    def test_basic_fields(self) -> None:
        step = _make_step()
        assert step.priority == "(a)"
        assert step.recipient == "security_trustee_fees"
        assert step.condition is None
        assert step.is_pari_passu is False
        assert isinstance(step.citation, dict)

    def test_condition_none_when_unconditional(self) -> None:
        step = _make_step(condition=None)
        assert step.condition is None

    def test_condition_set(self) -> None:
        step = _make_step(condition="if Sequential Pay Trigger is not in effect")
        assert "Sequential Pay Trigger" in step.condition

    def test_is_pari_passu_true(self) -> None:
        step = _make_step(is_pari_passu=True)
        assert step.is_pari_passu is True

    def test_citation_has_required_keys(self) -> None:
        step = _make_step()
        assert "document" in step.citation
        assert "page_or_row" in step.citation
        assert "excerpt" in step.citation


# ---------------------------------------------------------------------------
# Unit tests — ExtractedWaterfall
# ---------------------------------------------------------------------------


class TestExtractedWaterfall:
    """ExtractedWaterfall is a Pydantic model — validate fields and structure."""

    def test_fields(self) -> None:
        w = _make_waterfall()
        assert w.deal_name == _DEAL_NAME
        assert w.waterfall_type == "revenue"
        assert len(w.steps) == 11
        assert isinstance(w.extraction_confidence, float)

    def test_steps_ordered(self) -> None:
        w = _make_waterfall(n_steps=5)
        priorities = [s.priority for s in w.steps]
        assert priorities == ["(a)", "(b)", "(c)", "(d)", "(e)"]

    def test_confidence_between_0_and_1(self) -> None:
        w = _make_waterfall()
        assert 0.0 <= w.extraction_confidence <= 1.0


# ---------------------------------------------------------------------------
# Unit tests — cache round-trip
# ---------------------------------------------------------------------------


class TestCacheRoundTrip:
    """_waterfall_to_dict / _waterfall_from_dict round-trip."""

    def test_round_trip_preserves_steps(self) -> None:
        original = _make_waterfall(n_steps=11)
        data = _waterfall_to_dict(original)
        restored = _waterfall_from_dict(data)

        assert len(restored.steps) == 11
        assert restored.deal_name == original.deal_name
        assert restored.waterfall_type == original.waterfall_type
        assert restored.source_section == original.source_section
        assert restored.extraction_confidence == original.extraction_confidence

    def test_round_trip_step_fields(self) -> None:
        original = _make_waterfall(n_steps=3)
        data = _waterfall_to_dict(original)
        restored = _waterfall_from_dict(data)

        for orig_step, rest_step in zip(original.steps, restored.steps):
            assert orig_step.priority == rest_step.priority
            assert orig_step.recipient == rest_step.recipient
            assert orig_step.condition == rest_step.condition
            assert orig_step.is_pari_passu == rest_step.is_pari_passu

    def test_round_trip_condition_none(self) -> None:
        step = _make_step(condition=None)
        w = ExtractedWaterfall(
            deal_name="test",
            waterfall_type="revenue",
            steps=[step],
            source_section="Section 5.2",
            extraction_confidence=1.0,
        )
        data = _waterfall_to_dict(w)
        restored = _waterfall_from_dict(data)
        assert restored.steps[0].condition is None

    def test_round_trip_condition_non_none(self) -> None:
        step = _make_step(condition="if trigger is active")
        w = ExtractedWaterfall(
            deal_name="test",
            waterfall_type="revenue",
            steps=[step],
            source_section="Section 5.2",
            extraction_confidence=1.0,
        )
        data = _waterfall_to_dict(w)
        restored = _waterfall_from_dict(data)
        assert restored.steps[0].condition == "if trigger is active"

    def test_empty_steps_round_trip(self) -> None:
        w = ExtractedWaterfall(
            deal_name="empty",
            waterfall_type="revenue",
            steps=[],
            source_section="Section 5.2",
            extraction_confidence=0.0,
        )
        data = _waterfall_to_dict(w)
        restored = _waterfall_from_dict(data)
        assert len(restored.steps) == 0

    def test_file_cache_read_skips_gemini(self) -> None:
        """If a cache file exists, extract_waterfall must NOT call Gemini."""
        synthetic = _make_waterfall(n_steps=11)

        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = Path(tmpdir) / "waterfall_test_revenue.json"
            _write_cache(synthetic, cache_file)

            # Patch genai.Client to detect if Gemini is called.
            with patch("loanwhiz.extraction.waterfall_extractor.genai.Client") as mock_client:
                # Build a minimal SectionMap stub and DefinitionsGraph stub.
                section_map = MagicMock()
                definitions = MagicMock()

                result = extract_waterfall(
                    section_map=section_map,
                    definitions=definitions,
                    waterfall_type="revenue",
                    deal_name="test",
                    cache_path=str(cache_file),
                )

                # Gemini client must NOT have been instantiated.
                mock_client.assert_not_called()

            assert len(result.steps) == 11
            assert result.waterfall_type == "revenue"


class TestCachePathFor:
    """_cache_path_for helper."""

    def test_explicit_path(self) -> None:
        result = _cache_path_for("MyDeal", "revenue", "/tmp/custom.json")
        assert result == Path("/tmp/custom.json")

    def test_auto_path_contains_deal_and_type(self) -> None:
        result = _cache_path_for("Green Lion 2026-1 B.V.", "revenue", None)
        assert "revenue" in result.name
        assert result.parent == _CACHE_DIR


# ---------------------------------------------------------------------------
# Unit tests — extract_all_waterfalls (skips missing sections gracefully)
# ---------------------------------------------------------------------------


class TestExtractAllWaterfalls:
    """extract_all_waterfalls returns only waterfall types whose sections exist."""

    def test_returns_only_found_sections(self) -> None:
        """When all sections raise ValueError (not found), the result is empty."""
        section_map = MagicMock()
        definitions = MagicMock()

        with tempfile.TemporaryDirectory() as tmpdir:
            # No cache files; sections not found → ValueError → skip.
            with patch(
                "loanwhiz.extraction.waterfall_extractor.extract_waterfall"
            ) as mock_extract:
                mock_extract.side_effect = ValueError("section not found")
                result = extract_all_waterfalls(
                    section_map=section_map,
                    definitions=definitions,
                    deal_name="test",
                    cache_dir=tmpdir,
                )
                assert result == {}

    def test_returns_all_three_when_all_succeed(self) -> None:
        """When all sections succeed, all three keys are present."""
        section_map = MagicMock()
        definitions = MagicMock()

        def _fake_extract(
            section_map, definitions, waterfall_type, deal_name, cache_path=None
        ):
            return _make_waterfall(waterfall_type=waterfall_type)

        with patch(
            "loanwhiz.extraction.waterfall_extractor.extract_waterfall",
            side_effect=_fake_extract,
        ):
            result = extract_all_waterfalls(
                section_map=section_map,
                definitions=definitions,
                deal_name="test",
            )

        assert set(result.keys()) == {"revenue", "redemption", "post_enforcement"}

    def test_partial_sections_found(self) -> None:
        """When only revenue is found, only revenue is in the result."""
        section_map = MagicMock()
        definitions = MagicMock()

        def _fake_extract(
            section_map, definitions, waterfall_type, deal_name, cache_path=None
        ):
            if waterfall_type == "revenue":
                return _make_waterfall(waterfall_type="revenue")
            raise ValueError("not found")

        with patch(
            "loanwhiz.extraction.waterfall_extractor.extract_waterfall",
            side_effect=_fake_extract,
        ):
            result = extract_all_waterfalls(
                section_map=section_map,
                definitions=definitions,
                deal_name="test",
            )

        assert list(result.keys()) == ["revenue"]


# ---------------------------------------------------------------------------
# Integration tests — require cached waterfall JSON (or Gemini credentials)
# ---------------------------------------------------------------------------


def _load_or_extract_revenue() -> ExtractedWaterfall | None:
    """Load the Green Lion revenue waterfall from cache or via Gemini.

    Returns None on any failure so callers can skip the test.
    """
    if _CACHE_PATH_REVENUE.exists():
        try:
            data = json.loads(_CACHE_PATH_REVENUE.read_text(encoding="utf-8"))
            return _waterfall_from_dict(data)
        except Exception:
            return None

    # No cache — attempt a live extraction.
    try:
        from loanwhiz.extraction.definitions_graph import load_or_extract
        from loanwhiz.extraction.section_router import route_sections

        import httpx
        from docling.document_converter import DocumentConverter

        pdf_path = _CACHE_DIR / "green_lion_2026_1_prospectus.pdf"
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)

        if not pdf_path.exists():
            with httpx.Client(follow_redirects=True, timeout=120) as client:
                resp = client.get(_PROSPECTUS_URL)
                resp.raise_for_status()
            pdf_path.write_bytes(resp.content)

        converter = DocumentConverter()
        result = converter.convert(str(pdf_path))
        markdown = result.document.export_to_markdown()
        section_map = route_sections(markdown)

        defs_cache = _CACHE_DIR / "definitions_green_lion_2026_1_prospectus.json"
        definitions = load_or_extract(_PROSPECTUS_URL, cache_path=str(defs_cache))

        return extract_waterfall(
            section_map=section_map,
            definitions=definitions,
            waterfall_type="revenue",
            deal_name=_DEAL_NAME,
            cache_path=str(_CACHE_PATH_REVENUE),
        )
    except Exception:
        return None


def _load_or_extract_redemption() -> ExtractedWaterfall | None:
    """Load the Green Lion redemption waterfall from cache or via Gemini."""
    if _CACHE_PATH_REDEMPTION.exists():
        try:
            data = json.loads(_CACHE_PATH_REDEMPTION.read_text(encoding="utf-8"))
            return _waterfall_from_dict(data)
        except Exception:
            return None
    return None


@pytest.mark.integration
class TestGreenLionRevenueWaterfall:
    """Integration tests against the real Green Lion 2026-1 revenue waterfall.

    Run: ``pytest -m integration tests/test_waterfall_extractor.py``
    Skip in CI: ``pytest -m 'not integration'``
    """

    @pytest.fixture(scope="class")
    def waterfall(self) -> ExtractedWaterfall:
        w = _load_or_extract_revenue()
        if w is None:
            pytest.skip(
                "Green Lion revenue waterfall unavailable "
                "(no cache, no network, or Gemini credentials absent)"
            )
        return w

    def test_revenue_waterfall_has_11_steps(self, waterfall: ExtractedWaterfall) -> None:
        """Green Lion 2026-1 Section 5.2 has exactly 11 payment steps (a)–(k)."""
        assert len(waterfall.steps) == 11, (
            f"Expected 11 steps, got {len(waterfall.steps)}. "
            f"Steps: {[s.priority for s in waterfall.steps]}"
        )

    def test_step_d_recipient_contains_class_a_and_interest(
        self, waterfall: ExtractedWaterfall
    ) -> None:
        """Step (d) pays Class A interest — recipient must contain 'class_a' and 'interest'."""
        step_d = next(
            (s for s in waterfall.steps if s.priority.strip().lower() == "(d)"), None
        )
        assert step_d is not None, "Step (d) not found in revenue waterfall"
        recipient_lower = step_d.recipient.lower()
        assert "class_a" in recipient_lower or "class a" in recipient_lower, (
            f"Step (d) recipient {step_d.recipient!r} does not contain 'class_a'"
        )
        assert "interest" in recipient_lower, (
            f"Step (d) recipient {step_d.recipient!r} does not contain 'interest'"
        )

    def test_step_k_is_present(self, waterfall: ExtractedWaterfall) -> None:
        """Step (k) (deferred purchase price / residual) must be present."""
        step_k = next(
            (s for s in waterfall.steps if s.priority.strip().lower() == "(k)"), None
        )
        assert step_k is not None, "Step (k) not found in revenue waterfall"

    def test_extraction_confidence_above_threshold(
        self, waterfall: ExtractedWaterfall
    ) -> None:
        """extraction_confidence must exceed 0.8 (most steps have non-empty recipients)."""
        assert waterfall.extraction_confidence > 0.8, (
            f"Expected extraction_confidence > 0.8, got {waterfall.extraction_confidence}"
        )

    def test_waterfall_type_is_revenue(self, waterfall: ExtractedWaterfall) -> None:
        assert waterfall.waterfall_type == "revenue"

    def test_steps_have_non_empty_priorities(self, waterfall: ExtractedWaterfall) -> None:
        for step in waterfall.steps:
            assert step.priority, f"Step has empty priority: {step}"

    def test_steps_have_non_empty_descriptions(self, waterfall: ExtractedWaterfall) -> None:
        for step in waterfall.steps:
            assert step.description, f"Step {step.priority} has empty description"

    def test_steps_have_citations(self, waterfall: ExtractedWaterfall) -> None:
        for step in waterfall.steps:
            assert isinstance(step.citation, dict), (
                f"Step {step.priority} citation is not a dict"
            )
            assert step.citation, f"Step {step.priority} has empty citation dict"

    def test_cache_file_exists_after_extraction(
        self, waterfall: ExtractedWaterfall
    ) -> None:
        """After loading, the cache file must exist."""
        assert _CACHE_PATH_REVENUE.exists(), (
            f"Cache file not found at {_CACHE_PATH_REVENUE}"
        )

    def test_cache_reload_returns_same_step_count(
        self, waterfall: ExtractedWaterfall
    ) -> None:
        """Loading from cache a second time returns the same number of steps."""
        if not _CACHE_PATH_REVENUE.exists():
            pytest.skip("Cache not available")
        data = json.loads(_CACHE_PATH_REVENUE.read_text(encoding="utf-8"))
        reloaded = _waterfall_from_dict(data)
        assert len(reloaded.steps) == len(waterfall.steps)


@pytest.mark.integration
class TestGreenLionRedemptionWaterfall:
    """Integration tests for the Green Lion 2026-1 redemption waterfall."""

    @pytest.fixture(scope="class")
    def waterfall(self) -> ExtractedWaterfall:
        w = _load_or_extract_redemption()
        if w is None:
            pytest.skip(
                "Green Lion redemption waterfall unavailable "
                "(no cache available; run the revenue integration test first "
                "to build the extraction pipeline, then run redemption)"
            )
        return w

    def test_redemption_has_steps(self, waterfall: ExtractedWaterfall) -> None:
        assert len(waterfall.steps) > 0, "Redemption waterfall must have at least one step"

    def test_redemption_has_class_a_principal(self, waterfall: ExtractedWaterfall) -> None:
        """Redemption waterfall must include Class A principal repayment."""
        recipients = " ".join(s.recipient.lower() for s in waterfall.steps)
        descriptions = " ".join(s.description.lower() for s in waterfall.steps)
        combined = recipients + " " + descriptions
        assert "class" in combined and ("a" in combined or "class_a" in combined), (
            "Expected Class A principal reference in redemption waterfall"
        )

    def test_redemption_has_class_b_reference(self, waterfall: ExtractedWaterfall) -> None:
        """Redemption waterfall must include Class B reference."""
        recipients = " ".join(s.recipient.lower() for s in waterfall.steps)
        descriptions = " ".join(s.description.lower() for s in waterfall.steps)
        combined = recipients + " " + descriptions
        assert "class_b" in combined or "class b" in combined, (
            "Expected Class B reference in redemption waterfall"
        )

    def test_redemption_confidence_above_threshold(
        self, waterfall: ExtractedWaterfall
    ) -> None:
        assert waterfall.extraction_confidence > 0.8
