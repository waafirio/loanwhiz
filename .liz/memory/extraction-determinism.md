# Extraction determinism & LLM caching

<!-- Agent-appended entries below. Append only; never reorder or rewrite an
     existing entry (`merge=union` would duplicate it rather than replace it). -->

## 2026-09-02 · pattern · #445

Caching an LLM call that degrades to a safe default on error? Split "the call
produced no answer" from "the model answered nothing matched" first. A bare
`except` makes an outage indistinguishable from a result, so caching it freezes
the output as degraded until someone deletes the file by hand — cache the
answer, retry the failure. Key on the rendered prompt plus the model id, not a
deal name: that covers document content and prompt revision at once. Expect the
cache to make any test that already reached the live model order-dependent.

Refs: #445
