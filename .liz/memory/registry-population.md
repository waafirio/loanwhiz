# Primitive registry population & catalogue surfaces

<!-- Agent-appended entries below. Append only; never reorder or rewrite an
     existing entry (`merge=union` would duplicate it rather than replace it). -->

## 2026-09-09 · pitfall · #574

A registry populated by **import side effect** holds whatever the importing
process listed, so every consumer naming its own modules gets a different
catalogue and each looks authoritative. `PRIMITIVE_REGISTRY` had two importers —
`api/main.py`'s registration imports and the MCP catalogue's `_PRIMITIVE_MODULES` —
and each served a different, incomplete subset while claiming to be the
catalogue. Derive the import set from the package (`pkgutil.iter_modules`) so
membership follows from the module existing, and assert the consuming surfaces
agree: the disagreement, not either tally, is what reveals the second list.

Refs: #574

## 2026-09-09 · gotcha · #574

Census a **process-global** registry in a clean subprocess, reading the surface
under test *before* importing anything yourself. An in-process census that
imports the modules it expects, then compares the registry to the endpoint,
passes even when the endpoint's own walk skips one — the test's imports backfill
exactly the gap it exists to find. This survived its `red-when:` mutant and only
reded once the probe hit the endpoint first in a fresh interpreter. Any assertion
over module-import state shares the flaw: pytest has already imported half the tree.

Refs: #574
