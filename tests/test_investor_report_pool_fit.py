"""The fit specs must be reproducible from the committed report fixtures.

A synthetic pool that contradicts the deal's own investor report is worse than
no pool, so every figure a pool is fitted to has to be traceable to a published
statement. These tests hold that end of the contract: the committed fit specs
re-derive byte-for-byte from the committed text fixtures, and every way the
parse can go quietly wrong — a dropped table row, an arrears bucket that maps
to nothing, an aggregate the report does not state — refuses instead.
"""

from __future__ import annotations

import json

import pytest

from scripts import investor_report_pool_fit as fit_tool


@pytest.mark.parametrize("deal_id", sorted(fit_tool.DEALS))
def test_the_committed_fit_spec_reproduces_from_its_fixture(deal_id):
    """Re-parsing the fixture must reproduce the committed spec exactly.

    This is what makes the generated tapes reproducible from the repo alone:
    the fixture is the published report's text, the spec is what the parser
    reads out of it, and nothing in between is hand-edited.
    """
    committed = json.loads(fit_tool.fit_path(deal_id).read_text(encoding="utf-8"))

    assert fit_tool.build_fit(deal_id).as_json() == committed


@pytest.mark.parametrize("deal_id", sorted(fit_tool.DEALS))
def test_every_fitted_figure_cites_the_section_it_was_read_from(deal_id):
    """A figure with no citation is indistinguishable from one that was invented."""
    spec = json.loads(fit_tool.fit_path(deal_id).read_text(encoding="utf-8"))

    assert spec["fitted"], "a fit spec with no fitted aggregate fits nothing"
    for name, entry in spec["fitted"].items():
        assert entry["cited"].startswith("section "), f"{name} cites no report section"
        assert entry["unit"], f"{name} states no unit"
    for name, distribution in spec["distributions"].items():
        assert distribution["cited"].startswith("section "), f"{name} cites no section"


def test_the_iberian_reports_declare_epc_and_property_type_absent():
    """Absence is a fact worth stating, not an empty slot.

    Neither Iberian report publishes an energy-performance or property-type
    stratification, so the generated tape omits both columns — and says why,
    rather than leaving a consumer to read the omission as "the pool has none".
    """
    for deal_id in ("leone-arancio-2023-1", "sol-lion-ii"):
        spec = json.loads(fit_tool.fit_path(deal_id).read_text(encoding="utf-8"))
        absent = {entry["canonical_column"]: entry for entry in spec["absent"]}

        assert set(absent) == {"epc_label", "property_type"}
        for entry in absent.values():
            assert len(entry["reason"]) > 20, "an absence needs a stated cause"

    for deal_id in ("green-lion-2023-1", "green-lion-2024-1"):
        spec = json.loads(fit_tool.fit_path(deal_id).read_text(encoding="utf-8"))
        assert spec["absent"] == [], "the Dutch report publishes both"
        assert "epc_label" in spec["distributions"]
        assert "property_type" in spec["distributions"]


# ---------------------------------------------------------------------------
# Every way the parse can go quietly wrong
# ---------------------------------------------------------------------------


def test_a_distribution_that_lost_a_row_is_refused():
    """#469: reconcile against the source's own total, refuse on divergence.

    A stratification table that dropped a row still looks like a distribution —
    plausible buckets, shares summing to something near 100 — so this check is
    the only thing between a lossy extraction and a confidently wrong pool.
    """
    distribution = {
        "cited": "section 15 Property Description (p22)",
        "buckets": [
            {"label": "House", "balance": 600_000.0, "count": 6},
            {"label": "Apartment", "balance": 200_000.0, "count": 2},
        ],
    }

    fit_tool.reconcile_distribution(distribution, 800_000.0, "property_type")

    with pytest.raises(fit_tool.ReportParseError) as excinfo:
        fit_tool.reconcile_distribution(distribution, 1_000_000.0, "property_type")
    assert "property_type" in str(excinfo.value)
    assert "refusing" in str(excinfo.value).lower()


def test_an_unmapped_arrears_bucket_is_refused_not_silently_current():
    """#471: an unrecognised bucket must not fall through to "performing".

    The tape's vocabulary is closed, so an unmapped label would be written as
    an ``arrears_bucket`` value the normaliser does not read — landing those
    loans in ``current_pct`` at full confidence.
    """
    assert fit_tool.map_arrears_bucket("No Arrears") == ("Performing", 0)
    assert fit_tool.map_arrears_bucket("90 - 179 Days") == ("180+d", 90)
    assert fit_tool.map_arrears_bucket("Defaulted (>12M)") == ("default", 365)

    with pytest.raises(fit_tool.ReportParseError) as excinfo:
        fit_tool.map_arrears_bucket("Under judicial review")
    assert "performing" in str(excinfo.value).lower()


