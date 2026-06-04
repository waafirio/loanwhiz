"""End-to-end verification that the multi-period machinery is non-degenerate
over the deal's *full* tape history (currently 27 monthly periods, #165).

#163 wired Green Lion's 27-month history into ``GREEN_LION["tape_urls"]``. The
risk this module guards against is a future regression that silently *truncates*
the period list — a slice, a dedup, an off-by-one cap — that would leave the
API and the agent tooling quietly analysing a subset while still returning a
plausible-looking response. A count-only assertion would not catch a bug that
echoes one period N times; these tests therefore assert both the *count*
(== ``len(GREEN_LION["tape_urls"])``) and that the periods are genuinely
*distinct* and *chronological*.

Everything is offline: the only thing mocked is the network/normaliser boundary
(``EsmaTapeNormaliser`` / ``CovenantMonitor``), via a per-URL ``side_effect``
keyed on the tape URL so each period gets a distinct deterministic dump —
mirroring the pattern established in ``tests/test_api.py`` after #163. No
network, no live Gemini/Docling.

Every count is derived from ``len(GREEN_LION["tape_urls"])`` (never a literal
27) so the suite stays correct if the deal's history changes.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from loanwhiz.agent.tools import MAX_VERBATIM_PERIODS, check_covenants
from loanwhiz.api import app
from loanwhiz.config import GREEN_LION
from loanwhiz.primitives.base import AuditEntry, PrimitiveResult
from loanwhiz.primitives.covenant_monitor import (
    CovenantOutput,
    TriggerStatus,
)

client = TestClient(app)

# The single source of truth for "how many periods is the full history". Drive
# every expectation off this, not a literal 27, so the suite tracks the deal.
TAPE_COUNT = len(GREEN_LION["tape_urls"])

# This module only carries its weight when the history is genuinely multi-period
# *and* above the agent-tooling summarisation threshold — both true at 27. The
# regression it guards (silent truncation of a long history; the > threshold
# summarisation branch) only exists when there *is* a long history to truncate,
# so if the deal's history ever legitimately shrinks to <= MAX_VERBATIM_PERIODS
# the whole module skips cleanly rather than failing — it no longer applies.
pytestmark = pytest.mark.skipif(
    TAPE_COUNT <= MAX_VERBATIM_PERIODS,
    reason=(
        f"deal history ({TAPE_COUNT} tapes) is at/below MAX_VERBATIM_PERIODS "
        f"({MAX_VERBATIM_PERIODS}); the multi-period verification does not apply"
    ),
)


# ---------------------------------------------------------------------------
# Offline fakes (mirror tests/test_api.py)
# ---------------------------------------------------------------------------


class _FakeResult:
    """Stand-in for a PrimitiveResult whose ``output`` model_dumps to a dict."""

    def __init__(self, dump: dict):
        self._dump = dump

    @property
    def output(self):
        return self

    def model_dump(self):
        return self._dump


_FAKE_AUDIT = AuditEntry(
    primitive_name="test",
    version="0.1.0",
    input_hash="a" * 64,
    executed_at="2026-04-30T00:00:00+00:00",
    duration_ms=1.0,
)


def _tape_output_dump(tape: dict) -> dict:
    """A full EsmaTapeOutput-shaped dict for one config tape.

    Each tape gets its own config date as ``reporting_date`` and a pool balance
    that declines monotonically across the history (older tape = larger balance),
    so the 27 periods are provably *distinct* — a degenerate response that
    repeats one period would collapse these and fail the assertions below.
    """
    index = GREEN_LION["tape_urls"].index(tape)
    pool_balance = 1_050_000_000.0 - index * 1_000_000.0
    return {
        "reporting_date": tape["date"],
        "asset_class": "RMBS",
        "transaction_name": "Green Lion 2026-1 B.V.",
        "loan_count": 1000 - index,
        "pool_balance_eur": pool_balance,
        "pool_stats": {"wtd_ltv": 65.0, "wtd_coupon_pct": 3.6},
        "arrears_breakdown": {
            "current_pct": 98.0,
            "arrears_1_2m_pct": 1.0,
            "arrears_180d_plus_pct": 0.5,
            "default_pct": 0.5,
        },
        "epc_breakdown": {"A": 40.0, "B": 60.0},
        "rate_type_breakdown": {"Fixed": 100.0},
        "property_type_breakdown": {"House": 70.0, "Apartment": 30.0},
        "geographic_breakdown": {"NL-NH": 50.0, "NL-ZH": 50.0},
        "annex_detected": "Annex 2 (RMBS)",
    }


def _by_url_normaliser_side_effect():
    """A ``side_effect`` mapping each tape's input URL to its distinct dump.

    Both ``/compliance`` and ``/tape-analytics`` normalise
    ``EsmaTapeInput(file_url=url)`` once per tape; keying the fake off
    ``inp.file_url`` returns one consistent, distinct result per period
    regardless of call order or caching.
    """
    by_url = {
        tape["url"]: _FakeResult(_tape_output_dump(tape))
        for tape in GREEN_LION["tape_urls"]
    }
    return lambda inp: by_url[inp.file_url]


# ---------------------------------------------------------------------------
# Cache isolation (mirror tests/test_api.py::_isolated_tape_cache)
# ---------------------------------------------------------------------------


@pytest.fixture
def _isolated_tape_cache(tmp_path):
    """Point the tape-analytics cache at a clean tmp dir and empty memo.

    Keeps the analytics-cache deterministic across this module's runs: each
    test starts cold (no on-disk artifact, no in-process memo) and never
    touches the shared ``/tmp/loanwhiz_cache/tape_analytics`` dir.
    """
    from loanwhiz.api import main as api_main

    saved_memo = dict(api_main._TAPE_ANALYTICS_MEMO)
    api_main._TAPE_ANALYTICS_MEMO.clear()
    with patch("loanwhiz.api.main.TAPE_ANALYTICS_CACHE_DIR", str(tmp_path)):
        yield tmp_path
    api_main._TAPE_ANALYTICS_MEMO.clear()
    api_main._TAPE_ANALYTICS_MEMO.update(saved_memo)


# ---------------------------------------------------------------------------
# 1. /tape-analytics — one distinct row per tape, chronological
# ---------------------------------------------------------------------------


def test_tape_analytics_spans_all_periods_distinct_and_chronological(
    _isolated_tape_cache,
):
    """One analytics row per tape (== len(tape_urls)), distinct, in order.

    Guards against silent truncation/dedup: not only must the row count equal
    the tape count, but the per-period dates must match the config order exactly
    and the pool balances must all be distinct — a response that repeated one
    period N times would pass a count check yet fail here.
    """
    tapes = GREEN_LION["tape_urls"]

    with patch("loanwhiz.api.main.EsmaTapeNormaliser") as MockNorm:
        MockNorm.return_value.execute.side_effect = _by_url_normaliser_side_effect()
        resp = client.get("/deal/green-lion-2026-1/tape-analytics")

    assert resp.status_code == 200
    body = resp.json()

    # One row per tape — driven off the config, not a literal.
    assert len(body) == TAPE_COUNT
    assert MockNorm.return_value.execute.call_count == TAPE_COUNT

    # Chronological order: rows follow the config's tape ordering exactly.
    assert [p["tape_date"] for p in body] == [t["date"] for t in tapes]
    assert [p["reporting_date"] for p in body] == [t["date"] for t in tapes]

    # Non-degenerate: every period is distinct (dates and balances unique).
    assert len({p["tape_date"] for p in body}) == TAPE_COUNT
    assert len({p["pool_balance_eur"] for p in body}) == TAPE_COUNT
    # Balance declines monotonically over the pool's life (the fake's contract),
    # proving each row carries its *own* period's data rather than one echoed row.
    balances = [p["pool_balance_eur"] for p in body]
    assert balances == sorted(balances, reverse=True)


# ---------------------------------------------------------------------------
# 2. /compliance — monitor runs across all periods, output spans them
# ---------------------------------------------------------------------------


def test_compliance_runs_monitor_over_all_periods():
    """The covenant monitor is driven with the full chronological history.

    Asserts the monitor is called exactly once with ``CovenantInput.periods``
    of length ``len(tape_urls)``, in chronological ``reporting_date`` order, and
    that the endpoint returns the monitor's output. A truncating regression
    would shrink that periods list below ``TAPE_COUNT`` and fail here.
    """
    tapes = GREEN_LION["tape_urls"]
    compliance_dump = {
        "trigger_statuses": [],
        "active_triggers": [],
        "near_miss_triggers": [],
        "summary": f"All covenants within limits across {TAPE_COUNT} periods.",
    }

    with patch("loanwhiz.api.main.EsmaTapeNormaliser") as MockNorm, patch(
        "loanwhiz.api.main.CovenantMonitor"
    ) as MockMon:
        MockNorm.return_value.execute.side_effect = _by_url_normaliser_side_effect()
        MockMon.DEFAULT_TRIGGERS = []
        MockMon.return_value.execute.return_value = _FakeResult(compliance_dump)

        resp = client.get("/deal/green-lion-2026-1/compliance")

    assert resp.status_code == 200
    assert resp.json() == compliance_dump

    # One normalise per tape; one monitor run over all of them.
    assert MockNorm.return_value.execute.call_count == TAPE_COUNT
    MockMon.return_value.execute.assert_called_once()

    # The monitor received the FULL history, chronological, one entry per tape.
    covenant_input = MockMon.return_value.execute.call_args.args[0]
    assert len(covenant_input.periods) == TAPE_COUNT
    assert [p["reporting_date"] for p in covenant_input.periods] == [
        t["date"] for t in tapes
    ]
    # Periods are distinct (non-degenerate), not one row repeated.
    assert len({p["reporting_date"] for p in covenant_input.periods}) == TAPE_COUNT


# ---------------------------------------------------------------------------
# 3. Agent covenant tooling — MAX_VERBATIM_PERIODS summarisation above 6
# ---------------------------------------------------------------------------


def _multi_period_covenant_output(n_periods: int) -> CovenantOutput:
    """Synthetic covenant output spanning ``n_periods`` periods × 2 triggers.

    Mirrors what ``CovenantMonitor`` returns over the real history: one
    ``TriggerStatus`` row per trigger per period, keyed by the deal's actual
    tape dates so the bounded output's "latest period" matches the config's
    final tape. The loss trigger's proximity climbs over time (deteriorating);
    the reserve trigger holds flat — so ``trend_summary`` has real signal.
    """
    dates = [t["date"] for t in GREEN_LION["tape_urls"][:n_periods]]
    statuses: list[TriggerStatus] = []
    for i, period in enumerate(dates):
        loss_prox = 10.0 + (70.0 * i / max(n_periods - 1, 1))
        statuses.append(
            TriggerStatus(
                trigger_name="cumulative_loss_trigger",
                period=period,
                metric_value=round(loss_prox / 100.0 * 1.5, 4),
                threshold=1.5,
                is_triggered=loss_prox > 100.0,
                proximity_pct=round(loss_prox, 4),
                direction="deteriorating" if i else "n/a",
            )
        )
        statuses.append(
            TriggerStatus(
                trigger_name="reserve_fund_trigger",
                period=period,
                metric_value=100.0,
                threshold=100.0,
                is_triggered=False,
                proximity_pct=100.0,
                direction="stable" if i else "n/a",
            )
        )
    return CovenantOutput(
        trigger_statuses=statuses,
        active_triggers=[],
        near_miss_triggers=[],
        summary=f"All triggers within compliance across {n_periods} periods.",
    )


def test_check_covenants_summarises_over_full_history():
    """Over the full history (> MAX_VERBATIM_PERIODS) the tool summarises cleanly.

    Drives ``check_covenants`` with a synthetic ``CovenantOutput`` spanning all
    ``TAPE_COUNT`` periods (above the verbatim threshold of 6). The wrapper must
    take the ``> MAX_VERBATIM_PERIODS`` branch: collapse ``trigger_statuses`` to
    just the latest period's rows, attach a computed ``trend_summary``, and note
    ``periods_summarised`` — without dumping all ``TAPE_COUNT × triggers`` rows
    and without erroring. The expected count is derived from the config, not a
    literal 27 (and not the literal 48 the existing unit test uses).
    """
    fake_output = _multi_period_covenant_output(TAPE_COUNT)
    raw_row_count = len(fake_output.trigger_statuses)
    assert raw_row_count == TAPE_COUNT * 2  # sanity: primitive is verbose

    fake_result = PrimitiveResult[CovenantOutput](
        output=fake_output,
        confidence=1.0,
        citations=[],
        audit_entry=_FAKE_AUDIT,
    )

    with patch(
        "loanwhiz.agent.tools.CovenantMonitor.execute", return_value=fake_result
    ):
        # periods_json content is irrelevant — the patched primitive supplies the
        # output; what matters is the wrapper's bounding of that output.
        result = check_covenants.invoke({"periods_json": json.dumps([])})

    # The > MAX_VERBATIM_PERIODS branch engaged: only the latest period survives
    # in trigger_statuses (2 triggers), NOT all raw_row_count rows.
    assert len(result["trigger_statuses"]) == 2
    assert len(result["trigger_statuses"]) < raw_row_count
    latest_period = GREEN_LION["tape_urls"][TAPE_COUNT - 1]["date"]
    assert {s["period"] for s in result["trigger_statuses"]} == {latest_period}

    # A computed trend summary is present, one entry per trigger, no error.
    assert "trend_summary" in result
    names = {t["trigger_name"] for t in result["trend_summary"]}
    assert names == {"cumulative_loss_trigger", "reserve_fund_trigger"}
    loss = next(
        t
        for t in result["trend_summary"]
        if t["trigger_name"] == "cumulative_loss_trigger"
    )
    assert loss["min_proximity_pct"] < loss["max_proximity_pct"]
    assert loss["latest_proximity_pct"] > loss["first_proximity_pct"]
    assert loss["net_trend"] == "deteriorating"

    # An explicit note tells the agent the data was summarised, citing the count.
    assert "periods_summarised" in result
    assert f"{TAPE_COUNT} periods" in result["periods_summarised"]

    # Latest-period aggregates + confidence still pass through unchanged.
    assert result["active_triggers"] == []
    assert result["near_miss_triggers"] == []
    assert result["confidence"] == 1.0
