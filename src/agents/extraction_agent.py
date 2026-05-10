"""Extraction agent — transcript → SurrealDB graph nodes.

LangGraph pipeline:
  load_transcript → extract_entities → fetch_fundamentals
  → normalize_entities → persist_graph → persist_fundamentals
  → mark_processed → END

FMP fetch nodes call localhost:8000 (the mock FastAPI server) concurrently
via asyncio.gather inside a single fetch_fundamentals node.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, TypedDict

import httpx
from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import END, START, StateGraph

from src.db.connection import get_db
from src.db.normalizer import (
    mark_transcript_processed,
    upsert_analyst_target,
    upsert_company,
    upsert_guidance_entry,
    upsert_key_metric_snapshot,
    upsert_metric,
    upsert_revenue_segments,
    upsert_sentiment_edge,
    upsert_transcript_doc,
)

DATA_DIR = Path(__file__).parent.parent.parent / "data" / "transcripts"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
FMP_BASE = os.getenv("FMP_MOCK_URL", "http://localhost:8000")

_EXTRACTION_SCHEMA = """\
Return ONLY a valid JSON object — no markdown, no code fences:
{
  "companies_mentioned": [
    {"ticker": "AMD", "name": "Advanced Micro Devices", "relationship": "competitor",
     "sector": "Technology", "industry": "Semiconductors"}
  ],
  "metrics": [
    {"name": "Gross Margin", "category": "financial", "value_mentioned": "73%",
     "sentiment_score": 0.8, "context": "<exact supporting quote>", "section": "prepared"}
  ],
  "events": [
    {"name": "Data Center Demand Surge", "type": "macro", "relevance": 0.9}
  ],
  "guidance": {
    "metric": "Revenue", "company_guide": 28.0, "analyst_est": 26.8, "unit": "billion_usd"
  }
}
Sentiment score range: -1.0 (very negative) to 1.0 (very positive).
Section must be "prepared" or "qa". Include at most 5 metrics and 3 events."""


class ExtractionState(TypedDict):
    symbol: str
    quarter: int
    year: int
    raw_content: str
    entities: dict
    normalized: dict       # maps ticker → company_id after dedup
    key_metrics: dict
    segments: list[dict]
    analyst_targets: dict
    db_ids: dict
    error: str | None


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def load_transcript(state: ExtractionState) -> ExtractionState:
    path = DATA_DIR / state["symbol"].upper() / f"Q{state['quarter']}_{state['year']}.json"
    if not path.exists():
        return {**state, "error": f"Transcript not found: {path}"}
    with open(path) as f:
        data = json.load(f)
    return {**state, "raw_content": data.get("content", "")}


def extract_entities(state: ExtractionState) -> ExtractionState:
    if state.get("error"):
        return state
    llm = ChatGoogleGenerativeAI(model=GEMINI_MODEL, temperature=0.2)
    prompt = (
        f"Analyze this earnings call transcript for {state['symbol']} "
        f"Q{state['quarter']} {state['year']}.\n\n"
        f"{_EXTRACTION_SCHEMA}\n\n"
        f"TRANSCRIPT:\n{state['raw_content'][:8000]}"
    )
    response = llm.invoke([HumanMessage(content=prompt)])
    raw = response.content.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        entities = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}") + 1
        try:
            entities = json.loads(raw[start:end]) if start >= 0 and end > start else {}
        except json.JSONDecodeError:
            entities = {}
    return {**state, "entities": entities}


async def fetch_fundamentals(state: ExtractionState) -> ExtractionState:
    if state.get("error"):
        return state
    symbol = state["symbol"].upper()
    async with httpx.AsyncClient(timeout=10.0) as client:
        km_task = client.get(f"{FMP_BASE}/api/v3/key-metrics/{symbol}", params={"period": "quarter"})
        seg_task = client.get(f"{FMP_BASE}/api/v3/revenue-product-segmentation/{symbol}")
        pt_task = client.get(f"{FMP_BASE}/api/v3/price-target-consensus/{symbol}")
        km_resp, seg_resp, pt_resp = await asyncio.gather(km_task, seg_task, pt_task, return_exceptions=True)

    key_metrics: dict = {}
    segments: list[dict] = []
    analyst_targets: dict = {}

    if isinstance(km_resp, httpx.Response) and km_resp.status_code == 200:
        data = km_resp.json()
        key_metrics = data[0] if isinstance(data, list) and data else data

    if isinstance(seg_resp, httpx.Response) and seg_resp.status_code == 200:
        raw_seg = seg_resp.json()
        if isinstance(raw_seg, dict):
            segments = [{"segment_name": k, "revenue": v} for k, v in raw_seg.items() if isinstance(v, (int, float))]
        elif isinstance(raw_seg, list):
            segments = raw_seg

    if isinstance(pt_resp, httpx.Response) and pt_resp.status_code == 200:
        data = pt_resp.json()
        analyst_targets = data[0] if isinstance(data, list) and data else data

    return {**state, "key_metrics": key_metrics, "segments": segments, "analyst_targets": analyst_targets}


async def normalize_entities(state: ExtractionState) -> ExtractionState:
    if state.get("error"):
        return state
    db = await get_db()
    normalized: dict[str, Any] = {}

    # Always upsert the primary company from this transcript
    primary_id = await upsert_company(db, state["symbol"], state["symbol"])
    normalized[state["symbol"].upper()] = primary_id

    for co in state.get("entities", {}).get("companies_mentioned", []):
        ticker = co.get("ticker", "")
        name = co.get("name", ticker)
        if ticker:
            cid = await upsert_company(db, ticker, name, co.get("sector"), co.get("industry"))
            normalized[ticker.upper()] = cid

    return {**state, "normalized": normalized}


async def persist_graph(state: ExtractionState) -> ExtractionState:
    if state.get("error"):
        return state
    db = await get_db()
    db_ids: dict[str, Any] = {}

    symbol = state["symbol"].upper()
    company_id = state["normalized"].get(symbol, f"company:{symbol.lower()}")

    transcript_id = await upsert_transcript_doc(
        db,
        company_id,
        state["quarter"],
        state["year"],
        f"{state['year']}-01-01T00:00:00Z",
        str(DATA_DIR / symbol / f"Q{state['quarter']}_{state['year']}.json"),
    )
    db_ids["transcript_doc"] = transcript_id

    entities = state.get("entities", {})
    for m in entities.get("metrics", []):
        name = m.get("name", "")
        if not name:
            continue
        metric_id = await upsert_metric(db, name, m.get("category", "financial"))
        await upsert_sentiment_edge(
            db,
            transcript_id,
            metric_id,
            float(m.get("sentiment_score", 0.0)),
            m.get("context", ""),
            m.get("section", "prepared"),
        )

    guidance = entities.get("guidance")
    if guidance and guidance.get("metric"):
        metric_id = await upsert_metric(db, guidance["metric"], "financial")
        await upsert_guidance_entry(
            db,
            company_id,
            metric_id,
            state["quarter"],
            state["year"],
            guidance.get("company_guide"),
            guidance.get("analyst_est"),
        )

    return {**state, "db_ids": db_ids}


async def persist_fundamentals(state: ExtractionState) -> ExtractionState:
    if state.get("error"):
        return state
    db = await get_db()
    symbol = state["symbol"].upper()
    company_id = state["normalized"].get(symbol, f"company:{symbol.lower()}")
    period = f"{state['year']}-Q{state['quarter']}"

    km = state.get("key_metrics", {})
    if km:
        await upsert_key_metric_snapshot(
            db,
            company_id,
            period,
            dso=km.get("daysOfSalesOutstanding") or km.get("dso"),
            inventory_turnover=km.get("inventoryTurnover"),
            revenue_per_share=km.get("revenuePerShare"),
            gross_profit_margin=km.get("grossProfitMargin"),
        )

    segs = state.get("segments", [])
    if segs:
        await upsert_revenue_segments(db, company_id, period, segs)

    at = state.get("analyst_targets", {})
    if at:
        await upsert_analyst_target(
            db,
            company_id,
            target_consensus=at.get("targetConsensus"),
            target_high=at.get("targetHigh"),
            target_low=at.get("targetLow"),
            target_median=at.get("targetMedian"),
        )

    return state


async def mark_processed(state: ExtractionState) -> ExtractionState:
    if state.get("error"):
        return state
    db = await get_db()
    transcript_id = state.get("db_ids", {}).get("transcript_doc")
    if transcript_id:
        await mark_transcript_processed(db, transcript_id)
    return state


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

_graph = StateGraph(ExtractionState)
_graph.add_node("load_transcript", load_transcript)
_graph.add_node("extract_entities", extract_entities)
_graph.add_node("fetch_fundamentals", fetch_fundamentals)
_graph.add_node("normalize_entities", normalize_entities)
_graph.add_node("persist_graph", persist_graph)
_graph.add_node("persist_fundamentals", persist_fundamentals)
_graph.add_node("mark_processed", mark_processed)

_graph.add_edge(START, "load_transcript")
_graph.add_edge("load_transcript", "extract_entities")
_graph.add_edge("extract_entities", "fetch_fundamentals")
_graph.add_edge("fetch_fundamentals", "normalize_entities")
_graph.add_edge("normalize_entities", "persist_graph")
_graph.add_edge("persist_graph", "persist_fundamentals")
_graph.add_edge("persist_fundamentals", "mark_processed")
_graph.add_edge("mark_processed", END)

extraction_graph = _graph.compile()


async def run_extraction(symbol: str, quarter: int, year: int) -> dict:
    """Run the extraction pipeline for a single transcript. Returns final state."""
    initial: ExtractionState = {
        "symbol": symbol.upper(),
        "quarter": quarter,
        "year": year,
        "raw_content": "",
        "entities": {},
        "normalized": {},
        "key_metrics": {},
        "segments": [],
        "analyst_targets": {},
        "db_ids": {},
        "error": None,
    }
    return await extraction_graph.ainvoke(initial)
