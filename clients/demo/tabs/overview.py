"""Deal Overview tab — cache-first deal-model display (issue #78).

The first tab in the demo's narrative arc (epic #75). It renders a top-level
summary of the loaded deal **from the shared** :class:`DealState`, reading the
pre-warmed extraction cache when it is present and degrading clearly when it is
not. It never re-loads tapes and never triggers a (slow) cold extraction — it
only reads what the shell already put in ``state`` (see ``CONTRACT.md`` §1, §4).

When the deal model is present (cache warm) it surfaces:

- the tranche structure (one row per revenue-waterfall payment step),
- the count of revenue-waterfall steps,
- the list of trigger names,
- the metadata completeness score, and
- an explicit "✓ extraction pre-warmed (cached)" affordance.

When the deal model is ``None`` (cache miss) it explains that the model was not
extracted, points at the pre-warm runbook, notes that cold extraction is
~10 min, and still shows what tapes alone provide (deal name, periods, pool
balance).
"""

from __future__ import annotations

from typing import Any

import gradio as gr


# ---------------------------------------------------------------------------
# Pure view helpers (no Gradio) — easy to unit-test, reused by render().
# ---------------------------------------------------------------------------


def _revenue_step_count(deal_model: Any) -> int:
    """Count steps in the revenue waterfall (falling back to the first one).

    ``deal_model.waterfalls`` is ``{waterfall_type: ExtractedWaterfall.model_dump()}``
    so each value is a plain dict with a ``"steps"`` list. Prefer the revenue
    waterfall — the canonical tranche hierarchy — and fall back to whatever
    waterfall is available. Returns 0 when there are no waterfalls.
    """
    waterfalls = getattr(deal_model, "waterfalls", None) or {}
    chosen = waterfalls.get("revenue") or next(iter(waterfalls.values()), None)
    if not chosen:
        return 0
    return len(chosen.get("steps", []) or [])


def _tranche_rows(deal_model: Any) -> list[list[str]]:
    """Build dataframe rows from ``deal_model.tranche_structure``.

    Each tranche dict is ``{priority, recipient, description, waterfall_type}``
    (see ``assembler._extract_tranches``). Missing keys degrade to an empty
    string rather than raising.
    """
    rows: list[list[str]] = []
    for t in getattr(deal_model, "tranche_structure", None) or []:
        rows.append(
            [
                str(t.get("priority", "")),
                str(t.get("recipient", "")),
                str(t.get("description", "")),
            ]
        )
    return rows


def _format_pool_balance(tapes: list[dict]) -> str:
    """Return the latest period's pool balance as a human string, or 'n/a'."""
    if not tapes:
        return "n/a"
    latest = tapes[-1]
    bal = latest.get("pool_balance_eur")
    if bal is None:
        return "n/a"
    try:
        return f"€{float(bal):,.0f}"
    except (TypeError, ValueError):
        return str(bal)


def _warm_summary_md(state: Any) -> str:
    """Markdown summary for a warm (deal_model present) state."""
    dm = state.deal_model
    meta = getattr(dm, "metadata", None)
    completeness = getattr(meta, "completeness_score", None)
    completeness_str = (
        f"{completeness * 100:.0f}%" if isinstance(completeness, (int, float)) else "n/a"
    )
    step_count = _revenue_step_count(dm)
    triggers = getattr(dm, "trigger_names", None) or []

    trigger_block = (
        "\n".join(f"- {name}" for name in triggers)
        if triggers
        else "*No triggers extracted.*"
    )

    return (
        f"## {state.deal_name or 'Deal'}\n"
        "**✓ extraction pre-warmed (cached)** — deal model loaded from the "
        "pre-warmed extraction cache; no cold extraction was triggered.\n\n"
        f"- **Tranche / waterfall steps (revenue):** {step_count}\n"
        f"- **Trigger names:** {len(triggers)}\n"
        f"- **Extraction completeness score:** {completeness_str}\n"
        f"- **Reporting periods loaded:** {len(state.tapes)}\n"
        f"- **Latest pool balance:** {_format_pool_balance(state.tapes)}\n\n"
        "### Triggers\n"
        f"{trigger_block}"
    )


def _cold_summary_md(state: Any) -> str:
    """Markdown summary for a cold (deal_model is None) state."""
    note = state.load_error or "deal model not present in the extraction cache"
    return (
        f"## {state.deal_name or 'Deal'}\n"
        "### ⚠️ Deal model not extracted\n"
        f"The extracted deal model is **not available** ({note}).\n\n"
        "Run the extraction **pre-warm** to populate the cache (see the demo "
        "runbook / `clients/demo/CONTRACT.md` §4). The live demo never triggers "
        "extraction inline because a **cold extraction is ~10 minutes** "
        "(Docling + Gemini on CPU).\n\n"
        "**Available from the loaded tapes:**\n"
        f"- **Deal:** {state.deal_name or 'n/a'}\n"
        f"- **Reporting periods loaded:** {len(state.tapes)}\n"
        f"- **Latest pool balance:** {_format_pool_balance(state.tapes)}\n"
    )


def _empty_summary_md() -> str:
    """Markdown shown before any deal has been loaded."""
    return (
        "## Deal Overview\n"
        "No deal loaded yet — click **📂 Load Green Lion 2026-1 Deal** above to "
        "fetch the tapes and the cached deal model."
    )


# ---------------------------------------------------------------------------
# render() — the tab-plugin entry point (CONTRACT.md §2)
# ---------------------------------------------------------------------------


def render(state: gr.State) -> None:
    """Populate the Deal Overview tab. Called inside an open ``gr.Tab`` context.

    Reads the shared :class:`DealState` (``state``) by wiring it as an input to
    the handlers below; it never re-loads tapes or triggers extraction. The
    summary refreshes when the deal is (re)loaded and on demand via the button.
    """
    gr.Markdown(
        "Cache-first overview of the loaded deal. When the extraction cache is "
        "pre-warmed, this shows the deal model (tranches, triggers, "
        "completeness); otherwise it explains how to pre-warm it."
    )

    summary = gr.Markdown(_empty_summary_md())

    gr.Markdown("#### Tranche structure (revenue waterfall steps)")
    tranches = gr.Dataframe(
        headers=["Priority", "Recipient", "Description"],
        column_count=3,
        interactive=False,
        wrap=True,
        value=[],
    )

    refresh = gr.Button("↻ Refresh from loaded deal", size="sm")

    def _show(s: Any) -> tuple[str, list[list[str]]]:
        # ``s`` is the DealState held in the shared gr.State.
        if s is None or not getattr(s, "loaded", False):
            return _empty_summary_md(), []
        if getattr(s, "deal_model", None) is not None:
            return _warm_summary_md(s), _tranche_rows(s.deal_model)
        return _cold_summary_md(s), []

    refresh.click(_show, inputs=state, outputs=[summary, tranches])
    # Refresh automatically whenever the shared state value changes (e.g. after
    # the shell's Load button populates it).
    state.change(_show, inputs=state, outputs=[summary, tranches])
