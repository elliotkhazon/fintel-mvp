"""Signal scoring — computes the 6-signal bundle from retrieved graph data.

All signals are normalized to [-1.0, 1.0] before weighting.
Composite score and beat_probability are derived from signal_weights.json.
"""

import json
from pathlib import Path

from src.models.graph_models import SignalBundle, SignalScore

WEIGHTS_PATH = Path(__file__).parent.parent.parent / "config" / "signal_weights.json"

_DEFAULT_WEIGHTS = {
    "management_confidence_shift": 0.25,
    "laggard_signal": 0.20,
    "guidance_gap": 0.20,
    "dso_trend": 0.12,
    "inventory_velocity": 0.10,
    "segment_mix_shift": 0.08,
    "analyst_target_gap": 0.05,
}


def _load_weights() -> dict[str, float]:
    if WEIGHTS_PATH.exists():
        return json.loads(WEIGHTS_PATH.read_text())
    return _DEFAULT_WEIGHTS


def _clamp(v: float) -> float:
    return max(-1.0, min(1.0, v))


def _direction(score: float) -> str:
    if score > 0.1:
        return "bullish"
    if score < -0.1:
        return "bearish"
    return "neutral"


# ---------------------------------------------------------------------------
# Individual signal calculators
# ---------------------------------------------------------------------------

def _management_confidence_shift(hop1: list[dict]) -> SignalScore:
    """Compare avg QA sentiment this quarter vs. prior 3 quarters."""
    if not hop1:
        return SignalScore(name="management_confidence_shift", score=0.0,
                           direction="neutral", evidence="No sentiment data available.")
    qa = [r for r in hop1 if r.get("section") == "qa"]
    if not qa:
        qa = hop1
    if len(qa) < 2:
        return SignalScore(name="management_confidence_shift", score=0.0,
                           direction="neutral", evidence="Insufficient QA data for trend.")
    scores = [float(r.get("score", 0)) for r in qa]
    current = scores[0]
    prior_avg = sum(scores[1:]) / len(scores[1:])
    delta = _clamp(current - prior_avg)
    evidence = f"Current QA sentiment: {current:.2f}, Prior avg: {prior_avg:.2f}, Delta: {delta:+.2f}"
    return SignalScore(name="management_confidence_shift", score=delta,
                       direction=_direction(delta), evidence=evidence)


def _laggard_signal(hop2: list[dict]) -> SignalScore:
    """1.0 if any competitor has recent positive sentiment (beat proxy)."""
    if not hop2:
        return SignalScore(name="laggard_signal", score=0.0,
                           direction="neutral", evidence="No competitor data available.")
    positive_competitors = []
    for comp in hop2:
        sigs = comp.get("signals", [])
        if sigs:
            avg = sum(float(s.get("score", 0)) for s in sigs) / len(sigs)
            if avg > 0.3:
                positive_competitors.append(f"{comp.get('competitor', '?')} ({avg:+.2f})")
    if positive_competitors:
        score = _clamp(0.5 + 0.1 * len(positive_competitors))
        evidence = f"Positive competitors: {', '.join(positive_competitors)}"
    else:
        score = -0.1
        evidence = "No competitors showing strong positive signals."
    return SignalScore(name="laggard_signal", score=score,
                       direction=_direction(score), evidence=evidence)


def _guidance_gap(guidance: dict) -> SignalScore:
    """(analyst_est - company_guide) / company_guide — positive = conservative guidance."""
    guide = guidance.get("company_guide")
    est = guidance.get("analyst_est")
    if guide is None or est is None or guide == 0:
        return SignalScore(name="guidance_gap", score=0.0,
                           direction="neutral", evidence="No guidance data available.")
    raw = (est - guide) / abs(guide)
    score = _clamp(raw * 2)  # scale: 50% gap → 1.0
    evidence = (f"Analyst est: {est:.2f}, Company guide: {guide:.2f}, "
                f"Gap: {raw:+.1%} ({'conservative' if raw > 0 else 'aggressive'} guidance)")
    return SignalScore(name="guidance_gap", score=score,
                       direction=_direction(score), evidence=evidence)


def _dso_trend(km_history: list[dict]) -> SignalScore:
    """dso[t-1] - dso[t] — positive delta = faster cash collection = quality signal."""
    dso_vals = [r.get("dso") for r in km_history if r.get("dso") is not None]
    if len(dso_vals) < 2:
        return SignalScore(name="dso_trend", score=0.0,
                           direction="neutral", evidence="Insufficient DSO history (<2 periods).")
    delta = float(dso_vals[1]) - float(dso_vals[0])  # prior - current (positive = improvement)
    score = _clamp(delta / 10.0)  # 10-day DSO improvement → score of 1.0
    evidence = f"DSO current: {dso_vals[0]:.1f}, prior: {dso_vals[1]:.1f}, delta: {delta:+.1f} days"
    return SignalScore(name="dso_trend", score=score,
                       direction=_direction(score), evidence=evidence)


