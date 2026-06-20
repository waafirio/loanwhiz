"""Tests for reconciliation-as-gate (#272, epic #258).

The gate wires the Reconciler (#270) as an automated governance gate over the
report path: extract (#271) → adapt (#267) → fold (``run_period``) → reconcile,
then mark the report fields the engine confirmed **to the cent** as
``reconciled=True`` in the ``ParsedReport``'s provenance, routing only the
*unreconciled + low-confidence* fields to human review.

Driven entirely offline against the committed Green Lion 2024-1 fixtures
(deterministic extract, no network/LLM) plus small synthetic ``ParsedReport``s
for the routing-logic units.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Import the ``loanwhiz.primitives`` package first: importing
# ``loanwhiz.domain.provenance`` as the very first import triggers a pre-existing
# circular import (domain.inputs ↔ primitives.capability_matrix → reconciler).
# Importing the primitives package first fully initialises both halves in order.
from loanwhiz.primitives import (
    DEFAULT_REVIEW_CONFIDENCE_THRESHOLD,
    ReconciliationGateResult,
    ReviewItem,
    apply_reconciliation,
    fields_for_human_review,
    reconcile_as_gate,
)
from loanwhiz.domain.provenance import FieldProvenance
from loanwhiz.primitives.report_extractor import (
    ParsedReport,
    ParsedReportPeriod,
    ReportedStep,
    ReportExtractInput,
    extract_report,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_GL_FIXTURES = (
    "green-lion-2024-1-september-2025.txt",
    "green-lion-2024-1-december-2025.txt",
    "green-lion-2024-1-march-2026.txt",
)
SEED_MODEL = (
    _REPO_ROOT / "src" / "loanwhiz" / "data" / "deals" / "seed" / "green-lion-2024-1-bv.json"
)


class _Model:
    """Duck-typed extracted model exposing ``.waterfalls`` (mirrors adapter test)."""

    def __init__(self, data: dict) -> None:
        self.waterfalls = data["waterfalls"]
        self.metadata = type("M", (), {"deal_name": "Green Lion 2024-1 B.V."})()


@pytest.fixture()
def deal_model() -> _Model:
    return _Model(json.loads(SEED_MODEL.read_text(encoding="utf-8")))


@pytest.fixture()
def gl_parsed_report() -> ParsedReport:
    """One ``ParsedReport`` with all 3 GL-2024-1 periods (deterministic extract).

    Each committed fixture is a single period; the deterministic extractor returns
    one period per call, so we splice the three into one report (the fold +
    reconciler read them positionally, oldest first).
    """
    periods: list[ParsedReportPeriod] = []
    for fixture in _GL_FIXTURES:
        text = (_REPO_ROOT / "tests" / "fixtures" / "notes_cash" / fixture).read_text(
            encoding="utf-8"
        )
        r = extract_report(
            ReportExtractInput(deal_name="Green Lion 2024-1 B.V.", text=text)
        ).output
        periods.extend(r.periods)
    return ParsedReport(
        deal_name="Green Lion 2024-1 B.V.",
        report_type="notes_and_cash",
        periods=periods,
        extraction_method="deterministic",
    )


@pytest.fixture()
def gate_result(
    gl_parsed_report: ParsedReport, deal_model: _Model
) -> ReconciliationGateResult:
    return reconcile_as_gate(gl_parsed_report, deal_model)


# ---------------------------------------------------------------------------
# End-to-end gate over GL-2024-1 (offline) — engine-computed steps get marked
# ---------------------------------------------------------------------------


def test_gate_runs_offline_and_returns_typed_result(
    gate_result: ReconciliationGateResult,
) -> None:
    assert isinstance(gate_result, ReconciliationGateResult)
    # The underlying reconciliation passed all 3 periods to the cent.
    assert gate_result.reconciliation.passed
    assert gate_result.reconciliation.periods_checked == 3
    assert gate_result.reconciled_field_count > 0


def test_engine_computed_pop_steps_are_marked_reconciled(
    gate_result: ReconciliationGateResult,
) -> None:
    """The engine-computed revenue lines (Class A interest (d), PDL/reserve
    replenishment (e)/(f)/(h)) reconcile to the cent → reconciled=True."""
    report = gate_result.report
    period0 = report.periods[0]
    # Locate the (d) Class A interest step index in the parsed revenue PoP.
    labels = [s.priority_label for s in period0.revenue_pop]
    for engine_label in ("(d)", "(e)", "(f)", "(h)"):
        idx = labels.index(engine_label)
        path = f"periods.0.revenue_pop.{idx}.amount"
        fp = report.provenance.get(path)
        assert fp is not None, f"no provenance entry created for {engine_label}"
        assert fp.reconciled is True, f"{engine_label} not marked reconciled"


def test_marked_fields_are_excluded_from_review(
    gate_result: ReconciliationGateResult,
) -> None:
    """No reconciled PoP-step field appears in the review list, regardless of
    extraction confidence (auto-trusted)."""
    review_paths = {item.field_path for item in gate_result.review_items}
    reconciled_paths = {
        path for path, fp in gate_result.report.provenance.items() if fp.reconciled
    }
    assert reconciled_paths, "expected some reconciled fields"
    assert reconciled_paths.isdisjoint(review_paths)


# ---------------------------------------------------------------------------
# The two headline cases the issue mandates
# ---------------------------------------------------------------------------


def test_reconciled_low_confidence_field_is_not_flagged() -> None:
    """A field the engine confirmed is NOT routed to review even when its
    extraction confidence is below the threshold — reconciled overrides confidence."""
    report = ParsedReport(
        deal_name="X",
        periods=[
            ParsedReportPeriod(
                reporting_date="2026-01-01",
                revenue_pop=[ReportedStep(priority_label="(d)", amount=100.0)],
            )
        ],
        # Pre-seed a LOW-confidence provenance entry on the (d) step.
        provenance={
            "periods.0.revenue_pop.0.amount": FieldProvenance(
                source="report", method="ocr+llm", confidence=0.2, reconciled=False
            )
        },
    )
    # The reconciler confirmed (d) to the cent.
    _mark_via_step(report, "revenue", "(d)", passed=True)

    items = fields_for_human_review(report, confidence_threshold=0.7)
    assert all(i.field_path != "periods.0.revenue_pop.0.amount" for i in items), (
        "a reconciled field must not be flagged even at 0.2 confidence"
    )
    assert report.provenance["periods.0.revenue_pop.0.amount"].reconciled is True


def test_unreconciled_low_confidence_field_is_flagged() -> None:
    """A field the engine could NOT confirm AND that is low-confidence IS routed."""
    report = ParsedReport(
        deal_name="X",
        periods=[ParsedReportPeriod(reporting_date="2026-01-01")],
        provenance={
            "periods.0.reserve_balance": FieldProvenance(
                source="report", method="ocr+llm", confidence=0.3, reconciled=False
            )
        },
    )
    items = fields_for_human_review(report, confidence_threshold=0.7)
    paths = {i.field_path for i in items}
    assert "periods.0.reserve_balance" in paths
    item = next(i for i in items if i.field_path == "periods.0.reserve_balance")
    assert item.confidence == pytest.approx(0.3)
    assert "low-confidence" in item.reason


def test_unreconciled_high_confidence_field_is_not_flagged() -> None:
    """An unreconciled field whose confidence is at/above the threshold is left alone."""
    report = ParsedReport(
        deal_name="X",
        periods=[ParsedReportPeriod(reporting_date="2026-01-01")],
        provenance={
            "periods.0.reserve_balance": FieldProvenance(
                source="report", method="deterministic", confidence=1.0, reconciled=False
            )
        },
    )
    assert fields_for_human_review(report, confidence_threshold=0.7) == []


def test_threshold_is_honored() -> None:
    report = ParsedReport(
        deal_name="X",
        periods=[ParsedReportPeriod(reporting_date="2026-01-01")],
        provenance={
            "periods.0.reserve_balance": FieldProvenance(
                source="report", method="ocr+llm", confidence=0.6, reconciled=False
            )
        },
    )
    # 0.6 is below 0.7 → flagged; at threshold 0.5 → not flagged (strictly-below).
    assert len(fields_for_human_review(report, confidence_threshold=0.7)) == 1
    assert fields_for_human_review(report, confidence_threshold=0.5) == []


def test_default_threshold_constant() -> None:
    assert DEFAULT_REVIEW_CONFIDENCE_THRESHOLD == 0.7


# ---------------------------------------------------------------------------
# apply_reconciliation unit behaviour
# ---------------------------------------------------------------------------


def test_apply_reconciliation_preserves_existing_provenance_metadata() -> None:
    """Marking reconciled only flips the flag — source/method/confidence/citation
    of an existing entry survive."""
    report = ParsedReport(
        deal_name="X",
        periods=[
            ParsedReportPeriod(
                reporting_date="2026-01-01",
                revenue_pop=[ReportedStep(priority_label="(d)", amount=100.0)],
            )
        ],
        provenance={
            "periods.0.revenue_pop.0.amount": FieldProvenance(
                source="report", method="ocr+llm", confidence=0.42, reconciled=False
            )
        },
    )
    _mark_via_step(report, "revenue", "(d)", passed=True)
    fp = report.provenance["periods.0.revenue_pop.0.amount"]
    assert fp.reconciled is True
    assert fp.source == "report"
    assert fp.method == "ocr+llm"
    assert fp.confidence == pytest.approx(0.42)


def test_apply_reconciliation_creates_entry_when_absent() -> None:
    """A deterministic PoP step has no pre-existing provenance; the gate creates a
    synthesized reconciled entry."""
    report = ParsedReport(
        deal_name="X",
        periods=[
            ParsedReportPeriod(
                reporting_date="2026-01-01",
                revenue_pop=[ReportedStep(priority_label="(d)", amount=100.0)],
            )
        ],
        provenance={},
    )
    _mark_via_step(report, "revenue", "(d)", passed=True)
    fp = report.provenance["periods.0.revenue_pop.0.amount"]
    assert fp.reconciled is True
    assert fp.source == "reconciled"
    assert fp.method == "computed"


def test_apply_reconciliation_skips_unpassed_steps() -> None:
    report = ParsedReport(
        deal_name="X",
        periods=[
            ParsedReportPeriod(
                reporting_date="2026-01-01",
                revenue_pop=[ReportedStep(priority_label="(d)", amount=100.0)],
            )
        ],
        provenance={},
    )
    marked = _mark_via_step(report, "revenue", "(d)", passed=False)
    assert marked == 0
    assert "periods.0.revenue_pop.0.amount" not in report.provenance


def test_apply_reconciliation_ignores_folded_label_with_no_step() -> None:
    """A reconciled label the report carries only as sub-items (no top-level PoP
    step) is silently skipped — nothing to mark."""
    report = ParsedReport(
        deal_name="X",
        periods=[
            ParsedReportPeriod(
                reporting_date="2026-01-01",
                revenue_pop=[ReportedStep(priority_label="(1)", amount=50.0)],
            )
        ],
        provenance={},
    )
    # Reconciler proves a folded "(b)" total that has no top-level step here.
    marked = _mark_via_step(report, "revenue", "(b)", passed=True)
    assert marked == 0


# ---------------------------------------------------------------------------
# Helpers — build a minimal ReconciliationReport carrying one step
# ---------------------------------------------------------------------------


def _mark_via_step(
    report: ParsedReport, waterfall_type: str, label: str, *, passed: bool
) -> int:
    """Apply a one-step reconciliation to ``report`` and return the count marked."""
    from loanwhiz.primitives.reconciler import (
        PeriodValidation,
        ReconciliationReport,
        StepReconciliation,
        WaterfallReconciliation,
    )

    step = StepReconciliation(
        priority=label,
        recipient="x",
        engine_amount=100.0,
        report_amount=100.0,
        delta=0.0,
        source="engine",
        passed=passed,
    )
    empty_wf = WaterfallReconciliation(
        waterfall_type=("redemption" if waterfall_type == "revenue" else "revenue"),
        steps=[],
        engine_total=0.0,
        report_total=0.0,
        available_funds=0.0,
    )
    this_wf = WaterfallReconciliation(
        waterfall_type=waterfall_type,
        steps=[step],
        engine_total=100.0,
        report_total=100.0,
        available_funds=100.0,
    )
    pv = PeriodValidation(
        reporting_date="2026-01-01",
        period_label="P0",
        revenue=this_wf if waterfall_type == "revenue" else empty_wf,
        redemption=this_wf if waterfall_type == "redemption" else empty_wf,
    )
    recon = ReconciliationReport(deal_name="X", periods=[pv])
    return apply_reconciliation(report, recon)
