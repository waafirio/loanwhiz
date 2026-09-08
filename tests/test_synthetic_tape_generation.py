"""A committed synthetic pool must reproduce the report it was fitted to.

The failure this suite exists to prevent is a pool that looks plausible and
contradicts its own deal's published figures — which would make the comparison
charts confidently wrong rather than honestly empty. So every committed tape is
re-derived from its committed fit spec, driven through the **real** normaliser,
and checked against the aggregates the investor report states.
"""

from __future__ import annotations

import gzip
import json

import pandas as pd
import pytest

import loanwhiz.primitives  # noqa: F401  (registers the primitives)
from loanwhiz.primitives.esma_tape_normaliser import (
    EsmaTapeInput,
    EsmaTapeNormaliser,
)
from scripts import generate_synthetic_tapes as generator
from scripts.investor_report_pool_fit import DEALS, fit_path, map_arrears_bucket

DEAL_IDS = sorted(DEALS)
IBERIAN = ("leone-arancio-2023-1", "sol-lion-ii")
DUTCH = ("green-lion-2023-1", "green-lion-2024-1")


def _spec(deal_id: str) -> dict:
    return json.loads(fit_path(deal_id).read_text(encoding="utf-8"))


def _committed_path(deal_id: str):
    return generator.TAPE_DIR / generator.tape_filename(_spec(deal_id))


@pytest.fixture(scope="module")
def normalised() -> dict[str, dict]:
    """Every committed tape, through the real normaliser, once."""
    outputs = {}
    for deal_id in DEAL_IDS:
        result = EsmaTapeNormaliser().execute(
            EsmaTapeInput(file_url=f"synthetic:{_committed_path(deal_id)}")
        )
        outputs[deal_id] = result.model_dump()
    return outputs


# ---------------------------------------------------------------------------
# Reproducibility
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("deal_id", DEAL_IDS)
def test_generation_is_deterministic(deal_id):
    """Re-running the generator reproduces the committed rows exactly.

    This is what makes a committed tape auditable rather than merely present:
    the fit spec plus this script is the whole derivation, and anyone can
    re-run it and diff.
    """
    rebuilt = generator.build_tape(_spec(deal_id))
    committed = pd.read_csv(_committed_path(deal_id))

    pd.testing.assert_frame_equal(
        rebuilt.reset_index(drop=True), committed.reset_index(drop=True), check_dtype=False
    )


@pytest.mark.parametrize("deal_id", DEAL_IDS)
def test_the_committed_file_is_the_generator_s_own_output(deal_id):
    """Guards the gap between "regenerates" and "was regenerated".

    ``assert_frame_equal`` passes on a file whose bytes drifted from the
    generator's writer — a different quoting or line ending would still parse
    to the same frame — so the committed bytes are compared too.
    """
    spec = _spec(deal_id)
    expected = generator.build_tape(spec).to_csv(index=False, lineterminator="\n")

    with gzip.open(_committed_path(deal_id), "rt", encoding="utf-8") as handle:
        assert handle.read() == expected


# ---------------------------------------------------------------------------
# The contract: reproduce the fit, or refuse
# ---------------------------------------------------------------------------


def test_the_generator_refuses_a_pool_that_misses_its_fit():
    """A pool contradicting its own fit must not reach disk.

    The tolerance is tight enough that moving a single loan's balance by 1% is
    caught: a synthetic pool's whole claim to honesty is that it reproduces
    published figures, so "close" is not the standard.
    """
    deal_id = "green-lion-2023-1"
    spec = _spec(deal_id)
    frame = generator.build_tape(spec)

    generator.reconcile(frame, spec)  # the committed pool reconciles

    tampered = frame.copy()
    tampered.loc[0, "current_balance"] = tampered.loc[0, "current_balance"] * 1.01
    with pytest.raises(generator.FitDivergence) as excinfo:
        generator.reconcile(tampered, spec)

    message = str(excinfo.value)
    assert "pool_balance_eur" in message
    assert "refusing to write it" in message.lower()


def test_the_guard_covers_every_fitted_aggregate():
    """A guard that checked only the easy figures would pass anything.

    Each fitted key must appear in the reconciliation's own list of what it
    checked, so adding a figure to a fit spec cannot silently go unverified.
    """
    for deal_id in DEAL_IDS:
        spec = _spec(deal_id)
        checked = generator.reconcile(generator.build_tape(spec), spec)

        for key in spec["fitted"]:
            assert key in checked, f"{deal_id}: {key} is fitted but never verified"
        for column in spec["distributions"]:
            assert any(
                name.startswith(column) or name.startswith("arrears[")
                for name in checked
            ), f"{deal_id}: {column} is fitted but never verified"


# ---------------------------------------------------------------------------
# Through the real normaliser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("deal_id", DEAL_IDS)
def test_the_normalised_tape_reports_the_published_aggregates(deal_id, normalised):
    """The figures a reader sees on the Pool page are the report's own."""
    fitted = _spec(deal_id)["fitted"]
    output = normalised[deal_id]["output"]

    assert output["loan_count"] == int(fitted["loan_count"]["value"])
    assert output["pool_balance_eur"] == pytest.approx(
        fitted["pool_balance_eur"]["value"], abs=0.01
    )
    stats = output["pool_stats"]
    assert stats["wtd_coupon_pct"] == pytest.approx(fitted["wtd_coupon_pct"]["value"], abs=0.001)
    assert stats["wtd_ltv"] == pytest.approx(fitted["wtd_ltv_pct"]["value"], abs=0.001)
    assert stats["wtd_seasoning"] == pytest.approx(
        fitted["wtd_seasoning_months"]["value"], abs=0.001
    )
    assert stats["wtd_remaining_term"] == pytest.approx(
        fitted["wtd_remaining_term_months"]["value"], abs=0.001
    )