def _inventory_velocity(km_history: list[dict]) -> SignalScore:
    """QoQ change in inventory_turnover — acceleration signals demand pull-through."""
    inv_vals = [r.get("inventory_turnover") for r in km_history if r.get("inventory_turnover") is not None]
    if len(inv_vals) < 2:
        return SignalScore(name="inventory_velocity", score=0.0,
                           direction="neutral", evidence="Insufficient inventory history (<2 periods).")
    current, prior = float(inv_vals[0]), float(inv_vals[1])
    if prior == 0:
        return SignalScore(name="inventory_velocity", score=0.0,
                           direction="neutral", evidence="Prior inventory turnover is zero.")
    pct_change = (current - prior) / abs(prior)
    score = _clamp(pct_change * 2)
    evidence = f"Inventory turnover current: {current:.2f}, prior: {prior:.2f}, Δ: {pct_change:+.1%}"
    return SignalScore(name="inventory_velocity", score=score,
                       direction=_direction(score), evidence=evidence)


def _segment_mix_shift(segments: list[dict]) -> SignalScore:
    """Growth of largest segment vs. blended revenue growth — positive divergence = tailwind."""
    if not segments:
        return SignalScore(name="segment_mix_shift", score=0.0,
                           direction="neutral", evidence="No segment data available.")
    periods = sorted(set(s.get("period", "") for s in segments), reverse=True)
    if len(periods) < 2:
        return SignalScore(name="segment_mix_shift", score=0.0,
                           direction="neutral", evidence="Only one period of segment data available.")
    curr_segs = {s["segment_name"]: s["revenue"] for s in segments if s.get("period") == periods[0]}
    prev_segs = {s["segment_name"]: s["revenue"] for s in segments if s.get("period") == periods[1]}
    if not curr_segs or not prev_segs:
        return SignalScore(name="segment_mix_shift", score=0.0,
                           direction="neutral", evidence="Insufficient segment data for comparison.")
    total_curr = sum(curr_segs.values()) or 1
    total_prev = sum(prev_segs.values()) or 1
    blended_growth = (total_curr - total_prev) / abs(total_prev)
    # Find highest-revenue segment in current period
    top_seg = max(curr_segs, key=curr_segs.get)
    top_curr = curr_segs.get(top_seg, 0)
    top_prev = prev_segs.get(top_seg, total_prev * 0.5)
    top_growth = (top_curr - top_prev) / abs(top_prev) if top_prev else 0
    divergence = top_growth - blended_growth
    score = _clamp(divergence * 2)
    evidence = (f"Top segment '{top_seg}': {top_growth:+.1%} growth vs blended {blended_growth:+.1%}; "
                f"mix-shift divergence: {divergence:+.1%}")
    return SignalScore(name="segment_mix_shift", score=score,
                       direction=_direction(score), evidence=evidence)


def _analyst_target_gap(analyst_targets: dict, guidance: dict) -> SignalScore:
    """(consensus - company_guide_implied) / implied — wide positive = analyst optimism."""
    consensus = analyst_targets.get("target_consensus")
    guide = guidance.get("company_guide")
    if consensus is None:
        return SignalScore(name="analyst_target_gap", score=0.0,
                           direction="neutral", evidence="No analyst consensus target available.")
    if guide is None:
        evidence = f"Analyst consensus target: {consensus:.2f} (no guidance to compare)"
        return SignalScore(name="analyst_target_gap", score=0.1,
                           direction="bullish", evidence=evidence)
    raw = (consensus - guide) / abs(guide)
    score = _clamp(raw)
    evidence = (f"Analyst consensus: {consensus:.2f}, company guide (proxy): {guide:.2f}, "
                f"gap: {raw:+.1%}")
    return SignalScore(name="analyst_target_gap", score=score,
                       direction=_direction(score), evidence=evidence)


# ---------------------------------------------------------------------------
# Bundle
# ---------------------------------------------------------------------------

def compute_signals(
    symbol: str,
    quarter: int,
    year: int,
    hop1: list[dict],
    hop2: list[dict],
    km_history: list[dict],
    segments: list[dict],
    analyst_targets: dict,
    guidance: dict,
) -> SignalBundle:
    weights = _load_weights()

    all_signals = [
        _management_confidence_shift(hop1),
        _laggard_signal(hop2),
        _guidance_gap(guidance),
        _dso_trend(km_history),
        _inventory_velocity(km_history),
        _segment_mix_shift(segments),
        _analyst_target_gap(analyst_targets, guidance),
    ]

    composite = sum(
        s.score * weights.get(s.name, 0.0) for s in all_signals
    )
    composite = _clamp(composite)

    if composite > 0.3:
        probability = "High"
    elif composite > 0.05:
        probability = "Medium"
    else:
        probability = "Low"

    return SignalBundle(
        symbol=symbol,
        quarter=quarter,
        year=year,
        composite_score=round(composite, 4),
        signals=all_signals,
        beat_probability=probability,
    )
