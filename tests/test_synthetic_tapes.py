"""Synthetic pools must never displace a deal's real, published ground truth.

#484 gives four tape-less deals a synthetic Annex 2 pool so their pool charts
render. Two of them — Green Lion 2023-1 and 2024-1 — also publish quarterly
Notes & Cash reports, and those reports are what their committed answer keys
grade against; Green Lion 2024-1's reconciliation is the repo's **only**
``validated`` capability cell.

``_reconstruct_series`` used to select the tape path on the mere *presence* of
``tape_urls``, so registering a synthetic tape against either deal would have
moved it off the report path silently. These tests pin the precedence contract
that stops that, and pin its two counter-cases: a real tape still outranks
reports, and a deal whose only pool data is synthetic keeps the tape path
rather than becoming not-modelable.

#524 added the middle rank. #484 ranked only *synthetic* below a report, so a
**derived** tape — LoanWhiz's own rendering of a document the deal already
publishes — outranked the document it was rendered from, and Cairn CLO XVII sat
on the tape path by dispatch order rather than by decision. The contract now
reads, senior first: a **first-hand** tape, then the deal's **published
report**, then a **derived or synthetic** tape. The tests below pin all three
ranks, and pin that yielding names the reporting dates it sets aside rather than
narrowing a deal's series in silence.
"""

from __future__ import annotations

import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from loanwhiz.api import main as api_main
from loanwhiz.primitives import esma_tape_normaliser
from loanwhiz.config import DEAL_REGISTRY, GREEN_LION
from loanwhiz.domain.tape_provenance import TapeSourceKind, source_kind_for

_SYNTHETIC_TAPE = {
    "date": "2026-04-30",
    "url": "synthetic:data/tapes/synthetic/green_lion_2024_1_202604_synthetic_loan_tape.csv.gz",
}
_UNDECLARED_TAPE = {
    "date": "2026-04-30",
    "url": "https://example.invalid/some_published_tape.csv",
}
_DERIVED_TAPE = {
    "date": "2025-03-18",
    "url": (
        "derived+trustee-report:https://example.invalid/report.pdf#period=March%202025"
    ),
}



def _which_path(deal_id: str, deal: dict) -> str:
    """Run the real selector, reporting which adapter it chose.

    Only the two adapters are stubbed — both need the network and are covered
    by their own suites. The branch under test (``_reconstruct_series``'s
    selection) runs for real, against real registry entries.
    """
    with (
        patch.object(api_main, "_reconstruct_series_from_tapes", return_value="tapes"),
        patch.object(api_main, "_reconstruct_series_from_reports", return_value="reports"),
    ):
        return api_main._reconstruct_series(deal_id, deal)


@pytest.mark.parametrize("deal_id", ["green-lion-2023-1", "green-lion-2024-1"])
def test_a_synthetic_tape_does_not_displace_the_notes_cash_report_path(deal_id):
    """The graded Green Lion vintages keep their report-derived series."""
    deal = deepcopy(DEAL_REGISTRY[deal_id])
    assert deal["notes_cash_report_urls"], "precondition: the deal publishes Notes & Cash"
    deal["tape_urls"] = [_SYNTHETIC_TAPE]

    assert _which_path(deal_id, deal) == "reports"


def test_removing_the_tape_restores_the_report_path():
    """Round-trip: registering then removing the tape returns the deal unchanged."""
    deal = deepcopy(DEAL_REGISTRY["green-lion-2024-1"])
    before = _which_path("green-lion-2024-1", deal)

    deal["tape_urls"] = [_SYNTHETIC_TAPE]
    with_tape = _which_path("green-lion-2024-1", deal)

    deal["tape_urls"] = []
    assert _which_path("green-lion-2024-1", deal) == before == with_tape == "reports"


