"""Step 0.2c / 1.4 — Regime classifier tests.

SurrealDB response shapes (Python SDK v2.x):
  SELECT ... → list[dict] flat list of row dicts.

Run:
    pytest tests/functional/test_regime_classifier.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.models.regime_classifier import (
    ALL_REGIME_LABELS,
    RegimeClassifier,
    classify_by_year,
)

MODEL_PATH = Path(__file__).parent.parent.parent / "models" / "hmm_regime.pkl"

KNOWN_ASSIGNMENTS = [
    (2016, 1, "GrowthExpansion"),
    (2017, 3, "GrowthExpansion"),
    (2019, 4, "GrowthExpansion"),
    (2020, 2, "BlackSwan"),
    (2021, 1, "BlackSwan"),
    (2022, 1, "HighInflation"),
    (2023, 3, "HighInflation"),
    (2024, 2, "AIExpansion"),
    (2025, 4, "AIExpansion"),
]


# ─── Deterministic classifier ─────────────────────────────────────────────────

@pytest.mark.parametrize("year,quarter,expected", KNOWN_ASSIGNMENTS)
def test_deterministic_regime_assignment(year: int, quarter: int, expected: str):
    result = classify_by_year(year)
    assert result == expected, (
        f"Year {year} Q{quarter}: expected '{expected}', got '{result}'"
    )


def test_all_regime_labels_covered():
    seen = {classify_by_year(y) for y in range(2016, 2027)}
    assert seen == set(ALL_REGIME_LABELS), (
        f"Not all regimes reachable. Covered: {seen}"
    )


def test_boundary_years():
    assert classify_by_year(2010) == "GrowthExpansion"
    assert classify_by_year(2030) == "AIExpansion"


# ─── RegimeClassifier class ───────────────────────────────────────────────────

def test_classifier_deterministic_mode():
    clf = RegimeClassifier()
    assert not clf.hmm_loaded
    assert clf.classify(2020, 2) == "BlackSwan"
    assert clf.classify(2022, 1) == "HighInflation"


@pytest.mark.skipif(not MODEL_PATH.exists(), reason="HMM not fitted yet (run fit-hmm first)")
def test_classifier_hmm_loads():
    clf = RegimeClassifier()
    loaded = clf.load_hmm(MODEL_PATH)
    assert loaded, f"Failed to load HMM from {MODEL_PATH}"
    assert clf.hmm_loaded


@pytest.mark.skipif(not MODEL_PATH.exists(), reason="HMM not fitted yet (run fit-hmm first)")
@pytest.mark.parametrize("year,quarter,expected", KNOWN_ASSIGNMENTS)
def test_hmm_regime_assignment(year: int, quarter: int, expected: str):
    clf = RegimeClassifier()
    clf.load_hmm(MODEL_PATH)
    result = clf.classify(year, quarter)
    assert result == expected


# ─── DB-level regime node checks ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_all_regime_nodes_in_db(db):
    """All 4 regime nodes must be present in SurrealDB after generate-synthetic runs."""
    rows = await db.query("SELECT label FROM regime")
    # SELECT returns list[dict] directly
    assert isinstance(rows, list), f"Expected list from SELECT, got {type(rows)}"
    labels = {r.get("label") for r in rows if isinstance(r, dict)}
    missing = set(ALL_REGIME_LABELS) - labels
    assert not missing, f"Missing regime nodes in DB: {missing}"


@pytest.mark.asyncio
async def test_regime_nodes_have_hmm_state_ids(db):
    """Each regime node must have a unique hmm_state_id in [0, 3]."""
    rows = await db.query("SELECT label, hmm_state_id FROM regime")
    assert isinstance(rows, list)
    state_ids = [r.get("hmm_state_id") for r in rows if isinstance(r, dict)]
    assert sorted(state_ids) == [0, 1, 2, 3], (
        f"Expected hmm_state_ids [0,1,2,3], got {sorted(state_ids)}"
    )
