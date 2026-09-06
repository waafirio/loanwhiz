"""Derived tapes: the URI is the provenance, and the loader cannot contradict it.

The contract this file exists to hold is narrow and absolute: **a tape LoanWhiz
reconstructed from a trustee report must stay distinguishable from a filed
Article 7(1)(a) tape at every surface that reports provenance.** Cairn CLO XVII
DAC does file real Loan Reports; this repo does not have them, so a derived tape
a reader could mistake for that filing is provenance laundering.

These tests are offline. They derive from the committed report-text fixtures
through a ``file://`` source rather than fetching the registered PDFs, which is
the same seam an operator gets for a local extraction — no network, no pypdf.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from loanwhiz.primitives.derived_tape import (
    DerivationError,
    DerivedTapeScheme,
    derive_tape,
    derived_tape_uri,
    declared_annex_id_for,
    is_derived_uri,
    source_document_for,
    source_kind_for,
)
from loanwhiz.primitives.esma_tape_normaliser import (
    DATA_SOURCE_DERIVED,
    DATA_SOURCE_DIRECT,
    EsmaTapeInput,
    EsmaTapeNormaliser,
    _load_tape,
)

_FIXTURES = Path(__file__).parent / "fixtures" / "collateral_schedule"

#: The three committed trustee-report extractions, with the asset count and
#: aggregate par each report states about itself. Every figure here is checked
#: by ``collateral_schedule_parser.reconcile_schedule`` against the source
#: document at parse time; repeating them is how a silent change in the parse
#: shows up as a test failure rather than as a quietly different tape.
_PERIODS = [
    ("december-2024", "December 2024", "2024-12-16", 193, "407181748.22"),
    ("february-2025", "February 2025", "2025-02-18", 191, "401342140.14"),
    ("march-2025", "March 2025", "2025-03-18", 196, "411342140.14"),
]


def _uri(stem: str, period_label: str) -> str:
    source = (_FIXTURES / f"cairn-clo-xvii-{stem}.txt").resolve()
    return derived_tape_uri(
        f"file://{source}", period_label, scheme=DerivedTapeScheme.TRUSTEE_REPORT
    )


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    """A per-test derivation cache, so no test reads another's persisted answer."""
    return tmp_path / "extraction_cache"
@pytest.fixture(autouse=True)
def _isolated_derivation_cache(tmp_path, monkeypatch):
    """Point the derivation cache at a tmp dir and clear the in-process memo.

    Without this a test writes into the repo's ``data/extraction_cache/`` and the
    *next* run reads its answer back — so a broken derivation would keep these
    tests green off a stale artefact. The cache exists to save a parse, never to
    stand in for one, and a test that lets it do the latter is not testing the
    derivation at all.
    """
    from loanwhiz.primitives import derived_tape as _dt

    monkeypatch.setattr(_dt, "DEFAULT_DERIVED_TAPE_CACHE_DIR", tmp_path / "default-cache")
    _dt._MEMO.clear()
    yield
    _dt._MEMO.clear()


# ---------------------------------------------------------------------------
# The URI is the provenance
# ---------------------------------------------------------------------------


def test_source_kind_is_recoverable_from_the_uri_alone() -> None:
    """A caller holding only the URL string can still tell what the tape is.

    This is the property that makes the scheme worth using instead of a sibling
    registry field: several of ``_load_tape``'s call sites hold nothing but
    ``tape["url"]``, so any provenance that needed a second field would be
    forgettable at exactly those sites.
    """
    uri = _uri("december-2024", "December 2024")
    kind = source_kind_for(uri)
    assert kind is not None
    assert kind.value == "derived_from_investor_report"
    assert kind.is_regulatory_filing is False
    assert kind.rts_coded_values is False


def test_an_ordinary_tape_url_is_undeclared_and_not_assumed_filed() -> None:
    """``None`` means *not declared* — never ``FILED_ARTICLE_7_1_A``.

    Defaulting an ordinary published tape to "filed" would make the system's
    most consequential claim by omission, and this repo holds no evidence about
    whether a given published file is its originator's Article 7(1)(a)
    disclosure or a redistribution of it.
    """
    assert source_kind_for("https://example.test/tape.csv") is None
    assert is_derived_uri("https://example.test/tape.csv") is False
    assert source_document_for("https://example.test/tape.csv") is None


def test_the_uri_carries_the_source_document_itself() -> None:
    """A reader who sees only the tape entry still sees what grounds it."""
    uri = _uri("march-2025", "March 2025")
    source = source_document_for(uri)
    assert source is not None
    assert source.startswith("file://")
    assert source.endswith("cairn-clo-xvii-march-2025.txt")


