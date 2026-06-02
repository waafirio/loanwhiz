"""Smoke test — Vertex AI / Gemini 2.5 Flash reachable."""
from google import genai
from loanwhiz.config import GCP_PROJECT, GCP_LOCATION, MODEL_FLASH


def test_gemini_reachable():
    client = genai.Client(vertexai=True, project=GCP_PROJECT, location=GCP_LOCATION)
    r = client.models.generate_content(model=MODEL_FLASH, contents="What is 2 + 2? Reply with just the number.")
    assert "4" in r.text
