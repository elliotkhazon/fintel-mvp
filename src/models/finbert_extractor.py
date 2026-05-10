"""FinBERT sentence-level sentiment extractor.

Lazy-loads ProsusAI/finbert via HuggingFace Transformers. Falls back to
returning None if the library or model are unavailable (CPU-only safe).

Output schema per sentence:
    {"text": str, "label": "positive"|"negative"|"neutral", "score": float [-1, 1]}
"""

from __future__ import annotations

import re
from typing import Optional

_pipeline = None      # lazy singleton
_pipeline_tried = False  # sentinel so patching _pipeline=None doesn't trigger reload


def _load_pipeline():
    global _pipeline, _pipeline_tried
    if _pipeline_tried:
        return _pipeline
    _pipeline_tried = True
    try:
        from transformers import pipeline as hf_pipeline
        _pipeline = hf_pipeline(
            "text-classification",
            model="ProsusAI/finbert",
            tokenizer="ProsusAI/finbert",
            top_k=None,  # return all label scores
            device=-1,   # CPU
        )
    except Exception:
        _pipeline = None
    return _pipeline


def _score_from_labels(label_scores: list[dict]) -> float:
    """Convert FinBERT label/score pairs to a single [-1, 1] float."""
    mapping = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}
    best = max(label_scores, key=lambda x: x["score"])
    return round(mapping.get(best["label"].lower(), 0.0) * best["score"], 4)


def _split_sentences(text: str) -> list[str]:
    """Naive sentence splitter on . ! ? boundaries."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if len(p.strip()) > 10]


def extract_sentiment(
    text: str,
    section: str = "prepared",
    max_sentences: int = 50,
) -> list[dict]:
    """Run FinBERT over each sentence in `text`.

    Returns a list of dicts:
        [{"text": ..., "label": ..., "score": float [-1,1]}, ...]

    Returns an empty list if FinBERT is not available.
    """
    pipe = _load_pipeline()
    if pipe is None:
        return []

    sentences = _split_sentences(text)[:max_sentences]
    if not sentences:
        return []

    results = []
    for sentence in sentences:
        try:
            raw = pipe(sentence[:512])  # FinBERT max 512 tokens
            # raw is list[list[dict]] when top_k=None
            label_scores = raw[0] if isinstance(raw[0], list) else raw
            score = _score_from_labels(label_scores)
            best_label = max(label_scores, key=lambda x: x["score"])["label"].lower()
            results.append({"text": sentence, "label": best_label, "score": score})
        except Exception:
            continue

    return results


def mean_sentiment(text: str) -> Optional[float]:
    """Return mean sentiment score across all sentences, or None if unavailable."""
    results = extract_sentiment(text)
    if not results:
        return None
    return round(sum(r["score"] for r in results) / len(results), 4)


def is_available() -> bool:
    """Return True if FinBERT can be loaded."""
    return _load_pipeline() is not None
