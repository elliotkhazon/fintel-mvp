"""Analyst pressure index — SBERT cosine similarity clustering on Q&A questions.

analyst_pressure_index = min(1.0, repeated_question_pairs / 5)

"Repeated" = cosine similarity ≥ 0.85.
Returns 0.0 when:
  - Q&A section is absent or has fewer than 2 questions.
  - sentence-transformers is not installed.

Score range: [0.0, 1.0]
"""

from __future__ import annotations

import re
from typing import Optional

_model = None  # lazy singleton
_MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"


def _load_model():
    global _model
    if _model is not None:
        return _model
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(_MODEL_NAME)
    except Exception:
        _model = None
    return _model


def _extract_questions(text: str) -> list[str]:
    """Extract analyst questions from Q&A section.

    Looks for patterns like:
      ANALYST 1 (Name, Firm): question text
      ANALYST: question text
    """
    qa_match = re.search(r"ANALYST Q&A:?(.*?)$", text, re.DOTALL | re.IGNORECASE)
    if not qa_match:
        return []

    qa_text = qa_match.group(1)
    # Match "ANALYST N ..." or "ANALYST:" lines followed by question text up to next EXECUTIVE/ANALYST
    question_blocks = re.findall(
        r"ANALYST\s*\d*\s*(?:\([^)]*\))?:\s*(.*?)(?=\n\s*(?:EXECUTIVE|ANALYST\s*\d)|$)",
        qa_text,
        re.DOTALL | re.IGNORECASE,
    )
    questions = []
    for block in question_blocks:
        q = block.strip().replace("\n", " ")
        if len(q) > 10:
            questions.append(q)
    return questions


def _cosine_similarity(a, b) -> float:
    """Compute cosine similarity between two numpy arrays."""
    import numpy as np
    norm_a = float(np.linalg.norm(a))
    norm_b = float(np.linalg.norm(b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def score(text: str, similarity_threshold: float = 0.70, max_pairs: int = 5) -> float:
    """Compute analyst pressure index for the given transcript text.

    Returns 0.0 if Q&A section is missing or fewer than 2 questions found,
    or if sentence-transformers is unavailable.
    """
    questions = _extract_questions(text)
    if len(questions) < 2:
        return 0.0

    model = _load_model()
    if model is None:
        return 0.0

    try:
        embeddings = model.encode(questions, show_progress_bar=False)
    except Exception:
        return 0.0

    repeated_pairs = 0
    n = len(embeddings)
    for i in range(n):
        for j in range(i + 1, n):
            sim = _cosine_similarity(embeddings[i], embeddings[j])
            if sim >= similarity_threshold:
                repeated_pairs += 1

    return round(min(1.0, repeated_pairs / max_pairs), 4)


def score_questions(questions: list[str], similarity_threshold: float = 0.70) -> float:
    """Compute pressure index from a pre-split list of questions."""
    if len(questions) < 2:
        return 0.0
    model = _load_model()
    if model is None:
        return 0.0
    try:
        embeddings = model.encode(questions, show_progress_bar=False)
    except Exception:
        return 0.0

    repeated_pairs = 0
    n = len(embeddings)
    for i in range(n):
        for j in range(i + 1, n):
            if _cosine_similarity(embeddings[i], embeddings[j]) >= similarity_threshold:
                repeated_pairs += 1
    return round(min(1.0, repeated_pairs / 5), 4)


def is_available() -> bool:
    """Return True if sentence-transformers can be loaded."""
    return _load_model() is not None
