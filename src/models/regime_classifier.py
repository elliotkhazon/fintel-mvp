"""Market regime classifier — deterministic (year-range) and HMM-based.

Phase 0: deterministic lookup by year.
Phase 0 (pipeline validation): fit-hmm --synthetic generates hmm_regime.pkl from
  synthetic macro data so the full HMM code path can be exercised before FMP access.
Phase 1: fit-hmm (no flag) replaces the synthetic model with one trained on real
  VIX / Fed Funds / CPI data fetched from FMP.
"""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Optional

MODEL_PATH = Path(__file__).parent.parent.parent / "models" / "hmm_regime.pkl"

# Deterministic regime assignment by year range (used in Phase 0 synthetic data).
YEAR_TO_REGIME: dict[int, str] = {
    **{y: "GrowthExpansion" for y in range(2016, 2020)},
    **{y: "BlackSwan" for y in range(2020, 2022)},
    **{y: "HighInflation" for y in range(2022, 2024)},
    **{y: "AIExpansion" for y in range(2024, 2027)},
}

ALL_REGIME_LABELS = ["GrowthExpansion", "BlackSwan", "HighInflation", "AIExpansion"]

# Synthetic macro feature distributions used by fit_synthetic_hmm() and _classify_hmm().
# Tuples are (mean, std) for each of [VIX, Fed Funds Rate %, CPI YoY %].
REGIME_MACRO_FEATURES: dict[str, dict] = {
    "GrowthExpansion": {"vix": (13.0, 2.0),  "fed_funds": (1.5, 0.7),  "cpi": (2.0, 0.3)},
    "BlackSwan":       {"vix": (40.0, 15.0), "fed_funds": (0.2, 0.1),  "cpi": (1.5, 0.5)},
    "HighInflation":   {"vix": (22.0, 3.0),  "fed_funds": (3.5, 1.5),  "cpi": (7.0, 1.5)},
    "AIExpansion":     {"vix": (15.0, 2.5),  "fed_funds": (4.8, 0.5),  "cpi": (3.2, 0.6)},
}

# Regime metadata used when upserting regime nodes.
REGIME_META: dict[str, dict] = {
    "GrowthExpansion": {
        "start_date": "2016-01-01T00:00:00Z",
        "end_date": "2019-12-31T23:59:59Z",
        "hmm_state_id": 0,
        "key_signals": ["Revenue Growth", "Rate Guidance"],
    },
    "BlackSwan": {
        "start_date": "2020-01-01T00:00:00Z",
        "end_date": "2021-12-31T23:59:59Z",
        "hmm_state_id": 1,
        "key_signals": ["Supply Chain", "Tail Risk"],
    },
    "HighInflation": {
        "start_date": "2022-01-01T00:00:00Z",
        "end_date": "2023-12-31T23:59:59Z",
        "hmm_state_id": 2,
        "key_signals": ["Pricing Power", "Margin Compression"],
    },
    "AIExpansion": {
        "start_date": "2024-01-01T00:00:00Z",
        "end_date": "2026-12-31T23:59:59Z",
        "hmm_state_id": 3,
        "key_signals": ["AI Capex", "Data Center Demand"],
    },
}


def _synthetic_macro_features(year: int, quarter: int) -> list[float]:
    """Return a seeded synthetic macro feature vector [vix, fed_funds, cpi] for a quarter.

    Uses 10% of each regime's sigma so that regimes remain tightly clustered
    (clean HMM convergence) while covariance matrices stay non-degenerate.
    The full sigma is intentionally NOT used here: BlackSwan σ_vix=15 would
    produce samples from 10–70 that overlap with GrowthExpansion, preventing
    reliable single-point classification.
    """
    import random
    label = classify_by_year(year)
    cfg = REGIME_MACRO_FEATURES[label]
    rng = random.Random(year * 100 + quarter)
    noise_scale = 0.10
    vix = max(0.0, cfg["vix"][0] + rng.gauss(0, cfg["vix"][1] * noise_scale))
    fed = max(0.0, cfg["fed_funds"][0] + rng.gauss(0, cfg["fed_funds"][1] * noise_scale))
    cpi = cfg["cpi"][0] + rng.gauss(0, cfg["cpi"][1] * noise_scale)
    return [vix, fed, cpi]


