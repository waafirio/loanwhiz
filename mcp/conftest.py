"""Pytest bootstrap for the MCP package tests.

Puts this ``mcp/`` directory on ``sys.path`` so ``loanwhiz_primitives_mcp`` is
importable when the suite is invoked from the repo root (the documented
``PYTHONPATH=src python3 -m pytest mcp/tests`` invocation, where ``src`` covers
``loanwhiz`` but not this package). With the package installed
(``pip install -e mcp``) this is a harmless no-op.

Deliberately, ``mcp/tests`` has **no** ``__init__.py``. With one, pytest's
default ``prepend`` import mode walks up to the first directory lacking it --
``mcp/`` -- and inserts *that* at ``sys.path[0]``. Because ``mcp/tests`` would
then be an importable package named ``tests``, it shadows the repo-root
``tests`` package, and the three root tests that do
``from tests.clo_answer_key_source import ...`` die on collection. Re-adding
that file reds them (#577).
"""

import sys
from pathlib import Path

_MCP_ROOT = str(Path(__file__).resolve().parent)
if _MCP_ROOT not in sys.path:
    sys.path.insert(0, _MCP_ROOT)
