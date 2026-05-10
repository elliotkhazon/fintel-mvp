"""Prediction orchestrator — LangGraph pipeline.

START
  ↓
fetch_all_data   (hops 1-3 + fundamentals + analyst targets, concurrent via asyncio.gather)
  ↓
score_signals    (compute 7-signal SignalBundle)
  ↓
generate_report  (Gemini LLM → narrative Probability Report)
  ↓
END
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import TypedDict

from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, StateGraph

from src.agents.signal_agent import compute_signals
from src.db.connection import get_db
from src.db.graph_queries import (
    fetch_analyst_targets,
    fetch_guidance,
    fetch_key_metrics_history,
    fetch_segments,
    hop1_sentiment,
    hop2_competitor_signals,
    hop3_supplier_signals,
)
from src.models.graph_models import PredictionReport, SignalBundle

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


class PredictionState(TypedDict):
    symbol: str
    quarter: int
    year: int
    hop1: list[dict]
    hop2: list[dict]
    hop3: list[dict]
    km_history: list[dict]
    segments: list[dict]
    analyst_targets: dict
    guidance: dict
    signals: SignalBundle | None
    report: str | None
    error: str | None


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

async def fetch_all_data(state: PredictionState) -> PredictionState:
    """Fetch all five data sources concurrently."""
    db = await get_db()
    ticker = state["symbol"].upper()
    q, y = state["quarter"], state["year"]
    try:
        hop1, hop2, hop3, km, segs, at, guidance = await asyncio.gather(
            hop1_sentiment(db, ticker),
            hop2_competitor_signals(db, ticker),
            hop3_supplier_signals(db, ticker),
            fetch_key_metrics_history(db, ticker),
            fetch_segments(db, ticker),
            fetch_analyst_targets(db, ticker),
            fetch_guidance(db, ticker, q, y),
        )
    except Exception as exc:
        return {**state, "error": f"Data fetch failed: {exc}"}
    return {
        **state,
        "hop1": hop1,
        "hop2": hop2,
        "hop3": hop3,
        "km_history": km,
        "segments": segs,
        "analyst_targets": at,
        "guidance": guidance,
    }


def score_signals(state: PredictionState) -> PredictionState:
    if state.get("error"):
        return state
    bundle = compute_signals(
        symbol=state["symbol"],
        quarter=state["quarter"],
        year=state["year"],
        hop1=state["hop1"],
        hop2=state["hop2"],
        km_history=state["km_history"],
        segments=state["segments"],
        analyst_targets=state["analyst_targets"],
        guidance=state["guidance"],
    )
    return {**state, "signals": bundle}


def generate_report(state: PredictionState) -> PredictionState:
    if state.get("error") or not state.get("signals"):
        return state
    bundle: SignalBundle = state["signals"]
    llm = ChatGoogleGenerativeAI(model=GEMINI_MODEL, temperature=0.3)

    signals_text = "\n".join(
        f"- [{s.name}] score={s.score:+.3f} ({s.direction}): {s.evidence}"
        for s in bundle.signals
    )
    supplier_context = ""
    for sup in state.get("hop3", [])[:3]:
        sigs = sup.get("signals", [])
        if sigs:
            top = sigs[0]
            supplier_context += (
                f"\n  {sup.get('supplier', '?')}: \"{top.get('context', '')}\" "
                f"(score={top.get('score', 0):+.2f})"
            )

    prompt = f"""You are a quantitative equity analyst at a top-tier hedge fund.
Based on the structured signal data below, produce a concise Earnings Beat/Miss Probability Report
for {bundle.symbol} Q{bundle.quarter} {bundle.year}.

COMPOSITE BEAT PROBABILITY: {bundle.beat_probability} (score: {bundle.composite_score:+.4f})

SIGNAL EVIDENCE:
{signals_text}

SUPPLIER SIGNALS:{supplier_context if supplier_context else " None available."}

Write the report in this format:
1. **Verdict**: [Beat / Miss / Neutral] — one sentence.
2. **Top 3 Supporting Signals**: bullet list with the metric name, direction, and the key evidence quote.
3. **Key Risks**: two bullet points for what could invalidate this thesis.
4. **Confidence**: Low / Medium / High — one sentence justification.

Be specific. Reference actual numbers from the signal evidence. Keep it under 300 words."""

    response = llm.invoke([HumanMessage(content=prompt)])
    return {**state, "report": response.content.strip()}


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

_graph = StateGraph(PredictionState)
_graph.add_node("fetch_all_data", fetch_all_data)
_graph.add_node("score_signals", score_signals)
_graph.add_node("generate_report", generate_report)

_graph.add_edge(START, "fetch_all_data")
_graph.add_edge("fetch_all_data", "score_signals")
_graph.add_edge("score_signals", "generate_report")
_graph.add_edge("generate_report", END)

prediction_graph = _graph.compile()


async def run_prediction(symbol: str, quarter: int, year: int) -> PredictionReport:
    """Run the full prediction pipeline. Returns a PredictionReport."""
    initial: PredictionState = {
        "symbol": symbol.upper(),
        "quarter": quarter,
        "year": year,
        "hop1": [],
        "hop2": [],
        "hop3": [],
        "km_history": [],
        "segments": [],
        "analyst_targets": {},
        "guidance": {},
        "signals": None,
        "report": None,
        "error": None,
    }
    result = await prediction_graph.ainvoke(initial)
    if result.get("error"):
        raise ValueError(result["error"])
    if not result.get("signals"):
        raise ValueError("Signal scoring produced no output.")
    return PredictionReport(
        symbol=symbol.upper(),
        quarter=quarter,
        year=year,
        signals=result["signals"],
        report=result.get("report", ""),
    )
