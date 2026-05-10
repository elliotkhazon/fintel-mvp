"""Backtesting orchestrator — LangGraph pipeline.

START
  ↓
resolve_events       — query transcript_doc rows in [from_date, to_date] for ticker universe
  ↓
process_all_events   — per event: classify_regime, load price gap, compute signals,
                       compare outcome, persist regime edges + price_gap_outcome + predicted_by
  ↓
aggregate_metrics    — directional_accuracy, hit_rate_by_regime
  ↓
persist_metrics      — update backtest_run record
  ↓
END
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import TypedDict

_log = logging.getLogger(__name__)

from langgraph.graph import END, START, StateGraph

from src.db.normalizer import (
    upsert_backtest_run,
    upsert_occurred_during_edge,
    upsert_predicted_by_edge,
    upsert_price_gap_outcome,
    upsert_regime,
)
from src.models.graph_models import SignalBundle
from src.models.regime_classifier import REGIME_META, RegimeClassifier


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class BacktestState(TypedDict):
    run_id: str
    run_record_id: str
    ticker_universe: list[str]
    from_year: int
    to_year: int
    sentiment_threshold: float
    benchmark: str
    include_ext_hours: bool
    with_report: bool
    events: list[dict]
    results: list[dict]
    directional_accuracy: float | None
    hit_rate_by_regime: dict | None
    total_processed: int
    error: str | None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _predicted_direction(composite_score: float, threshold: float) -> str:
    if composite_score > threshold:
        return "up"
    if composite_score < -threshold:
        return "down"
    return "flat"


def _gap_dir_from_pct(gap_pct: float) -> str:
    if gap_pct > 0.005:
        return "up"
    if gap_pct < -0.005:
        return "down"
    return "flat"


def _neutral_bundle(ticker: str, quarter: int, year: int) -> SignalBundle:
    return SignalBundle(
        symbol=ticker, quarter=quarter, year=year,
        composite_score=0.0, beat_probability="Low", signals=[],
    )


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

async def resolve_events(state: BacktestState) -> BacktestState:
    """Query transcript_doc rows matching ticker universe and year range."""
    from src.db.connection import get_db
    db = await get_db()
    try:
        universe_upper = {t.upper() for t in state["ticker_universe"]}
        from_year = state["from_year"]
        to_year = state["to_year"]

        rows = await db.query(
            "SELECT id, raw_path, confidence_score, analyst_pressure_index, "
            "quarter, year, company.ticker AS ticker "
            "FROM transcript_doc "
            "WHERE year >= $from_year AND year <= $to_year",
            {"from_year": from_year, "to_year": to_year},
        )
        all_rows = rows if isinstance(rows, list) else []

        if universe_upper:
            all_rows = [
                r for r in all_rows
                if isinstance(r, dict) and str(r.get("ticker", "")).upper() in universe_upper
            ]

        return {**state, "events": all_rows}
    except Exception as exc:
        return {**state, "error": f"resolve_events failed: {exc}", "events": []}


async def process_all_events(state: BacktestState) -> BacktestState:
    """For each resolved event: classify regime, load price gap, score signals, persist."""
    if state.get("error"):
        return state

    from src.agents.signal_agent import compute_signals
    from src.db.connection import get_db
    from src.db.graph_queries import (
        fetch_analyst_targets,
        fetch_guidance,
        fetch_key_metrics_history,
        fetch_segments,
        hop1_sentiment,
        hop2_competitor_signals,
    )

    db = await get_db()
    classifier = RegimeClassifier()
    classifier.load_hmm()

    # Ensure all 4 regime nodes exist in SurrealDB
    regime_ids: dict[str, str] = {}
    for label, meta in REGIME_META.items():
        rid = await upsert_regime(
            db, label,
            start_date=meta["start_date"],
            end_date=meta["end_date"],
            hmm_state_id=meta["hmm_state_id"],
            key_signals=meta["key_signals"],
        )
        regime_ids[label] = rid

    results: list[dict] = []

    for event in state["events"]:
        if not isinstance(event, dict):
            continue

        transcript_id = str(event.get("id", ""))
        ticker = str(event.get("ticker", "")).upper()
        year = int(event.get("year", 0))
        quarter = int(event.get("quarter", 0))
        raw_path = event.get("raw_path") or ""

        if not transcript_id or not ticker or not year or not quarter:
            continue

        # 1. Classify regime
        regime_label = classifier.classify(year, quarter)
        regime_id = regime_ids.get(regime_label, f"regime:{regime_label.lower()}")

        # 2. Link transcript → regime (occurred_during edge)
        try:
            await upsert_occurred_during_edge(db, transcript_id, regime_id)
        except Exception:
            pass

        # 3. Load price gap data from raw JSON file
        gap_data: dict = {}
        p = Path(raw_path)
        if p.exists():
            try:
                with open(p, encoding="utf-8") as fh:
                    gap_data = json.load(fh)
            except Exception:
                pass

        prev_close = float(gap_data.get("prev_close", 100.0))
        next_open = float(gap_data.get("next_open", 100.0))
        gap_pct = float(gap_data.get("gap_pct", 0.0))
        gap_direction = gap_data.get("gap_direction") or _gap_dir_from_pct(gap_pct)
        benchmark_return = gap_data.get("benchmark_return")
        relative_gap = gap_data.get("relative_gap")
        earnings_date = gap_data.get("earnings_date") or f"{year}-01-01T00:00:00Z"
        is_ext = bool(gap_data.get("is_extended_hours", state["include_ext_hours"]))

        company_id = f"company:{ticker.lower()}"

        # 4. Upsert price_gap_outcome
        try:
            gap_outcome_id = await upsert_price_gap_outcome(
                db,
                company_id=company_id,
                transcript_id=transcript_id,
                regime_id=regime_id,
                earnings_date=earnings_date,
                prev_close=prev_close,
                next_open=next_open,
                gap_pct=gap_pct,
                gap_direction=gap_direction,
                is_extended_hours=is_ext,
                benchmark_return=benchmark_return,
                relative_gap=relative_gap,
            )
        except Exception:
            slug = transcript_id.split(":")[-1]
            gap_outcome_id = f"price_gap_outcome:{ticker.lower()}_{slug}"

        # 5. Score signals (no LLM by default; use LLM only if with_report)
        bundle = _neutral_bundle(ticker, quarter, year)
        if state.get("with_report"):
            try:
                from src.agents.prediction_agent import run_prediction
                report = await run_prediction(ticker, quarter, year)
                bundle = report.signals
            except Exception as exc:
                _log.warning("run_prediction failed for %s Q%d %d: %s", ticker, quarter, year, exc)
        else:
            # Run each graph query independently so one failure doesn't abort all.
            # asyncio.gather on a single WebSocket connection can race and fail silently.
            try:
                hop1 = await hop1_sentiment(db, ticker)
            except Exception as exc:
                _log.warning("hop1_sentiment %s Q%d %d: %s", ticker, quarter, year, exc)
                hop1 = []

            try:
                hop2 = await hop2_competitor_signals(db, ticker)
            except Exception as exc:
                _log.warning("hop2_competitor_signals %s Q%d %d: %s", ticker, quarter, year, exc)
                hop2 = []

            try:
                km = await fetch_key_metrics_history(db, ticker)
            except Exception as exc:
                _log.warning("fetch_key_metrics_history %s Q%d %d: %s", ticker, quarter, year, exc)
                km = []

            try:
                segs = await fetch_segments(db, ticker)
            except Exception as exc:
                _log.warning("fetch_segments %s Q%d %d: %s", ticker, quarter, year, exc)
                segs = []

            try:
                at = await fetch_analyst_targets(db, ticker)
            except Exception as exc:
                _log.warning("fetch_analyst_targets %s Q%d %d: %s", ticker, quarter, year, exc)
                at = {}

            try:
                guidance = await fetch_guidance(db, ticker, quarter, year)
            except Exception as exc:
                _log.warning("fetch_guidance %s Q%d %d: %s", ticker, quarter, year, exc)
                guidance = {}

            # Seed hop1 from the transcript's own confidence_score when no
            # expressed_sentiment edges exist (synthetic data, pre-NLP pipeline).
            # Maps [0,1] → [-1,1] with ×6 scale so ≥0.647 → bullish signal.
            if not hop1 and event.get("confidence_score") is not None:
                raw = (float(event["confidence_score"]) - 0.5) * 6
                conf_score = max(-1.0, min(1.0, raw))
                hop1 = [
                    {"score": conf_score, "section": "qa", "quarter": quarter, "year": year},
                    {"score": 0.0, "section": "qa", "quarter": max(1, quarter - 1), "year": year},
                ]

            try:
                bundle = compute_signals(
                    symbol=ticker, quarter=quarter, year=year,
                    hop1=hop1, hop2=hop2, km_history=km,
                    segments=segs, analyst_targets=at, guidance=guidance,
                )
            except Exception as exc:
                _log.error(
                    "compute_signals failed for %s Q%d %d: %s", ticker, quarter, year, exc
                )

        predicted_direction = _predicted_direction(
            bundle.composite_score, state["sentiment_threshold"]
        )
        correct = predicted_direction == gap_direction

        # 6. Persist predicted_by edge
        signal_bundle_dict = bundle.model_dump()
        try:
            await upsert_predicted_by_edge(
                db,
                gap_outcome_id=gap_outcome_id,
                run_record_id=state["run_record_id"],
                signal_bundle=signal_bundle_dict,
                predicted_direction=predicted_direction,
                correct=correct,
            )
        except Exception:
            pass

        results.append({
            "transcript_id": transcript_id,
            "ticker": ticker,
            "year": year,
            "quarter": quarter,
            "regime_label": regime_label,
            "gap_direction": gap_direction,
            "predicted_direction": predicted_direction,
            "correct": correct,
            "composite_score": bundle.composite_score,
            "signal_bundle": signal_bundle_dict,
        })

    return {**state, "results": results, "total_processed": len(results)}


def aggregate_metrics(state: BacktestState) -> BacktestState:
    """Compute directional_accuracy and hit_rate_by_regime from in-memory results."""
    if state.get("error") or not state.get("results"):
        return {**state, "directional_accuracy": None, "hit_rate_by_regime": None}

    results = state["results"]
    total = len(results)
    correct_count = sum(1 for r in results if r["correct"])
    directional_accuracy = round(correct_count / total, 4) if total > 0 else None

    by_regime: dict[str, list[bool]] = {}
    for r in results:
        label = r.get("regime_label", "Unknown")
        by_regime.setdefault(label, []).append(r["correct"])

    hit_rate_by_regime = {
        label: round(sum(vals) / len(vals), 4)
        for label, vals in by_regime.items()
        if vals
    }

    return {**state, "directional_accuracy": directional_accuracy, "hit_rate_by_regime": hit_rate_by_regime}


async def persist_metrics(state: BacktestState) -> BacktestState:
    """Write final accuracy metrics back to the backtest_run record."""
    if state.get("error"):
        return state
    from src.db.connection import get_db
    db = await get_db()
    try:
        await db.merge(state["run_record_id"], {
            "directional_accuracy": state.get("directional_accuracy"),
            "hit_rate_by_regime": state.get("hit_rate_by_regime"),
        })
    except Exception as exc:
        return {**state, "error": f"persist_metrics failed: {exc}"}
    return state


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

_graph = StateGraph(BacktestState)
_graph.add_node("resolve_events", resolve_events)
_graph.add_node("process_all_events", process_all_events)
_graph.add_node("aggregate_metrics", aggregate_metrics)
_graph.add_node("persist_metrics", persist_metrics)

_graph.add_edge(START, "resolve_events")
_graph.add_edge("resolve_events", "process_all_events")
_graph.add_edge("process_all_events", "aggregate_metrics")
_graph.add_edge("aggregate_metrics", "persist_metrics")
_graph.add_edge("persist_metrics", END)

backtest_graph = _graph.compile()


async def run_backtest(
    ticker_universe: list[str],
    from_date: str,
    to_date: str,
    sentiment_threshold: float = 0.2,
    benchmark: str = "SPY",
    include_ext_hours: bool = True,
    with_report: bool = False,
    run_id: str | None = None,
) -> dict:
    """Run the full backtesting pipeline. Returns the final BacktestState dict."""
    if run_id is None:
        run_id = str(uuid.uuid4())

    from src.db.connection import get_db
    db = await get_db()

    from_year = int(from_date[:4])
    to_year = int(to_date[:4])

    run_record_id = await upsert_backtest_run(
        db,
        run_id=run_id,
        ticker_universe=ticker_universe,
        from_date=from_date,
        to_date=to_date,
        sentiment_threshold=sentiment_threshold,
        benchmark=benchmark,
        include_ext_hours=include_ext_hours,
        event_type="Earnings",
    )

    initial: BacktestState = {
        "run_id": run_id,
        "run_record_id": run_record_id,
        "ticker_universe": ticker_universe,
        "from_year": from_year,
        "to_year": to_year,
        "sentiment_threshold": sentiment_threshold,
        "benchmark": benchmark,
        "include_ext_hours": include_ext_hours,
        "with_report": with_report,
        "events": [],
        "results": [],
        "directional_accuracy": None,
        "hit_rate_by_regime": None,
        "total_processed": 0,
        "error": None,
    }

    return await backtest_graph.ainvoke(initial)