def test_a_deal_whose_only_pool_data_is_synthetic_keeps_the_tape_path():
    """Green Lion 2026-1's three tapes are synthetic and it publishes no Notes & Cash.

    Yielding to reports here would leave the repo's flagship deal not-modelable,
    which is a regression rather than honesty — the contract is "real data
    outranks generated", not "generated data is unusable".
    """
    deal = deepcopy(GREEN_LION)
    assert deal["tape_urls"], "precondition: the deal registers tapes"
    assert not deal.get("notes_cash_report_urls"), "precondition: no Notes & Cash report"

    assert _which_path("green-lion-2026-1", deal) == "tapes"


def test_a_real_tape_still_outranks_the_report_path():
    """The pre-#484 behaviour is preserved for any tape that is not synthetic."""
    deal = deepcopy(DEAL_REGISTRY["green-lion-2024-1"])
    deal["tape_urls"] = [_UNDECLARED_TAPE]

    assert _which_path("green-lion-2024-1", deal) == "tapes"


def test_a_mixed_tape_list_takes_the_tape_path():
    """One real tape is enough: the deal has genuine pool data to fold."""
    deal = deepcopy(DEAL_REGISTRY["green-lion-2024-1"])
    deal["tape_urls"] = [_SYNTHETIC_TAPE, _UNDECLARED_TAPE]

    assert _which_path("green-lion-2024-1", deal) == "tapes"


def test_an_undeclared_identifier_is_treated_as_first_hand():
    """``None`` means *not declared*, never *fabricated*.

    Reading an undeclared published file as anything less than the originator's
    own would be the mirror of the laundering ``tape_provenance`` exists to
    prevent, so it keeps the tape path exactly as it did before #524.
    """
    assert api_main._tape_is_first_hand(_UNDECLARED_TAPE["url"]) is True
    assert api_main._tape_is_first_hand(_SYNTHETIC_TAPE["url"]) is False
    assert api_main._tape_is_first_hand(_DERIVED_TAPE["url"]) is False


def test_first_hand_and_describes_real_assets_are_different_questions():
    """A derived tape describes real loans AND is not a first-hand account.

    The two facts must not collapse into one another: #484's rule branched on
    ``describes_real_assets``, which a derived tape satisfies, and that is
    exactly why it had no rank for one.
    """
    kind = source_kind_for(_DERIVED_TAPE["url"])
    assert kind is TapeSourceKind.DERIVED_FROM_INVESTOR_REPORT
    assert kind.describes_real_assets is True
    assert kind.first_hand is False


def test_a_derived_tape_yields_to_a_registered_report():
    """#524's rank: the document outranks LoanWhiz's rendering of the document.

    Cairn registers three ``derived+trustee-report:`` tapes (#471) and one Note
    Valuation Report (#495). The report is the deal's only Priority-of-Payments
    bearing source, so the ledger folds it.
    """
    deal = deepcopy(DEAL_REGISTRY["cairn-clo-xvii"])
    assert deal["notes_cash_report_urls"], "precondition: the deal registers a report"
    assert all(
        t["url"].startswith("derived+trustee-report:") for t in deal["tape_urls"]
    ), "precondition: every registered tape is derived"

    assert _which_path("cairn-clo-xvii", deal) == "reports"


def test_a_derived_tape_keeps_the_tape_path_when_no_report_is_registered():
    """Yielding needs somewhere to yield to.

    The counter-case to the rank above, and the same shape as Green Lion
    2026-1's: a deal with only a derived tape and no report keeps it, because
    yielding would leave it not-modelable rather than honest.
    """
    deal = deepcopy(DEAL_REGISTRY["cairn-clo-xvii"])
    deal["notes_cash_report_urls"] = []

    assert _which_path("cairn-clo-xvii", deal) == "tapes"


def test_one_first_hand_tape_outranks_a_report_even_beside_derived_ones():
    """The senior rank is not a majority vote — one first-hand tape is enough."""
    deal = deepcopy(DEAL_REGISTRY["cairn-clo-xvii"])
    deal["tape_urls"] = [*deal["tape_urls"], _UNDECLARED_TAPE]

    assert _which_path("cairn-clo-xvii", deal) == "tapes"


