# What the default test invocation covers, and what dependency ranges promise

<!-- Agent-appended entries below. Append only; never reorder or rewrite an
     existing entry (`merge=union` would duplicate it rather than replace it). -->

## 2026-09-09 · pitfall · #577

A suite excluded from the **default** invocation does not merely miss drift — it
lets the code rot against its own declared dependencies, invisibly. `mcp/tests`
was outside root `testpaths` and this repo has no CI, so it had never executed
once; meanwhile `mcp/pyproject.toml` declared `mcp>=1.0`, which resolves to
2.2.0, where the `Server.list_tools()` decorator API the server calls directly
no longer exists. The shipped MCP server was unbuildable from a fresh install
and **five of the seven failures were this**, not the catalogue drift the issue
was filed about. An unbounded range across a major, on a package whose API you
call directly, is a promise the packaging cannot keep. Bound it, and put the
bound's guard in a suite that runs **without** the optional dependency — a guard
living only in the SDK-dependent suite is absent in exactly the environment that
lacks the SDK.

Refs: #577

## 2026-09-09 · gotcha · #577

Adding a nested directory to root `testpaths` can red *unrelated* tests. Under
pytest's default `prepend` import mode, a test file in a package (one with
`__init__.py`) makes pytest insert the first **non**-package ancestor at
`sys.path[0]` — for `mcp/tests/…` that is `mcp/`. Since `mcp/tests` is then
importable as a top-level `tests`, it shadowed the repo-root `tests` package and
three root tests doing `from tests.clo_answer_key_source import …` died at
collection. Deleting `mcp/tests/__init__.py` fixes it; a `conftest.py`
`sys.path` tweak does not, because the insertion is pytest's, not the
conftest's. Check the **collected** count, not just the failure count: the three
casualties were collection errors, which contribute no test and therefore no
failure.

Refs: #577
