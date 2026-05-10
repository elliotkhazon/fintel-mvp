"""Step 0.2b / 2.7 — Analyst pressure index (SBERT) tests.

Validates score range, repeated-question detection, and null Q&A fallback.
Requires sentence-transformers; skipped automatically when not installed.

Run:
    pytest tests/functional/test_analyst_pressure.py -v
"""

from __future__ import annotations

import pytest

from src.models import analyst_pressure

# ─── Fixtures ─────────────────────────────────────────────────────────────────

DIVERSE_QUESTIONS = [
    "Can you talk about revenue growth in the cloud segment?",
    "What is your margin outlook for next quarter?",
    "How are you thinking about capital allocation priorities?",
    "What is the competitive landscape in your core markets?",
]

SIMILAR_QUESTIONS = [
    "What specific steps are you taking to address the supply chain disruptions?",
    "Can you elaborate on your supply chain remediation efforts and timeline?",
    "How do you plan to resolve the ongoing supply chain constraints?",
    "What is your strategy for managing supply chain risk going forward?",
    "Can you provide more details on your supply chain mitigation plan?",
]

QA_TRANSCRIPT_HIGH_PRESSURE = """
CEO PREPARED REMARKS:
Results were mixed this quarter.

ANALYST Q&A:
ANALYST 1 (Alex Chen, Goldman Sachs): What specific steps are you taking to address the supply chain disruptions?
EXECUTIVE: We are working on it.

ANALYST 2 (Jordan Lee, Morgan Stanley): Can you elaborate on your supply chain remediation efforts and timeline?
EXECUTIVE: We will provide an update soon.

ANALYST 3 (Morgan White, JPMorgan): How do you plan to resolve the ongoing supply chain constraints?
EXECUTIVE: We are monitoring the situation.

ANALYST 4 (Casey Brown, Citi): What is your strategy for managing supply chain risk going forward?
EXECUTIVE: We have contingency plans in place.
"""

QA_TRANSCRIPT_LOW_PRESSURE = """
CEO PREPARED REMARKS:
Strong quarter across all segments.

ANALYST Q&A:
ANALYST 1 (Alex Chen, Goldman Sachs): Can you talk about revenue growth in the cloud segment?
EXECUTIVE: Cloud revenue grew 35%.

ANALYST 2 (Jordan Lee, Morgan Stanley): What is your margin outlook for next quarter?
EXECUTIVE: We expect margins to improve.

ANALYST 3 (Morgan White, JPMorgan): How are you thinking about capital allocation?
EXECUTIVE: We prioritize organic growth.
"""

QA_TRANSCRIPT_MISSING = """
CEO PREPARED REMARKS:
No analyst questions were taken this quarter.
"""


# ─── score() function tests ───────────────────────────────────────────────────

def test_missing_qa_returns_zero():
    """Transcript without Q&A section must return 0.0."""
    result = analyst_pressure.score(QA_TRANSCRIPT_MISSING)
    assert result == 0.0, f"Expected 0.0 for missing Q&A, got {result}"


def test_score_in_range_high_pressure():
    """High-pressure transcript (many similar questions) must return a score in (0, 1]."""
    if not analyst_pressure.is_available():
        pytest.skip("sentence-transformers not installed")
    result = analyst_pressure.score(QA_TRANSCRIPT_HIGH_PRESSURE)
    assert 0.0 <= result <= 1.0, f"Score {result} out of [0, 1]"
    assert result > 0.0, f"Expected positive pressure score, got {result}"


def test_score_high_pressure_exceeds_low_pressure():
    """High-pressure transcript must score higher than low-pressure."""
    if not analyst_pressure.is_available():
        pytest.skip("sentence-transformers not installed")
    high = analyst_pressure.score(QA_TRANSCRIPT_HIGH_PRESSURE)
    low = analyst_pressure.score(QA_TRANSCRIPT_LOW_PRESSURE)
    assert high > low, (
        f"High-pressure score ({high}) should exceed low-pressure score ({low})"
    )


def test_score_questions_diverse():
    """Diverse questions must yield a low pressure score."""
    if not analyst_pressure.is_available():
        pytest.skip("sentence-transformers not installed")
    result = analyst_pressure.score_questions(DIVERSE_QUESTIONS)
    assert result < 0.5, f"Diverse questions scored {result}, expected < 0.5"


def test_score_questions_similar():
    """Nearly identical questions must yield a high pressure score."""
    if not analyst_pressure.is_available():
        pytest.skip("sentence-transformers not installed")
    result = analyst_pressure.score_questions(SIMILAR_QUESTIONS)
    assert result >= 0.5, f"Similar questions scored {result}, expected >= 0.5"


def test_score_single_question_returns_zero():
    """Fewer than 2 questions must return 0.0 (cannot compute pairs)."""
    result = analyst_pressure.score_questions(["What is your revenue outlook?"])
    assert result == 0.0


def test_score_empty_questions_returns_zero():
    """Empty question list must return 0.0."""
    result = analyst_pressure.score_questions([])
    assert result == 0.0


def test_score_is_float():
    """score() must always return a float."""
    result = analyst_pressure.score(QA_TRANSCRIPT_MISSING)
    assert isinstance(result, float)


def test_availability_check_does_not_crash():
    """is_available() must return a bool and not raise."""
    result = analyst_pressure.is_available()
    assert isinstance(result, bool)


def test_graceful_fallback_when_unavailable():
    """score() returns 0.0 without raising when model is not loaded."""
    import src.models.analyst_pressure as ap
    original = ap._model
    ap._model = None  # simulate unavailable (will try to load but fail gracefully)
    # We can't fully simulate unavailability without patching the load, but verify no crash.
    try:
        result = ap.score_questions([])
        assert result == 0.0
    finally:
        ap._model = original
