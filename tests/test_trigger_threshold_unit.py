"""Threshold-unit normalisation when mapping extracted triggers (MODELING-GAPS C8).

The covenant extractor captures ``threshold_unit`` ("fraction", "percentage",
"bps", ...), but ``_map_extracted_trigger`` dropped it. The monitor's metrics
(``pool_balance_pct``, ``reserve_fund_ratio``, ``cumulative_loss_rate_pct``) are
all on a **percent** scale (0–100), so a prospectus threshold stated as a
fraction (``0.10`` meaning 10%) was compared against a percent metric — a 100×
error that silently turns a real breach into a non-event (or vice versa).

The mapping now normalises the threshold onto the metric's percent scale.
"""

from __future__ import annotations

from loanwhiz.api.main import _map_extracted_trigger


def _raw(**overrides) -> dict:
    raw = dict(
        name="clean_up_call_trigger",
        display_name="Clean-Up Call",
        description="Pool below 10% of original balance.",
        metric="pool_balance_pct",
        threshold=10.0,
        threshold_unit="percentage",
        direction="below",
        consequence="Optional redemption.",
        section_reference="§6",
        citation={},
    )
    raw.update(overrides)
    return raw


def test_fraction_threshold_normalised_to_percent():
    """A fraction threshold (0.10) on a percent metric becomes 10.0, not 0.10."""
    td = _map_extracted_trigger(_raw(threshold=0.10, threshold_unit="fraction"))
    assert td.threshold == 10.0


def test_percentage_threshold_unchanged():
    """A percentage threshold passes through unchanged."""
    td = _map_extracted_trigger(_raw(threshold=10.0, threshold_unit="percentage"))
    assert td.threshold == 10.0


def test_bps_threshold_normalised_to_percent():
    """A basis-points threshold (1000 bps) becomes 10.0 percent."""
    td = _map_extracted_trigger(_raw(threshold=1000.0, threshold_unit="bps"))
    assert td.threshold == 10.0


def test_none_threshold_stays_none():
    """A non-quantified threshold stays None regardless of unit."""
    td = _map_extracted_trigger(_raw(threshold=None, threshold_unit=None))
    assert td.threshold is None
