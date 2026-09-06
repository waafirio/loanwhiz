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

## 2026-09-06 · decision · #470

A regulatory locator asserts **field identity, not value conformance** — ask what
the field is *defined as*, never what it is named. A scheme, unit or datum type in
that definition is part of the identity, so a near-match is a different datum: an
S&P industry is not `CRPL14` (NACE), a country not `CRPL10` (NUTS-3), a price per
100 of par not `CRPL41` (an amount), an obligor *name* not `CRPL4` (an identifier).
Where identity does hold, carry the source's own words — `Senior Secured Loan`, not
`SNDB`: translating into the RTS vocabulary is a second mapping with no source
behind it. Check a column's unit against a figure the document reconciles itself.

Refs: #470
