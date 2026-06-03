"""Smoke tests for the Green Lion demo script (``demo/run_green_lion.py``).

Two categories:
1. Unit tests — no network access. Import the demo module, exercise the pure
   formatting helpers, the static section map / fast-mode extraction path, and
   the proximity-marker logic. These guard against import regressions and the
   stub-removal contract (no ``[issue #N in progress]`` text remains).
2. Integration test — marked ``@pytest.mark.slow``; runs ``main(fast=True)``
   end to end against the live HuggingFace tapes and asserts all 8 sections
   produce output with no stubs.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import the demo module by path (it lives outside the package, in demo/).
# ---------------------------------------------------------------------------

_DEMO_PATH = Path(__file__).resolve().parent.parent / "demo" / "run_green_lion.py"


def _load_demo_module():
    spec = importlib.util.spec_from_file_location("run_green_lion", _DEMO_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_green_lion"] = module
    spec.loader.exec_module(module)
    return module


demo = _load_demo_module()


# ---------------------------------------------------------------------------
# Unit tests — no network
# ---------------------------------------------------------------------------


def test_demo_imports_and_exposes_sections():
    """The demo module imports and exposes all eight section functions."""
    for fn in (
        "section_deal_context",
        "section_esma_analytics",
        "section_prospectus_extraction",
        "section_waterfall_execution",
        "section_investor_report_verification",
        "section_covenant_monitor",
        "section_cashflow_projection",
        "section_nlq",
        "main",
    ):
        assert callable(getattr(demo, fn)), f"{fn} should be callable"


def test_source_has_no_stub_tags():
    """No ``[issue #N in progress]`` stub tags remain in the demo source."""
    source = _DEMO_PATH.read_text(encoding="utf-8")
    assert "in progress]" not in source
    assert "[issue #" not in source


def test_fmt_helpers():
    """EUR formatting helpers produce the expected M / B suffixes."""
    assert demo._fmt_eur(1_500_000_000) == "€1.50B"
    assert demo._fmt_eur(53_100_000) == "€53.1M"
    assert demo._m(9_050_000) == "€9.05m"


def test_static_section_map_present():
    """The fast-mode fallback section map references the 11-step waterfall."""
    assert "Revenue Priority of Payments" in demo._STATIC_SECTION_MAP
    assert "11 steps" in demo._STATIC_SECTION_MAP


def test_capital_structure_constants():
    """Capital structure exposes the Green Lion tranche balances + rate."""
    cs = demo.CAPITAL_STRUCTURE
    assert cs["class_a_balance"] == 1_000_000_000.0
    assert cs["class_b_balance"] == 53_100_000.0
    assert cs["class_c_balance"] == 10_500_000.0
    assert cs["class_a_rate_pct"] == pytest.approx(3.62)


def test_prospectus_extraction_fast_no_cache(tmp_path, monkeypatch, capsys):
    """Fast-mode section 3 prints the static map without touching the network."""
    # Point the deal cache at an empty temp dir so the "no cache" branch fires.
    import loanwhiz.extraction.assembler as assembler

    monkeypatch.setattr(
        assembler, "_slug", lambda name: "nonexistent-deal", raising=True
    )
    # Run with fast=True — must not raise and must print the static map.
    demo.section_prospectus_extraction(fast=True)
    out = capsys.readouterr().out
    assert "Revenue Priority of Payments" in out
    assert "Skipping live extraction" in out


def test_proximity_marker_logic():
    """🟢/🟡/🔴 marker reflects triggered / near-miss / safe states."""

    class _St:
        def __init__(self, is_triggered, threshold, proximity_pct):
            self.is_triggered = is_triggered
            self.threshold = threshold
            self.proximity_pct = proximity_pct

    assert demo._proximity_marker(_St(True, 1.5, 120.0)) == "🔴"
    assert demo._proximity_marker(_St(False, 1.5, 90.0)) == "🟡"
    assert demo._proximity_marker(_St(False, 1.5, 40.0)) == "🟢"
    # No threshold (e.g. PDL): not a near-miss → green when not triggered.
    assert demo._proximity_marker(_St(False, None, 0.0)) == "🟢"


# ---------------------------------------------------------------------------
# Integration — live tapes (slow)
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_full_fast_run(capsys):
    """``main(fast=True)`` runs all 8 sections live with no stubs, no crash."""
    demo.main(fast=True)
    out = capsys.readouterr().out
    # All eight section headers present.
    for header in (
        "1. DEAL CONTEXT",
        "2. ESMA TAPE ANALYTICS",
        "3. PROSPECTUS EXTRACTION",
        "4. WATERFALL EXECUTION",
        "5. INVESTOR REPORT VERIFICATION",
        "6. COVENANT MONITOR",
        "7. CASHFLOW PROJECTION",
        "8. NATURAL LANGUAGE Q&A",
        "DEMO COMPLETE",
    ):
        assert header in out, f"missing section: {header}"
    # No stub tags leaked into the output.
    assert "in progress]" not in out
    # Section 4 produced real computed distributions.
    assert "Per-tranche distributions" in out
    # Section 6 produced trigger status rows.
    assert "Trigger status by period" in out
