"""Tests for the Deal Overview tab (``clients/demo/tabs/overview.py``, issue #78).

Offline by design — no UI launch, no network. They verify:

1. ``render`` exists and is callable per the tab-plugin contract.
2. The tab builds without error inside a Gradio Blocks/Tab context with a
   warm :class:`DealState` (deal_model present), with a cold one
   (deal_model is None), and with the empty default.
3. The pure view helpers summarise the deal model correctly.

The module is imported by file path (mirroring ``tests/test_demo_shell.py``)
so it loads as the package module ``clients.demo.tabs.overview`` with the repo
root on ``sys.path``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import gradio as gr

# ---------------------------------------------------------------------------
# Imports: the shell (for DealState) and the overview tab module.
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = str(_REPO_ROOT / "src")
for _p in (str(_REPO_ROOT), _SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _import_shell():
    shell_path = _REPO_ROOT / "clients" / "demo" / "shell.py"
    spec = importlib.util.spec_from_file_location("demo_shell", shell_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


shell = _import_shell()
from clients.demo.tabs import overview  # noqa: E402
from loanwhiz.extraction.assembler import DealModel, DealModelMetadata  # noqa: E402


# ---------------------------------------------------------------------------
# Mock deal models / states
# ---------------------------------------------------------------------------


def _fake_deal_model() -> DealModel:
    """A real :class:`DealModel` with the fields the overview tab reads.

    A genuine ``DealModel`` (not a duck-typed stand-in) is required because
    ``DealState`` validates ``deal_model`` against this type.
    """
    return DealModel(
        metadata=DealModelMetadata(
            deal_name="Green Lion 2026-1 B.V.",
            prospectus_url="https://example.test/prospectus.pdf",
            extracted_at="2026-06-01T00:00:00Z",
            extraction_duration_sec=612.0,
            sections_found=["definitions", "revenue_priority_of_payments"],
            completeness_score=0.75,
            cache_path="/tmp/loanwhiz_cache/deals/green-lion-2026-1-bv.json",
        ),
        definitions={},
        waterfalls={
            "revenue": {
                "waterfall_type": "revenue",
                "steps": [
                    {"priority": "(a)", "recipient": "security_trustee_fees"},
                    {"priority": "(b)", "recipient": "servicer_fees"},
                    {"priority": "(c)", "recipient": "class_a_interest"},
                ],
            }
        },
        covenants={},
        tranche_structure=[
            {
                "priority": "(a)",
                "recipient": "security_trustee_fees",
                "description": "Security trustee fees and expenses",
                "waterfall_type": "revenue",
            },
            {
                "priority": "(c)",
                "recipient": "class_a_interest",
                "description": "Class A interest",
                "waterfall_type": "revenue",
            },
        ],
        trigger_names=["Arrears Trigger", "Pro Rata Trigger"],
    )


def _warm_state():
    return shell.DealState(
        deal_name="Green Lion 2026-1 B.V.",
        tapes=[
            {"period": "2026-01-31", "pool_balance_eur": 500_000_000.0},
            {"period": "2026-02-28", "pool_balance_eur": 495_000_000.0},
        ],
        deal_model=_fake_deal_model(),
        loaded=True,
    )


def _cold_state():
    return shell.DealState(
        deal_name="Green Lion 2026-1 B.V.",
        tapes=[{"period": "2026-02-28", "pool_balance_eur": 495_000_000.0}],
        deal_model=None,
        loaded=True,
        load_error="deal model not in extraction cache",
    )


# ---------------------------------------------------------------------------
# 1. render exists and is callable
# ---------------------------------------------------------------------------


def test_render_exists_and_callable():
    assert hasattr(overview, "render")
    assert callable(overview.render)


def test_registry_wires_overview_render():
    """The shell's Deal Overview entry uses the real overview render."""
    spec = shell.TAB_REGISTRY[0]
    assert spec.title == "Deal Overview"
    assert spec.render is overview.render


# ---------------------------------------------------------------------------
# 2. Tab builds without error for each state variant
# ---------------------------------------------------------------------------


def _build_tab(state_value):
    """Render the tab inside a Blocks/Tab context (no launch)."""
    with gr.Blocks():
        state = gr.State(state_value)
        with gr.Tabs():
            with gr.Tab("Deal Overview"):
                overview.render(state)


def test_tab_builds_with_warm_state():
    _build_tab(_warm_state())


def test_tab_builds_with_cold_state():
    _build_tab(_cold_state())


def test_tab_builds_with_empty_state():
    _build_tab(shell.DealState.empty())


# ---------------------------------------------------------------------------
# 3. View helpers
# ---------------------------------------------------------------------------


def test_revenue_step_count():
    assert overview._revenue_step_count(_fake_deal_model()) == 3


def test_tranche_rows():
    rows = overview._tranche_rows(_fake_deal_model())
    assert len(rows) == 2
    assert rows[0][0] == "(a)"
    assert rows[0][1] == "security_trustee_fees"


def test_warm_summary_mentions_prewarmed_and_completeness():
    md = overview._warm_summary_md(_warm_state())
    assert "extraction pre-warmed (cached)" in md
    assert "75%" in md  # completeness_score 0.75
    assert "Arrears Trigger" in md


def test_cold_summary_mentions_prewarm_and_cold_cost():
    md = overview._cold_summary_md(_cold_state())
    assert "not extracted" in md.lower()
    assert "pre-warm" in md.lower()
    assert "10 min" in md or "~10" in md
    # Still surfaces tape-derived facts.
    assert "Green Lion 2026-1 B.V." in md


def test_format_pool_balance_uses_latest_period():
    md = overview._format_pool_balance(_warm_state().tapes)
    assert md.startswith("€")
    assert "495,000,000" in md
