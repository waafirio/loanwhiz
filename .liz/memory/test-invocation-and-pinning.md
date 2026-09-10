# What the default test invocation covers, and what dependency ranges promise

<!-- Agent-appended entries below. Append only; never reorder or rewrite an
     existing entry (`merge=union` would duplicate it rather than replace it). -->

## 2026-09-09 · pitfall · #577

A suite outside the **default** invocation does not merely miss drift — it lets
code rot against its own declared dependencies, invisibly. Bound any range that
spans a major on a package whose API you call directly (`mcp>=1.0` resolved to
2.2.0, where the decorator API `server.py` calls was gone and the server could
not be built at all). Put the bound's guard in a suite that runs **without** the
optional dependency: a guard living only in the SDK-dependent suite is absent in
exactly the environment that lacks the SDK. Assert the range admits no version
at or above the bad major — checking one version passes a range wholly above it.

Refs: #577

## 2026-09-09 · gotcha · #577

Adding a nested directory to root `testpaths` can red *unrelated* tests. Under
pytest's default `prepend` import mode, a test file inside a package makes
pytest insert the first **non**-package ancestor at `sys.path[0]` — for
`mcp/tests/…` that is `mcp/`, which then shadows the repo-root `tests` package
and kills every `from tests.x import y` at collection. Delete the nested
`__init__.py`; a `conftest.py` `sys.path` tweak cannot fix it, because the
insertion is pytest's, not the conftest's. Read the **collected** count, not the
failure count: collection errors contribute no test and so no failure.

Refs: #577

## 2026-09-10 · pitfall · #600

When a fix reds a test elsewhere, ask whether that test was pinning the bug
before assuming the fix is wrong. A cross-deal resolution test asserted "the
identifier rescues a row whose name is unusable" and drew its unusable name
from a *parser* defect — so repairing the parser broke a test about resolution,
which had not changed, and the cheap-looking move was to revert the fix. Source
a malformed input from a synthetic fixture, never from another component's
defect, and pin the property actually under test: here, that two trustees
really do spell one obligor differently.

Refs: #600