def test_every_published_arrears_bucket_maps():
    """The buckets both families actually print, kept honest against the fixtures."""
    for deal_id in sorted(fit_tool.DEALS):
        spec = json.loads(fit_tool.fit_path(deal_id).read_text(encoding="utf-8"))
        for bucket in spec["distributions"]["arrears_bucket"]["buckets"]:
            fit_tool.map_arrears_bucket(bucket["label"])


def test_an_unclassifiable_interest_type_bucket_is_refused():
    """Leone Arancio nests its floating buckets, so labels are read lexically.

    A label naming neither a fixed rate nor a floating index raises rather than
    landing a quarter of the pool in whichever bucket happened to be last.
    """
    assert fit_tool.classify_rate_type("Fixed 10Y") == "Fixed"
    assert fit_tool.classify_rate_type("Floating Rate BCE") == "Floating"
    assert fit_tool.classify_rate_type("3M") == "Floating"

    with pytest.raises(fit_tool.ReportParseError):
        fit_tool.classify_rate_type("Unknown")


def test_rate_type_shares_refuse_when_they_do_not_account_for_the_pool():
    """A nested table that lost a row would otherwise still sum tidily."""
    whole = [("Fixed Rate", [0.0, 74.36]), ("Floating Rate BCE", [0.0, 25.64])]
    assert fit_tool.rate_type_shares(whole, 1) == {"Fixed": 74.36, "Floating": 25.64}

    with pytest.raises(fit_tool.ReportParseError) as excinfo:
        fit_tool.rate_type_shares([("Fixed Rate", [0.0, 74.36])], 1)
    assert "100%" in str(excinfo.value)


def test_a_missing_aggregate_is_refused_rather_than_defaulted():
    """The whole point is that no fitted figure is ever invented."""
    with pytest.raises(fit_tool.ReportParseError) as excinfo:
        fit_tool._require({"Number of loans": [10.0]}, "Net principal balance", "section 1")
    assert "Net principal balance" in str(excinfo.value)


def test_the_cut_off_date_is_read_from_the_report_not_inferred():
    """Leone Arancio's May 2026 report carries a 31-03-2026 cut-off.

    Inferring the reporting date from the report's period label would be wrong
    for that deal and right for its sibling — the silent kind of wrong.
    """
    import re

    assert fit_tool.parse_cutoff(
        ["Portfolio Cut off Date", "31-03-2026", "30-04-2023"],
        re.compile(r"^Portfolio Cut off Date"),
    ) == "2026-03-31"
    assert fit_tool.parse_cutoff(
        ["Portfolio Cut-off Date", "30 Apr 2026"],
        re.compile(r"^Portfolio Cut-off Date"),
    ) == "2026-04-30"

    with pytest.raises(fit_tool.ReportParseError):
        fit_tool.parse_cutoff(["Portfolio Cut off Date"], re.compile(r"^Portfolio Cut off Date"))


def test_the_committed_cut_offs_differ_between_the_two_iberian_deals():
    """Pins the fact the previous test exists for, against the real fixtures."""
    leone = json.loads(fit_tool.fit_path("leone-arancio-2023-1").read_text(encoding="utf-8"))
    sol = json.loads(fit_tool.fit_path("sol-lion-ii").read_text(encoding="utf-8"))

    assert leone["source"]["period"] == sol["source"]["period"] == "May 2026"
    assert leone["reporting_date"] == "2026-03-31"
    assert sol["reporting_date"] == "2026-04-30"


def test_a_forbearance_bucket_is_recorded_rather_than_absorbed():
    """Leone Arancio's Payment Holiday bucket is a claim the tape cannot make.

    It sits outside the report's days-past-due ladder, so the loans are written
    as Performing — but the share that was moved is named in the spec's notes,
    because "the tape says performing" and "the report says current" are not
    the same statement.
    """
    spec = json.loads(fit_tool.fit_path("leone-arancio-2023-1").read_text(encoding="utf-8"))
    moved = [note for note in spec["notes"] if note.startswith("Payment Holiday")]

    assert len(moved) == 1, "the concession must be stated once, with its share"
    assert "0.198% of balance" in moved[0]
    assert "119 loans" in moved[0]

    sol = json.loads(fit_tool.fit_path("sol-lion-ii").read_text(encoding="utf-8"))
    assert not [n for n in sol["notes"] if n.startswith("Payment Holiday")], (
        "Sol-Lion II publishes no such bucket; the note must not be boilerplate"
    )
