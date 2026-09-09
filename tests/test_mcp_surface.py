"""Tests for ``GET /mcp/surface`` — the MCP tool surface, stated honestly (#574).

The endpoint's whole claim is that it describes the MCP server *from the
server's own logic*, so these tests are mostly agreement tests: between the
endpoint and the registry, between the endpoint and the predicate the server
dispatches on, and between the endpoint and ``GET /primitives``. A page that
merely *looked* right while the server exposed something else is the failure
mode being prevented — ``mcp/README.md``'s hand-maintained table had drifted in
exactly that way before this issue.

These run offline: no MCP SDK, no server process, no network. Importing the MCP
package's catalogue without the SDK is itself part of the contract (#574 made
``__init__`` lazy for it), and one test asserts it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from loanwhiz.api import app
from loanwhiz.primitives.reachability import LIBRARY_ONLY, LIVE, is_exposed_as_tool
from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY, ensure_all_registered

client = TestClient(app)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PRIMITIVES_DIR = _REPO_ROOT / "src" / "loanwhiz" / "primitives"
_MCP_PACKAGE_DIR = _REPO_ROOT / "mcp"


def _surface() -> list[dict]:
    resp = client.get("/mcp/surface")
    assert resp.status_code == 200
    return resp.json()


def _modules_that_register() -> set[str]:
    """Enumerate primitive modules by reading the package directory.

    Deliberately independent of ``ensure_all_registered``: this globs the source
    tree itself, so if the walk it performs ever stops reaching a module — a
    skip, a filter, a reversion to a hand-listed tuple — the two disagree and
    the census test below reds. An oracle built from the same walk could not
    catch that.
    """
    found = set()
    for path in _PRIMITIVES_DIR.glob("*.py"):
        if path.stem in {"registry", "__init__"}:
            continue
        if "@register_primitive" in path.read_text(encoding="utf-8"):
            found.add(path.stem)
    return found


# Hit the endpoint FIRST, in a pristine interpreter, then import every module
# the caller globbed and re-read the registry. Order is the whole point: the
# registry is process-global, so anything this suite already imported would
# otherwise mask a module the endpoint's own walk failed to reach — which is
# precisely the bug being guarded, and it makes an in-process census vacuous.
_CENSUS_PROBE = """
import importlib, json, sys
from fastapi.testclient import TestClient
from loanwhiz.api import app
surfaced = sorted(e["name"] for e in TestClient(app).get("/mcp/surface").json())
from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY
for module_name in sys.argv[1:]:
    importlib.import_module("loanwhiz.primitives." + module_name)
print(json.dumps({"surfaced": surfaced, "registered": sorted(PRIMITIVE_REGISTRY.describe())}))
"""


def _census_in_clean_process(modules: set[str]) -> dict[str, list[str]]:
    env = dict(os.environ)
    src = str(_REPO_ROOT / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", _CENSUS_PROBE, *sorted(modules)],
        capture_output=True, text=True, env=env, cwd=str(_REPO_ROOT), check=True,
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_every_registering_module_reaches_the_surface():
    """Census: no registered primitive is invisible to the catalogue.

    The regression for the bug the issue found — two hand-maintained import
    lists, neither complete, so `report_extractor` and `tranche_analytics`
    registered primitives that appeared in no catalogue at all.
    """
    modules = _modules_that_register()
    assert modules, "expected to find primitive modules by globbing the package"

    census = _census_in_clean_process(modules)
    # Importing every globbed module must reveal nothing the endpoint missed.
    assert census["registered"] == census["surfaced"], (
        "these primitives register but the surface never reached them: "
        f"{sorted(set(census['registered']) - set(census['surfaced']))}"
    )

    # The two that were invisible before #574 are present, honestly not callable.
    by_name = {entry["name"]: entry for entry in _surface()}
    for name in ("report_extractor", "tranche_analytics"):
        assert name in by_name, f"{name} registers a primitive but is not surfaced"
        assert by_name[name]["reachability"] == LIBRARY_ONLY
        assert by_name[name]["exposed_as_tool"] is False


def test_exposure_is_the_shared_predicates_answer():
    """Every row's `exposed_as_tool` is what `is_exposed_as_tool` returned."""
    surface = _surface()
    assert surface
    for entry in surface:
        assert entry["exposed_as_tool"] is is_exposed_as_tool(entry["name"])
        # And the rendered reachability agrees with the exposure it implies.
        assert entry["exposed_as_tool"] == (entry["reachability"] == LIVE)