@pytest.mark.parametrize("deal_id", DEAL_IDS)
def test_every_committed_tape_reports_synthetic_provenance(deal_id, normalised):
    """#483's whole point: the claim travels with the tape, not with a filename."""
    assert normalised[deal_id]["output"]["data_source"] == "synthetic"


@pytest.mark.parametrize("deal_id", DEAL_IDS)
def test_the_arrears_breakdown_is_stated_not_defaulted(deal_id, normalised):
    """#471: an absent arrears column would report a 100%-performing pool."""
    breakdown = normalised[deal_id]["output"]["arrears_breakdown"]

    assert breakdown, "an empty breakdown means the tape states arrears in no form"
    assert breakdown["current_pct"] < 100.0, "every one of these pools has arrears"
    assert sum(breakdown.values()) == pytest.approx(100.0, abs=0.01)


@pytest.mark.parametrize("deal_id", IBERIAN)
def test_the_iberian_arrears_counts_match_the_report_exactly(deal_id, normalised):
    """Their reports count loans, so there is no approximation to hide behind."""
    spec = _spec(deal_id)
    buckets = spec["distributions"]["arrears_bucket"]["buckets"]
    total = sum(bucket["count"] for bucket in buckets)

    wanted: dict[str, float] = {}
    for bucket in buckets:
        tape_bucket, _ = map_arrears_bucket(bucket["label"])
        wanted[tape_bucket] = wanted.get(tape_bucket, 0.0) + bucket["count"]

    breakdown = normalised[deal_id]["output"]["arrears_breakdown"]
    rendered = {
        "Performing": breakdown["current_pct"],
        "<29d": breakdown["arrears_1_2m_pct"],
        "180+d": breakdown["arrears_180d_plus_pct"],
        "default": breakdown["default_pct"],
    }
    for tape_bucket, want in wanted.items():
        assert rendered[tape_bucket] == pytest.approx(want / total * 100, abs=0.001)


# ---------------------------------------------------------------------------
# What the source does not publish, the tape does not claim
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("deal_id", IBERIAN)
def test_an_unpublished_column_is_omitted_rather_than_invented(deal_id, normalised):
    """Neither Iberian report publishes EPC or property type.

    Emitting the columns with a made-up distribution would be the exact failure
    this whole child is fitted to avoid, so they are absent — and the platform
    says "Unknown ABS" at reduced confidence rather than claiming Annex 2
    conformance the tape does not have.
    """
    frame = pd.read_csv(_committed_path(deal_id), nrows=5)
    assert "epc_label" not in frame.columns
    assert "property_type" not in frame.columns

    output = normalised[deal_id]["output"]
    assert output["epc_breakdown"] is None
    assert output["property_type_breakdown"] is None
    assert output["annex_detected"] == "Unknown ABS"
    assert normalised[deal_id]["confidence"] < 1.0


@pytest.mark.parametrize("deal_id", DUTCH)
def test_a_published_column_is_carried_and_detects_as_annex_2(deal_id, normalised):
    """The Dutch report publishes both, so these tapes are Annex 2 conforming."""
    frame = pd.read_csv(_committed_path(deal_id), nrows=5)
    assert "epc_label" in frame.columns
    assert "property_type" in frame.columns

    output = normalised[deal_id]["output"]
    assert output["annex_detected"] == "Annex 2 (RMBS)"
    assert output["epc_breakdown"]
    assert normalised[deal_id]["confidence"] == 1.0


@pytest.mark.parametrize("deal_id", DEAL_IDS)
def test_the_tape_carries_no_column_its_fit_spec_declares_absent(deal_id):
    """The declaration and the file must agree, in both directions."""
    spec = _spec(deal_id)
    columns = set(pd.read_csv(_committed_path(deal_id), nrows=1).columns)

    for entry in spec["absent"]:
        assert entry["canonical_column"] not in columns
    for column in spec["distributions"]:
        if column == "rate_type":
            assert "rate_type" in columns
        else:
            assert column in columns or column == "arrears_bucket"


@pytest.mark.parametrize("deal_id", DEAL_IDS)
def test_the_registry_points_at_the_tape_the_generator_writes(deal_id):
    """The registered identifier and the generator's filename must agree.

    They are produced in two places — ``deals.json`` by hand, the file by the
    script — so a regenerated tape under a new reporting period would leave the
    registry pointing at the old name. That fails loudly at load today, but only
    for someone who runs it; this pins the pair.
    """
    from loanwhiz.config import DEAL_REGISTRY

    spec = _spec(deal_id)
    registered = [tape["url"] for tape in DEAL_REGISTRY[deal_id]["tape_urls"]]

    assert registered == [generator.registered_url(spec)]
    assert generator.tape_filename(spec) in registered[0]
    assert "synthetic" in generator.tape_filename(spec), (
        "the registry census in test_tape_provenance keys off the filename"
    )
