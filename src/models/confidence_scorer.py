"""Management confidence scorer — spaCy assertive/hedge verb ratio.

confidence_score = assertive_count / (assertive_count + hedge_count)
Returns 0.5 (neutral) when no relevant verbs are found or spaCy is unavailable.

Score range: [0.0, 1.0]
"""

from __future__ import annotations

import re

ASSERTIVE_LEMMAS = frozenset({"will", "expect", "achieve", "deliver", "commit"})
HEDGE_LEMMAS = frozenset({"believe", "estimate", "hope", "assume", "could", "might", "may"})

_nlp = None  # lazy singleton


def _load_nlp():
    global _nlp
    if _nlp is not None:
        return _nlp
    try:
        import spacy
        _nlp = spacy.load("en_core_web_sm", disable=["parser", "ner"])
    except Exception:
        _nlp = None
    return _nlp


def _extract_prepared_remarks(text: str) -> str:
    """Pull everything between CEO PREPARED REMARKS and ANALYST Q&A."""
    match = re.search(
        r"(?:PREPARED REMARKS?|CEO[:\s])(.*?)(?:ANALYST Q&A|FORWARD GUIDANCE:|$)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    return match.group(1).strip() if match else text


def score(text: str, use_prepared_only: bool = True) -> float:
    """Compute confidence score for the given transcript text.

    Falls back gracefully:
    - If spaCy unavailable: regex-based verb count.
    - If no relevant verbs found: returns 0.5.
    """
    section = _extract_prepared_remarks(text) if use_prepared_only else text
    if not section.strip():
        return 0.5

    nlp = _load_nlp()
    if nlp is not None:
        return _score_spacy(section, nlp)
    return _score_regex(section)


def _score_spacy(text: str, nlp) -> float:
    doc = nlp(text[:50_000])  # cap to avoid memory issues
    assertive = sum(1 for t in doc if t.lemma_.lower() in ASSERTIVE_LEMMAS and t.pos_ == "VERB")
    hedge = sum(1 for t in doc if t.lemma_.lower() in HEDGE_LEMMAS and t.pos_ in {"VERB", "AUX"})
    total = assertive + hedge
    return round(assertive / total, 4) if total > 0 else 0.5


def _score_regex(text: str) -> float:
    """Regex fallback when spaCy is not installed."""
    words = text.lower().split()
    assertive = sum(1 for w in words if re.sub(r"[^a-z]", "", w) in ASSERTIVE_LEMMAS)
    hedge = sum(1 for w in words if re.sub(r"[^a-z]", "", w) in HEDGE_LEMMAS)
    total = assertive + hedge
    return round(assertive / total, 4) if total > 0 else 0.5


def is_available() -> bool:
    """Return True if spaCy en_core_web_sm is loadable."""
    return _load_nlp() is not None
