"""Step 0.2b / 2.7 — Confidence scorer (spaCy) tests.

Validates score range, assertive/hedge verb detection, and fallbacks.
spaCy is used when available; regex fallback is tested directly.

Skipped automatically when spaCy is not installed.

Run:
    pytest tests/functional/test_confidence_scorer.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.models import confidence_scorer

FIXTURES_PATH = Path(__file__).parent / "fixtures" / "confidence_fixtures.json"


def load_fixtures() -> list[dict]:
    with open(FIXTURES_PATH) as f:
        return json.load(f)


# ─── Score range validation ───────────────────────────────────────────────────

@pytest.mark.parametrize("fixture", load_fixtures())
def test_fixture_score(fixture: dict):
    """confidence_score must satisfy the fixture's expectation."""
    text = fixture["text"]
    result = confidence_scorer.score(text)

    # Valid range always required.
    assert 0.0 <= result <= 1.0, (
        f"Score {result} out of [0, 1] for: '{fixture.get('description', text[:40])}'"
    )

    if "expected_fallback" in fixture:
        assert result == fixture["expected_fallback"], (
            f"Expected fallback {fixture['expected_fallback']}, got {result}"
        )
    if "expected_min" in fixture:
        assert result >= fixture["expected_min"], (
            f"Score {result} below expected_min {fixture['expected_min']} "
            f"for: '{fixture.get('description')}'"
        )
    if "expected_max" in fixture:
        assert result <= fixture["expected_max"], (
            f"Score {result} above expected_max {fixture['expected_max']} "
            f"for: '{fixture.get('description')}'"
        )
    if "expected_range" in fixture:
        lo, hi = fixture["expected_range"]
        assert lo <= result <= hi, f"Score {result} outside [{lo}, {hi}]"


def test_empty_input_returns_neutral():
    """Empty string must return 0.5 (neutral fallback)."""
    assert confidence_scorer.score("") == 0.5


def test_assertive_heavy_scores_high():
    """Text with only assertive verbs must score above 0.5."""
    text = "We will deliver exceptional results. We expect to achieve our targets. We commit to growth."
    result = confidence_scorer.score(text)
    assert result > 0.5, f"Assertive text scored {result}, expected > 0.5"


def test_hedge_heavy_scores_low():
    """Text with only hedge verbs must score below 0.5."""
    text = "We believe conditions might improve. We assume demand could recover. We hope the environment may stabilize."
    result = confidence_scorer.score(text)
    assert result < 0.5, f"Hedge text scored {result}, expected < 0.5"


def test_score_is_float():
    """Score must always be a float."""
    result = confidence_scorer.score("Any text here.")
    assert isinstance(result, float), f"Expected float, got {type(result)}"


def test_regex_fallback():
    """Regex fallback (no spaCy) produces a score in [0, 1]."""
    text = "We will achieve targets. We believe we might face headwinds."
    result = confidence_scorer._score_regex(text)
    assert 0.0 <= result <= 1.0, f"Regex fallback score {result} out of range"
    # Should score assertive higher: "will" and "achieve" vs "believe" and "might"
    assertive_only = "We will achieve our targets and deliver results."
    hedge_only = "We believe we might face headwinds and assume conditions could worsen."
    assert confidence_scorer._score_regex(assertive_only) > confidence_scorer._score_regex(hedge_only)


# ─── NLP model availability test ─────────────────────────────────────────────

def test_availability_check_does_not_crash():
    """is_available() must return a bool and not raise."""
    result = confidence_scorer.is_available()
    assert isinstance(result, bool)