def test_yielding_names_the_reporting_dates_it_sets_aside():
    """A narrowed series must say so — the second half of the contract.

    Cairn's tapes are reconstructed from December 2024, February 2025 and March
    2025 trustee reports; the report it yields to is a January 2025 cut, so the
    two sources overlap on no period at all. Returning those dates is what stops
    a one-period screen reading as the deal's whole history.
    """
    deal = deepcopy(DEAL_REGISTRY["cairn-clo-xvii"])

    assert api_main._set_aside_tape_periods(deal) == (
        "2024-12-16",
        "2025-02-18",
        "2025-03-18",
    )


def test_a_deal_that_does_not_yield_sets_nothing_aside():
    """Nothing is set aside when nothing was displaced.

    ``()`` here means "the series folds every period the deal registers", so it
    must not be reachable from a deal that kept its tape path — otherwise the
    two states are indistinguishable to a caller.
    """
    assert api_main._set_aside_tape_periods(deepcopy(GREEN_LION)) == ()
    assert (
        api_main._set_aside_tape_periods(deepcopy(DEAL_REGISTRY["leone-arancio-2023-1"]))
        == ()
    )


def test_a_displaced_tape_stating_no_date_is_still_named():
    """The set-aside list is total over displaced tapes, never a filtered subset.

    Dropping a tape that states no period would be this rule committing, one
    level down, the silent narrowing it exists to prevent — and it would make the
    refusal contradict itself, listing fewer periods than the count beside them.
    """
    deal = deepcopy(DEAL_REGISTRY["cairn-clo-xvii"])
    del deal["tape_urls"][1]["date"]

    set_aside = api_main._set_aside_tape_periods(deal)

    assert len(set_aside) == len(deal["tape_urls"])
    assert set_aside[0] == "2024-12-16"
    assert set_aside[2] == "2025-03-18"
    assert deal["tape_urls"][1]["url"] in set_aside[1]


def test_the_refusal_names_no_cause_it_did_not_check():
    """A report-only deal reaches this refusal two ways; only one is about caches.

    ``_reconstruct_series_from_reports`` raises here both when the report will not
    resolve **and** when the deal has no extracted model, which consults neither a
    committed fixture nor a durable cache. Naming that cause anyway sends the
    reader to fix the wrong thing — the defect this refusal was rewritten to stop
    committing for the both-sources case.
    """
    deal = {"notes_cash_report_urls": [{"period": "X", "url": "https://e.invalid/r.pdf"}]}
    detail = api_main._not_modelable_deal("no-model-deal", deal).detail

    assert "could not fold" in detail
    assert "committed fixture" not in detail
    assert "durable cache" not in detail


def test_the_refusal_describes_what_the_deal_actually_registers():
    """A deal registering both sources must not be told it registers neither.

    Cairn yields to a report that resolves offline for no committed fixture, so
    the engine folds nothing — and the pre-#524 message sent the reader to add a
    tape that has been registered since #471.
    """
    deal = deepcopy(DEAL_REGISTRY["cairn-clo-xvii"])
    detail = api_main._not_modelable_deal("cairn-clo-xvii", deal).detail

    assert "neither" in detail
    assert "set aside" in detail
    for date in ("2024-12-16", "2025-02-18", "2025-03-18"):
        assert date in detail


# ---------------------------------------------------------------------------
# A tape committed in this repo must resolve the same from any directory
# ---------------------------------------------------------------------------


def _write_probe_tape(root, name="probe.csv"):
    tape = root / "data" / "tapes" / "synthetic" / name
    tape.parent.mkdir(parents=True, exist_ok=True)
    tape.write_text(
        "loan_identifier,current_balance,current_interest_rate_pct\n"
        "L1,100000.0,3.0\nL2,200000.0,4.0\n",
        encoding="utf-8",
    )
    return f"data/tapes/synthetic/{name}"


