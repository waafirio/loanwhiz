"""The MCP SDK dependency must stay inside the major its server code targets.

``mcp/loanwhiz_primitives_mcp/server.py`` calls the SDK's decorator API directly
-- ``Server.list_tools()``, ``Server.call_tool()``, ``Server.read_resource()``.
Those names moved in the SDK's 2.x line, so a requirement spanning the major
boundary lets a fresh install resolve an SDK the server cannot build against.

That is not hypothetical. ``mcp/pyproject.toml`` declared ``mcp>=1.0``; that
range resolves to 2.2.0, where ``build_server()`` raises
``AttributeError: 'Server' object has no attribute 'list_tools'`` and every
server-side test fails. Five of the seven failures in ``mcp/tests`` were this
one cause -- SDK rot, not the catalogue drift the issue was filed about.

The reason it went unnoticed for so long is the part worth keeping guarded:
``mcp/tests`` is the only suite that exercises the server, and it never ran.
Root ``testpaths`` excluded it and this repo has no CI, so nothing executed it
even once. It runs by default now, but it *skips* when the SDK is absent -- so
the checks here are written to need **no SDK**, which is what keeps the bound
guarded in an environment that cannot run the server tests at all.

This extends the class of defect ``tests/test_live_seam_dependencies.py``
already pins -- a seam depending on something the packaging does not guarantee
-- and follows its lesson that a *declared* dependency is not a working one:
where that module opens a real PDF rather than merely importing ``pypdf``, the
SDK-dependent test below actually builds the server rather than merely
importing ``mcp``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MCP_PYPROJECT = _REPO_ROOT / "mcp" / "pyproject.toml"

# The major the server's decorator API does not survive. Raising the declared
# bound past this is a deliberate act that must come with a server.py rewrite.
_INCOMPATIBLE_MAJOR = Version("2.0.0")


def _declared_mcp_requirement() -> Requirement:
    """Return the ``mcp`` requirement declared by the MCP package."""
    data = tomllib.loads(_MCP_PYPROJECT.read_text())
    for raw in data["project"]["dependencies"]:
        requirement = Requirement(raw)
        if canonicalize_name(requirement.name) == "mcp":
            return requirement
    raise AssertionError(f"no 'mcp' dependency declared in {_MCP_PYPROJECT}")


def test_declared_mcp_requirement_excludes_the_incompatible_major() -> None:
    """The declared range must not admit an SDK major the server cannot build on.

    Reds if the bound is widened back to an unbounded ``mcp>=1.0`` -- the exact
    declaration that shipped a server nothing could start. Needs no SDK
    installed, so this holds even where the server tests skip.
    """
    requirement = _declared_mcp_requirement()
    assert requirement.specifier, (
        f"'{requirement}' declares no version bound at all; server.py calls the "
        "SDK's decorator API directly and does not survive a major bump"
    )
    assert not requirement.specifier.contains(_INCOMPATIBLE_MAJOR, prereleases=True), (
        f"'{requirement}' admits mcp {_INCOMPATIBLE_MAJOR}, where "
        "Server.list_tools() does not exist and build_server() raises "
        "AttributeError. Raise this bound only together with the server.py "
        "rewrite the 2.x API requires."
    )


def test_installed_mcp_sdk_satisfies_the_declared_requirement() -> None:
    """Whatever SDK is installed here must be one the declaration allows.

    Catches the drift in the other direction: an environment provisioned
    outside the declared range, where the server tests would pass or fail for
    reasons the packaging does not describe.
    """
    try:
        installed = version("mcp")
    except PackageNotFoundError:
        pytest.skip("MCP SDK not installed in this environment")

    requirement = _declared_mcp_requirement()
    assert requirement.specifier.contains(Version(installed), prereleases=True), (
        f"installed mcp {installed} is outside the declared '{requirement}'"
    )


def test_server_builds_against_the_installed_sdk() -> None:
    """``build_server()`` must actually work, not merely import.

    A declared-but-incompatible SDK sails through a bare import check --
    ``import mcp`` succeeds on 2.2.0; it is ``Server.list_tools()`` that is
    gone. Building the server is the capability the package exists to provide,
    so that is what gets exercised, mirroring
    ``test_live_seam_dependencies.py::test_pdf_reader_opens_a_real_pdf``.
    """
    pytest.importorskip("mcp.types", reason="MCP SDK not installed")
    sys.path.insert(0, str(_REPO_ROOT / "mcp"))
    try:
        from loanwhiz_primitives_mcp.server import build_server

        assert build_server() is not None
    finally:
        sys.path.remove(str(_REPO_ROOT / "mcp"))


def test_mcp_catalogue_import_does_not_pull_in_the_rest_app() -> None:
    """Importing the MCP catalogue must not drag in FastAPI or the REST app.

    This is the property the reachability re-export exists for and the one
    thing nothing asserted. ``loanwhiz_primitives_mcp.reachability`` re-exports
    ``loanwhiz.primitives.reachability`` precisely so the MCP server does not
    import ``loanwhiz.api.main`` -- and with it FastAPI and the whole REST
    import graph -- just to read one dict.

    Runs in a subprocess because the rest of this suite imports FastAPI long
    before this test executes, so ``sys.modules`` in-process proves nothing.
    """
    probe = (
        "import sys\n"
        "from loanwhiz_primitives_mcp.catalogue import build_catalogue\n"
        "build_catalogue()\n"
        "leaked = [m for m in ('fastapi', 'starlette', 'loanwhiz.api')\n"
        "          if m in sys.modules]\n"
        "print(','.join(leaked))\n"
    )
    # Inherit the environment rather than replacing it -- a bare env drops HOME
    # and friends, which some interpreters and site hooks need. Only PYTHONPATH
    # matters to what is being asserted, so only PYTHONPATH is overridden.
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{_REPO_ROOT / 'src'}:{_REPO_ROOT / 'mcp'}"
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    leaked = result.stdout.strip()
    assert not leaked, (
        f"importing the MCP catalogue pulled in {leaked}; the reachability "
        "re-export exists so the MCP server stays independent of the REST app"
    )
