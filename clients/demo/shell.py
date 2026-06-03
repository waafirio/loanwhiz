"""LoanWhiz unified demo — app shell, shared deal state, and tab-plugin contract.

This module is the **foundation** of the Demo UI: it defines the contract the
five tab children build against, and it assembles them (plus a docked chat
panel) into one ``gr.Blocks`` app. It deliberately contains *no* tab content —
each tab is populated by a sibling module's ``render(state)`` function.

Run the shell standalone with::

    python clients/demo/app.py

────────────────────────────────────────────────────────────────────────────
THE TAB-PLUGIN CONTRACT  (see also clients/demo/CONTRACT.md)
────────────────────────────────────────────────────────────────────────────

1. Shared deal state — :class:`DealState`
   ---------------------------------------
   The shell loads the deal **once** and shares it with every tab via a
   per-session ``gr.State`` holding a :class:`DealState`. Tabs **read** this
   object; they do not re-load tapes themselves. Fields:

       deal_name   : str               — human deal name.
       tapes       : list[dict]        — one normalised ESMA tape per reporting
                                         period (``EsmaTapeOutput.model_dump()``
                                         plus a ``"period"`` label key), in
                                         chronological order. Matches the dict
                                         shape the standalone dashboard/chat
                                         clients already consume.
       deal_model  : DealModel | None  — the cached, extracted deal model
                                         (waterfalls, covenants, definitions,
                                         tranche structure) when the pre-warmed
                                         extraction cache is present; ``None``
                                         otherwise. Cold extraction is >10min,
                                         so the loader NEVER triggers it.
       loaded      : bool              — True once a load has been attempted and
                                         tapes are available.
       load_error  : str | None       — human-readable note if loading degraded
                                         (e.g. deal-model cache miss, network
                                         error). Tabs can surface this.

   Construct the empty default with :meth:`DealState.empty`. Load the demo deal
   (cache-aware) with :meth:`DealState.load_green_lion`.

2. The ``render(state)`` convention
   ---------------------------------
   Each tab module exposes exactly::

       def render(state: gr.State) -> None:
           '''Populate this tab. Called inside an open gr.Tab context.'''
           ...

   - ``render`` is called by the shell **inside** the tab's ``gr.Tab`` /
     ``gr.Column`` context, so any components it creates land in that tab.
   - ``state`` is the shared session ``gr.State`` (its ``.value`` is a
     :class:`DealState`). Wire it as an *input* to your event handlers to read
     the loaded deal; do not mutate it in place — return a new ``DealState``
     from a handler if a tab needs to update shared state.
   - ``render`` returns ``None``; it builds UI as a side effect (the standard
     Gradio Blocks idiom).

3. Registering a tab
   ------------------
   The shell holds :data:`TAB_REGISTRY`, an ordered list of :class:`TabSpec`
   (``title`` + ``render``). Until a tab lands, its ``render`` is a placeholder
   stub. A tab worker plugs in by:

       a. adding ``clients/demo/tabs/<module>.py`` with a ``render(state)``; then
       b. replacing their entry's ``render`` in :data:`TAB_REGISTRY` with an
          import of that function.

   The tab **order** in :data:`TAB_REGISTRY` is the demo's narrative arc and is
   load-bearing — do not reorder it.

4. The docked chat panel
   ----------------------
   Chat lives in a single right-hand column laid out **beside** the
   ``gr.Tabs()`` in one shared ``gr.Row``. Because it sits outside the tab
   container, it is visible and reachable no matter which tab is active — one
   chat instance, not one per tab. The shell provides a stub chat handler
   (:func:`_chat_stub_respond`); issue #81 replaces it with a real call to
   ``loanwhiz.agent.run_query`` and wires in the loaded :class:`DealState` as
   grounding context.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import gradio as gr
from pydantic import BaseModel, ConfigDict, Field

# Make the ``loanwhiz`` package importable when this app is run directly,
# mirroring the sys.path shim the standalone clients use.
_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from loanwhiz.config import GREEN_LION  # noqa: E402
from loanwhiz.extraction.assembler import DealModel  # noqa: E402

# Default on-disk extraction cache — must match ``extract_deal_model``'s
# default so the cache-aware read finds what the (pre-warm) extraction wrote.
DEAL_CACHE_DIR = "/tmp/loanwhiz_cache/deals"

# ---------------------------------------------------------------------------
# Branding
# ---------------------------------------------------------------------------

APP_TITLE = "LoanWhiz — Structured Finance Deal Intelligence"

_HEADER_MD = (
    "# 🦁 LoanWhiz\n"
    "### Structured Finance Agent Framework — Green Lion 2026-1 B.V.\n"
    "*Dutch RMBS · ING Bank N.V. originator · deal model extracted from "
    "prospectus, run live against ESMA tapes.*"
)


# ---------------------------------------------------------------------------
# Shared deal state
# ---------------------------------------------------------------------------


class DealState(BaseModel):
    """Shared "currently loaded deal" object every tab reads.

    Loaded once by the shell and shared with all tabs via a per-session
    ``gr.State``. See the module docstring and ``CONTRACT.md`` for the field
    semantics and the load-once-share rule.
    """

    # ``deal_model`` is an arbitrary (non-trivially-validatable here) pydantic
    # type from another module; allow it through without re-validation.
    model_config = ConfigDict(arbitrary_types_allowed=True)

    deal_name: str = ""
    tapes: list[dict] = Field(default_factory=list)
    deal_model: DealModel | None = None
    loaded: bool = False
    load_error: str | None = None

    @classmethod
    def empty(cls) -> "DealState":
        """Return an empty, unloaded deal state (the session default)."""
        return cls()

    @classmethod
    def load_green_lion(
        cls,
        *,
        load_tapes: bool = True,
        load_model: bool = True,
        cache_aware: bool = True,
        cache_dir: str = DEAL_CACHE_DIR,
    ) -> "DealState":
        """Load the Green Lion 2026-1 demo deal — cache-aware, never cold.

        Loads the three monthly ESMA tapes live (fast — a few HTTP CSV reads
        normalised by ``EsmaTapeNormaliser``) and, when ``load_model`` is set,
        loads the extracted deal model **from the pre-warmed extraction cache
        only**. Cold extraction (Docling + Gemini) takes >10 minutes, so when
        ``cache_aware`` is True (the default and the demo-day setting) a cache
        miss leaves ``deal_model=None`` and records a ``load_error`` rather
        than blocking on a cold run.

        Parameters
        ----------
        load_tapes:
            Load and normalise the three ESMA tapes. Disable for fast, network-
            free construction (e.g. tests).
        load_model:
            Attempt to load the cached deal model.
        cache_aware:
            When True, never trigger a cold extraction — only read an existing
            cache. When False, fall back to ``extract_deal_model`` which will
            run the full (slow) pipeline on a cache miss. Leave True for the
            live demo.
        cache_dir:
            Directory holding the pre-warmed deal-model cache. Defaults to the
            same path ``extract_deal_model`` writes to.

        Returns
        -------
        DealState
            Populated state. ``load_error`` is set (and the corresponding
            field left empty/None) when a part degraded gracefully.
        """
        errors: list[str] = []
        tapes: list[dict] = []

        if load_tapes:
            try:
                # Imported lazily so test/headless construction with
                # load_tapes=False never imports the (heavier) primitive.
                from loanwhiz.primitives.esma_tape_normaliser import (
                    EsmaTapeInput,
                    EsmaTapeNormaliser,
                )

                normaliser = EsmaTapeNormaliser()
                for tape_info in GREEN_LION["tape_urls"]:
                    result = normaliser.execute(
                        EsmaTapeInput(file_url=tape_info["url"])
                    )
                    tapes.append(
                        {"period": tape_info["date"], **result.output.model_dump()}
                    )
            except Exception as exc:  # noqa: BLE001 — degrade, don't crash demo.
                errors.append(f"tape load failed: {exc}")

        deal_model = (
            cls._load_cached_deal_model(cache_aware, cache_dir)
            if load_model
            else None
        )
        if load_model and deal_model is None and cache_aware:
            errors.append(
                "deal model not in extraction cache "
                "(cold extraction skipped — pre-warm the cache to enable it)"
            )

        return cls(
            deal_name=GREEN_LION["deal_name"],
            tapes=tapes,
            deal_model=deal_model,
            loaded=bool(tapes) or deal_model is not None,
            load_error="; ".join(errors) if errors else None,
        )

    @staticmethod
    def _load_cached_deal_model(
        cache_aware: bool, cache_dir: str = DEAL_CACHE_DIR
    ) -> "DealModel | None":
        """Load the deal model from the extraction cache.

        When ``cache_aware`` is True, returns the cached :class:`DealModel` if
        present and ``None`` on a cache miss (never triggers extraction). When
        False, delegates to ``extract_deal_model`` which extracts on a miss.

        ``cache_dir`` defaults to the same directory ``extract_deal_model``
        writes to, so the cache-aware read and the extraction write agree; it
        is parameterised so tests can point it at a fixture directory.
        """
        from loanwhiz.extraction.assembler import (  # local import: heavy module
            _slug,
            extract_deal_model,
        )

        deal_name = GREEN_LION["deal_name"]

        if cache_aware:
            # Inspect the cache directly so a miss costs nothing and never
            # kicks off a >10min cold extraction.
            cache_path = Path(cache_dir) / f"{_slug(deal_name)}.json"
            if not cache_path.exists():
                return None
            try:
                return DealModel.model_validate_json(
                    cache_path.read_text(encoding="utf-8")
                )
            except Exception:  # noqa: BLE001 — corrupt cache → treat as miss.
                return None

        try:
            return extract_deal_model(
                prospectus_url=GREEN_LION["prospectus_url"],
                deal_name=deal_name,
                cache_dir=cache_dir,
            )
        except Exception:  # noqa: BLE001 — degrade, don't crash demo.
            return None


# ``DealState`` references ``DealModel`` via a forward annotation; resolve it
# now so the model is fully defined (needed because callers may import this
# module by path, where lazy resolution wouldn't otherwise fire).
DealState.model_rebuild()


# ---------------------------------------------------------------------------
# Tab registry (the narrative arc — order is load-bearing)
# ---------------------------------------------------------------------------


class TabSpec(BaseModel):
    """One tab in the shell: a title plus its ``render(state)`` callable."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    title: str
    render: Callable[[gr.State], None]


