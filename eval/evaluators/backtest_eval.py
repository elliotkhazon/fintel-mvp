"""Layer 4 — Backtest Performance Evaluator.

Queries a completed backtest_run from SurrealDB and computes all 7 metrics:
  directional_accuracy, hit_rate_by_regime, signal_attribution,
  precision_bull, precision_bear, threshold_sensitivity, relative_gap_accuracy
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any


async def evaluate_backtest(run_id: str) -> dict[str, Any]:
    """Compute all Layer 4 metrics for a completed backtest run."""
    from src.db.connection import get_db
    db = await get_db()

    # Locate the backtest_run record
    run_rows = await db.query(
        "SELECT id, run_id, directional_accuracy, hit_rate_by_regime "
        "FROM backtest_run WHERE run_id = $run_id",
        {"run_id": run_id},
    )
    run_records = run_rows if isinstance(run_rows, list) else []
    if not run_records:
        raise ValueError(f"No backtest_run found for run_id={run_id}")

    run = run_records[0]
    run_record_id = str(run.get("id", f"backtest_run:{run_id}"))

    # Fetch all predicted_by edges for this run
    pred_rows = await db.query(
        f"SELECT in, predicted_direction, correct, composite_score, signals_json "
        f"FROM predicted_by WHERE out = {run_record_id}"
    )
    preds = pred_rows if isinstance(pred_rows, list) else []

    empty = {
        "run_id": run_id,
        "total_predictions": 0,
        "directional_accuracy": None,
        "hit_rate_by_regime": {},
        "signal_attribution": {},
        "precision_bull": None,
        "precision_bear": None,
        "threshold_sensitivity": {},
        "relative_gap_accuracy": None,
    }
    if not preds:
        return empty

    # Enrich each prediction with price_gap_outcome data
    enriched: list[dict] = []
    for pred in preds:
        if not isinstance(pred, dict):
            continue
        gap_ref = pred.get("in")
        if gap_ref is None:
            continue
        gap_rows = await db.query(
            f"SELECT gap_direction, relative_gap, regime.label AS regime_label "
            f"FROM {gap_ref} FETCH regime"
        )
        gap_records = gap_rows if isinstance(gap_rows, list) else []
        gap = gap_records[0] if gap_records else {}

        signals = []
        signals_raw = pred.get("signals_json")
        if signals_raw:
            try:
                signals = json.loads(signals_raw)
            except Exception:
                pass

        enriched.append({
            "predicted_direction": pred.get("predicted_direction", "flat"),
            "correct": bool(pred.get("correct", False)),
            "composite_score": float(pred.get("composite_score") or 0.0),
            "signals": signals,
            "gap_direction": gap.get("gap_direction", "flat"),
            "relative_gap": gap.get("relative_gap"),
            "regime_label": gap.get("regime_label", "Unknown"),
        })

    if not enriched:
        return empty

    total = len(enriched)
    correct_count = sum(1 for e in enriched if e["correct"])
    directional_accuracy = round(correct_count / total, 4)

    # Hit rate by regime
    by_regime: dict[str, list[bool]] = defaultdict(list)
    for e in enriched:
        by_regime[e["regime_label"]].append(e["correct"])
    hit_rate_by_regime = {
        label: round(sum(vals) / len(vals), 4)
        for label, vals in by_regime.items()
    }

    # Signal attribution: mean score per signal for correct vs incorrect predictions
    sig_correct: dict[str, list[float]] = defaultdict(list)
    sig_incorrect: dict[str, list[float]] = defaultdict(list)
    for e in enriched:
        for sig in e["signals"]:
            name = sig.get("name", "")
            score = float(sig.get("score", 0.0))
            if e["correct"]:
                sig_correct[name].append(score)
            else:
                sig_incorrect[name].append(score)

    all_signal_names = set(sig_correct) | set(sig_incorrect)
    signal_attribution = {
        name: {
            "correct_mean": round(sum(sig_correct[name]) / len(sig_correct[name]), 4)
            if sig_correct[name] else None,
            "incorrect_mean": round(sum(sig_incorrect[name]) / len(sig_incorrect[name]), 4)
            if sig_incorrect[name] else None,
        }
        for name in all_signal_names
    }

    # Precision bull / bear
    tp_bull = sum(1 for e in enriched if e["predicted_direction"] == "up" and e["gap_direction"] == "up")
    fp_bull = sum(1 for e in enriched if e["predicted_direction"] == "up" and e["gap_direction"] != "up")
    tp_bear = sum(1 for e in enriched if e["predicted_direction"] == "down" and e["gap_direction"] == "down")
    fp_bear = sum(1 for e in enriched if e["predicted_direction"] == "down" and e["gap_direction"] != "down")

    precision_bull = round(tp_bull / (tp_bull + fp_bull), 4) if (tp_bull + fp_bull) > 0 else None
    precision_bear = round(tp_bear / (tp_bear + fp_bear), 4) if (tp_bear + fp_bear) > 0 else None

    # Threshold sensitivity
    threshold_sensitivity: dict[str, float | None] = {}
    for thresh in [0.1, 0.2, 0.3, 0.4]:
        t_correct = 0
        for e in enriched:
            composite = e["composite_score"]
            pred = "up" if composite > thresh else ("down" if composite < -thresh else "flat")
            if pred == e["gap_direction"]:
                t_correct += 1
        threshold_sensitivity[str(thresh)] = round(t_correct / total, 4)

    # Relative gap accuracy
    rel_correct = rel_total = 0
    for e in enriched:
        rel = e.get("relative_gap")
        if rel is None:
            continue
        actual_rel_dir = "up" if rel > 0.005 else ("down" if rel < -0.005 else "flat")
        if e["predicted_direction"] == actual_rel_dir:
            rel_correct += 1
        rel_total += 1
    relative_gap_accuracy = round(rel_correct / rel_total, 4) if rel_total > 0 else None

    return {
        "run_id": run_id,
        "total_predictions": total,
        "directional_accuracy": directional_accuracy,
        "hit_rate_by_regime": hit_rate_by_regime,
        "signal_attribution": signal_attribution,
        "precision_bull": precision_bull,
        "precision_bear": precision_bear,
        "threshold_sensitivity": threshold_sensitivity,
        "relative_gap_accuracy": relative_gap_accuracy,
    }


def run_evaluation(run_id: str) -> dict:
    """Synchronous entry point for CLI use."""
    return asyncio.run(evaluate_backtest(run_id))
