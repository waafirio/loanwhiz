"""Tests for the Green Lion 2026-1 tape wiring in ``loanwhiz.config``.

Green Lion 2026-1 is its own deal: only its three 2026 tapes (Feb/Mar/Apr,
~EUR 1bn pool) belong to it. The 2024-2025 ``green-lion-2024-2025`` dataset is a
SEPARATE deal (~EUR 139bn) and is deliberately NOT chained in here — different
deals' loan tapes are not interchangeable. Asserts the 3-tape scope, chronological
ordering, that all tapes resolve from ``HF_BASE``, the irregular April filename,
and that Jan-2026 is absent.
"""

from __future__ import annotations

import json
from pathlib import Path

from loanwhiz import config
from loanwhiz.config import GREEN_LION, HF_BASE

TAPE_URLS = GREEN_LION["tape_urls"]


def test_deal_has_its_own_three_2026_tapes() -> None:
    assert len(TAPE_URLS) == 3
    # All three belong to Green Lion 2026-1 (HF_BASE / Hackathon_Data) — none are
    # drawn from the separate 2024-2025 dataset.
    for entry in TAPE_URLS:
        assert entry["url"].startswith(HF_BASE)
        assert "Hackathon_Data" in entry["url"]
        assert entry["date"] >= "2026-01-01"


def test_tape_history_is_chronologically_ordered() -> None:
    dates = [entry["date"] for entry in TAPE_URLS]
    assert dates == sorted(dates)
    # Strictly increasing — no duplicate dates would shadow a tape.
    assert len(set(dates)) == len(dates)
    assert dates[0] == "2026-02-28"
    assert dates[-1] == "2026-04-30"


def test_jan_2026_is_absent() -> None:
    dates = {entry["date"] for entry in TAPE_URLS}
    assert "2026-01-31" not in dates
    assert not any("202601" in entry["url"] for entry in TAPE_URLS)


def test_april_2026_irregular_filename_preserved() -> None:
    # The April-2026 tape keeps its irregular ``2026_1`` filename; downstream
    # (tests/test_esma_tape_normaliser.py) looks it up by this exact date.
    april = next(e for e in TAPE_URLS if e["date"] == "2026-04-30")
    assert april["url"] == f"{HF_BASE}/green_lion_2026_1_synthetic_loan_tape.csv"


def test_investor_reports_unchanged() -> None:
    assert len(GREEN_LION["investor_report_urls"]) == 3


# ---------------------------------------------------------------------------
# Runtime-file split (#399): register_deal persists to a SEPARATE runtime overlay
# (data/deals.runtime.json); the committed data/deals.json is never mutated at
# runtime; _load_deal_registry merges committed THEN runtime, tolerating an
# absent/malformed runtime file. The committed file path is pointed at a tmp file
# in these tests so the real committed registry is never touched either.
# ---------------------------------------------------------------------------


def _ctx(name: str = "XYZ 2026-1") -> dict:
    return {
        "deal_name": name,
        "prospectus_url": "http://example/p.pdf",
        "tape_urls": [],
        "investor_report_urls": [],
    }


def test_register_deal_writes_runtime_file_only(tmp_path) -> None:
    """register_deal persists into the runtime overlay; the committed file is untouched."""
    committed = tmp_path / "deals.json"
    runtime = tmp_path / "deals.runtime.json"
    committed.write_text(json.dumps({"committed-1": _ctx("Committed One")}), encoding="utf-8")

    overlay = config.register_deal("runtime-1", _ctx(), runtime_file=runtime)

    assert "runtime-1" in overlay
    # The runtime file now exists and carries only the runtime entry.
    assert runtime.exists()
    on_disk = json.loads(runtime.read_text(encoding="utf-8"))
    assert on_disk == {"runtime-1": _ctx()}
    # The committed file was never read into / merged with the runtime write.
    assert json.loads(committed.read_text(encoding="utf-8")) == {
        "committed-1": _ctx("Committed One")
    }


def test_load_merges_committed_then_runtime(tmp_path) -> None:
    """_load_deal_registry overlays committed then runtime; runtime overrides by id."""
    committed = tmp_path / "deals.json"
    runtime = tmp_path / "deals.runtime.json"
    committed.write_text(
        json.dumps({"shared": _ctx("Committed Shared"), "committed-only": _ctx("C")}),
        encoding="utf-8",
    )
    runtime.write_text(
        json.dumps({"shared": _ctx("Runtime Shared"), "runtime-only": _ctx("R")}),
        encoding="utf-8",
    )

    registry = config._load_deal_registry(data_file=committed, runtime_file=runtime)

    # In-code default always present.
    assert registry["green-lion-2026-1"] is GREEN_LION
    # Committed-only + runtime-only both surface.
    assert registry["committed-only"]["deal_name"] == "C"
    assert registry["runtime-only"]["deal_name"] == "R"
    # Runtime overrides the committed entry on a shared id.
    assert registry["shared"]["deal_name"] == "Runtime Shared"


def test_load_tolerates_absent_runtime_file(tmp_path) -> None:
    """A missing runtime file is ignored — committed deals still load."""
    committed = tmp_path / "deals.json"
    runtime = tmp_path / "deals.runtime.json"  # not created
    committed.write_text(json.dumps({"committed-1": _ctx()}), encoding="utf-8")

    registry = config._load_deal_registry(data_file=committed, runtime_file=runtime)

    assert "committed-1" in registry
    assert "green-lion-2026-1" in registry


def test_load_tolerates_malformed_runtime_file(tmp_path) -> None:
    """A corrupt runtime file is logged + skipped — committed deals still served."""
    committed = tmp_path / "deals.json"
    runtime = tmp_path / "deals.runtime.json"
    committed.write_text(json.dumps({"committed-1": _ctx()}), encoding="utf-8")
    runtime.write_text("{ this is not valid json", encoding="utf-8")

    registry = config._load_deal_registry(data_file=committed, runtime_file=runtime)

    # Malformed runtime ignored; committed + in-code default still load.
    assert "committed-1" in registry
    assert "green-lion-2026-1" in registry


def test_register_deal_atomic_write_no_partial_on_corrupt_prior(tmp_path) -> None:
    """A malformed pre-existing runtime file is tolerated: register_deal starts fresh."""
    runtime = tmp_path / "deals.runtime.json"
    runtime.write_text("{ broken", encoding="utf-8")

    overlay = config.register_deal("runtime-1", _ctx(), runtime_file=runtime)

    assert overlay == {"runtime-1": _ctx()}
    assert json.loads(runtime.read_text(encoding="utf-8")) == {"runtime-1": _ctx()}


def test_committed_deals_json_is_never_written_by_register(tmp_path, monkeypatch) -> None:
    """The module-default committed file is never mutated by a register_deal call.

    Guards the file-split invariant directly: even when register_deal is called with
    its default runtime target redirected to a tmp file, the committed
    data/deals.json sentinel is byte-identical before and after.
    """
    committed_before = Path(config.DEALS_DATA_FILE).read_bytes()
    runtime = tmp_path / "deals.runtime.json"

    config.register_deal("runtime-x", _ctx(), runtime_file=runtime)

    assert Path(config.DEALS_DATA_FILE).read_bytes() == committed_before