def _stub_render(issue_number: int, title: str) -> Callable[[gr.State], None]:
    """Build a placeholder ``render`` showing a "tab in progress" stub.

    Tab workers replace the corresponding :class:`TabSpec.render` with an import
    of their real ``render`` (see the module docstring, step 3).
    """

    def render(state: gr.State) -> None:  # noqa: ARG001 — stub ignores state.
        gr.Markdown(
            f"### {title}\n\n"
            f"🚧 *Tab #{issue_number} — in progress.*\n\n"
            f"This tab is a placeholder. Issue #{issue_number} replaces this "
            f"stub with a `render(state)` that reads the shared `DealState`. "
            f"See `clients/demo/CONTRACT.md`."
        )

    return render


# Ordered to tell the deal-model story (epic #75). Tabs are freely navigable
# but the ORDER is the narrative arc — do not reorder. Each entry's ``render``
# is a stub until the named sibling issue lands its tab module.
TAB_REGISTRY: list[TabSpec] = [
    TabSpec(title="Deal Overview", render=_stub_render(78, "Deal Overview")),
    TabSpec(title="Pool & Performance", render=_stub_render(79, "Pool & Performance")),
    TabSpec(title="Waterfall", render=_stub_render(80, "Waterfall")),
    TabSpec(
        title="Compliance & Covenants",
        render=_stub_render(82, "Compliance & Covenants"),
    ),
    TabSpec(
        title="Cashflow Projection",
        render=_stub_render(83, "Cashflow Projection"),
    ),
]