def test_a_committed_tape_resolves_independently_of_the_working_directory(
    tmp_path, monkeypatch
):
    """``run-demo-v2.sh`` starts uvicorn without cd-ing to the repo root.

    A bare relative identifier would therefore be read against whatever
    directory the demo was launched from, and ``_tape_analytics_period``
    degrades on a per-tape error rather than raising — so the Pool page would
    silently lose the period instead of failing.
    """
    body = _write_probe_tape(tmp_path)
    monkeypatch.setattr(esma_tape_normaliser, "_PACKAGE_ROOT", tmp_path)

    elsewhere = tmp_path / "some" / "other" / "cwd"
    elsewhere.mkdir(parents=True)
    monkeypatch.chdir(elsewhere)

    frame, channel = esma_tape_normaliser._load_tape(f"synthetic:{body}", None)

    assert len(frame) == 2
    assert channel == "synthetic", "the scheme survives the path resolution"


def test_resolution_leaves_urls_and_absolute_paths_alone(tmp_path, monkeypatch):
    """Only a package-relative body is rewritten; everything else is untouched."""
    monkeypatch.setattr(esma_tape_normaliser, "_PACKAGE_ROOT", tmp_path)

    for identifier in (
        "https://example.invalid/tape.csv",
        "synthetic:https://example.invalid/tape.csv",
        "file:///tmp/tape.csv",
        "/tmp/tape.csv",
    ):
        assert esma_tape_normaliser._resolve_committed_tape(identifier) == identifier


def test_a_missing_committed_tape_names_the_path_it_looked_for(tmp_path, monkeypatch):
    """A silent skip is the failure mode here, so the message has to be diagnosable."""
    monkeypatch.setattr(esma_tape_normaliser, "_PACKAGE_ROOT", tmp_path)

    with pytest.raises(FileNotFoundError) as excinfo:
        esma_tape_normaliser._resolve_committed_tape("synthetic:data/tapes/nope.csv")

    message = str(excinfo.value)
    assert "data/tapes/nope.csv" in message
    assert str(tmp_path) in message


# ---------------------------------------------------------------------------
# The registry's own committed tapes
# ---------------------------------------------------------------------------


def _registered_committed_tapes() -> list[tuple[str, str]]:
    """Every registered tape whose identifier names a file inside this repo."""
    from loanwhiz.domain.tape_provenance import underlying_url

    found = []
    for deal_id, deal in DEAL_REGISTRY.items():
        for tape in deal.get("tape_urls") or []:
            body = underlying_url(tape["url"])
            if "://" not in body and not body.startswith("/"):
                found.append((deal_id, tape["url"]))
    return found


def test_the_registry_holds_committed_tapes():
    """Guards the two tests below against passing on an empty list."""
    assert len(_registered_committed_tapes()) >= 4


@pytest.mark.parametrize(
    "deal_id,url", _registered_committed_tapes(), ids=lambda v: str(v)[:40]
)
def test_every_committed_tape_resolves_and_loads(deal_id, url, tmp_path, monkeypatch):
    """From a foreign working directory, which is how the demo actually starts.

    ``run-demo-v2.sh`` launches uvicorn without changing directory, and a tape
    that failed to resolve would be swallowed by ``_tape_analytics_period`` and
    disappear from the Pool page rather than raising.
    """
    monkeypatch.chdir(tmp_path)

    frame, channel = esma_tape_normaliser._load_tape(url, None)

    assert channel == "synthetic"
    assert len(frame) > 0
    assert "current_balance" in {column.lower() for column in frame.columns}


@pytest.mark.parametrize(
    "deal_id,url", _registered_committed_tapes(), ids=lambda v: str(v)[:40]
)
def test_every_committed_tape_has_its_analytics_seed(deal_id, url):
    """#483: the seed is named ``sha256(tape_url)``, so a re-identified tape
    orphans its seed and the offline demo silently drops that period."""
    from loanwhiz.api.main import _tape_seed_path

    seed = _tape_seed_path(url)
    assert seed.exists(), f"{deal_id}: no committed seed for {url}"
    payload = json.loads(seed.read_text(encoding="utf-8"))
    assert payload["data_source"] == "synthetic"
