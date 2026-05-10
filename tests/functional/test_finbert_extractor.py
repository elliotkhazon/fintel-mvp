"""Step 0.2b / 2.6 — FinBERT extractor tests.

Validates that FinBERT correctly classifies sentiment direction on known
fixture sentences. Requires `transformers` and the ProsusAI/finbert model.

Skipped automatically when transformers is not installed.

Run:
    pytest tests/functional/test_finbert_extractor.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("transformers", reason="transformers not installed — skipping FinBERT tests")

from src.models import finbert_extractor

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "finbert_fixtures.json"


def load_fixtures() -> list[dict]:
    with open(FIXTURES_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def finbert_available():
    if not finbert_extractor.is_available():
        pytest.skip("ProsusAI/finbert model not available (download first)")


@pytest.mark.parametrize("fixture", load_fixtures())
def test_finbert_direction(fixture: dict, finbert_available):
    """FinBERT must return the correct sentiment direction for fixture sentences."""
    text = fixture["text"]
    expected = fixture["expected_direction"]
    results = finbert_extractor.extract_sentiment(text)

    assert results, f"FinBERT returned no results for: '{text[:80]}'"
    assert len(results) >= 1

    result = results[0]
    assert result["label"] == expected, (
        f"Text: '{text[:80]}'\n"
        f"Expected direction: '{expected}', got: '{result['label']}' "
        f"(score={result['score']:.4f})"
    )


def test_finbert_score_in_range(finbert_available):
    """All FinBERT scores must be in [-1, 1]."""
    text = "Revenue grew 15% year-over-year driven by strong demand in our core markets."
    results = finbert_extractor.extract_sentiment(text)
    assert results
    for r in results:
        assert -1.0 <= r["score"] <= 1.0, (
            f"Score {r['score']} out of [-1, 1] range for: '{r['text'][:60]}'"
        )


def test_finbert_positive_sentence_positive_score(finbert_available):
    """A clearly positive sentence must produce score > 0."""
    score = finbert_extractor.mean_sentiment(
        "We delivered record earnings and raised our full-year guidance."
    )
    assert score is not None
    assert score > 0, f"Expected positive score, got {score}"


def test_finbert_negative_sentence_negative_score(finbert_available):
    """A clearly negative sentence must produce score < 0."""
    score = finbert_extractor.mean_sentiment(
        "We reported a significant loss and are cutting our dividend due to deteriorating conditions."
    )
    assert score is not None
    assert score < 0, f"Expected negative score, got {score}"


def test_finbert_empty_input_returns_empty(finbert_available):
    """Empty or very short input must return an empty result list."""
    results = finbert_extractor.extract_sentiment("")
    assert results == [], f"Expected empty list, got {results}"

    results = finbert_extractor.extract_sentiment("Hi.")
    assert isinstance(results, list)  # may be empty or one result — just must not crash


def test_finbert_mean_returns_none_when_unavailable():
    """mean_sentiment must return None (not raise) when model is unavailable."""
    # This tests the graceful fallback path by temporarily patching the pipeline.
    import src.models.finbert_extractor as fe
    original = fe._pipeline
    fe._pipeline = None  # simulate unavailable
    try:
        result = fe.mean_sentiment("Test sentence.")
        assert result is None
    finally:
        fe._pipeline = original