# ---------------------------------------------------------------------------
# Docked chat panel (stub — #81 wires the real agent)
# ---------------------------------------------------------------------------


def _chat_stub_respond(message: str, history: list[dict]) -> list[dict]:
    """Placeholder chat handler for the docked chat shell.

    Echoes a "chat wiring pending" notice so the shell is runnable on its own.
    Issue #81 replaces this with a real call to ``loanwhiz.agent.run_query``,
    grounding the answer in the loaded :class:`DealState`.

    Parameters
    ----------
    message:
        The user's message.
    history:
        Gradio ``messages``-format chat history (list of
        ``{"role", "content"}`` dicts).

    Returns
    -------
    list[dict]
        The updated history with the user turn and the stub assistant reply.
    """
    reply = (
        "💬 *Chat wiring is pending (issue #81).* Once wired, I'll answer "
        "questions about the loaded Green Lion deal — e.g. *“What happens to "
        "Class B if defaults double?”* — grounded in the deal model and tapes."
    )
    return history + [
        {"role": "user", "content": message},
        {"role": "assistant", "content": reply},
    ]


def _render_chat_panel() -> None:
    """Render the docked chat panel (visible from every tab).

    Built once, inside the shared right-hand column, so it persists across tab
    switches. Issue #81 owns the real wiring; everything here is the shell.
    """
    gr.Markdown("### 💬 Ask LoanWhiz")
    # This Gradio build's Chatbot is messages-only (value is a list of
    # {"role", "content"} dicts) — see _chat_stub_respond. On Gradio builds
    # that still default to tuple format, pass type="messages".
    chatbot = gr.Chatbot(
        height=460,
        label="Deal Q&A",
        show_label=False,
    )
    msg = gr.Textbox(
        placeholder="Ask about the Green Lion deal…",
        show_label=False,
        container=False,
    )
    with gr.Row():
        send = gr.Button("Send", variant="primary", size="sm")
        clear = gr.Button("Clear", size="sm")

    def _respond(message: str, history: list[dict]) -> tuple[str, list[dict]]:
        if not message.strip():
            return "", history
        return "", _chat_stub_respond(message, history)

    send.click(_respond, [msg, chatbot], [msg, chatbot])
    msg.submit(_respond, [msg, chatbot], [msg, chatbot])
    clear.click(lambda: [], None, chatbot)