# ---------------------------------------------------------------------------
# The derivation itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("stem", "label", "date", "assets", "par"), _PERIODS)
def test_each_period_derives_to_its_own_reconciled_tape(
    stem: str, label: str, date: str, assets: int, par: str, cache: Path
) -> None:
    """The asset count genuinely moves between periods — a series, not one cut."""
    tape = derive_tape(_uri(stem, label), cache_dir=cache)
    assert len(tape.rows) == assets
    assert tape.reporting_date == date
    assert tape.period_label == label
    # Exact, not approximate: balances are ``Decimal`` all the way from the
    # report, and the parse reconciles to the stated total to the cent.
    assert sum(r["current_balance"] for r in tape.rows) == Decimal(par)


def test_a_derived_tape_states_annex_4_which_detection_cannot_find(cache: Path) -> None:
    """Stated, not sniffed — and #470's refusal to fabricate the signature holds.

    Annex 4's entire detection signature is ``enterprise_size``, which no trustee
    report publishes. The tape declares its annex instead of the registry
    guessing one, and ``enterprise_size`` stays a declared absence.
    """
    from loanwhiz.domain.esma_annexes import ANNEX_REGISTRY

    uri = _uri("december-2024", "December 2024")
    tape = derive_tape(uri, cache_dir=cache)
    assert tape.annex_id == "annex_4"
    assert declared_annex_id_for(uri, cache_dir=cache) == "annex_4"
    # Detection still cannot find it, which is precisely why the tape states it.
    assert ANNEX_REGISTRY.detect(set(tape.columns)) is None
    assert "enterprise_size" in {c.canonical_column for c in tape.absent_columns}
    assert "enterprise_size" not in tape.columns


def test_absent_columns_emit_no_key_rather_than_a_zero(cache: Path) -> None:
    """Absence is not zero. A missing key is unmistakable; a ``0`` is a lie."""
    tape = derive_tape(_uri("february-2025", "February 2025"), cache_dir=cache)
    absent = {c.canonical_column for c in tape.absent_columns}
    for credit_field in (
        "arrears_balance",
        "days_in_arrears",
        "account_status",
        "default_amount",
        "cumulative_recoveries",
        "market_value",
    ):
        assert credit_field in absent
        assert credit_field not in tape.columns
        assert all(credit_field not in row for row in tape.rows)


# ---------------------------------------------------------------------------
# The loader
# ---------------------------------------------------------------------------


def test_loading_a_derived_uri_cannot_yield_the_direct_channel() -> None:
    """The channel follows the identifier, not the branch that happened to run."""
    frame, data_source = _load_tape(_uri("march-2025", "March 2025"), None)
    assert data_source == DATA_SOURCE_DERIVED
    assert data_source != DATA_SOURCE_DIRECT
    assert isinstance(frame, pd.DataFrame)
    assert len(frame) == 196
    assert list(frame.columns)[:3] == [
        "loan_identifier",
        "reporting_date",
        "current_balance",
    ]


def test_the_tape_citation_quotes_the_disclosure_verbatim() -> None:
    """Every provenance surface renders the source kind's own sentence.

    Composing a local paraphrase is how a derived tape starts reading like a
    filing, so the excerpt must carry the disclosure exactly.
    """
    uri = _uri("december-2024", "December 2024")
    result = EsmaTapeNormaliser().execute(EsmaTapeInput(file_url=uri))

    assert result.output.data_source == DATA_SOURCE_DERIVED
    assert result.output.annex_detected == "Annex 4 (Corporate)"
    assert result.output.loan_count == 193

    excerpt = result.citations[0].excerpt
    assert "(ingested via derived)" in excerpt
    assert source_kind_for(uri).disclosure in excerpt
    assert "NOT filed under Article 7(1)(a)" in excerpt


def test_pool_analytics_report_no_arrears_rather_than_a_clean_pool() -> None:
    """A tape that states arrears in no form must not read as 100% performing.

    Every arrears/default mask falls back to all-``False`` on a missing column,
    so before this the derived tape landed on ``current_pct: 100.0`` /
    ``default_pct: 0.0`` — a pristine pool, indistinguishable from a real one and
    reported at full confidence. The trustee report publishes no arrears,
    account-status, default or recovery column at all, so the honest output is no
    buckets.
    """
    result = EsmaTapeNormaliser().execute(
        EsmaTapeInput(file_url=_uri("december-2024", "December 2024"))
    )
    assert result.output.arrears_breakdown == {}
    # The stats the tape *does* support are still computed, so this is absence
    # rather than a blanket refusal to analyse.
    assert result.output.pool_stats["wtd_coupon_pct"] > 0
    assert result.output.pool_balance_eur == pytest.approx(407181748.22)
    # And nothing invents a geography or an EPC label the source never carried.
    assert result.output.geographic_breakdown is None
    assert result.output.epc_breakdown is None


# ---------------------------------------------------------------------------
# Failure modes — the contract fails loudly or not at all
# ---------------------------------------------------------------------------


