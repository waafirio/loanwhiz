# Regulatory mapping tables (ESMA annexes, field codes, locators)

<!-- Agent-appended entries below. Append only; never reorder or rewrite an
     existing entry (`merge=union` would duplicate it rather than replace it). -->

## 2026-09-04 · pitfall · #451

When a real tape column has no field code in the regulatory template, model it as
an explicit code-less **extension field** (`AnnexField(code=None, ...)`) — never
borrow the nearest plausible code. A borrowed code resolves and cites fine, so
nothing fails; it just attributes the value to a template it does not belong to,
surfacing as bad provenance nobody re-checks. ESMA RTS Annex V has no
vehicle-type field, Annex IV no rating and no cov-lite field. Verify a code
against the RTS OJ text first — the repo's own `"Annex 8 (SME)"` label was wrong
twice over (Annex VIII is leasing; corporate incl. SMEs is Annex IV).

Refs: #451
