"""C2 (epic #236) — the Italian + Spanish deal-model seeds load as DealModels.

The cross-jurisdiction proof: C2 runs the **unmodified** extraction pipeline on
the non-Dutch prospectuses (Leone Arancio — Italy; Sol-Lion II — Spain) and
commits the resulting seeds under ``src/loanwhiz/data/deals/seed/``. These tests
assert those committed seeds are real, schema-valid
:class:`~loanwhiz.extraction.assembler.DealModel` artifacts the same loader the
API uses (``loanwhiz.api.main._load_cached_deal_model``) can read.

A *partial* extraction is a legitimate result for a non-English prospectus
(the existing Green Lion seeds already carry 0 definitions), so these tests
assert the seed is a valid ``DealModel`` and carries the right provenance — not
that every sub-extractor produced rich content.

No network, no Docling, no Gemini: they only read committed JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loanwhiz.extraction.assembler import DealModel, _slug

# Repo-root-relative seed dir (mirrors loanwhiz.api.main.DEAL_MODEL_SEED_DIR).
_SEED_DIR = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "loanwhiz"
    / "data"
    / "deals"
    / "seed"
)
_DEALS_JSON = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "loanwhiz"
    / "data"
    / "deals.json"
)

# The two C2 jurisdictions, by their deal-registry key in deals.json.
_C2_DEAL_KEYS = ("leone-arancio-2023-1", "sol-lion-ii")


def _deal_name(deal_key: str) -> str:
    deals = json.loads(_DEALS_JSON.read_text(encoding="utf-8"))
    return deals[deal_key]["deal_name"]


def _seed_path(deal_key: str) -> Path:
    """The committed seed path for a deal, derived exactly as the API loader does."""
    return _SEED_DIR / f"{_slug(_deal_name(deal_key))}.json"


@pytest.mark.parametrize("deal_key", _C2_DEAL_KEYS)
def test_c2_seed_exists(deal_key: str) -> None:
    """Both C2 jurisdiction seeds are committed under the seed dir."""
    path = _seed_path(deal_key)
    assert path.exists(), (
        f"C2 seed for {deal_key} not committed at {path} — run the unmodified "
        f"extraction pipeline (scripts/extract_c2_deals.py) then "
        f"scripts/seed_deal_models.py --deal {path.stem}"
    )


@pytest.mark.parametrize("deal_key", _C2_DEAL_KEYS)
def test_c2_seed_loads_as_deal_model(deal_key: str) -> None:
    """Each committed C2 seed validates as a DealModel with matching provenance."""
    path = _seed_path(deal_key)
    if not path.exists():
        pytest.skip(f"seed for {deal_key} not yet committed at {path}")

    model = DealModel.model_validate_json(path.read_text(encoding="utf-8"))

    # Provenance: the seed was produced from this deal's registered prospectus.
    deals = json.loads(_DEALS_JSON.read_text(encoding="utf-8"))
    expected = deals[deal_key]
    assert model.metadata.deal_name == expected["deal_name"]
    assert model.metadata.prospectus_url == expected["prospectus_url"]
    assert 0.0 <= model.metadata.completeness_score <= 1.0
    # The committed seed carries a host-agnostic, repo-relative cache path.
    assert not Path(model.metadata.cache_path).is_absolute()


def _all_citation_documents(model: DealModel) -> list[str]:
    """Every ``citation.document`` string across waterfalls + covenant triggers."""
    docs: list[str] = []
    for waterfall in model.waterfalls.values():
        for step in waterfall.get("steps", []):
            doc = step.get("citation", {}).get("document")
            if isinstance(doc, str):
                docs.append(doc)
    for trigger in model.covenants.get("triggers", []):
        doc = trigger.get("citation", {}).get("document")
        if isinstance(doc, str):
            docs.append(doc)
    return docs


@pytest.mark.parametrize("deal_key", _C2_DEAL_KEYS)
def test_c2_seed_citations_are_not_cross_jurisdiction_contaminated(
    deal_key: str,
) -> None:
    """No non-Dutch C2 seed may cite a Dutch (Green Lion) prospectus.

    Regression guard for #367: the Leone Arancio (IT) seed had been committed
    with all 40 waterfall-step citations stamped ``document: "Green Lion
    2026-1 Prospectus"`` — a Dutch deal's prospectus pasted onto an Italian
    RMBS. Each non-Dutch seed's citations must reference its own deal, never a
    Green Lion document.
    """
    path = _seed_path(deal_key)
    if not path.exists():
        pytest.skip(f"seed for {deal_key} not yet committed at {path}")

    model = DealModel.model_validate_json(path.read_text(encoding="utf-8"))

    contaminated = [d for d in _all_citation_documents(model) if "Green Lion" in d]
    assert not contaminated, (
        f"{deal_key} carries Green-Lion citation provenance on a non-Dutch deal "
        f"(cross-jurisdiction contamination): {sorted(set(contaminated))}"
    )

    # Belt-and-braces: no "Green Lion" substring anywhere in the raw seed text.
    assert "Green Lion" not in path.read_text(encoding="utf-8"), (
        f"{deal_key} seed still contains a 'Green Lion' reference somewhere"
    )


def test_leone_arancio_completeness_is_honest_not_false_perfect() -> None:
    """The IT seed must not re-assert a false ``completeness_score: 1.0`` (#367).

    The corrupted seed claimed a perfect 1.0 while carrying empty ``definitions``
    and a single all-null stub tranche — higher than any honestly-extracted seed.
    An honest IT extraction is partial; its completeness must be < 1.0.
    """
    path = _seed_path("leone-arancio-2023-1")
    if not path.exists():
        pytest.skip(f"Leone Arancio seed not yet committed at {path}")

    model = DealModel.model_validate_json(path.read_text(encoding="utf-8"))
    assert model.metadata.completeness_score < 1.0, (
        "Leone Arancio seed re-asserts a false completeness_score of 1.0 — the "
        "extraction is partial (empty definitions, stub tranche) and must say so"
    )
