# Live seams the offline suite never executes

<!-- Agent-appended entries below. Append only; never reorder or rewrite an
     existing entry (`merge=union` would duplicate it rather than replace it). -->

## 2026-09-08 · pitfall · #483

A green suite is no evidence about a path whose only test needs the network.
Before changing the *shape* of a registry value — a tape URL, a report URI —
grep every **reader** of it, not every caller of the seam: the readers that
break are the bypassing ones, exactly the ones no offline test executes.
Guard the class statically, not per reader: `tests/test_tape_seam_bypass.py`
walks the AST for `pandas` reads whose path never passed `underlying_url`, so
it reds on a bypassing reader written tomorrow in a module with no tests.

Refs: #483

## 2026-09-08 · decision · #483

Fix a seam bypass by **delegating to the seam**, not by stripping the scheme at
the reader. `underlying_url` resolves a `synthetic:` identifier to the file it
names, but a `derived+trustee-report:` one resolves to a source PDF that has to
be *derived* into rows — so a stripping reader trades a loud failure for a CSV
parser eating a PDF. `esma_tape_normaliser._load_tape` is the seam and is
already imported directly by `api/main.py` and `pool_stratification`; a direct
read (`data/green_lion.py`, `demo/`) may call `underlying_url` only where the
identifiers it can receive are all published files.

Refs: #483
