"""Tests for the LoanWhiz unified demo shell (``clients/demo/shell.py``).

The shell is the foundation of the Demo UI epic: it defines the tab-plugin
contract and assembles the tabs + docked chat into one ``gr.Blocks``. These
tests verify the contract surface without launching any UI or hitting the
network:

1. ``build_app()`` returns a ``gr.Blocks``.
2. ``DealState`` constructs (empty and from data).
3. The tab registry exists with the five expected titles in narrative order.
4. The contract functions exist (stub renderer, chat handler, render contract).
5. ``DealState.load_green_lion`` is cache-aware — it never triggers a cold
   extraction and degrades gracefully on a cache miss / disabled tape load.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import gradio as gr

# ---------------------------------------------------------------------------
# Import the shell module by path (mirrors tests/test_dashboard.py).
# ---------------------------------------------------------------------------


def _import_shell():
    """Import ``clients/demo/shell.py`` as a module."""
    repo_root = Path(__file__).resolve().parent.parent
    src_path = str(repo_root / "src")
    if src_path not in sys.path:
        sys.path.insert(0, src_path)

    shell_path = repo_root / "clients" / "demo" / "shell.py"
    spec = importlib.util.spec_from_file_location("demo_shell", shell_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


shell = _import_shell()


# ---------------------------------------------------------------------------
# 1. build_app returns a gr.Blocks
# ---------------------------------------------------------------------------


def test_build_app_returns_blocks():
    """build_app() returns a gradio.Blocks without launching."""
    app = shell.build_app()
    assert isinstance(app, gr.Blocks), f"expected gr.Blocks, got {type(app)}"


# ---------------------------------------------------------------------------
# 2. DealState constructs
# ---------------------------------------------------------------------------


def test_dealstate_empty():
    """DealState.empty() yields an unloaded, empty state."""
    state = shell.DealState.empty()
    assert state.deal_name == ""
    assert state.tapes == []
    assert state.deal_model is None
    assert state.loaded is False
    assert state.load_error is None


def test_dealstate_from_data():
    """DealState constructs from explicit field values."""
    state = shell.DealState(
        deal_name="Green Lion 2026-1 B.V.",
        tapes=[{"period": "2026-02-28", "pool_balance_eur": 1.0}],
        deal_model=None,
        loaded=True,
    )
    assert state.deal_name == "Green Lion 2026-1 B.V."
    assert len(state.tapes) == 1
    assert state.loaded is True


# ---------------------------------------------------------------------------
# 3. Tab registry: five tabs in narrative order
# ---------------------------------------------------------------------------


def test_tab_registry_titles_and_order():
    """TAB_REGISTRY holds the five tabs in the epic's narrative order."""
    titles = [spec.title for spec in shell.TAB_REGISTRY]
    assert titles == [
        "Deal Overview",
        "Pool & Performance",
        "Waterfall",
        "Compliance & Covenants",
        "Cashflow Projection",
    ]


def test_tab_registry_entries_are_callable():
    """Each registry entry exposes a callable render(state)."""
    for spec in shell.TAB_REGISTRY:
        assert callable(spec.render), f"{spec.title} render is not callable"


# ---------------------------------------------------------------------------
# 4. Contract functions exist
# ---------------------------------------------------------------------------


def test_contract_surface_exists():
    """The shell exposes the documented contract symbols."""
    for name in (
        "DealState",
        "TabSpec",
        "TAB_REGISTRY",
        "build_app",
        "_stub_render",
        "_chat_stub_respond",
    ):
        assert hasattr(shell, name), f"missing contract symbol: {name}"


def test_stub_render_is_callable_factory():
    """_stub_render returns a callable render(state) (the placeholder)."""
    render = shell._stub_render(78, "Deal Overview")
    assert callable(render)


def test_chat_stub_appends_messages_format():
    """The stub chat handler returns messages-format history with a reply."""
    out = shell._chat_stub_respond("hello", [])
    assert isinstance(out, list)
    assert out[0] == {"role": "user", "content": "hello"}
    assert out[1]["role"] == "assistant"
    assert out[1]["content"]  # non-empty stub reply


# ---------------------------------------------------------------------------
# 5. Cache-aware loader — no cold extraction, graceful degradation
# ---------------------------------------------------------------------------


def test_load_green_lion_cache_miss_no_extraction(monkeypatch, tmp_path):
    """A cache miss leaves deal_model=None and never runs cold extraction.

    Points the loader at an empty cache directory and asserts it returns None
    for the model with an explanatory load_error, without calling
    extract_deal_model. Tapes are disabled to keep the test offline.
    """
    import loanwhiz.extraction.assembler as assembler

    # Guard: cache-aware path must NOT call extract_deal_model at all.
    def _boom(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("cache_aware loader must not trigger extraction")

    monkeypatch.setattr(assembler, "extract_deal_model", _boom)

    # An empty fixture cache dir guarantees a miss.
    state = shell.DealState.load_green_lion(
        load_tapes=False, cache_aware=True, cache_dir=str(tmp_path)
    )

    assert state.deal_model is None
    assert state.load_error is not None
    assert "cache" in state.load_error.lower()


def test_load_green_lion_cache_hit(monkeypatch, tmp_path):
    """A present, valid cache file is loaded as the deal model (no extraction).

    Writes a minimal valid DealModel to a fixture cache dir and points the
    loader at it via the ``cache_dir`` parameter — no extraction is triggered.
    """
    import loanwhiz.extraction.assembler as assembler

    # Build a minimal valid DealModel and persist it where the loader looks.
    model = assembler.DealModel(
        metadata=assembler.DealModelMetadata(
            deal_name="Green Lion 2026-1 B.V.",
            prospectus_url="https://example/p.pdf",
            extracted_at="2026-06-01T00:00:00+00:00",
            extraction_duration_sec=1.0,
            sections_found=["definitions"],
            completeness_score=0.25,
            cache_path="x",
        ),
        definitions={},
        waterfalls={},
        covenants={},
        tranche_structure=[],
        trigger_names=[],
    )

    # The loader derives the filename from _slug(deal_name); use the real slug.
    cache_dir = tmp_path / "deals"
    cache_dir.mkdir(parents=True)
    slug = assembler._slug("Green Lion 2026-1 B.V.")
    (cache_dir / f"{slug}.json").write_text(
        model.model_dump_json(), encoding="utf-8"
    )

    # Guard: must not extract on a cache hit.
    def _boom(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("cache hit must not trigger extraction")

    monkeypatch.setattr(assembler, "extract_deal_model", _boom)

    state = shell.DealState.load_green_lion(
        load_tapes=False, cache_aware=True, cache_dir=str(cache_dir)
    )
    assert state.deal_model is not None
    assert state.deal_model.metadata.deal_name == "Green Lion 2026-1 B.V."
    assert state.load_error is None
