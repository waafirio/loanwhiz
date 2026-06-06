"""Pytest bootstrap for the MCP package tests.

Puts this ``mcp/`` directory on ``sys.path`` so ``loanwhiz_primitives_mcp`` is
importable when the suite is invoked from the repo root (the documented
``PYTHONPATH=src python3 -m pytest mcp/tests`` invocation, where ``src`` covers
``loanwhiz`` but not this package). With the package installed
(``pip install -e mcp``) this is a harmless no-op.
"""

import sys
from pathlib import Path

_MCP_ROOT = str(Path(__file__).resolve().parent)
if _MCP_ROOT not in sys.path:
    sys.path.insert(0, _MCP_ROOT)
