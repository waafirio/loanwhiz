# LoanWhiz Primitive Catalogue

> **Version:** 0.1.0 · **Status:** Framework operational; primitives in-progress

This document is the authoritative reference for the LoanWhiz structured-finance
primitive framework. It covers the base interface, how to register a new primitive,
the current primitive catalogue, and the rules for confidence scoring and citations.

---

## 1. Introduction

### What is a primitive?

A **primitive** is a versioned, typed, self-describing unit of structured-finance
computation. Each primitive:

- Accepts a **validated Pydantic input** and returns a **validated Pydantic output**.
- Attaches a **confidence score** (0.0–1.0) grounded in verifiable data properties.
- Includes **source citations** that trace every claim back to a specific document or tape row.
- Records an **audit entry** capturing the input hash, timestamp, and execution duration for governance replay.

Primitives are the building blocks that the LangGraph agent DAG executor composes to
answer structured-finance questions. A new team can add a primitive without touching
any core framework code — only the primitive file and its test file need to be created.

> **Consumable as an MCP server.** The same registry is packaged as a governed
> [Model Context Protocol](https://modelcontextprotocol.io) server under
> [`mcp/`](../../mcp/README.md). Each `live` (endpoint-reachable) primitive is
> exposed as an MCP tool whose `inputSchema` is the primitive's own typed
> Pydantic input; calling it runs `execute()` and returns the full
> `PrimitiveResult` envelope — output **plus** the governance evidence pack
> (confidence, citations, audit entry). A `primitives://catalogue` resource
> lists all 8 registered primitives (live + `library-only`) with honest
> reachability, so a third party can consume the framework over MCP without
> rewriting any primitive. `library-only` primitives appear in the catalogue
> resource but are not advertised as callable tools.

---

### The `Primitive[InputT, OutputT]` interface

Every primitive subclasses the generic abstract base class defined in
`src/loanwhiz/primitives/base.py`:

```python
from abc import ABC, abstractmethod
from typing import ClassVar, Generic, TypeVar
from loanwhiz.primitives.base import BaseInput, PrimitiveResult
from pydantic import BaseModel

InputT = TypeVar("InputT", bound=BaseInput)
OutputT = TypeVar("OutputT", bound=BaseModel)

class Primitive(ABC, Generic[InputT, OutputT]):
    name: ClassVar[str]        # snake_case identifier, e.g. "waterfall_runner"
    version: ClassVar[str]     # semver string, e.g. "1.0.0"
    description: ClassVar[str] # one-line description for the registry

    @abstractmethod
    def execute(self, input: InputT) -> PrimitiveResult[OutputT]: ...

    @classmethod
    def describe(cls) -> PrimitiveMetadata: ...  # machine-readable metadata
```

**Class attributes (required on every concrete subclass):**

| Attribute | Type | Purpose |
|---|---|---|
| `name` | `str` | Unique snake_case identifier. Used in `AuditEntry` and the registry. |
| `version` | `str` | Semver string (`"0.1.0"`). Bumped when the output schema changes. |
| `description` | `str` | One-sentence description shown in the registry catalogue. |

---

### `PrimitiveResult` — what `execute()` returns

```python
class PrimitiveResult(BaseModel, Generic[OutputT]):
    output: OutputT           # The typed computation result
    confidence: float         # 0.0–1.0 (see §4 Confidence Scoring)
    citations: list[Citation] # Source references (see §5 Citations)
    audit_entry: AuditEntry   # Execution metadata for governance
```

**Fields:**

| Field | Type | Description |
|---|---|---|
| `output` | `OutputT` | The primitive's typed computation result. |
| `confidence` | `float` | How certain the primitive is about its output. `1.0` = fully deterministic / rule-based; lower values signal data quality issues, missing fields, or model uncertainty. |
| `citations` | `list[Citation]` | Source references that trace the output back to specific document fragments or tape rows. An empty list is valid for purely synthetic outputs, but should be avoided when source material is available. |
| `audit_entry` | `AuditEntry` | Execution metadata: primitive name, version, SHA-256 hash of the input, UTC timestamp, and wall-clock duration in milliseconds. Use `AuditEntry.now(...)` to construct this. |

---

### `BaseInput` — the input base class

All primitive input schemas must subclass `BaseInput` (not plain `BaseModel`):

```python
class BaseInput(BaseModel):
    model_config = {"frozen": True}  # inputs are immutable

    def input_hash(self) -> str:
        """SHA-256 hex digest of the canonical JSON serialisation."""
```

The `frozen = True` config prevents accidental mutation. The `input_hash()` method
produces the deterministic hash stored in `AuditEntry.input_hash`, enabling replay.

---

### `@register_primitive` — registering with the framework

The `register_primitive` decorator stamps metadata onto the class and adds it to the
global `PRIMITIVE_REGISTRY` singleton:

```python
from loanwhiz.primitives import register_primitive

@register_primitive(
    name="my_primitive",
    version="1.0.0",
    description="One-line description of what this primitive computes.",
    author="acme-team",          # defaults to "loanwhiz" for core primitives
    tags=["cashflow", "esma"],   # used for tag-based discovery
)
class MyPrimitive(Primitive[MyInput, MyOutput]):
    name = "my_primitive"
    version = "1.0.0"
    description = "One-line description of what this primitive computes."

    def execute(self, input: MyInput) -> PrimitiveResult[MyOutput]:
        ...
```

**Decorator parameters:**

| Parameter | Required | Description |
|---|---|---|
| `name` | Yes | Unique snake_case identifier. Raises `ValueError` if already registered. |
| `version` | Yes | Semver string. |
| `description` | Yes | One-line description for the catalogue. |
| `author` | No | Team/author identifier. Defaults to `"loanwhiz"`. |
| `tags` | No | List of tags for `PRIMITIVE_REGISTRY.list_by_tag()`. |

**Discovering registered primitives:**

```python
from loanwhiz.primitives import PRIMITIVE_REGISTRY

# List all
PRIMITIVE_REGISTRY.list_all()            # -> list[PrimitiveRegistration]

# Filter by tag
PRIMITIVE_REGISTRY.list_by_tag("esma")  # -> list[PrimitiveRegistration]

# JSON-serialisable catalogue dict
PRIMITIVE_REGISTRY.describe()            # -> dict[str, dict]
```

---

## 2. Contribution Guide

Follow these steps to add a new primitive to the framework.

### Step 1 — Subclass `Primitive`

Create `src/loanwhiz/primitives/<your_name>.py`. Define your input and output
Pydantic models first, then the primitive class:

```python
"""<Your primitive name> — <one-line description>.

<Longer description: what structured-finance computation this does, what
input data it expects, what it produces.>
"""

from __future__ import annotations

import time
from pydantic import BaseModel, Field
from loanwhiz.primitives.base import (
    AuditEntry,
    BaseInput,
    Citation,
    Primitive,
    PrimitiveResult,
)
from loanwhiz.primitives import register_primitive
```

### Step 2 — Define `InputModel` (subclass `BaseInput`)

```python
class YourInput(BaseInput):
    """Validated input for YourPrimitive.

    Attributes:
        field_one: <description>.
        field_two: <description>.
    """
    field_one: str = Field(..., description="<description>")
    field_two: float = Field(..., ge=0.0, description="<description>")
```

Rules:
- Subclass `BaseInput`, not `BaseModel` — this gives you `input_hash()`.
- Mark every field with `Field(...)` and a `description`. These flow into the
  auto-generated JSON schema surfaced by `Primitive.describe()`.
- Keep inputs frozen (inherited from `BaseInput`). Never mutate inside `execute`.

### Step 3 — Define `OutputModel` (subclass `BaseModel`)

```python
class YourOutput(BaseModel):
    """Output produced by YourPrimitive.

    Attributes:
        result_a: <description>.
        result_b: <description>.
    """
    result_a: float = Field(..., description="<description>")
    result_b: list[str] = Field(default_factory=list, description="<description>")
```

### Step 4 — Implement `execute()`

```python
@register_primitive(
    name="your_primitive",
    version="0.1.0",
    description="One-line description of what this computes.",
    tags=["<tag1>", "<tag2>"],
)
class YourPrimitive(Primitive[YourInput, YourOutput]):
    name = "your_primitive"
    version = "0.1.0"
    description = "One-line description of what this computes."

    def execute(self, input: YourInput) -> PrimitiveResult[YourOutput]:
        t0 = time.perf_counter()

        # --- Your computation here ---
        result_a = 0.0
        result_b = []
        citations: list[Citation] = []
        confidence = 1.0  # start at 1.0; lower it for missing/uncertain data

        # Example: lower confidence if expected fields are missing
        expected_fields = 10
        present_fields = 8
        confidence = present_fields / expected_fields  # 0.8

        # Example: attach a citation
        citations.append(Citation(
            document="green_lion_202602_loan_tape.csv",
            page_or_row=42,
            excerpt="CURR_INT_RT=0.0425 from row 42",
        ))

        duration_ms = (time.perf_counter() - t0) * 1000
        return PrimitiveResult(
            output=YourOutput(result_a=result_a, result_b=result_b),
            confidence=confidence,
            citations=citations,
            audit_entry=AuditEntry.now(
                primitive_name=self.name,
                version=self.version,
                input_hash=input.input_hash(),
                duration_ms=duration_ms,
            ),
        )
```

**Mandatory implementation checklist inside `execute()`:**

- [ ] Record start time with `time.perf_counter()`.
- [ ] Call `input.input_hash()` and pass it to `AuditEntry.now(...)`.
- [ ] Use `AuditEntry.now(...)` — do not construct `AuditEntry` manually.
- [ ] Set `confidence` to a value grounded in a verifiable heuristic (see §4).
- [ ] Attach at least one `Citation` per document fragment relied upon (see §5).
- [ ] Return `PrimitiveResult` — never raise to signal low confidence; return
  the result with a low `confidence` score and explain it in a citation excerpt.

### Step 5 — Decorate with `@register_primitive`

Apply the decorator **before** the class definition (as shown above). The decorator:
1. Stamps `__primitive_name__`, `__primitive_version__`, `__primitive_description__`,
   `__primitive_author__`, and `__primitive_tags__` on the class.
2. Calls `PRIMITIVE_REGISTRY.register(cls, ...)` so the primitive is discoverable
   from the moment the module is imported.

The `name` argument to `@register_primitive` must match the `name` class attribute
exactly — they serve different roles (decorator registers; class attribute is used
at runtime inside `AuditEntry`) but must agree.

### Step 6 — Add tests in `tests/test_<name>.py`

```python
# tests/test_your_primitive.py
import pytest
from loanwhiz.primitives import PRIMITIVE_REGISTRY
from loanwhiz.primitives.your_name import YourInput, YourOutput, YourPrimitive

# --- Registry ---

def test_registered():
    reg = PRIMITIVE_REGISTRY.get("your_primitive")
    assert reg is not None
    assert reg.version == "0.1.0"

def test_registered_by_tag():
    names = [r.name for r in PRIMITIVE_REGISTRY.list_by_tag("<tag1>")]
    assert "your_primitive" in names

# --- execute() ---

def test_execute_happy_path():
    prim = YourPrimitive()
    inp = YourInput(field_one="value", field_two=1.0)
    result = prim.execute(inp)
    assert isinstance(result.output, YourOutput)
    assert 0.0 <= result.confidence <= 1.0
    assert result.audit_entry.primitive_name == "your_primitive"
    assert len(result.audit_entry.input_hash) == 64  # SHA-256 hex

def test_confidence_lower_on_missing_data():
    # Exercise the confidence degradation path specific to your primitive.
    ...

def test_citations_present():
    prim = YourPrimitive()
    inp = YourInput(field_one="value", field_two=1.0)
    result = prim.execute(inp)
    assert len(result.citations) > 0
    assert result.citations[0].document != ""
    assert result.citations[0].excerpt != ""
```

**Test coverage requirements:**
- Happy-path `execute()` that checks output type, confidence range, and audit entry.
- At least one test for each confidence-degradation path (what happens when data is sparse?).
- At least one test that verifies citations are non-empty and well-formed.
- Registration tests: `PRIMITIVE_REGISTRY.get(name)` returns the expected version and tags.

### Step 7 — Open a PR

```bash
git add src/loanwhiz/primitives/<your_name>.py tests/test_<your_name>.py
git commit -m "feat(primitives): add <your_name> primitive"
git push origin your-branch
gh pr create --title "feat(primitives): add <your_name> primitive (#<issue>)" \
  --body "Closes #<issue> ..." \
  --base liz/epic/12   # or the relevant epic branch
```

The PR body must include:
- What the primitive computes and why it belongs in the framework.
- Which confidence heuristics it uses and why (see §4).
- Which source documents its citations reference.
- Evidence that tests pass locally (`pytest tests/test_<your_name>.py -v`).

---

## 3. Primitive Catalogue

All primitives currently tracked in the framework. Status key:
- **implemented** — class exists in `src/`, tests pass.
- **in-progress** — issue open, implementation not yet merged.

| Name | Version | Status | Description | Input (key fields) | Output (key fields) | Tags |
|---|---|---|---|---|---|---|
| `esma_tape_normaliser` | 0.1.0 | in-progress | Normalise an ESMA loan-level CSV tape into pool-level analytics: weighted-average rate, arrears breakdown, EPC/geo/rate-type distributions. Multi-annex schema detection (Annex 2–8). | `tape_path: str`, `annex: int \| None` | `pool_balance: float`, `wac: float`, `arrears_buckets: dict`, `epc_distribution: dict`, `field_coverage: float` | `esma`, `tape`, `pool-analytics` |
| `waterfall_runner` | 0.1.0 | in-progress | Execute an extracted deal waterfall (from `deal_model.json`) against monthly tape collections. Produces computed distributions per tranche per period with a full audit trace. | `waterfall: list[WaterfallStep]`, `collections: MonthlyCollections` | `distributions: list[TrancheDist]`, `period: str`, `residual: float` | `cashflow`, `waterfall` |
| `covenant_monitor` | 0.1.0 | in-progress | Check pool tape metrics against prospectus trigger thresholds. Tracks proximity (% of threshold) and flags breaches and near-misses with citations to prospectus definitions. | `triggers: list[Trigger]`, `tape_metrics: PoolMetrics`, `period: str` | `compliance: list[TriggerStatus]`, `breach_count: int`, `near_miss_count: int` | `compliance`, `covenant`, `trigger` |
| `report_verifier` | 0.1.0 | in-progress | Compare waterfall-computed distributions against investor report actuals. Flags line-item discrepancies (match/mismatch/delta). Confidence degraded when report parsing is incomplete. | `computed: list[TrancheDist]`, `reported: list[ReportLine]`, `period: str` | `verification: list[VerificationLine]`, `match_rate: float` | `verification`, `reporting` |
| `cashflow_projector` | 0.1.0 | in-progress | Project forward cashflows under base and stress scenarios (e.g. 2× default rate, rate shift). Uses `waterfall_runner` internally. Produces 12-month projections per tranche, scenario comparison. | `deal_model: DealModel`, `scenarios: list[Scenario]`, `horizon_months: int` | `projections: dict[str, list[TrancheDist]]`, `scenario_labels: list[str]` | `cashflow`, `projection`, `stress` |
| `audit_logger` | 0.1.0 | implemented | Wrap any primitive call with provenance metadata: input hash, output, confidence score, citations, timestamp, model version, human-review flag. Follows FINOS AI Governance Framework patterns for replayable traces. | `primitive: Primitive`, `input: BaseInput`, `review_threshold: float` | `result: PrimitiveResult`, `flagged_for_review: bool`, `trace_id: str` | `governance`, `audit`, `finos` |
| `collections_aggregator` | 0.1.0 | in-progress | Aggregate per-loan tape rows into period-level waterfall inputs: total interest, principal, prepayments, recoveries, defaults — bucketed by period. Used to bridge raw ESMA tape output to `waterfall_runner` input. | `tape_rows: list[LoanRow]`, `period: str` | `collections: MonthlyCollections`, `loan_count: int`, `coverage: float` | `esma`, `tape`, `aggregation`, `waterfall` |

---

## 4. Confidence Scoring Guide

### The 0.0–1.0 scale

Every `PrimitiveResult.confidence` must lie in `[0.0, 1.0]`:

| Range | Meaning | When to use |
|---|---|---|
| `1.0` | Fully deterministic. Output is mechanically derived from complete, validated inputs. | Rule-based computation on a fully populated tape with all expected fields present. |
| `0.8–0.99` | High confidence. Minor data gaps or a small fraction of missing fields, but the computation is sound. | A tape where 9/10 expected ESMA fields are present; the missing field has a safe default. |
| `0.5–0.79` | Moderate confidence. Meaningful data gaps or structural ambiguity that the primitive had to resolve heuristically. | A waterfall step whose condition relies on a defined term that the extractor could not locate. |
| `0.2–0.49` | Low confidence. Significant structural uncertainty or large missing-data fractions. Output is a best-effort estimate. | A tape annex that could not be positively identified; a waterfall run against estimated collections. |
| `0.0–0.19` | Very low / unreliable. Output should not be used for decisions without human review. | Parsing failed for more than half the expected structure; LLM extraction returned contradictory values. |

**Never use LLM self-assessment as the confidence score.** Asking a model "how
confident are you?" produces scores that are poorly calibrated, inconsistent across
runs, and unauditable. Every score must be derivable from a mechanical property of
the input data.

---

### Grounding confidence in verifiable heuristics

**Choose a heuristic that is computable from the data, not from the model's feeling.**

#### Heuristic 1 — Field coverage fraction

*Use when:* The computation depends on a set of named fields (e.g. ESMA tape columns,
prospectus section headings). Confidence degrades proportionally to missing fields.

```python
expected_fields = {"CURR_INT_RT", "OUTSTANDING_BALANCE", "ARREARS_BALANCE", "EPC_LABEL"}
present_fields = {f for f in expected_fields if f in tape_row and tape_row[f] is not None}
confidence = len(present_fields) / len(expected_fields)
# e.g. 3/4 present → confidence = 0.75
```

#### Heuristic 2 — Record count match

*Use when:* The output is an aggregation over N records and the primitive expected
a specific count (e.g. from a prior period's known pool size).

```python
expected_records = 2_500   # from prior period or deal model
actual_records = len(tape_rows)
coverage = min(actual_records / expected_records, 1.0)
confidence = coverage  # 0.0 if no records; 1.0 if at or above expected
```

#### Heuristic 3 — Structural completeness

*Use when:* The output is a structured object (e.g. a waterfall) with a known
expected shape. Count required sections present vs. expected.

```python
required_sections = ["revenue_priority", "redemption_priority", "post_enforcement"]
found_sections = [s for s in required_sections if s in extracted_waterfall]
confidence = len(found_sections) / len(required_sections)
```

#### Heuristic 4 — Cross-validation delta

*Use when:* The same value appears in multiple sources (e.g. tape aggregate vs.
investor report). Confidence degrades with the relative discrepancy.

```python
tape_total = sum(row["OUTSTANDING_BALANCE"] for row in tape_rows)
report_total = investor_report["pool_balance"]
relative_delta = abs(tape_total - report_total) / max(report_total, 1e-9)
confidence = max(0.0, 1.0 - relative_delta * 10)  # 1% delta → 0.90; 10% → 0.0
```

---

### Combining multiple heuristics

When a primitive depends on several independent data properties, combine heuristics
by taking the **minimum** (the weakest link governs the overall confidence):

```python
confidence = min(field_coverage, record_coverage, structural_completeness)
```

Or a **weighted product** when the heuristics are correlated and you want a smoother
degradation:

```python
confidence = (field_coverage ** 0.5) * (record_coverage ** 0.3) * (structural_completeness ** 0.2)
```

Document the chosen formula in the primitive's docstring and in the `excerpt` field
of a dedicated `Citation(document="confidence_formula", ...)`.

---

### Known limitations

- These heuristics measure **data completeness**, not **computational correctness**.
  A tape with all fields present but with corrupt values scores `1.0` on field
  coverage but may produce a wrong output. Cross-validation heuristics (#4) catch
  some corruption; peer review catches the rest.
- The `confidence` score is not a probability in the statistical sense. Do not
  treat `0.8` as "80% chance of being right." Treat it as "20% of the expected
  data quality is missing."
- Confidence is per-result, not per-field. If you need field-level uncertainty,
  include it in the `OutputModel` (e.g. a `quality: dict[str, float]` field).

---

## 5. Citation Guide

### What is a `Citation`?

A `Citation` traces a specific claim in the primitive's output back to a named
source document and a locatable fragment within it.

```python
class Citation(BaseModel):
    document: str               # Human-readable document name or URL
    page_or_row: int | str | None  # Page number, row index, ESMA field code, etc.
    excerpt: str                # Verbatim or lightly summarised fragment
```

**Fields:**

| Field | Type | Required | Description |
|---|---|---|---|
| `document` | `str` | Yes | Document name or URL. Use the filename for local files; the URL for remote assets. Should be stable enough for a human to locate the source. |
| `page_or_row` | `int \| str \| None` | No | Locator within the document. Use an integer for page numbers or tape row indices. Use a string for ESMA field codes (`"CURR_INT_RT"`), prospectus section numbers (`"5.2.1"`), or any other structured reference. `None` when the citation is document-level. |
| `excerpt` | `str` | Yes | The verbatim text, numerical value, or concise summary of the fragment relied upon. This is what an auditor reads to verify the computation. Never leave this empty. |

---

### Constructing meaningful citations

**Example 1 — ESMA tape row**

```python
Citation(
    document="green_lion_202602_1_synthetic_loan_tape.csv",
    page_or_row=1042,   # row index (0-based)
    excerpt="CURR_INT_RT=0.0425, OUTSTANDING_BALANCE=187500.00, ARREARS_BALANCE=0.00",
)
```

**Example 2 — Prospectus section**

```python
Citation(
    document="green-lion-2026-1-prospectus.pdf",
    page_or_row="5.2.1",  # section number, or page=73 if section is ambiguous
    excerpt="The Available Distribution Amount shall be applied in the following order of priority: (i) …",
)
```

**Example 3 — Investor report**

```python
Citation(
    document="monthly-investor-report-green-lion-2026-1-february-2026.pdf",
    page_or_row=3,
    excerpt="Class A1 Interest: EUR 42,187.50; Class A1 Principal: EUR 1,250,000.00",
)
```

**Example 4 — Computed / derived value (no external source)**

```python
Citation(
    document="waterfall_runner:internal",
    page_or_row=None,
    excerpt="Computed from 11 waterfall steps; residual after senior/junior tranches: EUR 3,241.18",
)
```

---

### Citation discipline rules

1. **One citation per relied-upon fragment.** If the computation reads 3 rows and 1 prospectus
   section, attach 4 citations — not one aggregated citation.

2. **Never cite a document you did not actually read.** If the input included a path to
   a prospectus but the primitive only used pre-extracted data, cite the extracted
   JSON/cache, not the PDF.

3. **Excerpts must be specific enough to verify.** `"From the prospectus"` is useless.
   `"Section 5.2.1: Available Distribution Amount applied as follows…"` is verifiable.

4. **Confidence-lowering decisions need citations.** When you lower confidence because
   a field is missing, cite the absence explicitly:

   ```python
   Citation(
       document="green_lion_202602_1_synthetic_loan_tape.csv",
       page_or_row="EPC_LABEL",
       excerpt="Field EPC_LABEL absent in 143/2500 rows; confidence reduced by 0.057",
   )
   ```

5. **Internal computations that are not traceable to an external source** use the
   `document="<primitive_name>:internal"` convention (Example 4 above).
