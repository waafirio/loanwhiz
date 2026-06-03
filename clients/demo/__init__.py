"""LoanWhiz unified demo app — multi-tab Gradio Blocks shell.

This package hosts the single unified Gradio application that supersedes the
three standalone clients (``clients/chat``, ``clients/dashboard``,
``clients/compliance``). It presents the LoanWhiz deal-model story as an
ordered-but-navigable multi-tab experience with a chat panel docked on every
tab.

Public surface:

- :func:`clients.demo.shell.build_app` — returns the ``gr.Blocks`` app.
- :class:`clients.demo.shell.DealState` — the shared "currently loaded deal"
  object the tabs read.

See ``clients/demo/CONTRACT.md`` for the tab-plugin contract.
"""