def fit_synthetic_hmm(
    output_path: Path = MODEL_PATH,
    from_year: int = 2010,
    to_year: int = 2026,
) -> Path:
    """Fit a 4-state GaussianHMM on synthetic macro data and save to disk.

    Two problems with naive HMM fitting on these features:
      1. VIX (13–40) dominates Fed Funds (0.2–4.8) and CPI (1.5–7) — the model
         collapses to one state because VIX variance swamps the others.
      2. Random EM initialisation may not converge to regime-aligned clusters.

    Fixes: z-score normalise all features; seed the HMM means from regime centroids
    so the EM algorithm starts in the correct basin. The normalisation stats are saved
    alongside the model so inference uses the same scale.

    Phase 1 replaces this with fit_real_hmm() trained on actual FRED/FMP series.
    """
    import numpy as np
    from collections import Counter
    from hmmlearn.hmm import GaussianHMM

    # Fixed state order: index i → regime label (must match hmm_state_id in REGIME_META)
    STATE_ORDER = ["GrowthExpansion", "BlackSwan", "HighInflation", "AIExpansion"]
    n = len(STATE_ORDER)

    quarters = [(y, q) for y in range(from_year, to_year + 1) for q in range(1, 5)]
    X = np.array([_synthetic_macro_features(y, q) for y, q in quarters])

    # Z-score normalise so all three features contribute equally.
    X_mean = X.mean(axis=0)
    X_std = X.std(axis=0) + 1e-8
    X_scaled = (X - X_mean) / X_std

    # Regime centroid means in the normalised space.
    raw_centroids = np.array([
        [REGIME_MACRO_FEATURES[lbl]["vix"][0],
         REGIME_MACRO_FEATURES[lbl]["fed_funds"][0],
         REGIME_MACRO_FEATURES[lbl]["cpi"][0]]
        for lbl in STATE_ORDER
    ])
    init_means = (raw_centroids - X_mean) / X_std

    # Initialise all HMM parameters manually (init_params="") so EM starts at
    # the centroid means instead of a random assignment.
    model = GaussianHMM(
        n_components=n, covariance_type="full", n_iter=200,
        random_state=42, init_params="", params="stmc",
    )
    model.startprob_ = np.full(n, 1.0 / n)
    model.transmat_ = np.where(
        np.eye(n, dtype=bool), 0.85, 0.15 / (n - 1)
    )
    model.means_ = init_means
    model.covars_ = np.array([np.eye(3) for _ in range(n)])

    model.fit(X_scaled)

    # Majority-vote: which regime does each fitted state most often represent?
    predicted_states = model.predict(X_scaled)
    state_votes: dict[int, Counter] = {s: Counter() for s in range(n)}
    for (year, _), state in zip(quarters, predicted_states):
        state_votes[int(state)][classify_by_year(year)] += 1

    state_to_label: dict[int, str] = {}
    used_labels: set[str] = set()
    for state in range(n):
        for label, _ in state_votes[state].most_common():
            if label not in used_labels:
                state_to_label[state] = label
                used_labels.add(label)
                break

    remaining = [lb for lb in ALL_REGIME_LABELS if lb not in used_labels]
    for state in range(n):
        if state not in state_to_label and remaining:
            state_to_label[state] = remaining.pop(0)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        pickle.dump({
            "model": model,
            "state_to_label": state_to_label,
            "X_mean": X_mean,
            "X_std": X_std,
        }, f)

    return output_path


def classify_by_year(year: int) -> str:
    """Return the deterministic regime label for a given year.

    Falls back to the nearest boundary for years outside the defined range.
    """
    if year < 2016:
        return "GrowthExpansion"
    if year > 2026:
        return "AIExpansion"
    return YEAR_TO_REGIME.get(year, "AIExpansion")


class RegimeClassifier:
    """Wraps deterministic and HMM-based regime classification.

    Deterministic mode is always available. HMM mode requires `fit-hmm` to
    have been run and `models/hmm_regime.pkl` to exist.
    """

    def __init__(self) -> None:
        self._model: Optional[object] = None
        self._state_to_label: dict[int, str] = {}
        self._X_mean: Optional[object] = None
        self._X_std: Optional[object] = None
        self._hmm_loaded = False

    def load_hmm(self, path: Path = MODEL_PATH) -> bool:
        """Attempt to load the serialised HMM. Returns True on success."""
        if not path.exists():
            return False
        try:
            import numpy as np
            with open(path, "rb") as f:
                payload = pickle.load(f)
            self._model = payload["model"]
            self._state_to_label = payload["state_to_label"]
            self._X_mean = np.array(payload.get("X_mean", [0.0, 0.0, 0.0]))
            self._X_std = np.array(payload.get("X_std", [1.0, 1.0, 1.0]))
            self._hmm_loaded = True
            return True
        except Exception:
            return False

    def classify(self, year: int, quarter: int = 1) -> str:
        """Return regime label. Uses HMM if loaded, otherwise deterministic."""
        if self._hmm_loaded and self._model is not None:
            return self._classify_hmm(year, quarter)
        return classify_by_year(year)

    def _classify_hmm(self, year: int, quarter: int) -> str:
        """Predict regime via nearest centroid to the HMM's trained emission means.

        Viterbi on a single observation is dominated by startprob (which reflects
        regime frequency, not the query point), making it unreliable for point
        queries. Nearest-centroid in normalised feature space is the correct
        single-point equivalent of argmax P(x|state) with spherical covariance.

        Phase 0: synthetic macro features from REGIME_MACRO_FEATURES distributions.
        Phase 1: replace _synthetic_macro_features() with a real FRED/FMP lookup.
        """
        try:
            import numpy as np
            features = np.array(_synthetic_macro_features(year, quarter))
            features_scaled = (features - self._X_mean) / self._X_std
            dists = np.linalg.norm(self._model.means_ - features_scaled, axis=1)
            state = int(np.argmin(dists))
            return self._state_to_label.get(state, classify_by_year(year))
        except Exception:
            return classify_by_year(year)

    @property
    def hmm_loaded(self) -> bool:
        return self._hmm_loaded


_default_classifier = RegimeClassifier()


def get_regime(year: int, quarter: int = 1) -> str:
    """Module-level convenience: classify with the default singleton."""
    return _default_classifier.classify(year, quarter)
