"""Tests for the general, governed report extractor (#271).

Covers the hybrid mechanism end-to-end with **no live LLM / network** in the
fast suite:

- deterministic format-registry path against the committed GL-2024-1 fixtures;
- registry dispatch + deterministic-first short-circuit;
- LLM (OCR+LLM) structured-output fallback via an **injected fake client**;
- the determinism cache (a cache hit short-circuits the LLM);
- per-field provenance for both paths + citation source-span verification;
- the governed ``PrimitiveResult`` envelope + catalogue registration;
- the ``to_notes_cash_report()`` bridge round-tripping into the real #267
  ``ReportAdapter``.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from loanwhiz.primitives import (
    FORMAT_REGISTRY,
    ParsedReport,
    ReportAdapter,
    ReportExtractInput,
    ReportExtractor,
    extract_report,
)
from loanwhiz.primitives import report_extractor as rx
from loanwhiz.primitives.base import PrimitiveResult
from loanwhiz.primitives.registry import PRIMITIVE_REGISTRY

_REPO_ROOT = Path(__file__).resolve().parents[1]
GL_FIXTURE = (
    _REPO_ROOT / "tests" / "fixtures" / "notes_cash" / "green-lion-2024-1-march-2026.txt"
)
SEED_MODEL = (
    _REPO_ROOT / "src" / "loanwhiz" / "data" / "deals" / "seed" / "green-lion-2024-1-bv.json"
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def gl_text() -> str:
    return GL_FIXTURE.read_text(encoding="utf-8")


@pytest.fixture()
def deal_model():
    """Duck-typed extracted model exposing ``.waterfalls`` (mirrors adapter test)."""

    class _Model:
        def __init__(self, data: dict) -> None:
            self.waterfalls = data["waterfalls"]

    return _Model(json.loads(SEED_MODEL.read_text(encoding="utf-8")))


class _FakeLlmClient:
    """A google-genai-shaped stub: ``client.models.generate_content(...).text``.

    Returns a queued list of raw text responses (one per call), so a test can
    drive both the happy path and the validation-retry path deterministically
    with no network.
    """

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.models = SimpleNamespace(generate_content=self._generate)

    def _generate(self, *, model: str, contents: list[dict]):
        self.calls.append({"model": model, "contents": contents})
        raw = self._responses.pop(0)
        return SimpleNamespace(text=raw)


def _llm_report_json(*, reserve_excerpt: str) -> str:
    """A minimal valid ParsedReport JSON an LLM would return for a non-GL report."""
    return json.dumps(
        {
            "deal_name": "Leone Arancio RMBS 2023-1 S.r.l.",
            "report_type": "investor_report",
            "extraction_method": "ocr+llm",
            "periods": [
                {
                    "reporting_date": "2026-03-31",
                    "reserve_balance": 5_000_000.0,
                    "pool_balance": 250_000_000.0,
                    "note_balances": [
                        {"note_class": "class_a", "closing": 200_000_000.0}
                    ],
                }
            ],
            "provenance": {
                "periods.0.reserve_balance": {
                    "source": "report",
                    "method": "ocr+llm",
                    "confidence": 0.92,
                    "citation": {
                        "document": "Leone Arancio investor report",
                        "page_or_row": 3,
                        "excerpt": reserve_excerpt,
                    },
                },
                "periods.0.pool_balance": {
                    "source": "report",
                    "method": "ocr+llm",
                    "confidence": 0.88,
                    "citation": {
                        "document": "Leone Arancio investor report",
                        "page_or_row": 2,
                        "excerpt": "Portfolio outstanding balance 250,000,000",
                    },
                },
            },
        }
    )


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_parsed_report_fields_all_optional_in_period() -> None:
    """A period requires only ``reporting_date``; everything else is optional."""
    p = ParsedReport(deal_name="X", periods=[{"reporting_date": "2026-01-01"}])  # type: ignore[list-item]
    assert p.periods[0].reserve_balance is None
    assert p.periods[0].note_balances == []
    assert p.report_type == "unknown"


# ---------------------------------------------------------------------------
# Format registry + deterministic path
# ---------------------------------------------------------------------------


def test_registry_has_notes_cash_as_first_deterministic_entry() -> None:
    assert FORMAT_REGISTRY[0].name == "green_lion_notes_cash"


def test_match_format_recognizes_gl_notes_cash(gl_text: str) -> None:
    fmt = rx.match_format(gl_text)
    assert fmt is not None and fmt.name == "green_lion_notes_cash"


def test_match_format_returns_none_on_unknown_layout() -> None:
    assert rx.match_format("a thin monthly investor report with no bond report") is None


def test_deterministic_path_parses_gl_fixture(gl_text: str) -> None:
    res = extract_report(ReportExtractInput(deal_name="Green Lion 2024-1 B.V.", text=gl_text))
    assert isinstance(res, PrimitiveResult)
    r = res.output
    assert r.extraction_method == "deterministic"
    assert r.report_type == "notes_and_cash"
    assert len(r.periods) == 1
    p = r.periods[0]
    assert p.reporting_date == "2026-04-23"
    assert p.reserve_balance == 10_500_000.0
    assert p.available_revenue == pytest.approx(13_615_514.93)
    assert p.note_balance("class_a").closing == 1_000_000_000.0


def test_deterministic_path_provenance_is_full_confidence(gl_text: str) -> None:
    r = extract_report(
        ReportExtractInput(deal_name="Green Lion 2024-1 B.V.", text=gl_text)
    ).output
    assert r.provenance, "deterministic parse must emit a provenance map"
    for path, fp in r.provenance.items():
        assert fp.source == "report"
        assert fp.method == "deterministic"
        assert fp.confidence == 1.0
        assert fp.citation is not None
    assert "periods.0.reserve_balance" in r.provenance


def test_deterministic_envelope_confidence_and_citation(gl_text: str) -> None:
    res = extract_report(
        ReportExtractInput(deal_name="Green Lion 2024-1 B.V.", text=gl_text)
    )
    assert res.confidence == 1.0
    assert res.citations and res.citations[0].document
    assert res.audit_entry.primitive_name == "report_extractor"
    assert len(res.audit_entry.input_hash) == 64


def test_deterministic_short_circuits_llm(gl_text: str) -> None:
    """A deterministic match must never touch the LLM client (cost + nondeterminism)."""
    fake = _FakeLlmClient(responses=[])  # empty: any call would IndexError
    res = extract_report(
        ReportExtractInput(deal_name="Green Lion 2024-1 B.V.", text=gl_text),
        client=fake,
    )
    assert res.output.extraction_method == "deterministic"
    assert fake.calls == []


# ---------------------------------------------------------------------------
# LLM fallback path (injected fake client — no network)
# ---------------------------------------------------------------------------


def test_llm_fallback_when_no_format_matches(tmp_path: Path) -> None:
    source = "Reserve fund balance: 5,000,000 EUR at period end."
    fake = _FakeLlmClient(responses=[_llm_report_json(reserve_excerpt=source)])
    res = extract_report(
        ReportExtractInput(deal_name="Leone Arancio RMBS 2023-1 S.r.l.", text=source),
        client=fake,
        cache_dir=tmp_path,
    )
    r = res.output
    assert len(fake.calls) == 1
    assert r.extraction_method == "ocr+llm"
    assert r.report_type == "investor_report"
    assert r.periods[0].reserve_balance == 5_000_000.0
    # The reserve citation is contained in `source` (verified → 0.92 kept); the
    # pool citation is NOT in `source` (unverified → capped to 0.5). Envelope
    # confidence is the mean of the two.
    assert res.confidence == pytest.approx((0.92 + rx._UNVERIFIED_CITATION_CONFIDENCE_CAP) / 2)


def test_llm_fallback_retries_once_on_validation_failure(tmp_path: Path) -> None:
    good = _llm_report_json(reserve_excerpt="reserve 5,000,000")
    fake = _FakeLlmClient(responses=["{ not valid json", good])
    res = extract_report(
        ReportExtractInput(deal_name="Leone Arancio RMBS 2023-1 S.r.l.", text="reserve 5,000,000"),
        client=fake,
        cache_dir=tmp_path,
    )
    assert len(fake.calls) == 2  # first failed, retried, succeeded
    assert res.output.extraction_method == "ocr+llm"


def test_llm_fallback_raises_when_retries_exhausted(tmp_path: Path) -> None:
    fake = _FakeLlmClient(responses=["nope", "still nope"])
    with pytest.raises(ValueError, match="did not yield a valid ParsedReport"):
        extract_report(
            ReportExtractInput(deal_name="X", text="something with no recognized format"),
            client=fake,
            cache_dir=tmp_path,
        )


def test_llm_citation_verified_against_source_span_keeps_confidence(tmp_path: Path) -> None:
    source = "Reserve fund balance: 5,000,000 EUR at period end."
    fake = _FakeLlmClient(responses=[_llm_report_json(reserve_excerpt="Reserve fund balance: 5,000,000")])
    r = extract_report(
        ReportExtractInput(deal_name="Leone Arancio", text=source),
        client=fake,
        cache_dir=tmp_path,
    ).output
    # The reserve excerpt IS contained in the source → confidence preserved.
    assert r.provenance["periods.0.reserve_balance"].confidence == pytest.approx(0.92)


def test_llm_unverifiable_citation_drops_confidence(tmp_path: Path) -> None:
    source = "Reserve fund balance: 5,000,000 EUR at period end."
    # Excerpt the model claims is NOT present in the source text.
    fake = _FakeLlmClient(responses=[_llm_report_json(reserve_excerpt="this string is absent from source")])
    r = extract_report(
        ReportExtractInput(deal_name="Leone Arancio", text=source),
        client=fake,
        cache_dir=tmp_path,
    ).output
    assert r.provenance["periods.0.reserve_balance"].confidence <= rx._UNVERIFIED_CITATION_CONFIDENCE_CAP


# ---------------------------------------------------------------------------
# Determinism cache
# ---------------------------------------------------------------------------


def test_llm_result_is_cached(tmp_path: Path) -> None:
    source = "Reserve fund balance: 5,000,000"
    fake = _FakeLlmClient(responses=[_llm_report_json(reserve_excerpt=source)])
    inp = ReportExtractInput(deal_name="Leone Arancio", text=source)
    extract_report(inp, client=fake, cache_dir=tmp_path)
    # A cache file was written for this input.
    assert list(tmp_path.glob("parsed-report-*.json"))


def test_cache_hit_short_circuits_llm(tmp_path: Path) -> None:
    source = "Reserve fund balance: 5,000,000"
    inp = ReportExtractInput(deal_name="Leone Arancio", text=source)
    warm = _FakeLlmClient(responses=[_llm_report_json(reserve_excerpt=source)])
    extract_report(inp, client=warm, cache_dir=tmp_path)  # populates cache
    assert len(warm.calls) == 1

    # Second call with an EMPTY client: a cache hit must not call the LLM.
    cold = _FakeLlmClient(responses=[])
    res = extract_report(inp, client=cold, cache_dir=tmp_path)
    assert cold.calls == []
    assert res.output.extraction_method == "ocr+llm"
    assert res.output.periods[0].reserve_balance == 5_000_000.0


def test_force_refresh_bypasses_cache(tmp_path: Path) -> None:
    source = "Reserve fund balance: 5,000,000"
    inp = ReportExtractInput(deal_name="Leone Arancio", text=source)
    extract_report(inp, client=_FakeLlmClient([_llm_report_json(reserve_excerpt=source)]), cache_dir=tmp_path)
    refresh = _FakeLlmClient(responses=[_llm_report_json(reserve_excerpt=source)])
    extract_report(inp, client=refresh, cache_dir=tmp_path, force_refresh=True)
    assert len(refresh.calls) == 1  # re-extracted despite a warm cache


# ---------------------------------------------------------------------------
# Governed primitive + registry
# ---------------------------------------------------------------------------


def test_report_extractor_registered_in_catalogue() -> None:
    assert "report_extractor" in PRIMITIVE_REGISTRY
    reg = PRIMITIVE_REGISTRY.get("report_extractor")
    assert reg is not None
    assert "report" in reg.tags


def test_primitive_execute_runs_deterministic_path(gl_text: str) -> None:
    prim = ReportExtractor()
    res = prim.execute(ReportExtractInput(deal_name="Green Lion 2024-1 B.V.", text=gl_text))
    assert res.output.extraction_method == "deterministic"


def test_primitive_execute_uses_injected_client(tmp_path: Path) -> None:
    source = "Reserve fund balance: 5,000,000"
    fake = _FakeLlmClient(responses=[_llm_report_json(reserve_excerpt=source)])
    prim = ReportExtractor(client=fake, cache_dir=tmp_path)
    res = prim.execute(ReportExtractInput(deal_name="Leone Arancio", text=source))
    assert res.output.extraction_method == "ocr+llm"


def test_extract_requires_text_or_url() -> None:
    with pytest.raises(ValueError, match="requires one of"):
        extract_report(ReportExtractInput(deal_name="X"))


# ---------------------------------------------------------------------------
# Bridge → the real #267 ReportAdapter
# ---------------------------------------------------------------------------


def test_to_notes_cash_report_round_trips_into_report_adapter(gl_text: str, deal_model) -> None:
    """The deterministic ParsedReport bridges into the existing ReportAdapter unchanged."""
    r = extract_report(
        ReportExtractInput(deal_name="Green Lion 2024-1 B.V.", text=gl_text)
    ).output
    ncr = r.to_notes_cash_report()
    adapter = ReportAdapter.from_deal_model(deal_model)
    seed, inputs = adapter.to_inputs(ncr)
    assert len(inputs) == len(r.periods)
    # Seed reconstructs the opening Class A balance (closing + principal repaid).
    class_a = next(t for t in seed.tranches if t.name == "class_a")
    assert class_a.balance > 0
    assert seed.reserve_balance > 0
