# Adding a New Primitive — Checklist

Quick-reference for contributors. Full context in [CATALOGUE.md](CATALOGUE.md).

---

## Before you start

- [ ] Read `docs/primitives/CATALOGUE.md` — understand `Primitive[InputT, OutputT]`,
  `PrimitiveResult`, `BaseInput`, `Citation`, and `AuditEntry`.
- [ ] Check the catalogue table (§3) — confirm your primitive is not already planned
  or in-progress.
- [ ] Open a GitHub issue under epic #12 (SF Primitives) describing what the primitive
  computes and what data it consumes. Get a thumbs-up before implementing.

---

## Implementation checklist

### 1. Create `src/loanwhiz/primitives/<your_name>.py`

- [ ] Define `YourInput(BaseInput)` — all fields with `Field(...)` and `description`.
  `BaseInput` is frozen; do not add mutable fields.
- [ ] Define `YourOutput(BaseModel)` — all fields with `Field(...)` and `description`.
- [ ] Subclass `Primitive[YourInput, YourOutput]` and set `name`, `version`,
  `description` class attributes.
- [ ] Apply `@register_primitive(name=..., version=..., description=..., tags=[...])`
  immediately above the class definition.
- [ ] Implement `execute(self, input: YourInput) -> PrimitiveResult[YourOutput]`:
  - [ ] Start timer: `t0 = time.perf_counter()`
  - [ ] Compute output
  - [ ] Set `confidence` via a verifiable heuristic (field coverage, record count,
    structural completeness, or cross-validation delta — see CATALOGUE §4)
  - [ ] Attach `Citation` objects for every document fragment relied upon
    (see CATALOGUE §5)
  - [ ] Build audit entry: `AuditEntry.now(primitive_name=self.name, version=self.version, input_hash=input.input_hash(), duration_ms=(time.perf_counter()-t0)*1000)`
  - [ ] Return `PrimitiveResult(output=..., confidence=..., citations=..., audit_entry=...)`

### 2. Create `tests/test_<your_name>.py`

- [ ] `test_registered()` — `PRIMITIVE_REGISTRY.get("your_name")` returns expected version.
- [ ] `test_registered_by_tag()` — `list_by_tag(...)` includes your primitive.
- [ ] `test_execute_happy_path()` — output is the right type; confidence in [0, 1];
  audit entry has correct `primitive_name` and a 64-char `input_hash`.
- [ ] `test_confidence_degradation()` — at least one test that exercises the path
  where confidence is lowered (missing fields, sparse data, etc.).
- [ ] `test_citations_present()` — `result.citations` is non-empty; `document` and
  `excerpt` are non-empty strings.
- [ ] Run locally: `pytest tests/test_<your_name>.py -v` — all tests pass.

### 3. Confidence scoring — do not skip

- [ ] Confidence is derived from a **mechanical property of the input data**, not
  from an LLM's self-reported certainty.
- [ ] At least one confidence-lowering path is documented in the primitive's docstring.
- [ ] If confidence can reach `0.0`, the docstring explains the condition and what
  the caller should do (flag for human review via `audit_logger`).

### 4. Citations — do not skip

- [ ] Every document fragment relied upon has a `Citation`.
- [ ] Each `Citation.excerpt` is specific enough for an auditor to verify
  (not just `"from the prospectus"`).
- [ ] Missing-field / low-quality decisions are cited explicitly (see CATALOGUE §5,
  rule 4).

---

## PR requirements

- [ ] PR title: `feat(primitives): add <your_name> primitive (#<issue>)`
- [ ] PR base branch: `liz/epic/12` (SF Primitives epic)
- [ ] PR body includes: what the primitive computes, which confidence heuristic(s)
  it uses, which source documents its citations reference, and evidence that
  local tests pass.
- [ ] No changes outside `src/loanwhiz/primitives/<your_name>.py` and
  `tests/test_<your_name>.py` unless agreed in the issue.

---

## Review criteria

The reviewer will check:

1. `execute()` sets up the timer, calls `input.input_hash()`, and uses
   `AuditEntry.now()` — not a manual `AuditEntry(...)` with a hardcoded timestamp.
2. Confidence is grounded — the reviewer must be able to look at the input and
   independently compute the same confidence score.
3. Citations are specific — every `Citation.excerpt` cites an actual value or text
   fragment, not just a document name.
4. Tests cover the confidence-degradation path (not just the happy path).
5. The primitive is registered (`@register_primitive`) and tests verify this.
