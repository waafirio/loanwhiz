"""Tests for ReportVerifier primitive.

Unit tests use mocked Gemini extraction so no API key is required.
The integration test (marked @pytest.mark.integration) makes a real
Gemini call against the April 2026 Green Lion investor report.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from loanwhiz.config import GREEN_LION
from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY
from loanwhiz.primitives.report_verifier import (
    ReportVerifier,
    ReportVerifierInput,
    ReportVerifierOutput,
    _CONFIDENCE_HIGH,
    _CONFIDENCE_LOW,
    _build_reported_figure,
    _build_summary,
    _period_slug,
)

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

_APRIL_REPORT_URL: str = next(
    r["url"]
    for r in GREEN_LION["investor_report_urls"]
    if r["period"] == "April 2026"
)

# Synthetic waterfall output dict that mirrors WaterfallOutput.model_dump()
# for a realistic Green Lion scenario.
_WATERFALL_OUTPUT: dict[str, Any] = {
    "reporting_period": "April 2026",
    "revenue_waterfall": [],
    "redemption_waterfall": [],
    "tranche_distributions": [
        {
            "tranche": "class_a",
            "interest_received": 9_050_000.0,
            "principal_received": 5_000_000.0,
            "total_received": 14_050_000.0,
            "opening_balance": 1_000_000_000.0,
            "closing_balance": 995_000_000.0,
        },
        {
            "tranche": "class_b",
            "interest_received": 0.0,
            "principal_received": 0.0,
            "total_received": 0.0,
            "opening_balance": 53_100_000.0,
            "closing_balance": 53_100_000.0,
        },
        {
            "tranche": "class_c",
            "interest_received": 0.0,
            "principal_received": 0.0,
            "total_received": 0.0,
            "opening_balance": 10_500_000.0,
            "closing_balance": 10_500_000.0,
        },
    ],
    "total_distributed": 14_050_000.0,
    "shortfall": 0.0,
    # Extra keys callers can add to enable reserve_fund_balance and pool_balance comparison.
    "reserve_fund_balance": 5_000_000.0,
    "pool_balance": 1_063_600_000.0,
}

# Reported figures that match the waterfall within 1% tolerance.
_MATCHING_REPORTED: dict[str, float] = {
    "class_a_interest_paid": 9_050_000.0,  # exact match
    "class_a_principal_paid": 5_000_000.0,  # exact match
    "reserve_fund_balance": 5_000_000.0,    # exact match
    "pool_balance": 1_063_600_000.0,        # exact match
    "total_collections": 14_050_000.0,      # exact match
}

# Reported figures where one item (class_a_interest_paid) is off by >1%.
_MISMATCHING_REPORTED: dict[str, float] = {
    **_MATCHING_REPORTED,
    "class_a_interest_paid": 9_500_000.0,  # ~4.97% off — mismatch
}


def _make_input(**overrides) -> ReportVerifierInput:
    """Build a ReportVerifierInput with sensible defaults."""
    defaults: dict[str, Any] = dict(
        investor_report_url=_APRIL_REPORT_URL,
        waterfall_output=_WATERFALL_OUTPUT,
        reporting_period="April 2026",
        tolerance_pct=1.0,
    )
    defaults.update(overrides)
    return ReportVerifierInput(**defaults)


def _mock_extract(reported: dict[str, float]):
    """Return a context manager that patches _extract_figures_with_gemini."""
    return patch(
        "loanwhiz.primitives.report_verifier._extract_figures_with_gemini",
        return_value=reported,
    )


@pytest.fixture
def verifier() -> ReportVerifier:
    return ReportVerifier()


@pytest.fixture(autouse=True)
def no_cache(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    """Redirect the cache directory to a fresh temp path for each test."""
    monkeypatch.setattr(
        "loanwhiz.primitives.report_verifier._CACHE_DIR",
        tmp_path / "loanwhiz_cache",
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_report_verifier_registered():
    """ReportVerifier must be discoverable from PRIMITIVE_REGISTRY."""
    reg = PRIMITIVE_REGISTRY.get("report_verifier")
    assert reg is not None
    assert reg.name == "report_verifier"
    assert reg.version == "0.1.0"
    assert "verification" in reg.tags
    assert "investor_report" in reg.tags


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------


class TestPeriodSlug:
    def test_lowercase_spaces_to_underscores(self):
        assert _period_slug("April 2026") == "april_2026"

    def test_already_lowercase(self):
        assert _period_slug("march_2026") == "march_2026"

    def test_special_chars_stripped(self):
        assert _period_slug("Q1 2026!") == "q1_2026"


class TestBuildReportedFigure:
    def test_exact_match(self):
        fig = _build_reported_figure("x", 100.0, 100.0, 1.0)
        assert fig.delta == pytest.approx(0.0)
        assert fig.delta_pct == pytest.approx(0.0)
        assert fig.match is True

    def test_within_tolerance_match(self):
        # 0.5% off — within 1% tolerance
        fig = _build_reported_figure("x", 100.5, 100.0, 1.0)
        assert fig.match is True
        assert fig.delta_pct == pytest.approx(0.5)

    def test_outside_tolerance_mismatch(self):
        # 2% off — outside 1% tolerance
        fig = _build_reported_figure("x", 102.0, 100.0, 1.0)
        assert fig.match is False
        assert fig.delta_pct == pytest.approx(2.0)

    def test_exactly_at_tolerance_is_mismatch(self):
        # Strict less-than: 1.0% is NOT within 1.0% tolerance.
        fig = _build_reported_figure("x", 101.0, 100.0, 1.0)
        assert fig.match is False

    def test_delta_sign(self):
        # Reported < computed → negative delta
        fig = _build_reported_figure("x", 90.0, 100.0, 1.0)
        assert fig.delta == pytest.approx(-10.0)
        assert fig.delta_pct == pytest.approx(-10.0)

    def test_zero_computed_zero_reported(self):
        # Both zero → treated as match
        fig = _build_reported_figure("x", 0.0, 0.0, 1.0)
        assert fig.match is True
        assert fig.delta_pct == pytest.approx(0.0)

    def test_zero_computed_nonzero_reported(self):
        # Division by zero case → sentinel 999.0 delta_pct → mismatch
        fig = _build_reported_figure("x", 100.0, 0.0, 1.0)
        assert fig.match is False
        assert fig.delta_pct == pytest.approx(999.0)
        # Verify JSON-serializability (no inf/nan → null issue)
        import json
        from loanwhiz.primitives.report_verifier import ReportedFigure
        rf = ReportedFigure(
            line_item="x",
            reported_value=100.0,
            computed_value=0.0,
            delta=100.0,
            delta_pct=fig.delta_pct,
            match=fig.match,
        )
        serialized = json.loads(rf.model_dump_json())
        assert serialized["delta_pct"] == pytest.approx(999.0)

    def test_tolerance_field_stored(self):
        fig = _build_reported_figure("x", 100.0, 100.0, 2.5)
        assert fig.tolerance_pct == pytest.approx(2.5)


class TestBuildSummary:
    def test_all_match(self):
        summary = _build_summary(5, 5, [], 1.0)
        assert "5/5" in summary
        assert "match" in summary.lower()
        assert "mismatch" not in summary.lower()

    def test_one_mismatch(self):
        mismatch = _build_reported_figure("class_a_interest_paid", 9_500_000.0, 9_050_000.0, 1.0)
        summary = _build_summary(5, 4, [mismatch], 1.0)
        assert "4/5" in summary
        assert "class_a_interest_paid" in summary
        assert "mismatch" in summary.lower()

    def test_tolerance_shown(self):
        summary = _build_summary(3, 3, [], 0.5)
        assert "0.5%" in summary


# ---------------------------------------------------------------------------
# Match within tolerance
# ---------------------------------------------------------------------------


class TestMatchWithinTolerance:
    """When reported figures are within tolerance, all items should match."""

    def test_all_match(self, verifier: ReportVerifier):
        with _mock_extract(_MATCHING_REPORTED):
            result = verifier.execute(_make_input())

        out = result.output
        assert out.figures_matched == out.figures_checked
        assert out.figures_mismatched == 0
        assert out.overall_match is True
        assert all(f.match for f in out.line_items)

    def test_reporting_period_preserved(self, verifier: ReportVerifier):
        with _mock_extract(_MATCHING_REPORTED):
            result = verifier.execute(_make_input())
        assert result.output.reporting_period == "April 2026"

    def test_summary_mentions_match(self, verifier: ReportVerifier):
        with _mock_extract(_MATCHING_REPORTED):
            result = verifier.execute(_make_input())
        assert "match" in result.output.summary.lower()

    def test_audit_entry_populated(self, verifier: ReportVerifier):
        with _mock_extract(_MATCHING_REPORTED):
            result = verifier.execute(_make_input())
        audit = result.audit_entry
        assert audit.primitive_name == "report_verifier"
        assert audit.version == "0.1.0"
        assert len(audit.input_hash) == 64
        assert audit.duration_ms >= 0.0

    def test_citations_present(self, verifier: ReportVerifier):
        with _mock_extract(_MATCHING_REPORTED):
            result = verifier.execute(_make_input())
        assert len(result.citations) >= 1


# ---------------------------------------------------------------------------
# Mismatch detection
# ---------------------------------------------------------------------------


class TestMismatchDetection:
    """When a reported figure is outside tolerance, it should be flagged."""

    def test_one_mismatch_detected(self, verifier: ReportVerifier):
        with _mock_extract(_MISMATCHING_REPORTED):
            result = verifier.execute(_make_input())

        out = result.output
        mismatch_items = [f for f in out.line_items if not f.match]
        assert len(mismatch_items) == 1
        assert mismatch_items[0].line_item == "class_a_interest_paid"

    def test_overall_match_false(self, verifier: ReportVerifier):
        with _mock_extract(_MISMATCHING_REPORTED):
            result = verifier.execute(_make_input())
        assert result.output.overall_match is False

    def test_figures_mismatched_count(self, verifier: ReportVerifier):
        with _mock_extract(_MISMATCHING_REPORTED):
            result = verifier.execute(_make_input())
        assert result.output.figures_mismatched == 1

    def test_delta_values_correct(self, verifier: ReportVerifier):
        with _mock_extract(_MISMATCHING_REPORTED):
            result = verifier.execute(_make_input())
        mismatch = next(
            f for f in result.output.line_items if f.line_item == "class_a_interest_paid"
        )
        assert mismatch.delta == pytest.approx(9_500_000.0 - 9_050_000.0)
        expected_pct = (9_500_000.0 - 9_050_000.0) / 9_050_000.0 * 100.0
        assert mismatch.delta_pct == pytest.approx(expected_pct, rel=1e-6)


# ---------------------------------------------------------------------------
# Summary generation
# ---------------------------------------------------------------------------


class TestSummaryGeneration:
    def test_summary_includes_counts(self, verifier: ReportVerifier):
        with _mock_extract(_MISMATCHING_REPORTED):
            result = verifier.execute(_make_input())
        out = result.output
        assert str(out.figures_matched) in out.summary
        assert str(out.figures_checked) in out.summary

    def test_summary_names_mismatch_item(self, verifier: ReportVerifier):
        with _mock_extract(_MISMATCHING_REPORTED):
            result = verifier.execute(_make_input())
        assert "class_a_interest_paid" in result.output.summary

    def test_all_match_summary_no_mismatch_word(self, verifier: ReportVerifier):
        with _mock_extract(_MATCHING_REPORTED):
            result = verifier.execute(_make_input())
        # When all match, the summary should not say "mismatch"
        assert "mismatch" not in result.output.summary.lower()


# ---------------------------------------------------------------------------
# Confidence scoring
# ---------------------------------------------------------------------------


class TestConfidenceScoring:
    def test_high_confidence_five_figures(self, verifier: ReportVerifier):
        with _mock_extract(_MATCHING_REPORTED):
            result = verifier.execute(_make_input())
        assert result.confidence == pytest.approx(_CONFIDENCE_HIGH)

    def test_high_confidence_exactly_three_figures(self, verifier: ReportVerifier):
        three_figures = {
            "class_a_interest_paid": 9_050_000.0,
            "class_a_principal_paid": 5_000_000.0,
            "total_collections": 14_050_000.0,
        }
        with _mock_extract(three_figures):
            result = verifier.execute(_make_input())
        assert result.output.figures_checked == 3
        assert result.confidence == pytest.approx(_CONFIDENCE_HIGH)

    def test_low_confidence_two_figures(self, verifier: ReportVerifier):
        two_figures = {
            "class_a_interest_paid": 9_050_000.0,
            "class_a_principal_paid": 5_000_000.0,
        }
        with _mock_extract(two_figures):
            result = verifier.execute(_make_input())
        assert result.output.figures_checked == 2
        assert result.confidence == pytest.approx(_CONFIDENCE_LOW)

    def test_low_confidence_zero_figures(self, verifier: ReportVerifier):
        with _mock_extract({}):
            result = verifier.execute(_make_input())
        assert result.output.figures_checked == 0
        assert result.confidence == pytest.approx(_CONFIDENCE_LOW)

    def test_low_confidence_one_figure(self, verifier: ReportVerifier):
        one_figure = {"class_a_interest_paid": 9_050_000.0}
        with _mock_extract(one_figure):
            result = verifier.execute(_make_input())
        assert result.confidence == pytest.approx(_CONFIDENCE_LOW)


# ---------------------------------------------------------------------------
# Gemini client construction (Vertex AI / ADC)
# ---------------------------------------------------------------------------


class TestGeminiClientConstruction:
    """The Gemini client must be built for Vertex AI so it works under ADC.

    Regression for #76: a bare ``genai.Client()`` defaults to the Gemini
    Developer API and requires ``GEMINI_API_KEY``, failing under Vertex/ADC.
    """

    def test_client_built_for_vertex(self):
        from loanwhiz.config import GCP_LOCATION, GCP_PROJECT, MODEL_FLASH
        from loanwhiz.primitives.report_verifier import _extract_figures_with_gemini

        mock_response = MagicMock()
        mock_response.text = json.dumps({"class_a_interest_paid": 9_050_000.0})

        with patch(
            "loanwhiz.primitives.report_verifier.genai.Client"
        ) as mock_client_cls:
            mock_client = mock_client_cls.return_value
            mock_client.models.generate_content.return_value = mock_response

            figures = _extract_figures_with_gemini(
                pdf_url=_APRIL_REPORT_URL,
                reporting_period="April 2026",
            )

        mock_client_cls.assert_called_once_with(
            vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION
        )
        # The configured model name is used, not a hardcoded/dev-API name.
        _, call_kwargs = mock_client.models.generate_content.call_args
        assert call_kwargs["model"] == MODEL_FLASH
        assert figures == {"class_a_interest_paid": 9_050_000.0}


# ---------------------------------------------------------------------------
# Caching behaviour
# ---------------------------------------------------------------------------


class TestCacheBehaviour:
    def test_cache_written_on_first_call(
        self,
        verifier: ReportVerifier,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        cache_dir = tmp_path / "loanwhiz_cache"
        monkeypatch.setattr("loanwhiz.primitives.report_verifier._CACHE_DIR", cache_dir)

        with _mock_extract(_MATCHING_REPORTED):
            verifier.execute(_make_input())

        cache_file = cache_dir / "report_april_2026.json"
        assert cache_file.exists()
        cached = json.loads(cache_file.read_text())
        assert "class_a_interest_paid" in cached

    def test_gemini_called_only_once_for_same_period(
        self,
        verifier: ReportVerifier,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        cache_dir = tmp_path / "loanwhiz_cache"
        monkeypatch.setattr("loanwhiz.primitives.report_verifier._CACHE_DIR", cache_dir)

        with patch(
            "loanwhiz.primitives.report_verifier._extract_figures_with_gemini",
            return_value=_MATCHING_REPORTED,
        ) as mock_gemini:
            verifier.execute(_make_input())  # first call — Gemini called
            verifier.execute(_make_input())  # second call — cache hit

        mock_gemini.assert_called_once()

    def test_second_call_uses_cached_values(
        self,
        verifier: ReportVerifier,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        cache_dir = tmp_path / "loanwhiz_cache"
        monkeypatch.setattr("loanwhiz.primitives.report_verifier._CACHE_DIR", cache_dir)

        with _mock_extract(_MATCHING_REPORTED):
            result1 = verifier.execute(_make_input())

        # Second call — no mock; Gemini would fail if called (not patched)
        # but the cache should be used instead.
        with patch(
            "loanwhiz.primitives.report_verifier._extract_figures_with_gemini",
            side_effect=AssertionError("Gemini should not be called on cache hit"),
        ):
            result2 = verifier.execute(_make_input())

        assert result1.output.figures_checked == result2.output.figures_checked
        assert result1.output.overall_match == result2.output.overall_match

    def test_different_periods_separate_caches(
        self,
        verifier: ReportVerifier,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ):
        cache_dir = tmp_path / "loanwhiz_cache"
        monkeypatch.setattr("loanwhiz.primitives.report_verifier._CACHE_DIR", cache_dir)

        march_url = next(
            r["url"]
            for r in GREEN_LION["investor_report_urls"]
            if r["period"] == "March 2026"
        )

        with patch(
            "loanwhiz.primitives.report_verifier._extract_figures_with_gemini",
            return_value=_MATCHING_REPORTED,
        ) as mock_gemini:
            verifier.execute(_make_input(reporting_period="April 2026"))
            verifier.execute(
                _make_input(
                    investor_report_url=march_url,
                    reporting_period="March 2026",
                )
            )

        # Two distinct periods → two Gemini calls
        assert mock_gemini.call_count == 2


# ---------------------------------------------------------------------------
# Custom tolerance
# ---------------------------------------------------------------------------


class TestCustomTolerance:
    def test_tighter_tolerance_causes_mismatch(self, verifier: ReportVerifier):
        # 0.5% off — within 1% but outside 0.1% tolerance
        slightly_off = {
            **_MATCHING_REPORTED,
            "class_a_interest_paid": 9_050_000.0 * 1.005,  # 0.5% off
        }
        with _mock_extract(slightly_off):
            result = verifier.execute(_make_input(tolerance_pct=0.1))
        mismatch = [f for f in result.output.line_items if not f.match]
        assert any(f.line_item == "class_a_interest_paid" for f in mismatch)

    def test_wider_tolerance_allows_match(self, verifier: ReportVerifier):
        # 4.97% off — outside 1% but within 5% tolerance
        with _mock_extract(_MISMATCHING_REPORTED):
            result = verifier.execute(_make_input(tolerance_pct=5.0))
        assert result.output.overall_match is True


# ---------------------------------------------------------------------------
# Overall_match semantics
# ---------------------------------------------------------------------------


class TestOverallMatch:
    def test_overall_match_all_items(self, verifier: ReportVerifier):
        with _mock_extract(_MATCHING_REPORTED):
            result = verifier.execute(_make_input())
        assert result.output.overall_match is True
        assert result.output.overall_match == all(
            f.match for f in result.output.line_items
        )

    def test_overall_match_false_if_any_mismatch(self, verifier: ReportVerifier):
        with _mock_extract(_MISMATCHING_REPORTED):
            result = verifier.execute(_make_input())
        assert result.output.overall_match is False

    def test_overall_match_true_when_no_figures_extracted(self, verifier: ReportVerifier):
        # all() on an empty sequence is True — document this edge case.
        with _mock_extract({}):
            result = verifier.execute(_make_input())
        assert result.output.line_items == []
        assert result.output.overall_match is True


# ---------------------------------------------------------------------------
# Integration test — real Gemini call
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_integration_april_report():
    """Real Gemini extraction from the April 2026 Green Lion investor report.

    Requires a valid GOOGLE_API_KEY or GOOGLE_APPLICATION_CREDENTIALS
    in the environment.  Run with: pytest -m integration tests/test_report_verifier.py
    """
    verifier = ReportVerifier()
    inp = ReportVerifierInput(
        investor_report_url=_APRIL_REPORT_URL,
        waterfall_output=_WATERFALL_OUTPUT,
        reporting_period="April 2026",
        tolerance_pct=1.0,
    )
    result = verifier.execute(inp)

    out = result.output
    assert out.reporting_period == "April 2026"
    assert out.figures_checked >= 1, (
        "Expected at least 1 figure to be extracted from the investor report PDF"
    )
    assert isinstance(out.overall_match, bool)
    assert len(out.summary) > 0
    assert result.audit_entry.primitive_name == "report_verifier"
    # Confidence depends on how many figures were extracted
    assert result.confidence in (_CONFIDENCE_HIGH, _CONFIDENCE_LOW)