def test_a_uri_without_a_period_is_refused(cache: Path) -> None:
    """A tape that cannot say which cut it is has lost what makes it a period."""
    source = (_FIXTURES / "cairn-clo-xvii-march-2025.txt").resolve()
    with pytest.raises(DerivationError, match="period"):
        derive_tape(f"derived+trustee-report:file://{source}", cache_dir=cache)


def test_an_unknown_scheme_is_refused_by_name(cache: Path) -> None:
    """A scheme with no registered deriver is not a tape identifier."""
    with pytest.raises(DerivationError, match="derived\\+trustee-report"):
        derive_tape("derived+moon-phase:file:///tmp/x.txt#period=X", cache_dir=cache)


def test_a_source_that_does_not_reconcile_is_refused_not_returned(
    tmp_path: Path, cache: Path
) -> None:
    """#469's contract is load-bearing at ingest, not merely at parse time.

    A tape the source document itself contradicts must never reach a caller —
    which is what makes ``POST /deal/{id}/ingest/tape``'s 422 mean something.
    """
    truncated = tmp_path / "not-a-trustee-report.txt"
    truncated.write_text("--- page 1 ---\nCairn CLO XVII DAC\nMonthly Report\n")
    uri = derived_tape_uri(
        f"file://{truncated}", "December 2024", scheme=DerivedTapeScheme.TRUSTEE_REPORT
    )
    with pytest.raises(ValueError):
        derive_tape(uri, cache_dir=cache)
    # Nothing was cached, so a later call re-derives rather than serving a
    # half-built answer. Written as an unconditional assertion: a
    # ``... if cache.exists() else True`` guard passes vacuously in exactly the
    # case it is meant to check, since a refused derivation never creates the
    # directory at all.
    assert list(cache.glob("derived-tape-*.json")) == []


# ---------------------------------------------------------------------------
# The cache is an accelerator, never an authority
# ---------------------------------------------------------------------------


def test_cache_and_cold_derivation_agree(cache: Path) -> None:
    """Deleting the cache re-derives the same tape from the same report.

    This is the property a committed tape artefact would not have had: the
    report is the authority, and the cache only saves the second parse.
    """
    uri = _uri("february-2025", "February 2025")

    cold = derive_tape(uri, cache_dir=cache)
    cached_files = list(cache.glob("derived-tape-*.json"))
    assert len(cached_files) == 1, "the derivation persists exactly one artefact"

    warm = derive_tape(uri, cache_dir=cache, force_refresh=True)
    assert warm.model_dump_json() == cold.model_dump_json()

    # Now serve it from disk with the in-process memo bypassed, and prove the
    # round-trip through JSON preserves the provenance as well as the rows.
    from_disk = type(cold).model_validate_json(
        cached_files[0].read_text(encoding="utf-8")
    )
    assert from_disk.model_dump_json() == cold.model_dump_json()
    assert from_disk.source_kind is cold.source_kind
    assert from_disk.is_regulatory_filing is False


def test_the_cache_is_read_from_disk_when_the_source_is_gone(
    cache: Path, tmp_path: Path
) -> None:
    """The disk-cache branch must be exercised by something other than the memo.

    Every other test in this file re-derives or hits the in-process memo, so the
    read-from-disk path — the one that makes a warm checkout independent of the
    source document being reachable — would survive deletion untested. This
    forces it: derive once, drop the memo, then move the source away so a cache
    hit is the *only* way the call can succeed.
    """
    from loanwhiz.primitives import derived_tape as _dt

    source = tmp_path / "report.txt"
    source.write_bytes(
        (_FIXTURES / "cairn-clo-xvii-march-2025.txt").read_bytes()
    )
    uri = derived_tape_uri(
        f"file://{source}", "March 2025", scheme=DerivedTapeScheme.TRUSTEE_REPORT
    )

    first = derive_tape(uri, cache_dir=cache)
    _dt._MEMO.clear()
    source.unlink()

    from_cache = derive_tape(uri, cache_dir=cache)
    assert from_cache.model_dump_json() == first.model_dump_json()
    assert from_cache.source_kind is first.source_kind

    # And with the cache gone too, the call fails rather than inventing a tape.
    _dt._MEMO.clear()
    for stale in cache.glob("derived-tape-*.json"):
        stale.unlink()
    with pytest.raises(Exception):  # noqa: B017,PT011 - urllib raises its own type
        derive_tape(uri, cache_dir=cache)


def test_an_unreadable_cache_re_derives_instead_of_failing(cache: Path) -> None:
    """A corrupt accelerator must degrade to the slow path, never to an error."""
    uri = _uri("march-2025", "March 2025")
    first = derive_tape(uri, cache_dir=cache)
    cached = next(iter(cache.glob("derived-tape-*.json")))
    cached.write_text("{not json at all", encoding="utf-8")

    again = derive_tape(uri, cache_dir=cache, force_refresh=True)
    assert len(again.rows) == len(first.rows)
    assert json.loads(cached.read_text(encoding="utf-8"))["annex_id"] == "annex_4"
