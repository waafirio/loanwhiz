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
