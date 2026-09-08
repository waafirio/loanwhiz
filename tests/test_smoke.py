"""Smoke test — Vertex AI / Gemini 2.5 Flash reachable.

Marked ``integration``: it makes a real external API call, which is exactly what
that marker is for. It was previously unmarked, so it ran in the default suite
and failed on any machine without live application-default credentials — a red
suite that says nothing about the code under test. Run it deliberately with
``pytest -m integration``.
"""
import pytest
from google import genai
from loanwhiz.config import GCP_PROJECT, GCP_LOCATION, MODEL_FLASH


@pytest.mark.integration
def test_gemini_reachable():
    client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)
    r = client.models.generate_content(model=MODEL_FLASH, contents="What is 2 + 2? Reply with just the number.")
    assert "4" in r.text
