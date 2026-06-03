"""LoanWhiz unified demo — entrypoint.

Launches the multi-tab Gradio app with the docked chat panel.

Run::

    python clients/demo/app.py

The app supersedes the three standalone clients (chat/dashboard/compliance);
those remain until issue #84 retires them.
"""

import sys
from pathlib import Path

import gradio as gr

# Allow ``from clients.demo.shell import build_app`` when run as a script by
# putting the repo root on the path (mirrors the standalone clients' shim).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from clients.demo.shell import build_app  # noqa: E402

if __name__ == "__main__":
    build_app().launch(
        server_name="0.0.0.0", server_port=7860, theme=gr.themes.Soft()
    )