# ---------------------------------------------------------------------------
# App assembly
# ---------------------------------------------------------------------------


def build_app() -> gr.Blocks:
    """Build and return the unified LoanWhiz demo app.

    Lays out LoanWhiz branding, a top deal-loading control, and a single shared
    row containing the tab container (left) beside the docked chat panel
    (right). Each tab is populated by its :class:`TabSpec.render`. Does not
    launch the server and does not hit the network — construction is cheap and
    test-safe.
    """
    # Theme is applied at launch() (Gradio 6 moved it off the Blocks
    # constructor) — see clients/demo/app.py.
    with gr.Blocks(title=APP_TITLE) as app:
        gr.Markdown(_HEADER_MD)

        # Session-scoped shared deal state. Default is empty; the load control
        # populates it. Tabs read it; #81's chat reads it.
        deal_state = gr.State(DealState.empty())

        with gr.Row():
            load_btn = gr.Button(
                "📂 Load Green Lion 2026-1 Deal", variant="primary", scale=0
            )
            load_status = gr.Markdown(
                "*Click **Load** to fetch the Green Lion tapes and the cached "
                "deal model. Tabs share this loaded deal — loaded once.*"
            )

        with gr.Row():
            # Left: the ordered, navigable tabs.
            with gr.Column(scale=3):
                with gr.Tabs():
                    for spec in TAB_REGISTRY:
                        with gr.Tab(spec.title):
                            spec.render(deal_state)

            # Right: the docked chat panel — present beside every tab.
            with gr.Column(scale=1, min_width=320):
                _render_chat_panel()

        def _load() -> tuple[DealState, str]:
            state = DealState.load_green_lion()
            if not state.loaded:
                status = f"❌ Load failed: {state.load_error or 'unknown error'}"
            elif state.load_error:
                status = (
                    f"✅ Loaded **{state.deal_name}** "
                    f"({len(state.tapes)} tape period(s)). "
                    f"⚠️ {state.load_error}"
                )
            else:
                model_note = (
                    "deal model from cache"
                    if state.deal_model is not None
                    else "no cached deal model"
                )
                status = (
                    f"✅ Loaded **{state.deal_name}** "
                    f"({len(state.tapes)} tape period(s); {model_note})."
                )
            return state, status

        load_btn.click(_load, outputs=[deal_state, load_status])

    return app
