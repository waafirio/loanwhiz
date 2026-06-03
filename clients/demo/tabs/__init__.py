"""Tab modules for the LoanWhiz unified demo app.

Each tab is a module in this package exposing a single ``render(state)``
function that the shell calls inside that tab's Gradio context. See
``clients/demo/CONTRACT.md`` for the full plug-in contract.

The shell (``clients.demo.shell``) holds an ordered registry mapping each tab
title to its ``render`` callable. Until a tab worker lands their module, the
shell uses a placeholder stub renderer; a tab worker plugs in by importing
their ``render`` here and swapping it into the registry in ``shell.py``.

Expected tab modules (one per sibling issue):

- ``deal_overview``       — #78 Deal Overview
- ``pool_performance``    — #79 Pool & Performance
- ``waterfall``           — #80 Waterfall
- ``compliance``          — #82 Compliance & Covenants
- ``cashflow_projection`` — #83 Cashflow Projection
"""
