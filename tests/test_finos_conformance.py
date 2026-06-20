"""Tests for the FINOS AI Governance Framework conformance mapping.

Covers:
- The control catalogue is complete (all 23 mitigation ids, no duplicates).
- Every control has a valid status and a non-empty rationale, and every risk
  it references exists in the risk catalogue.
- The framework verdict (`is_framework_conformant`) is True for the shipped
  mapping, and the summary counts are internally consistent.
- Per-primitive conformance covers every registered primitive and always
  includes the universal base-contract controls.
"""

from __future__ import annotations

from loanwhiz.governance import (
    FINOS_CONTROL_CATALOGUE,
    FINOS_RISK_CATALOGUE,
    FinosControl,
    finos_conformance_summary,
    is_framework_conformant,
    primitive_conformance,
)
from loanwhiz.governance.finos_conformance import _UNIVERSAL_PRIMITIVE_CONTROLS

# The framework's published mitigation catalogue (single-page.html): 15
# preventative + 8 detective = 23 controls.
_EXPECTED_MITIGATION_IDS = {
    "AIR-PREV-002",
    "AIR-PREV-003",
    "AIR-PREV-005",
    "AIR-PREV-006",
    "AIR-PREV-007",
    "AIR-PREV-008",
    "AIR-PREV-010",
    "AIR-PREV-012",
    "AIR-PREV-014",
    "AIR-PREV-017",
    "AIR-PREV-018",
    "AIR-PREV-019",
    "AIR-PREV-020",
    "AIR-PREV-022",
    "AIR-PREV-023",
    "AIR-DET-001",
    "AIR-DET-004",
    "AIR-DET-009",
    "AIR-DET-011",
    "AIR-DET-013",
    "AIR-DET-015",
    "AIR-DET-016",
    "AIR-DET-021",
}


class TestControlCatalogue:
    def test_all_23_mitigations_present(self) -> None:
        ids = {c.control_id for c in FINOS_CONTROL_CATALOGUE}
        assert ids == _EXPECTED_MITIGATION_IDS

    def test_no_duplicate_control_ids(self) -> None:
        ids = [c.control_id for c in FINOS_CONTROL_CATALOGUE]
        assert len(ids) == len(set(ids))
        assert len(ids) == 23

    def test_every_control_has_valid_status(self) -> None:
        valid = {"satisfied", "partial", "not_applicable"}
        for c in FINOS_CONTROL_CATALOGUE:
            assert c.status in valid, f"{c.control_id} has bad status {c.status}"

    def test_every_control_has_nonempty_rationale(self) -> None:
        for c in FINOS_CONTROL_CATALOGUE:
            assert c.rationale.strip(), f"{c.control_id} has empty rationale"

    def test_every_control_has_evidence(self) -> None:
        for c in FINOS_CONTROL_CATALOGUE:
            assert c.loanwhiz_evidence, f"{c.control_id} has no evidence"

    def test_category_matches_id_prefix(self) -> None:
        for c in FINOS_CONTROL_CATALOGUE:
            if c.control_id.startswith("AIR-PREV-"):
                assert c.category == "preventative"
            elif c.control_id.startswith("AIR-DET-"):
                assert c.category == "detective"
            else:  # pragma: no cover - guard against a typo'd id
                raise AssertionError(f"unexpected id prefix: {c.control_id}")

    def test_addressed_risks_exist_in_risk_catalogue(self) -> None:
        for c in FINOS_CONTROL_CATALOGUE:
            for risk in c.addresses_risks:
                assert risk in FINOS_RISK_CATALOGUE, (
                    f"{c.control_id} references unknown risk {risk}"
                )


class TestFrameworkVerdict:
    def test_shipped_mapping_is_conformant(self) -> None:
        assert is_framework_conformant() is True

    def test_summary_counts_sum_to_total(self) -> None:
        s = finos_conformance_summary()
        counts = s["counts"]
        assert counts["satisfied"] + counts["partial"] + counts["not_applicable"] == (
            s["total_controls"]
        )
        assert s["total_controls"] == 23

    def test_summary_is_json_serialisable(self) -> None:
        import json

        # Round-trips without error → safe for the API response + JSONL.
        json.loads(json.dumps(finos_conformance_summary()))

    def test_a_failing_control_breaks_conformance(self) -> None:
        # An out-of-catalogue / failing posture must flip the verdict. We can't
        # construct an invalid pydantic status, so emulate by checking the
        # per-control predicate the verdict reduces over.
        bad = FinosControl.model_construct(
            control_id="AIR-X-999",
            title="synthetic failing control",
            category="detective",
            addresses_risks=[],
            status="failed",  # not a non-failing status
            rationale="synthetic",
            loanwhiz_evidence=[],
        )
        assert bad.is_conformant is False


class TestPrimitiveConformance:
    def test_covers_every_registered_primitive(self) -> None:
        import loanwhiz.primitives  # noqa: F401 — populates PRIMITIVE_REGISTRY
        from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY

        pc = primitive_conformance()
        for reg in PRIMITIVE_REGISTRY.list_all():
            assert reg.name in pc, f"{reg.name} missing from primitive_conformance()"
            assert pc[reg.name], f"{reg.name} has no mapped controls"

    def test_every_primitive_has_universal_controls(self) -> None:
        pc = primitive_conformance()
        assert pc, "expected at least one primitive"
        for name, controls in pc.items():
            for universal in _UNIVERSAL_PRIMITIVE_CONTROLS:
                assert universal in controls, (
                    f"{name} missing universal control {universal}"
                )

    def test_control_ids_are_in_catalogue(self) -> None:
        catalogue_ids = {c.control_id for c in FINOS_CONTROL_CATALOGUE}
        for name, controls in primitive_conformance().items():
            for cid in controls:
                assert cid in catalogue_ids, (
                    f"{name} maps unknown control {cid}"
                )