def test_surface_agrees_with_the_mcp_packages_own_tool_list():
    """The endpoint's exposed set equals the MCP package's `live_tool_names()`.

    The server builds `tools/list` from the same predicate, so this is the
    check that the page cannot advertise a tool the server does not serve.
    Imports the MCP package directly — which also proves the SDK-free import
    path, since the `mcp` SDK is not a dependency of this test suite.
    """
    if str(_MCP_PACKAGE_DIR) not in sys.path:
        sys.path.insert(0, str(_MCP_PACKAGE_DIR))
    from loanwhiz_primitives_mcp import build_catalogue, live_tool_names

    exposed = {entry["name"] for entry in _surface() if entry["exposed_as_tool"]}
    assert exposed == set(live_tool_names())
    # The MCP catalogue resource and the endpoint cover the same primitives.
    assert {entry["name"] for entry in build_catalogue()} == {
        entry["name"] for entry in _surface()
    }


def test_every_exposed_tool_advertises_a_typed_object_input_schema():
    """A callable tool states the typed contract a client must satisfy."""
    exposed = [entry for entry in _surface() if entry["exposed_as_tool"]]
    assert exposed, "expected at least one exposed tool"
    for entry in exposed:
        schema = entry["input_schema"]
        assert schema.get("type") == "object", f"{entry['name']} input schema not an object"
        assert "properties" in schema, f"{entry['name']} input schema has no properties"
        assert entry["description"], f"{entry['name']} has no description"


def test_every_exposed_tool_states_the_governance_it_returns():
    """The evidence pack is stated per tool, and read off the result models."""
    exposed = [entry for entry in _surface() if entry["exposed_as_tool"]]
    for entry in exposed:
        governance = {field["name"]: field for field in entry["result_governance"]}
        assert {"confidence", "citations", "audit_entry"} <= set(governance), entry["name"]
        # `output` is the answer, not evidence about it — it must not be listed.
        assert "output" not in governance
        # Every governance field carries its documented meaning.
        for field in entry["result_governance"]:
            assert field["description"], f"{entry['name']}.{field['name']} undocumented"
        # The audit entry's own structure is stated, so a reader knows what
        # provenance a call actually leaves behind.
        assert {"input_hash", "executed_at", "duration_ms"} <= set(
            governance["audit_entry"]["fields"]
        )
        assert {"document", "page_or_row", "excerpt"} <= set(
            governance["citations"]["fields"]
        )


def test_library_only_primitives_are_listed_but_never_advertised_as_callable():
    """A non-callable primitive keeps its contract and gains a stated reason."""
    unexposed = [entry for entry in _surface() if not entry["exposed_as_tool"]]
    assert unexposed, "expected at least one library-only primitive"
    for entry in unexposed:
        assert entry["reachability"] == LIBRARY_ONLY
        assert entry["not_exposed_reason"], f"{entry['name']} unexposed with no reason"
        # It returns nothing, so it carries no result governance.
        assert entry["result_governance"] == []
        # Its typed contract is still legible.
        assert entry["input_schema"].get("type") == "object"


def test_exposed_tools_state_no_reason_for_non_exposure():
    """`not_exposed_reason` is null exactly when the primitive is callable."""
    for entry in _surface():
        if entry["exposed_as_tool"]:
            assert entry["not_exposed_reason"] is None
        else:
            assert entry["not_exposed_reason"] is not None


def test_primitives_and_mcp_surface_cannot_disagree():
    """`/primitives` and `/mcp/surface` answer from one registry and one map."""
    ensure_all_registered()
    primitives = {entry["name"]: entry for entry in client.get("/primitives").json()}
    surface = {entry["name"]: entry for entry in _surface()}

    assert set(primitives) == set(surface)
    for name, entry in surface.items():
        assert entry["reachability"] == primitives[name]["reachability"], name
        assert entry["input_schema"] == primitives[name]["input_schema"], name
        assert entry["version"] == primitives[name]["version"], name
